"""Living specification commands: validate and spec CRUD (`eidos spec ...`)."""

import json
from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import PANEL_GOVERNANCE, console
from eidos.cli.component_commands import component_app
from eidos.core.lifecycle.catalog import find_spec_by_id_or_path
from eidos.core.lifecycle.scaffolder import create_spec, delete_spec, list_specs
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import find_repo_root

spec_app = typer.Typer(
    name="spec",
    help="Manage living specifications and C4 components (list, show, create, delete, component).",
    add_completion=False,
)
spec_app.add_typer(component_app, name="component")


def _has_eidos_markers(cand: Path) -> bool:
    return cand.is_dir() and (
        (cand / "system.adoc").exists()
        or any((cand / sub).is_dir() for sub in ("architecture", "domains", "policies", "design", "deltas"))
        or any(cand.glob("*.adoc"))
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
            repo_eidos = find_repo_root() / "eidos"
            if repo_eidos.exists():
                target_path = repo_eidos
            elif _has_eidos_markers(Path(".")):
                target_path = Path(".")
    elif (target_path / "eidos").is_dir() and not _has_eidos_markers(target_path):
        target_path = target_path / "eidos"

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
        help="Filter by spec type (capability, policy, contract, container, component, domain, persona, delta, exploration, design-system, ui-component, screen, client, flow).",
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (active, draft, proposed, applied).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON specification list.",
    ),
):
    """List living specifications in the repository."""
    specs = list_specs(path, type_filter=type_filter, status_filter=status_filter)
    if json_output:
        serializable = [
            {
                "id": s["id"],
                "type": s["type"],
                "status": s["status"],
                "title": s["title"],
                "path": str(s["path"]),
                "relative_path": str(s["relative_path"]),
            }
            for s in specs
        ]
        typer.echo(json.dumps({"count": len(serializable), "specs": serializable}, indent=2))
        return

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
        "auto",
        help="Unique identifier (e.g. CAP-AUTH-001, POL-003, CTR-PAYMENT-API, or 'auto' for SpecDeltas).",
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
        "feature",
        "--kind",
        "-k",
        help="Mutation kind for SpecDeltas (feature, enabler, bug).",
    ),
    motivation: str = typer.Option(
        "Proposed specification change.",
        "--motivation",
        "-m",
        help="Motivation/problem statement for deltas.",
    ),
    slug: bool = typer.Option(
        False,
        "--slug",
        "-s",
        help="Append a deterministic title-derived slug suffix when creating a SpecDelta.",
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
    container: str | None = typer.Option(
        None,
        "--container",
        help="Parent container ID (CNT-*) for components, implementing container for clients, or owner for contracts.",
    ),
    source_path: str = typer.Option(
        "src",
        "--source-path",
        help="Source path or glob owned by a container or component (source_path).",
    ),
    specification_file: str = typer.Option(
        "src/main.py",
        "--spec-file",
        help="Relative path to raw interface specification file for contracts.",
    ),
    client_id: str | None = typer.Option(
        None,
        "--client",
        help="Client ID (CLT-*) for screens/flows, or brand identifier for client-specific design systems.",
    ),
    extends: str | None = typer.Option(
        None,
        "--extends",
        help="Base design system ID or path to extend (e.g. DS-CORE).",
    ),
    route: str = typer.Option(
        "/view",
        "--route",
        help="URL route or view identifier for screens.",
    ),
    design_system: str | None = typer.Option(
        None,
        "--system",
        help="Owning design system ID for UI components, screens, or clients (defaults to primary design/system or DS-CORE).",
    ),
    implements: str | None = typer.Option(
        None,
        "--implements",
        help="Base component ID being overridden/specialized by a UI component.",
    ),
    platform: str = typer.Option(
        "web",
        "--platform",
        help="Runtime platform for clients (web, mobile-ios, mobile-android, desktop, tui, multi-platform).",
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
            container=container,
            specification_file=specification_file,
            source_path=source_path,
            client_id=client_id,
            extends=extends,
            route=route,
            design_system=design_system,
            implements=implements,
            platform=platform,
            slug_suffix=slug,
        )
        rel = dest_file.relative_to(path) if dest_file.is_relative_to(path) else dest_file
        parsed_doc = parse_spec_file(dest_file)
        display_id = parsed_doc.doc_id or (doc_id if doc_id.lower() != "auto" else dest_file.stem.upper())
        console.print(
            f"[bold green]✓ Created and validated specification ({display_id}):[/bold green] [bold]{dest_file.name}[/bold]"
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


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("validate", rich_help_panel=PANEL_GOVERNANCE)(validate)

