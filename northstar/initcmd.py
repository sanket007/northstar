from __future__ import annotations

from northstar.proc import run
from northstar.doctor import run_checks, all_critical_ok
from northstar.skills import install_all
from northstar import paths
from northstar.assets import copy_plane_mcp_to


def do_init(runner=run, deep=False, backend="tmux") -> int:
    checks = run_checks(runner=runner, deep=deep)
    if not all_critical_ok(checks):
        failed = [c for c in checks if c.critical and not c.ok]
        print("Cannot init — fix these first:")
        for c in failed:
            print(f"  ✗ {c.name}: {c.detail} — {c.fix}")
        return 1
    paths.ensure_dirs()
    copy_plane_mcp_to(paths.home())
    paths.set_backend(backend)
    results = install_all(runner=runner)
    for name, ok, kind in results:
        print(f"  {'✓' if ok else '⚠'} {name} ({kind})")
    print(f"  process backend: {backend}")
    return 0
