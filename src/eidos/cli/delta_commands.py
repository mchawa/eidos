"""SpecDelta lifecycle commands (`eidos delta ...`) and the `changelog` shortcut."""

import json
from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import PANEL_SPECS, console
from eidos.core.lifecycle.audit import AuditEngine
from eidos.core.lifecycle.changelog import ChangelogGenerator
from eidos.core.lifecycle.delta_engine import (
    apply_delta,
    get_delta_status_summary,
    list_deltas,
    sync_delta_views,
    verify_delta_mutations,
)
from eidos.core.lifecycle.scaffolder import create_spec, delete_spec

delta_app = typer.Typer(
    name="delta",
    help="Manage SpecDeltas through their mutation lifecycle (list, create, apply, delete).",
    add_completion=False,
)


@delta_app.command("list")
def delta_list_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter deltas by status (e.g. proposed, in_review, applied, rejected).",
    ),
):
    """List SpecDeltas in active/ and applied/ states."""
    deltas = list_deltas(path, status_filter=status_filter)
    if not deltas:
        console.print(f"[yellow]No SpecDeltas found under '{path}/deltas'.[/yellow]")
        return

    table = Table(title=f"Eidos SpecDeltas ({len(deltas)} found)", show_header=True)
    table.add_column("Delta ID", style="bold cyan")
    table.add_column("Status", style="green")
    table.add_column("Kind", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("State", style="yellow")
    table.add_column("Path", style="dim")

    for d in deltas:
        state_str = "[bold green]ACTIVE[/bold green]" if d["is_active"] else "[dim]APPLIED[/dim]"
        table.add_row(
            d["id"],
            str(d["status"]),
            d["kind"],
            d["title"],
            state_str,
            str(d["relative_path"]),
        )
    console.print(table)


@delta_app.command("create")
def delta_create_cmd(
    delta_id: str = typer.Argument(
        ...,
        help="Unique delta identifier (e.g. DLT-2026-002).",
    ),
    title: str = typer.Option(
        ...,
        "--title",
        "-t",
        help="Title of the proposed change.",
    ),
    kind: str = typer.Option(
        "feature_addition",
        "--kind",
        "-k",
        help="Mutation kind: feature_addition, bugfix, refactor, modification, feature_removal.",
    ),
    motivation: str = typer.Option(
        "Problem statement and motivation.",
        "--motivation",
        "-m",
        help="Problem statement and motivation.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Create a new SpecDelta in flat storage (eidos/deltas/<id>.adoc) and sync views."""
    try:
        dest_file = create_spec(
            eidos_dir=path,
            doc_type_key="delta",
            raw_id=delta_id,
            title=title,
            kind=kind,
            motivation=motivation,
        )
        rel = dest_file.relative_to(path) if dest_file.is_relative_to(path) else dest_file
        console.print(f"[bold green]✓ Created SpecDelta:[/bold green] [bold]{dest_file.name}[/bold]")
        console.print(f"  [dim]Location: {rel}[/dim]")
        console.print("  [dim]Status: proposed (symlinked in eidos/deltas/by-status/active/ and proposed/)[/dim]")
        console.print(f"  [dim]Next step: Edit mutations in {rel} and update living specs.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to create SpecDelta:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@delta_app.command("verify")
def delta_verify_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename of the SpecDelta to verify.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON verification result.",
    ),
):
    """Verify that all semantic mutations declared in a SpecDelta exist in the living specifications."""
    is_verified, msg, report = verify_delta_mutations(path, target)
    if json_output:
        if report:
            typer.echo(json.dumps(report.to_dict(), indent=2))
        else:
            typer.echo(json.dumps({"error": msg, "is_verified": False}))
        if not is_verified:
            raise typer.Exit(code=1)
        return

    if not report:
        console.print(f"[bold red]✗ Verification Failed:[/bold red] {msg}")
        raise typer.Exit(code=1)

    table = Table(
        title=f"SpecDelta Mutation Verification: {report.delta_id}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Action", style="cyan")
    table.add_column("Target", style="yellow")
    table.add_column("Status", justify="center")
    table.add_column("Verified", justify="center")
    table.add_column("Details", style="dim")

    for m in report.mutations:
        status_label = f"[bold green]{m.status}[/bold green]" if m.verified else f"[bold red]{m.status}[/bold red]"
        ver_label = "[bold green]PASS[/bold green]" if m.verified else "[bold red]FAIL[/bold red]"
        table.add_row(
            m.action,
            m.target,
            status_label,
            ver_label,
            m.details[:50] + ("..." if len(m.details) > 50 else ""),
        )

    console.print(table)
    if not is_verified:
        console.print(f"\n[bold red]✗ Verification Incomplete:[/bold red] {msg}")
        raise typer.Exit(code=1)

    console.print(f"\n[bold green]✓ Verification Passed:[/bold green] {msg}")


@delta_app.command("audit")
def delta_audit_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename of the SpecDelta to audit.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        "-s",
        help="Exit with code 1 if any unacknowledged active leaks or graph violations exist.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON audit result.",
    ),
):
    """Audit entities removed or deprecated by a SpecDelta for dangling references and active leaks."""
    engine = AuditEngine(eidos_dir=path)
    result = engine.audit_delta(target)

    if json_output:
        typer.echo(result.to_json(indent=2))
        if strict and not result.is_clean:
            raise typer.Exit(code=1)
        if result.status == "ERROR":
            raise typer.Exit(code=1)
        return

    if result.status == "ERROR":
        console.print(f"[bold red]✗ Audit Error:[/bold red] {result.error_message}")
        raise typer.Exit(code=1)

    targets_display = ", ".join(result.targets_audited) if result.targets_audited else "None"
    header_text = f"[bold]Target Entities Audited:[/bold] {targets_display}"
    if result.search_tokens:
        header_text += f"\n[dim]Additional Search Tokens: {', '.join(result.search_tokens)}[/dim]"

    console.print(
        Panel(
            header_text,
            title=f"[bold blue]Eidos SpecDelta Deletion Audit: {result.delta_id}[/bold blue]",
            expand=False,
        )
    )

    if result.status == "NO_DELETIONS":
        console.print("\n[dim]No deletion or deprecation mutations found in this SpecDelta. Nothing to audit.[/dim]")
        return

    # Graph violations table
    if result.graph_violations:
        console.print(f"\n[bold red]Graph-Level Foreign Key Violations ({len(result.graph_violations)}):[/bold red]")
        g_table = Table(show_header=True, header_style="bold red")
        g_table.add_column("Source ID", style="cyan")
        g_table.add_column("Relationship", style="magenta")
        g_table.add_column("Target ID", style="yellow")
        g_table.add_column("Source Spec Path", style="dim")
        for g in result.graph_violations:
            g_table.add_row(g.source_id, g.rel_type, g.target_id, g.source_file)
        console.print(g_table)

    # Unacknowledged active leaks table
    if result.unacknowledged_findings:
        console.print(
            f"\n[bold red]Unacknowledged Active Leaks ({len(result.unacknowledged_findings)}):[/bold red]"
        )
        u_table = Table(show_header=True, header_style="bold red")
        u_table.add_column("Location", style="cyan")
        u_table.add_column("Term", style="yellow")
        u_table.add_column("Classification", style="magenta")
        u_table.add_column("Code Snippet", style="dim")
        u_table.add_column("Risk", justify="center")

        for u in result.unacknowledged_findings:
            risk_style = (
                "[bold red]CRITICAL[/bold red]"
                if u.risk_level == "critical"
                else ("[bold yellow]HIGH[/bold yellow]" if u.risk_level == "high" else "[dim]MEDIUM[/dim]")
            )
            u_table.add_row(
                f"{u.file_path}:{u.line_number}",
                u.matched_term,
                u.classification,
                u.line_snippet[:55],
                risk_style,
            )
        console.print(u_table)

    # Approved / Exempt references breakdown
    if result.approved_findings:
        console.print(f"\n[bold green]✓ Approved References ({len(result.approved_findings)}):[/bold green]")
        breakdown_parts = []
        if result.summary.get("historical_ledgers", 0) > 0:
            breakdown_parts.append(f"{result.summary['historical_ledgers']} historical deltas")
        if result.summary.get("negative_tests", 0) > 0:
            breakdown_parts.append(f"{result.summary['negative_tests']} negative regression tests")
        if result.summary.get("cleanup_routines", 0) > 0:
            breakdown_parts.append(f"{result.summary['cleanup_routines']} cleanup/uninstaller routines")
        if result.summary.get("tombstone_exemptions", 0) > 0:
            breakdown_parts.append(f"{result.summary['tombstone_exemptions']} declared tombstones")
        if result.summary.get("documentation_rationales", 0) > 0:
            breakdown_parts.append(f"{result.summary['documentation_rationales']} documentation rationales")
        if breakdown_parts:
            console.print(f"  [dim]• Classified as: {', '.join(breakdown_parts)}[/dim]")

    if result.is_clean:
        console.print(
            "\n[bold green]✓ Audit Passed:[/bold green] Zero unacknowledged active leaks or graph violations detected."
        )
    else:
        console.print(
            f"\n[bold red]✗ Audit Found Leaks:[/bold red] Detected {len(result.unacknowledged_findings)} unacknowledged references and {len(result.graph_violations)} graph violations."
        )
        console.print(
            "  [dim]Action: Clean up the files listed above or declare intentional tombstones in the SpecDelta.[/dim]"
        )
        if strict:
            raise typer.Exit(code=1)


@delta_app.command("apply")
def delta_apply_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename of the SpecDelta to apply.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        "-e",
        help="Deterministically scaffold missing specs and apply structural mutations declared in the delta.",
    ),
    verify: bool = typer.Option(
        False,
        "--verify",
        "-v",
        help="Verify all mutations are satisfied in the living spec catalog before applying.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON application result.",
    ),
):
    """Reconcile a SpecDelta in-place, updating status to 'applied' and syncing symlink views."""
    success, msg, applied_file, mutated_files = apply_delta(
        path, target, execute=execute, verify=verify
    )
    if json_output:
        payload = {
            "success": success,
            "message": msg,
            "target": target,
            "applied_file": str(applied_file) if applied_file else None,
            "executed": execute,
            "mutated_files": [str(p) for p in mutated_files],
        }
        typer.echo(json.dumps(payload, indent=2))
        if not success:
            raise typer.Exit(code=1)
        return

    if not success:
        console.print(f"[bold red]✗ Failed to apply SpecDelta:[/bold red] {msg}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓[/bold green] {msg}")
    if execute and mutated_files:
        console.print(f"[bold cyan]Scaffolded/Mutated {len(mutated_files)} specification(s):[/bold cyan]")
        for mf in mutated_files:
            rel = mf.relative_to(path.parent) if mf.is_relative_to(path.parent) else mf
            console.print(f"  • [green]{rel}[/green]")
    if applied_file:
        rel = applied_file.relative_to(path) if applied_file.is_relative_to(path) else applied_file
        console.print(f"  [dim]Updated in-place: {rel}[/dim]")
        console.print(f"  [dim]Symlink moved to: eidos/deltas/by-status/applied/{applied_file.name}[/dim]")


@delta_app.command("status")
def delta_status_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Display a status summary of all SpecDeltas and their lifecycle states."""
    summary = get_delta_status_summary(path)
    total = summary["total"]
    active_count = summary["active_count"]
    by_status = summary["by_status"]
    views_dir = summary["views_dir"]

    console.print(f"[bold cyan]SpecDelta Lifecycle Status Dashboard[/bold cyan] ({total} total)\n")

    summary_table = Table(title="SpecDelta Lifecycle Summary", show_header=True)
    summary_table.add_column("Lifecycle State", style="bold cyan")
    summary_table.add_column("Count", style="bold white", justify="right")
    summary_table.add_column("Description", style="dim")

    summary_table.add_row("Proposed", str(len(by_status.get("proposed", []))), "Draft mutations awaiting review")
    summary_table.add_row("In Review", str(len(by_status.get("in_review", []))), "Under review by team or agents")
    summary_table.add_row(
        "Approved", str(len(by_status.get("approved", []))), "Approved, awaiting implementation/merge"
    )
    summary_table.add_row("Applied", str(len(by_status.get("applied", []))), "Reconciled into living specifications")
    summary_table.add_row("Rejected", str(len(by_status.get("rejected", []))), "Abandoned or rejected proposals")
    summary_table.add_row(
        "In-Flight (Active)", f"[bold yellow]{active_count}[/bold yellow]", "Total active/unmerged deltas"
    )

    console.print(summary_table)

    active_deltas = by_status.get("proposed", []) + by_status.get("in_review", []) + by_status.get("approved", [])
    if active_deltas:
        console.print("\n[bold yellow]In-Flight SpecDeltas:[/bold yellow]")
        active_table = Table(show_header=True)
        active_table.add_column("Delta ID", style="bold cyan")
        active_table.add_column("Status", style="yellow")
        active_table.add_column("Kind", style="dim")
        active_table.add_column("Title", style="white")
        for d in active_deltas:
            active_table.add_row(d["id"], str(d["status"]), str(d["kind"]), d["title"])
        console.print(active_table)
    else:
        console.print("\n[dim]No in-flight SpecDeltas. Living specifications are up to date.[/dim]")

    rel_views = views_dir.relative_to(path.parent) if views_dir.is_relative_to(path.parent) else views_dir
    console.print(f"\n[dim]Gitignored symlink views: {rel_views} (use 'eidos delta sync-views' to refresh)[/dim]")


@delta_app.command("sync-views")
def delta_sync_views_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Regenerate gitignored symlinks in eidos/deltas/by-status/ categorized by lifecycle state."""
    synced = sync_delta_views(path)
    total_symlinks = sum(len(v) for v in synced.values())
    views_dir = path / "deltas" / "by-status"
    rel_views = views_dir.relative_to(path.parent) if views_dir.is_relative_to(path.parent) else views_dir
    console.print(f"[bold green]✓ Synchronized {total_symlinks} symlinks in {rel_views}[/bold green]")
    for cat, items in synced.items():
        if items:
            console.print(f"  • [bold cyan]{cat}[/bold cyan]: {len(items)} deltas")


@delta_app.command("delete")
def delta_delete_cmd(
    target: str = typer.Argument(
        ...,
        help="Delta ID, path, or filename to delete.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force deletion.",
    ),
):
    """Delete a SpecDelta file."""
    success, msg, _ = delete_spec(path, target, force=force)
    if not success:
        console.print(f"[bold red]✗ Failed to delete SpecDelta:[/bold red] {msg}")
        raise typer.Exit(code=1)
    console.print(f"[bold green]✓[/bold green] {msg}")


@delta_app.command("changelog")
def delta_changelog_cmd(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Target changelog file path (defaults to ./CHANGELOG.md).",
    ),
    unreleased: bool = typer.Option(
        True,
        "--unreleased/--no-unreleased",
        help="Include unreleased changes section.",
    ),
    docs: bool = typer.Option(
        True,
        "--docs/--no-docs",
        help="Automatically synchronize docs/changelog.md and register in zensical.toml.",
    ),
    stdout: bool = typer.Option(
        False,
        "--stdout",
        help="Print changelog directly to stdout instead of writing to disk.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Generate an auditable, SpecDelta-backed changelog partitioned by Git release tags."""
    eidos_dir = path or Path("eidos")
    repo_root = eidos_dir.resolve().parent if eidos_dir.name == "eidos" else Path.cwd()
    gen = ChangelogGenerator(repo_root=repo_root, eidos_dir=eidos_dir)

    if stdout:
        typer.echo(gen.generate_changelog(include_unreleased=unreleased))
        return

    res = gen.update_changelog_files(
        output_file=output,
        update_docs=docs,
        include_unreleased=unreleased,
    )

    console.print(f"[bold green]✓ Changelog generated:[/bold green] {res['output_file']} ({res['bytes_written']} bytes)")
    if res.get("docs_file"):
        link_mode = "symlinked" if res.get("symlink_created") else "mirrored"
        console.print(f"  [dim]Documentation {link_mode}: {res['docs_file']}[/dim]")


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("changelog", rich_help_panel=PANEL_SPECS)(delta_changelog_cmd)
