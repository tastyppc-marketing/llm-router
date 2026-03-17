#!/usr/bin/env bash
# codex_smart_team.sh — invoke Claude /smart-team from Codex and wait for a routed result
#
# Usage:
#   codex_smart_team.sh [-C workdir] [-t timeout_sec] "<task prompt>"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/codex_smart_team_runs"
mkdir -p "$RUNS_DIR"

WORKDIR="$PWD"
TIMEOUT_SEC=1800
EXTRA_ARGS=()

usage() {
  cat <<'EOF'
Usage: codex_smart_team.sh [-C workdir] [-t timeout_sec] "<task prompt>"
EOF
}

while getopts ":C:t:h" opt; do
  case "$opt" in
    C) WORKDIR="$OPTARG" ;;
    t) TIMEOUT_SEC="$OPTARG" ;;
    h)
      usage
      exit 0
      ;;
    :)
      echo "Missing argument for -$OPTARG" >&2
      usage >&2
      exit 2
      ;;
    \?)
      echo "Unknown option: -$OPTARG" >&2
      usage >&2
      exit 2
      ;;
  esac
done
shift $((OPTIND - 1))

EXTRA_ARGS=("$@")
if [ ${#EXTRA_ARGS[@]} -lt 1 ]; then
  usage >&2
  exit 2
fi

TASK_PROMPT="${EXTRA_ARGS[*]}"
ESCAPED_TASK_PROMPT="$(printf '%s' "$TASK_PROMPT" | sed 's/\\/\\\\/g; s/"/\\"/g')"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
SHORT_ID="$(date +%H%M%S)-$$"
SESSION_NAME="codex_smart_team_$SHORT_ID"
RUN_DIR="$RUNS_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

PROMPT_FILE="$RUN_DIR/prompt.txt"
PROMPT_SEND_FILE="$RUN_DIR/prompt.single-line.txt"
CLAUDE_DEBUG="$RUN_DIR/claude.debug.txt"
PANE_OUT="$RUN_DIR/pane.txt"
TRANSCRIPT_FILE="$RUN_DIR/transcript.txt"
TRANSCRIPT_CLEAN="$RUN_DIR/transcript.clean.txt"
REPORT_FILE="$RUN_DIR/report.md"

cat >"$PROMPT_FILE" <<EOF
/smart-team "$ESCAPED_TASK_PROMPT

Wrapper requirements:
- Complete the full routed run before responding.
- If you need user input, stop after the blocking questions and do not keep working in the background.
- Ensure the final lead response includes these exact lines at the end:
SMART_TEAM_DONE
SMART_TEAM_STATUS=<done|needs_user_input|failed>
SMART_TEAM_SUMMARY=<one concise sentence>
"
EOF

tr '\n' ' ' <"$PROMPT_FILE" | sed 's/[[:space:]][[:space:]]*/ /g' >"$PROMPT_SEND_FILE"

tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

tmux new-session -d -x 240 -y 80 -s "$SESSION_NAME" \
  "bash -lc 'cd \"$WORKDIR\" && claude --permission-mode bypassPermissions --debug-file \"$CLAUDE_DEBUG\"; echo CLAUDE_EXIT=\$?; sleep 120'"
tmux pipe-pane -o -t "$SESSION_NAME":0.0 "cat >> $TRANSCRIPT_FILE"

sleep 4
tmux capture-pane -t "$SESSION_NAME":0.0 -J -p -S -200 >"$PANE_OUT" 2>/dev/null || true
if rg -q 'WARNING: Claude Code running in Bypass Permissions mode|Yes, I accept' "$PANE_OUT"; then
  tmux send-keys -t "$SESSION_NAME":0.0 -l 2
  sleep 1
  tmux send-keys -t "$SESSION_NAME":0.0 C-m
  sleep 4
fi

tmux load-buffer "$PROMPT_SEND_FILE"
tmux paste-buffer -t "$SESSION_NAME":0.0
sleep 1
tmux send-keys -t "$SESSION_NAME":0.0 C-m

deadline=$((SECONDS + TIMEOUT_SEC))
STATUS="timeout"
SUMMARY="Timed out before SMART_TEAM_DONE sentinel appeared."

clean_transcript() {
  if [ ! -f "$TRANSCRIPT_FILE" ]; then
    : >"$TRANSCRIPT_CLEAN"
    return
  fi
  perl -0pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g; s/\e\][^\a]*(?:\a|\e\\)//g; s/\r/\n/g' \
    "$TRANSCRIPT_FILE" >"$TRANSCRIPT_CLEAN"
}

debug_count() {
  local pattern="$1"
  if [ ! -f "$CLAUDE_DEBUG" ]; then
    echo 0
    return
  fi
  rg -c "$pattern" "$CLAUDE_DEBUG" 2>/dev/null || echo 0
}

while [ $SECONDS -lt $deadline ]; do
  tmux capture-pane -t "$SESSION_NAME":0.0 -J -p -S -3000 >"$PANE_OUT" 2>/dev/null || true
  clean_transcript
  if rg -q '^SMART_TEAM_DONE$' "$TRANSCRIPT_CLEAN"; then
    STATUS="$(sed -n 's/^SMART_TEAM_STATUS=//p' "$TRANSCRIPT_CLEAN" | tail -n 1)"
    SUMMARY="$(sed -n 's/^SMART_TEAM_SUMMARY=//p' "$TRANSCRIPT_CLEAN" | tail -n 1)"
    [ -n "$STATUS" ] || STATUS="done"
    [ -n "$SUMMARY" ] || SUMMARY="Routed run completed."
    break
  fi
  sleep 5
done

tmux capture-pane -t "$SESSION_NAME":0.0 -J -p -S -3000 >"$PANE_OUT" 2>/dev/null || true
clean_transcript
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

TOOLSEARCH_TEAM_COUNT="$(debug_count 'ToolSearchTool: selected .*TeamCreate')"
TEAM_CREATE_COUNT="$(debug_count 'executePreToolHooks called for tool: TeamCreate')"
TASK_CREATE_COUNT="$(debug_count 'executePreToolHooks called for tool: TaskCreate')"
TASK_UPDATE_COUNT="$(debug_count 'executePreToolHooks called for tool: TaskUpdate')"
TASK_LIST_COUNT="$(debug_count 'executePreToolHooks called for tool: TaskList')"
TASK_GET_COUNT="$(debug_count 'executePreToolHooks called for tool: TaskGet')"
TEAM_DELETE_COUNT="$(debug_count 'executePreToolHooks called for tool: TeamDelete')"
TMUX_PANE_COUNT="$(debug_count '\\[TmuxBackend\\] Created teammate pane')"

if [ "$STATUS" = "timeout" ]; then
  if [ "$TEAM_DELETE_COUNT" -gt 0 ]; then
    STATUS="done"
    SUMMARY="Detected completed routed run via TeamDelete in the Claude debug log."
  elif [ "$TEAM_CREATE_COUNT" -gt 0 ] || [ "$TASK_CREATE_COUNT" -gt 0 ] || [ "$TMUX_PANE_COUNT" -gt 0 ]; then
    SUMMARY="Timed out after routed launch: team_create=$TEAM_CREATE_COUNT task_create=$TASK_CREATE_COUNT task_update=$TASK_UPDATE_COUNT tmux_panes=$TMUX_PANE_COUNT team_delete=$TEAM_DELETE_COUNT."
  elif [ "$TOOLSEARCH_TEAM_COUNT" -gt 0 ]; then
    SUMMARY="Timed out after /smart-team selected team tools, before observable team bootstrap."
  fi
fi

TRANSCRIPT_TAIL=""
if [ -f "$TRANSCRIPT_CLEAN" ]; then
  TRANSCRIPT_TAIL="$(tail -n 80 "$TRANSCRIPT_CLEAN" | sed 's/`/'"'"'/g')"
fi

cat >"$REPORT_FILE" <<EOF
# Codex Smart Team Run

- workdir: \`$WORKDIR\`
- timeout_sec: \`$TIMEOUT_SEC\`
- status: \`$STATUS\`
- summary: \`$SUMMARY\`

Artifacts:
- \`$PROMPT_FILE\`
- \`$CLAUDE_DEBUG\`
- \`$PANE_OUT\`
- \`$TRANSCRIPT_FILE\`
- \`$TRANSCRIPT_CLEAN\`

Debug counters:
- \`toolsearch_team=$TOOLSEARCH_TEAM_COUNT\`
- \`team_create=$TEAM_CREATE_COUNT\`
- \`task_create=$TASK_CREATE_COUNT\`
- \`task_update=$TASK_UPDATE_COUNT\`
- \`task_list=$TASK_LIST_COUNT\`
- \`task_get=$TASK_GET_COUNT\`
- \`team_delete=$TEAM_DELETE_COUNT\`
- \`tmux_panes=$TMUX_PANE_COUNT\`

Transcript tail:

\`\`\`
$TRANSCRIPT_TAIL
\`\`\`
EOF

cat "$REPORT_FILE"

case "$STATUS" in
  done) exit 0 ;;
  needs_user_input) exit 2 ;;
  *) exit 1 ;;
esac
