import importlib
from pathlib import Path


def reload_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as p
    return importlib.reload(p)


def test_ensure_dirs_creates_layout(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    assert p.home().is_dir()
    assert p.projects_dir().is_dir()
    assert p.logs_dir().is_dir()
    assert p.project_config_path("acme") == p.projects_dir() / "acme.yaml"
    assert p.log_path("acme") == p.logs_dir() / "acme.log"


def test_register_and_list_roundtrip(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    p.register_project("acme", {"github_repo": "o/acme", "repo_dir": "/tmp/acme"})
    assert p.list_projects()["acme"]["github_repo"] == "o/acme"
    p.register_project("beta", {"github_repo": "o/beta"})
    assert set(p.list_projects()) == {"acme", "beta"}
    p.unregister_project("acme")
    assert set(p.list_projects()) == {"beta"}
