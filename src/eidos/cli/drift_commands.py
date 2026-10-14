"""Spec-to-code drift detection and living specification reconciliation commands."""

from pathlib import Path

import typer
from rich.table import Table

from eidos.cli._shared import PANEL_GOVERNANCE, console
from eidos.vcs import detect_drift, reconcile_specs


def drift_cmd(
    base: str | None = typer.Option(
        None,
        "--base",
        "-b",
        help="Base git reference to compare against (e.g. origin/main, main).",
    ),
    head: str = typer.Option(
        "HEAD",
        "--head",
        help="Head git reference to compare (defaults to HEAD).",
    ),
    uncommitted: bool = typer.Option(
        False,
        "--uncommitted",
        "-u",
        help="Analyze uncommitted working tree and staged changes instead of branch diff.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos specifications directory (defaults to ./eidos).",
    ),
    warn_only: bool = typer.Option(
        False,
        "--warn-only",
        help="Do not exit with code 1 on drift or validation failures.",
    ),
    markdown_out: Path | None = typer.Option(
        None,
        "--markdown",
        "-m",
        help="Write markdown summary to file (e.g. for CI/CD reports).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON verification result.",
    ),
):
    """Detect spec-to-code drift, schema validity, and invariant test coverage."""
    eidos_dir = path or Path("eidos")
    repo_root = Path.cwd()

    result = detect_drift(
        repo_root=repo_root,
        base_ref=base,
        head_ref=head,
        eidos_dir=eidos_dir,
        uncommitted=uncommitted,
    )

    if json_output:
        data = {
            "is_clean": result.is_clean,
            "drift_detected": result.drift_detected,
            "workflow_mode": result.workflow_mode,
            "code_files": result.classification.code_files,
            "spec_files": result.classification.spec_files,
            "delta_files": result.classification.delta_files,
            "test_files": result.classification.test_files,
            "touched_components": result.touched_components,
            "valid_specs": result.validation_report.valid_docs,
            "total_specs": result.validation_report.total_docs,
            "remediation_suggestions": result.remediation_suggestions,
            "trivially_covered_files": result.trivially_covered_files,
        }
        console.print_json(data=data)
    else:
        # Rich Terminal Output
        mode_badge = (
            "[bold cyan]Planned Mode (SpecDelta)[/bold cyan]"
            if result.workflow_mode == "planned"
            else "[bold green]Direct Mode (Co-Evolution)[/bold green]"
        )
        console.print(f"[bold cyan]Eidos Spec-to-Code Drift Detection[/bold cyan] ({mode_badge})\n")

        table = Table(title="Spec-Code Synchronization Summary", show_header=True)
        table.add_column("Metric", style="bold cyan")
        table.add_column("Count", justify="right")
        table.add_column("Status", justify="center")

        code_count = len(result.classification.code_files)
        spec_count = len(result.classification.spec_files)
        delta_count = len(result.classification.delta_files)

        code_status = (
            "[bold yellow]Needs Spec[/bold yellow]" if result.drift_detected else "[bold green]Synced[/bold green]"
        )
        spec_status = "[bold green]Updated[/bold green]" if spec_count else "[dim]Unchanged[/dim]"
        delta_status = "[bold cyan]Active[/bold cyan]" if delta_count else "[dim]None (Direct)[/dim]"

        table.add_row("Modified Code Files", str(code_count), code_status)
        table.add_row("Modified Living Specs", str(spec_count), spec_status)
        table.add_row("Modified SpecDeltas", str(delta_count), delta_status)
        table.add_row(
            "Modified Test Files",
            str(len(result.classification.test_files)),
            "[bold green]✓[/bold green]" if result.classification.test_files else "[dim]—[/dim]",
        )

        val_status = (
            "[bold green]100% Pass[/bold green]" if result.validation_report.is_clean else "[bold red]FAIL[/bold red]"
        )
        table.add_row(
            "Living Specs Validated",
            f"{result.validation_report.valid_docs}/{result.validation_report.total_docs}",
            val_status,
        )

        console.print(table)

        if result.touched_components:
            console.print(f"\n[bold blue]Impacted Components:[/bold blue] {', '.join(result.touched_components)}")

        if result.drift_detected:
            console.print("\n[bold yellow]⚠️  SPEC-TO-CODE DRIFT DETECTED:[/bold yellow]")
            for rem in result.remediation_suggestions:
                console.print(f"  [yellow]•[/yellow] {rem}")
            console.print(
                "\n[dim]Tip: Run [bold green]eidos reconcile[/bold green] to get suggestions for updating living specifications.[/dim]"
            )

        if not result.validation_report.is_clean:
            console.print("\n[bold red]✗ Specification Schema Errors:[/bold red]")
            for p, errs in result.validation_report.schema_errors.items():
                console.print(f"  [red]• {p}:[/red] {'; '.join(errs)}")

        if result.is_clean:
            console.print(
                "\n[bold green]✓ Verification Passed![/bold green] Living specifications and code are synchronized."
            )

    if markdown_out:
        markdown_out.write_text(result.summary_markdown, encoding="utf-8")
        console.print(f"\n[dim]Saved markdown summary to: {markdown_out}[/dim]")

    if not result.is_clean and not warn_only:
        raise typer.Exit(code=1)


def reconcile_cmd(
    base: str | None = typer.Option(
        None,
        "--base",
        "-b",
        help="Base git reference to compare against.",
    ),
    uncommitted: bool = typer.Option(
        False,
        "--uncommitted",
        "-u",
        help="Analyze uncommitted working tree changes.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
    prompt: bool = typer.Option(
        False,
        "--prompt",
        help="Print structured AI prompt for copy-pasting to an AI assistant.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON reconcile analysis.",
    ),
):
    """Analyze code changes and suggest living spec updates to eliminate drift."""
    eidos_dir = path or Path("eidos")
    repo_root = Path.cwd()

    result = reconcile_specs(
        repo_root=repo_root,
        base_ref=base,
        eidos_dir=eidos_dir,
        uncommitted=uncommitted,
    )

    if json_output:
        data = {
            "changed_code_files": result.changed_code_files,
            "suggestions": [
                {
                    "code_file": s.code_file,
                    "candidate_specs": s.candidate_specs,
                    "reason": s.reason,
                }
                for s in result.suggestions
            ],
            "catalog": result.catalog,
            "ai_prompt": result.ai_prompt,
        }
        console.print_json(data=data)
        return

    if prompt:
        console.print(result.ai_prompt)
        return

    console.print("[bold cyan]Eidos Living Specification Reconciliation[/bold cyan]\n")

    if not result.changed_code_files:
        console.print("[bold green]✓ No code drift detected.[/bold green] Working tree and living specs are in sync.")
        return

    table = Table(
        title=f"Code Modifications & Candidate Specs ({len(result.changed_code_files)} files)", show_header=True
    )
    table.add_column("Modified Code File", style="bold cyan")
    table.add_column("Candidate Living Specs", style="white")

    for s in result.suggestions:
        table.add_row(s.code_file, "\n".join(s.candidate_specs))

    console.print(table)
    console.print(
        "\n[dim]Next step:[/dim] Update the candidate living specs in [cyan]eidos/[/cyan] to reflect these changes."
    )
    console.print(
        "[dim]Use [bold green]eidos reconcile --prompt[/bold green] to generate a structured AI prompt for your coding agent.[/dim]\n"
    )


def sync_shortcut(
    base: str | None = typer.Option(None, "--base", "-b", help="Base git reference to compare against."),
    uncommitted: bool = typer.Option(False, "--uncommitted", "-u", help="Analyze uncommitted working tree changes."),
    path: Path | None = typer.Option(None, "--path", "-p", help="Path to eidos directory."),
    prompt: bool = typer.Option(False, "--prompt", help="Print structured AI prompt."),
):
    """Shortcut alias for 'eidos reconcile'."""
    reconcile_cmd(base=base, uncommitted=uncommitted, path=path, prompt=prompt)


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("drift", rich_help_panel=PANEL_GOVERNANCE)(drift_cmd)
    app.command("reconcile", rich_help_panel=PANEL_GOVERNANCE)(reconcile_cmd)
    app.command("sync", rich_help_panel=PANEL_GOVERNANCE)(sync_shortcut)
