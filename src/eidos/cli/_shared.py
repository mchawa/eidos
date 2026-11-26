"""Shared CLI primitives: the Rich console, help-panel names, path resolution, and repo config helpers (CMP-CLI-APP)."""

from pathlib import Path
from typing import Any

import orjson
import typer
from rich.console import Console

from eidos.core.workspace.config import (
    ConfigValidationError,
    EidosConfig,
    load_repo_config_with_sources,
)
from eidos.core.workspace.project import _has_eidos_markers, find_git_root, find_repo_root, resolve_spec_and_repo_roots

PANEL_SETUP = "Setup & Workspace"
PANEL_GOVERNANCE = "Governance & Verification"
PANEL_SPECS = "Living Catalog & Mutations"
PANEL_KNOWLEDGE = "Knowledge Graph & AI Context"
PANEL_PORTAL = "Living Web Portal"
PANEL_PLUGINS = "Agent Ecosystem & Plugins"

console = Console()


def has_eidos_markers(cand: Path) -> bool:
    """Return True when ``cand`` looks like an ``eidos/`` specification root directory (ignoring cache-only dirs)."""
    return _has_eidos_markers(cand)


def _get_current_cli_context() -> Any:
    """Return the active Typer/Click execution context, supporting both Typer 0.27+ vendored _click and click."""
    try:
        from typer._click.globals import get_current_context
    except ImportError:  # pragma: no cover - fallback for older Typer < 0.27 with standalone click
        import click  # type: ignore[import-not-found]

        get_current_context = click.get_current_context
    return get_current_context(silent=True)


def resolve_param_from_config(param_name: str, cli_value: Any, config_value: Any) -> Any:
    """Return ``cli_value`` if passed explicitly on the CLI (or outside Click/Typer context), else ``config_value``."""
    ctx = _get_current_cli_context()
    if ctx is None:
        return cli_value
    source = ctx.get_parameter_source(param_name)
    if source is not None and getattr(source, "name", None) == "COMMANDLINE":
        return cli_value
    return config_value


def load_cli_repo_config_with_sources(
    eidos_dir: Path,
    *,
    repo_root: Path | None = None,
    json_output: bool = False,
) -> tuple[EidosConfig, Path | None, dict[str, str]]:
    """Load optional ``<eidos_dir>/eidos.yaml`` with per-key provenance for CLI commands, failing closed on errors."""
    effective_root = repo_root
    if effective_root is None:
        resolved_eidos = eidos_dir.resolve()
        if resolved_eidos.name == "eidos":
            effective_root = resolved_eidos.parent
        elif resolved_eidos.exists():
            _, effective_root = resolve_spec_and_repo_roots(resolved_eidos)
        else:
            effective_root = find_repo_root(resolved_eidos.parent if resolved_eidos.parent.exists() else None)
    try:
        return load_repo_config_with_sources(eidos_dir=eidos_dir, repo_root=effective_root)
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


def load_cli_repo_config(
    eidos_dir: Path,
    *,
    repo_root: Path | None = None,
    json_output: bool = False,
) -> EidosConfig:
    """Load optional ``<eidos_dir>/eidos.yaml`` for CLI commands, failing closed with exit code 1 on errors."""
    cfg, _cfg_path, _sources = load_cli_repo_config_with_sources(
        eidos_dir=eidos_dir,
        repo_root=repo_root,
        json_output=json_output,
    )
    return cfg


def resolve_cli_eidos_dir(
    target_path: Path | None = None,
    hint_files: list[str] | None = None,
) -> Path:
    """Resolve the active ``eidos/`` specification directory for CLI commands (supporting monorepo sub-projects)."""
    if target_path is None or target_path == Path("eidos"):
        if hint_files:
            for raw_f in hint_files:
                if not raw_f or not str(raw_f).strip():
                    continue
                cand_f = Path(str(raw_f).strip())
                search_start = cand_f if cand_f.exists() else (cand_f.parent if cand_f.parent.exists() else None)
                if search_start is not None:
                    hint_repo = find_repo_root(search_start)
                    hint_eidos = hint_repo / "eidos"
                    if has_eidos_markers(hint_eidos):
                        return hint_eidos
        resolved = Path("eidos")
        if has_eidos_markers(resolved):
            return resolved
        repo_eidos = find_repo_root() / "eidos"
        if has_eidos_markers(repo_eidos):
            return repo_eidos
        if has_eidos_markers(Path(".")):
            return Path(".")
        return resolved
    if (target_path / "eidos").is_dir() and not has_eidos_markers(target_path):
        return target_path / "eidos"
    if target_path.exists() and not has_eidos_markers(target_path) and "eidos" not in target_path.name.lower():
        git_root = find_git_root(target_path)
        repo = find_repo_root(target_path)
        if (
            repo != target_path.resolve()
            and has_eidos_markers(repo / "eidos")
            and (
                (git_root is not None and repo != git_root)
                or target_path.name in ("src", "lib", "app", "tests", "test", "pkg", "cmd", "internal")
            )
        ):
            return repo / "eidos"
    return target_path
