"""
Tests for ir_store.py

Validates storage operations:
  - Schema initialization
  - Node insert and retrieval
  - Session upsert and duplicate detection
  - Keyword search across fields
  - Type and recency filtering
  - Project stats aggregation
  - Idempotent operations (insert same node twice)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.models.ir import IRNode, NodeType, Scope, SessionMeta
from src.storage.ir_store import IRStorage


@pytest.fixture
def storage(tmp_path: Path) -> IRStorage:
    """Create a fresh storage instance with temporary database."""
    db_path = tmp_path / "test_ir.db"
    s = IRStorage(db_path=db_path)
    _ = s.conn  # force schema creation
    yield s
    s.close()


def _make_node(
    node_id: str = "n001",
    session_id: str = "sess-001",
    project_path: str = "/home/user/project",
    node_type: NodeType = NodeType.ARCHITECTURE,
    summary: str = "Chose JWT authentication over session-based auth",
    rationale: str = "JWT allows stateless API design",
    **kwargs,
) -> IRNode:
    """Factory for test IR nodes."""
    return IRNode(
        id=node_id,
        session_id=session_id,
        project_path=project_path,
        timestamp=kwargs.get("timestamp", datetime(2026, 4, 1, 10, 0)),
        node_type=node_type,
        scope=kwargs.get("scope", Scope.MODULE),
        summary=summary,
        rationale=rationale,
        alternatives_rejected=kwargs.get("alternatives_rejected", []),
        files_affected=kwargs.get("files_affected", []),
        tags=kwargs.get("tags", ["security", "backend"]),
        confidence=kwargs.get("confidence", 0.8),
    )


class TestSchemaInit:
    """Test database initialization."""

    def test_creates_tables(self, storage: IRStorage):
        tables = storage.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}

        assert "sessions" in table_names
        assert "nodes" in table_names

    def test_creates_indexes(self, storage: IRStorage):
        indexes = storage.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}

        assert "idx_nodes_project" in index_names
        assert "idx_nodes_type" in index_names


class TestNodeOperations:
    """Test node insert and retrieval."""

    def test_store_and_retrieve(self, storage: IRStorage):
        node = _make_node()
        stored = storage.store_nodes([node])
        assert stored == 1

        results = storage.query_nodes("/home/user/project")
        assert len(results) == 1
        assert results[0].id == "n001"
        assert results[0].summary == node.summary

    def test_store_multiple_nodes(self, storage: IRStorage):
        nodes = [
            _make_node("n001", timestamp=datetime(2026, 4, 1, 10, 0)),
            _make_node("n002", node_type=NodeType.BUGFIX, timestamp=datetime(2026, 4, 1, 11, 0)),
            _make_node("n003", node_type=NodeType.PATTERN, timestamp=datetime(2026, 4, 1, 12, 0)),
        ]
        stored = storage.store_nodes(nodes)
        assert stored == 3

    def test_duplicate_node_ignored(self, storage: IRStorage):
        node = _make_node()
        storage.store_nodes([node])
        stored = storage.store_nodes([node])  # same id again
        assert stored == 0  # duplicate ignored, rowcount=0

        results = storage.query_nodes("/home/user/project")
        assert len(results) == 1  # not duplicated

    def test_filter_by_node_type(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", node_type=NodeType.ARCHITECTURE),
            _make_node("n002", node_type=NodeType.BUGFIX),
            _make_node("n003", node_type=NodeType.ARCHITECTURE),
        ])

        results = storage.query_nodes(
            "/home/user/project", node_types=[NodeType.ARCHITECTURE]
        )
        assert len(results) == 2
        assert all(r.node_type == NodeType.ARCHITECTURE for r in results)

    def test_filter_by_recency(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", timestamp=datetime(2026, 1, 1)),
            _make_node("n002", timestamp=datetime(2026, 4, 1)),
        ])

        results = storage.query_nodes(
            "/home/user/project", since=datetime(2026, 3, 1)
        )
        assert len(results) == 1
        assert results[0].id == "n002"

    def test_results_ordered_by_timestamp_desc(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", timestamp=datetime(2026, 4, 1, 10, 0)),
            _make_node("n002", timestamp=datetime(2026, 4, 1, 12, 0)),
            _make_node("n003", timestamp=datetime(2026, 4, 1, 11, 0)),
        ])

        results = storage.query_nodes("/home/user/project")
        timestamps = [r.timestamp for r in results]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_limit_respected(self, storage: IRStorage):
        nodes = [_make_node(f"n{i:03d}") for i in range(10)]
        storage.store_nodes(nodes)

        results = storage.query_nodes("/home/user/project", limit=3)
        assert len(results) == 3

    def test_project_isolation(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", project_path="/project-a"),
            _make_node("n002", project_path="/project-b"),
        ])

        results_a = storage.query_nodes("/project-a")
        results_b = storage.query_nodes("/project-b")

        assert len(results_a) == 1
        assert len(results_b) == 1
        assert results_a[0].id == "n001"


class TestKeywordSearch:
    """Test keyword search functionality."""

    def test_search_by_summary(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", summary="Chose JWT authentication",
                       rationale="Token-based auth for stateless design"),
            _make_node("n002", summary="Added Docker configuration",
                       rationale="Container orchestration for deployment"),
        ])

        results = storage.search_keyword("/home/user/project", ["JWT"])
        assert len(results) == 1
        assert results[0].id == "n001"

    def test_search_by_rationale(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", rationale="Stateless API design allows horizontal scaling"),
        ])

        results = storage.search_keyword("/home/user/project", ["scaling"])
        assert len(results) == 1

    def test_search_multiple_keywords(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", summary="JWT auth with bcrypt hashing"),
            _make_node("n002", summary="Docker multi-stage build"),
        ])

        results = storage.search_keyword("/home/user/project", ["JWT", "bcrypt"])
        assert len(results) == 1  # both keywords must match

    def test_search_no_results(self, storage: IRStorage):
        storage.store_nodes([_make_node()])
        results = storage.search_keyword("/home/user/project", ["kubernetes"])
        assert len(results) == 0

    def test_search_empty_keywords(self, storage: IRStorage):
        results = storage.search_keyword("/home/user/project", [])
        assert len(results) == 0


class TestSessionOperations:
    """Test session metadata tracking."""

    def test_upsert_session(self, storage: IRStorage):
        meta = SessionMeta(
            session_id="sess-001",
            project_path="/home/user/project",
            started_at=datetime(2026, 4, 1, 10, 0),
            nodes_extracted=5,
        )
        storage.upsert_session(meta)

        assert storage.is_session_parsed("sess-001")
        assert not storage.is_session_parsed("sess-999")

    def test_upsert_updates_existing(self, storage: IRStorage):
        meta1 = SessionMeta(
            session_id="sess-001",
            project_path="/home/user/project",
            started_at=datetime(2026, 4, 1),
            nodes_extracted=3,
        )
        meta2 = SessionMeta(
            session_id="sess-001",
            project_path="/home/user/project",
            started_at=datetime(2026, 4, 1),
            nodes_extracted=7,
        )
        storage.upsert_session(meta1)
        storage.upsert_session(meta2)

        row = storage.conn.execute(
            "SELECT nodes_extracted FROM sessions WHERE session_id = ?",
            ("sess-001",),
        ).fetchone()
        assert row["nodes_extracted"] == 7


class TestProjectStats:
    """Test aggregation queries."""

    def test_stats_with_data(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", node_type=NodeType.ARCHITECTURE),
            _make_node("n002", node_type=NodeType.BUGFIX),
            _make_node("n003", node_type=NodeType.ARCHITECTURE),
        ])

        stats = storage.get_project_stats("/home/user/project")
        assert stats["total_nodes"] == 3
        assert stats["by_type"]["architecture"] == 2
        assert stats["by_type"]["bugfix"] == 1

    def test_stats_empty_project(self, storage: IRStorage):
        stats = storage.get_project_stats("/nonexistent")
        assert stats["total_nodes"] == 0

    def test_stats_session_count(self, storage: IRStorage):
        storage.store_nodes([
            _make_node("n001", session_id="sess-001"),
            _make_node("n002", session_id="sess-001"),
            _make_node("n003", session_id="sess-002"),
        ])

        stats = storage.get_project_stats("/home/user/project")
        assert stats["total_sessions"] == 2
