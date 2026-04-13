# Smart Team Collaboration Mode — Design Spec

**Date:** 2026-04-13
**Status:** Draft
**Branch:** `smart-team-collaboration-v1`
**Scope:** v1 — local machine only (cross-machine is a future direction)

---

## Overview

Smart Team Collaboration is a new mode within the existing `/smart-team` skill that enables persistent, peer-to-peer collaboration between independent LLM sessions (Claude Code, Codex, Gemini, etc.) working on the same project.

Unlike the existing smart-team dispatch model — where an orchestrator spins up subagents that do scoped jobs and return — collaboration mode creates **persistent, interactive sessions** that run for the entire duration of a build. They communicate bidirectionally, debate decisions, and coordinate work as peers.

### How it differs from existing smart-team

| | Existing smart-team | Collaboration mode |
|---|---|---|
| Session lifetime | Ephemeral — do a job, return | Persistent — alive for the full build |
| Communication | Sequential hand-offs | Ongoing peer-to-peer conversation |
| Hierarchy | Orchestrator dispatches workers | Peer-to-peer with domain authority |
| Environment | Subagents share the orchestrator's context | Each session has its own independent environment |

---

## Section 1: Core Architecture

### Components

1. **`collab` CLI tool** — A Python CLI that wraps a SQLite database. Every session interacts with collaboration exclusively through this CLI. No direct DB access. Lives at `~/.claude/plugins/llm-router/tools/collab.py` with a shell shim `collab.sh`, following the same pattern as the existing worker scripts.

2. **SQLite database** (`collab.db`) — One per collaboration. Stores messages, file locks, session registry, and config. Lives in the project directory at `<project-root>/.collab/collab.db`.

3. **Hook-based injection** — Each participating session has a PostToolUse hook that runs `collab check` and injects new messages into the session's context at natural boundaries (after a tool completes, before the next action starts).

4. **Session registry** — Each participant registers on join with their name, role, working directory, and preferences.

5. **TUI dashboard** — A `textual`-based terminal UI that shows the conversation in real time, active sessions, file locks, and lets the user type messages to participate.

### Project directory structure

```
<project-root>/
    .collab/
        collab.db          # SQLite: messages, locks, sessions, config
        history/           # Exported archive segments
```

One active collaboration per project in v1.

### Lifecycle

```
Start → Register sessions → Collaborate (messages + locks) → Purge as needed → End → Archive
```

---

## Section 2: Communication Protocol

### Message types

| Type | Purpose | Example |
|------|---------|---------|
| `proposal` | Suggesting an approach or change | "I think we should use SSE instead of WebSocket" |
| `response` | Replying to a proposal or question | "Agree, but let's add a REST fallback" |
| `status` | Sharing progress or intent | "Just finished the auth middleware, moving to routes" |
| `conflict` | Flagging a disagreement with reasoning | "I disagree with the schema because X" |
| `question` | Asking another session something | "Are you handling validation or should I?" |
| `lock-notify` | Automated — sent on file lock/release | "Locked: src/api/stream.ts" |
| `escalation` | Unresolved conflict, needs user input | "We disagree on X. Here are both positions..." |
| `directive` | Message from the user (via TUI) | "Both of you, change direction — use GraphQL" |
| `arbitration-request` | System-generated, sent to neutral session | "Review these two positions and decide" |
| `arbitration` | Neutral session's binding decision | "Go with position B because..." |
| `system` | System notifications (subteam spawned, etc.) | "claude-code spawned a subteam" |

### SQLite schema

```sql
sessions (
    name        TEXT PRIMARY KEY,
    role        TEXT,
    directory   TEXT,
    mode        TEXT DEFAULT 'live',
    escalation  TEXT DEFAULT 'user',
    joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen   TIMESTAMP
)

messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender      TEXT NOT NULL,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL,
    reply_to    INTEGER REFERENCES messages(id),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_by     TEXT DEFAULT '[]',
    archived    INTEGER DEFAULT 0
)

locks (
    file_path   TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    claimed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)

config (
    key         TEXT PRIMARY KEY,
    value       TEXT
)
```

### Conversation modes (set per session at join, changeable anytime)

- **Live** — Sessions check mail after every tool call. High communication, real-time feel. Best for planning and tight coordination.
- **Event-driven** — Sessions check mail after completing a task or at explicit checkpoints. Lower noise, more autonomy. Best for heads-down parallel implementation.

### Threading

Messages reference each other via `reply_to`. This creates conversation threads that the conflict resolution system uses to track debate rounds.

### Read tracking

The `read_by` field (JSON array of session names) tracks which sessions have seen each message. `collab check` queries for messages where the current session isn't in `read_by`, returns them, and marks them read.

---

## Section 3: The `collab` CLI Tool

Every session interacts with collaboration through this CLI. No session ever touches SQLite directly.

### Commands

```bash
# Session management
collab init                          # Create .collab/ in project root
collab join --name "claude-code" --role "planner" --dir "./src"
collab leave --name "codex"          # Graceful departure, releases all locks
collab status                        # Who's online, active locks, unread count, mode

# Messaging
collab send "message here"           # Send (default type: status)
collab send --type proposal "Let's use Redis for caching"
collab send --type conflict --reply-to 14 "I disagree because..."
collab send --type question "Are you handling auth?"
collab check                         # Return unread messages, mark them read
collab check --peek                  # Return unread WITHOUT marking read
collab check --format inject --name "claude-code"  # Hook format
collab log                           # Full conversation history
collab log --last 10                 # Last 10 messages

# File locking
collab lock src/api/routes.ts        # Claim a file
collab unlock src/api/routes.ts      # Release
collab unlock src/api/routes.ts --force  # Force-release someone else's lock
collab locks                         # Show all active locks
collab lock-check <path> --name <session>  # PreToolUse hook check

# Mode switching
collab mode live
collab mode event-driven

# Maintenance
collab purge                         # Archive oldest N messages per config
collab purge --count 10              # Override: archive oldest 10
collab archive                       # Export full history, reset active
collab end                           # End collaboration, archive everything

# Config
collab config set purge_threshold 20
collab config set purge_count 5
collab config set escalation user     # Per-session escalation mode
collab config set escalation autonomous
collab config set max_rounds 3        # Conflict rounds before escalation
collab config get mode

# Dashboard
collab ui                            # Launch TUI dashboard
collab ui --follow                   # Auto-scroll (default)
collab ui --readonly                 # Watch only, no input bar

# Help
collab help                          # Full overview
collab help start                    # Getting started
collab help messaging                # Message types and threading
collab help conflicts                # Conflict resolution
collab help locks                    # File locking
collab help dashboard                # TUI guide
collab help config                   # All settings
```

### Message injection format (what the hook delivers)

When there are new messages:

```markdown
[Collaboration] 2 new messages:

[#14 codex | proposal | 14:32:01]
I think we should split the API into separate router files per resource.

[#15 codex | lock-notify | 14:32:05]
Locked: src/models/user.ts

Reply with: collab send --reply-to <id> "your response"
```

When an escalation or user directive arrives:

```markdown
[Collaboration] ESCALATION requires your attention:

[#31 escalation | from codex + claude-code | 14:40:00]
Unresolved conflict after 3 rounds:
- claude-code position: Single PostgreSQL DB
- codex position: PostgreSQL + Redis for sessions
Both sides request user decision.

Pause current work and respond to this escalation.
```

---

## Section 4: TUI Dashboard

Built with Python `textual`. Runs in any terminal.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Smart Team Collaboration: <collab-id>          Mode: Live  │
├────────────────────────────────────────────┬────────────────┤
│                                            │  Sessions      │
│  [14:32:01] claude-code (proposal)         │  * claude-code │
│  I think we should use SSE for the live    │    planner     │
│  data feed instead of WebSocket.           │    ./src       │
│                                            │                │
│  [14:32:15] codex (response)               │  * codex       │
│  Agree. I'll wire up the SSE endpoint.     │    implementer │
│                                            │    ./src       │
│  [14:32:20] codex (lock-notify)            │                │
│  Locked: src/api/stream.ts                 │  * gemini      │
│                                            │    researcher  │
│  [14:33:01] gemini (status)                │    ./docs      │
│  Finished mapping all API dependencies.    ├────────────────┤
│                                            │  Locks         │
│  [14:34:10] codex (conflict)               │  src/api/      │
│  Re: #14 — SSE won't work for admin,      │   stream.ts    │
│  it needs push. Suggest hybrid approach.   │   -> codex     │
│                                            │                │
│  [14:34:30] claude-code (response)         │  src/models/   │
│  Good catch. Agreed — hybrid approach.     │   user.ts      │
│  Conflict resolved.                        │   -> claude    │
│                                            ├────────────────┤
│                                            │  Unread: 0     │
│                                            │  Messages: 24  │
│                                            │  Purge at: 30  │
├────────────────────────────────────────────┴────────────────┤
│  > Type a message (Enter to send, Ctrl+Q to quit)           │
│  > _                                                        │
└─────────────────────────────────────────────────────────────┘
```

### Features

- **Live message stream** — polls SQLite every 1-2 seconds. Color-coded by sender.
- **Session panel** — active participants, roles, directories. Green for active, gray for inactive.
- **Locks panel** — current file locks with owner. Stale locks shown in different color.
- **Stats footer** — unread count, total messages, purge threshold proximity.
- **User input** — type messages at the bottom. Sent with `sender: "user"` and `type: "directive"`. LLMs treat user directives as highest priority.
- **Message type indicators** — proposals, conflicts, and escalations get visual treatment (colors, icons) for quick scanning.
- **Conflict highlighting** — active conflicts get warning indicators. Escalations needing user input get a persistent banner until resolved.
- **Subteam notifications** — when a session spawns a subteam, the dashboard shows the tmux session name and attach command.
- **Auto-scroll** with ability to scroll back through history.

---

## Section 5: Hook-Based Message Injection

### PostToolUse hook (message delivery)

Fires after every tool call. Runs:

```bash
collab check --format inject --name "$SESSION_NAME"
```

Behavior by mode:

| Mode | When hook fires | Effect |
|------|----------------|--------|
| Live | After every tool call | Real-time conversation feel |
| Event-driven | Only after task completion or explicit check | Uninterrupted deep work |

### PreToolUse hook (file lock awareness)

Before any `Edit` or `Write`, runs:

```bash
collab lock-check "$FILE_PATH" --name "$SESSION_NAME"
```

Three outcomes:

1. **Unlocked** — auto-claims for current session. Proceeds normally.
2. **Owned by current session** — proceeds.
3. **Owned by someone else** — returns warning with owner and timestamp. Session should coordinate before editing.

Lock auto-releases via PostToolUse after the edit completes.

### Hook installation

When a session runs `collab join`, the appropriate hook config is generated. For Claude Code (`.claude/settings.json`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "command": "collab check --format inject --name claude-code",
        "timeout": 5000
      }
    ],
    "PreToolUse": [
      {
        "command": "collab lock-check \"$FILE_PATH\" --name claude-code",
        "timeout": 5000
      }
    ]
  }
}
```

Equivalent configs generated for Codex and Gemini hook systems.

### Safeguards

- **Timeout:** 5 seconds max. If SQLite is locked or slow, hook silently fails rather than blocking the session.
- **Debounce:** If last check was less than 2 seconds ago, skip. Prevents rapid-fire tool calls from hammering the DB.
- **Mid-thought protection:** Hook fires only between actions — after a tool completes, before the next action starts. Cannot interrupt mid-generation.
- **Priority escalation:** Escalation and directive messages get prominent banners in injection output.

---

## Section 6: Conflict Resolution

### Per-session settings (configurable at join or anytime mid-session)

```bash
collab config set escalation user          # Conflicts escalate to the user
collab config set escalation autonomous    # Bring in a neutral AI arbitrator
collab config set max_rounds 3             # Rounds before escalation (default: 3)
```

These take effect immediately, even on in-progress conflicts.

### Resolution flow

```
Session A sends proposal
        |
Session B disagrees -> sends conflict (round 1)
        |
    Back and forth (response <-> response)
        |
    Round limit hit?
        |
   +----+----+
   No        Yes
   |         |
 Continue    Check escalation setting
             |
        +----+----------+
        |               |
   mode: user     mode: autonomous
        |               |
   Escalation msg    Find a session NOT
   sent to user      involved in the
   via TUI           conflict
        |               |
   User sends           |
   directive         Neutral session
                     receives arbitration
                     request
                        |
                     Reviews both
                     positions, decides
                     with reasoning
                        |
                     Decision posted as
                     type: "arbitration"
                     (binding)
                        |
                     Both sides proceed
```

### Domain authority

Before a conflict reaches escalation:

1. **The session that owns the relevant domain gets first say.** Planner owns the plan, implementer owns implementation details.
2. **If the other side disagrees, they debate** — up to `max_rounds` exchanges.
3. **The domain owner's position carries more weight** but isn't a veto.

### Autonomous arbitration

When `escalation` is set to `autonomous` and the round limit is hit:

1. System identifies sessions NOT involved in the conflict thread.
2. A neutral session receives an `arbitration-request` with both positions and context.
3. The neutral session evaluates and posts an `arbitration` decision with reasoning.
4. Both conflicting sessions see the decision and proceed with it.

Example arbitration request:

```markdown
[Arbitration Request] You've been asked to resolve a conflict.

Position A (claude-code):
"Single PostgreSQL DB. Simpler ops, transactional consistency."

Position B (codex):
"PostgreSQL for relational data, Redis for session cache.
Sessions are ephemeral and high-frequency."

Context: Building an API with user auth and real-time data feeds.

Review both positions and decide which is stronger, or propose
a synthesis. Post your decision with reasoning.
```

### Edge cases

- **Only 2 sessions, mode is autonomous:** No neutral party available. Falls back to user escalation with message: "No neutral session available for arbitration. Escalating to user."
- **Neutral session is busy:** Arbitration request goes into their inbox. Picked up at next hook check. Conflicting sessions told "arbitration pending, continue non-conflicting work."
- **Multiple simultaneous conflicts:** Each tracked independently by `reply_to` chains. Different neutrals can arbitrate different conflicts.
- **User overrides arbitration:** User can always send a `directive` via TUI that overrides any arbitration decision. User has final authority.

### Transparency

All conflicts and resolutions are reported to the user via the TUI dashboard, regardless of escalation mode. The user always sees what's happening — they just don't have to act on it unless they want to (or unless escalation mode is `user`).

---

## Section 7: Entry Points

Three ways to start a collaboration, all within `/smart-team`:

### Cold Start (most common)

```
/smart-team --collaborate
```

Run from an interactive LLM session. Walks through setup:

1. What are we building?
2. Which LLMs to bring in?
3. Role for each?
4. Conversation mode? (live / event-driven)
5. Conflict resolution? (user / autonomous, per session or global default)
6. Purge settings? (threshold / count, or accept defaults)

Then automatically:
- Runs `collab init` in the project root
- Joins the current session
- Spawns each additional session in new tmux panes
- Runs `collab join` in each with appropriate config
- Installs hooks in each session
- Opens TUI dashboard in a separate pane

### Hot Connect (link existing sessions)

```
/smart-team --collaborate --connect
```

For when you already have sessions running and want to wire them together.

1. Runs `collab init` if `.collab/` doesn't exist
2. Identifies running sessions (auto-detects tmux panes or asks)
3. Runs `collab join` in each
4. Installs hooks
5. Opens TUI

Key difference from cold start: doesn't spawn new sessions, wires up existing ones.

### Manual (full control)

In each session, run:

```bash
collab init                    # First session only
collab join --name "claude-code" --role "planner"
```

In the other session:

```bash
collab join --name "codex" --role "implementer"
```

Optionally:

```bash
collab ui                      # Open dashboard
```

No automation, no tmux assumptions. For power users with exotic setups.

### Ending a collaboration

```
/smart-team --collaborate --end
```

Or: `collab end`

1. Sends system message to all sessions: "Collaboration ending"
2. Archives full conversation to `history/`
3. Removes hooks from all sessions
4. Closes TUI
5. Leaves `.collab/` in place with archive for review

---

## Section 8: File Locking

### How it works

Locks are per-file, advisory, and automatic.

**Claiming:** The PreToolUse hook auto-claims a lock when a session starts editing a file. Sessions can also manually claim with `collab lock <path>`.

**Checking:** Before any `Edit` or `Write`, the hook runs `collab lock-check`. If someone else holds the lock, a warning is returned with the owner and timestamp.

**Releasing:** The PostToolUse hook auto-releases after an edit completes. Also released by `collab unlock`, `collab leave`, and `collab end`.

### Staleness timeout

If a session crashes without releasing locks, a configurable staleness timeout (default: 10 minutes) allows other sessions to claim the lock. The TUI shows stale locks in a different color.

### Force release

```bash
collab unlock src/api/routes.ts --force
```

Any session can force-release another session's lock. This sends a `lock-notify` message to the owner: "claude-code force-released your lock on src/api/routes.ts."

### Lock granularity

Per-file only. Locking `src/api/routes.ts` does not affect `src/api/middleware.ts`. This keeps sessions from blocking each other unnecessarily.

---

## Section 9: Subteam Isolation

### The rule

Collaboration lives in the primary tmux session. Any subteams spawned by a collaborating session go into their own separate tmux session.

### Why

If Claude Code and Codex are collaborating (3 panes: Claude, Codex, Dashboard) and one of them spawns a smart-team with QA, reviewer, and verifier — that's 3 more panes. If both spawn teams, that's 6 more. The primary session becomes unreadable.

### How it works

When a collaborating session spawns a subteam:

1. A new tmux session is created: `collab-<session-name>-team` (e.g., `collab-claude-team`)
2. All subteam agents spawn in panes within that session
3. The collaboration dashboard shows a notification:

```
[System] claude-code spawned a subteam: "collab-claude-team"
   Agents: qa-tester, code-reviewer, product-verifier
   To watch: tmux attach -t collab-claude-team
```

4. When the subteam finishes:

```
[System] claude-code's subteam "collab-claude-team" completed and shut down.
```

### Primary session layout stays clean

```
tmux session: "collab" (your main view)
+----------------+----------------+----------------+
| Claude Code    | Codex          | Dashboard      |
| (interactive)  | (interactive)  |                |
+----------------+----------------+----------------+

tmux session: "collab-claude-team" (attach to watch)
+----------+----------+----------+
| QA       | Reviewer | Verifier |
+----------+----------+----------+

tmux session: "collab-codex-team" (attach to watch)
+----------+----------+
| GI-Map   | QA       |
+----------+----------+
```

---

## Section 10: Built-in Help System

### Access

- `/smart-team --collaborate --help` — full overview
- Or ask naturally: "How does smart team collaboration work?"

### Help topics

| Command | Shows |
|---------|-------|
| `--help` | Full overview |
| `--help start` | The three entry points with examples |
| `--help messaging` | Message types, threading, how to reply |
| `--help conflicts` | Conflict resolution, escalation, arbitration |
| `--help locks` | File locking |
| `--help dashboard` | TUI panels and how to interact |
| `--help config` | All settings with defaults and examples |

### Design principles

- **Visual structure** — boxes, dividers, alignment. Easy to scan.
- **Plain language** — "Tell your LLM" not "invoke the orchestrator." No jargon.
- **Examples first** — show what to do before explaining why.
- **Short** — each topic fits in one screen.
- **Friendly tone** — a guide, not documentation.

### Prerequisites section (shown first in all help)

The help system leads with a tmux explainer:

```
Before You Start
----------------
  Collaboration mode needs tmux to work.

  When you collaborate, multiple LLM sessions run side by side —
  your session, your partner's session, and the dashboard. tmux
  is what makes that possible. Think of it like a window manager
  for your terminal: it splits your screen into panes, each
  running independently.

  If you're not already in tmux, start it:
    tmux new -s collab

  Then launch your LLM inside it and you're ready to go.

  Not sure if you're in tmux? Run:
    echo $TMUX
  If it prints something, you're in. If blank, you're not.
```

During cold start, if the LLM detects it's not in tmux, it stops with a friendly message:

```
Collaboration mode needs tmux to run multiple sessions side by side.
You're not in a tmux session right now. Start one with:
  tmux new -s collab
Then launch me again inside it and we'll get going.
```

---

## Section 11: Purge and Maintenance

### Sliding window purge

Messages accumulate during collaboration. The purge system keeps the active mailbox lean.

**Config:**

| Setting | Default | Description |
|---------|---------|-------------|
| `purge_threshold` | 20 | Max messages before auto-purge triggers |
| `purge_count` | 5 | How many oldest messages to archive per purge |

**Behavior:** When message count exceeds `purge_threshold`, the oldest `purge_count` messages are moved to the `history/` directory (not deleted). The audit trail always survives.

**Manual purge:**

```bash
collab purge              # Use configured count
collab purge --count 10   # Override: archive 10
```

**Full archive:**

```bash
collab archive            # Export everything to history/, reset active
```

### History format

Archived messages are exported as timestamped markdown files in `history/` for human readability and git-friendliness.

---

## Section 12: Configuration Summary

### Global defaults (set in config table)

| Key | Default | Description |
|-----|---------|-------------|
| `mode` | `live` | Default conversation mode for new sessions |
| `escalation` | `user` | Default escalation mode for new sessions |
| `max_rounds` | `3` | Conflict debate rounds before escalation |
| `purge_threshold` | `20` | Messages before auto-purge |
| `purge_count` | `5` | Messages archived per purge |
| `lock_staleness` | `600` | Seconds before a lock is considered stale |

### Per-session overrides

Each session can override `mode`, `escalation`, and `max_rounds` independently:

```bash
collab config set mode event-driven          # This session only
collab config set escalation autonomous      # This session only
collab config set max_rounds 5               # This session only
```

---

## Future Directions (not in v1)

- **Cross-machine collaboration** — Replace SQLite with a lightweight server or networked database. The `collab` CLI interface stays the same; only the backend changes.
- **Multiple simultaneous collaborations per project** — Nest under `<collab-id>/` directories.
- **Web UI** — Browser-based dashboard alternative to the TUI, reading from the same SQLite backend.
- **Persistent session memory** — Sessions remember context from previous collaborations on the same project.
