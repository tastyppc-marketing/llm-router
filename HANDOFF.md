# Handoff — Smart Team v2

**Last updated:** 2026-04-08
**Branch:** `smart-team-v2`
**Latest commit:** `de929c6` (installer + first-run welcome)
**Backup tag:** `backup-before-smart-team-v2` at `75a556b` on `main`

---

## Reading Order

1. This file (orient yourself)
2. `SMART-TEAM-V2-SPEC.md` (the 12 improvements spec — written, not yet implemented)
3. `CHANGELOG.md` (tracks what's planned and what shipped)
4. `commands/smart-team.md` (the current slash command definition)
5. `tools/codex_smart_team.py` (the bridge script)

---

## Current State

### What exists (v1 — shipped, on `main`)
- Three-lane LLM routing: CX-Executor (Codex), CC-Diagnostician (Claude), GI-Mapper (Gemini)
- `/smart-team` slash command with full orchestration lifecycle
- Worker scripts for Claude, Codex, Gemini CLI invocation (pipe/headless mode)
- Failure-mode flow: GI -> CC -> CX -> CC sequential planning
- `/router-validate` and `/router-status` commands
- Safety hooks: file protection, async test runner, task completion gate
- Codex bridge for tmux-based session control
- Agent definitions: CX-Executor, CC-Diagnostician, GI-Mapper, QA, code-reviewer, product-verifier, researcher

### What's on `smart-team-v2` branch (planning docs + installer)
- `SMART-TEAM-V2-SPEC.md` — detailed specs for 12 improvements (not yet implemented)
- `CHANGELOG.md` — version history
- `install.sh` — full installer with prereq checks, plugin registration, settings config, Gemini policy, global availability
- `tools/first_run_welcome.sh` — ASCII art welcome banner shown once after install

### What is NOT yet built
All 12 improvements from the spec are still pending implementation. None have been started.

---

## Decisions Made (This Conversation)

These decisions were discussed and confirmed by the user. Do not re-litigate.

### Architecture decisions
1. **Workers will run in interactive tmux sessions**, not pipe mode. This gives each agent access to its native tools, MCP servers, and plugins. The current pipe mode (`claude -p`, `codex exec`, `gemini -p`) loses access to these.

2. **Agent failure policy: never give up.** Retry 3x on the same model with exponential backoff. If all 3 fail, reroute to a fallback model (CX->CC, GI->CC, CC->CX). Fallback also gets 3 retries. Only escalate to user after both primary and fallback are exhausted.

3. **Auto-routing scorer uses a weighted rule engine**, not ML. Transparent scoring with visible reasoning. Kill switch via `LLM_ROUTER_AUTO_ROUTING=0`. Learner freeze via `LLM_ROUTER_LEARNER_ENABLED=0`. Learner auto-adjusts weights from manifest data but only after meeting a sample threshold (default 10 runs). User gets notified of weight changes and can manually override.

4. **Task dependency graph: the orchestrator auto-detects** which tasks can run in parallel vs. need sequential execution. It analyzes output dependencies and file dependencies. Independent tasks run concurrently. User can see the graph before execution (unless "auto" mode).

5. **`needs_user_input` must NOT kill the session.** Extract the question, present it to the user in the same terminal, accept input, relay it back, and continue the run.

6. **Legacy aliases will be removed.** Delete `codex-specialist.md`, `claude-implementer.md`, `gemini-specialist.md`. Keep only `CX-Executor`, `CC-Diagnostician`, `GI-Mapper`.

7. **Gemini policy path becomes configurable** via `LLM_ROUTER_GEMINI_POLICY_PATH` env var. Default: `~/.gemini/policies/llm-router.toml` using `Path.home()`, not hardcoded.

### Implementation order (confirmed)
```
Group A (sequential, foundational): #1 → #2 → #3
Group B (parallel with A):          #4, #5
Group C (sequential):               #6 → #7
Group D (after A):                  #8, #9
Group E (anytime, quick):           #10, #11, #12
```

### Installer decisions
- Single `install.sh` at repo root handles everything
- Supports `--branch`, `--skip-prereqs`, `--uninstall`
- Symlinks commands and agents to `~/.claude/commands/` and `~/.claude/agents/` for global availability
- Configures `settings.json` with agent teams env var, permissions, and hooks
- Creates Gemini safety policy
- Shows ASCII art welcome + changelog on first run
- Does NOT work for Codex-only installations (Claude Code is required for the plugin system)

---

## The Ecosystem Vision (Improvement #13 — not yet in spec)

This was discussed at length and the user is enthusiastic about it. This is the direction the project is heading — transforming smart-team from a Claude Code plugin into a **true multi-LLM agent platform** with a shared tool ecosystem.

### Core concept
Create a shared middleware layer where tools, skills, data, and services are available to ANY agent regardless of which LLM drives it.

### Three sharing layers

**1. Skills/Methodologies (portable)**
Split each skill into universal logic + per-LLM adapter:
- `core.md` — the methodology (LLM-agnostic)
- `adapters/claude.md` — Claude tool-call translations
- `adapters/codex.md` — Codex tool-call translations
- `adapters/gemini.md` — Gemini tool-call translations

Uses a `tool-map.yaml` that maps equivalent operations across LLMs:
- `Read` (Claude) = `cat` (Codex) = `read_file` (Gemini)
- `Edit` (Claude) = direct write (Codex) = `replace` (Gemini)
- etc.

Example: `/systematic-debugging` methodology works the same way regardless of LLM. The adapter just tells the agent which tool calls to use.

**2. Shared services (MCP-based)**
Run tools as services that all agents connect to:
- Playwright browser automation (shared browser pool, any agent can navigate/scrape/screenshot)
- Database connections
- API clients (Ahrefs, GSC, etc. — already MCP)
- File watchers, build servers

The Playwright scraper was specifically called out by the user as a priority — they want all agents to be able to navigate the browser and share scraped data.

**3. Shared data workspace**
All agents read/write to `~/.smart-team/shared-data/`:
- `scrape-results/` — crawler output
- `research-cache/` — shared findings
- `analysis/` — processed results

No file passing between agents needed — just a shared directory.

### What stays proprietary per LLM
- Claude Code: TeamCreate, TaskCreate, Agent teams, plugin system
- Codex: Sandbox isolation, output schemas, `--full-auto` mode
- Gemini: Policy system, approval modes, multimodal input

### Proposed directory structure
```
~/.smart-team/
  ecosystem.yaml              # Registry of all shared tools/skills/services
  shared-skills/               # Portable skill definitions with adapters
  shared-services/             # MCP servers (Playwright, etc.)
  shared-data/                 # Common workspace
  adapters/                    # Tool-call translation tables
```

### What needs to be built for the ecosystem
1. Tool adapter layer (tool-map.yaml + per-LLM adapters)
2. Skill splitter (extract universal core from existing Claude Code skills)
3. Shared service framework (MCP server launcher for Playwright, etc.)
4. Shared data workspace with conventions
5. Ecosystem registry
6. Installer update for multi-CLI setup
7. Codex compatibility (interactive sessions, shared MCP config)

### User's stated goal
> "I'm trying to create something seamless, powerful, something that is a sort of shared ecosystem of tools."

The user wants agents to share tools like `/systematic-debugging` and Playwright across all LLMs, while each LLM retains its proprietary tools for what it does best.

---

## Two Conversation Threads

The user has forked their thinking into two paths:

### Thread A: Original (this conversation)
- Focus: The 12 improvements from `SMART-TEAM-V2-SPEC.md`
- Scope: Make `/smart-team` better within the current architecture
- Status: Planning complete, ready for implementation starting with #1 (consolidate workers)

### Thread B: Fork (new conversation)
- Focus: The ecosystem vision (#13) — turning smart-team into a multi-LLM agent platform
- Scope: Shared tool ecosystem, Codex compatibility, interactive workers, skill portability
- Status: Vision discussed, not yet specced. New conversation should write the spec.

Both threads work on the same `smart-team-v2` branch. The ecosystem work builds on top of the 12 improvements (especially #1 consolidate workers and the interactive worker sessions).

---

## Rollback Points

| Tag/Commit | Description |
|---|---|
| `backup-before-smart-team-v2` (`75a556b`) | Clean v1 on `main` before any v2 work |
| `6a8ac50` | Spec + changelog committed |
| `de929c6` | Installer + first-run welcome added |

---

## Key Files Reference

| File | Purpose |
|---|---|
| `SMART-TEAM-V2-SPEC.md` | Full spec for 12 improvements with acceptance criteria |
| `CHANGELOG.md` | Version history |
| `install.sh` | One-command installer |
| `commands/smart-team.md` | Current `/smart-team` slash command |
| `tools/codex_smart_team.py` | Bridge script (Codex -> Claude Code tmux) |
| `tools/claude_tmux.py` | Shared tmux orchestration helpers |
| `tools/router_common.py` | Shared utilities (retry, manifest, file I/O) |
| `tools/claude_worker.py` | Claude worker (pipe mode, to be upgraded) |
| `tools/codex_worker.py` | Codex worker (pipe mode, to be upgraded) |
| `tools/gemini_worker.py` | Gemini worker (pipe mode, to be upgraded) |
| `skills/llm-routing/references/routing-heuristics.md` | Current routing decision matrix |
| `hooks/hooks.json` | Plugin hook definitions |
| `.claude-plugin/plugin.json` | Plugin manifest |
