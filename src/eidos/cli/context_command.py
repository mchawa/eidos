"""Token-budgeted architectural context slicing command (`eidos context`)."""

from pathlib import Path

import orjson
import typer

from eidos.cli._shared import PANEL_KNOWLEDGE, console, resolve_cli_eidos_dir
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.workspace.project import find_repo_root


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
        help="Target component identifier (e.g. 'CMP-CORE').",
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
    semantic: bool = typer.Option(
        True,
        "--semantic/--no-semantic",
        help="Enable or disable opt-in Hybrid RRF semantic fusion when spec_embeddings are available.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output context slice as machine-readable JSON instead of Markdown.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Extract a compact architectural subgraph slice (C4 components, contracts, capabilities, invariants, policies)."""
    eidos_dir = resolve_cli_eidos_dir(path)

    if not eidos_dir.exists():
        if json_output:
            typer.echo(orjson.dumps({"error": f"Directory '{eidos_dir}' not found."}).decode("utf-8"))
        else:
            console.print(f"[bold red]Error:[/bold red] Directory '{eidos_dir}' not found.")
        raise typer.Exit(code=1)

    resolved_eidos = eidos_dir.resolve()
    repo_root = resolved_eidos.parent if resolved_eidos.name == "eidos" else find_repo_root(resolved_eidos)
    idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
    slice_result = idx.get_tiered_context_slice(
        files=files,
        component=component,
        task=task,
        tier=tier,
        use_semantic=semantic,
    )

    if json_output:
        typer.echo(slice_result.to_json())
    else:
        typer.echo(slice_result.to_markdown())


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("context", rich_help_panel=PANEL_KNOWLEDGE)(context_cmd)
