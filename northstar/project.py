from __future__ import annotations
from pathlib import Path
import json
import shutil
import stat
from dataclasses import dataclass, field

import yaml
from northstar.plane_admin import PlaneAdmin
from northstar.proc import run
from northstar.assets import templates_dir
from northstar import paths, formatting


def detect_build_commands(repo_dir: Path) -> dict:
    pkg = Path(repo_dir) / "package.json"
    if not pkg.exists():
        return {}
    try:
        scripts = json.loads(pkg.read_text()).get("scripts", {})
    except json.JSONDecodeError:
        return {}
    out = {}
    if "lint" in scripts:
        out["lint"] = "npm run lint"
    if "build" in scripts:
        out["build"] = "npm run build"
    if "test" in scripts:
        out["test"] = "npm test"
    return out


def repo_exists(github_repo: str, runner=run) -> bool:
    return runner(["gh", "repo", "view", github_repo]).ok


def create_repo(github_repo: str, repo_dir: Path, runner=run) -> None:
    runner(["gh", "repo", "create", github_repo, "--private", "--clone", str(repo_dir)])
    repo_dir = Path(repo_dir)
    (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
    readme = repo_dir / "README.md"
    if not readme.exists():
        readme.write_text(f"# {github_repo}\n")


def install_guardrails(repo_dir: Path, project_name: str,
                       lint_cmd: str, build_cmd: str, test_cmd: str) -> None:
    repo_dir = Path(repo_dir)
    tdir = templates_dir()
    claude_dir = repo_dir / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # settings.json with the project's build commands injected into the hook env
    settings = json.loads((tdir / "claude-settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    hook["command"] = (
        f'LINT_CMD="{lint_cmd}" BUILD_CMD="{build_cmd}" TEST_CMD="{test_cmd}" '
        '$CLAUDE_PROJECT_DIR/.claude/hooks/precommit_gate.sh'
    )
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # gate script (executable)
    gate = hooks_dir / "precommit_gate.sh"
    shutil.copyfile(tdir / "hooks" / "precommit_gate.sh", gate)
    gate.chmod(gate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # CLAUDE.md with the project name substituted
    tmpl = (tdir / "CLAUDE.md.tmpl").read_text()
    (repo_dir / "CLAUDE.md").write_text(tmpl.replace("{{PROJECT_NAME}}", project_name))


@dataclass
class ProjectInputs:
    name: str
    plane_base_url: str
    plane_api_key: str
    plane_workspace_slug: str
    plane_project_id: str
    github_repo: str
    repo_dir: Path
    lint_cmd: str
    build_cmd: str
    test_cmd: str
    claude_model: str = "claude-opus-4-8"
    poll_interval_seconds: int = 30
    max_concurrency: int = 1
    base_branch: str = "main"
    max_reworks: int = 3
    max_turns: int = 200
    # auto-continue count after a session hits max_turns (each is a fresh, context-reset
    # session that resumes from the branch); the throughput dial, raise freely
    max_turn_retries: int = 4
    # wall-clock kill per session
    session_timeout_seconds: int = 1800
    # work-type labels whose tickets skip the reviewer session (orchestrator auto-advances
    # Review -> QA). The importer tags each task with a work type; these are the low-risk ones.
    skip_review_labels: list = field(default_factory=lambda: ["docs", "chore"])
    # Optional self-contained trunk-health command run after each merge (must install deps
    # itself). Off by default — QA already verifies on the integrated branch before merging.
    verify_cmd: str | None = None
    enforce_formatting: bool = True
    plane_new_project: bool = False
    plane_project_name: str = ""
    plane_identifier: str = ""


def write_plane_mcp(name: str, plane_env: dict) -> Path:
    """Write a per-project Plane MCP config with literal credentials.

    Claude Code does not reliably expand ${VAR} placeholders into a spawned MCP server's
    environment, so we bake the real values in. Lives at ~/.northstar/mcp/<name>.json — same
    trust level as the project config, which already stores the API key in plaintext.
    """
    cfg = {
        "mcpServers": {
            "plane": {
                "command": "uvx",
                "args": ["plane-mcp-server", "stdio"],
                "env": {
                    "PLANE_API_KEY": plane_env.get("PLANE_API_KEY", ""),
                    "PLANE_WORKSPACE_SLUG": plane_env.get("PLANE_WORKSPACE_SLUG", ""),
                    # strip trailing slash so the server doesn't build //api/... URLs
                    "PLANE_BASE_URL": plane_env.get("PLANE_BASE_URL", "").rstrip("/"),
                },
            }
        }
    }
    out = paths.plane_mcp_path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, indent=2))
    return out


def write_project_config(inp: "ProjectInputs", state_ids: dict, mcp_path: Path,
                         project_id: str) -> Path:
    cfg = {
        "plane_base_url": inp.plane_base_url,
        "plane_api_key": inp.plane_api_key,
        "plane_workspace_slug": inp.plane_workspace_slug,
        "plane_project_id": project_id,
        "github_repo": inp.github_repo,
        "repo_dir": str(inp.repo_dir),
        "worktrees_root": str(paths.home() / "worktrees" / inp.name),
        "poll_interval_seconds": inp.poll_interval_seconds,
        "claude_binary": "claude",
        "claude_model": inp.claude_model,
        "mcp_config_path": str(mcp_path),
        "templates_dir": str(templates_dir()),
        "max_concurrency": inp.max_concurrency,
        "max_turns": inp.max_turns,
        "max_turn_retries": inp.max_turn_retries,
        "session_timeout_seconds": inp.session_timeout_seconds,
        "skip_review_labels": list(inp.skip_review_labels),
        "base_branch": inp.base_branch,
        # Off by default: the bare lint/build/test gate is not self-contained (no dep install
        # in a fresh worktree) and false-fails. QA verifies on the integrated branch pre-merge.
        # Set a self-contained command here (that installs deps) to enable the post-merge check.
        "verify_cmd": inp.verify_cmd,
        "max_reworks": inp.max_reworks,
        "state_ids": state_ids,
    }
    out = paths.project_config_path(inp.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=True))
    return out


def add_project(inp: "ProjectInputs", *, runner=run, create_if_missing=False, admin=None) -> dict:
    if not runner(["gh", "auth", "status"]).ok:
        raise RuntimeError("GitHub not reachable — run: gh auth login")

    admin = admin or PlaneAdmin(inp.plane_base_url, inp.plane_api_key, inp.plane_workspace_slug)
    if inp.plane_new_project:
        project_id = admin.create_project(inp.plane_project_name, inp.plane_identifier)["id"]
        fresh = True
    else:
        project_id = inp.plane_project_id
        fresh = False
    state_ids = admin.ensure_board(project_id, fresh=fresh)

    if not repo_exists(inp.github_repo, runner=runner):
        if not create_if_missing:
            raise RuntimeError(
                f"repo {inp.github_repo} not found; pass create_if_missing=True to create it")
        create_repo(inp.github_repo, inp.repo_dir, runner=runner)
    else:
        if not Path(inp.repo_dir).exists():
            runner(["gh", "repo", "clone", inp.github_repo, str(inp.repo_dir)])

    # Optionally impose strong formatting/lint rules for the detected language, folding the
    # format+lint check into the lint gate so commits + trunk-health enforce it.
    lint_cmd = inp.lint_cmd
    fmt_language = None
    if inp.enforce_formatting:
        fmt_language = formatting.detect_language(inp.repo_dir)
        if fmt_language:
            spec = formatting.install_formatting(inp.repo_dir, fmt_language, runner=runner)
            lint_cmd = f"{spec.check_cmd} && {inp.lint_cmd}" if inp.lint_cmd else spec.check_cmd

    install_guardrails(inp.repo_dir, inp.name, lint_cmd, inp.build_cmd, inp.test_cmd)
    # Per-project MCP config with literal Plane creds (Claude doesn't expand ${VAR} into
    # a spawned server's env), so the Plane MCP server is reachable in plan import + sessions.
    mcp_path = write_plane_mcp(inp.name, {
        "PLANE_API_KEY": inp.plane_api_key,
        "PLANE_BASE_URL": inp.plane_base_url,
        "PLANE_WORKSPACE_SLUG": inp.plane_workspace_slug,
    })
    write_project_config(inp, state_ids, mcp_path, project_id)
    meta = {"github_repo": inp.github_repo, "repo_dir": str(inp.repo_dir),
            "plane_project_id": project_id, "formatting": fmt_language}
    paths.register_project(inp.name, meta)
    return meta
