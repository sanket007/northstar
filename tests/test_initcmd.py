import importlib
from northstar.proc import CommandResult
from northstar.doctor import Check


def test_do_init_aborts_when_critical_check_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.initcmd as initcmd
    initcmd = importlib.reload(initcmd)
    monkeypatch.setattr(initcmd, "run_checks",
                        lambda runner, deep=False: [Check("tmux", False, True, "missing", "install tmux")])
    called = {"install": False}
    monkeypatch.setattr(initcmd, "install_all",
                        lambda runner: called.__setitem__("install", True) or [])
    rc = initcmd.do_init(runner=lambda *a, **k: CommandResult(0, "", ""))
    assert rc != 0
    assert called["install"] is False


def test_do_init_installs_and_creates_dirs_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.setenv("NORTHSTAR_ASSETS_DIR", str(tmp_path / "assets"))
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "templates").mkdir()
    (tmp_path / "assets" / "plane-mcp.json").write_text("{}")
    import northstar.initcmd as initcmd
    initcmd = importlib.reload(initcmd)
    monkeypatch.setattr(initcmd, "run_checks",
                        lambda runner, deep=False: [Check("git", True, True, "ok", "")])
    monkeypatch.setattr(initcmd, "install_all", lambda runner: [("superpowers", True, "plugin")])
    rc = initcmd.do_init(runner=lambda *a, **k: CommandResult(0, "", ""))
    assert rc == 0
    import northstar.paths as paths
    assert paths.home().is_dir()
    assert (paths.home() / "plane-mcp.json").exists()
