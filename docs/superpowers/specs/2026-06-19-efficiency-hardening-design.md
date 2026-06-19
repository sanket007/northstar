# Efficiency Hardening — Design Spec

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Origin:** a 3-part efficiency audit (role docs / engine harness / CLI) run before building the planning bridge + dependency scheduler on top.

---

## 1. Goal

Tighten what's already built — engine resilience + per-session token cost + CLI ergonomics — so the
foundation is solid before the planning bridge and dependency-aware scheduler land on it. Every change
is covered by tests; behavior stays the same except where noted (the daemon survives transient errors;
sessions read less; setup errors are friendlier).

---

## 2. Engine (`orchestrator/`)

### 2.1 Resilience — retry/backoff + daemon survives transient errors (HIGH)
- Add a small retry helper used by all Plane HTTP calls in `plane.py`: retry on connect/timeout errors,
  HTTP 429, and 5xx; up to 3 attempts with exponential backoff (e.g. 0.5s, 1s, 2s); re-raise other
  errors (4xx) immediately. Inject a `sleep` callable so tests don't actually wait.
- Wrap `poll_once(...)` inside `poller.run`'s loop in a `try/except`: on any exception, log to stderr
  and continue to the next cycle — **a transient Plane error must never kill the daemon.**

### 2.2 Poll efficiency (HIGH)
- In `poll_once`, add an early `if ownership.count() >= cfg.max_concurrency: return` **before** the
  per-state list call, so once all slots are full we stop issuing list requests that cycle.
- Pass a small `per_page` to `list_issues_in_state` (we only need up to `max_concurrency` free slots,
  not the whole backlog) to cap pagination volume.

### 2.3 Cheap cleanups (MEDIUM/LOW)
- Cache role docs at launcher import/startup (read each `templates/<role>.md` once into a dict) instead
  of `read_text()` on every dispatch.
- Build the `PlaneClient` once in `poller.run` and inject it into `make_dispatch` (one shared connection
  pool/headers instead of two clients).
- Remove the unused `worktree` parameter from `build_claude_command` (dead — never used in the command).

### Deferred (to the concurrency phase, with reason)
- Stream `stdout` line-by-line instead of `communicate()` buffering — only matters at scale/long runs.
- Atomic claim-if-under-cap in `poll_once` — only needed when `max_concurrency > 1`.
- Dropping the redundant client-side `state` filter — needs live-API verification first; safe to keep.

---

## 3. Role docs & launcher prompt (`templates/`, `orchestrator/launcher.py`)

The role doc is re-sent verbatim every session via `--append-system-prompt`, and `CLAUDE.md` is
auto-loaded every session — so anything duplicated across them is paid for on every task.

### 3.1 De-duplicate context hydration (HIGH)
- Move the generic hydration recipe ("reconstruct context from the ticket + comment trail + PR thread +
  `docs/` + git history before acting") to **`CLAUDE.md.tmpl` only**.
- Each role doc keeps just its **role-specific** hydration extras (builder: latest comment intent;
  reviewer: the PR diff; QA: extract the acceptance criteria) — not the full recipe.

### 3.2 Cap comment re-reading (HIGH — this is the cost that compounds)
- Replace "read **every** comment (paginate to the end)" with: "read the **latest** comment and any
  comments **since your last state move**; skim earlier trail only if needed." Caps token growth as
  tickets accumulate history.

### 3.3 Trim the launcher `-p` prompt (HIGH)
- The `-p` prompt currently restates "follow your role instructions; hydrate context…" — a third copy.
  Reduce it to the **dynamic facts only**: the role and the ticket id. All procedure lives in the
  appended role doc + CLAUDE.md.

### 3.4 Correctness & churn guards (MEDIUM)
- Fix the malformed builder context-load tag (`🤖 [builder] <FROM> → <FROM>`) to a proper context-load
  line that doesn't imply a transition.
- Add a one-line **idempotency guard** to builder and QA: "if the ticket has already moved past your
  stage (check current state before transitioning), do not re-post or re-move." Prevents duplicate
  comments and wasted re-work sessions.
- Drop the builder's restated `karpathy-guidelines` line (already always-on via CLAUDE.md); compress the
  most verbose human-narration paragraphs (~15–20% shrink) without losing any instruction.

---

## 4. CLI (`northstar/`)

### 4.1 Single project load (HIGH)
- Add `load_project(name) -> ProjectRuntime` (a small dataclass/namedtuple with `meta`, `repo_dir`,
  `plane_env`, `cfg_path`) that parses the registry and the per-project config **at most once each**.
  `start`/`restart`/`status`/`logs` use it instead of the current `_repo_dir` + `_plane_env` +
  `supervisor`'s own re-read.

### 4.2 Friendly Plane errors (HIGH)
- Add a `_request(method, url, **kw)` wrapper in `PlaneAdmin` that issues the call and, on an httpx
  connect/timeout/`HTTPStatusError`, raises `RuntimeError` with status + URL + a hint (so `project add`
  shows "Plane returned 401 at …/projects/ — check the API key/URL", not a raw traceback). Route all
  `PlaneAdmin` HTTP through it.

### 4.3 Cheaper skill install (MEDIUM)
- In `skills.install_all`, call `installed_plugins()` once up front; **skip `install` for plugins
  already present** and run `update` only for those (or all, but not install+update unconditionally).

### 4.4 Wire in build-command detection (LOW)
- Use `detect_build_commands(repo_dir)` to **prefill** the lint/build/test prompts in `project add` when
  the repo has a `package.json` (it's currently computed nowhere). Falls back to the npm defaults.

### Deferred (with reason)
- Sharing one Plane HTTP base between `orchestrator/plane.py` and `northstar/plane_admin.py` — a larger
  refactor that couples the two packages; the friendly-error wrapper (4.2) lives in `plane_admin` for now.
- Running `doctor` checks concurrently — a latency nicety, not correctness; revisit if `doctor` feels slow.
- Replacing per-project `tmux has-session` with one `list-sessions` parse — negligible at current N.

---

## 5. Success criteria

- **Resilience:** a unit test injects a Plane client that raises a 5xx then succeeds; the retry helper
  recovers, and `poll_once` raising once does not stop `run` (the loop continues). The daemon survives.
- **Token cost:** the context-hydration recipe appears in `CLAUDE.md.tmpl` and **not** verbatim in all
  three role docs; the launcher `-p` prompt no longer restates hydration; comment-reading instruction is
  capped. (Verified by inspection + a doc-content test grep.)
- **CLI:** `project add` against a bad Plane URL/key surfaces a `RuntimeError` with status+URL (tested
  with a faked failing client), not a raw httpx traceback; `load_project` parses each file once (tested);
  `install_all` skips install for already-present plugins (tested via the fake runner + `installed_plugins`).
- **No regressions:** the full existing suite (69) stays green; the malformed builder tag and dead
  `worktree` param are gone.

---

## 6. Out of scope

- The planning bridge and dependency-aware scheduler (the next spec — this one just cleans the ground).
- Concurrency > 1 and everything gated on it (atomic claim, stream-json streaming).
- Cross-package HTTP-base sharing; `doctor` concurrency; observability/cost tracking.
- Any change to the board state machine, the Plane setup reconcile, or the public CLI command surface
  (these are tightenings, not new behavior).
