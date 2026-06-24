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
    assert "ROLE TEXT" in cmd and "stream-json" in cmd and "--dangerously-skip-permissions" in cmd
    assert "--strict-mcp-config" in cmd  # only the Plane server, no personal-MCP contention
    p = cmd[cmd.index("-p") + 1]
    assert "i1" in p and "builder" in p
    assert "project p" in p  # project id handed to the session (cfg.plane_project_id == "p")
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
        stdout = None

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=["claude"], timeout=timeout)

        def kill(self):
            pass

    def fake_runner(cmd, **kwargs):
        return FakeProc()

    result = run_session(cfg, "builder", "i1", tmp_path / "wt", runner=fake_runner)
    assert result.ok is False
    assert result.error == "session timeout"


def test_claude_event_line_formats_events():
    from orchestrator.launcher import claude_event_line
    assert claude_event_line('{"type":"system","subtype":"init"}') == "session initialized"
    assert claude_event_line(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hello\\nworld"}]}}'
    ) == "says: hello world"
    assert claude_event_line(
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}'
    ) == "tool: Bash"
    assert claude_event_line('{"type":"result","subtype":"success"}') == "result: success"
    assert claude_event_line("not json") is None


def test_run_session_streams_events_live_and_parses_result(tmp_path, capsys):
    cfg = make_cfg(tmp_path)
    (cfg.templates_dir).mkdir(parents=True, exist_ok=True)
    (cfg.templates_dir / "builder.md").write_text("stub")

    class FakeStream:
        def __init__(self, lines):
            self._it = iter(lines)

        def readline(self):
            return next(self._it, "")

    class FakeProc:
        returncode = 0

        def __init__(self):
            self.stdout = FakeStream([
                '{"type":"system","subtype":"init"}\n',
                '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit"}]}}\n',
                '{"type":"result","subtype":"success","is_error":false}\n',
            ])

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    result = run_session(cfg, "builder", "abc12345", tmp_path / "wt",
                         runner=lambda cmd, **kw: FakeProc())
    assert result.ok is True
    err = capsys.readouterr().err
    assert "tool: Edit" in err          # streamed live, per event
    assert "builder/abc12345" in err
