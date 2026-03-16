#!/usr/bin/env bash
# failure_mode_flow.sh — Sequential GI -> CC -> CX planning flow
# Usage: failure_mode_flow.sh [-C dir] [--accept-defaults] "<task prompt>"

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/failure_mode_flow_runs"
mkdir -p "$RUNS_DIR"

WORKDIR=""
ACCEPT_DEFAULTS="no"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -C)
      WORKDIR="$2"
      shift 2
      ;;
    --accept-defaults)
      ACCEPT_DEFAULTS="yes"
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if [ ${#EXTRA_ARGS[@]} -lt 1 ]; then
  echo "Usage: failure_mode_flow.sh [-C dir] [--accept-defaults] \"<task prompt>\"" >&2
  exit 1
fi

TASK_PROMPT="${EXTRA_ARGS[*]}"
FLOW_ID="$(date +%Y%m%d-%H%M%S)-$$"
FLOW_DIR="$RUNS_DIR/$FLOW_ID"
mkdir -p "$FLOW_DIR"
REPORT_FILE="$FLOW_DIR/report.md"

GI_RUN_ID=""
CC_RUN_ID=""
CX_RUN_ID=""
STATUS="started"

run_wrapper() {
  local label="$1"
  local wrapper="$2"
  local model="$3"
  local prompt="$4"
  local stdout_file="$FLOW_DIR/${label}.stdout.txt"
  local stderr_file="$FLOW_DIR/${label}.stderr.txt"
  local -a cmd

  cmd=("$wrapper")
  if [ -n "$model" ]; then
    cmd+=(-m "$model")
  fi
  if [ -n "$WORKDIR" ]; then
    cmd+=(-C "$WORKDIR")
  fi
  cmd+=("$prompt")

  set +e
  "${cmd[@]}" >"$stdout_file" 2>"$stderr_file"
  local exit_code=$?
  set -e

  if [ $exit_code -ne 0 ]; then
    echo "Step '$label' failed. See $stderr_file" >&2
    exit $exit_code
  fi

  sed -n 's/^Run ID: //p' "$stderr_file" | head -n 1
}

write_manifest() {
  jq -nc \
    --arg flow_id "$FLOW_ID" \
    --arg task "$TASK_PROMPT" \
    --arg workdir "$WORKDIR" \
    --arg accept_defaults "$ACCEPT_DEFAULTS" \
    --arg gi_run_id "$GI_RUN_ID" \
    --arg cc_run_id "$CC_RUN_ID" \
    --arg cx_run_id "$CX_RUN_ID" \
    --arg status "$STATUS" \
    --arg report_file "$REPORT_FILE" \
    --arg finished_at "$(date -Iseconds)" \
    '{
      flow_id: $flow_id,
      task: $task,
      workdir: $workdir,
      accept_defaults: ($accept_defaults == "yes"),
      gi_run_id: $gi_run_id,
      cc_run_id: $cc_run_id,
      cx_run_id: $cx_run_id,
      status: $status,
      report_file: $report_file,
      finished_at: $finished_at
    }' >>"$RUNS_DIR/manifest.jsonl"
}

GI_PROMPT=$(cat <<EOF
Act as GI-Mapper.

Task:
$TASK_PROMPT

Instructions:
- Do not code.
- Start with either \`No blocking questions\` or \`Blocking questions (max 5)\` with proposed defaults.
- Ask only questions that materially change correctness, scope, UX, or irreversible behavior.
- Then output \`Map\` with 3-5 bullets and \`Handoff\` with the best next lane.
- Keep it concise.
EOF
)

GI_RUN_ID="$(run_wrapper "gi" "$SCRIPT_DIR/gemini_worker.sh" "gemini-2.5-pro" "$GI_PROMPT")"
GI_OUTPUT="$(cat "$FLOW_DIR/gi.stdout.txt")"

if grep -q '^Blocking questions' "$FLOW_DIR/gi.stdout.txt" && [ "$ACCEPT_DEFAULTS" != "yes" ]; then
  CC_PROMPT=$(cat <<EOF
Act as CC-Diagnostician.

Another agent found these raw blocker questions plus defaults:

$GI_OUTPUT

Instructions:
- Rewrite only the blocker questions into the clearest possible user-facing wording.
- Keep them concise and easy to answer.
- Keep the proposed defaults.
- Output only \`Blocking questions (max 5)\` followed by the rewritten questions.
EOF
)

  CC_RUN_ID="$(run_wrapper "cc" "$SCRIPT_DIR/claude_worker.sh" "sonnet" "$CC_PROMPT")"
  CC_OUTPUT="$(cat "$FLOW_DIR/cc.stdout.txt")"
  STATUS="needs_user_input"

  cat >"$REPORT_FILE" <<EOF
# Failure-Mode Flow Report

## GI-Mapper

$GI_OUTPUT

## CC-Diagnostician

$CC_OUTPUT
EOF

  write_manifest
  cat "$REPORT_FILE"
  exit 2
fi

CC_PROMPT=$(cat <<EOF
Act as CC-Diagnostician.

GI-Mapper output:

$GI_OUTPUT

Instructions:
- If GI-Mapper listed blocker questions, assume the proposed defaults are accepted for this planning run.
- Do not code.
- Start with either \`No blocking questions\` or \`Blocking questions (max 5)\`.
- Then output \`Decisions\` with accepted defaults or clarified rules.
- Then output \`Invariants\` with 3-5 bullets.
- Then output \`Handoff\` with the best next lane.
- Keep it concise.
EOF
)

CC_RUN_ID="$(run_wrapper "cc" "$SCRIPT_DIR/claude_worker.sh" "sonnet" "$CC_PROMPT")"
CC_OUTPUT="$(cat "$FLOW_DIR/cc.stdout.txt")"

CX_PROMPT=$(cat <<EOF
Act as CX-Executor.

Task:
$TASK_PROMPT

GI-Mapper output:

$GI_OUTPUT

CC-Diagnostician output:

$CC_OUTPUT

Instructions:
- Do not code.
- Decide whether execution can proceed.
- Start with either \`No blocking questions\` or \`Blocking questions (max 5)\`.
- Then output \`Execution plan\` with 3-5 concise bullets.
- Keep it concise.
EOF
)

CX_RUN_ID="$(run_wrapper "cx" "$SCRIPT_DIR/codex_worker.sh" "" "$CX_PROMPT")"
CX_OUTPUT="$(cat "$FLOW_DIR/cx.stdout.txt")"
STATUS="planned"

cat >"$REPORT_FILE" <<EOF
# Failure-Mode Flow Report

## GI-Mapper

$GI_OUTPUT

## CC-Diagnostician

$CC_OUTPUT

## CX-Executor

$CX_OUTPUT
EOF

write_manifest
cat "$REPORT_FILE"
