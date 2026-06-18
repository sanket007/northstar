import json
from pathlib import Path
from northstar.proc import CommandResult
from northstar import project


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
