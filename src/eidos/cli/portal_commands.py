"""Living Web Portal commands (`eidos portal ...`): interactive preview server and static site compilation."""

from pathlib import Path

import typer

from eidos.cli._shared import (
    console,
    load_cli_repo_config,
    load_cli_repo_config_with_sources,
    resolve_cli_eidos_dir,
    resolve_param_from_config,
)
from eidos.core.workspace.project import resolve_spec_and_repo_roots
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
        "--open/--no-open",
        help="Automatically open the portal in your default web browser.",
    ),
):
    """Launch the interactive Eidos Living Web Portal."""
    target = resolve_cli_eidos_dir(target_path)
    if not target.exists():
        console.print(f"[bold red]Error:[/bold red] Specifications directory '{target}' not found.")
        raise typer.Exit(code=1)

    spec_root, repo_root = resolve_spec_and_repo_roots(target)
    repo_cfg = load_cli_repo_config(spec_root, repo_root=repo_root)
    eff_host = str(resolve_param_from_config("host", host, repo_cfg.portal.serve.host))
    eff_port = int(resolve_param_from_config("port", port, repo_cfg.portal.serve.port))
    eff_open = bool(resolve_param_from_config("open_browser", open_browser, repo_cfg.portal.serve.open_browser))

    run_portal_server(eidos_dir=target, host=eff_host, port=eff_port, open_browser=eff_open)


@portal_app.command("build")
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
    single_file: bool = typer.Option(
        False,
        "--single-file/--no-single-file",
        help="Inline all JS, CSS, and spec bodies into a single standalone index.html file.",
    ),
):
    """Export the Eidos Living Web Portal as a self-contained static web site."""
    target = resolve_cli_eidos_dir(target_path)
    if not target.exists():
        console.print(f"[bold red]Error:[/bold red] Specifications directory '{target}' not found.")
        raise typer.Exit(code=1)

    spec_root, repo_root = resolve_spec_and_repo_roots(target)
    repo_cfg, _cfg_path, cfg_sources = load_cli_repo_config_with_sources(spec_root, repo_root=repo_root)
    default_out = (
        (repo_root / repo_cfg.portal.build.out_dir) if cfg_sources.get("portal.build.out_dir") == "file" else out_dir
    )
    eff_out_dir = Path(resolve_param_from_config("out_dir", out_dir, default_out))
    eff_single_file = bool(resolve_param_from_config("single_file", single_file, repo_cfg.portal.build.single_file))

    generated_dir = build_static_portal(eidos_dir=target, output_dir=eff_out_dir, single_file=eff_single_file)
    console.print(f"[bold green]✓ Successfully generated static portal at:[/bold green] {generated_dir.resolve()}")
    if eff_single_file:
        console.print("  Mode: single-file standalone HTML")
    console.print(f"  Entrypoint: {generated_dir / 'index.html'}")
    console.print(f"  Data payload: {generated_dir / 'data.json'}")
