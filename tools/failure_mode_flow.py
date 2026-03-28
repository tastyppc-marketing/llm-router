#!/usr/bin/env python3
"""Sequential GI -> CC -> CX planning flow."""

from __future__ import annotations

import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from router_common import (
    TIMEOUT_EXIT_CODE,
    append_jsonl,
    child_env,
    make_run_id,
    now_iso,
    read_text,
    resolve_timeout_sec,
    resolve_trace_id,
    run_capture_command,
    write_text,
)


SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "failure_mode_flow_runs"
RUN_ID_RE = re.compile(r"^Run ID: (.+)$", re.MULTILINE)


@dataclass
class StepFailure(RuntimeError):
    label: str
    exit_code: int
    stderr_file: Path
    reason: str


def usage() -> str:
    return 'Usage: failure_mode_flow.sh [-C dir] [--accept-defaults] "<task prompt>"'


def parse_args(argv: list[str]) -> tuple[str, bool, str]:
    workdir = ""
    accept_defaults = False
    prompt_parts: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"-h", "--help"}:
            print(usage())
            raise SystemExit(0)
        if arg == "-C":
            index += 1
            if index >= len(argv):
                print(usage(), file=sys.stderr)
                raise SystemExit(1)
            workdir = argv[index]
        elif arg == "--accept-defaults":
            accept_defaults = True
        else:
            prompt_parts.append(arg)
        index += 1

    if not prompt_parts:
        print(usage(), file=sys.stderr)
        raise SystemExit(1)
    return workdir, accept_defaults, " ".join(prompt_parts)


def extract_run_id(stderr_text: str) -> str:
    match = RUN_ID_RE.search(stderr_text)
    return match.group(1).strip() if match else ""


def run_wrapper(
    *,
    label: str,
    wrapper: Path,
    model: str,
    prompt: str,
    flow_dir: Path,
    workdir: str,
    timeout_sec: int,
    trace_id: str,
) -> str:
    stdout_file = flow_dir / f"{label}.stdout.txt"
    stderr_file = flow_dir / f"{label}.stderr.txt"

    cmd = [str(wrapper)]
    if model:
        cmd.extend(["-m", model])
    if workdir:
        cmd.extend(["-C", workdir])
    cmd.append(prompt)

    exit_code = run_capture_command(
        cmd,
        stdout_file=stdout_file,
        stderr_file=stderr_file,
        timeout_sec=timeout_sec,
        env=child_env(trace_id),
    )
    if exit_code != 0:
        if exit_code == TIMEOUT_EXIT_CODE:
            reason = f"Step '{label}' timed out after {timeout_sec}s. See {stderr_file}."
        else:
            reason = f"Step '{label}' failed with exit {exit_code}. See {stderr_file}."
        raise StepFailure(label=label, exit_code=exit_code, stderr_file=stderr_file, reason=reason)
    return extract_run_id(read_text(stderr_file))


def write_manifest(
    *,
    flow_id: str,
    task_prompt: str,
    workdir: str,
    accept_defaults: bool,
    gi_run_id: str,
    cc_run_id: str,
    cx_run_id: str,
    status: str,
    report_file: Path,
    trace_id: str,
) -> None:
    append_jsonl(
        RUNS_DIR / "manifest.jsonl",
        {
            "flow_id": flow_id,
            "trace_id": trace_id,
            "task": task_prompt,
            "workdir": workdir,
            "accept_defaults": accept_defaults,
            "gi_run_id": gi_run_id,
            "cc_run_id": cc_run_id,
            "cx_run_id": cx_run_id,
            "status": status,
            "report_file": str(report_file),
            "finished_at": now_iso(),
        },
    )


def render_report(report_file: Path, trace_id: str, sections: list[tuple[str, str]]) -> str:
    parts = ["# Failure-Mode Flow Report", "", f"- trace_id: `{trace_id}`", ""]
    for title, body in sections:
        parts.append(f"## {title}")
        parts.append("")
        parts.append(body.rstrip())
        parts.append("")
    report = "\n".join(parts).rstrip() + "\n"
    write_text(report_file, report)
    return report


def main(argv: list[str]) -> int:
    workdir, accept_defaults, task_prompt = parse_args(argv)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = resolve_trace_id()
    step_timeout_sec = resolve_timeout_sec(
        "LLM_ROUTER_FLOW_STEP_TIMEOUT_SEC",
        "LLM_ROUTER_WORKER_TIMEOUT_SEC",
        "LLM_ROUTER_TIMEOUT_SEC",
        default_sec=4500,
    )

    flow_id = make_run_id()
    flow_dir = RUNS_DIR / flow_id
    flow_dir.mkdir(parents=True, exist_ok=True)
    report_file = flow_dir / "report.md"

    gi_run_id = ""
    cc_run_id = ""
    cx_run_id = ""

    gi_prompt = textwrap.dedent(
        f"""\
        Act as GI-Mapper.

        Task:
        {task_prompt}

        Instructions:
        - Do not code.
        - Start with either `No blocking questions` or `Blocking questions (max 5)` with proposed defaults.
        - Ask only questions that materially change correctness, scope, UX, or irreversible behavior.
        - Then output `Map` with 3-5 bullets and `Handoff` with the best next lane.
        - Keep it concise.
        """
    )
    try:
        gi_run_id = run_wrapper(
            label="gi",
            wrapper=SCRIPT_DIR / "gemini_worker.sh",
            model="gemini-2.5-pro",
            prompt=gi_prompt,
            flow_dir=flow_dir,
            workdir=workdir,
            timeout_sec=step_timeout_sec,
            trace_id=trace_id,
        )
        gi_output = read_text(flow_dir / "gi.stdout.txt")

        if "Blocking questions" in gi_output and not accept_defaults:
            cc_prompt = textwrap.dedent(
                f"""\
                Act as CC-Diagnostician.

                Another agent found these raw blocker questions plus defaults:

                {gi_output}

                Instructions:
                - Rewrite only the blocker questions into the clearest possible user-facing wording.
                - Keep them concise and easy to answer.
                - Keep the proposed defaults.
                - Output only `Blocking questions (max 5)` followed by the rewritten questions.
                """
            )
            cc_run_id = run_wrapper(
                label="cc",
                wrapper=SCRIPT_DIR / "claude_worker.sh",
                model="sonnet",
                prompt=cc_prompt,
                flow_dir=flow_dir,
                workdir=workdir,
                timeout_sec=step_timeout_sec,
                trace_id=trace_id,
            )
            cc_output = read_text(flow_dir / "cc.stdout.txt")
            report = render_report(
                report_file,
                trace_id,
                [("GI-Mapper", gi_output), ("CC-Diagnostician", cc_output)],
            )
            write_manifest(
                flow_id=flow_id,
                task_prompt=task_prompt,
                workdir=workdir,
                accept_defaults=accept_defaults,
                gi_run_id=gi_run_id,
                cc_run_id=cc_run_id,
                cx_run_id=cx_run_id,
                status="needs_user_input",
                report_file=report_file,
                trace_id=trace_id,
            )
            print(report, end="")
            return 2

        cc_prompt = textwrap.dedent(
            f"""\
            Act as CC-Diagnostician.

            GI-Mapper output:

            {gi_output}

            Instructions:
            - If GI-Mapper listed blocker questions, assume the proposed defaults are accepted for this planning run.
            - Do not code.
            - Start with either `No blocking questions` or `Blocking questions (max 5)`.
            - Then output `Decisions` with accepted defaults or clarified rules.
            - Then output `Invariants` with 3-5 bullets.
            - Then output `Handoff` with the best next lane.
            - Keep it concise.
            """
        )
        cc_run_id = run_wrapper(
            label="cc",
            wrapper=SCRIPT_DIR / "claude_worker.sh",
            model="sonnet",
            prompt=cc_prompt,
            flow_dir=flow_dir,
            workdir=workdir,
            timeout_sec=step_timeout_sec,
            trace_id=trace_id,
        )
        cc_output = read_text(flow_dir / "cc.stdout.txt")

        cx_prompt = textwrap.dedent(
            f"""\
            Act as CX-Executor.

            Task:
            {task_prompt}

            GI-Mapper output:

            {gi_output}

            CC-Diagnostician output:

            {cc_output}

            Instructions:
            - Do not code.
            - Decide whether execution can proceed.
            - Start with either `No blocking questions` or `Blocking questions (max 5)`.
            - Then output `Execution plan` with 3-5 concise bullets.
            - Keep it concise.
            """
        )
        cx_run_id = run_wrapper(
            label="cx",
            wrapper=SCRIPT_DIR / "codex_worker.sh",
            model="",
            prompt=cx_prompt,
            flow_dir=flow_dir,
            workdir=workdir,
            timeout_sec=step_timeout_sec,
            trace_id=trace_id,
        )
        cx_output = read_text(flow_dir / "cx.stdout.txt")

        report = render_report(
            report_file,
            trace_id,
            [("GI-Mapper", gi_output), ("CC-Diagnostician", cc_output), ("CX-Executor", cx_output)],
        )
        write_manifest(
            flow_id=flow_id,
            task_prompt=task_prompt,
            workdir=workdir,
            accept_defaults=accept_defaults,
            gi_run_id=gi_run_id,
            cc_run_id=cc_run_id,
            cx_run_id=cx_run_id,
            status="planned",
            report_file=report_file,
            trace_id=trace_id,
        )
        print(report, end="")
        return 0
    except StepFailure as exc:
        sections: list[tuple[str, str]] = []
        for title, name in (("GI-Mapper", "gi"), ("CC-Diagnostician", "cc"), ("CX-Executor", "cx")):
            output = read_text(flow_dir / f"{name}.stdout.txt").strip()
            if output:
                sections.append((title, output))
        sections.append(("Failure", exc.reason))
        report = render_report(report_file, trace_id, sections)
        write_manifest(
            flow_id=flow_id,
            task_prompt=task_prompt,
            workdir=workdir,
            accept_defaults=accept_defaults,
            gi_run_id=gi_run_id,
            cc_run_id=cc_run_id,
            cx_run_id=cx_run_id,
            status="timed_out" if exc.exit_code == TIMEOUT_EXIT_CODE else "failed",
            report_file=report_file,
            trace_id=trace_id,
        )
        print(exc.reason, file=sys.stderr)
        print(report, end="")
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
