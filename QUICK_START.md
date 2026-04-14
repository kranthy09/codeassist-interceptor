# CodeAssist Interceptor — Quick Start (TL;DR)

## 60-Second Setup

```bash
# 1. Install (one-time)
pip install -e ~/Projects/codeassist-interceptor/codeassist-interceptor

# 2. For each project, register MCP:
cai-setup ~/Projects/your_project --mcp

# Done! Now open Claude Code:
cd ~/Projects/your_project
claude
```

## In Claude Code

Ask naturally — the MCP tools will surface captured decisions automatically:

```
"What architectural decisions have been made?"
"Why was library X rejected?"
"Show me recent decisions from the last 7 days"
"What patterns are established for testing?"
```

## Common Commands

```bash
# Check what's captured
cai-setup ~/Projects/your_project --status

# Update IR after coding session
cai-setup ~/Projects/your_project

# Use LLM for better accuracy (costs tokens)
export ANTHROPIC_API_KEY="sk-ant-..."
cai-setup ~/Projects/your_project --llm

# Auto-parse new sessions as you work
cai-setup ~/Projects/your_project --watch

# Re-parse everything
cai-setup ~/Projects/your_project --force

# Inspect nodes
codeassist inspect ~/Projects/your_project --limit 20
```

## What It Does

| Phase | Command | Time | Output |
|-------|---------|------|--------|
| First setup | `cai-setup . --mcp` | 5–20 min | 1600+ decision nodes |
| Regular update | `cai-setup .` | <1 min | Only new sessions parsed |
| Query in Claude | Ask naturally | <1 sec | Relevant decisions returned |

## Aliases

Add to `~/.bashrc`:
```bash
alias cai-setup="/home/kranthi/Projects/codeassist-interceptor/cai-setup.sh"
```

Then reload: `source ~/.bashrc`

## Current Status

✅ **12 projects ready** for integration:
- enterprise_ai (121 sessions) → 1601 decisions
- companygate (14 sessions)
- codegate (13 sessions)
- cagentkb (9 sessions)
- vizport (8 sessions)
- 7 smaller projects (1–6 sessions each)

## Storage

Single database: `~/.codeassist/ir.db` (SQLite)
- All projects use same DB
- Indexed by project_path
- Searchable across all projects

## Next: Full Guide

See `SETUP_GUIDE.md` for:
- Detailed installation
- All script options  
- Troubleshooting
- Architecture & design
- Development notes

---

**TL;DR**: `pip install`, `cai-setup . --mcp`, `claude` → ask questions, get answers from past sessions.
