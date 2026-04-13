#!/usr/bin/env python3
"""Tests for collab CLI."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def run_collab(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    script = str(Path(__file__).resolve().parent / "collab.py")
    return subprocess.run(
        ["python3", script, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_init_creates_collab_directory():
    with tempfile.TemporaryDirectory() as tmp:
        result = run_collab("init", cwd=tmp)
        assert result.returncode == 0
        assert (Path(tmp) / ".collab" / "collab.db").exists()


def test_join_and_status():
    with tempfile.TemporaryDirectory() as tmp:
        run_collab("init", cwd=tmp)
        run_collab("join", "--name", "claude-code", "--role", "planner", cwd=tmp)

        result = run_collab("status", cwd=tmp)
        assert result.returncode == 0
        assert "claude-code" in result.stdout
        assert "planner" in result.stdout


def test_send_and_check():
    with tempfile.TemporaryDirectory() as tmp:
        run_collab("init", cwd=tmp)
        run_collab("join", "--name", "claude-code", "--role", "planner", cwd=tmp)
        run_collab("join", "--name", "codex", "--role", "implementer", cwd=tmp)

        run_collab("send", "--name", "codex", "Hello from Codex", cwd=tmp)
        result = run_collab("check", "--name", "claude-code", cwd=tmp)
        assert result.returncode == 0
        assert "Hello from Codex" in result.stdout


def test_send_and_log():
    with tempfile.TemporaryDirectory() as tmp:
        run_collab("init", cwd=tmp)
        run_collab("join", "--name", "codex", "--role", "implementer", cwd=tmp)
        run_collab("join", "--name", "claude-code", "--role", "planner", cwd=tmp)
        run_collab("send", "--name", "codex", "First message", cwd=tmp)
        run_collab("send", "--name", "claude-code", "Second message", cwd=tmp)

        result = run_collab("log", cwd=tmp)
        assert result.returncode == 0
        assert "First message" in result.stdout
        assert "Second message" in result.stdout


def test_lock_and_unlock():
    with tempfile.TemporaryDirectory() as tmp:
        run_collab("init", cwd=tmp)
        run_collab("join", "--name", "codex", "--role", "implementer", cwd=tmp)

        run_collab("lock", "src/api/routes.ts", "--name", "codex", cwd=tmp)
        result = run_collab("locks", cwd=tmp)
        assert "src/api/routes.ts" in result.stdout
        assert "codex" in result.stdout

        run_collab("unlock", "src/api/routes.ts", "--name", "codex", cwd=tmp)
        result2 = run_collab("locks", cwd=tmp)
        assert "No active locks" in result2.stdout or "src/api/routes.ts" not in result2.stdout


def test_config_set_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        run_collab("init", cwd=tmp)
        run_collab("config", "set", "purge_threshold", "30", cwd=tmp)

        result = run_collab("config", "get", "purge_threshold", cwd=tmp)
        assert "30" in result.stdout


def test_help_prints_overview():
    result = run_collab("help")
    assert result.returncode == 0
    assert "Smart Team Collaboration" in result.stdout


def test_no_args_prints_usage():
    result = run_collab()
    assert result.returncode != 0 or "Usage" in result.stdout or "usage" in result.stderr


def test_send_rejects_unregistered_sender():
    with tempfile.TemporaryDirectory() as tmp:
        run_collab("init", cwd=tmp)
        result = run_collab("send", "--name", "ghost", "Hello", cwd=tmp)
        assert result.returncode == 1
        assert "not a registered session" in result.stderr
