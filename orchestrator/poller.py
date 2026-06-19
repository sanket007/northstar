from __future__ import annotations
from threading import Lock
import sys
import time

from orchestrator.config import Config
from orchestrator.state_machine import role_for_state, READY_TO_DEV


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
    i = 0
    while max_iterations is None or i < max_iterations:
        try:
            poll_once(client, cfg, ownership, dispatch)
        except Exception as e:  # noqa: BLE001 — daemon must survive transient errors
            print(f"northstar: poll error: {e}", file=sys.stderr)
        sleep(cfg.poll_interval_seconds)
        i += 1
