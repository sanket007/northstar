from __future__ import annotations

DRAFT = "Draft"
READY_TO_DEV = "Ready to Dev"
IN_PROGRESS = "In Progress"
REVIEW = "Review"
QA = "QA"
COMPLETED = "Completed"
BLOCKED = "Blocked"
DEPLOYED = "Deployed"

# Which session role acts on a ticket sitting in this state.
_ROLE_FOR_STATE = {
    READY_TO_DEV: "builder",
    IN_PROGRESS: "builder",
    REVIEW: "reviewer",
    QA: "qa",
}

_ALLOWED = {
    READY_TO_DEV: {IN_PROGRESS, BLOCKED},
    IN_PROGRESS: {BLOCKED, REVIEW},
    REVIEW: {IN_PROGRESS, QA},
    QA: {IN_PROGRESS, COMPLETED},
    BLOCKED: {READY_TO_DEV},
}


def role_for_state(name: str) -> str | None:
    return _ROLE_FOR_STATE.get(name)


def is_allowed(frm: str, to: str) -> bool:
    return to in _ALLOWED.get(frm, set())
