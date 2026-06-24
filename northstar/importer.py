from __future__ import annotations
import os
import subprocess

from northstar import paths, assets
from orchestrator import obs


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
        "--dangerously-skip-permissions",
        "--mcp-config", str(mcp_config_path),
        # only the Plane server — ignore the user's personal MCP servers so it connects
        # fast without contention and never blocks on an unrelated server needing auth
        "--strict-mcp-config",
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
    # give the Plane MCP server room to start, and pass creds via env too (belt and suspenders)
    env = {**os.environ, **rt.plane_env, "MCP_TIMEOUT": "30000", "MCP_TOOL_TIMEOUT": "60000"}
    obs.info("import", f"launching interactive plan import for {name} from {plan_path}")
    runner(cmd, cwd=str(rt.repo_dir), env=env)
