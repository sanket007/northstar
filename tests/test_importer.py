from northstar import importer


def test_build_import_command():
    cmd = importer.build_import_command("claude", "/h/plane-mcp.json", "DOC TEXT", "plan.md", "proj1")
    assert cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in cmd
    assert "--mcp-config" in cmd and "/h/plane-mcp.json" in cmd
    assert "--append-system-prompt" in cmd and "DOC TEXT" in cmd
    initial = cmd[-1]
    assert "plan.md" in initial and "proj1" in initial


def test_run_import_uses_project_env_and_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib_reload(paths)
    paths.ensure_dirs(); paths.register_project("acme", {"repo_dir": str(tmp_path / "repo")})
    (tmp_path / "repo").mkdir()
    paths.project_config_path("acme").write_text(
        "plane_api_key: K\nplane_base_url: https://x\nplane_workspace_slug: w\n"
        "plane_project_id: proj1\nrepo_dir: " + str(tmp_path / "repo") +
        "\nmcp_config_path: /h/m.json\nclaude_binary: claude\n")
    seen = {}
    def fake_runner(cmd, **kw):
        seen["cmd"] = cmd; seen["cwd"] = kw.get("cwd"); seen["env"] = kw.get("env")
        class R: returncode = 0
        return R()
    importer.run_import("acme", "plan.md", runner=fake_runner)
    assert seen["cwd"] == str(tmp_path / "repo")
    assert seen["env"]["PLANE_API_KEY"] == "K" and seen["env"]["PLANE_BASE_URL"] == "https://x"
    assert "proj1" in seen["cmd"][-1]


def importlib_reload(m):
    import importlib; return importlib.reload(m)
