---
name: cai-mcp
description: Skill for working with the CodeAssist Interceptor MCP server (src/mcp/server.py). Use when adding new MCP tools, modifying existing tool behavior, debugging MCP integration with Claude Code, changing tool parameter schemas, or updating response formatting. Trigger on: "MCP tool", "MCP server", "server.py", "FastMCP", "new tool", "Claude Code integration", "mcp add", "get_project_context", "get_decision_history", "get_project_summary", "search_decisions".
---

# CAI MCP Server Module Skill

Working with `src/mcp/server.py` — 4 MCP tools exposed to Claude Code via FastMCP.

## Registration

Always use the **direct virtualenv binary path**, not the pyenv shim.
The shim resolves Python version from `.python-version` in the working directory —
Claude Code spawns MCP servers from a different directory, so shims fail with `command not found`.

```bash
# Local scope (this project only, not committed):
claude mcp add --scope local --transport stdio codeassist-interceptor \
  -- /home/kranthi/.pyenv/versions/cai/bin/codeassist serve

# Project scope (shared via .mcp.json, commit this):
claude mcp add --scope project --transport stdio codeassist-interceptor \
  -- /home/kranthi/.pyenv/versions/cai/bin/codeassist serve

# User scope (all projects):
claude mcp add --scope user --transport stdio codeassist-interceptor \
  -- /home/kranthi/.pyenv/versions/cai/bin/codeassist serve

# Verify connected:
claude mcp list
```

## Current Tools (4)

### 1. `get_project_context`
```python
@mcp.tool()
async def get_project_context(
    query: str,
    project_path: str = "",
    max_results: int = 10,
    recency_weight: float = 0.3,
) -> str:
    """
    Retrieve relevant architectural decisions for a query.
    Call BEFORE reading files to avoid redundant codebase re-reads.
    Returns formatted markdown with matched decisions.
    """
```

### 2. `get_decision_history`
```python
@mcp.tool()
async def get_decision_history(
    project_path: str = "",
    decision_type: str = "",  # architecture|implementation|rejection|etc
    days: int = 14,
    limit: int = 15,
) -> str:
    """
    List recent decisions filtered by type and recency.
    """
```

### 3. `get_project_summary`
```python
@mcp.tool()
async def get_project_summary(
    project_path: str = "",
) -> str:
    """
    Concise overview of all captured reasoning.
    Call at start of new session to orient yourself.
    """
```

### 4. `search_decisions`
```python
@mcp.tool()
async def search_decisions(
    query: str,
    project_path: str = "",
    limit: int = 10,
) -> str:
    """
    Keyword search across all captured IR nodes.
    """
```

## Adding a New MCP Tool

Follow this template exactly:

```python
@mcp.tool()
async def get_related_decisions(
    node_id: str,
    project_path: str = "",
    limit: int = 5,
) -> str:
    """
    Find decisions related to a specific node (by parent chain and shared files).
    
    Args:
        node_id: The IRNode id to find relatives for
        project_path: Project root path (uses cwd if empty)
        limit: Maximum results to return
    """
    store, embeddings, retriever = _get_components()
    project_path = project_path or os.getcwd()
    
    # Implementation
    node = store.get_node(node_id)
    if not node:
        return f"Node {node_id} not found."
    
    related = store.query_nodes(
        project_path,
        limit=limit * 2,
    )
    # Filter for shared files
    relevant = [
        n for n in related
        if set(n.files_affected) & set(node.files_affected)
        and n.id != node_id
    ][:limit]
    
    if not relevant:
        return "No related decisions found."
    
    lines = [f"# Related to: {node.summary}\n"]
    for n in relevant:
        lines.append(f"**[{n.node_type.upper()}]** {n.summary}")
        if n.rationale:
            lines.append(f"  *{n.rationale[:100]}*")
    
    return "\n".join(lines)
```

## Lazy Component Initialization

```python
_components: tuple | None = None

def _get_components() -> tuple[IRStorage, EmbeddingManager, QueryContextRetriever]:
    global _components
    if _components is None:
        store = IRStorage()
        embeddings = EmbeddingManager(store)
        retriever = QueryContextRetriever(store, embeddings)
        _components = (store, embeddings, retriever)
    return _components
```

This pattern:
- Avoids loading ~80MB embedding model at server startup
- Loads only when first tool is called
- Shared across all tool calls (singleton)

## Response Formatting

Tools return **markdown strings**. Claude Code renders these in its UI. Conventions:

```python
# Good: structured, scannable
lines = [
    f"## Results for: {query}\n",
    f"Found {len(nodes)} decisions\n",
]
for node in nodes:
    lines.append(f"### [{node.node_type.upper()}] {node.summary}")
    lines.append(f"**Confidence:** {node.confidence:.0%} | **Scope:** {node.scope}")
    if node.rationale:
        lines.append(f"\n{node.rationale}")
    if node.files_affected:
        lines.append(f"\n*Files:* {', '.join(node.files_affected[:3])}")
    if node.alternatives_rejected:
        lines.append(f"\n*Rejected:* {'; '.join(node.alternatives_rejected[:2])}")
    lines.append("")

return "\n".join(lines)
```

## Error Handling in Tools

```python
@mcp.tool()
async def my_tool(query: str, project_path: str = "") -> str:
    try:
        store, embeddings, retriever = _get_components()
        # ... implementation ...
    except Exception as e:
        logger.error(f"Tool error: {e}", exc_info=True)
        return f"Error retrieving context: {str(e)}"
```

Always return a string even on error — Claude Code expects string responses from tools.

## Debugging MCP Integration

```bash
# Start server manually and send test JSON-RPC:
codeassist serve &
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_project_summary","arguments":{"project_path":"/path/to/project"}},"id":1}' | nc -U /tmp/mcp.sock

# Check Claude Code can see the tools:
claude mcp list
# Should show: codeassist-interceptor with 4 tools

# Test a specific tool from Python:
python -c "
import asyncio
from src.mcp.server import create_mcp_server
# FastMCP has a test client you can use
"

# Check server logs:
codeassist serve -v 2>&1 | tee /tmp/mcp-server.log
```

## FastMCP Patterns

```python
from fastmcp import FastMCP

mcp = FastMCP("codeassist-interceptor")

@mcp.tool()
async def my_tool(param: str) -> str:
    """Docstring becomes the tool description in Claude Code."""
    return "result"

def run_server():
    mcp.run()  # stdio transport (Claude Code default)
```

- Tool name = function name (underscores → hyphens in some clients)
- Parameters become tool input schema automatically from type hints
- Docstring = tool description shown to Claude
- Return type must be `str`

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| Tool not showing in Claude Code | Registration not done | `claude mcp add ...` |
| `ImportError` on serve | Missing dep | `pip install -e .` |
| Empty responses | No nodes in DB | Run `codeassist parse` first |
| Slow first call | Embedding model loading | Expected — first call loads ~80MB |
| `project_path` wrong | Tool gets wrong cwd | Pass explicit path from Claude Code |
