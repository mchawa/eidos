"""Installation health diagnostics (`eidos doctor`) and CLI-native agent lifecycle hooks (`eidos hook ...`)."""

import shutil
import subprocess
import sys
from pathlib import Path

import orjson
import typer
from rich.table import Table

import eidos
from eidos.cli._shared import PANEL_PLUGINS, PANEL_SETUP, console
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import find_repo_root
from eidos.plugin.hooks import execute_hook
from eidos.plugin.version import (
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
    start = (path or Path.cwd()).resolve()
    repo_root = find_repo_root(start)
    if path is None:
        eidos_dir = repo_root / "eidos"
    elif (
        (start / "system.adoc").exists()
        or (start != repo_root and not (start / "eidos").is_dir())
        or any(start.glob("*.adoc"))
    ):
        eidos_dir = start
    else:
        eidos_dir = start / "eidos"

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

    specs_exist = eidos_dir.exists() and ((eidos_dir / "system.adoc").exists() or any(eidos_dir.rglob("*.adoc")))
    specs_valid = False
    total_specs = 0
    valid_specs = 0
    if eidos_dir.exists():
        val_report = SpecValidator().validate_directory(eidos_dir)
        specs_valid = val_report.is_clean
        total_specs = val_report.total_docs
        valid_specs = val_report.valid_docs

    db_path = eidos_dir / ".cache" / "index.db"
    index_ok = False
    index_entities = 0
    if eidos_dir.exists():
        try:
            idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
            st = idx.get_index_status()
            index_ok = True
            index_entities = st.get("total_entities", 0)
        except (OSError, RuntimeError):
            index_ok = False

    plugin_status = check_plugin_version_status(project_dir=repo_root)
    plugin_installed = plugin_status.get("status") != "not_installed"
    plugin_up_to_date = plugin_status.get("status") == "up_to_date"

    checks = {
        "cli_binary": {
            "ok": cli_ok,
            "on_path": cli_on_path,
            "eidos_bin": eidos_bin,
            "version": eidos.__version__,
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
            "ok": not plugin_installed or plugin_up_to_date,
            "installed": plugin_installed,
            "status": plugin_status.get("status"),
            "installed_version": plugin_status.get("installed_version"),
            "bundled_version": plugin_status.get("bundled_version"),
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
    table.add_row(
        "Agent Plugin Status",
        "[bold green]PASS[/bold green]"
        if plugin_up_to_date
        else (
            "[dim]NOT INSTALLED[/dim]"
            if not plugin_installed
            else f"[bold yellow]{plugin_status.get('status', '').upper()}[/bold yellow]"
        ),
        f"Installed: v{plugin_status.get('installed_version')} / Bundled: v{plugin_status.get('bundled_version')}"
        if plugin_installed
        else "Optional: Run 'eidos plugin install' to enable agent skills & hooks",
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
