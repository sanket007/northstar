# Contributing to northstar

Thanks for your interest in improving northstar! This is an early, experimental project — issues,
ideas, and pull requests are all welcome.

## Getting started

```bash
git clone https://github.com/sanket007/northstar.git
cd northstar
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python3.11 -m pytest -q          # the suite should be green before you start
```

Requires **Python 3.11+** (the system `python3` is often older — use `python3.11` explicitly).

## How the code is organized

| Path | Responsibility |
|------|----------------|
| `northstar/` | the CLI + supervisor: `init`, `doctor`, `project add`, `plan import`, `start/stop/logs` |
| `orchestrator/` | the engine: `poller`, `dispatch`, `worktree`, `launcher`, `plane` client, `health`, `obs` |
| `templates/` | role prompts (`builder`/`reviewer`/`qa`), `CLAUDE.md`, the guardrail hook, formatting configs |
| `tests/` | pytest suite — fakes for subprocess (`runner`) and Plane HTTP (`respx`) |
| `docs/` | setup guide, roadmap, and the design archive under `docs/superpowers/` |

The CLI (`northstar/`) wraps the engine (`orchestrator/`); the engine never imports the CLI layer.

## Conventions

- **Test-driven.** Add a failing test, make it pass, keep the suite green. Every external boundary
  (subprocess, HTTP) goes through an injectable `runner`/client so it can be faked — follow that
  pattern rather than calling `subprocess`/`httpx` directly in new code.
- **Keep role prompts lean.** The `templates/*.md` prompts are tuned for adherence and token cost;
  prefer tightening over adding. Run the prompt-invariant tests (`tests/test_role_docs.py`).
- **No emojis** in anything the system writes — Plane comments, CLI output, or logs. There's a test
  that enforces this for the prompts.
- **Match the surrounding style** — small, focused files; clear names; comments only where the
  "why" isn't obvious.

## Making a change

1. Branch off `main`.
2. Write the test, then the code. Keep commits focused.
3. Run `python3.11 -m pytest -q` — all tests must pass.
4. Open a PR describing **what** changed and **why**, and how you verified it.

## Reporting bugs / requesting features

Open an issue using the templates. For bugs, include the command you ran, what you expected, what
happened, and relevant `northstar logs` output (secrets are redacted automatically, but double-check
before pasting).

## A note on safety

northstar launches Claude Code sessions with `--dangerously-skip-permissions`. When testing changes
to the orchestration loop, use a **throwaway repo and Plane workspace you control** — never point it
at production.
