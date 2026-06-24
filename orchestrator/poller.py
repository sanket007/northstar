from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import sys
import time

from orchestrator.config import Config
from orchestrator.state_machine import role_for_state, READY_TO_DEV, REVIEW, QA

# Set by a dispatch when Claude reports a usage/session limit, so the daemon cools down
# instead of re-dispatching straight into the wall.
usage_limit_hit = Event()
_USAGE_LIMIT_COOLDOWN = 900  # seconds


class Ownership:
    def __init__(self):
        self._ids: set[str] = set()
        self._lock = Lock()

    def claim(self, ticket_id: str) -> None:
        with self._lock:
            self._ids.add(ticket_id)

    def release(self, ticket_id: str) -> None:
        with self._lock:
            self._ids.discard(ticket_id)

    def owns(self, ticket_id: str) -> bool:
        with self._lock:
            return ticket_id in self._ids

    def count(self) -> int:
        with self._lock:
            return len(self._ids)


# States that trigger a session, in priority order (finish work before starting new).
_ACTIONABLE_ORDER = ["QA", "Review", "In Progress", "Ready to Dev"]

_DONE_STATES = {"Completed", "Deployed"}


def dependencies_clear(client, cfg, issue, cache: dict | None = None) -> bool:
    blockers = client.list_blocked_by(issue.id)
    if not blockers:
        return True
    id_to_name = {v: k for k, v in cfg.state_ids.items()}
    cache = cache if cache is not None else {}
    for bid in blockers:
        if bid not in cache:
            cache[bid] = id_to_name.get(client.get_issue(bid).state_id)
        if cache[bid] not in _DONE_STATES:
            return False
    return True


def rework_count(comments) -> int:
    """How many times reviewer/QA bounced this ticket back to In Progress.

    Counts append-only trail comments authored by the reviewer or QA role that moved the
    ticket to In Progress. Robust to comment casing; ignores the builder's initial claim.
    """
    n = 0
    for c in comments:
        body = (getattr(c, "body_html", "") or "").lower()
        if ("[reviewer]" in body or "[qa]" in body) and "in progress" in body:
            n += 1
    return n


def _skip_review(cfg: Config, issue) -> bool:
    """A low-risk ticket (by work-type label) skips the reviewer session entirely."""
    return bool(set(cfg.skip_review_labels or []) & set(getattr(issue, "labels", []) or []))


def poll_once(client, cfg: Config, ownership: Ownership, dispatch) -> None:
    dep_cache: dict = {}
    for state_name in _ACTIONABLE_ORDER:
        if ownership.count() >= cfg.max_concurrency:
            return
        state_id = cfg.state_ids.get(state_name)
        if not state_id:
            continue
        role = role_for_state(state_name)
        if role is None:
            continue
        for issue in client.list_issues_in_state(state_id):
            if ownership.count() >= cfg.max_concurrency:
                return
            if ownership.owns(issue.id):
                continue
            if state_name == READY_TO_DEV and not dependencies_clear(client, cfg, issue, dep_cache):
                continue
            # Low-risk work-type: auto-advance Review -> QA, no reviewer session launched.
            qa_id = cfg.state_ids.get(QA)
            if state_name == REVIEW and qa_id and _skip_review(cfg, issue):
                client.set_state(issue.id, qa_id)
                client.add_comment(
                    issue.id,
                    "**[orchestrator] Review → QA** — auto-skipped review (low-risk work-type "
                    "label); proceeding to QA.")
                continue
            ownership.claim(issue.id)
            dispatch(issue, role)


def run(cfg: Config, *, client=None, dispatch=None, sleep=time.sleep,
        max_iterations=None) -> None:
    from orchestrator.plane import PlaneClient
    client = client or PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                                   cfg.plane_workspace_slug, cfg.plane_project_id)
    ownership = Ownership()
    if dispatch is None:
        from orchestrator.dispatch import make_dispatch
        dispatch = make_dispatch(cfg, ownership, plane=client)

    # Run dispatches concurrently up to max_concurrency. poll_once claims a ticket
    # (ownership) before submitting, so the count() gate never over-subscribes the pool;
    # each dispatch releases its claim when its session finishes (in its own thread).
    concurrency = max(1, cfg.max_concurrency)
    pool = ThreadPoolExecutor(max_workers=concurrency) if concurrency > 1 else None
    submit = (lambda issue, role: pool.submit(dispatch, issue, role)) if pool else dispatch

    i = 0
    try:
        while max_iterations is None or i < max_iterations:
            if usage_limit_hit.is_set():
                usage_limit_hit.clear()
                print("northstar: Claude usage limit hit — cooling down before retrying",
                      file=sys.stderr)
                sleep(getattr(cfg, "usage_limit_cooldown_seconds", _USAGE_LIMIT_COOLDOWN))
            try:
                poll_once(client, cfg, ownership, submit)
            except Exception as e:  # noqa: BLE001 — daemon must survive transient errors
                print(f"northstar: poll error: {e}", file=sys.stderr)
            sleep(cfg.poll_interval_seconds)
            i += 1
    finally:
        if pool:
            pool.shutdown(wait=True)
