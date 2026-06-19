from __future__ import annotations
import os
import subprocess

from northstar import paths, assets


def build_import_command(claude_binary, mcp_config_path, importer_doc_text,
                         plan_path, project_id) -> list[str]:
    initial = (
        f"Import the plan at {plan_path} into Plane project {project_id}. "
        "Follow your plane-importer instructions: first grill the entire plan with me to resolve "
        "every ambiguity, then create Draft tasks with acceptance criteria, citations, and "
        "blocked_by dependency relations."
    )
    return [
        claude_binary,
        "--mcp-config", str(mcp_config_path),
        "--append-system-prompt", importer_doc_text,
        initial,
    ]


def run_import(name, plan_path, *, runner=subprocess.run) -> None:
    rt = paths.load_project(name)
    doc = (assets.templates_dir() / "plane-importer.md").read_text()
    mcp = rt.cfg.get("mcp_config_path") or str(paths.home() / "plane-mcp.json")
    claude_binary = rt.cfg.get("claude_binary", "claude")
    project_id = rt.cfg.get("plane_project_id", "")
    cmd = build_import_command(claude_binary, mcp, doc, plan_path, project_id)
    env = {**os.environ, **rt.plane_env}
    runner(cmd, cwd=str(rt.repo_dir), env=env)
