from pathlib import Path
import subprocess
from orchestrator.config import Config
from orchestrator.launcher import (
    build_claude_command, parse_stream_json, run_session, SessionResult, role_doc_path,
)


def make_cfg(tmp_path) -> Config:
    return Config(
        plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/r", repo_dir=tmp_path / "repo",
        worktrees_root=tmp_path / "wt", poll_interval_seconds=30, claude_binary="claude",
        claude_model="claude-opus-4-8", mcp_config_path=tmp_path / "mcp.json",
        templates_dir=tmp_path / "templates", state_ids={}, max_concurrency=1,
    )


def test_build_command_drops_worktree_and_trims_prompt(tmp_path):
    from orchestrator.launcher import build_claude_command
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "builder", "i1", "ROLE TEXT")  # no worktree arg
    assert "ROLE TEXT" in cmd and "stream-json" in cmd and "bypassPermissions" in cmd
    p = cmd[cmd.index("-p") + 1]
    assert "i1" in p and "builder" in p
    assert "hydrat" not in p.lower() and "comment" not in p.lower()  # prompt no longer restates hydration


def test_role_doc_path(tmp_path):
    cfg = make_cfg(tmp_path)
    assert role_doc_path(cfg, "qa") == cfg.templates_dir / "qa.md"


def test_parse_stream_json_success():
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"assistant","message":{}}',
        '{"type":"result","subtype":"success","is_error":false}',
    ]
    assert parse_stream_json(lines) == SessionResult(ok=True, error=None)


def test_parse_stream_json_error_flag():
    lines = ['{"type":"result","subtype":"error_max_turns","is_error":true}']
    res = parse_stream_json(lines)
    assert res.ok is False
    assert "error_max_turns" in (res.error or "")


def test_parse_stream_json_no_result_is_failure():
    res = parse_stream_json(['{"type":"assistant","message":{}}'])
    assert res.ok is False
    assert "no result" in (res.error or "").lower()


def test_run_session_timeout_returns_failure(tmp_path):
    """When the subprocess times out, run_session returns SessionResult(ok=False, error='session timeout')."""
    cfg = make_cfg(tmp_path)
    # Write a stub role doc so role_doc_path().read_text() works
    role_doc = cfg.templates_dir
    role_doc.mkdir(parents=True, exist_ok=True)
    (role_doc / "builder.md").write_text("stub")

    class FakeProc:
        returncode = -9

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=["claude"], timeout=timeout)
            return ("", "")

        def kill(self):
            pass

    def fake_runner(cmd, **kwargs):
        return FakeProc()

    result = run_session(cfg, "builder", "i1", tmp_path / "wt", runner=fake_runner)
    assert result.ok is False
    assert result.error == "session timeout"
