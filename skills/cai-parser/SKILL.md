---
name: cai-parser
description: Skill for working with the CodeAssist Interceptor parser module (session_parser.py, extractor.py, llm_extractor.py). Use when debugging JSONL parsing failures, improving extraction accuracy, adding extraction signal patterns, modifying classification logic, tuning confidence scores, or working with LLM-assisted extraction. Trigger on: "parser", "extraction", "JSONL", "session_parser", "extractor", "classification", "signal patterns", "confidence threshold", "llm extraction", "Haiku".
---

# CAI Parser Module Skill

Three files, one pipeline: JSONL → messages → classified IRNodes.

## Files

- `src/parser/session_parser.py` — parse raw JSONL into structured messages
- `src/parser/extractor.py` — rule-based classification into IRNode[]
- `src/parser/llm_extractor.py` — optional LLM-assisted classification via Haiku

---

## session_parser.py — JSONL Parsing

### Key Classes

```python
class ContentBlock:
    type: str          # "text" | "thinking" | "tool_use" | "tool_result"
    text: str | None
    name: str | None   # tool name
    input: dict | None # tool inputs
    content: Any | None

class SessionMessage:
    uuid: str
    role: str          # "user" | "assistant"
    content: list[ContentBlock]
    timestamp: datetime
    session_id: str
    
    @property text_content -> str           # joined text blocks
    @property thinking_content -> str       # joined thinking blocks
    @property tool_calls -> list[ContentBlock]    # tool_use blocks only
    @property files_touched -> list[str]    # paths from Read/Edit/Write calls

class ParsedSession:
    session_id: str
    project_path: str
    messages: list[SessionMessage]
    started_at: datetime
    ended_at: datetime
    
    @property assistant_turns -> list[SessionMessage]
    @property all_files_touched -> list[str]       # deduplicated
```

### How Sessions Are Discovered

```python
# Claude Code stores sessions at:
# ~/.claude/projects/<encoded-cwd>/*.jsonl
# where encoded-cwd = cwd with "/" replaced by "-"
# e.g. /home/user/myproject → -home-user-myproject

def discover_sessions(project_path: str) -> list[Path]:
    encoded = project_path.replace("/", "-")
    base = Path.home() / ".claude" / "projects" / encoded
    return sorted(base.glob("*.jsonl"))
```

### Parsing Edge Cases

The parser handles:
- **Session continuation**: multiple `sessionId` values in one file (deduplicates by UUID)
- **Compact summaries**: messages with `"isSidechain": true` — **skip these**, they're synthetic
- **Malformed JSON**: lines that don't parse are silently skipped with a warning
- **Multiple timestamp formats**: ISO 8601 with/without timezone

### When Parsing Fails

1. Check file is not still being written: `wc -c file.jsonl` twice 2s apart — size should be stable
2. Check for compact/sidechain messages: `grep isSidechain session.jsonl`
3. Check UUID deduplication: `grep -c '"uuid"' session.jsonl` vs unique count
4. Enable debug logging: `codeassist --verbose parse /path`

---

## extractor.py — Rule-Based Classification

### Signal Pattern Dictionaries

```python
_ARCHITECTURE_SIGNALS: list[re.Pattern]   # system design patterns
_REJECTION_SIGNALS: list[re.Pattern]      # explicitly dropped approaches
_PATTERN_SIGNALS: list[re.Pattern]        # recurring conventions
_BUGFIX_SIGNALS: list[re.Pattern]         # problem diagnosis & fixes
_DEPENDENCY_SIGNALS: list[re.Pattern]     # library/module relationships
```

### Classification Logic

```python
def _classify_node_type(text: str) -> tuple[NodeType, float]:
    scores: dict[NodeType, float] = defaultdict(float)
    
    for pattern in _ARCHITECTURE_SIGNALS:
        if pattern.search(text):
            scores[NodeType.ARCHITECTURE] += 1.0
    # ... same for other types ...
    
    if not scores:
        return NodeType.IMPLEMENTATION, 0.3  # default fallback
    
    best_type = max(scores, key=scores.get)
    confidence = min(scores[best_type] / 3.0, 1.0)  # normalize
    return best_type, confidence
```

### Adding a Signal Pattern

```python
# Add to the appropriate list:
_ARCHITECTURE_SIGNALS.append(re.compile(r"chose .+ because", re.I))

# For a new signal category, add a new list AND update _classify_node_type:
_MY_SIGNALS: list[re.Pattern] = [
    re.compile(r"my pattern", re.I),
]
# In _classify_node_type():
for pattern in _MY_SIGNALS:
    if pattern.search(text):
        scores[NodeType.MY_TYPE] += 1.0
```

### Tuning Confidence

The normalization `min(score / 3.0, 1.0)` means 3+ signal matches → confidence=1.0.
Adjust the divisor if your signals are sparse (higher = lower confidence per match) or dense (lower = faster saturation).

### Context Chaining

`extract_with_context_chaining()` links consecutive nodes that:
- Share files (same file in `files_affected`)
- Are temporally close (< 5 minutes apart)

This creates `parent_node_id` links for decision sequences.

---

## llm_extractor.py — LLM-Assisted Extraction

### When It Runs

Only for turns with `confidence < llm_threshold` (default 0.65). Low-confidence turns are ambiguous — the LLM adds value there.

### Haiku API Call

```python
# Direct HTTP to Anthropic API, no SDK dependency
# Model: claude-haiku-4-5-20251001
# Batches of 5 turns per call
# Cost: ~$0.001-0.003 per session
# Latency: ~200-500ms per batch
```

### The LLM Prompt

The system prompt asks Haiku to return JSON:
```json
[{
  "node_type": "architecture",
  "scope": "system",
  "summary": "one-line summary",
  "rationale": "why this was done",
  "alternatives_rejected": ["option A", "option B"],
  "tags": ["python", "fastapi"],
  "confidence": 0.85
}]
```

### Merge Strategy

After LLM extraction, results are merged with rule-based by timestamp:
- LLM nodes replace rule-based nodes for the same turn (LLM wins for low-confidence turns)
- High-confidence rule-based nodes (>= llm_threshold) are kept as-is
- Parent chains are re-linked after merge

### Extending LLM Extraction

To add a new NodeType to LLM classification, update the system prompt in `llm_extractor.py`:
```python
SYSTEM_PROMPT = """
...
Node types:
- architecture: ...
- my_new_type: description of when to use this type  ← ADD HERE
...
"""
```

---

## Testing the Parser

```bash
# Parser tests
pytest tests/test_parser.py -v
pytest tests/test_extractor.py -v

# Quick manual test
python -c "
from src.parser.session_parser import parse_jsonl_file, discover_sessions
sessions = discover_sessions('/path/to/project')
print(f'Found {len(sessions)} sessions')
parsed = parse_jsonl_file(sessions[0])
print(f'Messages: {len(parsed.messages)}, Files: {parsed.all_files_touched[:5]}')
"
```

## Common Issues

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| 0 nodes extracted | All turns below confidence threshold | Lower `--min-confidence` or use `--llm` |
| Wrong node type | Missing signal pattern | Add pattern to extractor.py |
| Session not found | Path encoding mismatch | Check `discover_sessions()` output |
| Duplicate nodes | Continuation session with duplicates | Parser deduplicates by UUID — check UUID uniqueness |
| LLM extraction fails | Bad/missing API key | Set `ANTHROPIC_API_KEY` or pass `--api-key` |
