# Sandbox setup (one-time, throwaway)

## 1. GitHub repo
- Create a throwaway repo `your-org/sandbox` with a minimal Node app (an Express server with
  `npm run lint`, `npm run build`, `npm test` wired up) and a `docs/` folder.
- Clone it to `repo_dir` from your config.
- Install the guardrail hooks into it:
  - copy `templates/claude-settings.json` → `<repo>/.claude/settings.json`
  - copy `templates/hooks/precommit_gate.sh` → `<repo>/.claude/hooks/precommit_gate.sh` (chmod +x)
  - copy `templates/CLAUDE.md.tmpl` → `<repo>/CLAUDE.md` (replace `{{PROJECT_NAME}}`)
- Authenticate `gh auth login` so sessions can open/merge PRs.

## 2. Plane project
- Create a project with states named EXACTLY: Draft, Ready to Dev, In Progress, Review, QA,
  Completed, Blocked, Deployed.
- Create a Workspace API token; put it + the workspace slug + project UUID + base URL in
  `config.yaml`.
- Run `python -m orchestrator --config config.yaml --print-states` and paste the printed
  name→id map into `state_ids` in `config.yaml`.

## 3. Install the skill stack at user scope
Ensure these are installed so every headless session inherits them: superpowers, frontend-design,
playwright, `karpathy-guidelines`, and mattpocock's `caveman` / `grill-me`.

## 4. Seed tasks (create in Plane, in "Ready to Dev")
- **HAPPY:** "Add a `GET /health` endpoint that returns HTTP 200 with body `{"status":"ok"}`.
  Acceptance: hitting `/health` returns 200 and the JSON body; a test covers it."
- **VAGUE (negative):** "Make the app better." (no acceptance criteria — must land in Blocked.)
- **QA-CATCH:** "Add `GET /health` returning 200." but with acceptance criteria demanding the body
  be exactly `{"status":"ok"}`. (Used to verify QA catches a body mismatch the unit test missed.)
