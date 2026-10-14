"""Eidos CLI root dispatcher (CMP-CLI-APP): builds the Typer app, mounts command modules, and fixes help ordering.

Command implementations live in the sibling ``*_commands.py`` / ``*_command.py`` modules (one per C4 Level 3
component). Every name that used to be defined in this module is re-exported below so existing imports such as
``from eidos.cli.main import app`` keep working (DLT-2026-038).
"""

import typer
from rich.panel import Panel
from rich.table import Table

import eidos
from eidos.cli import (
    component_commands,
    context_command,
    delta_commands,
    doctor_hook_commands,
    drift_commands,
    index_commands,
    init_command,
    plugin_commands,
    portal_commands,
    spec_commands,
)

# Backwards-compatible re-exports (every name formerly defined in this module).
from eidos.cli._shared import (  # noqa: F401
    PANEL_GOVERNANCE,
    PANEL_KNOWLEDGE,
    PANEL_PLUGINS,
    PANEL_PORTAL,
    PANEL_SETUP,
    PANEL_SPECS,
    console,
)
from eidos.cli.component_commands import component_app, component_inline_cmd, component_list_cmd, component_promote_cmd  # noqa: F401
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
    delta_sync_views_cmd,
    delta_verify_cmd,
)
from eidos.cli.doctor_hook_commands import doctor_cmd, hook_app, hook_drift_cmd, hook_lint_cmd  # noqa: F401
from eidos.cli.drift_commands import drift_cmd, reconcile_cmd, sync_shortcut  # noqa: F401
from eidos.cli.index_commands import (  # noqa: F401
    index_app,
    index_blast_radius_cmd,
    index_find_symbol_cmd,
    index_repo_map_cmd,
    index_search_cmd,
    index_status_cmd,
    index_sync_cmd,
    index_update_cmd,
)
from eidos.cli.init_command import init_cmd  # noqa: F401
from eidos.cli.plugin_commands import (  # noqa: F401
    install_shortcut,
    plugin_app,
    plugin_info,
    plugin_install,
    plugin_remove,
    plugin_uninstall,
    plugin_update,
    plugin_upgrade,
    plugin_validate,
    plugin_version,
    uninstall_shortcut,
)
from eidos.cli.portal_commands import build_site_cmd, portal_app, serve_cmd  # noqa: F401
from eidos.cli.spec_commands import (  # noqa: F401
    _render_containers_table,
    components,
    containers,
    create_shortcut,
    delete_shortcut,
    lint_cmd,
    list_shortcut,
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
app.add_typer(component_app, name="component", rich_help_panel=PANEL_SPECS)
app.add_typer(index_app, name="index", rich_help_panel=PANEL_KNOWLEDGE)
app.add_typer(portal_app, name="portal", rich_help_panel=PANEL_PORTAL)
app.add_typer(plugin_app, name="plugin", rich_help_panel=PANEL_PLUGINS)
app.add_typer(hook_app, name="hook", rich_help_panel=PANEL_PLUGINS)


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


def version_cmd():
    """Display the Eidos engine version."""
    typer.echo(f"eidos {eidos.__version__}")


def info():
    """Display Eidos engine version, bundled schemas, and platform status."""
    validator = SpecValidator()
    schemas = sorted({s for s in validator._schema_cache if "/" not in s})

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


app.command("version", rich_help_panel=PANEL_SETUP)(version_cmd)
app.command(rich_help_panel=PANEL_SETUP)(info)

# Mount the root-level commands owned by each command module.
doctor_hook_commands.register(app)
init_command.register(app)
spec_commands.register(app)
drift_commands.register(app)
portal_commands.register(app)
component_commands.register(app)
context_command.register(app)
index_commands.register(app)
delta_commands.register(app)
plugin_commands.register(app)


# Canonical root command order. Command modules register in module order, so the dispatcher pins the
# help layout explicitly instead of relying on import/registration side effects.
ROOT_COMMAND_ORDER = (
    "version",
    "info",
    "doctor",
    "init",
    "validate",
    "lint",
    "drift",
    "reconcile",
    "sync",
    "serve",
    "build-site",
    "containers",
    "components",
    "context",
    "repo-map",
    "find-symbol",
    "changelog",
    "create",
    "delete",
    "list",
    "install",
    "uninstall",
)


def _command_name(command_info: typer.models.CommandInfo) -> str:
    return command_info.name or command_info.callback.__name__.lower().replace("_", "-")


app.registered_commands.sort(key=lambda c: ROOT_COMMAND_ORDER.index(_command_name(c)))


if __name__ == "__main__":
    app()
