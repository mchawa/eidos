"""Main CLI entrypoint for Eidos."""

from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import eidos
from eidos.core.validator import SpecValidator

app = typer.Typer(
    name="eidos",
    help="Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator.",
    add_completion=False,
)
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
    grid.add_row("Supported Agents:", "Antigravity (agy), Claude Code, OpenAI Codex, Copilot")

    console.print(Panel(grid, title="[bold blue]Eidos AI Ecosystem[/bold blue]", expand=False))


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

    # Print validation errors if any
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


if __name__ == "__main__":
    app()
