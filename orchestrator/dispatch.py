from __future__ import annotations
from typing import Callable
import re
import threading

from orchestrator import obs
from orchestrator.config import Config
from orchestrator.plane import Issue, PlaneClient
from orchestrator.poller import Ownership, rework_count, usage_limit_hit
from orchestrator.worktree import create_worktree, remove_worktree
from orchestrator.launcher import run_session, PERSISTENT_ROLES
from orchestrator.health import verify_main

# marker for orchestrator-posted continuation notes after a session hit max_turns
_CONTINUE_MARKER = "continuing after reaching the turn limit"


def _continuations(comments) -> int:
    return sum(1 for c in comments
               if _CONTINUE_MARKER in (getattr(c, "body_html", "") or "").lower())


def _strip_html(html) -> str:
    return re.sub("<[^>]+>", "", html or "").strip()


# --- persistent per-ticket session markers (so later stages resume, not recreate) ---
def _session_marker(cfg: Config, ticket_id: str):
    return cfg.worktrees_root / ".sessions" / ticket_id


def _session_exists(cfg: Config, ticket_id: str) -> bool:
    return _session_marker(cfg, ticket_id).exists()


def _mark_session(cfg: Config, ticket_id: str) -> None:
    try:
        m = _session_marker(cfg, ticket_id)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.touch()
    except Exception:
        pass


def _latest_feedback(comments) -> str:
    """The most recent reviewer/QA comment (the bounce we must address), else the last comment."""
    for c in reversed(comments):
        b = getattr(c, "body_html", "") or ""
        if "[reviewer]" in b.lower() or "[qa]" in b.lower():
            return _strip_html(b)
    return _strip_html(getattr(comments[-1], "body_html", "")) if comments else ""


def _phase_instruction(role: str, comments) -> str:
    """Short next-phase message for a resumed (context-retained) session — no re-hydration."""
    if role == "qa":
        return ("QA phase. Your PR passed INDEPENDENT review. Verify each acceptance criterion from "
                "the outside (real behavior, not just unit tests), confirm the branch is current with "
                "origin and CI is green, then SAFELY merge the PR and move the ticket to Completed. If "
                "any criterion fails, move it back to In Progress with specifics instead of merging. "
                "Use Plane MCP only to write (move state, comment).")
    return ("Rework. Address the latest review feedback below: fix the code, rebase onto trunk and "
            "resolve ALL conflicts so the PR is mergeable, get every check green, push, then move the "
            "ticket back to Review. Never hand a conflicting PR onward. Latest feedback:\n\n"
            + _latest_feedback(comments))


def ticket_context(cfg: Config, issue, comments) -> str:
    """Pre-fetched ticket context for the session prompt, built from data we already hold —
    so the session needn't read Plane via MCP (those results bloat its context all session)."""
    id_to_name = {v: k for k, v in cfg.state_ids.items()}
    lines = [
        "## Ticket context (provided — do NOT re-fetch via Plane MCP; use Plane MCP only to WRITE:",
        "## update_work_item to move state, create_work_item_comment to comment)",
        f"- Work item id: {issue.id}",
        f"- Title: {issue.name}",
        f"- Current state: {id_to_name.get(issue.state_id, '?')}",
        "- State name -> id (for update_work_item transitions):",
    ]
    labels = getattr(issue, "labels", None)
    if labels:
        lines.insert(5, f"- Labels (work type): {', '.join(labels)}")
    lines += [f"    {n}: {i}" for n, i in cfg.state_ids.items()]
    desc = _strip_html(getattr(issue, "description_html", ""))
    if desc:
        lines += ["- Description / acceptance criteria:", desc[:2500]]
    recent = comments[-6:]
    if recent:
        lines.append("- Recent comments (oldest first):")
        lines += ["    • " + _strip_html(getattr(c, "body_html", "")).replace("\n", " ")[:300]
                  for c in recent]
    return "\n".join(lines)


def make_dispatch(cfg: Config, ownership: Ownership, *, run=run_session,
                  mk_worktree=create_worktree, rm_worktree=remove_worktree,
                  verify=verify_main,
                  plane: PlaneClient | None = None) -> Callable[[Issue, str], None]:
    plane = plane or PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                                 cfg.plane_workspace_slug, cfg.plane_project_id)
    # git worktree add/remove/prune mutate shared repo metadata — serialize them so
    # concurrent dispatches don't race on the same repo. Sessions still run in parallel.
    wt_lock = threading.Lock()

    def _block(issue_id: str, reason: str) -> None:
        try:
            plane.add_comment(issue_id, f"**[orchestrator] → Blocked** — {reason}")
            plane.set_state(issue_id, cfg.state_ids["Blocked"])
        except Exception:
            pass

    def dispatch(issue: Issue, role: str) -> None:
        # One read of the trail, used for both the rework cap and the max-turns retry count.
        try:
            comments = plane.list_comments(issue.id)
        except Exception:
            comments = []
        # Rework cap: a ticket that has thrashed through too many reviewer/QA bounces is
        # parked for a human instead of looping forever and burning the budget.
        rounds = rework_count(comments)
        if rounds >= cfg.max_reworks:
            obs.info("orchestrator", f"{issue.id}: {rounds} rework rounds ≥ cap; blocking")
            _block(issue.id, f"exceeded {cfg.max_reworks} rework rounds — needs human attention")
            ownership.release(issue.id)
            return

        slug = f"{issue.sequence_id}-{role}"
        # Builder + QA share one persistent session per ticket (context retained across stages);
        # resume it once it exists. Reviewer is always a fresh, independent session.
        persistent = role in PERSISTENT_ROLES
        resume = persistent and _session_exists(cfg, issue.id)
        instruction = _phase_instruction(role, comments) if resume else ""
        worktree = None
        failure = None
        try:
            with wt_lock:
                worktree = mk_worktree(cfg.repo_dir, cfg.worktrees_root, slug, cfg.base_branch)
            # On resume the context already lives in the session — don't re-inject it (that's the
            # whole point); a fresh session (create / reviewer) gets the pre-fetched ticket context.
            ctx = "" if resume else ticket_context(cfg, issue, comments)
            result = run(cfg, role, issue.id, worktree, context=ctx,
                         resume=resume, instruction=instruction)
            if persistent and not resume:
                _mark_session(cfg, issue.id)  # created now -> later stages resume this conversation
            if result is None or not result.ok:
                failure = (result.error if result is not None
                           else "session returned no result")
        except Exception as e:  # noqa: BLE001 — daemon must never die on one task
            failure = f"dispatch error: {e}"
        finally:
            if worktree is not None:
                try:
                    with wt_lock:
                        rm_worktree(cfg.repo_dir, worktree)
                except Exception:
                    pass
            if failure == "usage_limit":
                # Claude hit the plan's usage/session limit — the session did no work. Don't
                # block and don't loop; trip the daemon cooldown and leave the ticket as-is.
                obs.info("orchestrator", f"{issue.id}: Claude usage limit hit — pausing daemon "
                                         "(switch model or wait for reset)")
                usage_limit_hit.set()
            elif failure is not None:
                # A session that ran out of turns usually made progress — let it continue
                # in a fresh session (bounded), instead of blocking, since the next poll
                # re-picks the ticket up from its current state.
                if "max_turns" in failure and _continuations(comments) < cfg.max_turn_retries:
                    try:
                        plane.add_comment(
                            issue.id,
                            f"**[orchestrator] continuing after reaching the turn limit** — the "
                            f"{role} session hit max_turns ({cfg.max_turns}) with progress made; "
                            "re-queuing to continue where it left off.")
                    except Exception:
                        pass
                    obs.info("orchestrator", f"{issue.id}: max_turns — re-queuing to continue")
                else:
                    _block(issue.id, failure)
            elif role == "qa":
                # QA just merged — independently confirm trunk is still green.
                try:
                    ok, detail = verify(cfg)
                    if not ok:
                        obs.info("orchestrator", f"main RED after merging {issue.id}")
                        plane.add_comment(
                            issue.id,
                            "**[orchestrator] main is RED after this merge** — "
                            f"trunk verify failed:\n\n{detail}")
                except Exception as e:  # noqa: BLE001 — health check must not kill the daemon
                    obs.info("orchestrator", f"main-health check errored: {e}")
            ownership.release(issue.id)
    return dispatch
