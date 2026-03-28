#!/usr/bin/env python3
"""Summarize the latest router validation results."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from router_common import extract_report_field, latest_report_for_prefix


SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "router_validation_runs"


def usage() -> str:
    return "Usage: router_status.sh"


def main(argv: list[str]) -> int:
    if any(arg in {"-h", "--help"} for arg in argv):
        print(usage())
        return 0

    wrapper_report = latest_report_for_prefix(RUNS_DIR, "wrapper")
    team_report = latest_report_for_prefix(RUNS_DIR, "team")
    validate_report = latest_report_for_prefix(RUNS_DIR, "validate")
    validate_trace_id = extract_report_field(validate_report, "trace_id")

    wrapper_pass = extract_report_field(wrapper_report, "pass", "missing")
    wrapper_workdir = extract_report_field(wrapper_report, "workdir")
    wrapper_cc = extract_report_field(wrapper_report, "cc_result")
    wrapper_cx = extract_report_field(wrapper_report, "cx_result")
    wrapper_gi = extract_report_field(wrapper_report, "gi_result")
    wrapper_block_result = extract_report_field(wrapper_report, "gi_block_result")
    wrapper_block = extract_report_field(wrapper_report, "gi_block_tool_calls")
    wrapper_block_success = extract_report_field(wrapper_report, "gi_block_tool_success")
    wrapper_block_fail = extract_report_field(wrapper_report, "gi_block_tool_fail")

    team_created = extract_report_field(team_report, "team_created", "missing")
    team_task_create = extract_report_field(team_report, "task_create_pass", "missing")
    team_task_update = extract_report_field(team_report, "task_update_pass", "missing")
    team_tmux = extract_report_field(team_report, "tmux_backend_pass", "missing")
    team_cleanup = extract_report_field(team_report, "cleanup_pass", "missing")
    team_ids = extract_report_field(team_report, "task_ids")
    team_owners = extract_report_field(team_report, "owners")
    team_backend_researcher = extract_report_field(team_report, "researcher_backend")
    team_backend_qa = extract_report_field(team_report, "qa_backend")

    router_ready = "yes" if all(
        value == "yes"
        for value in (wrapper_pass, team_created, team_task_create, team_task_update, team_tmux, team_cleanup)
    ) else "no"

    report = textwrap.dedent(
        f"""\
        # Router Status

        - trace_id: `{validate_trace_id}`
        - router_ready: `{router_ready}`
        - latest_validate_report: `{validate_report or ""}`
        - latest_wrapper_report: `{wrapper_report or ""}`
        - latest_team_report: `{team_report or ""}`

        ## Wrapper
        - pass: `{wrapper_pass}`
        - workdir: `{wrapper_workdir}`
        - cc_result: `{wrapper_cc}`
        - cx_result: `{wrapper_cx}`
        - gi_result: `{wrapper_gi}`
        - gi_block_result: `{wrapper_block_result}`
        - gi_block_tool_calls: `{wrapper_block}`
        - gi_block_tool_success: `{wrapper_block_success}`
        - gi_block_tool_fail: `{wrapper_block_fail}`

        ## Team
        - team_created: `{team_created}`
        - task_create_pass: `{team_task_create}`
        - task_update_pass: `{team_task_update}`
        - tmux_backend_pass: `{team_tmux}`
        - cleanup_pass: `{team_cleanup}`
        - task_ids: `{team_ids}`
        - owners: `{team_owners}`
        - researcher_backend: `{team_backend_researcher}`
        - qa_backend: `{team_backend_qa}`
        """
    )
    print(report, end="" if report.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
