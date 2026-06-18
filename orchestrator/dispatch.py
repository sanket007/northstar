from __future__ import annotations
from typing import Callable

from orchestrator.config import Config
from orchestrator.plane import Issue, PlaneClient
from orchestrator.poller import Ownership
from orchestrator.worktree import create_worktree, remove_worktree
from orchestrator.launcher import run_session


def make_dispatch(cfg: Config, ownership: Ownership, *, run=run_session,
                  mk_worktree=create_worktree, rm_worktree=remove_worktree,
                  plane: PlaneClient | None = None) -> Callable[[Issue, str], None]:
    plane = plane or PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                                 cfg.plane_workspace_slug, cfg.plane_project_id)

    def dispatch(issue: Issue, role: str) -> None:
        slug = f"{issue.sequence_id}-{role}"
        worktree = None
        failure = None
        try:
            worktree = mk_worktree(cfg.repo_dir, cfg.worktrees_root, slug)
            result = run(cfg, role, issue.id, worktree)
            if result is None or not result.ok:
                failure = (result.error if result is not None
                           else "session returned no result")
        except Exception as e:  # noqa: BLE001 — daemon must never die on one task
            failure = f"dispatch error: {e}"
        finally:
            if worktree is not None:
                try:
                    rm_worktree(cfg.repo_dir, worktree)
                except Exception:
                    pass
            if failure is not None:
                try:
                    plane.add_comment(issue.id,
                                      f"\U0001f916 [orchestrator] → BLOCKED: {failure}")
                    plane.set_state(issue.id, cfg.state_ids["Blocked"])
                except Exception:
                    pass
            ownership.release(issue.id)
    return dispatch
