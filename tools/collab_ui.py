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
