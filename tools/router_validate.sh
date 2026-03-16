#!/usr/bin/env bash
# router_validate.sh — reusable router validation entrypoint
#
# Runs wrapper and/or team smoke checks, captures their latest reports, and
# emits a combined markdown summary for quick auditing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/router_validation_runs"
mkdir -p "$RUNS_DIR"

WORKDIR="/mnt/c/Users/mjfos"
MODE="all"

usage() {
  cat <<'EOF'
Usage: router_validate.sh [-C workdir] [-m all|wrapper|team]
EOF
}

while getopts ":C:m:h" opt; do
  case "$opt" in
    C) WORKDIR="$OPTARG" ;;
    m) MODE="$OPTARG" ;;
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

case "$MODE" in
  all|wrapper|team) ;;
  *)
    echo "Invalid mode: $MODE" >&2
    usage >&2
    exit 2
    ;;
esac

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_DIR="$RUNS_DIR/validate-$RUN_ID"
mkdir -p "$RUN_DIR"

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

run_check() {
  local label="$1"
  local script_path="$2"
  local console_file="$3"
  if "$script_path" "$WORKDIR" >"$console_file" 2>&1; then
    return 0
  fi
  return 1
}

WRAPPER_STATUS="skip"
WRAPPER_REPORT=""
TEAM_STATUS="skip"
TEAM_REPORT=""

if [ "$MODE" = "all" ] || [ "$MODE" = "wrapper" ]; then
  WRAPPER_STATUS="no"
  if run_check "wrapper" "$SCRIPT_DIR/router_wrapper_smoke.sh" "$RUN_DIR/wrapper.console.txt"; then
    WRAPPER_STATUS="yes"
  fi
  WRAPPER_REPORT="$(latest_report_for_prefix wrapper)"
  if [ ! -f "$WRAPPER_REPORT" ]; then
    WRAPPER_REPORT=""
    WRAPPER_STATUS="no"
  else
    WRAPPER_STATUS="$(extract_field "$WRAPPER_REPORT" pass)"
  fi
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "team" ]; then
  TEAM_STATUS="no"
  if run_check "team" "$SCRIPT_DIR/router_team_smoke.sh" "$RUN_DIR/team.console.txt"; then
    TEAM_STATUS="yes"
  fi
  TEAM_REPORT="$(latest_report_for_prefix team)"
  if [ ! -f "$TEAM_REPORT" ]; then
    TEAM_REPORT=""
    TEAM_STATUS="no"
  else
    local_team_created="$(extract_field "$TEAM_REPORT" team_created)"
    local_task_create="$(extract_field "$TEAM_REPORT" task_create_pass)"
    local_task_update="$(extract_field "$TEAM_REPORT" task_update_pass)"
    local_tmux="$(extract_field "$TEAM_REPORT" tmux_backend_pass)"
    local_cleanup="$(extract_field "$TEAM_REPORT" cleanup_pass)"
    if [ "$local_team_created" = "yes" ] && [ "$local_task_create" = "yes" ] && \
       [ "$local_task_update" = "yes" ] && [ "$local_tmux" = "yes" ] && \
       [ "$local_cleanup" = "yes" ]; then
      TEAM_STATUS="yes"
    else
      TEAM_STATUS="no"
    fi
  fi
fi

OVERALL_PASS="yes"
if [ "$MODE" = "all" ] || [ "$MODE" = "wrapper" ]; then
  if [ "$WRAPPER_STATUS" != "yes" ]; then
    OVERALL_PASS="no"
  fi
fi
if [ "$MODE" = "all" ] || [ "$MODE" = "team" ]; then
  if [ "$TEAM_STATUS" != "yes" ]; then
    OVERALL_PASS="no"
  fi
fi

REPORT_FILE="$RUN_DIR/report.md"
cat >"$REPORT_FILE" <<EOF
# Router Validation

- mode: \`$MODE\`
- workdir: \`$WORKDIR\`
- overall_pass: \`$OVERALL_PASS\`
- wrapper_pass: \`${WRAPPER_STATUS}\`
- team_pass: \`${TEAM_STATUS}\`

Artifacts:
- \`$REPORT_FILE\`
- \`${WRAPPER_REPORT:-}\`
- \`${TEAM_REPORT:-}\`
- \`$RUN_DIR/wrapper.console.txt\`
- \`$RUN_DIR/team.console.txt\`
EOF

cat "$REPORT_FILE"

if [ "$OVERALL_PASS" != "yes" ]; then
  exit 1
fi
