from __future__ import annotations
from pathlib import Path
import json
import os
import shutil
import stat

from northstar.proc import run
from northstar.assets import templates_dir


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
