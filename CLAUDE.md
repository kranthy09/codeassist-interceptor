# CodeAssist Interceptor — Claude Code Guide

## What This Project Does

**CodeAssist Interceptor** captures architectural reasoning from Claude Code sessions and surfaces it as context in future sessions. It solves ephemeral session memory: every decision, rejected approach, and architectural pattern made with Claude is extracted, stored in SQLite with vector embeddings, and served back via MCP (Model Context Protocol) tools.

```
Claude Code JSONL → Parse → Extract IR → SQLite + Embeddings → MCP Server → Claude Code
```

## Quick Commands

```bash
# Install in development mode
cd codeassist-interceptor && pip install -e ".[dev]"

# Parse existing sessions for a project
codeassist parse /path/to/my-project

# Watch sessions and auto-parse (debounced)
codeassist watch /path/to/my-project --llm

# Start MCP server (called by Claude Code)
codeassist serve

# Browse captured nodes
codeassist inspect /path/to/my-project --type architecture

# Run tests
cd codeassist-interceptor && pytest

# Lint
cd codeassist-interceptor && ruff check src/
```

## Register MCP With Claude Code

Always use the **direct virtualenv binary path** — never shims or `which` output.
Pyenv shims resolve the Python version from `.python-version` in the working directory.
Claude Code spawns MCP servers from a different directory, so the shim fails with `command not found`.

```bash
# Local scope (test in this project only — stored in ~/.claude.json, not committed)
claude mcp add --scope local --transport stdio codeassist-interceptor \
  -- /home/kranthi/.pyenv/versions/cai/bin/codeassist serve

# Project scope (shared via .mcp.json — commit this)
claude mcp add --scope project --transport stdio codeassist-interceptor \
  -- /home/kranthi/.pyenv/versions/cai/bin/codeassist serve

# User scope (all projects on this machine — stored in ~/.claude/settings.json)
claude mcp add --scope user --transport stdio codeassist-interceptor \
  -- /home/kranthi/.pyenv/versions/cai/bin/codeassist serve

# Verify it connected
claude mcp list
```

## Architecture Overview

```
src/
├── models/ir.py              ← IRNode, NodeType, Scope, SessionMeta, ContextRequest/Result
├── parser/
│   ├── session_parser.py     ← Parse Claude Code JSONL → SessionMessage / ParsedSession
│   ├── extractor.py          ← Rule-based extraction → IRNode[] (pattern matching)
│   ├── llm_extractor.py      ← LLM-assisted extraction via Haiku (optional, high accuracy)
│   └── watcher.py            ← DebouncedWatcher: auto-parse on file settle
├── storage/
│   ├── ir_store.py           ← SQLite persistence (sessions + nodes + embeddings tables)
│   └── embeddings.py         ← sentence-transformers all-MiniLM-L6-v2, local, no API
├── retriever/
│   └── context_retriever.py  ← Hybrid semantic+keyword+recency search, blended scoring
├── mcp/
│   └── server.py             ← FastMCP: 4 tools exposed to Claude Code
└── cli.py                    ← Click CLI: parse, serve, inspect, watch
```

## Key Data Model

```python
class IRNode:
    node_type: NodeType     # ARCHITECTURE | IMPLEMENTATION | REJECTION | DEPENDENCY
                            # PATTERN | BUGFIX | REFACTOR | CONVENTION
    scope: Scope            # SYSTEM | MODULE | FILE | FUNCTION
    summary: str            # One-line description
    rationale: str          # Why this decision was made
    alternatives_rejected: list[str]  # What was considered and dropped
    files_affected: list[str]         # Paths touched
    tags: list[str]                   # Tech stack tags
    confidence: float       # 0.0-1.0 (rule-based varies; LLM baseline 0.85)
    session_id: str
    timestamp: datetime
```

## MCP Tools (4)

| Tool | Purpose | When to Call |
|------|---------|-------------|
| `get_project_context(query)` | Semantic search over decisions | Before starting any change |
| `get_project_summary()` | Overview of all captured reasoning | Start of new session |
| `get_decision_history(type, days)` | Filtered decision list | Reviewing patterns |
| `search_decisions(query)` | Keyword search | Finding specific decisions |

## Module Responsibilities

| Module | Responsibility | Key Class/Function |
|--------|---------------|-------------------|
| `models/ir.py` | Data contracts | `IRNode`, `NodeType`, `Scope` |
| `parser/session_parser.py` | JSONL → messages | `parse_jsonl_file()`, `ParsedSession` |
| `parser/extractor.py` | Pattern-based classification | `extract_nodes_from_session()` |
| `parser/llm_extractor.py` | LLM classification (Haiku) | `extract_with_llm()` |
| `parser/watcher.py` | Debounced file watching | `DebouncedWatcher` |
| `storage/ir_store.py` | SQLite CRUD | `IRStorage` |
| `storage/embeddings.py` | Vector encode + search | `EmbeddingManager` |
| `retriever/context_retriever.py` | Hybrid search | `QueryContextRetriever.retrieve()` |
| `mcp/server.py` | MCP tool registration | `create_mcp_server()` |
| `cli.py` | CLI entry points | `parse`, `serve`, `inspect`, `watch` |

## Extraction Pipeline

```
ParsedSession.assistant_turns
  → extractor._classify_node_type(text)   # score against 5 signal pattern sets
  → extractor._infer_scope(msg)            # SYSTEM/MODULE/FILE/FUNCTION
  → extractor._extract_summary(text)       # first meaningful sentence
  → extractor._extract_tags(text, files)   # py/ts/react/fastapi/etc
  → IRNode (if confidence >= threshold)
  → [optional] llm_extractor for low-confidence turns (batch of 5 to Haiku)
```

## Retrieval Pipeline

```
ContextRequest(query, project_path, files_in_scope, max_results, recency_weight)
  → keyword search (non-stopword terms from query)
  → semantic search (encode query → cosine sim vs stored embeddings)
  → recency score (exponential decay, half-life=7 days)
  → blended = semantic * (1-recency_weight) + recency * recency_weight
  → boost ARCHITECTURE/PATTERN types
  → rank and return top-k ContextResult
```

## Storage Layout

```
~/.codeassist/ir.db
├── sessions     (session_id PK, project_path, timestamps, nodes_extracted)
├── nodes        (id PK, session_id FK, node_type, scope, summary, rationale, ...)
└── node_embeddings (node_id PK, embedding BLOB, model_name)
```

## Extension Points

### Add a new NodeType
1. Add to `NodeType` enum in `src/models/ir.py`
2. Add extraction signals to `extractor._ARCHITECTURE_SIGNALS` (or new dict)
3. Update `_classify_node_type()` scoring
4. Add to LLM prompt in `llm_extractor.py` system message
5. Update CLI help strings and MCP tool descriptions

### Add an extraction signal pattern
1. Edit the relevant signal dict in `src/parser/extractor.py`
2. Add `re.compile(r"...", re.I)` to the appropriate list
3. Adjust weight in `_classify_node_type()` scoring loop

### Add a new MCP tool
1. Add `@mcp.tool()` decorated function in `src/mcp/server.py`
2. Use `_get_components()` for lazy storage/retriever access
3. Return formatted markdown string

### Add retrieval ranking factor
1. Edit `context_retriever._blend_scores()` or `retrieve()`
2. Add new score component and blend weight

## Testing

```bash
# All tests
pytest

# Specific module
pytest tests/test_parser.py -v
pytest tests/test_storage.py -v
pytest tests/test_retriever.py -v

# With coverage
pytest --cov=src --cov-report=html

# Single test
pytest tests/test_extractor.py::TestExtractor::test_architecture_classification -v
```

## Development Workflow

1. **New feature** → read relevant module, write tests first, implement
2. **Debug extraction** → `codeassist inspect --json | python -m json.tool`
3. **Debug MCP** → `codeassist serve` in terminal, send JSON-RPC manually
4. **Debug watch** → `codeassist watch --debounce 1.0` with verbose flag
5. **Check DB** → `sqlite3 ~/.codeassist/ir.db ".tables"` then `.schema nodes`

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | LLM extraction via Haiku (optional) |
| `CODEASSIST_DB_PATH` | Override default `~/.codeassist/ir.db` |
| `CODEASSIST_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` |

## Skills Available

Project-specific skills live in `skills/` at the repo root and `~/.claude/skills/cai-*/`.

| Skill | Trigger | Purpose |
|-------|---------|---------|
| `/cai` | "work on codeassist" | Master router to all modules |
| `/cai-parser` | "parser", "extraction", "JSONL" | Parser module development |
| `/cai-watcher` | "watcher", "auto-parse", "debounce" | Watcher module development |
| `/cai-storage` | "storage", "SQLite", "embeddings" | Storage module development |
| `/cai-retriever` | "retrieval", "search", "context" | Retriever module development |
| `/cai-mcp` | "MCP tool", "MCP server" | MCP server development |
| `/cai-models` | "IRNode", "NodeType", "models" | Data model development |
| `/cai-extend` | "add node type", "new signal", "new tool" | System extension patterns |
| `/cai-test` | "write test", "test coverage" | Test patterns |
| `/cai-debug` | "debug", "not extracting", "wrong" | Debug common issues |
