"""
IR Storage — SQLite + vector index.

Zero-infrastructure storage: everything lives in a single SQLite file
at ~/.codeassist/ir.db. Vector search uses sqlite-vec for semantic
retrieval without external services.

Tables:
  - sessions: parsed session metadata
  - nodes: IR nodes with full content
  - embeddings: vector index for semantic search (via sqlite-vec)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models.ir import (
    ContextRequest,
    ContextResult,
    IRNode,
    NodeType,
    Scope,
    SessionMeta,
)

DB_DIR = Path.home() / ".codeassist"
DB_PATH = DB_DIR / "ir.db"

# ── Schema ────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    project_path  TEXT NOT NULL,
    started_at    TEXT,
    ended_at      TEXT,
    model_used    TEXT DEFAULT 'unknown',
    total_turns   INTEGER DEFAULT 0,
    nodes_extracted INTEGER DEFAULT 0,
    parsed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    project_path    TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    node_type       TEXT NOT NULL,
    scope           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    alternatives    TEXT DEFAULT '[]',
    files_affected  TEXT DEFAULT '[]',
    tags            TEXT DEFAULT '[]',
    parent_node_id  TEXT,
    confidence      REAL DEFAULT 0.8,
    raw_source      TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_path);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_timestamp ON nodes(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id);
"""


class IRStorage:
    """
    Persistent storage for IR nodes.

    Handles SQLite operations and delegates to the embedding
    index for vector search.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Session operations ────────────────────────────────────────

    def upsert_session(self, meta: SessionMeta) -> None:
        self.conn.execute(
            """
            INSERT INTO sessions (session_id, project_path, started_at,
                                  ended_at, model_used, total_turns,
                                  nodes_extracted, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                nodes_extracted = excluded.nodes_extracted,
                parsed_at = excluded.parsed_at
            """,
            (
                meta.session_id, meta.project_path,
                meta.started_at.isoformat() if meta.started_at else None,
                meta.ended_at.isoformat() if meta.ended_at else None,
                meta.model_used, meta.total_turns,
                meta.nodes_extracted, meta.parsed_at.isoformat(),
            ),
        )
        self.conn.commit()

    def is_session_parsed(self, session_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None

    # ── Node operations ───────────────────────────────────────────

    def store_nodes(self, nodes: list[IRNode]) -> int:
        """Store a batch of IR nodes. Returns count stored."""
        stored = 0
        # ensure parent sessions exist (FK constraint)
        seen_sessions: set[str] = set()
        for node in nodes:
            if node.session_id not in seen_sessions:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO sessions
                        (session_id, project_path, parsed_at)
                    VALUES (?, ?, ?)
                    """,
                    (node.session_id, node.project_path,
                     datetime.utcnow().isoformat()),
                )
                seen_sessions.add(node.session_id)

        for node in nodes:
            try:
                cursor = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO nodes
                        (id, session_id, project_path, timestamp,
                         node_type, scope, summary, rationale,
                         alternatives, files_affected, tags,
                         parent_node_id, confidence, raw_source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id, node.session_id, node.project_path,
                        node.timestamp.isoformat(), node.node_type.value,
                        node.scope.value, node.summary, node.rationale,
                        json.dumps(node.alternatives_rejected),
                        json.dumps(node.files_affected),
                        json.dumps(node.tags),
                        node.parent_node_id, node.confidence,
                        node.raw_source, datetime.utcnow().isoformat(),
                    ),
                )
                if cursor.rowcount > 0:
                    stored += 1
            except sqlite3.IntegrityError:
                continue

        self.conn.commit()
        return stored

    def query_nodes(
        self,
        project_path: str,
        node_types: Optional[list[NodeType]] = None,
        limit: int = 20,
        since: Optional[datetime] = None,
    ) -> list[IRNode]:
        """Query nodes with filters."""
        clauses = ["project_path = ?"]
        params: list = [project_path]

        if node_types:
            placeholders = ",".join("?" * len(node_types))
            clauses.append(f"node_type IN ({placeholders})")
            params.extend(t.value for t in node_types)

        if since:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        where = " AND ".join(clauses)
        params.append(limit)

        rows = self.conn.execute(
            f"""
            SELECT * FROM nodes
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [self._row_to_node(r) for r in rows]

    def search_keyword(
        self,
        project_path: str,
        keywords: list[str],
        limit: int = 10,
    ) -> list[IRNode]:
        """Keyword search across summary, rationale, and tags."""
        if not keywords:
            return []

        like_clauses = []
        params: list = [project_path]
        for kw in keywords:
            like_clauses.append(
                "(summary LIKE ? OR rationale LIKE ? OR tags LIKE ? OR raw_source LIKE ?)"
            )
            pattern = f"%{kw}%"
            params.extend([pattern, pattern, pattern, pattern])

        where = " AND ".join(like_clauses)
        params.append(limit)

        rows = self.conn.execute(
            f"""
            SELECT * FROM nodes
            WHERE project_path = ? AND {where}
            ORDER BY confidence DESC, timestamp DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [self._row_to_node(r) for r in rows]

    def get_project_stats(self, project_path: str) -> dict:
        """Get summary stats for a project's IR."""
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) as total_nodes,
                COUNT(DISTINCT session_id) as total_sessions,
                MIN(timestamp) as earliest,
                MAX(timestamp) as latest
            FROM nodes WHERE project_path = ?
            """,
            (project_path,),
        ).fetchone()

        type_counts = self.conn.execute(
            """
            SELECT node_type, COUNT(*) as count
            FROM nodes WHERE project_path = ?
            GROUP BY node_type ORDER BY count DESC
            """,
            (project_path,),
        ).fetchall()

        return {
            "total_nodes": row["total_nodes"],
            "total_sessions": row["total_sessions"],
            "earliest": row["earliest"],
            "latest": row["latest"],
            "by_type": {r["node_type"]: r["count"] for r in type_counts},
        }

    def _row_to_node(self, row: sqlite3.Row) -> IRNode:
        return IRNode(
            id=row["id"],
            session_id=row["session_id"],
            project_path=row["project_path"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            node_type=NodeType(row["node_type"]),
            scope=Scope(row["scope"]),
            summary=row["summary"],
            rationale=row["rationale"],
            alternatives_rejected=json.loads(row["alternatives"]),
            files_affected=json.loads(row["files_affected"]),
            tags=json.loads(row["tags"]),
            parent_node_id=row["parent_node_id"],
            confidence=row["confidence"],
            raw_source=row["raw_source"],
        )
