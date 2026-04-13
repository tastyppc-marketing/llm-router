#!/usr/bin/env bash
set -euo pipefail

# PreToolUse hook: checks file locks before Edit/Write operations.
# Requires COLLAB_SESSION_NAME to be set in the environment.
# Reads the file path from the tool input.
# Exits 0 always (advisory only — warns but never blocks).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COLLAB_CLI="$SCRIPT_DIR/../tools/collab.sh"

SESSION_NAME="${COLLAB_SESSION_NAME:-}"
if [[ -z "$SESSION_NAME" ]]; then
    exit 0
fi

# The file_path comes from the tool input via CLAUDE_TOOL_INPUT
FILE_PATH=""
if [[ -n "${CLAUDE_TOOL_INPUT:-}" ]]; then
    FILE_PATH=$(echo "$CLAUDE_TOOL_INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('file_path', ''))
except:
    pass
" 2>/dev/null || true)
fi

if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

# Check lock status
output=$("$COLLAB_CLI" lock-check "$FILE_PATH" --name "$SESSION_NAME" 2>/dev/null || true)

if [[ -n "$output" ]]; then
    echo "$output"
fi

exit 0
