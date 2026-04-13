# tools/test_collab_integration.py
#!/usr/bin/env python3
"""Integration tests for the full collaboration flow."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def run_collab(*args: str, cwd: str) -> subprocess.CompletedProcess:
    script = str(Path(__file__).resolve().parent / "collab.py")
    return subprocess.run(
        ["python3", script, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_full_collaboration_flow():
    """Simulates a collaboration between two sessions."""
    with tempfile.TemporaryDirectory() as project:
        # 1. Initialize
        result = run_collab("init", cwd=project)
        assert result.returncode == 0
        assert (Path(project) / ".collab" / "collab.db").exists()

        # 2. Two sessions join
        run_collab("join", "--name", "claude-code", "--role", "planner", cwd=project)
        run_collab("join", "--name", "codex", "--role", "implementer", cwd=project)

        # 3. Verify status
        result = run_collab("status", cwd=project)
        assert "claude-code" in result.stdout
        assert "codex" in result.stdout

        # 4. Claude sends a proposal
        run_collab(
            "send", "--name", "claude-code", "--type", "proposal",
            "Let's use PostgreSQL for everything",
            cwd=project,
        )

        # 5. Codex checks and sees it
        result = run_collab("check", "--name", "codex", cwd=project)
        assert "PostgreSQL" in result.stdout

        # 6. Codex disagrees
        run_collab(
            "send", "--name", "codex", "--type", "conflict", "--reply-to", "1",
            "Disagree — Redis is better for sessions",
            cwd=project,
        )

        # 7. Claude checks and sees conflict
        result = run_collab("check", "--name", "claude-code", cwd=project)
        assert "Disagree" in result.stdout

        # 8. File locking
        run_collab("lock", "src/api/routes.ts", "--name", "codex", cwd=project)
        result = run_collab("locks", cwd=project)
        assert "src/api/routes.ts" in result.stdout
        assert "codex" in result.stdout

        # 9. Lock check from other session
        result = run_collab(
            "lock-check", "src/api/routes.ts", "--name", "claude-code", cwd=project
        )
        assert "locked by codex" in result.stdout.lower() or "Warning" in result.stdout

        # 10. Unlock
        run_collab("unlock", "src/api/routes.ts", "--name", "codex", cwd=project)

        # 11. Log shows full conversation
        result = run_collab("log", cwd=project)
        assert "PostgreSQL" in result.stdout
        assert "Redis" in result.stdout

        # 12. Purge
        run_collab("config", "set", "purge_count", "2", cwd=project)
        result = run_collab("purge", cwd=project)
        assert "Archived 2" in result.stdout

        # 13. End
        result = run_collab("end", cwd=project)
        assert result.returncode == 0
        assert "ended" in result.stdout.lower() or "Archive" in result.stdout


def test_collaboration_inject_format():
    """Tests the hook injection format."""
    with tempfile.TemporaryDirectory() as project:
        run_collab("init", cwd=project)
        run_collab("join", "--name", "claude-code", "--role", "planner", cwd=project)
        run_collab("join", "--name", "codex", "--role", "implementer", cwd=project)

        run_collab(
            "send", "--name", "codex", "--type", "proposal",
            "Use WebSocket for live data",
            cwd=project,
        )

        result = run_collab(
            "check", "--name", "claude-code", "--format", "inject", cwd=project
        )
        assert "[Collaboration]" in result.stdout
        assert "WebSocket" in result.stdout
        assert "Reply with:" in result.stdout


def test_collaboration_escalation_inject_format():
    """Tests that escalation messages get priority formatting."""
    with tempfile.TemporaryDirectory() as project:
        run_collab("init", cwd=project)
        run_collab("join", "--name", "claude-code", "--role", "planner", cwd=project)

        # Simulate an escalation message
        run_collab(
            "send", "--name", "system", "--type", "escalation",
            "Unresolved: claude-code vs codex on database choice",
            cwd=project,
        )

        result = run_collab(
            "check", "--name", "claude-code", "--format", "inject", cwd=project
        )
        assert "ESCALATION" in result.stdout
        assert "Pause current work" in result.stdout
