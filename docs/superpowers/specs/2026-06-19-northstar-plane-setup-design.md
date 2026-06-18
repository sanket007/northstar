# northstar Plane Setup — Design Spec

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Extends:** the northstar CLI (`docs/superpowers/specs/2026-06-19-northstar-cli-design.md`)

---

## 1. Goal

Make `northstar project add` set up Plane for the user instead of assuming a hand-made project +
board. During `project add` it can **create a new Plane project** (or target an existing one) and
**reconcile the board to exactly the 8 canonical states** (Draft → … → Deployed) before discovering
their IDs. This removes the manual "create the project and name the 8 columns first" step.

The Plane connection (base URL, API key, workspace slug) is still asked **per project** (unchanged).

---

## 2. What Plane allows (verified)

Self-hosted, with the workspace `X-API-Key` (no admin endpoint needed):

- **Create project:** `POST /api/v1/workspaces/{slug}/projects/` with `{"name", "identifier"}` →
  `201`, project `id` in the response. `identifier` is a short uppercase code, unique per workspace.
- Creating a project **auto-seeds 5 states**: Backlog (`backlog`), Todo (`unstarted`),
  In Progress (`started`), Done (`completed`), Cancelled (`cancelled`). One per group; **Backlog is
  the default state**.
- **States:** `POST .../projects/{id}/states/` `{"name","group","color","sequence?"}`;
  `PATCH .../states/{state_id}/` (rename/regroup); `DELETE .../states/{state_id}/`.
- Valid groups (fixed): `backlog, unstarted, started, completed, cancelled`. There is **no
  "blocked"/"deployed"/"triage"** group creatable via REST.
- **Delete constraints:** cannot delete the project's default state, nor a state that still holds
  work items.
- Rate limit 60 req/min (fine — setup is a handful of calls).

---

## 3. Board reconcile (target = exactly the 8 canonical states)

Canonical columns and their Plane group:

| Column | Group |
|---|---|
| Draft | `backlog` |
| Ready to Dev | `unstarted` |
| In Progress | `started` |
| Review | `started` |
| QA | `started` |
| Blocked | `started` |
| Completed | `completed` |
| Deployed | `completed` |

`sequence` orders states within a group; we assign increasing sequences so Review/QA/Blocked render
after In Progress, and Deployed after Completed.

### 3a. New project (deterministic, zero deletes)
A freshly created project has exactly the 5 known defaults, so reconcile by rename + repurpose +
create — never delete:

- PATCH **Backlog** → name `Draft` (group `backlog`)
- PATCH **Todo** → name `Ready to Dev` (group `unstarted`)
- keep **In Progress** (group `started`)
- PATCH **Done** → name `Completed` (group `completed`)
- PATCH **Cancelled** → name `Blocked`, group `started` (repurpose — no native "blocked" group)
- POST **Review** (`started`), **QA** (`started`), **Deployed** (`completed`)

Result: exactly the 8, no deletions, no constraint hazards.

### 3b. Existing project (safe, best-effort exactly-8)
The project may already have custom states and real issues. Reconcile safely:

1. Fetch current states.
2. For each of the 8 canonical names **missing**, create it with the mapped group + a sequence.
3. Rename obvious default matches if present and unused (Backlog→Draft, Todo→Ready to Dev,
   Done→Completed) — only when the source name is a known Plane default and the target name is absent.
4. **Leftover (extra) states:** delete only if the state is empty (no work items) **and** not the
   project default; otherwise **log a warning and leave it**. Never destroy states holding issues.

So "exactly 8" is guaranteed for new projects and best-effort (additive + safe) for existing ones.

---

## 4. `PlaneAdmin` client (new, northstar-side)

A new `northstar/plane_admin.py` holds all Plane **setup** concerns, keeping the engine's runtime
`orchestrator.plane.PlaneClient` untouched.

```
class PlaneAdmin:
    def __init__(self, base_url, api_key, workspace_slug, client=None)   # workspace-scoped
    def create_project(self, name, identifier, description="") -> dict   # returns project incl. "id"
    def list_states(self, project_id) -> list[dict]                      # full state objects
    def create_state(self, project_id, name, group, color="#6B7280", sequence=None) -> dict
    def update_state(self, project_id, state_id, **fields) -> None       # rename/regroup
    def delete_state(self, project_id, state_id) -> None
    def state_has_items(self, project_id, state_id) -> bool              # guard for safe delete
    def ensure_board(self, project_id, *, fresh: bool) -> dict[str, str] # returns name -> id for the 8
```

- Uses `httpx` with the `X-API-Key` header, like the engine client. `client` is injectable for tests.
- `ensure_board(fresh=True)` runs §3a; `ensure_board(fresh=False)` runs §3b. Returns the canonical
  name → state-id map for the 8 (this replaces the separate state discovery for newly set-up boards).

---

## 5. `project add` flow change

After collecting the Plane connection (base_url, api_key, workspace_slug — per project, unchanged):

1. **Choose Plane project target:**
   - **New:** prompt for project `name` + `identifier` → `PlaneAdmin.create_project(...)` → capture
     `project_id`; `fresh = True`.
   - **Existing:** prompt for `project_id`; `fresh = False`.
2. **Reconcile board:** `state_ids = PlaneAdmin.ensure_board(project_id, fresh=fresh)`.
3. Continue the existing flow unchanged: resolve/clone the GitHub repo (gh-auth gated), install
   guardrails, `write_project_config(... state_ids ...)`, register.

`discover_state_ids` (engine-`PlaneClient`-based) is no longer needed for the setup path — the
state IDs come straight from `ensure_board`. (Keep `discover_state_ids` only if still used elsewhere;
otherwise remove it to avoid two code paths.)

CLI additions: `project add` gains `--new-plane-project` / `--existing-plane-project <id>` (and the
`name`/`identifier` options) so it can run non-interactively, mirroring the interactive prompts.

---

## 6. Success criteria

- `PlaneAdmin.create_project` creates a project and returns its id (unit-tested with a faked HTTP
  client asserting the POST payload + parsed id).
- `ensure_board(fresh=True)` against the 5 known defaults issues exactly: 4 PATCH renames, 1 PATCH
  repurpose (Cancelled→Blocked/started), 3 POST creates — and returns all 8 name→id pairs.
  Unit-tested by asserting the sequence of calls a fake client receives.
- `ensure_board(fresh=False)` creates only missing states, and never deletes a state reported to
  hold items or the default state (unit-tested: a non-empty extra state is warned, not deleted).
- `project add` (new-project path) end-to-end writes a per-project config whose `state_ids` contains
  all 8 canonical names.
- The engine and all existing tests stay green; `orchestrator.plane.PlaneClient` is unmodified.

---

## 7. Out of scope

- Deleting/merging arbitrary user states on existing projects beyond the safe rule in §3b.
- Creating cycles/modules/labels on the Plane project.
- A `triage`/intake state (not creatable via REST).
- Reordering state *groups* (fixed by Plane).
- Moving the Plane connection to machine-level (the user chose per-project; that's a possible later
  change, tracked separately).
- Seeding test tickets (`--seed`) — still the separate roadmap item.
