from __future__ import annotations
from typing import Callable

from orchestrator.config import Config
from orchestrator.plane import Issue
from orchestrator.poller import Ownership
from orchestrator.worktree import create_worktree, remove_worktree
from orchestrator.launcher import run_session


def make_dispatch(cfg: Config, ownership: Ownership, *, run=run_session,
                  mk_worktree=create_worktree, rm_worktree=remove_worktree
                  ) -> Callable[[Issue, str], None]:
    def dispatch(issue: Issue, role: str) -> None:
        slug = f"{issue.sequence_id}-{role}"
        worktree = None
        try:
            worktree = mk_worktree(cfg.repo_dir, cfg.worktrees_root, slug)
            run(cfg, role, issue.id, worktree)
        finally:
            try:
                if worktree is not None:
                    rm_worktree(cfg.repo_dir, worktree)
            finally:
                ownership.release(issue.id)
    return dispatch
