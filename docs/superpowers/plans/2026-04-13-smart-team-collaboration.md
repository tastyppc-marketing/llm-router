# Smart Team Collaboration Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collaboration mode to `/smart-team` that enables persistent, peer-to-peer communication between independent LLM sessions via a SQLite-backed CLI, with a TUI dashboard, hook-based message injection, conflict resolution, and file locking.

**Architecture:** A `collab` CLI tool (Python + shell shim) wraps a per-project SQLite database (`.collab/collab.db`). LLM sessions call CLI commands to send/receive messages and manage file locks. A PostToolUse hook injects new messages into each session's context. A `textual`-based TUI dashboard provides real-time visibility and user participation. The system integrates into the existing `/smart-team` skill as a `--collaborate` flag.

**Tech Stack:** Python 3 (stdlib + sqlite3), `textual` (TUI), bash (hook scripts, shell shims). Follows existing llm-router conventions: pathlib paths, `from __future__ import annotations`, manual arg parsing, JSONL manifests, `router_common` utilities.

**Design spec:** `docs/superpowers/specs/2026-04-13-smart-team-collaboration-design.md`

---

## File Structure

```
tools/
    collab_db.py          # SQLite database layer — schema, connection, all queries
    collab.py             # CLI entry point — argument parsing, command dispatch
    collab.sh             # Shell shim
    collab_ui.py          # TUI dashboard — textual app
    collab_ui.sh          # Shell shim for dashboard
hooks/
    collab_check.sh       # PostToolUse hook — runs collab check --format inject
    collab_lock_check.sh  # PreToolUse hook — runs collab lock-check
commands/
    smart-team.md         # Updated to include --collaborate mode documentation
```

**Responsibilities:**

- `collab_db.py` — All SQLite interaction. Creates/opens the database, manages schema, provides typed functions for every operation (insert message, query unread, claim lock, etc.). No CLI logic. No formatting.
- `collab.py` — CLI interface. Parses arguments, calls `collab_db` functions, formats output for terminal or hook injection. No direct SQL.
- `collab_ui.py` — TUI dashboard. Reads from the database via `collab_db`, displays live conversation, handles user input. Independent process.
- Hook scripts — Thin bash wrappers that call `collab.py` subcommands. Follow existing hook patterns (`set -euo pipefail`, exit codes 0/2).

---

## Task 1: Database Layer — Schema and Connection

**Files:**
- Create: `tools/collab_db.py`

- [ ] **Step 1: Write the test for database initialization**

Create a test that verifies the database can be initialized and the schema is correct.

```python
# tools/test_collab_db.py
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py::test_init_creates_database_and_tables -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'collab_db'`

- [ ] **Step 3: Implement the database layer — schema and connection**

```python
# tools/collab_db.py
#!/usr/bin/env python3
"""SQLite database layer for smart-team collaboration mode."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    name        TEXT PRIMARY KEY,
    role        TEXT,
    directory   TEXT,
    mode        TEXT DEFAULT 'live',
    escalation  TEXT DEFAULT 'user',
    joined_at   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender      TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'status',
    content     TEXT NOT NULL,
    reply_to    INTEGER REFERENCES messages(id),
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    read_by     TEXT DEFAULT '[]',
    archived    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS locks (
    file_path   TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    claimed_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT
);
"""

DEFAULT_CONFIG = {
    "mode": "live",
    "escalation": "user",
    "max_rounds": "3",
    "purge_threshold": "20",
    "purge_count": "5",
    "lock_staleness": "600",
}


class CollabDB:
    """Interface to the collaboration SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        for key, value in DEFAULT_CONFIG.items():
            self._conn.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Config ---------------------------------------------------------

    def config_get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def config_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run all three tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/collab_db.py tools/test_collab_db.py
git commit -m "feat(collab): add database layer — schema and connection"
```

---

## Task 2: Database Layer — Session Management

**Files:**
- Modify: `tools/collab_db.py`
- Modify: `tools/test_collab_db.py`

- [ ] **Step 1: Write the tests for session join, leave, list, and heartbeat**

Append to `tools/test_collab_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py::test_session_join_and_list -v`
Expected: FAIL with `AttributeError: 'CollabDB' object has no attribute 'session_join'`

- [ ] **Step 3: Implement session management methods**

Add to `CollabDB` class in `tools/collab_db.py`:

```python
    # -- Sessions -------------------------------------------------------

    def session_join(self, name: str, *, role: str = "", directory: str = "") -> None:
        now = self._now()
        self._conn.execute(
            "INSERT INTO sessions (name, role, directory, joined_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET role=excluded.role, "
            "directory=excluded.directory, last_seen=excluded.last_seen",
            (name, role, directory, now, now),
        )
        self._conn.commit()

    def session_leave(self, name: str) -> None:
        self._conn.execute("DELETE FROM locks WHERE owner = ?", (name,))
        self._conn.execute("DELETE FROM sessions WHERE name = ?", (name,))
        self._conn.commit()

    def session_list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT name, role, directory, mode, escalation, joined_at, last_seen "
            "FROM sessions ORDER BY joined_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def session_heartbeat(self, name: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE name = ?",
            (self._now(), name),
        )
        self._conn.commit()

    def session_get(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT name, role, directory, mode, escalation, joined_at, last_seen "
            "FROM sessions WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def session_set_mode(self, name: str, mode: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET mode = ? WHERE name = ?", (mode, name)
        )
        self._conn.commit()

    def session_set_escalation(self, name: str, escalation: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET escalation = ? WHERE name = ?",
            (escalation, name),
        )
        self._conn.commit()

    # -- Helpers --------------------------------------------------------

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/collab_db.py tools/test_collab_db.py
git commit -m "feat(collab): add session management — join, leave, list, heartbeat"
```

---

## Task 3: Database Layer — Messaging

**Files:**
- Modify: `tools/collab_db.py`
- Modify: `tools/test_collab_db.py`

- [ ] **Step 1: Write the tests for send, check, log, and read tracking**

Append to `tools/test_collab_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py::test_send_and_check_messages -v`
Expected: FAIL with `AttributeError: 'CollabDB' object has no attribute 'message_send'`

- [ ] **Step 3: Implement messaging methods**

Add to `CollabDB` class in `tools/collab_db.py`:

```python
    # -- Messages -------------------------------------------------------

    def message_send(
        self,
        sender: str,
        content: str,
        *,
        msg_type: str = "status",
        reply_to: int | None = None,
    ) -> int:
        read_by = json.dumps([sender])
        cursor = self._conn.execute(
            "INSERT INTO messages (sender, type, content, reply_to, read_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (sender, msg_type, content, reply_to, read_by),
        )
        self._conn.commit()
        return cursor.lastrowid

    def message_check(self, session_name: str, *, peek: bool = False) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, sender, type, content, reply_to, created_at, read_by "
            "FROM messages WHERE archived = 0 "
            "ORDER BY id"
        ).fetchall()

        unread = []
        ids_to_mark: list[int] = []
        for row in rows:
            readers = json.loads(row["read_by"])
            if session_name not in readers:
                unread.append(dict(row))
                ids_to_mark.append(row["id"])

        if not peek and ids_to_mark:
            for msg_id in ids_to_mark:
                row = self._conn.execute(
                    "SELECT read_by FROM messages WHERE id = ?", (msg_id,)
                ).fetchone()
                readers = json.loads(row["read_by"])
                if session_name not in readers:
                    readers.append(session_name)
                    self._conn.execute(
                        "UPDATE messages SET read_by = ? WHERE id = ?",
                        (json.dumps(readers), msg_id),
                    )
            self._conn.commit()

        return unread

    def message_log(self, *, last: int | None = None) -> list[dict]:
        if last is not None:
            rows = self._conn.execute(
                "SELECT id, sender, type, content, reply_to, created_at "
                "FROM messages WHERE archived = 0 "
                "ORDER BY id DESC LIMIT ?",
                (last,),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        rows = self._conn.execute(
            "SELECT id, sender, type, content, reply_to, created_at "
            "FROM messages WHERE archived = 0 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def message_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE archived = 0"
        ).fetchone()
        return row["cnt"]
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py -v`
Expected: 12 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/collab_db.py tools/test_collab_db.py
git commit -m "feat(collab): add messaging — send, check, log, read tracking"
```

---

## Task 4: Database Layer — File Locking

**Files:**
- Modify: `tools/collab_db.py`
- Modify: `tools/test_collab_db.py`

- [ ] **Step 1: Write the tests for lock, unlock, lock-check, and staleness**

Append to `tools/test_collab_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py::test_lock_claim_and_owner -v`
Expected: FAIL with `AttributeError: 'CollabDB' object has no attribute 'lock_claim'`

- [ ] **Step 3: Implement file locking methods**

Add to `CollabDB` class in `tools/collab_db.py`:

```python
    # -- Locks ----------------------------------------------------------

    def lock_claim(self, file_path: str, owner: str) -> bool:
        existing = self.lock_owner(file_path)
        if existing is not None and existing != owner:
            return False
        self._conn.execute(
            "INSERT INTO locks (file_path, owner) VALUES (?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET owner=excluded.owner, "
            "claimed_at=strftime('%Y-%m-%dT%H:%M:%S', 'now')",
            (file_path, owner),
        )
        self._conn.commit()
        return True

    def lock_release(self, file_path: str, requester: str, *, force: bool = False) -> bool:
        if force:
            self._conn.execute("DELETE FROM locks WHERE file_path = ?", (file_path,))
        else:
            self._conn.execute(
                "DELETE FROM locks WHERE file_path = ? AND owner = ?",
                (file_path, requester),
            )
        self._conn.commit()
        return True

    def lock_release_all(self, owner: str) -> None:
        self._conn.execute("DELETE FROM locks WHERE owner = ?", (owner,))
        self._conn.commit()

    def lock_owner(self, file_path: str) -> str | None:
        row = self._conn.execute(
            "SELECT owner FROM locks WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row["owner"] if row else None

    def lock_list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT file_path, owner, claimed_at FROM locks ORDER BY claimed_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def lock_check(self, file_path: str, session_name: str) -> dict:
        owner = self.lock_owner(file_path)
        if owner is None:
            self.lock_claim(file_path, session_name)
            return {"status": "claimed", "owner": session_name}
        if owner == session_name:
            return {"status": "owned", "owner": session_name}
        return {"status": "blocked", "owner": owner}
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py -v`
Expected: 18 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/collab_db.py tools/test_collab_db.py
git commit -m "feat(collab): add file locking — claim, release, check, staleness"
```

---

## Task 5: Database Layer — Purge and Archive

**Files:**
- Modify: `tools/collab_db.py`
- Modify: `tools/test_collab_db.py`

- [ ] **Step 1: Write the tests for purge and archive**

Append to `tools/test_collab_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py::test_purge_archives_oldest_messages -v`
Expected: FAIL with `AttributeError: 'CollabDB' object has no attribute 'purge'`

- [ ] **Step 3: Implement purge and archive methods**

Add to `CollabDB` class in `tools/collab_db.py`:

```python
    # -- Purge & Archive ------------------------------------------------

    def purge(self, *, count: int | None = None) -> int:
        if count is None:
            count = int(self.config_get("purge_count") or "5")
        cursor = self._conn.execute(
            "UPDATE messages SET archived = 1 "
            "WHERE id IN ("
            "  SELECT id FROM messages WHERE archived = 0 ORDER BY id LIMIT ?"
            ")",
            (count,),
        )
        self._conn.commit()
        return cursor.rowcount

    def archive(self, history_dir: Path) -> Path:
        history_dir.mkdir(parents=True, exist_ok=True)
        rows = self._conn.execute(
            "SELECT id, sender, type, content, reply_to, created_at "
            "FROM messages WHERE archived = 0 ORDER BY id"
        ).fetchall()

        timestamp = self._now().replace(":", "-")
        archive_path = history_dir / f"archive-{timestamp}.md"

        lines: list[str] = []
        lines.append(f"# Collaboration Archive — {timestamp}\n")
        for row in rows:
            r = dict(row)
            lines.append(f"\n[#{r['id']} {r['sender']} | {r['type']} | {r['created_at']}]")
            if r["reply_to"]:
                lines.append(f"  (reply to #{r['reply_to']})")
            lines.append(r["content"])

        archive_path.write_text("\n".join(lines) + "\n")

        self._conn.execute("UPDATE messages SET archived = 1 WHERE archived = 0")
        self._conn.commit()

        return archive_path
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py -v`
Expected: 21 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/collab_db.py tools/test_collab_db.py
git commit -m "feat(collab): add purge and archive — sliding window cleanup"
```

---

## Task 6: Database Layer — Conflict Tracking

**Files:**
- Modify: `tools/collab_db.py`
- Modify: `tools/test_collab_db.py`

- [ ] **Step 1: Write the tests for conflict round counting and escalation detection**

Append to `tools/test_collab_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py::test_conflict_round_counting -v`
Expected: FAIL with `AttributeError: 'CollabDB' object has no attribute 'conflict_round_count'`

- [ ] **Step 3: Implement conflict tracking methods**

Add to `CollabDB` class in `tools/collab_db.py`:

```python
    # -- Conflict tracking ----------------------------------------------

    def conflict_round_count(self, root_msg_id: int) -> int:
        rows = self._conn.execute(
            "SELECT id FROM messages "
            "WHERE (reply_to = ? OR id = ?) "
            "AND type IN ('conflict', 'response') "
            "AND archived = 0",
            (root_msg_id, root_msg_id),
        ).fetchall()
        return len(rows)

    def conflict_needs_escalation(self, root_msg_id: int) -> bool:
        max_rounds = int(self.config_get("max_rounds") or "3")
        return self.conflict_round_count(root_msg_id) >= max_rounds

    def conflict_participants(self, root_msg_id: int) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT sender FROM messages "
            "WHERE id = ? OR reply_to = ?",
            (root_msg_id, root_msg_id),
        ).fetchall()
        return {row["sender"] for row in rows}

    def conflict_neutral_sessions(self, root_msg_id: int) -> list[str]:
        involved = self.conflict_participants(root_msg_id)
        sessions = self.session_list()
        return [s["name"] for s in sessions if s["name"] not in involved]
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py -v`
Expected: 24 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/collab_db.py tools/test_collab_db.py
git commit -m "feat(collab): add conflict tracking — round counting, escalation, neutrals"
```

---

## Task 7: CLI Entry Point — Core Commands

**Files:**
- Create: `tools/collab.py`
- Create: `tools/collab.sh`

- [ ] **Step 1: Write tests for CLI argument parsing and core commands**

```python
# tools/test_collab_cli.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_cli.py::test_init_creates_collab_directory -v`
Expected: FAIL (script doesn't exist yet)

- [ ] **Step 3: Create the shell shim**

```bash
# tools/collab.sh
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/collab.py" "$@"
```

Make executable: `chmod +x tools/collab.sh`

- [ ] **Step 4: Implement the CLI entry point**

```python
# tools/collab.py
#!/usr/bin/env python3
"""CLI entry point for smart-team collaboration mode."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def usage() -> str:
    return (
        "Usage: collab.sh <command> [options]\n"
        "\n"
        "Commands:\n"
        "  init                          Create a new collaboration in current project\n"
        "  join --name NAME [--role R]   Join the collaboration\n"
        "  leave --name NAME             Leave the collaboration\n"
        "  status                        Show sessions, locks, unread count\n"
        "  send [--name N] [--type T] [--reply-to ID] MESSAGE\n"
        "  check [--name N] [--peek] [--format inject]\n"
        "  log [--last N]                Show conversation history\n"
        "  lock FILE --name NAME         Claim a file lock\n"
        "  unlock FILE --name NAME [--force]\n"
        "  lock-check FILE --name NAME   Check lock before edit\n"
        "  locks                         Show all active locks\n"
        "  mode live|event-driven        Switch conversation mode\n"
        "  purge [--count N]             Archive oldest messages\n"
        "  archive                       Export full history and reset\n"
        "  end                           End the collaboration\n"
        "  config set KEY VALUE          Set a config value\n"
        "  config get KEY                Get a config value\n"
        "  help [topic]                  Show help\n"
        "  ui                            Launch TUI dashboard\n"
    )


def find_collab_db(start_dir: Path | None = None) -> Path:
    """Walk up from start_dir to find .collab/collab.db."""
    search = start_dir or Path.cwd()
    current = search.resolve()
    while True:
        candidate = current / ".collab" / "collab.db"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return search / ".collab" / "collab.db"


def open_db(must_exist: bool = True) -> "CollabDB":
    from collab_db import CollabDB

    db_path = find_collab_db()
    if must_exist and not db_path.exists():
        print("Error: No collaboration found. Run 'collab init' first.", file=sys.stderr)
        raise SystemExit(1)
    return CollabDB(db_path)


# -- Command handlers --------------------------------------------------


def cmd_init(argv: list[str]) -> int:
    from collab_db import CollabDB

    db_path = Path.cwd() / ".collab" / "collab.db"
    if db_path.exists():
        print("Collaboration already initialized.", file=sys.stderr)
        return 0
    CollabDB(db_path).close()
    print(f"Collaboration initialized at .collab/")
    return 0


def cmd_join(argv: list[str]) -> int:
    name = ""
    role = ""
    directory = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        elif argv[i] == "--role" and i + 1 < len(argv):
            i += 1
            role = argv[i]
        elif argv[i] == "--dir" and i + 1 < len(argv):
            i += 1
            directory = argv[i]
        i += 1
    if not name:
        print("Error: --name is required.", file=sys.stderr)
        return 1
    db = open_db()
    db.session_join(name, role=role, directory=directory or str(Path.cwd()))
    db.close()
    print(f"Joined collaboration as '{name}' (role: {role or 'unset'})")
    return 0


def cmd_leave(argv: list[str]) -> int:
    name = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        i += 1
    if not name:
        print("Error: --name is required.", file=sys.stderr)
        return 1
    db = open_db()
    db.session_leave(name)
    db.message_send("system", f"{name} left the collaboration", msg_type="system")
    db.close()
    print(f"Left collaboration: {name}")
    return 0


def cmd_status(argv: list[str]) -> int:
    db = open_db()
    sessions = db.session_list()
    locks = db.lock_list()
    msg_count = db.message_count()
    threshold = db.config_get("purge_threshold") or "20"
    mode = db.config_get("mode") or "live"

    print(f"Collaboration Status (mode: {mode})")
    print("=" * 45)

    print("\nSessions:")
    if not sessions:
        print("  (none)")
    for s in sessions:
        print(f"  * {s['name']} — {s['role'] or 'no role'} ({s['directory']})")
        print(f"    mode: {s['mode']}  escalation: {s['escalation']}")

    print("\nLocks:")
    if not locks:
        print("  No active locks")
    for lock in locks:
        print(f"  {lock['file_path']} -> {lock['owner']} (since {lock['claimed_at']})")

    print(f"\nMessages: {msg_count} (purge at {threshold})")
    db.close()
    return 0


def cmd_send(argv: list[str]) -> int:
    name = ""
    msg_type = "status"
    reply_to = None
    parts: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        elif argv[i] == "--type" and i + 1 < len(argv):
            i += 1
            msg_type = argv[i]
        elif argv[i] == "--reply-to" and i + 1 < len(argv):
            i += 1
            reply_to = int(argv[i])
        else:
            parts.append(argv[i])
        i += 1
    if not name:
        print("Error: --name is required.", file=sys.stderr)
        return 1
    content = " ".join(parts)
    if not content:
        print("Error: message content required.", file=sys.stderr)
        return 1
    db = open_db()
    msg_id = db.message_send(name, content, msg_type=msg_type, reply_to=reply_to)
    db.close()
    print(f"Sent message #{msg_id} ({msg_type})")
    return 0


def cmd_check(argv: list[str]) -> int:
    name = ""
    peek = False
    fmt = "default"
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        elif argv[i] == "--peek":
            peek = True
        elif argv[i] == "--format" and i + 1 < len(argv):
            i += 1
            fmt = argv[i]
        i += 1
    if not name:
        print("Error: --name is required.", file=sys.stderr)
        return 1
    db = open_db()
    db.session_heartbeat(name)
    messages = db.message_check(name, peek=peek)
    if not messages:
        if fmt != "inject":
            print("No new messages.")
        db.close()
        return 0

    if fmt == "inject":
        print(format_inject(messages))
    else:
        print(f"{len(messages)} new message(s):\n")
        for m in messages:
            print(format_message(m))
    db.close()
    return 0


def cmd_log(argv: list[str]) -> int:
    last = None
    i = 0
    while i < len(argv):
        if argv[i] == "--last" and i + 1 < len(argv):
            i += 1
            last = int(argv[i])
        i += 1
    db = open_db()
    messages = db.message_log(last=last)
    if not messages:
        print("No messages yet.")
        db.close()
        return 0
    for m in messages:
        print(format_message(m))
    db.close()
    return 0


def cmd_lock(argv: list[str]) -> int:
    name = ""
    file_path = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        elif not file_path and not argv[i].startswith("-"):
            file_path = argv[i]
        i += 1
    if not name or not file_path:
        print("Error: FILE and --name required.", file=sys.stderr)
        return 1
    db = open_db()
    claimed = db.lock_claim(file_path, name)
    if claimed:
        db.message_send(name, f"Locked: {file_path}", msg_type="lock-notify")
        print(f"Locked: {file_path}")
    else:
        owner = db.lock_owner(file_path)
        print(f"Cannot lock: {file_path} is held by {owner}", file=sys.stderr)
    db.close()
    return 0 if claimed else 1


def cmd_unlock(argv: list[str]) -> int:
    name = ""
    file_path = ""
    force = False
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        elif argv[i] == "--force":
            force = True
        elif not file_path and not argv[i].startswith("-"):
            file_path = argv[i]
        i += 1
    if not name or not file_path:
        print("Error: FILE and --name required.", file=sys.stderr)
        return 1
    db = open_db()
    db.lock_release(file_path, name, force=force)
    if force:
        db.message_send(
            "system",
            f"{name} force-released lock on {file_path}",
            msg_type="lock-notify",
        )
    else:
        db.message_send(name, f"Unlocked: {file_path}", msg_type="lock-notify")
    print(f"Unlocked: {file_path}")
    db.close()
    return 0


def cmd_lock_check(argv: list[str]) -> int:
    name = ""
    file_path = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--name" and i + 1 < len(argv):
            i += 1
            name = argv[i]
        elif not file_path and not argv[i].startswith("-"):
            file_path = argv[i]
        i += 1
    if not name or not file_path:
        return 0  # Silently pass if args missing (hook safety)
    db_path = find_collab_db()
    if not db_path.exists():
        return 0  # No collaboration active, allow edit
    from collab_db import CollabDB

    db = CollabDB(db_path)
    result = db.lock_check(file_path, name)
    if result["status"] == "blocked":
        owner = result["owner"]
        print(
            f"Warning: {file_path} is locked by {owner}. "
            f"Send them a message to coordinate, or proceed at your own risk."
        )
    db.close()
    return 0  # Advisory only, never blocks


def cmd_locks(argv: list[str]) -> int:
    db = open_db()
    locks = db.lock_list()
    if not locks:
        print("No active locks.")
    else:
        print("Active locks:")
        for lock in locks:
            print(f"  {lock['file_path']} -> {lock['owner']} (since {lock['claimed_at']})")
    db.close()
    return 0


def cmd_mode(argv: list[str]) -> int:
    if not argv:
        print("Error: specify 'live' or 'event-driven'.", file=sys.stderr)
        return 1
    mode = argv[0]
    if mode not in ("live", "event-driven"):
        print(f"Error: invalid mode '{mode}'. Use 'live' or 'event-driven'.", file=sys.stderr)
        return 1
    db = open_db()
    db.config_set("mode", mode)
    print(f"Mode set to: {mode}")
    db.close()
    return 0


def cmd_purge(argv: list[str]) -> int:
    count = None
    i = 0
    while i < len(argv):
        if argv[i] == "--count" and i + 1 < len(argv):
            i += 1
            count = int(argv[i])
        i += 1
    db = open_db()
    purged = db.purge(count=count)
    print(f"Archived {purged} message(s).")
    db.close()
    return 0


def cmd_archive(argv: list[str]) -> int:
    db = open_db()
    db_path = find_collab_db()
    history_dir = db_path.parent / "history"
    archive_path = db.archive(history_dir)
    print(f"Archived to: {archive_path}")
    db.close()
    return 0


def cmd_end(argv: list[str]) -> int:
    db = open_db()
    db.message_send("system", "Collaboration ending.", msg_type="system")
    db_path = find_collab_db()
    history_dir = db_path.parent / "history"
    archive_path = db.archive(history_dir)
    print(f"Collaboration ended. Archive: {archive_path}")
    db.close()
    return 0


def cmd_config(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: collab config set KEY VALUE | collab config get KEY", file=sys.stderr)
        return 1
    action = argv[0]
    key = argv[1]
    if action == "get":
        db = open_db()
        value = db.config_get(key)
        print(f"{key} = {value}" if value is not None else f"{key} is not set")
        db.close()
        return 0
    if action == "set" and len(argv) >= 3:
        value = argv[2]
        db = open_db()
        db.config_set(key, value)
        print(f"{key} = {value}")
        db.close()
        return 0
    print("Usage: collab config set KEY VALUE | collab config get KEY", file=sys.stderr)
    return 1


def cmd_help(argv: list[str]) -> int:
    topic = argv[0] if argv else ""
    print(get_help_text(topic))
    return 0


# -- Formatting helpers -------------------------------------------------


def format_message(m: dict) -> str:
    reply = f"  (reply to #{m['reply_to']})" if m.get("reply_to") else ""
    return f"[#{m['id']} {m['sender']} | {m['type']} | {m['created_at']}]{reply}\n{m['content']}\n"


def format_inject(messages: list[dict]) -> str:
    lines: list[str] = []
    has_escalation = any(m["type"] in ("escalation", "directive") for m in messages)

    if has_escalation:
        escalations = [m for m in messages if m["type"] in ("escalation", "directive")]
        others = [m for m in messages if m["type"] not in ("escalation", "directive")]

        lines.append("[Collaboration] ESCALATION requires your attention:\n")
        for m in escalations:
            lines.append(format_message(m))
        lines.append("Pause current work and respond to this escalation.\n")

        if others:
            lines.append(f"[Collaboration] {len(others)} other message(s):\n")
            for m in others:
                lines.append(format_message(m))
    else:
        lines.append(f"[Collaboration] {len(messages)} new message(s):\n")
        for m in messages:
            lines.append(format_message(m))
        lines.append("Reply with: collab send --reply-to <id> \"your response\"")

    return "\n".join(lines)


def get_help_text(topic: str) -> str:
    if topic == "start":
        return HELP_START
    if topic == "messaging":
        return HELP_MESSAGING
    if topic == "conflicts":
        return HELP_CONFLICTS
    if topic == "locks":
        return HELP_LOCKS
    if topic == "dashboard":
        return HELP_DASHBOARD
    if topic == "config":
        return HELP_CONFIG
    return HELP_OVERVIEW


HELP_OVERVIEW = """
+-----------------------------------------------------------+
|           Smart Team Collaboration Mode                    |
|                                                            |
|  Multiple LLM sessions working together as peers           |
|  on the same project, in real time.                        |
+-----------------------------------------------------------+

Before You Start
----------------
  Collaboration mode needs tmux to work.

  When you collaborate, multiple LLM sessions run side by
  side -- your session, your partner's session, and the
  dashboard. tmux is what makes that possible. Think of it
  like a window manager for your terminal: it splits your
  screen into panes, each running independently.

  If you're not already in tmux, start it:
    tmux new -s collab

  Then launch your LLM inside it and you're ready to go.

  Not sure if you're in tmux? Run:
    echo $TMUX
  If it prints something, you're in. If blank, you're not.

  Quick tmux survival guide:
    Ctrl+B then "     Split pane horizontally
    Ctrl+B then %     Split pane vertically
    Ctrl+B then ->    Move to the pane on the right
    Ctrl+B then <-    Move to the pane on the left
    Ctrl+B then z     Zoom into current pane (toggle)

Getting Started
---------------
  There are three ways to kick off a collaboration:

  1. Cold Start (most common)
     Tell your LLM: "Let's collaborate with Codex on this"
     It sets everything up automatically.

  2. Hot Connect
     Already have sessions running? Say:
     "Connect my existing Codex session to this collaboration"
     Links them without restarting anything.

  3. Manual
     For full control, run in each session:
       collab init          (first session only)
       collab join --name "my-session" --role "my-role"

What You'll See
---------------

  +------------+------------+------------+
  | Your LLM   | Partner    | Dashboard  |
  | session    | session    |            |
  |            |            | Watch the  |
  | Chat here  | Works on   | convo and  |
  | normally   | its own    | jump in    |
  +------------+------------+------------+

Key Concepts
------------
  Messaging     Sessions talk to each other automatically.
                You'll see it in the dashboard.

  File Locks    Sessions claim files before editing.
                Prevents two LLMs editing the same file.

  Conflicts     When sessions disagree, they debate it
                (configurable rounds). Then either you
                decide or a neutral LLM arbitrates.

  Modes         live = constant conversation
                event-driven = check in at milestones

Settings You Can Change Anytime
-------------------------------
  Conversation mode     live / event-driven
  Conflict rounds       how many before escalation (default: 3)
  Escalation mode       user / autonomous (per session)
  Purge threshold       max messages before cleanup (default: 20)
  Purge count           how many to archive per purge (default: 5)

More Detail
-----------
  collab help start        Getting started in depth
  collab help messaging    Message types and threading
  collab help conflicts    Conflict resolution and arbitration
  collab help locks        File locking
  collab help dashboard    TUI dashboard guide
  collab help config       All configuration options
"""

HELP_START = """
Getting Started
===============

Cold Start
----------
  From an interactive LLM session, say:
  "Let's collaborate with Codex on this project"

  Your LLM will:
  1. Run 'collab init' to create .collab/ in the project
  2. Join itself to the collaboration
  3. Spawn partner sessions in new tmux panes
  4. Install communication hooks in each session
  5. Open the TUI dashboard

Hot Connect
-----------
  Already have sessions running?
  "Connect my existing sessions to this collaboration"

  Same as cold start, but it finds and links existing
  sessions instead of spawning new ones.

Manual Setup
------------
  In your first session:
    collab init
    collab join --name "claude-code" --role "planner"

  In another session:
    collab join --name "codex" --role "implementer"

  Optionally open the dashboard:
    collab ui
"""

HELP_MESSAGING = """
Messaging
=========

Message Types
-------------
  status      Share progress or intent
  proposal    Suggest an approach
  response    Reply to a proposal or question
  question    Ask another session something
  conflict    Flag a disagreement with reasoning
  lock-notify Automated file lock notifications
  escalation  Unresolved conflict needing user input
  directive   Message from the user (highest priority)

Sending Messages
----------------
  collab send --name "codex" "I finished the API routes"
  collab send --name "codex" --type proposal "Let's use Redis"
  collab send --name "codex" --type conflict --reply-to 14 "I disagree"

Checking Messages
-----------------
  collab check --name "claude-code"          Read and mark as read
  collab check --name "claude-code" --peek   Read without marking

Conversation Log
----------------
  collab log                    Full history
  collab log --last 10          Last 10 messages
"""

HELP_CONFLICTS = """
Conflict Resolution
===================

How It Works
------------
  1. Session A makes a proposal
  2. Session B disagrees (sends a 'conflict' message)
  3. They debate back and forth (max rounds, default 3)
  4. If resolved, a summary is posted
  5. If not, escalation happens

Escalation Modes (per session)
------------------------------
  user        You get pulled in to decide
  autonomous  A neutral LLM session arbitrates

  Set it:
    collab config set escalation autonomous
    collab config set max_rounds 5

Autonomous Arbitration
----------------------
  When a conflict can't be resolved:
  - A session NOT involved in the debate receives
    both positions
  - They evaluate and post a binding decision
  - Both sides proceed with that decision

  If no neutral session is available, falls back
  to user escalation.

You're Always in the Loop
-------------------------
  All conflicts appear in the dashboard regardless
  of mode. You can always override with a directive.
"""

HELP_LOCKS = """
File Locking
============

How It Works
------------
  Before editing a file, sessions claim a lock.
  Locks are per-file and advisory.

  collab lock src/api/routes.ts --name "codex"
  collab unlock src/api/routes.ts --name "codex"
  collab locks                    See all active locks

Automatic Locking
-----------------
  Hooks handle this for you. Before an Edit or Write,
  the hook checks if the file is locked. If not, it
  auto-claims. After the edit, it auto-releases.

  If someone else has the lock, you'll see:
  "Warning: src/api/routes.ts is locked by codex.
   Send them a message to coordinate."

Force Release
-------------
  collab unlock src/api/routes.ts --name "codex" --force

  Force-releases someone else's lock. Use sparingly.
  A notification is sent to the lock owner.

Staleness
---------
  If a session crashes without releasing, locks go
  stale after 10 minutes (configurable). Stale locks
  can be claimed by anyone.
"""

HELP_DASHBOARD = """
TUI Dashboard
=============

Launch
------
  collab ui                   Standard mode
  collab ui --readonly        Watch only, no input

Layout
------
  Left panel:  Live message stream, color-coded by sender
  Top right:   Active sessions with roles and directories
  Mid right:   Current file locks
  Bottom right: Message count and purge threshold
  Bottom:       Text input for sending messages

Sending Messages
----------------
  Type at the bottom and press Enter. Your messages
  are sent as 'directive' type, which LLMs treat
  as highest priority.

  Ctrl+Q to quit the dashboard.
"""

HELP_CONFIG = """
Configuration
=============

Global Defaults
---------------
  mode              live | event-driven     (default: live)
  escalation        user | autonomous       (default: user)
  max_rounds        integer                 (default: 3)
  purge_threshold   integer                 (default: 20)
  purge_count       integer                 (default: 5)
  lock_staleness    seconds                 (default: 600)

Commands
--------
  collab config set mode event-driven
  collab config set max_rounds 5
  collab config get purge_threshold
"""


# -- Main dispatch ------------------------------------------------------

COMMANDS = {
    "init": cmd_init,
    "join": cmd_join,
    "leave": cmd_leave,
    "status": cmd_status,
    "send": cmd_send,
    "check": cmd_check,
    "log": cmd_log,
    "lock": cmd_lock,
    "unlock": cmd_unlock,
    "lock-check": cmd_lock_check,
    "locks": cmd_locks,
    "mode": cmd_mode,
    "purge": cmd_purge,
    "archive": cmd_archive,
    "end": cmd_end,
    "config": cmd_config,
    "help": cmd_help,
}


def main(argv: list[str]) -> int:
    if not argv:
        print(usage(), file=sys.stderr)
        return 1
    command = argv[0]
    handler = COMMANDS.get(command)
    if handler is None:
        print(f"Unknown command: {command}\n", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 1
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run all CLI tests to verify they pass**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_cli.py -v`
Expected: 8 PASS

- [ ] **Step 6: Commit**

```bash
chmod +x tools/collab.sh
git add tools/collab.py tools/collab.sh tools/test_collab_cli.py
git commit -m "feat(collab): add CLI entry point — all core commands and help system"
```

---

## Task 8: Hook Scripts

**Files:**
- Create: `hooks/collab_check.sh`
- Create: `hooks/collab_lock_check.sh`

- [ ] **Step 1: Write the PostToolUse hook for message checking**

```bash
# hooks/collab_check.sh
#!/usr/bin/env bash
set -euo pipefail

# PostToolUse hook: checks for new collaboration messages after each tool call.
# Requires COLLAB_SESSION_NAME to be set in the environment.
# Exits 0 always (advisory only — never blocks tool execution).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COLLAB_CLI="$SCRIPT_DIR/../tools/collab.sh"

SESSION_NAME="${COLLAB_SESSION_NAME:-}"
if [[ -z "$SESSION_NAME" ]]; then
    exit 0
fi

# Debounce: skip if last check was < 2 seconds ago
DEBOUNCE_FILE="/tmp/.collab-check-${SESSION_NAME}"
if [[ -f "$DEBOUNCE_FILE" ]]; then
    last_check=$(stat -c %Y "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    if (( now - last_check < 2 )); then
        exit 0
    fi
fi
touch "$DEBOUNCE_FILE"

# Check for new messages
output=$("$COLLAB_CLI" check --name "$SESSION_NAME" --format inject 2>/dev/null || true)

if [[ -n "$output" ]]; then
    echo "$output"
fi

exit 0
```

- [ ] **Step 2: Write the PreToolUse hook for file lock checking**

```bash
# hooks/collab_lock_check.sh
#!/usr/bin/env bash
set -euo pipefail

# PreToolUse hook: checks file locks before Edit/Write operations.
# Requires COLLAB_SESSION_NAME to be set in the environment.
# Reads the file path from the tool input.
# Exits 0 always (advisory only — warns but never blocks).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COLLAB_CLI="$SCRIPT_DIR/../tools/collab.sh"

SESSION_NAME="${COLLAB_SESSION_NAME:-}"
if [[ -z "$SESSION_NAME" ]]; then
    exit 0
fi

# The file_path comes from the tool input via CLAUDE_TOOL_INPUT
# Parse the file_path from JSON input
FILE_PATH=""
if [[ -n "${CLAUDE_TOOL_INPUT:-}" ]]; then
    FILE_PATH=$(echo "$CLAUDE_TOOL_INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('file_path', ''))
except:
    pass
" 2>/dev/null || true)
fi

if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

# Check lock status
output=$("$COLLAB_CLI" lock-check "$FILE_PATH" --name "$SESSION_NAME" 2>/dev/null || true)

if [[ -n "$output" ]]; then
    echo "$output"
fi

exit 0
```

- [ ] **Step 3: Make hooks executable and test basic execution**

```bash
chmod +x hooks/collab_check.sh hooks/collab_lock_check.sh
```

Run: `bash -n /root/llm-router/hooks/collab_check.sh && echo "syntax ok"`
Expected: `syntax ok`

Run: `bash -n /root/llm-router/hooks/collab_lock_check.sh && echo "syntax ok"`
Expected: `syntax ok`

- [ ] **Step 4: Commit**

```bash
git add hooks/collab_check.sh hooks/collab_lock_check.sh
git commit -m "feat(collab): add hook scripts — PostToolUse message check, PreToolUse lock check"
```

---

## Task 9: TUI Dashboard — Core Layout

**Files:**
- Create: `tools/collab_ui.py`
- Create: `tools/collab_ui.sh`

- [ ] **Step 1: Install textual dependency**

Run: `pip install textual`
Expected: Successful installation

- [ ] **Step 2: Create the shell shim**

```bash
# tools/collab_ui.sh
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/collab_ui.py" "$@"
```

Make executable: `chmod +x tools/collab_ui.sh`

- [ ] **Step 3: Implement the TUI dashboard**

```python
# tools/collab_ui.py
#!/usr/bin/env python3
"""TUI dashboard for smart-team collaboration mode."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.reactive import reactive
    from textual.widgets import Footer, Header, Input, RichLog, Static
except ImportError:
    print(
        "Error: textual is required for the dashboard.\n"
        "Install it with: pip install textual",
        file=sys.stderr,
    )
    raise SystemExit(1)

sys.path.insert(0, str(SCRIPT_DIR))
from collab_db import CollabDB


def find_collab_db() -> Path:
    current = Path.cwd().resolve()
    while True:
        candidate = current / ".collab" / "collab.db"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    print("Error: No collaboration found. Run 'collab init' first.", file=sys.stderr)
    raise SystemExit(1)


# -- Color mapping per sender -------------------------------------------

SENDER_COLORS = [
    "cyan",
    "green",
    "yellow",
    "magenta",
    "blue",
    "red",
]

_sender_color_map: dict[str, str] = {}


def color_for(sender: str) -> str:
    if sender == "user":
        return "bold white"
    if sender == "system":
        return "dim white"
    if sender not in _sender_color_map:
        idx = len(_sender_color_map) % len(SENDER_COLORS)
        _sender_color_map[sender] = SENDER_COLORS[idx]
    return _sender_color_map[sender]


TYPE_ICONS = {
    "proposal": "[bold]PROPOSAL[/bold]",
    "conflict": "[bold red]CONFLICT[/bold red]",
    "escalation": "[bold red on white] ESCALATION [/bold red on white]",
    "directive": "[bold white on blue] DIRECTIVE [/bold white on blue]",
    "lock-notify": "[dim]LOCK[/dim]",
    "question": "[bold]QUESTION[/bold]",
    "arbitration-request": "[bold yellow]ARBITRATION REQUEST[/bold yellow]",
    "arbitration": "[bold yellow]ARBITRATION[/bold yellow]",
    "system": "[dim]SYSTEM[/dim]",
}


def format_rich_message(m: dict) -> str:
    sender = m["sender"]
    msg_type = m["type"]
    color = color_for(sender)
    icon = TYPE_ICONS.get(msg_type, msg_type)
    reply = f" (re: #{m['reply_to']})" if m.get("reply_to") else ""
    timestamp = m.get("created_at", "")
    if len(timestamp) > 10:
        timestamp = timestamp[11:]  # Show time only

    header = f"[{color}][#{m['id']} {sender}][/{color}] {icon} [{timestamp}]{reply}"
    return f"{header}\n{m['content']}\n"


# -- Widgets ------------------------------------------------------------


class SessionPanel(Static):
    """Shows active sessions."""

    def render_sessions(self, sessions: list[dict]) -> str:
        if not sessions:
            return "[dim]Sessions\n--------\n(none)[/dim]"
        lines = ["[bold]Sessions[/bold]", "--------"]
        for s in sessions:
            status_dot = "[green]*[/green]"
            lines.append(f"  {status_dot} [bold]{s['name']}[/bold]")
            lines.append(f"    {s['role'] or 'no role'}")
            lines.append(f"    {s['directory']}")
            lines.append("")
        return "\n".join(lines)


class LocksPanel(Static):
    """Shows active file locks."""

    def render_locks(self, locks: list[dict]) -> str:
        if not locks:
            return "[dim]Locks\n-----\nNo active locks[/dim]"
        lines = ["[bold]Locks[/bold]", "-----"]
        for lock in locks:
            lines.append(f"  {lock['file_path']}")
            lines.append(f"  -> {lock['owner']}")
            lines.append("")
        return "\n".join(lines)


class StatsPanel(Static):
    """Shows message count and purge info."""

    def render_stats(self, msg_count: int, threshold: str, unread: int) -> str:
        lines = [
            "[bold]Stats[/bold]",
            "-----",
            f"  Unread: {unread}",
            f"  Messages: {msg_count}",
            f"  Purge at: {threshold}",
        ]
        return "\n".join(lines)


# -- Main App -----------------------------------------------------------


class CollabDashboard(App):
    """Smart Team Collaboration Dashboard."""

    CSS = """
    Screen {
        layout: horizontal;
    }
    #message-area {
        width: 3fr;
        height: 100%;
    }
    #sidebar {
        width: 1fr;
        min-width: 20;
        height: 100%;
        border-left: solid $accent;
        padding: 1;
    }
    #message-log {
        height: 1fr;
    }
    #input-area {
        height: 3;
        dock: bottom;
        border-top: solid $accent;
        padding: 0 1;
    }
    #session-panel {
        height: auto;
        margin-bottom: 1;
    }
    #locks-panel {
        height: auto;
        margin-bottom: 1;
    }
    #stats-panel {
        height: auto;
    }
    Header {
        dock: top;
    }
    """

    TITLE = "Smart Team Collaboration"
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(
        self,
        db_path: Path,
        *,
        readonly: bool = False,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._readonly = readonly
        self._last_msg_id = 0

    def compose(self) -> ComposeResult:
        db = CollabDB(self._db_path)
        mode = db.config_get("mode") or "live"
        db.close()

        yield Header()
        with Horizontal():
            with Vertical(id="message-area"):
                yield RichLog(id="message-log", highlight=True, markup=True, wrap=True)
                if not self._readonly:
                    yield Input(
                        placeholder="Type a message (Enter to send, Ctrl+Q to quit)",
                        id="input-area",
                    )
            with Vertical(id="sidebar"):
                yield SessionPanel(id="session-panel")
                yield LocksPanel(id="locks-panel")
                yield StatsPanel(id="stats-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._load_initial_messages()
        self.set_interval(1.5, self._poll_updates)

    def _load_initial_messages(self) -> None:
        db = CollabDB(self._db_path)
        messages = db.message_log(last=50)
        log_widget = self.query_one("#message-log", RichLog)
        for m in messages:
            log_widget.write(format_rich_message(m))
            self._last_msg_id = max(self._last_msg_id, m["id"])
        self._refresh_sidebar(db)
        db.close()

    def _poll_updates(self) -> None:
        db = CollabDB(self._db_path)

        # Check for new messages since last known ID
        all_msgs = db.message_log()
        new_msgs = [m for m in all_msgs if m["id"] > self._last_msg_id]

        if new_msgs:
            log_widget = self.query_one("#message-log", RichLog)
            for m in new_msgs:
                log_widget.write(format_rich_message(m))
                self._last_msg_id = max(self._last_msg_id, m["id"])

        self._refresh_sidebar(db)
        db.close()

    def _refresh_sidebar(self, db: CollabDB) -> None:
        sessions = db.session_list()
        locks = db.lock_list()
        msg_count = db.message_count()
        threshold = db.config_get("purge_threshold") or "20"

        session_panel = self.query_one("#session-panel", SessionPanel)
        session_panel.update(session_panel.render_sessions(sessions))

        locks_panel = self.query_one("#locks-panel", LocksPanel)
        locks_panel.update(locks_panel.render_locks(locks))

        stats_panel = self.query_one("#stats-panel", StatsPanel)
        stats_panel.update(stats_panel.render_stats(msg_count, threshold, 0))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._readonly or not event.value.strip():
            return
        content = event.value.strip()
        db = CollabDB(self._db_path)
        db.message_send("user", content, msg_type="directive")
        db.close()
        event.input.value = ""


# -- Entry point --------------------------------------------------------


def main(argv: list[str]) -> int:
    readonly = "--readonly" in argv
    db_path = find_collab_db()
    app = CollabDashboard(db_path, readonly=readonly)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Test that the TUI module imports without error**

Run: `cd /root/llm-router/tools && python3 -c "import collab_ui; print('import ok')"`
Expected: `import ok` (or textual import error if not installed)

- [ ] **Step 5: Commit**

```bash
chmod +x tools/collab_ui.sh
git add tools/collab_ui.py tools/collab_ui.sh
git commit -m "feat(collab): add TUI dashboard — live message stream, sessions, locks, user input"
```

---

## Task 10: Wire `ui` Command Into CLI

**Files:**
- Modify: `tools/collab.py`

- [ ] **Step 1: Add the ui command handler to collab.py**

Add the following function before the `COMMANDS` dict in `tools/collab.py`:

```python
def cmd_ui(argv: list[str]) -> int:
    readonly = "--readonly" in argv
    script = SCRIPT_DIR / "collab_ui.py"
    import subprocess

    cmd = ["python3", str(script)]
    if readonly:
        cmd.append("--readonly")
    return subprocess.call(cmd)
```

Then add `"ui": cmd_ui` to the `COMMANDS` dict.

- [ ] **Step 2: Test that `collab ui --help` doesn't crash**

Run: `cd /root/llm-router/tools && python3 collab.py help dashboard`
Expected: Prints the dashboard help text without error.

- [ ] **Step 3: Commit**

```bash
git add tools/collab.py
git commit -m "feat(collab): wire ui command into CLI"
```

---

## Task 11: Update hooks.json for Collaboration Hooks

**Files:**
- Modify: `hooks/hooks.json`

- [ ] **Step 1: Read the current hooks.json**

Read: `hooks/hooks.json`

- [ ] **Step 2: Add collaboration hook entries**

Add the collaboration hooks alongside the existing hooks. The PostToolUse entry goes after the existing `run-tests-async.sh` entry. The PreToolUse entry goes after the existing `protect-files.sh` entry.

Add to the `PostToolUse` array:

```json
{
  "matcher": "",
  "hooks": [
    {
      "type": "command",
      "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/collab_check.sh\"",
      "timeout": 5
    }
  ]
}
```

Add to the `PreToolUse` array:

```json
{
  "matcher": "Edit|Write",
  "hooks": [
    {
      "type": "command",
      "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/collab_lock_check.sh\"",
      "timeout": 5
    }
  ]
}
```

- [ ] **Step 3: Validate JSON syntax**

Run: `python3 -c "import json; json.load(open('/root/llm-router/hooks/hooks.json')); print('valid')"`
Expected: `valid`

- [ ] **Step 4: Commit**

```bash
git add hooks/hooks.json
git commit -m "feat(collab): register collaboration hooks in hooks.json"
```

---

## Task 12: Update Smart-Team Command to Support --collaborate

**Files:**
- Modify: `commands/smart-team.md`

- [ ] **Step 1: Read the current smart-team.md command**

Read: `commands/smart-team.md`

- [ ] **Step 2: Add collaboration mode section**

Add a new section at the top of the command file (after the existing title/description) that detects the `--collaborate` flag and routes to collaboration mode. The section should include:

- Detection of `--collaborate`, `--collaborate --connect`, and `--collaborate --end` flags
- tmux preflight check (same as existing Step 0)
- Cold start flow: `collab init` -> session registration -> spawn partner sessions via tmux -> install hooks -> open dashboard
- Hot connect flow: `collab init` if needed -> detect running sessions -> `collab join` each -> install hooks -> open dashboard
- End flow: `collab end` -> remove hooks -> close dashboard
- Setup questions: what to build, which LLMs, roles, mode, escalation, purge settings
- Subteam isolation rule: any `/smart-team` (non-collaborate) spawned by a collaborating session must create agents in a separate tmux session named `collab-<session-name>-team`

The exact markdown content depends on the current file structure — read it first, then add the collaboration section following the same style and formatting conventions.

- [ ] **Step 3: Verify the command file is valid markdown**

Run: `head -5 /root/llm-router/commands/smart-team.md`
Expected: Shows the command header.

- [ ] **Step 4: Commit**

```bash
git add commands/smart-team.md
git commit -m "feat(collab): add --collaborate mode to smart-team command"
```

---

## Task 13: Integration Test — Full Collaboration Flow

**Files:**
- Create: `tools/test_collab_integration.py`

- [ ] **Step 1: Write an integration test that exercises the full flow**

```python
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
```

- [ ] **Step 2: Run the integration tests**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_integration.py -v`
Expected: 3 PASS

- [ ] **Step 3: Commit**

```bash
git add tools/test_collab_integration.py
git commit -m "test(collab): add integration tests for full collaboration flow"
```

---

## Task 14: Update .gitignore and Final Cleanup

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Read current .gitignore**

Read: `.gitignore`

- [ ] **Step 2: Add collaboration artifacts to .gitignore**

Add the following entries:

```
# Collaboration runtime artifacts
.collab/
tools/test_*.py
__pycache__/
*.pyc
```

Note: `.collab/` is gitignored at the plugin level. Individual projects may choose to track their `.collab/history/` directory for audit purposes.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: update gitignore for collaboration artifacts"
```

---

## Task 15: Final Push to GitHub

- [ ] **Step 1: Run all tests to confirm everything passes**

Run: `cd /root/llm-router/tools && python3 -m pytest test_collab_db.py test_collab_cli.py test_collab_integration.py -v`
Expected: All tests PASS

- [ ] **Step 2: Push to GitHub**

```bash
gh auth setup-git
git push origin smart-team-collaboration-v1
```

Expected: Successful push to remote branch.
