"""Shared CLI primitives: the Rich console, help-panel names, and path resolution helpers (CMP-CLI-APP)."""

from pathlib import Path

from rich.console import Console

from eidos.core.workspace.project import find_repo_root

PANEL_SETUP = "Setup & Workspace"
PANEL_GOVERNANCE = "Governance & Verification"
PANEL_SPECS = "Living Catalog & Mutations"
PANEL_KNOWLEDGE = "Knowledge Graph & AI Context"
PANEL_PORTAL = "Living Web Portal"
PANEL_PLUGINS = "Agent Ecosystem & Plugins"

console = Console()


def has_eidos_markers(cand: Path) -> bool:
    """Return True when ``cand`` looks like an ``eidos/`` specification root directory."""
    return cand.is_dir() and (
        (cand / "system.adoc").exists()
        or any((cand / sub).is_dir() for sub in ("architecture", "domains", "policies", "design", "deltas"))
        or any(cand.glob("*.adoc"))
    )


def resolve_cli_eidos_dir(target_path: Path | None = None) -> Path:
    """Resolve the active ``eidos/`` specification directory for CLI commands."""
    if target_path is None:
        resolved = Path("eidos")
        if not resolved.exists():
            repo_eidos = find_repo_root() / "eidos"
            if repo_eidos.exists():
                resolved = repo_eidos
            elif has_eidos_markers(Path(".")):
                resolved = Path(".")
        return resolved
    if (target_path / "eidos").is_dir() and not has_eidos_markers(target_path):
        return target_path / "eidos"
    return target_path
