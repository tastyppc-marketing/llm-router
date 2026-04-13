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
   python3 /root/llm-router/tools/collab.py init
   ```

4. **Join the current session** to the collaboration:

   ```bash
   python3 /root/llm-router/tools/collab.py join --name "<your-session-name>" --role "<your-role>"
   export COLLAB_SESSION_NAME="<your-session-name>"
   ```

5. **Write collaboration instructions** for partner sessions. Create a file at `.collab/partner-prompt.md` in the project root with the following content (adapt the project description, partner name, and role for each partner):

   ```markdown
   # Collaboration Mode — Active

   You are in a multi-LLM collaboration session. Other LLM sessions are working
   on this project with you. You communicate using the `collab` CLI.

   ## Your identity
   - Name: <partner-name>
   - Role: <partner-role>

   ## Communication protocol

   **Check for messages regularly.** After every significant action you take,
   run this command to see if your collaborators have sent you anything:

   ```
   python3 /root/llm-router/tools/collab.py check --name "<partner-name>" --format inject
   ```

   **Send messages to share your work.** When you complete something, make a
   proposal, have a question, or disagree with something, tell your collaborators:

   ```
   python3 /root/llm-router/tools/collab.py send --name "<partner-name>" --type status "What you did or plan to do"
   python3 /root/llm-router/tools/collab.py send --name "<partner-name>" --type proposal "Your suggestion"
   python3 /root/llm-router/tools/collab.py send --name "<partner-name>" --type question "Your question"
   python3 /root/llm-router/tools/collab.py send --name "<partner-name>" --type conflict --reply-to <id> "Why you disagree"
   ```

   **Lock files before editing.** Before you edit any file, claim it:

   ```
   python3 /root/llm-router/tools/collab.py lock <file-path> --name "<partner-name>"
   ```

   Release it when done:

   ```
   python3 /root/llm-router/tools/collab.py unlock <file-path> --name "<partner-name>"
   ```

   **Check messages after every tool call.** This is critical — your collaborators
   may have sent you proposals, questions, or conflicts that need your attention.
   Always check before starting new work.

   ## Rules
   - Be a peer, not a follower. Push back if you disagree, with reasoning.
   - If someone locks a file, don't edit it. Message them to coordinate.
   - If a conflict can't be resolved in 3 rounds, it escalates.
   - User directives (type: "directive") always take priority.
   ```

6. **Spawn each partner session** in a new tmux pane. For every partner LLM, open an empty pane, set up the environment, then launch the LLM with the collaboration prompt.

   **CRITICAL: Every `tmux send-keys` command MUST end with `C-m` to press Enter.** Without `C-m`, the text is typed but never executed.

   ```bash
   # Step A: Create an empty pane
   tmux split-window -h -t collab

   # Step B: Identify the new pane ID
   tmux list-panes -t collab -F '#{pane_id} #{pane_current_command}'

   # Step C: Set env var and join collaboration
   tmux send-keys -t <pane-id> "cd /path/to/project" C-m
   tmux send-keys -t <pane-id> "export COLLAB_SESSION_NAME='codex'" C-m
   tmux send-keys -t <pane-id> "python3 /root/llm-router/tools/collab.py join --name codex --role implementer" C-m

   # Step D: Launch the LLM with the collaboration prompt
   # For Claude Code:
   tmux send-keys -t <pane-id> "claude --system-prompt .collab/partner-prompt.md" C-m
   # For Codex (pass prompt via -p for initial instruction, then interactive):
   tmux send-keys -t <pane-id> "codex --full-auto" C-m
   tmux send-keys -t <pane-id> "$(cat .collab/partner-prompt.md | head -5) — Read .collab/partner-prompt.md for your full collaboration instructions, then check for messages with: python3 /root/llm-router/tools/collab.py check --name codex --format inject" C-m
   # For Gemini:
   tmux send-keys -t <pane-id> "gemini" C-m
   tmux send-keys -t <pane-id> "Read .collab/partner-prompt.md for your collaboration instructions, then check for messages with: python3 /root/llm-router/tools/collab.py check --name gemini --format inject" C-m
   ```

   Adapt the launch sequence for each LLM's CLI interface. The key requirement: **the LLM must know to read `.collab/partner-prompt.md` and start checking messages.**

   **Common mistake:** Using `Enter` instead of `C-m`. Always use `C-m`.

7. **Verify sessions joined** before proceeding:

   ```bash
   python3 /root/llm-router/tools/collab.py status
   ```

   All partner sessions should appear in the list.

8. **Open the TUI dashboard** in a dedicated pane:

   ```bash
   tmux split-window -v -t collab
   tmux send-keys -t collab "cd /path/to/project && python3 /root/llm-router/tools/collab_ui.py" C-m
   ```

9. **Send the first message** to kick off the collaboration. From your session, send a message that tells all partners what to work on:

   ```bash
   python3 /root/llm-router/tools/collab.py send --name "<your-name>" --type proposal "Here's what we're building: <description>. <partner-1>, start with X. <partner-2>, start with Y."
   ```

   Partners will see this on their next `collab check`.

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
