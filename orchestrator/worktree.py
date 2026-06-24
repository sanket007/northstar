from __future__ import annotations
from pathlib import Path
import shutil
import subprocess

from orchestrator import obs


def _git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(repo_dir), *args]
    started = obs.exec_start(cmd)
    proc = subprocess.run(cmd)
    obs.exec_done(started, proc.returncode)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc


def create_worktree(repo_dir: Path, worktrees_root: Path, slug: str,
                    base_branch: str = "main") -> Path:
    worktrees_root.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_root / slug
    branch = f"agent/{slug}"
    # Self-heal a leftover worktree at this path (a prior session that died before cleanup),
    # otherwise `git worktree add` fails with "already exists" (exit 128) on every retry.
    _git(repo_dir, "worktree", "remove", "--force", str(wt_path), check=False)
    _git(repo_dir, "worktree", "prune", check=False)
    if wt_path.exists():
        shutil.rmtree(wt_path, ignore_errors=True)
    # Branch from the latest trunk so a sibling task that merged while we were queued
    # doesn't leave us building on a stale base. Fall back to local HEAD when there's
    # no remote (e.g. a brand-new local-only repo) so we never hard-fail on fetch.
    fetched = _git(repo_dir, "fetch", "origin", base_branch, check=False).returncode == 0
    if fetched:
        _git(repo_dir, "worktree", "add", "-B", branch, str(wt_path), f"origin/{base_branch}")
    else:
        obs.info("git", f"no origin/{base_branch} to fetch; branching {branch} from local HEAD")
        _git(repo_dir, "worktree", "add", "-B", branch, str(wt_path))
    return wt_path


def remove_worktree(repo_dir: Path, worktree_path: Path) -> None:
    _git(repo_dir, "worktree", "remove", "--force", str(worktree_path))
    _git(repo_dir, "worktree", "prune")
