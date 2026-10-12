"""Main CLI entrypoint for Eidos."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import eidos
from eidos.core.context import get_context_slice
from eidos.core.graph_index import get_architectural_index
from eidos.core.parser import parse_spec_file
from eidos.core.scaffolder import (
    apply_delta,
    create_spec,
    delete_spec,
    find_spec_by_id_or_path,
    get_delta_status_summary,
    init_repository,
    list_deltas,
    list_specs,
    sync_delta_views,
)
from eidos.core.validator import SpecValidator
from eidos.mcp.server import run_mcp_server
from eidos.plugin.installer import (
    PluginInstallTarget,
    install_plugin,
    locate_plugin_bundle,
    uninstall_plugin,
    update_plugin,
)
from eidos.plugin.validator import validate_plugin_package
from eidos.plugin.version import (
    check_plugin_version_status,
)
from eidos.portal.builder import build_static_portal
from eidos.portal.server import run_portal_server
from eidos.vcs import detect_drift, reconcile_specs

app = typer.Typer(
    name="eidos",
    help="Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator.",
    add_completion=False,
)
plugin_app = typer.Typer(
    name="plugin",
    help="Manage, install, and validate Eidos agent plugins (agent-plugins.org v1.0.0).",
    add_completion=False,
)
spec_app = typer.Typer(
    name="spec",
    help="Manage living specifications (list, create, delete, show).",
    add_completion=False,
)
delta_app = typer.Typer(
    name="delta",
    help="Manage SpecDeltas through their mutation lifecycle (list, create, apply, delete).",
    add_completion=False,
)
index_app = typer.Typer(
    name="index",
    help="Manage and query the embedded SQLite architectural knowledge graph (.eidos/cache/index.db).",
    add_completion=False,
)

app.add_typer(plugin_app, name="plugin")
app.add_typer(spec_app, name="spec")
app.add_typer(delta_app, name="delta")
app.add_typer(index_app, name="index")
console = Console()


@app.command()
def info():
    """Display Eidos engine version, bundled schemas, and platform status."""
    validator = SpecValidator()
    schemas = sorted({s for s in validator._schema_cache if "/" not in s})

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Eidos Engine Version:", f"[bold green]v{eidos.__version__}[/bold green]")
    grid.add_row("Bundled v1 Schemas:", f"[bold yellow]{len(schemas)} loaded[/bold yellow]")
    for s in schemas:
        grid.add_row("", f"• {s}")
    grid.add_row("Supported VCS:", "GitHub, GitLab")
    grid.add_row("Supported Agents:", "Antigravity (agy), Claude Code, Cursor, Copilot")
    grid.add_row("Plugin Standard:", "agent-plugins.org v1.0.0 & agentskills.io")

    console.print(Panel(grid, title="[bold blue]Eidos AI Ecosystem[/bold blue]", expand=False))


@app.command("init")
def init_cmd(
    target_path: Path | None = typer.Argument(
        None,
        help="Target project directory to initialize with Eidos (defaults to current directory).",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="System identifier (e.g. 'my-app'). Defaults to directory name.",
    ),
    title: str | None = typer.Option(
        None,
        "--title",
        "-t",
        help="Human-readable system title (e.g. 'My Application').",
    ),
    with_plugin: bool = typer.Option(
        True,
        "--with-plugin/--no-plugin",
        help="Install the Eidos Agent Plugin into .agents/plugins/eidos.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force initialization even if eidos/ directory already exists.",
    ),
):
    """Initialize a repository with an empty Eidos living specification skeleton."""
    project_dir = target_path or Path.cwd()
    console.print(f"[bold cyan]Initializing empty Eidos skeleton in:[/bold cyan] {project_dir.resolve()}\n")

    success, msg, created_files = init_repository(
        project_dir=project_dir,
        name=name,
        title=title,
        with_plugin=with_plugin,
        force=force,
    )

    if not success:
        console.print(f"[bold red]✗ Initialization Failed:[/bold red] {msg}")
        raise typer.Exit(code=1)

    table = Table(title="Scaffolded Eidos Skeleton", show_header=True)
    table.add_column("Skeleton File", style="bold cyan")
    table.add_column("Path", style="dim")

    for f in created_files:
        rel = f.relative_to(project_dir) if f.is_relative_to(project_dir) else f
        table.add_row(f.name, str(rel))

    console.print(table)
    console.print(f"\n[bold green]✓[/bold green] {msg}")
    console.print("[dim]Next steps:[/dim]")
    console.print("  • Author your system vision in [cyan]eidos/system.adoc[/cyan].")
    console.print("  • Create specifications using [cyan]eidos spec create <type> <id> --title <title>[/cyan] or AI agent skills.")
    console.print("  • Run [cyan]eidos validate eidos[/cyan] to verify specification compliance.")
    if with_plugin:
        console.print("  • Eidos Agent Plugin installed at [cyan].agents/plugins/eidos[/cyan].")


@app.command()
def validate(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to the directory containing .adoc specifications (defaults to ./eidos).",
    ),
):
    """Validate all specification documents against their schemas and verify cross-references."""
    if target_path is None:
        target_path = Path("eidos")
        if not target_path.exists():
            target_path = Path(".")

    if not target_path.exists():
        console.print(f"[bold red]Error:[/bold red] Directory '{target_path}' not found.")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Validating specifications in:[/bold cyan] {target_path.resolve()}\n")

    validator = SpecValidator()
    report = validator.validate_directory(target_path)

    table = Table(title="Eidos Specification Validation", show_header=True, header_style="bold magenta")
    table.add_column("Document ID", style="bold cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Status", justify="center")
    table.add_column("Path", style="dim")

    for doc in report.documents:
        status_label = "[bold green]PASS[/bold green]" if doc.is_valid else "[bold red]FAIL[/bold red]"
        rel_path = (
            str(doc.file_path.relative_to(Path.cwd()))
            if doc.file_path.is_relative_to(Path.cwd())
            else str(doc.file_path)
        )
        table.add_row(doc.doc_id, doc.doc_type, status_label, rel_path)

    console.print(table)

    if not report.is_clean:
        console.print("\n[bold red]Validation Failures Detected:[/bold red]")
        for file_path, errors in report.schema_errors.items():
            console.print(f"\n[bold yellow]{file_path}:[/bold yellow]")
            for err in errors:
                console.print(f"  [red]✗[/red] {err}")

        if report.broken_references:
            console.print("\n[bold red]Broken Cross-References:[/bold red]")
            for ref in report.broken_references:
                console.print(f"  [red]✗[/red] {ref}")

        console.print(f"\n[bold red]Summary:[/bold red] {report.valid_docs}/{report.total_docs} valid documents.")
        raise typer.Exit(code=1)

    console.print(f"\n[bold green]Success![/bold green] All {report.total_docs} specification documents are valid.")


@app.command("lint")
def lint_cmd(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to directory containing .adoc specifications (defaults to ./eidos).",
    ),
):
    """Lint and validate all specification documents against schemas and link integrity."""
    validate(target_path)


@app.command("drift")
def drift_cmd(
    base: str | None = typer.Option(
        None,
        "--base",
        "-b",
        help="Base git reference to compare against (e.g. origin/main, main).",
    ),
    head: str = typer.Option(
        "HEAD",
        "--head",
        help="Head git reference to compare (defaults to HEAD).",
    ),
    uncommitted: bool = typer.Option(
        False,
        "--uncommitted",
        "-u",
        help="Analyze uncommitted working tree and staged changes instead of branch diff.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos specifications directory (defaults to ./eidos).",
    ),
    warn_only: bool = typer.Option(
        False,
        "--warn-only",
        help="Do not exit with code 1 on drift or validation failures.",
    ),
    markdown_out: Path | None = typer.Option(
        None,
        "--markdown",
        "-m",
        help="Write markdown summary to file (e.g. for CI/CD reports).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON verification result.",
    ),
):
    """Detect spec-to-code drift, schema validity, and invariant test coverage."""
    eidos_dir = path or Path("eidos")
    repo_root = Path.cwd()

    result = detect_drift(
        repo_root=repo_root,
        base_ref=base,
        head_ref=head,
        eidos_dir=eidos_dir,
        uncommitted=uncommitted,
    )

    if json_output:
        data = {
            "is_clean": result.is_clean,
            "drift_detected": result.drift_detected,
            "workflow_mode": result.workflow_mode,
            "code_files": result.classification.code_files,
            "spec_files": result.classification.spec_files,
            "delta_files": result.classification.delta_files,
            "test_files": result.classification.test_files,
            "touched_components": result.touched_components,
            "valid_specs": result.validation_report.valid_docs,
            "total_specs": result.validation_report.total_docs,
            "remediation_suggestions": result.remediation_suggestions,
        }
        console.print_json(data=data)
    else:
        # Rich Terminal Output
        mode_badge = (
            "[bold cyan]Planned Mode (SpecDelta)[/bold cyan]"
            if result.workflow_mode == "planned"
            else "[bold green]Direct Mode (Co-Evolution)[/bold green]"
        )
        console.print(f"[bold cyan]Eidos Spec-to-Code Drift Detection[/bold cyan] ({mode_badge})\n")

        table = Table(title="Spec-Code Synchronization Summary", show_header=True)
        table.add_column("Metric", style="bold cyan")
        table.add_column("Count", justify="right")
        table.add_column("Status", justify="center")

        code_count = len(result.classification.code_files)
        spec_count = len(result.classification.spec_files)
        delta_count = len(result.classification.delta_files)

        code_status = (
            "[bold yellow]Needs Spec[/bold yellow]" if result.drift_detected else "[bold green]Synced[/bold green]"
        )
        spec_status = "[bold green]Updated[/bold green]" if spec_count else "[dim]Unchanged[/dim]"
        delta_status = "[bold cyan]Active[/bold cyan]" if delta_count else "[dim]None (Direct)[/dim]"

        table.add_row("Modified Code Files", str(code_count), code_status)
        table.add_row("Modified Living Specs", str(spec_count), spec_status)
        table.add_row("Modified SpecDeltas", str(delta_count), delta_status)
        table.add_row(
            "Modified Test Files",
            str(len(result.classification.test_files)),
            "[bold green]✓[/bold green]" if result.classification.test_files else "[dim]—[/dim]",
        )

        val_status = (
            "[bold green]100% Pass[/bold green]" if result.validation_report.is_clean else "[bold red]FAIL[/bold red]"
        )
        table.add_row(
            "Living Specs Validated",
            f"{result.validation_report.valid_docs}/{result.validation_report.total_docs}",
            val_status,
        )

        console.print(table)

        if result.touched_components:
            console.print(f"\n[bold blue]Impacted Components:[/bold blue] {', '.join(result.touched_components)}")

        if result.drift_detected:
            console.print("\n[bold yellow]⚠️  SPEC-TO-CODE DRIFT DETECTED:[/bold yellow]")
            for rem in result.remediation_suggestions:
                console.print(f"  [yellow]•[/yellow] {rem}")
            console.print(
                "\n[dim]Tip: Run [bold green]eidos reconcile[/bold green] to get suggestions for updating living specifications.[/dim]"
            )

        if not result.validation_report.is_clean:
            console.print("\n[bold red]✗ Specification Schema Errors:[/bold red]")
            for p, errs in result.validation_report.schema_errors.items():
                console.print(f"  [red]• {p}:[/red] {'; '.join(errs)}")

        if result.is_clean:
            console.print(
                "\n[bold green]✓ Verification Passed![/bold green] Living specifications and code are synchronized."
            )

    if markdown_out:
        markdown_out.write_text(result.summary_markdown, encoding="utf-8")
        console.print(f"\n[dim]Saved markdown summary to: {markdown_out}[/dim]")

    if not result.is_clean and not warn_only:
        raise typer.Exit(code=1)


@app.command("reconcile")
def reconcile_cmd(
    base: str | None = typer.Option(
        None,
        "--base",
        "-b",
        help="Base git reference to compare against.",
    ),
    uncommitted: bool = typer.Option(
        False,
        "--uncommitted",
        "-u",
        help="Analyze uncommitted working tree changes.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
    prompt: bool = typer.Option(
        False,
        "--prompt",
        help="Print structured AI prompt for copy-pasting to an AI assistant.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON reconcile analysis.",
    ),
):
    """Analyze code changes and suggest living spec updates to eliminate drift."""
    eidos_dir = path or Path("eidos")
    repo_root = Path.cwd()

    result = reconcile_specs(
        repo_root=repo_root,
        base_ref=base,
        eidos_dir=eidos_dir,
        uncommitted=uncommitted,
    )

    if json_output:
        data = {
            "changed_code_files": result.changed_code_files,
            "suggestions": [
                {
                    "code_file": s.code_file,
                    "candidate_specs": s.candidate_specs,
                    "reason": s.reason,
                }
                for s in result.suggestions
            ],
            "catalog": result.catalog,
            "ai_prompt": result.ai_prompt,
        }
        console.print_json(data=data)
        return

    if prompt:
        console.print(result.ai_prompt)
        return

    console.print("[bold cyan]Eidos Living Specification Reconciliation[/bold cyan]\n")

    if not result.changed_code_files:
        console.print("[bold green]✓ No code drift detected.[/bold green] Working tree and living specs are in sync.")
        return

    table = Table(
        title=f"Code Modifications & Candidate Specs ({len(result.changed_code_files)} files)", show_header=True
    )
    table.add_column("Modified Code File", style="bold cyan")
    table.add_column("Candidate Living Specs", style="white")

    for s in result.suggestions:
        table.add_row(s.code_file, "\n".join(s.candidate_specs))

    console.print(table)
    console.print(
        "\n[dim]Next step:[/dim] Update the candidate living specs in [cyan]eidos/[/cyan] to reflect these changes."
    )
    console.print(
        "[dim]Use [bold green]eidos reconcile --prompt[/bold green] to generate a structured AI prompt for your coding agent.[/dim]\n"
    )


@app.command("sync")
def sync_shortcut(
    base: str | None = typer.Option(None, "--base", "-b", help="Base git reference to compare against."),
    uncommitted: bool = typer.Option(False, "--uncommitted", "-u", help="Analyze uncommitted working tree changes."),
    path: Path | None = typer.Option(None, "--path", "-p", help="Path to eidos directory."),
    prompt: bool = typer.Option(False, "--prompt", help="Print structured AI prompt."),
):
    """Shortcut alias for 'eidos reconcile'."""
    reconcile_cmd(base=base, uncommitted=uncommitted, path=path, prompt=prompt)


@app.command("serve")
def serve_cmd(
    target_path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to directory containing .adoc specifications (defaults to ./eidos).",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        "-h",
        help="Host interface to bind the portal server to.",
    ),
    port: int = typer.Option(
        3000,
        "--port",
        help="Port number to serve the portal on.",
    ),
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Automatically open the portal in your default web browser.",
    ),
):
    """Launch the interactive Eidos Living Web Portal."""
    target = target_path or Path("eidos")
    if not target.exists():
        console.print(f"[bold red]Error:[/bold red] Specifications directory '{target}' not found.")
        raise typer.Exit(code=1)

    run_portal_server(eidos_dir=target, host=host, port=port, open_browser=open_browser)


@app.command("build-site")
def build_site_cmd(
    target_path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to directory containing .adoc specifications (defaults to ./eidos).",
    ),
    out_dir: Path = typer.Option(
        Path("site"),
        "--out",
        "-o",
        help="Target output directory for the static site (defaults to ./site).",
    ),
):
    """Export the Eidos Living Web Portal as a self-contained static site for GitHub/GitLab Pages."""
    target = target_path or Path("eidos")
    if not target.exists():
        console.print(f"[bold red]Error:[/bold red] Specifications directory '{target}' not found.")
        raise typer.Exit(code=1)

    generated_dir = build_static_portal(eidos_dir=target, output_dir=out_dir)
    console.print(f"[bold green]✓ Successfully generated static portal at:[/bold green] {generated_dir.resolve()}")
    console.print(f"  Entrypoint: {generated_dir / 'index.html'}")
    console.print(f"  Data payload: {generated_dir / 'data.json'}")


def _render_containers_table(target_path: Path | None = None) -> None:
    search_dir = target_path or Path("eidos/architecture")
    eidos_dir = search_dir.parent if search_dir.name == "architecture" else search_dir
    if not search_dir.exists() and not eidos_dir.exists():
        console.print(f"[bold red]Error:[/bold red] Directory '{search_dir}' not found.")
        raise typer.Exit(code=1)

    idx = get_architectural_index(eidos_dir=eidos_dir)
    found_containers = idx.get_components()

    if not found_containers:
        console.print(f"[bold yellow]No container specifications found in '{search_dir}'.[/bold yellow]")
        return

    table = Table(title="Eidos Monorepo Containers (C4 Level 2)", show_header=True, header_style="bold blue")
    table.add_column("Container ID", style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Container Type", style="yellow")
    table.add_column("Runtime", style="magenta")
    table.add_column("Package Path", style="dim")
    table.add_column("L3 Components", style="white")

    for c in found_containers:
        pkg = c.get("package_path") or "centralized"
        internals = ", ".join(m.get("name", "") for m in (c.get("components", []) or c.get("c4_internal_components", []))) or "N/A"
        table.add_row(
            c.get("id", "N/A"),
            c.get("name", "N/A"),
            c.get("container_type", "N/A"),
            c.get("runtime", "N/A"),
            str(pkg),
            internals,
        )

    console.print(table)


@app.command("containers")
def containers(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to scan for container manifests (defaults to eidos/architecture).",
    ),
):
    """List all C4 Level 2 containers, runtimes, package paths, and internal building blocks."""
    _render_containers_table(target_path=target_path)


@app.command()
def components(
    container_id: str | None = typer.Argument(
        None,
        help="Optional container ID to filter components (e.g. CNT-EIDOS-CORE).",
    ),
    target_path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to scan for component manifests (defaults to eidos/architecture).",
    ),
):
    """List all C4 Level 3 internal components across containers."""
    search_dir = target_path or Path("eidos/architecture")
    eidos_dir = search_dir.parent if search_dir.name == "architecture" else search_dir
    if not search_dir.exists() and not eidos_dir.exists():
        console.print(f"[bold red]Error:[/bold red] Directory '{search_dir}' not found.")
        raise typer.Exit(code=1)

    idx = get_architectural_index(eidos_dir=eidos_dir)
    found_containers = idx.get_components()

    if not found_containers:
        console.print(f"[bold yellow]No component or container specifications found in '{search_dir}'.[/bold yellow]")
        return

    table = Table(title="Eidos C4 Level 3 Components (In-Process Building Blocks)", show_header=True, header_style="bold blue")
    table.add_column("Component ID", style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Owning Container", style="yellow")
    table.add_column("Source Path", style="dim")
    table.add_column("Responsibility", style="white")

    cid_filter = container_id.strip().upper() if container_id else None
    count = 0
    for c in found_containers:
        cid = c.get("id", "N/A")
        if cid_filter and cid_filter not in cid.upper():
            continue
        internals = c.get("components", []) or c.get("c4_internal_components", [])
        for m in internals:
            count += 1
            table.add_row(
                m.get("id", "CMP-INTERNAL"),
                m.get("name", "N/A"),
                cid,
                m.get("source_path", "N/A"),
                m.get("responsibility", "N/A"),
            )

    if count == 0 and cid_filter:
        console.print(f"[bold yellow]No components found for container '{container_id}'.[/bold yellow]")
        return

    console.print(table)


@app.command()
def mcp():
    """Launch the stdio Model Context Protocol (MCP) server for AI agents."""
    run_mcp_server()


@app.command("context")
def context_cmd(
    files: list[str] | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Target source code file path(s) to slice context for.",
    ),
    component: str | None = typer.Option(
        None,
        "--component",
        "-c",
        help="Target component identifier (e.g. 'CMP-EIDOS-CLI').",
    ),
    task: str | None = typer.Option(
        None,
        "--task",
        "-t",
        help="Free-text task description or prompt to slice architectural context for.",
    ),
    tier: str = typer.Option(
        "guardrails",
        "--tier",
        "-T",
        help="Context slice verbosity tier: 'anchor' (~140 tokens), 'guardrails' (~260 tokens), 'deep' (~500 tokens). Defaults to 'guardrails'.",
    ),
    format: str = typer.Option(
        "markdown",
        "--format",
        help="Output format: 'markdown' or 'json' (defaults to 'markdown').",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Extract a compact architectural subgraph slice (C4 components, contracts, capabilities, invariants, policies)."""
    eidos_dir = path or Path("eidos")
    if not eidos_dir.exists():
        eidos_dir = Path(".")

    idx = get_architectural_index(eidos_dir=eidos_dir)
    slice_result = idx.get_tiered_context_slice(
        files=files,
        component=component,
        task=task,
        tier=tier,
    )

    if format.lower() == "json":
        typer.echo(slice_result.to_json())
    else:
        typer.echo(slice_result.to_markdown())


# -------------------------------------------------------------------------
# Index Embedded Knowledge Graph & Hybrid Retrieval Commands
# -------------------------------------------------------------------------


@index_app.command("status")
def index_status_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Display health, schema version, cache size, and statistics of the architectural index."""
    eidos_dir = path or Path("eidos")
    idx = get_architectural_index(eidos_dir=eidos_dir)
    status = idx.get_index_status()

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
    grid.add_row("AST Symbol Mappings:", f"[bold white]{status['total_symbols']}[/bold white]")

    console.print(Panel(grid, title="[bold blue]Eidos Architectural Knowledge Graph Index[/bold blue]", expand=False))

    if status.get("entity_counts_by_type"):
        table = Table(title="Entities by Type", show_header=True)
        table.add_column("Type", style="bold cyan")
        table.add_column("Count", justify="right", style="green")
        for etype, count in sorted(status["entity_counts_by_type"].items()):
            table.add_row(etype, str(count))
        console.print(table)


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
        help="Target architectural entity ID (e.g. 'CMP-EIDOS-CORE', 'CAP-PROD-001').",
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




# -------------------------------------------------------------------------
# Spec Living Specifications Management Commands
# -------------------------------------------------------------------------


@spec_app.command("list")
def spec_list_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by spec type (capability, policy, contract, component, domain, persona, delta).",
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (active, draft, proposed, applied).",
    ),
):
    """List living specifications in the repository."""
    specs = list_specs(path, type_filter=type_filter, status_filter=status_filter)
    if not specs:
        console.print(f"[yellow]No specifications found under '{path}'.[/yellow]")
        return

    table = Table(title=f"Eidos Living Specifications ({len(specs)} found)", show_header=True)
    table.add_column("Document ID", style="bold cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Title", style="white")
    table.add_column("Relative Path", style="dim")

    for s in specs:
        table.add_row(
            s["id"],
            s["type"],
            str(s["status"]),
            s["title"],
            str(s["relative_path"]),
        )
    console.print(table)


@spec_app.command("show")
def spec_show_cmd(
    target: str = typer.Argument(
        ...,
        help="Document ID, relative path, or filename of the specification.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
):
    """Display frontmatter metadata and preview of a specification document."""
    file_path = find_spec_by_id_or_path(path, target)
    if not file_path:
        console.print(f"[bold red]Error:[/bold red] Specification '{target}' not found under '{path}'.")
        raise typer.Exit(code=1)

    doc = parse_spec_file(file_path)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Document ID:", f"[bold green]{doc.doc_id}[/bold green]")
    grid.add_row("Type:", doc.doc_type)
    grid.add_row("Title:", doc.title or "N/A")
    grid.add_row("Status:", str(doc.frontmatter.get("status", "N/A")))
    grid.add_row("Path:", str(file_path))

    console.print(Panel(grid, title=f"[bold blue]{doc.title or doc.doc_id}[/bold blue]", expand=False))


@spec_app.command("create")
def spec_create_cmd(
    doc_type: str = typer.Argument(
        ...,
        help="Specification type: capability, domain, policy, contract, component, persona, delta, exploration.",
    ),
    doc_id: str = typer.Argument(
        ...,
        help="Unique identifier (e.g. CAP-AUTH-001, POL-003, CTR-PAYMENT-API).",
    ),
    title: str = typer.Option(
        ...,
        "--title",
        "-t",
        help="Human-readable title for the specification document.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
    domain: str | None = typer.Option(
        None,
        "--domain",
        "-d",
        help="Parent domain ID for capabilities (e.g. DOM-SPEC-ENGINE).",
    ),
    kind: str = typer.Option(
        "feature_addition",
        "--kind",
        "-k",
        help="Mutation kind for SpecDeltas (feature_addition, bugfix, refactor, modification).",
    ),
    motivation: str = typer.Option(
        "Proposed specification change.",
        "--motivation",
        "-m",
        help="Motivation/problem statement for deltas.",
    ),
    category: str = typer.Option(
        "governance",
        "--category",
        "-c",
        help="Category for policies (governance, architectural, security, testing).",
    ),
    rule: str = typer.Option(
        "Authoritative rule statement.",
        "--rule",
        "-r",
        help="Rule statement for policies.",
    ),
    protocol: str = typer.Option(
        "cli_args",
        "--protocol",
        help="Protocol for contracts (cli_args, openapi, protobuf, graphql, json_rpc_stdio).",
    ),
    owning_component: str = typer.Option(
        "CMP-EIDOS-CORE",
        "--owning-component",
        help="Owning component ID for contracts.",
    ),
    client_id: str | None = typer.Option(
        None,
        "--client",
        help="Client or brand identifier for client-specific design systems (e.g. acme).",
    ),
    extends_system: str | None = typer.Option(
        None,
        "--extends",
        help="Base design system ID or path to extend (e.g. DS-CORE).",
    ),
    route: str = typer.Option(
        "/view",
        "--route",
        help="URL route or view identifier for screens.",
    ),
    design_system: str = typer.Option(
        "DS-CORE",
        "--system",
        help="Owning design system ID for UI components.",
    ),
    implements_component: str | None = typer.Option(
        None,
        "--implements",
        help="Base component ID being overridden/specialized by a UI component.",
    ),
):
    """Scaffold a new living specification or SpecDelta."""
    try:
        dest_file = create_spec(
            eidos_dir=path,
            doc_type_key=doc_type,
            raw_id=doc_id,
            title=title,
            domain=domain,
            kind=kind,
            motivation=motivation,
            category=category,
            rule=rule,
            protocol=protocol,
            owning_component=owning_component,
            client_id=client_id,
            extends_system=extends_system,
            route=route,
            design_system=design_system,
            implements_component=implements_component,
        )
        rel = dest_file.relative_to(path) if dest_file.is_relative_to(path) else dest_file
        console.print(
            f"[bold green]✓ Created and validated specification ({doc_id}):[/bold green] [bold]{dest_file.name}[/bold]"
        )
        console.print(f"  [dim]Location: {rel}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to create specification:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@spec_app.command("delete")
def spec_delete_cmd(
    target: str = typer.Argument(
        ...,
        help="Document ID, path, or filename of the specification to delete.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force deletion even if other specifications reference this document.",
    ),
):
    """Safely delete a living specification with reference checks."""
    success, msg, refs = delete_spec(path, target, force=force)
    if not success:
        console.print(f"[bold red]✗ Deletion Blocked:[/bold red] {msg}")
        if refs:
            console.print("\n[yellow]Referencing specifications:[/yellow]")
            for r in refs:
                rel = r.relative_to(path) if r.is_relative_to(path) else r
                console.print(f"  • {rel}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓[/bold green] {msg}")
    if refs:
        console.print(f"[yellow]Warning:[/yellow] Deleted despite {len(refs)} reference(s) (--force used).")


# -------------------------------------------------------------------------
# SpecDelta Lifecycle Management Commands
# -------------------------------------------------------------------------


@delta_app.command("list")
def delta_list_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter deltas by status (e.g. proposed, in_review, applied, rejected).",
    ),
):
    """List SpecDeltas in active/ and applied/ states."""
    deltas = list_deltas(path, status_filter=status_filter)
    if not deltas:
        console.print(f"[yellow]No SpecDeltas found under '{path}/deltas'.[/yellow]")
        return

    table = Table(title=f"Eidos SpecDeltas ({len(deltas)} found)", show_header=True)
    table.add_column("Delta ID", style="bold cyan")
    table.add_column("Status", style="green")
    table.add_column("Kind", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("State", style="yellow")
    table.add_column("Path", style="dim")

    for d in deltas:
        state_str = "[bold green]ACTIVE[/bold green]" if d["is_active"] else "[dim]APPLIED[/dim]"
        table.add_row(
            d["id"],
            str(d["status"]),
            d["kind"],
            d["title"],
            state_str,
            str(d["relative_path"]),
        )
    console.print(table)


@delta_app.command("create")
def delta_create_cmd(
    delta_id: str = typer.Argument(
        ...,
        help="Unique delta identifier (e.g. DLT-2026-002).",
    ),
    title: str = typer.Option(
        ...,
        "--title",
        "-t",
        help="Title of the proposed change.",
    ),
    kind: str = typer.Option(
        "feature_addition",
        "--kind",
        "-k",
        help="Mutation kind: feature_addition, bugfix, refactor, modification, feature_removal.",
    ),
    motivation: str = typer.Option(
        "Problem statement and motivation.",
        "--motivation",
        "-m",
        help="Problem statement and motivation.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Create a new SpecDelta in flat storage (eidos/deltas/<id>.adoc) and sync views."""
    try:
        dest_file = create_spec(
            eidos_dir=path,
            doc_type_key="delta",
            raw_id=delta_id,
            title=title,
            kind=kind,
            motivation=motivation,
        )
        rel = dest_file.relative_to(path) if dest_file.is_relative_to(path) else dest_file
        console.print(f"[bold green]✓ Created SpecDelta:[/bold green] [bold]{dest_file.name}[/bold]")
        console.print(f"  [dim]Location: {rel}[/dim]")
        console.print("  [dim]Status: proposed (symlinked in eidos/deltas/by-status/active/ and proposed/)[/dim]")
        console.print(f"  [dim]Next step: Edit mutations in {rel} and update living specs.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to create SpecDelta:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@delta_app.command("apply")
def delta_apply_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename of the SpecDelta to apply.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Reconcile a SpecDelta in-place, updating status to 'applied' and syncing symlink views."""
    success, msg, applied_file = apply_delta(path, target)
    if not success:
        console.print(f"[bold red]✗ Failed to apply SpecDelta:[/bold red] {msg}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓[/bold green] {msg}")
    if applied_file:
        rel = applied_file.relative_to(path) if applied_file.is_relative_to(path) else applied_file
        console.print(f"  [dim]Updated in-place: {rel}[/dim]")
        console.print(f"  [dim]Symlink moved to: eidos/deltas/by-status/applied/{applied_file.name}[/dim]")


@delta_app.command("status")
def delta_status_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Display a status summary of all SpecDeltas and their lifecycle states."""
    summary = get_delta_status_summary(path)
    total = summary["total"]
    active_count = summary["active_count"]
    by_status = summary["by_status"]
    views_dir = summary["views_dir"]

    console.print(f"[bold cyan]SpecDelta Lifecycle Status Dashboard[/bold cyan] ({total} total)\n")

    summary_table = Table(title="SpecDelta Lifecycle Summary", show_header=True)
    summary_table.add_column("Lifecycle State", style="bold cyan")
    summary_table.add_column("Count", style="bold white", justify="right")
    summary_table.add_column("Description", style="dim")

    summary_table.add_row("Proposed", str(len(by_status.get("proposed", []))), "Draft mutations awaiting review")
    summary_table.add_row("In Review", str(len(by_status.get("in_review", []))), "Under review by team or agents")
    summary_table.add_row(
        "Approved", str(len(by_status.get("approved", []))), "Approved, awaiting implementation/merge"
    )
    summary_table.add_row("Applied", str(len(by_status.get("applied", []))), "Reconciled into living specifications")
    summary_table.add_row("Rejected", str(len(by_status.get("rejected", []))), "Abandoned or rejected proposals")
    summary_table.add_row(
        "In-Flight (Active)", f"[bold yellow]{active_count}[/bold yellow]", "Total active/unmerged deltas"
    )

    console.print(summary_table)

    active_deltas = by_status.get("proposed", []) + by_status.get("in_review", []) + by_status.get("approved", [])
    if active_deltas:
        console.print("\n[bold yellow]In-Flight SpecDeltas:[/bold yellow]")
        active_table = Table(show_header=True)
        active_table.add_column("Delta ID", style="bold cyan")
        active_table.add_column("Status", style="yellow")
        active_table.add_column("Kind", style="dim")
        active_table.add_column("Title", style="white")
        for d in active_deltas:
            active_table.add_row(d["id"], str(d["status"]), str(d["kind"]), d["title"])
        console.print(active_table)
    else:
        console.print("\n[dim]No in-flight SpecDeltas. Living specifications are up to date.[/dim]")

    rel_views = views_dir.relative_to(path.parent) if views_dir.is_relative_to(path.parent) else views_dir
    console.print(f"\n[dim]Gitignored symlink views: {rel_views} (use 'eidos delta sync-views' to refresh)[/dim]")


@delta_app.command("sync-views")
def delta_sync_views_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Regenerate gitignored symlinks in eidos/deltas/by-status/ categorized by lifecycle state."""
    synced = sync_delta_views(path)
    total_symlinks = sum(len(v) for v in synced.values())
    views_dir = path / "deltas" / "by-status"
    rel_views = views_dir.relative_to(path.parent) if views_dir.is_relative_to(path.parent) else views_dir
    console.print(f"[bold green]✓ Synchronized {total_symlinks} symlinks in {rel_views}[/bold green]")
    for cat, items in synced.items():
        if items:
            console.print(f"  • [bold cyan]{cat}[/bold cyan]: {len(items)} deltas")


@delta_app.command("delete")
def delta_delete_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename to delete.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force deletion.",
    ),
):
    """Delete a SpecDelta file."""
    success, msg, _ = delete_spec(path, target, force=force)
    if not success:
        console.print(f"[bold red]✗ Failed to delete SpecDelta:[/bold red] {msg}")
        raise typer.Exit(code=1)
    console.print(f"[bold green]✓[/bold green] {msg}")


# -------------------------------------------------------------------------
# Top-Level Convenience Shortcuts
# -------------------------------------------------------------------------


@app.command("create")
def create_shortcut(
    doc_type: str = typer.Argument(
        ..., help="Specification type: capability, domain, policy, contract, component, persona, delta."
    ),
    doc_id: str = typer.Argument(..., help="Document ID (e.g. CAP-AUTH-001, POL-002, DLT-2026-002)."),
    title: str = typer.Option(..., "--title", "-t", help="Title of the document."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Parent domain for capabilities."),
    kind: str = typer.Option("feature_addition", "--kind", "-k", help="Mutation kind for deltas."),
    motivation: str = typer.Option("Motivation statement.", "--motivation", "-m", help="Motivation statement."),
    category: str = typer.Option("governance", "--category", "-c", help="Category for policies."),
    rule: str = typer.Option("Rule statement.", "--rule", "-r", help="Rule statement for policies."),
):
    """Shortcut to scaffold a new living specification or SpecDelta."""
    spec_create_cmd(
        doc_type=doc_type,
        doc_id=doc_id,
        title=title,
        path=path,
        domain=domain,
        kind=kind,
        motivation=motivation,
        category=category,
        rule=rule,
    )


@app.command("delete")
def delete_shortcut(
    target: str = typer.Argument(..., help="Document ID or path to delete."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion even if referenced."),
):
    """Shortcut to safely delete a specification document or delta."""
    spec_delete_cmd(target=target, path=path, force=force)


@app.command("list")
def list_shortcut(
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    type_filter: str | None = typer.Option(None, "--type", "-t", help="Filter by type."),
    status_filter: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
):
    """Shortcut to list all living specifications."""
    spec_list_cmd(path=path, type_filter=type_filter, status_filter=status_filter)


@plugin_app.command("install")
def plugin_install(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Installation target in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project.",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Overwrite existing files at destination.",
    ),
):
    """Install the Eidos agent plugin strictly inside project scope (.agents/plugins/eidos, agent-plugins.org v1.0.0)."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    console.print(
        f"[bold cyan]Installing Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{resolved_target.value}[/bold green])...\n"
    )

    try:
        results = install_plugin(target=resolved_target, project_dir=project_dir, dest_path=dest, force=force)
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
                console.print(f"  [dim]Destination: {res.destination}[/dim]")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] The Eidos Agent Plugin is installed and ready.")
        console.print("AI agents can now discover Eidos living specifications, C4 architectures, and MCP tools.\n")
    except Exception as e:
        console.print(f"[bold red]Installation Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command("install")
def install_shortcut(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Installation target in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project.",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Overwrite existing files at destination.",
    ),
):
    """Shortcut alias for 'eidos plugin install'."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    plugin_install(target=resolved_target, project_dir=project_dir, dest=dest, force=force)


@plugin_app.command("uninstall")
def plugin_uninstall(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to remove in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Uninstall / remove the Eidos agent plugin from project scope."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    console.print(
        f"[bold cyan]Uninstalling Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{resolved_target.value}[/bold green])...\n"
    )

    try:
        results = uninstall_plugin(target=resolved_target, project_dir=project_dir, dest_path=dest)
        for res in results:
            if res.removed:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold yellow]•[/bold yellow] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] Plugin cleanup complete.")
    except Exception as e:
        console.print(f"[bold red]Uninstall Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command("uninstall")
def uninstall_shortcut(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to remove in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Shortcut alias for 'eidos plugin uninstall'."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    plugin_uninstall(target=resolved_target, project_dir=project_dir, dest=dest)


@plugin_app.command("remove")
def plugin_remove(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to remove in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Alias for 'eidos plugin uninstall'."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    plugin_uninstall(target=resolved_target, project_dir=project_dir, dest=dest)


@plugin_app.command("version")
def plugin_version(
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom plugin directory within the project to inspect.",
    ),
):
    """Display and compare bundled vs installed Agent Plugin version."""
    status_info = check_plugin_version_status(project_dir=project_dir, dest_path=dest)

    table = Table(title="Eidos Agent Plugin Version Status", show_header=True)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Bundled Version", f"[bold green]{status_info['bundled_version']}[/bold green] (eidos engine)")

    inst_ver = status_info["installed_version"]
    if inst_ver:
        table.add_row("Installed Version", f"[bold cyan]{inst_ver}[/bold cyan]")
    else:
        table.add_row("Installed Version", "[dim]Not installed[/dim]")

    table.add_row("Plugin Location", str(status_info["plugin_dir"]))

    status = status_info["status"]
    if status == "up_to_date":
        status_badge = "[bold green]✓ Up to date[/bold green]"
    elif status == "outdated":
        status_badge = f"[bold yellow]▲ Outdated ({status_info['status_text']})[/bold yellow]"
    elif status == "newer":
        status_badge = f"[bold blue]◆ Newer ({inst_ver} > {status_info['bundled_version']})[/bold blue]"
    elif status == "invalid":
        status_badge = f"[bold red]✗ Invalid SemVer ({inst_ver})[/bold red]"
    else:
        status_badge = "[dim]○ Not installed in project scope[/dim]"

    table.add_row("Sync Status", status_badge)

    console.print(table)

    if status == "outdated":
        console.print(
            "\n[bold yellow]Update Available:[/bold yellow] Run [bold green]eidos plugin update[/bold green] to upgrade to the latest bundled version.\n"
        )
    elif status == "not_installed":
        console.print(
            "\n[dim]Note:[/dim] Run [bold green]eidos plugin install[/bold green] to install the plugin into this project.\n"
        )


@plugin_app.command("update")
def plugin_update(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to update in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to update.",
    ),
):
    """Update project-scoped Agent Plugin to the latest bundled version."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    console.print(
        f"[bold cyan]Updating Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{resolved_target.value}[/bold green])...\n"
    )

    try:
        prev_ver, current_ver, results = update_plugin(target=resolved_target, project_dir=project_dir, dest_path=dest)
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        if prev_ver is None:
            console.print(
                f"\n[bold green]Success![/bold green] Installed Eidos Agent Plugin at [bold cyan]v{current_ver}[/bold cyan].\n"
            )
        elif prev_ver == current_ver:
            console.print(
                f"\n[bold green]Success![/bold green] Plugin is already at latest version [bold cyan]v{current_ver}[/bold cyan] (files refreshed).\n"
            )
        else:
            console.print(
                f"\n[bold green]Success![/bold green] Upgraded Eidos Agent Plugin from [bold yellow]v{prev_ver}[/bold yellow] to [bold cyan]v{current_ver}[/bold cyan]!\n"
            )
    except Exception as e:
        console.print(f"[bold red]Update Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@plugin_app.command("upgrade")
def plugin_upgrade(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to update in project scope: project, antigravity, cursor, claude, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, cursor, claude, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to update.",
    ),
):
    """Alias for 'eidos plugin update'."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    plugin_update(target=resolved_target, project_dir=project_dir, dest=dest)


@plugin_app.command("validate")
def plugin_validate(
    plugin_path: Path | None = typer.Argument(
        None,
        help="Path to plugin directory (defaults to assets/plugin_reference or bundled).",
    ),
):
    """Validate an Agent Plugin against agent-plugins.org v1.0.0 and agentskills.io specifications."""
    target_dir = plugin_path
    if target_dir is None:
        try:
            target_dir = locate_plugin_bundle()
        except FileNotFoundError:
            target_dir = Path("assets/plugin_reference")

    console.print(f"[bold cyan]Validating Agent Plugin at:[/bold cyan] {target_dir.resolve()}\n")
    report = validate_plugin_package(target_dir)

    table = Table(title="Agent Plugin Conformance (agent-plugins.org v1.0.0)", show_header=True)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Plugin Name", str(report.plugin_name or "N/A"))
    table.add_row("Plugin Version", str(report.plugin_version or "N/A"))
    table.add_row(
        "Overall Status",
        "[bold green]CONFORMANT[/bold green]" if report.is_valid else "[bold red]NON-CONFORMANT[/bold red]",
    )
    table.add_row("Discovered Skills", ", ".join(report.skills) if report.skills else "None")
    table.add_row("Configured MCP Servers", ", ".join(report.mcp_servers) if report.mcp_servers else "None")
    table.add_row("Lifecycle Hooks", ", ".join(report.hooks) if report.hooks else "None")
    table.add_row("Client Extensions", ", ".join(report.extensions) if report.extensions else "None")

    console.print(table)

    if report.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in report.warnings:
            console.print(f"  [yellow]![/yellow] {w}")

    if not report.is_valid:
        console.print("\n[bold red]Validation Errors:[/bold red]")
        for err in report.errors:
            console.print(f"  [red]✗[/red] {err}")
        raise typer.Exit(code=1)

    console.print("\n[bold green]Success![/bold green] Plugin strictly satisfies agent-plugins.org v1.0.0.")


@plugin_app.command("info")
def plugin_info(
    plugin_path: Path | None = typer.Argument(
        None,
        help="Path to plugin directory (defaults to assets/plugin_reference or bundled).",
    ),
):
    """Display metadata and configuration for the Eidos Agent Plugin."""
    target_dir = plugin_path
    if target_dir is None:
        try:
            target_dir = locate_plugin_bundle()
        except FileNotFoundError:
            target_dir = Path("assets/plugin_reference")

    report = validate_plugin_package(target_dir)
    manifest_file = target_dir / "plugin.json"
    manifest_data = {}
    if manifest_file.exists():
        with open(manifest_file, encoding="utf-8") as f:
            manifest_data = json.load(f)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Plugin Identifier:", f"[bold green]{manifest_data.get('name')}[/bold green]")
    grid.add_row("Version:", str(manifest_data.get("version")))
    grid.add_row("Description:", str(manifest_data.get("description")))
    grid.add_row("License:", str(manifest_data.get("license")))
    grid.add_row("Specification:", "agent-plugins.org v1.0.0")
    grid.add_row("Skills:", ", ".join(report.skills) or "None")
    grid.add_row("MCP Servers:", ", ".join(report.mcp_servers) or "None")
    grid.add_row("Lifecycle Hooks:", ", ".join(report.hooks) or "None")
    grid.add_row("Extensions:", ", ".join(report.extensions) or "None")

    console.print(Panel(grid, title="[bold blue]Eidos Agent Plugin Manifest[/bold blue]", expand=False))


if __name__ == "__main__":
    app()
