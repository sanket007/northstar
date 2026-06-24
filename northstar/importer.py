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


def _launch(name, doc_name, build, *build_args):
    rt = paths.load_project(name)
    doc = (assets.templates_dir() / doc_name).read_text()
    mcp = rt.cfg.get("mcp_config_path") or str(paths.home() / "plane-mcp.json")
    claude_binary = rt.cfg.get("claude_binary", "claude")
    project_id = rt.cfg.get("plane_project_id", "")
    cmd = build(claude_binary, mcp, doc, *build_args, project_id)
    # give the Plane MCP server room to start, and pass creds via env too (belt and suspenders)
    env = {**os.environ, **rt.plane_env, "MCP_TIMEOUT": "30000", "MCP_TOOL_TIMEOUT": "60000"}
    return rt, cmd, env


def run_import(name, plan_path, *, runner=subprocess.run) -> None:
    rt, cmd, env = _launch(name, "plane-importer.md", build_import_command, plan_path)
    obs.info("import", f"launching interactive plan import for {name} from {plan_path}")
    runner(cmd, cwd=str(rt.repo_dir), env=env)


def build_relabel_command(claude_binary, mcp_config_path, relabeler_doc_text,
                          project_id) -> list[str]:
    initial = (
        f"Backfill work-type labels on the existing tickets in Plane project {project_id}. "
        "Follow your plane-relabeler instructions: list the work items, ensure the label set "
        "exists, and tag each unlabeled ticket with one of feature/bug/chore/docs. Read and "
        "label only — do not create, move, or grill tasks."
    )
    return [
        claude_binary,
        "--dangerously-skip-permissions",
        "--mcp-config", str(mcp_config_path),
        "--strict-mcp-config",
        "--append-system-prompt", relabeler_doc_text,
        initial,
    ]


def run_relabel(name, *, runner=subprocess.run) -> None:
    rt, cmd, env = _launch(name, "plane-relabeler.md", build_relabel_command)
    obs.info("relabel", f"launching work-type relabel pass for {name}")
    runner(cmd, cwd=str(rt.repo_dir), env=env)
