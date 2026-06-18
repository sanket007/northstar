# northstar CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `northstar` CLI that sets up a machine (prereq checks + installs the skill stack to latest), adds projects (link/create a GitHub repo, install guardrails, wire Plane), and runs the existing orchestrator engine per project as detached tmux sessions.

**Architecture:** A new `northstar/` package wraps the unchanged `orchestrator/` engine. Every external tool (`claude`, `gh`, `git`, `tmux`, `uvx`, `curl`, `npx`) is invoked through one injectable `runner` (`northstar/proc.py`), so behavior is unit-tested by asserting the commands a fake runner receives — no real tools run in tests. The CLI is built with Typer.

**Tech Stack:** Python 3.11+, Typer (CLI), PyYAML, the `claude`/`gh`/`git`/`tmux`/`uvx` CLIs, pytest. Reuses `orchestrator.plane.PlaneClient` for Plane state discovery.

## Global Constraints

- **No Anthropic SDK / no Anthropic imports.** northstar only shells out to the `claude` binary (via `runner`); it never imports an Anthropic SDK.
- **Always-latest skills.** `init` adds marketplaces, runs `claude plugin marketplace update`, then `claude plugin install` + `claude plugin update` per plugin. Idempotent.
- **Skill sources (exact):** `superpowers`, `frontend-design`, `playwright` → marketplace `claude-plugins-official` (add source `anthropics/claude-plugins-official`); `andrej-karpathy-skills` → marketplace `karpathy-skills` (add source `multica-ai/andrej-karpathy-skills`). Native installers: caveman → `curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash`; grill-me → `npx --yes skills@latest add mattpocock/skills`.
- **GitHub-reachable gate:** `gh auth status` must pass before any repo create/link in `project add`.
- **Run backend is tmux:** one detached session `ns-<project>` per running project; logs piped to `~/.northstar/logs/<project>.log`.
- **Canonical board states:** Draft, Ready to Dev, In Progress, Review, QA, Completed, Blocked, Deployed.
- **Machine state lives under `~/.northstar/`** (override with `NORTHSTAR_HOME` for tests).
- **Engine untouched:** the `orchestrator/` package and its 27 tests stay green.

---

## File Structure

```
northstar/
  __init__.py
  proc.py          # CommandResult + run() — the single injectable subprocess seam
  paths.py         # ~/.northstar paths + registry read/write
  assets.py        # locate bundled templates/ and plane-mcp.json
  doctor.py        # Check dataclass + run_checks()
  skills.py        # PLUGINS/NATIVE lists + install_all()/installed_plugins()/verify()
  initcmd.py       # do_init(): doctor gate + ensure dirs + install skills + copy mcp
  project.py       # detect/resolve/create repo, install guardrails, discover states, add_project()
  supervisor.py    # tmux start/stop/restart/status + logs command
  cli.py           # Typer app: doctor / init / project / start / stop / restart / status / logs
tests/
  test_proc.py  test_paths.py  test_assets.py  test_doctor.py  test_skills.py
  test_initcmd.py  test_project.py  test_supervisor.py  test_cli.py
```

`pyproject.toml`: rename project to `northstar`, add `typer` dep, add `[project.scripts]` for `northstar` + `ns`, and include `northstar*` in package discovery.

---

## Task 1: Package scaffold, pyproject, and the `proc` runner seam

**Files:**
- Modify: `pyproject.toml`
- Create: `northstar/__init__.py` (empty)
- Create: `northstar/proc.py`
- Test: `tests/test_proc.py`

**Interfaces:**
- Produces: `CommandResult` dataclass (`returncode: int`, `stdout: str`, `stderr: str`, property `ok -> bool`). `run(cmd, *, shell=False, env=None, timeout=None, input=None) -> CommandResult`. A "runner" is any callable with `run`'s signature; downstream modules default `runner=run` and accept a fake in tests.

- [ ] **Step 1: Update `pyproject.toml`** (rename + typer + scripts + package discovery)

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "northstar"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27", "PyYAML>=6.0", "typer>=0.12"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21"]

[project.scripts]
northstar = "northstar.cli:app"
ns = "northstar.cli:app"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["orchestrator*", "northstar*"]
```

- [ ] **Step 2: Create empty `northstar/__init__.py`**

```python
```

- [ ] **Step 3: Write the failing test** in `tests/test_proc.py`

```python
from northstar.proc import run, CommandResult


def test_run_captures_stdout_and_returncode():
    res = run(["python3", "-c", "print('hello')"])
    assert isinstance(res, CommandResult)
    assert res.returncode == 0
    assert res.ok is True
    assert "hello" in res.stdout


def test_run_reports_nonzero_and_not_ok():
    res = run(["python3", "-c", "import sys; sys.exit(3)"])
    assert res.returncode == 3
    assert res.ok is False


def test_run_shell_string():
    res = run("echo shelltest", shell=True)
    assert "shelltest" in res.stdout
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_proc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.proc'`

- [ ] **Step 5: Implement `northstar/proc.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
import subprocess


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd, *, shell=False, env=None, timeout=None, input=None) -> CommandResult:
    proc = subprocess.run(
        cmd, shell=shell, env=env, timeout=timeout, input=input,
        capture_output=True, text=True,
    )
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
```

- [ ] **Step 6: Reinstall (project renamed) and run tests**

Run: `.venv/bin/pip install -q -e ".[dev]" && .venv/bin/pytest tests/test_proc.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml northstar/__init__.py northstar/proc.py tests/test_proc.py
git commit -m "feat(northstar): package scaffold, pyproject rename, proc runner seam"
```

---

## Task 2: `~/.northstar` paths + project registry

**Files:**
- Create: `northstar/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `home() -> Path` (uses `$NORTHSTAR_HOME` else `~/.northstar`), `ensure_dirs()`, `projects_dir()`, `project_config_path(name) -> Path`, `logs_dir()`, `log_path(name) -> Path`, `registry_path() -> Path`, `load_registry() -> dict`, `save_registry(reg)`, `register_project(name, meta: dict)`, `unregister_project(name)`, `list_projects() -> dict`.

- [ ] **Step 1: Write the failing test** in `tests/test_paths.py`

```python
import importlib
from pathlib import Path


def reload_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as p
    return importlib.reload(p)


def test_ensure_dirs_creates_layout(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    assert p.home().is_dir()
    assert p.projects_dir().is_dir()
    assert p.logs_dir().is_dir()
    assert p.project_config_path("acme") == p.projects_dir() / "acme.yaml"
    assert p.log_path("acme") == p.logs_dir() / "acme.log"


def test_register_and_list_roundtrip(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    p.register_project("acme", {"github_repo": "o/acme", "repo_dir": "/tmp/acme"})
    assert p.list_projects()["acme"]["github_repo"] == "o/acme"
    p.register_project("beta", {"github_repo": "o/beta"})
    assert set(p.list_projects()) == {"acme", "beta"}
    p.unregister_project("acme")
    assert set(p.list_projects()) == {"beta"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.paths'`

- [ ] **Step 3: Implement `northstar/paths.py`**

```python
from __future__ import annotations
from pathlib import Path
import os
import yaml


def home() -> Path:
    return Path(os.environ.get("NORTHSTAR_HOME", str(Path.home() / ".northstar")))


def projects_dir() -> Path:
    return home() / "projects"


def logs_dir() -> Path:
    return home() / "logs"


def project_config_path(name: str) -> Path:
    return projects_dir() / f"{name}.yaml"


def log_path(name: str) -> Path:
    return logs_dir() / f"{name}.log"


def registry_path() -> Path:
    return home() / "registry.yaml"


def ensure_dirs() -> None:
    for d in (home(), projects_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def save_registry(reg: dict) -> None:
    ensure_dirs()
    registry_path().write_text(yaml.safe_dump(reg, sort_keys=True))


def register_project(name: str, meta: dict) -> None:
    reg = load_registry()
    reg[name] = meta
    save_registry(reg)


def unregister_project(name: str) -> None:
    reg = load_registry()
    reg.pop(name, None)
    save_registry(reg)


def list_projects() -> dict:
    return load_registry()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_paths.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/paths.py tests/test_paths.py
git commit -m "feat(northstar): ~/.northstar paths and project registry"
```

---

## Task 3: Bundled asset resolution

**Files:**
- Create: `northstar/assets.py`
- Test: `tests/test_assets.py`

**Interfaces:**
- Produces: `assets_root() -> Path` (uses `$NORTHSTAR_ASSETS_DIR` else the repo root containing `templates/`, resolved as `Path(__file__).resolve().parent.parent`), `templates_dir() -> Path`, `plane_mcp_json() -> Path`, `copy_plane_mcp_to(dest_dir: Path) -> Path`.

- [ ] **Step 1: Write the failing test** in `tests/test_assets.py`

```python
import importlib


def test_assets_resolve_with_override(tmp_path, monkeypatch):
    (tmp_path / "templates").mkdir()
    (tmp_path / "plane-mcp.json").write_text("{}")
    monkeypatch.setenv("NORTHSTAR_ASSETS_DIR", str(tmp_path))
    import northstar.assets as a
    a = importlib.reload(a)
    assert a.templates_dir() == tmp_path / "templates"
    assert a.plane_mcp_json() == tmp_path / "plane-mcp.json"
    dest = tmp_path / "dest"
    dest.mkdir()
    out = a.copy_plane_mcp_to(dest)
    assert out.exists() and out.read_text() == "{}"


def test_assets_root_defaults_to_repo(monkeypatch):
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.assets as a
    a = importlib.reload(a)
    # the repo root (package parent) must contain the templates dir
    assert (a.assets_root() / "templates").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_assets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.assets'`

- [ ] **Step 3: Implement `northstar/assets.py`**

```python
from __future__ import annotations
from pathlib import Path
import os
import shutil


def assets_root() -> Path:
    override = os.environ.get("NORTHSTAR_ASSETS_DIR")
    if override:
        return Path(override)
    # northstar/assets.py -> repo root holding templates/ and plane-mcp.json
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return assets_root() / "templates"


def plane_mcp_json() -> Path:
    return assets_root() / "plane-mcp.json"


def copy_plane_mcp_to(dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "plane-mcp.json"
    shutil.copyfile(plane_mcp_json(), out)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_assets.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/assets.py tests/test_assets.py
git commit -m "feat(northstar): bundled asset resolution (templates, plane-mcp.json)"
```

---

## Task 4: `doctor` prerequisite checks

**Files:**
- Create: `northstar/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `CommandResult` (Task 1).
- Produces: `Check` dataclass (`name: str`, `ok: bool`, `critical: bool`, `detail: str`, `fix: str`); `run_checks(runner=run, deep=False) -> list[Check]`; `all_critical_ok(checks) -> bool`. `runner(cmd, **kw) -> CommandResult`.

- [ ] **Step 1: Write the failing test** in `tests/test_doctor.py`

```python
from northstar.proc import CommandResult
from northstar.doctor import run_checks, all_critical_ok


def fake_runner_factory(table):
    # table maps the FIRST token of cmd -> CommandResult
    def runner(cmd, **kw):
        key = (cmd if isinstance(cmd, str) else cmd[0])
        first = key.split()[0] if isinstance(key, str) else key
        return table.get(first, CommandResult(127, "", "not found"))
    return runner


def test_all_present_passes():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "tmux", "npx"]}
    table["gh"] = CommandResult(0, "Logged in to github.com", "")
    checks = run_checks(runner=fake_runner_factory(table))
    assert all_critical_ok(checks) is True


def test_missing_tmux_is_critical_failure():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "npx"]}
    table["gh"] = CommandResult(0, "Logged in", "")
    # tmux absent -> 127
    checks = run_checks(runner=fake_runner_factory(table))
    tmux = next(c for c in checks if c.name == "tmux")
    assert tmux.ok is False and tmux.critical is True
    assert all_critical_ok(checks) is False


def test_gh_unauthenticated_fails_github_check():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "tmux", "npx"]}
    table["gh"] = CommandResult(1, "", "not logged in")  # gh present but auth fails
    checks = run_checks(runner=fake_runner_factory(table))
    gh_auth = next(c for c in checks if c.name == "github-auth")
    assert gh_auth.ok is False and gh_auth.critical is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_doctor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.doctor'`

- [ ] **Step 3: Implement `northstar/doctor.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
import sys

from northstar.proc import run


@dataclass
class Check:
    name: str
    ok: bool
    critical: bool
    detail: str
    fix: str


def _tool(runner, name, cmd, *, critical, fix) -> Check:
    res = runner(cmd)
    ok = res.ok
    detail = (res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else ""
    return Check(name, ok, critical, detail or ("missing" if not ok else ""), fix)


def run_checks(runner=run, deep=False) -> list[Check]:
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(Check("python>=3.11", py_ok, True,
                        ".".join(map(str, sys.version_info[:3])),
                        "install Python 3.11+"))

    checks.append(_tool(runner, "git", ["git", "--version"], critical=True,
                        fix="install git"))
    checks.append(_tool(runner, "gh", ["gh", "--version"], critical=True,
                        fix="install the GitHub CLI"))

    gh_auth = runner(["gh", "auth", "status"])
    checks.append(Check("github-auth", gh_auth.ok, True,
                        "reachable" if gh_auth.ok else "not authenticated",
                        "run: gh auth login"))

    checks.append(_tool(runner, "claude", ["claude", "--version"], critical=True,
                        fix="install Claude Code (claude.com/code)"))
    if deep:
        smoke = runner(["claude", "-p", "reply with OK", "--output-format", "json"])
        checks.append(Check("claude-smoke", smoke.ok, True,
                            "ok" if smoke.ok else "smoke run failed",
                            "check `claude` login/subscription"))

    checks.append(_tool(runner, "uvx", ["uvx", "--version"], critical=True,
                        fix="install uv (astral.sh/uv)"))
    checks.append(_tool(runner, "tmux", ["tmux", "-V"], critical=True,
                        fix="install tmux"))
    checks.append(_tool(runner, "npx", ["npx", "--version"], critical=False,
                        fix="install Node.js (needed for the grill-me skill)"))
    return checks


def all_critical_ok(checks) -> bool:
    return all(c.ok for c in checks if c.critical)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_doctor.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/doctor.py tests/test_doctor.py
git commit -m "feat(northstar): doctor prerequisite checks"
```

---

## Task 5: Skill stack list + install/verify

**Files:**
- Create: `northstar/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `CommandResult` (Task 1).
- Produces: `Plugin` (`name`, `marketplace`, `add_source`), `Native` (`name`, `kind`, `cmd`), module lists `PLUGINS` and `NATIVE`, `marketplaces() -> list[str]` (unique add_sources), `installed_plugins(runner=run) -> dict[str,str]`, `install_all(runner=run) -> list[tuple[str,bool,str]]`, `verify(runner=run) -> list[tuple[str,bool]]`.

- [ ] **Step 1: Write the failing test** in `tests/test_skills.py`

```python
import json
from northstar.proc import CommandResult
from northstar import skills


def test_plugin_and_marketplace_lists():
    names = {p.name for p in skills.PLUGINS}
    assert names == {"superpowers", "frontend-design", "playwright", "andrej-karpathy-skills"}
    assert set(skills.marketplaces()) == {
        "anthropics/claude-plugins-official", "multica-ai/andrej-karpathy-skills"}
    assert {n.name for n in skills.NATIVE} == {"caveman", "grill-me"}


def test_installed_plugins_parses_json():
    payload = json.dumps([{"name": "superpowers", "version": "6.0.2"},
                          {"name": "playwright", "version": "1.0.0"}])
    runner = lambda cmd, **kw: CommandResult(0, payload, "")
    got = skills.installed_plugins(runner=runner)
    assert got["superpowers"] == "6.0.2"


def test_install_all_runs_marketplace_then_install_then_update_then_native():
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd if isinstance(cmd, str) else " ".join(cmd))
        return CommandResult(0, "[]", "")
    skills.install_all(runner=runner)
    joined = "\n".join(calls)
    assert "plugin marketplace add anthropics/claude-plugins-official" in joined
    assert "plugin marketplace add multica-ai/andrej-karpathy-skills" in joined
    assert "plugin marketplace update" in joined
    assert "plugin install superpowers@claude-plugins-official" in joined
    assert "plugin update superpowers@claude-plugins-official" in joined
    assert "plugin install andrej-karpathy-skills@karpathy-skills" in joined
    # native installers attempted
    assert any("JuliusBrussee/caveman" in c for c in calls)
    assert any("skills@latest add mattpocock/skills" in c for c in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_skills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.skills'`

- [ ] **Step 3: Implement `northstar/skills.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
import json

from northstar.proc import run


@dataclass(frozen=True)
class Plugin:
    name: str
    marketplace: str
    add_source: str


@dataclass(frozen=True)
class Native:
    name: str
    kind: str   # "script" | "npx"
    cmd: str


PLUGINS = [
    Plugin("superpowers", "claude-plugins-official", "anthropics/claude-plugins-official"),
    Plugin("frontend-design", "claude-plugins-official", "anthropics/claude-plugins-official"),
    Plugin("playwright", "claude-plugins-official", "anthropics/claude-plugins-official"),
    Plugin("andrej-karpathy-skills", "karpathy-skills", "multica-ai/andrej-karpathy-skills"),
]

NATIVE = [
    Native("caveman", "script",
           "curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash"),
    Native("grill-me", "npx", "npx --yes skills@latest add mattpocock/skills"),
]


def marketplaces() -> list[str]:
    seen, out = set(), []
    for p in PLUGINS:
        if p.add_source not in seen:
            seen.add(p.add_source)
            out.append(p.add_source)
    return out


def installed_plugins(runner=run) -> dict[str, str]:
    res = runner(["claude", "plugin", "list", "--json"])
    if not res.ok:
        return {}
    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    rows = data if isinstance(data, list) else data.get("plugins", [])
    return {r["name"]: r.get("version", "") for r in rows if "name" in r}


def install_all(runner=run) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for src in marketplaces():
        runner(["claude", "plugin", "marketplace", "add", src])
    runner(["claude", "plugin", "marketplace", "update"])
    for p in PLUGINS:
        ref = f"{p.name}@{p.marketplace}"
        runner(["claude", "plugin", "install", ref, "--scope", "user"])
        upd = runner(["claude", "plugin", "update", ref, "--scope", "user"])
        results.append((p.name, upd.ok, "plugin"))
    for n in NATIVE:
        res = runner(n.cmd, shell=True)
        results.append((n.name, res.ok, "native"))
    return results


def verify(runner=run) -> list[tuple[str, bool]]:
    installed = installed_plugins(runner=runner)
    return [(p.name, p.name in installed) for p in PLUGINS]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skills.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/skills.py tests/test_skills.py
git commit -m "feat(northstar): skill stack list, install-all, verify"
```

---

## Task 6: `init` command logic

**Files:**
- Create: `northstar/initcmd.py`
- Test: `tests/test_initcmd.py`

**Interfaces:**
- Consumes: `doctor.run_checks`/`all_critical_ok` (4), `skills.install_all` (5), `paths.ensure_dirs`/`home` (2), `assets.copy_plane_mcp_to` (3).
- Produces: `do_init(runner=run, deep=False) -> int` (returns 0 on success, non-zero if a critical doctor check fails — in which case it does NOT install).

- [ ] **Step 1: Write the failing test** in `tests/test_initcmd.py`

```python
import importlib
from northstar.proc import CommandResult
from northstar.doctor import Check


def test_do_init_aborts_when_critical_check_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.initcmd as initcmd
    initcmd = importlib.reload(initcmd)
    monkeypatch.setattr(initcmd, "run_checks",
                        lambda runner, deep=False: [Check("tmux", False, True, "missing", "install tmux")])
    called = {"install": False}
    monkeypatch.setattr(initcmd, "install_all",
                        lambda runner: called.__setitem__("install", True) or [])
    rc = initcmd.do_init(runner=lambda *a, **k: CommandResult(0, "", ""))
    assert rc != 0
    assert called["install"] is False


def test_do_init_installs_and_creates_dirs_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.setenv("NORTHSTAR_ASSETS_DIR", str(tmp_path / "assets"))
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "templates").mkdir()
    (tmp_path / "assets" / "plane-mcp.json").write_text("{}")
    import northstar.initcmd as initcmd
    initcmd = importlib.reload(initcmd)
    monkeypatch.setattr(initcmd, "run_checks",
                        lambda runner, deep=False: [Check("git", True, True, "ok", "")])
    monkeypatch.setattr(initcmd, "install_all", lambda runner: [("superpowers", True, "plugin")])
    rc = initcmd.do_init(runner=lambda *a, **k: CommandResult(0, "", ""))
    assert rc == 0
    import northstar.paths as paths
    assert paths.home().is_dir()
    assert (paths.home() / "plane-mcp.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_initcmd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.initcmd'`

- [ ] **Step 3: Implement `northstar/initcmd.py`**

```python
from __future__ import annotations

from northstar.proc import run
from northstar.doctor import run_checks, all_critical_ok
from northstar.skills import install_all
from northstar import paths
from northstar.assets import copy_plane_mcp_to


def do_init(runner=run, deep=False) -> int:
    checks = run_checks(runner=runner, deep=deep)
    if not all_critical_ok(checks):
        failed = [c for c in checks if c.critical and not c.ok]
        print("Cannot init — fix these first:")
        for c in failed:
            print(f"  ✗ {c.name}: {c.detail} — {c.fix}")
        return 1
    paths.ensure_dirs()
    copy_plane_mcp_to(paths.home())
    results = install_all(runner=runner)
    for name, ok, kind in results:
        print(f"  {'✓' if ok else '⚠'} {name} ({kind})")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_initcmd.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/initcmd.py tests/test_initcmd.py
git commit -m "feat(northstar): init command (doctor gate + install + dirs)"
```

---

## Task 7: Project repo resolution + guardrail install

**Files:**
- Create: `northstar/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `proc.run` (1), `assets.templates_dir` (3).
- Produces (this task adds these to `northstar/project.py`):
  - `detect_build_commands(repo_dir: Path) -> dict` — returns `{"lint","build","test"}` from `package.json` scripts if present, else `{}`.
  - `repo_exists(github_repo: str, runner=run) -> bool` — `gh repo view`.
  - `create_repo(github_repo: str, repo_dir: Path, runner=run) -> None` — `gh repo create --private --clone` + minimal scaffold (`README.md`, empty `docs/`).
  - `install_guardrails(repo_dir: Path, project_name: str, lint_cmd: str, build_cmd: str, test_cmd: str) -> None` — copy `claude-settings.json` (with the three commands injected into the hook env), `hooks/precommit_gate.sh` (chmod +x), `CLAUDE.md` (name substituted) into the repo.

- [ ] **Step 1: Write the failing test** in `tests/test_project.py`

```python
import json
from pathlib import Path
from northstar.proc import CommandResult
from northstar import project


def test_detect_build_commands_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"lint": "eslint .", "build": "tsc", "test": "vitest run"}}))
    cmds = project.detect_build_commands(tmp_path)
    assert cmds == {"lint": "npm run lint", "build": "npm run build", "test": "npm test"}


def test_detect_build_commands_empty_when_absent(tmp_path):
    assert project.detect_build_commands(tmp_path) == {}


def test_repo_exists_uses_gh(monkeypatch):
    calls = []
    def runner(cmd, **kw):
        calls.append(" ".join(cmd))
        return CommandResult(0, "", "")
    assert project.repo_exists("o/acme", runner=runner) is True
    assert "gh repo view o/acme" in calls[0]


def test_install_guardrails_writes_settings_hook_and_claude(tmp_path, monkeypatch):
    # point assets at the real repo templates
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    project.install_guardrails(repo, "acme", "make lint", "make build", "make test")
    settings = (repo / ".claude" / "settings.json").read_text()
    assert "make lint" in settings and "make test" in settings
    assert (repo / ".claude" / "hooks" / "precommit_gate.sh").exists()
    claude_md = (repo / "CLAUDE.md").read_text()
    assert "acme" in claude_md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_project.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.project'`

- [ ] **Step 3: Implement `northstar/project.py`** (this task's portion)

```python
from __future__ import annotations
from pathlib import Path
import json
import os
import shutil
import stat

from northstar.proc import run
from northstar.assets import templates_dir


def detect_build_commands(repo_dir: Path) -> dict:
    pkg = Path(repo_dir) / "package.json"
    if not pkg.exists():
        return {}
    try:
        scripts = json.loads(pkg.read_text()).get("scripts", {})
    except json.JSONDecodeError:
        return {}
    out = {}
    if "lint" in scripts:
        out["lint"] = "npm run lint"
    if "build" in scripts:
        out["build"] = "npm run build"
    if "test" in scripts:
        out["test"] = "npm test"
    return out


def repo_exists(github_repo: str, runner=run) -> bool:
    return runner(["gh", "repo", "view", github_repo]).ok


def create_repo(github_repo: str, repo_dir: Path, runner=run) -> None:
    runner(["gh", "repo", "create", github_repo, "--private", "--clone", str(repo_dir)])
    repo_dir = Path(repo_dir)
    (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
    readme = repo_dir / "README.md"
    if not readme.exists():
        readme.write_text(f"# {github_repo}\n")


def install_guardrails(repo_dir: Path, project_name: str,
                       lint_cmd: str, build_cmd: str, test_cmd: str) -> None:
    repo_dir = Path(repo_dir)
    tdir = templates_dir()
    claude_dir = repo_dir / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # settings.json with the project's build commands injected into the hook env
    settings = json.loads((tdir / "claude-settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    hook["command"] = (
        f'LINT_CMD="{lint_cmd}" BUILD_CMD="{build_cmd}" TEST_CMD="{test_cmd}" '
        '$CLAUDE_PROJECT_DIR/.claude/hooks/precommit_gate.sh'
    )
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # gate script (executable)
    gate = hooks_dir / "precommit_gate.sh"
    shutil.copyfile(tdir / "hooks" / "precommit_gate.sh", gate)
    gate.chmod(gate.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # CLAUDE.md with the project name substituted
    tmpl = (tdir / "CLAUDE.md.tmpl").read_text()
    (repo_dir / "CLAUDE.md").write_text(tmpl.replace("{{PROJECT_NAME}}", project_name))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_project.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/project.py tests/test_project.py
git commit -m "feat(northstar): project repo resolution and guardrail install"
```

---

## Task 8: Plane state discovery + `add_project` orchestration

**Files:**
- Modify: `northstar/project.py` (append)
- Test: `tests/test_project.py` (append)

**Interfaces:**
- Consumes: `orchestrator.plane.PlaneClient` (engine), `paths.project_config_path`/`register_project` (2), `assets`/`paths.home` for the mcp path (2,3), the Task-7 functions.
- Produces:
  - `ProjectInputs` dataclass: `name`, `plane_base_url`, `plane_api_key`, `plane_workspace_slug`, `plane_project_id`, `github_repo`, `repo_dir: Path`, `lint_cmd`, `build_cmd`, `test_cmd`, `claude_model="claude-opus-4-8"`, `poll_interval_seconds=30`, `max_concurrency=1`.
  - `discover_state_ids(inp: ProjectInputs, client=None) -> dict[str,str]`.
  - `write_project_config(inp: ProjectInputs, state_ids: dict, mcp_path: Path) -> Path` (writes `~/.northstar/projects/<name>.yaml` in the engine's config schema).
  - `add_project(inp: ProjectInputs, *, runner=run, create_if_missing=False, client=None) -> dict` (resolves/creates repo, installs guardrails, discovers states, writes config, registers; returns the registry meta).

- [ ] **Step 1: Write the failing test** (append to `tests/test_project.py`)

```python
import importlib
from northstar.project import ProjectInputs


def _inputs(tmp_path):
    repo = tmp_path / "repo"; (repo / "docs").mkdir(parents=True)
    return ProjectInputs(
        name="acme", plane_base_url="https://plane.x", plane_api_key="k",
        plane_workspace_slug="w", plane_project_id="p", github_repo="o/acme",
        repo_dir=repo, lint_cmd="make lint", build_cmd="make build", test_cmd="make test")


class FakePlane:
    def __init__(self, *a, **k): pass
    def list_states(self):
        return {"Ready to Dev": "s1", "QA": "s2", "Blocked": "s3"}


def test_discover_state_ids(tmp_path):
    from northstar import project
    ids = project.discover_state_ids(_inputs(tmp_path), client=FakePlane())
    assert ids["QA"] == "s2"


def test_add_project_links_existing_writes_config_and_registers(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.delenv("NORTHSTAR_ASSETS_DIR", raising=False)
    import northstar.paths as paths; importlib.reload(paths)
    from northstar import project
    from northstar.proc import CommandResult
    runner = lambda cmd, **kw: CommandResult(0, "", "")   # gh repo view ok => exists
    inp = _inputs(tmp_path)
    meta = project.add_project(inp, runner=runner, client=FakePlane())
    cfg = paths.project_config_path("acme")
    assert cfg.exists()
    import yaml
    data = yaml.safe_load(cfg.read_text())
    assert data["github_repo"] == "o/acme"
    assert data["state_ids"]["QA"] == "s2"
    assert "acme" in paths.list_projects()
    assert meta["github_repo"] == "o/acme"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_project.py -v`
Expected: FAIL with `ImportError: cannot import name 'ProjectInputs'`

- [ ] **Step 3: Implement (append to `northstar/project.py`)**

```python
from dataclasses import dataclass, field

import yaml
from orchestrator.plane import PlaneClient
from northstar import paths


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


def discover_state_ids(inp: "ProjectInputs", client=None) -> dict:
    client = client or PlaneClient(inp.plane_base_url, inp.plane_api_key,
                                   inp.plane_workspace_slug, inp.plane_project_id)
    return client.list_states()


def write_project_config(inp: "ProjectInputs", state_ids: dict, mcp_path: Path) -> Path:
    cfg = {
        "plane_base_url": inp.plane_base_url,
        "plane_api_key": inp.plane_api_key,
        "plane_workspace_slug": inp.plane_workspace_slug,
        "plane_project_id": inp.plane_project_id,
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


def add_project(inp: "ProjectInputs", *, runner=run,
                create_if_missing=False, client=None) -> dict:
    if not repo_exists(inp.github_repo, runner=runner):
        if not create_if_missing:
            raise RuntimeError(
                f"repo {inp.github_repo} not found; pass create_if_missing=True to create it")
        create_repo(inp.github_repo, inp.repo_dir, runner=runner)
    install_guardrails(inp.repo_dir, inp.name, inp.lint_cmd, inp.build_cmd, inp.test_cmd)
    state_ids = discover_state_ids(inp, client=client)
    mcp_path = paths.home() / "plane-mcp.json"
    write_project_config(inp, state_ids, mcp_path)
    meta = {"github_repo": inp.github_repo, "repo_dir": str(inp.repo_dir),
            "plane_project_id": inp.plane_project_id}
    paths.register_project(inp.name, meta)
    return meta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_project.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/project.py tests/test_project.py
git commit -m "feat(northstar): plane state discovery and add_project orchestration"
```

---

## Task 9: tmux supervisor

**Files:**
- Create: `northstar/supervisor.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `proc.run` (1), `paths.project_config_path`/`log_path` (2).
- Produces: `session_name(project) -> str` (`f"ns-{project}"`), `is_running(project, runner=run) -> bool` (`tmux has-session`), `start(project, repo_dir: Path, plane_env: dict, runner=run) -> None`, `stop(project, runner=run) -> None`, `restart(project, repo_dir, plane_env, runner=run) -> None`, `status(project_names: list[str], runner=run) -> list[dict]` (each `{"name","running"}`), `logs_command(project, follow: bool) -> list[str]`.

- [ ] **Step 1: Write the failing test** in `tests/test_supervisor.py`

```python
import importlib
from northstar.proc import CommandResult


def test_session_name():
    from northstar import supervisor
    assert supervisor.session_name("acme") == "ns-acme"


def test_start_builds_tmux_new_session_with_env_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd if isinstance(cmd, str) else " ".join(cmd))
        # has-session returns non-zero (not running) so start proceeds
        if "has-session" in (cmd if isinstance(cmd, str) else " ".join(cmd)):
            return CommandResult(1, "", "")
        return CommandResult(0, "", "")
    supervisor.start("acme", repo_dir=tmp_path / "repo",
                     plane_env={"PLANE_API_KEY": "k", "PLANE_BASE_URL": "https://x",
                                "PLANE_WORKSPACE_SLUG": "w"}, runner=runner)
    joined = "\n".join(calls)
    assert "tmux new-session -d -s ns-acme" in joined
    assert "python -m orchestrator --config" in joined
    assert "PLANE_API_KEY=k" in joined
    assert "pipe-pane" in joined


def test_stop_kills_session():
    from northstar import supervisor
    calls = []
    runner = lambda cmd, **kw: (calls.append(" ".join(cmd)), CommandResult(0, "", ""))[1]
    supervisor.stop("acme", runner=runner)
    assert "tmux kill-session -t ns-acme" in calls[0]


def test_status_reports_running_flag():
    from northstar import supervisor
    def runner(cmd, **kw):
        # has-session ok only for ns-acme
        c = " ".join(cmd)
        return CommandResult(0 if "ns-acme" in c else 1, "", "")
    rows = supervisor.status(["acme", "beta"], runner=runner)
    by = {r["name"]: r["running"] for r in rows}
    assert by == {"acme": True, "beta": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_supervisor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.supervisor'`

- [ ] **Step 3: Implement `northstar/supervisor.py`**

```python
from __future__ import annotations
from pathlib import Path
import shlex

from northstar.proc import run
from northstar import paths


def session_name(project: str) -> str:
    return f"ns-{project}"


def is_running(project: str, runner=run) -> bool:
    return runner(["tmux", "has-session", "-t", session_name(project)]).ok


def start(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    if is_running(project, runner=runner):
        return
    cfg = paths.project_config_path(project)
    envstr = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in plane_env.items())
    inner = f"env {envstr} python -m orchestrator --config {shlex.quote(str(cfg))}"
    runner(f"tmux new-session -d -s {session_name(project)} -c {shlex.quote(str(repo_dir))} "
           f"{shlex.quote(inner)}", shell=True)
    log = paths.log_path(project)
    runner(f"tmux pipe-pane -t {session_name(project)} -o "
           f"{shlex.quote('cat >> ' + str(log))}", shell=True)


def stop(project: str, runner=run) -> None:
    runner(["tmux", "kill-session", "-t", session_name(project)])


def restart(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    stop(project, runner=runner)
    start(project, repo_dir, plane_env, runner=runner)


def status(project_names, runner=run) -> list[dict]:
    return [{"name": n, "running": is_running(n, runner=runner)} for n in project_names]


def logs_command(project: str, follow: bool) -> list[str]:
    if follow:
        return ["tmux", "attach", "-t", session_name(project)]
    return ["tail", "-n", "200", str(paths.log_path(project))]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_supervisor.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add northstar/supervisor.py tests/test_supervisor.py
git commit -m "feat(northstar): tmux supervisor (start/stop/restart/status/logs)"
```

---

## Task 10: Typer CLI wiring

**Files:**
- Create: `northstar/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `doctor` (4), `initcmd.do_init` (6), `project` (7,8), `supervisor` (9), `paths` (2).
- Produces: a Typer `app` exposing: `doctor [--deep]`, `init [--deep]`, `project add|list|remove`, `start <project>`, `stop <project>`, `restart <project>`, `status`, `logs <project> [-f]`. `project add` reads the per-project config to get `plane_env` for `start`.

- [ ] **Step 1: Write the failing test** in `tests/test_cli.py`

```python
import importlib
from typer.testing import CliRunner
from northstar.doctor import Check

runner = CliRunner()


def test_doctor_command_exit_code_reflects_critical(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    monkeypatch.setattr(cli.doctor, "run_checks",
                        lambda runner=None, deep=False: [Check("git", False, True, "missing", "install git")])
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "git" in result.stdout


def test_init_command_invokes_do_init(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli, "do_init", lambda deep=False: seen.setdefault("deep", deep) or 0)
    result = runner.invoke(cli.app, ["init"])
    assert result.exit_code == 0
    assert seen["deep"] is False


def test_status_lists_registered_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    paths.ensure_dirs(); paths.register_project("acme", {"github_repo": "o/acme"})
    import northstar.cli as cli; importlib.reload(cli)
    monkeypatch.setattr(cli.supervisor, "status",
                        lambda names, runner=None: [{"name": "acme", "running": True}])
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "acme" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'northstar.cli'`

- [ ] **Step 3: Implement `northstar/cli.py`**

```python
from __future__ import annotations
from pathlib import Path
import yaml
import typer

from northstar import doctor, project, supervisor, paths
from northstar.initcmd import do_init

app = typer.Typer(help="northstar — autonomous dev orchestrator CLI", no_args_is_help=True)
project_app = typer.Typer(help="manage projects")
app.add_typer(project_app, name="project")


# Registered as the `doctor` command. The function is NOT named `doctor` because
# `doctor` is the imported module (the test monkeypatches `cli.doctor.run_checks`).
@app.command(name="doctor")
def doctor_cmd(deep: bool = typer.Option(False, "--deep")):
    """Check prerequisites."""
    checks = doctor.run_checks(deep=deep)
    for c in checks:
        mark = "✓" if c.ok else "✗"
        line = f"  {mark} {c.name}: {c.detail}"
        if not c.ok:
            line += f" — {c.fix}"
        typer.echo(line)
    raise typer.Exit(0 if doctor.all_critical_ok(checks) else 1)


@app.command()
def init(deep: bool = typer.Option(False, "--deep")):
    """Set up this machine (checks + install skills to latest)."""
    raise typer.Exit(do_init(deep=deep))


@project_app.command("list")
def project_list():
    for name, meta in paths.list_projects().items():
        typer.echo(f"  {name}  {meta.get('github_repo','')}")


@project_app.command("remove")
def project_remove(name: str):
    paths.unregister_project(name)
    typer.echo(f"removed {name}")


@project_app.command("add")
def project_add(
    name: str = typer.Option(..., prompt=True),
    plane_base_url: str = typer.Option(..., prompt=True),
    plane_api_key: str = typer.Option(..., prompt=True, hide_input=True),
    plane_workspace_slug: str = typer.Option(..., prompt=True),
    plane_project_id: str = typer.Option(..., prompt=True),
    github_repo: str = typer.Option(..., prompt="GitHub repo (owner/name)"),
    repo_dir: Path = typer.Option(..., prompt="Local path for the repo"),
    lint_cmd: str = typer.Option("npm run lint", prompt=True),
    build_cmd: str = typer.Option("npm run build", prompt=True),
    test_cmd: str = typer.Option("npm test", prompt=True),
    create_if_missing: bool = typer.Option(False, "--create"),
):
    """Add or link a project."""
    inp = project.ProjectInputs(
        name=name, plane_base_url=plane_base_url, plane_api_key=plane_api_key,
        plane_workspace_slug=plane_workspace_slug, plane_project_id=plane_project_id,
        github_repo=github_repo, repo_dir=repo_dir,
        lint_cmd=lint_cmd, build_cmd=build_cmd, test_cmd=test_cmd)
    meta = project.add_project(inp, create_if_missing=create_if_missing)
    typer.echo(f"added {name}: {meta['github_repo']}")


def _plane_env(name: str) -> dict:
    cfg = yaml.safe_load(paths.project_config_path(name).read_text())
    return {"PLANE_API_KEY": cfg["plane_api_key"],
            "PLANE_BASE_URL": cfg["plane_base_url"],
            "PLANE_WORKSPACE_SLUG": cfg["plane_workspace_slug"]}


def _repo_dir(name: str) -> Path:
    return Path(paths.list_projects()[name]["repo_dir"])


@app.command()
def start(name: str):
    supervisor.start(name, _repo_dir(name), _plane_env(name))
    typer.echo(f"started ns-{name}")


@app.command()
def stop(name: str):
    supervisor.stop(name)
    typer.echo(f"stopped ns-{name}")


@app.command()
def restart(name: str):
    supervisor.restart(name, _repo_dir(name), _plane_env(name))
    typer.echo(f"restarted ns-{name}")


@app.command()
def status():
    rows = supervisor.status(list(paths.list_projects()))
    for r in rows:
        typer.echo(f"  {'● running' if r['running'] else '○ stopped'}  {r['name']}")


@app.command()
def logs(name: str, follow: bool = typer.Option(False, "-f", "--follow")):
    import subprocess
    subprocess.run(supervisor.logs_command(name, follow))
```

> Implementer note: the `doctor` command is registered with `@app.command(name="doctor")` on a function named `doctor_cmd` (the function can't be named `doctor` — that name is the imported module, which `test_cli.py` monkeypatches via `cli.doctor.run_checks`). The test invokes `["doctor"]` and asserts exit code 1 on a critical failure.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the FULL suite (engine + northstar)**

Run: `.venv/bin/pytest -q`
Expected: PASS (engine's 27 + all northstar tests green)

- [ ] **Step 6: Commit**

```bash
git add northstar/cli.py tests/test_cli.py
git commit -m "feat(northstar): Typer CLI wiring for all commands"
```

---

## Task 11: Usage docs + smoke check

**Files:**
- Create: `docs/northstar-usage.md`
- Modify: `README.md` (create if absent) — point at the northstar commands

**Interfaces:** documentation; no code interfaces.

- [ ] **Step 1: Write `docs/northstar-usage.md`**

```markdown
# northstar — usage

## Install
```bash
pipx install -e .        # or: pip install -e ".[dev]"
```

## Machine setup
```bash
northstar doctor            # check prerequisites
northstar init             # install skills to latest + create ~/.northstar
```
Prerequisites: Python 3.11+, git, GitHub CLI (`gh auth login`), Claude Code (`claude`, logged in),
`uv`/`uvx`, tmux, and Node/`npx` (for the grill-me skill). `doctor` reports each.

## Add a project
```bash
northstar project add      # prompts for Plane details, repo URL, build commands
#   links the repo if it exists; with --create it creates one (gh must be authed)
northstar project list
```

## Run (tmux, detached)
```bash
northstar start <project>      # runs the daemon in tmux session ns-<project>
northstar status               # which projects are running
northstar logs <project> -f    # attach to the live session (Ctrl-b d to detach)
northstar stop <project>
```
```

- [ ] **Step 2: Add a short pointer to `README.md`**

```markdown
# northstar

Autonomous development orchestrator: picks tasks from a self-hosted Plane board and drives them
through build → review → QA → merge using real Claude Code sessions.

See `docs/northstar-usage.md` to get started, and `docs/superpowers/specs/` for the design.
```

- [ ] **Step 3: Run the full suite once more**

Run: `.venv/bin/pytest -q`
Expected: PASS (all green)

- [ ] **Step 4: Commit**

```bash
git add docs/northstar-usage.md README.md
git commit -m "docs(northstar): usage guide and README pointer"
```

---

## Self-Review notes (addressed)

- **Spec §2 (Typer, engine reuse, tmux, layout, bundled assets):** Tasks 1 (pyproject/scaffold), 3 (assets), 9 (tmux), 2 (layout).
- **Spec §3 (doctor checks incl. github-auth, tmux, claude smoke via --deep):** Task 4.
- **Spec §4 (init always-latest, marketplaces, plugin update, native installers, idempotent, copy mcp):** Tasks 5 + 6.
- **Spec §5 (project add: detect build cmds, gh gate, link/create, guardrails with cmds, discover states, register, optional seed):** Tasks 7 + 8. (Seed is the spec's optional item; left to a follow-up — noted in §8 out-of-scope of the spec as "if not present, print manual seed instructions," so no task required for the slice.)
- **Spec §6 (tmux start/stop/restart/status/logs, env exported, no double-start):** Task 9 + CLI in 10.
- **Spec §7 (success criteria — doctor accuracy, idempotent init, project add, run lifecycle, engine stays green):** Tasks 4/6/8/9/10 + full-suite runs in 10 & 11.
- **Spec §8 (out of scope):** no single-daemon-all-projects, no OS service, no engine concurrency changes, no lockfile — none included.
- **Global constraints:** no Anthropic imports (northstar only shells `claude` via runner); always-latest enforced in `skills.install_all`; gh-auth gate in `add_project`; tmux session naming `ns-<project>`; `~/.northstar` via `NORTHSTAR_HOME`.
