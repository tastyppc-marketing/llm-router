# Smart Team v2 — Improvement Spec

**Branch:** `smart-team-v2`
**Backup tag:** `backup-before-smart-team-v2` at `75a556b`
**Created:** 2026-04-06
**Status:** Planning complete, ready for implementation

---

## Overview

Ten improvements to the `/smart-team` orchestration system, organized by implementation order. Each improvement builds on the previous where noted.

---

## 1. Consolidate Worker Boilerplate

**Priority:** Foundational — implement first
**Files affected:** `tools/codex_worker.py`, `tools/claude_worker.py`, `tools/gemini_worker.py` (new: `tools/base_worker.py`)

### Problem
The three worker scripts share ~80% identical code: argument parsing, retry logic with exponential backoff, manifest JSONL logging, artifact file writing, timeout handling, and error reporting. Bug fixes or feature additions must be applied three times.

### Solution
Extract a `BaseWorker` class in `tools/base_worker.py` that handles:
- Argument parsing (model, workdir, timeout, output flags)
- Retry loop with configurable count and backoff (reads from `LLM_ROUTER_*` env vars)
- Transient failure detection via `looks_transient_failure()`
- Artifact writing (stdout, stderr, last_message)
- Manifest JSONL appending with run metadata
- Report generation

Each worker subclass overrides only:
- `cli_command()` — returns the CLI invocation list
- `parse_output()` — extracts model info and result from CLI output
- `runs_dir` / `manifest_name` — paths for artifacts

### Acceptance Criteria
- [ ] All three workers delegate to `BaseWorker`
- [ ] Each worker file is < 60 lines (just the subclass + overrides)
- [ ] Existing manifest.jsonl format unchanged (backwards compatible)
- [ ] All env var overrides still work (`LLM_ROUTER_*_TIMEOUT_SEC`, `*_RETRY_COUNT`, `*_BACKOFF_SEC`)
- [ ] `/router-validate wrapper` still passes

---

## 2. Agent Failure Retry with Fallback Routing

**Priority:** High — reliability
**Files affected:** `commands/smart-team.md`, `tools/base_worker.py` (from #1)
**Depends on:** #1 (BaseWorker)

### Problem
When an agent's underlying LLM fails (timeout, rate limit, sandbox error), the task fails and the entire run can stall. The orchestrator has no retry or rerouting logic.

### Solution
**Retry policy (same model):**
- On task failure, retry on the same LLM up to 3 times with exponential backoff
- Retries are transparent to the orchestrator — handled inside `BaseWorker`

**Fallback routing (different model):**
- If all 3 retries on the original model fail, the orchestrator reroutes to a fallback:
  - `CX-Executor` fails → reroute to `CC-Diagnostician`
  - `GI-Mapper` fails → reroute to `CC-Diagnostician`
  - `CC-Diagnostician` fails → reroute to `CX-Executor`
- Fallback uses the same retry policy (up to 3 attempts)
- If fallback also exhausts retries, escalate to user with full error context — never silently give up

**Orchestrator contract update:**
- Add to `smart-team.md` Step 6: "On task failure after retry exhaustion, reroute to fallback model per the routing table. Log original failure reason in the task metadata."
- The fallback agent receives the original prompt + context about what failed and why

### Acceptance Criteria
- [ ] Worker retries 3x on same model before returning failure
- [ ] Orchestrator detects worker failure and reroutes per fallback table
- [ ] Fallback agent receives original prompt + failure context
- [ ] User is only asked to intervene after both primary and fallback are exhausted
- [ ] All retry/fallback events logged in bridge events and manifest

---

## 3. Stale Run Cleanup

**Priority:** High — hygiene
**Files affected:** `tools/base_worker.py`, `tools/codex_smart_team.py` (new: `tools/cleanup_runs.py`)

### Problem
Run artifact directories (`codex_smart_team_runs/`, `codex_runs/`, `claude_runs/`, `gemini_runs/`) accumulate indefinitely, consuming disk space.

### Solution
Add `tools/cleanup_runs.py` with a `cleanup_stale_runs()` function:
- Default retention: keep last 20 runs OR runs less than 7 days old (whichever keeps more)
- Configurable via `LLM_ROUTER_RUN_RETENTION_COUNT` and `LLM_ROUTER_RUN_RETENTION_DAYS`
- Called automatically at the start of each new run (before creating the new run directory)
- Also callable standalone: `python3 tools/cleanup_runs.py [--dry-run] [--keep N] [--days D]`
- Cleanup targets all four run directories

### Acceptance Criteria
- [ ] Runs older than retention policy are deleted on new run start
- [ ] `--dry-run` mode lists what would be deleted without acting
- [ ] Configurable via env vars
- [ ] Standalone CLI works for manual cleanup

---

## 4. Live Progress Reporting

**Priority:** High — UX
**Files affected:** `tools/codex_smart_team.py`, `commands/smart-team.md`

### Problem
The bridge polls for completion but prints nothing to the caller's terminal until the entire run finishes or times out. The only visibility is navigating tmux panes, which is clunky.

### Solution
Define milestone events that the orchestrator emits during execution. The bridge parses these from the transcript/pane in real-time and prints them to stdout.

**Milestone format (emitted by orchestrator via Bash `echo`):**
```
SMART_TEAM_PROGRESS=<phase>|<detail>
```

**Phases:**
- `preflight` — tmux/backend checks
- `team_created` — team bootstrapped with member list
- `task_routing` — routing decision made (which model, why)
- `agent_started:<agent-name>` — agent spawned and working
- `agent_completed:<agent-name>` — agent finished (success/fail)
- `qa_started` / `qa_passed` / `qa_failed`
- `review_started` / `review_passed` / `review_findings`
- `needs_input` — waiting for user
- `wrapping_up` — cleanup phase

**Bridge behavior:**
- New regex `PROGRESS_RE` matches `SMART_TEAM_PROGRESS=...` lines
- On match, print a formatted line to stdout: `[12:34:05] [smart-team] Agent CX-Executor started task 2/4`
- Deduplicate — don't reprint the same milestone
- Progress lines are also logged to `bridge.events.txt`

### Acceptance Criteria
- [ ] At least 8 distinct milestone types emitted during a typical run
- [ ] Bridge prints milestones to stdout in real-time (within one poll cycle, ~5s)
- [ ] Milestones are deduped — no repeated lines
- [ ] All milestones also appear in bridge.events.txt
- [ ] Zero impact on footer detection or completion logic

---

## 5. `needs_user_input` Relay (Don't Kill Session)

**Priority:** High — UX
**Files affected:** `tools/codex_smart_team.py`

### Problem
When the bridge detects `needs_user_input` (via AskUserQuestion in debug log or status file), it kills the tmux session and returns exit code 2. The user then has to restart the entire run.

### Solution
**Relay loop:**
1. Bridge detects `needs_user_input` status
2. Extract the question from the transcript (last AskUserQuestion content or the text after the status marker)
3. Print the question to stdout with a clear prompt: `[smart-team] Question from agent: <question>`
4. Read user input from stdin
5. Send the user's answer back to the tmux pane via `send_prompt()`
6. Reset the polling loop — continue waiting for next status/completion
7. Allow up to 5 relay rounds per run (configurable via `LLM_ROUTER_MAX_USER_RELAYS`)
8. After max relays, warn the user and continue waiting (don't kill)

**Question extraction:**
- Parse the transcript for the most recent block after `AskUserQuestion` tool call
- Fall back to the last 20 lines of the pane if structured extraction fails

### Acceptance Criteria
- [ ] User questions are relayed to stdout without killing the session
- [ ] User answers are sent back and the run continues
- [ ] Multiple Q&A rounds supported in a single run
- [ ] Configurable max relay count
- [ ] Session is only killed at final wrap-up, not on user input

---

## 6. Run Analytics Command (`/router-analytics`)

**Priority:** Medium — observability
**Files affected:** new `commands/router-analytics.md`, new `tools/router_analytics.py`, new `tools/router_analytics.sh`

### Problem
Manifest JSONL files contain rich run data (model, duration, success/fail, task type) but there's no way to query or visualize it.

### Solution
New `/router-analytics` slash command that reads all manifest files and reports:

**Default output (no args):**
- Total runs per model (Claude / Codex / Gemini)
- Success rate per model (last 30 days)
- Average duration per model
- Most common failure patterns (top 3)
- Retry rate (how often retries were needed)
- Fallback rate (how often rerouting happened, once #2 is implemented)

**Optional args:**
- `--since <date>` — filter to runs after a date
- `--model <claude|codex|gemini>` — filter to one model
- `--failures` — show only failed runs with error details
- `--json` — output as JSON for programmatic use

**Implementation:**
- Read all `manifest.jsonl` files from `claude_runs/`, `codex_runs/`, `gemini_runs/`
- Parse each line, aggregate stats
- Print formatted table to stdout

### Acceptance Criteria
- [ ] Reads all three manifest files
- [ ] Reports success rate, duration, failure patterns per model
- [ ] `--since`, `--model`, `--failures`, `--json` flags work
- [ ] Handles empty/missing manifests gracefully
- [ ] Runs in < 2 seconds for typical history sizes

---

## 7. Auto-Routing Scorer

**Priority:** Medium — intelligence
**Files affected:** new `tools/routing_scorer.py`, `commands/smart-team.md`, `skills/llm-routing/references/routing-heuristics.md`
**Depends on:** #6 (analytics data for learner)

### Problem
Routing decisions are made by the orchestrator based on prose heuristics in `routing-heuristics.md`. This is inconsistent and opaque.

### Solution
**Weighted rule engine** in `tools/routing_scorer.py`:

**Architecture:**
```python
class RoutingScorer:
    def __init__(self, weights_file: Path):
        self.weights = load_weights(weights_file)  # YAML/JSON

    def score(self, task_description: str, context: dict) -> ScoringResult:
        # Returns scores per model + reasoning
        ...

    def recommend(self, task_description: str, context: dict) -> str:
        # Returns "CX-Executor" | "CC-Diagnostician" | "GI-Mapper"
        ...
```

**Signals analyzed:**
- File count and types mentioned (single-file → CX, multi-file → CC)
- Task category keywords (scaffolding, refactor, debug, architecture, synthesis, docs)
- Estimated scope (LoC touched, number of modules)
- Prior success rates for similar task patterns (from manifests)

**Output format (always transparent):**
```
Routing recommendation: CX-Executor (score: 8.2)
  Signals: single-file (+3), test-generation (+2.5), scaffolding (+2), prior-success-rate-92% (+0.7)
  Alternatives: CC-Diagnostician (4.1), GI-Mapper (2.3)
```

**Weights file:** `tools/routing_weights.yaml`
- Ships with sensible defaults from current `routing-heuristics.md`
- User can edit directly
- Learner updates are written here with a comment noting what changed and when

**Learner:**
- Runs as part of `/router-analytics` (optional `--update-weights` flag)
- Reads manifest outcomes: success rate and duration per model per task-type pattern
- Adjusts weights only when confidence threshold is met (default: 10+ runs of that pattern)
- Changes are logged: `[2026-04-07] Auto-adjusted: test-generation CX weight 2.5 → 3.0 (95% success over 14 runs)`
- Threshold configurable via `LLM_ROUTER_LEARNER_MIN_SAMPLES` (default: 10)

**Kill switch:**
- Env var `LLM_ROUTER_AUTO_ROUTING=0` disables the scorer entirely — falls back to manual selection
- Env var `LLM_ROUTER_LEARNER_ENABLED=0` freezes weights (scorer still works, just doesn't auto-update)
- Both default to enabled (`1`)

### Acceptance Criteria
- [ ] Scorer produces transparent, explainable recommendations
- [ ] Weights file is human-editable YAML
- [ ] Kill switch disables scorer completely, falling back to manual routing
- [ ] Learner freeze switch stops auto-updates while keeping scorer active
- [ ] Learner only adjusts after meeting sample threshold
- [ ] All weight changes logged with timestamp and reasoning

---

## 8. Task Dependency Graph

**Priority:** Medium — performance
**Files affected:** `commands/smart-team.md`, potentially `tools/codex_smart_team.py`

### Problem
Tasks execute roughly sequentially even when independent. Three independent implementation tasks that could finish in ~12 minutes take ~30+ because they wait for each other.

### Solution
**Dependency declaration in task breakdown:**
The orchestrator, when breaking tasks in Step 2, builds an explicit dependency graph:

```
TaskCreate: "Implement auth fix" → id: impl-1
TaskCreate: "Implement config parser" → id: impl-2
TaskCreate: "Write auth tests" → id: qa-1, blocked_by: [impl-1]
TaskCreate: "Write config tests" → id: qa-2, blocked_by: [impl-2]
TaskCreate: "Final integration review" → id: review-final, blocked_by: [qa-1, qa-2]
```

**How the orchestrator detects dependencies:**
- **Output dependency:** Task B references something task A creates (e.g., "test the auth module" depends on "create the auth module")
- **File dependency:** Task B modifies files that task A also modifies → sequential
- **No dependency:** Tasks touch different files/modules with no data flow between them → parallel

**Orchestrator behavior:**
- After task breakdown, the lead builds the graph and presents it to the user (or logs it if "auto" mode)
- Independent tasks are spawned concurrently via `Agent` with `run_in_background: true`
- Dependent tasks wait until their blockers are marked complete
- The graph is logged in the run report for debugging

**Smart-team.md updates:**
- New Step 2.5b: "Build dependency graph. For each task pair, determine if B depends on A's output. Independent tasks MUST be spawned in parallel. Dependent tasks use `addBlockedBy` via `TaskUpdate`."

### Acceptance Criteria
- [ ] Orchestrator produces an explicit dependency graph before execution
- [ ] Independent tasks run in parallel (verified via overlapping timestamps in bridge events)
- [ ] Dependent tasks wait for their blockers
- [ ] Graph is logged in the run report
- [ ] User can see the graph before execution starts (unless "auto" mode)

---

## 9. Resumable Runs

**Priority:** Medium — resilience
**Files affected:** `tools/codex_smart_team.py`, `commands/smart-team.md`

### Problem
If a smart-team run is interrupted (timeout, crash, user cancels), all progress is lost. The user must restart from scratch.

### Solution
**State checkpointing:**
- After each task completes, save a checkpoint to `<run_dir>/checkpoint.json`:
  ```json
  {
    "run_id": "20260407-143022-12345",
    "task_prompt": "original prompt",
    "team_name": "smart-team-abc",
    "completed_tasks": ["impl-1", "qa-1"],
    "pending_tasks": ["impl-2", "qa-2", "review-final"],
    "dependency_graph": {...},
    "routing_decisions": {"impl-1": "CX-Executor", "impl-2": "GI-Mapper"},
    "artifacts": {"impl-1": "codex_runs/manifest.jsonl:line42"}
  }
  ```

**Resume command:**
- `/smart-team --resume <run-id>` or `/smart-team --resume latest`
- Reads the checkpoint, skips completed tasks, resumes pending ones
- Creates a new run directory linked to the original
- Re-establishes the team if it was torn down

**Bridge support:**
- `codex_smart_team.py` accepts `--resume <run-id>` flag
- Looks up checkpoint in `codex_smart_team_runs/<run-id>/checkpoint.json`
- Injects checkpoint context into the prompt

### Acceptance Criteria
- [ ] Checkpoints saved after each task completion
- [ ] `--resume` flag resumes from last checkpoint
- [ ] Completed tasks are not re-executed
- [ ] New run directory links back to original
- [ ] Works even if original tmux session was killed

---

## 10. Remove Legacy Agent Aliases

**Priority:** Low — cleanup
**Files affected:** `agents/codex-specialist.md`, `agents/claude-implementer.md`, `agents/gemini-specialist.md`, `commands/smart-team.md`

### Problem
Legacy agent files (`codex-specialist`, `claude-implementer`, `gemini-specialist`) coexist with current roles (`CX-Executor`, `CC-Diagnostician`, `GI-Mapper`). Different prompts, same intent — causes confusion.

### Solution
- Delete `agents/codex-specialist.md`, `agents/claude-implementer.md`, `agents/gemini-specialist.md`
- Remove the "Legacy aliases may still exist" note from `commands/smart-team.md`
- Verify no other files reference the legacy names

### Acceptance Criteria
- [ ] Legacy agent files deleted
- [ ] No references to legacy names in any remaining file
- [ ] `/smart-team` runs without referencing legacy agents

---

## 11. Configurable Gemini Policy Path

**Priority:** Low — portability
**Files affected:** `tools/gemini_worker.py` (or `base_worker.py` after #1)

### Problem
`gemini_worker.py` hardcodes the policy path to `/home/mjfos/.gemini/policies/llm-router.toml`. Breaks on other machines or different directory layouts.

### Solution
- Read from `LLM_ROUTER_GEMINI_POLICY_PATH` env var
- Default fallback: `~/.gemini/policies/llm-router.toml` (using `Path.home()`)
- If the path doesn't exist, warn but don't crash — Gemini runs without policy

### Acceptance Criteria
- [ ] Env var override works
- [ ] Default uses `~` expansion, not hardcoded `/home/mjfos`
- [ ] Missing policy file produces a warning, not a crash

---

## 12. Unified Hook Test Detection

**Priority:** Low — cleanup
**Files affected:** `hooks/run-tests-async.sh`, `hooks/task-complete-gate.sh` (new: `hooks/detect-test-framework.sh`)

### Problem
Both hooks independently detect the test framework (npm, pytest, cargo, make, go). Duplicated logic that could diverge.

### Solution
Extract shared detection into `hooks/detect-test-framework.sh`:
```bash
# Returns the test command for the detected framework
detect_test_command() {
    if [ -f package.json ]; then echo "npm test"
    elif [ -f pytest.ini ] || [ -f pyproject.toml ]; then echo "pytest"
    elif [ -f Cargo.toml ]; then echo "cargo test"
    elif [ -f Makefile ]; then echo "make test"
    elif [ -f go.mod ]; then echo "go test ./..."
    fi
}
```

Both hooks source this file and call `detect_test_command()`.

### Acceptance Criteria
- [ ] Single source of truth for test framework detection
- [ ] Both hooks produce identical behavior to current implementation
- [ ] Adding a new framework requires editing only one file

---

## Implementation Order

```
#1 Consolidate Workers (foundational)
 ├── #2 Retry + Fallback Routing (depends on BaseWorker)
 ├── #3 Stale Run Cleanup (uses BaseWorker lifecycle)
 │
#4 Live Progress Reporting (independent)
#5 needs_user_input Relay (independent)
 │
#6 Run Analytics (independent, but feeds #7)
 └── #7 Auto-Routing Scorer (uses analytics data)
 │
#8 Task Dependency Graph (independent)
#9 Resumable Runs (independent, benefits from #8 checkpoint data)
 │
#10 Remove Legacy Aliases (independent, quick)
#11 Configurable Gemini Policy (independent, quick)
#12 Unified Hook Detection (independent, quick)
```

**Parallelizable groups:**
- Group A: #1 → #2 → #3 (sequential, foundational)
- Group B: #4, #5 (independent, can run alongside Group A)
- Group C: #6 → #7 (sequential)
- Group D: #8, #9 (can start after Group A)
- Group E: #10, #11, #12 (quick cleanups, anytime)
