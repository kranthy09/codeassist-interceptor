"""
Embedding manager for semantic search.

Uses sentence-transformers for local embedding generation (no API calls).
Stores vectors in a parallel SQLite table for fast retrieval.

Model: all-MiniLM-L6-v2 (384 dimensions, ~80MB, runs on CPU)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import numpy as np

from ..models.ir import IRNode

logger = logging.getLogger(__name__)

# lazy-load the model to avoid import-time cost
_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


_VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id    TEXT PRIMARY KEY,
    embedding  BLOB NOT NULL,
    model_name TEXT DEFAULT 'all-MiniLM-L6-v2'
);
"""

EMBEDDING_DIM = 384


class EmbeddingManager:
    """
    Manages vector embeddings for IR nodes.

    Encodes node content (summary + rationale + tags) into dense vectors
    and provides cosine similarity search.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_VECTOR_SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _node_to_text(self, node: IRNode) -> str:
        """Build the text representation for embedding."""
        parts = [
            f"[{node.node_type.value}]",
            node.summary,
            node.rationale,
        ]
        if node.tags:
            parts.append(f"tags: {', '.join(node.tags)}")
        if node.files_affected:
            parts.append(f"files: {', '.join(node.files_affected[:5])}")
        return " ".join(parts)

    def encode_text(self, text: str) -> np.ndarray:
        """Encode a single text string to a vector."""
        try:
            model = _get_model()
        except ImportError:
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)
        return model.encode(text, normalize_embeddings=True)

    def encode_and_store(self, nodes: list[IRNode]) -> int:
        """Encode and store embeddings for a batch of nodes."""
        if not nodes:
            return 0

        try:
            model = _get_model()
        except ImportError:
            logger.warning(
                "sentence-transformers not installed — skipping embeddings. "
                "Install with: pip install sentence-transformers"
            )
            return 0

        texts = [self._node_to_text(n) for n in nodes]
        vectors = model.encode(texts, normalize_embeddings=True, batch_size=32)

        stored = 0
        for node, vec in zip(nodes, vectors):
            try:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO node_embeddings (node_id, embedding)
                    VALUES (?, ?)
                    """,
                    (node.id, vec.astype(np.float32).tobytes()),
                )
                stored += 1
            except sqlite3.Error:
                continue

        self.conn.commit()
        return stored

    def search_similar(
        self,
        query: str,
        node_ids: list[str],
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        Find most similar nodes to a query string.

        Args:
            query: search text
            node_ids: candidate node IDs to search within
            top_k: number of results

        Returns:
            List of (node_id, similarity_score) sorted by score desc
        """
        if not node_ids:
            return []

        t0 = time.monotonic()
        query_vec = self.encode_text(query)
        encode_ms = (time.monotonic() - t0) * 1000

        # fetch candidate embeddings
        placeholders = ",".join("?" * len(node_ids))
        rows = self.conn.execute(
            f"SELECT node_id, embedding FROM node_embeddings WHERE node_id IN ({placeholders})",
            node_ids,
        ).fetchall()

        if not rows:
            return []

        # compute cosine similarities (vectors are already normalized)
        scores = []
        for row in rows:
            stored_vec = np.frombuffer(row["embedding"], dtype=np.float32)
            similarity = float(np.dot(query_vec, stored_vec))
            scores.append((row["node_id"], similarity))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def has_embedding(self, node_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM node_embeddings WHERE node_id = ?", (node_id,)
        ).fetchone()
        return row is not None
