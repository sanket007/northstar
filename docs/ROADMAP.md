# Agentic Dev Framework — Roadmap (beyond the MVP slice)

**Date:** 2026-06-18
**Status:** Living document — the north star and deferred scope
**Companion:** [`docs/superpowers/specs/2026-06-18-orchestrator-mvp-slice-design.md`](superpowers/specs/2026-06-18-orchestrator-mvp-slice-design.md) (the first cycle)

---

## Vision

An automated agentic development platform that **simulates a team of talented engineers**:
it picks tasks from a self-hosted Plane.so board and autonomously builds, tests, reviews,
merges, and deploys features — so multiple projects can progress while the user sleeps.
The user stays in the loop only at the front (idea → validated plan); everything downstream
runs autonomously behind strong guardrails, using **real Claude Code CLI sessions** (subscription
auth, no Agent SDK) directed by a plain daemon.

**The full board the platform drives:**
`Draft → Ready to Dev → In Progress → Review → QA → Completed → Deployed`, plus `Blocked`.
The MVP slice exercises `Ready to Dev → In Progress → (Blocked | Review) → QA → Completed`
(review, independent QA, and auto-merge included).

---

## Where the MVP slice stops

The first cycle proves the riskiest integration on one task, concurrency = 1, on a throwaway
sandbox: Plane pickup → worktree build with guardrail hooks → PR → review → auto-merge, with a
full comment trail and stateless-session context hydration. It deliberately leaves out
parallelism, webhooks, the planning bridge, deploy, conflict resolution, multi-project, and
auto-scaffolding. (Review, independent QA, and auto-merge **are** in the slice.) This roadmap is
everything after that.

---

## Phases (recommended build order)

Each phase is its own spec → plan → implementation cycle. Order is a recommendation, not a
contract — we can resequence as we learn.

### Phase 1 — Foundations & conventions
Harden the contracts everything else depends on, promoted from sandbox hacks to real specs.
- Full **8-column state machine** incl. `Draft`, `QA`, `Deployed` — every legal transition, who
  triggers it, what comment it writes.
- **Comment-trail protocol** spec — the machine-tagged format, append-only/self-contained rule,
  and the context-hydration contract as a reusable standard.
- **Memory-layer convention** — structure of the `docs/` markdown memory, citation format, what
  each session must write before committing.
- **Per-language lint/format/build configs** — a library of ready rule files (TS, Python, Go, …)
  dropped into each project.
- **Guardrail hook library** — reusable `PreToolUse` hooks (lint+build+test gate, memory-update
  gate, destructive-command deny rules).

### Phase 2 — Orchestrator core (scale the daemon)
Turn the single-slot poller into a real scheduler.
- **Webhooks replace polling** — Plane webhook ingestion (HMAC-verified) for event-driven pickup,
  reachable on the VPC; polling kept as a fallback.
- **≤5 concurrency scheduler** across worktrees — the semaphore opens from 1 to 5.
- **Worktree lifecycle at scale** — creation, per-worktree dep install, port allocation, cleanup,
  and orphan recovery.
- **Crash/restart recovery** — rebuild the ownership set from Plane state on daemon restart.
- **Observability** — structured logs, per-task status, a lightweight dashboard.

### Phase 3 — Builder agent hardening
Make the autonomous builder production-grade.
- Full **clarify-or-block loop** with rich question quality and dependency-aware readiness checks.
- **Unit + integration tests** required, not just unit.
- **Memory-citation hook** — block commits whose memory entry lacks citations.
- **Context optimization** — fresh session per independent task, mid-task context cleaning when
  no longer needed, token-budget awareness (and opt-in `caveman` compression for internal notes).
- **Per-project CLAUDE.md auto-scaffolding generator** — new project → comprehensive `CLAUDE.md`,
  lint configs, hooks, and `docs/` memory skeleton generated automatically.

### Phase 4 — Review/QA hardening, conflict resolution & deploy
The MVP already does review, independent QA, and auto-merge on a clean path; this phase makes
them production-grade and closes the loop through Deployed.
- **Extensive code-review agent** — deeper review dimensions, severity routing, sensitive-area
  gating (security/architecture/migrations require human sign-off).
- **QA hardening** — richer acceptance suites beyond the MVP smoke check (broader Playwright/e2e,
  perf/regression gates).
- **Intelligent conflict resolution** before merge (rebase/resolve, re-run gates, abort to human
  on ambiguity) — the MVP assumes clean merges.
- **Deploy stage automation** — the `Deployed` column triggers CI/CD deploy with health checks
  and rollback; human approval gate configurable per project.

### Phase 5 — Planning bridge (front of the funnel)
Automate idea → tasks-in-Plane, where the user hands off.
- `idea.md` → `superpowers:brainstorming` + `grill-me`/`grill-with-docs` → specs → plans
  (user in the loop through here — the existing flow).
- Framework **ingests the plans**, lints each task, **asks clarifying questions** before creating,
  then **creates Plane tasks in `Draft`** with: accurate descriptions, **citation links**,
  acceptance criteria, and a **dependency graph** (via `create_work_item_relation`) capturing what
  must clear before a task is workable.

### Phase 6 — Multi-project & "team while you sleep"
The end state.
- **Multiple projects in parallel**, each with its own board, repo, and conventions.
- Cross-project scheduling and resource/seat management.
- Steady-state autonomy: drop validated plans in, wake up to merged/deployed features.

---

## Cross-cutting tracks (run alongside every phase)

- **Safety & guardrails** — the hard commit gate, deny rules, sensitive-area human gates, and
  runaway-loop protection (turn/time/token caps) to avoid Ralph-style cost blowups and goal drift.
- **Memory & context** — the `docs/` memory layer and context-hydration discipline are load-bearing
  for stateless sessions; they grow with every phase.
- **Cost & auth** — running up to 5 concurrent Claude Code sessions on subscription auth needs
  entitlement/seat verification and per-task cost tracking. **Open risk to resolve early in Phase 2.**
- **Observability & escalation** — clear human-escalation paths when an agent is stuck, and audit
  trails for everything the platform did autonomously.

---

## Open questions & risks to resolve

- **Concurrency vs. subscription limits** — can N parallel `claude` sessions run under one
  subscription, and what are the rate/seat limits? (Gates Phase 2.)
- **Webhook reachability** — exposing a Plane webhook receiver on the VPC securely.
- **Merge-conflict safety** — how aggressive should autonomous conflict resolution be before it
  must defer to a human?
- **Deploy safety** — rollback strategy, environment gating, and what never deploys without a human.
- **Cost control** — budgets/caps per task and per project; alerting on runaway loops.
- **Plane API maturity** — relation/dependency endpoints lag the cloud docs on self-hosted; verify
  against the deployed version (Phase 5). Also: the MVP's Plane client uses documented-but-unverified
  JSON key names (`state`, `comment_html`, `description_html`, `next_cursor`, `sequence_id`) — smoke-test
  reads with `--print-states` and confirm the write shape against the live instance before relying on it.
- **Synchronous dispatch (Phase 2 blocker)** — the MVP runs each session on the polling thread, so it
  is safe only at concurrency=1. Raising the cap to 5 requires making dispatch non-blocking (thread
  pool/executor) AND fixing the `poll_once` check-then-act TOCTOU; the `Ownership` set generalizes but
  the dispatch path does not. (Final review I2.)
- **Soft guardrail wall** — `bypassPermissions` inside a git worktree is NOT a real sandbox (shared
  `.git`), and the `claude-settings.json` deny rules are bypassable (`--force-with-lease`, glob gaps,
  env indirection). Acceptable only for the disposable sandbox; real targets need container-level
  isolation. (Final review I3.)
