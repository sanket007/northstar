# Pluggable Process Backend (tmux optional) — Design Spec

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Extends:** the northstar supervisor (`start/stop/restart/status/logs`).

---

## 1. Goal

Make **tmux optional**. Today the supervisor runs every project's daemon in a tmux session and tmux is a
hard prerequisite. Add a dependency-free **detached** backend; on `init`, if tmux is absent, tell the
user the tradeoffs and offer the fallback. The backend is the user's choice, stored at machine level.

---

## 2. Backends

A small backend abstraction; `start/stop/restart/status/logs` dispatch to the configured one. Both
backends run the same daemon command (`<sys.executable> -m orchestrator --config <cfg>` with the
project's `PLANE_*` env), in the project's `repo_dir`, logging to `~/.northstar/logs/<project>.log`.

### 2a. `tmux` (existing)
- Detached tmux session `ns-<project>`; `pipe-pane` to the log file.
- `logs -f` **attaches to the live session** (real-time, interactive).
- Requires tmux installed.

### 2b. `detached` (new, no dependency)
- `subprocess.Popen([...], cwd=repo_dir, env=merged, stdout=log, stderr=STDOUT, start_new_session=True)`
  — survives the terminal closing. Writes the PID to `~/.northstar/run/<project>.pid`.
- `stop` reads the PID and sends SIGTERM; `status`/`is_running` check `~/.northstar/run/<project>.pid`
  + process liveness (`os.kill(pid, 0)`); `logs -f` does `tail -f` the log file (no interactive attach).
- Refuses to double-start if a live PID exists.

---

## 3. Selection & config

- Machine config `~/.northstar/config.yaml` gains `process_backend: tmux | detached` (default `tmux`).
- **`northstar init`** picks the backend:
  - `--backend tmux|detached|auto` flag overrides (default `auto`).
  - `auto`: if tmux is present → `tmux`; if absent → **print the tradeoffs (§5) and prompt**:
    "tmux not found — use the built-in detached backend instead? [Y/n]". `Y` → `detached`; `n` → abort
    with "install tmux, or re-run with `--backend detached`."
  - The chosen value is written to `~/.northstar/config.yaml`. Re-running `init` (or
    `northstar init --backend …`) changes it.
- `supervisor` reads `process_backend` (default `tmux`) to pick the implementation. If the configured
  backend is `tmux` but tmux is missing at run time, `start` raises a friendly error pointing at
  `init --backend detached`.

---

## 4. `doctor` changes

- The tmux check becomes **non-critical** (warn), labeled "needed only for the tmux process backend."
- `doctor` reports the **active backend** (from config, default tmux) and, if it's `tmux`, whether tmux
  is actually available (so a tmux-configured machine with tmux missing is surfaced).
- `detached` backend needs no extra tool — `doctor` says so.

---

## 5. Tradeoffs (documented in `doctor` output + usage doc)

- **tmux:** live-attach to the running session (`logs -f` shows it in real time, re-attach from any
  terminal). Costs an extra dependency (tmux).
- **detached:** zero extra dependencies; logs are file-based (`logs -f` tails the file — no interactive
  attach). Slightly less convenient to watch a live session.
- **Both:** survive your terminal closing. **Neither survives a reboot** (both are user processes) — an
  OS-service backend (launchd/systemd) that auto-starts on boot remains a later roadmap item.

---

## 6. Success criteria

- `init --backend detached` (or `auto` with tmux absent, answering `Y`) writes `process_backend: detached`
  to `~/.northstar/config.yaml`; `init --backend tmux` writes `tmux`. (Unit-tested with a fake runner +
  `NORTHSTAR_HOME`.)
- `doctor` reports tmux as non-critical and prints the active backend; missing tmux does **not** fail
  `all_critical_ok` when the backend is `detached`. (Tested.)
- `supervisor.start/stop/status` dispatch to the configured backend. The **detached** backend: `start`
  spawns a detached process + writes a PID file (tested with a fake spawner asserting the command, cwd,
  env, and `start_new_session`); `is_running` reflects PID liveness; `stop` signals the PID; double-start
  is refused. The **tmux** path is unchanged (existing tests stay green).
- Full suite stays green (88 + new tests).

---

## 7. Out of scope

- OS-service backend (launchd/systemd) + reboot persistence — later roadmap item.
- Per-project backend (machine-level only for now).
- Windows process management (mac/Linux first).
- Changing the daemon itself, the engine, or the Plane integration.
