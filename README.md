# northstar

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

**An autonomous software-development orchestrator.** northstar watches a self-hosted
[Plane](https://plane.so) board and drives each task through the full delivery
lifecycle — **build → review → QA → merge** — by launching real
[Claude Code](https://claude.com/claude-code) sessions, one per role, as if a small team of
engineers were working the board on their own.

> Status: early and experimental. The core loop works end-to-end; it runs one task at a time
> by default and is under active shakedown. Treat it as a power tool, not a turnkey product.

---

## Why

Most "AI dev" tools either wrap an API behind a bespoke agent framework or stop at code
generation. northstar takes a different stance:

- **It drives the real Claude Code CLI**, not the Agent SDK or raw API — so it runs on your
  existing Claude subscription and behaves exactly like a human directing Claude Code.
- **The board is the source of truth.** Tasks, state transitions, and the full decision trail
  live on your Plane board as comments. Every session rehydrates context from the ticket, its
  comment trail, the PR thread, the repo's `docs/` memory, and git history — so nothing is lost
  between stateless sessions.
- **GitHub is the code host.** PRs are opened, reviewed, and merged there; Plane only tracks work.

## How it works

```
        Plane board (Draft → Ready to Dev → In Progress → Review → QA → Completed)
                                   │
                          poll + dependency gate
                                   │
                                   ▼
   ┌──────────────────────────  daemon  ──────────────────────────┐
   │  for each actionable ticket:                                  │
   │    1. fresh git worktree off origin/main                      │
   │    2. launch a Claude Code session for the ticket's role      │
   │         builder → reviewer → qa                               │
   │    3. session reads the ticket, does the work, comments,      │
   │         and moves the ticket to the next state                │
   │    4. guardrail hook gates every commit (lint+build+test+docs)│
   └───────────────────────────────────────────────────────────────┘
                                   │
                       GitHub PR  →  squash-merge by QA
```

Each role is an independent Claude Code session driven by a focused role prompt:

- **Builder** — claims a Ready-to-Dev ticket, builds it test-first in an isolated worktree, opens a PR, hands to Review.
- **Reviewer** — reviews the PR adversarially against acceptance criteria and CI, then routes to QA or back for rework.
- **QA** — independently verifies acceptance criteria, integrates with trunk, and is the **only** role that merges.

Safety rails keep the loop honest: a **rework cap** parks thrashing tickets in `Blocked` for a
human, the orchestrator re-checks **trunk health** after every merge, worktrees branch from fresh
`origin/main`, and a **pre-commit guardrail hook** blocks any commit that fails lint/build/test or
omits a `docs/` memory note.

## Requirements

Install on the machine that will run the orchestrator:

| Tool | Why | Notes |
|------|-----|-------|
| Python 3.11+ | the CLI + daemon | system `python3` may be older — use `python3.11` |
| [Claude Code](https://claude.com/claude-code) (`claude`) | runs the sessions | signed in on your subscription |
| `git` + [`gh`](https://cli.github.com) | code host | `gh auth login` must be authenticated |
| [`uv`](https://docs.astral.sh/uv/) (`uvx`) | runs the Plane MCP server | |
| A self-hosted Plane instance | the board | API token from Profile → Personal Access Tokens |
| `tmux` *(optional)* | live-attach to the daemon | falls back to a detached process if absent |
| Node.js / `npx` *(optional)* | some skills + JS projects | |

## Install

```bash
git clone https://github.com/sanket007/northstar.git
cd northstar
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
northstar --help
```

> Use an **editable** install (`-e`). The CLI resolves its bundled `templates/` and `plane-mcp.json`
> relative to the source tree; to run from elsewhere, set `NORTHSTAR_ASSETS_DIR` to the repo root.

## Quickstart

```bash
# 1. one-time machine setup (prereq checks + install the Claude skill stack)
northstar init

# 2. verify the environment
northstar doctor

# 3. add a project — creates/links the Plane project + 8-column board, clones the repo,
#    installs the guardrail hook, and (optionally) imposes strong formatting/lint rules
northstar project add

# 4. turn an implementation plan into Plane Draft tasks (interactive, dependency-aware)
northstar plan import <project> path/to/plan.md

# 5. move the tasks you want built from Draft → Ready to Dev on the board, then:
northstar start <project>      # launches the daemon
northstar logs <project> -f    # watch each session work in real time
```

A full new-user walkthrough lives in [`docs/SETUP-AND-TEST.md`](docs/SETUP-AND-TEST.md).

## Command reference

| Command | Purpose |
|---------|---------|
| `northstar init` | machine setup: prereq gate, install skill stack, choose process backend |
| `northstar doctor` | check prerequisites and report the active backend |
| `northstar project add` | create/link the Plane project + board, clone repo, install guardrails (+ formatting) |
| `northstar project list` / `remove` | manage registered projects |
| `northstar plan import <project> <plan.md>` | grill a plan and create dependency-linked Draft tasks |
| `northstar start` / `stop` / `restart` `<project>` | control the per-project daemon |
| `northstar status` | show which projects are running |
| `northstar logs <project> [-f]` | view (or live-follow) the daemon + session activity |

## Strong formatting (opt-in)

At `project add`, northstar can impose strict formatting + lint rules based on the repo's language,
installing the tooling and folding a format+lint check into the commit gate so agents can't land
unformatted code:

- **JavaScript / TypeScript** — ESLint (flat config) + Prettier
- **Python** — Ruff (lint + format)
- **Go** — gofumpt + golangci-lint

## Visibility

Everything northstar does to the outside world — every command, every Plane/GitHub call, and each
Claude session's activity (`says:` / `tool:` / `result:`) — is printed as a readable, timestamped
line and captured in `northstar logs`. Secrets are redacted. Quiet it with `NORTHSTAR_QUIET=1`;
add detail with `NORTHSTAR_DEBUG=1`.

## Safety

northstar runs sessions with `--dangerously-skip-permissions` so they can work unattended. The
guardrail pre-commit hook, git-worktree isolation, role-prompt rules, the rework cap, and the
post-merge trunk-health check are the safety net. **Run it against repositories and a Plane
workspace you control, ideally a sandbox first.** You are responsible for what the agents commit
and merge.

## Project layout

```
northstar/       the CLI + supervisor (init, doctor, project add, plan import, start/stop/logs)
orchestrator/    the engine (poller, dispatch, worktree, launcher, Plane client, health checks)
templates/       role prompts (builder/reviewer/qa), CLAUDE.md, guardrail hook, formatting configs
docs/            setup guide, usage, roadmap, design specs
tests/           pytest suite (fakes for subprocess + Plane HTTP)
```

## Development

```bash
pip install -e '.[dev]'
python3.11 -m pytest -q
```

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for what's planned (concurrency > 1, deploy automation,
cycle detection, reboot-persistent service backend, and more).

## License

Released under the [MIT License](LICENSE) © 2026 Sanket Lakhani.
