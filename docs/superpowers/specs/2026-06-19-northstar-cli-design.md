# northstar CLI — Design Spec

**Date:** 2026-06-19
**Status:** Approved (design); pending implementation plan
**Repo:** the current repo, renamed to **`northstar`** (the orchestrator becomes its engine)

---

## 1. Goal

Turn the orchestrator into a single CLI, **`northstar`**, that does everything from the terminal:
machine setup (prerequisite checks + installing the skill/plugin stack to latest), project setup
(link or create a GitHub repo, install guardrails, wire Plane), and running the autonomous daemon
per project in a "start it and forget it" way.

Replaces the manual two-phase runbook (machine prereqs → per-project sandbox setup) with:

```
northstar doctor          # are we ready?
northstar init            # set this machine up (skills, marketplaces, dirs) — always latest
northstar project add     # add/link a project
northstar start <project> # run it, detached (tmux); forget about it
northstar status / logs / stop
```

---

## 2. Architecture

- **Language/framework:** Python + **Typer** (Click-based) CLI. Installed via `pipx install` (or
  `pip install -e .`). Entry points: `northstar` and short alias `ns`.
- **Engine reuse:** the existing `orchestrator/` package is unchanged as the daemon engine. The
  new `northstar/` package wraps it: it shells out to `claude`, `gh`, `git`, `tmux`, `uvx`, and
  calls `python -m orchestrator` to run a project.
- **No Anthropic SDK** anywhere (same global constraint as the engine).
- **Process model:** **tmux-backed supervisor.** Each running project is a detached tmux session
  named `ns-<project>`; this is how "run and forget" survives the terminal closing, and how
  `logs` can attach to a live session. tmux is therefore a hard prerequisite (checked by `doctor`).

### File layout (machine state)

```
~/.northstar/
  config.yaml              # machine config: defaults, path to the bundled skill list
  projects/<name>.yaml     # per-project orchestrator config (the existing config schema)
  registry.yaml            # list of registered projects + metadata (repo, plane project, repo_dir)
  logs/<name>.log          # daemon logs (tmux pipe-pane target)
```

### Package layout (added to the repo)

```
northstar/
  __init__.py
  cli.py            # Typer app: wires the command groups, `northstar` + `ns` entry points
  doctor.py         # prerequisite checks (returns structured results)
  skills.py         # the version-controlled skill/plugin list (source of truth) + install/update
  initcmd.py        # `init`: doctor + install/update skills + create ~/.northstar
  project.py        # `project add/list/remove`: link/create repo, guardrails, plane wiring, registry
  supervisor.py     # tmux start/stop/restart/status/logs
  paths.py          # ~/.northstar path helpers + registry read/write
tests/
  test_doctor.py
  test_skills.py
  test_project.py
  test_supervisor.py
  test_registry.py
```

`pyproject.toml` gains `typer` as a dependency and:
```toml
[project.scripts]
northstar = "northstar.cli:app"
ns = "northstar.cli:app"
```
Project `name` is renamed to `northstar`.

**Bundled assets:** `templates/` and `plane-mcp.json` are shipped as package data and resolved via
`importlib.resources` (not a hard-coded path), so the CLI finds them when pip/pipx-installed. On
`project add`, `plane-mcp.json` is copied into `~/.northstar/` and the per-project config's
`mcp_config_path` / `templates_dir` point at the resolved installed locations.

---

## 3. `northstar doctor` — prerequisite checks

Each check returns `(name, ok: bool, detail, fix_hint)`. The command prints a ✅/❌ table and exits
non-zero if any **critical** check fails. Checks:

| Check | Critical | How |
|---|---|---|
| Python ≥ 3.11 | yes | `sys.version_info` |
| `git` present | yes | `git --version` |
| `gh` present | yes | `gh --version` |
| **GitHub reachable** | yes | `gh auth status` exit 0 — required before any repo create/link |
| `claude` present + working | yes | `claude --version`; `--deep` flag also runs a tiny `claude -p "reply OK"` smoke test |
| `uv` / `uvx` present | yes | `uvx --version` (runs the Plane MCP server) |
| `tmux` present | yes | `tmux -V` (the run backend) |
| `node`/`npx` present | warn | `npx --version` (needed for the grill-me installer) |
| Skill plugins installed | warn | `claude plugin list --json` parsed against the bundled skill list |
| caveman / grill-me present | warn | presence check (see §4) |

`doctor` is also invoked at the start of `init` and before any repo create/link in `project add`.

---

## 4. `northstar init` — machine setup (always latest)

Runs `doctor` (aborts on critical failures with fix hints), creates `~/.northstar/` and its
subdirs, then installs/updates the skill stack to **latest**. Idempotent — re-running re-syncs.

The skill list is defined in `northstar/skills.py` as the single source of truth:

```python
PLUGINS = [
  Plugin("superpowers",            marketplace="claude-plugins-official", add="anthropics/claude-plugins-official"),
  Plugin("frontend-design",        marketplace="claude-plugins-official", add="anthropics/claude-plugins-official"),
  Plugin("playwright",             marketplace="claude-plugins-official", add="anthropics/claude-plugins-official"),
  Plugin("andrej-karpathy-skills", marketplace="karpathy-skills",         add="multica-ai/andrej-karpathy-skills"),
]
NATIVE = [
  Native("caveman",  kind="script", cmd="curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash"),
  Native("grill-me", kind="npx",    cmd="npx --yes skills@latest add mattpocock/skills"),
]
```

**Plugin install/update (scriptable, non-interactive):**
```bash
# add marketplaces (idempotent; ignore "already added")
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin marketplace add multica-ai/andrej-karpathy-skills
# refresh catalogs to latest, then install + update each plugin
claude plugin marketplace update
for p in superpowers@claude-plugins-official frontend-design@claude-plugins-official \
         playwright@claude-plugins-official andrej-karpathy-skills@karpathy-skills; do
  claude plugin install "$p" --scope user 2>/dev/null || true
  claude plugin update  "$p" --scope user 2>/dev/null || true
done
```

**Native installers (auto-run, best-effort):** run caveman's `curl | bash` and grill-me's
`npx skills add` automatically. These can be flaky/interactive (grill-me uses a picker), so each is
wrapped: run it, then verify presence; if a native install can't be confirmed, `init` prints the
exact one-line command and a ⚠️, but does NOT fail the whole run. `doctor` reports their status
afterward. Detection: official plugins via `claude plugin list --json`; caveman/grill-me by checking
whether their skill is discoverable (the skill directory exists under the Claude skills/plugins path).

---

## 5. `northstar project add` — project setup

Interactive flow (flags allow non-interactive use):

1. **Collect inputs:** project name; Plane `base_url`, `api_key`, `workspace_slug`, `project_id`;
   the **GitHub repo URL** (owner/name or full URL); and the build commands `LINT_CMD` / `BUILD_CMD`
   / `TEST_CMD` (auto-detected from `package.json` if present, else prompted — keeps it
   language-agnostic and feeds the guardrail hook).
2. **Verify GitHub reachable** (`doctor`'s `gh auth status` check) — hard gate before create/link.
3. **Resolve the repo:**
   - If `gh repo view <owner/name>` succeeds → **link** it. Clone to `repo_dir` if not already local
     (ask for/confirm the local path).
   - If it does NOT exist → offer to **create** it: `gh repo create <name> --private --clone` into
     the chosen `repo_dir`, with a minimal scaffold (`README.md`, empty `docs/`). (Only offered once
     the GitHub-reachable check has passed.)
4. **Install guardrails into the repo:**
   - `templates/claude-settings.json` → `<repo>/.claude/settings.json`, with the hook env populated
     with the project's `LINT_CMD`/`BUILD_CMD`/`TEST_CMD`.
   - `templates/hooks/precommit_gate.sh` → `<repo>/.claude/hooks/` (chmod +x).
   - `templates/CLAUDE.md.tmpl` → `<repo>/CLAUDE.md` (project name substituted).
   - Commit + push these.
5. **Discover Plane state ids:** run the engine's `--print-states` using the entered Plane creds and
   write the full per-project config (including `state_ids`, `repo_dir`, `mcp_config_path`,
   `templates_dir`) to `~/.northstar/projects/<name>.yaml`.
6. **Register** the project in `~/.northstar/registry.yaml`.
7. **Optional `--seed`:** create the three test tickets (HAPPY / VAGUE / QA-CATCH) in Plane's
   "Ready to Dev". (Requires a `create_work_item` call added to the Plane client; if not present,
   print the manual seed instructions instead.)

`northstar project list` prints registered projects + run status; `northstar project remove <name>`
unregisters (and optionally deletes the per-project config).

---

## 6. Run — tmux-backed supervisor

- **`northstar start <project>`** — starts the daemon detached in a tmux session `ns-<project>`,
  with the project's Plane env exported so the session's MCP config resolves:
  ```bash
  tmux new-session -d -s "ns-<project>" -c "<repo_dir>" \
    "env PLANE_API_KEY=… PLANE_WORKSPACE_SLUG=… PLANE_BASE_URL=… \
       python -m orchestrator --config ~/.northstar/projects/<project>.yaml"
  tmux pipe-pane -t "ns-<project>" -o "cat >> ~/.northstar/logs/<project>.log"
  ```
  Refuses to double-start if the session already exists.
- **`northstar stop <project>`** — `tmux kill-session -t ns-<project>`.
- **`northstar restart <project>`** — stop + start.
- **`northstar status`** — table of registered projects × {running?, tmux session, uptime, last log
  line}, by cross-referencing `tmux ls` (`ns-*`) with the registry.
- **`northstar logs <project> [-f]`** — `-f` attaches to the live session (`tmux attach`); without
  it, tails `~/.northstar/logs/<project>.log`.

---

## 7. Success criteria

- **`doctor`** on a fresh machine reports each prerequisite accurately (✅/❌ + fix hint) and exits
  non-zero when a critical tool is missing. Unit-tested with the subprocess layer faked.
- **`init`** is idempotent: after a run, `claude plugin list --json` shows all four plugins; caveman
  and grill-me are either present or clearly flagged; `~/.northstar/` exists. Re-running re-syncs to
  latest without error.
- **`project add`** links an existing repo (and, gated on `gh` reachable, can create a new one),
  installs the guardrails with the project's real build commands, discovers `state_ids`, and writes
  a valid per-project config + registry entry.
- **`start` → `status` → `logs` → `stop`** runs a project's daemon in tmux, survives the terminal
  closing, shows it running, tails its log, and stops it cleanly.
- The existing `orchestrator` engine and its 27 tests remain green (the CLI only wraps it).

---

## 8. Out of scope (this cycle)

- A single daemon watching all projects at once (we run per-project tmux sessions).
- OS-service backend (launchd/systemd) — tmux only for now.
- The Phase-2 engine concurrency work (synchronous dispatch / `poll_once` TOCTOU) — unchanged.
- Webhooks, the planning bridge, deploy/QA-stage expansion — all per the main roadmap.
- A lockfile for reproducible skill versions (we install latest; lockfile is a later option).
- Auto-managing the GitHub *rename* of the repo itself (the user renames the repo to `northstar`).
