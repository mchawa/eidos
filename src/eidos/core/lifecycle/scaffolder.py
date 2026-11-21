"""Lifecycle management, blueprint scaffolding, and deletion for Eidos living specifications (CMP-LIFECYCLE-SCAFFOLDER)."""

import contextlib
import os
import re
from pathlib import Path
from typing import Any

import orjson

from eidos.core.lifecycle.catalog import (
    VALID_SPEC_TYPES,
    _render_spec,
    _single_line,
    find_references_to_spec,
    find_spec_by_id_or_path,
    normalize_id,
)
from eidos.core.lifecycle.design_engine import DESIGN_SPEC_KEYS, build_design_spec
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import render_spec_template
from eidos.core.workspace.project import (
    _POLYGLOT_TEST_CODE_EXTS,
    STANDARD_EXCLUDED_DIRS,
    _is_test_dir_segment,
    extract_entity_paths,
    is_dotnet_bin_dir,
    match_path_pattern,
)


def _scan_live_specs_by_id(eidos_dir: Path) -> dict[str, tuple[Path, str, dict[str, Any]]]:
    """Scan live .adoc files under eidos_dir directly without warming or mutating ArchitecturalIndex."""
    if not eidos_dir.is_dir():
        return {}
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    resolved_root = eidos_dir.resolve()
    for p in sorted(eidos_dir.rglob("*.adoc")):
        if p.is_symlink() or not p.is_file():
            continue
        if p.name.startswith("_") or p.name.lower() == "readme.adoc":
            continue
        with contextlib.suppress(ValueError):
            rel_parts = p.resolve().relative_to(resolved_root).parts
            if any(part in (".cache", "deltas", "archive") for part in rel_parts):
                continue
        doc = parse_spec_file(p)
        if doc.doc_id and doc.doc_type and doc.doc_id != "SYS-UNINITIALIZED" and doc.doc_type != "delta/mutation":
            specs_by_id[doc.doc_id] = (p.resolve(), doc.doc_type, doc.frontmatter)
    return specs_by_id


def create_spec(
    eidos_dir: Path,
    doc_type_key: str,
    raw_id: str,
    title: str,
    *,
    client: str | None = None,
) -> Path:
    """Scaffold a minimal, schema-valid living specification blueprint across the 15 living specification types.

    SpecDeltas (DLT-*) are rejected here and must be created via ``create_delta`` / ``eidos delta create``.
    """
    type_key = doc_type_key.strip().lower().replace("_", "/")
    if type_key in ("delta", "mutation", "delta/mutation"):
        raise ValueError(
            "SpecDeltas (DLT-*) cannot be created via 'eidos spec create'. Use 'eidos delta create' instead."
        )

    key_aliases = {
        "system": "system",
        "product/system": "system",
        "architecture": "architecture",
        "tech/architecture": "architecture",
        "capability": "capability",
        "product/capability": "capability",
        "domain": "domain",
        "product/domain": "domain",
        "policy": "policy",
        "product/policy": "policy",
        "contract": "contract",
        "tech/contract": "contract",
        "container": "container",
        "tech/container": "container",
        "component": "component",
        "tech/component": "component",
        "persona": "persona",
        "product/persona": "persona",
        "exploration": "exploration",
        "product/exploration": "exploration",
        "design-system": "design-system",
        "design/system": "design-system",
        "ui-component": "ui-component",
        "ui/component": "ui-component",
        "design/ui-component": "ui-component",
        "screen": "screen",
        "design/screen": "screen",
        "client": "client",
        "design/client": "client",
        "flow": "flow",
        "design/flow": "flow",
    }
    canonical_key = key_aliases.get(type_key)
    if not canonical_key:
        supported = ", ".join(sorted(set(key_aliases.values())))
        raise ValueError(f"Unknown specification type '{doc_type_key}'. Supported types: {supported}")

    doc_id = normalize_id(canonical_key, raw_id)
    slug = doc_id.lower().replace("_", "-")
    heading = _single_line(title)
    fm: dict[str, Any]
    body: str

    specs_by_id = _scan_live_specs_by_id(eidos_dir)

    def _first_of_type(dtype: str, fallback: str) -> str:
        ids = sorted(i for i, (_, dt, _) in specs_by_id.items() if dt == dtype)
        return ids[0] if ids else fallback

    def _derive_project_stem() -> str:
        for dtype, pfx in (
            ("product/system", "SYS-"),
            ("product/domain", "DOM-"),
            ("tech/container", "CNT-"),
            ("design/system", "DS-"),
        ):
            ids = sorted(i for i, (_, dt, _) in specs_by_id.items() if dt == dtype)
            if ids:
                raw_stem = ids[0].upper().removeprefix(pfx)
                trimmed = re.sub(r"-\d+$", "", raw_stem) if dtype == "product/system" else raw_stem
                cleaned = re.sub(r"[^A-Z0-9_-]+", "-", (trimmed or raw_stem)).strip("-_")
                if cleaned:
                    return cleaned
        resolved_dir = eidos_dir.resolve()
        raw_name = resolved_dir.parent.name if resolved_dir.name == "eidos" else resolved_dir.name
        return re.sub(r"[^A-Z0-9]+", "-", raw_name.upper()).strip("-") or "APP"

    project_stem = _derive_project_stem()

    tokens_existed = False
    if canonical_key == "design-system":
        tokens_existed = (eidos_dir / "design" / "systems" / slug.removeprefix("ds-") / "tokens.json").exists()

    if canonical_key == "system":
        dest_dir = eidos_dir
        dest_file = dest_dir / "system.adoc"
        fm = {
            "type": "product/system",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "archetypes": ["service"],
        }
        body = render_spec_template("product/system", heading=heading)

    elif canonical_key == "architecture":
        dest_dir = eidos_dir / "architecture"
        dest_file = dest_dir / "system-architecture.adoc"
        existing_personas = sorted(pid for pid, (_, dt, _) in specs_by_id.items() if dt == "product/persona")
        fm = {
            "type": "tech/architecture",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "c4_context": {
                "actors": [existing_personas[0]] if existing_personas else [],
            },
        }
        body = render_spec_template("tech/architecture", heading=heading)

    elif canonical_key == "capability":
        existing_domains = sorted(did for did, (_, dt, _) in specs_by_id.items() if dt == "product/domain")
        resolved_domain = existing_domains[0] if existing_domains else f"DOM-{project_stem}"
        domain_slug = resolved_domain.lower().removeprefix("dom-")

        existing_cnts = sorted(cid for cid, (_, dt, _) in specs_by_id.items() if dt == "tech/container")
        existing_personas = sorted(pid for pid, (_, dt, _) in specs_by_id.items() if dt == "product/persona")

        exact_dom_dir = eidos_dir / "domains" / domain_slug
        if exact_dom_dir.is_dir():
            dest_dir = exact_dom_dir / "capabilities"
        else:
            domain_dirs = [d for d in eidos_dir.glob(f"domains/*{domain_slug}*") if d.is_dir()]
            dest_dir = domain_dirs[0] / "capabilities" if domain_dirs else exact_dom_dir / "capabilities"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "product/capability",
            "id": doc_id,
            "title": title,
            "domain": resolved_domain,
            "status": "draft",
        }
        if existing_personas:
            fm["business"] = {"personas": [existing_personas[0]]}
        fm["technical"] = {
            "containers": [existing_cnts[0]] if existing_cnts else [],
        }
        body = render_spec_template("product/capability", heading=heading)

    elif canonical_key == "domain":
        dom_slug = slug.removeprefix("dom-")
        dest_dir = eidos_dir / "domains" / dom_slug
        dest_file = dest_dir / "domain.adoc"
        fm = {
            "type": "product/domain",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "classification": "core",
        }
        body = render_spec_template("product/domain", heading=heading)

    elif canonical_key == "policy":
        dest_dir = eidos_dir / "policies"
        dest_file = dest_dir / f"{slug}.adoc"
        inv_id = f"INV-{doc_id}"
        fm = {
            "type": "product/policy",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "category": "governance",
            "invariants": [
                {
                    "id": inv_id,
                    "rule": f"Normative rule for {heading}.",
                }
            ],
        }
        body = render_spec_template("product/policy", heading=heading)

    elif canonical_key == "contract":
        dest_dir = eidos_dir / "architecture" / "contracts"
        dest_file = dest_dir / f"{slug}.adoc"
        cnt_owner = _first_of_type("tech/container", f"CNT-{project_stem}")
        fm = {
            "type": "tech/contract",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "protocol": "cli_args",
            "container": cnt_owner,
        }
        body = render_spec_template("tech/contract", heading=heading)

    elif canonical_key == "container":
        dest_dir = eidos_dir / "architecture" / "containers"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "tech/container",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "container_type": "service",
            "runtime": "Application Runtime",
            "source_path": "src",
        }
        body = render_spec_template("tech/container", heading=heading)

    elif canonical_key == "component":
        # INV-COMPONENT-CENTRALIZATION-MANDATE: dedicated components live in eidos/architecture/components/
        dest_dir = eidos_dir / "architecture" / "components"
        dest_file = dest_dir / f"{slug.removeprefix('cmp-')}.adoc"
        parent_cnt = _first_of_type("tech/container", f"CNT-{project_stem}")
        fm = {
            "type": "tech/component",
            "id": doc_id,
            "title": title,
            "container": parent_cnt,
            "status": "draft",
            "source_path": "src",
        }
        body = render_spec_template("tech/component", heading=heading)

    elif canonical_key == "persona":
        dest_dir = eidos_dir / "personas"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "product/persona",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "role_type": "end_user",
        }
        body = render_spec_template("product/persona", heading=heading)

    elif canonical_key == "exploration":
        dest_dir = eidos_dir / "explorations"
        dest_file = dest_dir / f"{slug}.adoc"
        archive_dir = dest_dir / "archive"
        if archive_dir.is_dir():
            for arch_p in archive_dir.glob("*.adoc"):
                if arch_p.stem.lower().replace("_", "-") == slug:
                    raise FileExistsError(f"Specification document already exists in archive at: {arch_p}")
        if doc_id in specs_by_id:
            existing_p = specs_by_id[doc_id][0]
            raise FileExistsError(f"Specification document '{doc_id}' already exists at: {existing_p}")
        fm = {
            "type": "product/exploration",
            "id": doc_id,
            "title": title,
            "status": "proposed",
        }
        body = render_spec_template("product/exploration", heading=heading)

    elif canonical_key in DESIGN_SPEC_KEYS:
        resolved_design_system = _first_of_type("design/system", f"DS-{project_stem}")
        dest_dir, dest_file, fm, body = build_design_spec(
            canonical_key,
            eidos_dir=eidos_dir,
            doc_id=doc_id,
            slug=slug,
            title=title,
            heading=heading,
            design_system=resolved_design_system,
            client=client,
        )

    else:
        raise ValueError(f"Unsupported canonical key '{canonical_key}'")

    content = _render_spec(fm, body)

    if dest_file.exists():
        raise FileExistsError(f"Specification document already exists at: {dest_file}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file.write_text(content, encoding="utf-8")

    # Validate immediately to ensure document is syntactically conforming
    doc = parse_spec_file(dest_file)
    validator = SpecValidator()
    errors = validator.validate_document(doc, root_dir=eidos_dir)
    if errors:
        # Roll back newly created file
        dest_file.unlink()
        if canonical_key == "design-system" and not tokens_existed:
            tokens_f = dest_dir / "tokens.json"
            if tokens_f.exists():
                with contextlib.suppress(OSError):
                    tokens_f.unlink()
            if dest_dir.exists() and not any(dest_dir.iterdir()):
                with contextlib.suppress(OSError):
                    dest_dir.rmdir()
        err_msg = "; ".join(errors)
        raise ValueError(f"Generated specification failed validation: {err_msg}")

    return dest_file


def delete_spec(eidos_dir: Path, target: str, force: bool = False) -> tuple[bool, str, list[Path]]:
    """Safely delete a living specification document with cross-reference safety checks (rejecting SpecDeltas)."""
    try:
        file_path = find_spec_by_id_or_path(eidos_dir, target)
    except ValueError as exc:
        return False, str(exc), []
    if not file_path:
        return False, f"Specification '{target}' not found under '{eidos_dir}'.", []

    doc = parse_spec_file(file_path)
    doc_id = doc.doc_id or file_path.stem
    if doc.doc_type == "delta/mutation":
        return (
            False,
            f"Document '{doc_id}' is a SpecDelta (delta/mutation). Use 'eidos delta delete {target}' instead.",
            [],
        )

    references = find_references_to_spec(eidos_dir, doc_id, file_path)
    if references and not force:
        refs_str = ", ".join(p.name for p in references[:5])
        return (
            False,
            f"Cannot delete '{doc_id}': referenced by {len(references)} specification(s) ({refs_str}). Use --force to delete anyway.",
            references,
        )

    file_path.unlink()

    # Clean up empty parent directory if not a top-level dir
    parent = file_path.parent
    if parent != eidos_dir and parent.exists() and not any(parent.iterdir()):
        with contextlib.suppress(OSError):
            parent.rmdir()

    return True, f"Successfully deleted specification '{doc_id}' ({file_path.name}).", references


def list_specs(
    eidos_dir: Path,
    type_filter: str | None = None,
    status_filter: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """List all living specifications (excluding SpecDeltas) under eidos_dir."""
    type_filter_clean = type_filter.strip().lower().replace("_", "/") if type_filter else None
    if type_filter_clean in ("delta", "mutation", "delta/mutation"):
        raise ValueError("SpecDeltas (DLT-*) are managed via 'eidos delta'. Use 'eidos delta list' instead.")

    if not eidos_dir.exists():
        return []

    from eidos.core.index.graph_index import get_architectural_index

    eidos_resolved = eidos_dir.resolve()
    idx = get_architectural_index(eidos_dir=eidos_dir, force_new=True)
    all_entities = idx.get_entities()
    results = []

    raw_type_clean = type_filter.strip().lower() if type_filter else None
    canonical_type = VALID_SPEC_TYPES.get(raw_type_clean) if raw_type_clean else None
    status_filter_clean = status_filter.strip().lower() if status_filter else None

    def _matches_type(doc_type: str, eid: str) -> bool:
        if not raw_type_clean:
            return True
        if canonical_type:
            return doc_type.lower() == canonical_type
        return raw_type_clean in doc_type.lower() or raw_type_clean in eid.lower()

    for ent in all_entities:
        doc_type = ent.get("type", "")
        eid = ent.get("id", "")
        if doc_type in ("invariant", "delta/mutation"):
            continue

        status = ent.get("status") or "active"
        raw_fp = Path(ent.get("file_path", ""))
        if raw_fp.is_absolute():
            cand_p = raw_fp
        elif (idx.root / raw_fp).exists():
            cand_p = idx.root / raw_fp
        elif (eidos_resolved / raw_fp).exists():
            cand_p = eidos_resolved / raw_fp
        else:
            cand_p = idx.root / raw_fp
        resolved_p = cand_p.resolve() if cand_p.exists() else cand_p
        rel_p = resolved_p.relative_to(eidos_resolved) if resolved_p.is_relative_to(eidos_resolved) else raw_fp
        is_archived = "archive" in rel_p.parts
        if is_archived and not include_archived:
            continue

        if _matches_type(doc_type, eid) and (not status_filter_clean or str(status).lower() == status_filter_clean):
            results.append(
                {
                    "id": eid,
                    "type": doc_type,
                    "title": ent.get("title") or "Untitled",
                    "status": status,
                    "is_archived": is_archived,
                    "path": resolved_p,
                    "relative_path": rel_p,
                }
            )

    return sorted(results, key=lambda x: str(x.get("id", "")))


def _resolve_schema_node(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve top-level $ref to #/$defs/... if present while preserving sibling annotations."""
    if not isinstance(node, dict):
        return {}
    current = dict(node)
    seen: set[str] = set()
    while isinstance(current.get("$ref"), str) and str(current["$ref"]).startswith("#/$defs/"):
        def_key = str(current["$ref"]).removeprefix("#/$defs/")
        if def_key in seen:
            break
        seen.add(def_key)
        resolved = dict(defs.get(def_key) or {})
        for k, v in current.items():
            if k != "$ref":
                resolved[k] = v
        current = resolved
    return current


def _extract_id_prefix(id_pattern: str) -> str:
    """Extract the canonical entity ID prefix (e.g. 'CAP-') from a schema regex pattern."""
    cleaned = id_pattern.strip().lstrip("^").lstrip("(")
    if "-" in cleaned:
        prefix_token = cleaned.split("-", 1)[0]
        if prefix_token.isalnum():
            return f"{prefix_token.upper()}-"
    return ""


def _derive_short_type(canonical_type: str) -> str:
    """Derive the CLI short type alias from a canonical schema type string."""
    if canonical_type == "design/system":
        return "design-system"
    if canonical_type == "delta/mutation":
        return "delta"
    return canonical_type.split("/")[-1]


def _synthesize_example_value(
    key: str,
    prop_schema: dict[str, Any],
    defs: dict[str, Any],
    *,
    id_prefix: str = "",
    include_optional: bool = False,
) -> Any:
    """Synthesize a schema-conforming example value directly from a v1 JSON Schema property definition."""
    resolved = _resolve_schema_node(prop_schema, defs)

    if "const" in resolved:
        return resolved["const"]
    if isinstance(resolved.get("examples"), list) and resolved["examples"]:
        return resolved["examples"][0]
    if "default" in resolved:
        return resolved["default"]
    if isinstance(resolved.get("enum"), list) and resolved["enum"]:
        enums = resolved["enum"]
        return "active" if "active" in enums else enums[0]

    ref_meta = resolved.get("x-eidos-ref")
    if isinstance(ref_meta, dict) and isinstance(ref_meta.get("prefixes"), list) and ref_meta["prefixes"]:
        first_pfx = str(ref_meta["prefixes"][0])
        chosen_pfx = "CNT-" if "CNT-" in ref_meta["prefixes"] else first_pfx
        return f"{chosen_pfx}EXAMPLE-001"

    prop_type = resolved.get("type")
    if not prop_type and "oneOf" in resolved:
        for branch in resolved.get("oneOf") or []:
            if isinstance(branch, dict):
                return _synthesize_example_value(key, branch, defs, id_prefix=id_prefix)

    if prop_type == "string":
        pattern = str(resolved.get("pattern") or "")
        pfx = _extract_id_prefix(pattern) or (id_prefix if key == "id" else "")
        if pfx:
            return f"{pfx}EXAMPLE-001"
        if key == "test_binding":
            return "tests/test_example.py::test_invariant"
        if key == "version":
            return "1.0.0"
        if key == "base_commit":
            return "a1b2c3d"
        if key == "route":
            return "/example"
        if key in ("source_path", "specification_file"):
            return "src/example"
        if key == "manifest":
            return "tokens.json"
        if pattern == "^[a-z0-9_-]+$":
            return "core"
        return f"Example {key.replace('_', ' ')}"

    if prop_type == "integer":
        return 1

    if prop_type == "boolean":
        return True

    if prop_type == "array":
        items_schema = resolved.get("items")
        if isinstance(items_schema, dict):
            return [_synthesize_example_value(key, items_schema, defs, include_optional=False)]
        return []

    if prop_type == "object":
        sub_props = resolved.get("properties")
        if isinstance(sub_props, dict) and sub_props:
            req_keys = list(resolved.get("required") or [])
            keys_to_emit = list(sub_props.keys()) if (include_optional or not req_keys) else req_keys
            return {
                sk: _synthesize_example_value(sk, sub_props[sk], defs, include_optional=False)
                for sk in keys_to_emit
                if sk in sub_props
            }
        return {"example": "https://example.com"}

    return f"example_{key}"


def _describe_property_schema(prop_schema: dict[str, Any], defs: dict[str, Any], is_required: bool) -> dict[str, Any]:
    """Extract a concise, agent-friendly descriptor for a single frontmatter property."""
    resolved = _resolve_schema_node(prop_schema, defs)
    prop_type = resolved.get("type")
    if not prop_type and "oneOf" in resolved:
        branches = [_resolve_schema_node(b, defs) for b in resolved.get("oneOf", []) if isinstance(b, dict)]
        branch_types: list[str] = []
        for b in branches:
            bt = b.get("type")
            if bt == "array" and isinstance(b.get("items"), dict):
                it = _resolve_schema_node(b["items"], defs).get("type", "any")
                branch_types.append(f"array<{it}>")
            elif isinstance(bt, str):
                branch_types.append(bt)
        prop_type = " | ".join(branch_types) if branch_types else "oneOf"

    desc_info: dict[str, Any] = {
        "required": is_required,
        "type": prop_type or "any",
        "description": str(resolved.get("description") or "").strip(),
    }
    if "const" in resolved:
        desc_info["const"] = resolved["const"]
    if "enum" in resolved:
        desc_info["enum"] = list(resolved["enum"])
    if "pattern" in resolved:
        desc_info["pattern"] = resolved["pattern"]
    if isinstance(resolved.get("examples"), list) and resolved["examples"]:
        desc_info["examples"] = list(resolved["examples"])
    ref_meta = resolved.get("x-eidos-ref")
    if isinstance(ref_meta, dict) and ref_meta.get("prefixes"):
        desc_info["ref_prefixes"] = list(ref_meta["prefixes"])

    if prop_type == "object":
        sub_props = resolved.get("properties") or {}
        if isinstance(sub_props, dict) and sub_props:
            desc_info["required_keys"] = list(resolved.get("required") or [])
            desc_info["allowed_keys"] = list(sub_props.keys())
            desc_info["additional_properties"] = resolved.get("additionalProperties", True)
    elif prop_type == "array" and isinstance(resolved.get("items"), dict):
        items_resolved = _resolve_schema_node(resolved["items"], defs)
        items_type = items_resolved.get("type")
        if not items_type and "oneOf" in items_resolved:
            items_type = "string | object"
        desc_info["items_type"] = items_type or "any"
        if isinstance(items_resolved.get("x-eidos-ref"), dict) and items_resolved["x-eidos-ref"].get("prefixes"):
            desc_info["ref_prefixes"] = list(items_resolved["x-eidos-ref"]["prefixes"])
        item_props = items_resolved.get("properties") or {}
        if isinstance(item_props, dict) and item_props:
            desc_info["item_required_keys"] = list(items_resolved.get("required") or [])
            desc_info["item_allowed_keys"] = list(item_props.keys())
            desc_info["item_additional_properties"] = items_resolved.get("additionalProperties", True)

    return desc_info


def get_spec_schema_info(doc_type_key: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    """Load and inspect bundled v1 YAML schemas directly from ``assets/schemas/v1/``.

    If ``doc_type_key`` is None, returns a summary list across all 16 bundled schemas.
    Otherwise resolves ``doc_type_key`` by short type alias (e.g. ``"capability"``, ``"container"``, ``"delta"``)
    or canonical type (e.g. ``"product/capability"``, ``"tech/container"``, ``"delta/mutation"``) and returns
    detailed property constraints and a schema-synthesized example YAML frontmatter block.
    """
    from eidos.core.spec.models import TYPE_TO_SCHEMA_NAME
    from eidos.core.spec.parser import safe_dump_yaml

    validator = SpecValidator()

    def _build_single_schema_info(canonical_type: str) -> dict[str, Any]:
        schema_rel = TYPE_TO_SCHEMA_NAME[canonical_type]
        raw_schema = validator._schema_cache.get(schema_rel) or {}
        defs = raw_schema.get("$defs") if isinstance(raw_schema.get("$defs"), dict) else {}

        merged_required: list[str] = []
        merged_props: dict[str, Any] = {}

        # Flatten allOf branches if present
        for branch in raw_schema.get("allOf") or []:
            if isinstance(branch, dict):
                resolved_branch = _resolve_schema_node(branch, defs)
                for req_k in resolved_branch.get("required") or []:
                    if req_k not in merged_required:
                        merged_required.append(req_k)
                branch_props = resolved_branch.get("properties") or {}
                if isinstance(branch_props, dict):
                    merged_props.update(branch_props)

        for req_k in raw_schema.get("required") or []:
            if req_k not in merged_required:
                merged_required.append(req_k)

        own_props = raw_schema.get("properties") or {}
        if isinstance(own_props, dict):
            merged_props.update(own_props)

        priority_keys = ["type", "id", "title", "status"]
        ordered_keys: list[str] = [k for k in priority_keys if k in merged_props]
        for k in merged_required:
            if k not in ordered_keys and k in merged_props:
                ordered_keys.append(k)
        for k in merged_props:
            if k not in ordered_keys:
                ordered_keys.append(k)

        merged_required = [k for k in ordered_keys if k in merged_required]
        optional_keys = [k for k in ordered_keys if k not in merged_required]
        prop_descriptors: dict[str, dict[str, Any]] = {
            k: _describe_property_schema(merged_props[k], defs, k in merged_required) for k in ordered_keys
        }

        status_desc = prop_descriptors.get("status") or {}
        status_values = status_desc.get("enum") or []
        id_desc = prop_descriptors.get("id") or {}
        id_pattern = str(id_desc.get("pattern") or "")
        id_prefix = _extract_id_prefix(id_pattern)
        short_type = _derive_short_type(canonical_type)

        # Synthesize example frontmatter directly from the loaded schema properties
        example_dict: dict[str, Any] = {}
        keys_for_example = list(merged_required)
        if "status" in merged_props and "status" not in keys_for_example:
            keys_for_example.insert(min(3, len(keys_for_example)), "status")
        for k in keys_for_example:
            example_dict[k] = _synthesize_example_value(
                k,
                merged_props[k],
                defs,
                id_prefix=id_prefix,
                include_optional=(k in ("technical", "c4_context")),
            )

        yaml_body = safe_dump_yaml(example_dict).strip()
        example_fm_str = f"---\n{yaml_body}\n---"

        return {
            "type": canonical_type,
            "short_type": short_type,
            "schema_path": f"assets/schemas/v1/{schema_rel}",
            "id_prefix": id_prefix,
            "id_pattern": id_pattern,
            "description": str(raw_schema.get("description") or "").strip(),
            "additional_properties": bool(
                raw_schema.get("additionalProperties", False)
                if "additionalProperties" in raw_schema
                else raw_schema.get("unevaluatedProperties", False)
            ),
            "status_values": status_values,
            "required": merged_required,
            "optional": optional_keys,
            "properties": prop_descriptors,
            "example_frontmatter": example_fm_str,
        }

    all_schemas = [_build_single_schema_info(ctype) for ctype in TYPE_TO_SCHEMA_NAME]

    if doc_type_key is None or not doc_type_key.strip():
        return all_schemas

    cleaned = doc_type_key.strip()
    lower_key = cleaned.lower().replace("_", "-")
    canonical_type = VALID_SPEC_TYPES.get(lower_key) or VALID_SPEC_TYPES.get(cleaned.lower())

    by_canonical = {s["type"]: s for s in all_schemas}
    if canonical_type and canonical_type in by_canonical:
        return by_canonical[canonical_type]

    supported = ", ".join(sorted(s["short_type"] for s in all_schemas))
    raise ValueError(f"Unknown specification schema type '{doc_type_key}'. Supported types: {supported}")


def _slugify_entity_id(prefix: str, raw_name: str, seen_ids: set[str]) -> str:
    """Build a unique uppercase Eidos ID (e.g. CNT-ORDERS-API or CTR-ORDERS-OPENAPI)."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", raw_name.strip()).strip("-").upper() or "MODULE"
    base = f"{prefix}-{stem}"
    candidate = base
    suffix = 2
    while candidate in seen_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    seen_ids.add(candidate)
    return candidate


def _is_source_path_mapped(candidate_path: str, owned_patterns: list[str]) -> bool:
    """Return True when candidate_path is covered by (or encloses) an active CNT-*/CMP-* source_path pattern."""
    norm = candidate_path.strip().replace("\\", "/").strip("/")
    if not norm or norm == ".":
        return bool(owned_patterns)
    for pat in owned_patterns:
        norm_pat = pat.strip().replace("\\", "/").strip("/")
        if not norm_pat:
            continue
        if (
            norm_pat == norm
            or norm.startswith(f"{norm_pat}/")
            or norm_pat.startswith(f"{norm}/")
            or match_path_pattern(norm, norm_pat)
            or match_path_pattern(f"{norm}/index", norm_pat)
        ):
            return True
    return False


def _detect_contract_protocol(filename: str) -> str | None:
    """Classify a candidate interface specification filename into its canonical tech/contract protocol."""
    lower = filename.lower()
    if lower.endswith(".proto"):
        return "protobuf"
    if lower.endswith((".graphql", ".gql")):
        return "graphql"
    if lower.endswith((".yaml", ".yml", ".json")):
        if "openapi" in lower or "swagger" in lower:
            return "openapi"
        if "asyncapi" in lower:
            return "asyncapi"
        if "openrpc" in lower:
            return "json_rpc_stdio"
    return None


def _rel_dir_str(cur: Path, root: Path) -> str:
    rel = cur.relative_to(root).as_posix()
    return rel or "."


def _preferred_source_path(cur: Path, root: Path, sub_candidates: tuple[str, ...] = ("src",)) -> str:
    rel_dir = _rel_dir_str(cur, root)
    for sub in sub_candidates:
        if (cur / sub).is_dir():
            return sub if rel_dir == "." else f"{rel_dir}/{sub}"
    return rel_dir


def _inspect_manifest_container(cur: Path, root: Path, filenames: list[str]) -> list[dict[str, str]]:
    """Extract deterministic container candidates from build/package manifests in a single directory."""
    found: list[dict[str, str]] = []
    rel_dir = _rel_dir_str(cur, root)
    dir_label = root.name if rel_dir == "." else cur.name

    for fn in sorted(filenames):
        lower = fn.lower()
        manifest_rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
        full = cur / fn

        if lower.endswith((".csproj", ".fsproj")):
            stem = Path(fn).stem
            if _is_test_dir_segment(stem) or _is_test_dir_segment(cur.name):
                continue
            text = full.read_text(encoding="utf-8", errors="ignore")
            is_web = "microsoft.net.sdk.web" in text.lower() or (cur / "Controllers").is_dir()
            is_exe = "<outputtype>exe</outputtype>" in text.lower()
            ctype = "service" if is_web else ("cli" if is_exe else "library")
            runtime = ".NET / F#" if lower.endswith(".fsproj") else ".NET / C#"
            found.append(
                {
                    "title": stem,
                    "runtime": runtime,
                    "container_type": ctype,
                    "source_path": rel_dir,
                    "manifest": manifest_rel,
                }
            )
            continue

        if fn == "pyproject.toml":
            text = full.read_text(encoding="utf-8", errors="ignore")
            m_name = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            title = m_name.group(1) if m_name else dir_label
            lower_txt = text.lower()
            if "[project.scripts]" in lower_txt or "[tool.poetry.scripts]" in lower_txt:
                ctype = "cli"
            elif any(k in lower_txt for k in ("fastapi", "flask", "django", "uvicorn", "litestar")):
                ctype = "service"
            else:
                ctype = "library"
            found.append(
                {
                    "title": title,
                    "runtime": "Python",
                    "container_type": ctype,
                    "source_path": _preferred_source_path(cur, root, ("src",)),
                    "manifest": manifest_rel,
                }
            )
            continue

        if fn == "package.json":
            try:
                pkg = orjson.loads(full.read_bytes())
            except orjson.JSONDecodeError:
                continue
            if not isinstance(pkg, dict):
                continue
            if "workspaces" in pkg and not any((cur / d).is_dir() for d in ("src", "app", "pages")):
                continue
            raw_name = str(pkg.get("name") or dir_label).split("/")[-1]
            deps: set[str] = set()
            for dep_key in ("dependencies", "devDependencies"):
                val = pkg.get(dep_key)
                if isinstance(val, dict):
                    deps.update(str(k).lower() for k in val)
            is_ts = (cur / "tsconfig.json").is_file() or "typescript" in deps
            if deps & {"react", "vue", "svelte", "next", "vite", "@angular/core"}:
                ctype = "webapp"
            elif pkg.get("bin"):
                ctype = "cli"
            elif deps & {"express", "@nestjs/core", "fastify", "koa", "hono"}:
                ctype = "service"
            else:
                ctype = "library"
            found.append(
                {
                    "title": raw_name,
                    "runtime": "TypeScript / Node.js" if is_ts else "Node.js",
                    "container_type": ctype,
                    "source_path": _preferred_source_path(cur, root, ("src", "app")),
                    "manifest": manifest_rel,
                }
            )
            continue

        if fn == "go.mod":
            text = full.read_text(encoding="utf-8", errors="ignore")
            m_mod = re.search(r"^\s*module\s+(\S+)", text, re.MULTILINE)
            title = m_mod.group(1).rstrip("/").split("/")[-1] if m_mod else dir_label
            ctype = "service" if (cur / "cmd").is_dir() else "library"
            found.append(
                {
                    "title": title,
                    "runtime": "Go",
                    "container_type": ctype,
                    "source_path": rel_dir,
                    "manifest": manifest_rel,
                }
            )
            continue

        if fn == "Cargo.toml":
            text = full.read_text(encoding="utf-8", errors="ignore")
            if "[package]" not in text:
                continue
            m_name = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            title = m_name.group(1) if m_name else dir_label
            ctype = "cli" if (cur / "src" / "main.rs").is_file() else "library"
            found.append(
                {
                    "title": title,
                    "runtime": "Rust",
                    "container_type": ctype,
                    "source_path": _preferred_source_path(cur, root, ("src",)),
                    "manifest": manifest_rel,
                }
            )
            continue

        if fn in ("pom.xml", "build.gradle", "build.gradle.kts"):
            text = full.read_text(encoding="utf-8", errors="ignore")
            lower_txt = text.lower()
            is_kt = fn.endswith(".kts") or (cur / "src" / "main" / "kotlin").is_dir()
            ctype = "service" if any(k in lower_txt for k in ("spring-boot", "quarkus", "micronaut")) else "library"
            found.append(
                {
                    "title": dir_label,
                    "runtime": "Kotlin / JVM" if is_kt else "Java / JVM",
                    "container_type": ctype,
                    "source_path": _preferred_source_path(cur, root, ("src/main", "src")),
                    "manifest": manifest_rel,
                }
            )

    return found


def discover_repository_blueprint(
    repo_root: Path,
    eidos_dir: Path | None = None,
) -> dict[str, Any]:
    """Deterministically scan a polyglot repository to propose CNT-* containers, CTR-* contracts, test roots, and unmapped source_path gaps.

    Zero AI calls (read-only static filesystem inspection). Prunes standard excluded directories,
    dot-directories, the specification root, and .NET bin/obj build outputs.
    """
    root = repo_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository directory '{repo_root}' not found.")
    spec_dir = (eidos_dir if eidos_dir is not None else root / "eidos").resolve()

    owned_source_patterns: list[str] = []
    mapped_contract_patterns: list[str] = []
    if spec_dir.is_dir():
        for _eid, (_p, doc_type, fm) in _scan_live_specs_by_id(spec_dir).items():
            if str(fm.get("status", "active")).lower() in ("deprecated", "retired"):
                continue
            if doc_type in ("tech/container", "tech/component"):
                owned_source_patterns.extend(extract_entity_paths(fm))
            elif doc_type == "tech/contract":
                mapped_contract_patterns.extend(extract_entity_paths(fm))

    containers: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    test_roots: list[str] = []
    code_top_dirs: set[str] = set()
    seen_cnt_ids: set[str] = set()
    seen_ctr_ids: set[str] = set()
    skip_top_non_code = {"docs", "assets", "eidos", "scripts", "examples", "samples"}

    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        cur_rel = _rel_dir_str(cur, root)

        # Record test directories and avoid treating test projects as production containers
        pruned_dirs: list[str] = []
        for d in sorted(dirnames):
            child = cur / d
            if (
                d in STANDARD_EXCLUDED_DIRS
                or d.startswith(".")
                or child.resolve() == spec_dir
                or is_dotnet_bin_dir(child)
            ):
                continue
            if _is_test_dir_segment(d, allow_root_names=(cur == root)):
                test_rel = d if cur_rel == "." else f"{cur_rel}/{d}"
                test_roots.append(test_rel)
                continue
            pruned_dirs.append(d)
        dirnames[:] = pruned_dirs

        for item in _inspect_manifest_container(cur, root, filenames):
            sp = item["source_path"]
            containers.append(
                {
                    "suggested_id": _slugify_entity_id("CNT", item["title"], seen_cnt_ids),
                    "title": item["title"],
                    "runtime": item["runtime"],
                    "container_type": item["container_type"],
                    "source_path": sp,
                    "manifest": item["manifest"],
                    "already_mapped": _is_source_path_mapped(sp, owned_source_patterns),
                }
            )

        for fn in sorted(filenames):
            rel_file = fn if cur_rel == "." else f"{cur_rel}/{fn}"
            protocol = _detect_contract_protocol(fn)
            if protocol is not None:
                stem = Path(fn).stem
                label = f"{cur.name}-{stem}" if cur_rel != "." and stem.lower() in ("openapi", "swagger", "schema", "asyncapi") else stem
                already_mapped = any(match_path_pattern(rel_file, pat) for pat in mapped_contract_patterns)
                contracts.append(
                    {
                        "suggested_id": _slugify_entity_id("CTR", label, seen_ctr_ids),
                        "protocol": protocol,
                        "specification_file": rel_file,
                        "already_mapped": already_mapped,
                    }
                )
            elif Path(fn).suffix.lower() in _POLYGLOT_TEST_CODE_EXTS and cur_rel != ".":
                top_seg = cur_rel.split("/", 1)[0]
                if top_seg.lower() not in skip_top_non_code:
                    code_top_dirs.add(top_seg)

    unmapped_roots_set: set[str] = set()
    for cnt in containers:
        sp = cnt["source_path"]
        if not cnt["already_mapped"] and sp != ".":
            unmapped_roots_set.add(sp)
    for top_dir in sorted(code_top_dirs):
        if not _is_source_path_mapped(top_dir, owned_source_patterns) and not any(
            r == top_dir or r.startswith(f"{top_dir}/") for r in unmapped_roots_set
        ):
            unmapped_roots_set.add(top_dir)

    test_roots_sorted = sorted(set(test_roots))
    unmapped_code_roots = sorted(unmapped_roots_set)
    return {
        "repo_root": str(root),
        "eidos_dir": str(spec_dir),
        "eidos_initialized": spec_dir.is_dir(),
        "containers": containers,
        "contracts": contracts,
        "test_roots": test_roots_sorted,
        "unmapped_code_roots": unmapped_code_roots,
        "summary": {
            "containers_found": len(containers),
            "containers_unmapped": sum(1 for c in containers if not c["already_mapped"]),
            "contracts_found": len(contracts),
            "contracts_unmapped": sum(1 for c in contracts if not c["already_mapped"]),
            "test_roots_found": len(test_roots_sorted),
            "unmapped_code_roots_found": len(unmapped_code_roots),
        },
    }
