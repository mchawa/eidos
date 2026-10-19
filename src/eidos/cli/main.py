"""Eidos CLI root dispatcher (CMP-CLI-APP): builds the Typer app, mounts command modules, and fixes help ordering.

Command implementations live in the sibling ``*_commands.py`` / ``*_command.py`` modules (one per C4 Level 3
component). Canonical command functions are re-exported below so existing imports such as
``from eidos.cli.main import app`` keep working.
"""

import typer
from rich.panel import Panel
from rich.table import Table

import eidos
from eidos.cli import (
    context_command,
    doctor_hook_commands,
    drift_commands,
    init_command,
    spec_commands,
)

# Backwards-compatible re-exports of canonical CLI symbols.
from eidos.cli._shared import (  # noqa: F401
    PANEL_GOVERNANCE,
    PANEL_KNOWLEDGE,
    PANEL_PLUGINS,
    PANEL_PORTAL,
    PANEL_SETUP,
    PANEL_SPECS,
    console,
    has_eidos_markers,
    resolve_cli_eidos_dir,
)
from eidos.cli.component_commands import component_app, component_inline_cmd, component_promote_cmd  # noqa: F401
from eidos.cli.context_command import context_cmd  # noqa: F401
from eidos.cli.delta_commands import (  # noqa: F401
    delta_app,
    delta_apply_cmd,
    delta_audit_cmd,
    delta_changelog_cmd,
    delta_create_cmd,
    delta_delete_cmd,
    delta_list_cmd,
    delta_status_cmd,
    delta_verify_cmd,
)
from eidos.cli.doctor_hook_commands import doctor_cmd, hook_cmd  # noqa: F401
from eidos.cli.drift_commands import drift_cmd  # noqa: F401
from eidos.cli.index_commands import (  # noqa: F401
    index_app,
    index_blast_radius_cmd,
    index_find_symbol_cmd,
    index_repo_map_cmd,
    index_search_cmd,
    index_status_cmd,
    index_sync_cmd,
)
from eidos.cli.init_command import init_cmd  # noqa: F401
from eidos.cli.plugin_commands import (  # noqa: F401
    plugin_app,
    plugin_info,
    plugin_install,
    plugin_uninstall,
    plugin_update,
    plugin_validate,
    plugin_version,
)
from eidos.cli.portal_commands import build_site_cmd, portal_app, serve_cmd  # noqa: F401
from eidos.cli.spec_commands import (  # noqa: F401
    spec_app,
    spec_create_cmd,
    spec_delete_cmd,
    spec_list_cmd,
    spec_show_cmd,
    validate,
)
from eidos.core.spec.validator import SpecValidator

app = typer.Typer(
    name="eidos",
    help="Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator.",
    add_completion=False,
)

app.add_typer(spec_app, name="spec", rich_help_panel=PANEL_SPECS)
app.add_typer(delta_app, name="delta", rich_help_panel=PANEL_SPECS)
app.add_typer(index_app, name="index", rich_help_panel=PANEL_KNOWLEDGE)
app.add_typer(portal_app, name="portal", rich_help_panel=PANEL_PORTAL)
app.add_typer(plugin_app, name="plugin", rich_help_panel=PANEL_PLUGINS)


def version_callback(value: bool):
    if value:
        typer.echo(f"eidos {eidos.__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        help="Show Eidos engine version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
):
    """Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator."""
    pass


def info():
    """Display Eidos engine version, bundled schemas, and platform status."""
    validator = SpecValidator()
    schemas = sorted(
        {
            s.removeprefix("schemas/v1/")
            for s in validator._schema_cache
            if s.startswith("schemas/v1/") and "/" in s.removeprefix("schemas/v1/")
        }
    )

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Eidos Engine Version:", f"[bold green]v{eidos.__version__}[/bold green]")
    grid.add_row("Bundled v1 Schemas:", f"[bold yellow]{len(schemas)} loaded[/bold yellow]")
    for s in schemas:
        grid.add_row("", f"• {s}")
    grid.add_row("Supported VCS:", "Git (local repository)")
    grid.add_row("Supported Agents:", "Antigravity (agy), Claude Code, Cursor, Copilot")
    grid.add_row("Plugin Standard:", "agent-plugins.org v1.0.0 & agentskills.io")

    console.print(Panel(grid, title="[bold blue]Eidos AI Ecosystem[/bold blue]", expand=False))


app.command(rich_help_panel=PANEL_SETUP)(info)

# Mount the root-level commands owned by each command module.
doctor_hook_commands.register(app)
init_command.register(app)
spec_commands.register(app)
drift_commands.register(app)
context_command.register(app)


# Canonical root command order. Command modules register in module order, so the dispatcher pins the
# help layout explicitly instead of relying on import/registration side effects.
ROOT_COMMAND_ORDER = (
    "info",
    "doctor",
    "init",
    "validate",
    "drift",
    "context",
    "hook",
)


def _command_name(command_info: typer.models.CommandInfo) -> str:
    return command_info.name or command_info.callback.__name__.lower().replace("_", "-")


app.registered_commands.sort(key=lambda c: ROOT_COMMAND_ORDER.index(_command_name(c)))


if __name__ == "__main__":
    app()
