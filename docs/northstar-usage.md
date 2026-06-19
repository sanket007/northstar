# northstar — usage

## Install
```bash
pipx install -e .        # or: pip install -e ".[dev]"
```
> Use an **editable** install (`-e`). The CLI resolves its bundled `templates/` and
> `plane-mcp.json` relative to the source tree; a non-editable install can't find them yet
> (packaging them as `package-data` is a tracked follow-up). To run from elsewhere, set
> `NORTHSTAR_ASSETS_DIR` to the repo root.

## Machine setup
```bash
northstar doctor            # check prerequisites
northstar init             # install skills to latest + create ~/.northstar
```
Prerequisites: Python 3.11+, git, GitHub CLI (`gh auth login`), Claude Code (`claude`, logged in),
`uv`/`uvx`, and Node/`npx` (for the grill-me skill); **tmux is optional** (only for the live-attach
backend — see below). `doctor` reports each.

northstar picks a **process backend** at init: `tmux` (live-attach, needs tmux) or `detached`
(no dependency, logs via file). Override with `northstar init --backend tmux|detached`. Both survive the
terminal closing; neither survives a reboot.

## Add a project
```bash
northstar project add      # prompts for Plane details, repo URL, build commands
#   links the repo if it exists; with --create it creates one (gh must be authed)
northstar project list
```

`project add` now sets up Plane for you: choose **new** (it creates the project and the 8-state
board) or **existing** (it reconciles the board to the 8 states on a project id you provide).

> Note: on the **new** path, `project add` creates the Plane project before wiring GitHub. If a
> later step fails, the Plane project already exists — re-run with `--existing-plane-project <id>`
> (its identifier is now taken), or delete the half-created project first.

## Import a plan (create the tasks)
```bash
northstar plan import <project> path/to/plan.md
```
Launches an interactive session that grills you over the whole plan, then creates Plane **Draft** tasks
with acceptance criteria, citations, and dependency links. Run it again for each new plan as the project
grows (idempotent — it won't duplicate tasks). Then move the ready tasks Draft → Ready to Dev.

## Run
```bash
northstar start <project>      # runs the daemon (tmux session ns-<project>, or a detached process)
northstar status               # which projects are running
northstar logs <project> -f    # tmux backend: attaches live (Ctrl-b d to detach); detached: tails the log
northstar stop <project>
```
