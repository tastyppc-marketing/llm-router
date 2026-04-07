# Changelog

All notable changes to the llm-router plugin are documented here.

---

## [Unreleased] — smart-team-v2 branch

### Planned (see SMART-TEAM-V2-SPEC.md for full details)

#### Foundational
- **Consolidate worker boilerplate** — Extract `BaseWorker` class from the three near-identical worker scripts (claude/codex/gemini), reducing each to < 60 lines
- **Agent failure retry + fallback routing** — 3 retries on same model, then auto-reroute to fallback model; never silently give up
- **Stale run cleanup** — Auto-prune run artifact directories (keep last 20 or 7 days, configurable)

#### UX & Visibility
- **Live progress reporting** — Real-time milestone events printed to caller's terminal during smart-team runs
- **`needs_user_input` relay** — Extract questions, present to user, relay answers back without killing the session
- **Run analytics (`/router-analytics`)** — Query manifest data for success rates, durations, failure patterns per model

#### Intelligence & Performance
- **Auto-routing scorer** — Weighted rule engine with transparent scoring, optional auto-learning from manifest outcomes, easy kill switch
- **Task dependency graph** — Explicit dependency declarations enabling parallel execution of independent tasks
- **Resumable runs** — Checkpoint state after each task; resume interrupted runs with `--resume`

#### Cleanup
- **Remove legacy agent aliases** — Delete `codex-specialist`, `claude-implementer`, `gemini-specialist` in favor of `CX-Executor`, `CC-Diagnostician`, `GI-Mapper`
- **Configurable Gemini policy path** — Read from `LLM_ROUTER_GEMINI_POLICY_PATH` env var instead of hardcoded path
- **Unified hook test detection** — Shared test framework detection for `run-tests-async.sh` and `task-complete-gate.sh`

---

## [1.0.0] — 2026-04-05

**Tag:** `backup-before-smart-team-v2` (`75a556b`)

### Initial Release
- Three-lane LLM routing: CX-Executor (Codex), CC-Diagnostician (Claude), GI-Mapper (Gemini)
- `/smart-team` slash command with full orchestration lifecycle
- Worker scripts for Claude, Codex, and Gemini CLI invocation with retry and artifact logging
- Failure-mode flow: GI -> CC -> CX -> CC sequential planning
- `/router-validate` and `/router-status` commands for infrastructure smoke testing
- Safety hooks: file protection, async test runner, task completion gate
- Codex bridge (`codex_smart_team.py`) for tmux-based interactive session control
- Agent definitions: CX-Executor, CC-Diagnostician, GI-Mapper, QA-tester, code-reviewer, product-verifier, researcher
