---
description: Run the llm-router wrapper and tmux-team validation checks and summarize the latest result.
allowed-tools: [Bash, Read]
argument-hint: [all|wrapper|team]
---

# /router-validate

If no argument is provided, use `all`.

Run:

```bash
bash "$HOME/.claude/plugins/llm-router/tools/router_validate.sh" -m "${ARGUMENTS:-all}" -C "$PWD"
```

Then:
- report whether the validation passed
- point to the combined validation report
- point to the latest wrapper/team subreports
- if anything failed, say which phase failed
