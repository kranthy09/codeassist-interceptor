"""
IR Node extractor.

Takes parsed session messages and extracts structured IRNode decisions.

Two extraction strategies:
1. Rule-based (fast, free) — pattern matching on tool calls and text
2. LLM-assisted (accurate, costs tokens) — uses a small model to classify

The rule-based extractor handles ~70% of cases well. LLM fills in the
nuanced architectural reasoning that patterns can't capture.
"""

from __future__ import annotations

import re

from ..models.ir import IRNode, NodeType, Scope
from .session_parser import ParsedSession, SessionMessage


# ── Pattern matchers for rule-based extraction ─────────────────────

_ARCHITECTURE_SIGNALS = [
    r"(?i)(architect|design|structure|organiz|pattern|approach)",
    r"(?i)(decided to|choosing|went with|opted for|instead of)",
    r"(?i)(separation of|layer|module|component|service)",
]

_REJECTION_SIGNALS = [
    r"(?i)(instead of|rather than|avoided|rejected|didn.t use)",
    r"(?i)(considered but|thought about|alternative|could have)",
    r"(?i)(won.t work|problematic|downside|drawback)",
]

_PATTERN_SIGNALS = [
    r"(?i)(pattern|convention|consistent|standard|always use)",
    r"(?i)(naming convention|file structure|folder structure)",
    r"(?i)(best practice|idiom|approach we.re using)",
]

_BUGFIX_SIGNALS = [
    r"(?i)(bug|fix|issue|error|broken|crash|fail)",
    r"(?i)(root cause|caused by|due to|because of)",
    r"(?i)(the problem was|fixed by|resolved by)",
]

_DEPENDENCY_SIGNALS = [
    r"(?i)(install|import|require|depend|package|library|module)",
    r"(?i)(pip install|npm install|yarn add|added .+ to)",
]


def _score_signals(text: str, patterns: list[str]) -> float:
    """Score how many signal patterns match in text."""
    if not text:
        return 0.0
    matches = sum(1 for p in patterns if re.search(p, text))
    return min(matches / max(len(patterns) * 0.4, 1), 1.0)


def _classify_node_type(text: str) -> tuple[NodeType, float]:
    """Classify text into a node type with confidence score."""
    scores = {
        NodeType.ARCHITECTURE: _score_signals(text, _ARCHITECTURE_SIGNALS),
        NodeType.REJECTION: _score_signals(text, _REJECTION_SIGNALS),
        NodeType.PATTERN: _score_signals(text, _PATTERN_SIGNALS),
        NodeType.BUGFIX: _score_signals(text, _BUGFIX_SIGNALS),
        NodeType.DEPENDENCY: _score_signals(text, _DEPENDENCY_SIGNALS),
    }

    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]

    if best_score < 0.2:
        return NodeType.IMPLEMENTATION, 0.5

    return best_type, min(best_score + 0.3, 1.0)


def _infer_scope(msg: SessionMessage) -> Scope:
    """Infer decision scope from file paths and content."""
    files = msg.files_touched

    if not files:
        text = msg.text_content.lower()
        if any(w in text for w in ("project", "system", "architecture", "overall")):
            return Scope.SYSTEM
        return Scope.MODULE

    # single file → FILE scope, multiple → MODULE, config files → SYSTEM
    config_indicators = (
        "config", "env", "docker", "nginx", "package.json", "pyproject"
    )
    if any(any(c in f.lower() for c in config_indicators) for f in files):
        return Scope.SYSTEM

    if len(files) == 1:
        return Scope.FILE

    # check if all files are in the same directory
    dirs = {
        str(f).rsplit("/", 1)[0] if "/" in str(f) else "."
        for f in files
    }
    return Scope.FILE if len(dirs) == 1 else Scope.MODULE


_FILLER_PATTERNS = [
    # Claude thinking/introspection
    re.compile(
        r"^(now |let me |i('ll| will| can| need to)|here'?s? "
        r"(what|the|a )|looking at|wait |so )", re.I
    ),
    re.compile(
        r"^(let'?s |i think|i should|i need|i want|i'm going to|"
        r"let'?s go)", re.I
    ),
    re.compile(
        r"^(time to |moment to |need to investigate|should|going to) ",
        re.I
    ),
    # Conversation fillers
    re.compile(
        r"^(good|great|ok|sure|right|perfect|alright|excellent|"
        r"wonderful)[\s,.!—-]", re.I
    ),
    re.compile(
        r"^(I (see|understand|have|found|realize|notice|get it)|"
        r"this (is|looks|means|appears))\b", re.I
    ),
    # Empty/meta/self-referential
    re.compile(r"^\.\.\.$"),
    re.compile(
        r"^(no captured reasoning|the (issue|problem|error) is|"
        r"the reason is)", re.I
    ),
    re.compile(
        r"^(for the record|to be clear|in summary|what this means|"
        r"what this shows)\b", re.I
    ),
]

_DECISION_SENTENCE_PATTERNS = [
    re.compile(
        r"(decided|choosing|chose|went with|opted|using|switched to)",
        re.I
    ),
    re.compile(
        r"(instead of|rather than|rejected|avoided|won't use)", re.I
    ),
    re.compile(
        r"(because|since|the reason|this ensures|so that)", re.I
    ),
    re.compile(
        r"(architecture|design|pattern|convention|approach)", re.I
    ),
    re.compile(
        r"(added|created|implemented|configured|set up)\b.*\b"
        r"(for|to|so)", re.I
    ),
]


def _is_filler(sentence: str) -> bool:
    """Check if a sentence is conversational filler, not a decision."""
    s = sentence.strip()
    if len(s) < 15:
        return True
    return any(p.search(s) for p in _FILLER_PATTERNS)


def _extract_summary(text: str, max_len: int = 120) -> str:
    """Extract a clean 1-line summary from response text.

    Skips filler sentences ("Now I have the full picture", "Let me
    check...") and prefers sentences that contain decision-making language.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    # Pass 1: find a sentence with decision language
    for s in sentences:
        s = s.strip()
        if len(s) < 20 or s.startswith("```") or _is_filler(s):
            continue
        if any(p.search(s) for p in _DECISION_SENTENCE_PATTERNS):
            suffix = "..." if len(s) > max_len else ""
            return s[:max_len] + suffix

    # Pass 2: first non-filler sentence with substance
    for s in sentences:
        s = s.strip()
        if (len(s) > 30 and not s.startswith("```") and
                not _is_filler(s)):
            suffix = "..." if len(s) > max_len else ""
            return s[:max_len] + suffix

    # Pass 3: anything over 20 chars
    for s in sentences:
        s = s.strip()
        if len(s) > 20 and not s.startswith("```"):
            suffix = "..." if len(s) > max_len else ""
            return s[:max_len] + suffix

    return text[:max_len].strip() + "..."


def _extract_tags(text: str, files: list[str]) -> list[str]:
    """Extract relevant tags from text content and file paths."""
    tags = set()

    # from file extensions
    for f in files:
        ext = f.rsplit(".", 1)[-1] if "." in f else ""
        allowed_exts = ("py", "ts", "tsx", "js", "jsx", "css", "sql", "md")
        if ext in allowed_exts:
            tags.add(ext)

    # from content
    tech_patterns = [
        (r"(?i)\b(react|next\.?js|vue|angular)\b", "frontend"),
        (r"(?i)\b(fastapi|django|flask|express)\b", "backend"),
        (r"(?i)\b(postgres|sqlite|mongodb|redis)\b", "database"),
        (r"(?i)\b(docker|nginx|kubernetes|ci/cd)\b", "infrastructure"),
        (r"(?i)\b(test|spec|jest|pytest)\b", "testing"),
        (r"(?i)\b(auth|jwt|oauth|security)\b", "security"),
        (r"(?i)\b(api|rest|graphql|endpoint)\b", "api"),
    ]
    for pattern, tag in tech_patterns:
        if re.search(pattern, text):
            tags.add(tag)

    return sorted(tags)[:8]


# ── Main extraction pipeline ──────────────────────────────────────

def extract_nodes_from_session(
    session: ParsedSession,
    min_confidence: float = 0.4,
) -> list[IRNode]:
    """
    Extract IR nodes from a parsed session using rule-based classification.

    Processes each assistant turn, scores it against signal patterns,
    and emits structured nodes for decisions above the confidence threshold.
    Filters out turns that are purely conversational or procedural.
    """
    nodes: list[IRNode] = []

    for msg in session.assistant_turns:
        text = msg.text_content
        thinking = msg.thinking_content

        # combine text + thinking for classification
        full_text = f"{thinking}\n{text}".strip()
        if len(full_text) < 50:
            continue

        # If we have substantial thinking but no/minimal text,
        # mark as REASONING to preserve Claude's analysis for reuse
        is_pure_thinking = (
            thinking and len(thinking) > 100 and
            (not text or len(text) < 50)
        )

        node_type, confidence = _classify_node_type(full_text)
        # Override to REASONING if it's a pure thinking block
        if is_pure_thinking:
            node_type = NodeType.REASONING
            confidence = min(confidence, 0.9)  # cap confidence
        if confidence < min_confidence:
            continue

        # Try text first, fall back to thinking if text is empty/filler
        summary = _extract_summary(text) if text else ""
        if not summary or _is_filler(summary):
            summary = _extract_summary(thinking)

        # Capture full rationale from thinking (preserves Claude's reasoning)
        # Max 2000 chars to preserve detailed reasoning blocks
        rationale = _extract_summary(
            thinking or text, max_len=2000
        )

        # Skip nodes where summary extraction found nothing meaningful
        if summary in ("...", "") or len(summary) < 15:
            continue
        # Skip filler/introspection regardless of node type
        if _is_filler(summary):
            continue

        # extract alternatives for rejection nodes
        alternatives = []
        if node_type == NodeType.REJECTION:
            alt_matches = re.findall(
                r"(?i)(?:instead of|rather than|not using)\s+(.+?)(?:\.|,|$)",
                full_text
            )
            alternatives = [m.strip() for m in alt_matches[:5]]

        node = IRNode(
            session_id=session.session_id,
            project_path=session.project_path,
            timestamp=msg.timestamp,
            node_type=node_type,
            scope=_infer_scope(msg),
            summary=summary,
            rationale=rationale,
            alternatives_rejected=alternatives,
            files_affected=msg.files_touched,
            tags=_extract_tags(full_text, msg.files_touched),
            confidence=confidence,
            raw_source=full_text[:2000],
        )

        nodes.append(node)

    return nodes


def extract_with_context_chaining(
    session: ParsedSession,
    min_confidence: float = 0.4,
) -> list[IRNode]:
    """
    Extract nodes with parent-child chaining.

    When consecutive assistant messages build on each other
    (same files, same topic), link them as a decision chain.
    """
    nodes = extract_nodes_from_session(session, min_confidence)

    for i in range(1, len(nodes)):
        prev = nodes[i - 1]
        curr = nodes[i]

        # chain if they share files or are close in time
        shared_files = set(prev.files_affected) & set(curr.files_affected)
        time_gap = (curr.timestamp - prev.timestamp).total_seconds()

        if shared_files or time_gap <= 180:
            curr.parent_node_id = prev.id

    return nodes
