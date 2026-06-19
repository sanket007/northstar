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


def test_load_project_reads_each_file_once(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    p.register_project("acme", {"github_repo": "o/acme", "repo_dir": "/tmp/acme"})
    p.project_config_path("acme").write_text(
        "plane_api_key: K\nplane_base_url: https://x\nplane_workspace_slug: w\nrepo_dir: /tmp/acme\n")
    rt = p.load_project("acme")
    assert rt.repo_dir == __import__("pathlib").Path("/tmp/acme")
    assert rt.plane_env == {"PLANE_API_KEY": "K", "PLANE_BASE_URL": "https://x", "PLANE_WORKSPACE_SLUG": "w"}
    assert rt.cfg_path == p.project_config_path("acme")


def test_backend_default_and_roundtrip(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    assert p.get_backend() == "tmux"          # default when unset
    p.set_backend("detached")
    assert p.get_backend() == "detached"
    assert p.machine_config_path() == p.home() / "config.yaml"
