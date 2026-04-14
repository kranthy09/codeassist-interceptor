# Project-level CLAUDE.md integration example
#
# Add this block to your project's CLAUDE.md to enable
# automatic context retrieval from the interceptor.

## CodeAssist Interceptor

This project uses codeassist-interceptor for cross-session reasoning persistence.

**Before starting work on any feature:**
1. Call `get_project_summary` to understand the current architectural state
2. Call `get_project_context` with what you're about to work on
3. Check `search_decisions` for any relevant prior decisions

**Before making architectural decisions:**
- Call `get_decision_history` with type="rejection" to see what was already tried and dropped
- Check type="pattern" to maintain consistency with established conventions

**MCP tools available:**
- `get_project_context` — semantic search for relevant decisions
- `get_decision_history` — chronological decision list by type
- `get_project_summary` — quick overview of captured reasoning
- `search_decisions` — keyword search across all IR nodes

This avoids re-reading files that were already analyzed in previous sessions.
