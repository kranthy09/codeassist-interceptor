# CodeAssist Interceptor

**Structured reasoning capture + semantic context retrieval for Claude Code sessions.**

Parses Claude Code JSONL session files, extracts architectural decisions and reasoning into a queryable IR (Intermediate Representation), and serves relevant context back to Claude Code via MCP — eliminating redundant codebase re-reads across sessions.

## Problem

Each new Claude Code session re-reads files and reconstructs context that existed in previous sessions. The architectural thinking — why patterns were chosen, what was rejected, how components relate — is lost to ephemeral session boundaries.

## How It Works

```
Claude Code Session (JSONL)
    ↓ (watch)
Extract IRNodes: architecture decisions, implementations, rejections, patterns, bugs
    ↓ (parse)
Store: SQLite + vector embeddings (local, no API)
    ↓ (query)
MCP Tools: semantic + keyword search, ranked by relevance + recency
    ↓
Claude Code auto-calls tools during new sessions
```

## Prerequisites

- Python 3.11+
- Claude Code (any version)
- pyenv (for isolated virtualenv) — optional but recommended

## Integration

Three phases: **one-time machine setup** → **one-time project setup** → **daily workflow**.

### Phase A: One-time Machine Setup

Install codeassist-interceptor into a dedicated Python environment.

```bash
# 1. Create Python 3.11 virtualenv (pyenv recommended)
pyenv virtualenv 3.11.9 cai
pyenv activate cai

# 2. Install codeassist-interceptor
cd /path/to/codeassist-interceptor
pip install -e .

# 3. Verify installation
codeassist --version
# Expected output: 0.1.0
```

**What it does:** Sets up a Python environment with codeassist CLI tool and MCP server.

**Alternative installs (skip if using pyenv above):**
```bash
pip install --user codeassist-interceptor  # System user install
pip install codeassist-interceptor         # In activated venv
pipx install codeassist-interceptor        # Isolated pipx
```

### Phase B: One-time Project Setup

Commit MCP configuration to the project repo. Runs once per project.

```bash
# 1. Create wrapper script (portable across machines)
mkdir -p scripts
cat > scripts/cai-serve.sh << 'SCRIPT'
#!/bin/sh
if command -v codeassist >/dev/null 2>&1; then
    exec codeassist serve
fi
for VENV in cai codeassist codeassist-interceptor; do
    BIN="$HOME/.pyenv/versions/$VENV/bin/codeassist"
    [ -f "$BIN" ] && exec "$BIN" serve
done
echo "codeassist not found. Install: https://github.com/your-org/codeassist-interceptor" >&2
exit 1
SCRIPT
chmod +x scripts/cai-serve.sh

# 2. Create MCP config (committed to repo)
cat > .mcp.json << 'CONFIG'
{
  "mcpServers": {
    "codeassist-interceptor": {
      "type": "stdio",
      "command": "sh",
      "args": ["./scripts/cai-serve.sh"]
    }
  }
}
CONFIG

# 3. Verify MCP server loads
claude mcp list
# Expected: codeassist-interceptor: ✓ Connected

# 4. Backfill: Parse existing sessions
codeassist parse . --llm
# What it does: Scans ~/.claude/projects/<project>/ for session JSONL files,
# extracts IRNodes (decisions, patterns, bugs, etc.), stores in ~/.codeassist/ir.db

# 5. Start watcher (keep running in background)
codeassist watch . --llm &
# What it does: Monitors new sessions, auto-parses when file settles (debounce),
# LLM-assisted extraction for better accuracy

# 6. Commit to repo
git add scripts/cai-serve.sh .mcp.json
git commit -m "Add codeassist-interceptor MCP integration (project-scope)"
```

**What it does:** Registers codeassist as a project-scoped MCP server. When Claude Code opens a session in this project, it auto-loads the tools.

### Phase C: Developer Workflow

After Phase A and B setup, this is daily usage.

```bash
# Start watcher once per machine (keep running)
codeassist watch . --llm

# That's it. Claude Code does the rest:
# - Auto-calls MCP tools during sessions
# - Retrieves past decisions without re-reading files
# - No manual steps needed
```

**What it does:** When you ask Claude about the project, it can call `get_project_context("query")` automatically to pull relevant past decisions. No more re-reading.

---

## MCP Tools Reference

Called automatically by Claude Code. Can also be invoked manually via `/mcp` in Claude Code chat.

| Tool | Purpose | Example |
|------|---------|---------|
| **get_project_context** | Semantic search over past decisions | `get_project_context("authentication flow")` → returns 8 most relevant decisions ranked by relevance + recency |
| **get_decision_history** | Filter decisions by type and recency | `get_decision_history(decision_type="bugfix", days=7)` → returns bugs fixed in last week |
| **get_project_summary** | Overview of all captured reasoning | `get_project_summary()` → summarizes architectural patterns, common decisions, decision distribution |
| **search_decisions** | Keyword search | `search_decisions("database schema migration")` → returns all nodes matching keywords |

---

## CLI Reference

All commands work on the current project (use `.` as path).

| Command | Purpose | Example |
|---------|---------|---------|
| **parse** | One-time: extract existing sessions into DB | `codeassist parse . --llm` |
| **watch** | Auto-parse new sessions (debounced, keep running) | `codeassist watch . --llm` |
| **inspect** | View extracted nodes (table or JSON) | `codeassist inspect . --type bugfix` |
| **serve** | Start MCP server (called by Claude Code, not manual) | `codeassist serve` |

**Common options:**
- `--llm` — LLM-assisted extraction (Haiku, requires ANTHROPIC_API_KEY env var). Better accuracy, slower. Optional.
- `--json` — Output as JSON (inspect command)
- `--type <type>` — Filter by node type: `architecture`, `implementation`, `bugfix`, `dependency`, `pattern`, `rejection`, `convention`, `reasoning`

---

## Troubleshooting

### MCP server shows "not connected"

**Symptom:** `claude mcp list` shows `codeassist-interceptor: ! Not connected`

**Fix:**
```bash
# 1. Check binary can be found
which codeassist
# or
$HOME/.pyenv/versions/cai/bin/codeassist --version

# 2. If missing, install Phase A setup
pyenv virtualenv 3.11.9 cai && pyenv activate cai && pip install -e .

# 3. Re-verify MCP
claude mcp list
```

### No decisions found

**Symptom:** `get_project_summary()` returns "No reasoning captured yet"

**Fix:**
```bash
# 1. Backfill sessions
codeassist parse . --llm

# 2. Verify nodes were extracted
codeassist inspect .
# Should show table with nodes. If empty, check:
#   - Are session JSONL files in ~/.claude/projects/<project>/? 
#   - ls -la ~/.claude/projects/ | grep $(pwd | sed 's/\//-/g')

# 3. Start watcher for future sessions
codeassist watch . --llm &
```

### `codeassist` command not found in wrapper script

**Symptom:** Claude Code error: "codeassist not found. Install: ..."

**Fix:**
```bash
# Ensure installation in Phase A
pyenv virtualenv 3.11.9 cai
pyenv activate cai
pip install -e /path/to/codeassist-interceptor/
which codeassist  # should find it

# Test wrapper script manually
./scripts/cai-serve.sh --version
# If error, check $HOME/.pyenv/versions/cai/bin/codeassist exists
```

### Embedding model slow on first tool call

**Symptom:** First `get_project_context()` call takes 5-10 seconds, subsequent calls instant

**Expected behavior** — not a bug. The ~80MB sentence-transformers model loads on first call and is cached in memory. Subsequent calls reuse it instantly.

---

## Data Storage

All data is **local** (no cloud, no API calls):

```
~/.codeassist/ir.db
├── sessions table       (session metadata)
├── nodes table         (extracted IRNodes)
└── node_embeddings table (vector embeddings, sqlite-vec)
```

**Embeddings model:** `sentence-transformers/all-MiniLM-L6-v2` (~80MB, downloaded on first use, cached locally).

---

## Tech Stack

- Python 3.11+
- SQLite + sqlite-vec (zero infrastructure, local storage)
- FastMCP (MCP server framework)
- sentence-transformers (local embeddings, no API)
- watchdog (file system monitoring)
- Click (CLI)
- Pydantic (data validation)

---

## Architecture

**3 core modules:**

1. **Parser** (`src/parser/`) — Watches `~/.claude/projects/` for session JSONL, extracts IRNodes using rule-based patterns + optional LLM classifier
2. **Storage** (`src/storage/`) — Persists IRNodes to SQLite with vector embeddings
3. **Retriever** (`src/retriever/`) — Semantic + keyword + recency-ranked hybrid search
4. **MCP Server** (`src/mcp/`) — FastMCP stdio transport exposing retriever as 4 tools

**Key flow:** JSONL → Parse → Extract → Embed → Store → Retrieve → Tools

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Watch and auto-parse during development
CODEASSIST_LOG_LEVEL=DEBUG codeassist watch . --llm
```

---

## License

See LICENSE file.
