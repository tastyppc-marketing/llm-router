#!/usr/bin/env bash
# claude_worker.sh — Global Claude CLI wrapper
# Usage: claude_worker.sh [-m model] [-C dir] [--output-last-message] "<prompt>"

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/claude_runs"
mkdir -p "$RUNS_DIR"

MODEL=""
WORKDIR=""
SAVE_LAST_MESSAGE=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m)
      MODEL="$2"
      shift 2
      ;;
    -C)
      WORKDIR="$2"
      shift 2
      ;;
    --output-last-message)
      SAVE_LAST_MESSAGE="yes"
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [ ${#EXTRA_ARGS[@]} -lt 1 ]; then
  echo "Usage: claude_worker.sh [-m model] [-C dir] [--output-last-message] \"<prompt>\"" >&2
  exit 1
fi

TASK_PROMPT="${EXTRA_ARGS[*]}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_STDOUT="$RUNS_DIR/claude_stdout.$RUN_ID.json"
RUN_STDERR="$RUNS_DIR/claude_stderr.$RUN_ID.txt"
RUN_LAST="$RUNS_DIR/claude_last_message.$RUN_ID.md"

echo "=== Claude Worker Started ===" >&2
echo "Task: $TASK_PROMPT" >&2
echo "Time: $(date -Iseconds)" >&2
echo "Run ID: $RUN_ID" >&2

CMD=(claude -p --output-format json --permission-mode bypassPermissions)

if [ -n "$MODEL" ]; then
  CMD+=(--model "$MODEL")
fi

CMD+=("$TASK_PROMPT")

if [ -n "$WORKDIR" ]; then
  (
    cd "$WORKDIR" &&
    "${CMD[@]}" >"$RUN_STDOUT" 2>"$RUN_STDERR"
  )
  CLAUDE_EXIT=$?
else
  "${CMD[@]}" >"$RUN_STDOUT" 2>"$RUN_STDERR"
  CLAUDE_EXIT=$?
fi

REQUESTED_MODEL="$MODEL"
SESSION_ID=""
DETECTED_MAIN_MODEL=""
DETECTED_MODELS_JSON='[]'
RESULT_TEXT=""

if [ -s "$RUN_STDOUT" ]; then
  SESSION_ID="$(jq -r '.session_id // ""' "$RUN_STDOUT" 2>/dev/null || true)"
  DETECTED_MAIN_MODEL="$(jq -r '(.modelUsage // {}) | keys | map(sub("\\[[^]]+\\]$"; "")) | .[0] // ""' "$RUN_STDOUT" 2>/dev/null || true)"
  DETECTED_MODELS_JSON="$(jq -c '((.modelUsage // {}) | keys | map(sub("\\[[^]]+\\]$"; ""))) // []' "$RUN_STDOUT" 2>/dev/null || echo '[]')"
  RESULT_TEXT="$(jq -r '.result // ""' "$RUN_STDOUT" 2>/dev/null || true)"
fi

printf '%s\n' "$RESULT_TEXT" >"$RUN_LAST"

cp "$RUN_STDOUT" "$SCRIPT_DIR/claude_stdout.json" 2>/dev/null || true
cp "$RUN_STDERR" "$SCRIPT_DIR/claude_stderr.txt" 2>/dev/null || true
if [ -f "$RUN_LAST" ]; then
  cp "$RUN_LAST" "$SCRIPT_DIR/claude_last_message.md" 2>/dev/null || true
fi

jq -nc \
  --arg run_id "$RUN_ID" \
  --arg task "$TASK_PROMPT" \
  --arg requested_model "$REQUESTED_MODEL" \
  --arg detected_main_model "$DETECTED_MAIN_MODEL" \
  --arg detected_provider "anthropic" \
  --arg session_id "$SESSION_ID" \
  --arg stdout_file "$RUN_STDOUT" \
  --arg stderr_file "$RUN_STDERR" \
  --arg last_file "$RUN_LAST" \
  --arg finished_at "$(date -Iseconds)" \
  --argjson detected_models "${DETECTED_MODELS_JSON:-[]}" \
  --argjson exit_code "$CLAUDE_EXIT" \
  '{
    run_id: $run_id,
    task: $task,
    requested_model: $requested_model,
    detected_main_model: $detected_main_model,
    detected_models: $detected_models,
    detected_provider: $detected_provider,
    session_id: $session_id,
    stdout_file: $stdout_file,
    stderr_file: $stderr_file,
    last_message_file: $last_file,
    exit_code: $exit_code,
    finished_at: $finished_at
  }' >>"$RUNS_DIR/manifest.jsonl"

if [ -n "$RESULT_TEXT" ]; then
  printf '%s\n' "$RESULT_TEXT"
fi

echo "=== Claude Worker Finished (exit: $CLAUDE_EXIT, run: $RUN_ID) ===" >&2

exit $CLAUDE_EXIT
