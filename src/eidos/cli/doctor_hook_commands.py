"""Installation health diagnostics (`eidos doctor`) and CLI-native agent lifecycle hooks (`eidos hook ...`)."""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson
import typer
from rich.table import Table

import eidos
from eidos.cli._shared import PANEL_PLUGINS, PANEL_SETUP, console, has_eidos_markers, resolve_cli_eidos_dir
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.lifecycle.delta_engine import is_delta_ready_to_apply, list_deltas
from eidos.core.lifecycle.memory_engine import list_memories
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.config import CONFIG_FILENAME, find_config_file, load_repo_config_with_errors
from eidos.core.workspace.project import find_git_root, find_repo_root, resolve_spec_and_repo_roots
from eidos.plugin.hooks import execute_hook
from eidos.plugin.managed_block import is_managed_block_opted_out, read_managed_block
from eidos.plugin.version import (
    check_cli_update_status,
    check_plugin_version_status,
)


def _check_onboarding_governance(plugin_root: Path) -> dict[str, Any]:
    """Verify root AGENTS.md managed-block integrity when Agent Skills are installed."""
    agents_path = plugin_root / "AGENTS.md"
    onboarding_expected = any(
        (plugin_root / sub / ".eidos-manifest.json").is_file()
        or (
            not (plugin_root / sub / "skills").is_symlink()
            and (plugin_root / sub / "skills" / "eidos" / "SKILL.md").is_file()
        )
        for sub in (".agents", ".claude", ".aider")
    )

    if not onboarding_expected:
        agents_md_ok = True
        agents_md_status = "not_installed"
        detail = "No Agent Skills installed"
    elif is_managed_block_opted_out(agents_path, "agents-md"):
        agents_md_ok = True
        agents_md_status = "opted_out"
        detail = f"Opted out via <!-- eidos:agents-md off --> ({agents_path})"
    elif not agents_path.is_file():
        agents_md_ok = False
        agents_md_status = "missing_file"
        detail = f"Missing root AGENTS.md ({agents_path}); run 'eidos plugin install <target>' to restore"
    else:
        try:
            block = read_managed_block(agents_path, "agents-md")
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            agents_md_ok = False
            agents_md_status = "malformed"
            detail = f"Malformed AGENTS.md managed block: {exc}"
        else:
            if block is None or not block.inner.strip():
                agents_md_ok = False
                agents_md_status = "missing_block"
                detail = (
                    f"Missing or wiped Eidos managed block in {agents_path}; "
                    "run 'eidos plugin install <target>' to restore"
                )
            else:
                agents_md_ok = True
                agents_md_status = "valid"
                detail = f"Managed block intact ({agents_path})"

    return {
        "ok": agents_md_ok,
        "status": agents_md_status,
        "expected": onboarding_expected,
        "path": str(agents_path),
        "agents_md_ok": agents_md_ok,
        "detail": detail,
    }


def _check_open_deltas(
    eidos_dir: Path,
    specs_exist: bool,
    *,
    schema_error_paths: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    """Detect open SpecDeltas that satisfy all apply_delta preconditions on disk (advisory, non-blocking)."""
    if not specs_exist or not (eidos_dir / "deltas").is_dir():
        return {
            "ok": True,
            "status": "clean",
            "active_count": 0,
            "ready_to_apply": [],
            "detail": "No open SpecDeltas",
        }
    active_deltas = list_deltas(eidos_dir, status_filter="active", include_archived=False)
    ready_to_apply: list[str] = []
    for delta in active_deltas:
        delta_id = str(delta.get("id") or "")
        delta_target = str(delta.get("path") or delta_id)
        if not delta_target:
            continue
        ready_item = is_delta_ready_to_apply(
            eidos_dir,
            delta_target,
            delta_rel_path=str(delta.get("relative_path") or ""),
            delta_status=str(delta.get("status") or "proposed"),
            schema_error_paths=schema_error_paths,
        )
        if ready_item is not None:
            ready_to_apply.append(str(ready_item["id"]) or delta_id or Path(delta_target).stem)
    if ready_to_apply:
        sample = ", ".join(ready_to_apply[:3])
        suffix = f" (+{len(ready_to_apply) - 3} more)" if len(ready_to_apply) > 3 else ""
        return {
            "ok": True,
            "status": "ready_to_apply",
            "active_count": len(active_deltas),
            "ready_to_apply": ready_to_apply,
            "detail": (
                f"{len(ready_to_apply)} open SpecDelta(s) already verified on disk "
                f"({sample}{suffix}); run 'eidos delta apply <ID>'"
            ),
        }
    if active_deltas:
        return {
            "ok": True,
            "status": "in_progress",
            "active_count": len(active_deltas),
            "ready_to_apply": [],
            "detail": f"{len(active_deltas)} active SpecDelta(s) in progress",
        }
    return {
        "ok": True,
        "status": "clean",
        "active_count": 0,
        "ready_to_apply": [],
        "detail": "No open SpecDeltas",
    }


def _check_team_memories(eidos_dir: Path, specs_exist: bool) -> dict[str, Any]:
    """Report non-blocking advisory health summary of active team operational memories under eidos/memory/."""
    mem_dir = eidos_dir / "memory"
    if not specs_exist or not mem_dir.is_dir() or mem_dir.is_symlink():
        return {
            "ok": True,
            "status": "empty",
            "total_active": 0,
            "fresh": 0,
            "stale": 0,
            "expired": 0,
            "unverifiable": 0,
            "detail": "No active team memories (capture gotchas via 'eidos memory add')",
        }

    try:
        entries = list_memories(eidos_dir, status="active", include_archived=False, include_freshness=True)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        return {
            "ok": True,
            "status": "unavailable",
            "total_active": 0,
            "fresh": 0,
            "stale": 0,
            "expired": 0,
            "unverifiable": 0,
            "detail": f"Unable to inspect team memories ({exc})",
        }
    total_active = len(entries)
    if total_active == 0:
        return {
            "ok": True,
            "status": "empty",
            "total_active": 0,
            "fresh": 0,
            "stale": 0,
            "expired": 0,
            "unverifiable": 0,
            "detail": "No active team memories (capture gotchas via 'eidos memory add')",
        }

    fresh = sum(1 for e in entries if e.get("freshness") == "fresh")
    stale = sum(1 for e in entries if e.get("freshness") == "stale")
    expired = sum(1 for e in entries if e.get("freshness") == "expired")
    unverifiable = sum(1 for e in entries if e.get("freshness") == "unverifiable")
    needs_review = stale + expired + unverifiable

    if needs_review > 0:
        parts = [f"{fresh} fresh"]
        if stale:
            parts.append(f"{stale} stale")
        if expired:
            parts.append(f"{expired} expired")
        if unverifiable:
            parts.append(f"{unverifiable} unverifiable")
        return {
            "ok": True,
            "status": "needs_review",
            "total_active": total_active,
            "fresh": fresh,
            "stale": stale,
            "expired": expired,
            "unverifiable": unverifiable,
            "detail": (
                f"{total_active} active ({', '.join(parts)}) — run 'eidos memory audit' or 'eidos memory verify <ID>'"
            ),
        }

    return {
        "ok": True,
        "status": "clean",
        "total_active": total_active,
        "fresh": fresh,
        "stale": 0,
        "expired": 0,
        "unverifiable": 0,
        "detail": f"{total_active} active ({fresh} fresh)",
    }


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

    cfg_path = find_config_file(eidos_dir=eidos_dir, repo_root=repo_root)
    repo_cfg, cfg_errors = load_repo_config_with_errors(eidos_dir=eidos_dir, repo_root=repo_root)
    if cfg_errors:
        cfg_status = "invalid"
        cfg_ok = False
        cfg_detail = "; ".join(cfg_errors)
    elif cfg_path is not None:
        cfg_status = "valid"
        cfg_ok = True
        rel_cfg = str(cfg_path.relative_to(repo_root)) if cfg_path.is_relative_to(repo_root) else str(cfg_path)
        cfg_detail = f"Valid configuration ({rel_cfg})"
    else:
        cfg_status = "absent"
        cfg_ok = True
        cfg_detail = "Not configured (using built-in defaults; optional: run 'eidos config init')"
    repo_config_check = {
        "ok": cfg_ok,
        "status": cfg_status,
        "path": str(cfg_path) if cfg_path is not None else str(eidos_dir / CONFIG_FILENAME),
        "errors": list(cfg_errors),
        "detail": cfg_detail,
    }

    specs_exist = has_eidos_markers(eidos_dir)
    specs_valid = False
    total_specs = 0
    valid_specs = 0
    schema_error_paths: tuple[str, ...] | None = None
    if specs_exist:
        val_report = SpecValidator().validate_directory(eidos_dir)
        specs_valid = val_report.is_clean
        total_specs = val_report.total_docs
        valid_specs = val_report.valid_docs
        schema_error_paths = tuple(val_report.schema_errors.keys())

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
    skipped_files = list(plugin_status.get("skipped_files", []))
    configured_targets = list(repo_cfg.plugin.targets)
    missing_configured_targets = [t for t in configured_targets if t not in target_hooks]
    agent_plugin_ok = not plugin_installed or (plugin_up_to_date and hooks_ok)
    agent_plugin_status = (
        "hook_error" if (plugin_installed and plugin_up_to_date and not hooks_ok) else plugin_status.get("status")
    )
    agents_md_check = _check_onboarding_governance(plugin_root)
    open_deltas_check = _check_open_deltas(eidos_dir, specs_exist, schema_error_paths=schema_error_paths)
    team_memories_check = _check_team_memories(eidos_dir, specs_exist)
    cli_update = (
        check_cli_update_status() if repo_cfg.plugin.updates_check else check_cli_update_status(allow_network=False)
    )

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
        "repo_config": repo_config_check,
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
            "skipped_files": skipped_files,
            "hooks_ok": hooks_ok,
            "hooks_status": hooks_status,
            "target_hooks": target_hooks,
            "hook_errors": hook_errors,
            "configured_targets": configured_targets,
            "missing_configured_targets": missing_configured_targets,
            "agents_md_ok": agents_md_check["agents_md_ok"],
        },
        "agents_md": agents_md_check,
        "open_deltas": open_deltas_check,
        "team_memories": team_memories_check,
    }
    overall_ok = (
        cli_ok
        and git_ok
        and checks["repo_config"]["ok"]
        and (not specs_exist or (specs_valid and index_ok))
        and checks["agent_plugin"]["ok"]
        and checks["agents_md"]["ok"]
    )

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
    if cfg_status == "invalid":
        cfg_badge = "[bold red]FAIL[/bold red]"
    elif cfg_status == "valid":
        cfg_badge = "[bold green]PASS[/bold green]"
    else:
        cfg_badge = "[dim]ABSENT[/dim]"
    table.add_row(
        "Repo Configuration",
        cfg_badge,
        cfg_detail,
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
        elif plugin_status.get("status") == "modified":
            plugin_details = f"{base_plugin_detail} — {plugin_status.get('status_text')}"
        else:
            plugin_details = f"{base_plugin_detail} (hooks: {hooks_status})"
    else:
        plugin_details = "Optional: Run 'eidos plugin install <target>' to enable agent skills & hooks"
    if missing_configured_targets:
        plugin_details += (
            f" — Advisory: configured target(s) not installed: {', '.join(missing_configured_targets)} "
            "(run 'eidos plugin install')"
        )

    if plugin_installed and not hooks_ok:
        plugin_badge = "[bold red]FAIL[/bold red]"
    elif plugin_installed and not plugin_up_to_date:
        plugin_badge = f"[bold yellow]{plugin_status.get('status', '').upper()}[/bold yellow]"
    elif plugin_up_to_date and not missing_configured_targets:
        plugin_badge = "[bold green]PASS[/bold green]"
    elif missing_configured_targets:
        plugin_badge = "[bold yellow]WARN[/bold yellow]"
    else:
        plugin_badge = "[dim]NOT INSTALLED[/dim]"

    table.add_row(
        "Agent Plugin Status",
        plugin_badge,
        plugin_details,
    )

    if not agents_md_check["ok"]:
        onboarding_badge = "[bold red]FAIL[/bold red]"
    elif agents_md_check["status"] == "not_installed":
        onboarding_badge = "[dim]NOT INSTALLED[/dim]"
    elif agents_md_check["status"] == "opted_out":
        onboarding_badge = "[dim]OPTED OUT[/dim]"
    else:
        onboarding_badge = "[bold green]PASS[/bold green]"

    table.add_row(
        "Onboarding Governance",
        onboarding_badge,
        str(agents_md_check["detail"]),
    )

    deltas_badge = (
        "[bold yellow]WARN[/bold yellow]"
        if open_deltas_check["status"] == "ready_to_apply"
        else "[bold green]PASS[/bold green]"
    )
    table.add_row(
        "Open SpecDeltas",
        deltas_badge,
        str(open_deltas_check["detail"]),
    )

    mem_status = team_memories_check["status"]
    if mem_status == "needs_review":
        mem_badge = "[bold yellow]WARN[/bold yellow]"
    elif mem_status == "empty":
        mem_badge = "[dim]EMPTY[/dim]"
    elif mem_status == "unavailable":
        mem_badge = "[dim]UNAVAILABLE[/dim]"
    else:
        mem_badge = "[bold green]PASS[/bold green]"
    table.add_row(
        "Team Memories",
        mem_badge,
        str(team_memories_check["detail"]),
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
