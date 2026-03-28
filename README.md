# llm-router

`llm-router` is a Claude Code plugin/workflow for routing software tasks across Claude Code, Codex, and Gemini by failure mode instead of by model loyalty.

## What it contains

- agent prompts in `agents/`
- slash commands in `commands/`
- hook scripts in `hooks/`
- routing skill docs in `skills/`
- worker and validation scripts in `tools/`
- Claude plugin metadata in `.claude-plugin/plugin.json`

## Current routing model

- `GI-Mapper`: repo-wide mapping, ambiguity reduction, and large-context discovery
- `CC-Diagnostician`: risky logic, architecture, and user-facing clarifications
- `CX-Executor`: implementation, tests, and tight fix loops

Default handoff:

`GI-Mapper -> CC-Diagnostician -> CX-Executor -> CC-Diagnostician`

## Validation

Run the repo-agnostic checks with:

```bash
bash "$HOME/.claude/plugins/llm-router/tools/router_validate.sh" -m all -C "$PWD"
bash "$HOME/.claude/plugins/llm-router/tools/router_status.sh"
```

These validate:

- Bash access for `CC`, `CX`, and `GI`
- Gemini destructive-command blocking
- tmux-backed `TeamCreate`, `TaskCreate`, and `TaskUpdate`

## Using From Codex

Codex cannot use Claude slash commands directly, but it can call a bridge script
that launches an interactive Claude session, runs `/smart-team`, waits for the
sentinel output, and returns a report.

Example:

```bash
cd /mnt/c/Dev/HBreplyBot
bash "$HOME/.claude/plugins/llm-router/tools/codex_smart_team.sh" -C "$PWD" -t 900 \
  "Resume HBReplyBot from the latest notes, implement the highest-value next slice, run relevant tests, and return real results."
```

If Claude is not loading the router commands/agents from the plugin directory,
install the live command surface with:

```bash
bash "$HOME/.claude/plugins/llm-router/tools/install_claude_surface.sh"
```

## Notes

- Tool entrypoints under `tools/` keep their existing `.sh` paths, but the orchestration and worker logic now lives in Python-backed controllers alongside thin shell shims.
- The remaining shell scripts are the hook entrypoints in `hooks/`, which stay shell-native because they are small and rely on shell-style hook exit semantics.
- Worker subprocess timeouts are safety bounds, not short deadlines. Defaults are intentionally generous, and can be overridden with env vars such as `LLM_ROUTER_TIMEOUT_SEC`, `LLM_ROUTER_CLAUDE_TIMEOUT_SEC`, `LLM_ROUTER_CODEX_TIMEOUT_SEC`, `LLM_ROUTER_GEMINI_TIMEOUT_SEC`, `LLM_ROUTER_FLOW_STEP_TIMEOUT_SEC`, `LLM_ROUTER_VALIDATE_STEP_TIMEOUT_SEC`, and `LLM_ROUTER_TMUX_TIMEOUT_SEC`.
- Worker wrappers now stamp runs with a shared `LLM_ROUTER_TRACE_ID` and perform bounded retries with exponential backoff for likely transient failures such as timeouts or upstream rate-limit/network issues.
- Runtime outputs and validation artifacts are intentionally ignored by git.
- Local environment setup such as Gemini policy files under `~/.gemini/` is not committed here and should be documented separately when needed.
