from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json
import os
import subprocess
import threading
import time
import uuid

from orchestrator.config import Config
from orchestrator import obs

# Roles that share ONE long-lived Claude session per ticket (context retained across stages,
# so no re-hydration tax). The reviewer is deliberately NOT here — it stays a fresh, independent
# session so its review is adversarial, not the builder grading its own work.
PERSISTENT_ROLES = {"builder", "qa"}

# Fixed namespace -> deterministic per-ticket session id (same id every dispatch for a ticket).
_SESSION_NS = uuid.UUID("b1f0e7a2-5c3d-4e6f-9a8b-0c1d2e3f4a5b")

# Appended to every session's system prompt: terse output to cut token spend, but never at the
# cost of correctness, and code/commits/PRs/security explanations stay normal.
CAVEMAN_ULTRA = (
    "OUTPUT STYLE (caveman ultra): in prose, Plane comments, and your reasoning be maximally "
    "terse — drop articles, filler, pleasantries, and hedging; fragments are fine; pick the "
    "shortest words. EXEMPT, write normally with full correctness: code, commit messages, PR "
    "titles/bodies, and any security-relevant explanation. Never trade technical accuracy or a "
    "required step for brevity."
)


def ticket_session_id(ticket_id: str) -> str:
    return str(uuid.uuid5(_SESSION_NS, ticket_id))


@dataclass
class SessionResult:
    ok: bool
    error: str | None = None


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
    return [
        "--output-format", "stream-json", "--verbose",
        "--dangerously-skip-permissions",
        "--mcp-config", str(cfg.mcp_config_path),
        # only the Plane server — ignore the user's personal MCP servers so it connects
        # fast without contention and never blocks on an unrelated server needing auth
        "--strict-mcp-config",
        "--model", model,
        "--max-turns", str(cfg.max_turns),
    ]


def build_claude_command(cfg: Config, role: str, ticket_id: str, role_doc_text: str,
                         context: str = "", *, resume: bool = False,
                         instruction: str = "") -> list[str]:
    persistent = role in PERSISTENT_ROLES
    if persistent and resume:
        # The ticket context AND role instructions already live in the retained session — send
        # ONLY the next-phase instruction (plus the latest comment when a rework needs it). No
        # system prompt: --resume keeps the one set at creation. This is the re-hydration saving.
        model = cfg.claude_model  # persistent session was created with this model; keep it
        return [cfg.claude_binary, "-p", instruction or "Continue the next phase for this ticket.",
                *_common_flags(cfg, model), "--resume", ticket_session_id(ticket_id)]
    # Fresh start: persistent role's first run, or a non-persistent role (reviewer). Hand over the
    # project id + pre-fetched ticket context so it doesn't re-read Plane via MCP (the big drain).
    prompt = (f"You are the {role} for Plane work item {ticket_id} in Plane project "
              f"{cfg.plane_project_id}. Follow your role instructions.")
    if context:
        prompt += "\n\n" + context
    model = cfg.claude_model if persistent else model_for_role(cfg, role)
    cmd = [cfg.claude_binary, "-p", prompt, *_common_flags(cfg, model),
           "--append-system-prompt", role_doc_text + "\n\n" + CAVEMAN_ULTRA]
    if persistent:
        # pin a stable id so later stages (rework, QA) resume THIS conversation
        cmd += ["--session-id", ticket_session_id(ticket_id)]
    return cmd


_LIMIT_PHRASES = ("session limit", "usage limit", "hit your limit", "limit reached")


def _is_usage_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _LIMIT_PHRASES)


def parse_stream_json(lines: Iterable[str]) -> SessionResult:
    saw_result = False
    limit_hit = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "assistant":
            for b in obj.get("message", {}).get("content", []):
                if b.get("type") == "text" and _is_usage_limit(b.get("text", "")):
                    limit_hit = True
        if obj.get("type") == "result":
            saw_result = True
            # Claude prints the usage-limit notice then exits result=success having done nothing —
            # surface it as its own error so the daemon pauses instead of looping into the wall.
            if limit_hit:
                return SessionResult(ok=False, error="usage_limit")
            if obj.get("is_error"):
                return SessionResult(ok=False, error=obj.get("subtype", "error"))
            return SessionResult(ok=True, error=None)
    if limit_hit:
        return SessionResult(ok=False, error="usage_limit")
    if not saw_result:
        return SessionResult(ok=False, error="session ended with no result event")
    return SessionResult(ok=False, error="unknown")


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
                resume: bool = False, instruction: str = "") -> SessionResult:
    role_doc_text = _role_doc_text(cfg, role)
    cmd = build_claude_command(cfg, role, ticket_id, role_doc_text, context,
                               resume=resume, instruction=instruction)
    verb = "resuming" if resume else "launching"
    obs.info("claude", f"{verb} {role} session for {ticket_id} in {worktree.name}")
    started = time.monotonic()
    # Line-buffered text pipe so the reader thread sees each stream-json event as it lands,
    # giving real-time visibility into what the session is doing (in `northstar logs`).
    env = {**os.environ, "MCP_TIMEOUT": "30000", "MCP_TOOL_TIMEOUT": "60000"}
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
        obs.info("claude", f"{role} session for {ticket_id} timed out "
                           f"({time.monotonic() - started:.0f}s)")
        return SessionResult(ok=False, error="session timeout")
    pump.join(timeout=5)
    result = parse_stream_json(lines)
    dur = time.monotonic() - started
    if proc.returncode not in (0, None) and result.ok:
        obs.info("claude", f"{role} session for {ticket_id} exited {proc.returncode} ({dur:.0f}s)")
        return SessionResult(ok=False, error=f"claude exited {proc.returncode}")
    obs.info("claude", f"{role} session for {ticket_id} "
                       f"{'finished' if result.ok else 'failed'} ({dur:.0f}s)")
    return result
