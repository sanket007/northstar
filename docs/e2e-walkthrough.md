# End-to-end walkthrough (the walk-away test)

Prereq: Task 13 sandbox is set up; `config.yaml` has real `state_ids`; the skill stack is
installed at user scope; `PLANE_API_KEY` / `PLANE_WORKSPACE_SLUG` / `PLANE_BASE_URL` are exported
(used by `plane-mcp.json`).

## Run
1. Start the daemon: `python -m orchestrator --config config.yaml`
2. Watch the Plane board and the daemon logs.

## Test A — Happy path (HAPPY seed task)
Expected, hands-off:
- ticket moves Ready to Dev → In Progress → Review → QA → Completed,
- the trail shows builder context-load + claim, a reviewer approval, and an **independent QA pass**
  citing the `/health` 200 check,
- a GitHub PR is **merged**, and merge happened **after** the QA comment,
- a `docs/` entry with citations exists in the merge commit.

## Test B — Negative / clarify gate (VAGUE seed task)
Expected: ticket moves to **Blocked** with a `🤖 [builder] … → BLOCKED` comment listing specific
questions; no branch/PR is created. Then: add an answering comment, move it back to Ready to Dev,
and confirm the builder picks it up again and reads the latest comment.

## Test C — QA catch (QA-CATCH seed task)
Arrange for the implementation to pass the builder's own test but violate the exact-body
acceptance criterion (e.g. body `{"status":"OK"}`). Expected: QA moves the ticket back to **In
Progress** with a `🤖 [qa] QA → IN PROGRESS` comment citing the body mismatch; the PR is NOT
merged until a rework fixes it.

## Seam checks (record pass/fail)
- Plane↔daemon: states/comments update correctly; ownership prevents double-pickup.
- daemon↔Claude Code: sessions launch in the right worktree; stream-json completion detected;
  a crash leaves a Blocked comment, not a hang.
- Claude Code↔git/GitHub: worktree → branch → PR → merge → worktree cleanup all happen.
