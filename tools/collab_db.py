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
        self._conn.execute("PRAGMA foreign_keys = ON")
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
            with self._conn:
                for entry in unread:
                    readers = json.loads(entry["read_by"])
                    if session_name not in readers:
                        readers.append(session_name)
                    self._conn.execute(
                        "UPDATE messages SET read_by = ? WHERE id = ?",
                        (json.dumps(readers), entry["id"]),
                    )

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

    # -- Locks ----------------------------------------------------------

    def lock_claim(self, file_path: str, owner: str) -> bool:
        cursor = self._conn.execute(
            "INSERT INTO locks (file_path, owner) "
            "SELECT ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM locks WHERE file_path = ? AND owner != ?)",
            (file_path, owner, file_path, owner),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            # Either someone else holds it, or we already own it (idempotent)
            existing = self.lock_owner(file_path)
            if existing == owner:
                return True
            return False
        return True

    def lock_release(self, file_path: str, requester: str, *, force: bool = False) -> bool:
        if force:
            self._conn.execute("DELETE FROM locks WHERE file_path = ?", (file_path,))
            self._conn.commit()
            return True
        cursor = self._conn.execute(
            "DELETE FROM locks WHERE file_path = ? AND owner = ?",
            (file_path, requester),
        )
        self._conn.commit()
        return cursor.rowcount > 0

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

    # -- Conflict tracking ----------------------------------------------

    def conflict_round_count(self, root_msg_id: int) -> int:
        rows = self._conn.execute(
            "WITH RECURSIVE chain(id, type, archived) AS ( "
            "  SELECT id, type, archived FROM messages WHERE id = ? "
            "  UNION ALL "
            "  SELECT m.id, m.type, m.archived FROM messages m "
            "  JOIN chain c ON m.reply_to = c.id "
            ") "
            "SELECT id FROM chain "
            "WHERE type IN ('conflict', 'response') AND archived = 0",
            (root_msg_id,),
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

    # -- Helpers --------------------------------------------------------

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
