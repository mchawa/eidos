"""Lifecycle management, scaffolding, and deletion for Eidos living specifications and SpecDeltas.

The delta engine, design engine, repository initializer, and shared catalog helpers live in dedicated modules;
every previously public name stays importable from here (DLT-2026-038)."""

import contextlib
from pathlib import Path
from typing import Any

from eidos.core.lifecycle.catalog import (  # noqa: F401  (backwards-compatible re-exports)
    VALID_SPEC_TYPES,
    _build_spec_catalog,
    _build_spec_catalog_with_invariants,
    _format_component_item,
    _mutate_frontmatter_components,
    _render_spec,
    _set_frontmatter_scalar,
    _single_line,
    _split_raw_frontmatter,
    find_references_to_spec,
    find_spec_by_id_or_path,
    normalize_id,
)
from eidos.core.lifecycle.delta_engine import (  # noqa: F401  (backwards-compatible re-exports)
    DeltaVerificationReport,
    MutationVerificationItem,
    _check_or_record_baseline_sha,
    _ensure_eidos_gitignore,
    _evaluate_declarative_postcondition,
    _extract_mutation_target,
    _resolve_git_branch,
    _resolve_git_head_sha,
    _snapshot_delta_baselines,
    apply_delta,
    create_delta,
    delete_delta,
    generate_collision_free_delta_id,
    get_delta_status_summary,
    list_deltas,
    verify_delta_mutations,
)
from eidos.core.lifecycle.design_engine import DESIGN_SPEC_KEYS, build_design_spec
from eidos.core.lifecycle.initializer import init_repository  # noqa: F401  (backwards-compatible re-export)
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator


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
            "schema": "schemas/v1/product/system.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "archetypes": ["service"],
            "business_identity": {
                "vision": f"Core vision and purpose of {heading}.",
                "target_users": ["Primary users"],
                "value_proposition": f"Distinct value delivered by {heading}.",
            },
            "technical_profile": {
                "primary_languages": ["TBD"],
            },
        }
        body = f"""= System: {heading}
:toc: left

== System Vision & Scope
Describe the strategic vision, target users, and architectural boundaries of this system.
"""

    elif canonical_key == "architecture":
        dest_dir = eidos_dir / "architecture"
        dest_file = dest_dir / "system-architecture.adoc"
        actor_ref = _first_of_type("product/persona", "PER-USER")
        fm = {
            "type": "tech/architecture",
            "schema": "schemas/v1/tech/architecture.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "topology_style": "modular_monolith",
            "c4_context": {
                "actors": [actor_ref],
            },
            "c4_containers": [],
            "solution_strategy": [f"Foundational architectural strategy for {heading}."],
            "cross_cutting_concepts": {},
            "deployment_view": {
                "hosting_model": "TBD",
                "ci_cd_engine": "TBD",
            },
        }
        body = f"""= Architecture: {heading}
:toc: left

== System Context & Topology
Describe the C4 system context, container topology, solution strategy, and deployment view.
"""

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
            "schema": "schemas/v1/product/capability.schema.yaml",
            "id": doc_id,
            "title": title,
            "domain": resolved_domain,
            "status": "draft",
            "business": {
                "personas": [persona_ref],
                "jtbd": f"Realize {heading}.",
            },
            "technical": {
                "containers": [cnt_ref],
            },
        }
        body = f"""= Capability: {heading}
:toc: left

== Intent
Describe the business intent and user outcome realized by this capability.
"""

    elif canonical_key == "domain":
        dom_slug = slug.removeprefix("dom-")
        dest_dir = eidos_dir / "domains" / dom_slug
        dest_file = dest_dir / "domain.adoc"
        fm = {
            "type": "product/domain",
            "schema": "schemas/v1/product/domain.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "classification": "core",
            "boundaries": {"responsibilities": [f"Primary core responsibility of {heading}."]},
        }
        body = f"""= Domain: {heading}
:toc: left

== Strategic Mission
Describe the bounded context, scope, and strategic objectives of this domain.
"""

    elif canonical_key == "policy":
        dest_dir = eidos_dir / "policies"
        dest_file = dest_dir / f"{slug}.adoc"
        inv_id = f"INV-{doc_id}"
        fm = {
            "type": "product/policy",
            "schema": "schemas/v1/product/policy.schema.yaml",
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
        body = f"""= Policy: {heading}
:toc: left

== Rationale & Context
Explain why this non-negotiable architectural or product guardrail exists.
"""

    elif canonical_key == "contract":
        dest_dir = eidos_dir / "architecture" / "contracts"
        dest_file = dest_dir / f"{slug}.adoc"
        cnt_owner = _first_of_type("tech/container", "CNT-CORE")
        fm = {
            "type": "tech/contract",
            "schema": "schemas/v1/tech/contract.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "protocol": "cli_args",
            "container": cnt_owner,
            "specification_file": "src/main.py",
        }
        body = f"""= Contract: {heading}
:toc: left

== Protocol & Interface Specification
Document the endpoints, command line arguments, payloads, or schemas for this contract.
"""

    elif canonical_key == "container":
        dest_dir = eidos_dir / "architecture" / "containers"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "tech/container",
            "schema": "schemas/v1/tech/container.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "container_type": "service",
            "runtime": "Application Runtime",
            "source_path": "src",
        }
        body = f"""= Container: {heading}
:toc: left

== Architectural Boundary
Describe the container boundary, deployment unit, and responsibilities.

== Building Block View (C4 Level 3 Components)
Internal components and modular building blocks contained within this deployable.
"""

    elif canonical_key == "component":
        # INV-COMPONENT-CENTRALIZATION-MANDATE: dedicated components live in eidos/architecture/components/
        dest_dir = eidos_dir / "architecture" / "components"
        dest_file = dest_dir / f"{slug.removeprefix('cmp-')}.adoc"
        parent_cnt = _first_of_type("tech/container", "CNT-CORE")
        fm = {
            "type": "tech/component",
            "schema": "schemas/v1/tech/component.schema.yaml",
            "id": doc_id,
            "title": title,
            "container": parent_cnt,
            "status": "draft",
            "responsibility": f"Responsibility of {heading}.",
            "source_path": "src",
        }
        body = f"""= Component: {heading}
:toc: left

== Responsibility
Describe the in-process logic this C4 Level 3 component encapsulates within `{parent_cnt}`.

== In-Process Interfaces
Document exported language-level APIs (functions, classes, traits) provided to peer components.
"""

    elif canonical_key == "persona":
        dest_dir = eidos_dir / "personas"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "product/persona",
            "schema": "schemas/v1/product/persona.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "draft",
            "role_type": "end_user",
            "goals": [f"Primary objective of {heading}."],
            "pain_points": ["Key friction or inefficiency faced in current workflows."],
        }
        body = f"""= Persona: {heading}
:toc: left

== Profile & Motivations
Detailed contextual background, user psychology, and job-to-be-done for this actor.
"""

    elif canonical_key == "exploration":
        dest_dir = eidos_dir / "explorations"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "product/exploration",
            "schema": "schemas/v1/product/exploration.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "proposed",
            "hypothesis": f"Investigating feasibility and performance characteristics of {heading}.",
            "exit_criteria": {"max_duration": "1_week"},
        }
        body = f"""= Exploration: {heading}
:toc: left

== Investigation & Hypotheses
Document prototyping discoveries, trade-offs, and research findings.
"""

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
) -> list[dict[str, Any]]:
    """List all living specifications (including inline C4 Level 3 components, excluding SpecDeltas) under eidos_dir."""
    type_filter_clean = type_filter.strip().lower().replace("_", "/") if type_filter else None
    if type_filter_clean in ("delta", "mutation", "delta/mutation"):
        raise ValueError(
            "SpecDeltas (DLT-*) are managed via 'eidos delta'. Use 'eidos delta list' instead."
        )

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
        rel_p = (
            resolved_p.relative_to(eidos_resolved)
            if resolved_p.is_relative_to(eidos_resolved)
            else raw_fp
        )

        if _matches_type(doc_type, eid) and (not status_filter_clean or str(status).lower() == status_filter_clean):
            results.append(
                {
                    "id": eid,
                    "type": doc_type,
                    "title": ent.get("title") or "Untitled",
                    "status": status,
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
        "schema": "schemas/v1/tech/component.schema.yaml",
        "id": norm_id,
        "title": comp_title,
        "container": container_id,
        "status": target_comp_data.get("status") or "active",
        "source_path": source_path_val,
        "responsibility": responsibility,
        "interfaces": target_comp_data.get("interfaces") or {"provides": [], "consumes": []},
        "quality_attributes": target_comp_data.get("quality_attributes") or {"stateless": True, "reentrant": True},
        "invariants": target_comp_data.get("invariants") if isinstance(target_comp_data.get("invariants"), list) else [],
    }
    if isinstance(target_comp_data.get("domain"), str) and target_comp_data["domain"]:
        promoted_fm["domain"] = target_comp_data["domain"]
    if isinstance(target_comp_data.get("decisions"), list) and target_comp_data["decisions"]:
        promoted_fm["decisions"] = target_comp_data["decisions"]

    heading_name = _single_line(comp_title)
    component_body = f"""= Component: {heading_name}
:toc: left

== 1. Internal Architecture & Subsystem Design
{responsibility}

== 2. In-Process APIs & Interfaces
Language-level function signatures, traits, and public interfaces provided to the owning container.
"""
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
    responsibility = comp_fm.get("responsibility") or "In-process module logic."
    if isinstance(responsibility, str):
        responsibility = responsibility.strip()
    source_path = comp_fm.get("source_path") or ""

    inline_entry: dict[str, Any] = {
        "id": norm_id,
        "title": comp_title,
        "responsibility": responsibility,
        "source_path": source_path,
    }
    comp_status = comp_fm.get("status")
    if isinstance(comp_status, str) and comp_status.strip() and comp_status.strip() != "active":
        inline_entry["status"] = comp_status.strip()
    if isinstance(comp_fm.get("domain"), str) and comp_fm["domain"]:
        inline_entry["domain"] = comp_fm["domain"]
    if isinstance(comp_fm.get("invariants"), list) and comp_fm["invariants"]:
        inline_entry["invariants"] = comp_fm["invariants"]
    if isinstance(comp_fm.get("decisions"), list) and comp_fm["decisions"]:
        inline_entry["decisions"] = comp_fm["decisions"]
    interfaces = comp_fm.get("interfaces")
    if isinstance(interfaces, dict) and (interfaces.get("provides") or interfaces.get("consumes")):
        inline_entry["interfaces"] = interfaces
    qa = comp_fm.get("quality_attributes")
    if isinstance(qa, dict) and qa and qa != {"stateless": True, "reentrant": True}:
        inline_entry["quality_attributes"] = qa

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
