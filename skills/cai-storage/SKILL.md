---
name: cai-storage
description: Skill for working with the CodeAssist Interceptor storage layer (src/storage/ir_store.py and src/storage/embeddings.py). Use when debugging database issues, adding new query methods, changing the schema, optimizing queries, working with vector embeddings, or extending the storage API. Trigger on: "storage", "SQLite", "database", "ir_store", "embeddings", "EmbeddingManager", "IRStorage", "sqlite-vec", "vector search", "schema change".
---

# CAI Storage Module Skill

Two files: `src/storage/ir_store.py` (SQLite persistence) and `src/storage/embeddings.py` (vector search).

## Database Location

```
~/.codeassist/ir.db   (default)
$CODEASSIST_DB_PATH   (override via env var)
```

## Schema

```sql
-- Sessions table
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    model_used TEXT DEFAULT '',
    total_turns INTEGER DEFAULT 0,
    nodes_extracted INTEGER DEFAULT 0,
    parsed_at TEXT NOT NULL
);

-- Nodes table (main content store)
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(session_id),
    project_path TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    node_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    summary TEXT NOT NULL,
    rationale TEXT DEFAULT '',
    alternatives TEXT DEFAULT '[]',   -- JSON array
    files_affected TEXT DEFAULT '[]', -- JSON array
    tags TEXT DEFAULT '[]',           -- JSON array
    parent_node_id TEXT,
    confidence REAL DEFAULT 0.5,
    raw_source TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_nodes_project ON nodes(project_path);
CREATE INDEX idx_nodes_type ON nodes(node_type);
CREATE INDEX idx_nodes_time ON nodes(timestamp DESC);
CREATE INDEX idx_nodes_session ON nodes(session_id);

-- Vector index (sqlite-vec extension)
CREATE VIRTUAL TABLE node_embeddings USING vec0(
    node_id TEXT PRIMARY KEY,
    embedding FLOAT[384],  -- all-MiniLM-L6-v2 dimensions
    model_name TEXT
);
```

## IRStorage API

```python
class IRStorage:
    def __init__(self, db_path: str | None = None): ...
    
    # Session operations
    def upsert_session(self, meta: SessionMeta) -> None
    def is_session_parsed(self, session_id: str) -> bool  # idempotency check
    
    # Node operations
    def store_nodes(self, nodes: list[IRNode]) -> int  # returns count stored
    def get_node(self, node_id: str) -> IRNode | None
    def delete_nodes_for_session(self, session_id: str) -> int
    
    # Query operations
    def query_nodes(
        self,
        project_path: str,
        node_types: list[NodeType] | None = None,
        limit: int = 50,
        since: datetime | None = None,
    ) -> list[IRNode]
    
    def search_keyword(
        self,
        project_path: str,
        keywords: list[str],
        limit: int = 20,
    ) -> list[IRNode]  # LIKE-based search across summary + rationale
    
    def get_project_stats(self, project_path: str) -> dict
    # Returns: {total_nodes, total_sessions, first_seen, last_seen,
    #           nodes_by_type: {type: count}, top_files: [...]}
    
    def get_nodes_with_embeddings(
        self,
        node_ids: list[str],
    ) -> list[IRNode]  # includes embedding field
```

## EmbeddingManager API

```python
class EmbeddingManager:
    def __init__(self, store: IRStorage): ...
    
    def encode_text(self, text: str) -> list[float]  # 384-dim vector
    def encode_and_store(self, nodes: list[IRNode]) -> int  # encode + persist
    
    def search_similar(
        self,
        query: str,
        node_ids: list[str],   # candidate set (pre-filter)
        top_k: int = 20,
    ) -> list[tuple[str, float]]  # [(node_id, similarity_score)]
```

### Embedding Text Construction

The text used for embedding = `summary + " " + rationale + " " + tags_joined + " " + files_joined`

To change what gets embedded:
```python
# In embeddings.py, the encode_text call site:
text = f"{node.summary} {node.rationale} {' '.join(node.tags)} {' '.join(node.files_affected)}"
embedding = self.encode_text(text)
```

## Adding a New Query Method

Pattern for adding a query to `ir_store.py`:

```python
def query_by_file(
    self,
    project_path: str,
    file_path: str,
    limit: int = 20,
) -> list[IRNode]:
    """Get nodes where file_path appears in files_affected."""
    with self._connect() as conn:
        rows = conn.execute("""
            SELECT * FROM nodes
            WHERE project_path = ?
              AND files_affected LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (project_path, f'%{file_path}%', limit)).fetchall()
    return [self._row_to_node(row) for row in rows]
```

Note: `files_affected` is stored as JSON — use LIKE for substring search or `json_each()` for proper array search:

```sql
SELECT * FROM nodes, json_each(nodes.files_affected)
WHERE json_each.value = ?
```

## Schema Migration

There's no migration framework. When changing the schema:

```python
# In _create_tables():
conn.execute("""
    CREATE TABLE IF NOT EXISTS nodes (
        -- existing columns ...
        new_column TEXT DEFAULT ''  -- ← ADD with DEFAULT
    )
""")

# For existing DBs, add ALTER TABLE in a migration:
def _migrate(self, conn):
    try:
        conn.execute("ALTER TABLE nodes ADD COLUMN new_column TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # column already exists
```

Call `_migrate()` from `__init__()` after `_create_tables()`.

## SQLite Performance Notes

- WAL mode is enabled: `PRAGMA journal_mode=WAL` — supports concurrent reads
- Foreign keys enforced: `PRAGMA foreign_keys=ON`
- For batch inserts, use `executemany()` — already done in `store_nodes()`
- Index on `(project_path, timestamp DESC)` — all queries filter by project first

## Debugging Storage

```bash
# Inspect the database directly
sqlite3 ~/.codeassist/ir.db

# Useful queries
.tables
.schema nodes
SELECT node_type, count(*) FROM nodes GROUP BY node_type;
SELECT * FROM nodes WHERE project_path LIKE '%myproject%' LIMIT 5;
SELECT count(*) FROM node_embeddings;

# Check if sqlite-vec is loaded
python -c "import sqlite_vec; print(sqlite_vec.loadable_path())"
```

## Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| `sqlite_vec not found` | Extension not installed | `pip install sqlite-vec` |
| Foreign key violation | Storing node before session | Always `upsert_session()` before `store_nodes()` |
| Duplicate nodes | Re-parsing already-parsed session | Check `is_session_parsed()` first |
| Embedding model slow | First load (~80MB model) | It's one-time; subsequent calls are fast |
| Wrong nodes returned | project_path encoding mismatch | Normalize path with `os.path.realpath()` |

## Testing Storage

```bash
pytest tests/test_storage.py -v

# Tests use a temp SQLite file (not ~/.codeassist/ir.db)
# Key test cases:
# - test_store_and_retrieve_nodes
# - test_keyword_search
# - test_project_stats
# - test_session_idempotency
```
