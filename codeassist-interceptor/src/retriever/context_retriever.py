"""
QueryContextRetriever — the payoff module.

Combines three retrieval signals:
1. Semantic similarity (vector search via embeddings)
2. Keyword matching (exact terms in summaries/rationale)
3. Recency decay (newer decisions weighted higher)

Assembles a ranked context payload that gets served to Claude Code,
giving it rich project understanding without re-reading files.
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta

from ..models.ir import ContextRequest, ContextResult, IRNode, NodeType
from ..storage.embeddings import EmbeddingManager
from ..storage.ir_store import IRStorage


def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a natural language query."""
    stop_words = {
        "the", "a", "an", "is", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "can", "shall", "to", "of",
        "in", "for", "on", "with", "at", "by", "from", "as", "into",
        "about", "this", "that", "these", "those", "it", "its", "and",
        "or", "but", "not", "what", "how", "why", "when", "where",
        "which", "who", "whom", "my", "our", "we", "me", "i", "you",
    }
    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', query.lower())
    return [w for w in words if w not in stop_words and len(w) > 2]


def _recency_score(timestamp: datetime, half_life_days: float = 7.0) -> float:
    """
    Exponential decay score based on age.
    Score = 1.0 for now, ~0.5 after half_life_days.
    """
    age = (datetime.utcnow() - timestamp).total_seconds()
    decay = math.exp(-0.693 * age / (half_life_days * 86400))
    return max(decay, 0.01)


class QueryContextRetriever:
    """
    Retrieves relevant IR context for a given query.

    The retrieve() method is the main entry point, called by the
    MCP server when Claude Code needs project context.
    """

    def __init__(
        self,
        storage: IRStorage,
        embeddings: EmbeddingManager,
    ):
        self.storage = storage
        self.embeddings = embeddings

    def retrieve(self, request: ContextRequest) -> ContextResult:
        """
        Main retrieval pipeline.

        1. Get candidate nodes from storage (keyword + type filter)
        2. Score candidates with semantic similarity
        3. Blend with recency decay
        4. Return top-k ranked results
        """
        t0 = time.monotonic()

        # ── Step 1: gather candidates ─────────────────────────────
        keywords = _extract_keywords(request.query)
        candidates: dict[str, IRNode] = {}

        # keyword matches
        if keywords:
            kw_nodes = self.storage.search_keyword(
                request.project_path, keywords, limit=50
            )
            for node in kw_nodes:
                candidates[node.id] = node

        # recent nodes (always include)
        recent_nodes = self.storage.query_nodes(
            request.project_path,
            node_types=request.node_types,
            limit=30,
            since=datetime.utcnow() - timedelta(days=30),
        )
        for node in recent_nodes:
            candidates[node.id] = node

        # file-scoped nodes (if files_in_scope provided)
        if request.files_in_scope:
            for node in self.storage.query_nodes(
                request.project_path, limit=50
            ):
                if any(
                    f in node.files_affected
                    for f in request.files_in_scope
                ):
                    candidates[node.id] = node

        if not candidates:
            return ContextResult(
                nodes=[], total_available=0,
                retrieval_time_ms=(time.monotonic() - t0) * 1000,
            )

        # ── Step 2: semantic scoring ──────────────────────────────
        t_embed = time.monotonic()
        candidate_ids = list(candidates.keys())
        semantic_scores = dict(
            self.embeddings.search_similar(
                request.query, candidate_ids, top_k=len(candidate_ids)
            )
        )
        embed_ms = (time.monotonic() - t_embed) * 1000

        # ── Step 3: blend scores ──────────────────────────────────
        rw = request.recency_weight
        sw = 1.0 - rw

        scored: list[tuple[IRNode, float]] = []
        for node_id, node in candidates.items():
            sem = semantic_scores.get(node_id, 0.0)
            rec = _recency_score(node.timestamp)
            blended = (sw * sem) + (rw * rec)

            # boost high-confidence nodes
            blended *= (0.7 + 0.3 * node.confidence)

            # boost architecture and pattern nodes (higher signal)
            if node.node_type in (
                NodeType.ARCHITECTURE, NodeType.PATTERN
            ):
                blended *= 1.15

            # penalize filler/introspection summaries
            filler_prefixes = (
                "Let me", "I ", "Now ", "The ", "You",
                "Here's", "So "
            )
            if any(
                node.summary.startswith(p) for p in filler_prefixes
            ):
                blended *= 0.85

            # boost nodes mentioning architectural concepts
            arch_keywords = (
                "invariant", "dependency", "rule", "layer",
                "separation", "module", "component"
            )
            summary_lower = node.summary.lower()
            rationale_lower = node.rationale.lower()
            if any(
                kw in summary_lower or kw in rationale_lower
                for kw in arch_keywords
            ):
                blended *= 1.2

            scored.append((node, blended))

        scored.sort(key=lambda x: x[1], reverse=True)

        # ── Step 4: assemble result ───────────────────────────────
        top_nodes = [node for node, _ in scored[:request.max_results]]

        return ContextResult(
            nodes=top_nodes,
            total_available=len(candidates),
            query_embedding_time_ms=embed_ms,
            retrieval_time_ms=(time.monotonic() - t0) * 1000,
        )

    def get_context_summary(
        self,
        project_path: str,
        max_tokens: int = 2000,
    ) -> str:
        """
        Generate a concise context summary for CLAUDE.md injection.

        Produces a markdown block with the most important decisions,
        patterns, and active conventions — formatted for Claude Code
        to consume efficiently.
        """
        stats = self.storage.get_project_stats(project_path)
        if stats["total_nodes"] == 0:
            return ""

        # get top decisions by type, deduping by node ID
        seen_ids = set()

        arch_nodes = self.storage.query_nodes(
            project_path,
            [NodeType.ARCHITECTURE, NodeType.PATTERN],
            limit=5
        )
        for n in arch_nodes:
            seen_ids.add(n.id)

        reject_nodes = self.storage.query_nodes(
            project_path, [NodeType.REJECTION], limit=3
        )
        for n in reject_nodes:
            seen_ids.add(n.id)

        recent_nodes = self.storage.query_nodes(
            project_path, limit=5,
            since=datetime.utcnow() - timedelta(days=7),
        )
        # Filter out duplicates
        recent_nodes = [n for n in recent_nodes if n.id not in seen_ids]

        lines = [
            "## Project Context (auto-generated by codeassist-interceptor)",
            "",
            (f"*{stats['total_nodes']} decisions captured across "
             f"{stats['total_sessions']} sessions*"),
            "",
        ]

        if arch_nodes:
            lines.append("### Key architectural decisions")
            for n in arch_nodes:
                # Show full rationale (max 500 chars) instead of truncating
                rationale_preview = (
                    n.rationale[:500] + "..."
                    if len(n.rationale) > 500
                    else n.rationale
                )
                lines.append(f"- **{n.summary}**")
                if rationale_preview:
                    lines.append(f"  *{rationale_preview}*")
            lines.append("")

        if reject_nodes:
            lines.append("### Explicitly rejected approaches")
            for n in reject_nodes:
                alts = (
                    ", ".join(n.alternatives_rejected[:3])
                    if n.alternatives_rejected
                    else "see rationale"
                )
                lines.append(f"- {n.summary} (rejected: {alts})")
            lines.append("")

        if recent_nodes:
            lines.append("### Recent decisions (last 7 days)")
            for n in recent_nodes:
                lines.append(f"- [{n.node_type.value}] {n.summary}")
            lines.append("")

        return "\n".join(lines)
