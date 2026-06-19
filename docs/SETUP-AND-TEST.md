# northstar — Setup & First Test (new user guide)

This walks you from a bare machine to watching northstar take a task from a Plane board to a merged
GitHub PR — including a second task that **waits for its dependency**. Budget ~30–45 min.

> **Honest heads-up:** this is the first real end-to-end run of the system. The code is well unit-tested,
> but the live integration (Plane API + headless/interactive Claude Code + git/GitHub + the MCP) has not
> been exercised before. Expect to hit one or two real-world snags — that's the point of this shakedown.
> Keep the logs; if something breaks, the Troubleshooting section at the bottom is the first stop.

---

## 0. What you're setting up

- **northstar** runs on your machine/VPC. It watches a Plane board and, for each task, launches a real
  **Claude Code** session (your subscription — no API key) in an isolated git worktree to build → review
  → QA → merge, leaving a comment trail.
- You stay in the loop at the **front** (idea → plan → import → decide what's ready) and **deploy** is
  manual. The autonomous middle is northstar's job.

---

## 1. Prerequisites

### Tools (on the machine running northstar)
```bash
# macOS (Homebrew)
brew install python@3.11 git gh node
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv / uvx (runs the Plane MCP server)

# Linux (apt) — equivalents
# sudo apt install -y python3.11 python3.11-venv git nodejs npm
# gh: https://github.com/cli/cli#installation ; uv: the curl line above
```
- **tmux** is **optional** — for the live-attach process backend. If you skip it, `northstar init` will
  offer the built-in **detached** backend (no extra dependency; logs via file). You choose at init.
Install **Claude Code** (the `claude` CLI) per https://claude.com/code and log in with your subscription.

### Accounts / services
- **GitHub account**, authenticated:
  ```bash
  gh auth login          # choose GitHub.com, grant 'repo' scope
  gh auth status         # must say "Logged in"
  ```
- **Claude Code** working: `claude --version` and you've signed in.
- **A self-hosted Plane instance** you can reach, plus:
  - your **base URL** (e.g. `https://plane.yourco.com`),
  - a **workspace API token**: Plane → Workspace Settings → API Tokens → create one,
  - your **workspace slug** (the `…/<slug>/…` part of your Plane URL).

You do **not** need to pre-create a Plane project — northstar makes it for you.

---

## 2. Install northstar

```bash
git clone <your northstar repo url> northstar && cd northstar
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
# optional: put it on PATH for this shell
export PATH="$PWD/.venv/bin:$PATH"
northstar --help        # or: .venv/bin/northstar --help
```
> Use the **editable** install (`-e`). The CLI finds its bundled templates/MCP config relative to the
> source tree; a non-editable install can't yet (tracked follow-up). To run from elsewhere, set
> `NORTHSTAR_ASSETS_DIR` to this repo's path.

---

## 3. Machine setup

```bash
northstar doctor          # checks python3.11/git/gh+auth/claude/uvx/tmux/npx — fix any ✗ before continuing
northstar init            # installs your skill stack to latest + creates ~/.northstar
```
`init` installs superpowers, frontend-design, playwright, karpathy-guidelines via `claude plugin`, and
runs the caveman/grill-me installers. If grill-me's installer needs interaction, follow its prompt (or
run `npx --yes skills@latest add mattpocock/skills` once yourself); `doctor` will confirm.

`init` will ask about the process backend if tmux isn't installed (or pass
`northstar init --backend tmux|detached`).

---

## 4. Create the sandbox GitHub repo

We'll use a throwaway repo with **permissive** build commands so the commit guardrail never blocks while
you prove the loop. (Tighten them to real lint/build/test once it works.)

```bash
gh repo create northstar-sandbox --private --clone
cd northstar-sandbox

cat > package.json <<'JSON'
{
  "name": "northstar-sandbox",
  "version": "0.0.0",
  "scripts": {
    "lint": "echo lint-ok",
    "build": "echo build-ok",
    "test": "echo test-ok"
  }
}
JSON

mkdir -p docs && touch docs/.gitkeep
git add -A && git commit -m "chore: sandbox scaffold" && git push
cd ..
```
> The permissive `echo …` scripts mean the pre-commit guardrail (lint+build+test) always passes for the
> smoke test. The builder still writes real code/tests; QA still verifies behavior independently. Swap in
> real commands later.

---

## 5. Add the project to northstar

```bash
northstar project add
```
Answer the prompts (example values):
- **name:** `sandbox`
- **plane base url:** `https://plane.yourco.com`
- **plane api key:** `<your token>`
- **plane workspace slug:** `<your slug>`
- **Create a NEW Plane project? [y]:** `y`  → **name:** `Northstar Sandbox`, **identifier:** `NSBX`
- **GitHub repo (owner/name):** `<youruser>/northstar-sandbox`
- **Local path for the repo:** the absolute path where you cloned it in step 4
- **lint/build/test:** `npm run lint`, `npm run build`, `npm test`

This creates the Plane project, reconciles its board to the 8 columns (Draft → … → Deployed), installs
the guardrail hook + `CLAUDE.md` into the repo, discovers the state IDs, and registers the project.
Verify: open the project in Plane and confirm the 8 columns exist.

```bash
northstar project list        # should show 'sandbox'
```

---

## 6. Write a tiny plan (with a dependency)

Create `firsttest-plan.md` anywhere:

```markdown
# Plan: greeting file

## Task 1: Create the greeting file
Create a file `greeting.txt` at the repo root whose contents are exactly `hello world`.
Acceptance criteria: `greeting.txt` exists and its first line is exactly `hello world`. Add a test that
asserts the file's content.
Citations: this plan, Task 1.

## Task 2: Append a goodbye line
Append a second line `goodbye` to `greeting.txt`.
Acceptance criteria: `greeting.txt`'s second line is exactly `goodbye`.
Interfaces: Consumes `greeting.txt` produced by Task 1 (so Task 2 depends on / is blocked_by Task 1).
Citations: this plan, Task 2.
```

This is deliberately trivial (no web framework) so the first run exercises the **whole loop + the
dependency gate** without extra moving parts.

---

## 7. Import the plan → Plane Draft tasks

```bash
northstar plan import sandbox firsttest-plan.md
```
This opens an **interactive** Claude session that will **grill you about the plan** (it may ask to
confirm acceptance criteria and the Task-2-depends-on-Task-1 edge). Answer its questions. When it's
satisfied it creates two **Draft** tasks in Plane with the dependency relation, then summarizes.

Verify in Plane: two tasks in **Draft**; Task 2 shows a `blocked_by` relation to Task 1.

---

## 8. Decide what's ready → move to Ready to Dev

On the Plane board, drag **both** tasks from **Draft** to **Ready to Dev**.
(Moving both is intentional — it lets you watch the scheduler hold Task 2 until Task 1 finishes.)

---

## 9. Run it

```bash
northstar start sandbox        # launches the daemon (tmux session ns-sandbox, or a detached process)
northstar status               # shows sandbox as running
northstar logs sandbox -f      # tmux: attaches live (Ctrl-b then d to detach); detached: tails the log
```
Now watch **the Plane board** and **GitHub**.

> **Activity logging.** Every command northstar runs and every external call it makes
> (Plane, GitHub, git, claude) is printed as a readable line, e.g.
> `12:30:01 northstar › plane ✓ POST /…/states/ → 201 (0.34s)` or
> `12:30:05 northstar › exec $ claude plugin install … → ✓ ok (4.1s)`.
> These lines go to **stderr** (and into `northstar logs` for the daemon). Secrets
> (API keys, tokens) are redacted automatically. To quiet them set `NORTHSTAR_QUIET=1`;
> for extra detail (full URLs) set `NORTHSTAR_DEBUG=1`.

---

## 10. What success looks like

**Task 1 (no blockers)** moves, hands-off:
`Ready to Dev → In Progress → Review → QA → Completed`, with:
- a comment trail at each transition (`🤖 [builder] …`, `🤖 [reviewer] …`, `🤖 [qa] …`),
- a **merged** GitHub PR adding `greeting.txt`,
- the merge happening **after** the QA comment (QA independently checks the file content).

**Task 2 (blocked_by Task 1)** stays put in Ready to Dev — the scheduler **skips it every poll** while
Task 1 is unfinished. Once Task 1 reaches **Completed**, the next poll picks up Task 2 and runs it to
Completed. That wait is the dependency gate working.

**Negative check (optional):** add a third task in Plane by hand (directly on the board — no import) with
a vague description like "make it better" and move it to Ready to Dev. The builder should move it to
**Blocked** with specific questions instead of guessing. (This also confirms hand-created tasks are
first-class.)

---

## 11. Stop & clean up

```bash
northstar stop sandbox
# remove the project registration (keeps the repo + Plane project):
northstar project remove sandbox
# throwaway cleanup:
gh repo delete <youruser>/northstar-sandbox --yes   # and delete the Plane project in the UI
```

---

## 12. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `doctor` shows ✗ for a tool | Install it (section 1). `github-auth` ✗ → `gh auth login`. |
| `project add` errors with a Plane message (e.g. 401) | Wrong API key / base URL / slug. You'll get a friendly "Plane API returned 401 at …" — fix the value and re-run. If it half-created the Plane project, re-run with `--existing-plane-project <id>` or delete the project and retry. |
| `plan import` session can't reach Plane (MCP errors) | Confirm `~/.northstar/plane-mcp.json` exists (created by `init`) and the project's `~/.northstar/projects/sandbox.yaml` has the right `plane_*` values. `uvx` must be installed. |
| `start` then `status` shows stopped | `northstar logs sandbox` (tail the file). Common: `claude` not on PATH in the tmux env, or the repo_dir path is wrong. The daemon uses the same interpreter that owns the package. |
| Builder keeps moving tasks to **Blocked** | The task's acceptance criteria are ambiguous — answer its questions in the Plane comment and move the task back to Ready to Dev. Crisper plans (more grilling at import) reduce this. |
| Commit guardrail blocks the builder | Your lint/build/test commands fail or a `docs/` update wasn't staged. For the smoke test keep them permissive (`echo …`); the builder writes a `docs/` note before committing. |
| Task 2 **never** starts even after Task 1 completes | Check Task 1 actually reached **Completed/Deployed** (only those clear a blocker). If you accidentally created a dependency cycle, neither task will ever start — fix the relations in Plane. |
| A session hangs | There's a per-session turn/time cap; it will fail out and the daemon comments the failure on the ticket and moves it to **Blocked** rather than hanging the daemon. |

---

## 13. Known limitations (today)

- **Unproven live until you run it** — this guide is the first real test.
- **Concurrency = 1** — one task at a time per project (parallelism is a later phase).
- **Deploy is manual** — the loop ends at merge (`Completed`); the `Deployed` column isn't automated yet.
- **Editable install required** (asset packaging is a tracked follow-up).
- **No cycle detection** in the scheduler — a dependency cycle silently parks tasks; confirm the graph at
  import time.

When something breaks, grab: the `northstar logs <project>` output, the Plane ticket's comment trail, and
any error from `project add`/`plan import` — that's enough to debug the first run.
