# llm-router

`llm-router` is a Claude Code plugin/workflow layer for routing work across multiple coding models with explicit role boundaries.

Current routing model:
- `GI-Mapper` for large-context mapping and decomposition
- `CC-Diagnostician` for risky logic, architecture, and user-facing clarification wording
- `CX-Executor` for scoped implementation and test/fix loops

Core pieces:
- `agents/` contains the routed role prompts
- `commands/` contains slash commands such as `/smart-team`, `/router-validate`, and `/router-status`
- `hooks/` contains repo-safety and async test hooks
- `skills/` contains the routing skill and kickoff template
- `tools/` contains the provider wrappers, smoke tests, and validation scripts

Useful local commands:

```bash
bash "$HOME/.claude/plugins/llm-router/tools/router_validate.sh" -m all -C "$PWD"
bash "$HOME/.claude/plugins/llm-router/tools/router_status.sh"
```

This repository intentionally excludes local model outputs, validation artifacts, and machine-specific auth/config state. Those remain local to the workstation.
