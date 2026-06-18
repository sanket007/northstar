# northstar Plane Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `northstar project add` create a Plane project (or target an existing one) and reconcile its board to exactly the 8 canonical states, via a new `PlaneAdmin` client — so users no longer hand-build the Plane project + columns.

**Architecture:** A new `northstar/plane_admin.py` (`PlaneAdmin`) owns all Plane *setup* (project + state CRUD + the `ensure_board` reconcile), using `httpx` with the workspace `X-API-Key`. The engine's runtime `orchestrator.plane.PlaneClient` is unchanged. `project.add_project` is rewired to use `PlaneAdmin` instead of the old read-only `discover_state_ids`, and the CLI's `project add` gains a new-vs-existing-project branch.

**Tech Stack:** Python 3.11+, httpx, respx (HTTP test mocking), PyYAML, Typer, pytest. Reuses the `northstar.proc` runner for `gh`.

## Global Constraints

- **Engine untouched:** do not modify `orchestrator/`. Plane setup lives entirely in `northstar/`.
- **Auth:** Plane calls use header `X-API-Key`. Base: `{base_url}/api/v1/workspaces/{slug}`. No Anthropic/SDK imports.
- **Canonical states + groups (exact):** Draft→`backlog`, Ready to Dev→`unstarted`, In Progress→`started`, Review→`started`, QA→`started`, Blocked→`started`, Completed→`completed`, Deployed→`completed`.
- **Plane state groups are fixed:** `backlog, unstarted, started, completed, cancelled`. There is no "blocked"/"deployed" group — Blocked lives in `started`, Deployed in `completed`.
- **Safe reconcile:** never delete a state that holds work items or the project default state.
- **Plane connection stays per-project** (base_url, api_key, workspace_slug asked in `project add`).
- All existing tests stay green; full suite currently 57.

---

## File Structure

```
northstar/
  plane_admin.py     # NEW — PlaneAdmin: create_project, state CRUD, ensure_board
  project.py         # MODIFY — add_project uses PlaneAdmin; ProjectInputs gains new-project fields
  cli.py             # MODIFY — `project add` new-vs-existing Plane project options/prompts
tests/
  test_plane_admin.py  # NEW
  test_project.py      # MODIFY — adapt add_project tests to PlaneAdmin
```

---

## Task 1: `PlaneAdmin` client — project + state CRUD

**Files:**
- Create: `northstar/plane_admin.py`
- Test: `tests/test_plane_admin.py`

**Interfaces:**
- Produces:
  - Module constants `CANONICAL_GROUPS: dict[str,str]` (the 8 name→group) and `CANONICAL_ORDER: list[str]` (the 8 in board order).
  - `PlaneAdmin(base_url, api_key, workspace_slug, client: httpx.Client | None = None)` with:
    `create_project(name, identifier, description="") -> dict`, `list_states(project_id) -> list[dict]`,
    `create_state(project_id, name, group, color="#6B7280", sequence=None) -> dict`,
    `update_state(project_id, state_id, **fields) -> None`, `delete_state(project_id, state_id) -> None`,
    `state_has_items(project_id, state_id) -> bool`.

- [ ] **Step 1: Write the failing test** in `tests/test_plane_admin.py`

```python
import httpx, respx
from northstar.plane_admin import PlaneAdmin, CANONICAL_GROUPS, CANONICAL_ORDER

BASE = "https://plane.test"
WS = "acme"
WPREFIX = f"{BASE}/api/v1/workspaces/{WS}"


def admin():
    return PlaneAdmin(BASE, "key", WS, client=httpx.Client())


def test_canonical_constants():
    assert CANONICAL_GROUPS["Blocked"] == "started"
    assert CANONICAL_GROUPS["Deployed"] == "completed"
    assert CANONICAL_ORDER == ["Draft", "Ready to Dev", "In Progress", "Review",
                               "QA", "Blocked", "Completed", "Deployed"]


@respx.mock
def test_create_project_posts_and_returns_id():
    route = respx.post(f"{WPREFIX}/projects/").mock(
        return_value=httpx.Response(201, json={"id": "p1", "name": "Web", "identifier": "WEB"}))
    proj = admin().create_project("Web", "WEB")
    assert proj["id"] == "p1"
    assert b'"identifier": "WEB"' in route.calls.last.request.content


@respx.mock
def test_list_states_paginates():
    route = respx.get(f"{WPREFIX}/projects/p1/states/")
    route.side_effect = [
        httpx.Response(200, json={"results": [{"id": "s1", "name": "Backlog", "group": "backlog"}],
                                  "next_cursor": "C"}),
        httpx.Response(200, json={"results": [{"id": "s2", "name": "Done", "group": "completed"}],
                                  "next_cursor": None}),
    ]
    names = [s["name"] for s in admin().list_states("p1")]
    assert names == ["Backlog", "Done"]


@respx.mock
def test_create_state_posts_payload():
    route = respx.post(f"{WPREFIX}/projects/p1/states/").mock(
        return_value=httpx.Response(201, json={"id": "s9", "name": "QA", "group": "started"}))
    out = admin().create_state("p1", "QA", "started", sequence=20000)
    assert out["id"] == "s9"
    body = route.calls.last.request.content
    assert b'"name": "QA"' in body and b'"group": "started"' in body and b'"sequence": 20000' in body


@respx.mock
def test_update_state_patches():
    route = respx.patch(f"{WPREFIX}/projects/p1/states/s1/").mock(return_value=httpx.Response(200, json={}))
    admin().update_state("p1", "s1", name="Draft", group="backlog")
    assert route.called and b'"name": "Draft"' in route.calls.last.request.content


@respx.mock
def test_delete_state():
    route = respx.delete(f"{WPREFIX}/projects/p1/states/s1/").mock(return_value=httpx.Response(204))
    admin().delete_state("p1", "s1")
    assert route.called


@respx.mock
def test_state_has_items_true_when_results_nonempty():
    respx.get(f"{WPREFIX}/projects/p1/work-items/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "i1"}]}))
    assert admin().state_has_items("p1", "s1") is True


@respx.mock
def test_state_has_items_false_when_empty():
    respx.get(f"{WPREFIX}/projects/p1/work-items/").mock(
        return_value=httpx.Response(200, json={"results": []}))
    assert admin().state_has_items("p1", "s1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plane_admin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.plane_admin'`

- [ ] **Step 3: Implement `northstar/plane_admin.py`**

```python
from __future__ import annotations
import httpx

CANONICAL_GROUPS = {
    "Draft": "backlog",
    "Ready to Dev": "unstarted",
    "In Progress": "started",
    "Review": "started",
    "QA": "started",
    "Blocked": "started",
    "Completed": "completed",
    "Deployed": "completed",
}
CANONICAL_ORDER = ["Draft", "Ready to Dev", "In Progress", "Review",
                   "QA", "Blocked", "Completed", "Deployed"]


class PlaneAdmin:
    def __init__(self, base_url, api_key, workspace_slug, client: httpx.Client | None = None):
        self._base = f"{base_url.rstrip('/')}/api/v1/workspaces/{workspace_slug}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def create_project(self, name, identifier, description="") -> dict:
        r = self._http.post(f"{self._base}/projects/",
                            json={"name": name, "identifier": identifier, "description": description})
        r.raise_for_status()
        return r.json()

    def list_states(self, project_id) -> list[dict]:
        out, params = [], {}
        url = f"{self._base}/projects/{project_id}/states/"
        while True:
            r = self._http.get(url, params=params)
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def create_state(self, project_id, name, group, color="#6B7280", sequence=None) -> dict:
        payload = {"name": name, "group": group, "color": color}
        if sequence is not None:
            payload["sequence"] = sequence
        r = self._http.post(f"{self._base}/projects/{project_id}/states/", json=payload)
        r.raise_for_status()
        return r.json()

    def update_state(self, project_id, state_id, **fields) -> None:
        r = self._http.patch(f"{self._base}/projects/{project_id}/states/{state_id}/", json=fields)
        r.raise_for_status()

    def delete_state(self, project_id, state_id) -> None:
        r = self._http.delete(f"{self._base}/projects/{project_id}/states/{state_id}/")
        r.raise_for_status()

    def state_has_items(self, project_id, state_id) -> bool:
        r = self._http.get(f"{self._base}/projects/{project_id}/work-items/",
                           params={"state": state_id, "per_page": 1})
        r.raise_for_status()
        return len(r.json().get("results", [])) > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_plane_admin.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/plane_admin.py tests/test_plane_admin.py
git commit -m "feat(northstar): PlaneAdmin client (project + state CRUD)"
```

---

## Task 2: `ensure_board` reconcile

**Files:**
- Modify: `northstar/plane_admin.py` (add the `ensure_board` method)
- Test: `tests/test_plane_admin.py` (append)

**Interfaces:**
- Consumes: the Task-1 CRUD methods on `self`.
- Produces: `PlaneAdmin.ensure_board(project_id, *, fresh: bool) -> dict[str, str]` — reconciles the
  board to the 8 canonical states and returns the canonical name → state-id map.

- [ ] **Step 1: Write the failing test** (append to `tests/test_plane_admin.py`)

```python
from northstar.plane_admin import PlaneAdmin


class RecordingAdmin(PlaneAdmin):
    """A PlaneAdmin whose CRUD methods are replaced by recorders (no HTTP)."""
    def __init__(self, states):
        self._states = states           # list of {id,name,group,default?}
        self.updates = []
        self.creates = []
        self.deletes = []
        self._has_items = set()         # state ids that "have items"
        self._next = 100

    def list_states(self, project_id):
        return list(self._states)

    def update_state(self, project_id, state_id, **fields):
        self.updates.append((state_id, fields))
        for s in self._states:
            if s["id"] == state_id:
                s.update(fields)

    def create_state(self, project_id, name, group, color="#6B7280", sequence=None):
        self._next += 1
        s = {"id": f"new{self._next}", "name": name, "group": group}
        self._states.append(s)
        self.creates.append((name, group))
        return s

    def delete_state(self, project_id, state_id):
        self.deletes.append(state_id)
        self._states = [s for s in self._states if s["id"] != state_id]

    def state_has_items(self, project_id, state_id):
        return state_id in self._has_items


DEFAULTS = lambda: [
    {"id": "d1", "name": "Backlog", "group": "backlog", "default": True},
    {"id": "d2", "name": "Todo", "group": "unstarted"},
    {"id": "d3", "name": "In Progress", "group": "started"},
    {"id": "d4", "name": "Done", "group": "completed"},
    {"id": "d5", "name": "Cancelled", "group": "cancelled"},
]


def test_ensure_board_fresh_renames_repurposes_creates():
    a = RecordingAdmin(DEFAULTS())
    ids = a.ensure_board("p1", fresh=True)
    # 4 updates: Backlog->Draft, Todo->Ready to Dev, Done->Completed, Cancelled->Blocked
    renamed = {f["name"] for _, f in a.updates}
    assert renamed == {"Draft", "Ready to Dev", "Completed", "Blocked"}
    assert len(a.updates) == 4
    # 3 creates: Review, QA, Deployed
    assert {n for n, _ in a.creates} == {"Review", "QA", "Deployed"}
    assert a.deletes == []
    # returns all 8 canonical ids
    assert set(ids) == set(CANONICAL_ORDER)


def test_ensure_board_existing_creates_missing_and_warns_nonempty_extra():
    states = [
        {"id": "x1", "name": "In Progress", "group": "started"},
        {"id": "x2", "name": "Notes", "group": "started"},   # extra, has items -> must NOT delete
        {"id": "x3", "name": "Scratch", "group": "started"},  # extra, empty -> may delete
    ]
    a = RecordingAdmin(states)
    a._has_items = {"x2"}
    ids = a.ensure_board("p1", fresh=False)
    # all 8 canonical present in the returned map
    assert set(ids) == set(CANONICAL_ORDER)
    # the non-empty extra was never deleted
    assert "x2" not in a.deletes
    # the empty extra may be removed
    assert "x3" in a.deletes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plane_admin.py -k ensure_board -v`
Expected: FAIL with `AttributeError: 'RecordingAdmin' object has no attribute 'ensure_board'`

- [ ] **Step 3: Implement `ensure_board`** (append to the `PlaneAdmin` class in `northstar/plane_admin.py`)

```python
    _DEFAULT_RENAME = {"Backlog": "Draft", "Todo": "Ready to Dev", "Done": "Completed"}

    def ensure_board(self, project_id, *, fresh: bool) -> dict:
        states = self.list_states(project_id)
        by_name = {s["name"]: s for s in states}

        # 1. rename known Plane defaults to canonical names (only if target absent)
        for src, dst in self._DEFAULT_RENAME.items():
            if src in by_name and dst not in by_name:
                self.update_state(project_id, by_name[src]["id"],
                                  name=dst, group=CANONICAL_GROUPS[dst])
                s = by_name.pop(src); s["name"] = dst; by_name[dst] = s

        # 2. fresh projects: repurpose the seeded Cancelled state into Blocked (no native group)
        if fresh and "Cancelled" in by_name and "Blocked" not in by_name:
            self.update_state(project_id, by_name["Cancelled"]["id"], name="Blocked", group="started")
            s = by_name.pop("Cancelled"); s["name"] = "Blocked"; by_name["Blocked"] = s

        # 3. create any canonical states still missing, ordered by sequence
        seq = 15000
        for name in CANONICAL_ORDER:
            if name not in by_name:
                by_name[name] = self.create_state(project_id, name, CANONICAL_GROUPS[name],
                                                   sequence=seq)
            seq += 5000

        # 4. existing projects: remove only safe leftover (empty, non-default, non-canonical) states
        if not fresh:
            for name, s in list(by_name.items()):
                if name in CANONICAL_GROUPS or s.get("default"):
                    continue
                if self.state_has_items(project_id, s["id"]):
                    continue  # holds work items — warn (left in place), never delete
                self.delete_state(project_id, s["id"])
                by_name.pop(name, None)

        return {name: by_name[name]["id"] for name in CANONICAL_ORDER}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_plane_admin.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/plane_admin.py tests/test_plane_admin.py
git commit -m "feat(northstar): ensure_board reconcile to the 8 canonical states"
```

---

## Task 3: Rewire `add_project` to use `PlaneAdmin`

**Files:**
- Modify: `northstar/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `PlaneAdmin` (Tasks 1-2).
- Produces (changes):
  - `ProjectInputs` gains `plane_new_project: bool = False`, `plane_project_name: str = ""`,
    `plane_identifier: str = ""`; `plane_project_id: str` stays (used on the existing path, may be "").
  - `write_project_config(inp, state_ids, mcp_path, project_id)` — now takes the resolved
    `project_id` explicitly (a new project's id isn't on `inp`).
  - `add_project(inp, *, runner=run, create_if_missing=False, admin=None) -> dict` — uses
    `PlaneAdmin`: creates the project (new path) or uses `inp.plane_project_id` (existing path),
    runs `ensure_board`, then the unchanged GitHub-repo + guardrails + config + register steps.
  - `discover_state_ids` is REMOVED (replaced by `ensure_board`).

- [ ] **Step 1: Update the existing tests + add new ones** in `tests/test_project.py`

Replace the `FakePlane` class and the two `add_project`/`discover_state_ids` tests with these:

```python
from northstar.plane_admin import CANONICAL_ORDER


class FakeAdmin:
    def __init__(self):
        self.created = None
        self.ensured = None

    def create_project(self, name, identifier, description=""):
        self.created = (name, identifier)
        return {"id": "newproj"}

    def ensure_board(self, project_id, *, fresh):
        self.ensured = (project_id, fresh)
        return {n: f"sid-{n}" for n in CANONICAL_ORDER}


def test_add_project_existing_runs_ensure_board_and_writes_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    from northstar.proc import CommandResult
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/acme", repo_dir=repo,
        lint_cmd="make lint", build_cmd="make build", test_cmd="make test")
    admin = FakeAdmin()
    runner = lambda cmd, **kw: CommandResult(0, "", "")  # gh ok everywhere
    meta = project.add_project(inp, runner=runner, admin=admin)
    assert admin.ensured == ("p", False)        # existing -> fresh False, project id "p"
    import yaml
    data = yaml.safe_load(paths.project_config_path("acme").read_text())
    assert data["plane_project_id"] == "p"
    assert data["state_ids"]["QA"] == "sid-QA"
    assert "acme" in paths.list_projects()


def test_add_project_new_creates_plane_project_then_ensures_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    from northstar.proc import CommandResult
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    inp = project.ProjectInputs(
        name="acme", plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="", github_repo="o/acme", repo_dir=repo,
        lint_cmd="l", build_cmd="b", test_cmd="t",
        plane_new_project=True, plane_project_name="Acme", plane_identifier="ACME")
    admin = FakeAdmin()
    project.add_project(inp, runner=lambda c, **k: CommandResult(0, "", ""), admin=admin)
    assert admin.created == ("Acme", "ACME")
    assert admin.ensured == ("newproj", True)   # new -> fresh True, id from create_project
    data = __import__("yaml").safe_load(paths.project_config_path("acme").read_text())
    assert data["plane_project_id"] == "newproj"
```

Keep the existing `test_add_project_aborts_when_gh_unauthenticated` and
`test_add_project_clones_existing_repo_when_not_local` tests, but change each to pass `admin=FakeAdmin()`
(instead of the removed `client=FakePlane()`), and ensure their `ProjectInputs` set `plane_project_id="p"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_project.py -v`
Expected: FAIL (ProjectInputs lacks the new fields / `add_project` has no `admin` param)

- [ ] **Step 3: Update `northstar/project.py`**

Change `ProjectInputs` to add the three fields:

```python
@dataclass
class ProjectInputs:
    name: str
    plane_base_url: str
    plane_api_key: str
    plane_workspace_slug: str
    plane_project_id: str
    github_repo: str
    repo_dir: Path
    lint_cmd: str
    build_cmd: str
    test_cmd: str
    claude_model: str = "claude-opus-4-8"
    poll_interval_seconds: int = 30
    max_concurrency: int = 1
    plane_new_project: bool = False
    plane_project_name: str = ""
    plane_identifier: str = ""
```

Delete the old `discover_state_ids` function. Update `write_project_config` to take `project_id`:

```python
def write_project_config(inp: "ProjectInputs", state_ids: dict, mcp_path: Path,
                         project_id: str) -> Path:
    cfg = {
        "plane_base_url": inp.plane_base_url,
        "plane_api_key": inp.plane_api_key,
        "plane_workspace_slug": inp.plane_workspace_slug,
        "plane_project_id": project_id,
        "github_repo": inp.github_repo,
        "repo_dir": str(inp.repo_dir),
        "worktrees_root": str(paths.home() / "worktrees" / inp.name),
        "poll_interval_seconds": inp.poll_interval_seconds,
        "claude_binary": "claude",
        "claude_model": inp.claude_model,
        "mcp_config_path": str(mcp_path),
        "templates_dir": str(templates_dir()),
        "max_concurrency": inp.max_concurrency,
        "state_ids": state_ids,
    }
    out = paths.project_config_path(inp.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=True))
    return out
```

Rewrite `add_project` (and add the import `from northstar.plane_admin import PlaneAdmin`):

```python
def add_project(inp: "ProjectInputs", *, runner=run, create_if_missing=False, admin=None) -> dict:
    if not runner(["gh", "auth", "status"]).ok:
        raise RuntimeError("GitHub not reachable — run: gh auth login")

    admin = admin or PlaneAdmin(inp.plane_base_url, inp.plane_api_key, inp.plane_workspace_slug)
    if inp.plane_new_project:
        project_id = admin.create_project(inp.plane_project_name, inp.plane_identifier)["id"]
        fresh = True
    else:
        project_id = inp.plane_project_id
        fresh = False
    state_ids = admin.ensure_board(project_id, fresh=fresh)

    if not repo_exists(inp.github_repo, runner=runner):
        if not create_if_missing:
            raise RuntimeError(
                f"repo {inp.github_repo} not found; pass create_if_missing=True to create it")
        create_repo(inp.github_repo, inp.repo_dir, runner=runner)
    else:
        if not Path(inp.repo_dir).exists():
            runner(["gh", "repo", "clone", inp.github_repo, str(inp.repo_dir)])

    install_guardrails(inp.repo_dir, inp.name, inp.lint_cmd, inp.build_cmd, inp.test_cmd)
    mcp_path = paths.home() / "plane-mcp.json"
    write_project_config(inp, state_ids, mcp_path, project_id)
    meta = {"github_repo": inp.github_repo, "repo_dir": str(inp.repo_dir),
            "plane_project_id": project_id}
    paths.register_project(inp.name, meta)
    return meta
```

Remove the now-unused `from orchestrator.plane import PlaneClient` import if `discover_state_ids`
was its only user (verify with `grep -n PlaneClient northstar/project.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_project.py -v`
Expected: PASS (all project tests green)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (engine + northstar, ~ existing count adjusted for the swapped tests)

- [ ] **Step 6: Commit**

```bash
git add northstar/project.py tests/test_project.py
git commit -m "feat(northstar): add_project sets up the Plane project + board via PlaneAdmin"
```

---

## Task 4: CLI — new-vs-existing Plane project in `project add`

**Files:**
- Modify: `northstar/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `project.ProjectInputs`/`add_project` (Task 3).
- Produces: `project add` accepts `--new-plane-project/--existing-plane-project` plus
  `--plane-project-name`, `--plane-identifier`, and (existing) `--plane-project-id`, building the
  right `ProjectInputs`. Interactive prompts mirror these.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`)

```python
def test_project_add_new_plane_project_builds_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.cli as cli; importlib.reload(cli)
    captured = {}
    monkeypatch.setattr(cli.project, "add_project",
                        lambda inp, **kw: captured.setdefault("inp", inp) or {"github_repo": inp.github_repo})
    result = runner.invoke(cli.app, [
        "project", "add",
        "--name", "acme", "--plane-base-url", "https://x", "--plane-api-key", "k",
        "--plane-workspace-slug", "w", "--github-repo", "o/acme",
        "--repo-dir", str(tmp_path / "repo"),
        "--lint-cmd", "l", "--build-cmd", "b", "--test-cmd", "t",
        "--new-plane-project", "--plane-project-name", "Acme", "--plane-identifier", "ACME",
    ])
    assert result.exit_code == 0
    inp = captured["inp"]
    assert inp.plane_new_project is True
    assert inp.plane_project_name == "Acme" and inp.plane_identifier == "ACME"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -k new_plane_project -v`
Expected: FAIL (option not recognized / attribute missing)

- [ ] **Step 3: Update `project_add` in `northstar/cli.py`**

```python
@project_app.command("add")
def project_add(
    name: str = typer.Option(..., prompt=True),
    plane_base_url: str = typer.Option(..., prompt=True),
    plane_api_key: str = typer.Option(..., prompt=True, hide_input=True),
    plane_workspace_slug: str = typer.Option(..., prompt=True),
    new_plane_project: bool = typer.Option(False, "--new-plane-project/--existing-plane-project",
                                           prompt="Create a NEW Plane project?"),
    plane_project_id: str = typer.Option("", "--plane-project-id"),
    plane_project_name: str = typer.Option("", "--plane-project-name"),
    plane_identifier: str = typer.Option("", "--plane-identifier"),
    github_repo: str = typer.Option(..., prompt="GitHub repo (owner/name)"),
    repo_dir: Path = typer.Option(..., prompt="Local path for the repo"),
    lint_cmd: str = typer.Option("npm run lint", prompt=True),
    build_cmd: str = typer.Option("npm run build", prompt=True),
    test_cmd: str = typer.Option("npm test", prompt=True),
    create_if_missing: bool = typer.Option(False, "--create"),
):
    """Add or link a project (sets up the Plane project + board)."""
    if new_plane_project:
        if not plane_project_name:
            plane_project_name = typer.prompt("Plane project name")
        if not plane_identifier:
            plane_identifier = typer.prompt("Plane project identifier (short, UPPERCASE)")
    else:
        if not plane_project_id:
            plane_project_id = typer.prompt("Existing Plane project id")
    inp = project.ProjectInputs(
        name=name, plane_base_url=plane_base_url, plane_api_key=plane_api_key,
        plane_workspace_slug=plane_workspace_slug, plane_project_id=plane_project_id,
        github_repo=github_repo, repo_dir=repo_dir,
        lint_cmd=lint_cmd, build_cmd=build_cmd, test_cmd=test_cmd,
        plane_new_project=new_plane_project, plane_project_name=plane_project_name,
        plane_identifier=plane_identifier)
    meta = project.add_project(inp, create_if_missing=create_if_missing)
    typer.echo(f"added {name}: {meta['github_repo']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all green)

- [ ] **Step 6: Update the usage doc** — in `docs/northstar-usage.md`, under "Add a project", note that `project add` now creates the Plane project + board:

```markdown
`project add` now sets up Plane for you: choose **new** (it creates the project and the 8-state
board) or **existing** (it reconciles the board to the 8 states on a project id you provide).
```

- [ ] **Step 6b: Commit**

```bash
git add northstar/cli.py tests/test_cli.py docs/northstar-usage.md
git commit -m "feat(northstar): project add creates/selects the Plane project + board"
```

---

## Self-Review notes (addressed)

- **Spec §2 (create project + state CRUD via X-API-Key):** Task 1.
- **Spec §3a (new-project reconcile: 3 renames + Cancelled→Blocked + 3 creates):** Task 2 `ensure_board(fresh=True)` + its test.
- **Spec §3b (existing-project: create missing, never delete states with items/default):** Task 2 `ensure_board(fresh=False)` + its test (non-empty extra not deleted).
- **Spec §4 (PlaneAdmin interface):** Tasks 1-2 (all methods + ensure_board).
- **Spec §5 (project add new-vs-existing branch; discover_state_ids removed; project_id threaded):** Tasks 3 + 4.
- **Spec §6 (success criteria):** Task 1 (create_project), Task 2 (both ensure_board paths), Task 3 (new-path config has 8 state_ids), full-suite runs in Tasks 3-4; engine untouched (no `orchestrator/` edits in any task).
- **Spec §7 (out of scope):** no cycles/labels/triage/group-reorder/seed/machine-level-creds work included.
- **Global constraints:** engine untouched; `X-API-Key`; canonical groups exact; Blocked→started, Deployed→completed; safe reconcile; no Anthropic imports.
