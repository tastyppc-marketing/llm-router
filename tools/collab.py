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
        "  send --name NAME [--type T] [--reply-to ID] MESSAGE\n"
        "  check --name NAME [--peek] [--format inject]\n"
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


PARTNER_PROMPT_TEMPLATE = """\
# Collaboration Mode — Active

You are in a multi-LLM collaboration session. Other LLM sessions are working
on this project with you. You communicate using the `collab` CLI.

## Your identity
- Name: PARTNER_NAME
- Role: PARTNER_ROLE

## Communication protocol

**Check for messages regularly.** After every significant action you take,
run this command to see if your collaborators have sent you anything:

```
python3 /root/llm-router/tools/collab.py check --name "PARTNER_NAME" --format inject
```

**Send messages to share your work.** When you complete something, make a
proposal, have a question, or disagree with something, tell your collaborators:

```
python3 /root/llm-router/tools/collab.py send --name "PARTNER_NAME" --type status "What you did or plan to do"
python3 /root/llm-router/tools/collab.py send --name "PARTNER_NAME" --type proposal "Your suggestion"
python3 /root/llm-router/tools/collab.py send --name "PARTNER_NAME" --type question "Your question"
python3 /root/llm-router/tools/collab.py send --name "PARTNER_NAME" --type conflict --reply-to <id> "Why you disagree"
```

**Lock files before editing.** Before you edit any file, claim it:

```
python3 /root/llm-router/tools/collab.py lock <file-path> --name "PARTNER_NAME"
```

Release it when done:

```
python3 /root/llm-router/tools/collab.py unlock <file-path> --name "PARTNER_NAME"
```

**Check messages after every tool call.** This is critical — your collaborators
may have sent you proposals, questions, or conflicts that need your attention.
Always check before starting new work.

## Rules
- Be a peer, not a follower. Push back if you disagree, with reasoning.
- If someone locks a file, don't edit it. Message them to coordinate.
- If a conflict can't be resolved in 3 rounds, it escalates.
- User directives (type: "directive") always take priority.
"""


def cmd_init(argv: list[str]) -> int:
    from collab_db import CollabDB

    db_path = Path.cwd() / ".collab" / "collab.db"
    if db_path.exists():
        print("Collaboration already initialized.", file=sys.stderr)
        return 0
    CollabDB(db_path).close()

    # Write partner prompt template
    prompt_path = Path.cwd() / ".collab" / "partner-prompt.md"
    if not prompt_path.exists():
        prompt_path.write_text(PARTNER_PROMPT_TEMPLATE)

    print(f"Collaboration initialized at .collab/")
    print(f"Partner prompt template at .collab/partner-prompt.md")
    print(f"Edit PARTNER_NAME and PARTNER_ROLE before spawning partners.")
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

    # Generate a personalized partner prompt for this session
    collab_dir = find_collab_db().parent
    prompt_path = collab_dir / f"prompt-{name}.md"
    prompt_content = PARTNER_PROMPT_TEMPLATE.replace("PARTNER_NAME", name).replace("PARTNER_ROLE", role or "collaborator")
    prompt_path.write_text(prompt_content)

    print(f"Joined collaboration as '{name}' (role: {role or 'unset'})")
    print(f"Session prompt written to .collab/prompt-{name}.md")
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
            try:
                reply_to = int(argv[i])
            except ValueError:
                print("Error: --reply-to must be an integer.", file=sys.stderr)
                return 1
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
    # Validate sender is a registered session (allow "user" and "system" as special senders)
    if name not in ("user", "system"):
        session = db.session_get(name)
        if session is None:
            db.close()
            print(f"Error: '{name}' is not a registered session. Run 'collab join --name {name}' first.", file=sys.stderr)
            return 1
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
            try:
                last = int(argv[i])
            except ValueError:
                print("Error: --last must be an integer.", file=sys.stderr)
                return 1
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
            try:
                count = int(argv[i])
            except ValueError:
                print("Error: --count must be an integer.", file=sys.stderr)
                return 1
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


def cmd_ui(argv: list[str]) -> int:
    ui_script = SCRIPT_DIR / "collab_ui.py"
    if not ui_script.exists():
        print("Error: TUI dashboard (collab_ui.py) not yet installed.", file=sys.stderr)
        return 1
    import subprocess
    return subprocess.call(["python3", str(ui_script), *argv])


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
    "ui": cmd_ui,
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
