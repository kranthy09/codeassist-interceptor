"""
Tests for session_parser.py

Validates JSONL parsing against realistic Claude Code session fixtures:
  - Basic message extraction
  - Content block typing (text, thinking, tool_use)
  - Session continuation handling
  - Compact summary skipping
  - UUID deduplication
  - Timestamp parsing
  - File path extraction from tool calls
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parser.session_parser import parse_jsonl_file
from tests.fixtures import (
    ARCHITECTURE_SESSION,
    BUGFIX_SESSION,
    COMPACT_SUMMARY_SESSION,
    CONTINUATION_SESSION,
    DUPLICATE_UUID_SESSION,
    EMPTY_THINKING_SESSION,
    LOW_SIGNAL_SESSION,
    write_fixture,
)


class TestParseBasicSession:
    """Test basic JSONL parsing mechanics."""

    def test_extracts_user_and_assistant_messages(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        assert len(session.user_turns) == 2
        assert len(session.assistant_turns) == 2

    def test_session_id_from_filename(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        assert session.session_id == "sess-arch-001"

    def test_project_path_decoded(self, tmp_path: Path):
        # create a directory that looks like an encoded project path
        project_dir = tmp_path / "-home-user-myproject"
        project_dir.mkdir()
        path = write_fixture(project_dir, "test-sess", BUGFIX_SESSION)
        session = parse_jsonl_file(path)

        assert "/" in session.project_path  # decoded from directory name

    def test_timestamps_parsed(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        assert session.started_at is not None
        assert session.ended_at is not None
        assert session.started_at <= session.ended_at


class TestContentBlocks:
    """Test extraction of typed content blocks."""

    def test_text_blocks_extracted(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        first_asst = session.assistant_turns[0]
        assert first_asst.text_content != ""
        assert "JWT" in first_asst.text_content

    def test_thinking_blocks_extracted(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        first_asst = session.assistant_turns[0]
        assert "NextAuth" in first_asst.thinking_content

    def test_empty_thinking_handled(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-empty-001", EMPTY_THINKING_SESSION)
        session = parse_jsonl_file(path)

        asst = session.assistant_turns[0]
        assert asst.thinking_content == ""
        assert asst.text_content != ""  # text still present

    def test_tool_calls_extracted(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        first_asst = session.assistant_turns[0]
        tool_calls = first_asst.tool_calls
        assert len(tool_calls) == 2
        assert tool_calls[0].tool_name == "Write"

    def test_files_touched_from_tools(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        first_asst = session.assistant_turns[0]
        files = first_asst.files_touched
        assert "backend/app/core/security.py" in files
        assert "backend/app/schemas/user.py" in files

    def test_all_files_touched_aggregation(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        all_files = session.all_files_touched
        assert len(all_files) >= 3  # files from both assistant turns


class TestEdgeCases:
    """Test edge cases in JSONL parsing."""

    def test_compact_summary_skipped(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-compact-001", COMPACT_SUMMARY_SESSION)
        session = parse_jsonl_file(path)

        # the compact summary message should be excluded
        for msg in session.messages:
            if msg.role == "assistant":
                assert "Summary of previous work" not in msg.text_content

    def test_duplicate_uuids_deduplicated(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-dup-001", DUPLICATE_UUID_SESSION)
        session = parse_jsonl_file(path)

        # should have 2 assistant turns, not 3
        assert len(session.assistant_turns) == 2

    def test_continuation_session_handles_multiple_ids(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-child-001", CONTINUATION_SESSION)
        session = parse_jsonl_file(path)

        # should parse all messages regardless of session ID
        assert len(session.messages) >= 3

    def test_model_extracted_from_system(self, tmp_path: Path):
        path = write_fixture(tmp_path, "sess-arch-001", ARCHITECTURE_SESSION)
        session = parse_jsonl_file(path)

        assert session.model_used == "claude-opus-4-6"

    def test_empty_file_returns_empty_session(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        session = parse_jsonl_file(path)

        assert len(session.messages) == 0

    def test_malformed_json_lines_skipped(self, tmp_path: Path):
        content = "not valid json\n" + BUGFIX_SESSION
        path = write_fixture(tmp_path, "sess-malformed", content)
        session = parse_jsonl_file(path)

        # should still parse the valid lines
        assert len(session.messages) > 0
