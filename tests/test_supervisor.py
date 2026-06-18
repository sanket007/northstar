import importlib
import sys
from northstar.proc import CommandResult


def test_session_name():
    from northstar import supervisor
    assert supervisor.session_name("acme") == "ns-acme"


def test_start_builds_tmux_new_session_with_env_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd if isinstance(cmd, str) else " ".join(cmd))
        # has-session returns non-zero (not running) so start proceeds
        if "has-session" in (cmd if isinstance(cmd, str) else " ".join(cmd)):
            return CommandResult(1, "", "")
        return CommandResult(0, "", "")
    supervisor.start("acme", repo_dir=tmp_path / "repo",
                     plane_env={"PLANE_API_KEY": "k", "PLANE_BASE_URL": "https://x",
                                "PLANE_WORKSPACE_SLUG": "w"}, runner=runner)
    joined = "\n".join(calls)
    assert "tmux new-session -d -s ns-acme" in joined
    assert "-m orchestrator --config" in joined
    assert sys.executable in joined
    assert "PLANE_API_KEY=k" in joined
    assert "pipe-pane" in joined


def test_stop_kills_session():
    from northstar import supervisor
    calls = []
    runner = lambda cmd, **kw: (calls.append(" ".join(cmd)), CommandResult(0, "", ""))[1]
    supervisor.stop("acme", runner=runner)
    assert "tmux kill-session -t ns-acme" in calls[0]


def test_status_reports_running_flag():
    from northstar import supervisor
    def runner(cmd, **kw):
        # has-session ok only for ns-acme
        c = " ".join(cmd)
        return CommandResult(0 if "ns-acme" in c else 1, "", "")
    rows = supervisor.status(["acme", "beta"], runner=runner)
    by = {r["name"]: r["running"] for r in rows}
    assert by == {"acme": True, "beta": False}
