#!/usr/bin/env bash
# router_team_smoke.sh — repo-agnostic interactive TeamCreate/TaskCreate/tmux smoke
#
# Runs an interactive Claude session inside tmux, accepts the bypass-permissions
# warning, creates a temporary team and tasks, snapshots the resulting team
# metadata, and reports whether member backends are real tmux panes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/router_validation_runs"
mkdir -p "$RUNS_DIR"

WORKDIR="${1:-/mnt/c/Users/mjfos}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
SHORT_ID="$(date +%H%M%S)-$$"
TEAM_NAME="router-smoke-$SHORT_ID"
SESSION_NAME="router_team_smoke_$SHORT_ID"
RUN_DIR="$RUNS_DIR/team-$RUN_ID"
mkdir -p "$RUN_DIR"

PROMPT_FILE="$RUN_DIR/prompt.txt"
PROMPT_SEND_FILE="$RUN_DIR/prompt.single-line.txt"
CLAUDE_DEBUG="$RUN_DIR/claude.debug.txt"
PANE_OUT="$RUN_DIR/pane.txt"
CONFIG_SNAPSHOT="$RUN_DIR/config.json"
INBOX_SNAPSHOT="$RUN_DIR/team-lead.inbox.json"
TEAM_FILES_SNAPSHOT="$RUN_DIR/team-files.txt"
REPORT_FILE="$RUN_DIR/report.md"
CLEANUP_PROMPT_FILE="$RUN_DIR/cleanup.txt"
CLEANUP_SEND_FILE="$RUN_DIR/cleanup.single-line.txt"

cat >"$PROMPT_FILE" <<EOF
Direct interactive tool smoke test. Work only in team metadata, not project files.

1. Call TeamCreate to create a temporary team named "$TEAM_NAME".
2. Spawn exactly two background members on that team via Agent with run_in_background=true:
   - researcher
   - qa-tester
3. Immediately call TaskCreate twice:
   - one validation task for researcher
   - one validation task for qa-tester
4. Immediately call TaskUpdate so each task has an explicit owner.
5. The tasks should be trivial and file-read-only:
   - read ~/.claude/teams/$TEAM_NAME/config.json
   - send one short status message to the lead
   - then stay idle
6. Do not edit any project files.
7. Use TaskList and TaskGet as needed to verify both tasks exist and both owners are set correctly.
8. Do not delete the team yet.
9. Final response format exactly:
TEAM_NAME=<name>
TASK_IDS=<comma-separated task ids>
OWNERS=<taskId:owner,taskId:owner>
DONE
EOF

cat >"$CLEANUP_PROMPT_FILE" <<EOF
Delete the temporary team named "$TEAM_NAME" with TeamDelete, then reply with exactly:
CLEANED
EOF

tr '\n' ' ' <"$PROMPT_FILE" | sed 's/[[:space:]][[:space:]]*/ /g' >"$PROMPT_SEND_FILE"
tr '\n' ' ' <"$CLEANUP_PROMPT_FILE" | sed 's/[[:space:]][[:space:]]*/ /g' >"$CLEANUP_SEND_FILE"

tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

tmux new-session -d -x 240 -y 80 -s "$SESSION_NAME" \
  "bash -lc 'cd \"$WORKDIR\" && claude --permission-mode bypassPermissions --debug-file \"$CLAUDE_DEBUG\"; echo CLAUDE_EXIT=\$?; sleep 180'"

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

deadline=$((SECONDS + 120))
while [ $SECONDS -lt $deadline ]; do
  tmux capture-pane -t "$SESSION_NAME":0.0 -J -p -S -800 >"$PANE_OUT" 2>/dev/null || true
  if [ -f "/home/mjfos/.claude/teams/$TEAM_NAME/config.json" ] && [ ! -f "$CONFIG_SNAPSHOT" ]; then
    cp "/home/mjfos/.claude/teams/$TEAM_NAME/config.json" "$CONFIG_SNAPSHOT"
  fi
  if [ -f "/home/mjfos/.claude/teams/$TEAM_NAME/inboxes/team-lead.json" ] && [ ! -f "$INBOX_SNAPSHOT" ]; then
    cp "/home/mjfos/.claude/teams/$TEAM_NAME/inboxes/team-lead.json" "$INBOX_SNAPSHOT"
  fi
  if [ -d "/home/mjfos/.claude/teams/$TEAM_NAME" ]; then
    find "/home/mjfos/.claude/teams/$TEAM_NAME" -maxdepth 3 -type f | sort >"$TEAM_FILES_SNAPSHOT"
  fi
  if rg -q 'TASK_IDS=[0-9]' "$PANE_OUT" && rg -q 'OWNERS=[0-9]' "$PANE_OUT"; then
    break
  fi
  sleep 2
done

tmux capture-pane -t "$SESSION_NAME":0.0 -J -p -S -800 >"$PANE_OUT" 2>/dev/null || true

if [ -f "/home/mjfos/.claude/teams/$TEAM_NAME/config.json" ]; then
  cp "/home/mjfos/.claude/teams/$TEAM_NAME/config.json" "$CONFIG_SNAPSHOT"
fi

if [ -f "/home/mjfos/.claude/teams/$TEAM_NAME/inboxes/team-lead.json" ]; then
  cp "/home/mjfos/.claude/teams/$TEAM_NAME/inboxes/team-lead.json" "$INBOX_SNAPSHOT"
fi

if [ -d "/home/mjfos/.claude/teams/$TEAM_NAME" ]; then
  find "/home/mjfos/.claude/teams/$TEAM_NAME" -maxdepth 3 -type f | sort >"$TEAM_FILES_SNAPSHOT"
fi

TEAM_CREATED="no"
TASK_IDS=""
TASK_UPDATE_PASS="no"
OWNERS_LINE=""
RESEARCHER_BACKEND=""
QA_BACKEND=""
RESEARCHER_PANE=""
QA_PANE=""
CLEANUP_PASS="no"

if [ -f "$CONFIG_SNAPSHOT" ]; then
  TEAM_CREATED="yes"
  RESEARCHER_BACKEND="$(jq -r '.members[]? | select(.name=="researcher") | .backendType // ""' "$CONFIG_SNAPSHOT")"
  QA_BACKEND="$(jq -r '.members[]? | select(.name=="qa-tester") | .backendType // ""' "$CONFIG_SNAPSHOT")"
  RESEARCHER_PANE="$(jq -r '.members[]? | select(.name=="researcher") | .tmuxPaneId // ""' "$CONFIG_SNAPSHOT")"
  QA_PANE="$(jq -r '.members[]? | select(.name=="qa-tester") | .tmuxPaneId // ""' "$CONFIG_SNAPSHOT")"
fi

TASK_IDS="$(rg -o 'TASK_IDS=[0-9,]+' "$PANE_OUT" | tail -n 1 | cut -d= -f2- || true)"
OWNERS_LINE="$(rg -o 'OWNERS=[0-9]+:[^[:space:]]+,[0-9]+:[^[:space:]]+' "$PANE_OUT" | tail -n 1 | cut -d= -f2- || true)"
if rg -q "TEAM_NAME=$TEAM_NAME" "$PANE_OUT"; then
  TEAM_CREATED="yes"
fi

TMUX_BACKEND_PASS="no"
if [ -n "$RESEARCHER_BACKEND" ] && [ -n "$QA_BACKEND" ] && \
   [ "$RESEARCHER_BACKEND" = "tmux" ] && [ "$QA_BACKEND" = "tmux" ] && \
   [ -n "$RESEARCHER_PANE" ] && [ -n "$QA_PANE" ] && \
   [ "$RESEARCHER_PANE" != "in-process" ] && [ "$QA_PANE" != "in-process" ]; then
  TMUX_BACKEND_PASS="yes"
fi

TASK_CREATE_PASS="no"
if [ -n "$TASK_IDS" ] && [ "$TASK_IDS" != "<comma-separated task ids>" ]; then
  TASK_CREATE_PASS="yes"
fi

if printf '%s\n' "$OWNERS_LINE" | rg -q ':[^,]+' && \
   printf '%s\n' "$OWNERS_LINE" | rg -q 'researcher' && \
   printf '%s\n' "$OWNERS_LINE" | rg -q 'qa-tester'; then
  TASK_UPDATE_PASS="yes"
fi

if [ "$TEAM_CREATED" = "yes" ]; then
  tmux load-buffer "$CLEANUP_SEND_FILE"
  tmux paste-buffer -t "$SESSION_NAME":0.0
  sleep 1
  tmux send-keys -t "$SESSION_NAME":0.0 C-m
  cleanup_deadline=$((SECONDS + 60))
  while [ $SECONDS -lt $cleanup_deadline ]; do
    tmux capture-pane -t "$SESSION_NAME":0.0 -J -p -S -800 >"$PANE_OUT" 2>/dev/null || true
    if [ ! -d "/home/mjfos/.claude/teams/$TEAM_NAME" ]; then
      CLEANUP_PASS="yes"
      break
    fi
    sleep 2
  done
fi

cat >"$REPORT_FILE" <<EOF
# Router Team Smoke

- team_name: \`$TEAM_NAME\`
- workdir: \`$WORKDIR\`
- team_created: \`$TEAM_CREATED\`
- task_ids: \`${TASK_IDS:-}\`
- task_create_pass: \`$TASK_CREATE_PASS\`
- task_update_pass: \`$TASK_UPDATE_PASS\`
- owners: \`${OWNERS_LINE:-}\`
- researcher_backend: \`${RESEARCHER_BACKEND:-}\`
- qa_backend: \`${QA_BACKEND:-}\`
- researcher_pane: \`${RESEARCHER_PANE:-}\`
- qa_pane: \`${QA_PANE:-}\`
- tmux_backend_pass: \`$TMUX_BACKEND_PASS\`
- cleanup_pass: \`$CLEANUP_PASS\`

Artifacts:
- \`$PROMPT_FILE\`
- \`$CLAUDE_DEBUG\`
- \`$PANE_OUT\`
- \`$CONFIG_SNAPSHOT\`
- \`$INBOX_SNAPSHOT\`
- \`$TEAM_FILES_SNAPSHOT\`
EOF

cat "$REPORT_FILE"

tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

if [ "$TEAM_CREATED" != "yes" ] || [ "$TASK_CREATE_PASS" != "yes" ] || [ "$TASK_UPDATE_PASS" != "yes" ] || [ "$TMUX_BACKEND_PASS" != "yes" ] || [ "$CLEANUP_PASS" != "yes" ]; then
  exit 1
fi
