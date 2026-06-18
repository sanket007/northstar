from __future__ import annotations
from pathlib import Path
import subprocess


def _git(repo_dir: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo_dir), *args], check=True)


def create_worktree(repo_dir: Path, worktrees_root: Path, slug: str) -> Path:
    worktrees_root.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_root / slug
    branch = f"agent/{slug}"
    _git(repo_dir, "worktree", "add", "-B", branch, str(wt_path))
    return wt_path


def remove_worktree(repo_dir: Path, worktree_path: Path) -> None:
    _git(repo_dir, "worktree", "remove", "--force", str(worktree_path))
    _git(repo_dir, "worktree", "prune")
