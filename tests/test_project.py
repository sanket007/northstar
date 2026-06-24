import json
import importlib
from pathlib import Path
from northstar.proc import CommandResult
from northstar import project
from northstar.plane_admin import CANONICAL_ORDER


def _inputs(tmp_path):
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    return project.ProjectInputs(
        name="acme", plane_base_url="https://plane.x", plane_api_key="k",
        plane_workspace_slug="w", plane_project_id="p", github_repo="o/acme",
        repo_dir=repo, lint_cmd="make lint", build_cmd="make build", test_cmd="make test")


class FakeAdmin:
    def __init__(self):
        self.created = None
        self.ensured = None

    def create_project(self, name, identifier, description=""):
        self.created = (name, identifier)
        return {"id": "newproj"}

    def ensure_board(self, project_id, *, fresh):
        self.ensured = (project_id, fresh)
        return {n: f"sid-{n}" for n in CANONICAL_ORDER}


def test_add_project_existing_runs_ensure_board_and_writes_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    from northstar.proc import CommandResult
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/acme", repo_dir=repo,
        lint_cmd="make lint", build_cmd="make build", test_cmd="make test")
    admin = FakeAdmin()
    runner = lambda cmd, **kw: CommandResult(0, "", "")  # gh ok everywhere
    meta = project.add_project(inp, runner=runner, admin=admin)
    assert admin.ensured == ("p", False)        # existing -> fresh False, project id "p"
    import yaml
    data = yaml.safe_load(paths.project_config_path("acme").read_text())
    assert data["plane_project_id"] == "p"
    assert data["state_ids"]["QA"] == "sid-QA"
    assert "acme" in paths.list_projects()


def test_add_project_new_creates_plane_project_then_ensures_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    from northstar.proc import CommandResult
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="", github_repo="o/acme", repo_dir=repo,
        lint_cmd="l", build_cmd="b", test_cmd="t",
        plane_new_project=True, plane_project_name="Acme", plane_identifier="ACME")
    admin = FakeAdmin()
    project.add_project(inp, runner=lambda c, **k: CommandResult(0, "", ""), admin=admin)
    assert admin.created == ("Acme", "ACME")
    assert admin.ensured == ("newproj", True)   # new -> fresh True, id from create_project
    data = __import__("yaml").safe_load(paths.project_config_path("acme").read_text())
    assert data["plane_project_id"] == "newproj"


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
        project.add_project(inp, runner=runner, admin=FakeAdmin())


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
    project.add_project(inp, runner=runner, admin=FakeAdmin())
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


def test_add_project_enforces_formatting_for_detected_language(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/acme", repo_dir=repo,
        lint_cmd="make lint", build_cmd="make build", test_cmd="make test",
        enforce_formatting=True)
    meta = project.add_project(inp, runner=lambda cmd, **kw: CommandResult(0, "", ""),
                               admin=FakeAdmin())
    assert meta["formatting"] == "python"
    assert (repo / "ruff.toml").exists()
    # format+lint is folded into the COMMIT gate (the hook); verify_cmd stays off by default
    hook = json.loads((repo / ".claude" / "settings.json").read_text())
    cmd = hook["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "ruff check" in cmd
    import yaml
    data = yaml.safe_load(paths.project_config_path("acme").read_text())
    assert data["verify_cmd"] is None             # not auto-populated (avoids false-RED)


def test_add_project_skips_formatting_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/acme", repo_dir=repo,
        lint_cmd="make lint", build_cmd="make build", test_cmd="make test",
        enforce_formatting=False)
    meta = project.add_project(inp, runner=lambda cmd, **kw: CommandResult(0, "", ""),
                               admin=FakeAdmin())
    assert meta["formatting"] is None
    assert not (repo / "ruff.toml").exists()
    import yaml
    data = yaml.safe_load(paths.project_config_path("acme").read_text())
    assert data["verify_cmd"] is None


def test_write_plane_mcp_bakes_literal_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    p = project.write_plane_mcp("acme", {
        "PLANE_API_KEY": "secret-key", "PLANE_BASE_URL": "https://plane.x",
        "PLANE_WORKSPACE_SLUG": "w"})
    data = json.loads(p.read_text())
    server = data["mcpServers"]["plane"]
    assert server["command"] == "uvx" and server["args"] == ["plane-mcp-server", "stdio"]
    assert server["env"]["PLANE_API_KEY"] == "secret-key"      # literal, not a placeholder
    assert "${" not in json.dumps(data)                         # no unexpanded ${VAR}


def test_add_project_points_at_literal_per_project_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/acme", repo_dir=repo,
        lint_cmd="make lint", build_cmd="make build", test_cmd="make test",
        enforce_formatting=False)
    project.add_project(inp, runner=lambda cmd, **kw: CommandResult(0, "", ""), admin=FakeAdmin())
    import yaml
    data = yaml.safe_load(paths.project_config_path("acme").read_text())
    mcp_path = data["mcp_config_path"]
    assert mcp_path.endswith("mcp/acme.json")                  # per-project, not the shared one
    mcp = json.loads(open(mcp_path).read())
    assert mcp["mcpServers"]["plane"]["env"]["PLANE_API_KEY"] == "k"
