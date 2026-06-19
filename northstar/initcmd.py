from __future__ import annotations

from northstar.proc import run
from northstar.doctor import run_checks, all_critical_ok
from northstar.skills import install_all
from northstar import paths
from northstar.assets import copy_plane_mcp_to


def do_init(runner=run, deep=False, backend="tmux") -> int:
    print("northstar init")
    print("• Checking prerequisites…")
    checks = run_checks(runner=runner, deep=deep)
    if not all_critical_ok(checks):
        failed = [c for c in checks if c.critical and not c.ok]
        print("Cannot init — fix these first:")
        for c in failed:
            print(f"  ✗ {c.name}: {c.detail} — {c.fix}")
        return 1
    print(f"• Creating {paths.home()} and copying the Plane MCP config…")
    paths.ensure_dirs()
    copy_plane_mcp_to(paths.home())
    paths.set_backend(backend)
    print(f"• Process backend: {backend}")
    print("• Installing the skill stack to latest (this can take a minute)…")
    results = install_all(runner=runner)
    ok = sum(1 for _, o, _ in results if o)
    failed = [name for name, o, _ in results if not o]
    print(f"• Done — {ok}/{len(results)} skills ready; backend: {backend}.")
    if failed:
        print(f"  WARN needs attention: {', '.join(failed)} (see the lines above).")
    return 0
