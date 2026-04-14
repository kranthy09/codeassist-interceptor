#!/usr/bin/env python3
"""
Diagnose Claude Code project path mismatches and session capture failures.

Detects hyphen/underscore variants that prevent the watcher from seeing sessions
and suggests fixes.

Usage:
  python diagnose_paths.py
  python diagnose_paths.py /home/kranthi/Projects/my-project/
"""

import os
import sys
from pathlib import Path
from subprocess import run, PIPE


def encode_path(path: str) -> str:
    """Encode filesystem path like Claude Code does."""
    path = Path(path).resolve().as_posix()
    # Claude replaces / with - and keeps underscores... actually NO
    # It replaces EVERYTHING except alphanumerics, dots, underscores with hyphens
    # Actually no, it's simpler: just - for / and normalized
    return "-" + path.lstrip("/").replace("/", "-")


def find_claude_projects():
    """List all Claude project directories."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return []
    return sorted([d.name for d in claude_dir.iterdir() if d.is_dir()])


def get_sessions_in_project(project_encoded: str) -> list[Path]:
    """Get all JSONL session files in a project folder."""
    claude_dir = Path.home() / ".claude" / "projects" / project_encoded
    if not claude_dir.exists():
        return []
    return sorted(
        list(claude_dir.glob("*.jsonl")) +
        list(claude_dir.glob("*/"))
    )


def get_active_watcher() -> str | None:
    """Get the path being watched by active codeassist watch process."""
    try:
        result = run(
            ["pgrep", "-af", "codeassist watch"],
            capture_output=True,
            text=True,
        )
        if result.stdout:
            # Extract path from command line
            # Format: /path/to/python /path/to/bin/codeassist watch /actual/path/ [--llm]
            parts = result.stdout.strip().split()
            # Find the "watch" keyword and take the next arg
            if "watch" in parts:
                idx = parts.index("watch")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
    except:
        pass
    return None


def check_project(project_path: str | None = None):
    """Check a specific project or prompt for one."""
    if not project_path:
        projects = find_claude_projects()
        if not projects:
            print("❌ No Claude projects found. Run a Claude Code session first.")
            return

        # Find projects with path naming issues
        candidates = {}
        for proj in projects:
            # Decode: -home-kranthi-Projects-foo → /home/kranthi/Projects/foo
            decoded = "/" + proj.lstrip("-").replace("-", "/")
            # Extract just the project name (last component)
            proj_name = decoded.split("/")[-1]
            if proj_name not in candidates:
                candidates[proj_name] = []
            candidates[proj_name].append((proj, decoded))

        # Find duplicates (hyphen/underscore variants)
        print("\n📊 Claude Code Project Status\n")
        issues = []
        for name, variants in candidates.items():
            if len(variants) > 1:
                print(f"⚠️  Path mismatch detected: '{name}'")
                for encoded, decoded in variants:
                    sessions = get_sessions_in_project(encoded)
                    print(f"   {encoded}")
                    print(f"   └─ {len(sessions)} sessions")
                    if sessions:
                        latest = max(sessions, key=lambda p: p.stat().st_mtime)
                        mtime = latest.stat().st_mtime
                        import time
                        age = time.time() - mtime
                        if age < 3600:
                            age_str = f"{int(age/60)} min ago"
                        elif age < 86400:
                            age_str = f"{int(age/3600)} hrs ago"
                        else:
                            age_str = f"{int(age/86400)} days ago"
                        print(f"      Last: {age_str}")
                    issues.append((name, variants))
            else:
                sessions = get_sessions_in_project(variants[0][0])
                status = "✅" if sessions else "⭕"
                print(f"{status} {name}: {len(sessions)} sessions")

        if not issues:
            print("\n✅ No path mismatches detected!")
            return

        print(f"\n❌ Found {len(issues)} path mismatch(es) that prevent session capture!")
        print("\nFIX: Use canonical filesystem path everywhere:")
        for name, variants in issues:
            print(f"\nProject: {name}")
            # Determine which is the real one
            for encoded, decoded in variants:
                real_path = Path(decoded)
                if real_path.exists():
                    print(f"  ✓ Real path: {decoded}")
                    print(f"    Use this path for IDE and watcher")
                    print(f"    → codeassist watch {decoded} --llm")
                else:
                    print(f"  ✗ Not found: {decoded}")
    else:
        # Check a specific project
        real_path = Path(project_path).resolve()
        if not real_path.exists():
            print(f"❌ Path does not exist: {project_path}")
            return

        encoded = encode_path(str(real_path))
        sessions = get_sessions_in_project(encoded)

        print(f"\n📊 Project: {project_path}")
        print(f"Encoded:   {encoded}")
        print(f"Sessions:  {len(sessions)}")

        if not sessions:
            print("\n❌ No sessions found!")
            print(f"Fix: Create a Claude Code session in {project_path}")
            return

        # Check if watcher is watching this path
        watcher_path = get_active_watcher()
        if watcher_path:
            watcher_path = str(Path(watcher_path).resolve())
            real_path_str = str(real_path)
            if watcher_path == real_path_str:
                print(f"\n✅ Watcher IS monitoring this project")
            else:
                print(f"\n⚠️  Watcher is watching a different project:")
                print(f"    Watching: {watcher_path}")
                print(f"    This:     {real_path_str}")
        else:
            print(f"\n⚠️  No active watcher found")
            print(f"Start it: codeassist watch {project_path} --llm")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_project(sys.argv[1])
    else:
        check_project()
