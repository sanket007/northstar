import importlib


def test_assets_resolve_with_override(tmp_path, monkeypatch):
    (tmp_path / "templates").mkdir()
    (tmp_path / "plane-mcp.json").write_text("{}")
    monkeypatch.setenv("NORTHSTAR_ASSETS_DIR", str(tmp_path))
    import northstar.assets as a
    a = importlib.reload(a)
    assert a.templates_dir() == tmp_path / "templates"
    assert a.plane_mcp_json() == tmp_path / "plane-mcp.json"
    dest = tmp_path / "dest"
    dest.mkdir()
    out = a.copy_plane_mcp_to(dest)
    assert out.exists() and out.read_text() == "{}"


def test_assets_root_defaults_to_repo(monkeypatch):
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.assets as a
    a = importlib.reload(a)
    # the repo root (package parent) must contain the templates dir
    assert (a.assets_root() / "templates").is_dir()
