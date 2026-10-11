"""Main CLI entrypoint for Eidos."""

import json
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import eidos
from eidos.core.validator import SpecValidator
from eidos.core.parser import parse_spec_file
from eidos.mcp.server import run_mcp_server
from eidos.plugin.validator import validate_plugin_package
from eidos.plugin.installer import (
    install_plugin,
    uninstall_plugin,
    update_plugin,
    PluginInstallTarget,
    locate_plugin_bundle,
)
from eidos.plugin.version import (
    check_plugin_version_status,
    get_bundled_plugin_version,
    get_installed_plugin_version,
)

app = typer.Typer(
    name="eidos",
    help="Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator.",
    add_completion=False,
)
plugin_app = typer.Typer(
    name="plugin",
    help="Manage, install, and validate Eidos agent plugins (agent-plugins.org v1.0.0).",
    add_completion=False,
)
app.add_typer(plugin_app, name="plugin")
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
    grid.add_row("Supported Agents:", "Antigravity (agy), Claude Code, Cursor, Copilot")
    grid.add_row("Plugin Standard:", "agent-plugins.org v1.0.0 & agentskills.io")

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


@app.command()
def components(
    target_path: Optional[Path] = typer.Argument(
        None,
        help="Path to scan for component manifests (defaults to eidos/architecture).",
    )
):
    """List all C4 components, internal modules, dependencies, and monorepo package mappings."""
    search_dir = target_path or Path("eidos/architecture")
    if not search_dir.exists():
        console.print(f"[bold red]Error:[/bold red] Directory '{search_dir}' not found.")
        raise typer.Exit(code=1)

    found_components = []
    for p in search_dir.rglob("*.adoc"):
        doc = parse_spec_file(p)
        if doc.doc_type == "tech/component":
            found_components.append(doc)

    if not found_components:
        console.print(f"[bold yellow]No component specifications found in '{search_dir}'.[/bold yellow]")
        return

    table = Table(title="Eidos Monorepo & System Components (C4 L2/L3)", show_header=True, header_style="bold blue")
    table.add_column("Component ID", style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Container Type", style="yellow")
    table.add_column("Runtime", style="magenta")
    table.add_column("Package Path", style="dim")
    table.add_column("Internal C4 Modules", style="white")

    for c in found_components:
        fm = c.frontmatter
        pkg = fm.get("package_path", "centralized")
        internals = ", ".join(m.get("name", "") for m in fm.get("c4_internal_components", [])) or "N/A"
        table.add_row(
            c.doc_id,
            fm.get("name", "N/A"),
            fm.get("container_type", "N/A"),
            fm.get("runtime", "N/A"),
            str(pkg),
            internals,
        )

    console.print(table)


@app.command()
def mcp():
    """Launch the stdio Model Context Protocol (MCP) server for AI agents."""
    run_mcp_server()


@plugin_app.command("install")
def plugin_install(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Installation target in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project.",
    ),
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        help="Overwrite existing files at destination.",
    ),
):
    """Install the Eidos agent plugin strictly inside project scope (workspace)."""
    console.print(f"[bold cyan]Installing Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{target.value}[/bold green])...\n")

    try:
        results = install_plugin(target=target, project_dir=project_dir, dest_path=dest, force=force)
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
                console.print(f"  [dim]Destination: {res.destination}[/dim]")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] The Eidos Agent Plugin is installed and ready.")
        console.print("AI agents can now discover Eidos living specifications, C4 architectures, and MCP tools.\n")
    except Exception as e:
        console.print(f"[bold red]Installation Failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@plugin_app.command("uninstall")
def plugin_uninstall(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to remove in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Uninstall / remove the Eidos agent plugin from project scope."""
    console.print(f"[bold cyan]Uninstalling Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{target.value}[/bold green])...\n")

    try:
        results = uninstall_plugin(target=target, project_dir=project_dir, dest_path=dest)
        for res in results:
            if res.removed:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold yellow]•[/bold yellow] [bold]{res.target.upper()}:[/bold] {res.message}")

        console.print("\n[bold green]Success![/bold green] Plugin cleanup complete.")
    except Exception as e:
        console.print(f"[bold red]Uninstall Failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@plugin_app.command("remove")
def plugin_remove(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to remove in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to remove.",
    ),
):
    """Alias for 'eidos plugin uninstall'."""
    plugin_uninstall(target=target, project_dir=project_dir, dest=dest)


@plugin_app.command("version")
def plugin_version(
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
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
        console.print("\n[bold yellow]Update Available:[/bold yellow] Run [bold green]eidos plugin update[/bold green] to upgrade to the latest bundled version.\n")
    elif status == "not_installed":
        console.print("\n[dim]Note:[/dim] Run [bold green]eidos plugin install[/bold green] to install the plugin into this project.\n")


@plugin_app.command("update")
def plugin_update(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to update in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to update.",
    ),
):
    """Update project-scoped Agent Plugin to the latest bundled version."""
    console.print(f"[bold cyan]Updating Eidos Agent Plugin[/bold cyan] (scope: [bold green]project[/bold green], target: [bold green]{target.value}[/bold green])...\n")

    try:
        prev_ver, current_ver, results = update_plugin(
            target=target,
            project_dir=project_dir,
            dest_path=dest
        )
        for res in results:
            if res.success:
                console.print(f"[bold green]✓[/bold green] [bold]{res.target.upper()}:[/bold] {res.message}")
            else:
                console.print(f"[bold red]✗[/bold red] [bold]{res.target.upper()}:[/bold] {res.message}")

        if prev_ver is None:
            console.print(f"\n[bold green]Success![/bold green] Installed Eidos Agent Plugin at [bold cyan]v{current_ver}[/bold cyan].\n")
        elif prev_ver == current_ver:
            console.print(f"\n[bold green]Success![/bold green] Plugin is already at latest version [bold cyan]v{current_ver}[/bold cyan] (files refreshed).\n")
        else:
            console.print(f"\n[bold green]Success![/bold green] Upgraded Eidos Agent Plugin from [bold yellow]v{prev_ver}[/bold yellow] to [bold cyan]v{current_ver}[/bold cyan]!\n")
    except Exception as e:
        console.print(f"[bold red]Update Failed:[/bold red] {e}")
        raise typer.Exit(code=1)


@plugin_app.command("upgrade")
def plugin_upgrade(
    target: PluginInstallTarget = typer.Option(
        PluginInstallTarget.PROJECT,
        "--target", "-t",
        help="Target to update in project scope: project, workspace, antigravity, cursor, claude, all.",
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project-dir", "-p",
        help="Root directory of the project (defaults to current working directory).",
    ),
    dest: Optional[Path] = typer.Option(
        None,
        "--dest", "-d",
        help="Custom destination directory within the project to update.",
    ),
):
    """Alias for 'eidos plugin update'."""
    plugin_update(target=target, project_dir=project_dir, dest=dest)



@plugin_app.command("validate")
def plugin_validate(
    plugin_path: Optional[Path] = typer.Argument(
        None,
        help="Path to plugin directory (defaults to bundled or ./plugins/eidos).",
    )
):
    """Validate an Agent Plugin against agent-plugins.org v1.0.0 and agentskills.io specifications."""
    target_dir = plugin_path
    if target_dir is None:
        try:
            target_dir = locate_plugin_bundle()
        except FileNotFoundError:
            target_dir = Path("plugins/eidos")

    console.print(f"[bold cyan]Validating Agent Plugin at:[/bold cyan] {target_dir.resolve()}\n")
    report = validate_plugin_package(target_dir)

    table = Table(title="Agent Plugin Conformance (agent-plugins.org v1.0.0)", show_header=True)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="white")

    table.add_row("Plugin Name", str(report.plugin_name or "N/A"))
    table.add_row("Plugin Version", str(report.plugin_version or "N/A"))
    table.add_row("Overall Status", "[bold green]CONFORMANT[/bold green]" if report.is_valid else "[bold red]NON-CONFORMANT[/bold red]")
    table.add_row("Discovered Skills", ", ".join(report.skills) if report.skills else "None")
    table.add_row("Configured MCP Servers", ", ".join(report.mcp_servers) if report.mcp_servers else "None")
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
    plugin_path: Optional[Path] = typer.Argument(
        None,
        help="Path to plugin directory (defaults to bundled or ./plugins/eidos).",
    )
):
    """Display metadata and configuration for the Eidos Agent Plugin."""
    target_dir = plugin_path
    if target_dir is None:
        try:
            target_dir = locate_plugin_bundle()
        except FileNotFoundError:
            target_dir = Path("plugins/eidos")

    report = validate_plugin_package(target_dir)
    manifest_file = target_dir / "plugin.json"
    manifest_data = {}
    if manifest_file.exists():
        with open(manifest_file, "r", encoding="utf-8") as f:
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
    grid.add_row("Extensions:", ", ".join(report.extensions) or "None")

    console.print(Panel(grid, title="[bold blue]Eidos Agent Plugin Manifest[/bold blue]", expand=False))


if __name__ == "__main__":
    app()
