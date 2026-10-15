"""Repository bootstrapping command (`eidos init`)."""

from pathlib import Path

import typer
from rich.table import Table

from eidos.cli._shared import PANEL_SETUP, console
from eidos.core.lifecycle.initializer import init_repository


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
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force initialization even if eidos/ directory already exists.",
    ),
    lite: bool = typer.Option(
        False,
        "--lite",
        "-l",
        help="Initialize progressive Eidos Lite profile (essential baseline: system, containers, capabilities, deltas).",
    ),
    _with_plugin: bool = typer.Option(
        False,
        "--with-plugin/--no-plugin",
        hidden=True,
        help="Deprecated: plugins are no longer installed by init.",
    ),
):
    """Initialize a repository with an empty Eidos living specification skeleton."""
    project_dir = target_path or Path.cwd()
    console.print(f"[bold cyan]Initializing empty Eidos skeleton in:[/bold cyan] {project_dir.resolve()}\n")

    success, msg, created_files = init_repository(
        project_dir=project_dir,
        name=name,
        title=title,
        force=force,
        lite=lite,
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
    console.print("  • Run [cyan]eidos index sync[/cyan] to initialize the architectural knowledge graph index.")
    console.print("  • (Optional) Install agent plugins using [cyan]eidos plugin install[/cyan].")


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("init", rich_help_panel=PANEL_SETUP)(init_cmd)
