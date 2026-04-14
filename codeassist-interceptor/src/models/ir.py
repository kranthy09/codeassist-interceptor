"""
Intermediate Representation models.

These are the structured nodes that capture reasoning from Claude Code sessions.
Every session response gets classified into one or more of these node types.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """What kind of reasoning this node captures."""

    ARCHITECTURE = "architecture"      # system-level design decisions
    IMPLEMENTATION = "implementation"  # how something was built
    REJECTION = "rejection"            # paths considered and dropped
    DEPENDENCY = "dependency"          # library/module relationships
    PATTERN = "pattern"                # recurring code patterns adopted
    BUGFIX = "bugfix"                  # diagnosis and fix reasoning
    REFACTOR = "refactor"              # why code was restructured
    CONVENTION = "convention"          # style/naming/structure rules
    REASONING = "reasoning"            # pure thinking/analysis blocks


class Scope(str, Enum):
    """Granularity of the decision."""

    SYSTEM = "system"      # affects entire project
    MODULE = "module"      # affects a module/package
    FILE = "file"          # affects a single file
    FUNCTION = "function"  # affects a single function/block


class IRNode(BaseModel):
    """
    Single unit of captured reasoning.

    This is the atom of the IR — one decision, one pattern,
    one rejection. Linked to its session, timestamped, and
    embeddable for semantic retrieval.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str
    project_path: str
    timestamp: datetime
    node_type: NodeType
    scope: Scope
    summary: str                                   # 1-line human readable
    rationale: str                                  # WHY this decision was made
    alternatives_rejected: list[str] = Field(default_factory=list)
    files_affected: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    parent_node_id: Optional[str] = None            # chains decisions together
    confidence: float = 0.8                         # extraction confidence
    embedding: Optional[list[float]] = None         # populated by storage layer
    raw_source: str = ""                            # original text chunk

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class SessionMeta(BaseModel):
    """Metadata about a parsed session."""

    session_id: str
    project_path: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    model_used: str = "unknown"
    total_turns: int = 0
    nodes_extracted: int = 0
    parsed_at: datetime = Field(default_factory=datetime.utcnow)


class ContextRequest(BaseModel):
    """What the retriever receives when Claude Code asks for context."""

    query: str                                      # natural language query
    project_path: str                               # current project
    files_in_scope: list[str] = Field(default_factory=list)
    max_results: int = 10
    recency_weight: float = 0.3                     # 0 = pure semantic, 1 = pure recency
    node_types: Optional[list[NodeType]] = None     # filter by type


class ContextResult(BaseModel):
    """What gets returned to Claude Code."""

    nodes: list[IRNode]
    total_available: int
    query_embedding_time_ms: float = 0
    retrieval_time_ms: float = 0
