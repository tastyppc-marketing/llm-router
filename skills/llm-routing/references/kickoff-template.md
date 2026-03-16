# `/smart-team` Kickoff Template

Use this template when you want `/smart-team` to run the CC/CX/GI flow explicitly instead of deciding everything from scratch.

## Generic Template

```text
/smart-team "
Task: <clear outcome>

Routing mode: auto using CC/CX/GI failure-mode flow

Team roles:
- GI-Mapper for repo-wide mapping, dependency tracing, and ambiguity reduction
- CC-Diagnostician for risky logic, architecture decisions, and all user-facing clarification wording
- CX-Executor for scoped implementation, test/fix loops, and regression hunting
- qa-tester for validation
- code-reviewer for review

Clarification policy:
- Allow up to 5 blocking questions per implementation task
- Prefer teammate-to-teammate clarification first
- Only ask me questions that change correctness, scope, UX, or irreversible behavior
- If CX or GI finds blockers for me, CC must rewrite them into concise user-facing questions with proposed defaults
- If uncertainty is non-blocking, proceed with explicit assumptions

Execution flow:
- Default handoff is GI-Mapper -> CC-Diagnostician -> CX-Executor -> CC-Diagnostician
- Require explicit task ownership
- Require audit artifacts for every routed task
- Do not silently switch providers if one route fails

Deliverables:
- short plan
- blocker questions only if truly necessary
- implementation summary
- QA result
- review result
"
```

## Example Template

```text
/smart-team "
Task: Add a bulk CSV lead import feature to the CRM.

Goals:
- accept CSVs from multiple vendors
- dedupe by phone and email
- allow partial success
- record per-row audit logs
- show import results in the UI

Routing mode: auto using CC/CX/GI failure-mode flow

Team roles:
- GI-Mapper maps the import pipeline, data dependencies, and ambiguity
- CC-Diagnostician resolves dedupe rules, conflict handling, and rewrites any user-facing blocker questions
- CX-Executor implements the upload flow, import job, row-state handling, and tests
- qa-tester validates happy path, duplicate path, and split-identity conflict path
- code-reviewer checks correctness and regression risk

Clarification policy:
- Allow up to 5 blocking questions total per implementation task
- Ask me only if the answer changes correctness, scope, UX, or irreversible behavior
- Route any user-facing blockers through CC-Diagnostician for wording
- Otherwise proceed with explicit defaults

Execution flow:
- GI-Mapper -> CC-Diagnostician -> CX-Executor -> CC-Diagnostician
- Require audit artifacts from Gemini, Claude, and Codex runs
- Require QA and review before wrap-up
"
```

## Quick Guidance

- Use `GI-Mapper` first when the surface is broad or the context is messy.
- Use `CC-Diagnostician` first when the main risk is wrong reasoning, UX, or business rules.
- Use `CX-Executor` first when the task is already scoped and mostly needs fast execution.

## Local Sequential Helper

For a local sequential planning run outside `/smart-team`, use:

```bash
$HOME/.claude/plugins/llm-router/tools/failure_mode_flow.sh -C /path/to/repo --accept-defaults "<task prompt>"
```
