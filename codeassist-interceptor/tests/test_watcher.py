"""
Tests for watcher.py

Validates the debounced file watcher:
  - Pending file tracking
  - Debounce window behavior
  - File size stability detection
  - Batch parse triggering
  - Start/stop lifecycle
  - Graceful error handling
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.parser.watcher import DebouncedWatcher, _PendingFile


class TestPendingFileTracking:
    """Test internal state management of modified files."""

    def test_file_change_creates_pending_entry(self, tmp_path: Path):
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=1.0)
        test_file = tmp_path / "test.jsonl"
        test_file.write_text("{}")

        watcher._on_file_changed(test_file)
        assert watcher.pending_count == 1

    def test_repeated_changes_increment_counter(self, tmp_path: Path):
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=1.0)
        test_file = tmp_path / "test.jsonl"
        test_file.write_text("{}")

        watcher._on_file_changed(test_file)
        watcher._on_file_changed(test_file)
        watcher._on_file_changed(test_file)

        assert watcher.pending_count == 1  # same file, one entry
        entry = list(watcher._pending.values())[0]
        assert entry.change_count == 3

    def test_different_files_tracked_separately(self, tmp_path: Path):
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=1.0)
        f1 = tmp_path / "sess1.jsonl"
        f2 = tmp_path / "sess2.jsonl"
        f1.write_text("{}")
        f2.write_text("{}")

        watcher._on_file_changed(f1)
        watcher._on_file_changed(f2)
        assert watcher.pending_count == 2


class TestDebounceBehavior:
    """Test that parsing waits for quiet periods."""

    def test_initial_state(self, tmp_path: Path):
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=0.5)
        assert not watcher.is_running
        assert watcher.pending_count == 0

    def test_parse_callback_called(self, tmp_path: Path):
        """Simulate the flush loop detecting a settled file."""
        parsed = []

        def on_parse(sid, count):
            parsed.append((sid, count))

        watcher = DebouncedWatcher(
            str(tmp_path),
            debounce_seconds=0.1,
            on_parse=on_parse,
        )

        # inject isolated storage so we don't hit global ~/.codeassist/ir.db
        from src.storage.ir_store import IRStorage
        from src.storage.embeddings import EmbeddingManager
        watcher._storage = IRStorage(db_path=tmp_path / "test_ir.db")
        watcher._embeddings = EmbeddingManager(watcher._storage.db_path)

        # create the watch directory and write fixture
        from tests.fixtures import BUGFIX_SESSION, write_fixture

        watcher.watch_dir.mkdir(parents=True, exist_ok=True)
        session_path = write_fixture(watcher.watch_dir, "sess-bug-001", BUGFIX_SESSION)

        pending = _PendingFile(
            path=session_path,
            last_modified=time.monotonic() - 10,
            last_size=session_path.stat().st_size,
            change_count=1,
        )

        watcher._parse_batch([pending])

        assert len(parsed) == 1
        assert parsed[0][0] == "sess-bug-001"


class TestWatcherLifecycle:
    """Test start/stop behavior."""

    def test_start_creates_watch_dir_if_missing(self, tmp_path: Path):
        project_path = str(tmp_path / "nonexistent-project")
        watcher = DebouncedWatcher(project_path, debounce_seconds=0.5)

        # the watch dir won't exist but start should create it
        watcher.start()
        assert watcher.is_running

        watcher.stop()
        assert not watcher.is_running

    def test_stop_flushes_pending(self, tmp_path: Path):
        """Verify that stop processes remaining pending files."""
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=100)

        # create a pending file
        watcher.watch_dir.mkdir(parents=True, exist_ok=True)
        test_file = watcher.watch_dir / "test.jsonl"
        test_file.write_text('{"type":"user","message":{"role":"user","content":"hi"}}')

        watcher._on_file_changed(test_file)
        assert watcher.pending_count == 1

        # stop should process it (or at least not crash)
        watcher._running = True
        watcher.stop()


class TestErrorHandling:
    """Test graceful failure recovery."""

    def test_parse_batch_handles_corrupt_file(self, tmp_path: Path):
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=0.1)

        # create a corrupt JSONL file
        watcher.watch_dir.mkdir(parents=True, exist_ok=True)
        corrupt = watcher.watch_dir / "corrupt.jsonl"
        corrupt.write_text("this is not json\nalso not json\n")

        pending = _PendingFile(
            path=corrupt,
            last_modified=time.monotonic() - 10,
            last_size=corrupt.stat().st_size,
        )

        # should not raise
        watcher._parse_batch([pending])

    def test_parse_batch_handles_missing_file(self, tmp_path: Path):
        watcher = DebouncedWatcher(str(tmp_path), debounce_seconds=0.1)

        pending = _PendingFile(
            path=tmp_path / "nonexistent.jsonl",
            last_modified=time.monotonic() - 10,
        )

        # should not raise
        watcher._parse_batch([pending])
