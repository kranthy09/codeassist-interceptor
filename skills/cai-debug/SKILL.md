---
name: cai-debug
description: Skill for debugging CodeAssist Interceptor issues. Use when sessions aren't being parsed, extraction produces wrong results, MCP tools return empty results, embeddings are missing, the watcher isn't picking up files, or any unexpected behavior. Trigger on: "debug", "not working", "not extracting", "empty results", "session not found", "wrong node type", "MCP not responding", "watcher not picking up", "0 nodes extracted".
---

# CAI Debug Skill

Systematic debugging guide for common CodeAssist Interceptor issues.

## Diagnostic Checklist

Run these in order before deep-diving:

```bash
# 1. Check installation
cd codeassist-interceptor
pip show codeassist-interceptor
python -c "import src; print('Import OK')"

# 2. Check database
sqlite3 ~/.codeassist/ir.db "SELECT count(*) FROM nodes;" 2>/dev/null || echo "DB missing/empty"

# 3. Check sessions exist for the project
python -c "
from src.parser.session_parser import discover_sessions
sessions = discover_sessions('/path/to/your/project')  # ← change this
print(f'Found {len(sessions)} session files:')
for s in sessions[:5]:
    print(f'  {s} ({s.stat().st_size} bytes)')
"

# 4. Try parsing one session manually
python -c "
from src.parser.session_parser import discover_sessions, parse_jsonl_file
from src.parser.extractor import extract_nodes_from_session
sessions = discover_sessions('/path/to/project')
if sessions:
    parsed = parse_jsonl_file(sessions[-1])
    print(f'Messages: {len(parsed.messages)}, Assistant turns: {len(parsed.assistant_turns)}')
    nodes = extract_nodes_from_session(parsed, min_confidence=0.2)
    print(f'Nodes extracted: {len(nodes)}')
    for n in nodes[:5]:
        print(f'  [{n.node_type}] {n.summary[:60]} (conf={n.confidence:.2f})')
"
```

---

## Issue: 0 Nodes Extracted

### Diagnosis
```bash
# Check what's in the session
python -c "
from src.parser.session_parser import parse_jsonl_file
from pathlib import Path
session = parse_jsonl_file(Path('path/to/session.jsonl'))
print('Assistant turns:', len(session.assistant_turns))
for turn in session.assistant_turns[:3]:
    print('---')
    print(turn.text_content[:200])
"
```

### Causes & Fixes

| Cause | Fix |
|-------|-----|
| All turns are sidechain/compact | Parser skips `isSidechain: true` — check session file |
| Confidence below threshold | Lower `--min-confidence 0.2` |
| Session already parsed | Use `codeassist parse --force` |
| Text is too short | Extractor skips turns < ~50 chars |
| Wrong patterns for your project | Review and add signals to `extractor.py` |

---

## Issue: Wrong Node Types

### Diagnosis
```bash
python -c "
from src.parser.extractor import _classify_node_type
# Paste actual text from the session turn
text = '''paste the actual assistant message text here'''
node_type, confidence = _classify_node_type(text)
print(f'Classified as: {node_type} (confidence={confidence:.2f})')
"
```

### Fix
If the text should be `ARCHITECTURE` but classifies as `IMPLEMENTATION`:
1. Check which signals are firing: add `print(scores)` in `_classify_node_type()`
2. Add the missing pattern to `_ARCHITECTURE_SIGNALS`
3. Or lower the competing signal weights

---

## Issue: Session Files Not Found

### Diagnosis
```bash
# Check the encoding
python -c "
import os
project_path = '/home/kranthi/Projects/my-project'
encoded = project_path.replace('/', '-')
base = os.path.expanduser(f'~/.claude/projects/{encoded}')
print('Looking in:', base)
print('Exists:', os.path.exists(base))
if os.path.exists(base):
    files = list(os.scandir(base))
    print(f'Files: {[f.name for f in files]}')
"
```

### Fixes
- Path must match **exactly** what Claude Code uses as `cwd` when run
- Symlinks: use `os.path.realpath()` to resolve before encoding
- Check `~/.claude/projects/` directory for the correct encoded name

---

## Issue: MCP Tools Return Empty

### Diagnosis
```bash
# 1. Check the server starts without error
codeassist serve &
sleep 2
kill %1

# 2. Check nodes exist in DB for the project
python -c "
from src.storage.ir_store import IRStorage
store = IRStorage()
stats = store.get_project_stats('/path/to/project')
print(stats)
"

# 3. Test retrieval directly
python -c "
from src.storage.ir_store import IRStorage
from src.storage.embeddings import EmbeddingManager
from src.retriever.context_retriever import QueryContextRetriever
from src.models.ir import ContextRequest

store = IRStorage()
emb = EmbeddingManager(store)
ret = QueryContextRetriever(store, emb)
result = ret.retrieve(ContextRequest(
    query='test query',
    project_path='/path/to/project',
    max_results=5,
))
print(f'Nodes: {len(result.nodes)}, Time: {result.query_time_ms:.1f}ms')
"
```

### Fixes
- If DB is empty: run `codeassist parse /path/to/project` first
- If project_path mismatch: MCP tool uses `os.getcwd()` by default — call tool with explicit `project_path`
- If embedding errors: check `pip show sentence-transformers`

---

## Issue: Watcher Not Picking Up Files

### Diagnosis
```bash
# Run with verbose and short debounce
codeassist watch /path/to/project --debounce 2.0 -v

# In another terminal, touch a session file:
ls ~/.claude/projects/-path-to-project/
touch ~/.claude/projects/-path-to-project/some-session.jsonl
```

### Fixes
- Path encoding: same issue as session not found (see above)
- File permission: `ls -la ~/.claude/projects/-encoded-path/`
- File not .jsonl: watcher only watches `.jsonl` files
- Debounce too long: reduce with `--debounce 3.0`

---

## Issue: Embeddings Missing

```bash
# Check embedding table
sqlite3 ~/.codeassist/ir.db "
  SELECT count(*) as nodes FROM nodes;
  SELECT count(*) as embeddings FROM node_embeddings;
"
# If nodes > embeddings, some nodes are missing embeddings

# Re-generate embeddings
python -c "
from src.storage.ir_store import IRStorage
from src.storage.embeddings import EmbeddingManager

store = IRStorage()
emb = EmbeddingManager(store)
nodes = store.query_nodes('/path/to/project', limit=1000)
print(f'Encoding {len(nodes)} nodes...')
count = emb.encode_and_store(nodes)
print(f'Stored {count} embeddings')
"
```

---

## Issue: sqlite-vec Not Available

```bash
# Check installation
python -c "import sqlite_vec; print(sqlite_vec.loadable_path())"

# Install
pip install sqlite-vec

# If still failing, check Python version (needs 3.11+)
python --version
```

---

## Issue: LLM Extraction Fails

```bash
# Check API key
echo $ANTHROPIC_API_KEY

# Test API key manually
python -c "
import urllib.request, json
req = urllib.request.Request(
    'https://api.anthropic.com/v1/messages',
    data=json.dumps({
        'model': 'claude-haiku-4-5-20251001',
        'max_tokens': 10,
        'messages': [{'role': 'user', 'content': 'hi'}]
    }).encode(),
    headers={
        'x-api-key': '$ANTHROPIC_API_KEY',
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    },
)
resp = urllib.request.urlopen(req)
print('API OK:', resp.status)
"
```

---

## Debug Mode

Enable detailed logging:
```bash
codeassist --verbose parse /path/to/project
# Or:
export CODEASSIST_LOG_LEVEL=DEBUG
codeassist parse /path/to/project
```

## DB Inspection Commands

```bash
sqlite3 ~/.codeassist/ir.db << 'EOF'
.mode column
.headers on

-- Sessions
SELECT session_id, project_path, nodes_extracted, parsed_at FROM sessions LIMIT 5;

-- Node type distribution
SELECT node_type, count(*) as count FROM nodes GROUP BY node_type ORDER BY count DESC;

-- Recent nodes
SELECT timestamp, node_type, substr(summary, 1, 50) FROM nodes ORDER BY timestamp DESC LIMIT 10;

-- Nodes without embeddings
SELECT count(*) FROM nodes WHERE id NOT IN (SELECT node_id FROM node_embeddings);
EOF
```
