"""Token-budgeted architectural context slicing command (`eidos context`)."""

from pathlib import Path

import orjson
import typer

from eidos.cli._shared import PANEL_KNOWLEDGE, console, resolve_cli_eidos_dir
from eidos.core.index.context import _has_glob_magic, expand_brace_globs, split_brace_aware_csv
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.workspace.project import find_repo_root


def resolve_cli_target_files_and_workspace(
    files: list[str] | None,
    path: Path | None,
    *,
    json_output: bool = False,
) -> tuple[Path, Path, list[str] | None]:
    """Resolve ``(eidos_dir, repo_root, normalized_files)`` with brace-aware comma splitting."""
    split_files: list[str] | None = None
    if files:
        split_files = [part for raw in files for part in split_brace_aware_csv(raw) if part]

    hint_candidates: list[str] = []
    for raw_f in split_files or []:
        if not _has_glob_magic(raw_f):
            hint_candidates.append(raw_f)
            continue
        norm_hint = raw_f.replace("\\", "/")
        lead_slash = "/" if norm_hint.startswith("/") else ""
        prefix_segs: list[str] = []
        for seg in norm_hint.split("/"):
            if not seg:
                continue
            if _has_glob_magic(seg):
                break
            prefix_segs.append(seg)
        if prefix_segs:
            hint_candidates.append(f"{lead_slash}{'/'.join(prefix_segs)}")

    eidos_dir = resolve_cli_eidos_dir(path, hint_files=hint_candidates or None)
    if not eidos_dir.exists():
        if json_output:
            typer.echo(orjson.dumps({"error": f"Directory '{eidos_dir}' not found."}).decode("utf-8"))
        else:
            console.print(f"[bold red]Error:[/bold red] Directory '{eidos_dir}' not found.", soft_wrap=True)
        raise typer.Exit(code=1)

    resolved_eidos = eidos_dir.resolve()
    repo_root = resolved_eidos.parent if resolved_eidos.name == "eidos" else find_repo_root(resolved_eidos)
    cwd_resolved = Path.cwd().resolve()
    normalized_files: list[str] | None = None
    if split_files:
        normalized_files = []
        for raw_f in split_files:
            if _has_glob_magic(raw_f):
                clean_pat = raw_f.replace("\\", "/")
                root_prefix = f"{repo_root.as_posix().rstrip('/')}/"
                if clean_pat.startswith(root_prefix):
                    clean_pat = clean_pat[len(root_prefix) :]
                while clean_pat.startswith("./"):
                    clean_pat = clean_pat[2:]
                if not clean_pat.startswith("/"):
                    segs = [s for s in clean_pat.split("/") if s]
                    first_glob = next((i for i, s in enumerate(segs) if _has_glob_magic(s)), len(segs))
                    static_head = "/".join(segs[:first_glob])
                    glob_tail = "/".join(segs[first_glob:])
                    if clean_pat.startswith("../") or (
                        cwd_resolved != repo_root and repo_root.is_relative_to(cwd_resolved)
                    ):
                        resolved_head = (cwd_resolved / static_head).resolve() if static_head else cwd_resolved
                        if resolved_head == repo_root:
                            clean_pat = glob_tail
                        elif resolved_head.is_relative_to(repo_root):
                            clean_pat = f"{resolved_head.relative_to(repo_root).as_posix()}/{glob_tail}"
                        else:
                            clean_pat = f"{resolved_head.as_posix()}/{glob_tail}"
                    elif cwd_resolved != repo_root and cwd_resolved.is_relative_to(repo_root):
                        raw_parent = clean_pat.rsplit("/", 1)[0] if "/" in clean_pat else ""
                        parent_pat = (
                            raw_parent
                            if any(not _has_glob_magic(seg) for seg in raw_parent.split("/") if seg)
                            else ""
                        )
                        pat_variants = expand_brace_globs(clean_pat)
                        parent_variants = expand_brace_globs(parent_pat) if parent_pat else []
                        try:
                            root_has_match = any(
                                p.is_file() for v in pat_variants for p in repo_root.glob(v)
                            ) or any(p.is_dir() for pv in parent_variants for p in repo_root.glob(pv))
                        except (OSError, ValueError, NotImplementedError):
                            root_has_match = False
                        if not root_has_match:
                            try:
                                cwd_has_match = any(
                                    p.is_file() for v in pat_variants for p in cwd_resolved.glob(v)
                                ) or any(p.is_dir() for pv in parent_variants for p in cwd_resolved.glob(pv))
                            except (OSError, ValueError, NotImplementedError):
                                cwd_has_match = False
                            if cwd_has_match:
                                rel_cwd = str(cwd_resolved.relative_to(repo_root)).replace("\\", "/")
                                clean_pat = f"{rel_cwd}/{clean_pat}"
                normalized_files.append(clean_pat)
                continue

            p_f = Path(raw_f)
            abs_cand = p_f.resolve() if p_f.is_absolute() else (cwd_resolved / p_f).resolve()
            if abs_cand.is_relative_to(repo_root) and (abs_cand.exists() or not (repo_root / p_f).exists()):
                normalized_files.append(str(abs_cand.relative_to(repo_root)).replace("\\", "/"))
            else:
                normalized_files.append(raw_f)

    return eidos_dir, repo_root, normalized_files


def context_cmd(
    files: list[str] | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Target source file path(s), comma-separated paths, or quoted glob patterns (e.g. 'src/**/*.{ts,tsx}').",
    ),
    component: str | None = typer.Option(
        None,
        "--component",
        "-c",
        help="Target component identifier (e.g. 'CMP-CORE').",
    ),
    task: str | None = typer.Option(
        None,
        "--task",
        "-t",
        help="Free-text task description or prompt to slice architectural context for.",
    ),
    tier: str = typer.Option(
        "guardrails",
        "--tier",
        "-T",
        help="Context slice verbosity tier: 'anchor' (~140 tokens), 'guardrails' (~260 tokens), 'deep' (~500 tokens). Defaults to 'guardrails'.",
    ),
    semantic: bool = typer.Option(
        True,
        "--semantic/--no-semantic",
        help="Enable or disable opt-in Hybrid RRF semantic fusion when spec_embeddings are available.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output context slice as machine-readable JSON instead of Markdown.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path to eidos directory (defaults to ./eidos).",
    ),
):
    """Extract a compact architectural subgraph slice (C4 components, contracts, capabilities, invariants, policies)."""
    eidos_dir, repo_root, normalized_files = resolve_cli_target_files_and_workspace(
        files,
        path,
        json_output=json_output,
    )
    idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
    slice_result = idx.get_tiered_context_slice(
        files=normalized_files,
        component=component,
        task=task,
        tier=tier,
        use_semantic=semantic,
    )

    if json_output:
        typer.echo(slice_result.to_json())
    else:
        typer.echo(slice_result.to_markdown())


def register(app: typer.Typer) -> None:
    """Attach the root-level commands owned by this module to the root Typer app."""
    app.command("context", rich_help_panel=PANEL_KNOWLEDGE)(context_cmd)
