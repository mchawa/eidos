"""Lifecycle management, scaffolding, and deletion for Eidos living specifications and SpecDeltas."""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from eidos.core.parser import parse_spec_file, extract_frontmatter
from eidos.core.validator import SpecValidator

VALID_SPEC_TYPES = {
    "delta": "delta/mutation",
    "mutation": "delta/mutation",
    "capability": "product/capability",
    "domain": "product/domain",
    "policy": "product/policy",
    "contract": "tech/contract",
    "component": "tech/component",
    "persona": "product/persona",
    "exploration": "product/exploration",
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
        "component": "CMP-",
        "persona": "PER-",
        "exploration": "EXP-",
    }
    pfx = prefix_map.get(doc_type_key.lower())
    if pfx and not cleaned.startswith(pfx):
        cleaned = f"{pfx}{cleaned}"
    return cleaned


def create_spec(
    eidos_dir: Path,
    doc_type_key: str,
    raw_id: str,
    title: str,
    domain: Optional[str] = None,
    kind: str = "feature_addition",
    motivation: str = "Proposed change motivation.",
    category: str = "governance",
    rule: str = "Rule statement for this policy.",
    protocol: str = "cli_args",
    owning_component: str = "CMP-EIDOS-ENGINE",
    specification_file: str = "src/eidos/cli/main.py",
    container_type: str = "library",
    runtime: str = "Python 3.12 / Typer / Hatchling",
    package_path: str = "src/eidos",
    role_type: str = "human_engineer",
    jtbd: str = "When executing lifecycle commands, I want automated scaffolding so that specs are valid.",
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
        "component": "component",
        "tech/component": "component",
        "persona": "persona",
        "product/persona": "persona",
        "exploration": "exploration",
        "product/exploration": "exploration",
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
        parent_domain = domain or "DOM-PRODUCT-CORE"
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
        content = f"""---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: {doc_id}
title: "{title}"
protocol: "{protocol}"
owning_component: "{owning_component}"
specification_file: "{specification_file}"
---
= Contract: {title}
:toc: left

== Protocol & Interface Specification
Document the endpoints, command line arguments, payloads, or schemas for this contract.
"""

    elif canonical_key == "component":
        dest_dir = eidos_dir / "architecture" / "components"
        dest_file = dest_dir / f"{slug}.adoc"
        content = f"""---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: {doc_id}
name: "{title}"
container_type: "{container_type}"
runtime: "{runtime}"
package_path: "{package_path}"
---
= Component: {title}
:toc: left

== Whitebox View & Responsibilities
Describe the internal modules, responsibility boundaries, and dependencies of this component.
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


def find_spec_by_id_or_path(eidos_dir: Path, target: str) -> Optional[Path]:
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


def find_references_to_spec(eidos_dir: Path, target_id: str, target_path: Path) -> List[Path]:
    """Find other specification documents that reference target_id or target_path."""
    referencing_files = []
    target_id_lower = target_id.lower()
    rel_path_str = str(target_path.relative_to(eidos_dir)) if target_path.is_relative_to(eidos_dir) else target_path.name

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


def delete_spec(eidos_dir: Path, target: str, force: bool = False) -> Tuple[bool, str, List[Path]]:
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
            references
        )

    file_path.unlink()

    # Clean up empty parent directory if not a top-level dir
    parent = file_path.parent
    if parent != eidos_dir and parent.exists() and not any(parent.iterdir()):
        try:
            parent.rmdir()
        except OSError:
            pass

    if doc.doc_type == "delta/mutation":
        sync_delta_views(eidos_dir)

    return True, f"Successfully deleted specification '{doc_id}' ({file_path.name}).", references


def list_specs(
    eidos_dir: Path,
    type_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List all specifications under eidos_dir with optional filters."""
    results = []
    if not eidos_dir.exists():
        return results

    type_filter_clean = type_filter.lower() if type_filter else None
    status_filter_clean = status_filter.lower() if status_filter else None

    for p in sorted(eidos_dir.rglob("*.adoc")):
        if "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            doc_type = doc.doc_type
            status = doc.frontmatter.get("status", "active")

            if type_filter_clean:
                if type_filter_clean not in doc_type.lower() and type_filter_clean not in p.name.lower():
                    continue

            if status_filter_clean and str(status).lower() != status_filter_clean:
                continue

            results.append({
                "id": doc.doc_id,
                "type": doc_type,
                "title": doc.title or "Untitled",
                "status": status,
                "path": p.resolve(),
                "relative_path": p.relative_to(eidos_dir) if p.is_relative_to(eidos_dir) else p,
            })
        except Exception:
            continue

    return results


def sync_delta_views(eidos_dir: Path) -> Dict[str, List[Path]]:
    """Synchronize gitignored symlink views in eidos/deltas/by-status/ based on frontmatter status.

    Categories: proposed, in_review, approved, applied, rejected, active (in-flight).
    """
    deltas_dir = eidos_dir / "deltas"
    if not deltas_dir.exists():
        return {}

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
                try:
                    item.unlink()
                except Exception:
                    pass

    synced: Dict[str, List[Path]] = {cat: [] for cat in categories}

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


def get_delta_status_summary(eidos_dir: Path) -> Dict[str, Any]:
    """Get count and listing breakdown of deltas grouped by lifecycle status."""
    deltas = list_deltas(eidos_dir)
    by_status: Dict[str, List[dict]] = {
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


def list_deltas(eidos_dir: Path, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
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

            if status_filter_clean:
                if status_filter_clean == "active" and not is_active:
                    continue
                elif status_filter_clean != "active" and status != status_filter_clean:
                    continue

            intent = doc.frontmatter.get("intent", {})
            kind = intent.get("kind", "mutation") if isinstance(intent, dict) else "mutation"

            results.append({
                "id": doc.doc_id,
                "title": doc.title or "Untitled",
                "status": status,
                "kind": kind,
                "is_active": is_active,
                "path": p.resolve(),
                "relative_path": p.relative_to(eidos_dir) if p.is_relative_to(eidos_dir) else p,
            })
        except Exception:
            continue

    return results


def apply_delta(eidos_dir: Path, delta_id_or_path: str) -> Tuple[bool, str, Optional[Path]]:
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
