#!/usr/bin/env python3
"""Shared helpers for llm-router Python-backed tools."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


REPORT_FIELD_RE = re.compile(r"^- ([A-Za-z0-9_]+): `(.*)`$")
TIMEOUT_EXIT_CODE = 124
TRANSIENT_FAILURE_RE = re.compile(
    r"(rate limit|too many requests|\b429\b|\b5\d\d\b|temporar(?:ily)? unavailable|overloaded|"
    r"connection reset|connection aborted|connection refused|socket hang up|timed out|timeout|"
    r"network error|eai_again|econnreset|etimedout|try again)",
    re.IGNORECASE,
)


class RouterError(RuntimeError):
    """Base error for llm-router tool failures."""


class RouterTimeoutError(RouterError):
    """Raised when a subprocess exceeds its configured safety bound."""


def format_command(cmd: list[str]) -> str:
    return shlex.join(cmd)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def make_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"


def make_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:12]}"


def resolve_trace_id() -> str:
    return os.environ.get("LLM_ROUTER_TRACE_ID", "").strip() or make_trace_id()


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True)
        handle.write("\n")


def copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def safe_json_loads(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def parse_report_fields(report_file: Path | None) -> dict[str, str]:
    if report_file is None or not report_file.exists():
        return {}
    fields: dict[str, str] = {}
    for line in read_text(report_file).splitlines():
        match = REPORT_FIELD_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def extract_report_field(report_file: Path | None, key: str, default: str = "") -> str:
    return parse_report_fields(report_file).get(key, default)


def latest_report_for_prefix(runs_dir: Path, prefix: str) -> Path | None:
    candidates = sorted(
        runs_dir.glob(f"{prefix}-*/report.md"),
        key=lambda path: (path.stat().st_mtime, str(path)),
        reverse=True,
    )
    return candidates[0] if candidates else None


def resolve_timeout_sec(*env_names: str, default_sec: int) -> int:
    return resolve_positive_int(*env_names, default_value=default_sec)


def resolve_positive_int(*env_names: str, default_value: int) -> int:
    for env_name in env_names:
        raw_value = os.environ.get(env_name, "").strip()
        if not raw_value:
            continue
        try:
            parsed = int(raw_value)
        except ValueError:
            continue
        if parsed > 0:
            return parsed
    return default_value


def child_env(trace_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env["LLM_ROUTER_TRACE_ID"] = trace_id
    return env


def backoff_delay_sec(attempt_number: int, base_delay_sec: int) -> int:
    return base_delay_sec * (2 ** max(attempt_number - 1, 0))


def looks_transient_failure(*texts: str) -> bool:
    combined = "\n".join(text for text in texts if text).strip()
    return bool(combined and TRANSIENT_FAILURE_RE.search(combined))


def timeout_message(cmd: list[str], timeout_sec: int) -> str:
    return f"TIMEOUT after {timeout_sec}s while running: {format_command(cmd)}\n"


def run_console_command(
    cmd: list[str],
    console_file: Path,
    *,
    cwd: str | None = None,
    timeout_sec: int | None = None,
    env: dict[str, str] | None = None,
) -> int:
    console_file.parent.mkdir(parents=True, exist_ok=True)
    with console_file.open("w", encoding="utf-8") as handle:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
            return proc.returncode
        except subprocess.TimeoutExpired as exc:
            if exc.timeout is not None:
                handle.write("\n")
                handle.write(timeout_message(cmd, int(exc.timeout)))
                handle.flush()
            return TIMEOUT_EXIT_CODE


def run_capture_command(
    cmd: list[str],
    *,
    stdout_file: Path,
    stderr_file: Path,
    cwd: str | None = None,
    timeout_sec: int | None = None,
    env: dict[str, str] | None = None,
) -> int:
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    with stdout_file.open("w", encoding="utf-8") as stdout_handle, stderr_file.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
            return proc.returncode
        except subprocess.TimeoutExpired:
            stderr_handle.write(timeout_message(cmd, timeout_sec or 0))
            stderr_handle.flush()
            return TIMEOUT_EXIT_CODE


def stream_stdout_command(
    cmd: list[str],
    *,
    stdout_file: Path,
    stderr_file: Path,
    cwd: str | None = None,
) -> int:
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    with stdout_file.open("w", encoding="utf-8") as stdout_handle, stderr_file.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=stderr_handle,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if chunk == "":
                break
            stdout_handle.write(chunk)
            stdout_handle.flush()
            sys.stdout.write(chunk)
            sys.stdout.flush()
        return proc.wait()


def force_symlink(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            raise IsADirectoryError(f"Cannot replace directory target: {target}")
        target.unlink()
    target.symlink_to(source)
