"""
MCP Server for CodeAssist Interceptor.

Exposes IR retrieval as native Claude Code tools via FastMCP.
Claude Code can call these tools during any session to get
rich project context without re-reading files.

Tools exposed:
  - get_project_context: retrieve relevant decisions for a query
  - get_decision_history: list recent decisions by type
  - get_project_summary: concise overview of all captured reasoning
  - search_decisions: keyword search across all captured IR nodes

Registration:
  claude mcp add codeassist-interceptor -- codeassist serve
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..models.ir import ContextRequest, NodeType
from ..retriever.context_retriever import QueryContextRetriever
from ..storage.embeddings import EmbeddingManager
from ..storage.ir_store import DB_PATH, IRStorage

logger = logging.getLogger(__name__)

_components: tuple[IRStorage, EmbeddingManager, QueryContextRetriever] | None = None


def _get_components() -> tuple[IRStorage, EmbeddingManager, QueryContextRetriever]:
    """Lazy-initialize storage and retriever as a module-level singleton.

    First call loads the ~80MB embedding model. Subsequent calls return the
    cached tuple instantly — no reload on every tool invocation.
    """
    global _components
    if _components is None:
        logger.info("Initializing components (first call — loading embedding model)…")
        storage = IRStorage()
        embeddings = EmbeddingManager(storage.db_path)
        retriever = QueryContextRetriever(storage, embeddings)
        _components = (storage, embeddings, retriever)
        logger.info("Components ready.")
    return _components


def create_mcp_server():
    """Create and configure the FastMCP server."""
    from fastmcp import FastMCP

    mcp = FastMCP(
        name="codeassist-interceptor",
        instructions=(
            "Provides project reasoning context from previous Claude Code sessions. "
            "Use get_project_context to find relevant architectural decisions, "
            "patterns, and rejected approaches before starting new work. "
            "This avoids re-reading files that were already analyzed."
        ),
    )

    @mcp.tool
    def get_project_context(
        query: str,
        project_path: str = "",
        max_results: int = 8,
        recency_weight: float = 0.3,
    ) -> str:
        """
        Retrieve relevant project decisions and reasoning for a query.

        Use this BEFORE reading project files to understand what was
        previously decided, what patterns are established, and what
        approaches were rejected.

        Args:
            query: what you're about to work on (e.g. "auth flow", "database schema")
            project_path: project root path (auto-detected if empty)
            max_results: how many relevant decisions to return
            recency_weight: 0.0 = pure semantic match, 1.0 = most recent first
        """
        if not project_path:
            project_path = os.getcwd()

        _, _, retriever = _get_components()

        request = ContextRequest(
            query=query,
            project_path=project_path,
            max_results=max_results,
            recency_weight=recency_weight,
        )

        result = retriever.retrieve(request)

        if not result.nodes:
            return f"No prior decisions found for '{query}' in this project."

        lines = [
            f"Found {len(result.nodes)} relevant decisions "
            f"(from {result.total_available} total, "
            f"retrieved in {result.retrieval_time_ms:.0f}ms):",
            "",
        ]

        for i, node in enumerate(result.nodes, 1):
            lines.extend([
                f"### {i}. [{node.node_type.value.upper()}] {node.summary}",
                f"**Scope**: {node.scope.value} | "
                f"**When**: {node.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                f"**Confidence**: {node.confidence:.0%}",
                f"**Rationale**: {node.rationale}",
            ])
            if node.files_affected:
                lines.append(f"**Files**: {', '.join(node.files_affected[:5])}")
            if node.alternatives_rejected:
                lines.append(f"**Rejected**: {', '.join(node.alternatives_rejected)}")
            if node.tags:
                lines.append(f"**Tags**: {', '.join(node.tags)}")
            lines.append("")

        return "\n".join(lines)

    @mcp.tool
    def get_decision_history(
        project_path: str = "",
        decision_type: str = "",
        days: int = 14,
        limit: int = 15,
    ) -> str:
        """
        List recent project decisions filtered by type.

        Args:
            project_path: project root (auto-detected if empty)
            decision_type: filter by type (architecture, implementation,
                          rejection, dependency, pattern, bugfix, refactor, convention)
            days: how far back to look
            limit: max results
        """
        if not project_path:
            project_path = os.getcwd()

        storage, _, _ = _get_components()

        node_types = None
        if decision_type:
            try:
                node_types = [NodeType(decision_type.lower())]
            except ValueError:
                return f"Unknown type '{decision_type}'. Valid: {', '.join(t.value for t in NodeType)}"

        nodes = storage.query_nodes(
            project_path, node_types, limit,
            since=datetime.utcnow() - timedelta(days=days),
        )

        if not nodes:
            return f"No decisions found in the last {days} days."

        lines = [f"## {len(nodes)} decisions (last {days} days)", ""]
        for n in nodes:
            ts = n.timestamp.strftime("%m/%d %H:%M")
            lines.append(
                f"- **[{n.node_type.value}]** {n.summary} "
                f"({ts}, {n.scope.value} scope)"
            )

        return "\n".join(lines)

    @mcp.tool
    def get_project_summary(project_path: str = "") -> str:
        """
        Get a concise overview of all captured reasoning for this project.

        Call this at the start of a new session to quickly understand
        the project's architectural state without reading files.
        """
        if not project_path:
            project_path = os.getcwd()

        _, _, retriever = _get_components()
        summary = retriever.get_context_summary(project_path)

        if not summary:
            return (
                "No reasoning captured yet for this project. "
                "Run `codeassist parse` to process existing sessions."
            )

        return summary

    @mcp.tool
    def search_decisions(
        query: str,
        project_path: str = "",
        limit: int = 10,
    ) -> str:
        """
        Search all captured decisions by keyword.

        Args:
            query: search terms (e.g. "authentication JWT", "database migration")
            project_path: project root (auto-detected if empty)
            limit: max results
        """
        if not project_path:
            project_path = os.getcwd()

        storage, _, _ = _get_components()
        import re
        keywords = [
            w for w in re.findall(r'\b\w+\b', query.lower())
            if len(w) > 2
        ]

        nodes = storage.search_keyword(project_path, keywords, limit)

        if not nodes:
            return f"No decisions matching '{query}'."

        lines = [f"## {len(nodes)} results for '{query}'", ""]
        for n in nodes:
            lines.append(
                f"- **{n.summary}** [{n.node_type.value}] "
                f"({n.timestamp.strftime('%Y-%m-%d')})"
            )
            lines.append(f"  {n.rationale[:150]}")

        return "\n".join(lines)

    return mcp


def run_server():
    """Start the MCP server (stdio transport for Claude Code)."""
    mcp = create_mcp_server()
    mcp.run(transport="stdio")
