#!/usr/bin/env bash
# Claude Code PreToolUse hook. Reads hook JSON on stdin.
# Blocks (exit 2) a `git commit` unless lint+build+test pass and a docs/ file is staged.
set -uo pipefail

payload="$(cat)"
cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')"

case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

LINT_CMD="${LINT_CMD:-npm run lint}"
BUILD_CMD="${BUILD_CMD:-npm run build}"
TEST_CMD="${TEST_CMD:-npm test}"

fail() { echo "BLOCKED: $1" >&2; exit 2; }

eval "$LINT_CMD"  || fail "lint failed"
eval "$BUILD_CMD" || fail "build failed"
eval "$TEST_CMD"  || fail "tests failed"

if [ "${SKIP_MEMORY_CHECK:-0}" != "1" ]; then
  staged="$(git diff --cached --name-only 2>/dev/null || true)"
  if ! printf '%s\n' "$staged" | grep -q '^docs/'; then
    fail "no docs/ memory update staged with this commit"
  fi
fi
exit 0
