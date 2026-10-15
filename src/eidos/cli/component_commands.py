"""C4 Level 3 component promotion and inlining commands (`eidos spec component ...`)."""

from pathlib import Path
from typing import Annotated

import typer

from eidos.cli._shared import console
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.lifecycle.scaffolder import inline_component, promote_component

component_app = typer.Typer(
    name="component",
    help="Manage C4 Level 3 components (promote, inline).",
    add_completion=False,
)


@component_app.command("promote")
def component_promote_cmd(
    component_id: Annotated[str, typer.Argument(help="Identifier of the inline component to promote (e.g. CMP-CORE-VALIDATOR).")],
    eidos_dir: Annotated[Path | None, typer.Option("--path", "-p", help="Path to eidos directory.")] = None,
    repo_root: Annotated[Path | None, typer.Option("--repo-root", help="Path to repository root directory.")] = None,
):
    """Promote an inline C4 Level 3 component to a dedicated specification in eidos/architecture/components/."""
    try:
        comp_path, container_path = promote_component(component_id=component_id, eidos_dir=eidos_dir, repo_root=repo_root)
        console.print(f"[bold green]✓ Created dedicated component manifest:[/bold green] {comp_path}")
        console.print(f"[bold green]✓ Updated container:[/bold green] {container_path} [dim](referenced via 'ref')[/dim]")

        # Sync index
        idx = get_architectural_index(eidos_dir=eidos_dir, repo_root=repo_root)
        idx.sync(force=True)
        console.print("[bold green]✓ Reindexed architectural knowledge graph (zero drift).[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Error promoting component:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@component_app.command("inline")
def component_inline_cmd(
    component_id: Annotated[str, typer.Argument(help="Identifier of the dedicated component to inline back into its container.")],
    eidos_dir: Annotated[Path | None, typer.Option("--path", "-p", help="Path to eidos directory.")] = None,
    repo_root: Annotated[Path | None, typer.Option("--repo-root", help="Path to repository root directory.")] = None,
):
    """Demote / inline a dedicated C4 Level 3 component back into its container manifest."""
    try:
        container_path, deleted_path = inline_component(component_id=component_id, eidos_dir=eidos_dir, repo_root=repo_root)
        console.print(f"[bold green]✓ Inlined component into container:[/bold green] {container_path}")
        console.print(f"[bold green]✓ Safely removed dedicated component manifest:[/bold green] {deleted_path}")

        # Sync index
        idx = get_architectural_index(eidos_dir=eidos_dir, repo_root=repo_root)
        idx.sync(force=True)
        console.print("[bold green]✓ Reindexed architectural knowledge graph (zero drift).[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Error inlining component:[/bold red] {e}")
        raise typer.Exit(code=1) from e


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    return None

