---
name: llm-routing
description: Trigger when user asks for smart team / agent spawning / CC/CX/GI routing. Enforces strict delegation, ownership assignment, and tmux verification.
version: 1.2.0
---

# Smart LLM Routing Skill

Use this skill when user asks for smart team behavior, agent spawning, or CC/CX/GI routing.

## Core requirements

1. Use real Agent Teams (`TeamCreate`, `Agent`, `TaskCreate`, `TaskUpdate`, `SendMessage`).
2. Spawn teammates with `team_name` + `run_in_background: true`.
3. Enforce explicit task ownership (`TaskUpdate owner=...`) for every task.
4. Team lead does orchestration only; no direct implementation edits.
5. Validate tmux backend and pane IDs after spawn.
6. Use a clarification budget: at most 5 blocking questions per implementation task before coding starts.

## Routing process

For each implementation task:
- Ask user which LLM should handle it unless user requested auto mode.
- Recommend `CX-Executor` for constrained generation/test/scaffolding tasks.
- Recommend `CC-Diagnostician` for architecture/security/complex multi-file reasoning and user-facing clarification wording.
- Recommend `GI-Mapper` for large-context synthesis, documentation-heavy work, multimodal mapping, and decomposition.

If user says auto:
- Apply recommendation directly.

Default failure-mode flow:
- `GI-Mapper` for map and scope when context is large or ambiguous
- `CC-Diagnostician` for risky logic, invariants, and question wording
- `CX-Executor` for execution and test loops
- `CC-Diagnostician` again for risky final review when needed

## Clarification protocol

Before implementation starts:
- Require a gap check against repo context, tests, docs, and prior task messages.
- Allow at most 5 blocking questions per implementation task.
- Prefer teammate-to-teammate clarification first when another routed model can likely answer from context.
- Ask the user only when the answer changes correctness, scope, UX, or an irreversible choice.
- If `CX-Executor` or `GI-Mapper` identifies a blocker that must be shown to the user, route it through `CC-Diagnostician` so Claude rewrites the final user-facing wording.
- For non-blocking uncertainty, proceed with explicit assumptions instead of asking.
- Permit only one clarification round before proceeding with assumptions or escalating a true blocker.

## Kickoff template

For a reusable `/smart-team` starter prompt, see:
- `~/.claude/plugins/llm-router/skills/llm-routing/references/kickoff-template.md`

## Validation utilities

Use these when validating router health before real build work:
- `~/.claude/plugins/llm-router/tools/router_validate.sh` for reusable wrapper/team smoke runs
- `~/.claude/plugins/llm-router/tools/router_status.sh` for latest wrapper/team status
- `/router-validate` for an interactive validation command
- `/router-status` for a compact readiness summary

## tmux/backend verification

After agent spawn, inspect `~/.claude/teams/<team>/config.json`.

Every intended pane-visible agent should show:
- `backendType: "tmux"`
- non-empty `tmuxPaneId`

If not, report and respawn once.

## Codex reliability rules

For `CX-Executor` tasks:
- Assign to `CX-Executor`.
- Ensure `CX-Executor` calls:
  - `"$HOME/.claude/plugins/llm-router/tools/codex_worker.sh" "<task prompt>"`
- Ensure `CX-Executor` starts with either `No blocking questions` or `Blocking questions (max 5)` plus proposed defaults
- Ensure `CX-Executor` sends raw blocker questions to `CC-Diagnostician` for final wording if the user must be asked
- If Codex run is blocked/denied/fails, escalate the issue to user; do not silently replace with Claude implementation.

For `CC-Diagnostician` tasks:
- Assign to `CC-Diagnostician`.
- Ensure `CC-Diagnostician` calls:
  - `"$HOME/.claude/plugins/llm-router/tools/claude_worker.sh" "<task prompt>"`
- Ensure `CC-Diagnostician` starts with either `No blocking questions` or `Blocking questions (max 5)` plus proposed defaults
- Ensure `CC-Diagnostician` acts as the wording layer for user-facing clarification when another agent found the blocker
- If Claude run is blocked/denied/fails, escalate the issue to user; do not silently replace with another implementation path.

For `GI-Mapper` tasks:
- Assign to `GI-Mapper`.
- Ensure `GI-Mapper` calls:
  - `"$HOME/.claude/plugins/llm-router/tools/gemini_worker.sh" "<task prompt>"`
- Ensure `GI-Mapper` starts with either `No blocking questions` or `Blocking questions (max 5)` plus proposed defaults
- Ensure `GI-Mapper` sends raw blocker questions to `CC-Diagnostician` for final wording if the user must be asked
- If Gemini run is blocked/denied/fails, escalate the issue to user; do not silently replace with another implementation path.

## Completion checks

Before finalizing:
- No implementation tasks left pending/unassigned.
- QA and review completed for each implementation.
- `CX-Executor` tasks have Codex run evidence in:
  - `~/.claude/plugins/llm-router/tools/codex_runs/manifest.jsonl`
- `CC-Diagnostician` tasks have Claude run evidence in:
  - `~/.claude/plugins/llm-router/tools/claude_runs/manifest.jsonl`
- `GI-Mapper` tasks have Gemini run evidence in:
  - `~/.claude/plugins/llm-router/tools/gemini_runs/manifest.jsonl`
- Team is shutdown cleanly and deleted.
