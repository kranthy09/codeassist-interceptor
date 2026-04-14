#!/usr/bin/env bash
#
# cai-setup — Configure CodeAssist Interceptor for any project
#
# Parses Claude Code sessions, extracts IR nodes, and optionally
# registers the MCP server so Claude Code can query past decisions.
#
# Usage:
#   cai-setup <project-path> [options]
#
# Examples:
#   # Parse sessions (rule-based, skip already parsed)
#   cai-setup /home/kranthi/Projects/enterprise_ai
#
#   # Parse with LLM extraction for better accuracy
#   cai-setup /home/kranthi/Projects/enterprise_ai --llm
#
#   # Force re-parse all sessions + register MCP server
#   cai-setup /home/kranthi/Projects/enterprise_ai --force --mcp
#
#   # Just register MCP, no parsing
#   cai-setup /home/kranthi/Projects/enterprise_ai --mcp-only
#
#   # Parse + MCP + watch for new sessions
#   cai-setup /home/kranthi/Projects/enterprise_ai --mcp --watch
#
#   # Show what's already captured (no changes)
#   cai-setup /home/kranthi/Projects/enterprise_ai --status
#
#   # Remove MCP registration from a project
#   cai-setup /home/kranthi/Projects/enterprise_ai --remove-mcp

set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────
CODEASSIST_BIN="/home/kranthi/.pyenv/versions/cai/bin/codeassist"
CLAUDE_BIN="claude"
DB_PATH="${CODEASSIST_DB_PATH:-$HOME/.codeassist/ir.db}"
MCP_SERVER_NAME="codeassist-interceptor"

# ── Colors ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ── Helpers ──────────────────────────────────────────────────────────
info()  { echo -e "${GREEN}[+]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[!]${RESET} $*"; }
err()   { echo -e "${RED}[x]${RESET} $*" >&2; }
dim()   { echo -e "${DIM}    $*${RESET}"; }

usage() {
    cat <<'EOF'
Usage: cai-setup <project-path> [options]

Arguments:
  project-path          Absolute or relative path to the project directory

Options:
  --llm                 Use LLM-assisted extraction (needs ANTHROPIC_API_KEY)
  --force               Re-parse already processed sessions
  --min-confidence N    Minimum extraction confidence (default: 0.4)
  --mcp                 Register MCP server for this project (project-scope)
  --mcp-only            Only register MCP, skip parsing
  --watch               After parsing, start watching for new sessions
  --debounce N          Watch debounce seconds (default: 5)
  --status              Show current state, don't change anything
  --remove-mcp          Remove MCP registration from the project
  --dry-run             Show what would be done without doing it
  -h, --help            Show this help

Environment:
  ANTHROPIC_API_KEY     Required for --llm mode
  CODEASSIST_DB_PATH    Override default ~/.codeassist/ir.db

Examples:
  # First time setup for a project
  cai-setup ~/Projects/enterprise_ai --mcp

  # Update IR after a long coding session
  cai-setup ~/Projects/enterprise_ai

  # Full setup with LLM extraction
  cai-setup ~/Projects/enterprise_ai --llm --mcp

  # Check what's captured
  cai-setup ~/Projects/enterprise_ai --status
EOF
    exit 0
}

# ── Parse arguments ──────────────────────────────────────────────────
PROJECT_PATH=""
USE_LLM=false
FORCE=false
MIN_CONFIDENCE="0.4"
REGISTER_MCP=false
MCP_ONLY=false
WATCH=false
DEBOUNCE="5"
STATUS_ONLY=false
REMOVE_MCP=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --llm)            USE_LLM=true; shift ;;
        --force)          FORCE=true; shift ;;
        --min-confidence) MIN_CONFIDENCE="$2"; shift 2 ;;
        --mcp)            REGISTER_MCP=true; shift ;;
        --mcp-only)       MCP_ONLY=true; shift ;;
        --watch)          WATCH=true; shift ;;
        --debounce)       DEBOUNCE="$2"; shift 2 ;;
        --status)         STATUS_ONLY=true; shift ;;
        --remove-mcp)     REMOVE_MCP=true; shift ;;
        --dry-run)        DRY_RUN=true; shift ;;
        -h|--help)        usage ;;
        -*)               err "Unknown option: $1"; usage ;;
        *)
            if [[ -z "$PROJECT_PATH" ]]; then
                PROJECT_PATH="$1"
            else
                err "Unexpected argument: $1"
                usage
            fi
            shift
            ;;
    esac
done

# Default to current directory
if [[ -z "$PROJECT_PATH" ]]; then
    PROJECT_PATH="$(pwd)"
fi

# Resolve to absolute path
PROJECT_PATH="$(cd "$PROJECT_PATH" 2>/dev/null && pwd)" || {
    err "Directory does not exist: $PROJECT_PATH"
    exit 1
}

PROJECT_NAME="$(basename "$PROJECT_PATH")"

# ── Preflight checks ────────────────────────────────────────────────
preflight() {
    local ok=true

    if [[ ! -x "$CODEASSIST_BIN" ]]; then
        # Fallback: check if codeassist is anywhere on PATH
        if command -v codeassist &>/dev/null; then
            CODEASSIST_BIN="$(command -v codeassist)"
            warn "Using codeassist from PATH: $CODEASSIST_BIN"
            warn "Pyenv shims may fail when Claude Code spawns MCP. Consider:"
            dim "pip install -e '.[dev]' in the cai virtualenv"
        else
            err "codeassist not found. Install with:"
            dim "cd /home/kranthi/Projects/codeassist-interceptor/codeassist-interceptor"
            dim "pip install -e '.[dev]'"
            ok=false
        fi
    fi

    if $USE_LLM && [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        err "--llm requires ANTHROPIC_API_KEY to be set"
        dim "export ANTHROPIC_API_KEY='sk-...'"
        ok=false
    fi

    if ($REGISTER_MCP || $MCP_ONLY || $REMOVE_MCP) && ! command -v "$CLAUDE_BIN" &>/dev/null; then
        err "claude CLI not found (needed for MCP registration)"
        ok=false
    fi

    $ok || exit 1
}

# ── Count sessions available for a project ───────────────────────────
count_sessions() {
    # Claude Code encodes paths: /home/kranthi/Projects/foo_bar → -home-kranthi-Projects-foo_bar
    # But sometimes underscores also become dashes. Try exact match first, then fuzzy.
    local encoded
    encoded="$(echo "$PROJECT_PATH" | sed 's|/|-|g')"
    local session_dir="$HOME/.claude/projects/$encoded"
    local count=0

    if [[ -d "$session_dir" ]]; then
        count=$(ls -1 "$session_dir"/*.jsonl 2>/dev/null | wc -l)
    fi

    # If exact match found nothing, try with underscores replaced by dashes
    if [[ "$count" -eq 0 ]]; then
        local encoded_alt
        encoded_alt="$(echo "$PROJECT_PATH" | sed 's|[/_]|-|g')"
        session_dir="$HOME/.claude/projects/$encoded_alt"
        if [[ -d "$session_dir" ]]; then
            count=$(ls -1 "$session_dir"/*.jsonl 2>/dev/null | wc -l)
        fi
    fi

    # Last resort: scan dirs containing the project basename
    if [[ "$count" -eq 0 ]]; then
        local project_base
        project_base="$(basename "$PROJECT_PATH" | sed 's|_|-|g')"
        for d in "$HOME/.claude/projects/"*"$project_base"*; do
            if [[ -d "$d" ]]; then
                count=$(( count + $(ls -1 "$d"/*.jsonl 2>/dev/null | wc -l) ))
            fi
        done
    fi

    echo "$count"
}

# ── Query DB for project stats ───────────────────────────────────────
db_stats() {
    if [[ ! -f "$DB_PATH" ]]; then
        echo "no-db"
        return
    fi

    python3 -c "
import sqlite3, sys
conn = sqlite3.connect('$DB_PATH')
c = conn.cursor()
path = '$PROJECT_PATH'
try:
    parsed = c.execute('SELECT COUNT(*) FROM sessions WHERE project_path = ?', (path,)).fetchone()[0]
    nodes = c.execute('SELECT COUNT(*) FROM nodes WHERE project_path = ?', (path,)).fetchone()[0]
    types_row = c.execute('SELECT GROUP_CONCAT(DISTINCT node_type) FROM nodes WHERE project_path = ?', (path,)).fetchone()
    types = types_row[0] or '' if types_row else ''
    latest_row = c.execute('SELECT MAX(timestamp) FROM nodes WHERE project_path = ?', (path,)).fetchone()
    latest = latest_row[0] or '' if latest_row else ''
    print(f'{parsed},{nodes},{types},{latest}')
except Exception as e:
    print(f'0,0,,')
conn.close()
"
}

# ── Status command ───────────────────────────────────────────────────
show_status() {
    local session_count
    session_count="$(count_sessions)"

    echo ""
    echo -e "${BOLD}Project:${RESET}  $PROJECT_NAME"
    echo -e "${BOLD}Path:${RESET}     $PROJECT_PATH"
    echo -e "${BOLD}Sessions:${RESET} $session_count JSONL files found"

    if [[ ! -f "$DB_PATH" ]]; then
        warn "No database yet at $DB_PATH"
        dim "Run: cai-setup $PROJECT_PATH"
        echo ""
        return
    fi

    local stats
    stats="$(db_stats)"

    if [[ "$stats" == "no-db" ]]; then
        warn "Database not found"
        return
    fi

    local parsed nodes types latest
    IFS=',' read -r parsed nodes types latest <<< "$stats"

    echo -e "${BOLD}Parsed:${RESET}   $parsed sessions → $nodes IR nodes"
    if [[ -n "$types" ]]; then
        echo -e "${BOLD}Types:${RESET}    $types"
    fi
    if [[ -n "$latest" ]]; then
        echo -e "${BOLD}Latest:${RESET}  $latest"
    fi

    local unparsed=$(( session_count - parsed ))
    if [[ $unparsed -gt 0 ]]; then
        warn "$unparsed sessions not yet parsed"
        dim "Run: cai-setup $PROJECT_PATH"
    else
        info "All sessions parsed"
    fi

    # Check MCP registration
    local mcp_json="$PROJECT_PATH/.mcp.json"
    if [[ -f "$mcp_json" ]] && grep -q "$MCP_SERVER_NAME" "$mcp_json" 2>/dev/null; then
        info "MCP server registered (project-scope)"
    else
        warn "MCP server not registered"
        dim "Run: cai-setup $PROJECT_PATH --mcp"
    fi

    echo ""
}

# ── Parse sessions ───────────────────────────────────────────────────
parse_sessions() {
    local session_count
    session_count="$(count_sessions)"

    if [[ "$session_count" -eq 0 ]]; then
        warn "No Claude Code sessions found for $PROJECT_NAME"
        dim "Sessions expected at: ~/.claude/projects/$(echo "$PROJECT_PATH" | sed 's|/|-|g')/"
        return 1
    fi

    info "Parsing $session_count sessions for ${CYAN}$PROJECT_NAME${RESET}"

    local cmd=("$CODEASSIST_BIN" "parse" "$PROJECT_PATH")
    cmd+=("--min-confidence" "$MIN_CONFIDENCE")

    if $FORCE; then
        cmd+=("--force")
        dim "Force mode: re-parsing all sessions"
    fi

    if $USE_LLM; then
        cmd+=("--llm")
        dim "Using LLM-assisted extraction (Haiku)"
    fi

    if $DRY_RUN; then
        dim "Would run: ${cmd[*]}"
        return 0
    fi

    echo ""
    "${cmd[@]}"
    echo ""
}

# ── Register MCP ─────────────────────────────────────────────────────
register_mcp() {
    local mcp_json="$PROJECT_PATH/.mcp.json"

    # Check if already registered
    if [[ -f "$mcp_json" ]] && grep -q "$MCP_SERVER_NAME" "$mcp_json" 2>/dev/null; then
        info "MCP server already registered in $PROJECT_NAME"
        dim "Config: $mcp_json"
        return 0
    fi

    info "Registering MCP server for ${CYAN}$PROJECT_NAME${RESET} (project-scope)"

    if $DRY_RUN; then
        dim "Would run: claude mcp add --scope project -d $PROJECT_PATH ..."
        return 0
    fi

    # claude mcp add needs to run from the project dir or use -d flag
    (cd "$PROJECT_PATH" && \
        "$CLAUDE_BIN" mcp add \
            --scope project \
            --transport stdio \
            "$MCP_SERVER_NAME" \
            -- "$CODEASSIST_BIN" serve
    )

    if [[ -f "$mcp_json" ]]; then
        info "MCP registered. Config written to .mcp.json"
        dim "Claude Code will auto-discover this when opened in $PROJECT_NAME"
    else
        warn "Registration may have failed — check with: claude mcp list"
    fi
}

# ── Remove MCP ───────────────────────────────────────────────────────
remove_mcp() {
    local mcp_json="$PROJECT_PATH/.mcp.json"

    if [[ ! -f "$mcp_json" ]] || ! grep -q "$MCP_SERVER_NAME" "$mcp_json" 2>/dev/null; then
        warn "MCP server not registered in $PROJECT_NAME"
        return 0
    fi

    info "Removing MCP server from ${CYAN}$PROJECT_NAME${RESET}"

    if $DRY_RUN; then
        dim "Would run: claude mcp remove $MCP_SERVER_NAME in $PROJECT_PATH"
        return 0
    fi

    (cd "$PROJECT_PATH" && "$CLAUDE_BIN" mcp remove "$MCP_SERVER_NAME")
    info "MCP server removed"
}

# ── Watch ────────────────────────────────────────────────────────────
start_watch() {
    info "Starting watcher for ${CYAN}$PROJECT_NAME${RESET} (debounce: ${DEBOUNCE}s)"
    dim "Press Ctrl+C to stop"

    local cmd=("$CODEASSIST_BIN" "watch" "$PROJECT_PATH" "--debounce" "$DEBOUNCE")

    if $USE_LLM; then
        cmd+=("--llm")
    fi

    cmd+=("--min-confidence" "$MIN_CONFIDENCE")

    if $DRY_RUN; then
        dim "Would run: ${cmd[*]}"
        return 0
    fi

    echo ""
    "${cmd[@]}"
}

# ── Main ─────────────────────────────────────────────────────────────
main() {
    preflight

    echo ""
    echo -e "${BOLD}cai-setup${RESET} — CodeAssist Interceptor"
    echo -e "${DIM}─────────────────────────────────────${RESET}"

    # Status only
    if $STATUS_ONLY; then
        show_status
        return 0
    fi

    # Remove MCP
    if $REMOVE_MCP; then
        remove_mcp
        return 0
    fi

    # MCP only (no parse)
    if $MCP_ONLY; then
        register_mcp
        echo ""
        return 0
    fi

    # Parse (unless --mcp-only)
    if ! $MCP_ONLY; then
        parse_sessions || true
    fi

    # Register MCP if requested
    if $REGISTER_MCP; then
        register_mcp
    fi

    # Show summary
    show_status

    # Watch if requested (blocks)
    if $WATCH; then
        start_watch
    fi
}

main
