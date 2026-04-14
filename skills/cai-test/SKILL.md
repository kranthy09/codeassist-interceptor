---
name: cai-test
description: Skill for writing and running tests for CodeAssist Interceptor. Use when adding new tests, understanding the test structure, running the test suite, or debugging failing tests. Trigger on: "write test", "test coverage", "failing test", "pytest", "test_parser", "test_storage", "test_extractor", "conftest", "fixtures".
---

# CAI Testing Skill

Working with the test suite in `tests/` for the `codeassist-interceptor` package.

## Running Tests

```bash
cd codeassist-interceptor

# All tests
pytest

# Specific module
pytest tests/test_parser.py -v
pytest tests/test_extractor.py -v
pytest tests/test_storage.py -v
pytest tests/test_retriever.py -v
pytest tests/test_watcher.py -v

# With coverage
pytest --cov=src --cov-report=html
open htmlcov/index.html

# Single test by name
pytest tests/test_extractor.py -k "test_architecture" -v

# Stop on first failure
pytest -x

# Show local variables on failure
pytest -l
```

## Test Structure

```
tests/
├── conftest.py     ← shared fixtures (temp db, paths)
├── fixtures.py     ← realistic JSONL session examples
├── test_parser.py  ← session_parser.py tests
├── test_extractor.py ← extractor.py tests
├── test_storage.py  ← ir_store.py + embeddings.py tests
├── test_retriever.py ← context_retriever.py tests
└── test_watcher.py  ← watcher.py tests
```

## Key Fixtures (conftest.py)

```python
@pytest.fixture
def temp_db(tmp_path):
    """Isolated SQLite DB for each test."""
    db_path = tmp_path / "test_ir.db"
    store = IRStorage(db_path=str(db_path))
    yield store

@pytest.fixture
def sample_session():
    """A minimal ParsedSession for testing."""
    return ParsedSession(
        session_id="test-session-001",
        project_path="/tmp/test-project",
        messages=[...],
        started_at=datetime.now(),
        ended_at=datetime.now(),
    )

@pytest.fixture
def populated_store(temp_db):
    """DB with pre-inserted nodes across types."""
    nodes = [
        IRNode(
            id=str(uuid.uuid4()),
            session_id="s1",
            project_path="/tmp/test-project",
            timestamp=datetime.now(),
            node_type=NodeType.ARCHITECTURE,
            scope=Scope.SYSTEM,
            summary="Used SQLite for persistence",
            rationale="Zero infrastructure requirement",
            confidence=0.9,
        ),
        # ... more nodes ...
    ]
    temp_db.store_nodes(nodes)
    return temp_db, nodes
```

## Session Fixtures (fixtures.py)

```python
# Realistic JSONL session strings for parsing tests
ARCHITECTURE_SESSION = """
{"uuid":"...", "role":"user", "content":[{"type":"text","text":"implement storage"}], ...}
{"uuid":"...", "role":"assistant", "content":[{"type":"text","text":"I'll use SQLite because..."}], ...}
"""

BUGFIX_SESSION = """..."""

CONTINUATION_SESSION = """..."""  # Tests multi-session deduplication
```

## Writing a Parser Test

```python
# tests/test_parser.py
def test_parse_architecture_session():
    """Parsing should extract assistant turns with tool calls."""
    from tests.fixtures import ARCHITECTURE_SESSION
    import tempfile, os
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.write(ARCHITECTURE_SESSION)
        path = f.name
    
    try:
        session = parse_jsonl_file(Path(path))
        assert session is not None
        assert len(session.assistant_turns) >= 1
        assert session.session_id != ""
    finally:
        os.unlink(path)
```

## Writing an Extractor Test

```python
# tests/test_extractor.py
class TestClassification:
    def test_architecture_signals(self):
        """Texts with design decision language → ARCHITECTURE."""
        from src.parser.extractor import _classify_node_type
        
        texts = [
            "I chose SQLite because it requires no external service",
            "The architecture uses a layered approach",
            "This system is designed to be zero-infrastructure",
        ]
        for text in texts:
            node_type, confidence = _classify_node_type(text)
            assert node_type == NodeType.ARCHITECTURE, f"Failed for: {text}"
            assert confidence > 0.3
    
    def test_rejection_signals(self):
        """Texts with rejection language → REJECTION."""
        from src.parser.extractor import _classify_node_type
        
        text = "We decided against using PostgreSQL because it needs a server"
        node_type, confidence = _classify_node_type(text)
        assert node_type == NodeType.REJECTION
    
    def test_full_extraction_pipeline(self, sample_session):
        """End-to-end: session → nodes."""
        from src.parser.extractor import extract_nodes_from_session
        
        nodes = extract_nodes_from_session(sample_session, min_confidence=0.3)
        assert isinstance(nodes, list)
        assert all(isinstance(n, IRNode) for n in nodes)
        assert all(0.0 <= n.confidence <= 1.0 for n in nodes)
```

## Writing a Storage Test

```python
# tests/test_storage.py
def test_store_and_retrieve(temp_db):
    node = IRNode(
        id=str(uuid.uuid4()),
        session_id="s1",
        project_path="/tmp/test",
        timestamp=datetime.now(),
        node_type=NodeType.ARCHITECTURE,
        scope=Scope.SYSTEM,
        summary="Test node",
        confidence=0.9,
    )
    temp_db.store_nodes([node])
    
    results = temp_db.query_nodes("/tmp/test")
    assert len(results) == 1
    assert results[0].summary == "Test node"

def test_keyword_search(temp_db):
    # Store nodes with specific text
    # Then search for keywords that appear in summary/rationale
    ...

def test_session_idempotency(temp_db):
    # is_session_parsed() should return False before, True after
    assert not temp_db.is_session_parsed("new-session-id")
    meta = SessionMeta(session_id="new-session-id", ...)
    temp_db.upsert_session(meta)
    assert temp_db.is_session_parsed("new-session-id")
```

## Writing a Retriever Test

```python
# tests/test_retriever.py
def test_hybrid_retrieval_returns_relevant_nodes(populated_store):
    store, nodes = populated_store
    emb = EmbeddingManager(store)
    retriever = QueryContextRetriever(store, emb)
    
    result = retriever.retrieve(ContextRequest(
        query="SQLite database persistence",
        project_path="/tmp/test-project",
        max_results=5,
    ))
    
    assert len(result.nodes) > 0
    assert result.query_time_ms > 0
    # The architecture node about SQLite should rank high
    top_node = result.nodes[0]
    assert "sqlite" in top_node.summary.lower() or \
           top_node.node_type == NodeType.ARCHITECTURE
```

## Writing a Watcher Test

```python
# tests/test_watcher.py
def test_debounce_window(tmp_path):
    """Files modified during debounce window should not be parsed yet."""
    parsed_files = []
    
    def on_parsed(path, nodes, session):
        parsed_files.append(path)
    
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    watcher = DebouncedWatcher(
        watch_path=session_dir,
        debounce_seconds=0.5,  # short for test
        on_parsed=on_parsed,
    )
    watcher.start()
    
    # Write file (triggers event)
    test_file = session_dir / "test.jsonl"
    test_file.write_text('{"uuid":"1","role":"user","content":[]}')
    
    time.sleep(0.2)
    assert len(parsed_files) == 0  # still in debounce window
    
    time.sleep(0.5)
    assert len(parsed_files) == 1  # debounce settled
    
    watcher.stop()
```

## Test Data Patterns

```python
def make_ir_node(
    node_type=NodeType.ARCHITECTURE,
    summary="Test decision",
    confidence=0.8,
    project_path="/tmp/test",
    **kwargs,
) -> IRNode:
    """Factory for test IRNodes."""
    return IRNode(
        id=str(uuid.uuid4()),
        session_id="test-session",
        project_path=project_path,
        timestamp=datetime.now(),
        node_type=node_type,
        scope=Scope.SYSTEM,
        summary=summary,
        confidence=confidence,
        **kwargs,
    )
```

## Common Test Failures

| Failure | Cause | Fix |
|---------|-------|-----|
| `sqlite_vec ImportError` | Extension not in test env | `pip install sqlite-vec` |
| `SentenceTransformerError` | Model not cached | First run downloads ~80MB, ensure network access |
| Flaky timing tests | CI is slow | Use `time.sleep()` with generous margins |
| DB isolation failure | Tests sharing state | Each test should use `temp_db` fixture |
| Import errors | Package not installed | `pip install -e ".[dev]"` in project root |
