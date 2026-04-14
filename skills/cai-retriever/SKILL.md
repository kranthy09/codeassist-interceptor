---
name: cai-retriever
description: Skill for working with the CodeAssist Interceptor retrieval layer (src/retriever/context_retriever.py). Use when retrieval results are wrong or incomplete, when tuning semantic/recency/keyword blending, adding new ranking signals, improving context formatting, or extending the retrieval API. Trigger on: "retriever", "retrieval", "search results", "context_retriever", "wrong results", "ranking", "recency weight", "semantic search", "hybrid search", "get_context_summary".
---

# CAI Retriever Module Skill

Working with `src/retriever/context_retriever.py` — the hybrid search and ranking engine.

## Architecture

```
ContextRequest
  ├── keyword candidates (non-stopword query terms → SQL LIKE search)
  ├── recent candidates (last 30 days from storage)
  └── file-scoped candidates (if files_in_scope provided)
       ↓ (union of all candidates, deduplicated by id)
  get embeddings for all candidates
  compute cosine similarity(query_embedding, node_embedding)
       ↓
  blend scores:
    blended = semantic_score * (1 - recency_weight)
            + recency_score  * recency_weight
    + confidence_boost * node.confidence * 0.1
    + type_boost (ARCHITECTURE +0.15, PATTERN +0.1)
       ↓
  rank descending, return top max_results
       ↓
ContextResult(nodes, total_available, query_time_ms, embedding_time_ms)
```

## Key Classes

```python
class QueryContextRetriever:
    def __init__(self, store: IRStorage, embeddings: EmbeddingManager): ...
    
    def retrieve(self, request: ContextRequest) -> ContextResult
    def get_context_summary(
        self, project_path: str, max_tokens: int = 2000
    ) -> str  # formatted markdown for CLAUDE.md injection
```

## Retrieval Method in Detail

```python
def retrieve(self, request: ContextRequest) -> ContextResult:
    # 1. Gather candidates
    keywords = _extract_keywords(request.query)
    keyword_nodes = store.search_keyword(project_path, keywords, limit=30)
    recent_nodes = store.query_nodes(project_path, limit=50, since=30_days_ago)
    file_nodes = []
    if request.files_in_scope:
        for file in request.files_in_scope:
            file_nodes += store.query_nodes_by_file(file, limit=10)
    
    candidates = deduplicate([*keyword_nodes, *recent_nodes, *file_nodes])
    
    # 2. Semantic scoring
    node_ids = [n.id for n in candidates]
    similarities = embeddings.search_similar(request.query, node_ids, top_k=len(candidates))
    sim_map = dict(similarities)
    
    # 3. Blend and rank
    scored = []
    for node in candidates:
        semantic = sim_map.get(node.id, 0.0)
        recency = _recency_score(node.timestamp)
        blended = (semantic * (1 - request.recency_weight)
                 + recency * request.recency_weight
                 + node.confidence * 0.1)
        # Type boosts
        if node.node_type == NodeType.ARCHITECTURE: blended += 0.15
        if node.node_type == NodeType.PATTERN: blended += 0.10
        scored.append((blended, node))
    
    ranked = [n for _, n in sorted(scored, reverse=True)]
    return ContextResult(nodes=ranked[:request.max_results], ...)
```

## Recency Score

```python
def _recency_score(timestamp: datetime, half_life_days: float = 7.0) -> float:
    age_days = (datetime.now() - timestamp).total_seconds() / 86400
    return 2 ** (-age_days / half_life_days)
    # Score: 1.0 now, ~0.5 after 7 days, ~0.25 after 14 days, ~0.03 after 30 days
```

## Tuning Retrieval

### Change recency decay curve

```python
# Faster decay (emphasizes very recent):
half_life_days = 3.0

# Slower decay (older decisions stay relevant):
half_life_days = 14.0
```

### Change type boosts

```python
# In _blend_scores() or retrieve():
TYPE_BOOSTS = {
    NodeType.ARCHITECTURE: 0.20,  # was 0.15
    NodeType.PATTERN: 0.15,       # was 0.10
    NodeType.REJECTION: 0.10,     # ADD: rejections are important context
}
for node_type, boost in TYPE_BOOSTS.items():
    if node.node_type == node_type:
        blended += boost
```

### Add a file-proximity boost

```python
# Boost nodes that share files with the current query context
if request.files_in_scope:
    overlap = set(node.files_affected) & set(request.files_in_scope)
    if overlap:
        blended += 0.1 * len(overlap)
```

### Change candidate gathering strategy

```python
# Increase candidate pool (more nodes to rank, higher quality but slower):
recent_nodes = store.query_nodes(project_path, limit=100, since=60_days_ago)

# Filter candidates to specific node types upfront:
if request.node_types:
    keyword_nodes = store.query_nodes(
        project_path, node_types=request.node_types, limit=30
    )
```

## Keyword Extraction

```python
_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
              "have", "has", "had", "do", "does", "did", "will", "would",
              "could", "should", "may", "might", "shall", "can", "need",
              "i", "we", "you", "they", "it", "this", "that", "what",
              "how", "why", "when", "where", "which", "who"}

def _extract_keywords(query: str) -> list[str]:
    words = re.findall(r'\b[a-zA-Z]\w+\b', query.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]
```

To extend: add domain stopwords (`make`, `run`, `use`, `add`) or add phrase extraction.

## Context Summary Format

`get_context_summary()` produces markdown for CLAUDE.md injection:

```markdown
## Project Context (from CodeAssist)

### Key Architectural Decisions
- [ARCHITECTURE] Used SQLite + sqlite-vec for zero-infrastructure vector search
  *Files: src/storage/ir_store.py* | Confidence: 0.9

### Patterns & Conventions
- [PATTERN] All data structures use Pydantic BaseModel
  *Files: src/models/ir.py* | Confidence: 0.8

### Rejected Approaches
- [REJECTION] Rejected PostgreSQL — requires external service
  *Rationale: zero-infrastructure requirement*

### Recent Decisions (last 7 days)
- [IMPLEMENTATION] Added debounce logic to watcher
```

## Testing Retrieval

```bash
pytest tests/test_retriever.py -v

# Quick manual test:
python -c "
from src.storage.ir_store import IRStorage
from src.storage.embeddings import EmbeddingManager
from src.retriever.context_retriever import QueryContextRetriever
from src.models.ir import ContextRequest

store = IRStorage()
emb = EmbeddingManager(store)
retriever = QueryContextRetriever(store, emb)

result = retriever.retrieve(ContextRequest(
    query='how is session parsing implemented',
    project_path='/path/to/project',
    max_results=5,
))
for node in result.nodes:
    print(f'[{node.node_type}] {node.summary} (conf={node.confidence:.2f})')
"
```

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| Empty results | No nodes for project_path | Run `codeassist parse` first |
| Wrong results | recency_weight too high | Lower recency_weight (try 0.1) |
| Old nodes dominating | Stale embeddings | Re-run embeddings after schema change |
| Slow retrieval | Too many candidates | Reduce `limit` in query_nodes calls |
| Missing file-scoped results | query_nodes_by_file missing | Add the method to IRStorage |
