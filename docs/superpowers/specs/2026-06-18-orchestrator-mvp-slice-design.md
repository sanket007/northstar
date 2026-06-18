# Orchestrator MVP Vertical Slice — Design Spec

**Date:** 2026-06-18
**Status:** Approved (design); pending implementation plan
**Scope:** First spec → plan → implementation cycle of the Agentic Dev Framework

---

## 1. Context & goal

We are building an **automated agentic development framework**: a daemon that autonomously
picks tasks from a self-hosted **Plane.so** kanban board and drives them through
build → test → review → merge using **real Claude Code CLI sessions**, simulating a team of
talented engineers working while the user sleeps.

The full platform decomposes into five subsystems, each getting its own spec → plan →
implementation cycle:

1. Foundations & conventions
2. Planning bridge (superpowers plan → Plane tasks with citations + dependency graph)
3. Orchestrator core
4. Builder agent
5. Reviewer / merge / deploy agent

**This spec covers only the first cycle: a thin vertical slice (MVP)** that proves the riskiest
integration end to end before we invest in parallelism, the planning bridge, or deploy.

### Why a thin slice first

The slice exercises the three riskiest integration seams in one pass:

- **Plane ↔ daemon** — REST polling + state transitions + comments
- **daemon ↔ Claude Code** — headless session launch + monitoring
- **Claude Code ↔ git/GitHub** — worktree → PR → merge

The slice runs the loop through **QA** (an independent acceptance check) and merge, but
everything else (parallelism, webhooks, planning bridge, deploy, conflict resolution,
multi-project, auto-scaffolding) is deliberately deferred to later cycles.

### Foundational decisions (from brainstorming)

- **Build our own**, do not adopt an existing system. Closest reference is
  `claude-plan-orchestrator` (Hochfrequenz, MIT) — we borrow its design but keep Plane.
  We also borrow Ralph's inner-loop pattern (fresh context per task, git/files as memory)
  and the official `plane-mcp-server` (makeplane, MIT) for Plane actions.
- **Code host: GitHub.com** — PRs raised / reviewed / merged there. Plane only tracks tasks.
- **No Claude Agent SDK / no per-token API billing.** The orchestrator is a plain daemon that
  **launches real Claude Code CLI sessions** (`claude -p`, headless) using the user's existing
  Claude Code **subscription auth**, directing them autonomously as a human would. The daemon
  imports nothing from Anthropic; it only shells out to the `claude` binary.
- **Test target: a throwaway sandbox** — a disposable GitHub repo + a Plane test project with
  the 8 columns and a seed task. No real work at risk on the first end-to-end run.

---

## 2. Architecture & end-to-end flow

Two processes, cleanly split.

### A. The orchestrator daemon (Python, on the user's computer/VPC)

- Imports nothing from Anthropic — process orchestration + Plane REST + git only.
- **Polls** the Plane REST API every ~30s for **un-owned, actionable** work items. Polling
  (not webhooks) for the slice: no public endpoint needed, and the Plane API's 60 req/min limit
  is a non-issue at this rate for one project. Webhooks belong to the later orchestrator-core
  subsystem. Actionable states and the session each triggers:
  - **Ready to Dev** or **In Progress** → **builder session**. (In Progress is the rework state
    the reviewer or QA sets when requesting changes; the builder always reads the latest comment
    to know whether it's a fresh start or a rework, so a single pickup path covers both.)
  - **Review** → **reviewer session**.
  - **QA** → **QA session**.
- **Ownership set** — the daemon keeps an in-memory set of ticket IDs that currently have a live
  session, and **skips any ticket it already owns** so a ticket is never picked up twice between
  a poll and the session's first state transition. A ticket is released from the set when its
  session exits (whether it moved the ticket to Blocked, Review, QA, In Progress, or Completed).
- Holds a **concurrency semaphore = 1** for the slice (the dial we open to 5 later).
- For a picked task: adds it to the ownership set, creates an isolated **git worktree**, then
  **launches a real Claude Code session** pointed at that worktree and watches its `stream-json`
  output. The daemon is a director, not a coder — all real work happens inside the Claude Code
  session.

### B. The Claude Code sessions (real CLI, subscription auth)

Three roles, each a fresh isolated session:

1. **Builder session** — launched with the Plane MCP server attached (`--mcp-config`) so it can
   read the ticket + comment trail and write comments/state, plus `gh` via Bash for git/PR.
   Workflow (see §4): read ticket + latest comment → clarify-or-block gate → if clear, move
   ticket to **In Progress** + comment (the visible claim; the daemon's ownership set is the
   race guard) → TDD build + tests → commit (gated by guardrail hooks) → push → `gh pr` → move
   ticket to **Review** + comment with the PR link. (On a rework pickup the ticket is already in
   In Progress; the builder skips the claim transition and addresses the latest review comment.)

2. **Reviewer session** — triggered when a task hits **Review**. Reviews the PR extensively and
   posts findings. If changes needed → comment a short summary on the ticket (detailed findings
   on the PR) + move ticket back to **In Progress** (a builder session picks it up, reads the
   latest comment, addresses it). If approved → comment + move ticket to **QA**. The reviewer
   does **not** merge — merge is gated behind QA.

3. **QA session** — triggered when a task hits **QA**. This is **independent, black-box
   acceptance verification by a session that did not write the code**: it checks out the PR
   branch in a worktree, builds/runs the actual app, and verifies it against the ticket's
   **acceptance criteria** from the outside (e.g. boot the service and assert `/health` returns
   200; Playwright for UI tasks) — distinct from, and a check on, the builder's own tests. If QA
   fails → comment the failures + move ticket back to **In Progress** (rework loop). If QA passes
   → **merge the PR** (`gh pr merge`) → move ticket to **Completed**, clean up the worktree.

### Slice state machine

```
Ready to Dev → In Progress → (Blocked | Review) → (In Progress ⟲ | QA) → (In Progress ⟲ | Completed)
```

Every transition writes a Plane comment, so any session reconstructs context from the trail.

---

## 3. Context hydration (no context loss)

Because every pickup is a **fresh, stateless** Claude Code session, the durable record must live
entirely *outside* the session, and **every session rehydrates the full context before taking any
action**. The latest comment tells the session *what to do next*; the full history tells it
*everything it needs to do it well*. We never act on the latest comment alone.

**Single source of truth = four durable stores, all read at session start:**

1. **The Plane ticket** — description, acceptance criteria, labels, current state, and the
   **entire comment trail** (paginate to the end; never just the most recent N).
2. **The GitHub PR thread** — the *detailed* line-level review feedback lives here, not on Plane
   (Plane only carries the short summary). A rework builder **must** pull the PR review comments,
   not just the Plane summary comment, or it loses the actual feedback.
3. **The repo `docs/` memory layer** — prior worklog/decision entries with citations (see §5).
4. **Git history** — commits and branch state for the ticket's worktree.

**Enforcement:** the `builder.md`/`reviewer.md` role docs make full hydration step 1 of every run
(fetch ticket + all comments via Plane MCP, fetch PR thread via `gh`, read `docs/`), and the
session must summarize what it learned from the trail in its first new comment so the trail stays
self-describing. Comments are **append-only and self-contained** — each one carries enough context
(links, PR refs, citations, decisions) that the trail can be reconstructed end to end without
external memory. This is what makes the rework loop safe across stateless sessions.

## 4. The clarify-or-block gate & comment-trail protocol

This is the core guardrail: **nothing starts until the ticket is unambiguous.**

Every agent comment is machine-tagged so the trail is parseable and the **latest comment is
always the source of truth for what to do next**:

```
🤖 [builder] READY-TO-DEV → BLOCKED
Questions before I can start:
1. <specific question>
2. <specific blocker / unmet dependency>
```

- **Builder, on pickup**, reads the ticket description, acceptance criteria, and the *entire*
  comment trail. It judges: is everything needed present and unambiguous, are dependencies
  cleared, are acceptance criteria testable?
- If **not** → posts one structured questions-comment → moves ticket to **Blocked** → exits,
  freeing the slot. The user answers in a comment and moves it back to Ready to Dev; on
  re-pickup the builder reads the latest comment and re-judges.
- Only when fully clear does the builder write code.

The clarify-or-block gate is implemented as an **automated `grill-me`**: the builder self-grills
the ticket against the codebase, and the questions it cannot resolve become the Blocked comment.

---

## 5. Guardrail hooks & the memory layer

- **Hard commit gate** — the target repo's `.claude/settings.json` has a `PreToolUse` hook
  matching `git commit` that runs `lint && build && test` and **denies** (exit 2) on any
  failure. Plus `deny` rules on destructive commands as a circuit breaker. The daemon never
  trusts the session; the hook is the wall.
- **Memory layer** — a `docs/` folder of mid-size markdown files + an auto-loaded `CLAUDE.md`.
  The builder appends a short worklog/decision entry **with citations** before committing; a
  lightweight hook checks a `docs/` file was touched when source changed. Fresh session per
  task (the context-optimization goal) means this on-disk memory *is* how knowledge carries
  between sessions — the Ralph "git/files as memory" pattern.

---

## 6. Session launch & monitoring

The daemon launches (exact flags finalized at plan time):

```
claude -p "<small task prompt>" \
  --output-format stream-json \
  --permission-mode bypassPermissions \
  --mcp-config plane-mcp.json
# cwd = the worktree
```

`bypassPermissions` is safe *because* the session runs inside an isolated worktree. The prompt is
tiny — it names the ticket and says "read it via Plane MCP, follow your role instructions"; the
heavy workflow lives in the repo's `CLAUDE.md` + a `builder.md`/`reviewer.md` role doc.

The daemon parses `stream-json` for completion/errors, enforces a turn/time cap, and on a crash
comments the failure on the ticket and moves it to Blocked rather than hanging.

---

## 7. Skill stack integration

The sessions must work like the user does, with the user's skill stack. Skills installed at
**user scope** are inherited by every headless `claude -p` session on the machine, so the slice's
setup ensures the full stack is installed once (superpowers, frontend-design, `karpathy-guidelines`,
mattpocock's `caveman`/`grill-me`). Because headless mode won't reliably self-select skills, the
`builder.md`/`reviewer.md` role docs and the repo `CLAUDE.md` **explicitly name which skill to
invoke at which step** — deterministic, not hopeful.

Two skill contexts: the **intake phase** (user present) and the **autonomous sessions** (no human
in the room).

### Intake phase (user in the loop — the existing flow)

`superpowers:brainstorming` → `grill-me` / `grill-with-docs` to stress-test →
`superpowers:writing-plans`. This is the front of the pipeline (later "planning bridge"
subsystem), unchanged from how the user works today.

### Autonomous builder session

| Stage | Skill | Why |
|---|---|---|
| Global, always on | `karpathy-guidelines` | Simplicity, surgical changes, surface assumptions — injected via `CLAUDE.md` |
| Clarify-or-block gate | `grill-me` (reframed) | Self-grills the ticket; unanswerable questions become the Blocked comment |
| Build | `superpowers:test-driven-development` | Tests first |
| Build (UI tasks) | `frontend-design` | Invoked conditionally when the task touches frontend |
| When stuck | `superpowers:systematic-debugging` | On any test failure / unexpected behavior |
| Before "done" | `superpowers:verification-before-completion` | Evidence before claiming done |
| Opening PR | `superpowers:requesting-code-review` | Structures the PR + review request |

### Autonomous reviewer session

- `review` skill for the actual code review
- `superpowers:receiving-code-review` semantics when the builder later addresses feedback (the
  In Progress ⟲ loop)
- Hands off to QA on approval (does not merge)

### Autonomous QA session

- `verify` skill for black-box acceptance verification (run the app, observe real behavior)
- `superpowers:verification-before-completion` — evidence against acceptance criteria before pass
- `frontend-design` + the `playwright` plugin for UI tasks (end-to-end checks)
- `superpowers:finishing-a-development-branch` for the merge + cleanup once QA passes

### Cross-cutting

- **`caveman`** — efficiency tool, not on the critical path: compress verbose internal
  worklog/memory notes to cut tokens, **never** human-facing Plane comments (those stay
  readable). Opt-in for the slice; prove value before leaning on it.
- **`using-git-worktrees`** — the daemon's worktree handling follows this skill's pattern, so
  daemon and sessions share one mental model of isolation.

The `grill-me` skill literally becomes the clarify-or-block guardrail — same interrogation,
output redirected from a chat to the ticket. The stack maps onto stages we already designed.

### Plane integration note

The "plane plugin" (`makeplane/plane-claude-plugin`) is **cloud-only and ships no skills yet** —
unusable for self-hosted Plane. We use **`plane-mcp-server`** directly (stdio transport,
`PLANE_BASE_URL` pointing at the self-hosted instance, `PLANE_API_KEY` + `PLANE_WORKSPACE_SLUG`).

---

## 8. What we build (repo layout)

```
agentic-dev-framework/
  orchestrator/        # the Python daemon (no Anthropic imports — shells out to `claude`)
    poller.py          #   Plane polling + state machine
    launcher.py        #   worktree create + claude session launch + monitor
    plane.py           #   Plane REST client (states, comments, transitions)
    config.py
  templates/           # scaffolded into target projects
    CLAUDE.md.tmpl
    claude-settings.json   # the guardrail hooks
    builder.md / reviewer.md / qa.md  # role instructions
  plane-mcp.json       # MCP config attached to every session
  config.example.yaml
  docs/superpowers/specs/
```

Daemon config (`config.yaml` / `.env`): Plane base URL, API key, workspace slug, project id,
state-name → id map, GitHub repo, worktrees root, poll interval, `claude` binary path, model.

---

## 9. Success criteria

- **Walk-away test (happy path):** drop "add a `/health` endpoint returning 200 with a test"
  into Ready to Dev, walk away, return to:
  - the ticket having passed through **Review → QA** (visible in the trail), with the reviewer's
    findings and an **independent QA pass** against the acceptance criteria recorded,
  - a **merged GitHub PR** implementing it with passing tests, merged only **after QA passed**,
  - the **Plane ticket in Completed**,
  - a **full comment trail** documenting each transition.
- **Negative test (guardrail):** a deliberately vague task correctly lands in **Blocked** with
  sensible, specific questions.
- **QA-catch test:** a task whose implementation passes the builder's own tests but **fails the
  acceptance criteria** is caught by the QA session and bounced back to In Progress, not merged.
- Each integration seam (Plane↔daemon, daemon↔Claude Code, Claude Code↔git/GitHub) is validated
  by an integration test or a reproducible manual run.

---

## 10. Out of scope for this slice

Each is a later cycle:

- Parallelism beyond 1 (the semaphore stays at 1)
- Webhooks (we poll)
- The superpowers → Plane planning bridge
- The deploy stage (the `Deployed` column)
- Merge-conflict resolution
- Multi-project support
- The per-project auto-scaffolding generator (we hand-write the sandbox's config this time)
- Context-cleaning/optimization beyond fresh-session-per-task
