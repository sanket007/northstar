from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json
import subprocess
import time

from orchestrator.config import Config
from orchestrator import obs


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


def build_claude_command(cfg: Config, role: str, ticket_id: str,
                         role_doc_text: str) -> list[str]:
    prompt = f"You are the {role} for Plane work item {ticket_id}. Follow your role instructions."
    return [
        cfg.claude_binary, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--dangerously-skip-permissions",
        "--mcp-config", str(cfg.mcp_config_path),
        "--model", cfg.claude_model,
        "--max-turns", str(cfg.max_turns),
        "--append-system-prompt", role_doc_text,
    ]


def parse_stream_json(lines: Iterable[str]) -> SessionResult:
    saw_result = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            saw_result = True
            if obj.get("is_error"):
                return SessionResult(ok=False, error=obj.get("subtype", "error"))
            return SessionResult(ok=True, error=None)
    if not saw_result:
        return SessionResult(ok=False, error="session ended with no result event")
    return SessionResult(ok=False, error="unknown")


def run_session(cfg: Config, role: str, ticket_id: str, worktree: Path,
                *, runner=subprocess.Popen) -> SessionResult:
    role_doc_text = _role_doc_text(cfg, role)
    cmd = build_claude_command(cfg, role, ticket_id, role_doc_text)
    obs.info("claude", f"launching {role} session for {ticket_id} in {worktree.name}")
    started = time.monotonic()
    proc = runner(cmd, cwd=str(worktree), stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, text=True)
    try:
        stdout, _ = proc.communicate(timeout=cfg.session_timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        obs.info("claude", f"✗ {role} session for {ticket_id} timed out "
                           f"({time.monotonic() - started:.0f}s)")
        return SessionResult(ok=False, error="session timeout")
    result = parse_stream_json((stdout or "").splitlines())
    dur = time.monotonic() - started
    if proc.returncode not in (0, None) and result.ok:
        obs.info("claude", f"✗ {role} session for {ticket_id} exited {proc.returncode} ({dur:.0f}s)")
        return SessionResult(ok=False, error=f"claude exited {proc.returncode}")
    obs.info("claude", f"{'✓' if result.ok else '✗'} {role} session for {ticket_id} "
                       f"finished ({dur:.0f}s)")
    return result
