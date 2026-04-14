#!/bin/sh
# Portable launcher for codeassist-interceptor MCP server.
#
# This script is executed by Claude Code when loading the MCP server.
# It tries multiple strategies to locate the codeassist binary:
#   1. Direct command lookup in PATH (pip install --user, activated venv, pipx)
#   2. Pyenv virtualenv fallback (convention: pyenv virtualenv cai)
#   3. Clear error message if not found
#
# Usage: ./scripts/cai-serve.sh
# (Called automatically by Claude Code via .mcp.json)

# Try PATH first (covers: pip install --user, activated venv, pipx)
if command -v codeassist >/dev/null 2>&1; then
    exec codeassist serve
fi

# Common pyenv virtualenv fallback
# Try multiple virtualenv names in order of preference
for VENV in cai codeassist codeassist-interceptor; do
    BIN="$HOME/.pyenv/versions/$VENV/bin/codeassist"
    if [ -f "$BIN" ]; then
        exec "$BIN" serve
    fi
done

# Not found — print helpful error and exit
cat >&2 << 'EOF'
Error: codeassist binary not found.

Install instructions:
  https://github.com/your-org/codeassist-interceptor#integration

Quick fix:
  1. Create a pyenv virtualenv: pyenv virtualenv 3.11.9 cai
  2. Activate it: pyenv activate cai
  3. Install: pip install -e /path/to/codeassist-interceptor/
  4. Verify: codeassist --version

Or use any Python environment with codeassist installed:
  pip install codeassist-interceptor
EOF

exit 1
