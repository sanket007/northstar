# Orchestrator MVP Vertical Slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python daemon that polls a self-hosted Plane board, and for each actionable work item launches a real Claude Code CLI session (builder / reviewer / QA) in an isolated git worktree to take one task from "Ready to Dev" all the way to a merged GitHub PR, leaving a full comment trail.

**Architecture:** Two processes. (1) A stateless-friendly Python daemon (`orchestrator/`) that talks to Plane over REST, manages git worktrees, and shells out to the `claude` binary — it imports nothing from Anthropic. (2) Real Claude Code sessions whose behavior is defined entirely by markdown role docs + a per-project `CLAUDE.md` + guardrail hooks. The daemon is a director; all code work happens inside the sessions.

**Tech Stack:** Python 3.11+, `httpx` (Plane REST), `PyYAML` (config), `pytest` + `respx` (tests), `git` worktrees, the `claude` CLI, `gh` CLI, the `plane-mcp-server` MCP server.

## Global Constraints

Every task implicitly includes these (copied verbatim from the spec):

- **No Claude Agent SDK / no Anthropic imports.** The daemon only ever shells out to the `claude` binary. No `claude-agent-sdk` / `@anthropic-ai/*` dependency anywhere.
- **Plane REST uses the `/work-items/` paths, never the deprecated `/issues/` paths** (legacy `/issues/` support ended 2026-03-31). Auth header is `X-API-Key`. Base path: `{base}/api/v1/workspaces/{slug}/projects/{project_id}/...`. Pagination is cursor-based.
- **Canonical board states** (exact strings): `Draft`, `Ready to Dev`, `In Progress`, `Review`, `QA`, `Completed`, `Blocked`, `Deployed`.
- **Concurrency = 1** for this slice (config key `max_concurrency`, default 1). The mechanism must generalize to 5.
- **Merge only happens after QA passes** — the reviewer never merges.
- **`--permission-mode bypassPermissions` is used only inside an isolated worktree**, never the main checkout.
- **Every agent comment is append-only, self-contained, and machine-tagged** `🤖 [role] FROM-STATE → TO-STATE`.
- **Full context hydration before any action**: ticket + all comments + PR thread + `docs/` memory + git history.

---

## File Structure

```
agentic-dev-framework/
  pyproject.toml                 # package + deps + pytest config
  config.example.yaml            # documented sample config
  plane-mcp.json                 # MCP config attached to every session
  orchestrator/
    __init__.py
    config.py                    # Config dataclass + load_config()
    plane.py                     # PlaneClient (REST), Issue/Comment dataclasses
    state_machine.py             # canonical states, role_for_state(), is_allowed()
    worktree.py                  # create_worktree() / remove_worktree()
    launcher.py                  # build_claude_command(), parse_stream_json(), run_session()
    poller.py                    # Ownership, poll_once(), run()
    __main__.py                  # entrypoint: load config → poller.run()
  templates/
    CLAUDE.md.tmpl               # per-project context loaded by every session
    builder.md                   # builder role instructions
    reviewer.md                  # reviewer role instructions
    qa.md                        # QA role instructions
    claude-settings.json         # guardrail hooks + deny rules
    hooks/precommit_gate.sh      # lint+build+test+memory gate (exit 2 on fail)
  tests/
    test_config.py
    test_plane.py
    test_state_machine.py
    test_worktree.py
    test_launcher.py
    test_poller.py
  docs/
    sandbox-setup.md             # how to stand up the throwaway repo + Plane project
    e2e-walkthrough.md           # the walk-away / negative / QA-catch test procedure
```

Responsibilities are split so each file holds one concern and is independently testable. The daemon modules (`config`, `plane`, `state_machine`, `worktree`, `launcher`, `poller`) are unit-tested with fakes; the markdown role docs and templates are validated by review + the end-to-end run, since launching real `claude` sessions can't be unit-tested.

---

## Task 1: Project scaffolding & config loader

**Files:**
- Create: `pyproject.toml`
- Create: `orchestrator/__init__.py` (empty)
- Create: `orchestrator/config.py`
- Test: `tests/test_config.py`
- Create: `config.example.yaml`

**Interfaces:**
- Produces: `Config` dataclass with fields `plane_base_url: str`, `plane_api_key: str`, `plane_workspace_slug: str`, `plane_project_id: str`, `github_repo: str`, `worktrees_root: Path`, `repo_dir: Path`, `poll_interval_seconds: int`, `claude_binary: str`, `claude_model: str`, `mcp_config_path: Path`, `templates_dir: Path`, `max_concurrency: int`, `state_ids: dict[str, str]`. Function `load_config(path: Path) -> Config`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "agentic-orchestrator"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27", "PyYAML>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["orchestrator*"]
```

- [ ] **Step 2: Create empty `orchestrator/__init__.py`**

```python
```

- [ ] **Step 3: Write the failing test** in `tests/test_config.py`

```python
from pathlib import Path
import textwrap
from orchestrator.config import load_config


def test_load_config_parses_all_fields(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent("""
        plane_base_url: https://plane.example.com
        plane_api_key: key-123
        plane_workspace_slug: acme
        plane_project_id: proj-uuid
        github_repo: acme/sandbox
        repo_dir: /tmp/sandbox
        worktrees_root: /tmp/worktrees
        poll_interval_seconds: 30
        claude_binary: claude
        claude_model: claude-opus-4-8
        mcp_config_path: /tmp/plane-mcp.json
        templates_dir: /tmp/templates
        max_concurrency: 1
        state_ids:
          "Ready to Dev": s-ready
          "In Progress": s-prog
          "Review": s-review
          "QA": s-qa
          "Blocked": s-blocked
          "Completed": s-done
    """))
    cfg = load_config(cfg_file)
    assert cfg.plane_base_url == "https://plane.example.com"
    assert cfg.max_concurrency == 1
    assert cfg.worktrees_root == Path("/tmp/worktrees")
    assert cfg.state_ids["QA"] == "s-qa"


def test_load_config_missing_required_key_raises(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("plane_base_url: https://x\n")
    try:
        load_config(cfg_file)
        assert False, "expected KeyError"
    except KeyError:
        pass
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.config'`

- [ ] **Step 5: Implement `orchestrator/config.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

REQUIRED = [
    "plane_base_url", "plane_api_key", "plane_workspace_slug", "plane_project_id",
    "github_repo", "repo_dir", "worktrees_root", "poll_interval_seconds",
    "claude_binary", "claude_model", "mcp_config_path", "templates_dir", "state_ids",
]


@dataclass
class Config:
    plane_base_url: str
    plane_api_key: str
    plane_workspace_slug: str
    plane_project_id: str
    github_repo: str
    repo_dir: Path
    worktrees_root: Path
    poll_interval_seconds: int
    claude_binary: str
    claude_model: str
    mcp_config_path: Path
    templates_dir: Path
    state_ids: dict[str, str]
    max_concurrency: int = 1


def load_config(path: Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    for key in REQUIRED:
        if key not in data:
            raise KeyError(f"missing required config key: {key}")
    return Config(
        plane_base_url=data["plane_base_url"].rstrip("/"),
        plane_api_key=data["plane_api_key"],
        plane_workspace_slug=data["plane_workspace_slug"],
        plane_project_id=data["plane_project_id"],
        github_repo=data["github_repo"],
        repo_dir=Path(data["repo_dir"]),
        worktrees_root=Path(data["worktrees_root"]),
        poll_interval_seconds=int(data["poll_interval_seconds"]),
        claude_binary=data["claude_binary"],
        claude_model=data["claude_model"],
        mcp_config_path=Path(data["mcp_config_path"]),
        templates_dir=Path(data["templates_dir"]),
        state_ids=dict(data["state_ids"]),
        max_concurrency=int(data.get("max_concurrency", 1)),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Create `config.example.yaml`** (documented sample — `state_ids` filled in by Task 14)

```yaml
# Copy to config.yaml and fill in. state_ids are discovered via `python -m orchestrator --print-states`.
plane_base_url: https://plane.your-vpc.example.com
plane_api_key: REPLACE_ME            # Workspace Settings > API Tokens
plane_workspace_slug: your-workspace
plane_project_id: REPLACE_ME         # project UUID
github_repo: your-org/sandbox        # owner/name
repo_dir: /abs/path/to/sandbox-checkout
worktrees_root: /abs/path/to/worktrees
poll_interval_seconds: 30
claude_binary: claude
claude_model: claude-opus-4-8
mcp_config_path: /abs/path/to/plane-mcp.json
templates_dir: /abs/path/to/agentic-dev-framework/templates
max_concurrency: 1
state_ids:
  "Ready to Dev": REPLACE_ME
  "In Progress": REPLACE_ME
  "Review": REPLACE_ME
  "QA": REPLACE_ME
  "Blocked": REPLACE_ME
  "Completed": REPLACE_ME
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml orchestrator/__init__.py orchestrator/config.py tests/test_config.py config.example.yaml
git commit -m "feat: project scaffolding and config loader"
```

---

## Task 2: Plane REST client

**Files:**
- Create: `orchestrator/plane.py`
- Test: `tests/test_plane.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Issue` dataclass: `id: str`, `name: str`, `description_html: str`, `state_id: str`, `sequence_id: int`.
  - `Comment` dataclass: `id: str`, `body_html: str`, `created_at: str`.
  - `PlaneClient(base_url, api_key, workspace_slug, project_id, client: httpx.Client | None = None)` with methods:
    `list_states() -> dict[str, str]` (name→id), `list_issues_in_state(state_id) -> list[Issue]`, `list_comments(issue_id) -> list[Comment]`, `add_comment(issue_id, body_html) -> None`, `set_state(issue_id, state_id) -> None`.

> NOTE: Field names below follow the documented Plane v1 `/work-items/` surface. Exact JSON keys must be confirmed against the deployed instance in Task 14; the client is written so only the small `_parse_*` helpers change if keys differ.

- [ ] **Step 1: Write the failing test** in `tests/test_plane.py`

```python
import httpx, respx
from orchestrator.plane import PlaneClient, Issue

BASE = "https://plane.test"
WS = "acme"
PROJ = "proj1"
PREFIX = f"{BASE}/api/v1/workspaces/{WS}/projects/{PROJ}"


def make_client():
    return PlaneClient(BASE, "key", WS, PROJ, client=httpx.Client())


@respx.mock
def test_list_states_maps_name_to_id():
    respx.get(f"{PREFIX}/states/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"id": "s1", "name": "Ready to Dev"},
            {"id": "s2", "name": "QA"},
        ], "next_cursor": None,
    }))
    states = make_client().list_states()
    assert states == {"Ready to Dev": "s1", "QA": "s2"}


@respx.mock
def test_list_issues_in_state_parses_and_filters():
    respx.get(f"{PREFIX}/work-items/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"id": "i1", "name": "Add health", "description_html": "<p>do it</p>",
             "state": "s1", "sequence_id": 7},
        ], "next_cursor": None,
    }))
    issues = make_client().list_issues_in_state("s1")
    assert issues == [Issue(id="i1", name="Add health",
                            description_html="<p>do it</p>", state_id="s1", sequence_id=7)]


@respx.mock
def test_list_comments_paginates():
    route = respx.get(f"{PREFIX}/work-items/i1/comments/")
    route.side_effect = [
        httpx.Response(200, json={"results": [{"id": "c1", "comment_html": "<p>a</p>",
                                               "created_at": "t1"}], "next_cursor": "CUR"}),
        httpx.Response(200, json={"results": [{"id": "c2", "comment_html": "<p>b</p>",
                                               "created_at": "t2"}], "next_cursor": None}),
    ]
    comments = make_client().list_comments("i1")
    assert [c.id for c in comments] == ["c1", "c2"]


@respx.mock
def test_set_state_patches_work_item():
    route = respx.patch(f"{PREFIX}/work-items/i1/").mock(return_value=httpx.Response(200, json={}))
    make_client().set_state("i1", "s2")
    assert route.called
    sent = route.calls.last.request
    assert b'"state": "s2"' in sent.content


@respx.mock
def test_add_comment_posts_html():
    route = respx.post(f"{PREFIX}/work-items/i1/comments/").mock(
        return_value=httpx.Response(201, json={}))
    make_client().add_comment("i1", "<p>hi</p>")
    assert route.called
    assert b"<p>hi</p>" in route.calls.last.request.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plane.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.plane'`

- [ ] **Step 3: Implement `orchestrator/plane.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
import httpx


@dataclass
class Issue:
    id: str
    name: str
    description_html: str
    state_id: str
    sequence_id: int


@dataclass
class Comment:
    id: str
    body_html: str
    created_at: str


class PlaneClient:
    def __init__(self, base_url, api_key, workspace_slug, project_id,
                 client: httpx.Client | None = None):
        self._prefix = f"{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def _paginate(self, url: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {})
        out: list[dict] = []
        while True:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
            out.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def list_states(self) -> dict[str, str]:
        rows = self._paginate(f"{self._prefix}/states/")
        return {r["name"]: r["id"] for r in rows}

    def list_issues_in_state(self, state_id: str) -> list[Issue]:
        rows = self._paginate(f"{self._prefix}/work-items/", {"state": state_id})
        return [self._parse_issue(r) for r in rows if r.get("state") == state_id]

    def list_comments(self, issue_id: str) -> list[Comment]:
        rows = self._paginate(f"{self._prefix}/work-items/{issue_id}/comments/")
        return [self._parse_comment(r) for r in rows]

    def add_comment(self, issue_id: str, body_html: str) -> None:
        resp = self._http.post(f"{self._prefix}/work-items/{issue_id}/comments/",
                               json={"comment_html": body_html})
        resp.raise_for_status()

    def set_state(self, issue_id: str, state_id: str) -> None:
        resp = self._http.patch(f"{self._prefix}/work-items/{issue_id}/",
                                json={"state": state_id})
        resp.raise_for_status()

    @staticmethod
    def _parse_issue(r: dict) -> Issue:
        return Issue(id=r["id"], name=r.get("name", ""),
                     description_html=r.get("description_html", ""),
                     state_id=r.get("state", ""), sequence_id=int(r.get("sequence_id", 0)))

    @staticmethod
    def _parse_comment(r: dict) -> Comment:
        return Comment(id=r["id"], body_html=r.get("comment_html", ""),
                       created_at=r.get("created_at", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_plane.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/plane.py tests/test_plane.py
git commit -m "feat: Plane REST client for work items, states, comments"
```

---

## Task 3: State machine

**Files:**
- Create: `orchestrator/state_machine.py`
- Test: `tests/test_state_machine.py`

**Interfaces:**
- Produces: string constants `READY_TO_DEV="Ready to Dev"`, `IN_PROGRESS="In Progress"`, `REVIEW="Review"`, `QA="QA"`, `BLOCKED="Blocked"`, `COMPLETED="Completed"`, `DRAFT="Draft"`, `DEPLOYED="Deployed"`. `role_for_state(name: str) -> str | None` (returns `"builder"`/`"reviewer"`/`"qa"`/`None`). `is_allowed(frm: str, to: str) -> bool`.

- [ ] **Step 1: Write the failing test** in `tests/test_state_machine.py`

```python
from orchestrator import state_machine as sm


def test_role_for_state():
    assert sm.role_for_state("Ready to Dev") == "builder"
    assert sm.role_for_state("In Progress") == "builder"
    assert sm.role_for_state("Review") == "reviewer"
    assert sm.role_for_state("QA") == "qa"
    assert sm.role_for_state("Completed") is None
    assert sm.role_for_state("Blocked") is None


def test_allowed_transitions():
    assert sm.is_allowed("Ready to Dev", "In Progress")
    assert sm.is_allowed("In Progress", "Blocked")
    assert sm.is_allowed("In Progress", "Review")
    assert sm.is_allowed("Review", "In Progress")
    assert sm.is_allowed("Review", "QA")
    assert sm.is_allowed("QA", "In Progress")
    assert sm.is_allowed("QA", "Completed")


def test_disallowed_transitions():
    assert not sm.is_allowed("Review", "Completed")   # must pass QA first
    assert not sm.is_allowed("Ready to Dev", "Completed")
    assert not sm.is_allowed("QA", "Review")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_machine.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError`

- [ ] **Step 3: Implement `orchestrator/state_machine.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state_machine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/state_machine.py tests/test_state_machine.py
git commit -m "feat: board state machine with role mapping and transition rules"
```

---

## Task 4: Git worktree helper

**Files:**
- Create: `orchestrator/worktree.py`
- Test: `tests/test_worktree.py`

**Interfaces:**
- Produces: `create_worktree(repo_dir: Path, worktrees_root: Path, slug: str) -> Path` (creates worktree at `worktrees_root/slug` on a new branch `agent/<slug>`, returns the path). `remove_worktree(repo_dir: Path, worktree_path: Path) -> None` (force-removes the worktree and prunes).

- [ ] **Step 1: Write the failing test** in `tests/test_worktree.py`

```python
import subprocess
from pathlib import Path
from orchestrator.worktree import create_worktree, remove_worktree


def _init_repo(path: Path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t.t",
                    "-c", "user.name=t", "commit", "-qm", "init"], check=True)


def test_create_and_remove_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    roots = tmp_path / "wt"

    wt = create_worktree(repo, roots, "proj-7")
    assert wt.exists()
    assert (wt / "README.md").exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", "agent/proj-7"],
                              capture_output=True, text=True).stdout
    assert "agent/proj-7" in branches

    remove_worktree(repo, wt)
    assert not wt.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worktree.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.worktree'`

- [ ] **Step 3: Implement `orchestrator/worktree.py`**

```python
from __future__ import annotations
from pathlib import Path
import subprocess


def _git(repo_dir: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo_dir), *args], check=True)


def create_worktree(repo_dir: Path, worktrees_root: Path, slug: str) -> Path:
    worktrees_root.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_root / slug
    branch = f"agent/{slug}"
    _git(repo_dir, "worktree", "add", "-B", branch, str(wt_path))
    return wt_path


def remove_worktree(repo_dir: Path, worktree_path: Path) -> None:
    _git(repo_dir, "worktree", "remove", "--force", str(worktree_path))
    _git(repo_dir, "worktree", "prune")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worktree.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/worktree.py tests/test_worktree.py
git commit -m "feat: git worktree create/remove helpers"
```

---

## Task 5: Session launcher (command build + stream-json parse + run)

**Files:**
- Create: `orchestrator/launcher.py`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `SessionResult` dataclass: `ok: bool`, `error: str | None`.
  - `role_doc_path(cfg: Config, role: str) -> Path` → `cfg.templates_dir / f"{role}.md"`.
  - `build_claude_command(cfg, role, ticket_id, worktree, role_doc_text) -> list[str]`.
  - `parse_stream_json(lines: Iterable[str]) -> SessionResult`.
  - `run_session(cfg, role, ticket_id, worktree, *, runner=subprocess.Popen) -> SessionResult`.

- [ ] **Step 1: Write the failing test** in `tests/test_launcher.py`

```python
from pathlib import Path
from orchestrator.config import Config
from orchestrator.launcher import (
    build_claude_command, parse_stream_json, SessionResult, role_doc_path,
)


def make_cfg(tmp_path) -> Config:
    return Config(
        plane_base_url="https://x", plane_api_key="k", plane_workspace_slug="w",
        plane_project_id="p", github_repo="o/r", repo_dir=tmp_path / "repo",
        worktrees_root=tmp_path / "wt", poll_interval_seconds=30, claude_binary="claude",
        claude_model="claude-opus-4-8", mcp_config_path=tmp_path / "mcp.json",
        templates_dir=tmp_path / "templates", state_ids={}, max_concurrency=1,
    )


def test_build_command_includes_required_flags(tmp_path):
    cfg = make_cfg(tmp_path)
    cmd = build_claude_command(cfg, "builder", "i1", tmp_path / "wt/i1", "ROLE TEXT")
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "stream-json" in cmd
    assert "bypassPermissions" in cmd
    assert str(cfg.mcp_config_path) in cmd
    # role instructions injected via append-system-prompt
    assert "ROLE TEXT" in cmd
    # the prompt names the ticket id
    assert any("i1" in part for part in cmd)


def test_role_doc_path(tmp_path):
    cfg = make_cfg(tmp_path)
    assert role_doc_path(cfg, "qa") == cfg.templates_dir / "qa.md"


def test_parse_stream_json_success():
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"assistant","message":{}}',
        '{"type":"result","subtype":"success","is_error":false}',
    ]
    assert parse_stream_json(lines) == SessionResult(ok=True, error=None)


def test_parse_stream_json_error_flag():
    lines = ['{"type":"result","subtype":"error_max_turns","is_error":true}']
    res = parse_stream_json(lines)
    assert res.ok is False
    assert "error_max_turns" in (res.error or "")


def test_parse_stream_json_no_result_is_failure():
    res = parse_stream_json(['{"type":"assistant","message":{}}'])
    assert res.ok is False
    assert "no result" in (res.error or "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_launcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.launcher'`

- [ ] **Step 3: Implement `orchestrator/launcher.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json
import subprocess

from orchestrator.config import Config


@dataclass
class SessionResult:
    ok: bool
    error: str | None = None


def role_doc_path(cfg: Config, role: str) -> Path:
    return cfg.templates_dir / f"{role}.md"


def build_claude_command(cfg: Config, role: str, ticket_id: str,
                         worktree: Path, role_doc_text: str) -> list[str]:
    prompt = (
        f"You are acting as the {role} for Plane work item {ticket_id}. "
        f"Follow your role instructions exactly. Begin by fully hydrating context "
        f"(work item, all comments, PR thread, docs/ memory) before any action."
    )
    return [
        cfg.claude_binary,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--mcp-config", str(cfg.mcp_config_path),
        "--model", cfg.claude_model,
        "--append-system-prompt", role_doc_text,
    ]


def parse_stream_json(lines: Iterable[str]) -> SessionResult:
    saw_result = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            saw_result = True
            if obj.get("is_error"):
                return SessionResult(ok=False, error=obj.get("subtype", "error"))
            return SessionResult(ok=True, error=None)
    if not saw_result:
        return SessionResult(ok=False, error="session ended with no result event")
    return SessionResult(ok=False, error="unknown")


def run_session(cfg: Config, role: str, ticket_id: str, worktree: Path,
                *, runner=subprocess.Popen) -> SessionResult:
    role_doc_text = role_doc_path(cfg, role).read_text()
    cmd = build_claude_command(cfg, role, ticket_id, worktree, role_doc_text)
    proc = runner(cmd, cwd=str(worktree), stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, text=True)
    lines = list(iter(proc.stdout.readline, "")) if proc.stdout else []
    proc.wait()
    result = parse_stream_json(lines)
    if proc.returncode not in (0, None) and result.ok:
        return SessionResult(ok=False, error=f"claude exited {proc.returncode}")
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_launcher.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/launcher.py tests/test_launcher.py
git commit -m "feat: claude session launcher with stream-json parsing"
```

---

## Task 6: Poller (ownership + concurrency + dispatch loop)

**Files:**
- Create: `orchestrator/poller.py`
- Test: `tests/test_poller.py`

**Interfaces:**
- Consumes: `Config` (Task 1); `PlaneClient.list_issues_in_state` + `Issue` (Task 2); `state_machine.role_for_state` (Task 3).
- Produces:
  - `Ownership` class: `claim(id)`, `release(id)`, `owns(id) -> bool`, `count() -> int`.
  - `poll_once(client, cfg, ownership, dispatch) -> None` where `dispatch(issue: Issue, role: str) -> None` is called for each newly claimed, actionable, un-owned issue while `ownership.count() < cfg.max_concurrency`.
  - `run(cfg, *, client=None, dispatch=None, sleep=time.sleep, max_iterations=None) -> None`.

- [ ] **Step 1: Write the failing test** in `tests/test_poller.py`

```python
from orchestrator.poller import Ownership, poll_once
from orchestrator.plane import Issue
from orchestrator.config import Config
from pathlib import Path


def make_cfg(states, concurrency=1) -> Config:
    return Config(
        plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
        github_repo="o/r", repo_dir=Path("/tmp/r"), worktrees_root=Path("/tmp/wt"),
        poll_interval_seconds=1, claude_binary="claude", claude_model="m",
        mcp_config_path=Path("/tmp/mcp.json"), templates_dir=Path("/tmp/t"),
        state_ids=states, max_concurrency=concurrency,
    )


class FakeClient:
    def __init__(self, by_state):
        self.by_state = by_state

    def list_issues_in_state(self, state_id):
        return self.by_state.get(state_id, [])


def test_poll_dispatches_actionable_with_correct_role():
    states = {"Ready to Dev": "s-ready", "Review": "s-review", "QA": "s-qa",
              "In Progress": "s-prog", "Blocked": "s-blk", "Completed": "s-done"}
    cfg = make_cfg(states, concurrency=5)
    client = FakeClient({
        "s-ready": [Issue("i1", "a", "", "s-ready", 1)],
        "s-review": [Issue("i2", "b", "", "s-review", 2)],
        "s-qa": [Issue("i3", "c", "", "s-qa", 3)],
    })
    own = Ownership()
    calls = []
    poll_once(client, cfg, own, lambda issue, role: calls.append((issue.id, role)))
    assert set(calls) == {("i1", "builder"), ("i2", "reviewer"), ("i3", "qa")}
    assert own.count() == 3


def test_poll_respects_concurrency_cap():
    states = {"Ready to Dev": "s-ready", "Review": "s-r", "QA": "s-q",
              "In Progress": "s-p", "Blocked": "s-b", "Completed": "s-d"}
    cfg = make_cfg(states, concurrency=1)
    client = FakeClient({"s-ready": [Issue("i1", "a", "", "s-ready", 1),
                                     Issue("i2", "b", "", "s-ready", 2)]})
    own = Ownership()
    calls = []
    poll_once(client, cfg, own, lambda issue, role: calls.append(issue.id))
    assert len(calls) == 1


def test_poll_skips_owned_tickets():
    states = {"Ready to Dev": "s-ready", "Review": "s-r", "QA": "s-q",
              "In Progress": "s-p", "Blocked": "s-b", "Completed": "s-d"}
    cfg = make_cfg(states, concurrency=5)
    client = FakeClient({"s-ready": [Issue("i1", "a", "", "s-ready", 1)]})
    own = Ownership()
    own.claim("i1")
    calls = []
    poll_once(client, cfg, own, lambda issue, role: calls.append(issue.id))
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_poller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.poller'`

- [ ] **Step 3: Implement `orchestrator/poller.py`**

```python
from __future__ import annotations
from threading import Lock
import time

from orchestrator.config import Config
from orchestrator.state_machine import role_for_state


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


def poll_once(client, cfg: Config, ownership: Ownership, dispatch) -> None:
    for state_name in _ACTIONABLE_ORDER:
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


def run(cfg: Config, *, client=None, dispatch=None, sleep=time.sleep,
        max_iterations=None) -> None:
    from orchestrator.plane import PlaneClient
    client = client or PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                                   cfg.plane_workspace_slug, cfg.plane_project_id)
    ownership = Ownership()
    if dispatch is None:
        from orchestrator.dispatch import make_dispatch
        dispatch = make_dispatch(cfg, ownership)
    i = 0
    while max_iterations is None or i < max_iterations:
        poll_once(client, cfg, ownership, dispatch)
        sleep(cfg.poll_interval_seconds)
        i += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_poller.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/poller.py tests/test_poller.py
git commit -m "feat: poller with ownership set and concurrency-capped dispatch"
```

---

## Task 7: Dispatch + entrypoint (wire worktree → session → cleanup)

**Files:**
- Create: `orchestrator/dispatch.py`
- Create: `orchestrator/__main__.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `Config` (1), `create_worktree`/`remove_worktree` (4), `run_session`/`SessionResult` (5), `Ownership` (6).
- Produces: `make_dispatch(cfg, ownership, *, run=run_session, mk_worktree=create_worktree, rm_worktree=remove_worktree) -> Callable[[Issue, str], None]`. The returned `dispatch` creates a worktree, runs the session, then on completion removes the worktree and releases ownership. Worktree slug = `f"{issue.sequence_id}-{role}"`.

- [ ] **Step 1: Write the failing test** in `tests/test_dispatch.py`

```python
from pathlib import Path
from orchestrator.dispatch import make_dispatch
from orchestrator.poller import Ownership
from orchestrator.launcher import SessionResult
from orchestrator.plane import Issue
from orchestrator.config import Config


def make_cfg(tmp_path):
    return Config(
        plane_base_url="x", plane_api_key="k", plane_workspace_slug="w", plane_project_id="p",
        github_repo="o/r", repo_dir=tmp_path / "repo", worktrees_root=tmp_path / "wt",
        poll_interval_seconds=1, claude_binary="claude", claude_model="m",
        mcp_config_path=tmp_path / "mcp.json", templates_dir=tmp_path / "t",
        state_ids={}, max_concurrency=1,
    )


def test_dispatch_runs_session_then_cleans_up_and_releases(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")
    events = []

    def fake_mk(repo_dir, roots, slug):
        events.append(("mk", slug))
        return roots / slug

    def fake_rm(repo_dir, wt):
        events.append(("rm", wt.name))

    def fake_run(cfg, role, ticket_id, worktree):
        events.append(("run", role, ticket_id))
        return SessionResult(ok=True)

    dispatch = make_dispatch(cfg, own, run=fake_run, mk_worktree=fake_mk, rm_worktree=fake_rm)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")

    assert ("mk", "7-builder") in events
    assert ("run", "builder", "i1") in events
    assert ("rm", "7-builder") in events
    assert own.owns("i1") is False


def test_dispatch_releases_ownership_even_on_session_error(tmp_path):
    cfg = make_cfg(tmp_path)
    own = Ownership()
    own.claim("i1")

    def fake_run(cfg, role, ticket_id, worktree):
        return SessionResult(ok=False, error="boom")

    dispatch = make_dispatch(cfg, own, run=fake_run,
                             mk_worktree=lambda r, roots, s: roots / s,
                             rm_worktree=lambda r, w: None)
    dispatch(Issue("i1", "a", "", "s-ready", 7), "builder")
    assert own.owns("i1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.dispatch'`

- [ ] **Step 3: Implement `orchestrator/dispatch.py`**

```python
from __future__ import annotations
from typing import Callable

from orchestrator.config import Config
from orchestrator.plane import Issue
from orchestrator.poller import Ownership
from orchestrator.worktree import create_worktree, remove_worktree
from orchestrator.launcher import run_session


def make_dispatch(cfg: Config, ownership: Ownership, *, run=run_session,
                  mk_worktree=create_worktree, rm_worktree=remove_worktree
                  ) -> Callable[[Issue, str], None]:
    def dispatch(issue: Issue, role: str) -> None:
        slug = f"{issue.sequence_id}-{role}"
        worktree = None
        try:
            worktree = mk_worktree(cfg.repo_dir, cfg.worktrees_root, slug)
            run(cfg, role, issue.id, worktree)
        finally:
            try:
                if worktree is not None:
                    rm_worktree(cfg.repo_dir, worktree)
            finally:
                ownership.release(issue.id)
    return dispatch
```

- [ ] **Step 4: Implement `orchestrator/__main__.py`**

```python
from __future__ import annotations
import argparse
from pathlib import Path

from orchestrator.config import load_config
from orchestrator.plane import PlaneClient
from orchestrator import poller


def main() -> None:
    ap = argparse.ArgumentParser(prog="orchestrator")
    ap.add_argument("--config", default="config.yaml", type=Path)
    ap.add_argument("--print-states", action="store_true",
                    help="print Plane state name→id map and exit")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.print_states:
        client = PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                             cfg.plane_workspace_slug, cfg.plane_project_id)
        for name, sid in client.list_states().items():
            print(f"{name}: {sid}")
        return
    poller.run(cfg)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dispatch.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tasks 1–7 green)

- [ ] **Step 7: Commit**

```bash
git add orchestrator/dispatch.py orchestrator/__main__.py tests/test_dispatch.py
git commit -m "feat: dispatch worktree+session lifecycle and daemon entrypoint"
```

---

## Task 8: Guardrail hooks (precommit gate + deny rules)

**Files:**
- Create: `templates/claude-settings.json`
- Create: `templates/hooks/precommit_gate.sh`
- Test: `tests/test_precommit_gate.py`

**Interfaces:**
- Produces: a `.claude/settings.json` (copied into each target repo) whose `PreToolUse`/`Bash` hook runs `precommit_gate.sh`. The gate reads the hook JSON on stdin; if the command is a `git commit`, it runs `npm run lint && npm run build && npm test` (configurable via env) and checks that a `docs/` file is staged; on any failure it prints a reason and exits `2` (blocks the commit). Non-commit commands exit `0`.

- [ ] **Step 1: Write the failing test** in `tests/test_precommit_gate.py`

```python
import json, subprocess, os, stat
from pathlib import Path

GATE = Path("templates/hooks/precommit_gate.sh")


def run_gate(payload: dict, cwd: Path, env_extra: dict):
    env = {**os.environ, **env_extra}
    return subprocess.run(["bash", str(GATE.resolve())], input=json.dumps(payload),
                          text=True, capture_output=True, cwd=str(cwd), env=env)


def test_non_commit_command_passes(tmp_path):
    res = run_gate({"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
                   tmp_path, {})
    assert res.returncode == 0


def test_commit_blocked_when_checks_fail(tmp_path):
    res = run_gate({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
                   tmp_path, {"LINT_CMD": "false", "BUILD_CMD": "true", "TEST_CMD": "true",
                              "SKIP_MEMORY_CHECK": "1"})
    assert res.returncode == 2
    assert "lint" in (res.stdout + res.stderr).lower()


def test_commit_passes_when_checks_pass(tmp_path):
    res = run_gate({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
                   tmp_path, {"LINT_CMD": "true", "BUILD_CMD": "true", "TEST_CMD": "true",
                              "SKIP_MEMORY_CHECK": "1"})
    assert res.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_precommit_gate.py -v`
Expected: FAIL (gate script missing → non-zero / file-not-found)

- [ ] **Step 3: Implement `templates/hooks/precommit_gate.sh`**

```bash
#!/usr/bin/env bash
# Claude Code PreToolUse hook. Reads hook JSON on stdin.
# Blocks (exit 2) a `git commit` unless lint+build+test pass and a docs/ file is staged.
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')"

case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

LINT_CMD="${LINT_CMD:-npm run lint}"
BUILD_CMD="${BUILD_CMD:-npm run build}"
TEST_CMD="${TEST_CMD:-npm test}"

fail() { echo "BLOCKED: $1" >&2; exit 2; }

eval "$LINT_CMD"  || fail "lint failed"
eval "$BUILD_CMD" || fail "build failed"
eval "$TEST_CMD"  || fail "tests failed"

if [ "${SKIP_MEMORY_CHECK:-0}" != "1" ]; then
  staged="$(git diff --cached --name-only 2>/dev/null || true)"
  if ! printf '%s\n' "$staged" | grep -q '^docs/'; then
    fail "no docs/ memory update staged with this commit"
  fi
fi
exit 0
```

- [ ] **Step 4: Make the gate executable**

Run: `chmod +x templates/hooks/precommit_gate.sh`

- [ ] **Step 5: Implement `templates/claude-settings.json`**

```json
{
  "permissions": {
    "deny": [
      "Bash(rm -rf /*)",
      "Bash(sudo *)",
      "Bash(git push --force*)",
      "Bash(git push -f*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/precommit_gate.sh" }
        ]
      }
    ]
  }
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_precommit_gate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add templates/claude-settings.json templates/hooks/precommit_gate.sh tests/test_precommit_gate.py
git commit -m "feat: guardrail commit gate hook and deny rules"
```

---

## Task 9: Builder role doc

**Files:**
- Create: `templates/builder.md`

**Interfaces:** consumed by the launcher's `--append-system-prompt` for `role == "builder"`. References skills by exact name; relies on the per-project `CLAUDE.md` (Task 12) for lint/test commands and always-on `karpathy-guidelines`.

- [ ] **Step 1: Write `templates/builder.md`**

```markdown
# Role: Builder

You are an autonomous builder picking up a single Plane work item. No human is watching in
real time — your durable output is code on a branch, a PR, and the Plane comment trail. Use the
Plane MCP tools to read/write the ticket and `gh` for git/GitHub.

## Step 1 — Hydrate full context (MANDATORY, before anything else)
1. Fetch the work item (description, acceptance criteria, labels, current state) via Plane MCP.
2. Fetch **every** comment on the ticket (paginate to the end) — the latest comment tells you
   what to do next; the whole trail tells you how.
3. If a PR already exists for this ticket, fetch the **full PR review thread** via
   `gh pr view <n> --comments` — detailed review/QA feedback lives there, not on Plane.
4. Read the repo `docs/` memory layer and recent `git log`.
Post a first comment summarizing what you learned so the trail stays self-describing:
`🤖 [builder] <FROM-STATE> → <FROM-STATE>: context loaded — <1–2 line summary>`.

## Step 2 — Clarify-or-block gate
Invoke the `grill-me` skill against this ticket and the codebase: list every question whose
answer you cannot determine from the ticket, comments, or code, and every unmet dependency.
- If ANY blocking question remains, post:
  `🤖 [builder] <FROM-STATE> → BLOCKED` followed by a numbered list of specific questions,
  then move the ticket to **Blocked** and STOP. Do not write code.
- Only proceed when everything needed is present and unambiguous.

## Step 3 — Claim (fresh start only)
If the ticket is in **Ready to Dev**, move it to **In Progress** and comment
`🤖 [builder] READY-TO-DEV → IN PROGRESS: starting work`.
If it is already **In Progress** (a rework), skip this — you are addressing the latest
review/QA feedback from the trail and PR thread.

## Step 4 — Build
- Use `superpowers:test-driven-development`: write a failing test, then minimal code, then green.
- For any UI work, use the `frontend-design` skill.
- If anything misbehaves, use `superpowers:systematic-debugging`.
- Honor `karpathy-guidelines` (loaded via CLAUDE.md): simplest change that satisfies the
  acceptance criteria, surgical edits, no speculative abstractions.

## Step 5 — Memory + commit
Before committing, append a short entry to a `docs/` markdown file: what changed and why, with
**citations** (file paths, ticket id, PR link). Then commit. The commit hook will block you
unless lint+build+test pass and a `docs/` file is staged — fix and retry until it passes.

## Step 6 — Verify, push, open PR
- Use `superpowers:verification-before-completion`: actually run the tests and show they pass.
- Push the branch and open a PR with `superpowers:requesting-code-review`. Include the ticket id
  and a description mapping changes to the acceptance criteria.

## Step 7 — Hand off to Review
Move the ticket to **Review** and comment
`🤖 [builder] IN PROGRESS → REVIEW: PR <url> ready — <1-line summary>`.

## Rules
- Comments are append-only and self-contained (always include links/refs/decisions).
- Never merge. Never move past Review.
- If you cannot finish (crash/limit), leave a comment explaining where you stopped.
```

- [ ] **Step 2: Validate the doc references only real skills/states**

Run: `grep -E 'superpowers:|grill-me|frontend-design|BLOCKED|Review' templates/builder.md`
Expected: shows the skill names and state transitions (manual read-through confirms each skill name matches an installed skill and each state matches `state_machine.py`).

- [ ] **Step 3: Commit**

```bash
git add templates/builder.md
git commit -m "docs: builder role instructions"
```

---

## Task 10: Reviewer role doc

**Files:**
- Create: `templates/reviewer.md`

**Interfaces:** consumed by the launcher for `role == "reviewer"`. The reviewer never merges; it hands off to QA on approval.

- [ ] **Step 1: Write `templates/reviewer.md`**

```markdown
# Role: Reviewer

You are an autonomous code reviewer for a single Plane work item now in **Review**. You do NOT
merge — your job is to judge the PR and route it.

## Step 1 — Hydrate full context (MANDATORY)
Fetch the work item + every comment (Plane MCP), the **full PR diff and thread**
(`gh pr view <n> --comments`, `gh pr diff <n>`), the `docs/` memory, and `git log`.

## Step 2 — Review
Use the `review` skill to review the PR against (a) the ticket's acceptance criteria and (b) code
quality. Post detailed, line-level findings **on the PR** via `gh pr review`. Keep severity in
mind: anything touching security, architecture, or migrations is a hard stop for human attention —
flag it and leave the ticket in Review with a comment, do not pass it.

## Step 3 — Route
- **Changes needed:** post a SHORT summary comment on the ticket
  `🤖 [reviewer] REVIEW → IN PROGRESS: changes requested — <summary>; details on PR <url>`,
  then move the ticket to **In Progress** (a builder picks it up and reads your PR thread).
- **Approved:** approve the PR (`gh pr review --approve`), then comment
  `🤖 [reviewer] REVIEW → QA: approved — <1-line summary>` and move the ticket to **QA**.

## Rules
- Comments are append-only and self-contained.
- Never merge. Never move to Completed. Detailed feedback goes on the PR; the ticket gets the
  summary + the state move.
```

- [ ] **Step 2: Validate**

Run: `grep -E 'review|QA|IN PROGRESS|gh pr' templates/reviewer.md`
Expected: shows the `review` skill, the QA hand-off, and `gh pr` usage.

- [ ] **Step 3: Commit**

```bash
git add templates/reviewer.md
git commit -m "docs: reviewer role instructions"
```

---

## Task 11: QA role doc

**Files:**
- Create: `templates/qa.md`

**Interfaces:** consumed by the launcher for `role == "qa"`. QA is the only role that merges, and only after an independent acceptance check passes.

- [ ] **Step 1: Write `templates/qa.md`**

```markdown
# Role: QA

You are an autonomous, INDEPENDENT QA engineer for a single Plane work item now in **QA**. You did
not write this code. Your job is black-box verification against the acceptance criteria, then
merge if and only if it passes.

## Step 1 — Hydrate full context (MANDATORY)
Fetch the work item + every comment (Plane MCP), the PR (`gh pr view <n> --comments`,
`gh pr diff <n>`), the `docs/` memory, and `git log`. Extract the ticket's **acceptance criteria**
explicitly — these are what you verify.

## Step 2 — Independent acceptance verification
Check out the PR branch into this worktree (`gh pr checkout <n>`). Use the `verify` skill: build
and run the actual application, then exercise it from the outside against each acceptance
criterion (e.g. start the service and assert `GET /health` returns 200). For UI work, use the
`playwright` plugin + `frontend-design` for end-to-end checks. Do NOT just re-run the builder's
unit tests — verify real behavior. Use `superpowers:verification-before-completion` to require
evidence for each criterion.

## Step 3 — Route
- **Fails any criterion:** post
  `🤖 [qa] QA → IN PROGRESS: QA failed — <which criterion + evidence>` and move the ticket to
  **In Progress** (rework loop).
- **Passes all criteria:** merge with `superpowers:finishing-a-development-branch`
  (`gh pr merge <n> --squash --delete-branch`), then post
  `🤖 [qa] QA → COMPLETED: merged PR <url> — acceptance verified` and move the ticket to
  **Completed**.

## Rules
- Comments are append-only and self-contained, with evidence for each acceptance criterion.
- Merge ONLY after every acceptance criterion passes. On any doubt, bounce to In Progress.
```

- [ ] **Step 2: Validate**

Run: `grep -E 'verify|playwright|gh pr merge|COMPLETED|finishing-a-development-branch' templates/qa.md`
Expected: shows the independent-verification skills, the merge command, and the Completed transition.

- [ ] **Step 3: Commit**

```bash
git add templates/qa.md
git commit -m "docs: QA role instructions with independent acceptance gate"
```

---

## Task 12: Per-project CLAUDE.md template + plane-mcp.json

**Files:**
- Create: `templates/CLAUDE.md.tmpl`
- Create: `plane-mcp.json`

**Interfaces:** `CLAUDE.md.tmpl` is copied (with the project name filled in) into each target repo as `CLAUDE.md`; it is auto-loaded by every session and pins the always-on skill, memory rule, and commands. `plane-mcp.json` is the `--mcp-config` passed to every session.

- [ ] **Step 1: Write `templates/CLAUDE.md.tmpl`**

```markdown
# {{PROJECT_NAME}} — Agent Context

This project is built by autonomous Claude Code sessions directed by the orchestrator. Read this
before any task.

## Always on
- Follow `karpathy-guidelines` at all times: simplest change that meets the acceptance criteria,
  surgical edits, surface assumptions, define verifiable success criteria.

## Memory layer (REQUIRED)
- Durable knowledge lives in `docs/` as markdown. Before every commit, append a short, cited entry
  (what changed, why, ticket id, PR link). The commit hook blocks commits with no `docs/` update.
- Each session is fresh and stateless: reconstruct context from the Plane ticket + full comment
  trail + PR thread + `docs/` + git history. Never act on the latest comment alone.

## Commands
- Lint: `npm run lint`
- Build: `npm run build`
- Test: `npm test`
(Adjust per project language; the commit hook reads `LINT_CMD`/`BUILD_CMD`/`TEST_CMD`.)

## Workflow
Your specific role (builder / reviewer / QA) and its steps are provided in the session's system
prompt. Honor the board state machine and the `🤖 [role] FROM → TO` comment convention.
```

- [ ] **Step 2: Write `plane-mcp.json`**

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"],
      "env": {
        "PLANE_API_KEY": "${PLANE_API_KEY}",
        "PLANE_WORKSPACE_SLUG": "${PLANE_WORKSPACE_SLUG}",
        "PLANE_BASE_URL": "${PLANE_BASE_URL}"
      }
    }
  }
}
```

- [ ] **Step 3: Validate the MCP config is valid JSON**

Run: `python3 -c "import json; json.load(open('plane-mcp.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add templates/CLAUDE.md.tmpl plane-mcp.json
git commit -m "docs: per-project CLAUDE.md template and Plane MCP config"
```

---

## Task 13: Sandbox setup guide

**Files:**
- Create: `docs/sandbox-setup.md`

**Interfaces:** human-run, one-time. Produces the throwaway GitHub repo + Plane project the slice targets, and the three seed tasks used by the e2e tests (Task 14).

- [ ] **Step 1: Write `docs/sandbox-setup.md`**

```markdown
# Sandbox setup (one-time, throwaway)

## 1. GitHub repo
- Create a throwaway repo `your-org/sandbox` with a minimal Node app (an Express server with
  `npm run lint`, `npm run build`, `npm test` wired up) and a `docs/` folder.
- Clone it to `repo_dir` from your config.
- Install the guardrail hooks into it:
  - copy `templates/claude-settings.json` → `<repo>/.claude/settings.json`
  - copy `templates/hooks/precommit_gate.sh` → `<repo>/.claude/hooks/precommit_gate.sh` (chmod +x)
  - copy `templates/CLAUDE.md.tmpl` → `<repo>/CLAUDE.md` (replace `{{PROJECT_NAME}}`)
- Authenticate `gh auth login` so sessions can open/merge PRs.

## 2. Plane project
- Create a project with states named EXACTLY: Draft, Ready to Dev, In Progress, Review, QA,
  Completed, Blocked, Deployed.
- Create a Workspace API token; put it + the workspace slug + project UUID + base URL in
  `config.yaml`.
- Run `python -m orchestrator --config config.yaml --print-states` and paste the printed
  name→id map into `state_ids` in `config.yaml`.

## 3. Install the skill stack at user scope
Ensure these are installed so every headless session inherits them: superpowers, frontend-design,
playwright, `karpathy-guidelines`, and mattpocock's `caveman` / `grill-me`.

## 4. Seed tasks (create in Plane, in "Ready to Dev")
- **HAPPY:** "Add a `GET /health` endpoint that returns HTTP 200 with body `{"status":"ok"}`.
  Acceptance: hitting `/health` returns 200 and the JSON body; a test covers it."
- **VAGUE (negative):** "Make the app better." (no acceptance criteria — must land in Blocked.)
- **QA-CATCH:** "Add `GET /health` returning 200." but with acceptance criteria demanding the body
  be exactly `{"status":"ok"}`. (Used to verify QA catches a body mismatch the unit test missed.)
```

- [ ] **Step 2: Commit**

```bash
git add docs/sandbox-setup.md
git commit -m "docs: sandbox setup guide"
```

---

## Task 14: End-to-end walk-away test

**Files:**
- Create: `docs/e2e-walkthrough.md`

**Interfaces:** the acceptance test for the whole slice. Validates the three success criteria from the spec (§9): happy path, negative (Blocked), QA-catch.

- [ ] **Step 1: Write `docs/e2e-walkthrough.md`**

```markdown
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
```

- [ ] **Step 2: Run the full unit suite one last time**

Run: `pytest -v`
Expected: PASS (all green)

- [ ] **Step 3: Discover and record the real Plane state ids**

Run: `python -m orchestrator --config config.yaml --print-states`
Expected: prints the 8 state name→id pairs; paste them into `config.yaml` `state_ids`.

- [ ] **Step 4: Execute Tests A, B, C** from `docs/e2e-walkthrough.md` against the sandbox and record results. Fix any seam failures before declaring the slice done.

- [ ] **Step 5: Commit**

```bash
git add docs/e2e-walkthrough.md
git commit -m "docs: end-to-end walk-away test procedure"
```

---

## Self-Review notes (addressed)

- **Spec §2 (two processes, polling, ownership, concurrency):** Tasks 1, 2, 6, 7.
- **Spec §3 (context hydration):** enforced in role docs — Tasks 9/10/11 step 1; CLAUDE.md (12).
- **Spec §4 (clarify-or-block + comment protocol):** Task 9 steps 1–2; tags used across 9/10/11.
- **Spec §5 (guardrail hooks + memory):** Task 8 (gate) + CLAUDE.md memory rule (12) + builder step 5 (9).
- **Spec §6 (session launch + monitoring):** Task 5 (command + stream-json + timeout via run_session) + crash→Blocked recorded in e2e (14).
- **Spec §7 (skill stack):** named explicitly in builder/reviewer/qa docs (9/10/11) and CLAUDE.md (12); installed at user scope (13).
- **Spec §8 (repo layout):** matches the File Structure section above.
- **Spec §9 (success criteria — happy/negative/QA-catch):** Task 14 Tests A/B/C.
- **Spec §10 (out of scope):** no parallelism (>1), webhooks, planning bridge, deploy, conflict
  resolution, multi-project, or auto-scaffolding tasks included.
```
