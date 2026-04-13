# DevTrace

**AI coding tool knowledge provenance** — one setup script that configures memory and decision tracking across Claude Code, Cursor, OpenCode, and GitHub Copilot. Includes a local web dashboard to visualize activity timelines, project breakdowns, and decision logs.

---

## What it does

Every AI coding session produces decisions, context, and knowledge that disappears when the session ends. DevTrace solves this in two complementary layers:

- **omega-memory** — automatically captures every tool use, edit, and observation per session into a local SQLite database with vector search. Injected back into context on the next session start.
- **decisions/** — one markdown file per project, written automatically by the agent whenever a significant decision is made — no need to ask. Includes what was decided, why, what alternatives were rejected, and what's still open.

Both layers are local, git-friendly, and tool-agnostic via MCP.

---

## Files

| File | Purpose |
|---|---|
| `devtrace-setup.sh` | One-shot installer — configures all tools, installs omega, downloads embedding model |
| `devtrace-dashboard.py` | Local web dashboard at `http://localhost:7474` |
| `README.md` | This file |

---

## Requirements

- macOS or Linux
- Python 3.11+
- `claude` CLI installed (for Claude Code)
- Cursor, OpenCode, or VS Code with Copilot (optional — configure only what you use)

---

## Installation

```bash
# Clone or download both files into any folder you sync across machines
# (Dropbox, iCloud, a git dotfiles repo, etc.)

chmod +x devtrace-setup.sh
./devtrace-setup.sh
```

The script will prompt for:
- **Your name** — embedded in all tool config files
- **Your stack** — e.g. `Python, FastAPI, TypeScript`
- **Active projects** — comma-separated names, used as context hints

### Options

```bash
# Skip prompts entirely
./devtrace-setup.sh \
  --name "Noel Camacho" \
  --stack "Python, FastAPI, LiteLLM" \
  --projects "OmAgent, DSAgent, Aiuda"

# Configure only specific tools
./devtrace-setup.sh --tools claude,cursor

# Skip omega installation
./devtrace-setup.sh --no-omega

# Skip embedding model download (~86MB, slower first run)
./devtrace-setup.sh --no-embeddings

# Preview without writing anything
./devtrace-setup.sh --dry-run
```

### What gets configured

| Tool | Config files | Memory | Auto-decisions |
|---|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` | ✅ omega hooks — fully automatic | ✅ |
| Cursor | `~/.cursor/rules/devtrace.mdc` + `~/.cursor/mcp.json` | ✅ omega MCP connected | ✅ |
| OpenCode | `~/.config/opencode/instructions.md` + `config.json` | ✅ omega MCP connected | ✅ |
| GitHub Copilot | `~/.vscode/settings.json` | ❌ no MCP support | ✅ |

### What gets installed

```
~/.devtrace/
├── venv/                    ← Python virtualenv with omega-memory
├── decisions/               ← per-project decision logs
│   ├── README.md
│   ├── global.md            ← cross-project decisions
│   ├── dsagent.md           ← auto-created when working in ~/projects/dsagent
│   └── <project>.md         ← one file per project, auto-created
└── devtrace-dashboard.py    ← dashboard (copied here automatically)

~/.local/bin/omega           ← wrapper that runs omega from the venv
~/.omega/omega.db            ← omega SQLite database (created on first session)
~/.cache/omega/models/       ← ONNX embedding model (~86MB, downloaded once)
```

---

## Usage

### Shell aliases (added automatically to `~/.zshrc`)

```bash
devdash                    # open web dashboard at http://localhost:7474
devlog                     # terminal summary of recent activity
devwhy "topic or project"  # ask Claude why a decision was made
```

### How decisions are captured

You never need to ask the agent to save a decision. All tools are configured to write automatically to `~/.devtrace/decisions/<project>.md` whenever any of these happen:

- An architectural decision is made (framework, pattern, structure)
- A dependency is added or removed with a reason
- A design alternative is explicitly rejected
- A bug reveals a systemic issue
- An approach changes from what was previously decided

The project name is deduced from the current working directory — no configuration needed. Working in `~/projects/dsagent` writes to `decisions/dsagent.md`. Working from home dir writes to `decisions/global.md`.

Entries are written silently in the background without interrupting the conversation.

### Decision log format

```markdown
## [2026-04-12] Migrate DSAgent to LiteLLM
- **Tool**: Claude Code
- **Project**: dsagent
- **Decision**: Use LiteLLM as the LLM abstraction layer
- **Rationale**: Provider agnosticism; easy swap between Anthropic/Gemini/OpenAI without code changes
- **Files affected**: config/llm.py, agents/base.py
- **Open questions**: Evaluate Gemini 2.0 Flash latency vs Claude Haiku for routing
```

Entries are **append-only** — never edited, only added.

### Dashboard tabs

- **Timeline** — sessions grouped by day, expandable to see every memory captured. Decision entries appear as markers on the day they were made. Filterable by project and full-text search.
- **Projects** — one card per project with memory count, session count, decisions, and activity dates. Click any card for a detail view.
- **Memories** — all raw omega observations with filters by project and event type.
- **Decisions** — all decision files with search and project filter.

### Manual omega queries

```bash
omega query "why did we choose LiteLLM"
omega status
omega setup --download-model   # re-download embedding model
```

---

## Adding a new project

Nothing required — open Claude Code inside the new project folder and everything works automatically. omega tags memories with the project path, and the agent creates `decisions/<project>.md` on the first significant decision.

Optionally add a local `CLAUDE.md` in the project root for project-specific context:

```bash
cd ~/projects/new-project
cat > CLAUDE.md << 'EOF'
# new-project

## What this is
<description>

## Stack
Python, FastAPI...

## Conventions
- early returns, max 2 nesting levels
- no dependencies without asking first
EOF
```

The global `~/.claude/CLAUDE.md` (identity + provenance protocol) loads first, the local `CLAUDE.md` extends it.

---

## New machine setup

```bash
# 1. Copy devtrace-setup.sh and devtrace-dashboard.py to the new machine
# 2. Run the installer
./devtrace-setup.sh --name "Your Name" --stack "..." --projects "..."

# 3. Reload shell
source ~/.zshrc

# 4. Verify
omega doctor
devlog
```

Tip: keep both files in a git repo (e.g. `~/dotfiles/devtrace/`) so setup on any new machine is a single `git clone` + `./devtrace-setup.sh`.

---

## Clean reinstall

```bash
# Remove everything
rm -rf ~/.devtrace ~/.omega
rm -f ~/.local/bin/omega ~/.cursorrules
rm -f ~/.cursor/mcp.json ~/.cursor/rules/devtrace.mdc
rm -f ~/.config/opencode/instructions.md ~/.config/opencode/config.json ~/AGENTS.md
sed -i '' '/DevTrace aliases/,/devwhy/d' ~/.zshrc

# Fresh install
./devtrace-setup.sh
source ~/.zshrc
```

---

## Troubleshooting

### `devdash: command not found`
```bash
echo "alias devdash='python3 ~/.devtrace/devtrace-dashboard.py --serve'" >> ~/.zshrc
echo "alias devlog='python3 ~/.devtrace/devtrace-dashboard.py --summary'" >> ~/.zshrc
echo "alias devwhy='claude --print \"Read all .md files in ~/.devtrace/decisions/ and explain why: \$*\"'" >> ~/.zshrc
source ~/.zshrc
```

### `omega.db not found`
Normal — created on first Claude Code session. Open Claude Code inside a project folder, do some work, close it, then check `ls ~/.omega/`.

### Embedding model missing / hash fallback
```bash
~/.devtrace/venv/bin/omega setup --download-model
```

### `externally-managed-environment` (macOS pip error)
The script handles this automatically via `~/.devtrace/venv/`. If it still appears:
```bash
python3 -m venv ~/.devtrace/venv
~/.devtrace/venv/bin/pip install omega-memory
~/.devtrace/venv/bin/omega setup
```

### omega setup returns exit code 1
Usually `MCP server already registered` — not a real error. Run `omega doctor` to confirm.

---

## Architecture

```
Claude Code / Cursor / OpenCode / Copilot
         │
         │  MCP (stdio or HTTP daemon)
         ▼
    omega-memory
    ├── SessionStart hook  → injects relevant memories into context
    ├── PostToolUse hook   → captures each tool call silently
    └── Stop hook          → compresses session into structured memories
         │
         ▼
    ~/.omega/omega.db
    ├── memories table     → text + metadata + project path
    ├── memories_vec       → vector embeddings (bge-small-en-v1.5 ONNX)
    ├── memories_fts       → FTS5 full-text index
    └── edges table        → knowledge graph relations

    agent (all tools) — writes automatically on significant decisions
         ▼
    ~/.devtrace/decisions/
    ├── global.md          → cross-project decisions
    └── <project>.md       → per-project, deduced from cwd

         ▼
    devtrace-dashboard.py  →  http://localhost:7474
    ├── Timeline           → sessions by day, expandable memories + decision markers
    ├── Projects           → per-project stats and detail panel
    ├── Memories           → all omega observations, filterable
    └── Decisions          → all decision files, searchable
```

---

## Publishing to GitHub

```bash
# 1. Create the repo on GitHub first (github.com → New repository → devtrace)
#    Set it to Public, no README, no .gitignore (we have our own)

# 2. From the folder with your 3 files:
git init
git add devtrace-setup.sh devtrace-dashboard.py README.md .gitignore
git commit -m "Initial release — DevTrace v1.0"
git branch -M main
git remote add origin https://github.com/<your-username>/devtrace.git
git push -u origin main
```

Then anyone can install with:

```bash
git clone https://github.com/<your-username>/devtrace.git
cd devtrace
./devtrace-setup.sh
```

### Keeping it updated

When you improve the scripts, just commit and push from the same folder:

```bash
git add -A
git commit -m "Fix: <what changed>"
git push
```

On any machine that already has it cloned:

```bash
cd ~/path/to/devtrace
git pull
./devtrace-setup.sh   # re-run to apply changes
```

---

## License

MIT — use freely, share widely.
