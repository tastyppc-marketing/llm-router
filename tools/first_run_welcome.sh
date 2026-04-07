#!/usr/bin/env bash
# ============================================================================
# First-run welcome for llm-router
# Shows ASCII art + "What's new" once after install, then never again.
# Called by the installer at the end. Creates a marker file to prevent re-show.
# ============================================================================
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARKER_FILE="$PLUGIN_DIR/.first-run-shown"
CHANGELOG="$PLUGIN_DIR/CHANGELOG.md"

# If already shown, exit silently
if [ -f "$MARKER_FILE" ]; then
    exit 0
fi

cat << 'ART'

    ______________________________________________________
   /                                                      \
  |   ___  __  __    ___  ___  _   _ _____ _____ ___      |
  |  | | \|  \/  |  | _ \/ _ \| | | |_   _| ____|  _ \   |
  |  | |  | |\/| |  |   / | | | | | | | | |  _| | |_) |  |
  |  | |_ | |  | |  | |\ \ |_| | |_| | | | |___|  _ <   |
  |  |___|_|  |_|  |_| \_\___/ \___/  |_| |_____|_| \_\  |
  |                                                        |
  |     .-------.       .-------.       .-------.          |
  |    / GEMINI /|     / CLAUDE /|     / CODEX  /|         |
  |   '-------' |    '-------' |    '-------' |          |
  |   | GI-MAP | |   | CC-DX  | |   | CX-EX  | |         |
  |   |  scan  |/    |  think |/    |  build |/          |
  |   '-------'     '-------'     '-------'            |
  |                                                        |
  |         Smart Team - Three-Lane LLM Routing            |
  |       Map it. Think it. Build it. Ship it.             |
   \______________________________________________________/

ART

echo ""
echo "  Welcome to llm-router!"
echo "  Your multi-agent routing system is installed and ready."
echo ""

# Show what's new if changelog exists
if [ -f "$CHANGELOG" ]; then
    echo "  ┌─────────────────────────────────────────────────┐"
    echo "  │                  What's New                     │"
    echo "  └─────────────────────────────────────────────────┘"
    echo ""
    # Show the latest unreleased section (up to first ---)
    awk '/^## \[Unreleased\]/,/^---$/' "$CHANGELOG" | head -30 | sed 's/^/  /'
    echo ""
fi

echo "  ┌─────────────────────────────────────────────────┐"
echo "  │                Quick Start                      │"
echo "  └─────────────────────────────────────────────────┘"
echo ""
echo "  1. Validate your setup:    /router-validate"
echo "  2. Check readiness:        /router-status"
echo "  3. Run your first team:    /smart-team \"describe your task\""
echo ""
echo "  Available routing lanes:"
echo "    GI-Mapper        Gemini   Large-context mapping & synthesis"
echo "    CC-Diagnostician Claude   Architecture, debugging, reasoning"
echo "    CX-Executor      Codex    Fast implementation & test loops"
echo ""

# Create marker so this never shows again
touch "$MARKER_FILE"
