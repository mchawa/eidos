"""SpecDelta lifecycle commands (`eidos delta ...`)."""

from pathlib import Path

import orjson
import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import console
from eidos.core.lifecycle.audit import AuditEngine
from eidos.core.lifecycle.catalog import find_spec_by_id_or_path
from eidos.core.lifecycle.changelog import ChangelogGenerator
from eidos.core.lifecycle.delta_engine import (
    apply_delta,
    create_delta,
    delete_delta,
    get_delta_status_summary,
    list_deltas,
    set_delta_status,
    verify_delta_mutations,
)
from eidos.core.spec.parser import parse_spec_file

delta_app = typer.Typer(
    name="delta",
    help="Manage SpecDeltas through their mutation lifecycle (list, create, status, verify, audit, apply, delete, changelog).",
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
        help="Filter deltas by status (proposed, in_review, approved, applied, rejected).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON delta list.",
    ),
):
    """List SpecDeltas in active/ and applied/ states."""
    deltas = list_deltas(path, status_filter=status_filter)
    if json_output:
        serializable = [
            {
                "id": d["id"],
                "title": d["title"],
                "status": d["status"],
                "kind": d["kind"],
                "is_active": d["is_active"],
                "path": str(d["path"]),
                "relative_path": str(d["relative_path"]),
            }
            for d in deltas
        ]
        typer.echo(
            orjson.dumps({"count": len(serializable), "deltas": serializable}, option=orjson.OPT_INDENT_2).decode(
                "utf-8"
            )
        )
        return

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
        "auto",
        help="Unique delta identifier (e.g. DLT-2026-002), or 'auto' (default) for automatic collision-free numbering.",
    ),
    title: str = typer.Option(
        ...,
        "--title",
        "-t",
        help="Title of the proposed change.",
    ),
    kind: str = typer.Option(
        "feature",
        "--kind",
        "-k",
        help="Mutation kind: feature, enabler, bug.",
    ),
    slug: bool = typer.Option(
        False,
        "--slug",
        "-s",
        help="Append a deterministic title-derived slug suffix to prevent branch collisions.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos directory.",
    ),
):
    """Create a new SpecDelta blueprint in flat storage (eidos/deltas/<id>.adoc)."""
    try:
        dest_file = create_delta(
            eidos_dir=path,
            raw_id=delta_id,
            title=title,
            kind=kind,
            slug_suffix=slug,
        )
        rel = dest_file.relative_to(path) if dest_file.is_relative_to(path) else dest_file
        console.print(f"[bold green]✓ Created SpecDelta:[/bold green] [bold]{dest_file.name}[/bold]")
        console.print(f"  [dim]Location: {rel}[/dim]")
        console.print("  [dim]Status: proposed[/dim]")
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
            typer.echo(orjson.dumps(report.to_dict(), option=orjson.OPT_INDENT_2).decode("utf-8"))
        else:
            typer.echo(orjson.dumps({"error": msg, "is_verified": False}).decode("utf-8"))
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
        detail_label = (
            f"[red]{m.failure_reason}[/red]"
            if (not m.verified and m.failure_reason)
            else (m.details[:50] + ("..." if len(m.details) > 50 else ""))
        )
        table.add_row(
            m.action,
            m.target,
            status_label,
            ver_label,
            detail_label,
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
        console.print(f"\n[bold red]Unacknowledged Active Leaks ({len(result.unacknowledged_findings)}):[/bold red]")
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
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        "-v",
        help="Verify all mutations are satisfied in the living spec catalog before applying (enabled by default).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON application result.",
    ),
):
    """Reconcile a SpecDelta in-place, updating status to 'applied'."""
    success, msg, applied_file = apply_delta(path, target, verify=verify)
    if json_output:
        payload = {
            "success": success,
            "message": msg,
            "target": target,
            "applied_file": str(applied_file) if applied_file else None,
        }
        typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode("utf-8"))
        if not success:
            raise typer.Exit(code=1)
        return

    if not success:
        console.print(f"[bold red]✗ Failed to apply SpecDelta:[/bold red] {msg}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓[/bold green] {msg}")
    if applied_file:
        rel = applied_file.relative_to(path) if applied_file.is_relative_to(path) else applied_file
        console.print(f"  [dim]Updated in-place: {rel}[/dim]")


@delta_app.command("status")
def delta_status_cmd(
    delta_id: str | None = typer.Argument(
        None,
        help="Optional Delta ID or path to inspect or transition status (e.g. DLT-2026-015).",
    ),
    new_status: str | None = typer.Argument(
        None,
        help="Optional target lifecycle status when delta_id is given (proposed, in_review, approved, applied, rejected).",
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
        help="Output machine-readable JSON status summary or transition result.",
    ),
):
    """Display a status summary of all SpecDeltas or transition a specific SpecDelta's lifecycle status."""
    if delta_id and new_status:
        ok, msg, file_path, old_status = set_delta_status(path, delta_id, new_status)
        if json_output:
            payload = {
                "success": ok,
                "message": msg,
                "delta_id": delta_id,
                "previous_status": old_status,
                "status": new_status.strip().lower(),
                "path": str(file_path) if file_path else None,
            }
            typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode("utf-8"))
            if not ok:
                raise typer.Exit(code=1)
            return
        if not ok:
            console.print(f"[bold red]✗ Failed to update SpecDelta status:[/bold red] {msg}")
            raise typer.Exit(code=1)
        console.print(f"[bold green]✓[/bold green] {msg}")
        return

    if delta_id:
        file_path = find_spec_by_id_or_path(path / "deltas", delta_id) or find_spec_by_id_or_path(path, delta_id)
        if not file_path or not file_path.exists():
            if json_output:
                typer.echo(
                    orjson.dumps({"error": f"SpecDelta '{delta_id}' not found."}, option=orjson.OPT_INDENT_2).decode(
                        "utf-8"
                    )
                )
            else:
                console.print(f"[bold red]Error:[/bold red] SpecDelta '{delta_id}' not found.")
            raise typer.Exit(code=1)
        doc = parse_spec_file(file_path)
        current_status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
        if json_output:
            typer.echo(
                orjson.dumps(
                    {
                        "id": doc.doc_id,
                        "title": doc.title or "Untitled",
                        "status": current_status,
                        "path": str(file_path),
                    },
                    option=orjson.OPT_INDENT_2,
                ).decode("utf-8")
            )
            return
        console.print(f"[bold cyan]{doc.doc_id}[/bold cyan]: [bold yellow]{current_status}[/bold yellow] — {doc.title}")
        return

    summary = get_delta_status_summary(path)
    total = summary["total"]
    active_count = summary["active_count"]
    by_status = summary["by_status"]

    if json_output:
        serializable_by_status = {
            st: [
                {
                    "id": d["id"],
                    "title": d["title"],
                    "status": d["status"],
                    "kind": d["kind"],
                    "is_active": d["is_active"],
                    "path": str(d["path"]),
                    "relative_path": str(d["relative_path"]),
                }
                for d in items
            ]
            for st, items in by_status.items()
        }
        typer.echo(
            orjson.dumps(
                {
                    "total": total,
                    "active_count": active_count,
                    "by_status": serializable_by_status,
                },
                option=orjson.OPT_INDENT_2,
            ).decode("utf-8")
        )
        return

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
    success, msg, _ = delete_delta(path, target, force=force)
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
    from eidos.core.workspace.project import find_repo_root

    eidos_dir = path or Path("eidos")
    resolved_eidos = eidos_dir.resolve()
    repo_root = resolved_eidos.parent if resolved_eidos.name == "eidos" else find_repo_root(resolved_eidos)
    gen = ChangelogGenerator(repo_root=repo_root, eidos_dir=eidos_dir)

    if stdout:
        typer.echo(gen.generate_changelog(include_unreleased=unreleased))
        return

    res = gen.update_changelog_files(
        output_file=output,
        update_docs=docs,
        include_unreleased=unreleased,
    )

    console.print(
        f"[bold green]✓ Changelog generated:[/bold green] {res['output_file']} ({res['bytes_written']} bytes)"
    )
    if res.get("docs_file"):
        link_mode = "symlinked" if res.get("symlink_created") else "mirrored"
        console.print(f"  [dim]Documentation {link_mode}: {res['docs_file']}[/dim]")
