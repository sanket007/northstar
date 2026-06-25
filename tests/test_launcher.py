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
    assert "ROLE TEXT" in cmd[cmd.index("--append-system-prompt") + 1]
    assert "stream-json" in cmd and "--dangerously-skip-permissions" in cmd
    assert "--strict-mcp-config" in cmd  # only the Plane server, no personal-MCP contention
    p = cmd[cmd.index("-p") + 1]
    assert "i1" in p and "builder" in p
    assert "project p" in p  # project id handed to the session (cfg.plane_project_id == "p")
    assert "hydrat" not in p.lower() and "comment" not in p.lower()  # prompt no longer restates hydration


def test_per_role_model_override_and_context(tmp_path):
    from orchestrator.launcher import build_claude_command, model_for_role
    cfg = make_cfg(tmp_path)
    cfg.role_models = {"reviewer": "claude-opus-4-8"}
    assert model_for_role(cfg, "reviewer") == "claude-opus-4-8"
    assert model_for_role(cfg, "builder") == cfg.claude_model      # falls back
    cmd = build_claude_command(cfg, "reviewer", "i1", "doc", "CTX-BLOCK-XYZ")
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert "CTX-BLOCK-XYZ" in cmd[cmd.index("-p") + 1]             # context injected into prompt


def test_role_doc_path(tmp_path):
    cfg = make_cfg(tmp_path)
    assert role_doc_path(cfg, "qa") == cfg.templates_dir / "qa.md"


def test_persistent_roles_exclude_reviewer():
    from orchestrator.launcher import PERSISTENT_ROLES
    assert "builder" in PERSISTENT_ROLES and "qa" in PERSISTENT_ROLES
    assert "reviewer" not in PERSISTENT_ROLES  # review stays independent


def test_create_never_forces_session_id(tmp_path):
    # Regression: forcing --session-id made a re-dispatch collide ("already exists") and false-block.
    # claude must assign its own id; we capture it from the init event and resume that later.
    from orchestrator.launcher import build_claude_command
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "builder", "tkt1", "ROLE DOC", "CTX-BLOCK")
    assert "--session-id" not in cmd and "--resume" not in cmd
    sysp = cmd[cmd.index("--append-system-prompt") + 1]
    assert "ROLE DOC" in sysp and "caveman ultra" in sysp.lower()
    assert "CTX-BLOCK" in cmd[cmd.index("-p") + 1]


def test_persistent_resume_uses_captured_id_and_instruction_only(tmp_path):
    from orchestrator.launcher import build_claude_command
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "qa", "tkt1", "ROLE DOC", "CTX-BLOCK",
                               resume=True, instruction="DO QA NOW", session_id="sid-xyz")
    assert cmd[cmd.index("--resume") + 1] == "sid-xyz"
    assert cmd[cmd.index("-p") + 1] == "DO QA NOW"
    assert "--append-system-prompt" not in cmd      # retained from creation
    assert "--session-id" not in cmd
    assert "CTX-BLOCK" not in " ".join(cmd)          # context not re-injected on resume


def test_resume_without_captured_id_falls_back_to_create(tmp_path):
    # If we somehow have no stored id, never emit a bare --resume; build a fresh create instead.
    from orchestrator.launcher import build_claude_command
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "builder", "tkt1", "ROLE DOC", "CTX",
                               resume=True, instruction="x", session_id="")
    assert "--resume" not in cmd
    assert "--append-system-prompt" in cmd          # fell back to a full create


def test_parse_stream_json_captures_session_id():
    from orchestrator.launcher import parse_stream_json
    lines = [
        '{"type":"system","subtype":"init","session_id":"abc-123"}',
        '{"type":"result","subtype":"success","is_error":false}',
    ]
    res = parse_stream_json(lines)
    assert res.ok is True and res.session_id == "abc-123"


def test_parse_stream_json_captures_token_telemetry():
    from orchestrator.launcher import parse_stream_json
    lines = [
        '{"type":"system","subtype":"init","session_id":"s1"}',
        # first turn: initial context = input + cache_read + cache_creation
        '{"type":"assistant","message":{"usage":{"input_tokens":1000,'
        '"cache_read_input_tokens":18000,"cache_creation_input_tokens":1000}}}',
        '{"type":"assistant","message":{"usage":{"input_tokens":500}}}',  # later turn ignored for initial
        '{"type":"result","subtype":"success","is_error":false,"num_turns":7,'
        '"total_cost_usd":0.42,"usage":{"input_tokens":2000,"cache_read_input_tokens":40000,'
        '"output_tokens":3000}}',
    ]
    res = parse_stream_json(lines)
    assert res.initial_input_tokens == 20000      # 1000+18000+1000, from the FIRST turn
    assert res.total_input_tokens == 42000        # 2000+40000 from the result usage
    assert res.output_tokens == 3000
    assert res.num_turns == 7 and abs(res.cost_usd - 0.42) < 1e-9


def test_reviewer_is_fresh_session(tmp_path):
    from orchestrator.launcher import build_claude_command
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "reviewer", "tkt1", "ROLE DOC", "CTX")
    assert "--session-id" not in cmd and "--resume" not in cmd  # independent, not persistent


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


def test_parse_stream_json_detects_usage_limit():
    # Claude prints the limit notice then exits result=success having done nothing
    lines = [
        '{"type":"assistant","message":{"content":[{"type":"text","text":"You\\u2019ve hit your session limit, resets 11:30pm"}]}}',
        '{"type":"result","subtype":"success","is_error":false}',
    ]
    res = parse_stream_json(lines)
    assert res.ok is False and res.error == "usage_limit"


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
