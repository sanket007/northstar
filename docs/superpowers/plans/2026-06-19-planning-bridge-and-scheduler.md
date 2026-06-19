# Planning Bridge + Dependency-Aware Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `northstar plan import` (a grill-first importer session that turns a plan into Plane Draft tasks with dependencies) and make the engine only dispatch a Ready-to-Dev task once its `blocked_by` blockers are Completed/Deployed.

**Architecture:** Three independent edits across disjoint files: (1) engine scheduler — `PlaneClient` relation reads + a `poll_once` readiness gate; (2) `templates/plane-importer.md` — the importer role doc; (3) `northstar/importer.py` + a `plan import` CLI command. The importer creates tasks/relations via the Plane MCP; the scheduler reads relations via REST.

**Tech Stack:** Python 3.11+, httpx, respx, Typer, pytest. Plane REST relations endpoints (verified).

## Global Constraints

- **`external_id` is importer-local, never a system coupling.** The orchestrator and scheduler must work on ANY work item (including hand-created Plane tasks with no marker) — they key off state/comments/relations only.
- Plane reads use the existing `_send` retry wrapper; auth `X-API-Key`. No Anthropic/SDK imports (northstar shells out to `claude`).
- `blocked_by` = "this issue is blocked BY the listed ones"; relations symmetric. Done = blocker state in {Completed, Deployed}.
- Full suite (80) stays green.

---

## File Structure (touched)

```
orchestrator/plane.py poller.py            # Task 1 (scheduler)
templates/plane-importer.md                # Task 2 (importer role)
northstar/importer.py cli.py               # Task 3 (plan import command)
tests/test_plane.py test_poller.py test_importer_doc.py test_importer.py test_cli.py
```

---

## Task 1: Dependency-aware scheduler (engine)

**Files:**
- Modify: `orchestrator/plane.py`, `orchestrator/poller.py`
- Test: `tests/test_plane.py`, `tests/test_poller.py`

**Interfaces:**
- `PlaneClient.get_issue(issue_id) -> Issue`; `PlaneClient.list_blocked_by(issue_id) -> list[str]` (the `blocked_by` UUIDs; `[]` if none). Both via `_send`.
- `poller.dependencies_clear(client, cfg, issue, cache: dict | None = None) -> bool`.
- `poll_once` gates **only Ready-to-Dev** issues on `dependencies_clear` (skips, doesn't dispatch, if blockers unfinished), with a per-call cache.

- [ ] **Step 1: Write failing tests**

`tests/test_plane.py` (append):
```python
@respx.mock
def test_get_issue_and_list_blocked_by():
    import httpx, respx
    from orchestrator.plane import PlaneClient, Issue
    pfx = "https://x/api/v1/workspaces/w/projects/p"
    respx.get(f"{pfx}/work-items/i9/").mock(return_value=httpx.Response(200, json={
        "id": "i9", "name": "n", "description_html": "", "state": "sDone", "sequence_id": 9}))
    respx.get(f"{pfx}/work-items/i1/relations/").mock(return_value=httpx.Response(200, json={
        "blocked_by": ["i9"], "blocking": []}))
    c = PlaneClient("https://x", "k", "w", "p", client=httpx.Client())
    assert c.get_issue("i9") == Issue("i9", "n", "", "sDone", 9)
    assert c.list_blocked_by("i1") == ["i9"]
    assert c.list_blocked_by("i9") == [] or c.list_blocked_by("i1") == ["i9"]  # tolerant
```

`tests/test_poller.py` (append):
```python
def _cfg_with_states():
    from orchestrator.config import Config
    from pathlib import Path
    return Config(plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
                  github_repo="o/r", repo_dir=Path("/t"), worktrees_root=Path("/t"), poll_interval_seconds=0,
                  claude_binary="c", claude_model="m", mcp_config_path=Path("/t/m.json"), templates_dir=Path("/t"),
                  state_ids={"Ready to Dev": "rd", "In Progress": "ip", "Review": "rv", "QA": "qa",
                             "Completed": "done", "Deployed": "dep"}, max_concurrency=1)


class DepClient:
    def __init__(self, blocked_by, blocker_state):
        self._bb = blocked_by; self._bs = blocker_state
        from orchestrator.plane import Issue
        self._ready = [Issue("t1", "task", "", "rd", 1)]
        self.Issue = Issue
    def list_issues_in_state(self, s, per_page=25):
        return self._ready if s == "rd" else []
    def list_blocked_by(self, issue_id):
        return self._bb
    def get_issue(self, bid):
        return self.Issue(bid, "blk", "", self._bs, 2)


def test_poll_skips_ready_task_with_unfinished_blocker():
    from orchestrator.poller import poll_once, Ownership
    cfg = _cfg_with_states()
    client = DepClient(blocked_by=["b1"], blocker_state="ip")  # blocker In Progress -> not done
    dispatched = []
    poll_once(client, cfg, Ownership(), lambda i, r: dispatched.append(i.id))
    assert dispatched == []


def test_poll_dispatches_ready_task_when_blocker_done():
    from orchestrator.poller import poll_once, Ownership
    cfg = _cfg_with_states()
    client = DepClient(blocked_by=["b1"], blocker_state="done")  # blocker Completed
    dispatched = []
    poll_once(client, cfg, Ownership(), lambda i, r: dispatched.append(i.id))
    assert dispatched == ["t1"]


def test_poll_dispatches_ready_task_with_no_blockers():
    from orchestrator.poller import poll_once, Ownership
    cfg = _cfg_with_states()
    client = DepClient(blocked_by=[], blocker_state="ip")
    dispatched = []
    poll_once(client, cfg, Ownership(), lambda i, r: dispatched.append(i.id))
    assert dispatched == ["t1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_plane.py -k blocked_by tests/test_poller.py -k "blocker or no_blockers" -v`
Expected: FAIL (methods/gate missing)

- [ ] **Step 3: Implement — `orchestrator/plane.py`**

Add two methods to `PlaneClient`:
```python
    def get_issue(self, issue_id: str) -> Issue:
        resp = self._send("GET", f"{self._prefix}/work-items/{issue_id}/")
        return self._parse_issue(resp.json())

    def list_blocked_by(self, issue_id: str) -> list[str]:
        resp = self._send("GET", f"{self._prefix}/work-items/{issue_id}/relations/")
        return resp.json().get("blocked_by", []) or []
```

- [ ] **Step 4: Implement — `orchestrator/poller.py`**

Add the import `from orchestrator.state_machine import role_for_state, READY_TO_DEV` (extend the existing import), and the helper + gate:
```python
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
```
Update `poll_once` to gate Ready-to-Dev with a per-call cache:
```python
def poll_once(client, cfg, ownership, dispatch) -> None:
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
```

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_plane.py tests/test_poller.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/plane.py orchestrator/poller.py tests/test_plane.py tests/test_poller.py
git commit -m "feat(engine): dependency-aware scheduling — gate Ready-to-Dev on blocked_by relations"
```

---

## Task 2: Importer role doc

**Files:**
- Create: `templates/plane-importer.md`
- Test: `tests/test_importer_doc.py`

**Interfaces:** loaded by `plan import` via `--append-system-prompt`. The grep test pins the invariants.

- [ ] **Step 1: Write the failing test** `tests/test_importer_doc.py`

```python
from pathlib import Path
def test_importer_doc_has_key_invariants():
    d = Path("templates/plane-importer.md").read_text().lower()
    assert "grill-me" in d                      # grills the whole plan
    assert "draft" in d                          # creates Draft tasks
    assert "blocked_by" in d or "create_work_item_relation" in d   # dependencies
    assert "external_id" in d or "[ns:" in d     # idempotency marker
    assert "acceptance criteria" in d and "citation" in d
    assert "directly in the plane board" in d or "hand-created" in d  # compliance note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_importer_doc.py -v`
Expected: FAIL

- [ ] **Step 3: Write `templates/plane-importer.md`**

```markdown
# Role: Plane Importer

You turn an implementation plan into well-formed Plane **Draft** tasks. A human is present — this is an
interactive, grill-first session. You have the Plane MCP tools (create/list work items + relations).

## Step 1 — Load context
- Read the plan file named in the prompt.
- Via Plane MCP, list the project's **existing** work items (you need these for de-duplication and to
  link dependencies to tasks that already exist — including tasks the user created **directly in the
  Plane board**).

## Step 2 — Assess the plan
- If the plan already has discrete, well-specified tasks, use them as the basis.
- If the plan is vague or has no explicit tasks, **propose a task breakdown** (a numbered list) and get
  the user's agreement before going further.

## Step 3 — Grill the whole plan (MANDATORY, before creating anything)
Invoke the `grill-me` skill and interview the user across the **entire** plan. Resolve every ambiguity:
unclear or missing **acceptance criteria**, fuzzy scope, undefined dependencies, and missing
**citations** (links to the spec/plan section, files, or docs each task is based on). Keep grilling until
**every task is crisp enough that an autonomous builder could start it with no further questions.** This
front-loads all clarification here so the build phase never stalls on questions.

## Step 4 — Extract the dependency graph
Infer `blocked_by` edges from the plan's task order and the `Interfaces: Consumes/Produces` blocks: a
task that consumes another's output is **blocked_by** it. Present the full edge list to the user and
confirm it (watch for cycles — a cycle means a task can never become ready).

## Step 5 — Create Draft tasks (idempotent)
For each task, compute a stable id `external_id = <plan-filename>#<task-id>`.
- First check the existing work items: if one already has this `external_id` (or, if the MCP can't set
  `external_id`, a `[ns:<external_id>]` marker in its description), **skip it** (or update) — never
  duplicate. Match by title/content if no marker is present.
- Otherwise `create_work_item` in the **Draft** state with: a clear title; a description containing the
  **acceptance criteria**, the **citations**, and a reference to the source plan/task; and the
  `external_id` (or the `[ns:…]` marker in the description as a fallback).

## Step 6 — Create relations
For each `blocked_by` edge, call `create_work_item_relation` (relation_type `blocked_by`). Edges may
point at **existing** tasks (from earlier plans or hand-created on the board) — link those by the id you
found in Step 1; do not require them to carry a marker.

## Step 7 — Summarize
Report what you created vs skipped, and the dependency edges set. Leave the tasks in **Draft** — the
user moves the ready ones to **Ready to Dev** when they choose.

## Rules
- `external_id`/the `[ns:…]` marker is **only** for your re-import de-duplication. The rest of the
  platform does not depend on it — tasks created directly in the Plane board are first-class.
- Never move tasks past Draft. Never start implementing — you only create/curate tasks.
```

- [ ] **Step 4: Run the test + full suite**

Run: `.venv/bin/pytest tests/test_importer_doc.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/plane-importer.md tests/test_importer_doc.py
git commit -m "feat(bridge): plane-importer role doc (grill-first, idempotent, dependency-aware)"
```

---

## Task 3: `plan import` CLI command

**Files:**
- Create: `northstar/importer.py`
- Modify: `northstar/cli.py`
- Test: `tests/test_importer.py`, `tests/test_cli.py`

**Interfaces:**
- `importer.build_import_command(claude_binary, mcp_config_path, importer_doc_text, plan_path, project_id) -> list[str]` — the interactive `claude` invocation (no `-p`; an initial prompt naming the plan + project).
- `importer.run_import(name, plan_path, *, runner=subprocess.run) -> None` — loads the project, reads the importer doc, builds the command, runs it inheriting the terminal with the project's `PLANE_*` env.
- `cli.py` gains a `plan` Typer group with an `import` command → `importer.run_import`.

- [ ] **Step 1: Write failing tests**

`tests/test_importer.py`:
```python
from northstar import importer


def test_build_import_command():
    cmd = importer.build_import_command("claude", "/h/plane-mcp.json", "DOC TEXT", "plan.md", "proj1")
    assert cmd[0] == "claude"
    assert "--mcp-config" in cmd and "/h/plane-mcp.json" in cmd
    assert "--append-system-prompt" in cmd and "DOC TEXT" in cmd
    initial = cmd[-1]
    assert "plan.md" in initial and "proj1" in initial


def test_run_import_uses_project_env_and_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib_reload(paths)
    paths.ensure_dirs(); paths.register_project("acme", {"repo_dir": str(tmp_path / "repo")})
    (tmp_path / "repo").mkdir()
    paths.project_config_path("acme").write_text(
        "plane_api_key: K\nplane_base_url: https://x\nplane_workspace_slug: w\n"
        "plane_project_id: proj1\nrepo_dir: " + str(tmp_path / "repo") +
        "\nmcp_config_path: /h/m.json\nclaude_binary: claude\n")
    seen = {}
    def fake_runner(cmd, **kw):
        seen["cmd"] = cmd; seen["cwd"] = kw.get("cwd"); seen["env"] = kw.get("env")
        class R: returncode = 0
        return R()
    importer.run_import("acme", "plan.md", runner=fake_runner)
    assert seen["cwd"] == str(tmp_path / "repo")
    assert seen["env"]["PLANE_API_KEY"] == "K" and seen["env"]["PLANE_BASE_URL"] == "https://x"
    assert "proj1" in seen["cmd"][-1]


def importlib_reload(m):
    import importlib; return importlib.reload(m)
```

`tests/test_cli.py` (append):
```python
def test_plan_import_command_invokes_run_import(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli.importer, "run_import",
                        lambda name, plan_path, **kw: seen.update(name=name, plan=plan_path))
    result = runner.invoke(cli.app, ["plan", "import", "acme", "plan.md"])
    assert result.exit_code == 0
    assert seen == {"name": "acme", "plan": "plan.md"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_importer.py tests/test_cli.py -k "import" -v`
Expected: FAIL (`northstar.importer` missing; `plan` command missing)

- [ ] **Step 3: Implement `northstar/importer.py`**

```python
from __future__ import annotations
import os
import subprocess

from northstar import paths, assets


def build_import_command(claude_binary, mcp_config_path, importer_doc_text,
                         plan_path, project_id) -> list[str]:
    initial = (
        f"Import the plan at {plan_path} into Plane project {project_id}. "
        "Follow your plane-importer instructions: first grill the entire plan with me to resolve "
        "every ambiguity, then create Draft tasks with acceptance criteria, citations, and "
        "blocked_by dependency relations."
    )
    return [
        claude_binary,
        "--mcp-config", str(mcp_config_path),
        "--append-system-prompt", importer_doc_text,
        initial,
    ]


def run_import(name, plan_path, *, runner=subprocess.run) -> None:
    rt = paths.load_project(name)
    doc = (assets.templates_dir() / "plane-importer.md").read_text()
    mcp = rt.cfg.get("mcp_config_path") or str(paths.home() / "plane-mcp.json")
    claude_binary = rt.cfg.get("claude_binary", "claude")
    project_id = rt.cfg.get("plane_project_id", "")
    cmd = build_import_command(claude_binary, mcp, doc, plan_path, project_id)
    env = {**os.environ, **rt.plane_env}
    runner(cmd, cwd=str(rt.repo_dir), env=env)
```

- [ ] **Step 4: Implement the CLI command in `northstar/cli.py`**

Add `from northstar import ... importer` to the existing import, and a `plan` group:
```python
plan_app = typer.Typer(help="import plans into Plane")
app.add_typer(plan_app, name="plan")


@plan_app.command("import")
def plan_import(project: str, plan_path: str):
    """Grill a plan and create Plane Draft tasks (interactive)."""
    importer.run_import(project, plan_path)
```

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_importer.py tests/test_cli.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Update the usage doc** — in `docs/northstar-usage.md`, after "Add a project", add:

```markdown
## Import a plan (create the tasks)
```bash
northstar plan import <project> path/to/plan.md
```
Launches an interactive session that grills you over the whole plan, then creates Plane **Draft** tasks
with acceptance criteria, citations, and dependency links. Run it again for each new plan as the project
grows (idempotent — it won't duplicate tasks). Then move the ready tasks Draft → Ready to Dev.
```

- [ ] **Step 7: Commit**

```bash
git add northstar/importer.py northstar/cli.py tests/test_importer.py tests/test_cli.py docs/northstar-usage.md
git commit -m "feat(bridge): northstar plan import command (interactive grill-first importer)"
```

---

## Self-Review notes (addressed)

- **Spec §2 (relations create/read model):** Task 1 (`list_blocked_by` reads `blocked_by`); Task 2 doc (`create_work_item_relation`).
- **Spec §3.1 (plan import interactive launch):** Task 3 (`build_import_command` no `-p`, MCP + append-system-prompt + initial prompt; `run_import` inherits terminal + plane env).
- **Spec §3.2 (importer role: load/assess/grill/extract/create-idempotent/relate/summarize):** Task 2 doc + its grep test.
- **Spec §3.3 (multi-plan continuation, marker-or-lookup matching):** Task 2 doc Steps 1+5.
- **Spec §3.4 (external_id importer-local; direct-Plane tasks first-class):** Task 2 doc Rules + the Global Constraint; the scheduler (Task 1) keys off state/relations only, never external_id.
- **Spec §4 (scheduler reads + Ready-to-Dev gate, per-cycle cache):** Task 1.
- **Spec §5 (success criteria):** Task 1 (respx + fake-client gate tests), Task 2 (doc grep), Task 3 (command-build + run_import + CLI tests); full-suite runs in each task.
- **Spec §6 (out of scope):** no headless import, no plan-sync, no cycle detection, no concurrency/deploy — none included.
- **Disjoint files:** T1 `orchestrator/plane.py`+`poller.py` (+ test_plane, test_poller); T2 `templates/plane-importer.md` (+ test_importer_doc); T3 `northstar/importer.py`+`cli.py` (+ test_importer, test_cli). No two tasks share a file → parallel-safe.
