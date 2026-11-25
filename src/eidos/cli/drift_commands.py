"""Spec-to-code drift detection command (`eidos drift`)."""

from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from eidos.cli._shared import PANEL_GOVERNANCE, console, resolve_cli_eidos_dir, resolve_param_from_config
from eidos.core.workspace.config import load_repo_config_with_errors
from eidos.core.workspace.project import find_git_root, find_repo_root, resolve_spec_and_repo_roots
from eidos.vcs import GitResolutionError, detect_drift
from eidos.vcs.snapshot import _load_head_commit_config


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
        "--warn-only/--no-warn-only",
        help="Do not exit with code 1 on drift or validation failures.",
    ),
    fail_on_warnings: bool = typer.Option(
        False,
        "--fail-on-warnings/--no-fail-on-warnings",
        help="Exit with code 1 when advisory validation warnings or ready-to-apply open SpecDeltas are present (unless --warn-only is set).",
    ),
    strict: bool = typer.Option(
        False,
        "--strict/--no-strict",
        help="Treat body-only code changes as drift unless accompanied by a 1-hop spec, active SpecDelta, or test update.",
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
    eidos_dir = resolve_cli_eidos_dir(path)
    resolved_eidos = eidos_dir.resolve()
    if resolved_eidos.name == "eidos":
        repo_root = resolved_eidos.parent
    elif resolved_eidos.exists():
        _, repo_root = resolve_spec_and_repo_roots(resolved_eidos)
    else:
        repo_root = find_repo_root(resolved_eidos.parent if resolved_eidos.parent.exists() else None)

    git_root = find_git_root(repo_root)
    eval_cfg_errors: list[str] = []
    if uncommitted or git_root is None:
        eval_cfg, eval_cfg_errors = load_repo_config_with_errors(eidos_dir=resolved_eidos, repo_root=repo_root)
    else:
        try:
            eidos_rel_prefix = str(resolved_eidos.relative_to(repo_root.resolve())).replace("\\", "/").strip("/") or "eidos"
        except ValueError:
            eidos_rel_prefix = resolved_eidos.name or "eidos"
        eval_cfg = _load_head_commit_config(git_root, repo_root.resolve(), eidos_rel_prefix, head)

    cli_strict = resolve_param_from_config("strict", strict, None)
    cfg_warn_only = False if eval_cfg_errors else eval_cfg.drift.warn_only
    eff_warn_only = bool(resolve_param_from_config("warn_only", warn_only, cfg_warn_only))
    eff_fail_on_warnings = bool(
        resolve_param_from_config("fail_on_warnings", fail_on_warnings, eval_cfg.drift.fail_on_warnings)
    )

    try:
        result = detect_drift(
            repo_root=repo_root,
            base_ref=base,
            head_ref=head,
            eidos_dir=eidos_dir,
            uncommitted=uncommitted,
            strict=cli_strict,
        )
    except GitResolutionError as err:
        if json_output:
            console.print_json(data={"error": str(err), "is_clean": False})
        else:
            console.print(f"[bold red]Git Resolution Error:[/bold red] {err}")
        if not eff_warn_only:
            raise typer.Exit(code=1) from err
        return

    if json_output:
        data = {
            "is_clean": result.is_clean,
            "drift_detected": result.drift_detected,
            "workflow_mode": result.workflow_mode,
            "code_files": result.classification.code_files,
            "structural_code_files": result.structural_code_files,
            "body_only_code_files": result.body_only_code_files,
            "spec_files": result.classification.spec_files,
            "delta_files": result.classification.delta_files,
            "test_files": result.classification.test_files,
            "touched_components": result.touched_components,
            "unmapped_code_files": result.unmapped_code_files,
            "valid_specs": result.validation_report.valid_docs,
            "total_specs": result.validation_report.total_docs,
            "validation_errors": result.validation_errors,
            "validation_warnings": result.validation_warnings,
            "uncovered_invariants": [inv.invariant_id for inv in result.uncovered_invariants],
            "orphan_invariants": [{"invariant_id": oid, "location": loc} for oid, loc in result.orphan_invariants],
            "remediation_suggestions": result.remediation_suggestions,
            "trivially_covered_files": result.trivially_covered_files,
            "boundary_violations": result.boundary_violations,
            "ratchet_shrink_candidates": result.ratchet_shrink_candidates,
            "ready_to_apply_deltas": result.ready_to_apply_deltas,
            "untouched_consumers": result.untouched_consumers,
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
        struct_count = len(result.structural_code_files)
        body_count = len(result.body_only_code_files)
        spec_count = len(result.classification.spec_files)
        delta_count = len(result.classification.delta_files)

        code_status = (
            "[bold yellow]Needs Spec[/bold yellow]"
            if (result.drift_detected and code_count > 0)
            else "[bold green]Synced[/bold green]"
        )
        spec_status = "[bold green]Updated[/bold green]" if spec_count else "[dim]Unchanged[/dim]"
        delta_status = "[bold cyan]Active[/bold cyan]" if delta_count else "[dim]None (Direct)[/dim]"

        code_count_str = f"{code_count} ({struct_count} structural, {body_count} body-only)" if code_count > 0 else "0"
        table.add_row("Modified Code Files", code_count_str, code_status)
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

        has_non_code_drift = bool(result.uncovered_invariants or result.orphan_invariants)
        if result.drift_detected or has_non_code_drift:
            if result.drift_detected and code_count > 0:
                console.print("\n[bold yellow]⚠️  SPEC-TO-CODE DRIFT DETECTED:[/bold yellow]")
            elif result.drift_detected:
                console.print("\n[bold yellow]⚠️  NON-CODE SPECIFICATION DRIFT DETECTED:[/bold yellow]")
            else:
                console.print("\n[bold red]✗ INVARIANT TRACEABILITY DRIFT DETECTED:[/bold red]")
            for rem in result.remediation_suggestions:
                console.print(f"  [yellow]•[/yellow] {rem}")
            console.print(
                "\n[dim]Tip: Update the impacted living specifications in eidos/ or create a SpecDelta via [bold green]eidos delta create[/bold green].[/dim]"
            )

        if not result.validation_report.is_clean:
            if result.validation_report.schema_errors:
                console.print("\n[bold red]✗ Specification Schema Errors:[/bold red]")
                for p, errs in result.validation_report.schema_errors.items():
                    console.print(f"  [red]• {p}:[/red] {'; '.join(errs)}")
            if result.validation_report.broken_references:
                console.print("\n[bold red]✗ Broken Cross-References:[/bold red]")
                for ref in result.validation_report.broken_references:
                    console.print(f"  [red]•[/red] {ref}")

        if result.boundary_violations:
            console.print("\n[bold red]✗ Architectural Import Boundary Violations:[/bold red]")
            for bv in result.boundary_violations:
                ev_str = f" [{'; '.join(bv.get('evidence', [])[:3])}]" if bv.get("evidence") else ""
                console.print(
                    f"  [red]• {bv['source']} -> {bv['target']}[/red] ({bv['rule']}): "
                    f"observed {bv['observed_imports']} coupling(s), max allowed {bv['max_imports']}{ev_str}"
                )

        if result.ratchet_shrink_candidates:
            console.print("\n[bold cyan]→ Ratchet Tightening Opportunities (Advisory):[/bold cyan]")
            for sc in result.ratchet_shrink_candidates:
                console.print(
                    f"  [cyan]• {sc['source']} -> {sc['target']}[/cyan] ({sc.get('spec_path', '')}): "
                    f"observed {sc['observed_imports']} < max_imports {sc['max_imports']} ({sc['action']})"
                )

        if result.validation_warnings:
            console.print("\n[bold yellow]⚠ Advisory Validation Warnings:[/bold yellow]")
            for warn in result.validation_warnings:
                console.print(f"  [yellow]•[/yellow] {warn}")

        if result.ready_to_apply_deltas:
            console.print("\n[bold yellow]⚠ Open SpecDeltas Ready to Apply (Advisory):[/bold yellow]")
            for rtd in result.ready_to_apply_deltas:
                console.print(
                    f"  [yellow]• {rtd['id']}[/yellow] ({rtd['path']}, status={rtd['status']}): "
                    f"{rtd['verified_mutations']}/{rtd['total_mutations']} mutations verified — "
                    f"run [bold green]eidos delta apply {rtd['id']}[/bold green]"
                )

        if result.untouched_consumers:
            console.print("\n[bold cyan]→ Untouched Sibling Consumers to Review (Advisory):[/bold cyan]")
            for uc in result.untouched_consumers[:15]:
                owner_part = f" ({escape(uc['owner'])})" if uc.get("owner") else ""
                via_part = f" via {escape(uc['via'])}" if uc.get("via") else ""
                syms = uc.get("symbols") or []
                if syms:
                    sym_preview = ", ".join(syms[:5]) + (f" (+{len(syms) - 5})" if len(syms) > 5 else "")
                    sym_part = f" — {escape(sym_preview)}"
                else:
                    sym_part = ""
                kinds_badge = escape(f"[{', '.join(uc.get('change_kinds') or [])}]")
                console.print(
                    f"  [cyan]• {escape(uc['file'])}:{uc['line']}[/cyan]{owner_part} -> "
                    f"{escape(uc['target_file'])}{via_part} {kinds_badge}{sym_part}"
                )
            if len(result.untouched_consumers) > 15:
                console.print(
                    f"  [dim]... and {len(result.untouched_consumers) - 15} more (use --json for full list)[/dim]"
                )

        has_advisory_warnings = bool(result.validation_warnings or result.ready_to_apply_deltas)
        if result.is_clean and eff_fail_on_warnings and has_advisory_warnings:
            parts: list[str] = []
            if result.validation_warnings:
                parts.append(f"{len(result.validation_warnings)} advisory validation warning(s)")
            if result.ready_to_apply_deltas:
                parts.append(f"{len(result.ready_to_apply_deltas)} ready-to-apply open SpecDelta(s)")
            console.print(
                f"\n[bold red]✗ Verification Failed (--fail-on-warnings):[/bold red] {' and '.join(parts)} detected."
            )
        elif result.is_clean:
            console.print(
                "\n[bold green]✓ Verification Passed![/bold green] Living specifications and code are synchronized."
            )

    if markdown_out:
        markdown_out.write_text(result.summary_markdown, encoding="utf-8")
        if not json_output:
            console.print(f"\n[dim]Saved markdown summary to: {markdown_out}[/dim]")

    has_escalatable_advisories = bool(result.validation_warnings or result.ready_to_apply_deltas)
    if (not result.is_clean or (eff_fail_on_warnings and has_escalatable_advisories)) and not eff_warn_only:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("drift", rich_help_panel=PANEL_GOVERNANCE)(drift_cmd)
