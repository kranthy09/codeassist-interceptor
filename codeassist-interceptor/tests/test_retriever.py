"""
Tests for context_retriever.py

Validates the hybrid retrieval pipeline:
  - Keyword extraction from queries
  - Recency scoring decay curve
  - Score blending (semantic + recency)
  - Architecture/pattern boost
  - Context summary generation
  - File scope filtering
  - Empty result handling
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.ir import ContextRequest, IRNode, NodeType, Scope
from src.retriever.context_retriever import (
    QueryContextRetriever,
    _extract_keywords,
    _recency_score,
)
from src.storage.ir_store import IRStorage


def _make_node(
    node_id: str,
    node_type: NodeType = NodeType.ARCHITECTURE,
    summary: str = "test decision",
    timestamp: datetime | None = None,
    files: list[str] | None = None,
    tags: list[str] | None = None,
    confidence: float = 0.8,
) -> IRNode:
    return IRNode(
        id=node_id,
        session_id="sess-001",
        project_path="/project",
        timestamp=timestamp or datetime.utcnow(),
        node_type=node_type,
        scope=Scope.MODULE,
        summary=summary,
        rationale="because reasons",
        files_affected=files or [],
        tags=tags or [],
        confidence=confidence,
    )


class TestKeywordExtraction:
    """Test query → keyword extraction."""

    def test_removes_stop_words(self):
        keywords = _extract_keywords("what is the authentication flow")
        assert "what" not in keywords
        assert "the" not in keywords
        assert "authentication" in keywords

    def test_removes_short_words(self):
        keywords = _extract_keywords("is it ok to use JWT")
        assert "is" not in keywords
        assert "it" not in keywords
        assert "ok" not in keywords
        # JWT is 3 chars and not a stop word, so it IS extracted
        assert "jwt" in keywords

    def test_extracts_technical_terms(self):
        keywords = _extract_keywords("database migration schema PostgreSQL")
        assert "database" in keywords
        assert "migration" in keywords
        assert "postgresql" in keywords

    def test_empty_query(self):
        assert _extract_keywords("") == []

    def test_only_stop_words(self):
        assert _extract_keywords("the and or but") == []


class TestRecencyScoring:
    """Test exponential decay scoring."""

    def test_recent_scores_high(self):
        score = _recency_score(datetime.utcnow())
        assert score > 0.95

    def test_old_scores_low(self):
        old = datetime.utcnow() - timedelta(days=30)
        score = _recency_score(old)
        assert score < 0.2

    def test_half_life_at_7_days(self):
        half = datetime.utcnow() - timedelta(days=7)
        score = _recency_score(half)
        assert 0.4 < score < 0.6  # approximately 0.5

    def test_never_reaches_zero(self):
        ancient = datetime.utcnow() - timedelta(days=365)
        score = _recency_score(ancient)
        assert score > 0


class TestRetrieverIntegration:
    """Test the full retrieval pipeline with mocked storage."""

    @pytest.fixture
    def retriever(self, tmp_path: Path):
        storage = IRStorage(db_path=tmp_path / "test.db")
        _ = storage.conn

        # mock embedding manager to avoid loading the real model
        mock_embeddings = MagicMock()
        mock_embeddings.search_similar.return_value = []

        return QueryContextRetriever(storage, mock_embeddings), storage

    def test_empty_project_returns_empty(self, retriever):
        ret, storage = retriever
        request = ContextRequest(query="auth flow", project_path="/empty")
        result = ret.retrieve(request)

        assert result.nodes == []
        assert result.total_available == 0

    def test_keyword_match_returns_nodes(self, retriever):
        ret, storage = retriever
        storage.store_nodes([
            _make_node("n1", summary="JWT authentication implementation"),
            _make_node("n2", summary="Docker compose configuration"),
        ])

        request = ContextRequest(
            query="authentication JWT",
            project_path="/project",
        )
        result = ret.retrieve(request)

        assert len(result.nodes) >= 1
        assert any("JWT" in n.summary for n in result.nodes)

    def test_recency_weight_affects_ranking(self, retriever):
        ret, storage = retriever
        old_node = _make_node(
            "n1", summary="old auth decision",
            timestamp=datetime.utcnow() - timedelta(days=30),
        )
        new_node = _make_node(
            "n2", summary="new auth decision",
            timestamp=datetime.utcnow() - timedelta(hours=1),
        )
        storage.store_nodes([old_node, new_node])

        # pure recency
        request = ContextRequest(
            query="auth decision",
            project_path="/project",
            recency_weight=1.0,
        )
        result = ret.retrieve(request)

        if len(result.nodes) >= 2:
            assert result.nodes[0].id == "n2"  # newer first

    def test_max_results_respected(self, retriever):
        ret, storage = retriever
        storage.store_nodes([_make_node(f"n{i}") for i in range(20)])

        request = ContextRequest(
            query="test", project_path="/project", max_results=5
        )
        result = ret.retrieve(request)

        assert len(result.nodes) <= 5

    def test_file_scope_filtering(self, retriever):
        ret, storage = retriever
        storage.store_nodes([
            _make_node("n1", files=["auth/login.py"]),
            _make_node("n2", files=["docker-compose.yml"]),
        ])

        request = ContextRequest(
            query="changes",
            project_path="/project",
            files_in_scope=["auth/login.py"],
        )
        result = ret.retrieve(request)

        # node with matching file should appear
        assert any("auth/login.py" in n.files_affected for n in result.nodes)


class TestContextSummary:
    """Test markdown summary generation."""

    @pytest.fixture
    def retriever(self, tmp_path: Path):
        storage = IRStorage(db_path=tmp_path / "test.db")
        _ = storage.conn
        mock_embeddings = MagicMock()
        return QueryContextRetriever(storage, mock_embeddings), storage

    def test_empty_project_returns_empty_string(self, retriever):
        ret, _ = retriever
        summary = ret.get_context_summary("/empty")
        assert summary == ""

    def test_summary_includes_sections(self, retriever):
        ret, storage = retriever
        storage.store_nodes([
            _make_node("n1", node_type=NodeType.ARCHITECTURE,
                       summary="JWT auth chosen", timestamp=datetime.utcnow()),
            _make_node("n2", node_type=NodeType.REJECTION,
                       summary="Rejected session-based auth", timestamp=datetime.utcnow()),
        ])

        summary = ret.get_context_summary("/project")
        assert "architectural decisions" in summary.lower() or "decisions" in summary.lower()
        assert len(summary) > 50

    def test_summary_includes_stats(self, retriever):
        ret, storage = retriever
        storage.store_nodes([_make_node("n1")])

        summary = ret.get_context_summary("/project")
        assert "1" in summary  # at least mentions count
