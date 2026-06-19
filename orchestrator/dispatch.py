from __future__ import annotations
from typing import Callable

from orchestrator import obs
from orchestrator.config import Config
from orchestrator.plane import Issue, PlaneClient
from orchestrator.poller import Ownership, rework_count
from orchestrator.worktree import create_worktree, remove_worktree
from orchestrator.launcher import run_session
from orchestrator.health import verify_main


def make_dispatch(cfg: Config, ownership: Ownership, *, run=run_session,
                  mk_worktree=create_worktree, rm_worktree=remove_worktree,
                  verify=verify_main,
                  plane: PlaneClient | None = None) -> Callable[[Issue, str], None]:
    plane = plane or PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                                 cfg.plane_workspace_slug, cfg.plane_project_id)

    def _block(issue_id: str, reason: str) -> None:
        try:
            plane.add_comment(issue_id, f"\U0001f916 [orchestrator] → BLOCKED: {reason}")
            plane.set_state(issue_id, cfg.state_ids["Blocked"])
        except Exception:
            pass

    def dispatch(issue: Issue, role: str) -> None:
        # Rework cap: a ticket that has thrashed through too many reviewer/QA bounces is
        # parked for a human instead of looping forever and burning the budget.
        try:
            rounds = rework_count(plane.list_comments(issue.id))
        except Exception:
            rounds = 0
        if rounds >= cfg.max_reworks:
            obs.info("orchestrator", f"{issue.id}: {rounds} rework rounds ≥ cap; blocking")
            _block(issue.id, f"exceeded {cfg.max_reworks} rework rounds — needs human attention")
            ownership.release(issue.id)
            return

        slug = f"{issue.sequence_id}-{role}"
        worktree = None
        failure = None
        try:
            worktree = mk_worktree(cfg.repo_dir, cfg.worktrees_root, slug, cfg.base_branch)
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
                _block(issue.id, failure)
            elif role == "qa":
                # QA just merged — independently confirm trunk is still green.
                try:
                    ok, detail = verify(cfg)
                    if not ok:
                        obs.info("orchestrator", f"main RED after merging {issue.id}")
                        plane.add_comment(
                            issue.id,
                            "\U0001f916 [orchestrator] ⚠ main is RED after this merge — "
                            f"trunk verify failed:\n{detail}")
                except Exception as e:  # noqa: BLE001 — health check must not kill the daemon
                    obs.info("orchestrator", f"main-health check errored: {e}")
            ownership.release(issue.id)
    return dispatch
