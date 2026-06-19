"""Post-merge trunk health check.

After QA squash-merges a PR, the orchestrator independently confirms that `main` is still
green — protecting the trunk against semantic conflicts that unit tests on a stale branch
could not have caught. The check runs the project's verify command in a throwaway worktree
cut from the freshly-fetched trunk, so it never disturbs the main checkout or any in-flight
agent worktree.
"""
from __future__ import annotations
import subprocess

from orchestrator import obs
from orchestrator.worktree import create_worktree, remove_worktree


def _run_shell(cmd: str, cwd: str) -> tuple[bool, str]:
    started = obs.exec_start(cmd, shell=True)
    proc = subprocess.run(cmd, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True)
    obs.exec_done(started, proc.returncode)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()


def verify_main(cfg, *, mk_worktree=create_worktree, rm_worktree=remove_worktree,
                run_shell=_run_shell) -> tuple[bool, str]:
    """Return (ok, detail). ok=True (skipped) when no verify_cmd is configured."""
    if not getattr(cfg, "verify_cmd", None):
        return True, "skipped (no verify_cmd configured)"
    wt = mk_worktree(cfg.repo_dir, cfg.worktrees_root, "main-health", cfg.base_branch)
    try:
        ok, out = run_shell(cfg.verify_cmd, str(wt))
        return ok, out[-800:]  # tail is where the failure is
    finally:
        try:
            rm_worktree(cfg.repo_dir, wt)
        except Exception:
            pass
