"""
CLI entry point for CodeAssist Interceptor.

Commands:
  codeassist parse     — Parse Claude Code sessions and extract IR
  codeassist serve     — Start MCP server for Claude Code integration
  codeassist inspect   — Browse captured IR nodes
  codeassist watch     — Watch for new sessions and auto-parse (debounced)
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(version="0.1.0")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def main(verbose: bool):
    """CodeAssist Interceptor — reasoning capture for Claude Code."""
    _setup_logging(verbose)


@main.command()
@click.argument("project_path", type=click.Path(exists=True), default=".")
@click.option("--force", is_flag=True, help="Re-parse already processed sessions")
@click.option("--min-confidence", default=0.4, help="Minimum extraction confidence")
@click.option("--llm", is_flag=True, help="Use LLM-assisted extraction (requires ANTHROPIC_API_KEY)")
@click.option("--api-key", default=None, help="Anthropic API key (or set ANTHROPIC_API_KEY env)")
def parse(project_path: str, force: bool, min_confidence: float, llm: bool, api_key: str | None):
    """Parse Claude Code sessions and extract IR nodes."""
    import os
    from .parser.session_parser import discover_sessions, parse_jsonl_file
    from .models.ir import SessionMeta
    from .storage.ir_store import IRStorage
    from .storage.embeddings import EmbeddingManager

    project_path = str(Path(project_path).resolve())
    storage = IRStorage()
    embeddings = EmbeddingManager(storage.db_path)

    effective_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if llm and not effective_key:
        console.print("[red]LLM mode requires ANTHROPIC_API_KEY[/red]")
        console.print("Set it via --api-key or ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    mode = "LLM-assisted" if llm and effective_key else "rule-based"
    console.print(f"[bold]Scanning sessions for[/bold] {project_path} [dim]({mode})[/dim]")

    session_files = list(discover_sessions(project_path))
    if not session_files:
        console.print("[yellow]No session files found.[/yellow]")
        console.print(
            f"Expected at: ~/.claude/projects/ "
            f"(encoded path: {project_path.replace('/', '-')})"
        )
        return

    console.print(f"Found [cyan]{len(session_files)}[/cyan] session files")

    total_nodes = 0
    for sf in session_files:
        session_id = sf.stem
        if not force and storage.is_session_parsed(session_id):
            console.print(f"  [dim]skip[/dim] {session_id[:12]}… (already parsed)")
            continue

        console.print(f"  [blue]parse[/blue] {session_id[:12]}…", end=" ")

        session = parse_jsonl_file(sf)
        session.project_path = project_path  # use the canonical resolved path, not lossy decode

        if llm and effective_key:
            from .parser.llm_extractor import extract_with_llm
            nodes = extract_with_llm(
                session, min_confidence=min_confidence, api_key=effective_key,
            )
        else:
            from .parser.extractor import extract_with_context_chaining
            nodes = extract_with_context_chaining(session, min_confidence)

        if nodes:
            stored = storage.store_nodes(nodes)
            embedded = embeddings.encode_and_store(nodes)
            llm_count = sum(1 for n in nodes if n.confidence == 0.85)
            suffix = f" [magenta]({llm_count} via LLM)[/magenta]" if llm_count else ""
            console.print(
                f"→ [green]{stored} nodes[/green], "
                f"[cyan]{embedded} embeddings[/cyan]{suffix}"
            )
            total_nodes += stored
        else:
            console.print("→ [dim]no extractable decisions[/dim]")

        meta = SessionMeta(
            session_id=session.session_id,
            project_path=session.project_path,
            started_at=session.started_at or datetime.utcnow(),
            ended_at=session.ended_at,
            model_used=session.model_used,
            total_turns=len(session.messages),
            nodes_extracted=len(nodes),
        )
        storage.upsert_session(meta)

    console.print(f"\n[bold green]Done.[/bold green] {total_nodes} new nodes extracted.")
    storage.close()
    embeddings.close()


@main.command()
def serve():
    """Start MCP server for Claude Code integration."""
    from .mcp.server import run_server
    console.print("[bold]Starting MCP server[/bold] (stdio transport)")
    console.print("Register with: claude mcp add codeassist-interceptor -- codeassist serve")
    run_server()


@main.command()
@click.argument("project_path", type=click.Path(exists=True), default=".")
@click.option("--type", "node_type", default=None, help="Filter by node type")
@click.option("--limit", default=20, help="Max results to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def inspect(project_path: str, node_type: str | None, limit: int, as_json: bool):
    """Browse captured IR nodes for a project."""
    import json as json_mod
    from .models.ir import NodeType
    from .storage.ir_store import IRStorage

    project_path = str(Path(project_path).resolve())
    storage = IRStorage()

    stats = storage.get_project_stats(project_path)
    if stats["total_nodes"] == 0:
        console.print("[yellow]No IR nodes found. Run `codeassist parse` first.[/yellow]")
        return

    types = [NodeType(node_type)] if node_type else None
    nodes = storage.query_nodes(project_path, types, limit)

    if as_json:
        output = [
            {
                "id": n.id, "type": n.node_type.value, "scope": n.scope.value,
                "summary": n.summary, "rationale": n.rationale,
                "files": n.files_affected, "tags": n.tags,
                "confidence": n.confidence, "timestamp": n.timestamp.isoformat(),
            }
            for n in nodes
        ]
        console.print(json_mod.dumps(output, indent=2))
    else:
        console.print(f"\n[bold]Project IR Summary[/bold]")
        console.print(f"  Nodes: {stats['total_nodes']}  Sessions: {stats['total_sessions']}")
        if stats["earliest"]:
            console.print(f"  Range: {stats['earliest'][:10]} → {stats['latest'][:10]}")
        console.print(f"  Types: {stats['by_type']}\n")

        table = Table(show_header=True, header_style="bold")
        table.add_column("Type", width=14)
        table.add_column("Scope", width=8)
        table.add_column("Summary", min_width=30)
        table.add_column("Files", width=20)
        table.add_column("Date", width=12)
        table.add_column("Conf", width=5)

        for n in nodes:
            files_str = ", ".join(f.rsplit("/", 1)[-1] for f in n.files_affected[:2]) or "—"
            table.add_row(
                n.node_type.value, n.scope.value, n.summary[:55],
                files_str[:20], n.timestamp.strftime("%m/%d %H:%M"),
                f"{n.confidence:.0%}",
            )

        console.print(table)
    storage.close()


@main.command()
@click.argument("project_path", type=click.Path(exists=True), default=".")
@click.option("--debounce", default=5.0, help="Seconds to wait after last file change")
@click.option("--llm", is_flag=True, help="Use LLM-assisted extraction")
@click.option("--api-key", default=None, help="Anthropic API key")
@click.option("--min-confidence", default=0.4, help="Minimum extraction confidence")
def watch(project_path: str, debounce: float, llm: bool, api_key: str | None, min_confidence: float):
    """Watch for new sessions and auto-parse with debouncing."""
    import os
    from .parser.watcher import DebouncedWatcher

    project_path = str(Path(project_path).resolve())
    effective_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    if llm and not effective_key:
        console.print("[red]LLM mode requires ANTHROPIC_API_KEY[/red]")
        sys.exit(1)

    def on_parse(session_id: str, node_count: int):
        console.print(f"  [green]✓[/green] {session_id[:12]}… → [cyan]{node_count}[/cyan] nodes")

    mode = "LLM-assisted" if llm and effective_key else "rule-based"
    console.print(f"[bold]Watching[/bold] {project_path}")
    console.print(f"  Mode: {mode} | Debounce: {debounce}s | Min confidence: {min_confidence}")
    console.print("  Press Ctrl+C to stop\n")

    watcher = DebouncedWatcher(
        project_path=project_path,
        debounce_seconds=debounce,
        use_llm=llm and bool(effective_key),
        api_key=effective_key if llm else None,
        min_confidence=min_confidence,
        on_parse=on_parse,
    )
    watcher.run_forever()


if __name__ == "__main__":
    main()
