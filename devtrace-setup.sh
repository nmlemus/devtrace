#!/usr/bin/env bash
# =============================================================================
# devtrace-setup.sh
# One-shot setup for AI coding tool provenance across:
#   Claude Code · Cursor · OpenCode · GitHub Copilot
#
# Usage:
#   chmod +x devtrace-setup.sh
#   ./devtrace-setup.sh
#   ./devtrace-setup.sh --name "Your Name" --stack "Python, FastAPI"
#   ./devtrace-setup.sh --tools claude,cursor  # install only specific tools
#   ./devtrace-setup.sh --dry-run              # preview without writing
# =============================================================================

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
DEVNAME="${DEVNAME:-}"
DEVSTACK="${DEVSTACK:-}"
DEVPROJECTS="${DEVPROJECTS:-}"
TOOLS="claude,cursor,opencode,copilot"
DRY_RUN=false
INSTALL_OMEGA=true
INSTALL_EMBEDDINGS=true   # bge-small-en-v1.5 via sentence-transformers (~130MB)
TRACE_DIR="${HOME}/.devtrace"
DECISIONS_FILE="${TRACE_DIR}/decisions.md"
DEVTRACE_VENV="${TRACE_DIR}/venv"
OMEGA_BIN="${TRACE_DIR}/venv/bin/omega"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()     { echo -e "${CYAN}▶${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*"; exit 1; }
header()  { echo -e "\n${BOLD}$*${RESET}"; echo "$(printf '─%.0s' {1..60})"; }
dryrun()  { echo -e "${YELLOW}[dry-run]${RESET} $*"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)     DEVNAME="$2";     shift 2 ;;
    --stack)    DEVSTACK="$2";    shift 2 ;;
    --projects) DEVPROJECTS="$2"; shift 2 ;;
    --tools)    TOOLS="$2";       shift 2 ;;
    --no-omega)       INSTALL_OMEGA=false;      shift ;;
    --embeddings)     INSTALL_EMBEDDINGS=true;  shift ;;
    --no-embeddings)  INSTALL_EMBEDDINGS=false; shift ;;
    --dry-run)        DRY_RUN=true;             shift ;;
    -h|--help)
      echo "Usage: $0 [--name 'Your Name'] [--stack 'Python, Node'] [--tools claude,cursor] [--no-omega] [--no-embeddings] [--dry-run]"
      exit 0 ;;
    *) warn "Unknown argument: $1"; shift ;;
  esac
done

# ── Interactive prompts if not set ────────────────────────────────────────────
prompt_if_empty() {
  local varname="$1" prompt="$2" default="$3"
  if [[ -z "${!varname}" ]]; then
    read -rp "$(echo -e "${CYAN}?${RESET} ${prompt} [${default}]: ")" val
    eval "$varname=\"${val:-$default}\""
  fi
}

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   DevTrace — AI Tool Setup           ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""

prompt_if_empty DEVNAME     "Your full name"                   "Developer"
prompt_if_empty DEVSTACK    "Your primary stack (comma list)"  "Python, TypeScript"
prompt_if_empty DEVPROJECTS "Active projects (comma list)"     "project-a, project-b"

# ── Helpers ───────────────────────────────────────────────────────────────────
write_file() {
  local path="$1" content="$2"
  local dir
  dir="$(dirname "$path")"
  if $DRY_RUN; then
    dryrun "Would write: $path"
    return
  fi
  mkdir -p "$dir"
  if [[ -f "$path" ]]; then
    cp "$path" "${path}.devtrace-backup-$(date +%Y%m%d%H%M%S)"
    warn "Backed up existing: $path"
  fi
  printf '%s\n' "$content" > "$path"
  success "Written: $path"
}

tool_enabled() { echo "$TOOLS" | grep -qw "$1"; }

# ── Build the provenance protocol block (shared across tools) ─────────────────
PROV_BLOCK='## Provenance protocol (mandatory)

### Decision log location
Decisions are stored PER PROJECT in: ~/.devtrace/decisions/<project>.md
- Deduce <project> from the current working directory name automatically
  e.g. if cwd is ~/projects/dsagent → file is ~/.devtrace/decisions/dsagent.md
- If cwd is home (~) or unclear → use ~/.devtrace/decisions/global.md
- Create the file if it does not exist yet

### What to log automatically (no need to ask)
Log an entry WITHOUT waiting to be asked whenever ANY of these happen:
- An architectural decision is made (framework choice, pattern, structure)
- A dependency is added or removed with a reason
- A design alternative is rejected (log what was rejected and why)
- A bug is fixed that reveals a systemic issue
- An approach is changed from what was previously decided
- A significant tradeoff is accepted

### What NOT to log
- Routine code edits, formatting, small fixes
- Decisions that are immediately reversed in the same session
- Anything the user explicitly says not to log

### Log format (append-only)
```
## [YYYY-MM-DD] <short decision title>
- **Tool**: <Claude Code / Cursor / OpenCode / Copilot>
- **Project**: <deduced from cwd>
- **Decision**: <what was decided>
- **Rationale**: <why — tradeoffs, alternatives rejected>
- **Files affected**: <comma list or "none">
- **Open questions**: <anything unresolved, or "none">
```

### Rules
- APPEND-ONLY — never edit existing entries
- Write the entry silently in the background — do not announce it unless
  the user asks to see it
- If a decision reverses a prior one, reference the original entry date
- Confidence threshold: only log if the decision has non-obvious impact.
  Routine choices (variable names, minor formatting) do not qualify.

## Session start protocol

1. Deduce project name from current working directory
2. Read ~/.devtrace/decisions/<project>.md silently if it exists
3. Note any open questions from prior sessions — mention them briefly
   if relevant to what the user is asking
4. Do NOT summarise the log aloud — just use the context'

# ── CLAUDE CODE ───────────────────────────────────────────────────────────────
if tool_enabled "claude"; then
  header "Claude Code — ~/.claude/CLAUDE.md"

  CLAUDE_MD="# Global developer context — ${DEVNAME}
# Auto-generated by devtrace-setup.sh on $(date +%Y-%m-%d)
# Edit freely. Re-run setup script to regenerate.

## Identity
- Developer: ${DEVNAME}
- Stack: ${DEVSTACK}
- Active projects: ${DEVPROJECTS}
- Preferences: minimal architecture, no over-engineering, adversarial
  analysis before major architecture changes, early returns, max 2 nesting
  levels

${PROV_BLOCK}

## Coding style
- Prefer explicit over implicit
- Write adversarial analysis before proposing major architecture changes
- Tag architectural decisions with [ADR] in commit messages
- Never add dependencies without asking first
- Tests before implementation on refactors

## Context on first message
Deduce the current project from the working directory automatically.
Do NOT ask the user which project — figure it out from cwd."

  write_file "${HOME}/.claude/CLAUDE.md" "$CLAUDE_MD"
fi

# ── CURSOR ────────────────────────────────────────────────────────────────────
if tool_enabled "cursor"; then
  header "Cursor — ~/.cursor/rules/devtrace.mdc"

  CURSOR_MDC="---
description: DevTrace provenance rules for ${DEVNAME}
globs: ['**/*']
alwaysApply: true
---

# Developer context — ${DEVNAME}
Stack: ${DEVSTACK}
Active projects: ${DEVPROJECTS}

${PROV_BLOCK}

## Coding style
- Explicit over implicit
- ADR-tag commits for architecture decisions
- Ask before adding dependencies
- Prefer early returns, max 2 nesting levels"

  write_file "${HOME}/.cursor/rules/devtrace.mdc" "$CURSOR_MDC"

  # Also write legacy .cursorrules for older Cursor versions
  CURSORRULES="# DevTrace — ${DEVNAME}
# Stack: ${DEVSTACK}
# Generated: $(date +%Y-%m-%d)
#
# PROVENANCE: Append to ~/.devtrace/decisions.md at session end when a
# significant decision was made. Format: date, tool, project, decision,
# rationale, files affected, open questions. Entries are append-only.
#
# SESSION START: Read last 5 entries of ~/.devtrace/decisions.md silently.
#
# CODING: Early returns. Max 2 nesting. Explicit > implicit. ADR commits."

  write_file "${HOME}/.cursorrules" "$CURSORRULES"

  # Cursor MCP config — connects omega-memory server so Cursor has memory too
  # Cursor reads .cursor/mcp.json at the project level, but also supports
  # a global config at ~/.cursor/mcp.json for user-wide MCP servers
  CURSOR_MCP_FILE="${HOME}/.cursor/mcp.json"

  if $DRY_RUN; then
    dryrun "Would write Cursor MCP config: ${CURSOR_MCP_FILE}"
  else
    mkdir -p "${HOME}/.cursor"
    if [[ -f "$CURSOR_MCP_FILE" ]]; then
      cp "$CURSOR_MCP_FILE" "${CURSOR_MCP_FILE}.devtrace-backup-$(date +%Y%m%d%H%M%S)"
      warn "Backed up: ${CURSOR_MCP_FILE}"
      # Merge: add omega-memory to existing config using Python
      python3 - <<PYEOF2
import json
with open("${CURSOR_MCP_FILE}") as f:
    cfg = json.load(f)
cfg.setdefault("mcpServers", {})
cfg["mcpServers"]["omega-memory"] = {
    "command": "${OMEGA_BIN}",
    "args": ["mcp"],
    "env": {}
}
with open("${CURSOR_MCP_FILE}", "w") as f:
    json.dump(cfg, f, indent=2)
print("Merged omega-memory into existing Cursor MCP config")
PYEOF2
    else
      python3 - <<PYEOF2
import json, os
cfg = {
    "mcpServers": {
        "omega-memory": {
            "command": "${OMEGA_BIN}",
            "args": ["mcp"],
            "env": {}
        }
    }
}
os.makedirs("${HOME}/.cursor", exist_ok=True)
with open("${CURSOR_MCP_FILE}", "w") as f:
    json.dump(cfg, f, indent=2)
print("Created Cursor MCP config with omega-memory")
PYEOF2
    fi
    success "Cursor MCP configured → ${CURSOR_MCP_FILE}"
  fi
fi

# ── OPENCODE ──────────────────────────────────────────────────────────────────
if tool_enabled "opencode"; then
  header "OpenCode — ~/.config/opencode/instructions.md"

  OPENCODE_MD="# Global instructions — ${DEVNAME}
# Generated by devtrace-setup.sh on $(date +%Y-%m-%d)

## Developer context
- Name: ${DEVNAME}
- Stack: ${DEVSTACK}
- Active projects: ${DEVPROJECTS}

${PROV_BLOCK}

## Coding style
- Explicit over implicit
- Adversarial analysis before major changes
- Tag architectural decisions with [ADR] in commit messages"

  write_file "${HOME}/.config/opencode/instructions.md" "$OPENCODE_MD"

  # OpenCode also supports AGENTS.md at home level
  write_file "${HOME}/AGENTS.md" "$OPENCODE_MD"

  # OpenCode MCP config — ~/.config/opencode/config.json
  OPENCODE_CFG="${HOME}/.config/opencode/config.json"

  if $DRY_RUN; then
    dryrun "Would write OpenCode MCP config: ${OPENCODE_CFG}"
  else
    mkdir -p "${HOME}/.config/opencode"
    if [[ -f "$OPENCODE_CFG" ]]; then
      cp "$OPENCODE_CFG" "${OPENCODE_CFG}.devtrace-backup-$(date +%Y%m%d%H%M%S)"
      warn "Backed up: ${OPENCODE_CFG}"
      python3 - <<PYEOF2
import json
with open("${OPENCODE_CFG}") as f:
    cfg = json.load(f)
cfg.setdefault("mcp", {}).setdefault("servers", {})
cfg["mcp"]["servers"]["omega-memory"] = {
    "type": "local",
    "command": "${OMEGA_BIN}",
    "args": ["mcp"]
}
with open("${OPENCODE_CFG}", "w") as f:
    json.dump(cfg, f, indent=2)
print("Merged omega-memory into existing OpenCode config")
PYEOF2
    else
      python3 - <<PYEOF2
import json, os
cfg = {
    "mcp": {
        "servers": {
            "omega-memory": {
                "type": "local",
                "command": "${OMEGA_BIN}",
                "args": ["mcp"]
            }
        }
    }
}
os.makedirs("${HOME}/.config/opencode", exist_ok=True)
with open("${OPENCODE_CFG}", "w") as f:
    json.dump(cfg, f, indent=2)
print("Created OpenCode config with omega-memory MCP")
PYEOF2
    fi
    success "OpenCode MCP configured → ${OPENCODE_CFG}"
  fi
fi

# ── GITHUB COPILOT ────────────────────────────────────────────────────────────
if tool_enabled "copilot"; then
  header "GitHub Copilot — VS Code workspace settings"

  # Global VS Code settings
  VSCODE_SETTINGS_DIR="${HOME}/.vscode"
  VSCODE_SETTINGS_FILE="${VSCODE_SETTINGS_DIR}/settings.json"

  COPILOT_INSTRUCTIONS="You are assisting ${DEVNAME}. Stack: ${DEVSTACK}. Active projects: ${DEVPROJECTS}.

PROVENANCE: At the end of sessions where a significant architectural decision was made, remind the user to append an entry to ~/.devtrace/decisions.md with: date, tool (GitHub Copilot), project, decision, rationale, files affected, open questions. Entries are append-only.

SESSION START: If the user mentions a project name, note any relevant context from ~/.devtrace/decisions.md if accessible.

CODING: Explicit over implicit. Early returns. Max 2 nesting levels. Adversarial analysis before major architecture changes. ADR-tag commits."

  if $DRY_RUN; then
    dryrun "Would update VS Code settings: ${VSCODE_SETTINGS_FILE}"
    dryrun "Would set github.copilot.chat.codeGeneration.instructions"
  else
    mkdir -p "$VSCODE_SETTINGS_DIR"
    if [[ -f "$VSCODE_SETTINGS_FILE" ]]; then
      cp "$VSCODE_SETTINGS_FILE" "${VSCODE_SETTINGS_FILE}.devtrace-backup-$(date +%Y%m%d%H%M%S)"
      warn "Backed up: $VSCODE_SETTINGS_FILE"
      # Merge: inject copilot key using Python (avoid jq dependency)
      python3 - <<PYEOF
import json, sys
with open("${VSCODE_SETTINGS_FILE}") as f:
    s = json.load(f)
s.setdefault("github.copilot.chat.codeGeneration.instructions", [])
# Remove any previous devtrace entry
s["github.copilot.chat.codeGeneration.instructions"] = [
    x for x in s["github.copilot.chat.codeGeneration.instructions"]
    if "devtrace" not in str(x).lower()
]
s["github.copilot.chat.codeGeneration.instructions"].append({
    "text": """${COPILOT_INSTRUCTIONS}"""
})
with open("${VSCODE_SETTINGS_FILE}", "w") as f:
    json.dump(s, f, indent=2)
print("Merged Copilot instructions into existing settings.json")
PYEOF
    else
      python3 - <<PYEOF
import json
s = {
    "github.copilot.chat.codeGeneration.instructions": [
        {"text": """${COPILOT_INSTRUCTIONS}"""}
    ]
}
import os; os.makedirs("${VSCODE_SETTINGS_DIR}", exist_ok=True)
with open("${VSCODE_SETTINGS_FILE}", "w") as f:
    json.dump(s, f, indent=2)
print("Created VS Code settings.json with Copilot instructions")
PYEOF
    fi
    success "VS Code Copilot instructions configured"
  fi
fi

# ── DevTrace directory + decisions folder ────────────────────────────────────
header "DevTrace — ~/.devtrace/"

DECISIONS_DIR="${TRACE_DIR}/decisions"
GLOBAL_DECISIONS="${DECISIONS_DIR}/global.md"

if $DRY_RUN; then
  dryrun "Would create: ${TRACE_DIR}/"
  dryrun "Would create: ${DECISIONS_DIR}/"
  dryrun "Would create: ${GLOBAL_DECISIONS}"
else
  mkdir -p "$DECISIONS_DIR"

  # Migrate legacy decisions.md → decisions/global.md if it exists
  if [[ -f "$DECISIONS_FILE" ]]; then
    warn "Migrating legacy decisions.md → decisions/global.md"
    cp "$DECISIONS_FILE" "${DECISIONS_DIR}/global.md"
    mv "$DECISIONS_FILE" "${DECISIONS_FILE}.legacy-$(date +%Y%m%d)"
    success "Migrated: decisions.md → decisions/global.md"
  fi

  # Create global.md if nothing exists yet
  if [[ ! -f "$GLOBAL_DECISIONS" ]]; then
    cat > "$GLOBAL_DECISIONS" <<EOF
# DevTrace — Global Decision Log
# Developer: ${DEVNAME}
# Created: $(date +%Y-%m-%d)
# Cross-project decisions live here. Per-project files: decisions/<project>.md

---
EOF
    success "Created: ${GLOBAL_DECISIONS}"
  fi

  # Create a README inside decisions/ so the pattern is clear
  cat > "${DECISIONS_DIR}/README.md" <<EOF
# decisions/

One file per project, auto-created by the AI tool when the first decision is logged.

- **<project>.md** — decisions made while working in ~/.../<project>/
- **global.md** — cross-project decisions or decisions made from home dir

Files are append-only. Never edit existing entries.
Query: devwhy "<topic>"
EOF
  success "Created: ${DECISIONS_DIR}/README.md"
fi

# ── Python installer helper ───────────────────────────────────────────────────
# macOS + Homebrew Python is "externally managed" (PEP 668).
# We install omega into a dedicated venv at ~/.devtrace/venv so we never
# touch system Python. A wrapper script at ~/.local/bin/omega activates it.
VENV_PIP="${DEVTRACE_VENV}/bin/pip"
VENV_PYTHON="${DEVTRACE_VENV}/bin/python3"
OMEGA_WRAPPER="${HOME}/.local/bin/omega"

pip_install() {
  # Usage: pip_install <package> [--quiet]
  if $DRY_RUN; then
    dryrun "pip install $*  (inside ${DEVTRACE_VENV})"
    return
  fi
  "${VENV_PIP}" install "$@"
}

ensure_venv() {
  if [[ -d "$DEVTRACE_VENV" ]]; then
    success "venv already exists: ${DEVTRACE_VENV}"
    return
  fi
  log "Creating DevTrace venv at ${DEVTRACE_VENV}..."
  python3 -m venv "$DEVTRACE_VENV"
  "${VENV_PIP}" install --upgrade pip --quiet
  success "venv created"
}

# ── Install omega-memory ──────────────────────────────────────────────────────
if $INSTALL_OMEGA; then
  header "omega-memory — cross-session memory MCP"

  if $DRY_RUN; then
    dryrun "python3 -m venv ${DEVTRACE_VENV}"
    dryrun "pip install omega-memory  (inside venv)"
    dryrun "omega setup && omega doctor"
  else
    ensure_venv
    log "Installing omega-memory into venv..."
    pip_install omega-memory --quiet

    # Create wrapper so 'omega' works from any shell without activating venv
    mkdir -p "${HOME}/.local/bin"
    cat > "$OMEGA_WRAPPER" <<WRAPPER
#!/usr/bin/env bash
exec "${DEVTRACE_VENV}/bin/omega" "\$@"
WRAPPER
    chmod +x "$OMEGA_WRAPPER"

    # Add ~/.local/bin to PATH in shell rc if missing
    for RC in "${HOME}/.zshrc" "${HOME}/.bashrc"; do
      if [[ -f "$RC" ]] && ! grep -q 'local/bin' "$RC"; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
        warn "Added ~/.local/bin to PATH in ${RC}"
      fi
    done

    # Run omega setup using the venv binary directly — no source/activate needed
    # || true prevents set -euo pipefail from killing the script on non-fatal
    # errors like "MCP server already registered"
    "${DEVTRACE_VENV}/bin/omega" setup || true

    # Install daemon (HTTP mode) — runs as background launchd service on macOS
    # More robust than stdio: persists between sessions, survives shell restarts
    log "Installing omega daemon (HTTP mode)..."
    "${DEVTRACE_VENV}/bin/python3" -m omega serve install \
      || warn "Daemon install failed — try manually: ${DEVTRACE_VENV}/bin/python3 -m omega serve install"

    # Migrate MCP config so Claude Code knows how to connect to the daemon
    log "Configuring MCP server for Claude Code..."
    "${DEVTRACE_VENV}/bin/python3" -m omega serve migrate-config \
      || warn "MCP config migration failed — try manually: ${DEVTRACE_VENV}/bin/python3 -m omega serve migrate-config"

    "${DEVTRACE_VENV}/bin/omega" doctor || warn "omega doctor reported issues — check output above"
    success "omega-memory installed → ${OMEGA_WRAPPER}"
  fi
fi

# ── Download omega's ONNX embedding model ────────────────────────────────────
# omega uses its OWN ONNX model (bge-small-en-v1.5) stored at
# ~/.cache/omega/models/ — NOT sentence-transformers/HuggingFace.
# 'omega setup --download-model' downloads model.onnx + tokenizer.json.
if $INSTALL_OMEGA && $INSTALL_EMBEDDINGS; then
  header "omega embeddings model — bge-small-en-v1.5 ONNX (~86MB)"

  OMEGA_MODELS_DIR="${HOME}/.cache/omega/models"

  if $DRY_RUN; then
    dryrun "omega setup --download-model"
  else
    # Check if ANY omega model has a tokenizer.json (legacy or new)
    TOKENIZER_FOUND=$(find "${OMEGA_MODELS_DIR}" -name "tokenizer.json" 2>/dev/null | head -1)
    if [[ -n "$TOKENIZER_FOUND" ]]; then
      success "omega ONNX model already complete — skipping download"
    else
      log "Downloading omega embedding model (once only)..."
      "${DEVTRACE_VENV}/bin/omega" setup --download-model \
        || warn "Model download failed. Retry: source ~/.devtrace/venv/bin/activate && omega setup --download-model"
      # Verify
      TOKENIZER_FOUND=$(find "${OMEGA_MODELS_DIR}" -name "tokenizer.json" 2>/dev/null | head -1)
      if [[ -n "$TOKENIZER_FOUND" ]]; then
        success "omega ONNX model ready — semantic search enabled"
      else
        warn "tokenizer.json still missing — run: omega setup --download-model"
      fi
    fi
  fi
elif $INSTALL_OMEGA && ! $INSTALL_EMBEDDINGS; then
  warn "Embeddings skipped (--no-embeddings) — omega will use hash fallback"
  warn "Re-run with --embeddings anytime, or run: omega setup --download-model"
fi

# ── Shell aliases ─────────────────────────────────────────────────────────────
header "Shell aliases"

# Copy dashboard script to ~/.devtrace/ so aliases always find it
DASHBOARD_DEST="${TRACE_DIR}/devtrace-dashboard.py"
if [[ -f "${SCRIPT_DIR}/devtrace-dashboard.py" ]]; then
  if $DRY_RUN; then
    dryrun "Would copy devtrace-dashboard.py → ${DASHBOARD_DEST}"
  else
    cp "${SCRIPT_DIR}/devtrace-dashboard.py" "${DASHBOARD_DEST}"
    success "Dashboard installed → ${DASHBOARD_DEST}"
  fi
else
  warn "devtrace-dashboard.py not found next to setup script — download it and place at ${DASHBOARD_DEST}"
fi

ALIAS_BLOCK="
# DevTrace aliases (added by devtrace-setup.sh)
alias devlog='python3 ${TRACE_DIR}/devtrace-dashboard.py --summary'
alias devdash='python3 ${TRACE_DIR}/devtrace-dashboard.py --serve'
alias devask='claude --print'
alias devwhy='claude --print \"Read all .md files in ~/.devtrace/decisions/ and explain why: \$*\"'
"

SHELL_RC=""
if [[ "$OSTYPE" == "darwin"* ]]; then
  # macOS default shell is zsh — always use .zshrc, create if missing
  SHELL_RC="${HOME}/.zshrc"
  touch "$SHELL_RC"
elif [[ -f "${HOME}/.zshrc" ]]; then
  SHELL_RC="${HOME}/.zshrc"
elif [[ -f "${HOME}/.bashrc" ]]; then
  SHELL_RC="${HOME}/.bashrc"
else
  # Create .zshrc as fallback
  SHELL_RC="${HOME}/.zshrc"
  touch "$SHELL_RC"
fi

if [[ -n "$SHELL_RC" ]]; then
  if grep -q "DevTrace aliases" "$SHELL_RC" 2>/dev/null; then
    warn "Aliases already present in ${SHELL_RC} — skipping"
  else
    if $DRY_RUN; then
      dryrun "Would append aliases to ${SHELL_RC}"
    else
      echo "$ALIAS_BLOCK" >> "$SHELL_RC"
      success "Aliases added to ${SHELL_RC}"
    fi
  fi
else
  warn "No .zshrc or .bashrc found — add aliases manually"
  echo "$ALIAS_BLOCK"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
header "Setup complete"

echo ""
echo -e "  ${GREEN}✓${RESET}  Decision log  →  ${BOLD}~/.devtrace/decisions.md${RESET}"
[[ "$TOOLS" == *"claude"*  ]] && echo -e "  ${GREEN}✓${RESET}  Claude Code   →  ${BOLD}~/.claude/CLAUDE.md${RESET}"
[[ "$TOOLS" == *"cursor"*  ]] && echo -e "  ${GREEN}✓${RESET}  Cursor        →  ${BOLD}~/.cursor/rules/devtrace.mdc  +  ~/.cursor/mcp.json${RESET}"
[[ "$TOOLS" == *"opencode"* ]] && echo -e "  ${GREEN}✓${RESET}  OpenCode      →  ${BOLD}~/.config/opencode/instructions.md  +  config.json${RESET}"
[[ "$TOOLS" == *"copilot"* ]] && echo -e "  ${GREEN}✓${RESET}  Copilot       →  ${BOLD}~/.vscode/settings.json${RESET}"
$INSTALL_OMEGA && echo -e "  ${GREEN}✓${RESET}  omega-memory  →  ${BOLD}~/.devtrace/venv/${RESET}"
$INSTALL_OMEGA && echo -e "  ${GREEN}✓${RESET}  omega wrapper →  ${BOLD}~/.local/bin/omega${RESET}"
$INSTALL_OMEGA && $INSTALL_EMBEDDINGS && echo -e "  ${GREEN}✓${RESET}  embeddings    →  ${BOLD}~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5${RESET}"
echo ""
echo -e "  Reload shell:  ${CYAN}source ${SHELL_RC:-~/.zshrc}${RESET}"
echo -e "  View log:      ${CYAN}devlog${RESET}"
echo -e "  Dashboard:     ${CYAN}devdash${RESET}"
echo -e "  Ask a why:     ${CYAN}devwhy 'we chose LiteLLM'${RESET}"
echo ""
