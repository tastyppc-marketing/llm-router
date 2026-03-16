#!/usr/bin/env bash
# protect-files.sh — PreToolUse hook (global)
# Blocks Edit/Write to sensitive paths. Exit 2 = block.

set -euo pipefail

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

PROTECTED_PATTERNS=(
  ".env"
  ".git/"
  "secrets"
  "credentials"
  "id_rsa"
  "id_ed25519"
  "config.toml"
  ".pem"
  ".key"
  ".secret"
)

LOWER_PATH=$(echo "$FILE_PATH" | tr '[:upper:]' '[:lower:]')

for pattern in "${PROTECTED_PATTERNS[@]}"; do
  if [[ "$LOWER_PATH" == *"$pattern"* ]]; then
    echo "BLOCKED: Editing '$FILE_PATH' is not allowed — matches protected pattern '$pattern'" >&2
    exit 2
  fi
done

exit 0
