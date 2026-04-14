# Integration Quick Start

**For other developers:** Fast track to get codeassist-interceptor working.

## Phase A: One-Time (Per Machine)

```bash
# Create Python 3.11 virtualenv
pyenv virtualenv 3.11.9 cai
pyenv activate cai

# Install this project
cd /path/to/codeassist-interceptor
pip install -e .

# Verify
codeassist --version  # → 0.1.0
```

**Time:** ~2 min  
**Repeat for each new machine only**

---

## Phase B: One-Time (Per Project)

Already done. The `.mcp.json` and `scripts/cai-serve.sh` are in the repo.

```bash
# Pull the latest code
git pull

# Verify MCP connection
claude mcp list
# Expected: codeassist-interceptor: ✓ Connected

# Backfill existing sessions (optional, but recommended)
codeassist parse . --llm
```

**Time:** ~5 min  
**Only needed when first integrating a project**

---

## Phase C: Daily Usage

```bash
# Start watcher (once per machine)
codeassist watch . --llm

# That's it. Work normally in Claude Code.
# MCP tools are auto-called when needed.
```

**Time:** 1 command  
**Repeat whenever you start a new work session on this machine**

---

## What Gets You Going?

| Step | Command | What It Does |
|------|---------|-------------|
| Phase A | `pip install -e .` | Installs codeassist CLI + MCP server into your environment |
| Phase A | `codeassist --version` | Verifies installation worked |
| Phase B | `claude mcp list` | Checks that Claude Code can find the MCP server |
| Phase B | `codeassist parse . --llm` | Extracts all past session data into searchable database |
| Phase C | `codeassist watch . --llm` | Auto-parses future sessions as they happen |
| Phase C | (use Claude Code normally) | Tools called automatically when you ask Claude about the project |

---

## If Something Breaks

1. **`codeassist: command not found`**  
   → Run Phase A setup again in the right virtualenv

2. **MCP shows "not connected"**  
   → Check binary exists: `which codeassist` or `$HOME/.pyenv/versions/cai/bin/codeassist --version`

3. **No decisions found**  
   → Run `codeassist parse . --llm` to backfill

4. **First MCP call is slow**  
   → Normal — embedding model loads (~5s). Subsequent calls instant.

---

See `README.md` for full troubleshooting and reference.
