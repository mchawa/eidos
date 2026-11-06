"""Shared CLI primitives: the Rich console, help-panel names, and path resolution helpers (CMP-CLI-APP)."""

from pathlib import Path

from rich.console import Console

from eidos.core.workspace.project import _has_eidos_markers, find_git_root, find_repo_root

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
    if (
        target_path.exists()
        and not has_eidos_markers(target_path)
        and "eidos" not in target_path.name.lower()
    ):
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
