from __future__ import annotations
from dataclasses import dataclass
import sys

from northstar.proc import run


@dataclass
class Check:
    name: str
    ok: bool
    critical: bool
    detail: str
    fix: str


def _tool(runner, name, cmd, *, critical, fix) -> Check:
    res = runner(cmd)
    ok = res.ok
    detail = (res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else ""
    return Check(name, ok, critical, detail or ("missing" if not ok else ""), fix)


def run_checks(runner=run, deep=False) -> list[Check]:
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(Check("python>=3.11", py_ok, True,
                        ".".join(map(str, sys.version_info[:3])),
                        "install Python 3.11+"))

    checks.append(_tool(runner, "git", ["git", "--version"], critical=True,
                        fix="install git"))
    checks.append(_tool(runner, "gh", ["gh", "--version"], critical=True,
                        fix="install the GitHub CLI"))

    gh_auth = runner(["gh", "auth", "status"])
    checks.append(Check("github-auth", gh_auth.ok, True,
                        "reachable" if gh_auth.ok else "not authenticated",
                        "run: gh auth login"))

    checks.append(_tool(runner, "claude", ["claude", "--version"], critical=True,
                        fix="install Claude Code (claude.com/code)"))
    if deep:
        smoke = runner(["claude", "-p", "reply with OK", "--output-format", "json"])
        checks.append(Check("claude-smoke", smoke.ok, True,
                            "ok" if smoke.ok else "smoke run failed",
                            "check `claude` login/subscription"))

    checks.append(_tool(runner, "uvx", ["uvx", "--version"], critical=True,
                        fix="install uv (astral.sh/uv)"))
    checks.append(_tool(runner, "tmux", ["tmux", "-V"], critical=True,
                        fix="install tmux"))
    checks.append(_tool(runner, "npx", ["npx", "--version"], critical=False,
                        fix="install Node.js (needed for the grill-me skill)"))
    return checks


def all_critical_ok(checks) -> bool:
    return all(c.ok for c in checks if c.critical)
