#!/usr/bin/env python3
"""Expose llm-router commands and agents to Claude's live directories."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from router_common import force_symlink


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
SOURCE_COMMANDS_DIR = ROOT_DIR / "commands"
SOURCE_AGENTS_DIR = ROOT_DIR / "agents"
TARGET_COMMANDS_DIR = Path.home() / ".claude" / "commands"
TARGET_AGENTS_DIR = Path.home() / ".claude" / "agents"


def usage() -> str:
    return "Usage: install_claude_surface.sh"


def link_dir(source_dir: Path, target_dir: Path, kind: str) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_file in sorted(source_dir.glob("*.md")):
        target_file = target_dir / source_file.name
        force_symlink(source_file, target_file)
        print(f"{kind}_installed:{target_file} -> {source_file}")


def main(argv: list[str]) -> int:
    if any(arg in {"-h", "--help"} for arg in argv):
        print(usage())
        return 0
    if any(arg not in {"-h", "--help"} for arg in argv):
        print(usage(), file=sys.stderr)
        return 2
    link_dir(SOURCE_COMMANDS_DIR, TARGET_COMMANDS_DIR, "command")
    link_dir(SOURCE_AGENTS_DIR, TARGET_AGENTS_DIR, "agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
