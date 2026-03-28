#!/usr/bin/env python3
"""Interactive Codex -> Claude /smart-team bridge."""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from claude_tmux import (
    append_event,
    capture_session,
    kill_session,
    normalize_space,
    read_text,
    send_prompt,
    session_exists,
    start_claude_session,
    wait_for_prompt,
    write_text,
)
from router_common import resolve_trace_id

SCRIPT_DIR = Path(__file__).resolve().parent
RUNS_DIR = SCRIPT_DIR / "codex_smart_team_runs"

STATUS_RE = re.compile(r"^SMART_TEAM_STATUS=(.+)$", re.MULTILINE)
SUMMARY_RE = re.compile(r"^SMART_TEAM_SUMMARY=(.+)$", re.MULTILINE)
CLAUDE_EXIT_RE = re.compile(r"CLAUDE_EXIT=(\d+)")


@dataclass
class ParsedFooter:
    status: str
    summary: str


def usage() -> str:
    return 'Usage: codex_smart_team.sh [-C workdir] [-t timeout_sec] "<task prompt>"'


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, usage=usage())
    parser.add_argument("-C", dest="workdir", default=os.getcwd())
    parser.add_argument("-t", dest="timeout_sec", type=int, default=1800)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("prompt_parts", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.help:
        print(usage())
        raise SystemExit(0)
    if not args.prompt_parts:
        print(usage(), file=sys.stderr)
        raise SystemExit(2)
    args.task_prompt = " ".join(args.prompt_parts)
    return args


def parse_footer(text: str) -> ParsedFooter | None:
    if "SMART_TEAM_DONE" not in text:
        return None
    valid_statuses = {"done", "needs_user_input", "failed"}
    status_candidates = [
        match.group(1).strip()
        for match in STATUS_RE.finditer(text)
        if match.group(1).strip() in valid_statuses
    ]
    if not status_candidates:
        return None
    summary_candidates = [
        match.group(1).strip()
        for match in SUMMARY_RE.finditer(text)
        if "<" not in match.group(1)
    ]
    status = status_candidates[-1]
    summary = summary_candidates[-1] if summary_candidates else "Routed run completed."
    return ParsedFooter(status=status, summary=summary)


def parse_claude_exit(*texts: str) -> int | None:
    for text in texts:
        match = CLAUDE_EXIT_RE.search(text)
        if match:
            return int(match.group(1))
    return None


def count(pattern: str, text: str) -> int:
    if not text:
        return 0
    return len(re.findall(pattern, text, re.MULTILINE))


def prompt_echoed(transcript_clean: str, pane_clean: str, prompt_excerpt: str) -> bool:
    needle = normalize_space(prompt_excerpt)
    if not needle:
        return False
    return needle in normalize_space(transcript_clean) or needle in normalize_space(pane_clean)


def pane_idle(pane_clean: str) -> bool:
    lowered = pane_clean.lower()
    busy_markers = [
        "esc to interrupt",
        "running…",
        "running...",
        "tinkering",
        "flowing",
        "sprouting",
        "thinking",
    ]
    if any(marker in lowered for marker in busy_markers):
        return False
    return "❯" in pane_clean
def build_prompt(task_prompt: str, status_file: Path) -> str:
    escaped_task = task_prompt.replace("\\", "\\\\").replace('"', '\\"')
    status_path = str(status_file)
    return textwrap.dedent(
        f"""\
        /smart-team "{escaped_task}

        Wrapper requirements:
        - Complete the full routed run before responding.
        - If you need user input, stop after the blocking questions and do not keep working in the background.
        - Before your final lead response, use Bash to write these exact lines to {status_path}:
        SMART_TEAM_DONE
        SMART_TEAM_STATUS=<done|needs_user_input|failed>
        SMART_TEAM_SUMMARY=<one concise sentence>
        - If you need user input, write the same footer with SMART_TEAM_STATUS=needs_user_input before asking the user.
        - If the routed run fails, write the same footer with SMART_TEAM_STATUS=failed before your final response.
        - Ensure the final lead response includes these exact lines at the end:
        SMART_TEAM_DONE
        SMART_TEAM_STATUS=<done|needs_user_input|failed>
        SMART_TEAM_SUMMARY=<one concise sentence>
        "
        """
    )


def build_footer_recovery_prompt(status_file: Path) -> str:
    return textwrap.dedent(
        f"""\
        The wrapper is waiting for the machine-readable footer now. Do not continue working.
        Use Bash to write these exact lines to {status_file} and then reply with exactly the same three lines:
        SMART_TEAM_DONE
        SMART_TEAM_STATUS=<done|needs_user_input|failed>
        SMART_TEAM_SUMMARY=<one concise sentence>
        """
    )


def render_report(
    *,
    report_file: Path,
    trace_id: str,
    workdir: str,
    timeout_sec: int,
    status: str,
    summary: str,
    prompt_file: Path,
    claude_debug: Path,
    pane_out: Path,
    transcript_file: Path,
    transcript_clean: Path,
    status_file: Path,
    bridge_log: Path,
    debug_text: str,
    transcript_clean_text: str,
    bridge_events: list[str],
) -> str:
    toolsearch_team_count = count(r"ToolSearchTool: selected .*TeamCreate", debug_text)
    team_create_count = count(r"executePreToolHooks called for tool: TeamCreate", debug_text)
    task_create_count = count(r"executePreToolHooks called for tool: TaskCreate", debug_text)
    task_update_count = count(r"executePreToolHooks called for tool: TaskUpdate", debug_text)
    task_list_count = count(r"executePreToolHooks called for tool: TaskList", debug_text)
    task_get_count = count(r"executePreToolHooks called for tool: TaskGet", debug_text)
    team_delete_count = count(r"executePreToolHooks called for tool: TeamDelete", debug_text)
    tmux_pane_count = count(r"\[TmuxBackend\] Created teammate pane", debug_text)
    ask_user_question_count = count(r"executePreToolHooks called for tool: AskUserQuestion", debug_text)
    smart_team_complete_count = transcript_clean_text.count("/smart-team Complete")
    claude_exit = parse_claude_exit(debug_text, transcript_clean_text)

    transcript_tail = "\n".join(transcript_clean_text.splitlines()[-80:])
    bridge_tail = "\n".join(bridge_events[-20:])
    report = textwrap.dedent(
        f"""\
        # Codex Smart Team Run

        - trace_id: `{trace_id}`
        - workdir: `{workdir}`
        - timeout_sec: `{timeout_sec}`
        - status: `{status}`
        - summary: `{summary}`

        Artifacts:
        - `{prompt_file}`
        - `{claude_debug}`
        - `{pane_out}`
        - `{transcript_file}`
        - `{transcript_clean}`
        - `{status_file}`
        - `{bridge_log}`

        Debug counters:
        - `toolsearch_team={toolsearch_team_count}`
        - `team_create={team_create_count}`
        - `task_create={task_create_count}`
        - `task_update={task_update_count}`
        - `task_list={task_list_count}`
        - `task_get={task_get_count}`
        - `team_delete={team_delete_count}`
        - `tmux_panes={tmux_pane_count}`
        - `ask_user_question={ask_user_question_count}`
        - `smart_team_complete={smart_team_complete_count}`
        - `claude_exit={claude_exit if claude_exit is not None else ""}`

        Bridge events:

        ```
        {bridge_tail}
        ```

        Transcript tail:

        ```
        {transcript_tail}
        ```
        """
    )
    write_text(report_file, report)
    return report


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = resolve_trace_id()

    now = datetime.now()
    pid = os.getpid()
    run_id = f"{now:%Y%m%d-%H%M%S}-{pid}"
    short_id = f"{now:%H%M%S}-{pid}"
    session_name = f"codex_smart_team_{short_id}"
    session_target = f"{session_name}:0.0"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    prompt_file = run_dir / "prompt.txt"
    prompt_send_file = run_dir / "prompt.single-line.txt"
    claude_debug = run_dir / "claude.debug.txt"
    pane_out = run_dir / "pane.txt"
    transcript_file = run_dir / "transcript.txt"
    transcript_clean = run_dir / "transcript.clean.txt"
    report_file = run_dir / "report.md"
    status_file = run_dir / "smart_team_status.txt"
    bridge_log = run_dir / "bridge.events.txt"

    bridge_events: list[str] = []

    prompt_text = build_prompt(args.task_prompt, status_file)
    prompt_single_line = normalize_space(prompt_text) + " "
    write_text(prompt_file, prompt_text)
    write_text(prompt_send_file, prompt_single_line)

    recovery_prompt_file = run_dir / "footer_recovery.single-line.txt"
    recovery_prompt = normalize_space(build_footer_recovery_prompt(status_file)) + " "
    write_text(recovery_prompt_file, recovery_prompt)

    start_claude_session(
        session_name=session_name,
        workdir=args.workdir,
        claude_debug=claude_debug,
        transcript_file=transcript_file,
        sleep_after_exit=120,
    )
    append_event(bridge_events, f"Started tmux session {session_name}.")

    status = "timeout"
    summary = "Timed out before SMART_TEAM_DONE sentinel appeared."
    return_code = 1

    try:
        wait_for_prompt(session_target, pane_out, bridge_events, timeout_sec=45)

        excerpt = prompt_single_line[:200]
        prompt_confirmed = False
        for attempt in range(2):
            send_prompt(session_target, prompt_single_line, bridge_events, "primary")
            confirm_deadline = time.time() + 45
            while time.time() < confirm_deadline:
                _, pane_clean, _, transcript_clean_text = capture_session(
                    session_target=session_target,
                    pane_out=pane_out,
                    transcript_file=transcript_file,
                    transcript_clean=transcript_clean,
                )
                debug_text = read_text(claude_debug)
                smart_team_seen = (
                    "/smart-team" in pane_clean
                    or "/smart-team" in transcript_clean_text
                    or "Starting /smart-team" in pane_clean
                    or "Starting /smart-team" in transcript_clean_text
                    or "Starting the smart-team workflow" in pane_clean
                    or "Starting the smart-team workflow" in transcript_clean_text
                    or "ToolSearchTool: selected" in debug_text
                    or "executePreToolHooks called for tool: TeamCreate" in debug_text
                )
                if prompt_echoed(transcript_clean_text, pane_clean, excerpt) or smart_team_seen:
                    prompt_confirmed = True
                    append_event(bridge_events, "Confirmed /smart-team prompt was ingested by Claude.")
                    break
                time.sleep(1)
            if prompt_confirmed:
                break
            append_event(bridge_events, f"Prompt echo not observed after send attempt {attempt + 1}.")

        if not prompt_confirmed:
            status = "failed"
            summary = "Failed to confirm that the /smart-team prompt was ingested by Claude."
            return_code = 1
        else:
            followup_sent = False
            deadline = time.time() + args.timeout_sec
            return_code = 1

            while time.time() < deadline:
                _, pane_clean, _, transcript_clean_text = capture_session(
                    session_target=session_target,
                    pane_out=pane_out,
                    transcript_file=transcript_file,
                    transcript_clean=transcript_clean,
                )

                debug_text = read_text(claude_debug)
                footer_text = read_text(status_file)
                footer = parse_footer(footer_text) or parse_footer(transcript_clean_text) or parse_footer(pane_clean)

                if footer:
                    status = footer.status
                    summary = footer.summary
                    return_code = 0 if status == "done" else 2 if status == "needs_user_input" else 1
                    append_event(bridge_events, f"Observed machine-readable footer with status={status}.")
                    break

                claude_exit = parse_claude_exit(debug_text, transcript_clean_text, pane_clean)
                team_delete_count = count(r"executePreToolHooks called for tool: TeamDelete", debug_text)
                complete_seen = "/smart-team Complete" in transcript_clean_text or "/smart-team Complete" in pane_clean
                ask_user_question_count = count(r"executePreToolHooks called for tool: AskUserQuestion", debug_text)

                if (team_delete_count > 0 or complete_seen or ask_user_question_count > 0) and not followup_sent and pane_idle(pane_clean):
                    send_prompt(session_target, recovery_prompt, bridge_events, "footer recovery")
                    followup_sent = True
                    deadline = max(deadline, time.time() + 60)
                    continue

                if team_delete_count > 0:
                    status = "done"
                    summary = "Detected completed routed run via TeamDelete in the Claude debug log."
                    return_code = 0
                    append_event(bridge_events, "Marked run done from TeamDelete fallback signal.")
                    break

                if complete_seen and pane_idle(pane_clean):
                    status = "done"
                    summary = "Detected /smart-team completion marker in the terminal transcript."
                    return_code = 0
                    append_event(bridge_events, "Marked run done from /smart-team Complete fallback signal.")
                    break

                if ask_user_question_count > 0 and pane_idle(pane_clean):
                    status = "needs_user_input"
                    summary = "Claude requested user input but did not emit the wrapper footer."
                    return_code = 2
                    append_event(bridge_events, "Marked run as needs_user_input from AskUserQuestion fallback signal.")
                    break

                if claude_exit is not None and claude_exit != 0:
                    status = "failed"
                    summary = f"Claude exited with code {claude_exit} before the wrapper footer was observed."
                    return_code = 1
                    append_event(bridge_events, f"Claude exited early with code {claude_exit}.")
                    break

                time.sleep(5)

            else:
                debug_text = read_text(claude_debug)
                toolsearch_team_count = count(r"ToolSearchTool: selected .*TeamCreate", debug_text)
                team_create_count = count(r"executePreToolHooks called for tool: TeamCreate", debug_text)
                task_create_count = count(r"executePreToolHooks called for tool: TaskCreate", debug_text)
                task_update_count = count(r"executePreToolHooks called for tool: TaskUpdate", debug_text)
                team_delete_count = count(r"executePreToolHooks called for tool: TeamDelete", debug_text)
                tmux_pane_count = count(r"\[TmuxBackend\] Created teammate pane", debug_text)
                if team_delete_count > 0:
                    status = "done"
                    summary = "Detected completed routed run via TeamDelete in the Claude debug log."
                    return_code = 0
                elif team_create_count > 0 or task_create_count > 0 or tmux_pane_count > 0:
                    summary = (
                        "Timed out after routed launch: "
                        f"team_create={team_create_count} task_create={task_create_count} "
                        f"task_update={task_update_count} tmux_panes={tmux_pane_count} "
                        f"team_delete={team_delete_count}."
                    )
                elif toolsearch_team_count > 0:
                    summary = "Timed out after /smart-team selected team tools, before observable team bootstrap."
                return_code = 1
    except Exception as exc:
        status = "failed"
        summary = str(exc)
        return_code = 1
        append_event(bridge_events, f"Bridge raised {exc.__class__.__name__}: {exc}")
    finally:
        _, _, _, transcript_clean_text = capture_session(
            session_target=session_target,
            pane_out=pane_out,
            transcript_file=transcript_file,
            transcript_clean=transcript_clean,
        )
        debug_text = read_text(claude_debug)
        if session_exists(session_name):
            kill_session(session_name)
            append_event(bridge_events, f"Killed tmux session {session_name}.")
        write_text(bridge_log, "\n".join(bridge_events) + ("\n" if bridge_events else ""))
        report = render_report(
            report_file=report_file,
            trace_id=trace_id,
            workdir=args.workdir,
            timeout_sec=args.timeout_sec,
            status=status,
            summary=summary,
            prompt_file=prompt_file,
            claude_debug=claude_debug,
            pane_out=pane_out,
            transcript_file=transcript_file,
            transcript_clean=transcript_clean,
            status_file=status_file,
            bridge_log=bridge_log,
            debug_text=debug_text,
            transcript_clean_text=transcript_clean_text,
            bridge_events=bridge_events,
        )
        print(report, end="" if report.endswith("\n") else "\n")

    return return_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
