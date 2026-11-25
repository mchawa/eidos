"""Repository configuration CLI commands (`eidos config ...`, CMP-CLI-CONFIG-COMMANDS)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import console, resolve_cli_eidos_dir
from eidos.core.workspace.config import (
    ConfigValidationError,
    get_config_schema_info,
    init_config_file,
    load_repo_config_with_sources,
)
from eidos.core.workspace.project import find_repo_root, resolve_spec_and_repo_roots

config_app = typer.Typer(
    name="config",
    help="Scaffold, inspect, and view the schema for optional repository configuration (eidos/eidos.yaml).",
    add_completion=False,
)


def _flatten_config_dict(data: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(_flatten_config_dict(value, dotted))
        else:
            rows.append((dotted, value))
    return rows


def _format_config_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return orjson.dumps(value).decode("utf-8")
    return str(value)


def _resolve_repo_root_for_eidos(eidos_dir: Path) -> Path:
    resolved_eidos = eidos_dir.resolve()
    if resolved_eidos.name == "eidos":
        return resolved_eidos.parent
    if resolved_eidos.exists():
        _, repo_root = resolve_spec_and_repo_roots(resolved_eidos)
        return repo_root
    return find_repo_root(resolved_eidos.parent if resolved_eidos.parent.exists() else None)


@config_app.command("init")
def config_init_cmd(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite an existing eidos/eidos.yaml file.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos specifications directory (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output scaffolded configuration file path as machine-readable JSON.",
    ),
) -> None:
    """Scaffold a commented, schema-validated starter eidos/eidos.yaml file."""
    eidos_dir = resolve_cli_eidos_dir(path)
    try:
        cfg_path = init_config_file(eidos_dir, force=force)
    except (FileNotFoundError, FileExistsError, ConfigValidationError, OSError) as exc:
        if json_output:
            payload: dict[str, Any] = {"error": str(exc)}
            if isinstance(exc, ConfigValidationError):
                payload["errors"] = list(exc.errors)
            typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode("utf-8"))
        else:
            console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(
            orjson.dumps(
                {"path": str(cfg_path), "created": True, "forced": force},
                option=orjson.OPT_INDENT_2,
            ).decode("utf-8")
        )
        return

    console.print(f"[bold green]✓ Created repository configuration:[/bold green] {cfg_path}")
    console.print(
        "[dim]Inspect effective values with [bold cyan]eidos config show[/bold cyan] or view all keys via "
        "[bold cyan]eidos config schema[/bold cyan].[/dim]"
    )


@config_app.command("show")
def config_show_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos specifications directory (defaults to ./eidos).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output resolved configuration and per-key source attribution as machine-readable JSON.",
    ),
) -> None:
    """Display the active config file, effective resolved settings, and per-key source attribution."""
    eidos_dir = resolve_cli_eidos_dir(path)
    repo_root = _resolve_repo_root_for_eidos(eidos_dir)
    try:
        cfg, cfg_path, sources = load_repo_config_with_sources(eidos_dir=eidos_dir, repo_root=repo_root)
    except ConfigValidationError as exc:
        if json_output:
            typer.echo(
                orjson.dumps(
                    {"error": str(exc), "errors": list(exc.errors)},
                    option=orjson.OPT_INDENT_2,
                ).decode("utf-8")
            )
        else:
            console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    cfg_dict = cfg.to_dict()
    if json_output:
        typer.echo(
            orjson.dumps(
                {
                    "config_file": str(cfg_path) if cfg_path is not None else None,
                    "status": "loaded" if cfg_path is not None else "absent",
                    "config": cfg_dict,
                    "sources": sources,
                },
                option=orjson.OPT_INDENT_2,
            ).decode("utf-8")
        )
        return

    file_label = str(cfg_path) if cfg_path is not None else "absent (using built-in defaults)"
    console.print(
        Panel(
            f"[bold cyan]Config File:[/bold cyan] {file_label}\n"
            f"[bold cyan]Precedence:[/bold cyan] CLI flags > EIDOS_* env vars > eidos/eidos.yaml > built-in defaults",
            title="[bold blue]Eidos Effective Repository Configuration[/bold blue]",
            expand=False,
        )
    )

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="bold white")
    table.add_column("Effective Value", style="white")
    table.add_column("Source", justify="center")

    source_styles = {
        "env": "[bold magenta]env[/bold magenta]",
        "file": "[bold green]file[/bold green]",
        "default": "[dim]default[/dim]",
    }
    for key, val in _flatten_config_dict(cfg_dict):
        src = sources.get(key, "default")
        table.add_row(key, _format_config_value(val), source_styles.get(src, src))

    console.print(table)


@config_app.command("schema")
def config_schema_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output the offline eidos/eidos.yaml configuration schema and EIDOS_* env vars as JSON.",
    ),
) -> None:
    """Inspect the bundled eidos/eidos.yaml v1 schema, allowed keys, defaults, and EIDOS_* env vars offline."""
    info = get_config_schema_info()
    if json_output:
        typer.echo(orjson.dumps(info, option=orjson.OPT_INDENT_2).decode("utf-8"))
        return

    precedence_raw = info.get("precedence")
    precedence_text = (
        " > ".join(str(p) for p in precedence_raw)
        if isinstance(precedence_raw, (list, tuple))
        else str(precedence_raw or "")
    )
    console.print(
        Panel(
            f"[bold cyan]Schema Version:[/bold cyan] v{info['schema_version']}\n"
            f"[bold cyan]Canonical Path:[/bold cyan] {info['canonical_path']}\n"
            f"[bold cyan]Precedence:[/bold cyan] {precedence_text}",
            title="[bold blue]Eidos Repository Configuration Schema[/bold blue]",
            expand=False,
        )
    )

    keys_table = Table(title="Configuration Keys (eidos/eidos.yaml)", show_header=True, header_style="bold cyan")
    keys_table.add_column("Key", style="bold white")
    keys_table.add_column("Type / Allowed", style="yellow")
    keys_table.add_column("Default", style="green")
    keys_table.add_column("Description", style="dim")

    for section in info["sections"]:
        for k_info in section["keys"]:
            type_label = str(k_info.get("type") or "any")
            enum_vals = k_info.get("enum")
            if enum_vals:
                type_label = f"{type_label} ({', '.join(str(e) for e in enum_vals)})"
            keys_table.add_row(
                str(k_info["key"]),
                type_label,
                _format_config_value(k_info.get("default")),
                str(k_info.get("description") or ""),
            )
    console.print(keys_table)

    env_table = Table(title="Supported EIDOS_* Environment Variables", show_header=True, header_style="bold magenta")
    env_table.add_column("Environment Variable", style="bold cyan")
    env_table.add_column("Config Key", style="white")
    env_table.add_column("Type", style="yellow")
    env_table.add_column("Description", style="dim")
    for env_entry in info["env_vars"]:
        env_table.add_row(
            str(env_entry["env_var"]),
            str(env_entry["config_key"]),
            str(env_entry["type"]),
            str(env_entry["description"]),
        )
    console.print(env_table)
