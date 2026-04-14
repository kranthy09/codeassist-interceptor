---
name: cai-extend
description: Skill for extending the CodeAssist Interceptor system with new capabilities. Use when adding a new NodeType, adding extraction signal patterns, adding a new MCP tool, extending the retrieval pipeline, adding new CLI commands, or making cross-cutting system-wide changes. Trigger on: "add node type", "new signal", "new extraction pattern", "extend the system", "add MCP tool", "add command", "new feature", "cross-cutting change".
---

# CAI Extension Patterns Skill

This skill covers the cross-cutting patterns for extending the system. Each extension type has a specific checklist to ensure all layers are updated consistently.

## Extension Type 1: Add a New NodeType

A new `NodeType` enum value ripples through 5 files.

### Checklist

- [ ] `src/models/ir.py` — add enum value
- [ ] `src/parser/extractor.py` — add signal patterns + update `_classify_node_type()`
- [ ] `src/parser/llm_extractor.py` — update system prompt
- [ ] `src/mcp/server.py` — update `get_decision_history` docstring
- [ ] `src/cli.py` — update `--type` option help

### Example: Adding `DECISION` node type

```python
# 1. src/models/ir.py
class NodeType(str, Enum):
    # ... existing ...
    DECISION = "decision"  # explicit trade-off decision with alternatives

# 2. src/parser/extractor.py
_DECISION_SIGNALS: list[re.Pattern] = [
    re.compile(r"\bdecided (to|against|that)\b", re.I),
    re.compile(r"\btrade.?off\b", re.I),
    re.compile(r"\bweigh(ed|ing) (the )?option", re.I),
    re.compile(r"\bchose .+ over\b", re.I),
]

def _classify_node_type(text: str) -> tuple[NodeType, float]:
    scores: dict[NodeType, float] = defaultdict(float)
    # ... existing scoring ...
    for p in _DECISION_SIGNALS:
        if p.search(text):
            scores[NodeType.DECISION] += 1.0
    # ... rest of function ...

# 3. src/parser/llm_extractor.py — find system prompt, add:
# "- decision: An explicit trade-off decision where alternatives were weighed"

# 4. src/mcp/server.py — update docstring of get_decision_history:
# decision_type: "architecture|implementation|rejection|dependency|pattern|bugfix|refactor|convention|decision"

# 5. src/cli.py — update --type help:
# type="choice", choices=[..., "decision"]
```

---

## Extension Type 2: Add Extraction Signal Patterns

Only touches `extractor.py`. No ripple effects.

```python
# Find the relevant signal list in src/parser/extractor.py
# Add your pattern with re.compile(r"...", re.I)

# Architecture signals (system-level design choices):
_ARCHITECTURE_SIGNALS.extend([
    re.compile(r"\bdesigned (the|this) (system|architecture)\b", re.I),
    re.compile(r"\bseparated concerns\b", re.I),
])

# Rejection signals (explicitly dropped approaches):
_REJECTION_SIGNALS.extend([
    re.compile(r"\bstayed away from\b", re.I),
    re.compile(r"\bnot (going|using) .+ because\b", re.I),
])
```

**Testing:** After adding patterns, run:
```bash
python -c "
from src.parser.extractor import _classify_node_type
text = 'your test sentence with the new pattern'
node_type, confidence = _classify_node_type(text)
print(f'{node_type} (conf={confidence:.2f})')
"
```

---

## Extension Type 3: Add a New MCP Tool

Only touches `src/mcp/server.py`. See `/cai-mcp` for full pattern.

```python
@mcp.tool()
async def get_files_context(
    files: list[str],
    project_path: str = "",
    limit: int = 10,
) -> str:
    """
    Get all decisions related to specific files.
    Call when starting work on a file to see its decision history.
    
    Args:
        files: List of file paths to look up
        project_path: Project root (uses cwd if empty)
        limit: Max results per file
    """
    store, embeddings, retriever = _get_components()
    project_path = project_path or os.getcwd()
    
    results = []
    for file_path in files[:5]:  # cap to avoid huge responses
        nodes = store.query_nodes(project_path, limit=limit)
        file_nodes = [n for n in nodes if file_path in n.files_affected]
        if file_nodes:
            results.append(f"## {file_path}\n")
            for n in file_nodes[:3]:
                results.append(f"- [{n.node_type.upper()}] {n.summary}")
    
    return "\n".join(results) if results else "No decisions found for these files."
```

---

## Extension Type 4: Add a CLI Command

Add to `src/cli.py` using Click:

```python
@main.command()
@click.argument("project_path", default=".", type=click.Path())
@click.option("--format", type=click.Choice(["markdown", "json"]), default="markdown")
@click.pass_context
def export(ctx: click.Context, project_path: str, format: str) -> None:
    """Export all captured decisions to a file."""
    from src.storage.ir_store import IRStorage
    from src.retriever.context_retriever import QueryContextRetriever
    
    store = IRStorage()
    nodes = store.query_nodes(os.path.realpath(project_path), limit=1000)
    
    if format == "json":
        import json
        console.print(json.dumps([n.model_dump() for n in nodes], indent=2, default=str))
    else:
        for node in nodes:
            console.print(f"## [{node.node_type.upper()}] {node.summary}")
```

---

## Extension Type 5: Add a New Retrieval Signal

Extend `context_retriever.py` to blend in a new ranking signal:

```python
# Example: boost nodes that are tagged with the same tech stack as the query

def _tag_overlap_score(node: IRNode, query: str) -> float:
    """Score based on tag overlap with query terms."""
    query_lower = query.lower()
    matched = sum(1 for tag in node.tags if tag in query_lower)
    return min(matched * 0.1, 0.3)  # cap contribution at 0.3

# In retrieve(), add to blended score:
tag_boost = _tag_overlap_score(node, request.query)
blended += tag_boost
```

---

## Extension Type 6: Add a New Scope Level

Like NodeType, Scope ripples through the system:

- [ ] `src/models/ir.py` — add enum value
- [ ] `src/parser/extractor.py` — update `_infer_scope()` heuristics
- [ ] `src/storage/ir_store.py` — schema already handles it (TEXT column)

---

## Cross-Cutting: Changing the Embedding Model

The model (`all-MiniLM-L6-v2`) is set in `src/storage/embeddings.py`. To swap:

```python
# In EmbeddingManager.__init__():
MODEL_NAME = "all-mpnet-base-v2"  # larger, better quality (768 dims)
# OR
MODEL_NAME = "paraphrase-MiniLM-L3-v2"  # smaller, faster (384 dims)

# IMPORTANT: If you change the model, the vector dimensions change.
# You must:
# 1. Delete ~/.codeassist/ir.db (or the node_embeddings table)
# 2. Re-run codeassist parse to regenerate all embeddings
# 3. Update the embedding dimension in the CREATE VIRTUAL TABLE statement
```

---

## Testing Extensions

For any extension, write tests in the appropriate test file:

```bash
# Models
tests/test_parser.py        # for extraction changes
tests/test_extractor.py     # for signal pattern changes
tests/test_storage.py       # for storage/schema changes
tests/test_retriever.py     # for retrieval changes

# Run with verbose output
pytest tests/test_extractor.py -v -k "test_my_new_feature"
```

## Architecture Principles for Extensions

1. **Don't break idempotency** — always check `is_session_parsed()` before processing
2. **Keep extraction fast** — rule-based extraction should be <50ms per session
3. **Confidence is required** — every new extraction path must set a meaningful confidence float
4. **Preserve zero-infrastructure** — don't add required external services
5. **Test at the module boundary** — test the public function, not private helpers
