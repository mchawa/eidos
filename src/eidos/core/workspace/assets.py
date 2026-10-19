"""Centralized asset locator and loader for Eidos schemas and plugin reference assets."""

from pathlib import Path


def _iter_assets_roots() -> list[Path]:
    """Return ordered candidate assets directories across installed wheels, repo checkouts, and CWD."""
    candidates: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        if p.is_dir() and p not in seen:
            seen.add(p)
            candidates.append(p)

    # 1. Check embedded in package directory (e.g. site-packages/eidos/assets or local editable)
    _add(Path(__file__).resolve().parents[2] / "assets")

    # 2. Check repository root by ascending from this source file
    cur = Path(__file__).resolve().parent
    for _ in range(6):
        _add(cur / "assets")
        cur = cur.parent

    # 3. Check current working directory
    _add(Path.cwd() / "assets")

    return candidates


def get_assets_root() -> Path:
    """Locate the root assets directory across local repo checkouts and installed wheels."""
    candidates = _iter_assets_roots()
    for candidate in candidates:
        if (
            (candidate / "schemas").is_dir()
            or (candidate / "plugin_reference").is_dir()
            or (candidate / "portal").is_dir()
        ):
            return candidate
    if candidates:
        return candidates[0]

    raise FileNotFoundError("Could not locate Eidos assets directory in package or repository.")


def get_assets_path(subpath: str = "") -> Path:
    """Resolve a specific subpath within the Eidos assets directory."""
    if not subpath:
        return get_assets_root()
    for candidate in _iter_assets_roots():
        target = candidate / subpath
        if target.exists():
            return target
    root = get_assets_root()
    raise FileNotFoundError(f"Asset path '{subpath}' does not exist inside {root}")


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


_TEMPLATE_PATHS_BY_TYPE: dict[str, str] = {
    "product/system": "product/system.template.adoc",
    "system": "product/system.template.adoc",
    "tech/architecture": "tech/architecture.template.adoc",
    "architecture": "tech/architecture.template.adoc",
    "product/domain": "product/domain.template.adoc",
    "domain": "product/domain.template.adoc",
    "product/capability": "product/capability.template.adoc",
    "capability": "product/capability.template.adoc",
    "product/persona": "product/persona.template.adoc",
    "persona": "product/persona.template.adoc",
    "product/policy": "product/policy.template.adoc",
    "policy": "product/policy.template.adoc",
    "product/exploration": "product/exploration.template.adoc",
    "exploration": "product/exploration.template.adoc",
    "tech/container": "tech/container.template.adoc",
    "container": "tech/container.template.adoc",
    "tech/component": "tech/component.template.adoc",
    "component": "tech/component.template.adoc",
    "tech/contract": "tech/contract.template.adoc",
    "contract": "tech/contract.template.adoc",
    "design/system": "design/system.template.adoc",
    "design-system": "design/system.template.adoc",
    "design/ui-component": "design/ui-component.template.adoc",
    "ui-component": "design/ui-component.template.adoc",
    "design/screen": "design/screen.template.adoc",
    "screen": "design/screen.template.adoc",
    "design/client": "design/client.template.adoc",
    "client": "design/client.template.adoc",
    "design/flow": "design/flow.template.adoc",
    "flow": "design/flow.template.adoc",
    "delta/mutation": "delta/delta.template.adoc",
    "delta": "delta/delta.template.adoc",
}


def get_spec_template_path(doc_type: str) -> Path:
    """Resolve the AsciiDoc prose template path beside the corresponding v1 schema."""
    key = doc_type.strip().lower()
    rel = _TEMPLATE_PATHS_BY_TYPE.get(key)
    if not rel:
        raise ValueError(f"No specification template registered for type '{doc_type}'.")
    return get_v1_schemas_dir() / rel


def render_spec_template(doc_type: str, **context: object) -> str:
    """Load and render the AsciiDoc prose template for doc_type with the given placeholder context."""
    template_text = get_spec_template_path(doc_type).read_text(encoding="utf-8")
    for k, v in context.items():
        template_text = template_text.replace(f"{{{k}}}", str(v))
    return template_text
