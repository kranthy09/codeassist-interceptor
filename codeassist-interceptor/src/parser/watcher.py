"""
Debounced session watcher.

Monitors ~/.claude/projects/<encoded-path>/ for JSONL file changes
and triggers parsing after a quiet period (debounce). This prevents
thrashing when Claude Code writes multiple lines per second during
an active session.

Features:
  - Configurable debounce window (default 5s)
  - Batches multiple file changes into a single parse run
  - Skips files that are still being actively written to
  - Tracks file sizes to detect when a session is "settled"
  - Graceful error recovery — never crashes the watcher
  - Optional LLM-assisted extraction for ambiguous decisions
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from watchdog.observers import Observer

from ..models.ir import SessionMeta
from ..parser.extractor import extract_with_context_chaining
from ..parser.session_parser import parse_jsonl_file
from ..storage.embeddings import EmbeddingManager
from ..storage.ir_store import IRStorage

logger = logging.getLogger(__name__)


@dataclass
class _PendingFile:
    """Tracks a file that's been modified but not yet parsed."""

    path: Path
    last_modified: float          # monotonic timestamp of last change
    last_size: int = 0            # file size at last check
    change_count: int = 0         # how many times we've seen it change


class DebouncedWatcher:
    """
    Watches a directory for JSONL changes and auto-parses after quiet periods.

    The watcher accumulates file modification events, waits for a debounce
    window with no new changes, then triggers a batch parse of all pending
    files. Files that are still being written to (size changing) get deferred
    to the next cycle.
    """

    def __init__(
        self,
        project_path: str,
        debounce_seconds: float = 5.0,
        use_llm: bool = False,
        api_key: Optional[str] = None,
        min_confidence: float = 0.4,
        on_parse: Optional[Callable[[str, int], None]] = None,
    ):
        self.project_path = str(Path(project_path).resolve())
        self.debounce_seconds = debounce_seconds
        self.use_llm = use_llm
        self.api_key = api_key
        self.min_confidence = min_confidence
        self.on_parse = on_parse  # callback(session_id, node_count)

        self._pending: dict[str, _PendingFile] = {}
        self._lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._running = False
        self._observer: Optional[Observer] = None

        # resolve watch directory
        encoded = self.project_path.replace("/", "-")
        self.watch_dir = Path.home() / ".claude" / "projects" / encoded

        # storage components (lazy-initialized on first parse)
        self._storage: Optional[IRStorage] = None
        self._embeddings: Optional[EmbeddingManager] = None

    def _get_storage(self) -> tuple[IRStorage, EmbeddingManager]:
        if self._storage is None:
            self._storage = IRStorage()
            self._embeddings = EmbeddingManager(self._storage.db_path)
        return self._storage, self._embeddings

    # ── Event handler ─────────────────────────────────────────────

    class _Handler(FileSystemEventHandler):
        def __init__(self, watcher: DebouncedWatcher):
            self.watcher = watcher

        def on_modified(self, event: FileModifiedEvent):
            if not event.src_path.endswith(".jsonl"):
                return
            self.watcher._on_file_changed(Path(event.src_path))

        def on_created(self, event):
            if not event.src_path.endswith(".jsonl"):
                return
            self.watcher._on_file_changed(Path(event.src_path))

    def _on_file_changed(self, path: Path) -> None:
        """Record a file change event."""
        with self._lock:
            key = str(path)
            if key in self._pending:
                entry = self._pending[key]
                entry.last_modified = time.monotonic()
                entry.change_count += 1
            else:
                self._pending[key] = _PendingFile(
                    path=path,
                    last_modified=time.monotonic(),
                    change_count=1,
                )
                logger.debug(f"New pending file: {path.stem[:12]}…")

    # ── Flush loop ────────────────────────────────────────────────

    def _flush_loop(self) -> None:
        """Background thread that checks for settled files and parses them."""
        while self._running:
            time.sleep(1.0)  # check every second

            ready_files: list[_PendingFile] = []

            with self._lock:
                now = time.monotonic()
                settled_keys = []

                for key, entry in self._pending.items():
                    age = now - entry.last_modified

                    # check if debounce window has passed
                    if age < self.debounce_seconds:
                        continue

                    # check if file size is stable (not still being written)
                    try:
                        current_size = entry.path.stat().st_size
                    except OSError:
                        settled_keys.append(key)
                        continue

                    if current_size != entry.last_size:
                        # size changed — reset the debounce timer
                        entry.last_size = current_size
                        entry.last_modified = now
                        logger.debug(
                            f"File still growing: {entry.path.stem[:12]}… "
                            f"({current_size} bytes)"
                        )
                        continue

                    # file is settled — ready to parse
                    ready_files.append(entry)
                    settled_keys.append(key)

                for key in settled_keys:
                    self._pending.pop(key, None)

            # parse settled files outside the lock
            if ready_files:
                self._parse_batch(ready_files)

    def _parse_batch(self, files: list[_PendingFile]) -> None:
        """Parse a batch of settled session files."""
        storage, embeddings = self._get_storage()

        for entry in files:
            session_id = entry.path.stem

            if storage.is_session_parsed(session_id) and entry.change_count < 3:
                # skip if already parsed and not heavily modified
                # (change_count >= 3 means significant new content)
                logger.debug(f"Skip {session_id[:12]}… (already parsed, minimal changes)")
                continue

            try:
                logger.info(f"Parsing {session_id[:12]}…")
                session = parse_jsonl_file(entry.path)
                session.project_path = self.project_path  # canonical path, not lossy decode

                # choose extraction strategy
                if self.use_llm and self.api_key:
                    from ..parser.llm_extractor import extract_with_llm
                    nodes = extract_with_llm(
                        session,
                        min_confidence=self.min_confidence,
                        api_key=self.api_key,
                    )
                else:
                    nodes = extract_with_context_chaining(
                        session, self.min_confidence
                    )

                node_count = 0
                if nodes:
                    node_count = storage.store_nodes(nodes)
                    embeddings.encode_and_store(nodes)

                # record session metadata
                meta = SessionMeta(
                    session_id=session.session_id,
                    project_path=session.project_path,
                    started_at=session.started_at or datetime.utcnow(),
                    ended_at=session.ended_at,
                    model_used=session.model_used,
                    total_turns=len(session.messages),
                    nodes_extracted=len(nodes),
                )
                storage.upsert_session(meta)

                logger.info(
                    f"Parsed {session_id[:12]}… → {node_count} nodes"
                )

                if self.on_parse:
                    self.on_parse(session_id, node_count)

            except Exception as e:
                logger.error(
                    f"Failed to parse {session_id[:12]}…: {e}",
                    exc_info=True,
                )

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Start watching for session file changes."""
        if not self.watch_dir.exists():
            self.watch_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created watch directory: {self.watch_dir}")

        self._running = True

        # start the flush thread
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="codeassist-flush"
        )
        self._flush_thread.start()

        # start the file system observer
        self._observer = Observer()
        self._observer.schedule(
            self._Handler(self), str(self.watch_dir), recursive=False
        )
        self._observer.start()

        logger.info(
            f"Watching {self.watch_dir} "
            f"(debounce={self.debounce_seconds}s, llm={self.use_llm})"
        )

    def stop(self) -> None:
        """Stop watching and clean up resources."""
        self._running = False

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

        if self._flush_thread:
            self._flush_thread.join(timeout=5)

        # flush any remaining pending files
        remaining = list(self._pending.values())
        if remaining:
            logger.info(f"Flushing {len(remaining)} pending files before exit")
            self._parse_batch(remaining)

        if self._storage:
            self._storage.close()
        if self._embeddings:
            self._embeddings.close()

        logger.info("Watcher stopped")

    def run_forever(self) -> None:
        """Start and block until interrupted."""
        self.start()
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def is_running(self) -> bool:
        return self._running
