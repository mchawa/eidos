"""Lifecycle management, blueprint scaffolding, C4 component promotion/inlining,
and deletion for Eidos living specifications (CMP-LIFECYCLE-SCAFFOLDER)."""

import contextlib
from pathlib import Path
from typing import Any

from eidos.core.lifecycle.catalog import (
    VALID_SPEC_TYPES,
    _build_spec_catalog,
    _mutate_frontmatter_components,
    _render_spec,
    _single_line,
    find_references_to_spec,
    find_spec_by_id_or_path,
    normalize_id,
)
from eidos.core.lifecycle.design_engine import DESIGN_SPEC_KEYS, build_design_spec
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import render_spec_template


def create_spec(
    eidos_dir: Path,
    doc_type_key: str,
    raw_id: str,
    title: str,
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

    def _catalog() -> dict[str, tuple[Path, str, dict[str, Any]]]:
        specs_by_id, _, _ = _build_spec_catalog(eidos_dir)
        return specs_by_id

    def _first_of_type(dtype: str, fallback: str) -> str:
        ids = sorted(i for i, (_, dt, _) in _catalog().items() if dt == dtype)
        return ids[0] if ids else fallback

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
        actor_ref = _first_of_type("product/persona", "PER-USER")
        fm = {
            "type": "tech/architecture",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "c4_context": {
                "actors": [actor_ref],
            },
        }
        body = render_spec_template("tech/architecture", heading=heading)

    elif canonical_key == "capability":
        specs_by_id = _catalog()
        existing_domains = sorted(did for did, (_, dt, _) in specs_by_id.items() if dt == "product/domain")
        resolved_domain = existing_domains[0] if existing_domains else "DOM-CORE"
        domain_slug = resolved_domain.lower().removeprefix("dom-")

        existing_cnts = sorted(cid for cid, (_, dt, _) in specs_by_id.items() if dt == "tech/container")
        cnt_ref = existing_cnts[0] if existing_cnts else "CNT-CORE"

        existing_personas = sorted(pid for pid, (_, dt, _) in specs_by_id.items() if dt == "product/persona")
        persona_ref = existing_personas[0] if existing_personas else "PER-USER"

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
            "business": {
                "personas": [persona_ref],
            },
            "technical": {
                "containers": [cnt_ref],
            },
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
        cnt_owner = _first_of_type("tech/contract_cnt", _first_of_type("tech/container", "CNT-CORE"))
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
        parent_cnt = _first_of_type("tech/container", "CNT-CORE")
        responsibility = f"Responsibility of {heading}."
        fm = {
            "type": "tech/component",
            "id": doc_id,
            "title": title,
            "container": parent_cnt,
            "status": "draft",
            "responsibility": responsibility,
            "source_path": "src",
        }
        body = render_spec_template(
            "tech/component",
            heading=heading,
            responsibility=f"Describe the in-process logic this C4 Level 3 component encapsulates within `{parent_cnt}`.",
        )

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
        if doc_id in _catalog():
            existing_p = _catalog()[doc_id][0]
            raise FileExistsError(f"Specification document '{doc_id}' already exists at: {existing_p}")
        fm = {
            "type": "product/exploration",
            "id": doc_id,
            "title": title,
            "status": "proposed",
        }
        body = render_spec_template("product/exploration", heading=heading)

    elif canonical_key in DESIGN_SPEC_KEYS:
        resolved_design_system = _first_of_type("design/system", "DS-CORE")
        dest_dir, dest_file, fm, body = build_design_spec(
            canonical_key,
            eidos_dir=eidos_dir,
            doc_id=doc_id,
            slug=slug,
            title=title,
            heading=heading,
            design_system=resolved_design_system,
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
    errors = validator.validate_document(doc)
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
    file_path = find_spec_by_id_or_path(eidos_dir, target)
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
    """List all living specifications (including inline C4 Level 3 components, excluding SpecDeltas) under eidos_dir."""
    type_filter_clean = type_filter.strip().lower().replace("_", "/") if type_filter else None
    if type_filter_clean in ("delta", "mutation", "delta/mutation"):
        raise ValueError("SpecDeltas (DLT-*) are managed via 'eidos delta'. Use 'eidos delta list' instead.")

    if not eidos_dir.exists():
        return []

    from eidos.core.index.graph_index import get_architectural_index

    eidos_resolved = eidos_dir.resolve()
    idx = get_architectural_index(eidos_dir=eidos_dir)
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

    seen_ids: set[str] = {str(ent.get("id", "")) for ent in all_entities if ent.get("id")}

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

        if doc_type == "tech/container":
            inline_comps = ent.get("components", [])
            for comp in inline_comps:
                if not isinstance(comp, dict):
                    continue
                cid = str(comp.get("id", "")).strip()
                if not cid or cid in seen_ids:
                    continue
                comp_type = "tech/component"
                comp_status = comp.get("status") or status
                if not _matches_type(comp_type, cid):
                    continue
                if status_filter_clean and str(comp_status).lower() != status_filter_clean:
                    continue
                seen_ids.add(cid)
                results.append(
                    {
                        "id": cid,
                        "type": comp_type,
                        "title": comp.get("title") or "Untitled",
                        "status": comp_status,
                        "path": resolved_p,
                        "relative_path": rel_p,
                    }
                )

    return sorted(results, key=lambda x: str(x.get("id", "")))


def promote_component(
    component_id: str,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Promote an inline C4 Level 3 component to a dedicated specification file in eidos/architecture/components/.

    Returns:
        (component_spec_path, container_path)
    """
    if repo_root is not None:
        root = Path(repo_root).resolve()
    elif eidos_dir is not None:
        edir_resolved = Path(eidos_dir).resolve()
        root = (
            edir_resolved.parent
            if (
                edir_resolved.name == "eidos"
                or (edir_resolved / "architecture").exists()
                or (edir_resolved / "system.adoc").exists()
            )
            else edir_resolved
        )
    else:
        root = Path.cwd().resolve()
    edir = (Path(eidos_dir) if eidos_dir is not None else (root / "eidos")).resolve()
    containers_dir = edir / "architecture" / "containers"

    if not containers_dir.exists():
        raise FileNotFoundError(f"Container directory '{containers_dir}' does not exist.")

    norm_id = component_id.strip().upper()
    if not norm_id.startswith("CMP-"):
        norm_id = f"CMP-{norm_id}"

    target_container_path: Path | None = None
    target_comp_data: dict[str, Any] | None = None

    for cfile in sorted(containers_dir.glob("*.adoc")):
        doc = parse_spec_file(cfile)
        if doc.doc_type != "tech/container" or not doc.frontmatter:
            continue
        comps = list(doc.frontmatter.get("components") or [])
        for c in comps:
            if isinstance(c, dict) and c.get("id") == norm_id:
                target_container_path = cfile
                target_comp_data = c
                break
        if target_container_path:
            break

    if not target_container_path or not target_comp_data:
        raise ValueError(f"Component '{norm_id}' not found inline within any container in '{containers_dir}'.")

    c_doc = parse_spec_file(target_container_path)
    c_fm = c_doc.frontmatter or {}
    container_id = c_fm.get("id") or target_container_path.stem
    source_path_val = target_comp_data.get("source_path") or ""

    # Place dedicated component specification centrally in eidos/architecture/components/
    components_dir = edir / "architecture" / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    slug = norm_id.removeprefix("CMP-").lower().replace("_", "-")
    comp_file_path = components_dir / f"{slug}.adoc"

    # Author centralized tech/component specification losslessly via safe YAML serialization
    comp_title = target_comp_data.get("title") or norm_id
    responsibility = target_comp_data.get("responsibility") or "In-process module logic and domain services."
    promoted_fm: dict[str, Any] = {
        "type": "tech/component",
        "id": norm_id,
        "title": comp_title,
        "container": container_id,
        "status": target_comp_data.get("status") or "active",
        "source_path": source_path_val,
        "responsibility": responsibility,
        "invariants": target_comp_data.get("invariants")
        if isinstance(target_comp_data.get("invariants"), list)
        else [],
    }
    if isinstance(target_comp_data.get("domain"), str) and target_comp_data["domain"]:
        promoted_fm["domain"] = target_comp_data["domain"]
    if isinstance(target_comp_data.get("decisions"), list) and target_comp_data["decisions"]:
        promoted_fm["decisions"] = target_comp_data["decisions"]

    heading_name = _single_line(comp_title)
    component_body = render_spec_template("tech/component", heading=heading_name, responsibility=responsibility)
    component_content = _render_spec(promoted_fm, component_body)
    comp_file_path.write_text(component_content, encoding="utf-8")

    # Remove inline entry from container document (standalone component declares container: CNT-*)
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, _body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        new_c_content = _mutate_frontmatter_components(
            raw_text,
            "remove",
            norm_id,
        )
        target_container_path.write_text(new_c_content, encoding="utf-8")

    return comp_file_path, target_container_path


def inline_component(
    component_id: str,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Demote / inline a dedicated C4 Level 3 component back into its container.

    Returns:
        (container_path, deleted_spec_path)
    """
    if repo_root is not None:
        root = Path(repo_root).resolve()
    elif eidos_dir is not None:
        edir_resolved = Path(eidos_dir).resolve()
        root = (
            edir_resolved.parent
            if (
                edir_resolved.name == "eidos"
                or (edir_resolved / "architecture").exists()
                or (edir_resolved / "system.adoc").exists()
            )
            else edir_resolved
        )
    else:
        root = Path.cwd().resolve()
    edir = (Path(eidos_dir) if eidos_dir is not None else (root / "eidos")).resolve()
    containers_dir = edir / "architecture" / "containers"
    components_dir = edir / "architecture" / "components"

    if not containers_dir.exists():
        raise FileNotFoundError(f"Container directory '{containers_dir}' does not exist.")

    norm_id = component_id.strip().upper()
    if not norm_id.startswith("CMP-"):
        norm_id = f"CMP-{norm_id}"

    comp_file: Path | None = None
    comp_doc = None
    if components_dir.exists():
        for cand in sorted(components_dir.glob("*.adoc")):
            doc = parse_spec_file(cand)
            if doc.doc_type == "tech/component" and doc.doc_id == norm_id:
                comp_file = cand
                comp_doc = doc
                break
    if comp_file is None:
        cand = find_spec_by_id_or_path(edir, norm_id)
        if cand and cand.exists():
            doc = parse_spec_file(cand)
            if doc.doc_type == "tech/component":
                comp_file = cand
                comp_doc = doc

    if comp_file is None or comp_doc is None:
        raise ValueError(f"Standalone component '{norm_id}' not found in '{components_dir}'.")

    comp_fm = comp_doc.frontmatter or {}
    parent_cnt_ref = str(comp_fm.get("container") or "").strip()
    target_container_path: Path | None = None
    if parent_cnt_ref:
        target_container_path = find_spec_by_id_or_path(edir, parent_cnt_ref)
    if target_container_path is None:
        for cfile in sorted(containers_dir.glob("*.adoc")):
            doc = parse_spec_file(cfile)
            if doc.doc_type == "tech/container" and doc.doc_id == parent_cnt_ref:
                target_container_path = cfile
                break
    if not target_container_path or not target_container_path.exists():
        raise FileNotFoundError(
            f"Parent container '{parent_cnt_ref}' for component '{norm_id}' not found in '{containers_dir}'."
        )

    comp_title = comp_fm.get("title") or norm_id
    responsibility = comp_fm.get("responsibility")
    if isinstance(responsibility, str):
        responsibility = responsibility.strip()
    source_path = comp_fm.get("source_path") or ""

    inline_entry: dict[str, Any] = {
        "id": norm_id,
        "title": comp_title,
        "source_path": source_path,
    }
    if responsibility:
        inline_entry["responsibility"] = responsibility
    comp_status = comp_fm.get("status")
    if isinstance(comp_status, str) and comp_status.strip() and comp_status.strip() != "active":
        inline_entry["status"] = comp_status.strip()
    if isinstance(comp_fm.get("domain"), str) and comp_fm["domain"]:
        inline_entry["domain"] = comp_fm["domain"]
    if isinstance(comp_fm.get("invariants"), list) and comp_fm["invariants"]:
        inline_entry["invariants"] = comp_fm["invariants"]
    if isinstance(comp_fm.get("decisions"), list) and comp_fm["decisions"]:
        inline_entry["decisions"] = comp_fm["decisions"]

    # Append inline component back into container frontmatter while preserving YAML comments and formatting
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, _body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        new_c_content = _mutate_frontmatter_components(
            raw_text,
            "append",
            norm_id,
            inline_entry,
        )
        target_container_path.write_text(new_c_content, encoding="utf-8")

    # Safely remove the dedicated .adoc file
    comp_file.unlink(missing_ok=True)

    return target_container_path, comp_file


def _resolve_schema_node(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve top-level $ref to #/$defs/... if present while preserving sibling annotations."""
    if not isinstance(node, dict):
        return {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        def_key = ref.removeprefix("#/$defs/")
        resolved = dict(defs.get(def_key) or {})
        for k, v in node.items():
            if k != "$ref":
                resolved[k] = v
        return resolved
    return dict(node)


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
    from eidos.core.spec.parser import safe_dump_yaml
    from eidos.core.spec.validator import TYPE_TO_SCHEMA_NAME

    validator = SpecValidator()

    def _build_single_schema_info(canonical_type: str) -> dict[str, Any]:
        schema_rel = TYPE_TO_SCHEMA_NAME[canonical_type]
        raw_schema = validator._schema_cache.get(schema_rel) or {}
        defs = raw_schema.get("$defs") if isinstance(raw_schema.get("$defs"), dict) else {}

        merged_required: list[str] = []
        merged_props: dict[str, Any] = {}

        # Flatten allOf (e.g. tech/component inherits #/$defs/inline_component from common/defs.yaml)
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
