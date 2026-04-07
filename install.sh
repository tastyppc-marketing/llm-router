#!/usr/bin/env bash
# ============================================================================
# llm-router installer
#
# Installs the llm-router plugin for Claude Code with global availability.
# Handles prerequisites, plugin registration, settings, and Gemini policy.
#
# Usage:
#   curl -fsSL <raw-url>/install.sh | bash
#   -- or --
#   git clone https://github.com/tastyppc-marketing/llm-router.git && cd llm-router && bash install.sh
#
# Options:
#   --branch <name>    Install from a specific branch (default: main)
#   --skip-prereqs     Skip prerequisite checks (if you know they're installed)
#   --uninstall        Remove the plugin and clean up settings
#   --help             Show this help
# ============================================================================
set -euo pipefail

# ---- Configuration ---------------------------------------------------------
REPO_URL="https://github.com/tastyppc-marketing/llm-router.git"
PLUGIN_DIR="$HOME/.claude/plugins/llm-router"
GEMINI_POLICY_DIR="$HOME/.gemini/policies"
GEMINI_POLICY_FILE="$GEMINI_POLICY_DIR/llm-router.toml"
SETTINGS_FILE="$HOME/.claude/settings.json"
BRANCH="main"
SKIP_PREREQS=false
UNINSTALL=false

# ---- Colors ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No color

# ---- Helpers ---------------------------------------------------------------
info()  { echo -e "${BLUE}[info]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
fail()  { echo -e "${RED}[fail]${NC}  $*"; exit 1; }
step()  { echo -e "\n${BOLD}==> $*${NC}"; }

# ---- Parse args ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)   BRANCH="$2"; shift 2 ;;
        --skip-prereqs) SKIP_PREREQS=true; shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        --help|-h)
            head -20 "$0" | grep -E "^#" | sed 's/^# \?//'
            exit 0
            ;;
        *) fail "Unknown option: $1. Use --help for usage." ;;
    esac
done

# ---- Uninstall -------------------------------------------------------------
if $UNINSTALL; then
    step "Uninstalling llm-router"

    if [ -d "$PLUGIN_DIR" ]; then
        rm -rf "$PLUGIN_DIR"
        ok "Removed $PLUGIN_DIR"
    else
        warn "Plugin directory not found at $PLUGIN_DIR"
    fi

    if [ -f "$GEMINI_POLICY_FILE" ]; then
        rm -f "$GEMINI_POLICY_FILE"
        ok "Removed Gemini policy"
    fi

    # Remove symlinked commands and agents
    for f in "$HOME/.claude/commands/smart-team.md" \
             "$HOME/.claude/commands/router-status.md" \
             "$HOME/.claude/commands/router-validate.md"; do
        if [ -L "$f" ]; then rm -f "$f"; fi
    done
    for f in "$HOME/.claude/agents/"*.md; do
        if [ -L "$f" ] && readlink "$f" | grep -q "llm-router"; then
            rm -f "$f"
        fi
    done
    ok "Removed symlinked commands and agents"

    echo ""
    warn "Settings in $SETTINGS_FILE were not modified."
    warn "You may want to manually remove llm-router hooks and permissions."
    ok "Uninstall complete."
    exit 0
fi

# ---- Banner ----------------------------------------------------------------
echo ""
echo -e "${BOLD}  llm-router installer${NC}"
echo -e "  Smart LLM routing for Claude Code teams"
echo -e "  (Claude / Codex / Gemini)"
echo ""

# ---- Step 1: Prerequisites -------------------------------------------------
step "Checking prerequisites"

MISSING=()

check_cmd() {
    local cmd="$1"
    local name="$2"
    local install_hint="$3"
    if command -v "$cmd" &>/dev/null; then
        local version
        version=$("$cmd" --version 2>/dev/null | head -1 || echo "installed")
        ok "$name: $version"
    else
        MISSING+=("$name")
        warn "$name not found. Install: $install_hint"
    fi
}

if ! $SKIP_PREREQS; then
    # Required
    check_cmd "python3" "Python 3" "https://python.org or: sudo apt install python3"
    check_cmd "git"     "Git"      "https://git-scm.com or: sudo apt install git"
    check_cmd "tmux"    "tmux"     "sudo apt install tmux (Linux) or brew install tmux (macOS)"
    check_cmd "claude"  "Claude CLI" "npm install -g @anthropic-ai/claude-code"
    check_cmd "jq"      "jq"      "sudo apt install jq (Linux) or brew install jq (macOS)"

    # Optional but recommended
    if command -v codex &>/dev/null; then
        ok "Codex CLI: $(codex --version 2>/dev/null | head -1 || echo 'installed')"
    else
        warn "Codex CLI not found (optional). Install: npm install -g @openai/codex"
    fi

    if command -v gemini &>/dev/null; then
        ok "Gemini CLI: $(gemini --version 2>/dev/null | head -1 || echo 'installed')"
    else
        warn "Gemini CLI not found (optional). Install: npm install -g @anthropic-ai/gemini-cli (or see Google docs)"
    fi

    # Python version check
    PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        ok "Python version $PYTHON_VERSION (>= 3.10 required)"
    else
        MISSING+=("Python >= 3.10 (found $PYTHON_VERSION)")
    fi

    if [ ${#MISSING[@]} -gt 0 ]; then
        echo ""
        fail "Missing required prerequisites: ${MISSING[*]}. Install them and re-run."
    fi
else
    warn "Skipping prerequisite checks (--skip-prereqs)"
fi

# ---- Step 2: Clone or update repo ------------------------------------------
step "Installing plugin"

if [ -d "$PLUGIN_DIR/.git" ]; then
    info "Plugin directory exists. Updating from origin/$BRANCH..."
    cd "$PLUGIN_DIR"
    git fetch origin
    git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
    git pull origin "$BRANCH"
    ok "Updated to latest origin/$BRANCH"
else
    if [ -d "$PLUGIN_DIR" ]; then
        warn "Plugin directory exists but is not a git repo. Backing up..."
        mv "$PLUGIN_DIR" "${PLUGIN_DIR}.backup.$(date +%Y%m%d%H%M%S)"
    fi
    info "Cloning from $REPO_URL (branch: $BRANCH)..."
    mkdir -p "$(dirname "$PLUGIN_DIR")"
    git clone --branch "$BRANCH" "$REPO_URL" "$PLUGIN_DIR"
    ok "Cloned to $PLUGIN_DIR"
fi

# ---- Step 3: Make scripts executable ----------------------------------------
step "Setting permissions"

chmod +x "$PLUGIN_DIR"/tools/*.sh
chmod +x "$PLUGIN_DIR"/tools/*.py
chmod +x "$PLUGIN_DIR"/hooks/*.sh
chmod +x "$PLUGIN_DIR"/install.sh
ok "All scripts marked executable"

# ---- Step 4: Symlink commands and agents for global availability ------------
step "Registering commands and agents globally"

mkdir -p "$HOME/.claude/commands" "$HOME/.claude/agents"

# Commands
for cmd_file in "$PLUGIN_DIR"/commands/*.md; do
    target="$HOME/.claude/commands/$(basename "$cmd_file")"
    ln -sf "$cmd_file" "$target"
    ok "Command: /$(basename "$cmd_file" .md)"
done

# Agents
for agent_file in "$PLUGIN_DIR"/agents/*.md; do
    target="$HOME/.claude/agents/$(basename "$agent_file")"
    ln -sf "$agent_file" "$target"
    ok "Agent: $(basename "$agent_file" .md)"
done

# ---- Step 5: Install Gemini policy -----------------------------------------
step "Setting up Gemini safety policy"

if [ -f "$GEMINI_POLICY_FILE" ]; then
    ok "Gemini policy already exists at $GEMINI_POLICY_FILE"
else
    mkdir -p "$GEMINI_POLICY_DIR"
    cat > "$GEMINI_POLICY_FILE" << 'POLICY_EOF'
# llm-router Gemini policy rules
#
# Purpose:
# - keep Gemini usable in headless/yolo mode for delegated router tasks
# - block obviously destructive shell commands
# - keep the GI lane read-only; implementation belongs to CX/CC

[[rule]]
toolName = "write_file"
decision = "deny"
priority = 950
deny_message = "Gemini router tasks are read-only by policy. Use Codex or Claude execution lanes for edits."

[[rule]]
toolName = "replace"
decision = "deny"
priority = 951
deny_message = "Gemini router tasks are read-only by policy. Use Codex or Claude execution lanes for edits."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "rm -rf"
decision = "deny"
priority = 975
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "rmdir "
decision = "deny"
priority = 976
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "git reset --hard"
decision = "deny"
priority = 977
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "git clean -fd"
decision = "deny"
priority = 978
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "shutdown "
decision = "deny"
priority = 979
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "reboot "
decision = "deny"
priority = 980
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "poweroff "
decision = "deny"
priority = 981
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = "mkfs"
decision = "deny"
priority = 982
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."

[[rule]]
toolName = "run_shell_command"
commandPrefix = ":(){:"
decision = "deny"
priority = 983
deny_message = "Destructive shell commands are blocked by llm-router Gemini policy."
POLICY_EOF
    ok "Created Gemini policy at $GEMINI_POLICY_FILE"
fi

# ---- Step 6: Configure Claude settings.json --------------------------------
step "Configuring Claude Code settings"

PLUGIN_ROOT="$PLUGIN_DIR"

if [ ! -f "$SETTINGS_FILE" ]; then
    info "No settings.json found. Creating one..."
    mkdir -p "$(dirname "$SETTINGS_FILE")"
    echo '{}' > "$SETTINGS_FILE"
fi

# Use Python for reliable JSON manipulation (jq can lose formatting)
python3 << SETTINGS_PYTHON
import json
from pathlib import Path

settings_path = Path("$SETTINGS_FILE")
plugin_root = "$PLUGIN_ROOT"

settings = json.loads(settings_path.read_text())

# --- env: enable agent teams ---
env = settings.setdefault("env", {})
env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"

# --- permissions ---
perms = settings.setdefault("permissions", {})
allow = perms.setdefault("allow", [])

needed_perms = [
    'Bash("\$HOME/.claude/plugins/llm-router/tools/codex_worker.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/claude_worker.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/gemini_worker.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/codex_smart_team.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/router_validate.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/router_status.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/failure_mode_flow.sh":*)',
    'Bash("\$HOME/.claude/plugins/llm-router/tools/router_analytics.sh":*)',
    "Bash(tmux list-sessions:*)",
    "Bash(tmux list-panes:*)",
]

for perm in needed_perms:
    if perm not in allow:
        allow.append(perm)

# --- hooks: add llm-router hooks if not present ---
hooks = settings.setdefault("hooks", {})

# Helper: check if a hook command is already present
def has_hook(hook_list, command_fragment):
    for entry in hook_list:
        if isinstance(entry, dict) and command_fragment in entry.get("command", ""):
            return True
    return False

# PreToolUse: protect-files on Edit|Write
pre = hooks.setdefault("PreToolUse", [])
if not any(has_hook(entry.get("hooks", []), "protect-files.sh")
           for entry in pre if isinstance(entry, dict) and "Edit" in entry.get("matcher", "")):
    pre.append({
        "matcher": "Edit|Write",
        "hooks": [{
            "type": "command",
            "command": 'bash "\$HOME/.claude/plugins/llm-router/hooks/protect-files.sh"',
            "timeout": 10
        }]
    })

# PostToolUse: run-tests-async on Write|Edit
post = hooks.setdefault("PostToolUse", [])
if not any(has_hook(entry.get("hooks", []), "run-tests-async.sh")
           for entry in post if isinstance(entry, dict)):
    post.append({
        "matcher": "Write|Edit",
        "hooks": [{
            "type": "command",
            "command": 'bash "\$HOME/.claude/plugins/llm-router/hooks/run-tests-async.sh"',
            "timeout": 300,
            "async": True
        }]
    })

# TaskCompleted: task-complete-gate
tc = hooks.setdefault("TaskCompleted", [])
if not any(has_hook(entry.get("hooks", []), "task-complete-gate.sh")
           for entry in tc if isinstance(entry, dict)):
    tc.append({
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": 'bash "\$HOME/.claude/plugins/llm-router/hooks/task-complete-gate.sh"',
            "timeout": 900
        }]
    })

settings_path.write_text(json.dumps(settings, indent=4) + "\n")
print("Settings updated successfully.")
SETTINGS_PYTHON

ok "Claude Code settings configured"

# ---- Step 7: Verify installation -------------------------------------------
step "Verifying installation"

ERRORS=0

# Check plugin directory
if [ -d "$PLUGIN_DIR/.claude-plugin" ]; then
    ok "Plugin structure valid"
else
    warn "Missing .claude-plugin directory"
    ERRORS=$((ERRORS + 1))
fi

# Check commands are symlinked
for cmd in smart-team router-status router-validate; do
    if [ -L "$HOME/.claude/commands/$cmd.md" ]; then
        ok "Command /$cmd linked"
    else
        warn "Command /$cmd not linked"
        ERRORS=$((ERRORS + 1))
    fi
done

# Check key agents
for agent in cx-executor cc-diagnostician gi-mapper qa-tester code-reviewer; do
    if [ -L "$HOME/.claude/agents/$agent.md" ]; then
        ok "Agent $agent linked"
    else
        warn "Agent $agent not linked"
        ERRORS=$((ERRORS + 1))
    fi
done

# Check settings has agent teams enabled
if grep -q "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" "$SETTINGS_FILE" 2>/dev/null; then
    ok "Agent teams enabled in settings"
else
    warn "Agent teams not found in settings"
    ERRORS=$((ERRORS + 1))
fi

# Check Gemini policy
if [ -f "$GEMINI_POLICY_FILE" ]; then
    ok "Gemini safety policy installed"
else
    warn "Gemini policy not installed (Gemini routing will work but without safety blocks)"
fi

# ---- Step 8: Show first-run welcome ----------------------------------------
step "Welcome"

# Remove any previous marker so the welcome shows fresh after install/update
rm -f "$PLUGIN_DIR/.first-run-shown"
bash "$PLUGIN_DIR/tools/first_run_welcome.sh"

# ---- Done -------------------------------------------------------------------
echo ""
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}${BOLD}Installation complete!${NC}"
else
    echo -e "${YELLOW}${BOLD}Installation complete with $ERRORS warning(s).${NC}"
fi

echo ""
echo -e "  ${BOLD}To uninstall:${NC}"
echo "    bash $PLUGIN_DIR/install.sh --uninstall"
echo ""
