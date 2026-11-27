"""Design engine: scaffolds Multi-Design Systems, W3C DTCG tokens, UI components, screens, clients, and flows."""

import re
from pathlib import Path
from typing import Any

import orjson

from eidos.core.workspace.assets import render_spec_template

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


_DS_ID_RE = re.compile(r"^DS-[A-Z0-9_-]+$")


def _resolve_and_validate_design_system(raw_ds: str, eidos_dir: Path) -> tuple[str, str]:
    """Resolve and validate a design system ID, slug, or .adoc path, rejecting path traversal and empty/malformed slugs."""
    cleaned = (raw_ds or "").strip()
    if not cleaned:
        raise ValueError("Invalid --design-system value: must be a non-empty DS-* ID, path, or slug.")
    resolved_ds_id, raw_sys_slug = _resolve_design_entity_ref(cleaned, "DS-", "system.adoc", eidos_dir)
    sys_slug = re.sub(r"[^a-z0-9_-]+", "-", raw_sys_slug.lower()).strip("-_")
    if not sys_slug or not _DS_ID_RE.fullmatch(resolved_ds_id):
        raise ValueError(
            f"Invalid --design-system value '{raw_ds}': could not resolve a valid DS-* identifier and slug."
        )
    return resolved_ds_id, sys_slug


def build_design_spec(
    canonical_key: str,
    *,
    eidos_dir: Path,
    doc_id: str,
    slug: str,
    title: str,
    heading: str,
    design_system: str = "DS-CORE",
    client: str | None = None,
) -> tuple[Path, Path, dict[str, Any], str]:
    """Build (dest_dir, dest_file, frontmatter, body) for a minimal design-layer specification blueprint."""
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
                tokens_file.write_text(
                    orjson.dumps(base_tokens, option=orjson.OPT_INDENT_2).decode("utf-8"),
                    encoding="utf-8",
                )

        fm = {
            "type": "design/system",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "tokens": {"spec_format": "w3c_design_tokens", "manifest": "tokens.json"},
        }
        body = render_spec_template("design/system", heading=heading)

    elif canonical_key == "ui-component":
        clean_comp = slug.removeprefix("uic-")
        resolved_ds_id, sys_slug = _resolve_and_validate_design_system(design_system, eidos_dir)
        dest_dir = eidos_dir / "design" / "systems" / sys_slug / "components"
        dest_file = dest_dir / f"{clean_comp}.adoc"
        fm = {
            "type": "design/ui-component",
            "id": doc_id,
            "title": title,
            "design_system": resolved_ds_id,
            "status": "draft",
        }
        body = render_spec_template("design/ui-component", heading=heading)

    elif canonical_key == "screen":
        raw_client = (client or "").strip()
        if not raw_client:
            raise ValueError(
                "Creating a 'design/screen' specification requires --client <CLT-ID|path|slug> "
                "so the screen is placed under eidos/design/clients/<client>/screens/<screen>.adoc."
            )
        _, raw_client_slug = _resolve_design_entity_ref(raw_client, "CLT-", "client.adoc", eidos_dir)
        client_slug = re.sub(r"[^a-z0-9_-]+", "-", raw_client_slug.lower()).strip("-_")
        if not client_slug:
            raise ValueError(f"Invalid --client value '{raw_client}': could not resolve a valid client slug.")
        clean_screen = slug.removeprefix("scr-")
        dest_dir = eidos_dir / "design" / "clients" / client_slug / "screens"
        dest_file = dest_dir / f"{clean_screen}.adoc"
        fm = {
            "type": "design/screen",
            "id": doc_id,
            "title": title,
            "route": f"/{clean_screen}",
            "status": "draft",
        }
        body = render_spec_template("design/screen", heading=heading)

    elif canonical_key == "client":
        client_slug = slug.removeprefix("clt-")
        resolved_ds_id, _ = _resolve_and_validate_design_system(design_system, eidos_dir)
        dest_dir = eidos_dir / "design" / "clients" / client_slug
        dest_file = dest_dir / "client.adoc"
        fm = {
            "type": "design/client",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "platform": "web",
            "design_system": resolved_ds_id,
        }
        body = render_spec_template("design/client", heading=heading)

    elif canonical_key == "flow":
        flow_slug = slug.removeprefix("flow-")
        dest_dir = eidos_dir / "design" / "flows"
        dest_file = dest_dir / f"{flow_slug}.adoc"
        fm = {
            "type": "design/flow",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "steps": [{"step": 1, "title": "Entry point"}],
        }
        body = render_spec_template("design/flow", heading=heading)

    else:
        raise ValueError(f"Unsupported design specification key '{canonical_key}'")

    return dest_dir, dest_file, fm, body
