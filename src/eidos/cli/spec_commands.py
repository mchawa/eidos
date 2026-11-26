"""Living specification commands: validate and spec CRUD (`eidos spec ...`)."""

from pathlib import Path

import orjson
import typer
from rich.panel import Panel
from rich.table import Table

from eidos.cli._shared import PANEL_GOVERNANCE, console, resolve_cli_eidos_dir, resolve_param_from_config
from eidos.core.lifecycle.catalog import find_spec_by_id_or_path
from eidos.core.lifecycle.scaffolder import (
    create_spec,
    delete_spec,
    discover_repository_blueprint,
    get_spec_schema_info,
    list_specs,
)
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.config import load_repo_config_with_errors
from eidos.core.workspace.project import find_repo_root, resolve_spec_and_repo_roots

spec_app = typer.Typer(
    name="spec",
    help="Manage living specifications (list, show, schema, create, delete, discover).",
    add_completion=False,
)


def validate(
    target_path: Path | None = typer.Argument(
        None,
        help="Path to the directory containing .adoc specifications (defaults to ./eidos).",
    ),
    fail_on_warnings: bool = typer.Option(
        False,
        "--fail-on-warnings/--no-fail-on-warnings",
        help="Exit with code 1 when advisory validation warnings are present.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON validation report.",
    ),
):
    """Validate all specification documents against their schemas and verify cross-references."""
    target_path = resolve_cli_eidos_dir(target_path)

    if not target_path.exists():
        if json_output:
            typer.echo(
                orjson.dumps({"error": f"Directory '{target_path}' not found.", "is_clean": False}).decode("utf-8")
            )
            raise typer.Exit(code=1)
        console.print(f"[bold red]Error:[/bold red] Directory '{target_path}' not found.")
        raise typer.Exit(code=1)

    spec_root, repo_root = resolve_spec_and_repo_roots(target_path)
    repo_cfg, _ = load_repo_config_with_errors(eidos_dir=spec_root, repo_root=repo_root)
    effective_fail_on_warnings = bool(
        resolve_param_from_config("fail_on_warnings", fail_on_warnings, repo_cfg.drift.fail_on_warnings)
    )

    if not json_output:
        console.print(f"[bold cyan]Validating specifications in:[/bold cyan] {target_path.resolve()}\n")

    validator = SpecValidator()
    report = validator.validate_directory(target_path)

    if json_output:
        payload = {
            "is_clean": report.is_clean,
            "total_docs": report.total_docs,
            "valid_docs": report.valid_docs,
            "broken_references": report.broken_references,
            "warnings": report.warnings,
            "warning_count": len(report.warnings),
            "schema_errors": {str(k): v for k, v in report.schema_errors.items()},
            "documents": [
                {
                    "id": doc.doc_id,
                    "type": doc.doc_type,
                    "is_valid": doc.is_valid,
                    "path": (
                        str(doc.file_path.relative_to(Path.cwd()))
                        if doc.file_path.is_relative_to(Path.cwd())
                        else str(doc.file_path)
                    ),
                }
                for doc in report.documents
                if not doc.is_valid
            ],
        }
        typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode("utf-8"))
        if not report.is_clean or (effective_fail_on_warnings and report.warnings):
            raise typer.Exit(code=1)
        return

    table = Table(title="Eidos Specification Validation", show_header=True, header_style="bold magenta")
    table.add_column("Document ID", style="bold cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Status", justify="center")
    table.add_column("Path", style="dim")

    for doc in report.documents:
        status_label = "[bold green]PASS[/bold green]" if doc.is_valid else "[bold red]FAIL[/bold red]"
        rel_path = (
            str(doc.file_path.relative_to(Path.cwd()))
            if doc.file_path.is_relative_to(Path.cwd())
            else str(doc.file_path)
        )
        table.add_row(doc.doc_id, doc.doc_type, status_label, rel_path)

    console.print(table)

    if report.warnings:
        console.print("\n[bold yellow]Advisory Validation Warnings:[/bold yellow]")
        for warn in report.warnings:
            console.print(f"  [yellow]⚠[/yellow] {warn}")

    if not report.is_clean:
        console.print("\n[bold red]Validation Failures Detected:[/bold red]")
        for file_path, errors in report.schema_errors.items():
            console.print(f"\n[bold yellow]{file_path}:[/bold yellow]")
            for err in errors:
                console.print(f"  [red]✗[/red] {err}")

        if report.broken_references:
            console.print("\n[bold red]Broken Cross-References:[/bold red]")
            for ref in report.broken_references:
                console.print(f"  [red]✗[/red] {ref}")

        console.print(f"\n[bold red]Summary:[/bold red] {report.valid_docs}/{report.total_docs} valid documents.")
        raise typer.Exit(code=1)

    if effective_fail_on_warnings and report.warnings:
        console.print(
            f"\n[bold red]Failure (--fail-on-warnings):[/bold red] {len(report.warnings)} advisory validation warning(s) detected."
        )
        raise typer.Exit(code=1)

    console.print(f"\n[bold green]Success![/bold green] All {report.total_docs} specification documents are valid.")


@spec_app.command("list")
def spec_list_cmd(
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help=(
            "Filter by living spec type (system, architecture, domain, capability, policy, persona, exploration, "
            "container, component, contract, design-system, ui-component, screen, client, flow)."
        ),
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (draft, active, deprecated, retired, proposed, in_progress, adopted, discarded).",
    ),
    include_archived: bool = typer.Option(
        False,
        "--include-archived",
        help="Include archived specifications partitioned under archive/ subdirectories (e.g. eidos/explorations/archive/).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON specification list.",
    ),
):
    """List living specifications in the repository (excludes SpecDeltas; use `eidos delta list`)."""
    eidos_dir = resolve_cli_eidos_dir(path)
    try:
        specs = list_specs(
            eidos_dir,
            type_filter=type_filter,
            status_filter=status_filter,
            include_archived=include_archived,
        )
    except ValueError as e:
        if json_output:
            typer.echo(orjson.dumps({"error": str(e)}).decode("utf-8"))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if json_output:
        serializable = [
            {
                "id": s["id"],
                "type": s["type"],
                "status": s["status"],
                "title": s["title"],
                "is_archived": s.get("is_archived", False),
                "path": str(s["path"]),
                "relative_path": str(s["relative_path"]),
            }
            for s in specs
        ]
        typer.echo(
            orjson.dumps({"count": len(serializable), "specs": serializable}, option=orjson.OPT_INDENT_2).decode(
                "utf-8"
            )
        )
        return

    if not specs:
        console.print(f"[yellow]No specifications found under '{eidos_dir}'.[/yellow]")
        return

    table = Table(title=f"Eidos Living Specifications ({len(specs)} found)", show_header=True)
    table.add_column("Document ID", style="bold cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Title", style="white")
    table.add_column("Relative Path", style="dim")

    for s in specs:
        table.add_row(
            s["id"],
            s["type"],
            str(s["status"]),
            s["title"],
            str(s["relative_path"]),
        )
    console.print(table)


@spec_app.command("show")
def spec_show_cmd(
    target: str = typer.Argument(
        ...,
        help="Document ID, relative path, or filename of the living specification.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
):
    """Display frontmatter metadata and preview of a living specification document."""
    eidos_dir = resolve_cli_eidos_dir(path)
    target_clean = target.strip()
    if target_clean.upper().startswith("DLT-"):
        try:
            dlt_path = find_spec_by_id_or_path(eidos_dir / "deltas", target_clean, include_archived=True) or (
                find_spec_by_id_or_path(eidos_dir, target_clean, include_archived=True)
            )
        except ValueError as exc:
            console.print(
                f"[bold red]Error:[/bold red] {exc} Use `eidos delta show <ID>` with a specific SpecDelta ID."
            )
            raise typer.Exit(code=1) from exc
        resolved_id = (parse_spec_file(dlt_path).doc_id if dlt_path else None) or target_clean
        console.print(
            f"[bold red]Error:[/bold red] '{resolved_id}' is a SpecDelta (`delta/mutation`). "
            f"Use `eidos delta show {resolved_id}`, `eidos delta status {resolved_id}`, or `eidos delta verify {resolved_id}` instead."
        )
        raise typer.Exit(code=1)

    try:
        file_path = find_spec_by_id_or_path(eidos_dir, target)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    if not file_path:
        console.print(f"[bold red]Error:[/bold red] Specification '{target}' not found under '{eidos_dir}'.")
        raise typer.Exit(code=1)

    doc = parse_spec_file(file_path)
    if doc.doc_type == "delta/mutation":
        resolved_id = doc.doc_id or target_clean
        console.print(
            f"[bold red]Error:[/bold red] '{resolved_id}' is a SpecDelta (`delta/mutation`). "
            f"Use `eidos delta show {resolved_id}`, `eidos delta status {resolved_id}`, or `eidos delta verify {resolved_id}` instead."
        )
        raise typer.Exit(code=1)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Document ID:", f"[bold green]{doc.doc_id}[/bold green]")
    grid.add_row("Type:", doc.doc_type)
    grid.add_row("Title:", doc.title or "N/A")
    grid.add_row("Status:", str(doc.frontmatter.get("status", "N/A")))
    grid.add_row("Path:", str(file_path))

    console.print(Panel(grid, title=f"[bold blue]{doc.title or doc.doc_id}[/bold blue]", expand=False))


@spec_app.command("create")
def spec_create_cmd(
    doc_type: str = typer.Argument(
        ...,
        help=(
            "Living specification type: system, architecture, domain, capability, policy, persona, exploration, "
            "container, component, contract, design-system, ui-component, screen, client, flow."
        ),
    ),
    doc_id: str = typer.Argument(
        ...,
        help="Unique specification identifier (e.g. CAP-USER-AUTH, POL-ZERO-AI, CTR-PAYMENT-API, EXP-2026-001).",
    ),
    title: str = typer.Option(
        ...,
        "--title",
        "-t",
        help="Human-readable title for the specification blueprint.",
    ),
    client: str | None = typer.Option(
        None,
        "--client",
        "-c",
        help="Owning client ID (CLT-*), path, or slug (required when creating a 'screen' specification).",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
):
    """Scaffold a minimal, schema-valid draft blueprint (`status: draft`) for any living specification type."""
    eidos_dir = resolve_cli_eidos_dir(path)
    try:
        dest_file = create_spec(
            eidos_dir=eidos_dir,
            doc_type_key=doc_type,
            raw_id=doc_id,
            title=title,
            client=client,
        )
        rel = dest_file.relative_to(eidos_dir) if dest_file.is_relative_to(eidos_dir) else dest_file
        parsed_doc = parse_spec_file(dest_file)
        display_id = parsed_doc.doc_id or doc_id
        console.print(
            f"[bold green]✓ Created and validated specification ({display_id}):[/bold green] [bold]{dest_file.name}[/bold]"
        )
        console.print(f"  [dim]Location: {rel}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to create specification:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@spec_app.command("delete")
def spec_delete_cmd(
    target: str = typer.Argument(
        ...,
        help="Document ID, path, or filename of the specification to delete.",
    ),
    path: Path = typer.Option(
        Path("eidos"),
        "--path",
        "-p",
        help="Path to eidos specifications directory.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force deletion even if other specifications reference this document.",
    ),
):
    """Safely delete a living specification with reference checks."""
    eidos_dir = resolve_cli_eidos_dir(path)
    success, msg, refs = delete_spec(eidos_dir, target, force=force)
    if not success:
        console.print(f"[bold red]✗ Deletion Blocked:[/bold red] {msg}")
        if refs:
            console.print("\n[yellow]Referencing specifications:[/yellow]")
            for r in refs:
                rel = r.relative_to(eidos_dir) if r.is_relative_to(eidos_dir) else r
                console.print(f"  • {rel}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✓[/bold green] {msg}")
    if refs:
        console.print(f"[yellow]Warning:[/yellow] Deleted despite {len(refs)} reference(s) (--force used).")


@spec_app.command("schema")
def spec_schema_cmd(
    doc_type: str | None = typer.Argument(
        None,
        help=(
            "Specification type to inspect (e.g. capability, container, "
            "component, contract, policy, exploration, delta). Omit to list all 16 schemas."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON schema specification and example YAML frontmatter.",
    ),
):
    """Inspect bundled v1 YAML frontmatter schema constraints, required/optional keys, and valid templates."""
    try:
        info = get_spec_schema_info(doc_type)
    except ValueError as e:
        if json_output:
            typer.echo(orjson.dumps({"error": str(e)}).decode("utf-8"))
        else:
            console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    if isinstance(info, list):
        if json_output:
            summary_list = [
                {
                    "type": s["type"],
                    "short_type": s["short_type"],
                    "id_prefix": s["id_prefix"],
                    "schema_path": s["schema_path"],
                    "status_values": s["status_values"],
                    "required": s["required"],
                    "optional": s["optional"],
                    "additional_properties": s["additional_properties"],
                }
                for s in info
            ]
            typer.echo(
                orjson.dumps({"count": len(summary_list), "schemas": summary_list}, option=orjson.OPT_INDENT_2).decode(
                    "utf-8"
                )
            )
            return

        table = Table(
            title=f"Eidos v1 Specification Schemas ({len(info)} types)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Short Alias", style="bold cyan")
        table.add_column("Canonical Type", style="yellow")
        table.add_column("Prefix", style="green")
        table.add_column("Required Keys", style="white")
        table.add_column("Optional Keys", style="dim")

        for s in info:
            table.add_row(
                s["short_type"],
                s["type"],
                f"{s['id_prefix']}*",
                ", ".join(s["required"]),
                ", ".join(s["optional"]) or "—",
            )
        console.print(table)
        console.print("\n[dim]Tip: Run `eidos spec schema <type> [--json]` for nested keys and YAML templates.[/dim]")
        return

    if json_output:
        typer.echo(orjson.dumps(info, option=orjson.OPT_INDENT_2).decode("utf-8"))
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")
    grid.add_row("Canonical Type:", f"[bold yellow]{info['type']}[/bold yellow] (alias: `{info['short_type']}`)")
    grid.add_row("ID Pattern:", f"`{info['id_pattern']}`")
    grid.add_row("Schema File:", f"`{info['schema_path']}`")
    grid.add_row("Status Values:", ", ".join(info["status_values"]) if info["status_values"] else "N/A")
    grid.add_row("Required Keys:", ", ".join(info["required"]))
    grid.add_row("Optional Keys:", ", ".join(info["optional"]) or "None")
    grid.add_row("Strict Schema:", "additionalProperties: false (unknown keys rejected)")
    console.print(Panel(grid, title=f"[bold blue]Schema: {info['type']}[/bold blue]", expand=False))

    prop_table = Table(title="Frontmatter Properties", show_header=True, header_style="bold magenta")
    prop_table.add_column("Property", style="bold cyan")
    prop_table.add_column("Req", justify="center")
    prop_table.add_column("Type / Constraints", style="yellow")
    prop_table.add_column("Nested Keys / Description", style="white")

    for prop_name, pdesc in info["properties"].items():
        req_badge = "[bold green]yes[/bold green]" if pdesc.get("required") else "[dim]no[/dim]"
        t_str = str(pdesc.get("type", "any"))
        if pdesc.get("items_type"):
            t_str = f"array<{pdesc['items_type']}>"
        if pdesc.get("const"):
            t_str += f" = '{pdesc['const']}'"
        elif pdesc.get("enum"):
            t_str += f" ({' | '.join(str(x) for x in pdesc['enum'])})"
        elif pdesc.get("ref_prefixes"):
            t_str += f" ({', '.join(p + '*' for p in pdesc['ref_prefixes'])})"

        nested_parts: list[str] = []
        if pdesc.get("allowed_keys"):
            req_sub = set(pdesc.get("required_keys") or [])
            sub_fmt = [f"{k}*" if k in req_sub else k for k in pdesc["allowed_keys"]]
            nested_parts.append(f"[bold]Keys:[/bold] {', '.join(sub_fmt)}")
        elif pdesc.get("item_allowed_keys"):
            req_sub = set(pdesc.get("item_required_keys") or [])
            sub_fmt = [f"{k}*" if k in req_sub else k for k in pdesc["item_allowed_keys"]]
            nested_parts.append(f"[bold]Item keys:[/bold] {', '.join(sub_fmt)}")
        if pdesc.get("description"):
            nested_parts.append(pdesc["description"])

        prop_table.add_row(prop_name, req_badge, t_str, " — ".join(nested_parts))

    console.print(prop_table)
    console.print("\n[bold cyan]Example Schema-Valid YAML Frontmatter:[/bold cyan]")
    typer.echo(info["example_frontmatter"])


@spec_app.command("discover")
def spec_discover_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to the repository root or eidos specifications directory (defaults to current repository).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON brownfield discovery blueprint.",
    ),
):
    """Deterministically scan a polyglot repository to propose CNT-* containers, CTR-* contracts, and unmapped source_path gaps."""
    if path is None:
        repo_root = find_repo_root(None)
        eidos_dir = repo_root / "eidos"
    else:
        resolved = path.resolve()
        if resolved.name == "eidos" or (resolved / "system.adoc").is_file():
            eidos_dir = resolved
            repo_root = resolved.parent
        else:
            repo_root = resolved
            eidos_dir = resolved / "eidos"

    from eidos.cli._shared import load_cli_repo_config

    load_cli_repo_config(eidos_dir, repo_root=repo_root, json_output=json_output)
    try:
        blueprint = discover_repository_blueprint(repo_root=repo_root, eidos_dir=eidos_dir)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(orjson.dumps({"error": str(exc)}).decode("utf-8"))
        else:
            console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(orjson.dumps(blueprint, option=orjson.OPT_INDENT_2).decode("utf-8"))
        return

    summary = blueprint["summary"]
    console.print(
        Panel(
            f"[bold]Repository:[/bold] {blueprint['repo_root']}\n"
            f"[bold]Containers:[/bold] {summary['containers_found']} ({summary['containers_unmapped']} unmapped)  "
            f"[bold]Contracts:[/bold] {summary['contracts_found']} ({summary['contracts_unmapped']} unmapped)  "
            f"[bold]Test Roots:[/bold] {summary['test_roots_found']}  "
            f"[bold]Unmapped Code Roots:[/bold] {summary['unmapped_code_roots_found']}",
            title="[bold blue]Eidos Brownfield Repository Discovery[/bold blue]",
            expand=False,
        )
    )

    if blueprint["containers"]:
        c_table = Table(title="Candidate Containers (CNT-*)", show_header=True, header_style="bold magenta")
        c_table.add_column("Suggested ID", style="bold cyan")
        c_table.add_column("Type", style="yellow")
        c_table.add_column("Runtime", style="white")
        c_table.add_column("source_path", style="green")
        c_table.add_column("Mapped", justify="center")
        for cnt in blueprint["containers"]:
            mapped_lbl = "[green]yes[/green]" if cnt["already_mapped"] else "[yellow]no[/yellow]"
            c_table.add_row(
                cnt["suggested_id"],
                cnt["container_type"],
                cnt["runtime"],
                cnt["source_path"],
                mapped_lbl,
            )
        console.print(c_table)

    if blueprint["contracts"]:
        k_table = Table(title="Candidate Contracts (CTR-*)", show_header=True, header_style="bold magenta")
        k_table.add_column("Suggested ID", style="bold cyan")
        k_table.add_column("Protocol", style="yellow")
        k_table.add_column("specification_file", style="green")
        k_table.add_column("Mapped", justify="center")
        for ctr in blueprint["contracts"]:
            mapped_lbl = "[green]yes[/green]" if ctr["already_mapped"] else "[yellow]no[/yellow]"
            k_table.add_row(ctr["suggested_id"], ctr["protocol"], ctr["specification_file"], mapped_lbl)
        console.print(k_table)

    if blueprint["test_roots"]:
        console.print(f"\n[bold cyan]Test Roots:[/bold cyan] {', '.join(blueprint['test_roots'])}")
    if blueprint["unmapped_code_roots"]:
        console.print(f"[bold yellow]Unmapped Code Roots:[/bold yellow] {', '.join(blueprint['unmapped_code_roots'])}")


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("validate", rich_help_panel=PANEL_GOVERNANCE)(validate)
