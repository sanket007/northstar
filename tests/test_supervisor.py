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


def test_detached_start_spawns_and_writes_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    paths.ensure_dirs(); paths.set_backend("detached")
    paths.project_config_path("acme").write_text("plane_api_key: K\n")
    captured = {}
    class FakeProc:
        pid = 4321
    def fake_spawn(cmd, **kw):
        captured["cmd"] = cmd; captured["cwd"] = kw.get("cwd"); captured["env"] = kw.get("env")
        captured["new_session"] = kw.get("start_new_session")
        return FakeProc()
    supervisor._detached_start("acme", tmp_path / "repo",
                               {"PLANE_API_KEY": "K"}, spawn=fake_spawn)
    import sys
    assert sys.executable in captured["cmd"] and "-m" in captured["cmd"] and "orchestrator" in captured["cmd"]
    assert captured["cwd"] == str(tmp_path / "repo")
    assert captured["env"]["PLANE_API_KEY"] == "K"
    assert captured["new_session"] is True
    assert supervisor._pid_path("acme").read_text().strip() == "4321"


def test_start_dispatches_to_detached(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    paths.ensure_dirs(); paths.set_backend("detached")
    called = {}
    monkeypatch.setattr(supervisor, "_detached_start",
                        lambda p, r, e, **kw: called.setdefault("detached", True))
    monkeypatch.setattr(supervisor, "_tmux_start",
                        lambda *a, **kw: called.setdefault("tmux", True))
    supervisor.start("acme", tmp_path / "repo", {"PLANE_API_KEY": "K"})
    assert called == {"detached": True}


def test_logs_command_detached_is_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    paths.ensure_dirs(); paths.set_backend("detached")
    assert supervisor.logs_command("acme", follow=True)[0] == "tail"
