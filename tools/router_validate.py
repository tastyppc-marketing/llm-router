#!/usr/bin/env python3
"""Reusable router validation entrypoint."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from router_common import (
    child_env,
    extract_report_field,
    latest_report_for_prefix,
    make_run_id,
    resolve_timeout_sec,
    resolve_trace_id,
    run_console_command,
    write_text,
)


SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "router_validation_runs"
DEFAULT_WORKDIR = "/mnt/c/Users/mjfos"


def usage() -> str:
    return "Usage: router_validate.sh [-C workdir] [-m all|wrapper|team]"


def parse_args(argv: list[str]) -> tuple[str, str]:
    workdir = DEFAULT_WORKDIR
    mode = "all"
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"-h", "--help"}:
            print(usage())
            raise SystemExit(0)
        if arg == "-C":
            index += 1
            if index >= len(argv):
                print("Missing argument for -C", file=sys.stderr)
                print(usage(), file=sys.stderr)
                raise SystemExit(2)
            workdir = argv[index]
        elif arg == "-m":
            index += 1
            if index >= len(argv):
                print("Missing argument for -m", file=sys.stderr)
                print(usage(), file=sys.stderr)
                raise SystemExit(2)
            mode = argv[index]
        else:
            print(f"Unknown option: {arg}", file=sys.stderr)
            print(usage(), file=sys.stderr)
            raise SystemExit(2)
        index += 1

    if mode not in {"all", "wrapper", "team"}:
        print(f"Invalid mode: {mode}", file=sys.stderr)
        print(usage(), file=sys.stderr)
        raise SystemExit(2)
    return workdir, mode


def render_report(
    *,
    report_file: Path,
    trace_id: str,
    mode: str,
    workdir: str,
    overall_pass: str,
    wrapper_status: str,
    team_status: str,
    wrapper_report: Path | None,
    team_report: Path | None,
    run_dir: Path,
) -> str:
    report = textwrap.dedent(
        f"""\
        # Router Validation

        - trace_id: `{trace_id}`
        - mode: `{mode}`
        - workdir: `{workdir}`
        - overall_pass: `{overall_pass}`
        - wrapper_pass: `{wrapper_status}`
        - team_pass: `{team_status}`

        Artifacts:
        - `{report_file}`
        - `{wrapper_report or ""}`
        - `{team_report or ""}`
        - `{run_dir / "wrapper.console.txt"}`
        - `{run_dir / "team.console.txt"}`
        """
    )
    write_text(report_file, report)
    return report


def main(argv: list[str]) -> int:
    workdir, mode = parse_args(argv)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = resolve_trace_id()
    step_timeout_sec = resolve_timeout_sec(
        "LLM_ROUTER_VALIDATE_STEP_TIMEOUT_SEC",
        "LLM_ROUTER_TIMEOUT_SEC",
        default_sec=5400,
    )
    child_process_env = child_env(trace_id)

    run_dir = RUNS_DIR / f"validate-{make_run_id()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    wrapper_console = run_dir / "wrapper.console.txt"
    team_console = run_dir / "team.console.txt"

    wrapper_status = "skip"
    wrapper_report: Path | None = None
    if mode in {"all", "wrapper"}:
        wrapper_status = "no"
        run_console_command(
            [str(SCRIPT_DIR / "router_wrapper_smoke.sh"), workdir],
            wrapper_console,
            timeout_sec=step_timeout_sec,
            env=child_process_env,
        )
        wrapper_report = latest_report_for_prefix(RUNS_DIR, "wrapper")
        if wrapper_report is not None:
            wrapper_status = extract_report_field(wrapper_report, "pass", "no") or "no"

    team_status = "skip"
    team_report: Path | None = None
    if mode in {"all", "team"}:
        team_status = "no"
        run_console_command(
            [str(SCRIPT_DIR / "router_team_smoke.sh"), workdir],
            team_console,
            timeout_sec=step_timeout_sec,
            env=child_process_env,
        )
        team_report = latest_report_for_prefix(RUNS_DIR, "team")
        if team_report is not None:
            team_created = extract_report_field(team_report, "team_created")
            task_create = extract_report_field(team_report, "task_create_pass")
            task_update = extract_report_field(team_report, "task_update_pass")
            tmux_backend = extract_report_field(team_report, "tmux_backend_pass")
            cleanup = extract_report_field(team_report, "cleanup_pass")
            if all(value == "yes" for value in (team_created, task_create, task_update, tmux_backend, cleanup)):
                team_status = "yes"

    overall_pass = "yes"
    if mode in {"all", "wrapper"} and wrapper_status != "yes":
        overall_pass = "no"
    if mode in {"all", "team"} and team_status != "yes":
        overall_pass = "no"

    report_file = run_dir / "report.md"
    report = render_report(
        report_file=report_file,
        trace_id=trace_id,
        mode=mode,
        workdir=workdir,
        overall_pass=overall_pass,
        wrapper_status=wrapper_status,
        team_status=team_status,
        wrapper_report=wrapper_report,
        team_report=team_report,
        run_dir=run_dir,
    )
    print(report, end="" if report.endswith("\n") else "\n")
    return 0 if overall_pass == "yes" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
