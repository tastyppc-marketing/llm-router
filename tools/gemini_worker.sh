#!/usr/bin/env bash
# gemini_worker.sh — Global Gemini CLI wrapper
# Usage: gemini_worker.sh [-m model] [-C dir] [--output-last-message] "<prompt>"

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/gemini_runs"
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
  echo "Usage: gemini_worker.sh [-m model] [-C dir] [--output-last-message] \"<prompt>\"" >&2
  exit 1
fi

TASK_PROMPT="${EXTRA_ARGS[*]}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_STDOUT="$RUNS_DIR/gemini_stdout.$RUN_ID.json"
RUN_STDERR="$RUNS_DIR/gemini_stderr.$RUN_ID.txt"
RUN_LAST="$RUNS_DIR/gemini_last_message.$RUN_ID.md"

echo "=== Gemini Worker Started ===" >&2
echo "Task: $TASK_PROMPT" >&2
echo "Time: $(date -Iseconds)" >&2
echo "Run ID: $RUN_ID" >&2

CMD=(gemini --approval-mode yolo --policy /home/mjfos/.gemini/policies/llm-router.toml -o json)

if [ -n "$MODEL" ]; then
  CMD+=(-m "$MODEL")
fi

CMD+=(-p "$TASK_PROMPT")

if [ -n "$WORKDIR" ]; then
  (
    cd "$WORKDIR" &&
    "${CMD[@]}" >"$RUN_STDOUT" 2>"$RUN_STDERR"
  )
  GEMINI_EXIT=$?
else
  "${CMD[@]}" >"$RUN_STDOUT" 2>"$RUN_STDERR"
  GEMINI_EXIT=$?
fi

REQUESTED_MODEL="$MODEL"
SESSION_ID=""
DETECTED_MAIN_MODEL=""
DETECTED_MODELS_JSON='[]'
RESPONSE_TEXT=""

if [ -s "$RUN_STDOUT" ]; then
  SESSION_ID="$(jq -r '.session_id // ""' "$RUN_STDOUT" 2>/dev/null || true)"
  DETECTED_MAIN_MODEL="$(jq -r '(.stats.models // {}) | to_entries[]? | select(.value.roles.main != null) | .key' "$RUN_STDOUT" 2>/dev/null | head -n 1)"
  DETECTED_MODELS_JSON="$(jq -c '((.stats.models // {}) | keys) // []' "$RUN_STDOUT" 2>/dev/null || echo '[]')"
  RESPONSE_TEXT="$(jq -r '.response // ""' "$RUN_STDOUT" 2>/dev/null || true)"
fi

printf '%s\n' "$RESPONSE_TEXT" >"$RUN_LAST"

cp "$RUN_STDOUT" "$SCRIPT_DIR/gemini_stdout.json" 2>/dev/null || true
cp "$RUN_STDERR" "$SCRIPT_DIR/gemini_stderr.txt" 2>/dev/null || true
if [ -f "$RUN_LAST" ]; then
  cp "$RUN_LAST" "$SCRIPT_DIR/gemini_last_message.md" 2>/dev/null || true
fi

jq -nc \
  --arg run_id "$RUN_ID" \
  --arg task "$TASK_PROMPT" \
  --arg requested_model "$REQUESTED_MODEL" \
  --arg detected_main_model "$DETECTED_MAIN_MODEL" \
  --arg detected_provider "google" \
  --arg session_id "$SESSION_ID" \
  --arg stdout_file "$RUN_STDOUT" \
  --arg stderr_file "$RUN_STDERR" \
  --arg last_file "$RUN_LAST" \
  --arg finished_at "$(date -Iseconds)" \
  --argjson detected_models "${DETECTED_MODELS_JSON:-[]}" \
  --argjson exit_code "$GEMINI_EXIT" \
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

if [ -n "$RESPONSE_TEXT" ]; then
  printf '%s\n' "$RESPONSE_TEXT"
fi

echo "=== Gemini Worker Finished (exit: $GEMINI_EXIT, run: $RUN_ID) ===" >&2

exit $GEMINI_EXIT
