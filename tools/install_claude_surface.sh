#!/usr/bin/env bash
# install_claude_surface.sh — expose llm-router commands/agents to Claude's live directories

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_COMMANDS_DIR="$ROOT_DIR/commands"
SOURCE_AGENTS_DIR="$ROOT_DIR/agents"
TARGET_COMMANDS_DIR="$HOME/.claude/commands"
TARGET_AGENTS_DIR="$HOME/.claude/agents"

mkdir -p "$TARGET_COMMANDS_DIR" "$TARGET_AGENTS_DIR"

link_dir() {
  local source_dir="$1"
  local target_dir="$2"
  local kind="$3"
  local source_file
  local base_name

  for source_file in "$source_dir"/*.md; do
    [ -f "$source_file" ] || continue
    base_name="$(basename "$source_file")"
    ln -sfn "$source_file" "$target_dir/$base_name"
    printf '%s_installed:%s -> %s\n' "$kind" "$target_dir/$base_name" "$source_file"
  done
}

link_dir "$SOURCE_COMMANDS_DIR" "$TARGET_COMMANDS_DIR" "command"
link_dir "$SOURCE_AGENTS_DIR" "$TARGET_AGENTS_DIR" "agent"
