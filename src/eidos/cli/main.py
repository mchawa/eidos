"""Main CLI entrypoint for Eidos."""

import json
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import eidos
from eidos.core.validator import SpecValidator
from eidos.core.parser import parse_spec_file
from eidos.mcp.server import run_mcp_server
from eidos.plugin.validator import validate_plugin_package
from eidos.plugin.installer import (
    install_plugin,
    uninstall_plugin,
    update_plugin,
    PluginInstallTarget,
    locate_plugin_bundle,
)
from eidos.plugin.version import (
    check_plugin_version_status,
    get_bundled_plugin_version,
    get_installed_plugin_version,
)

from eidos.core.scaffolder import (
    create_spec,
    delete_spec,
    list_specs,
    list_deltas,
    apply_delta,
    find_spec_by_id_or_path,
    sync_delta_views,
    get_delta_status_summary,
    init_repository,
)

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

app.add_typer(plugin_app, name="plugin")
app.add_typer(spec_app, name="spec")
app.add_typer(delta_app, name="delta")
console = Console()


@app.command()
def info():
    """Display Eidos engine version, bundled schemas, and platform status."""
    validator = SpecValidator()
    schemas = sorted(set(s for s in validator._schema_cache.keys() if "/" not in s))

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
    target_path: Optional[Path] = typer.Argument(
        None,
        help="Target project directory to initialize with Eidos (defaults to current directory).",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name", "-n",
        help="System identifier (e.g. 'my-app'). Defaults to directory name.",
    ),
    title: Optional[str] = typer.Option(
        None,
        "--title", "-t",
        help="Human-readable system title (e.g. 'My Application').",
    ),
    with_plugin: bool = typer.Option(
        True,
        "--with-plugin/--no-plugin",
        help="Install the Eidos Agent Plugin into .agents/plugins/eidos.",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
        help="Force initialization even if eidos/ directory already exists.",
    ),
):
    """Initialize a repository with Eidos living specifications, Arc42/C4 architecture, and governance."""
    project_dir = target_path or Path.cwd()
    console.print(f"[bold cyan]Initializing Eidos living specifications in:[/bold cyan] {project_dir.resolve()}\n")

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

    table = Table(title="Scaffolded Eidos Living Specifications", show_header=True)
    table.add_column("Specification Document", style="bold cyan")
    table.add_column("Path", style="dim")

    for f in created_files:
        rel = f.relative_to(project_dir) if f.is_relative_to(project_dir) else f
        table.add_row(f.name, str(rel))

    console.print(table)
    console.print(f"\n[bold green]✓[/bold green] {msg}")
    console.print("[dim]Next steps:[/dim]")
    console.print("  • Edit [cyan]eidos/system.adoc[/cyan] to tailor system vision and strategic pillars.")
    console.print("  • Edit [cyan]eidos/architecture/system-architecture.adoc[/cyan] to map C4 containers.")
    console.print("  • Run [cyan]eidos validate eidos[/cyan] to verify specification compliance.")
    if with_plugin:
        console.print("  • Eidos Agent Plugin installed at [cyan].agents/plugins/eidos[/cyan].")


@app.command()
def validate(
    target_path: Optional[Path] = typer.Argument(
        None,
        help="Path to the directory containing .adoc specifications (defaults to ./eidos).",
    )
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
        rel_path = str(doc.file_path.relative_to(Path.cwd())) if doc.file_path.is_relative_to(Path.cwd()) else str(doc.file_path)
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


@app.command()
def components(
    target_path: Optional[Path] = typer.Argument(
        None,
        help="Path to scan for component manifests (defaults to eidos/architecture).",
    )
):
    """List all C4 components, internal modules, dependencies, and monorepo package mappings."""
    search_dir = target_path or Path("eidos/architecture")
    if not search_dir.exists():
        console.print(f"[bold red]Error:[/bold red] Directory '{search_dir}' not found.")
        raise typer.Exit(code=1)

    found_components = []
    for p in search_dir.rglob("*.adoc"):
        doc = parse_spec_file(p)
        if doc.doc_type == "tech/component":
            found_components.append(doc)

    if not found_components:
        console.print(f"[bold yellow]No component specifications found in '{search_dir}'.[/bold yellow]")
        return

    table = Table(title="Eidos Monorepo & System Components (C4 L2/L3)", show_header=True, header_style="bold blue")
    table.add_column("Component ID", style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Container Type", style="yellow")
    table.add_column("Runtime", style="magenta")
    table.add_column("Package Path", style="dim")
    table.add_column("Internal C4 Modules", style="white")

    for c in found_components:
        fm = c.frontmatter
        pkg = fm.get("package_path", "centralized")
        internals = ", ".join(m.get("name", "") for m in fm.get("c4_internal_components", [])) or "N/A"
        table.add_row(
            c.doc_id,
            fm.get("name", "N/A"),
            fm.get("container_type", "N/A"),
            fm.get("runtime", "N/A"),
            str(pkg),
            internals,
        )

    console.print(table)


@app.command()
def mcp():
    """Launch the stdio Model Context Protocol (MCP) server for AI agents."""
    run_mcp_server()


# -------------------------------------------------------------------------
# Spec Living Specifications Management Commands
# -------------------------------------------------------------------------

@spec_app.command("list")
def spec_list_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path", "-p",
        help="Path to eidos specifications directory.",
    ),
    type_filter: Optional[str] = typer.Option(
        None,
        "--type", "-t",
        help="Filter by spec type (capability, policy, contract, component, domain, persona, delta).",
    ),
    status_filter: Optional[str] = typer.Option(
        None,
        "--status", "-s",
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
        "--path", "-p",
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
        "--title", "-t",
        help="Human-readable title for the specification document.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path", "-p",
        help="Path to eidos specifications directory.",
    ),
    domain: Optional[str] = typer.Option(
        None,
        "--domain", "-d",
        help="Parent domain ID for capabilities (e.g. DOM-PRODUCT-CORE).",
    ),
    kind: str = typer.Option(
        "feature_addition",
        "--kind", "-k",
        help="Mutation kind for SpecDeltas (feature_addition, bugfix, refactor, modification).",
    ),
    motivation: str = typer.Option(
        "Proposed specification change.",
        "--motivation", "-m",
        help="Motivation/problem statement for deltas.",
    ),
    category: str = typer.Option(
        "governance",
        "--category", "-c",
        help="Category for policies (governance, architectural, security, testing).",
    ),
    rule: str = typer.Option(
        "Authoritative rule statement.",
        "--rule", "-r",
        help="Rule statement for policies.",
    ),
    protocol: str = typer.Option(
        "cli_args",
        "--protocol",
        help="Protocol for contracts (cli_args, openapi, protobuf, graphql, json_rpc_stdio).",
    ),
    owning_component: str = typer.Option(
        "CMP-EIDOS-ENGINE",
        "--owning-component",
        help="Owning component ID for contracts.",
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
        )
        rel = dest_file.relative_to(path) if dest_file.is_relative_to(path) else dest_file
        console.print(f"[bold green]✓ Created and validated specification ({doc_id}):[/bold green] [bold]{dest_file.name}[/bold]")
        console.print(f"  [dim]Location: {rel}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to create specification:[/bold red] {e}")
        raise typer.Exit(code=1)


@spec_app.command("delete")
def spec_delete_cmd(
    target: str = typer.Argument(
        ...,
        help="Document ID, path, or filename of the specification to delete.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path", "-p",
        help="Path to eidos specifications directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
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
        "--path", "-p",
        help="Path to eidos directory.",
    ),
    status_filter: Optional[str] = typer.Option(
        None,
        "--status", "-s",
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
        "--title", "-t",
        help="Title of the proposed change.",
    ),
    kind: str = typer.Option(
        "feature_addition",
        "--kind", "-k",
        help="Mutation kind: feature_addition, bugfix, refactor, modification, feature_removal.",
    ),
    motivation: str = typer.Option(
        "Problem statement and motivation.",
        "--motivation", "-m",
        help="Problem statement and motivation.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path", "-p",
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
        console.print(f"  [dim]Status: proposed (symlinked in eidos/deltas/by-status/active/ and proposed/)[/dim]")
        console.print(f"  [dim]Next step: Edit mutations in {rel} and update living specs.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to create SpecDelta:[/bold red] {e}")
        raise typer.Exit(code=1)


@delta_app.command("apply")
def delta_apply_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename of the SpecDelta to apply.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path", "-p",
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
        "--path", "-p",
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
    summary_table.add_row("Approved", str(len(by_status.get("approved", []))), "Approved, awaiting implementation/merge")
    summary_table.add_row("Applied", str(len(by_status.get("applied", []))), "Reconciled into living specifications")
    summary_table.add_row("Rejected", str(len(by_status.get("rejected", []))), "Abandoned or rejected proposals")
    summary_table.add_row("In-Flight (Active)", f"[bold yellow]{active_count}[/bold yellow]", "Total active/unmerged deltas")

    console.print(summary_table)

    active_deltas = (
        by_status.get("proposed", [])
        + by_status.get("in_review", [])
        + by_status.get("approved", [])
    )
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
        "--path", "-p",
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
        "--path", "-p",
        help="Path to eidos directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
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
    doc_type: str = typer.Argument(..., help="Specification type: capability, domain, policy, contract, component, persona, delta."),
    doc_id: str = typer.Argument(..., help="Document ID (e.g. CAP-AUTH-001, POL-002, DLT-2026-002)."),
    title: str = typer.Option(..., "--title", "-t", help="Title of the document."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Parent domain for capabilities."),
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
    type_filter: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by type."),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status."),
):
    """Shortcut to list all living specifications."""
    spec_list_cmd(path=path, type_filter=type_filter, status_filter=status_filter)


@plugin_app.command("install")
def plugin_install(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Installation target in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project.",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Overwrite existing files at destination.",
    ),
):
    """Install the Eidos agent plugin strictly inside project scope (workspace)."""
    console.print(f"[bold cyan]Installing Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{target.value}[/bold green])...\n")

    try:
        results = install_plugin(target=target, project_dir=project_dir, dest_path=dest, force=force)
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
        raise typer.Exit(code=1)


@plugin_app.command("uninstall")
def plugin_uninstall(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to remove in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Uninstall / remove the Eidos agent plugin from project scope."""
    console.print(f"[bold cyan]Uninstalling Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{target.value}[/bold green])...\n")

    try:
        results = uninstall_plugin(target=target, project_dir=project_dir, dest_path=dest)
        for res in results:
            if res.removed:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold yellow]•[/bold yellow] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] Plugin cleanup complete.")
    except Exception as e:
        console.print(f"[bold red]Uninstall Failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@plugin_app.command("remove")
def plugin_remove(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to remove in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Alias for 'eidos plugin uninstall'."""
    plugin_uninstall(target=target, project_dir=project_dir, dest=dest)


@plugin_app.command("version")
def plugin_version(
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
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
        console.print("\n[bold yellow]Update Available:[/bold yellow] Run [bold green]eidos plugin update[/bold green] to upgrade to the latest bundled version.\n")
    elif status == "not_installed":
        console.print("\n[dim]Note:[/dim] Run [bold green]eidos plugin install[/bold green] to install the plugin into this project.\n")


@plugin_app.command("update")
def plugin_update(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to update in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to update.",
    ),
):
    """Update project-scoped Agent Plugin to the latest bundled version."""
    console.print(f"[bold cyan]Updating Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{target.value}[/bold green])...\n")

    try:
        prev_ver, current_ver, results = update_plugin(
            target=target,
            project_dir=project_dir,
            dest_path=dest
        )
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        if prev_ver is None:
            console.print(f"\n[bold green]Success![/bold green] Installed Eidos Agent Plugin at [bold cyan]v{current_ver}[/bold cyan].\n")
        elif prev_ver == current_ver:
            console.print(f"\n[bold green]Success![/bold green] Plugin is already at latest version [bold cyan]v{current_ver}[/bold cyan] (files refreshed).\n")
        else:
            console.print(f"\n[bold green]Success![/bold green] Upgraded Eidos Agent Plugin from [bold yellow]v{prev_ver}[/bold yellow] to [bold cyan]v{current_ver}[/bold cyan]!\n")
    except Exception as e:
        console.print(f"[bold red]Update Failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@plugin_app.command("upgrade")
def plugin_upgrade(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to update in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to update.",
    ),
):
    """Alias for 'eidos plugin update'."""
    plugin_update(target=target, project_dir=project_dir, dest=dest)



@plugin_app.command("validate")
def plugin_validate(
    plugin_path: Optional[Path] = typer.Argument(
        None,
        help="Path to plugin directory (defaults to assets/plugin_reference or bundled).",
    )
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
    table.add_row("Overall Status", "[bold green]CONFORMANT[/bold green]" if report.is_valid else "[bold red]NON-CONFORMANT[/bold red]")
    table.add_row("Discovered Skills", ", ".join(report.skills) if report.skills else "None")
    table.add_row("Configured MCP Servers", ", ".join(report.mcp_servers) if report.mcp_servers else "None")
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
    plugin_path: Optional[Path] = typer.Argument(
        None,
        help="Path to plugin directory (defaults to assets/plugin_reference or bundled).",
    )
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
        with open(manifest_file, "r", encoding="utf-8") as f:
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
    grid.add_row("Extensions:", ", ".join(report.extensions) or "None")

    console.print(Panel(grid, title="[bold blue]Eidos Agent Plugin Manifest[/bold blue]", expand=False))


if __name__ == "__main__":
    app()
