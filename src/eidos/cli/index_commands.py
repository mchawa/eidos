"""Architectural knowledge graph commands (`eidos index ...`)."""

from pathlib import Path

import orjson
import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import console
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
        "[bold red]DIRTY (Needs sync)[/bold red]" if status["is_dirty"] else "[bold green]SYNCED (Clean)[/bold green]"
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
    semantic: bool = typer.Option(
        True,
        "--semantic/--no-semantic",
        help="Enable or disable opt-in Hybrid RRF semantic fusion when spec_embeddings are available.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output search results as machine-readable JSON.",
    ),
):
    """Execute BM25 ranked full-text search (FTS5) with optional Harrier Hybrid RRF across specifications, capabilities, and invariants."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    results = idx.query_fts(query, limit=limit, use_semantic=semantic)

    if json_output:
        typer.echo(
            orjson.dumps(
                {"query": query, "count": len(results), "results": results}, option=orjson.OPT_INDENT_2
            ).decode("utf-8")
        )
        return

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
    symbol_or_path: str = typer.Argument(
        ...,
        metavar="SYMBOL_OR_PATH",
        help="Target architectural entity ID, AST symbol, or file path (e.g. 'CNT-CORE', 'detect_drift', 'src/vcs/drift.py').",
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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as machine-readable JSON.",
    ),
):
    """Display recursive CTE graph walk / blast radius for an architectural entity, symbol, or file path."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    resolved_ids = idx.resolve_blast_radius_targets(symbol_or_path)
    neighbors = idx.get_k_hop_neighborhood(resolved_ids, max_depth=depth) if resolved_ids else []
    primary_entity_id = resolved_ids[0] if resolved_ids else symbol_or_path

    if json_output:
        console.print_json(
            data={
                "entity_id": primary_entity_id,
                "query": symbol_or_path,
                "resolved_entities": resolved_ids,
                "depth": depth,
                "count": len(neighbors),
                "neighbors": neighbors,
            }
        )
        return

    if not neighbors:
        console.print(f"[yellow]Entity '{symbol_or_path}' not found in architectural graph.[/yellow]")
        return

    resolved_str = ", ".join(resolved_ids)
    title_target = (
        f"'{symbol_or_path}' -> {resolved_str}"
        if resolved_ids and symbol_or_path.upper() != resolved_str.upper()
        else f"'{primary_entity_id}'"
    )
    table = Table(title=f"Blast Radius for {title_target} (Depth: {depth})", show_header=True)
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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output structural repo map as machine-readable JSON instead of Markdown.",
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

    if json_output:
        console.print_json(
            data={"repo_map": repo_map, "max_tokens": max_tokens, "estimated_tokens": len(repo_map) // 4}
        )
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


@index_app.command("semantic")
def index_semantic_cmd(
    action: str = typer.Argument(
        "status",
        metavar="ACTION",
        help="Semantic index action: 'status', 'setup', or 'sync'.",
    ),
    model: str = typer.Option(
        "quantized",
        "--model",
        "-m",
        help="Harrier ONNX model variant to provision ('quantized', 'fp32', or 'fp16').",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force re-download or full re-embedding of all specifications.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output semantic index telemetry as machine-readable JSON.",
    ),
):
    """Manage and inspect the optional Harrier-OSS-v1-0.6B ONNX semantic embedding index (status, setup, sync)."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    act = (action or "status").strip().lower()

    if act == "status":
        status = idx.get_semantic_status()
        if json_output:
            console.print_json(data=status)
            return
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column(style="white")
        grid.add_row("Model ID:", f"[bold white]{status['model_id']}[/bold white]")
        grid.add_row("Model Directory:", f"[dim]{status['model_dir']}[/dim]")
        grid.add_row(
            "Runtime Ready:",
            "[bold green]YES[/bold green]"
            if status["runtime_available"]
            else "[bold yellow]NO (Install eidos-ai[semantic])[/bold yellow]",
        )
        grid.add_row(
            "Model Provisioned:",
            f"[bold green]YES ({status['active_variant']})[/bold green]"
            if status["model_ready"]
            else "[bold yellow]NO (Run eidos index semantic setup)[/bold yellow]",
        )
        grid.add_row(
            "Cached Embeddings:",
            f"[bold white]{status['embedded_entities']}[/bold white] / {status['total_sliceable_entities']} "
            f"[dim]({status['fresh_embeddings']} fresh, {status['stale_or_missing_embeddings']} pending)[/dim]",
        )
        console.print(
            Panel(grid, title="[bold blue]Eidos Semantic Embedding Index (Harrier ONNX)[/bold blue]", expand=False)
        )
        return

    if act == "setup":
        try:
            res = idx.setup_semantic(variant=model, force=force)
        except Exception as exc:
            if json_output:
                console.print_json(data={"error": str(exc)})
            else:
                console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(code=1) from exc
        if json_output:
            console.print_json(data=res)
        else:
            console.print(
                f"[bold green]✓ Provisioned Harrier ONNX ({res['variant']}) in {res['model_dir']}.[/bold green] "
                f"[dim](Downloaded {len(res['downloaded'])}, skipped {len(res['skipped'])})[/dim]"
            )
        return

    if act == "sync":
        try:
            res = idx.sync_semantic(force=force)
        except Exception as exc:
            if json_output:
                console.print_json(data={"error": str(exc)})
            else:
                console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(code=1) from exc
        if json_output:
            console.print_json(data=res)
        else:
            console.print(
                f"[bold green]✓ Synchronized semantic embeddings in {res['duration_ms']:.2f}ms.[/bold green] "
                f"[dim](Encoded {res['encoded_entities']}, reused {res['reused_entities']} cached, pruned {res['pruned_orphans']})[/dim]"
            )
        return

    msg = f"Unknown semantic action '{action}'. Expected one of: status, setup, sync."
    if json_output:
        console.print_json(data={"error": msg})
    else:
        console.print(f"[bold red]Error:[/bold red] {msg}")
    raise typer.Exit(code=1)
