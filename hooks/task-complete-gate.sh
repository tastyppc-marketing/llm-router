#!/usr/bin/env bash
# task-complete-gate.sh — blocks task completion if tests fail. Exit 2 = block.

set -uo pipefail

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

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "BLOCKED: Cannot complete task — tests are failing." >&2
  echo "$TEST_OUTPUT" | tail -30 >&2
  exit 2
fi

exit 0
