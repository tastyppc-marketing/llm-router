#!/usr/bin/env python3
"""Shared Claude-in-tmux orchestration helpers."""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from router_common import format_command, resolve_timeout_sec


ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\][^\a]*(?:\a|\x1b\\)")


def run_cmd(
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
    timeout_sec: int | None = None,
) -> subprocess.CompletedProcess[str]:
    effective_timeout = timeout_sec or resolve_timeout_sec(
        "LLM_ROUTER_TMUX_TIMEOUT_SEC",
        "LLM_ROUTER_LOCAL_TIMEOUT_SEC",
        default_sec=20,
    )
    try:
        return subprocess.run(
            args,
            check=check,
            text=text,
            capture_output=True,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Local command timed out after {effective_timeout}s: {format_command(args)}"
        ) from exc


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_cmd(["tmux", *args], check=check)


def tmux_capture(target: str, start: int = -3000) -> str:
    proc = tmux("capture-pane", "-t", target, "-J", "-p", "-S", str(start), check=False)
    return proc.stdout


def clean_terminal_text(text: str) -> str:
    cleaned = ANSI_CSI_RE.sub("", text)
    cleaned = ANSI_OSC_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r", "\n")
    return cleaned


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def append_event(events: list[str], message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    events.append(f"{stamp} {message}")


def shell_quote(path: Path | str) -> str:
    return shlex.quote(str(path))


def session_exists(session_name: str) -> bool:
    return tmux("has-session", "-t", session_name, check=False).returncode == 0


def kill_session(session_name: str) -> None:
    tmux("kill-session", "-t", session_name, check=False)


def start_claude_session(
    *,
    session_name: str,
    workdir: str,
    claude_debug: Path,
    transcript_file: Path,
    sleep_after_exit: int = 120,
    width: int = 240,
    height: int = 80,
) -> str:
    session_target = f"{session_name}:0.0"
    kill_session(session_name)
    claude_cmd = (
        f"cd {shell_quote(workdir)} && "
        f"claude --permission-mode bypassPermissions --debug-file {shell_quote(claude_debug)}; "
        'printf "CLAUDE_EXIT=%s\\n" "$?"; '
        f"sleep {sleep_after_exit}"
    )
    tmux(
        "new-session",
        "-d",
        "-x",
        str(width),
        "-y",
        str(height),
        "-s",
        session_name,
        f"bash -lc {shlex.quote(claude_cmd)}",
    )
    tmux("pipe-pane", "-o", "-t", session_target, f"cat >> {shlex.quote(str(transcript_file))}")
    return session_target


def wait_for_prompt(session_target: str, pane_out: Path, events: list[str], timeout_sec: int) -> None:
    trust_sent = False
    bypass_sent = False
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        pane_text = tmux_capture(session_target, start=-250)
        write_text(pane_out, pane_text)
        clean_text = clean_terminal_text(pane_text)

        if (
            "Quick safety check" in clean_text
            and "Yes, I trust this folder" in clean_text
            and not trust_sent
        ):
            tmux("send-keys", "-t", session_target, "-l", "1")
            time.sleep(0.5)
            tmux("send-keys", "-t", session_target, "C-m")
            trust_sent = True
            append_event(events, "Accepted Claude workspace trust prompt.")
            time.sleep(2)
            continue

        if "Yes, I accept" in clean_text and not bypass_sent:
            tmux("send-keys", "-t", session_target, "-l", "2")
            time.sleep(0.5)
            tmux("send-keys", "-t", session_target, "C-m")
            bypass_sent = True
            append_event(events, "Accepted Claude bypass-permissions confirmation prompt.")
            time.sleep(2)
            continue

        prompt_ready = "Claude Code" in clean_text and "❯" in clean_text and "Quick safety check" not in clean_text
        if prompt_ready:
            return
        time.sleep(1)

    raise RuntimeError("Timed out waiting for Claude interactive prompt.")


def send_prompt(
    session_target: str,
    prompt_text: str,
    events: list[str],
    label: str,
    *,
    chunk_size: int = 180,
    chunk_sleep: float = 0.04,
) -> None:
    for index in range(0, len(prompt_text), chunk_size):
        tmux("send-keys", "-t", session_target, "-l", "--", prompt_text[index : index + chunk_size])
        time.sleep(chunk_sleep)
    time.sleep(1)
    tmux("send-keys", "-t", session_target, "C-m")
    append_event(events, f"Sent {label} prompt to Claude.")


def capture_session(
    *,
    session_target: str,
    pane_out: Path,
    transcript_file: Path,
    transcript_clean: Path,
    start: int = -3000,
) -> tuple[str, str, str, str]:
    pane_text = tmux_capture(session_target, start=start)
    write_text(pane_out, pane_text)
    pane_clean = clean_terminal_text(pane_text)
    transcript_text = read_text(transcript_file)
    transcript_clean_text = clean_terminal_text(transcript_text)
    write_text(transcript_clean, transcript_clean_text)
    return pane_text, pane_clean, transcript_text, transcript_clean_text
