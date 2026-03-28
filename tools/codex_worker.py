#!/usr/bin/env python3
"""Global Codex CLI wrapper."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from router_common import (
    TIMEOUT_EXIT_CODE,
    append_jsonl,
    backoff_delay_sec,
    child_env,
    copy_if_exists,
    looks_transient_failure,
    make_run_id,
    now_iso,
    read_text,
    resolve_positive_int,
    resolve_timeout_sec,
    resolve_trace_id,
    run_capture_command,
)


SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "codex_runs"
MODEL_RE = re.compile(r"^model:\s*(.+)$", re.MULTILINE)
PROVIDER_RE = re.compile(r"^provider:\s*(.+)$", re.MULTILINE)


def usage() -> str:
    return 'Usage: codex_worker.sh [-m model] [-C dir] [--json] [--output-schema \'<json>\'] [--output-last-message] "<prompt>"'


def parse_args(argv: list[str]) -> tuple[str, str, bool, str, bool, str]:
    model = ""
    workdir = ""
    json_mode = False
    output_schema = ""
    save_last_message = False
    prompt_parts: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"-h", "--help"}:
            print(usage())
            raise SystemExit(0)
        if arg == "-m":
            index += 1
            if index >= len(argv):
                print(usage(), file=sys.stderr)
                raise SystemExit(1)
            model = argv[index]
        elif arg == "-C":
            index += 1
            if index >= len(argv):
                print(usage(), file=sys.stderr)
                raise SystemExit(1)
            workdir = argv[index]
        elif arg == "--json":
            json_mode = True
        elif arg == "--output-schema":
            index += 1
            if index >= len(argv):
                print(usage(), file=sys.stderr)
                raise SystemExit(1)
            output_schema = argv[index]
        elif arg == "--output-last-message":
            save_last_message = True
        else:
            prompt_parts.append(arg)
        index += 1

    if not prompt_parts:
        print(usage(), file=sys.stderr)
        raise SystemExit(1)
    return model, workdir, json_mode, output_schema, save_last_message, " ".join(prompt_parts)


def first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def retry_reason(*, exit_code: int, timed_out: bool, stdout_text: str, stderr_text: str) -> str | None:
    if timed_out:
        return "subprocess timed out"
    if exit_code != 0 and looks_transient_failure(stderr_text, stdout_text):
        return "transient upstream failure"
    return None


def main(argv: list[str]) -> int:
    model, workdir, json_mode, output_schema, _save_last_message, task_prompt = parse_args(argv)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = resolve_trace_id()
    timeout_sec = resolve_timeout_sec(
        "LLM_ROUTER_CODEX_TIMEOUT_SEC",
        "LLM_ROUTER_WORKER_TIMEOUT_SEC",
        "LLM_ROUTER_TIMEOUT_SEC",
        default_sec=3600,
    )
    retry_count = resolve_positive_int(
        "LLM_ROUTER_CODEX_RETRY_COUNT",
        "LLM_ROUTER_RETRY_COUNT",
        default_value=2,
    )
    backoff_sec = resolve_positive_int(
        "LLM_ROUTER_CODEX_BACKOFF_SEC",
        "LLM_ROUTER_BACKOFF_SEC",
        default_value=3,
    )
    max_attempts = retry_count + 1

    run_id = make_run_id()
    run_stdout = RUNS_DIR / f"codex_stdout.{run_id}.txt"
    run_stderr = RUNS_DIR / f"codex_stderr.{run_id}.txt"
    run_last = RUNS_DIR / f"codex_last_message.{run_id}.md"

    print("=== Codex Worker Started ===", file=sys.stderr)
    print(f"Trace ID: {trace_id}", file=sys.stderr)
    print(f"Task: {task_prompt}", file=sys.stderr)
    print(f"Time: {now_iso()}", file=sys.stderr)
    print(f"Run ID: {run_id}", file=sys.stderr)

    cmd = ["codex", "exec", "--full-auto", "--skip-git-repo-check", "--sandbox", "workspace-write"]
    if model:
        cmd.extend(["-m", model])
    if workdir:
        cmd.extend(["-C", workdir])
    if json_mode:
        cmd.append("--json")
    if output_schema:
        cmd.extend(["--output-schema", output_schema])
    cmd.extend(["-o", str(run_last), task_prompt])

    exit_code = 1
    timed_out = False
    attempt_count = 0
    stderr_text = ""
    stdout_text = ""

    for attempt in range(1, max_attempts + 1):
        attempt_count = attempt
        print(f"Attempt: {attempt}/{max_attempts}", file=sys.stderr)
        exit_code = run_capture_command(
            cmd,
            stdout_file=run_stdout,
            stderr_file=run_stderr,
            cwd=None,
            timeout_sec=timeout_sec,
            env=child_env(trace_id),
        )
        timed_out = exit_code == TIMEOUT_EXIT_CODE
        stderr_text = read_text(run_stderr)
        stdout_text = read_text(run_stdout)

        reason = retry_reason(
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
        )
        if reason is None or attempt == max_attempts:
            break
        delay_sec = backoff_delay_sec(attempt, backoff_sec)
        print(
            f"Retrying after {reason}; sleeping {delay_sec}s before attempt {attempt + 1}/{max_attempts}.",
            file=sys.stderr,
        )
        time.sleep(delay_sec)

    detected_model = first_match(MODEL_RE, stderr_text)
    detected_provider = first_match(PROVIDER_RE, stderr_text)

    copy_if_exists(run_stdout, SCRIPT_DIR / "codex_stdout.txt")
    copy_if_exists(run_stderr, SCRIPT_DIR / "codex_stderr.txt")
    copy_if_exists(run_last, SCRIPT_DIR / "codex_last_message.md")

    append_jsonl(
        RUNS_DIR / "manifest.jsonl",
        {
            "run_id": run_id,
            "trace_id": trace_id,
            "task": task_prompt,
            "requested_model": model,
            "detected_model": detected_model,
            "detected_provider": detected_provider,
            "stdout_file": str(run_stdout),
            "stderr_file": str(run_stderr),
            "last_message_file": str(run_last),
            "exit_code": exit_code,
            "attempt_count": attempt_count,
            "retry_count": max(attempt_count - 1, 0),
            "timed_out": timed_out,
            "timeout_sec": timeout_sec,
            "finished_at": now_iso(),
        },
    )

    if stdout_text and not timed_out:
        print(stdout_text, end="" if stdout_text.endswith("\n") else "\n")
    if timed_out:
        print(f"ERROR: Codex worker timed out after {timeout_sec}s.", file=sys.stderr)
    print(f"=== Codex Worker Finished (exit: {exit_code}, run: {run_id}) ===", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
