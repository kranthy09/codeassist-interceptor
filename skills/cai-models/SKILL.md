---
name: cai-models
description: Skill for working with CodeAssist Interceptor data models (src/models/ir.py). Use when adding new NodeType enum values, modifying IRNode fields, updating Scope levels, changing ContextRequest/Result contracts, or when any change touches the core data structures that flow through the entire pipeline. Trigger on: "IRNode", "NodeType", "add node type", "Scope", "ContextRequest", "data model", "ir.py".
---

# CAI Models Module Skill

Working with `src/models/ir.py` — the single source of truth for all data structures.

## File: `src/models/ir.py` (~100 lines)

**Key classes:**
```python
class NodeType(str, Enum):
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    REJECTION = "rejection"
    DEPENDENCY = "dependency"
    PATTERN = "pattern"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    CONVENTION = "convention"

class Scope(str, Enum):
    SYSTEM = "system"
    MODULE = "module"
    FILE = "file"
    FUNCTION = "function"

class IRNode(BaseModel):
    id: str                         # UUID
    session_id: str
    project_path: str
    timestamp: datetime
    node_type: NodeType
    scope: Scope
    summary: str
    rationale: str = ""
    alternatives_rejected: list[str] = []
    files_affected: list[str] = []
    tags: list[str] = []
    parent_node_id: str | None = None
    confidence: float = 0.5         # [0.0, 1.0]
    raw_source: str = ""
    embedding: list[float] | None = None  # set by EmbeddingManager

class SessionMeta(BaseModel):
    session_id: str
    project_path: str
    started_at: datetime
    ended_at: datetime | None = None
    model_used: str = ""
    total_turns: int = 0
    nodes_extracted: int = 0

class ContextRequest(BaseModel):
    query: str
    project_path: str
    files_in_scope: list[str] = []
    max_results: int = 10
    recency_weight: float = 0.3     # 0.0=pure semantic, 1.0=pure recency
    node_types: list[NodeType] = [] # empty = all types

class ContextResult(BaseModel):
    nodes: list[IRNode]
    total_available: int
    query_time_ms: float
    embedding_time_ms: float = 0.0
```

## Adding a New NodeType

This change ripples through the entire pipeline. Do all steps:

### Step 1 — Add enum value
```python
# src/models/ir.py
class NodeType(str, Enum):
    # ... existing ...
    MY_NEW_TYPE = "my_new_type"
```

### Step 2 — Add extraction signals
```python
# src/parser/extractor.py
_MY_NEW_TYPE_SIGNALS: list[re.Pattern] = [
    re.compile(r"pattern one", re.I),
    re.compile(r"pattern two", re.I),
]

# In _classify_node_type():
scores[NodeType.MY_NEW_TYPE] = sum(
    1.0 for p in _MY_NEW_TYPE_SIGNALS if p.search(text)
)
```

### Step 3 — Update LLM extractor prompt
```python
# src/parser/llm_extractor.py — find the system prompt string
# Add "MY_NEW_TYPE: description of when to use it" to the node_type list
```

### Step 4 — Update MCP tool descriptions
```python
# src/mcp/server.py — update get_decision_history docstring
# Add "my_new_type" to the decision_type parameter description
```

### Step 5 — Update CLI help
```python
# src/cli.py — update --type option help string
```

### Step 6 — Write tests
```python
# tests/test_extractor.py
def test_my_new_type_classification():
    session = make_session_with_text("text that triggers my new type")
    nodes = extract_nodes_from_session(session)
    assert any(n.node_type == NodeType.MY_NEW_TYPE for n in nodes)
```

## Adding a Field to IRNode

Check all these locations after adding a field:

1. `src/storage/ir_store.py` — `_create_tables()` SQL schema + `store_nodes()` INSERT + `_row_to_node()` reconstruction
2. `src/storage/embeddings.py` — if the field contributes to embedding text, update `encode_text()`
3. `src/retriever/context_retriever.py` — if the field should affect ranking, update `_blend_scores()`
4. `tests/fixtures.py` — update fixture IRNodes

## Modifying Scope

Scope affects `extractor._infer_scope()`. If you add a new Scope:
1. Add enum value here
2. Update `_infer_scope()` heuristics in `extractor.py`
3. Update storage schema if needed

## Invariants

- `confidence` MUST be in [0.0, 1.0]
- `id` MUST be UUID string, generated with `str(uuid.uuid4())`
- `embedding` is set post-construction by `EmbeddingManager`; it's None in the model itself
- `node_type` and `scope` use string enums — they serialize to their string values in JSON/DB
