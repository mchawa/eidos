"""Centralized asset locator and loader for Eidos schemas and plugin reference assets."""

from pathlib import Path


def get_assets_root() -> Path:
    """Locate the root assets directory across local repo checkouts and installed wheels."""
    # 1. Check embedded in package directory (e.g. site-packages/eidos/assets or local editable)
    pkg_assets = Path(__file__).resolve().parents[2] / "assets"
    if pkg_assets.is_dir():
        return pkg_assets

    # 2. Check repository root by ascending from this source file
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = cur / "assets"
        if candidate.is_dir():
            return candidate
        cur = cur.parent

    # 3. Check current working directory
    cwd_candidate = Path.cwd() / "assets"
    if cwd_candidate.is_dir():
        return cwd_candidate

    raise FileNotFoundError("Could not locate Eidos assets directory in package or repository.")


def get_assets_path(subpath: str = "") -> Path:
    """Resolve a specific subpath within the Eidos assets directory."""
    root = get_assets_root()
    if not subpath:
        return root
    target = root / subpath
    if not target.exists():
        raise FileNotFoundError(f"Asset path '{subpath}' does not exist inside {root}")
    return target


def get_schemas_dir() -> Path:
    """Return the directory containing JSON and YAML specification schemas."""
    return get_assets_path("schemas")


def get_v1_schemas_dir() -> Path:
    """Return the directory containing Eidos v1 specification schemas."""
    return get_assets_path("schemas/v1")


def get_agent_plugins_schemas_dir() -> Path:
    """Return the directory containing agent-plugins.org JSON schemas."""
    return get_assets_path("schemas/agent_plugins")


def get_plugin_reference_dir() -> Path:
    """Return the directory containing the canonical Eidos Agent Plugin reference."""
    return get_assets_path("plugin_reference")
