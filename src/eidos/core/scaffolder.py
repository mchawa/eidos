"""Lifecycle management, scaffolding, and deletion for Eidos living specifications and SpecDeltas."""

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from eidos.core.parser import extract_frontmatter, parse_spec_file
from eidos.core.validator import SpecValidator

VALID_SPEC_TYPES = {
    "delta": "delta/mutation",
    "mutation": "delta/mutation",
    "capability": "product/capability",
    "domain": "product/domain",
    "policy": "product/policy",
    "contract": "tech/contract",
    "container": "tech/container",
    "tech/container": "tech/container",
    "component": "tech/component",
    "persona": "product/persona",
    "exploration": "product/exploration",
    "design-system": "design/system",
    "design_system": "design/system",
    "design/system": "design/system",
    "ui-component": "design/ui-component",
    "ui_component": "design/ui-component",
    "design/ui-component": "design/ui-component",
    "screen": "design/screen",
    "design/screen": "design/screen",
}


def normalize_id(doc_type_key: str, raw_id: str) -> str:
    """Ensure document ID follows standard prefix conventions."""
    cleaned = raw_id.strip().upper().replace(" ", "-")
    prefix_map = {
        "delta": "DLT-",
        "mutation": "DLT-",
        "capability": "CAP-",
        "domain": "DOM-",
        "policy": "POL-",
        "contract": "CTR-",
        "container": "CNT-",
        "tech/container": "CNT-",
        "component": "CMP-",
        "tech/component": "CMP-",
        "persona": "PER-",
        "exploration": "EXP-",
        "design-system": "DS-",
        "design_system": "DS-",
        "design/system": "DS-",
        "ui-component": "UIC-",
        "ui_component": "UIC-",
        "design/ui-component": "UIC-",
        "screen": "SCR-",
        "design/screen": "SCR-",
    }
    pfx = prefix_map.get(doc_type_key.lower())
    if pfx and not cleaned.startswith(pfx):
        if doc_type_key in ("container", "component", "tech/container", "tech/component") and (
            cleaned.startswith("CNT-") or cleaned.startswith("CMP-")
        ):
            return cleaned
        cleaned = f"{pfx}{cleaned}"
    return cleaned


def create_spec(
    eidos_dir: Path,
    doc_type_key: str,
    raw_id: str,
    title: str,
    domain: str | None = None,
    kind: str = "feature_addition",
    motivation: str = "Proposed change motivation.",
    category: str = "governance",
    rule: str = "Rule statement for this policy.",
    protocol: str = "cli_args",
    owning_container: str | None = None,
    owning_component: str | None = None,
    specification_file: str = "src/eidos/cli/main.py",
    container_type: str = "library",
    runtime: str = "Python 3.12 / Typer / Hatchling",
    package_path: str = "src/eidos",
    role_type: str = "human_engineer",
    jtbd: str = "When executing lifecycle commands, I want automated scaffolding so that specs are valid.",
    client_id: str | None = None,
    extends_system: str | None = None,
    route: str = "/view",
    design_system: str = "DS-CORE",
    implements_component: str | None = None,
) -> Path:
    """Scaffold a new living specification document with valid schema frontmatter."""
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
        "component": "container",
        "tech/component": "container",
        "persona": "persona",
        "product/persona": "persona",
        "exploration": "exploration",
        "product/exploration": "exploration",
        "design-system": "design-system",
        "design_system": "design-system",
        "design/system": "design-system",
        "ui-component": "ui-component",
        "ui_component": "ui-component",
        "design/ui-component": "ui-component",
        "screen": "screen",
        "design/screen": "screen",
    }
    canonical_key = key_aliases.get(type_key)
    if not canonical_key:
        supported = ", ".join(sorted(set(key_aliases.values())))
        raise ValueError(f"Unknown specification type '{doc_type_key}'. Supported types: {supported}")

    doc_id = normalize_id(canonical_key, raw_id)
    slug = doc_id.lower().replace("_", "-")

    # Determine destination directory and content
    if canonical_key == "delta":
        dest_dir = eidos_dir / "deltas"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: {doc_id}
title: "{title}"
status: proposed

intent:
  kind: {kind}
  motivation: "{motivation}"

mutations: []
---
= SpecDelta: {title}
:toc: left

== Problem Statement
{motivation}

== Proposed Specification Changes
Describe the exact behavioral diffs, mutations, and invariant updates here.
"""

    elif canonical_key == "capability":
        parent_domain = domain or "DOM-SPEC-ENGINE"
        domain_slug = parent_domain.lower().replace("dom-", "")
        # Find if domains/<domain_slug> exists
        domain_dirs = list(eidos_dir.glob(f"domains/*{domain_slug}*"))
        if domain_dirs and domain_dirs[0].is_dir():
            dest_dir = domain_dirs[0] / "capabilities"
        else:
            dest_dir = eidos_dir / "domains" / domain_slug / "capabilities"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: {doc_id}
title: "{title}"
domain: {parent_domain}
status: draft
version: "1.0.0"

business:
  personas:
    - eidos/personas/product-lead.adoc
  jtbd: "{jtbd}"

technical:
  components:
    - "{owning_component}"
  contracts:
    interfaces: []
  invariants: []
---
= Capability: {title}
:toc: left

== Intent
Describe the business intent and user outcome realized by this capability.
"""

    elif canonical_key == "domain":
        dom_slug = slug.replace("dom-", "")
        dest_dir = eidos_dir / "domains" / dom_slug
        dest_file = dest_dir / "domain.adoc"
        content = f"""---
type: product/domain
schema: schemas/v1/product/domain.schema.yaml
id: {doc_id}
name: "{title}"
classification: core

boundaries:
  responsibilities:
    - "Primary core responsibility of {title}."
---
= Domain: {title}
:toc: left

== Strategic Mission
Describe the bounded context, scope, and strategic objectives of this domain.
"""

    elif canonical_key == "policy":
        dest_dir = eidos_dir / "policies"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: {doc_id}
title: "{title}"
category: {category}
rule: "{rule}"

enforcement:
  mechanism: "CI automated validation and invariant testing"
  severity: critical
---
= Policy: {title}
:toc: left

== Rule Statement
{rule}

== Rationale & Context
Explain why this non-negotiable architectural or product guardrail exists.
"""

    elif canonical_key == "contract":
        dest_dir = eidos_dir / "architecture" / "contracts"
        dest_file = dest_dir / f"{slug}.adoc"
        owner_id = owning_container or owning_component or "CNT-EIDOS-CORE"
        content = f"""---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: {doc_id}
title: "{title}"
protocol: "{protocol}"
owning_container: "{owner_id}"
specification_file: "{specification_file}"
---
= Contract: {title}
:toc: left

== Protocol & Interface Specification
Document the endpoints, command line arguments, payloads, or schemas for this contract.
"""

    elif canonical_key in ("container", "component"):
        dest_dir = eidos_dir / "architecture" / "containers"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: {doc_id}
name: "{title}"
container_type: "{container_type}"
runtime: "{runtime}"
package_path: "{package_path}"
components: []
---
= Container: {title}
:toc: left

== Architectural Boundary
Describe the container boundary, deployment unit, and responsibilities.

== Building Block View (C4 Level 3 Components)
Internal components and modular building blocks contained within this deployable.
"""

    elif canonical_key == "persona":
        dest_dir = eidos_dir / "personas"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: product/persona
schema: schemas/v1/product/persona.schema.yaml
id: {doc_id}
name: "{title}"
role_type: "{role_type}"

goals:
  - "Primary objective of this persona."

pain_points:
  - "Key friction or inefficiency faced in current workflows."

interaction_modes:
  - "CLI commands"
---
= Persona: {title}
:toc: left

== Profile & Motivations
Detailed contextual background, user psychology, and job-to-be-done for this actor.
"""

    elif canonical_key == "exploration":
        dest_dir = eidos_dir / "explorations"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: product/exploration
schema: schemas/v1/product/exploration.schema.yaml
id: {doc_id}
title: "{title}"
status: proposed
hypothesis: "Investigating feasibility and performance characteristics of {title}."
exit_criteria:
  max_duration: "1_week"
---
= Exploration: {title}
:toc: left

== Investigation & Hypotheses
Document prototyping discoveries, trade-offs, and research findings.
"""

    elif canonical_key == "design-system":
        clean_sys = slug.replace("ds-", "")
        dest_dir = eidos_dir / "design" / "systems" / clean_sys
        dest_file = dest_dir / "system.adoc"

        # Ensure baseline W3C tokens.json exists in dest_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        tokens_file = dest_dir / "tokens.json"
        if not tokens_file.exists():
            base_tokens = {
                "color": {
                    "brand": {
                        "primary": {
                            "$value": "#0052cc",
                            "$type": "color",
                            "$description": f"Primary brand color for {title}",
                        }
                    }
                }
            }
            tokens_file.write_text(json.dumps(base_tokens, indent=2), encoding="utf-8")

        client_yaml = f'client_id: "{client_id}"\nbrand_name: "{title}"\n' if client_id else ""
        extends_yaml = (
            f"""inheritance:
  extends: "{extends_system}"
  override_mode: "token_and_component"
"""
            if extends_system
            else ""
        )

        content = f"""---
type: design/system
schema: schemas/v1/design/system.schema.yaml
id: {doc_id}
title: "{title}"
status: active
{client_yaml}{extends_yaml}tokens:
  spec_format: "w3c_design_tokens"
  manifest: "tokens.json"

theme_capabilities:
  color_modes:
    - light
    - dark
  density_modes:
    - comfortable
---
= Design System: {title}
:toc: left

== Foundations & W3C Tokens
This specification defines visual primitives, brand tokens, and inheritance rules for {title}.
Tokens are defined in W3C DTCG format at `tokens.json`.
"""

    elif canonical_key == "ui-component":
        clean_comp = slug.replace("uic-", "")
        sys_slug = (design_system or "ds-core").lower().replace("ds-", "")
        dest_dir = eidos_dir / "design" / "systems" / sys_slug / "components"
        dest_file = dest_dir / f"{clean_comp}.adoc"

        implements_yaml = f'implements: "{implements_component}"\n' if implements_component else ""

        content = f"""---
type: design/ui-component
schema: schemas/v1/design/ui-component.schema.yaml
id: {doc_id}
title: "{title}"
design_system: "{design_system}"
{implements_yaml}status: active
category: inputs

variants:
  - name: "primary"
    description: "Default visual emphasis."
  - name: "secondary"
    description: "Subtle visual emphasis."

states:
  - default
  - hover
  - active
  - focus
  - disabled

accessibility:
  wcag_level: "AA"
  aria_role: "button"
  keyboard_nav: "Enter and Space activate; Tab traverses focus."

test_bindings:
  storybook: "packages/ui/src/{title.replace(" ", "")}.stories.tsx"
---
= UI Component: {title}
:toc: left

== Component Contract
Describes variants, interactive states, accessibility contracts, and visual test bindings for {title}.
"""

    elif canonical_key == "screen":
        clean_screen = slug.replace("scr-", "")
        dest_dir = eidos_dir / "design" / "screens"
        dest_file = dest_dir / f"{clean_screen}.adoc"

        content = f"""---
type: design/screen
schema: schemas/v1/design/screen.schema.yaml
id: {doc_id}
title: "{title}"
route: "{route}"
status: active

capabilities: []

supported_design_systems:
  - "DS-CORE"

slots:
  main_content: "Primary view content"

states:
  - name: "loading"
    description: "Skeleton loader displayed during data fetch."
  - name: "active"
    description: "Standard interactive screen state."
  - name: "error"
    description: "Fallback error message if request fails."
---
= Screen: {title}
:toc: left

== View Layout & State Machine
This specification defines the route, layout slots, and state transitions for {title}.
"""

    else:
        raise ValueError(f"Unsupported canonical key '{canonical_key}'")

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
        sync_delta_views(eidos_dir)

    return dest_file


def find_spec_by_id_or_path(eidos_dir: Path, target: str) -> Path | None:
    """Resolve a document ID, relative path, or filename to an existing spec path."""
    target_clean = target.strip()

    # 1. Exact path or relative path
    direct_path = Path(target_clean)
    if direct_path.exists() and direct_path.is_file():
        return direct_path.resolve()

    rel_path = eidos_dir / target_clean
    if rel_path.exists() and rel_path.is_file():
        return rel_path.resolve()

    if not target_clean.endswith(".adoc"):
        rel_adoc = eidos_dir / f"{target_clean}.adoc"
        if rel_adoc.exists() and rel_adoc.is_file():
            return rel_adoc.resolve()

    # 2. Search by ID across all .adoc files in eidos_dir
    target_lower = target_clean.lower()
    for p in eidos_dir.rglob("*.adoc"):
        if p.stem.lower() == target_lower:
            return p.resolve()
        try:
            doc = parse_spec_file(p)
            if doc.doc_id and doc.doc_id.lower() == target_lower:
                return p.resolve()
        except Exception:
            continue

    return None


def find_references_to_spec(eidos_dir: Path, target_id: str, target_path: Path) -> list[Path]:
    """Find other specification documents that reference target_id or target_path."""
    referencing_files = []
    target_id_lower = target_id.lower()
    rel_path_str = (
        str(target_path.relative_to(eidos_dir)) if target_path.is_relative_to(eidos_dir) else target_path.name
    )

    for p in eidos_dir.rglob("*.adoc"):
        if p.resolve() == target_path.resolve():
            continue
        try:
            content = p.read_text(encoding="utf-8")
            if target_id in content or target_id_lower in content.lower() or rel_path_str in content:
                referencing_files.append(p.resolve())
        except Exception:
            continue

    return referencing_files


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

    if doc.doc_type == "delta/mutation":
        sync_delta_views(eidos_dir)

    return True, f"Successfully deleted specification '{doc_id}' ({file_path.name}).", references


def list_specs(
    eidos_dir: Path,
    type_filter: str | None = None,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """List all specifications under eidos_dir with optional filters."""
    if not eidos_dir.exists():
        return []

    from eidos.core.graph_index import get_architectural_index

    idx = get_architectural_index(eidos_dir=eidos_dir)
    entities = idx.get_entities(status=status_filter)
    results = []

    type_filter_clean = type_filter.lower() if type_filter else None
    status_filter_clean = status_filter.lower() if status_filter else None

    for ent in entities:
        doc_type = ent.get("type", "")
        eid = ent.get("id", "")
        if doc_type == "invariant":
            continue

        if type_filter_clean:
            if type_filter_clean not in doc_type.lower() and type_filter_clean not in eid.lower():
                continue

        status = ent.get("status", "active")
        if status_filter_clean and str(status).lower() != status_filter_clean:
            continue

        file_p = Path(ent.get("file_path", ""))
        rel_p = file_p.relative_to(eidos_dir) if file_p.is_relative_to(eidos_dir) else file_p

        results.append(
            {
                "id": eid,
                "type": doc_type,
                "title": ent.get("title") or "Untitled",
                "status": status,
                "path": file_p.resolve() if file_p.exists() else file_p,
                "relative_path": rel_p,
            }
        )

    return sorted(results, key=lambda x: str(x.get("id", "")))


def _ensure_eidos_gitignore(eidos_dir: Path) -> None:
    """Ensure eidos/.gitignore is present and ignores generated symlink views so host projects remain unpolluted."""
    gitignore_path = eidos_dir / ".gitignore"
    rule = "deltas/by-status/"
    if not gitignore_path.exists():
        with contextlib.suppress(Exception):
            gitignore_path.write_text(
                f"# Eidos living specification lifecycle views (generated symlinks)\n{rule}\n",
                encoding="utf-8",
            )
    else:
        with contextlib.suppress(Exception):
            content = gitignore_path.read_text(encoding="utf-8")
            if "deltas/by-status" not in content and "by-status" not in content:
                gitignore_path.write_text(
                    f"{content.rstrip()}\n\n# Eidos living specification lifecycle views (generated symlinks)\n{rule}\n",
                    encoding="utf-8",
                )


def sync_delta_views(eidos_dir: Path) -> dict[str, list[Path]]:
    """Synchronize gitignored symlink views in eidos/deltas/by-status/ based on frontmatter status.

    Categories: proposed, in_review, approved, applied, rejected, active (in-flight).
    """
    deltas_dir = eidos_dir / "deltas"
    if not deltas_dir.exists():
        return {}

    # Ensure self-contained eidos/.gitignore
    _ensure_eidos_gitignore(eidos_dir)

    views_dir = deltas_dir / "by-status"
    categories = ["proposed", "in_review", "approved", "applied", "rejected", "active"]

    # Ensure target category directories exist
    for cat in categories:
        (views_dir / cat).mkdir(parents=True, exist_ok=True)

    # Clean existing symlinks in by-status
    for cat in categories:
        cat_dir = views_dir / cat
        for item in cat_dir.iterdir():
            if item.is_symlink() or item.is_file():
                with contextlib.suppress(Exception):
                    item.unlink()

    synced: dict[str, list[Path]] = {cat: [] for cat in categories}

    for p in sorted(deltas_dir.rglob("*.adoc")):
        if "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            if doc.doc_type != "delta/mutation":
                continue

            status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
            cat_name = status if status in categories else "proposed"
            link_path = views_dir / cat_name / p.name
            rel_target = os.path.relpath(p, views_dir / cat_name)
            try:
                link_path.symlink_to(rel_target)
                synced[cat_name].append(p)
            except Exception:
                pass

            if status in ("proposed", "in_review", "approved", "in_progress", "draft"):
                active_link = views_dir / "active" / p.name
                rel_active = os.path.relpath(p, views_dir / "active")
                try:
                    active_link.symlink_to(rel_active)
                    synced["active"].append(p)
                except Exception:
                    pass
        except Exception:
            continue

    return synced


def get_delta_status_summary(eidos_dir: Path) -> dict[str, Any]:
    """Get count and listing breakdown of deltas grouped by lifecycle status."""
    deltas = list_deltas(eidos_dir)
    by_status: dict[str, list[dict]] = {
        "proposed": [],
        "in_review": [],
        "approved": [],
        "applied": [],
        "rejected": [],
    }
    for d in deltas:
        st = str(d.get("status", "proposed")).lower().strip()
        if st not in by_status:
            by_status[st] = []
        by_status[st].append(d)

    active_count = sum(len(by_status.get(s, [])) for s in ("proposed", "in_review", "approved"))

    return {
        "total": len(deltas),
        "active_count": active_count,
        "by_status": by_status,
        "views_dir": eidos_dir / "deltas" / "by-status",
    }


def list_deltas(eidos_dir: Path, status_filter: str | None = None) -> list[dict[str, Any]]:
    """List all SpecDeltas from eidos/deltas/ with optional status filtering."""
    deltas_dir = eidos_dir / "deltas"
    results = []
    if not deltas_dir.exists():
        return results

    status_filter_clean = status_filter.lower().strip() if status_filter else None

    for p in sorted(deltas_dir.rglob("*.adoc")):
        if "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            if doc.doc_type != "delta/mutation":
                continue

            status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
            is_active = status in ("proposed", "in_review", "approved", "in_progress", "draft")

            if status_filter_clean and (
                (status_filter_clean == "active" and not is_active)
                or (status_filter_clean != "active" and status != status_filter_clean)
            ):
                continue

            intent = doc.frontmatter.get("intent", {})
            kind = intent.get("kind", "mutation") if isinstance(intent, dict) else "mutation"

            results.append(
                {
                    "id": doc.doc_id,
                    "title": doc.title or "Untitled",
                    "status": status,
                    "kind": kind,
                    "is_active": is_active,
                    "path": p.resolve(),
                    "relative_path": p.relative_to(eidos_dir) if p.is_relative_to(eidos_dir) else p,
                }
            )
        except Exception:
            continue

    return results


def apply_delta(eidos_dir: Path, delta_id_or_path: str) -> tuple[bool, str, Path | None]:
    """Transition a SpecDelta to applied status in-place and sync gitignored symlink views."""
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None

    current_status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
    if current_status == "applied":
        sync_delta_views(eidos_dir)
        return True, f"SpecDelta '{doc.doc_id}' is already marked as applied.", file_path

    # Read and update frontmatter status to applied in-place
    content = file_path.read_text(encoding="utf-8")
    fm, body, err = extract_frontmatter(content)
    if err or not fm:
        return False, f"Failed to extract frontmatter: {err}", None

    fm["status"] = "applied"
    new_yaml = yaml.dump(fm, sort_keys=False, default_flow_style=False)
    new_content = f"---\n{new_yaml}---\n{body}"

    file_path.write_text(new_content, encoding="utf-8")

    # Validate applied document in-place
    doc_applied = parse_spec_file(file_path)
    validator = SpecValidator()
    errors = validator.validate_document(doc_applied)
    if errors:
        return False, f"Delta updated to applied but failed validation: {'; '.join(errors)}", file_path

    # Synchronize gitignored symlink views
    sync_delta_views(eidos_dir)

    return True, f"Successfully applied SpecDelta '{doc.doc_id}' in-place -> {file_path.name}.", file_path


def init_repository(
    project_dir: Path,
    name: str | None = None,
    title: str | None = None,
    eidos_dir_name: str = "eidos",
    with_plugin: bool = True,
    force: bool = False,
) -> tuple[bool, str, list[Path]]:
    """Initialize a repository with an empty Eidos living specification skeleton and governance."""
    eidos_dir = project_dir / eidos_dir_name
    created_files: list[Path] = []

    if eidos_dir.exists() and any(eidos_dir.iterdir()) and not force:
        return (
            False,
            f"Eidos specifications already exist at '{eidos_dir}'. Use --force to re-initialize.",
            [],
        )

    # Standard Eidos living specification directory skeleton
    skeleton_dirs = [
        eidos_dir / "architecture" / "containers",
        eidos_dir / "architecture" / "contracts",
        eidos_dir / "domains",
        eidos_dir / "policies",
        eidos_dir / "personas",
        eidos_dir / "deltas",
        eidos_dir / "explorations",
        eidos_dir / "design" / "clients",
        eidos_dir / "design" / "flows",
        eidos_dir / "design" / "screens",
        eidos_dir / "design" / "systems" / "core" / "components",
    ]
    for d in skeleton_dirs:
        d.mkdir(parents=True, exist_ok=True)
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()
        if gitkeep not in created_files:
            created_files.append(gitkeep)

    # Ensure any additional empty leaf directories under eidos_dir are tracked by .gitkeep
    for current_dir, dirnames, filenames in os.walk(eidos_dir):
        p = Path(current_dir)
        if "by-status" in p.parts:
            continue
        if not dirnames and not filenames:
            gitkeep = p / ".gitkeep"
            gitkeep.touch()
            if gitkeep not in created_files:
                created_files.append(gitkeep)

    # Ensure self-contained eidos/.gitignore and status symlink views
    _ensure_eidos_gitignore(eidos_dir)
    gitignore_file = eidos_dir / ".gitignore"
    if gitignore_file.exists() and gitignore_file not in created_files:
        created_files.append(gitignore_file)

    # Root system.adoc exists but is empty (ready for user authoring)
    sys_file = eidos_dir / "system.adoc"
    if not sys_file.exists():
        sys_file.write_text("", encoding="utf-8")
    if sys_file not in created_files:
        created_files.append(sys_file)

    sync_delta_views(eidos_dir)

    # Validate directory immediately
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    if not report.is_clean:
        errs = []
        for elist in report.schema_errors.values():
            errs.extend(elist)
        for b in report.broken_references:
            errs.append(f"Broken ref: {b}")
        return False, f"Scaffolded specifications failed validation: {'; '.join(errs)}", created_files

    # Optional Agent Plugin installation
    if with_plugin:
        try:
            from eidos.plugin.installer import PluginInstallTarget, install_plugin

            install_plugin(target=PluginInstallTarget.PROJECT, project_dir=project_dir, force=force)
        except Exception:
            pass

    return True, f"Successfully initialized empty Eidos living specification skeleton at '{eidos_dir}'.", created_files


def promote_component(
    component_id: str,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Promote an inline C4 Level 3 component to a co-located specification file.

    Returns:
        (colocated_spec_path, container_path)
    """
    root = (repo_root or Path.cwd()).resolve()
    edir = (eidos_dir or (root / "eidos")).resolve()
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
    pkg_path_str = c_fm.get("package_path", "src")
    pkg_dir = root / pkg_path_str

    # Determine co-located .adoc path
    source_path_str = target_comp_data.get("source_path", "")
    if source_path_str:
        src_file = root / source_path_str
        colocated_path = src_file.with_suffix(".adoc")
    else:
        # Fallback to package_path/<component_id_lower>.adoc
        slug = norm_id.replace("CMP-", "").lower().replace("-", "_")
        colocated_path = pkg_dir / f"{slug}.adoc"

    colocated_path.parent.mkdir(parents=True, exist_ok=True)
    rel_colocated = str(colocated_path.relative_to(root)).replace("\\", "/")

    # Author co-located tech/component specification
    comp_name = target_comp_data.get("name", norm_id)
    responsibility = target_comp_data.get("responsibility", "In-process module logic and domain services.")
    source_path_rel = source_path_str or str(colocated_path.with_suffix(".py").relative_to(root)).replace("\\", "/")

    component_content = f"""---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: {norm_id}
name: "{comp_name}"
container: {container_id}
status: active
package_path: "{source_path_rel}"
responsibility: >
  {responsibility}

# IN-PROCESS INTERFACES (Language-level APIs, NOT boundary contracts)
interfaces:
  provides: []
  consumes: []

quality_attributes:
  stateless: true
  reentrant: true

invariants: []
---
= Component: {comp_name}
:toc: left

== 1. Internal Architecture & Subsystem Design
{responsibility}

== 2. In-Process APIs & Interfaces
Language-level function signatures, traits, and public interfaces provided to the owning container.
"""

    colocated_path.write_text(component_content, encoding="utf-8")

    # Update container document replacing inline dict with {id: norm_id, ref: rel_colocated}
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        parsed_fm = fm_dict
        for idx, item in enumerate(parsed_fm.get("components", [])):
            if isinstance(item, dict) and item.get("id") == norm_id:
                parsed_fm["components"][idx] = {
                    "id": norm_id,
                    "ref": rel_colocated,
                }
                break
        new_fm_str = yaml.dump(parsed_fm, sort_keys=False, default_flow_style=False).strip()
        new_c_content = f"---\n{new_fm_str}\n---\n{body_raw}"
        target_container_path.write_text(new_c_content, encoding="utf-8")

    return colocated_path, target_container_path


def inline_component(
    component_id: str,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Demote / inline a co-located C4 Level 3 component back into its container.

    Returns:
        (container_path, deleted_spec_path)
    """
    root = (repo_root or Path.cwd()).resolve()
    edir = (eidos_dir or (root / "eidos")).resolve()
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
        raise ValueError(f"Component '{norm_id}' is not currently promoted with a co-located 'ref' in '{containers_dir}'.")

    colocated_file = root / target_ref_path
    if not colocated_file.exists():
        raise FileNotFoundError(f"Referenced co-located spec file '{colocated_file}' does not exist.")

    comp_doc = parse_spec_file(colocated_file)
    comp_fm = comp_doc.frontmatter or {}
    name = comp_fm.get("name", norm_id)
    responsibility = comp_fm.get("responsibility", "In-process module logic.")
    source_path = comp_fm.get("package_path", comp_fm.get("source_path", str(colocated_file.with_suffix(".py").relative_to(root)).replace("\\", "/")))

    # Update container frontmatter
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        parsed_fm = fm_dict
        for idx, item in enumerate(parsed_fm.get("components", [])):
            if isinstance(item, dict) and item.get("id") == norm_id:
                parsed_fm["components"][idx] = {
                    "id": norm_id,
                    "name": name,
                    "responsibility": responsibility,
                    "source_path": source_path,
                }
                break
        new_fm_str = yaml.dump(parsed_fm, sort_keys=False, default_flow_style=False).strip()
        new_c_content = f"---\n{new_fm_str}\n---\n{body_raw}"
        target_container_path.write_text(new_c_content, encoding="utf-8")

    # Safely remove the co-located .adoc file
    colocated_file.unlink(missing_ok=True)

    return target_container_path, colocated_file
