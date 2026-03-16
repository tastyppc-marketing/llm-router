#!/usr/bin/env bash
# codex_worker.sh — Global Codex CLI wrapper
# Usage: codex_worker.sh [-m model] [-C dir] [--json] [--output-schema '<json>'] [--output-last-message] "<prompt>"

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/codex_runs"
mkdir -p "$RUNS_DIR"

MODEL=""
WORKDIR=""
JSON_MODE=""
OUTPUT_SCHEMA=""
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
    --json)
      JSON_MODE="--json"
      shift
      ;;
    --output-schema)
      OUTPUT_SCHEMA="$2"
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
  echo "Usage: codex_worker.sh [-m model] [-C dir] [--json] [--output-schema '<json>'] [--output-last-message] \"<prompt>\"" >&2
  exit 1
fi

TASK_PROMPT="${EXTRA_ARGS[*]}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_STDOUT="$RUNS_DIR/codex_stdout.$RUN_ID.txt"
RUN_STDERR="$RUNS_DIR/codex_stderr.$RUN_ID.txt"
RUN_LAST="$RUNS_DIR/codex_last_message.$RUN_ID.md"

echo "=== Codex Worker Started ===" >&2
echo "Task: $TASK_PROMPT" >&2
echo "Time: $(date -Iseconds)" >&2
echo "Run ID: $RUN_ID" >&2

CMD=(codex exec --full-auto --skip-git-repo-check --sandbox workspace-write)

if [ -n "$MODEL" ]; then
  CMD+=(-m "$MODEL")
fi

if [ -n "$WORKDIR" ]; then
  CMD+=(-C "$WORKDIR")
fi

if [ -n "$JSON_MODE" ]; then
  CMD+=(--json)
fi

if [ -n "$OUTPUT_SCHEMA" ]; then
  CMD+=(--output-schema "$OUTPUT_SCHEMA")
fi

if [ -n "$SAVE_LAST_MESSAGE" ]; then
  CMD+=(-o "$RUN_LAST")
else
  CMD+=(-o "$RUN_LAST")
fi

CMD+=("$TASK_PROMPT")

"${CMD[@]}" 2>"$RUN_STDERR" | tee "$RUN_STDOUT"

CODEX_EXIT=${PIPESTATUS[0]}

REQUESTED_MODEL="$MODEL"
DETECTED_MODEL="$(sed -n 's/^model:[[:space:]]*//p' "$RUN_STDERR" | head -n 1)"
DETECTED_PROVIDER="$(sed -n 's/^provider:[[:space:]]*//p' "$RUN_STDERR" | head -n 1)"

cp "$RUN_STDOUT" "$SCRIPT_DIR/codex_stdout.txt" 2>/dev/null || true
cp "$RUN_STDERR" "$SCRIPT_DIR/codex_stderr.txt" 2>/dev/null || true
if [ -f "$RUN_LAST" ]; then
  cp "$RUN_LAST" "$SCRIPT_DIR/codex_last_message.md" 2>/dev/null || true
fi

jq -nc \
  --arg run_id "$RUN_ID" \
  --arg task "$TASK_PROMPT" \
  --arg requested_model "$REQUESTED_MODEL" \
  --arg detected_model "$DETECTED_MODEL" \
  --arg detected_provider "$DETECTED_PROVIDER" \
  --arg stdout_file "$RUN_STDOUT" \
  --arg stderr_file "$RUN_STDERR" \
  --arg last_file "$RUN_LAST" \
  --arg finished_at "$(date -Iseconds)" \
  --argjson exit_code "$CODEX_EXIT" \
  '{
    run_id: $run_id,
    task: $task,
    requested_model: $requested_model,
    detected_model: $detected_model,
    detected_provider: $detected_provider,
    stdout_file: $stdout_file,
    stderr_file: $stderr_file,
    last_message_file: $last_file,
    exit_code: $exit_code,
    finished_at: $finished_at
  }' >> "$RUNS_DIR/manifest.jsonl"

echo "=== Codex Worker Finished (exit: $CODEX_EXIT, run: $RUN_ID) ===" >&2

exit $CODEX_EXIT
