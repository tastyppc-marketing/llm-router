#!/usr/bin/env bash
# run-tests-async.sh — PostToolUse async hook (global)
# Runs tests after code file edits. Outputs JSON systemMessage.

set -uo pipefail

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

EXT="${FILE_PATH##*.}"
case "$EXT" in
  js|jsx|ts|tsx|py|rs|go|java|rb|c|cpp|h|hpp|cs|php|swift|kt|scala|sh|vue|svelte)
    ;;
  *)
    exit 0
    ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")

TEST_CMD=""
if [ -f "$REPO_ROOT/package.json" ]; then
  TEST_SCRIPT=$(jq -r '.scripts.test // empty' "$REPO_ROOT/package.json" 2>/dev/null)
  if [ -n "$TEST_SCRIPT" ] && [[ "$TEST_SCRIPT" != *"no test specified"* ]]; then
    TEST_CMD="cd \"$REPO_ROOT\" && npm test -- --passWithNoTests 2>&1"
  fi
elif [ -f "$REPO_ROOT/pyproject.toml" ] || [ -f "$REPO_ROOT/pytest.ini" ] || [ -f "$REPO_ROOT/setup.cfg" ] || [ -d "$REPO_ROOT/tests" ] || [ -d "$REPO_ROOT/test" ]; then
  TEST_CMD="cd \"$REPO_ROOT\" && python -m pytest -q 2>&1"
elif [ -f "$REPO_ROOT/Cargo.toml" ]; then
  TEST_CMD="cd \"$REPO_ROOT\" && cargo test -q 2>&1"
elif [ -f "$REPO_ROOT/Makefile" ] && grep -q "^test:" "$REPO_ROOT/Makefile" 2>/dev/null; then
  TEST_CMD="cd \"$REPO_ROOT\" && make test 2>&1"
elif [ -f "$REPO_ROOT/go.mod" ]; then
  TEST_CMD="cd \"$REPO_ROOT\" && go test ./... 2>&1"
fi

if [ -z "$TEST_CMD" ]; then
  exit 0
fi

TEST_OUTPUT=$(eval "$TEST_CMD" 2>&1)
EXIT_CODE=$?

TRUNCATED=$(echo "$TEST_OUTPUT" | head -c 500)
if [ "$EXIT_CODE" -eq 0 ]; then
  jq -n --arg path "$FILE_PATH" --arg out "$TRUNCATED" \
    '{"systemMessage": ("Tests passed after editing " + $path + ". Output: " + $out)}'
else
  jq -n --arg path "$FILE_PATH" --arg out "$TRUNCATED" \
    '{"systemMessage": ("Tests FAILED after editing " + $path + ". Output: " + $out)}'
fi

exit 0
