# Efficiency Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the audit's high-value fixes — engine resilience (retry/backoff so a transient Plane error can't kill the daemon), lower per-session token cost (de-dupe role-doc hydration, cap comment reads, trim the launch prompt), and friendlier/cheaper CLI — without changing behavior except as noted.

**Architecture:** Five independent edits across disjoint files: (1) engine `orchestrator/`, (2) role docs `templates/`, (3) CLI load helper, (4) `PlaneAdmin` errors, (5) skill install. All run in parallel; each is unit-tested; the full suite (69) stays green.

**Tech Stack:** Python 3.11+, httpx, respx, PyYAML, Typer, pytest.

## Global Constraints

- No behavior change to the board state machine, Plane reconcile, or the public CLI command surface — these are tightenings.
- No Anthropic/SDK imports. Tests inject fakes; no real network/subprocess/sleep in tests (inject `sleep`).
- Full existing suite (69) stays green.

---

## File Structure (touched)

```
orchestrator/plane.py poller.py dispatch.py launcher.py   # Task 1 (engine)
templates/CLAUDE.md.tmpl builder.md reviewer.md qa.md       # Task 2 (role docs)
northstar/paths.py cli.py                                   # Task 3 (load_project)
northstar/plane_admin.py                                    # Task 4 (friendly errors)
northstar/skills.py                                         # Task 5 (skip-if-present)
tests/test_plane.py test_poller.py test_launcher.py
tests/test_role_docs.py test_paths.py test_cli.py test_plane_admin.py test_skills.py
```

---

## Task 1: Engine resilience + efficiency

**Files:**
- Modify: `orchestrator/plane.py`, `orchestrator/poller.py`, `orchestrator/dispatch.py`, `orchestrator/launcher.py`
- Test: `tests/test_plane.py`, `tests/test_poller.py`, `tests/test_launcher.py`

**Interfaces:**
- `PlaneClient.__init__` gains `sleep=time.sleep`, `max_retries=3`; all HTTP routes through `_send` (retries 429/5xx/timeout). `list_issues_in_state(state_id, per_page=25)`.
- `poller.run` wraps `poll_once` in try/except and builds one `PlaneClient`, injecting it via `make_dispatch(cfg, ownership, plane=client)`. `poll_once` early-returns once `ownership.count() >= cfg.max_concurrency`.
- `build_claude_command(cfg, role, ticket_id, role_doc_text)` — the unused `worktree` param is removed; the `-p` prompt no longer restates hydration. Launcher caches role docs.

- [ ] **Step 1: Write failing tests**

`tests/test_plane.py` (append):
```python
def test_send_retries_on_5xx_then_succeeds():
    import httpx, respx
    from orchestrator.plane import PlaneClient
    slept = []
    with respx.mock:
        route = respx.get("https://x/api/v1/workspaces/w/projects/p/states/")
        route.side_effect = [httpx.Response(503), httpx.Response(200, json={"results": [{"id": "s1", "name": "Draft"}], "next_cursor": None})]
        c = PlaneClient("https://x", "k", "w", "p", client=httpx.Client(), sleep=lambda d: slept.append(d))
        assert c.list_states() == {"Draft": "s1"}
        assert route.call_count == 2 and slept  # retried and slept once
```

`tests/test_poller.py` (append):
```python
def test_run_survives_poll_exception(monkeypatch):
    from orchestrator import poller
    from orchestrator.config import Config
    from pathlib import Path
    cfg = Config(plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
                 github_repo="o/r", repo_dir=Path("/t"), worktrees_root=Path("/t"), poll_interval_seconds=0,
                 claude_binary="claude", claude_model="m", mcp_config_path=Path("/t/m.json"),
                 templates_dir=Path("/t"), state_ids={}, max_concurrency=1)
    class BoomClient:
        def list_issues_in_state(self, s): raise RuntimeError("plane down")
    calls = {"n": 0}
    def fake_sleep(_): calls["n"] += 1
    # must NOT raise; loop runs max_iterations then returns
    poller.run(cfg, client=BoomClient(), dispatch=lambda i, r: None, sleep=fake_sleep, max_iterations=2)
    assert calls["n"] == 2


def test_poll_once_short_circuits_when_full():
    from orchestrator.poller import Ownership, poll_once
    from orchestrator.config import Config
    from pathlib import Path
    cfg = Config(plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
                 github_repo="o/r", repo_dir=Path("/t"), worktrees_root=Path("/t"), poll_interval_seconds=0,
                 claude_binary="c", claude_model="m", mcp_config_path=Path("/t/m.json"), templates_dir=Path("/t"),
                 state_ids={"Ready to Dev": "s", "In Progress": "s2", "Review": "s3", "QA": "s4"}, max_concurrency=1)
    own = Ownership(); own.claim("already")
    listed = []
    class C:
        def list_issues_in_state(self, s): listed.append(s); return []
    poll_once(C(), cfg, own, lambda i, r: None)
    assert listed == []  # full -> no list calls at all
```

`tests/test_launcher.py` (replace `test_build_command_includes_required_flags`):
```python
def test_build_command_drops_worktree_and_trims_prompt(tmp_path):
    from orchestrator.launcher import build_claude_command
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "builder", "i1", "ROLE TEXT")  # no worktree arg
    assert "ROLE TEXT" in cmd and "stream-json" in cmd and "bypassPermissions" in cmd
    p = cmd[cmd.index("-p") + 1]
    assert "i1" in p and "builder" in p
    assert "hydrat" not in p.lower() and "comment" not in p.lower()  # prompt no longer restates hydration
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_plane.py::test_send_retries_on_5xx_then_succeeds tests/test_poller.py -k "survives or short_circuit" tests/test_launcher.py -k drops_worktree -v`
Expected: FAIL (new behavior absent; build_claude_command still takes worktree)

- [ ] **Step 3: Implement — `orchestrator/plane.py`**

Add at top: `import time` and `_RETRY_STATUS = {429, 500, 502, 503, 504}`. Update `__init__` and route calls through `_send`:

```python
    def __init__(self, base_url, api_key, workspace_slug, project_id,
                 client: httpx.Client | None = None, sleep=time.sleep, max_retries=3):
        self._prefix = f"{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})
        self._sleep = sleep
        self._max_retries = max_retries

    def _send(self, method, url, **kw):
        delay = 0.5
        for attempt in range(self._max_retries):
            try:
                resp = self._http.request(method, url, **kw)
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == self._max_retries - 1:
                    raise
                self._sleep(delay); delay *= 2; continue
            if resp.status_code in _RETRY_STATUS and attempt < self._max_retries - 1:
                self._sleep(delay); delay *= 2; continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp
```

Change `_paginate` to use `self._send("GET", url, params=params)`; `add_comment` → `self._send("POST", ..., json={"comment_html": body_html})`; `set_state` → `self._send("PATCH", ..., json={"state": state_id})`. Add `per_page` to the issue list:

```python
    def list_issues_in_state(self, state_id: str, per_page: int = 25) -> list[Issue]:
        rows = self._paginate(f"{self._prefix}/work-items/", {"state": state_id, "per_page": per_page})
        return [self._parse_issue(r) for r in rows if r.get("state") == state_id]
```

- [ ] **Step 4: Implement — `orchestrator/poller.py`**

Add `import sys`. In `poll_once`, hoist the cap check to the top of the state loop:
```python
def poll_once(client, cfg, ownership, dispatch) -> None:
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
            ownership.claim(issue.id)
            dispatch(issue, role)
```
In `run`, build one client, inject it into dispatch, and guard `poll_once`:
```python
def run(cfg, *, client=None, dispatch=None, sleep=time.sleep, max_iterations=None) -> None:
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
```

- [ ] **Step 5: Implement — `orchestrator/launcher.py`**

Add a role-doc cache and drop the `worktree` param + hydration restatement:
```python
_ROLE_DOC_CACHE: dict[str, str] = {}

def _role_doc_text(cfg: Config, role: str) -> str:
    if role not in _ROLE_DOC_CACHE:
        _ROLE_DOC_CACHE[role] = role_doc_path(cfg, role).read_text()
    return _ROLE_DOC_CACHE[role]


def build_claude_command(cfg, role, ticket_id, role_doc_text) -> list[str]:
    prompt = f"You are the {role} for Plane work item {ticket_id}. Follow your role instructions."
    return [
        cfg.claude_binary, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--mcp-config", str(cfg.mcp_config_path),
        "--model", cfg.claude_model,
        "--max-turns", str(cfg.max_turns),
        "--append-system-prompt", role_doc_text,
    ]
```
In `run_session`, replace `role_doc_text = role_doc_path(cfg, role).read_text()` with `role_doc_text = _role_doc_text(cfg, role)`, and call `build_claude_command(cfg, role, ticket_id, role_doc_text)` (no worktree arg).

- [ ] **Step 6: Implement — `orchestrator/dispatch.py`**

No change needed (it already accepts `plane=None`). Confirm `make_dispatch(cfg, ownership, plane=client)` works.

- [ ] **Step 7: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_plane.py tests/test_poller.py tests/test_launcher.py -v && .venv/bin/pytest -q`
Expected: PASS (all green)

- [ ] **Step 8: Commit**

```bash
git add orchestrator/plane.py orchestrator/poller.py orchestrator/launcher.py tests/test_plane.py tests/test_poller.py tests/test_launcher.py
git commit -m "perf(engine): Plane retry/backoff, daemon survives poll errors, poll short-circuit, cache role docs, shared client, trim prompt"
```

---

## Task 2: Role docs — de-dupe hydration, cap comment reads, fix tag, idempotency guard

**Files:**
- Modify: `templates/CLAUDE.md.tmpl`, `templates/builder.md`, `templates/reviewer.md`, `templates/qa.md`
- Test: `tests/test_role_docs.py` (create)

**Interfaces:** documentation; the launcher loads these. The grep test pins the key invariants.

- [ ] **Step 1: Write the failing test** `tests/test_role_docs.py`

```python
from pathlib import Path

T = Path("templates")
def read(n): return (T / n).read_text()


def test_hydration_recipe_lives_in_claude_md():
    c = read("CLAUDE.md.tmpl").lower()
    assert "comment trail" in c and "pr thread" in c and "git history" in c and "docs/" in c


def test_comment_reading_is_capped_not_full_every_time():
    for role in ("builder.md", "reviewer.md", "qa.md"):
        assert "since your last state move" in read(role).lower()


def test_builder_tag_is_not_malformed():
    b = read("builder.md")
    assert "→ <FROM-STATE>" not in b  # the broken FROM->FROM example is gone


def test_builder_and_qa_have_idempotency_guard():
    for role in ("builder.md", "qa.md"):
        assert "already moved" in read(role).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_role_docs.py -v`
Expected: FAIL

- [ ] **Step 3: Edit `templates/CLAUDE.md.tmpl`** — ensure the hydration recipe lives here (one place). Under the "Memory layer" section, make sure it explicitly says (add if missing):

```markdown
## Context hydration (every session)
Each session is fresh and stateless. Before acting, reconstruct context from: the Plane ticket, its
comment trail, the GitHub PR thread, the repo `docs/` memory, and git history. Read the **latest**
comment and any comments **since your last state move**; skim earlier trail only if needed. Act on the
latest comment for what to do next; use the rest for how.
```

- [ ] **Step 4: Edit `templates/builder.md`** —
  - Replace the malformed first tag line (`🤖 [builder] <FROM-STATE> → <FROM-STATE>: context loaded …`) with: `🤖 [builder] context loaded — <1-line summary of where the ticket stands>` (a context note, not a transition).
  - Replace the Step-1 full hydration recipe with one line: "Hydrate context per CLAUDE.md (latest comment + since your last state move; the PR thread holds detailed review feedback on a rework)."
  - Add an idempotency guard line: "Before any state transition, check the ticket's current state; if it has **already moved** past where you expect, stop — do not re-post or re-move."
  - Remove the restated `karpathy-guidelines` line in the Build step (it's always-on via CLAUDE.md).
  - Compress the opening "No human is watching…" paragraph to one sentence.

- [ ] **Step 5: Edit `templates/reviewer.md`** — replace its Step-1 hydration recipe with: "Hydrate context per CLAUDE.md (latest comment + since your last state move); fetch the PR diff + thread via `gh`." Keep the rest.

- [ ] **Step 6: Edit `templates/qa.md`** —
  - Replace its Step-1 hydration recipe with: "Hydrate context per CLAUDE.md (latest comment + since your last state move); extract the ticket's **acceptance criteria** — these are what you verify."
  - Add the same idempotency guard line ("if the ticket has **already moved** past QA, stop").
  - Collapse the duplicated "don't just re-run the builder's unit tests / verify real behavior" into one sentence.

- [ ] **Step 7: Run the test + full suite**

Run: `.venv/bin/pytest tests/test_role_docs.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add templates/CLAUDE.md.tmpl templates/builder.md templates/reviewer.md templates/qa.md tests/test_role_docs.py
git commit -m "perf(prompts): de-dupe hydration to CLAUDE.md, cap comment reads, fix tag, add idempotency guard"
```

---

## Task 3: CLI single project load

**Files:**
- Modify: `northstar/paths.py`, `northstar/cli.py`
- Test: `tests/test_paths.py`, `tests/test_cli.py`

**Interfaces:**
- Produces in `paths.py`: `ProjectRuntime` dataclass (`name`, `meta: dict`, `repo_dir: Path`, `cfg_path: Path`, `plane_env: dict`, `cfg: dict`) and `load_project(name) -> ProjectRuntime` (parses registry + per-project config at most once each).
- `cli.py` `start/restart/status/logs` use `load_project`; the old `_plane_env`/`_repo_dir` helpers are removed.

- [ ] **Step 1: Write the failing test** `tests/test_paths.py` (append)

```python
def test_load_project_reads_each_file_once(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    p.register_project("acme", {"github_repo": "o/acme", "repo_dir": "/tmp/acme"})
    p.project_config_path("acme").write_text(
        "plane_api_key: K\nplane_base_url: https://x\nplane_workspace_slug: w\nrepo_dir: /tmp/acme\n")
    rt = p.load_project("acme")
    assert rt.repo_dir == __import__("pathlib").Path("/tmp/acme")
    assert rt.plane_env == {"PLANE_API_KEY": "K", "PLANE_BASE_URL": "https://x", "PLANE_WORKSPACE_SLUG": "w"}
    assert rt.cfg_path == p.project_config_path("acme")
```

`tests/test_cli.py` (append):
```python
def test_start_uses_load_project(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    paths.ensure_dirs(); paths.register_project("acme", {"repo_dir": str(tmp_path / "repo")})
    paths.project_config_path("acme").write_text(
        "plane_api_key: K\nplane_base_url: https://x\nplane_workspace_slug: w\nrepo_dir: " + str(tmp_path / "repo") + "\n")
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli.supervisor, "start",
                        lambda name, repo_dir, plane_env, **kw: seen.update(repo_dir=repo_dir, env=plane_env))
    result = runner.invoke(cli.app, ["start", "acme"])
    assert result.exit_code == 0
    assert seen["env"]["PLANE_API_KEY"] == "K"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_paths.py -k load_project tests/test_cli.py -k start_uses -v`
Expected: FAIL (`load_project` missing)

- [ ] **Step 3: Implement `load_project` in `northstar/paths.py`**

```python
from dataclasses import dataclass


@dataclass
class ProjectRuntime:
    name: str
    meta: dict
    repo_dir: Path
    cfg_path: Path
    plane_env: dict
    cfg: dict


def load_project(name: str) -> ProjectRuntime:
    meta = list_projects().get(name, {})
    cfg_path = project_config_path(name)
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    repo_dir = Path(meta.get("repo_dir") or cfg.get("repo_dir", ""))
    plane_env = {
        "PLANE_API_KEY": cfg.get("plane_api_key", ""),
        "PLANE_BASE_URL": cfg.get("plane_base_url", ""),
        "PLANE_WORKSPACE_SLUG": cfg.get("plane_workspace_slug", ""),
    }
    return ProjectRuntime(name, meta, repo_dir, cfg_path, plane_env, cfg)
```

- [ ] **Step 4: Refactor `northstar/cli.py`**

Remove `_plane_env` and `_repo_dir`. Update the run commands:
```python
@app.command()
def start(name: str):
    rt = paths.load_project(name)
    supervisor.start(name, rt.repo_dir, rt.plane_env)
    typer.echo(f"started ns-{name}")


@app.command()
def stop(name: str):
    supervisor.stop(name)
    typer.echo(f"stopped ns-{name}")


@app.command()
def restart(name: str):
    rt = paths.load_project(name)
    supervisor.restart(name, rt.repo_dir, rt.plane_env)
    typer.echo(f"restarted ns-{name}")
```
(`status` and `logs` already use `paths.list_projects()` / `supervisor.logs_command` — leave them, or switch `logs`'s implicit needs to `load_project` if it referenced `_repo_dir`. It does not.)

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_paths.py tests/test_cli.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add northstar/paths.py northstar/cli.py tests/test_paths.py tests/test_cli.py
git commit -m "perf(cli): single load_project helper (parse each file once)"
```

---

## Task 4: Friendly Plane errors in `PlaneAdmin`

**Files:**
- Modify: `northstar/plane_admin.py`
- Test: `tests/test_plane_admin.py`

**Interfaces:** `PlaneAdmin._request(method, url, **kw) -> httpx.Response` wraps every call; on an httpx connect/timeout/`HTTPStatusError` it raises `RuntimeError` with status + URL + hint. All CRUD methods route through it.

- [ ] **Step 1: Write the failing test** `tests/test_plane_admin.py` (append)

```python
@respx.mock
def test_create_project_friendly_error_on_401():
    respx.post(f"{WPREFIX}/projects/").mock(return_value=httpx.Response(401, json={}))
    try:
        admin().create_project("Web", "WEB")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "401" in str(e) and "projects/" in str(e)
    except Exception as e:
        assert False, f"expected RuntimeError, got {type(e).__name__}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_plane_admin.py -k friendly_error -v`
Expected: FAIL (raises raw `HTTPStatusError`, not `RuntimeError`)

- [ ] **Step 3: Implement `_request` in `northstar/plane_admin.py`**

Add the wrapper and route the CRUD methods through it:
```python
    def _request(self, method, url, **kw):
        try:
            r = self._http.request(method, url, **kw)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Plane API returned {e.response.status_code} at {url} — "
                "check the API key, base URL, and project permissions") from e
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise RuntimeError(f"Plane API unreachable at {url}: {e}") from e
```
Replace the `self._http.post/get/patch/delete(...) ; r.raise_for_status()` bodies in `create_project`, `list_states` (its `_http.get`), `create_state`, `update_state`, `delete_state`, `state_has_items` with `self._request("POST"/"GET"/"PATCH"/"DELETE", url, **kw)` (and drop the now-redundant `raise_for_status()` calls — `_request` does it).

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_plane_admin.py -v && .venv/bin/pytest -q`
Expected: PASS (existing plane_admin tests still pass — `_request` returns the same Response on success)

- [ ] **Step 5: Commit**

```bash
git add northstar/plane_admin.py tests/test_plane_admin.py
git commit -m "feat(cli): friendly RuntimeError on Plane API failures instead of raw httpx tracebacks"
```

---

## Task 5: Skill install — skip already-present plugins

**Files:**
- Modify: `northstar/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:** `install_all` calls `installed_plugins()` once; for each plugin it **skips `install` when already present** and runs `update`; native installers unchanged.

- [ ] **Step 1: Write the failing test** `tests/test_skills.py` (append)

```python
def test_install_all_skips_install_for_present_plugin():
    import json as _json
    from northstar.proc import CommandResult
    present = _json.dumps([{"name": "superpowers", "version": "6.0.2"}])
    calls = []
    def runner(cmd, **kw):
        joined = cmd if isinstance(cmd, str) else " ".join(cmd)
        calls.append(joined)
        if "plugin list --json" in joined:
            return CommandResult(0, present, "")
        return CommandResult(0, "", "")
    skills.install_all(runner=runner)
    # superpowers is present -> no install line for it, but an update line yes
    assert not any("plugin install superpowers@" in c for c in calls)
    assert any("plugin update superpowers@" in c for c in calls)
    # an absent plugin (frontend-design) IS installed
    assert any("plugin install frontend-design@" in c for c in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_skills.py -k skips_install -v`
Expected: FAIL (install runs unconditionally today)

- [ ] **Step 3: Implement in `northstar/skills.py`**

```python
def install_all(runner=run) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for src in marketplaces():
        runner(["claude", "plugin", "marketplace", "add", src])
    runner(["claude", "plugin", "marketplace", "update"])
    present = installed_plugins(runner=runner)
    for p in PLUGINS:
        ref = f"{p.name}@{p.marketplace}"
        if p.name not in present:
            runner(["claude", "plugin", "install", ref, "--scope", "user"])
        upd = runner(["claude", "plugin", "update", ref, "--scope", "user"])
        results.append((p.name, upd.ok, "plugin"))
    for n in NATIVE:
        res = runner(n.cmd, shell=True)
        results.append((n.name, res.ok, "native"))
    return results
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_skills.py -v && .venv/bin/pytest -q`
Expected: PASS (the original `test_install_all_runs_marketplace_then_install_then_update_then_native` still passes — its runner returns `[]` for list, so all plugins are "absent" and still installed)

- [ ] **Step 5: Commit**

```bash
git add northstar/skills.py tests/test_skills.py
git commit -m "perf(cli): skill install skips plugins already present"
```

---

## Self-Review notes (addressed)

- **Spec §2.1 (retry/backoff + daemon survives):** Task 1 (`_send` retry + `run` try/except), tested.
- **Spec §2.2 (poll short-circuit + per_page):** Task 1 `poll_once` early return + `list_issues_in_state(per_page=)`, tested.
- **Spec §2.3 (cache role docs, share client, drop worktree param):** Task 1 (`_role_doc_text`, `make_dispatch(plane=client)`, signature change), tested.
- **Spec §3.1–3.4 (hydration to CLAUDE.md, cap comments, trim -p, fix tag, idempotency guard, drop karpathy/compress):** Task 2 (doc edits) + the `-p` trim in Task 1; `tests/test_role_docs.py` + the launcher prompt test.
- **Spec §4.1 (single project load):** Task 3.
- **Spec §4.2 (friendly Plane errors):** Task 4.
- **Spec §4.3 (skip-if-present skill install):** Task 5.
- **Spec §4.4 (wire detect_build_commands):** DEFERRED — entangled with repo-clone ordering (repo isn't local until `add_project` clones it); noted, not built. (Updated the spec's intent: this LOW item is deferred rather than forced.)
- **Spec §5 (success criteria):** retry test (T1), daemon-survives test (T1), doc-content tests (T2), friendly-error test (T4), load_project test (T3), skip-install test (T5); full suite stays green via the per-task full-suite runs.
- **Spec §6 (out of scope):** no concurrency, no HTTP-base share, no doctor concurrency, no state-machine/reconcile/command-surface change — none included.
- **Disjoint files** confirmed: T1 `orchestrator/*` + its 3 test files; T2 `templates/*` + test_role_docs; T3 `paths.py`/`cli.py` + their tests; T4 `plane_admin.py` + test; T5 `skills.py` + test. No two tasks touch the same file → parallel-safe.
