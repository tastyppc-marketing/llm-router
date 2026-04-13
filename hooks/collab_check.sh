#!/usr/bin/env bash
set -euo pipefail

# PostToolUse hook: checks for new collaboration messages after each tool call.
# Requires COLLAB_SESSION_NAME to be set in the environment.
# Exits 0 always (advisory only — never blocks tool execution).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COLLAB_CLI="$SCRIPT_DIR/../tools/collab.sh"

SESSION_NAME="${COLLAB_SESSION_NAME:-}"
if [[ -z "$SESSION_NAME" ]]; then
    exit 0
fi

# Debounce: skip if last check was < 2 seconds ago
DEBOUNCE_FILE="/tmp/.collab-check-${SESSION_NAME}"
if [[ -f "$DEBOUNCE_FILE" ]]; then
    last_check=$(stat -c %Y "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    if (( now - last_check < 2 )); then
        exit 0
    fi
fi
touch "$DEBOUNCE_FILE"

# Check for new messages
output=$("$COLLAB_CLI" check --name "$SESSION_NAME" --format inject 2>/dev/null || true)

if [[ -n "$output" ]]; then
    echo "$output"
fi

exit 0
