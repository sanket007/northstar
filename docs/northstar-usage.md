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
`uv`/`uvx`, tmux, and Node/`npx` (for the grill-me skill). `doctor` reports each.

## Add a project
```bash
northstar project add      # prompts for Plane details, repo URL, build commands
#   links the repo if it exists; with --create it creates one (gh must be authed)
northstar project list
```

`project add` now sets up Plane for you: choose **new** (it creates the project and the 8-state
board) or **existing** (it reconciles the board to the 8 states on a project id you provide).

## Run (tmux, detached)
```bash
northstar start <project>      # runs the daemon in tmux session ns-<project>
northstar status               # which projects are running
northstar logs <project> -f    # attach to the live session (Ctrl-b d to detach)
northstar stop <project>
```
