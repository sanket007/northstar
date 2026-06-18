from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json
import subprocess

from orchestrator.config import Config


@dataclass
class SessionResult:
    ok: bool
    error: str | None = None


def role_doc_path(cfg: Config, role: str) -> Path:
    return cfg.templates_dir / f"{role}.md"


def build_claude_command(cfg: Config, role: str, ticket_id: str,
                         worktree: Path, role_doc_text: str) -> list[str]:
    prompt = (
        f"You are acting as the {role} for Plane work item {ticket_id}. "
        f"Follow your role instructions exactly. Begin by fully hydrating context "
        f"(work item, all comments, PR thread, docs/ memory) before any action."
    )
    return [
        cfg.claude_binary,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--mcp-config", str(cfg.mcp_config_path),
        "--model", cfg.claude_model,
        "--append-system-prompt", role_doc_text,
        "--max-turns", str(cfg.max_turns),
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
    role_doc_text = role_doc_path(cfg, role).read_text()
    cmd = build_claude_command(cfg, role, ticket_id, worktree, role_doc_text)
    proc = runner(cmd, cwd=str(worktree), stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, text=True)
    try:
        stdout, _ = proc.communicate(timeout=cfg.session_timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return SessionResult(ok=False, error="session timeout")
    result = parse_stream_json((stdout or "").splitlines())
    if proc.returncode not in (0, None) and result.ok:
        return SessionResult(ok=False, error=f"claude exited {proc.returncode}")
    return result
