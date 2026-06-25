from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json
import os
import subprocess
import threading
import time

from orchestrator.config import Config
from orchestrator import obs

# Roles that share ONE long-lived Claude session per ticket (context retained across stages,
# so no re-hydration tax). The reviewer is deliberately NOT here — it stays a fresh, independent
# session so its review is adversarial, not the builder grading its own work.
PERSISTENT_ROLES = {"builder", "qa"}

# Appended to every session's system prompt: terse output to cut token spend, but never at the
# cost of correctness, and code/commits/PRs/security explanations stay normal.
CAVEMAN_ULTRA = (
    "OUTPUT STYLE (caveman ultra): in prose, Plane comments, and your reasoning be maximally "
    "terse — drop articles, filler, pleasantries, and hedging; fragments are fine; pick the "
    "shortest words. EXEMPT, write normally with full correctness: code, commit messages, PR "
    "titles/bodies, and any security-relevant explanation. Never trade technical accuracy or a "
    "required step for brevity."
)


@dataclass
class SessionResult:
    ok: bool
    error: str | None = None
    session_id: str | None = None  # the id claude assigned (captured from the init event)
    # token telemetry (captured from stream-json):
    initial_input_tokens: int = 0   # context size at the first turn (system+tools+prompt) ~= startup load
    total_input_tokens: int = 0     # cumulative input across the run (incl cache reads)
    output_tokens: int = 0
    num_turns: int = 0
    cost_usd: float = 0.0
    model: str | None = None        # model the session ran on
    duration_seconds: float = 0.0   # wall-clock for the session


def role_doc_path(cfg: Config, role: str) -> Path:
    return cfg.templates_dir / f"{role}.md"


_ROLE_DOC_CACHE: dict[str, str] = {}


def _role_doc_text(cfg: Config, role: str) -> str:
    if role not in _ROLE_DOC_CACHE:
        _ROLE_DOC_CACHE[role] = role_doc_path(cfg, role).read_text()
    return _ROLE_DOC_CACHE[role]


def model_for_role(cfg: Config, role: str) -> str:
    return (getattr(cfg, "role_models", None) or {}).get(role, cfg.claude_model)


def _common_flags(cfg: Config, model: str) -> list[str]:
    flags = [
        "--output-format", "stream-json", "--verbose",
        "--dangerously-skip-permissions",
        "--mcp-config", str(cfg.mcp_config_path),
        # only the Plane server — ignore the user's personal MCP servers so it connects
        # fast without contention and never blocks on an unrelated server needing auth
        "--strict-mcp-config",
        "--model", model,
        "--max-turns", str(cfg.max_turns),
    ]
    return flags


def build_claude_command(cfg: Config, role: str, ticket_id: str, role_doc_text: str,
                         context: str = "", *, resume: bool = False,
                         instruction: str = "", session_id: str = "") -> list[str]:
    persistent = role in PERSISTENT_ROLES
    if persistent and resume and session_id:
        # The ticket context AND role instructions already live in the retained session — send
        # ONLY the next-phase instruction (plus the latest comment when a rework needs it). No
        # system prompt: --resume keeps the one set at creation. This is the re-hydration saving.
        # We resume the id claude ASSIGNED at creation (captured then) — we never force an id, so
        # a re-dispatch can never collide with an existing session.
        model = cfg.claude_model  # persistent session was created with this model; keep it
        return [cfg.claude_binary, "-p", instruction or "Continue the next phase for this ticket.",
                *_common_flags(cfg, model), "--resume", session_id]
    # Fresh start: persistent role's first run, or a non-persistent role (reviewer). Hand over the
    # project id + pre-fetched ticket context so it doesn't re-read Plane via MCP (the big drain).
    prompt = (f"You are the {role} for Plane work item {ticket_id} in Plane project "
              f"{cfg.plane_project_id}. Follow your role instructions.")
    if context:
        prompt += "\n\n" + context
    model = cfg.claude_model if persistent else model_for_role(cfg, role)
    # NOTE: never force --session-id. claude assigns one; we capture it from the init event and
    # resume THAT later. Forcing a precomputed id makes any re-dispatch collide ("session already
    # exists") and exit instantly with no result -> false block.
    return [cfg.claude_binary, "-p", prompt, *_common_flags(cfg, model),
            "--append-system-prompt", role_doc_text + "\n\n" + CAVEMAN_ULTRA]


_LIMIT_PHRASES = ("session limit", "usage limit", "hit your limit", "limit reached")


def _is_usage_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _LIMIT_PHRASES)


def _usage_total(u: dict) -> int:
    """All input tokens a turn actually saw = fresh + cache-creation + cache-read."""
    return int((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
               + (u.get("cache_read_input_tokens") or 0))


def parse_stream_json(lines: Iterable[str]) -> SessionResult:
    saw_result = False
    limit_hit = False
    sid: str | None = None
    initial = 0          # context seen by the FIRST turn ~= startup load
    tele = {}            # telemetry to stamp onto the returned SessionResult
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        # claude assigns the session id and reports it on the init event (and on result);
        # capture it so the orchestrator can --resume this exact conversation later.
        sid = obj.get("session_id") or sid
        if obj.get("type") == "assistant":
            msg = obj.get("message", {})
            if not initial and isinstance(msg.get("usage"), dict):
                initial = _usage_total(msg["usage"])  # first turn's input ~= initial context
            for b in msg.get("content", []):
                if b.get("type") == "text" and _is_usage_limit(b.get("text", "")):
                    limit_hit = True
        if obj.get("type") == "result":
            saw_result = True
            u = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            tele = dict(initial_input_tokens=initial, total_input_tokens=_usage_total(u),
                        output_tokens=int(u.get("output_tokens") or 0),
                        num_turns=int(obj.get("num_turns") or 0),
                        cost_usd=float(obj.get("total_cost_usd") or 0.0))
            # Claude prints the usage-limit notice then exits result=success having done nothing —
            # surface it as its own error so the daemon pauses instead of looping into the wall.
            if limit_hit:
                return SessionResult(ok=False, error="usage_limit", session_id=sid, **tele)
            if obj.get("is_error"):
                # keep the subtype as a stable prefix (callers substring-match it) and append
                # whatever human-readable cause claude provided, so blocks/logs aren't opaque.
                sub = obj.get("subtype", "error")
                detail = obj.get("result") or obj.get("error") or ""
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("error") or ""
                detail = " ".join(str(detail).split())[:300]
                err = f"{sub}: {detail}" if detail and detail != sub else sub
                return SessionResult(ok=False, error=err, session_id=sid, **tele)
            return SessionResult(ok=True, error=None, session_id=sid, **tele)
    tele = dict(initial_input_tokens=initial)
    if limit_hit:
        return SessionResult(ok=False, error="usage_limit", session_id=sid, **tele)
    if not saw_result:
        return SessionResult(ok=False, error="session ended with no result event", session_id=sid, **tele)
    return SessionResult(ok=False, error="unknown", session_id=sid, **tele)


def claude_event_line(raw: str) -> str | None:
    """Turn one stream-json event into a short, human-readable activity line (or None to skip)."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    kind = obj.get("type")
    if kind == "system" and obj.get("subtype") == "init":
        return "session initialized"
    if kind == "assistant":
        parts = []
        for block in obj.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "text":
                txt = " ".join(block.get("text", "").split())
                if txt:
                    parts.append(f"says: {txt[:200]}")
            elif bt == "tool_use":
                parts.append(f"tool: {block.get('name', '?')}")
        return " | ".join(parts) or None
    if kind == "result":
        return f"result: {obj.get('subtype', 'done')}"
    return None


def _pump_stream(stream, role: str, ticket_id: str, sink: list[str]) -> None:
    """Read the session's stream-json line-by-line, capturing each line and emitting it live."""
    if stream is None:
        return
    short = ticket_id[:8]
    for raw in iter(stream.readline, ""):
        raw = raw.rstrip("\n")
        if not raw:
            continue
        sink.append(raw)
        msg = claude_event_line(raw)
        if msg:
            obs.info("claude", f"{role}/{short} {msg}")


def run_session(cfg: Config, role: str, ticket_id: str, worktree: Path,
                *, runner=subprocess.Popen, context: str = "",
                resume: bool = False, instruction: str = "", session_id: str = "") -> SessionResult:
    role_doc_text = _role_doc_text(cfg, role)
    cmd = build_claude_command(cfg, role, ticket_id, role_doc_text, context,
                               resume=resume, instruction=instruction, session_id=session_id)
    model = cfg.claude_model if role in PERSISTENT_ROLES else model_for_role(cfg, role)
    verb = "resuming" if resume else "launching"
    obs.info("claude", f"{verb} {role} session for {ticket_id} in {worktree.name}")
    started = time.monotonic()
    # Line-buffered text pipe so the reader thread sees each stream-json event as it lands,
    # giving real-time visibility into what the session is doing (in `northstar logs`).
    env = {**os.environ, "MCP_TIMEOUT": "30000", "MCP_TOOL_TIMEOUT": "60000"}
    if getattr(cfg, "defer_mcp_tools", True):
        # load MCP tool schemas on demand instead of all up front -> smaller initial context
        env["ENABLE_TOOL_SEARCH"] = "auto"
    proc = runner(cmd, cwd=str(worktree), stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    lines: list[str] = []
    pump = threading.Thread(target=_pump_stream,
                            args=(getattr(proc, "stdout", None), role, ticket_id, lines),
                            daemon=True)
    pump.start()
    try:
        proc.wait(timeout=cfg.session_timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        pump.join(timeout=2)
        dur = time.monotonic() - started
        # parse what streamed before the kill to recover the session id (from the init event) +
        # partial context size, so a persistent session can RESUME after a timeout instead of
        # being lost and recreated.
        partial = parse_stream_json(lines)
        obs.info("claude", f"{role} session for {ticket_id} timed out ({dur:.0f}s)")
        return SessionResult(ok=False, error="session timeout", model=model, duration_seconds=dur,
                             session_id=partial.session_id,
                             initial_input_tokens=partial.initial_input_tokens)
    pump.join(timeout=5)
    result = parse_stream_json(lines)
    dur = time.monotonic() - started
    result.model = model
    result.duration_seconds = dur
    # token telemetry: initial context (startup load) + run totals, per session
    if result.initial_input_tokens or result.total_input_tokens:
        obs.info("claude",
                 f"{role}/{ticket_id[:8]} tokens: initial-context ~{result.initial_input_tokens//1000}k "
                 f"({result.initial_input_tokens}) | run in {result.total_input_tokens} "
                 f"out {result.output_tokens} | turns {result.num_turns} | ${result.cost_usd:.3f}"
                 f"{' | RESUMED' if resume else ''}")
        warn = getattr(cfg, "context_warn_tokens", 0)
        peak = max(result.initial_input_tokens, result.total_input_tokens)
        if warn and peak > warn:
            obs.info("claude",
                     f"WARN {role}/{ticket_id[:8]} context {peak} > {warn} threshold — "
                     "Claude Code auto-compacts near the model window; watch this session's growth")
    if proc.returncode not in (0, None) and result.ok:
        obs.info("claude", f"{role} session for {ticket_id} exited {proc.returncode} ({dur:.0f}s)")
        return SessionResult(ok=False, error=f"claude exited {proc.returncode}",
                             model=model, duration_seconds=dur, session_id=result.session_id)
    obs.info("claude", f"{role} session for {ticket_id} "
                       f"{'finished' if result.ok else 'failed'} ({dur:.0f}s)")
    return result
