# CodeAssist Interceptor — Complete Setup & Usage Guide

## What is CodeAssist Interceptor?

CodeAssist Interceptor captures architectural reasoning from Claude Code sessions and surfaces it as context in future sessions via MCP (Model Context Protocol) tools. Every decision, rejected approach, and architectural pattern is extracted, stored in SQLite, and served back when you query Claude Code.

```
Claude Code JSONL sessions → Extract IR nodes → SQLite + embeddings → MCP tools → Claude Code
```

**Result**: Your project gains memory. "What was decided about auth?", "Why was library X rejected?" — instant answers without re-reading files.

---

## Installation (One-time setup)

### 1. Install the package

```bash
cd /home/kranthi/Projects/codeassist-interceptor/codeassist-interceptor
pip install -e ".[dev]"
```

Or if using a virtual environment:

```bash
/path/to/your/venv/bin/pip install -e ".[dev]"
```

### 2. Verify installation

```bash
codeassist --version
codeassist serve  # Should print: "Starting MCP server (stdio transport)"
```

Press `Ctrl+C` to stop the server.

### 3. Add bash alias (optional, but recommended)

Add to `~/.bashrc`:

```bash
alias cai-setup="/home/kranthi/Projects/codeassist-interceptor/cai-setup.sh"
```

Then reload:

```bash
source ~/.bashrc
```

---

## Quick Start: Set Up a Project

For any project with Claude Code sessions:

```bash
# Check current state (no changes)
cai-setup ~/Projects/your_project --status

# Parse sessions + register MCP server (typical workflow)
cai-setup ~/Projects/your_project --mcp

# With LLM-assisted extraction (better accuracy, costs tokens)
export ANTHROPIC_API_KEY="sk-ant-..."
cai-setup ~/Projects/your_project --llm --mcp

# Watch for new sessions (auto-parse as you work)
cai-setup ~/Projects/your_project --watch
```

---

## The `cai-setup` Script

**Location**: `/home/kranthi/Projects/codeassist-interceptor/cai-setup.sh`

### Flags

| Flag | Purpose |
|------|---------|
| `--mcp` | Register MCP server (project-scope `.mcp.json`) |
| `--mcp-only` | Only register MCP, skip parsing |
| `--llm` | Use Haiku for extraction (requires `ANTHROPIC_API_KEY`) |
| `--force` | Re-parse already processed sessions |
| `--watch` | Stay running, auto-parse new sessions (debounced) |
| `--status` | Show current state (no changes) |
| `--remove-mcp` | Remove MCP registration from project |
| `--min-confidence N` | Extraction threshold (default 0.4) |
| `--dry-run` | Show what would happen without executing |

### Examples

```bash
# First-time setup
cai-setup ~/Projects/enterprise_ai --mcp

# Update IR after coding session
cai-setup ~/Projects/enterprise_ai

# Full setup with LLM
cai-setup ~/Projects/enterprise_ai --llm --mcp

# Check status
cai-setup ~/Projects/enterprise_ai --status

# Force re-parse all
cai-setup ~/Projects/enterprise_ai --force --mcp

# Dry-run before committing
cai-setup ~/Projects/enterprise_ai --dry-run --llm --mcp
```

---

## Using in Claude Code

Once registered, MCP tools are automatically available in Claude Code sessions.

### 1. From Claude Code terminal, see what's captured:

```bash
cd ~/Projects/enterprise_ai
claude  # Opens Claude Code
```

In Claude Code, type `/mcp` to see available servers. Look for `codeassist-interceptor`.

### 2. In a conversation, query the tools naturally:

**User prompt**:
> "What architectural decisions have been made about the auth flow?"

Claude will automatically call `get_project_context("auth flow")` and return relevant decisions.

### 3. Available tools:

All 4 tools are available in the MCP server:

- **`get_project_context(query)`** — Semantic search over architectural decisions
  - Usage: "Before I change the database schema, check what was decided before"
  
- **`get_project_summary()`** — Overview of all captured reasoning
  - Usage: Start of new session to understand project state
  
- **`get_decision_history(decision_type, days)`** — Filtered decision list
  - Usage: "Show me all ARCHITECTURE decisions from the last 14 days"
  
- **`search_decisions(query)`** — Keyword search
  - Usage: "Find all decisions mentioning 'authentication'"

---

## How It Works: Extraction Pipeline

### 1. Session Discovery

Claude Code stores sessions at:
```
~/.claude/projects/<encoded-project-path>/*.jsonl
```

The script discovers sessions by encoding the project path (replacing `/` and `_` with `-`).

### 2. Session Parsing

Each JSONL file contains:
- User messages
- Claude's text responses
- Claude's thinking/reasoning (when available)
- Tool calls and results

The parser extracts meaningful turns: assistant responses that contain decisions.

### 3. IR Extraction

**Rule-based (fast)**:
- Regex patterns detect architecture, rejections, patterns, bugs, dependencies
- Confidence scored 0.0–1.0
- Filters conversational filler ("Let me check...", "I think...")

**LLM-assisted (accurate)**:
- Haiku classifies ambiguous turns
- Better for nuanced architectural reasoning
- Costs ~1 token per turn, confidence baseline 0.85

### 4. Deduplication

Nodes with summaries < 15 chars or matching filler patterns are dropped.

### 5. Storage & Indexing

Nodes stored in SQLite with:
- Full-text search (summary, rationale, tags, raw_source)
- Vector embeddings (semantic search)
- Project/timestamp indexing

### 6. Retrieval

When Claude Code queries a tool:
1. Extract keywords from query
2. Keyword search + recent nodes + file-scoped nodes
3. Semantic similarity scoring (vector search)
4. Recency decay (exponential half-life: 7 days)
5. Blended ranking with boosts for architecture/patterns
6. Return top-k deduped results

---

## Workflow Examples

### Example 1: First-time setup for enterprise_ai

```bash
$ cai-setup ~/Projects/enterprise_ai --status
[Shows 118 sessions found, 0 parsed]

$ cai-setup ~/Projects/enterprise_ai --mcp
[Parses 118 sessions...]
Done. 1601 new nodes extracted.
[Registers MCP server in .mcp.json]

$ cd ~/Projects/enterprise_ai
$ claude
# In Claude Code:
# "What's the architecture of this project?"
# → Returns get_project_summary() with key decisions
```

### Example 2: Update IR after a long coding session

```bash
$ cai-setup ~/Projects/enterprise_ai
[Detects new sessions since last parse]
[Parses only new ones (skips already-parsed)]
Done. 47 new nodes extracted.
```

### Example 3: Improve extraction quality with LLM

```bash
$ export ANTHROPIC_API_KEY="sk-ant-..."
$ cai-setup ~/Projects/enterprise_ai --force --llm
[Re-parses all 118 sessions with Haiku]
Done. 1723 nodes extracted (higher confidence avg).
```

### Example 4: Multiple projects

```bash
for proj in enterprise_ai companygate codegate cagentkb vizport; do
  cai-setup ~/Projects/$proj --mcp
done
```

---

## Database & Storage

**Location**: `~/.codeassist/ir.db` (SQLite)

### Schema

```sql
-- Sessions metadata
sessions (session_id PK, project_path, started_at, ended_at, model_used, total_turns, nodes_extracted, parsed_at)

-- IR nodes
nodes (id PK, session_id FK, project_path, timestamp, node_type, scope, 
       summary, rationale, alternatives, files_affected, tags, 
       parent_node_id, confidence, raw_source, created_at)

-- Vector embeddings for semantic search
node_embeddings (node_id PK, embedding BLOB, model_name)
```

### Node Types

| Type | Signal | Example |
|------|--------|---------|
| `architecture` | Design decisions, structure, patterns | "Chose JWT over session auth" |
| `implementation` | Implementation details (lower signal) | "Added error handling to login" |
| `pattern` | Recurring conventions, best practices | "Always validate input on API boundary" |
| `rejection` | Explicitly rejected approaches | "Considered Redis but chose Postgres" |
| `bugfix` | Root causes and fixes | "Fixed N+1 query in user list" |
| `dependency` | Library/package decisions | "Added FastAPI for async support" |

### Confidence Scoring

- **Rule-based**: 0.4–1.0 (depends on pattern match strength)
- **LLM-assisted**: 0.85 (baseline from Haiku)
- **Filtered**: nodes < 0.4 confidence are dropped by default

---

## Troubleshooting

### Issue: "No session files found"

**Cause**: Sessions not detected for your project path.

**Fix**:
1. Ensure Claude Code sessions exist: `ls ~/.claude/projects/ | grep your_project`
2. Use absolute path: `cai-setup /home/kranthi/Projects/your_project`
3. Check encoding: path should be `"home-kranthi-Projects-your_project"` in `~/.claude/projects/`

### Issue: MCP server not appearing in Claude Code

**Cause**: `.mcp.json` not created or Claude Code not restarted.

**Fix**:
1. Verify `.mcp.json` exists: `cat ~/Projects/your_project/.mcp.json`
2. Restart Claude Code
3. Type `/mcp` to see registered servers

### Issue: Low-quality summaries ("Let me check...", "...")

**Cause**: Extractor capturing conversational filler.

**Fix**:
1. Already fixed in latest version (use `--force` to re-parse)
2. Re-parse with LLM: `cai-setup ~/Projects/your_project --force --llm`

### Issue: Duplicate nodes in search results

**Cause**: Retriever not deduplicating.

**Fix**:
1. Already fixed in latest version
2. Clear and re-parse: Delete nodes from DB and run `cai-setup` again

---

## Performance Notes

- **First parse**: Loads ~80MB embedding model (sentence-transformers), ~10-20 min for 100+ sessions
- **Subsequent parses**: Parse only new sessions, ~5-10 sec per session
- **Watch mode**: Debounced 5 sec default, can tune with `--debounce`
- **Queries**: Semantic search ~50ms, keyword search ~10ms
- **Database**: Single `~/.codeassist/ir.db` file, shared across all projects

---

## Development

### Run tests

```bash
cd ~/Projects/codeassist-interceptor/codeassist-interceptor
pytest tests/ -v
```

### Debug extraction

```bash
codeassist --verbose parse ~/Projects/your_project
```

### Inspect stored nodes

```bash
codeassist inspect ~/Projects/your_project --limit 20
codeassist inspect ~/Projects/your_project --type architecture --limit 10
codeassist inspect ~/Projects/your_project --json | python3 -m json.tool
```

### Check database directly

```bash
sqlite3 ~/.codeassist/ir.db
.schema nodes
.tables
SELECT COUNT(*) FROM nodes WHERE project_path = '/home/kranthi/Projects/enterprise_ai';
```

---

## Architecture & Design

See [CLAUDE.md](CLAUDE.md) for:
- Module breakdown (parser, storage, retriever, MCP server)
- Extraction signal patterns
- Retrieval ranking algorithm
- Extension points (add node types, signals, MCP tools)

---

## What's Next?

- **Integrate with IDE extensions**: VSCode Claude Code extension could show captured context inline
- **Cross-project search**: Query decisions across all projects ("Find all auth decisions")
- **Decision audit trail**: Track why specific decisions were made (GitHub issues, JIRA links)
- **Automated enforcement**: CI checks enforce architectural invariants captured in decisions

---

## Getting Help

- **Slack**: Post in #engineering-excellence
- **GitHub**: Create issue at [codeassist-interceptor issues](https://github.com/anthropics/claude-code/issues)
- **Documentation**: See [src/](src/) module docstrings for technical details
