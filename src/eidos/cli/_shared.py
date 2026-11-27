"""Shared CLI primitives: the Rich console, help-panel names, path resolution, and repo config helpers (CMP-CLI-APP)."""

import os
import subprocess
from pathlib import Path
from typing import Any

import orjson
import typer
from rich.console import Console

from eidos.core.workspace.config import (
    ConfigValidationError,
    EidosConfig,
    load_repo_config_with_errors,
    load_repo_config_with_sources,
)
from eidos.core.workspace.project import (
    _has_eidos_markers,
    discover_nested_subproject_roots,
    find_git_root,
    find_repo_root,
    is_excluded_by_config,
    resolve_spec_and_repo_roots,
)

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


_GLOB_MAGIC_CHARS = frozenset("*?[{")


def _strip_glob_prefix(raw: str) -> str:
    """Return the static leading directory prefix of a path or glob pattern."""
    norm = raw.strip().replace("\\", "/")
    if not any(ch in norm for ch in _GLOB_MAGIC_CHARS):
        return norm
    lead_slash = "//" if norm.startswith("//") else ("/" if norm.startswith("/") else "")
    prefix_segs: list[str] = []
    for seg in norm.split("/"):
        if not seg:
            continue
        if any(ch in seg for ch in _GLOB_MAGIC_CHARS):
            break
        prefix_segs.append(seg)
    if not prefix_segs:
        return ""
    joined = f"{lead_slash}{'/'.join(prefix_segs)}"
    if len(prefix_segs) == 1 and len(prefix_segs[0]) == 2 and prefix_segs[0][1] == ":":
        return f"{joined}/"
    return joined


def _lexical_abs_path(raw_path: Path, base_dir: Path) -> Path:
    """Normalize a path lexically against ``base_dir`` without dereferencing leaf symlinks."""
    combined = raw_path if raw_path.is_absolute() else (base_dir / raw_path)
    return Path(os.path.normpath(str(combined)))


def _resolve_hint_file_eidos_dir(raw_hint: str) -> Path | None:
    """Resolve a target file/glob hint (including planned non-existent subpaths) to its owning sub-project eidos/ dir."""
    cleaned = _strip_glob_prefix(raw_hint)
    if not cleaned:
        return None
    cand = Path(cleaned)
    cwd_res = Path.cwd().resolve()
    abs_cand = _lexical_abs_path(cand, cwd_res)
    cwd_git_root = find_git_root(cwd_res)

    existing: Path | None = None
    for ancestor in (abs_cand, *abs_cand.parents):
        if ancestor.is_symlink():
            existing = ancestor.parent
            break
        if ancestor.exists():
            existing = ancestor
            break
    if existing is None:
        return None
    if (
        not cand.is_absolute()
        and cwd_git_root is not None
        and existing != cwd_git_root
        and not existing.is_relative_to(cwd_git_root)
    ):
        return None
    if existing.name == "eidos" and has_eidos_markers(existing):
        return existing.resolve()
    hint_repo = find_repo_root(existing)
    hint_eidos = hint_repo / "eidos"
    if has_eidos_markers(hint_eidos):
        return hint_eidos.resolve()
    return None


def resolve_hint_eidos_dirs(hint_files: list[str] | None) -> list[Path]:
    """Return deduplicated owning eidos/ directories resolved across one or more target file/glob hints."""
    if not hint_files:
        return []
    found: list[Path] = []
    seen: set[Path] = set()
    for raw_f in hint_files:
        if not raw_f or not str(raw_f).strip():
            continue
        resolved = _resolve_hint_file_eidos_dir(str(raw_f))
        if resolved is not None and resolved not in seen:
            seen.add(resolved)
            found.append(resolved)
    return found


def get_global_cli_path() -> Path | None:
    """Return the root-level global ``--path`` / ``-p`` option from the active Click/Typer context, if set."""
    ctx = _get_current_cli_context()
    if ctx is None:
        return None
    root_ctx = ctx.find_root()
    if isinstance(root_ctx.obj, dict):
        val = root_ctx.obj.get("global_path")
        if isinstance(val, Path):
            return val
    return None


def _is_subcommand_cli_path(target_path: Path | None, param_name: str = "path") -> bool:
    """Return True when the active subcommand explicitly received its own path parameter on the CLI."""
    ctx = _get_current_cli_context()
    if ctx is not None:
        for cand_name in dict.fromkeys((param_name, "target_path", "path")):
            if cand_name in getattr(ctx, "params", {}):
                source = ctx.get_parameter_source(cand_name)
                if source is not None:
                    return getattr(source, "name", None) == "COMMANDLINE"
    return target_path is not None and target_path != Path("eidos")


def is_explicit_cli_path(target_path: Path | None, param_name: str = "path") -> bool:
    """Return True when either the subcommand path parameter or the global root ``-p``/``--path`` was explicitly passed."""
    return _is_subcommand_cli_path(target_path, param_name=param_name) or (get_global_cli_path() is not None)


def discover_workspace_eidos_dirs(start_dir: Path | None = None) -> list[Path]:
    """Return all initialized eidos/ directories under ``start_dir`` (root eidos/ first if present, then sorted nested sub-projects)."""
    base = (start_dir or Path.cwd()).resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    root_eidos = (base / "eidos").resolve()
    if has_eidos_markers(root_eidos):
        found.append(root_eidos)
        seen.add(root_eidos)
    for sub_root in discover_nested_subproject_roots(base):
        sub_eidos = (sub_root / "eidos").resolve()
        if sub_eidos not in seen and has_eidos_markers(sub_eidos):
            found.append(sub_eidos)
            seen.add(sub_eidos)
    return found


def format_rel_eidos_paths(eidos_dirs: list[Path], base_dir: Path | None = None) -> list[str]:
    """Format resolved eidos/ paths cleanly relative to ``base_dir`` (POSIX style) for diagnostics and hints."""
    base = (base_dir or Path.cwd()).resolve()
    formatted: list[str] = []
    for ed in eidos_dirs:
        res = ed.resolve()
        try:
            formatted.append(res.relative_to(base).as_posix())
        except ValueError:
            formatted.append(str(res))
    return formatted


def resolve_uncommitted_workspace_eidos_dirs(
    base_dir: Path | None = None,
    *,
    eidos_dirs: list[Path] | None = None,
) -> list[Path]:
    """Return the subset of workspace eidos/ directories whose project tree has uncommitted Git changes."""
    base = (base_dir or Path.cwd()).resolve()
    projects = list(eidos_dirs) if eidos_dirs is not None else discover_workspace_eidos_dirs(base)
    if not projects:
        return []
    git_root = find_git_root(base)
    if git_root is None:
        return []
    git_res = git_root.resolve()
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=str(git_res),
            capture_output=True,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0 or not proc.stdout:
        return []

    tokens = proc.stdout.split(b"\x00")
    changed_abs: list[Path] = []
    idx = 0
    while idx < len(tokens):
        entry = tokens[idx]
        idx += 1
        if len(entry) < 4:
            continue
        xy = entry[:2].decode("utf-8", errors="replace")
        rel_path = entry[3:].decode("utf-8", errors="replace")
        raw_paths = [rel_path]
        if ("R" in xy or "C" in xy) and idx < len(tokens) and tokens[idx]:
            raw_paths.append(tokens[idx].decode("utf-8", errors="replace"))
            idx += 1
        for p_str in raw_paths:
            norm = p_str.replace("\\", "/")
            if norm.startswith(("eidos/.cache/", ".cache/", ".git/")) or "/.cache/" in norm or "/.git/" in norm:
                continue
            # Keep lexical path under git_res so symlinks stay attributed to their containing sub-project.
            changed_abs.append(_lexical_abs_path(Path(norm), git_res))

    if not changed_abs:
        return []

    # Match most-specific project root first so nested sub-project edits never bleed into a root eidos/ project.
    proj_pairs = sorted(
        [
            (ed.resolve().parent if ed.resolve().name == "eidos" else find_repo_root(ed), ed.resolve())
            for ed in projects
        ],
        key=lambda item: len(item[0].parts),
        reverse=True,
    )
    proj_configs: dict[Path, EidosConfig] = {}
    matched: set[Path] = set()
    for ch_path in changed_abs:
        for proj_root, proj_eidos in proj_pairs:
            if ch_path == proj_root or ch_path.is_relative_to(proj_root):
                if proj_eidos not in proj_configs:
                    proj_configs[proj_eidos], _ = load_repo_config_with_errors(
                        eidos_dir=proj_eidos, repo_root=proj_root
                    )
                rel_to_proj = ch_path.relative_to(proj_root).as_posix()
                if not is_excluded_by_config(rel_to_proj, proj_configs[proj_eidos]):
                    matched.add(proj_eidos)
                break

    return [ed.resolve() for ed in projects if ed.resolve() in matched]


def resolve_git_diff_workspace_eidos_dirs(
    base_dir: Path | None = None,
    *,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
    eidos_dirs: list[Path] | None = None,
) -> list[Path]:
    """Return workspace eidos/ directories with modified files in either uncommitted state or ``base_ref...head_ref``."""
    if uncommitted:
        return resolve_uncommitted_workspace_eidos_dirs(base_dir, eidos_dirs=eidos_dirs)

    from eidos.vcs.diff import GitResolutionError, get_git_diff_files
    from eidos.vcs.snapshot import _load_head_commit_config

    base = (base_dir or Path.cwd()).resolve()
    projects = list(eidos_dirs) if eidos_dirs is not None else discover_workspace_eidos_dirs(base)
    if not projects:
        return []

    dirty: list[Path] = []
    first_err: GitResolutionError | None = None
    succeeded_any = False
    for ed in projects:
        resolved_ed = ed.resolve()
        proj_root = resolved_ed.parent if resolved_ed.name == "eidos" else find_repo_root(resolved_ed)
        git_root = find_git_root(proj_root)
        if git_root is not None:
            try:
                eidos_rel_prefix = (
                    str(resolved_ed.relative_to(proj_root.resolve())).replace("\\", "/").strip("/") or "eidos"
                )
            except ValueError:
                eidos_rel_prefix = resolved_ed.name or "eidos"
            head_cfg = _load_head_commit_config(git_root, proj_root.resolve(), eidos_rel_prefix, head_ref)
        else:
            head_cfg, _ = load_repo_config_with_errors(eidos_dir=resolved_ed, repo_root=proj_root)
        try:
            diff_files = get_git_diff_files(
                proj_root,
                base_ref=base_ref,
                head_ref=head_ref,
                uncommitted=False,
                repo_config=head_cfg,
                eidos_dir=resolved_ed,
            )
            succeeded_any = True
            filtered = [f for f in diff_files if not is_excluded_by_config(f, head_cfg)]
            if filtered:
                dirty.append(resolved_ed)
        except GitResolutionError as exc:
            if base_ref is not None:
                raise
            if first_err is None:
                first_err = exc
    if not succeeded_any and first_err is not None:
        raise first_err
    return dirty


def _looks_like_path_hint(raw: str | None) -> bool:
    """Return True if a CLI argument resembles a filesystem path rather than a bare entity ID or symbol."""
    if not raw or not raw.strip():
        return False
    val = raw.strip()
    if any(
        val.upper().startswith(verb)
        for verb in ("GET /", "POST /", "PUT /", "PATCH /", "DELETE /", "HEAD /", "OPTIONS /")
    ):
        return False
    if "/" in val or "\\" in val or val.lower().endswith(".adoc"):
        return True
    return Path(val).exists()


def normalize_cli_spec_target(target: str, eidos_dir: Path) -> str:
    """Strip an outer monorepo prefix from a path-shaped spec/delta/memory target so stem lookups work with or without .adoc."""
    raw = target.strip()
    if not raw or not _looks_like_path_hint(raw):
        return target
    resolved_eidos = eidos_dir.resolve()
    cwd_res = Path.cwd().resolve()
    cand = Path(raw)
    abs_cand = _lexical_abs_path(cand, cwd_res)
    if abs_cand.is_relative_to(resolved_eidos):
        return abs_cand.relative_to(resolved_eidos).as_posix()
    return target


def require_cli_eidos_dir(
    target_path: Path | None = None,
    *,
    hint_files: list[str] | None = None,
    json_output: bool = False,
    param_name: str = "path",
) -> Path:
    """Resolve the active eidos/ dir, failing closed with discovered sub-project hints at a rootless monorepo."""
    eidos_dir = resolve_cli_eidos_dir(target_path, hint_files=hint_files)
    if not is_explicit_cli_path(target_path, param_name=param_name) and not has_eidos_markers(eidos_dir):
        nested = [sub / "eidos" for sub in discover_nested_subproject_roots(Path.cwd())]
        if nested:
            rel_list = format_rel_eidos_paths(nested)
            msg = (
                f"No root 'eidos/' specifications found in '{Path.cwd().resolve()}'. "
                f"Discovered monorepo sub-projects: {', '.join(rel_list)}. "
                f"Re-run with '--path <subproject>/eidos' (or run from the target sub-project directory)."
            )
            if json_output:
                typer.echo(
                    orjson.dumps(
                        {"error": msg, "discovered_subprojects": rel_list},
                        option=orjson.OPT_INDENT_2,
                    ).decode("utf-8")
                )
            else:
                console.print(f"[bold red]Error:[/bold red] {msg}", soft_wrap=True)
            raise typer.Exit(code=1)
    return eidos_dir


def resolve_cli_eidos_dir(
    target_path: Path | None = None,
    hint_files: list[str] | None = None,
) -> Path:
    """Resolve the active ``eidos/`` specification directory for CLI commands (supporting global -p and monorepo sub-projects)."""
    if not _is_subcommand_cli_path(target_path):
        global_path = get_global_cli_path()
        if global_path is not None:
            target_path = global_path
    if target_path is None or target_path == Path("eidos"):
        if hint_files:
            for raw_f in hint_files:
                if not raw_f or not str(raw_f).strip():
                    continue
                resolved_hint = _resolve_hint_file_eidos_dir(str(raw_f))
                if resolved_hint is not None:
                    return resolved_hint
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
