"""Installation health diagnostics (`eidos doctor`) and CLI-native agent lifecycle hooks (`eidos hook ...`)."""

import shutil
import subprocess
import sys
from pathlib import Path

import orjson
import typer
from rich.table import Table

import eidos
from eidos.cli._shared import PANEL_PLUGINS, PANEL_SETUP, console, has_eidos_markers, resolve_cli_eidos_dir
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import find_git_root, find_repo_root, resolve_spec_and_repo_roots
from eidos.plugin.hooks import execute_hook
from eidos.plugin.version import (
    check_cli_update_status,
    check_plugin_version_status,
)


def doctor_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to project or eidos directory (defaults to current directory).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON diagnostics report.",
    ),
):
    """Run health checks on the Eidos CLI installation, Git repository, living specs, index, and agent plugin."""
    eidos_dir = resolve_cli_eidos_dir(path).resolve()
    if eidos_dir.name == "eidos":
        repo_root = eidos_dir.parent
    elif eidos_dir.exists():
        _, repo_root = resolve_spec_and_repo_roots(eidos_dir)
    else:
        repo_root = find_repo_root((path or Path.cwd()).resolve())

    path_bin = shutil.which("eidos")
    venv_bin = Path(sys.executable).resolve().parent / ("eidos.exe" if sys.platform == "win32" else "eidos")
    eidos_bin = path_bin or (str(venv_bin) if venv_bin.is_file() else sys.executable)
    cli_on_path = bool(path_bin)
    cli_ok = bool(eidos_bin)

    git_ok = False
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        git_ok = res.returncode == 0
    except OSError:
        git_ok = False

    specs_exist = has_eidos_markers(eidos_dir)
    specs_valid = False
    total_specs = 0
    valid_specs = 0
    if specs_exist:
        val_report = SpecValidator().validate_directory(eidos_dir)
        specs_valid = val_report.is_clean
        total_specs = val_report.total_docs
        valid_specs = val_report.valid_docs

    db_path = eidos_dir / ".cache" / "index.db"
    index_ok = False
    index_entities = 0
    if specs_exist:
        try:
            idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
            st = idx.get_index_status()
            index_ok = True
            index_entities = st.get("total_entities", 0)
        except (OSError, RuntimeError):
            index_ok = False

    plugin_root = find_git_root(repo_root) or repo_root
    plugin_status = check_plugin_version_status(project_dir=plugin_root)
    plugin_installed = plugin_status.get("status") != "not_installed"
    plugin_up_to_date = plugin_status.get("status") == "up_to_date"
    hooks_ok = bool(plugin_status.get("hooks_ok", True))
    hooks_status = str(plugin_status.get("hooks_status", "not_installed" if not plugin_installed else "disabled"))
    target_hooks = plugin_status.get("target_hooks", {})
    hook_errors = list(plugin_status.get("hook_errors", []))
    agent_plugin_ok = not plugin_installed or (plugin_up_to_date and hooks_ok)
    agent_plugin_status = (
        "hook_error" if (plugin_installed and plugin_up_to_date and not hooks_ok) else plugin_status.get("status")
    )
    cli_update = check_cli_update_status()

    checks = {
        "cli_binary": {
            "ok": cli_ok,
            "on_path": cli_on_path,
            "eidos_bin": eidos_bin,
            "version": eidos.__version__,
        },
        "cli_update": {
            "ok": True,
            "status": cli_update.get("status"),
            "installed_version": cli_update.get("installed_version"),
            "latest_version": cli_update.get("latest_version"),
            "update_available": bool(cli_update.get("update_available")),
            "upgrade_command": cli_update.get("upgrade_command"),
        },
        "git_repository": {
            "ok": git_ok,
            "repo_root": str(repo_root),
        },
        "living_specs": {
            "ok": specs_exist and specs_valid,
            "exists": specs_exist,
            "valid": specs_valid,
            "valid_docs": valid_specs,
            "total_docs": total_specs,
            "eidos_dir": str(eidos_dir),
        },
        "knowledge_graph_index": {
            "ok": index_ok,
            "db_path": str(db_path),
            "total_entities": index_entities,
        },
        "agent_plugin": {
            "ok": agent_plugin_ok,
            "installed": plugin_installed,
            "status": agent_plugin_status,
            "installed_version": plugin_status.get("installed_version"),
            "bundled_version": plugin_status.get("bundled_version"),
            "hooks_ok": hooks_ok,
            "hooks_status": hooks_status,
            "target_hooks": target_hooks,
            "hook_errors": hook_errors,
        },
    }
    overall_ok = cli_ok and git_ok and (not specs_exist or (specs_valid and index_ok)) and checks["agent_plugin"]["ok"]

    if json_output:
        typer.echo(orjson.dumps({"healthy": overall_ok, "checks": checks}, option=orjson.OPT_INDENT_2).decode("utf-8"))
        if not overall_ok:
            raise typer.Exit(code=1)
        return

    table = Table(title="Eidos Environment & Workspace Doctor", show_header=True, header_style="bold cyan")
    table.add_column("Check", style="bold white")
    table.add_column("Status", justify="center")
    table.add_column("Details", style="dim")

    table.add_row(
        "CLI Binary on PATH",
        "[bold green]PASS[/bold green]" if cli_on_path else "[bold yellow]WARN[/bold yellow]",
        f"{eidos_bin} (v{eidos.__version__})"
        if cli_on_path
        else f"Active via virtualenv/interpreter ({eidos_bin}); not on global PATH (v{eidos.__version__})",
    )
    update_status = cli_update.get("status")
    table.add_row(
        "CLI Update Check",
        "[bold green]PASS[/bold green]"
        if update_status in ("up_to_date", "newer")
        else ("[bold yellow]WARN[/bold yellow]" if update_status == "update_available" else "[dim]SKIPPED[/dim]"),
        str(cli_update.get("status_text") or f"Installed: v{eidos.__version__}"),
    )
    table.add_row(
        "Git Repository",
        "[bold green]PASS[/bold green]" if git_ok else "[bold red]FAIL[/bold red]",
        str(repo_root),
    )
    table.add_row(
        "Living Specifications",
        "[bold green]PASS[/bold green]"
        if (specs_exist and specs_valid)
        else ("[bold yellow]UNINITIALIZED[/bold yellow]" if not specs_exist else "[bold red]FAIL[/bold red]"),
        f"{valid_specs}/{total_specs} valid specs in {eidos_dir}" if specs_exist else "Run 'eidos init' to initialize",
    )
    table.add_row(
        "SQLite Knowledge Graph",
        "[bold green]PASS[/bold green]" if index_ok else "[bold yellow]WARN[/bold yellow]",
        f"{index_entities} entities indexed ({db_path.relative_to(repo_root) if db_path.is_relative_to(repo_root) else db_path})",
    )
    if plugin_installed:
        base_plugin_detail = (
            f"Installed: v{plugin_status.get('installed_version')} / Bundled: v{plugin_status.get('bundled_version')}"
        )
        if not hooks_ok and hook_errors:
            plugin_details = f"{base_plugin_detail} — Hook error: {'; '.join(hook_errors)}"
        else:
            plugin_details = f"{base_plugin_detail} (hooks: {hooks_status})"
    else:
        plugin_details = "Optional: Run 'eidos plugin install <target>' to enable agent skills & hooks"

    if plugin_installed and not hooks_ok:
        plugin_badge = "[bold red]FAIL[/bold red]"
    elif plugin_up_to_date:
        plugin_badge = "[bold green]PASS[/bold green]"
    elif not plugin_installed:
        plugin_badge = "[dim]NOT INSTALLED[/dim]"
    else:
        plugin_badge = f"[bold yellow]{plugin_status.get('status', '').upper()}[/bold yellow]"

    table.add_row(
        "Agent Plugin Status",
        plugin_badge,
        plugin_details,
    )

    console.print(table)
    if not overall_ok:
        raise typer.Exit(code=1)


def hook_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Repository root or eidos directory path.",
    ),
):
    """Execute native Eidos stateless Stop lifecycle hook."""
    code = execute_hook(repo_root=path)
    if code != 0:
        raise typer.Exit(code=code)


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("doctor", rich_help_panel=PANEL_SETUP)(doctor_cmd)
    app.command("hook", rich_help_panel=PANEL_PLUGINS)(hook_cmd)
