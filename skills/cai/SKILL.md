---
name: cai
description: Master skill for developing the CodeAssist Interceptor project. Use whenever the user is working on codeassist-interceptor, wants to add features, debug issues, or understand the system. Routes to the right module skill. Trigger on: "codeassist", "cai", "interceptor", "claude session capture", "session memory", "IR extraction", any mention of parsing Claude sessions or MCP context tools.
---

# CodeAssist Interceptor — Master Development Skill

You are helping develop **CodeAssist Interceptor**, a Python system that captures architectural reasoning from Claude Code JSONL sessions and surfaces it as MCP context tools.

## System at a Glance

```
Claude Code JSONL → Parse → Extract IRNode[] → SQLite+Embeddings → MCP Tools → Claude Code
```

**10 modules, ~2100 lines Python.** Stack: Python 3.11+, SQLite, sqlite-vec, sentence-transformers, FastMCP, watchdog, Click, Pydantic, Rich.

## Module Map

| Module | Path | Skill |
|--------|------|-------|
| Data models | `src/models/ir.py` | `/cai-models` |
| JSONL parser | `src/parser/session_parser.py` | `/cai-parser` |
| Rule extractor | `src/parser/extractor.py` | `/cai-parser` |
| LLM extractor | `src/parser/llm_extractor.py` | `/cai-parser` |
| File watcher | `src/parser/watcher.py` | `/cai-watcher` |
| SQLite storage | `src/storage/ir_store.py` | `/cai-storage` |
| Vector embeddings | `src/storage/embeddings.py` | `/cai-storage` |
| Semantic retrieval | `src/retriever/context_retriever.py` | `/cai-retriever` |
| MCP server | `src/mcp/server.py` | `/cai-mcp` |
| CLI | `src/cli.py` | `/cai-cli` |

## How to Route

Read the user's request and direct yourself to the right sub-skill based on which module it touches. You can read multiple module files if the task spans layers (e.g., adding a new NodeType touches models + parser + MCP).

**Common task → module mapping:**
- "Parse sessions / JSONL fails" → `session_parser.py` → use `/cai-parser`
- "Extraction accuracy / patterns / confidence" → `extractor.py` / `llm_extractor.py` → use `/cai-parser`
- "File not being picked up / watcher" → `watcher.py` → use `/cai-watcher`
- "DB error / store not saving / embedding" → `ir_store.py` / `embeddings.py` → use `/cai-storage`
- "Search results bad / retrieval wrong" → `context_retriever.py` → use `/cai-retriever`
- "MCP tool / Claude Code integration / new tool" → `server.py` → use `/cai-mcp`
- "Add NodeType / add signal / extend system" → use `/cai-extend`
- "Test failing / write test" → use `/cai-test`
- "Debugging unclear issue" → use `/cai-debug`

## Key Invariants to Preserve

1. **Zero mandatory external services** — SQLite and local embeddings; Haiku API is always optional
2. **Confidence scoring** — every IRNode must have a float confidence [0.0, 1.0]
3. **Idempotent parsing** — `is_session_parsed()` prevents re-processing; always check
4. **Debounced watching** — never parse while a file is still being written to
5. **Pydantic models** — all data structures use Pydantic for validation
6. **Hybrid retrieval** — never pure keyword or pure semantic; always blend with recency

## Quickstart Commands

```bash
cd codeassist-interceptor
pip install -e ".[dev]"
pytest
codeassist parse /path/to/project
codeassist watch /path/to/project --llm
codeassist serve
```
