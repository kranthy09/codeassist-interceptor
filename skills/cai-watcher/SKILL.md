---
name: cai-watcher
description: Skill for working with the CodeAssist Interceptor file watcher module (src/parser/watcher.py). Use when debugging auto-parsing issues, modifying debounce behavior, tuning file settle detection, or changing how sessions are automatically picked up. Trigger on: "watcher", "auto-parse", "debounce", "file watching", "DebouncedWatcher", "watch command", "session not picked up automatically".
---

# CAI Watcher Module Skill

Working with `src/parser/watcher.py` — debounced file system monitoring for auto-parsing.

## What the Watcher Does

Claude Code writes JSONL session files incrementally during a session. The watcher:
1. Watches the session directory with `watchdog`
2. Accumulates file modification events (avoiding parse thrashing)
3. Waits for a debounce window with no changes
4. Verifies file size is stable (not still being written)
5. Batches settled files → parse → store → emit callback

## Key Classes

```python
@dataclass
class _PendingFile:
    path: Path
    last_modified: float    # time.time() of last event
    last_size: int          # bytes at last check
    change_count: int       # number of modification events

class DebouncedWatcher:
    def __init__(
        self,
        watch_path: Path,
        debounce_seconds: float = 5.0,   # wait after last change
        on_parsed: Callable | None = None,  # callback(path, nodes, session)
        use_llm: bool = False,
        api_key: str | None = None,
        min_confidence: float = 0.4,
    ): ...
    
    def start(self) -> None        # start observer + flush thread
    def stop(self) -> None         # graceful shutdown, flush pending
    def run_forever(self) -> None  # blocking loop (for CLI)
```

## Debounce Logic

```
File modified event arrives
  → _pending[path] = _PendingFile(path, now, size, count+1)
  
Background flush thread (every 1 second):
  for each pending file:
    if (now - last_modified) >= debounce_seconds:
      check current size == last_size  (file not still growing)
      if stable → add to ready_batch
      else → update last_size, reset timer
  
  if ready_batch:
    → _parse_batch(ready_batch)
    → remove from _pending
```

## Modifying Debounce Behavior

### Change the check interval (currently 1s)

```python
# In _flush_loop():
time.sleep(1.0)  # ← change this
```

### Change file stability check

```python
# In _flush_loop(), the stability check:
current_size = path.stat().st_size
if current_size != pending.last_size:
    pending.last_size = current_size
    pending.last_modified = time.time()  # reset timer
    continue
# ← You could add: check mtime hasn't changed too
```

### Add a minimum file size filter

```python
# In _flush_loop(), before adding to ready_batch:
if current_size < MIN_SESSION_SIZE_BYTES:  # e.g. 1024
    continue  # skip tiny files (likely empty/corrupt)
```

### Change which files are watched

```python
# In _on_file_changed():
if not event.src_path.endswith(".jsonl"):
    return  # ← currently only .jsonl; adjust pattern here
```

## Callback Interface

The `on_parsed` callback receives results per file:

```python
def my_callback(
    path: Path,
    nodes: list[IRNode],
    session: ParsedSession,
) -> None:
    print(f"Parsed {path.name}: {len(nodes)} nodes")
    for node in nodes:
        print(f"  [{node.node_type}] {node.summary}")

watcher = DebouncedWatcher(
    watch_path=sessions_dir,
    on_parsed=my_callback,
    debounce_seconds=3.0,
)
watcher.start()
```

## Integration With Storage

Currently, `_parse_batch()` calls the parser + extractor but does NOT automatically store nodes. The CLI's `watch` command wires storage in the callback. If you want the watcher to auto-store:

```python
# Pattern used in cli.py:
store = IRStorage()
emb = EmbeddingManager(store)

def on_parsed(path, nodes, session):
    if nodes:
        store.upsert_session(session_meta)
        store.store_nodes(nodes)
        emb.encode_and_store(nodes)

watcher = DebouncedWatcher(watch_path, on_parsed=on_parsed)
```

## Debugging the Watcher

```bash
# Run with short debounce and verbose
codeassist watch /path/to/project --debounce 2.0 -v

# Check what session directory is being watched
python -c "
from src.parser.session_parser import discover_sessions
from pathlib import Path
sessions = discover_sessions('/path/to/project')
print('Session dir:', sessions[0].parent if sessions else 'NOT FOUND')
"

# Manually trigger by touching a session file
touch ~/.claude/projects/-home-user-myproject/session.jsonl
```

## Common Issues

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Files never parsed | Wrong watch path | Verify path encoding with `discover_sessions()` |
| Parsed too early | Debounce too short | Increase `--debounce` (default 5s) |
| Same file parsed twice | No `is_session_parsed()` check | Add idempotency check in callback |
| Crash on large file | OOM from sentence-transformers | Use `--min-confidence 0.6` to reduce nodes |
| Watcher stops after error | Unhandled exception in callback | Wrap callback in try/except |

## Testing the Watcher

```bash
pytest tests/test_watcher.py -v

# The watcher tests use a temp directory and mock file events:
# - test_debounce_timing: verifies files settle before parse
# - test_file_stability: verifies size-stable check
# - test_batch_parsing: verifies multiple files batched together
```
