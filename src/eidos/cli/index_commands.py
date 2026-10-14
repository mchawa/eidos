"""Architectural knowledge graph commands (`eidos index ...`) and their root shortcuts."""

from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import PANEL_KNOWLEDGE, console
from eidos.core.index.graph_index import get_architectural_index

index_app = typer.Typer(
    name="index",
    help="Manage and query the embedded SQLite architectural knowledge graph (eidos/.cache/index.db).",
    add_completion=False,
)


@index_app.command("status")
def index_status_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw index telemetry as JSON for programmatic inspection.",
    ),
):
    """Display health, schema version, cache size, AST telemetry, and statistics of the architectural index."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    status = idx.get_index_status()

    if json_output:
        console.print_json(data=status)
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Database Path:", f"[dim]{status['db_path']}[/dim]")
    grid.add_row("Schema Version:", f"[green]{status['schema_version']}[/green]")
    grid.add_row("Cache Size:", f"[yellow]{status['db_size_bytes'] / 1024:.1f} KB[/yellow]")
    dirty_label = (
        "[bold red]DIRTY (Needs sync)[/bold red]"
        if status["is_dirty"]
        else "[bold green]SYNCED (Clean)[/bold green]"
    )
    grid.add_row("Status:", f"{dirty_label} [dim]({status['dirty_check_latency_ms']:.2f}ms check)[/dim]")
    grid.add_row("Total Entities:", f"[bold white]{status['total_entities']}[/bold white]")
    grid.add_row("Total Edges:", f"[bold white]{status['total_edges']}[/bold white]")
    grid.add_row("Indexed Files:", f"[bold white]{status['total_indexed_files']}[/bold white]")

    ast_info = status.get("ast", {})
    grid.add_row("AST Engine:", f"[magenta]{ast_info.get('engine', 'N/A')}[/magenta]")
    if "classes" in ast_info:
        cls_count = ast_info.get("classes", 0)
        fn_count = ast_info.get("functions", 0)
        meth_count = ast_info.get("methods", 0)
        tb_count = ast_info.get("test_bindings", 0)
        grid.add_row(
            "AST Symbols:",
            f"[bold white]{status['total_symbols']}[/bold white] "
            f"[dim]({cls_count} classes, {fn_count} funcs, {meth_count} methods, {tb_count} test bindings)[/dim]",
        )
    else:
        grid.add_row("AST Symbol Mappings:", f"[bold white]{status['total_symbols']}[/bold white]")

    if "test_invariant_assertions" in ast_info:
        grid.add_row(
            "Invariant Assertions:",
            f"[green]{ast_info['test_invariant_assertions']}[/green] assertions "
            f"across [bold green]{ast_info.get('tested_invariants', 0)}[/bold green] invariants",
        )

    console.print(Panel(grid, title="[bold blue]Eidos Architectural Knowledge Graph Index[/bold blue]", expand=False))

    if status.get("entity_counts_by_type"):
        table = Table(title="Entities by Type", show_header=True)
        table.add_column("Type", style="bold cyan")
        table.add_column("Count", justify="right", style="green")
        for etype, count in sorted(status["entity_counts_by_type"].items()):
            table.add_row(etype, str(count))
        console.print(table)

    if ast_info.get("symbols_by_kind"):
        ast_table = Table(title="AST Code Symbols & Traceability", show_header=True)
        ast_table.add_column("Syntax Kind", style="bold magenta")
        ast_table.add_column("Count", justify="right", style="green")
        for kind, count in sorted(ast_info["symbols_by_kind"].items()):
            ast_table.add_row(kind, str(count))
        console.print(ast_table)


@index_app.command("sync")
def index_sync_cmd(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force complete rebuild of the index from scratch.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Synchronize the architectural index with specifications on disk."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    stats = idx.sync(force=force)

    if stats.was_clean and not force:
        console.print(
            f"[bold green]✓ Index is up to date.[/bold green] Verified in [dim]{stats.duration_ms:.2f}ms[/dim]."
        )
    else:
        mode = "Rebuilt" if force else "Synchronized"
        console.print(
            f"[bold green]✓ {mode} index in {stats.duration_ms:.2f}ms.[/bold green] "
            f"[dim](Indexed {stats.indexed_new} new, updated {stats.updated_modified} modified, pruned {stats.pruned_deleted} deleted)[/dim]"
        )


@index_app.command("update", help="Alias for 'sync': Synchronize the architectural index with specifications on disk.")
def index_update_cmd(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force complete rebuild of the index from scratch.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Synchronize the architectural index with specifications on disk."""
    return index_sync_cmd(force=force, path=path)


@index_app.command("search")
def index_search_cmd(
    query: str = typer.Argument(
        ...,
        help="Search query keywords or phrase.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum number of matches to display.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Execute BM25 ranked full-text search (FTS5) across all specifications, capabilities, and invariants."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    results = idx.query_fts(query, limit=limit)

    if not results:
        console.print(f"[yellow]No architectural specifications matched query: '{query}'[/yellow]")
        return

    table = Table(title=f"Architectural Search Results for '{query}' ({len(results)} matches)", show_header=True)
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Entity ID", style="bold cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Title", style="white")
    table.add_column("Domain", style="dim")

    for r in results:
        table.add_row(f"{r['rank']:.2f}", r["id"], r["type"], r["title"], r.get("domain", "") or "—")

    console.print(table)


@index_app.command("blast-radius")
def index_blast_radius_cmd(
    entity_id: str = typer.Argument(
        ...,
        help="Target architectural entity ID (e.g. 'CNT-CORE', 'CAP-BILLING-001').",
    ),
    depth: int = typer.Option(
        2,
        "--depth",
        "-d",
        help="Maximum graph walk hop distance (defaults to 2).",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Display recursive CTE graph walk / blast radius for an architectural entity."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    neighbors = idx.get_k_hop_neighborhood(entity_id, max_depth=depth)

    if not neighbors:
        console.print(f"[yellow]Entity '{entity_id}' not found in architectural graph.[/yellow]")
        return

    table = Table(title=f"Blast Radius for '{entity_id}' (Depth: {depth})", show_header=True)
    table.add_column("Hop", justify="center", style="bold magenta")
    table.add_column("Entity ID", style="bold cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Title", style="white")
    table.add_column("Domain", style="dim")

    for n in neighbors:
        table.add_row(str(n["depth"]), n["id"], n["type"], n["title"], n.get("domain", "") or "—")

    console.print(table)


@index_app.command("repo-map")
def index_repo_map_cmd(
    max_tokens: int = typer.Option(
        1500,
        "--max-tokens",
        "-t",
        help="Maximum context tokens allocated for structural repo map outline.",
    ),
    format_type: str = typer.Option(
        "markdown",
        "--format",
        "-f",
        help="Output format: markdown (default) or json.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Generate a token-budgeted structural repo map outline for AI coding agents."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    repo_map = idx.get_repo_map(max_tokens=max_tokens)

    if format_type.lower() == "json":
        console.print_json(data={"repo_map": repo_map, "max_tokens": max_tokens, "estimated_tokens": len(repo_map) // 4})
    else:
        console.print(repo_map)


@index_app.command("find-symbol")
def index_find_symbol_cmd(
    query: str = typer.Argument(
        ...,
        help="Exact or partial symbol name (class, method, function, or route).",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of symbol results to return.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as machine-readable JSON.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Locate symbol matches and their associated living component specifications."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    matches = idx.find_symbol(query=query, limit=limit)

    if json_output:
        console.print_json(data={"query": query, "count": len(matches), "matches": matches})
        return

    if not matches:
        console.print(f"[yellow]No symbols found matching '{query}'.[/yellow]")
        return

    table = Table(title=f"Code Symbols matching '{query}' ({len(matches)} matches)", show_header=True)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Kind", style="yellow")
    table.add_column("Owning Entity", style="bold white", no_wrap=True)
    table.add_column("Location", style="green")
    table.add_column("Spec Path", style="dim")

    for m in matches:
        loc = m.get("location")
        location = f"{loc['file']}:{loc['line_start']}" if loc else "—"
        table.add_row(m["symbol"], m["kind"], m["entity_id"], location, m.get("file_path", "") or "—")

    console.print(table)


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("repo-map", rich_help_panel=PANEL_KNOWLEDGE)(index_repo_map_cmd)
    app.command("find-symbol", rich_help_panel=PANEL_KNOWLEDGE)(index_find_symbol_cmd)
