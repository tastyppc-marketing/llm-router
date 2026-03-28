#!/usr/bin/env python3
"""Repo-agnostic interactive TeamCreate/TaskCreate/tmux smoke test."""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
RUNS_DIR = SCRIPT_DIR / "router_validation_runs"
TEAMS_ROOT = Path("/home/mjfos/.claude/teams")


@dataclass
class SmokeResult:
    team_name: str
    task_ids: str
    owners_line: str


def usage() -> str:
    return "Usage: router_team_smoke.sh [workdir]"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, usage=usage())
    parser.add_argument("workdir", nargs="?", default="/mnt/c/Users/mjfos")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args(argv)
    if args.help:
        print(usage())
        raise SystemExit(0)
    return args


def build_prompt(team_name: str, status_file: Path) -> str:
    return textwrap.dedent(
        f"""\
        Direct interactive tool smoke test. Work only in team metadata, not project files.

        1. Call TeamCreate to create a temporary team named "{team_name}".
        2. Spawn exactly two background members on that team via Agent with run_in_background=true:
           - researcher
           - qa-tester
        3. Immediately call TaskCreate twice:
           - one validation task for researcher
           - one validation task for qa-tester
        4. Immediately call TaskUpdate so each task has an explicit owner.
        5. The tasks should be trivial and file-read-only:
           - read ~/.claude/teams/{team_name}/config.json
           - send one short status message to the lead
           - then stay idle
        6. Do not edit any project files.
        7. Use TaskList and TaskGet as needed to verify both tasks exist and both owners are set correctly.
        8. Do not delete the team yet.
        9. Before your final response, use Bash to write these exact lines to {status_file}:
           TEAM_NAME={team_name}
           TASK_IDS=<comma-separated task ids>
           OWNERS=<taskId:owner,taskId:owner>
           DONE
        10. Final response format exactly:
            TEAM_NAME={team_name}
            TASK_IDS=<comma-separated task ids>
            OWNERS=<taskId:owner,taskId:owner>
            DONE
        """
    )


def build_cleanup_prompt(team_name: str, cleanup_status_file: Path) -> str:
    return textwrap.dedent(
        f"""\
        Delete the temporary team named "{team_name}" with TeamDelete.
        Before your response, use Bash to write CLEANED to {cleanup_status_file}.
        Then reply with exactly:
        CLEANED
        """
    )


def parse_smoke_result(text: str, expected_team_name: str) -> SmokeResult | None:
    normalized = normalize_space(text)
    if "DONE" not in normalized:
        return None
    if f"TEAM_NAME={expected_team_name}" not in normalized:
        return None
    task_match = re.search(r"TASK_IDS=([0-9]+(?:,[0-9]+)*)", normalized)
    owners_match = re.search(r"OWNERS=([0-9]+:[A-Za-z0-9_-]+(?:,[0-9]+:[A-Za-z0-9_-]+)+)", normalized)
    if not task_match or not owners_match:
        return None
    owners_line = owners_match.group(1)
    if "researcher" not in owners_line or "qa-tester" not in owners_line:
        return None
    return SmokeResult(
        team_name=expected_team_name,
        task_ids=task_match.group(1),
        owners_line=owners_line,
    )


def snapshot_team_artifacts(
    *,
    team_name: str,
    config_snapshot: Path,
    inbox_snapshot: Path,
    team_files_snapshot: Path,
) -> None:
    team_dir = TEAMS_ROOT / team_name
    config_path = team_dir / "config.json"
    inbox_path = team_dir / "inboxes" / "team-lead.json"

    if config_path.exists():
        shutil.copyfile(config_path, config_snapshot)
    if inbox_path.exists():
        shutil.copyfile(inbox_path, inbox_snapshot)
    if team_dir.exists():
        files = sorted(str(path) for path in team_dir.rglob("*") if path.is_file())
        write_text(team_files_snapshot, "\n".join(files) + ("\n" if files else ""))


def load_member_fields(config_snapshot: Path, member_name: str) -> tuple[str, str]:
    if not config_snapshot.exists():
        return "", ""
    try:
        data = json.loads(read_text(config_snapshot))
    except json.JSONDecodeError:
        return "", ""
    for member in data.get("members", []):
        if member.get("name") == member_name:
            return str(member.get("backendType", "") or ""), str(member.get("tmuxPaneId", "") or "")
    return "", ""


def render_report(
    *,
    report_file: Path,
    trace_id: str,
    team_name: str,
    workdir: str,
    team_created: str,
    task_ids: str,
    task_create_pass: str,
    task_update_pass: str,
    owners_line: str,
    researcher_backend: str,
    qa_backend: str,
    researcher_pane: str,
    qa_pane: str,
    tmux_backend_pass: str,
    cleanup_pass: str,
    prompt_file: Path,
    claude_debug: Path,
    pane_out: Path,
    config_snapshot: Path,
    inbox_snapshot: Path,
    team_files_snapshot: Path,
    bridge_log: Path,
) -> str:
    report = textwrap.dedent(
        f"""\
        # Router Team Smoke

        - trace_id: `{trace_id}`
        - team_name: `{team_name}`
        - workdir: `{workdir}`
        - team_created: `{team_created}`
        - task_ids: `{task_ids}`
        - task_create_pass: `{task_create_pass}`
        - task_update_pass: `{task_update_pass}`
        - owners: `{owners_line}`
        - researcher_backend: `{researcher_backend}`
        - qa_backend: `{qa_backend}`
        - researcher_pane: `{researcher_pane}`
        - qa_pane: `{qa_pane}`
        - tmux_backend_pass: `{tmux_backend_pass}`
        - cleanup_pass: `{cleanup_pass}`

        Artifacts:
        - `{prompt_file}`
        - `{claude_debug}`
        - `{pane_out}`
        - `{config_snapshot}`
        - `{inbox_snapshot}`
        - `{team_files_snapshot}`
        - `{bridge_log}`
        """
    )
    write_text(report_file, report)
    return report


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    trace_id = resolve_trace_id()

    now = datetime.now()
    pid = __import__("os").getpid()
    run_id = f"{now:%Y%m%d-%H%M%S}-{pid}"
    short_id = f"{now:%H%M%S}-{pid}"
    team_name = f"router-smoke-{short_id}"
    session_name = f"router_team_smoke_{short_id}"
    session_target = f"{session_name}:0.0"
    run_dir = RUNS_DIR / f"team-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    prompt_file = run_dir / "prompt.txt"
    claude_debug = run_dir / "claude.debug.txt"
    pane_out = run_dir / "pane.txt"
    transcript_file = run_dir / "transcript.txt"
    transcript_clean = run_dir / "transcript.clean.txt"
    config_snapshot = run_dir / "config.json"
    inbox_snapshot = run_dir / "team-lead.inbox.json"
    team_files_snapshot = run_dir / "team-files.txt"
    report_file = run_dir / "report.md"
    status_file = run_dir / "result.txt"
    cleanup_prompt_file = run_dir / "cleanup.txt"
    cleanup_status_file = run_dir / "cleanup.txt.status"
    bridge_log = run_dir / "bridge.events.txt"

    bridge_events: list[str] = []
    prompt_text = build_prompt(team_name, status_file)
    cleanup_prompt = build_cleanup_prompt(team_name, cleanup_status_file)
    prompt_single_line = normalize_space(prompt_text) + " "
    cleanup_single_line = normalize_space(cleanup_prompt) + " "
    write_text(prompt_file, prompt_text)
    write_text(cleanup_prompt_file, cleanup_prompt)

    team_created = "no"
    task_ids = ""
    task_create_pass = "no"
    task_update_pass = "no"
    owners_line = ""
    researcher_backend = ""
    qa_backend = ""
    researcher_pane = ""
    qa_pane = ""
    tmux_backend_pass = "no"
    cleanup_pass = "no"
    return_code = 1

    start_claude_session(
        session_name=session_name,
        workdir=args.workdir,
        claude_debug=claude_debug,
        transcript_file=transcript_file,
        sleep_after_exit=180,
    )
    append_event(bridge_events, f"Started tmux session {session_name}.")

    try:
        wait_for_prompt(session_target, pane_out, bridge_events, timeout_sec=45)
        send_prompt(session_target, prompt_single_line, bridge_events, "team smoke")

        excerpt = prompt_single_line[:180]
        prompt_confirmed = False
        confirm_deadline = time.time() + 45
        while time.time() < confirm_deadline:
            _, pane_clean, _, transcript_clean_text = capture_session(
                session_target=session_target,
                pane_out=pane_out,
                transcript_file=transcript_file,
                transcript_clean=transcript_clean,
                start=-1200,
            )
            debug_text = read_text(claude_debug)
            if (
                excerpt in normalize_space(transcript_clean_text)
                or excerpt in normalize_space(pane_clean)
                or "executePreToolHooks called for tool: TeamCreate" in debug_text
                or "ToolSearchTool: selected" in debug_text
            ):
                prompt_confirmed = True
                append_event(bridge_events, "Confirmed team smoke prompt was ingested by Claude.")
                break
            time.sleep(1)
        if not prompt_confirmed:
            raise RuntimeError("Failed to confirm that the team smoke prompt was ingested by Claude.")

        smoke_result: SmokeResult | None = None
        deadline = time.time() + 120
        while time.time() < deadline:
            _, pane_clean, _, transcript_clean_text = capture_session(
                session_target=session_target,
                pane_out=pane_out,
                transcript_file=transcript_file,
                transcript_clean=transcript_clean,
                start=-1600,
            )
            snapshot_team_artifacts(
                team_name=team_name,
                config_snapshot=config_snapshot,
                inbox_snapshot=inbox_snapshot,
                team_files_snapshot=team_files_snapshot,
            )
            smoke_result = (
                parse_smoke_result(read_text(status_file), team_name)
                or parse_smoke_result(transcript_clean_text, team_name)
                or parse_smoke_result(pane_clean, team_name)
            )
            if smoke_result:
                append_event(bridge_events, "Observed concrete task/owner result from Claude.")
                break
            time.sleep(2)

        snapshot_team_artifacts(
            team_name=team_name,
            config_snapshot=config_snapshot,
            inbox_snapshot=inbox_snapshot,
            team_files_snapshot=team_files_snapshot,
        )

        if config_snapshot.exists():
            team_created = "yes"
        researcher_backend, researcher_pane = load_member_fields(config_snapshot, "researcher")
        qa_backend, qa_pane = load_member_fields(config_snapshot, "qa-tester")

        if smoke_result:
            task_ids = smoke_result.task_ids
            owners_line = smoke_result.owners_line
            team_created = "yes"

        if (
            researcher_backend == "tmux"
            and qa_backend == "tmux"
            and researcher_pane
            and qa_pane
            and researcher_pane != "in-process"
            and qa_pane != "in-process"
        ):
            tmux_backend_pass = "yes"

        if task_ids:
            task_create_pass = "yes"
        if owners_line and "researcher" in owners_line and "qa-tester" in owners_line:
            task_update_pass = "yes"

        if team_created == "yes":
            send_prompt(session_target, cleanup_single_line, bridge_events, "cleanup")
            cleanup_deadline = time.time() + 60
            while time.time() < cleanup_deadline:
                capture_session(
                    session_target=session_target,
                    pane_out=pane_out,
                    transcript_file=transcript_file,
                    transcript_clean=transcript_clean,
                    start=-1600,
                )
                if normalize_space(read_text(cleanup_status_file)) == "CLEANED" or not (TEAMS_ROOT / team_name).exists():
                    cleanup_pass = "yes"
                    append_event(bridge_events, "Observed cleanup completion.")
                    break
                time.sleep(2)

        return_code = 0
    except Exception as exc:
        append_event(bridge_events, f"Bridge raised {exc.__class__.__name__}: {exc}")
        return_code = 1
    finally:
        capture_session(
            session_target=session_target,
            pane_out=pane_out,
            transcript_file=transcript_file,
            transcript_clean=transcript_clean,
            start=-1600,
        )
        if session_exists(session_name):
            kill_session(session_name)
            append_event(bridge_events, f"Killed tmux session {session_name}.")
        write_text(bridge_log, "\n".join(bridge_events) + ("\n" if bridge_events else ""))
        report = render_report(
            report_file=report_file,
            trace_id=trace_id,
            team_name=team_name,
            workdir=args.workdir,
            team_created=team_created,
            task_ids=task_ids,
            task_create_pass=task_create_pass,
            task_update_pass=task_update_pass,
            owners_line=owners_line,
            researcher_backend=researcher_backend,
            qa_backend=qa_backend,
            researcher_pane=researcher_pane,
            qa_pane=qa_pane,
            tmux_backend_pass=tmux_backend_pass,
            cleanup_pass=cleanup_pass,
            prompt_file=prompt_file,
            claude_debug=claude_debug,
            pane_out=pane_out,
            config_snapshot=config_snapshot,
            inbox_snapshot=inbox_snapshot,
            team_files_snapshot=team_files_snapshot,
            bridge_log=bridge_log,
        )
        print(report, end="" if report.endswith("\n") else "\n")

    if (
        team_created != "yes"
        or task_create_pass != "yes"
        or task_update_pass != "yes"
        or tmux_backend_pass != "yes"
        or cleanup_pass != "yes"
    ):
        return 1
    return return_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
