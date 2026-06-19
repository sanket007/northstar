# Pluggable Process Backend (tmux optional) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tmux optional — add a dependency-free `detached` process backend, choose the backend at `init` (auto-detect tmux, prompt to fall back, `--backend` override), surface the tradeoffs, and have the supervisor + doctor honor the choice.

**Architecture:** A machine-level setting (`~/.northstar/config.yaml: process_backend`) selects the backend. The supervisor dispatches `start/stop/restart/status/logs` to a `tmux` impl (existing) or a new `detached` impl (Popen + PID file + log file). `init` resolves the backend; `doctor` makes tmux non-critical and reports the active backend.

**Tech Stack:** Python 3.11+, Typer, pytest, PyYAML, stdlib `subprocess`/`os`/`signal`.

## Global Constraints

- Default backend is `tmux` (existing behavior preserved when a machine config is absent).
- Both backends run `<sys.executable> -m orchestrator --config <cfg>` in `repo_dir` with the project's `PLANE_*` env merged into `os.environ`, logging to `~/.northstar/logs/<project>.log`.
- No Anthropic/SDK imports. Machine config path honors `NORTHSTAR_HOME`.
- Full suite (88) stays green; the existing tmux supervisor tests keep passing under the default backend.

---

## File Structure (touched)

```
northstar/paths.py        # Task 1: machine config (process_backend)
northstar/supervisor.py   # Task 2: backend dispatch + detached impl
northstar/doctor.py       # Task 3: tmux non-critical + report backend
northstar/initcmd.py cli.py  # Task 4: backend selection + --backend flag + docs
tests/test_paths.py test_supervisor.py test_doctor.py test_initcmd.py test_cli.py
docs/northstar-usage.md docs/SETUP-AND-TEST.md   # Task 4 doc updates
```

---

## Task 1: Machine config (`process_backend`)

**Files:**
- Modify: `northstar/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `machine_config_path() -> Path` (`home()/config.yaml`), `load_machine_config() -> dict`,
  `save_machine_config(cfg: dict)`, `get_backend() -> str` (default `"tmux"`), `set_backend(backend: str)`.

- [ ] **Step 1: Write the failing test** `tests/test_paths.py` (append)

```python
def test_backend_default_and_roundtrip(tmp_path, monkeypatch):
    p = reload_paths(tmp_path, monkeypatch)
    p.ensure_dirs()
    assert p.get_backend() == "tmux"          # default when unset
    p.set_backend("detached")
    assert p.get_backend() == "detached"
    assert p.machine_config_path() == p.home() / "config.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_paths.py -k backend -v`
Expected: FAIL (`get_backend` missing)

- [ ] **Step 3: Implement in `northstar/paths.py`**

```python
def machine_config_path() -> Path:
    return home() / "config.yaml"


def load_machine_config() -> dict:
    p = machine_config_path()
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def save_machine_config(cfg: dict) -> None:
    ensure_dirs()
    machine_config_path().write_text(yaml.safe_dump(cfg, sort_keys=True))


def get_backend() -> str:
    return load_machine_config().get("process_backend", "tmux")


def set_backend(backend: str) -> None:
    cfg = load_machine_config()
    cfg["process_backend"] = backend
    save_machine_config(cfg)
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_paths.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add northstar/paths.py tests/test_paths.py
git commit -m "feat(cli): machine config with process_backend (default tmux)"
```

---

## Task 2: Supervisor — backend dispatch + detached impl

**Files:**
- Modify: `northstar/supervisor.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- Public (unchanged signatures): `session_name(project)`, `is_running(project, runner=run) -> bool`,
  `start(project, repo_dir, plane_env, runner=run)`, `stop(project, runner=run)`,
  `restart(project, repo_dir, plane_env, runner=run)`, `status(project_names, runner=run) -> list[dict]`,
  `logs_command(project, follow) -> list[str]` — each dispatches on `paths.get_backend()`.
- New detached internals: `_detached_start(project, repo_dir, plane_env, spawn=subprocess.Popen)`,
  `_detached_is_running(project)`, `_detached_stop(project)`, `_pid_path(project)`.

- [ ] **Step 1: Write the failing tests** `tests/test_supervisor.py` (append)

```python
def test_detached_start_spawns_and_writes_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    paths.ensure_dirs(); paths.set_backend("detached")
    paths.project_config_path("acme").write_text("plane_api_key: K\n")
    captured = {}
    class FakeProc:
        pid = 4321
    def fake_spawn(cmd, **kw):
        captured["cmd"] = cmd; captured["cwd"] = kw.get("cwd"); captured["env"] = kw.get("env")
        captured["new_session"] = kw.get("start_new_session")
        return FakeProc()
    supervisor._detached_start("acme", tmp_path / "repo",
                               {"PLANE_API_KEY": "K"}, spawn=fake_spawn)
    import sys
    assert sys.executable in captured["cmd"] and "-m" in captured["cmd"] and "orchestrator" in captured["cmd"]
    assert captured["cwd"] == str(tmp_path / "repo")
    assert captured["env"]["PLANE_API_KEY"] == "K"
    assert captured["new_session"] is True
    assert supervisor._pid_path("acme").read_text().strip() == "4321"


def test_start_dispatches_to_detached(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    paths.ensure_dirs(); paths.set_backend("detached")
    called = {}
    monkeypatch.setattr(supervisor, "_detached_start",
                        lambda p, r, e, **kw: called.setdefault("detached", True))
    monkeypatch.setattr(supervisor, "_tmux_start",
                        lambda *a, **kw: called.setdefault("tmux", True))
    supervisor.start("acme", tmp_path / "repo", {"PLANE_API_KEY": "K"})
    assert called == {"detached": True}


def test_logs_command_detached_is_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    import northstar.supervisor as supervisor; importlib.reload(supervisor)
    paths.ensure_dirs(); paths.set_backend("detached")
    assert supervisor.logs_command("acme", follow=True)[0] == "tail"
```

(The existing tmux tests stay as-is — with no machine config / default backend `tmux`, dispatch routes to the tmux impl. Ensure those tests still set up `NORTHSTAR_HOME` without a backend so the default applies. If any pre-existing tmux test now sees a leaked `detached` config, set `paths.set_backend("tmux")` at its start.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_supervisor.py -k "detached or dispatches" -v`
Expected: FAIL

- [ ] **Step 3: Refactor `northstar/supervisor.py`**

Keep the existing tmux logic but move it behind `_tmux_*` and add the detached impl + dispatch:

```python
from __future__ import annotations
from pathlib import Path
import os
import shlex
import signal
import subprocess
import sys

from northstar.proc import run
from northstar import paths


# ---- tmux backend (existing behavior) ----
def session_name(project: str) -> str:
    return f"ns-{project}"


def _tmux_is_running(project: str, runner) -> bool:
    return runner(["tmux", "has-session", "-t", session_name(project)]).ok


def _tmux_start(project: str, repo_dir: Path, plane_env: dict, runner) -> None:
    if _tmux_is_running(project, runner):
        return
    cfg = paths.project_config_path(project)
    envstr = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in plane_env.items())
    inner = (f"env {envstr} {shlex.quote(sys.executable)} -m orchestrator "
             f"--config {shlex.quote(str(cfg))}")
    runner(f"tmux new-session -d -s {session_name(project)} -c {shlex.quote(str(repo_dir))} "
           f"{shlex.quote(inner)}", shell=True)
    log = paths.log_path(project)
    runner(f"tmux pipe-pane -t {session_name(project)} -o {shlex.quote('cat >> ' + str(log))}",
           shell=True)


def _tmux_stop(project: str, runner) -> None:
    runner(["tmux", "kill-session", "-t", session_name(project)])


def _tmux_logs_command(project: str, follow: bool) -> list[str]:
    if follow:
        return ["tmux", "attach", "-t", session_name(project)]
    return ["tail", "-n", "200", str(paths.log_path(project))]


# ---- detached backend (no dependency) ----
def _pid_path(project: str) -> Path:
    return paths.home() / "run" / f"{project}.pid"


def _detached_is_running(project: str) -> bool:
    p = _pid_path(project)
    if not p.exists():
        return False
    try:
        pid = int(p.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _detached_start(project: str, repo_dir: Path, plane_env: dict,
                    spawn=subprocess.Popen) -> None:
    if _detached_is_running(project):
        return
    (paths.home() / "run").mkdir(parents=True, exist_ok=True)
    cfg = paths.project_config_path(project)
    log = paths.log_path(project)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "orchestrator", "--config", str(cfg)]
    env = {**os.environ, **plane_env}
    logf = open(log, "a")
    proc = spawn(cmd, cwd=str(repo_dir), env=env, stdout=logf,
                 stderr=subprocess.STDOUT, start_new_session=True)
    _pid_path(project).write_text(str(proc.pid))


def _detached_stop(project: str) -> None:
    p = _pid_path(project)
    if not p.exists():
        return
    try:
        os.kill(int(p.read_text().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    p.unlink(missing_ok=True)


def _detached_logs_command(project: str, follow: bool) -> list[str]:
    log = str(paths.log_path(project))
    return ["tail", "-f", log] if follow else ["tail", "-n", "200", log]


# ---- dispatch ----
def is_running(project: str, runner=run) -> bool:
    if paths.get_backend() == "detached":
        return _detached_is_running(project)
    return _tmux_is_running(project, runner)


def start(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    if paths.get_backend() == "detached":
        _detached_start(project, repo_dir, plane_env)
        return
    if not runner(["tmux", "-V"]).ok:
        raise RuntimeError("tmux backend configured but tmux not found — "
                           "run `northstar init --backend detached`")
    _tmux_start(project, repo_dir, plane_env, runner)


def stop(project: str, runner=run) -> None:
    if paths.get_backend() == "detached":
        _detached_stop(project)
        return
    _tmux_stop(project, runner)


def restart(project: str, repo_dir: Path, plane_env: dict, runner=run) -> None:
    stop(project, runner=runner)
    start(project, repo_dir, plane_env, runner=runner)


def status(project_names, runner=run) -> list[dict]:
    return [{"name": n, "running": is_running(n, runner=runner)} for n in project_names]


def logs_command(project: str, follow: bool) -> list[str]:
    if paths.get_backend() == "detached":
        return _detached_logs_command(project, follow)
    return _tmux_logs_command(project, follow)
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_supervisor.py -v && .venv/bin/pytest -q`
Expected: PASS (new detached tests + existing tmux tests under default backend)

- [ ] **Step 5: Commit**

```bash
git add northstar/supervisor.py tests/test_supervisor.py
git commit -m "feat(cli): pluggable supervisor backend — add detached (no-tmux) impl + dispatch"
```

---

## Task 3: doctor — tmux non-critical + report backend

**Files:**
- Modify: `northstar/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:** `run_checks` now marks the tmux check **non-critical** and appends a non-critical
`process-backend` check reporting `paths.get_backend()`.

- [ ] **Step 1: Update the failing/over-strict test + add a new one** in `tests/test_doctor.py`

Replace `test_missing_tmux_is_critical_failure` with:
```python
def test_missing_tmux_is_not_critical():
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "npx"]}
    table["gh"] = CommandResult(0, "Logged in", "")
    checks = run_checks(runner=fake_runner_factory(table))      # tmux absent -> 127
    tmux = next(c for c in checks if c.name == "tmux")
    assert tmux.ok is False and tmux.critical is False          # now a warning, not critical
    assert all_critical_ok(checks) is True                      # missing tmux no longer blocks


def test_reports_active_process_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    import northstar.paths as paths; importlib.reload(paths)
    paths.ensure_dirs(); paths.set_backend("detached")
    import northstar.doctor as doctor; importlib.reload(doctor)
    ok = CommandResult(0, "v1", "")
    table = {t: ok for t in ["python3", "git", "gh", "claude", "uvx", "tmux", "npx"]}
    table["gh"] = CommandResult(0, "Logged in", "")
    checks = doctor.run_checks(runner=fake_runner_factory(table))
    backend = next(c for c in checks if c.name == "process-backend")
    assert "detached" in backend.detail and backend.critical is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_doctor.py -k "tmux or backend" -v`
Expected: FAIL

- [ ] **Step 3: Edit `northstar/doctor.py`**

Add `from northstar import paths` at the top. Change the tmux check to non-critical and append the
backend check:
```python
    checks.append(_tool(runner, "tmux", ["tmux", "-V"], critical=False,
                        fix="install tmux (needed only for the tmux backend; or use --backend detached)"))
    checks.append(_tool(runner, "npx", ["npx", "--version"], critical=False,
                        fix="install Node.js (needed for the grill-me skill)"))
    checks.append(Check("process-backend", True, False, paths.get_backend(), ""))
    return checks
```
(Leave the other checks unchanged; `tmux` moves from `critical=True` to `critical=False`.)

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_doctor.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add northstar/doctor.py tests/test_doctor.py
git commit -m "feat(cli): doctor marks tmux non-critical and reports the active process backend"
```

---

## Task 4: init backend selection + `--backend` flag + docs

**Files:**
- Modify: `northstar/initcmd.py`, `northstar/cli.py`, `docs/northstar-usage.md`, `docs/SETUP-AND-TEST.md`
- Test: `tests/test_initcmd.py`, `tests/test_cli.py`

**Interfaces:**
- `do_init(runner=run, deep=False, backend="tmux") -> int` — after the doctor gate + dirs + skills, calls
  `paths.set_backend(backend)`.
- `cli.init(deep=False, backend="auto")` — resolves `auto` (tmux present → `tmux`; absent → print tradeoffs
  + `typer.confirm` → `detached`, or abort), then calls `do_init(deep=deep, backend=resolved)`.

- [ ] **Step 1: Write failing tests**

`tests/test_initcmd.py` (append):
```python
def test_do_init_sets_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_HOME", str(tmp_path / ".northstar"))
    monkeypatch.setenv("NORTHSTAR_ASSETS_DIR", str(tmp_path / "assets"))
    (tmp_path / "assets" / "templates").mkdir(parents=True)
    (tmp_path / "assets" / "plane-mcp.json").write_text("{}")
    import northstar.initcmd as initcmd; initcmd = importlib.reload(initcmd)
    from northstar.doctor import Check
    monkeypatch.setattr(initcmd, "run_checks", lambda runner, deep=False: [Check("git", True, True, "ok", "")])
    monkeypatch.setattr(initcmd, "install_all", lambda runner: [])
    rc = initcmd.do_init(runner=lambda *a, **k: __import__("northstar.proc", fromlist=["CommandResult"]).CommandResult(0, "", ""),
                         backend="detached")
    assert rc == 0
    import northstar.paths as paths; importlib.reload(paths)
    assert paths.get_backend() == "detached"
```

`tests/test_cli.py` (append):
```python
def test_init_passes_backend_through(monkeypatch):
    import northstar.cli as cli; importlib.reload(cli)
    seen = {}
    monkeypatch.setattr(cli, "do_init", lambda deep=False, backend="tmux": seen.update(backend=backend) or 0)
    result = runner.invoke(cli.app, ["init", "--backend", "detached"])
    assert result.exit_code == 0
    assert seen["backend"] == "detached"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_initcmd.py -k set_backend tests/test_cli.py -k backend_through -v`
Expected: FAIL

- [ ] **Step 3: Edit `northstar/initcmd.py`**

Add `from northstar import paths` (if not already imported via `from northstar import paths`), and the
`backend` param:
```python
def do_init(runner=run, deep=False, backend="tmux") -> int:
    checks = run_checks(runner=runner, deep=deep)
    if not all_critical_ok(checks):
        failed = [c for c in checks if c.critical and not c.ok]
        print("Cannot init — fix these first:")
        for c in failed:
            print(f"  ✗ {c.name}: {c.detail} — {c.fix}")
        return 1
    paths.ensure_dirs()
    copy_plane_mcp_to(paths.home())
    paths.set_backend(backend)
    results = install_all(runner=runner)
    for name, ok, kind in results:
        print(f"  {'✓' if ok else '⚠'} {name} ({kind})")
    print(f"  process backend: {backend}")
    return 0
```

- [ ] **Step 4: Edit `northstar/cli.py`** — resolve `auto`/prompt in the CLI, add the flag:

```python
@app.command()
def init(deep: bool = typer.Option(False, "--deep"),
         backend: str = typer.Option("auto", "--backend",
                                      help="process backend: auto|tmux|detached")):
    """Set up this machine (checks + install skills to latest)."""
    resolved = backend
    if backend == "auto":
        if proc.run(["tmux", "-V"]).ok:
            resolved = "tmux"
        else:
            typer.echo(
                "tmux not found.\n"
                "  • tmux: live-attach to the running session (needs tmux installed)\n"
                "  • detached: no extra dependency; logs via file (no live attach)\n"
                "  Both survive your terminal closing; neither survives a reboot.")
            if typer.confirm("Use the built-in detached backend?", default=True):
                resolved = "detached"
            else:
                typer.echo("Aborted. Install tmux, or re-run with --backend detached.")
                raise typer.Exit(1)
    raise typer.Exit(do_init(deep=deep, backend=resolved))
```
(Ensure `from northstar import ... proc` is imported in cli.py — add `proc` to the existing northstar import, or `from northstar import proc`.)

- [ ] **Step 5: Update docs**

In `docs/SETUP-AND-TEST.md` section 1, move tmux to optional:
```markdown
- **tmux** is **optional** — for the live-attach process backend. If you skip it, `northstar init` will
  offer the built-in **detached** backend (no extra dependency; logs via file). You choose at init.
```
And in section 3, note: "`init` will ask about the process backend if tmux isn't installed (or pass
`northstar init --backend tmux|detached`)."

In `docs/northstar-usage.md`, under Machine setup, add:
```markdown
northstar picks a **process backend** at init: `tmux` (live-attach, needs tmux) or `detached`
(no dependency, logs via file). Override with `northstar init --backend tmux|detached`. Both survive the
terminal closing; neither survives a reboot.
```

- [ ] **Step 6: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_initcmd.py tests/test_cli.py -v && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add northstar/initcmd.py northstar/cli.py tests/test_initcmd.py tests/test_cli.py docs/northstar-usage.md docs/SETUP-AND-TEST.md
git commit -m "feat(cli): init resolves process backend (auto-detect tmux, prompt fallback, --backend) + docs"
```

---

## Self-Review notes (addressed)

- **Spec §2 (two backends, shared daemon command):** Task 2 (`_tmux_*` + `_detached_*`, both use `sys.executable -m orchestrator`).
- **Spec §3 (machine config, init auto/prompt/--backend, runtime friendly error):** Task 1 (config) + Task 4 (init resolve/prompt/flag) + Task 2 (`start` raises if tmux backend but tmux missing).
- **Spec §4 (doctor tmux non-critical + report backend):** Task 3.
- **Spec §5 (tradeoffs surfaced):** Task 4 (init prompt text + both doc updates).
- **Spec §6 (success criteria):** Task 1 (roundtrip), Task 2 (detached start/dispatch/logs tests), Task 3 (non-critical + backend-report tests), Task 4 (set_backend + flag-passthrough tests); full-suite runs each task.
- **Spec §7 (out of scope):** no OS-service backend, no per-project backend, no Windows — none included.
- **Disjoint files (after Task 1):** T2 `supervisor.py`(+test), T3 `doctor.py`(+test), T4 `initcmd.py`+`cli.py`+docs(+test_initcmd, test_cli). No shared files → T2/T3/T4 parallel-safe. Task 1 (`paths.py`) lands first since 2/3/4 import `get_backend`/`set_backend`.
- **Default-backend compatibility:** with no machine config, `get_backend()` returns `"tmux"`, so all existing supervisor tests route to the tmux impl unchanged.
```
