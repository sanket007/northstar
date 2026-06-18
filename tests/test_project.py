import json
import importlib
from pathlib import Path
from northstar.proc import CommandResult
from northstar import project


def _inputs(tmp_path):
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    return project.ProjectInputs(
        name="acme", plane_base_url="https://plane.x", plane_api_key="k",
        plane_workspace_slug="w", plane_project_id="p", github_repo="o/acme",
        repo_dir=repo, lint_cmd="make lint", build_cmd="make build", test_cmd="make test")


class FakePlane:
    def __init__(self, *a, **k): pass
    def list_states(self):
        return {"Ready to Dev": "s1", "QA": "s2", "Blocked": "s3"}


def test_discover_state_ids(tmp_path):
    ids = project.discover_state_ids(_inputs(tmp_path), client=FakePlane())
    assert ids["QA"] == "s2"


def test_add_project_links_existing_writes_config_and_registers(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    from northstar.proc import CommandResult
    runner = lambda cmd, **kw: CommandResult(0, "", "")   # gh repo view ok => exists
    inp = _inputs(tmp_path)
    meta = project.add_project(inp, runner=runner, client=FakePlane())
    cfg = paths.project_config_path("acme")
    assert cfg.exists()
    import yaml
    data = yaml.safe_load(cfg.read_text())
    assert data["github_repo"] == "o/acme"
    assert data["state_ids"]["QA"] == "s2"
    assert "acme" in paths.list_projects()
    assert meta["github_repo"] == "o/acme"


def test_detect_build_commands_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"lint": "eslint .", "build": "tsc", "test": "vitest run"}}))
    cmds = project.detect_build_commands(tmp_path)
    assert cmds == {"lint": "npm run lint", "build": "npm run build", "test": "npm test"}


def test_detect_build_commands_empty_when_absent(tmp_path):
    assert project.detect_build_commands(tmp_path) == {}


def test_repo_exists_uses_gh(monkeypatch):
    calls = []
    def runner(cmd, **kw):
        calls.append(" ".join(cmd))
        return CommandResult(0, "", "")
    assert project.repo_exists("o/acme", runner=runner) is True
    assert "gh repo view o/acme" in calls[0]


def test_add_project_aborts_when_gh_unauthenticated(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    import pytest
    def runner(cmd, **kw):
        if "auth" in cmd:
            return CommandResult(1, "", "")
        return CommandResult(0, "", "")
    inp = _inputs(tmp_path)
    with pytest.raises(RuntimeError, match="GitHub not reachable"):
        project.add_project(inp, runner=runner, client=FakePlane())


def test_add_project_clones_existing_repo_when_not_local(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    # repo_dir that does NOT exist yet
    repo_dir = tmp_path / "nonexistent_repo"
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://plane.x", plane_api_key="k",
        plane_workspace_slug="w", plane_project_id="p", github_repo="o/acme",
        repo_dir=repo_dir, lint_cmd="make lint", build_cmd="make build", test_cmd="make test")
    recorded = []
    def runner(cmd, **kw):
        recorded.append(list(cmd))
        # After clone is called, create the repo dir so install_guardrails succeeds
        if cmd[0] == "gh" and "clone" in cmd:
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
        return CommandResult(0, "", "")
    project.add_project(inp, runner=runner, client=FakePlane())
    clone_calls = [c for c in recorded if len(c) >= 2 and c[0] == "gh" and "clone" in c]
    assert clone_calls, "expected a gh repo clone command to be issued"
    assert any("o/acme" in c for c in clone_calls[0])


def test_install_guardrails_writes_settings_hook_and_claude(tmp_path, monkeypatch):
    # point assets at the real repo templates
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    project.install_guardrails(repo, "acme", "make lint", "make build", "make test")
    settings = (repo / ".claude" / "settings.json").read_text()
    assert "make lint" in settings and "make test" in settings
    assert (repo / ".claude" / "hooks" / "precommit_gate.sh").exists()
    claude_md = (repo / "CLAUDE.md").read_text()
    assert "acme" in claude_md
