# Planning Bridge + Dependency-Aware Scheduler — Design Spec

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Extends:** the northstar CLI + engine.

---

## 1. Goal

Close the two remaining gaps before a live run:

1. **Planning bridge** — `northstar plan import <project> <plan.md>` turns a plan into **Plane Draft
   tasks**. It is a **grill-first interactive session**: it reads the plan, grills the *whole* plan with
   the user to resolve every ambiguity (and proposes a task breakdown if the plan is vague/taskless),
   and only then creates fully-specified Draft tasks with acceptance criteria, citations, and an
   LLM-extracted **dependency graph**. Idempotent across multiple plans over a project's life.
2. **Dependency-aware scheduler** — the engine only dispatches a Ready-to-Dev task once its
   `blocked_by` dependencies are Completed/Deployed.

**Standardized flow:** `project add → plan import → (gate Draft→Ready to Dev) → start`.

---

## 2. Plane relations (verified)

- **Create** (what the importer's MCP `create_work_item_relation` calls):
  `POST .../work-items/{id}/relations/` body `{"relation_type": "blocked_by", "issues": ["<uuid>", …]}`.
- **Read** (what the scheduler calls):
  `GET .../work-items/{id}/relations/` → grouped object; `blocked_by` is an array of blocker issue UUIDs.
- `blocked_by` means **"this issue is blocked BY the listed ones."** Relations are **symmetric**
  (stored on both sides). Workspace `X-API-Key` works on self-hosted. Relation enum:
  `blocking, blocked_by, duplicate, relates_to, start_*, finish_*`.

---

## 3. Planning bridge

### 3.1 `northstar plan import <project> <plan_path>` (CLI)
- Loads the project (`load_project`), exports the project's `PLANE_*` env (so the MCP resolves), and
  **launches an interactive `claude` session** (inherits the terminal — the user is present) in the
  project's `repo_dir`, with:
  - `--mcp-config <plane-mcp.json>` (Plane MCP attached),
  - `--append-system-prompt <templates/plane-importer.md>` (the importer role),
  - an initial prompt naming the plan path + the Plane `project_id`.
- It is **not** `-p`/headless — the importer converses (grills) with the user. northstar just sets up
  context and hands over the terminal; the session does the work via MCP.

### 3.2 `templates/plane-importer.md` (the importer role — grill-first)
Encodes this procedure:
1. **Load context:** read the plan file; via Plane MCP, list the project's **existing** work items
   (for idempotency + cross-plan dependency linking).
2. **Assess the plan:** if it has discrete, well-specified tasks, use them; if it is vague or has no
   tasks, **propose a task breakdown**.
3. **Grill the whole plan (`grill-me`):** interview the user to resolve *every* ambiguity — unclear or
   missing acceptance criteria, scope, dependencies, and citations — until each task is crisp enough
   that an autonomous builder could start with **no further questions**. This front-loads all Q&A here.
4. **Extract the dependency graph:** infer `blocked_by` edges from the plan's task order + the
   `Interfaces: Consumes/Produces` blocks (a task that consumes another's output is blocked_by it).
   Confirm the graph with the user.
5. **Create Draft tasks (idempotent):** for each task, compute a stable **`external_id`** =
   `hash(plan_filename + task_id)`. If a work item with that `external_id` already exists, skip (or
   update); else `create_work_item` in **Draft** with: title, a description containing the **acceptance
   criteria + citations + a reference to the source plan/task**, and the `external_id`.
   (If the MCP can't set `external_id`, embed a `[ns:<external_id>]` marker in the description and match
   on it instead.)
6. **Create relations:** for each `blocked_by` edge, `create_work_item_relation`. Edges may point at
   **existing** tasks from earlier plans (found in step 1) — that's how continuation links cross-plan
   dependencies.
7. **Summarize:** report what was created/skipped + the dependency edges.

### 3.3 Multi-plan continuation
Importing plan #2..#N **adds** their tasks without duplicating prior ones, and their dependency edges
can reference existing tasks. Re-importing the same plan is a no-op. The importer matches "already
created" tasks by `external_id` (or the `[ns:…]` marker) **when present**, and otherwise by title/content
lookup — so it converges whether or not a task carries the marker.

### 3.4 `external_id` is importer-local — NOT a system coupling
The platform must be fully compliant with tasks created **directly in the Plane board** by the user
(which carry no `external_id`/marker). Therefore:
- The **orchestrator** (builder/reviewer/QA) and the **scheduler** never read or require `external_id` —
  they operate on *any* work item by its state, comments, and relations. A hand-created Plane task flows
  through Ready to Dev → … → Completed identically to an imported one.
- `external_id`/the `[ns:…]` marker is used **only** by the importer, **only** for its own
  re-import dedup. If it's absent, the importer falls back to title/content matching; it never assumes
  every task has it.
- Dependency edges may target hand-created tasks: the importer links blockers it finds by lookup
  (title/sequence), not by requiring a marker on the target.

---

## 4. Dependency-aware scheduler (engine)

### 4.1 `PlaneClient` reads (new methods)
- `get_issue(issue_id) -> Issue` — `GET .../work-items/{id}/` (need a blocker's current state).
- `list_blocked_by(issue_id) -> list[str]` — `GET .../work-items/{id}/relations/`, return the
  `blocked_by` array (empty if none). Routes through the existing `_send` retry wrapper.

### 4.2 Readiness gate in `poll_once`
- New helper `dependencies_clear(client, cfg, issue) -> bool`:
  - `blockers = client.list_blocked_by(issue.id)`; if empty → ready.
  - Build `id_to_name = {v: k for k, v in cfg.state_ids.items()}`.
  - For each blocker id: `state_name = id_to_name.get(client.get_issue(blocker).state_id)`; the blocker
    is **done** iff `state_name in {"Completed", "Deployed"}`. Unknown/other → not done.
  - Ready iff **all** blockers are done.
- In `poll_once`, gate **only the Ready-to-Dev pickup**: before claiming/dispatching a Ready-to-Dev
  issue, if `not dependencies_clear(...)`, **skip it** (leave it in Ready to Dev; don't dispatch). In-flight
  states (In Progress rework, Review, QA) are not dep-gated — they already started.
- **Efficiency:** cache blocker states within a single `poll_once` call (a dict) so shared blockers
  aren't re-fetched; the gate runs only for Ready-to-Dev candidates and only until the concurrency slot
  fills (the existing short-circuit still applies).

---

## 5. Success criteria

- **Bridge command:** `plan import` builds the right interactive `claude` invocation — `--mcp-config`,
  `--append-system-prompt <plane-importer.md>`, cwd = repo_dir, plane env exported, initial prompt names
  the plan path + project_id. Unit-tested by monkeypatching the launch and asserting the command/env.
- **Importer role doc:** a grep test pins the invariants — references `grill-me`, "Draft", `external_id`
  (or the marker fallback), `blocked_by`/`create_work_item_relation`, and the vague-plan breakdown step.
- **Scheduler reads:** `list_blocked_by` parses the `blocked_by` array; `get_issue` parses state.
  Unit-tested with respx.
- **Readiness gate:** `dependencies_clear` returns False when any blocker is not Completed/Deployed and
  True when all are (unit-tested with a fake client); `poll_once` does **not** dispatch a Ready-to-Dev
  task with an unfinished blocker, but **does** once the blocker is done (tested with a fake client).
- **No regressions:** full suite (80) stays green; engine runtime behavior otherwise unchanged.

---

## 6. Out of scope

- Headless/non-interactive plan import (the bridge is interactive by design — the user grills).
- A `plan sync`/watched-folder importer (explicit `plan import` per plan).
- Validating that the LLM-extracted graph is acyclic (the importer confirms the graph with the user;
  cycle detection in the scheduler is a later nicety — a cycle would simply leave tasks ungated-never,
  which the user sees on the board).
- Concurrency > 1, deploy automation, the human-inbox — all later.
- Any change to the board state machine, the role docs beyond adding `plane-importer.md`, or the Plane
  reconcile.
