#!/usr/bin/env bash
# router_status.sh — summarize latest router validation results

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/router_validation_runs"

extract_field() {
  local file="$1"
  local key="$2"
  local line
  line="$(rg -m1 "^- ${key}: " "$file" || true)"
  printf '%s' "$line" | cut -d'`' -f2
}

latest_report_for_prefix() {
  local prefix="$1"
  ls -1dt "$RUNS_DIR"/"$prefix"-*/report.md 2>/dev/null | head -n 1 || true
}

wrapper_report="$(latest_report_for_prefix wrapper)"
team_report="$(latest_report_for_prefix team)"
validate_report="$(latest_report_for_prefix validate)"

wrapper_pass="missing"
wrapper_workdir=""
wrapper_cc=""
wrapper_cx=""
wrapper_gi=""
wrapper_block_result=""
wrapper_block=""
wrapper_block_success=""
wrapper_block_fail=""
if [ -f "${wrapper_report:-}" ]; then
  wrapper_pass="$(extract_field "$wrapper_report" pass)"
  wrapper_workdir="$(extract_field "$wrapper_report" workdir)"
  wrapper_cc="$(extract_field "$wrapper_report" cc_result)"
  wrapper_cx="$(extract_field "$wrapper_report" cx_result)"
  wrapper_gi="$(extract_field "$wrapper_report" gi_result)"
  wrapper_block_result="$(extract_field "$wrapper_report" gi_block_result)"
  wrapper_block="$(extract_field "$wrapper_report" gi_block_tool_calls)"
  wrapper_block_success="$(extract_field "$wrapper_report" gi_block_tool_success)"
  wrapper_block_fail="$(extract_field "$wrapper_report" gi_block_tool_fail)"
fi

team_created="missing"
team_task_create="missing"
team_task_update="missing"
team_tmux="missing"
team_cleanup="missing"
team_ids=""
team_owners=""
team_backend_researcher=""
team_backend_qa=""
if [ -f "${team_report:-}" ]; then
  team_created="$(extract_field "$team_report" team_created)"
  team_task_create="$(extract_field "$team_report" task_create_pass)"
  team_task_update="$(extract_field "$team_report" task_update_pass)"
  team_tmux="$(extract_field "$team_report" tmux_backend_pass)"
  team_cleanup="$(extract_field "$team_report" cleanup_pass)"
  team_ids="$(extract_field "$team_report" task_ids)"
  team_owners="$(extract_field "$team_report" owners)"
  team_backend_researcher="$(extract_field "$team_report" researcher_backend)"
  team_backend_qa="$(extract_field "$team_report" qa_backend)"
fi

router_ready="no"
if [ "$wrapper_pass" = "yes" ] && [ "$team_created" = "yes" ] && \
   [ "$team_task_create" = "yes" ] && [ "$team_task_update" = "yes" ] && \
   [ "$team_tmux" = "yes" ] && [ "$team_cleanup" = "yes" ]; then
  router_ready="yes"
fi

cat <<EOF
# Router Status

- router_ready: \`$router_ready\`
- latest_validate_report: \`${validate_report:-}\`
- latest_wrapper_report: \`${wrapper_report:-}\`
- latest_team_report: \`${team_report:-}\`

## Wrapper
- pass: \`$wrapper_pass\`
- workdir: \`${wrapper_workdir}\`
- cc_result: \`${wrapper_cc}\`
- cx_result: \`${wrapper_cx}\`
- gi_result: \`${wrapper_gi}\`
- gi_block_result: \`${wrapper_block_result}\`
- gi_block_tool_calls: \`${wrapper_block}\`
- gi_block_tool_success: \`${wrapper_block_success}\`
- gi_block_tool_fail: \`${wrapper_block_fail}\`

## Team
- team_created: \`$team_created\`
- task_create_pass: \`$team_task_create\`
- task_update_pass: \`$team_task_update\`
- tmux_backend_pass: \`$team_tmux\`
- cleanup_pass: \`$team_cleanup\`
- task_ids: \`${team_ids}\`
- owners: \`${team_owners}\`
- researcher_backend: \`${team_backend_researcher}\`
- qa_backend: \`${team_backend_qa}\`
EOF
