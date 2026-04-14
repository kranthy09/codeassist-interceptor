"""
JSONL session file parser.

Reads Claude Code session files from ~/.claude/projects/<encoded-path>/*.jsonl
and extracts structured message blocks for downstream IR extraction.

Session file format (per line):
  - type: "user" | "assistant" | "system"
  - sessionId, timestamp, uuid, parentUuid
  - message.role, message.content (array of blocks for assistant)

Content block types in assistant messages:
  - text: natural language response
  - thinking: reasoning (may be empty in newer versions)
  - tool_use: tool invocation with name + input
  - tool_result: result from tool execution
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class ContentBlock:
    """Single content block from an assistant response."""

    block_type: str          # text | thinking | tool_use | tool_result
    content: str             # text content or tool input/output
    tool_name: str = ""      # only for tool_use blocks
    tool_input: dict = field(default_factory=dict)


@dataclass
class SessionMessage:
    """One turn in a session conversation."""

    role: str                # user | assistant | system
    timestamp: datetime
    session_id: str
    uuid: str
    parent_uuid: str = ""
    blocks: list[ContentBlock] = field(default_factory=list)
    raw_content: str = ""    # original text for user messages

    @property
    def text_content(self) -> str:
        """All text blocks concatenated."""
        return "\n".join(
            b.content for b in self.blocks if b.block_type == "text"
        )

    @property
    def thinking_content(self) -> str:
        """All thinking blocks concatenated."""
        return "\n".join(
            b.content for b in self.blocks
            if b.block_type == "thinking" and b.content
        )

    @property
    def tool_calls(self) -> list[ContentBlock]:
        """All tool use blocks."""
        return [b for b in self.blocks if b.block_type == "tool_use"]

    @property
    def files_touched(self) -> list[str]:
        """Extract file paths from tool calls (Read, Edit, Write)."""
        paths = []
        for tc in self.tool_calls:
            if tc.tool_name in ("Read", "Edit", "Write", "MultiEdit"):
                file_path = tc.tool_input.get("file_path", "")
                if file_path:
                    paths.append(file_path)
        return paths


@dataclass
class ParsedSession:
    """Complete parsed session with metadata."""

    session_id: str
    project_path: str
    source_file: Path
    messages: list[SessionMessage] = field(default_factory=list)
    model_used: str = "unknown"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    @property
    def assistant_turns(self) -> list[SessionMessage]:
        return [m for m in self.messages if m.role == "assistant"]

    @property
    def user_turns(self) -> list[SessionMessage]:
        return [m for m in self.messages if m.role == "user"]

    @property
    def all_files_touched(self) -> set[str]:
        return {
            f for m in self.assistant_turns for f in m.files_touched
        }


def _parse_content_blocks(content: list | str) -> list[ContentBlock]:
    """Parse assistant content array into typed blocks."""
    if isinstance(content, str):
        return [ContentBlock(block_type="text", content=content)]

    blocks = []
    for item in content:
        if not isinstance(item, dict):
            continue

        btype = item.get("type", "text")

        if btype == "text":
            blocks.append(ContentBlock(
                block_type="text",
                content=item.get("text", ""),
            ))
        elif btype == "thinking":
            blocks.append(ContentBlock(
                block_type="thinking",
                content=item.get("thinking", ""),
            ))
        elif btype == "tool_use":
            blocks.append(ContentBlock(
                block_type="tool_use",
                content=json.dumps(item.get("input", {})),
                tool_name=item.get("name", ""),
                tool_input=item.get("input", {}),
            ))
        elif btype == "tool_result":
            result_content = item.get("content", "")
            if isinstance(result_content, list):
                result_content = "\n".join(
                    r.get("text", "") for r in result_content
                    if isinstance(r, dict)
                )
            blocks.append(ContentBlock(
                block_type="tool_result",
                content=str(result_content),
            ))

    return blocks


def _parse_timestamp(ts: str) -> datetime:
    """Parse ISO timestamp, handling various formats."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_jsonl_file(file_path: Path) -> ParsedSession:
    """
    Parse a single JSONL session file into structured messages.

    Handles:
    - Session continuation (multiple session IDs in one file)
    - Compact boundaries (duplicate prefix from parent sessions)
    - System metadata events
    """
    session_id = file_path.stem
    project_encoded = file_path.parent.name
    project_path = project_encoded.replace("-", "/")

    session = ParsedSession(
        session_id=session_id,
        project_path=project_path,
        source_file=file_path,
    )

    seen_uuids: set[str] = set()

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = record.get("type", "")
            msg_uuid = record.get("uuid", "")

            # skip duplicates from session continuation
            if msg_uuid and msg_uuid in seen_uuids:
                continue
            if msg_uuid:
                seen_uuids.add(msg_uuid)

            # skip compact summaries (synthetic, not real conversation)
            if record.get("isCompactSummary"):
                continue

            timestamp = _parse_timestamp(
                record.get("timestamp", datetime.utcnow().isoformat())
            )

            if rtype in ("user", "assistant"):
                message_data = record.get("message", {})
                content = message_data.get("content", "")

                msg = SessionMessage(
                    role=rtype,
                    timestamp=timestamp,
                    session_id=record.get("sessionId", session_id),
                    uuid=msg_uuid,
                    parent_uuid=record.get("parentUuid", ""),
                )

                if rtype == "user":
                    msg.raw_content = content if isinstance(content, str) else json.dumps(content)
                    msg.blocks = [ContentBlock(block_type="text", content=msg.raw_content)]
                else:
                    msg.blocks = _parse_content_blocks(content)

                session.messages.append(msg)

                # track time bounds
                if not session.started_at or timestamp < session.started_at:
                    session.started_at = timestamp
                if not session.ended_at or timestamp > session.ended_at:
                    session.ended_at = timestamp

            elif rtype == "system":
                # extract model info if present
                model = record.get("model", "")
                if model:
                    session.model_used = model

    return session


def discover_sessions(project_path: str) -> Iterator[Path]:
    """
    Find all JSONL session files for a project.

    Claude Code stores sessions at:
    ~/.claude/projects/<encoded-cwd>/*.jsonl

    The encoding replaces / with - in the path. Some Claude Code versions
    also replace underscores and other non-alphanumeric chars with dashes.
    We try exact match first, then variants, then a fuzzy scan.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return

    # Try multiple encoding strategies — Claude Code's encoding has varied
    candidates = set()
    # Strategy 1: replace only /
    candidates.add(project_path.replace("/", "-"))
    # Strategy 2: replace / and _ (observed in some Claude Code versions)
    candidates.add(project_path.replace("/", "-").replace("_", "-"))

    for encoded in candidates:
        project_dir = claude_dir / encoded
        if project_dir.exists():
            sessions = sorted(project_dir.glob("*.jsonl"))
            if sessions:
                yield from sessions
                return

    # Fallback: fuzzy scan — normalize both sides to dashes and compare
    normalized_path = project_path.replace("/", "-").replace("_", "-").lower()
    for d in claude_dir.iterdir():
        if d.is_dir():
            normalized_dir = d.name.replace("_", "-").lower()
            if normalized_path == normalized_dir or normalized_path in normalized_dir:
                yield from sorted(d.glob("*.jsonl"))
