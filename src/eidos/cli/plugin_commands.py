"""Agent plugin lifecycle commands (`eidos plugin ...`)."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import console
from eidos.plugin.installer import (
    PluginInstallTarget,
    install_plugin,
    locate_plugin_bundle,
    uninstall_plugin,
    update_plugin,
)
from eidos.plugin.validator import validate_plugin_package
from eidos.plugin.version import (
    check_plugin_version_status,
)

plugin_app = typer.Typer(
    name="plugin",
    help="Manage, install, and validate Eidos agent plugins (agent-plugins.org v1.0.0).",
    add_completion=False,
)


@plugin_app.command("install")
def plugin_install(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Installation target in project scope: project, antigravity, claude, cursor, aider, cline, open-code, codex, copilot, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, claude, cursor, aider, cline, open-code, codex, copilot, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project.",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Overwrite existing files at destination.",
    ),
    launcher: str = typer.Option(
        "eidos",
        "--launcher",
        help="Hook command launcher: 'eidos' (global CLI on PATH) or 'uvx' (ephemeral uvx runner).",
    ),
):
    """Install the Eidos agent plugin strictly inside project scope (.agents/plugins/eidos, agent-plugins.org v1.0.0)."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    resolved_launcher = launcher if isinstance(launcher, str) else "eidos"
    console.print(
        f"[bold cyan]Installing Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{resolved_target.value}[/bold green])...\n"
    )

    try:
        results = install_plugin(
            target=resolved_target,
            project_dir=project_dir,
            dest_path=dest,
            force=force,
            launcher=resolved_launcher,
        )
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
                console.print(f"  [dim]Destination: {res.destination}[/dim]")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] The Eidos Agent Plugin is installed and ready.")
        console.print("AI agents can now discover Eidos living specifications, C4 architectures, and Agent Skills.\n")
    except Exception as e:
        console.print(f"[bold red]Installation Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@plugin_app.command("uninstall")
def plugin_uninstall(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to remove in project scope: project, antigravity, claude, cursor, aider, cline, open-code, codex, copilot, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, claude, cursor, aider, cline, open-code, codex, copilot, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Uninstall / remove the Eidos agent plugin from project scope."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    console.print(
        f"[bold cyan]Uninstalling Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{resolved_target.value}[/bold green])...\n"
    )

    try:
        results = uninstall_plugin(target=resolved_target, project_dir=project_dir, dest_path=dest)
        for res in results:
            if res.removed:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold yellow]•[/bold yellow] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] Plugin cleanup complete.")
    except Exception as e:
        console.print(f"[bold red]Uninstall Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@plugin_app.command("version")
def plugin_version(
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
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
        console.print(
            "\n[bold yellow]Update Available:[/bold yellow] Run [bold green]eidos plugin update[/bold green] to upgrade to the latest bundled version.\n"
        )
    elif status == "not_installed":
        console.print(
            "\n[dim]Note:[/dim] Run [bold green]eidos plugin install[/bold green] to install the plugin into this project.\n"
        )


@plugin_app.command("update")
def plugin_update(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target",
        "-t",
        help="Target to update in project scope: project, antigravity, claude, cursor, aider, cline, open-code, codex, copilot, all.",
    ),
    target_arg: Annotated[
        PluginInstallTarget | None,
        typer.Argument(help="Optional positional target: project, antigravity, claude, cursor, aider, cline, open-code, codex, copilot, all."),
    ] = None,
    project_dir: Path | None = typer.Option(
        None,
        "--project-dir",
        "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Path | None = typer.Option(
        None,
        "--dest",
        "-d",
        help="Custom destination directory within the project to update.",
    ),
    launcher: str = typer.Option(
        "eidos",
        "--launcher",
        help="Hook command launcher: 'eidos' (global CLI on PATH) or 'uvx' (ephemeral uvx runner).",
    ),
):
    """Update project-scoped Agent Plugin to the latest bundled version."""
    resolved_target = target_arg if isinstance(target_arg, PluginInstallTarget) else target
    resolved_launcher = launcher if isinstance(launcher, str) else "eidos"
    console.print(
        f"[bold cyan]Updating Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{resolved_target.value}[/bold green])...\n"
    )

    try:
        prev_ver, current_ver, results = update_plugin(
            target=resolved_target,
            project_dir=project_dir,
            dest_path=dest,
            launcher=resolved_launcher,
        )
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        if prev_ver is None:
            console.print(
                f"\n[bold green]Success![/bold green] Installed Eidos Agent Plugin at [bold cyan]v{current_ver}[/bold cyan].\n"
            )
        elif prev_ver == current_ver:
            console.print(
                f"\n[bold green]Success![/bold green] Plugin is already at latest version [bold cyan]v{current_ver}[/bold cyan] (files refreshed).\n"
            )
        else:
            console.print(
                f"\n[bold green]Success![/bold green] Upgraded Eidos Agent Plugin from [bold yellow]v{prev_ver}[/bold yellow] to [bold cyan]v{current_ver}[/bold cyan]!\n"
            )
    except Exception as e:
        console.print(f"[bold red]Update Failed:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@plugin_app.command("validate")
def plugin_validate(
    plugin_path: Path | None = typer.Argument(
        None,
        help="Path to plugin directory (defaults to assets/plugin_reference or bundled).",
    ),
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
    table.add_row(
        "Overall Status",
        "[bold green]CONFORMANT[/bold green]" if report.is_valid else "[bold red]NON-CONFORMANT[/bold red]",
    )
    table.add_row("Discovered Skills", ", ".join(report.skills) if report.skills else "None")
    table.add_row("Configured MCP Servers", ", ".join(report.mcp_servers) if report.mcp_servers else "None")
    table.add_row("Lifecycle Hooks", ", ".join(report.hooks) if report.hooks else "None")
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
    plugin_path: Path | None = typer.Argument(
        None,
        help="Path to plugin directory (defaults to assets/plugin_reference or bundled).",
    ),
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
        with open(manifest_file, encoding="utf-8") as f:
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
    grid.add_row("Lifecycle Hooks:", ", ".join(report.hooks) or "None")
    grid.add_row("Extensions:", ", ".join(report.extensions) or "None")

    console.print(Panel(grid, title="[bold blue]Eidos Agent Plugin Manifest[/bold blue]", expand=False))


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    return None

