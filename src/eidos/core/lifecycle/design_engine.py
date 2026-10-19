"""Design engine: scaffolds Multi-Design Systems, W3C DTCG tokens, UI components, screens, clients, and flows."""

import json
import re
from pathlib import Path
from typing import Any

DESIGN_SPEC_KEYS = ("design-system", "ui-component", "screen", "client", "flow")


def _resolve_design_entity_ref(
    raw_val: str,
    prefix: str,
    default_filename: str,
    eidos_dir: Path,
) -> tuple[str, str]:
    """Resolve a design entity ID, slug, or .adoc path into (resolved_ref_id, clean_slug)."""
    raw_clean = raw_val.strip()
    prefix_lower = prefix.lower()
    if "/" in raw_clean or "\\" in raw_clean or raw_clean.lower().endswith(".adoc"):
        rel_path = Path(raw_clean.replace("\\", "/"))
        candidate_files = [
            eidos_dir / rel_path,
            eidos_dir.parent / rel_path,
            rel_path,
        ]
        parsed_id = ""
        for cf in candidate_files:
            if cf.exists() and cf.is_file():
                try:
                    from eidos.core.spec.parser import parse_spec_file

                    doc = parse_spec_file(cf)
                    doc_id = str(doc.frontmatter.get("id") or "").strip()
                    if doc_id:
                        parsed_id = doc_id
                        break
                except Exception:
                    pass
        if rel_path.name.lower() == default_filename and rel_path.parent.name:
            clean_slug = rel_path.parent.name.lower().removeprefix(prefix_lower)
        elif parsed_id:
            clean_slug = parsed_id.lower().removeprefix(prefix_lower)
        else:
            clean_slug = rel_path.stem.lower().removeprefix(prefix_lower)
        resolved_id = parsed_id or f"{prefix}{clean_slug.upper()}"
        return resolved_id, clean_slug

    clean_slug = raw_clean.lower().removeprefix(prefix_lower)
    resolved_id = raw_clean.upper() if raw_clean.upper().startswith(prefix) else f"{prefix}{clean_slug.upper()}"
    return resolved_id, clean_slug


def build_design_spec(
    canonical_key: str,
    *,
    eidos_dir: Path,
    doc_id: str,
    slug: str,
    title: str,
    heading: str,
    container: str | None,
    client_id: str | None,
    extends: str | None,
    route: str,
    design_system: str,
    implements: str | None,
    platform: str,
) -> tuple[Path, Path, dict[str, Any], str]:
    """Build (dest_dir, dest_file, frontmatter, body) for a design-layer specification type."""
    dest_dir: Path
    dest_file: Path
    fm: dict[str, Any]
    body: str

    if canonical_key == "design-system":
        clean_sys = slug.removeprefix("ds-")
        dest_dir = eidos_dir / "design" / "systems" / clean_sys
        dest_file = dest_dir / "system.adoc"

        # Ensure baseline W3C tokens.json exists in dest_dir only when dest_file does not already exist
        if not dest_file.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            tokens_file = dest_dir / "tokens.json"
            if not tokens_file.exists():
                base_tokens = {
                    "color": {
                        "brand": {
                            "primary": {
                                "$value": "#0052cc",
                                "$type": "color",
                                "$description": f"Primary brand color for {heading}",
                            }
                        }
                    }
                }
                tokens_file.write_text(json.dumps(base_tokens, indent=2), encoding="utf-8")

        fm = {
            "type": "design/system",
            "schema": "schemas/v1/design/system.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "active",
        }
        if client_id:
            fm["brand_name"] = title
        if extends:
            fm["extends"] = extends
            fm["override_mode"] = "token_and_component"
        fm["tokens"] = {"spec_format": "w3c_design_tokens", "manifest": "tokens.json"}
        fm["theme_capabilities"] = {"color_modes": ["light", "dark"], "density_modes": ["comfortable"]}
        body = f"""= Design System: {heading}
:toc: left

== Foundations & W3C Tokens
This specification defines visual primitives, brand tokens, and inheritance rules for {heading}.
Tokens are defined in W3C DTCG format at `tokens.json`.
"""

    elif canonical_key == "ui-component":
        clean_comp = slug.removeprefix("uic-")
        raw_ds = (design_system or "DS-CORE").strip()
        resolved_ds_id, sys_slug = _resolve_design_entity_ref(raw_ds, "DS-", "system.adoc", eidos_dir)
        dest_dir = eidos_dir / "design" / "systems" / sys_slug / "components"
        dest_file = dest_dir / f"{clean_comp}.adoc"
        fm = {
            "type": "design/ui-component",
            "schema": "schemas/v1/design/ui-component.schema.yaml",
            "id": doc_id,
            "title": title,
            "design_system": resolved_ds_id,
        }
        if implements:
            fm["implements"] = implements
        fm.update(
            {
                "status": "draft",
                "category": "inputs",
                "variants": [
                    {"name": "primary", "description": "Default visual emphasis."},
                    {"name": "secondary", "description": "Subtle visual emphasis."},
                ],
                "states": ["default", "hover", "active", "focus", "disabled"],
                "accessibility": {
                    "wcag_level": "AA",
                    "aria_role": "button",
                    "keyboard_nav": "Enter and Space activate; Tab traverses focus.",
                },
                "test_bindings": {},
            }
        )
        body = f"""= UI Component: {heading}
:toc: left

== Component Contract
Describes variants, interactive states, accessibility contracts, and visual test bindings for {heading}.
"""

    elif canonical_key == "screen":
        clean_screen = slug.removeprefix("scr-")
        if client_id:
            _, client_slug = _resolve_design_entity_ref(client_id, "CLT-", "client.adoc", eidos_dir)
            dest_dir = eidos_dir / "design" / "clients" / client_slug / "screens"
        else:
            dest_dir = eidos_dir / "design" / "screens"
        dest_file = dest_dir / f"{clean_screen}.adoc"
        fm = {
            "type": "design/screen",
            "schema": "schemas/v1/design/screen.schema.yaml",
            "id": doc_id,
            "title": title,
            "route": route,
            "status": "active",
            "slots": {"main_content": "Primary view content"},
            "states": [
                {"name": "loading", "description": "Skeleton loader displayed during data fetch."},
                {"name": "active", "description": "Standard interactive screen state."},
                {"name": "error", "description": "Fallback error message if request fails."},
            ],
        }
        body = f"""= Screen: {heading}
:toc: left

== View Layout & State Machine
This specification defines the route, layout slots, and state transitions for {heading}.
"""

    elif canonical_key == "client":
        client_slug = slug.removeprefix("clt-")
        resolved_ds_id, _ = _resolve_design_entity_ref(
            (design_system or "DS-CORE").strip(), "DS-", "system.adoc", eidos_dir
        )
        dest_dir = eidos_dir / "design" / "clients" / client_slug
        dest_file = dest_dir / "client.adoc"
        fm = {
            "type": "design/client",
            "schema": "schemas/v1/design/client.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "active",
            "platform": platform,
            "design_system": resolved_ds_id,
        }
        if container:
            fm["container"] = container
        fm.update({"screens": [], "flows": []})
        body = f"""= Client: {heading}
:toc: left

== Client Identity & Platform Archetype
Describe the runtime platform, bound design system, and implementing container of this client surface.
"""

    elif canonical_key == "flow":
        flow_slug = slug.removeprefix("flow-")
        resolved_clt_id = ""
        if client_id:
            resolved_clt_id, client_slug = _resolve_design_entity_ref(client_id, "CLT-", "client.adoc", eidos_dir)
            dest_dir = eidos_dir / "design" / "clients" / client_slug / "flows"
        else:
            dest_dir = eidos_dir / "design" / "flows"
        dest_file = dest_dir / f"{flow_slug}.adoc"
        fm = {
            "type": "design/flow",
            "schema": "schemas/v1/design/flow.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "active",
            "flow_type": "client_internal" if resolved_clt_id else "cross_client",
            "steps": [{"step": 1, "title": "Entry point"}],
        }
        body = f"""= Flow: {heading}
:toc: left

== User Journey
Describe the ordered steps, screen transitions, and outcomes of this interaction flow.
"""

    else:
        raise ValueError(f"Unsupported design specification key '{canonical_key}'")

    return dest_dir, dest_file, fm, body
