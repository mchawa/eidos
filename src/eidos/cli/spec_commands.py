"""Living specification commands: validate, lint, containers/components listing, and spec CRUD (`eidos spec ...`)."""

import json
from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import PANEL_GOVERNANCE, PANEL_SPECS, console
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.lifecycle.catalog import find_spec_by_id_or_path
from eidos.core.lifecycle.scaffolder import create_spec, delete_spec, list_specs
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator

spec_app = typer.Typer(
    name="spec",
    help="Manage living specifications (list, create, delete, show).",
    add_completion=False,
)


def validate(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to the directory containing .adoc specifications (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON validation report.",
    ),
):
    """Validate all specification documents against their schemas and verify cross-references."""
    if target_path is None:
        target_path = Path("eidos")
        if not target_path.exists():
            target_path = Path(".")

    if not target_path.exists():
        if json_output:
            typer.echo(json.dumps({"error": f"Directory '{target_path}' not found.", "is_clean": False}))
            raise typer.Exit(code=1)
        console.print(f"[bold red]Error:[/bold red] Directory '{target_path}' not found.")
        raise typer.Exit(code=1)

    if not json_output:
        console.print(f"[bold cyan]Validating specifications in:[/bold cyan] {target_path.resolve()}\n")

    validator = SpecValidator()
    report = validator.validate_directory(target_path)

    if json_output:
        payload = {
            "is_clean": report.is_clean,
            "total_docs": report.total_docs,
            "valid_docs": report.valid_docs,
            "broken_references": report.broken_references,
            "schema_errors": {str(k): v for k, v in report.schema_errors.items()},
            "documents": [
                {
                    "id": doc.doc_id,
                    "type": doc.doc_type,
                    "is_valid": doc.is_valid,
                    "path": (
                        str(doc.file_path.relative_to(Path.cwd()))
                        if doc.file_path.is_relative_to(Path.cwd())
                        else str(doc.file_path)
                    ),
                }
                for doc in report.documents
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
        if not report.is_clean:
            raise typer.Exit(code=1)
        return

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


def lint_cmd(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to directory containing .adoc specifications (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON validation report.",
    ),
):
    """Lint and validate all specification documents against schemas and link integrity."""
    validate(target_path, json_output=json_output)


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


@spec_app.command("containers")
def containers(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to scan for container manifests (defaults to eidos/architecture).",
    ),
):
    """List all C4 Level 2 containers, runtimes, package paths, and internal building blocks."""
    _render_containers_table(target_path=target_path)


@spec_app.command("components")
def components(
    container_id: str | None = typer.Argument(
        None,
        help="Optional container ID to filter components (e.g. CNT-CORE).",
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
        ctype = c.get("type", "tech/container")

        if ctype == "tech/component":
            if cid_filter and cid_filter not in cid.upper():
                continue
            count += 1
            table.add_row(
                cid,
                c.get("name", c.get("title", "N/A")),
                c.get("container", "N/A"),
                str(c.get("source_path") or c.get("package_path") or c.get("file_path") or "N/A"),
                c.get("responsibility", "N/A"),
            )
            continue

        if cid_filter and cid_filter not in cid.upper():
            continue
        internals = c.get("components", []) or c.get("c4_internal_components", [])
        for m in internals:
            count += 1
            spec_ref = str(m.get("ref", m.get("spec_path", m.get("source_path", "N/A"))))
            table.add_row(
                m.get("id", "CMP-INTERNAL"),
                m.get("name", "N/A"),
                cid,
                spec_ref,
                m.get("responsibility", "N/A"),
            )

    if count == 0 and cid_filter:
        console.print(f"[bold yellow]No components found for container '{container_id}'.[/bold yellow]")
        return

    console.print(table)


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
        help=(
            "Specification type: capability, domain, policy, contract, container, component, persona, delta, "
            "exploration, design-system, ui-component, screen, client, flow."
        ),
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
        help="Parent domain ID for capabilities (e.g. DOM-CORE).",
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
    owning_component: str | None = typer.Option(
        None,
        "--owning-component",
        help="Owning component or container ID for contracts.",
    ),
    container: str | None = typer.Option(
        None,
        "--container",
        help="Parent container ID (CNT-*) for components, implementing container for clients, or owner for contracts.",
    ),
    source_path: str = typer.Option(
        "src",
        "--source-path",
        help="Source path or glob owned by a container (package_path) or component (source_path).",
    ),
    client_id: str | None = typer.Option(
        None,
        "--client",
        help="Client ID (CLT-*) for screens/flows, or brand identifier for client-specific design systems.",
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
            owning_container=container,
            owning_component=owning_component,
            package_path=source_path,
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


def delete_shortcut(
    target: str = typer.Argument(..., help="Document ID or path to delete."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    force: bool = typer.Option(False, "--force", "-f", help="Force deletion even if referenced."),
):
    """Shortcut to safely delete a specification document or delta."""
    spec_delete_cmd(target=target, path=path, force=force)


def list_shortcut(
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    type_filter: str | None = typer.Option(None, "--type", "-t", help="Filter by type."),
    status_filter: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
):
    """Shortcut to list all living specifications."""
    spec_list_cmd(path=path, type_filter=type_filter, status_filter=status_filter)


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("validate", rich_help_panel=PANEL_GOVERNANCE)(validate)
    app.command("lint", rich_help_panel=PANEL_GOVERNANCE)(lint_cmd)
    app.command("containers", rich_help_panel=PANEL_SPECS)(containers)
    app.command("components", rich_help_panel=PANEL_SPECS)(components)
    app.command("create", rich_help_panel=PANEL_SPECS)(create_shortcut)
    app.command("delete", rich_help_panel=PANEL_SPECS)(delete_shortcut)
    app.command("list", rich_help_panel=PANEL_SPECS)(list_shortcut)
