"""
LLM-assisted IR extraction.

Uses Claude Haiku (claude-haiku-4-5-20251001) via Anthropic API for
high-quality classification of session content that the rule-based
extractor can't confidently categorize.

Strategy:
  1. Rule-based extractor runs first (free, fast)
  2. Turns with confidence < threshold get sent to LLM
  3. LLM returns structured JSON with type, summary, rationale
  4. Results merged, LLM nodes marked with higher confidence

Cost: ~$0.001-0.003 per session (Haiku is extremely cheap)
Latency: ~200-500ms per batch (batches of 5 turns)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from ..models.ir import IRNode, NodeType, Scope
from .session_parser import ParsedSession, SessionMessage
from .extractor import (
    _classify_node_type,
    _extract_tags,
    _infer_scope,
    extract_with_context_chaining,
)

logger = logging.getLogger(__name__)

# ── Prompt template ───────────────────────────────────────────────

_SYSTEM_PROMPT = """You classify AI coding assistant responses into structured decision nodes.

For each assistant message, determine:
1. node_type: one of: architecture, implementation, rejection, dependency, pattern, bugfix, refactor, convention
2. scope: one of: system, module, file, function
3. summary: 1 sentence (max 120 chars) describing WHAT was decided
4. rationale: 1-2 sentences (max 300 chars) explaining WHY
5. alternatives_rejected: list of approaches that were considered but dropped (empty if none)
6. is_decision: boolean — false if the message is just executing code without any reasoning/decision

Respond ONLY with a JSON array. No markdown, no explanation.

Node type definitions:
- architecture: system-level design choices (patterns, structure, technology selection)
- implementation: how something was built (approach, algorithm, data flow)
- rejection: explicitly choosing NOT to do something
- dependency: adding/removing/choosing libraries or modules
- pattern: establishing or following a recurring code convention
- bugfix: diagnosing and fixing a problem
- refactor: restructuring existing code
- convention: naming, style, or structural rules"""

_USER_TEMPLATE = """Classify these assistant messages. Each has an index, the user's prompt, and the assistant's response.

{messages}

Return a JSON array with one object per message:
[{{"index": 0, "is_decision": true, "node_type": "architecture", "scope": "module", "summary": "...", "rationale": "...", "alternatives_rejected": []}}]"""


# ── API client ────────────────────────────────────────────────────

def _call_haiku(messages_text: str, api_key: str) -> Optional[list[dict]]:
    """
    Call Claude Haiku for classification.

    Uses raw HTTP to avoid heavy SDK dependency.
    Falls back gracefully on any failure.
    """
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 2000,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": messages_text}
        ],
    })

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        # extract text from response content
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        # parse JSON from response (strip any markdown fences)
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        return json.loads(text)

    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Haiku API call failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error calling Haiku: {e}")
        return None


# ── Batch preparation ─────────────────────────────────────────────

def _prepare_batch(
    turns: list[tuple[int, SessionMessage, SessionMessage]],
) -> str:
    """Format a batch of (user, assistant) turn pairs for the LLM."""
    parts = []
    for idx, (orig_idx, user_msg, asst_msg) in enumerate(turns):
        user_text = user_msg.raw_content[:500] if user_msg else "(no user prompt)"
        asst_text = asst_msg.text_content[:1500]
        thinking = asst_msg.thinking_content[:500]

        block = f"--- Message {idx} ---\nUser: {user_text}\n"
        if thinking:
            block += f"Thinking: {thinking}\n"
        block += f"Assistant: {asst_text}\n"
        parts.append(block)

    return "\n".join(parts)


# ── Main extraction pipeline ─────────────────────────────────────

def extract_with_llm(
    session: ParsedSession,
    min_confidence: float = 0.4,
    llm_threshold: float = 0.65,
    api_key: Optional[str] = None,
    batch_size: int = 5,
) -> list[IRNode]:
    """
    Extract IR nodes using hybrid rule-based + LLM approach.

    1. Run rule-based extraction on all turns
    2. Identify turns where confidence < llm_threshold
    3. Send ambiguous turns to Haiku in batches
    4. Merge LLM results with rule-based results

    Args:
        session: parsed session data
        min_confidence: minimum confidence to keep a node
        llm_threshold: below this confidence, escalate to LLM
        api_key: Anthropic API key (falls back to ANTHROPIC_API_KEY env)
        batch_size: how many turns to send per LLM call
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    # Step 1: rule-based extraction
    rule_nodes = extract_with_context_chaining(session, min_confidence)

    if not api_key:
        logger.info("No API key — returning rule-based results only")
        return rule_nodes

    # Step 2: find ambiguous turns (low confidence from rule-based)
    # Build a map of which messages already have confident nodes
    confident_uuids = set()
    ambiguous_indices: list[int] = []

    all_turns = session.assistant_turns
    for node in rule_nodes:
        if node.confidence >= llm_threshold:
            # find the message this node came from
            for msg in all_turns:
                if msg.timestamp == node.timestamp:
                    confident_uuids.add(msg.uuid)

    # collect turns that need LLM help
    user_turns = session.user_turns
    turn_pairs: list[tuple[int, SessionMessage, SessionMessage]] = []

    for i, asst_msg in enumerate(all_turns):
        if asst_msg.uuid in confident_uuids:
            continue
        if len(asst_msg.text_content) < 100:
            continue

        # find the preceding user message
        user_msg = None
        for u in reversed(user_turns):
            if u.timestamp < asst_msg.timestamp:
                user_msg = u
                break

        turn_pairs.append((i, user_msg, asst_msg))

    if not turn_pairs:
        logger.info("All turns confidently classified — skipping LLM")
        return rule_nodes

    logger.info(
        f"Sending {len(turn_pairs)} ambiguous turns to Haiku "
        f"(in {(len(turn_pairs) + batch_size - 1) // batch_size} batches)"
    )

    # Step 3: batch and send to LLM
    llm_nodes: list[IRNode] = []

    for batch_start in range(0, len(turn_pairs), batch_size):
        batch = turn_pairs[batch_start:batch_start + batch_size]
        prompt = _USER_TEMPLATE.format(messages=_prepare_batch(batch))

        results = _call_haiku(prompt, api_key)
        if not results:
            continue

        for result in results:
            if not isinstance(result, dict):
                continue
            if not result.get("is_decision", False):
                continue

            idx = result.get("index", 0)
            if idx >= len(batch):
                continue

            orig_idx, user_msg, asst_msg = batch[idx]

            # validate node_type
            try:
                node_type = NodeType(result.get("node_type", "implementation"))
            except ValueError:
                node_type = NodeType.IMPLEMENTATION

            try:
                scope = Scope(result.get("scope", "module"))
            except ValueError:
                scope = _infer_scope(asst_msg)

            node = IRNode(
                session_id=session.session_id,
                project_path=session.project_path,
                timestamp=asst_msg.timestamp,
                node_type=node_type,
                scope=scope,
                summary=result.get("summary", "")[:120],
                rationale=result.get("rationale", "")[:300],
                alternatives_rejected=result.get("alternatives_rejected", []),
                files_affected=asst_msg.files_touched,
                tags=_extract_tags(asst_msg.text_content, asst_msg.files_touched),
                confidence=0.85,  # LLM classifications get baseline 0.85
                raw_source=asst_msg.text_content[:2000],
            )

            llm_nodes.append(node)

    logger.info(f"LLM extracted {len(llm_nodes)} additional nodes")

    # Step 4: merge — rule-based nodes + LLM nodes, deduplicated by timestamp
    existing_timestamps = {n.timestamp for n in rule_nodes}
    for ln in llm_nodes:
        if ln.timestamp not in existing_timestamps:
            rule_nodes.append(ln)

    # re-sort by timestamp
    rule_nodes.sort(key=lambda n: n.timestamp)

    # re-chain parent relationships
    for i in range(1, len(rule_nodes)):
        prev = rule_nodes[i - 1]
        curr = rule_nodes[i]
        shared = set(prev.files_affected) & set(curr.files_affected)
        gap = (curr.timestamp - prev.timestamp).total_seconds()
        if shared or gap < 120:
            curr.parent_node_id = prev.id

    return rule_nodes
