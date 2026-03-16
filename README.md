# llm-router

Routing and validation layer for a mixed-agent Claude/Codex/Gemini workflow.

Contents:
- `agents/`: role and alias prompts
- `commands/`: slash-command entrypoints
- `hooks/`: guardrails and async test hooks
- `skills/`: routing guidance and templates
- `tools/`: worker wrappers and validation scripts
- `.claude-plugin/`: Claude plugin metadata

Key validation commands:

```bash
bash "$HOME/.claude/plugins/llm-router/tools/router_validate.sh" -m all -C "$PWD"
bash "$HOME/.claude/plugins/llm-router/tools/router_status.sh"
```

This repository intentionally excludes generated run artifacts, captured prompts,
debug output, and local machine state. See `.gitignore` for details.
