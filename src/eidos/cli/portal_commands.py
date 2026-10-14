"""Living Web Portal commands: interactive preview server and static site compilation."""

from pathlib import Path

import typer

from eidos.cli._shared import PANEL_PORTAL, console
from eidos.portal.builder import build_static_portal
from eidos.portal.server import run_portal_server

portal_app = typer.Typer(
    name="portal",
    help="Serve, preview, and build the interactive Eidos Living Web Portal.",
    add_completion=False,
)


@portal_app.command("serve")
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


@portal_app.command("build")
@portal_app.command("build-site")
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
    """Export the Eidos Living Web Portal as a self-contained static web site."""
    target = target_path or Path("eidos")
    if not target.exists():
        console.print(f"[bold red]Error:[/bold red] Specifications directory '{target}' not found.")
        raise typer.Exit(code=1)

    generated_dir = build_static_portal(eidos_dir=target, output_dir=out_dir)
    console.print(f"[bold green]✓ Successfully generated static portal at:[/bold green] {generated_dir.resolve()}")
    console.print(f"  Entrypoint: {generated_dir / 'index.html'}")
    console.print(f"  Data payload: {generated_dir / 'data.json'}")


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("serve", rich_help_panel=PANEL_PORTAL)(serve_cmd)
    app.command("build-site", rich_help_panel=PANEL_PORTAL)(build_site_cmd)
