import importlib
from typer.testing import CliRunner
from northstar.doctor import Check

runner = CliRunner()


def test_doctor_command_exit_code_reflects_critical(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    monkeypatch.setattr(cli.doctor, "run_checks",
                        lambda runner=None, deep=False: [Check("git", False, True, "missing", "install git")])
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "git" in result.stdout


def test_init_command_invokes_do_init(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli, "do_init", lambda deep=False: seen.setdefault("deep", deep) or 0)
    result = runner.invoke(cli.app, ["init"])
    assert result.exit_code == 0
    assert seen["deep"] is False


def test_status_lists_registered_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    paths.ensure_dirs(); paths.register_project("acme", {"github_repo": "o/acme"})
    import northstar.cli as cli; importlib.reload(cli)
    monkeypatch.setattr(cli.supervisor, "status",
                        lambda names, runner=None: [{"name": "acme", "running": True}])
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "acme" in result.stdout


def test_project_add_new_plane_project_builds_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.cli as cli; importlib.reload(cli)
    captured = {}
    def fake_add(inp, **kw):
        captured["inp"] = inp
        return {"github_repo": inp.github_repo}
    monkeypatch.setattr(cli.project, "add_project", fake_add)
    result = runner.invoke(cli.app, [
        "project", "add",
        "--name", "acme", "--plane-base-url", "https://x", "--plane-api-key", "k",
        "--plane-workspace-slug", "w", "--github-repo", "o/acme",
        "--repo-dir", str(tmp_path / "repo"),
        "--lint-cmd", "l", "--build-cmd", "b", "--test-cmd", "t",
        "--new-plane-project", "--plane-project-name", "Acme", "--plane-identifier", "ACME",
    ])
    assert result.exit_code == 0
    inp = captured["inp"]
    assert inp.plane_new_project is True
    assert inp.plane_project_name == "Acme" and inp.plane_identifier == "ACME"


def test_start_uses_load_project(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    paths.ensure_dirs(); paths.register_project("acme", {"repo_dir": str(tmp_path / "repo")})
    paths.project_config_path("acme").write_text(
        "plane_api_key: K\nplane_base_url: https://x\nplane_workspace_slug: w\nrepo_dir: " + str(tmp_path / "repo") + "\n")
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli.supervisor, "start",
                        lambda name, repo_dir, plane_env, **kw: seen.update(repo_dir=repo_dir, env=plane_env))
    result = runner.invoke(cli.app, ["start", "acme"])
    assert result.exit_code == 0
    assert seen["env"]["PLANE_API_KEY"] == "K"


def test_plan_import_command_invokes_run_import(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli.importer, "run_import",
                        lambda name, plan_path, **kw: seen.update(name=name, plan=plan_path))
    result = runner.invoke(cli.app, ["plan", "import", "acme", "plan.md"])
    assert result.exit_code == 0
    assert seen == {"name": "acme", "plan": "plan.md"}
