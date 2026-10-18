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
    _resolve_git_head_sha,
    _snapshot_delta_baselines,
    apply_delta,
    create_delta,
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
    domain: str | None = None,
    kind: str = "feature",
    motivation: str = "Proposed change motivation.",
    category: str = "governance",
    rule: str = "Rule statement for this policy.",
    protocol: str = "cli_args",
    owning_container: str | None = None,
    owning_component: str | None = None,
    specification_file: str = "src/main.py",
    container_type: str = "service",
    runtime: str = "Application Runtime",
    package_path: str = "src",
    role_type: str = "human_engineer",
    jtbd: str = "When executing lifecycle commands, I want automated scaffolding so that specs are valid.",
    client_id: str | None = None,
    extends_system: str | None = None,
    route: str = "/view",
    design_system: str | None = None,
    implements_component: str | None = None,
    platform: str = "web",
    branch: str | None = None,
    slug_suffix: bool = False,
) -> Path:
    """Scaffold a new living specification document with valid schema frontmatter.

    Frontmatter is always built as a Python dict and serialized with ``yaml.safe_dump`` so titles,
    motivations, and rules containing quotes, colons, or newlines cannot corrupt the YAML (DLT-2026-032).
    """
    type_key = doc_type_key.lower().replace("_", "/")
    # Map short names to standard keys
    key_aliases = {
        "delta": "delta",
        "mutation": "delta",
        "delta/mutation": "delta",
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

    if canonical_key == "delta":
        doc_id = generate_collision_free_delta_id(
            eidos_dir,
            raw_id=raw_id,
            title=title,
            slug_suffix=slug_suffix,
        )
    else:
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

    # Determine destination directory and content
    if canonical_key == "delta":
        dest_dir = eidos_dir / "deltas"
        dest_file = dest_dir / f"{slug}.adoc"
        fm = {
            "type": "delta/mutation",
            "schema": "schemas/v1/delta/delta.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "proposed",
            "intent": {"kind": kind, "motivation": motivation},
            "mutations": [],
        }
        head_sha = _resolve_git_head_sha(eidos_dir)
        vcs_dict: dict[str, Any] = {}
        if branch:
            vcs_dict["branch"] = branch
        if head_sha:
            vcs_dict["base_commit"] = head_sha
        if vcs_dict:
            fm["vcs"] = vcs_dict
        body = f"""= SpecDelta: {heading}
:toc: left

== Problem Statement
{motivation}

== Proposed Specification Changes
Describe the exact behavioral diffs, mutations, and invariant updates here.
"""

    elif canonical_key == "capability":
        specs_by_id = _catalog()
        if domain:
            parent_domain = domain
        else:
            existing_domains = sorted(did for did, (_, dt, _) in specs_by_id.items() if dt == "product/domain")
            parent_domain = existing_domains[0] if existing_domains else "DOM-CORE"

        cnt_ref = owning_container
        if not cnt_ref:
            if owning_component and owning_component.startswith("CNT-"):
                cnt_ref = owning_component
            else:
                existing_cnts = sorted(cid for cid, (_, dt, _) in specs_by_id.items() if dt == "tech/container")
                cnt_ref = existing_cnts[0] if existing_cnts else "CNT-CORE"

        existing_personas = sorted((eidos_dir / "personas").glob("*.adoc")) if (eidos_dir / "personas").exists() else []
        persona_ref = f"eidos/personas/{existing_personas[0].name}" if existing_personas else "PER-USER"

        domain_slug = parent_domain.lower().removeprefix("dom-")
        domain_dirs = list(eidos_dir.glob(f"domains/*{domain_slug}*"))
        if domain_dirs and domain_dirs[0].is_dir():
            dest_dir = domain_dirs[0] / "capabilities"
        else:
            dest_dir = eidos_dir / "domains" / domain_slug / "capabilities"
        dest_file = dest_dir / f"{slug}.adoc"
        technical: dict[str, Any] = {"containers": [cnt_ref]}
        if owning_component and owning_component != cnt_ref:
            technical["components"] = [owning_component]
        technical["contracts"] = {"interfaces": []}
        technical["invariants"] = []
        fm = {
            "type": "product/capability",
            "schema": "schemas/v1/product/capability.schema.yaml",
            "id": doc_id,
            "title": title,
            "domain": parent_domain,
            "status": "draft",
            "version": "1.0.0",
            "business": {"personas": [persona_ref], "jtbd": jtbd},
            "technical": technical,
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
            "name": title,
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
        fm = {
            "type": "product/policy",
            "schema": "schemas/v1/product/policy.schema.yaml",
            "id": doc_id,
            "title": title,
            "category": category,
            "rule": rule,
            "enforcement": {"mechanism": "CI automated validation and invariant testing", "severity": "critical"},
        }
        body = f"""= Policy: {heading}
:toc: left

== Rule Statement
{rule}

== Rationale & Context
Explain why this non-negotiable architectural or product guardrail exists.
"""

    elif canonical_key == "contract":
        dest_dir = eidos_dir / "architecture" / "contracts"
        dest_file = dest_dir / f"{slug}.adoc"
        owner_id = owning_container or (
            owning_component if owning_component and owning_component.startswith("CNT-") else None
        )
        if not owner_id:
            owner_id = _first_of_type("tech/container", "CNT-CORE")
        fm = {
            "type": "tech/contract",
            "schema": "schemas/v1/tech/contract.schema.yaml",
            "id": doc_id,
            "title": title,
            "protocol": protocol,
            "owning_container": owner_id,
            "specification_file": specification_file,
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
            "name": title,
            "container_type": container_type,
            "runtime": runtime,
            "package_path": package_path,
            "components": [],
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
        parent_cnt = owning_container or (
            owning_component if owning_component and owning_component.startswith("CNT-") else None
        )
        if not parent_cnt:
            parent_cnt = _first_of_type("tech/container", "CNT-CORE")
        fm = {
            "type": "tech/component",
            "schema": "schemas/v1/tech/component.schema.yaml",
            "id": doc_id,
            "name": title,
            "container": parent_cnt,
            "status": "proposed",
            "source_path": package_path,
            "responsibility": f"Responsibility of {heading}.",
            "interfaces": {"provides": [], "consumes": []},
            "invariants": [],
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
            "name": title,
            "role_type": role_type,
            "goals": ["Primary objective of this persona."],
            "pain_points": ["Key friction or inefficiency faced in current workflows."],
            "interaction_modes": ["CLI commands"],
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
        resolved_design_system = design_system or _first_of_type("design/system", "DS-CORE")
        dest_dir, dest_file, fm, body = build_design_spec(
            canonical_key,
            eidos_dir=eidos_dir,
            doc_id=doc_id,
            slug=slug,
            title=title,
            heading=heading,
            owning_container=owning_container,
            client_id=client_id,
            extends_system=extends_system,
            route=route,
            design_system=resolved_design_system,
            implements_component=implements_component,
            platform=platform,
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
        err_msg = "; ".join(errors)
        raise ValueError(f"Generated specification failed validation: {err_msg}")

    if canonical_key == "delta":
        _snapshot_delta_baselines(eidos_dir, doc_id)

    return dest_file


def delete_spec(eidos_dir: Path, target: str, force: bool = False) -> tuple[bool, str, list[Path]]:
    """Safely delete a specification document with cross-reference safety checks."""
    file_path = find_spec_by_id_or_path(eidos_dir, target)
    if not file_path:
        return False, f"Specification '{target}' not found under '{eidos_dir}'.", []

    doc = parse_spec_file(file_path)
    doc_id = doc.doc_id or file_path.stem

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
    """List all specifications (including inline C4 Level 3 components) under eidos_dir with optional filters."""
    if not eidos_dir.exists():
        return []

    from eidos.core.index.graph_index import get_architectural_index

    idx = get_architectural_index(eidos_dir=eidos_dir)
    entities = idx.get_entities(status=status_filter)
    results = []

    type_filter_clean = type_filter.strip().lower() if type_filter else None
    canonical_type = VALID_SPEC_TYPES.get(type_filter_clean) if type_filter_clean else None
    status_filter_clean = status_filter.strip().lower() if status_filter else None

    def _matches_type(doc_type: str, eid: str) -> bool:
        if not type_filter_clean:
            return True
        if canonical_type:
            return doc_type.lower() == canonical_type
        return type_filter_clean in doc_type.lower() or type_filter_clean in eid.lower()

    seen_ids: set[str] = {str(ent.get("id", "")) for ent in entities if ent.get("id")}

    for ent in entities:
        doc_type = ent.get("type", "")
        eid = ent.get("id", "")
        if doc_type == "invariant":
            continue

        status = ent.get("status", "active")
        file_p = Path(ent.get("file_path", ""))
        rel_p = file_p.relative_to(eidos_dir) if file_p.is_relative_to(eidos_dir) else file_p
        resolved_p = file_p.resolve() if file_p.exists() else file_p

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
            inline_comps = ent.get("components", []) or ent.get("c4_internal_components", [])
            for comp in inline_comps:
                if not isinstance(comp, dict) or comp.get("ref"):
                    continue
                cid = str(comp.get("id", "")).strip()
                if not cid or cid in seen_ids:
                    continue
                comp_type = "tech/component"
                comp_status = comp.get("status", status)
                if not _matches_type(comp_type, cid):
                    continue
                if status_filter_clean and str(comp_status).lower() != status_filter_clean:
                    continue
                seen_ids.add(cid)
                results.append(
                    {
                        "id": cid,
                        "type": comp_type,
                        "title": comp.get("name") or comp.get("title") or "Untitled",
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
        root = edir_resolved.parent if edir_resolved.name == "eidos" else edir_resolved
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
        comps = doc.frontmatter.get("components", [])
        for c in comps:
            if isinstance(c, dict) and c.get("id") == norm_id:
                if c.get("ref"):
                    raise ValueError(f"Component '{norm_id}' is already promoted with ref '{c['ref']}'.")
                target_container_path = cfile
                target_comp_data = c
                break
        if target_container_path:
            break

    if not target_container_path or not target_comp_data:
        raise ValueError(f"Component '{norm_id}' not found inline within any container in '{containers_dir}'.")

    c_doc = parse_spec_file(target_container_path)
    c_fm = c_doc.frontmatter or {}
    container_id = c_fm.get("id", target_container_path.stem)
    source_path_val = target_comp_data.get("source_path", "")

    # Place dedicated component specification centrally in eidos/architecture/components/
    components_dir = edir / "architecture" / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    slug = norm_id.removeprefix("CMP-").lower().replace("_", "-")
    comp_file_path = components_dir / f"{slug}.adoc"
    rel_comp_ref = (
        str(comp_file_path.relative_to(root)).replace("\\", "/")
        if comp_file_path.is_relative_to(root)
        else str(comp_file_path.relative_to(edir)).replace("\\", "/")
    )

    # Author centralized tech/component specification losslessly via safe YAML serialization
    comp_name = target_comp_data.get("name", norm_id)
    responsibility = target_comp_data.get("responsibility", "In-process module logic and domain services.")
    promoted_fm: dict[str, Any] = {
        "type": "tech/component",
        "schema": "schemas/v1/tech/component.schema.yaml",
        "id": norm_id,
        "name": comp_name,
        "container": container_id,
        "status": target_comp_data.get("status", "active"),
        "source_path": source_path_val,
        "responsibility": responsibility,
        "interfaces": target_comp_data.get("interfaces") or {"provides": [], "consumes": []},
        "quality_attributes": target_comp_data.get("quality_attributes") or {"stateless": True, "reentrant": True},
        "invariants": target_comp_data.get("invariants") if isinstance(target_comp_data.get("invariants"), list) else [],
    }
    if isinstance(target_comp_data.get("domain_binding"), str) and target_comp_data["domain_binding"]:
        promoted_fm["domain_binding"] = target_comp_data["domain_binding"]
    if isinstance(target_comp_data.get("decisions"), list) and target_comp_data["decisions"]:
        promoted_fm["decisions"] = target_comp_data["decisions"]

    heading_name = _single_line(comp_name)
    component_body = f"""= Component: {heading_name}
:toc: left

== 1. Internal Architecture & Subsystem Design
{responsibility}

== 2. In-Process APIs & Interfaces
Language-level function signatures, traits, and public interfaces provided to the owning container.
"""
    component_content = _render_spec(promoted_fm, component_body)
    comp_file_path.write_text(component_content, encoding="utf-8")

    # Update container document replacing inline dict with {id: norm_id, ref: rel_comp_ref}
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, _body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        new_c_content = _mutate_frontmatter_components(
            raw_text,
            "replace",
            norm_id,
            {"id": norm_id, "ref": rel_comp_ref},
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
        root = edir_resolved.parent if edir_resolved.name == "eidos" else edir_resolved
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
    target_ref_path: str | None = None

    for cfile in sorted(containers_dir.glob("*.adoc")):
        doc = parse_spec_file(cfile)
        if doc.doc_type != "tech/container" or not doc.frontmatter:
            continue
        comps = doc.frontmatter.get("components", [])
        for c in comps:
            if isinstance(c, dict) and c.get("id") == norm_id and c.get("ref"):
                target_container_path = cfile
                target_ref_path = c["ref"]
                break
        if target_container_path:
            break

    if not target_container_path or not target_ref_path:
        raise ValueError(f"Component '{norm_id}' is not currently promoted with a dedicated 'ref' in '{containers_dir}'.")

    # Locate component specification file
    comp_file = root / target_ref_path
    if not comp_file.exists():
        comp_file = edir / target_ref_path
    if not comp_file.exists():
        raise FileNotFoundError(f"Referenced component spec file '{comp_file}' does not exist.")

    comp_doc = parse_spec_file(comp_file)
    comp_fm = comp_doc.frontmatter or {}
    name = comp_fm.get("name", norm_id)
    responsibility = comp_fm.get("responsibility", "In-process module logic.")
    if isinstance(responsibility, str):
        responsibility = responsibility.strip()
    source_path = comp_fm.get("source_path", comp_fm.get("package_path", ""))

    inline_entry: dict[str, Any] = {
        "id": norm_id,
        "name": name,
        "responsibility": responsibility,
        "source_path": source_path,
    }
    if isinstance(comp_fm.get("domain_binding"), str) and comp_fm["domain_binding"]:
        inline_entry["domain_binding"] = comp_fm["domain_binding"]
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

    # Update container frontmatter while preserving YAML comments and formatting
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, _body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        new_c_content = _mutate_frontmatter_components(
            raw_text,
            "replace",
            norm_id,
            inline_entry,
        )
        target_container_path.write_text(new_c_content, encoding="utf-8")

    # Safely remove the dedicated .adoc file
    comp_file.unlink(missing_ok=True)

    return target_container_path, comp_file
