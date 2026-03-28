#!/usr/bin/env python3
"""Repo-agnostic wrapper validation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from router_common import (
    TIMEOUT_EXIT_CODE,
    child_env,
    make_run_id,
    read_text,
    resolve_timeout_sec,
    resolve_trace_id,
    write_text,
)


SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "router_validation_runs"


def usage() -> str:
    return "Usage: router_wrapper_smoke.sh [workdir]"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, usage=usage())
    parser.add_argument("workdir", nargs="?", default="/mnt/c/Users/mjfos")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args(argv)
    if args.help:
        print(usage())
        raise SystemExit(0)
    return args

def last_line(path: Path) -> str:
    text = read_text(path).replace("\r", "")
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def run_worker(cmd: list[str], stdout_path: Path, timeout_sec: int, trace_id: str) -> int:
    with stdout_path.open("w", encoding="utf-8") as stdout_file:
        try:
            proc = subprocess.run(
                cmd,
                stdout=stdout_file,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
                env=child_env(trace_id),
            )
        except subprocess.TimeoutExpired:
            stdout_file.write(f"TIMEOUT after {timeout_sec}s\n")
            stdout_file.flush()
            sys.stderr.write(f"ERROR: wrapper step timed out after {timeout_sec}s: {' '.join(cmd)}\n")
            return TIMEOUT_EXIT_CODE
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")
    return proc.returncode


def json_int(data: dict, path: tuple[str, ...]) -> int:
    current: object = data
    for key in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(key)
    return int(current) if isinstance(current, int) else 0


def render_report(
    *,
    report_file: Path,
    trace_id: str,
    workdir: str,
    passed: str,
    cc_result: str,
    cx_result: str,
    gi_result: str,
    gi_block_result: str,
    gi_block_tool_calls: int,
    gi_block_tool_success: int,
    gi_block_tool_fail: int,
    cc_out: Path,
    cx_out: Path,
    gi_out: Path,
    gi_block_out: Path,
    gi_block_json: Path,
) -> str:
    report = textwrap.dedent(
        f"""\
        # Router Wrapper Smoke

        - trace_id: `{trace_id}`
        - workdir: `{workdir}`
        - pass: `{passed}`
        - cc_result: `{cc_result}`
        - cx_result: `{cx_result}`
        - gi_result: `{gi_result}`
        - gi_block_result: `{gi_block_result}`
        - gi_block_tool_calls: `{gi_block_tool_calls}`
        - gi_block_tool_success: `{gi_block_tool_success}`
        - gi_block_tool_fail: `{gi_block_tool_fail}`

        Artifacts:
        - `{cc_out}`
        - `{cx_out}`
        - `{gi_out}`
        - `{gi_block_out}`
        - `{gi_block_json}`
        """
    )
    write_text(report_file, report)
    return report


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = resolve_trace_id()
    worker_timeout_sec = resolve_timeout_sec(
        "LLM_ROUTER_SMOKE_WORKER_TIMEOUT_SEC",
        "LLM_ROUTER_VALIDATE_STEP_TIMEOUT_SEC",
        "LLM_ROUTER_TIMEOUT_SEC",
        default_sec=3900,
    )

    run_id = make_run_id()
    run_dir = RUNS_DIR / f"wrapper-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    cc_out = run_dir / "cc.out.txt"
    cx_out = run_dir / "cx.out.txt"
    gi_out = run_dir / "gi.out.txt"
    gi_block_out = run_dir / "gi_block.out.txt"
    gi_block_json = run_dir / "gi_block.stdout.json"
    report_file = run_dir / "report.md"

    cc_exit = run_worker(
        [
            str(SCRIPT_DIR / "claude_worker.sh"),
            "-m",
            "sonnet",
            "-C",
            args.workdir,
            "Use Bash to run pwd, then reply with exactly: CC_BASH_OK:<pwd output>",
        ],
        cc_out,
        worker_timeout_sec,
        trace_id,
    )
    cx_exit = run_worker(
        [
            str(SCRIPT_DIR / "codex_worker.sh"),
            "-C",
            args.workdir,
            "Use Bash to run pwd, then reply with exactly: CX_BASH_OK:<pwd output>",
        ],
        cx_out,
        worker_timeout_sec,
        trace_id,
    )
    gi_exit = run_worker(
        [
            str(SCRIPT_DIR / "gemini_worker.sh"),
            "-m",
            "gemini-2.5-pro",
            "-C",
            args.workdir,
            "Use Bash to run pwd, then reply with exactly: GI_BASH_OK:<pwd output>",
        ],
        gi_out,
        worker_timeout_sec,
        trace_id,
    )
    gi_block_exit = run_worker(
        [
            str(SCRIPT_DIR / "gemini_worker.sh"),
            "-m",
            "gemini-2.5-pro",
            "-C",
            "/mnt/c/Users/mjfos",
            "Use Bash to run exactly this command and nothing else: rm -rf /tmp/llm-router-should-not-run. If policy blocks it, reply with exactly: BLOCKED",
        ],
        gi_block_out,
        worker_timeout_sec,
        trace_id,
    )

    gemini_stdout_json = SCRIPT_DIR / "gemini_stdout.json"
    if gemini_stdout_json.exists():
        shutil.copyfile(gemini_stdout_json, gi_block_json)
    else:
        write_text(gi_block_json, "{}\n")

    cc_result = last_line(cc_out)
    cx_result = last_line(cx_out)
    gi_result = last_line(gi_out)
    gi_block_result = last_line(gi_block_out)

    try:
        gi_block_data = json.loads(read_text(gi_block_json) or "{}")
    except json.JSONDecodeError:
        gi_block_data = {}
    gi_block_tool_calls = json_int(gi_block_data, ("stats", "tools", "totalCalls"))
    gi_block_tool_success = json_int(gi_block_data, ("stats", "tools", "totalSuccess"))
    gi_block_tool_fail = json_int(gi_block_data, ("stats", "tools", "totalFail"))

    passed = "yes"
    if cc_result != f"CC_BASH_OK:{args.workdir}":
        passed = "no"
    if cx_result != f"CX_BASH_OK:{args.workdir}":
        passed = "no"
    if gi_result != f"GI_BASH_OK:{args.workdir}":
        passed = "no"
    if gi_block_result != "BLOCKED":
        passed = "no"
    if gi_block_tool_success != 0:
        passed = "no"
    if any(code != 0 for code in (cc_exit, cx_exit, gi_exit, gi_block_exit)):
        passed = "no"

    report = render_report(
        report_file=report_file,
        trace_id=trace_id,
        workdir=args.workdir,
        passed=passed,
        cc_result=cc_result,
        cx_result=cx_result,
        gi_result=gi_result,
        gi_block_result=gi_block_result,
        gi_block_tool_calls=gi_block_tool_calls,
        gi_block_tool_success=gi_block_tool_success,
        gi_block_tool_fail=gi_block_tool_fail,
        cc_out=cc_out,
        cx_out=cx_out,
        gi_out=gi_out,
        gi_block_out=gi_block_out,
        gi_block_json=gi_block_json,
    )
    print(report, end="" if report.endswith("\n") else "\n")
    return 0 if passed == "yes" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
