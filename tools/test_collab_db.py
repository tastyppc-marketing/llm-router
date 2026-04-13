#!/usr/bin/env python3
"""Tests for collab_db module."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from collab_db import CollabDB


def test_init_creates_database_and_tables():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".collab" / "collab.db"
        db = CollabDB(db_path)
        db.close()

        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "sessions" in tables
        assert "messages" in tables
        assert "locks" in tables
        assert "config" in tables


def test_init_sets_default_config():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".collab" / "collab.db"
        db = CollabDB(db_path)

        assert db.config_get("mode") == "live"
        assert db.config_get("escalation") == "user"
        assert db.config_get("max_rounds") == "3"
        assert db.config_get("purge_threshold") == "20"
        assert db.config_get("purge_count") == "5"
        assert db.config_get("lock_staleness") == "600"

        db.close()


def test_init_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".collab" / "collab.db"
        db1 = CollabDB(db_path)
        db1.config_set("mode", "event-driven")
        db1.close()

        db2 = CollabDB(db_path)
        assert db2.config_get("mode") == "event-driven"
        db2.close()


# Task 2: Session management

def test_session_join_and_list():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("claude-code", role="planner", directory="./src")
        db.session_join("codex", role="implementer", directory="./src")

        sessions = db.session_list()
        assert len(sessions) == 2
        names = [s["name"] for s in sessions]
        assert "claude-code" in names
        assert "codex" in names

        db.close()


def test_session_leave():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("claude-code", role="planner", directory="./src")
        db.session_join("codex", role="implementer", directory="./src")
        db.session_leave("codex")

        sessions = db.session_list()
        assert len(sessions) == 1
        assert sessions[0]["name"] == "claude-code"

        db.close()


def test_session_leave_releases_locks():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("codex", role="implementer", directory="./src")
        db.lock_claim("src/api/routes.ts", "codex")
        assert db.lock_owner("src/api/routes.ts") == "codex"

        db.session_leave("codex")
        assert db.lock_owner("src/api/routes.ts") is None

        db.close()


def test_session_heartbeat():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("claude-code", role="planner", directory="./src")
        db.session_heartbeat("claude-code")

        sessions = db.session_list()
        assert sessions[0]["last_seen"] is not None

        db.close()


# Task 3: Messaging

def test_send_and_check_messages():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("claude-code", role="planner", directory="./src")
        db.session_join("codex", role="implementer", directory="./src")

        db.message_send("codex", "I finished the auth module", msg_type="status")
        db.message_send("codex", "Should we use Redis?", msg_type="proposal")

        unread = db.message_check("claude-code")
        assert len(unread) == 2
        assert unread[0]["content"] == "I finished the auth module"
        assert unread[1]["type"] == "proposal"

        # Second check should return nothing (already marked read)
        unread2 = db.message_check("claude-code")
        assert len(unread2) == 0

        db.close()


def test_check_peek_does_not_mark_read():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("claude-code", role="planner", directory="./src")
        db.message_send("codex", "Hello", msg_type="status")

        peeked = db.message_check("claude-code", peek=True)
        assert len(peeked) == 1

        # Should still be unread
        peeked2 = db.message_check("claude-code", peek=True)
        assert len(peeked2) == 1

        db.close()


def test_message_reply_to():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        msg_id = db.message_send("codex", "Use Redis?", msg_type="proposal")
        db.message_send(
            "claude-code", "I disagree", msg_type="conflict", reply_to=msg_id
        )

        log = db.message_log()
        assert len(log) == 2
        assert log[1]["reply_to"] == msg_id

        db.close()


def test_message_log_with_limit():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        for i in range(10):
            db.message_send("codex", f"Message {i}", msg_type="status")

        log = db.message_log(last=3)
        assert len(log) == 3
        assert log[0]["content"] == "Message 7"
        assert log[2]["content"] == "Message 9"

        db.close()


def test_sender_does_not_see_own_messages_as_unread():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("codex", role="implementer", directory="./src")
        db.message_send("codex", "My own message", msg_type="status")

        unread = db.message_check("codex")
        assert len(unread) == 0

        db.close()


# Task 4: File locking

def test_lock_claim_and_owner():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.lock_claim("src/api/routes.ts", "codex")
        assert db.lock_owner("src/api/routes.ts") == "codex"
        assert db.lock_owner("src/api/other.ts") is None

        db.close()


def test_lock_release():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.lock_claim("src/api/routes.ts", "codex")
        db.lock_release("src/api/routes.ts", "codex")
        assert db.lock_owner("src/api/routes.ts") is None

        db.close()


def test_lock_force_release():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.lock_claim("src/api/routes.ts", "codex")
        db.lock_release("src/api/routes.ts", "claude-code", force=True)
        assert db.lock_owner("src/api/routes.ts") is None

        db.close()


def test_lock_release_wrong_owner_without_force():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.lock_claim("src/api/routes.ts", "codex")
        db.lock_release("src/api/routes.ts", "claude-code", force=False)
        # Should NOT release — wrong owner
        assert db.lock_owner("src/api/routes.ts") == "codex"

        db.close()


def test_lock_list():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.lock_claim("src/api/routes.ts", "codex")
        db.lock_claim("src/models/user.ts", "claude-code")

        locks = db.lock_list()
        assert len(locks) == 2
        paths = [l["file_path"] for l in locks]
        assert "src/api/routes.ts" in paths
        assert "src/models/user.ts" in paths

        db.close()


def test_lock_release_all_for_session():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.lock_claim("src/a.ts", "codex")
        db.lock_claim("src/b.ts", "codex")
        db.lock_claim("src/c.ts", "claude-code")

        db.lock_release_all("codex")
        locks = db.lock_list()
        assert len(locks) == 1
        assert locks[0]["owner"] == "claude-code"

        db.close()


# Task 5: Purge and archive

def test_purge_archives_oldest_messages():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".collab" / "collab.db"
        db = CollabDB(db_path)

        for i in range(10):
            db.message_send("codex", f"Message {i}", msg_type="status")

        purged = db.purge(count=3)
        assert purged == 3

        log = db.message_log()
        assert len(log) == 7
        assert log[0]["content"] == "Message 3"

        db.close()


def test_purge_uses_config_default():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / ".collab" / "collab.db"
        db = CollabDB(db_path)
        db.config_set("purge_count", "4")

        for i in range(10):
            db.message_send("codex", f"Message {i}", msg_type="status")

        purged = db.purge()
        assert purged == 4

        log = db.message_log()
        assert len(log) == 6

        db.close()


def test_archive_exports_and_resets(tmp_path):
    db_path = tmp_path / ".collab" / "collab.db"
    db = CollabDB(db_path)

    for i in range(5):
        db.message_send("codex", f"Message {i}", msg_type="status")

    history_dir = tmp_path / ".collab" / "history"
    archive_path = db.archive(history_dir)

    assert archive_path.exists()
    assert archive_path.suffix == ".md"

    # Active messages should be zero after archive
    log = db.message_log()
    assert len(log) == 0

    # Archive file should contain all messages
    content = archive_path.read_text()
    assert "Message 0" in content
    assert "Message 4" in content

    db.close()


# Task 6: Conflict tracking

def test_conflict_round_counting():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")
        db.config_set("max_rounds", "3")

        msg1 = db.message_send("claude-code", "Use PostgreSQL", msg_type="proposal")
        msg2 = db.message_send(
            "codex", "Disagree, use Redis", msg_type="conflict", reply_to=msg1
        )
        msg3 = db.message_send(
            "claude-code", "PG is simpler", msg_type="response", reply_to=msg2
        )

        rounds = db.conflict_round_count(msg1)
        assert rounds == 2  # conflict + response

        needs_escalation = db.conflict_needs_escalation(msg1)
        assert needs_escalation is False

        db.close()


def test_conflict_triggers_escalation():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")
        db.config_set("max_rounds", "2")

        msg1 = db.message_send("claude-code", "Use PG", msg_type="proposal")
        db.message_send("codex", "No, Redis", msg_type="conflict", reply_to=msg1)
        db.message_send("claude-code", "PG is better", msg_type="response", reply_to=msg1)

        needs_escalation = db.conflict_needs_escalation(msg1)
        assert needs_escalation is True

        db.close()


def test_conflict_participants():
    with tempfile.TemporaryDirectory() as tmp:
        db = CollabDB(Path(tmp) / ".collab" / "collab.db")

        db.session_join("claude-code", role="planner", directory="./src")
        db.session_join("codex", role="implementer", directory="./src")
        db.session_join("gemini", role="researcher", directory="./docs")

        msg1 = db.message_send("claude-code", "Use PG", msg_type="proposal")
        db.message_send("codex", "No", msg_type="conflict", reply_to=msg1)

        involved = db.conflict_participants(msg1)
        assert "claude-code" in involved
        assert "codex" in involved
        assert "gemini" not in involved

        neutrals = db.conflict_neutral_sessions(msg1)
        assert "gemini" in neutrals
        assert "claude-code" not in neutrals

        db.close()
