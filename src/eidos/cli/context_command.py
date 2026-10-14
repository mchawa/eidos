"""Token-budgeted architectural context slicing command (`eidos context`)."""

from pathlib import Path

import typer

from eidos.cli._shared import PANEL_KNOWLEDGE
from eidos.core.index.graph_index import get_architectural_index


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


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("context", rich_help_panel=PANEL_KNOWLEDGE)(context_cmd)
