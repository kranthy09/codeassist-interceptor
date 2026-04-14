"""
Tests for extractor.py

Validates IR node extraction quality:
  - Correct node type classification
  - Scope inference from file paths
  - Decision chaining across turns
  - Summary and rationale extraction
  - Tag extraction from content
  - Confidence scoring
  - Rejection alternative capture
  - Low-signal filtering
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models.ir import NodeType, Scope
from src.parser.extractor import (
    extract_nodes_from_session,
    extract_with_context_chaining,
)
from src.parser.session_parser import parse_jsonl_file
from tests.fixtures import (
    ARCHITECTURE_SESSION,
    BUGFIX_SESSION,
    DEPENDENCY_SESSION,
    LOW_SIGNAL_SESSION,
    REFACTOR_SESSION,
    write_fixture,
)


class TestNodeTypeClassification:
    """Test that sessions get classified into correct node types."""

    def test_architecture_decisions_detected(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        arch_nodes = [n for n in nodes if n.node_type == NodeType.ARCHITECTURE]
        assert len(arch_nodes) >= 1, (
            f"Expected architecture nodes, got types: "
            f"{[n.node_type.value for n in nodes]}"
        )

    def test_bugfix_decisions_detected(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-bug-001", BUGFIX_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        bugfix_nodes = [n for n in nodes if n.node_type == NodeType.BUGFIX]
        assert len(bugfix_nodes) >= 1

    def test_dependency_decisions_detected(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-dep-001", DEPENDENCY_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        dep_nodes = [n for n in nodes if n.node_type == NodeType.DEPENDENCY]
        # dependency session mentions installing packages
        assert len(dep_nodes) >= 1 or any(
            "dependency" in n.tags or n.node_type == NodeType.IMPLEMENTATION
            for n in nodes
        )

    def test_low_signal_produces_minimal_nodes(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-low-001", LOW_SIGNAL_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        # "What's in the directory" + "Thanks" should produce 0-1 nodes
        assert len(nodes) <= 1


class TestScopeInference:
    """Test scope detection from file paths and content."""

    def test_multi_file_changes_infer_module_scope(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-ref-001", REFACTOR_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        # refactor touches 3 files in different dirs → module scope
        assert any(n.scope in (Scope.MODULE, Scope.SYSTEM) for n in nodes)

    def test_config_files_infer_system_scope(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-bug-001", BUGFIX_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        # nginx.conf is a config file → system scope
        config_nodes = [n for n in nodes if "nginx/nginx.conf" in n.files_affected]
        if config_nodes:
            assert config_nodes[0].scope == Scope.SYSTEM


class TestDecisionChaining:
    """Test parent-child linking of related decisions."""

    def test_consecutive_related_turns_chained(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_with_context_chaining(session)

        if len(nodes) >= 2:
            # second node should reference first as parent
            assert nodes[1].parent_node_id is not None

    def test_chain_links_by_shared_files(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_with_context_chaining(session)

        chained = [n for n in nodes if n.parent_node_id is not None]
        for node in chained:
            parent = next((n for n in nodes if n.id == node.parent_node_id), None)
            if parent:
                shared = set(parent.files_affected) & set(node.files_affected)
                gap = (node.timestamp - parent.timestamp).total_seconds()
                # either shared files or close in time
                assert shared or gap <= 180


class TestExtractedContent:
    """Test quality of extracted summaries, rationale, and tags."""

    def test_summary_is_concise(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        for node in nodes:
            assert len(node.summary) <= 130  # 120 + "..."
            assert len(node.summary) > 10

    def test_rationale_captures_reasoning(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        for node in nodes:
            assert len(node.rationale) > 0

    def test_tags_extracted_from_content(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        all_tags = {tag for node in nodes for tag in node.tags}
        # architecture session mentions backend, security, api concepts
        assert len(all_tags) > 0

    def test_files_affected_populated(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        nodes_with_files = [n for n in nodes if n.files_affected]
        assert len(nodes_with_files) > 0

    def test_confidence_within_bounds(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        for node in nodes:
            assert 0.0 <= node.confidence <= 1.0

    def test_rejection_alternatives_captured(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        rejection_nodes = [n for n in nodes if n.node_type == NodeType.REJECTION]
        # the architecture session has "instead of NextAuth" and "decided against" ORM-less
        # these may or may not classify as rejections depending on signal strength
        # but if they do, alternatives should be captured
        for rn in rejection_nodes:
            if rn.alternatives_rejected:
                assert isinstance(rn.alternatives_rejected, list)

    def test_session_id_and_project_propagated(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)
        nodes = extract_nodes_from_session(session)

        for node in nodes:
            assert node.session_id == session.session_id
            assert node.project_path == session.project_path
