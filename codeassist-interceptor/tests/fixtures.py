"""
Test fixtures — realistic JSONL session data.

These fixtures match Claude Code's actual session file format:
  - type: user/assistant/system
  - sessionId, timestamp, uuid, parentUuid
  - message.content as array of typed blocks for assistant messages
  - thinking blocks (empty string for Claude 4+ models)
  - tool_use blocks with Read/Edit/Write operations

Each fixture represents a different scenario to test extraction quality.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Generator

import pytest


# ── JSONL builders ────────────────────────────────────────────────

def _user(sid: str, uuid: str, parent: str, ts: str, content: str) -> str:
    return json.dumps({
        "type": "user",
        "sessionId": sid,
        "timestamp": ts,
        "uuid": uuid,
        "parentUuid": parent,
        "message": {"role": "user", "content": content},
    })


def _assistant(
    sid: str, uuid: str, parent: str, ts: str,
    text: str = "",
    thinking: str = "",
    tool_uses: list[dict] | None = None,
) -> str:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "EoAC..."})
    if text:
        content.append({"type": "text", "text": text})
    for tu in (tool_uses or []):
        content.append({
            "type": "tool_use",
            "name": tu["name"],
            "input": tu.get("input", {}),
            "id": tu.get("id", "tool_001"),
        })
    return json.dumps({
        "type": "assistant",
        "sessionId": sid,
        "timestamp": ts,
        "uuid": uuid,
        "parentUuid": parent,
        "message": {"role": "assistant", "content": content},
    })


def _system(sid: str, ts: str, model: str = "") -> str:
    return json.dumps({
        "type": "system",
        "sessionId": sid,
        "timestamp": ts,
        "model": model,
    })


# ── Fixture: architecture session ─────────────────────────────────

ARCHITECTURE_SESSION = "\n".join([
    _system("sess-arch-001", "2026-04-01T10:00:00.000Z", model="claude-opus-4-6"),

    _user("sess-arch-001", "u1", "", "2026-04-01T10:00:05.000Z",
          "Help me set up the authentication system for this Next.js app"),

    _assistant("sess-arch-001", "a1", "u1", "2026-04-01T10:00:30.000Z",
        thinking="I need to decide between NextAuth.js and a custom JWT implementation. "
                 "NextAuth is easier but less flexible. Custom JWT gives us full control "
                 "over the token structure and session management.",
        text="I'll architect the auth system using a custom JWT approach instead of NextAuth.js. "
             "The separation of concerns here matters — we want the auth logic in the FastAPI "
             "backend rather than the Next.js layer. This gives us a clean API-first design "
             "where the frontend is stateless and all auth decisions happen server-side. "
             "I opted for bcrypt password hashing with 7-day token expiry.",
        tool_uses=[
            {"name": "Write", "input": {"file_path": "backend/app/core/security.py"}},
            {"name": "Write", "input": {"file_path": "backend/app/schemas/user.py"}},
        ],
    ),

    _user("sess-arch-001", "u2", "a1", "2026-04-01T10:02:00.000Z",
          "Good. Now set up the database models for users"),

    _assistant("sess-arch-001", "a2", "u2", "2026-04-01T10:02:30.000Z",
        text="I'm using SQLAlchemy 2.0 with the declarative pattern. The User model "
             "has email as a unique constraint with a GIN index for fast lookups. "
             "I considered using an ORM-less approach with raw SQL but decided against it — "
             "SQLAlchemy gives us migration support via Alembic and type safety.",
        tool_uses=[
            {"name": "Write", "input": {"file_path": "backend/app/models/user.py"}},
            {"name": "Edit", "input": {"file_path": "backend/app/database.py"}},
        ],
    ),
])


# ── Fixture: bugfix session ───────────────────────────────────────

BUGFIX_SESSION = "\n".join([
    _system("sess-bug-001", "2026-04-02T14:00:00.000Z", model="claude-sonnet-4-6"),

    _user("sess-bug-001", "u1", "", "2026-04-02T14:00:05.000Z",
          "The SSL certificate isn't working on the production server"),

    _assistant("sess-bug-001", "a1", "u1", "2026-04-02T14:00:20.000Z",
        thinking="Let me check the nginx config and certbot setup. The issue is likely "
                 "either the certificate path or the nginx SSL directive pointing to wrong files.",
        text="I found the root cause — the nginx config was pointing to the wrong certificate "
             "path. The certbot auto-renewal created certificates at /etc/letsencrypt/live/domain/ "
             "but nginx.conf was referencing /etc/ssl/certs/. Fixed by updating the ssl_certificate "
             "and ssl_certificate_key directives. Also added a cron job for auto-renewal.",
        tool_uses=[
            {"name": "Edit", "input": {"file_path": "nginx/nginx.conf"}},
        ],
    ),
])


# ── Fixture: refactor session ─────────────────────────────────────

REFACTOR_SESSION = "\n".join([
    _user("sess-ref-001", "u1", "", "2026-04-03T09:00:00.000Z",
          "Refactor the content service to support multiple content types"),

    _assistant("sess-ref-001", "a1", "u1", "2026-04-03T09:00:45.000Z",
        text="I'm restructuring the content service layer to use a strategy pattern. "
             "Each content type (blog, project, case-study) gets its own validator and "
             "renderer instead of a monolithic if-else chain. This makes adding new "
             "content types a matter of adding a new class rather than modifying existing code. "
             "The naming convention for content handlers follows the pattern: "
             "{ContentType}Handler in content/handlers/.",
        tool_uses=[
            {"name": "Write", "input": {"file_path": "backend/app/services/content_service.py"}},
            {"name": "Write", "input": {"file_path": "backend/app/services/handlers/blog.py"}},
            {"name": "Write", "input": {"file_path": "backend/app/services/handlers/project.py"}},
        ],
    ),
])


# ── Fixture: dependency session ───────────────────────────────────

DEPENDENCY_SESSION = "\n".join([
    _user("sess-dep-001", "u1", "", "2026-04-04T11:00:00.000Z",
          "Set up the markdown rendering pipeline"),

    _assistant("sess-dep-001", "a1", "u1", "2026-04-04T11:00:30.000Z",
        text="I'm installing react-markdown with remark-gfm for GitHub-flavored markdown "
             "and react-syntax-highlighter for code blocks. I considered using marked.js "
             "but react-markdown integrates better with our React component tree — "
             "it renders markdown as React elements rather than raw HTML, which gives us "
             "better control over styling and XSS prevention.",
    ),
])


# ── Fixture: session with empty thinking (Claude 4+) ──────────────

EMPTY_THINKING_SESSION = "\n".join([
    _system("sess-empty-001", "2026-04-05T08:00:00.000Z", model="claude-opus-4-6"),

    _user("sess-empty-001", "u1", "", "2026-04-05T08:00:05.000Z",
          "Add Docker configuration for the project"),

    _assistant("sess-empty-001", "a1", "u1", "2026-04-05T08:00:30.000Z",
        thinking="",  # empty thinking — Claude 4+ behavior
        text="I'm setting up a multi-container Docker Compose configuration. "
             "The architecture uses separate containers for the database, backend, "
             "frontend, and nginx reverse proxy. I decided to use multi-stage builds "
             "for both frontend and backend to minimize image sizes. "
             "The postgres container uses a healthcheck with pg_isready so the backend "
             "waits for the database to be ready before starting.",
        tool_uses=[
            {"name": "Write", "input": {"file_path": "docker-compose.yml"}},
            {"name": "Write", "input": {"file_path": "backend/Dockerfile.prod"}},
            {"name": "Write", "input": {"file_path": "frontend/Dockerfile.prod"}},
        ],
    ),
])


# ── Fixture: session continuation (multiple session IDs) ──────────

CONTINUATION_SESSION = "\n".join([
    # prefix from parent session (different sessionId)
    _user("sess-parent-001", "u1", "", "2026-04-06T10:00:00.000Z",
          "Start building the API endpoints"),

    _assistant("sess-parent-001", "a1", "u1", "2026-04-06T10:00:30.000Z",
        text="Setting up the FastAPI router structure with versioned API prefix /api/v1.",
    ),

    # continuation starts here (new sessionId)
    _user("sess-child-001", "u2", "a1", "2026-04-06T10:05:00.000Z",
          "Now add the content upload endpoint"),

    _assistant("sess-child-001", "a2", "u2", "2026-04-06T10:05:30.000Z",
        text="I'm implementing the upload endpoint using FastAPI's UploadFile with "
             "frontmatter validation. The pattern is: receive file → validate YAML "
             "frontmatter → save to /media/markdown/{section}/ → create database record "
             "with JSONB metadata. This keeps the file system as the source of truth "
             "for content while the database indexes metadata for fast queries.",
        tool_uses=[
            {"name": "Write", "input": {"file_path": "backend/app/api/v1/content.py"}},
        ],
    ),
])


# ── Fixture: low-signal session (should extract minimal nodes) ────

LOW_SIGNAL_SESSION = "\n".join([
    _user("sess-low-001", "u1", "", "2026-04-07T15:00:00.000Z",
          "What's in the current directory?"),

    _assistant("sess-low-001", "a1", "u1", "2026-04-07T15:00:10.000Z",
        text="Here are the files in the current directory:",
        tool_uses=[
            {"name": "Read", "input": {"file_path": "."}},
        ],
    ),

    _user("sess-low-001", "u2", "a1", "2026-04-07T15:00:20.000Z",
          "Thanks"),

    _assistant("sess-low-001", "a2", "u2", "2026-04-07T15:00:22.000Z",
        text="You're welcome! Let me know if you need anything else.",
    ),
])


# ── Fixture: compact summary (should be skipped) ──────────────────

COMPACT_SUMMARY_SESSION = "\n".join([
    json.dumps({
        "type": "assistant",
        "sessionId": "sess-compact-001",
        "timestamp": "2026-04-08T12:00:00.000Z",
        "uuid": "compact-1",
        "parentUuid": "",
        "isCompactSummary": True,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Summary of previous work..."}],
        },
    }),

    _user("sess-compact-001", "u1", "compact-1", "2026-04-08T12:00:30.000Z",
          "Continue with the auth implementation"),

    _assistant("sess-compact-001", "a1", "u1", "2026-04-08T12:01:00.000Z",
        text="Continuing from where we left off. I'll implement the JWT middleware "
             "that validates tokens on every protected route. The approach uses "
             "FastAPI's dependency injection to keep the auth check clean.",
        tool_uses=[
            {"name": "Edit", "input": {"file_path": "backend/app/core/dependencies.py"}},
        ],
    ),
])


# ── Fixture: duplicate UUIDs (should deduplicate) ─────────────────

DUPLICATE_UUID_SESSION = "\n".join([
    _user("sess-dup-001", "u1", "", "2026-04-09T16:00:00.000Z",
          "Create the database migration"),

    _assistant("sess-dup-001", "a1", "u1", "2026-04-09T16:00:30.000Z",
        text="Running alembic to generate the initial migration.",
    ),

    # duplicate of a1 (same uuid — should be skipped)
    _assistant("sess-dup-001", "a1", "u1", "2026-04-09T16:00:30.000Z",
        text="Running alembic to generate the initial migration.",
    ),

    _user("sess-dup-001", "u2", "a1", "2026-04-09T16:01:00.000Z",
          "Looks good, apply it"),

    _assistant("sess-dup-001", "a2", "u2", "2026-04-09T16:01:30.000Z",
        text="Migration applied successfully. The database schema now includes "
             "all tables with proper foreign key constraints.",
    ),
])


# ── Pytest fixtures ───────────────────────────────────────────────

@pytest.fixture
def tmp_session_dir(tmp_path: Path) -> Path:
    """Create a temporary directory structure mimicking ~/.claude/projects/."""
    project_dir = tmp_path / "-home-user-myproject"
    project_dir.mkdir(parents=True)
    return project_dir


def write_fixture(directory: Path, session_id: str, content: str) -> Path:
    """Write a fixture to a JSONL file."""
    path = directory / f"{session_id}.jsonl"
    path.write_text(content, encoding="utf-8")
    return path
