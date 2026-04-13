---
description: Bootstrap a smart multi-agent team with strict CC/CX/GI routing, task ownership, and tmux visibility checks.
argument-hint: [task description]
allowed-tools: [Read, Glob, Grep, Bash, Agent, TeamCreate, TeamDelete, TaskCreate, TaskUpdate, TaskList, TaskGet, AskUserQuestion, SendMessage, Skill]
---

# /smart-team — Agent Teams with CC/CX/GI Routing

$ARGUMENTS

## Collaboration Mode

When the user's input contains `--collaborate`, route to collaboration mode instead of the normal smart-team flow. Recognize three variants:

| Flag | Meaning |
|---|---|
| `--collaborate` | Cold start (default) |
| `--collaborate --connect` | Hot connect to existing sessions |
| `--collaborate --end` | End collaboration |

### tmux preflight

Same requirement as Step 0 — you must be inside tmux. Run:

```bash
echo "TMUX=${TMUX:-}"; tmux list-sessions
```

If not inside tmux, tell the user with a friendly nudge:

> Hey — collaboration mode needs tmux so we can manage panes for each partner. Start a tmux session first and re-run `/smart-team --collaborate`.

Do not proceed until tmux is confirmed.

### Cold start flow (`--collaborate`)

1. **Check tmux availability** — run the preflight above.

2. **Ask setup questions** — gather the following before doing anything else:
   - What are we building?
   - Which LLMs to bring in? (e.g. codex, gemini, claude)
   - What role does each LLM play?
   - Conversation mode: `live` (continuous streaming) or `event-driven` (message-based)?
   - Conflict resolution strategy per session: `user` (human decides) or `autonomous` (LLM decides)?
   - Purge settings: threshold token count and max kept messages, or accept defaults (`20` threshold, `5` kept)?

3. **Initialize the collaboration workspace** — run from the project root:

   ```bash
   collab init
   ```

4. **Join the current session** to the collaboration:

   ```bash
   collab join
   ```

5. **Spawn each partner session** in a new tmux pane. For every partner LLM, open an empty pane first, then send commands into it. Do NOT pass commands directly to `tmux split-window` — interactive CLIs like codex and gemini need to be started via `send-keys` so you can control the sequence.

   **CRITICAL: Every `tmux send-keys` command MUST end with `C-m` to press Enter.** Without `C-m`, the text is typed but never executed.

   ```bash
   # Step A: Create an empty pane
   tmux split-window -h -t collab

   # Step B: Identify the new pane ID
   tmux list-panes -t collab -F '#{pane_id} #{pane_current_command}'

   # Step C: Set env var, join collaboration, then launch the LLM
   tmux send-keys -t <pane-id> "cd /path/to/project" C-m
   tmux send-keys -t <pane-id> "export COLLAB_SESSION_NAME='codex'" C-m
   tmux send-keys -t <pane-id> "/root/llm-router/tools/collab.sh join --name codex --role implementer" C-m
   tmux send-keys -t <pane-id> "codex" C-m
   ```

   Repeat for each partner LLM (gemini, claude, etc.), adjusting the name, role, and CLI command.

   **Common mistake:** Using `Enter` instead of `C-m`. Always use `C-m`. They mean the same thing in tmux but `C-m` is the reliable form.

6. **Verify sessions joined** before proceeding:

   ```bash
   collab status
   ```

   All partner sessions should appear in the list.

7. **Open the TUI dashboard** in a dedicated pane:

   ```bash
   tmux split-window -v "collab ui"
   ```

### Hot connect flow (`--collaborate --connect`)

1. **Bootstrap if needed** — if `.collab/` does not exist in the project root, run:

   ```bash
   collab init
   ```

2. **Detect running sessions** — auto-detect active tmux panes that look like LLM sessions, or ask the user which sessions to connect.

3. **Join each session** to the collaboration:

   ```bash
   collab join
   ```

   Run this in each target pane.

4. **Set env vars and install hooks** — for each connected pane (ALWAYS use `C-m` not `Enter`):

   ```bash
   tmux send-keys -t <pane-id> "export COLLAB_SESSION_NAME='<session-name>'" C-m
   ```

5. **Open the TUI dashboard**:

   ```bash
   tmux split-window -v "collab ui"
   ```

### End flow (`--collaborate --end`)

1. **End the collaboration**:

   ```bash
   collab end
   ```

2. **Clean up hooks and env vars** — unset `COLLAB_SESSION_NAME` in every connected pane:

   ```bash
   tmux send-keys -t <pane-id> "unset COLLAB_SESSION_NAME" Enter
   ```

3. **Close the TUI pane** — identify the pane running `collab ui` and kill it:

   ```bash
   tmux kill-pane -t <tui-pane-id>
   ```

### Subteam isolation rule

Any standard `/smart-team` invocation (without `--collaborate`) that runs inside a session currently participating in a collaboration MUST spawn its agents in a **separate tmux session** named `collab-<session-name>-team`. Do not mix subteam panes with collaboration panes.

```bash
tmux new-session -d -s "collab-${COLLAB_SESSION_NAME}-team"
```

When a subteam is spawned, the collaboration dashboard must show a notification:

> Subteam started for session `<session-name>`. Attach with: `tmux attach -t collab-<session-name>-team`

When the subteam finishes, the dashboard must show a follow-up notification:

> Subteam for session `<session-name>` has completed.

## Non-Negotiables

1. You are the orchestrator, not the implementer.
2. Do not use `Edit` or `Write` for implementation code.
3. Every created task must have an explicit `owner` via `TaskUpdate`.
4. Every implementer task must be followed by review + QA tasks.
5. For routed tasks, if the selected backend cannot run (permission, auth, sandbox, or tool failure), stop and request fix. Do not silently fall back to another provider.
6. Use a clarification budget: at most 5 blocking questions per implementation task before coding starts.

## Step 0: tmux/backend preflight (required)

Non-interactive `claude -p` sessions are not valid for tmux teammate verification. In local debug traces, `Agent` spawning in `-p` mode uses in-process teammates, so `/smart-team` must be run from an interactive Claude session when pane-backed evidence matters.

Run:

```bash
echo "TMUX=${TMUX:-}"; tmux list-sessions
```

If not inside tmux (or tmux is unavailable), inform user that pane visibility cannot be guaranteed and stop until they launch from tmux.

## Step 1: team lifecycle hygiene

- Before creating a new team, check if you are already leading one.
- If a stale team exists, ask user, then shut it down cleanly (`SendMessage` shutdown requests + `TeamDelete`).
- Create the new team via `TeamCreate`.

## Step 2: task breakdown and routing

- Break work into implementation, QA, review, and product-verification tasks.
- Ask routing for each implementation task unless user said "auto" or "just do it".

Routing defaults:
- Recommend **CX-Executor**: scaffolding, boilerplate, test generation, constrained single-file bug fixes, dependency upgrades.
- Recommend **CC-Diagnostician**: architecture, multi-file debugging/refactor, security-sensitive logic, ambiguous problems, and user-facing clarification wording.
- Recommend **GI-Mapper**: large-context synthesis, documentation-heavy analysis, multimodal mapping, and decomposition of broad tasks into parallel lanes.

Default failure-mode flow:
- `GI-Mapper` maps large or ambiguous surfaces first.
- `CC-Diagnostician` resolves risky logic, ambiguities, and user-facing wording.
- `CX-Executor` lands scoped implementation and test/fix loops.
- `CC-Diagnostician` reviews risky diffs or final user-facing blockers.

## Step 2.5: clarification protocol

Before implementation begins, require every implementer task to do a gap check:

- First read the repo, docs, tests, and prior task context.
- If material uncertainty remains, ask at most 5 blocking questions.
- Prefer asking another teammate first when the answer is likely discoverable from existing context.
- Ask the user only if the answer changes correctness, scope, UX, or an irreversible decision.
- If `CX-Executor` or `GI-Mapper` finds a user-facing blocker, route the raw blocker list through `CC-Diagnostician` first so Claude rewrites it into clear, concise user-facing questions with proposed defaults.
- If a question is non-blocking, proceed with an explicit assumption instead of asking.
- Allow only one clarification round before either proceeding with assumptions or escalating a blocker.

Good questions:
- narrow the acceptance criteria
- resolve ambiguous business rules
- pick between materially different implementations

Bad questions:
- ask for preferences when a safe default exists
- reopen settled scope
- request broad brainstorming during active implementation

## Step 3: spawn real teammates in background

Use `Agent` with both:
- `team_name: <team-name>`
- `run_in_background: true`

Core roles:
- `qa-tester`
- `code-reviewer`
- `product-verifier` (optional but recommended)
- `CX-Executor` for Codex-routed execution
- `CC-Diagnostician` for Claude-routed diagnosis and wording
- `GI-Mapper` for Gemini-routed mapping and decomposition

Legacy aliases may still exist:
- `codex-specialist`
- `claude-implementer`
- `gemini-specialist`

## Step 4: verify backend is actually tmux

After spawning, validate using team config:

```bash
cat ~/.claude/teams/<team-name>/config.json
```

For each spawned member, confirm:
- `backendType == "tmux"`
- `tmuxPaneId` is non-empty

If any agent is `in-process` unexpectedly, report it, respawn once with same config, and re-check.

## Step 5: mandatory ownership assignment

For every `TaskCreate`, immediately assign an owner with `TaskUpdate owner=<agent-name>`.

No task may remain unassigned when execution starts.

## Step 6: execution contract per role

### `CX-Executor` contract

Prompt must require:
- Always call `"$HOME/.claude/plugins/llm-router/tools/codex_worker.sh" "<task prompt>"`
- Never write implementation code directly
- Start with either `No blocking questions` or `Blocking questions (max 5)` plus proposed defaults
- If blocked, ask targeted questions; if not blocked, proceed with explicit assumptions
- If user clarification is needed, SendMessage raw blockers plus defaults to CC-Diagnostician for wording before asking the user
- Send completion summary + run result back to lead; summary must include enough detail for lead to satisfy Step 7 check #5 without re-querying
- May SendMessage detailed handoff context to the assigned QA and Reviewer agents as an additive note — lead retains exclusive phase initiation via TaskCreate + TaskUpdate

### `GI-Mapper` contract

Prompt must require:
- Always call `"$HOME/.claude/plugins/llm-router/tools/gemini_worker.sh" "<task prompt>"`
- Never write implementation code directly
- Start with either `No blocking questions` or `Blocking questions (max 5)` plus proposed defaults
- If blocked, ask targeted questions; if not blocked, proceed with explicit assumptions
- If user clarification is needed, SendMessage raw blockers plus defaults to CC-Diagnostician for wording before asking the user
- Send completion summary + run result back to lead; summary must include enough detail for lead to satisfy Step 7 check #5 without re-querying
- May SendMessage detailed handoff context to the assigned QA and Reviewer agents as an additive note — lead retains exclusive phase initiation via TaskCreate + TaskUpdate

### `CC-Diagnostician` contract

Prompt must require:
- Always call `"$HOME/.claude/plugins/llm-router/tools/claude_worker.sh" "<task prompt>"`
- Never write implementation code directly
- Start with either `No blocking questions` or `Blocking questions (max 5)` plus proposed defaults
- If blocked, ask targeted questions; if not blocked, proceed with explicit assumptions
- If another agent found the blocker, rewrite it into the clearest possible user-facing questions with proposed defaults
- Send completion summary + run result back to lead; summary must include enough detail for lead to satisfy Step 7 check #5 without re-querying

### QA contract

- Run full tests and report pass/fail and key failures to lead.
- Never approve while tests fail.
- On failure: SendMessage failure details and reproduction steps to lead for re-tasking decision. Lead creates any fix task via TaskCreate + TaskUpdate.
- May SendMessage supplemental failure context to the implementation agent as an additive note ONLY IF lead has already created and assigned the fix task.
- May receive additive handoff notes from implementation agents — these are supplemental context, not phase triggers. QA begins work only when lead assigns the task.

### Reviewer contract

- Review diff with high-confidence findings and report to lead.
- On findings requiring remediation: SendMessage findings to lead for re-tasking decision. Lead creates any remediation task via TaskCreate + TaskUpdate.
- May SendMessage supplemental finding context to the implementation agent as an additive note ONLY IF lead has already created and assigned the remediation task.
- May receive additive handoff notes from implementation agents — these are supplemental context, not phase triggers. Reviewer begins work only when lead assigns the task.

## Step 7: hard evidence checks before marking done

Before wrap-up, verify all are true:
1. No implementation task left `pending` or unowned.
2. Each `CX-Executor` task has at least one concrete Codex run artifact in:
   - `~/.claude/plugins/llm-router/tools/codex_runs/manifest.jsonl`
3. Each `GI-Mapper` task has at least one concrete Gemini run artifact in:
   - `~/.claude/plugins/llm-router/tools/gemini_runs/manifest.jsonl`
4. Each `CC-Diagnostician` task has at least one concrete Claude run artifact in:
   - `~/.claude/plugins/llm-router/tools/claude_runs/manifest.jsonl`
5. QA and review results received by lead for each completed implementation task. Lead must hold summaries sufficient to verify this check from agent completion messages alone — no additional round-trips required.
6. Product verification done (if role was spawned).

## Step 8: wrap up and cleanup

1. Update AI_LOG.md summary.
2. Send `shutdown_request` to teammates.
3. Call `TeamDelete`.
4. Report final result and any residual risks.

## tmux quick checks (reference)

```bash
tmux list-sessions
tmux list-panes -a -F '#S:#I.#P #{pane_pid} #{pane_current_command}'
```

## Kickoff template (reference)

For a reusable `/smart-team` starter prompt using `GI-Mapper -> CC-Diagnostician -> CX-Executor`, see:
- `~/.claude/plugins/llm-router/skills/llm-routing/references/kickoff-template.md`
