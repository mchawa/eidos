"""Team operational memory CLI commands (`eidos memory ...`, CMP-CLI-MEMORY-COMMANDS)."""

from __future__ import annotations

from pathlib import Path

import orjson
import typer
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import console, resolve_cli_eidos_dir
from eidos.core.lifecycle.memory_engine import (
    add_memory,
    archive_memory,
    audit_memories,
    list_memories,
    promote_memory,
    search_memories,
    show_memory,
    update_memory,
    verify_memory,
)

memory_app = typer.Typer(
    name="memory",
    help="Manage Git-native team operational memories under eidos/memory/ (add, update, list, search, show, verify, archive, audit, promote).",
    add_completion=False,
)


def _echo_json(payload: dict | list) -> None:
    typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode("utf-8"))


def _fail(message: str, *, json_output: bool) -> None:
    if json_output:
        _echo_json({"error": message})
    else:
        console.print(f"[bold red]Error:[/bold red] {rich_escape(message)}")
    raise typer.Exit(code=1)


@memory_app.command("add")
def memory_add_cmd(
    title: str = typer.Option(..., "--title", "-t", help="Concise human-readable memory title."),
    category: str = typer.Option(
        "feedback",
        "--category",
        "-c",
        help="Team memory category: feedback, project, or reference (never personal user preferences).",
    ),
    summary: str | None = typer.Option(
        None,
        "--summary",
        "-s",
        help="Optional single-line operational takeaway (<= 220 chars; auto-derived from --guidance or --title when omitted).",
    ),
    guidance: str = typer.Option("", "--guidance", "-g", help="Actionable engineering guidance body."),
    why: str = typer.Option("", "--why", "-w", help="Rationale or incident context explaining why this rule exists."),
    entity: list[str] = typer.Option(
        [],
        "--entity",
        "-e",
        help="Target Eidos entity ID(s) to anchor this memory (repeatable, e.g. -e CMP-ORDER-STORE).",
    ),
    file: list[str] = typer.Option(
        [],
        "--file",
        "-f",
        help="Target repository file path(s) or glob pattern(s) (repeatable, e.g. -f src/core/storage.py).",
    ),
    auto_entities: bool = typer.Option(
        True,
        "--auto-entities/--no-auto-entities",
        help="Automatically infer owning CMP-*/CNT-* entity anchors from --file / --from-diff paths.",
    ),
    from_diff: bool = typer.Option(
        False,
        "--from-diff",
        help="Anchor uncommitted changed workspace files (excluding eidos/memory/**) and auto-infer their owning entities.",
    ),
    expires: str | None = typer.Option(
        None,
        "--expires",
        help="Optional ISO expiration date YYYY-MM-DD (or 'none' to opt out of memory.default_expiry_days).",
    ),
    status: str = typer.Option("active", "--status", help="Initial status: active (default) or draft."),
    semantic: bool = typer.Option(
        True,
        "--semantic/--no-semantic",
        help="Use pre-computed Harrier embeddings for overlap detection when available.",
    ),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output created memory metadata as JSON."),
) -> None:
    """Create a schema-validated team operational memory under eidos/memory/."""
    from datetime import date, timedelta

    from eidos.cli._shared import load_cli_repo_config, resolve_param_from_config

    eidos_dir = resolve_cli_eidos_dir(path)
    repo_cfg = load_cli_repo_config(eidos_dir, json_output=json_output)
    eff_semantic = bool(resolve_param_from_config("semantic", semantic, repo_cfg.context.semantic.enabled))
    if expires is not None:
        eff_expires: str | None = None if expires.strip().lower() == "none" else expires
    elif repo_cfg.memory.default_expiry_days is not None:
        eff_expires = (date.today() + timedelta(days=repo_cfg.memory.default_expiry_days)).isoformat()
    else:
        eff_expires = None

    eff_paths = list(file)
    if from_diff:
        from eidos.core.workspace.project import find_git_root, find_repo_root, resolve_spec_and_repo_roots
        from eidos.vcs.diff import GitResolutionError, classify_changed_files, get_git_diff_files

        resolved_eidos = eidos_dir.resolve()
        if resolved_eidos.name == "eidos":
            repo_root = resolved_eidos.parent
        elif resolved_eidos.exists():
            _, repo_root = resolve_spec_and_repo_roots(resolved_eidos)
        else:
            repo_root = find_repo_root(resolved_eidos.parent if resolved_eidos.parent.exists() else None)

        if find_git_root(repo_root) is None:
            _fail("Cannot use --from-diff outside a Git repository.", json_output=json_output)

        try:
            changed = get_git_diff_files(
                repo_root,
                uncommitted=True,
                repo_config=repo_cfg,
                eidos_dir=resolved_eidos,
            )
        except GitResolutionError as exc:
            _fail(str(exc), json_output=json_output)

        cls = classify_changed_files(
            changed,
            repo_root=repo_root,
            eidos_dir=resolved_eidos,
            repo_config=repo_cfg,
        )
        try:
            eidos_rel_prefix = (
                str(resolved_eidos.relative_to(repo_root.resolve())).replace("\\", "/").strip("/") or "eidos"
            )
        except ValueError:
            eidos_rel_prefix = resolved_eidos.name or "eidos"
        mem_dir_prefix = f"{eidos_rel_prefix.lower()}/memory/"

        diff_candidates = [
            *cls.code_files,
            *cls.test_files,
            *cls.spec_files,
            *cls.doc_files,
            *cls.other_files,
        ]
        for rel_p in diff_candidates:
            norm_rel = rel_p.replace("\\", "/").lstrip("./")
            if norm_rel.lower().startswith(mem_dir_prefix) or norm_rel.lower() == mem_dir_prefix.rstrip("/"):
                continue
            if (repo_root / rel_p).exists() and rel_p not in eff_paths:
                eff_paths.append(rel_p)

    try:
        res = add_memory(
            eidos_dir,
            title=title,
            category=category,
            summary=summary,
            guidance=guidance,
            why=why,
            entities=entity,
            paths=eff_paths,
            auto_entities=auto_entities,
            expires_at=eff_expires,
            status=status,
            use_semantic=eff_semantic,
        )
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json(res)
        return

    console.print(f"[bold green]✓ Saved team memory:[/bold green] [cyan]{rich_escape(str(res['id']))}[/cyan]")
    console.print(f"  File: [dim]{rich_escape(str(res['relative_path']))}[/dim]")
    console.print(
        f"  Category: {rich_escape(str(res['category']))} | Status: {rich_escape(str(res['status']))} | Verified: {rich_escape(str(res['verified_at']))}"
    )
    if res.get("inferred_entities"):
        console.print(f"  Auto-anchored entities: [cyan]{', '.join(res['inferred_entities'])}[/cyan]")
    warnings = res.get("warnings") or {}
    for dup in warnings.get("duplicate_memories") or []:
        console.print(
            f"  [yellow]⚠ Potential duplicate memory:[/yellow] {rich_escape(str(dup['id']))} ({rich_escape(str(dup['title']))}, {dup['method']}={dup['score']})"
        )
    for ov in warnings.get("spec_overlaps") or []:
        console.print(
            f"  [yellow]⚠ Potential formal spec overlap:[/yellow] {rich_escape(str(ov['id']))} ({rich_escape(str(ov['title']))}, {ov['method']}={ov['score']})"
        )


@memory_app.command("update")
def memory_update_cmd(
    id_or_path: str = typer.Argument(..., help="Memory ID (MEM-*), unambiguous prefix, or path."),
    title: str | None = typer.Option(None, "--title", "-t", help="Updated memory title."),
    category: str | None = typer.Option(
        None,
        "--category",
        "-c",
        help="Updated team memory category: feedback, project, or reference.",
    ),
    summary: str | None = typer.Option(
        None, "--summary", "-s", help="Updated single-line operational takeaway (<= 220 chars)."
    ),
    guidance: str | None = typer.Option(None, "--guidance", "-g", help="Updated actionable engineering guidance body."),
    why: str | None = typer.Option(None, "--why", "-w", help="Updated rationale or incident context."),
    entity: list[str] | None = typer.Option(
        None,
        "--entity",
        "-e",
        help="Target Eidos entity ID(s) to add (or replace when --replace-targets is set).",
    ),
    file: list[str] | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Target repository file path(s) or glob pattern(s) to add (or replace when --replace-targets is set).",
    ),
    replace_targets: bool = typer.Option(
        False,
        "--replace-targets",
        help="Replace existing target entities and paths instead of merging.",
    ),
    auto_entities: bool = typer.Option(
        True,
        "--auto-entities/--no-auto-entities",
        help="Automatically infer owning CMP-*/CNT-* entity anchors from updated --file paths.",
    ),
    expires: str | None = typer.Option(None, "--expires", help="Update optional ISO expiration date YYYY-MM-DD."),
    clear_expires: bool = typer.Option(False, "--clear-expires", help="Remove existing expires_at TTL."),
    status: str | None = typer.Option(None, "--status", help="Update status: active or draft."),
    semantic: bool = typer.Option(
        True,
        "--semantic/--no-semantic",
        help="Use pre-computed Harrier embeddings for overlap detection when available.",
    ),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output updated memory metadata as JSON."),
) -> None:
    """Update an active or draft team operational memory in place and re-stamp verification."""
    from eidos.cli._shared import load_cli_repo_config, resolve_param_from_config

    eidos_dir = resolve_cli_eidos_dir(path)
    repo_cfg = load_cli_repo_config(eidos_dir, json_output=json_output)
    eff_semantic = bool(resolve_param_from_config("semantic", semantic, repo_cfg.context.semantic.enabled))

    try:
        res = update_memory(
            eidos_dir,
            id_or_path,
            title=title,
            category=category,
            summary=summary,
            guidance=guidance,
            why=why,
            entities=entity,
            paths=file,
            replace_targets=replace_targets,
            auto_entities=auto_entities,
            expires_at=expires,
            clear_expires=clear_expires,
            status=status,
            use_semantic=eff_semantic,
        )
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json(res)
        return

    console.print(f"[bold green]✓ Updated team memory:[/bold green] [cyan]{rich_escape(str(res['id']))}[/cyan]")
    console.print(f"  File: [dim]{rich_escape(str(res['relative_path']))}[/dim]")
    console.print(
        f"  Category: {rich_escape(str(res['category']))} | Status: {rich_escape(str(res['status']))} | "
        f"Freshness: [yellow]{rich_escape(str(res['freshness']))}[/yellow] | Verified: {rich_escape(str(res['verified_at']))}"
    )
    if res.get("inferred_entities"):
        console.print(f"  Auto-anchored entities: [cyan]{', '.join(res['inferred_entities'])}[/cyan]")
    warnings = res.get("warnings") or {}
    for dup in warnings.get("duplicate_memories") or []:
        console.print(
            f"  [yellow]⚠ Potential duplicate memory:[/yellow] {rich_escape(str(dup['id']))} ({rich_escape(str(dup['title']))}, {dup['method']}={dup['score']})"
        )
    for ov in warnings.get("spec_overlaps") or []:
        console.print(
            f"  [yellow]⚠ Potential formal spec overlap:[/yellow] {rich_escape(str(ov['id']))} ({rich_escape(str(ov['title']))}, {ov['method']}={ov['score']})"
        )


@memory_app.command("list")
def memory_list_cmd(
    category: str | None = typer.Option(
        None, "--category", "-c", help="Filter by category (feedback, project, reference)."
    ),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (draft, active, promoted, archived)."
    ),
    entity: str | None = typer.Option(None, "--entity", "-e", help="Filter by anchored entity ID."),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Include archived and promoted memories under eidos/memory/archive/.",
    ),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output memories as JSON."),
) -> None:
    """List team operational memories with computed freshness status."""
    from eidos.cli._shared import load_cli_repo_config

    eidos_dir = resolve_cli_eidos_dir(path)
    load_cli_repo_config(eidos_dir, json_output=json_output)
    try:
        items = list_memories(
            eidos_dir,
            category=category,
            status=status,
            entity=entity,
            include_archived=include_archived,
        )
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json({"count": len(items), "memories": items})
        return

    if not items:
        console.print(f"[yellow]No team memories found under '{eidos_dir / 'memory'}'.[/yellow]")
        return

    table = Table(title=f"Team Operational Memories ({len(items)} found)", show_header=True)
    table.add_column("ID", style="bold cyan")
    table.add_column("Category", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Freshness", style="yellow")
    table.add_column("Summary", style="white")
    table.add_column("Path", style="dim")
    for item in items:
        table.add_row(
            item["id"],
            item["category"],
            item["status"],
            item["freshness"],
            item["summary"],
            item["relative_path"],
        )
    console.print(table)


@memory_app.command("search")
def memory_search_cmd(
    query: str = typer.Argument(..., help="Search keywords or phrase across active team memories."),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum number of results to return."),
    semantic: bool = typer.Option(
        True,
        "--semantic/--no-semantic",
        help="Enable opt-in Harrier Hybrid RRF fusion when spec_embeddings are complete.",
    ),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output search results as JSON."),
) -> None:
    """Search active team memories via BM25 and optional Harrier Hybrid RRF."""
    from eidos.cli._shared import load_cli_repo_config, resolve_param_from_config

    eidos_dir = resolve_cli_eidos_dir(path)
    repo_cfg = load_cli_repo_config(eidos_dir, json_output=json_output)
    eff_semantic = bool(resolve_param_from_config("semantic", semantic, repo_cfg.context.semantic.enabled))
    results = search_memories(eidos_dir, query, limit=limit, use_semantic=eff_semantic)
    if json_output:
        _echo_json({"query": query, "count": len(results), "results": results})
        return

    if not results:
        console.print(f"[yellow]No active team memories matched '{rich_escape(query)}'.[/yellow]")
        return

    table = Table(title=f"Memory Search: '{rich_escape(query)}' ({len(results)} matches)", show_header=True)
    table.add_column("ID", style="bold cyan")
    table.add_column("Category", style="magenta")
    table.add_column("Freshness", style="yellow")
    table.add_column("Summary", style="white")
    table.add_column("Provenance", style="dim")
    for hit in results:
        table.add_row(
            hit["id"],
            hit["category"],
            hit["freshness"],
            hit["summary"],
            f"{hit['provenance']} ({hit['confidence']})",
        )
    console.print(table)


@memory_app.command("show")
def memory_show_cmd(
    id_or_path: str = typer.Argument(..., help="Memory ID (MEM-*), unambiguous prefix, or path."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output full memory details as JSON."),
) -> None:
    """Display frontmatter metadata, freshness diagnostics, and body of a team memory."""
    from eidos.cli._shared import load_cli_repo_config

    eidos_dir = resolve_cli_eidos_dir(path)
    load_cli_repo_config(eidos_dir, json_output=json_output)
    try:
        detail = show_memory(eidos_dir, id_or_path)
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json(detail)
        return

    lines = [
        f"[bold cyan]{rich_escape(str(detail['id']))}[/bold cyan] — {rich_escape(str(detail['title']))}",
        f"Category: [magenta]{rich_escape(str(detail['category']))}[/magenta] | Status: [green]{rich_escape(str(detail['status']))}[/green] | Freshness: [yellow]{rich_escape(str(detail['freshness']))}[/yellow]",
        f"Verified: {rich_escape(str(detail['verified_at']))} (commit: {rich_escape(str(detail['verified_commit'] or 'none'))})",
        f"File: [dim]{rich_escape(str(detail['relative_path']))}[/dim]",
        f"Summary: {rich_escape(str(detail['summary']))}",
    ]
    if detail.get("freshness_reason"):
        lines.append(f"Freshness Note: {rich_escape(str(detail['freshness_reason']))}")
    if detail.get("promoted_to"):
        lines.append(f"Promoted To: [bold]{rich_escape(str(detail['promoted_to']))}[/bold]")
    if detail.get("guidance"):
        lines.append(f"\n[bold]Guidance[/bold]\n{rich_escape(str(detail['guidance']))}")
    if detail.get("why"):
        lines.append(f"\n[bold]Why[/bold]\n{rich_escape(str(detail['why']))}")
    console.print(Panel("\n".join(lines), title="Team Operational Memory", expand=False))


@memory_app.command("verify")
def memory_verify_cmd(
    id_or_path: str = typer.Argument(..., help="Memory ID (MEM-*), unambiguous prefix, or path."),
    expires: str | None = typer.Option(None, "--expires", help="Update optional ISO expiration date YYYY-MM-DD."),
    clear_expires: bool = typer.Option(False, "--clear-expires", help="Remove existing expires_at TTL."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output updated verification result as JSON."),
) -> None:
    """Re-stamp verified_at and verified_commit on a memory after checking live code."""
    from eidos.cli._shared import load_cli_repo_config

    eidos_dir = resolve_cli_eidos_dir(path)
    load_cli_repo_config(eidos_dir, json_output=json_output)
    try:
        res = verify_memory(eidos_dir, id_or_path, expires_at=expires, clear_expires=clear_expires)
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json(res)
        return

    console.print(
        f"[bold green]✓ Verified memory:[/bold green] [cyan]{res['id']}[/cyan] "
        f"(freshness: [yellow]{res['freshness']}[/yellow], verified_at: {res['verified_at']})"
    )


@memory_app.command("archive")
def memory_archive_cmd(
    id_or_path: str = typer.Argument(..., help="Memory ID (MEM-*), unambiguous prefix, or path."),
    reason: str | None = typer.Option(None, "--reason", "-r", help="Optional archival rationale appended to body."),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output archive result as JSON."),
) -> None:
    """Retire a team memory (status: archived) and move it under eidos/memory/archive/."""
    from eidos.cli._shared import load_cli_repo_config

    eidos_dir = resolve_cli_eidos_dir(path)
    load_cli_repo_config(eidos_dir, json_output=json_output)
    try:
        res = archive_memory(eidos_dir, id_or_path, reason=reason)
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json(res)
        return

    console.print(
        f"[bold green]✓ Archived memory:[/bold green] [cyan]{res['id']}[/cyan] -> [dim]{res['relative_path']}[/dim]"
    )


@memory_app.command("audit")
def memory_audit_cmd(
    check_overlaps: bool = typer.Option(
        True,
        "--check-overlaps/--no-check-overlaps",
        help="Detect duplicate memory pairs and formal spec overlaps.",
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic/--no-semantic",
        help="Use pre-computed Harrier embeddings for overlap analysis when available.",
    ),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output non-blocking memory audit report as JSON."),
) -> None:
    """Audit team memories for staleness, expiry, duplicates, and spec overlap (strictly non-blocking, exit 0)."""
    from eidos.cli._shared import load_cli_repo_config, resolve_param_from_config

    eidos_dir = resolve_cli_eidos_dir(path)
    repo_cfg = load_cli_repo_config(eidos_dir, json_output=json_output)
    eff_semantic = bool(resolve_param_from_config("semantic", semantic, semantic and repo_cfg.context.semantic.enabled))
    report = audit_memories(eidos_dir, check_overlaps=check_overlaps, use_semantic=eff_semantic)

    if json_output:
        _echo_json(report)
        return

    table = Table(title="Eidos Team Memory Audit (Non-Blocking)", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Count", justify="right", style="white")
    for label, key in (
        ("Total Memories", "total"),
        ("Active", "active"),
        ("Draft", "draft"),
        ("Fresh", "fresh"),
        ("Stale", "stale"),
        ("Expired", "expired"),
        ("Unverifiable", "unverifiable"),
        ("Promoted", "promoted"),
        ("Archived", "archived"),
    ):
        table.add_row(label, str(report[key]))
    table.add_row("Duplicate Pairs", str(len(report["duplicate_pairs"])))
    table.add_row("Formal Spec Overlaps", str(len(report["spec_overlaps"])))
    console.print(table)
    for item in report["stale_memories"]:
        console.print(f"  [yellow]STALE[/yellow] {rich_escape(str(item['id']))}: {rich_escape(str(item['reason']))}")
    for item in report["expired_memories"]:
        console.print(f"  [yellow]EXPIRED[/yellow] {rich_escape(str(item['id']))}: {rich_escape(str(item['reason']))}")
    for item in report["unverifiable_memories"]:
        console.print(f"  [dim]UNVERIFIABLE[/dim] {rich_escape(str(item['id']))}: {rich_escape(str(item['reason']))}")
    for dup in report["duplicate_pairs"]:
        console.print(
            f"  [yellow]DUPLICATE[/yellow] {rich_escape(str(dup['memory_a']))} <-> {rich_escape(str(dup['memory_b']))} ({dup['method']}={dup['score']})"
        )
    for ov in report["spec_overlaps"]:
        console.print(
            f"  [yellow]SPEC OVERLAP[/yellow] {rich_escape(str(ov['memory_id']))} -> {rich_escape(str(ov['spec_id']))} ({ov['method']}={ov['score']})"
        )


@memory_app.command("promote")
def memory_promote_cmd(
    id_or_path: str = typer.Argument(..., help="Memory ID (MEM-*), unambiguous prefix, or path."),
    to: str = typer.Option(
        ...,
        "--to",
        help="Target formal specification kind: decision, invariant, or policy.",
    ),
    spec_id: str = typer.Option(
        ...,
        "--spec-id",
        help="Verified target specification ID (DEC-*, INV-*, or POL-*).",
    ),
    archive: bool = typer.Option(
        True,
        "--archive/--no-archive",
        help="Move the promoted memory into eidos/memory/archive/ (default: --archive).",
    ),
    path: Path = typer.Option(Path("eidos"), "--path", "-p", help="Path to eidos specifications directory."),
    json_output: bool = typer.Option(False, "--json", help="Output promotion result as JSON."),
) -> None:
    """Graduate a team memory into a verified formal DEC-*, INV-*, or POL-* specification."""
    from eidos.cli._shared import load_cli_repo_config

    eidos_dir = resolve_cli_eidos_dir(path)
    load_cli_repo_config(eidos_dir, json_output=json_output)
    try:
        res = promote_memory(eidos_dir, id_or_path, to_kind=to, spec_id=spec_id, archive=archive)
    except ValueError as exc:
        _fail(str(exc), json_output=json_output)

    if json_output:
        _echo_json(res)
        return

    console.print(
        f"[bold green]✓ Promoted memory:[/bold green] [cyan]{res['id']}[/cyan] -> [bold]{res['promoted_to']}[/bold] "
        f"([dim]{res['relative_path']}[/dim])"
    )
