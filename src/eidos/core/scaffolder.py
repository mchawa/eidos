"""Lifecycle management, scaffolding, and deletion for Eidos living specifications and SpecDeltas."""

import contextlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from eidos.core.parser import extract_frontmatter, parse_spec_file
from eidos.core.validator import SpecValidator


@dataclass
class MutationVerificationItem:
    """Status of an individual mutation verified against the living specification catalog."""

    action: str
    target: str
    verified: bool
    status: str  # "FOUND", "MISSING", "ABSENT_AS_EXPECTED", "STILL_PRESENT", "DEPRECATED", "ACTIVE_NOT_DEPRECATED"
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "verified": self.verified,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class DeltaVerificationReport:
    """Consolidated verification report for all mutations declared in a SpecDelta."""

    delta_id: str
    delta_file: Path
    total_mutations: int
    verified_count: int
    is_verified: bool
    mutations: list[MutationVerificationItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "delta_file": str(self.delta_file),
            "total_mutations": self.total_mutations,
            "verified_count": self.verified_count,
            "is_verified": self.is_verified,
            "mutations": [m.to_dict() for m in self.mutations],
        }


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
        "component": "CNT-",
        "tech/component": "CNT-",
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

        if type_filter_clean and (type_filter_clean not in doc_type.lower() and type_filter_clean not in eid.lower()):
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


def _extract_mutation_target(m: dict[str, Any]) -> str:
    """Extract primary target identifier or path from a mutation dictionary."""
    for key in (
        "new_capability_id",
        "target_capability",
        "target_component",
        "target_policy",
        "target_contract",
        "target_domain",
        "target_persona",
        "target_screen",
        "target_ui_component",
        "target_invariant_id",
        "target_design_system",
    ):
        val = m.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # Fallback to regex in details
    details = m.get("details", "")
    if details:
        match = re.search(r"\b(CNT|CMP|CAP|CTR|POL|DOM|SCR|UIC|DS|PER|INV)-[A-Z0-9_-]+\b", details)
        if match:
            return match.group(0)
        path_match = re.search(r"['\"]?([a-zA-Z0-9_\-\./]+\.adoc)['\"]?", details)
        if path_match:
            return path_match.group(1)

    return "unspecified_target"


def _build_spec_catalog(
    eidos_dir: Path,
) -> tuple[dict[str, tuple[Path, str, dict[str, Any]]], dict[str, str], set[str]]:
    """Build fast lookup indexes across living specs: specs_by_id, container_components, invariants."""
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    container_components: dict[str, str] = {}  # comp_id -> container_id
    invariants: set[str] = set()

    for p in eidos_dir.rglob("*.adoc"):
        if "deltas" in p.parts or "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            if doc.doc_id:
                specs_by_id[doc.doc_id] = (p, doc.doc_type, doc.frontmatter)
                # Check container components
                if doc.doc_type == "tech/container":
                    comps = (
                        doc.frontmatter.get("components", [])
                        or doc.frontmatter.get("c4_internal_components", [])
                    )
                    if isinstance(comps, list):
                        for comp in comps:
                            if isinstance(comp, dict) and comp.get("id"):
                                container_components[comp["id"]] = doc.doc_id
                # Check capability/policy invariants
                invs = doc.frontmatter.get("invariants", []) or (
                    doc.frontmatter.get("technical", {}) or {}
                ).get("invariants", [])
                if isinstance(invs, list):
                    for inv in invs:
                        if isinstance(inv, dict) and inv.get("id"):
                            invariants.add(inv["id"])
                        elif isinstance(inv, str):
                            invariants.add(inv)
        except Exception:
            continue

    return specs_by_id, container_components, invariants


def verify_delta_mutations(
    eidos_dir: Path, delta_id_or_path: str
) -> tuple[bool, str, DeltaVerificationReport | None]:
    """Verify that all semantic mutations declared in a SpecDelta exist in the living specifications."""
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None

    raw_mutations = doc.frontmatter.get("mutations", [])
    if not isinstance(raw_mutations, list):
        raw_mutations = []

    specs_by_id, container_components, invariants = _build_spec_catalog(eidos_dir)

    items: list[MutationVerificationItem] = []
    for m in raw_mutations:
        action = str(m.get("action", "")).strip().lower()
        target = _extract_mutation_target(m)
        details = str(m.get("details", "")).strip()

        verified = False
        status = "UNKNOWN"

        target_path_exists = False
        if "/" in target or target.endswith(".adoc"):
            clean_p = target.strip("'\"")
            if (eidos_dir / clean_p).exists() or (Path(clean_p).is_absolute() and Path(clean_p).exists()):
                target_path_exists = True

        # 1. Removal / Deprecation Actions
        if action in ("remove_component", "remove_contract", "remove_screen", "remove_ui_component"):
            still_present = (
                target in specs_by_id
                or target in container_components
                or target_path_exists
            )
            if not still_present:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"

        elif action in ("deprecate_capability", "remove_capability", "deprecate_policy", "remove_policy"):
            if target not in specs_by_id and not target_path_exists:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                doc_fm = specs_by_id[target][2] if target in specs_by_id else {}
                if str(doc_fm.get("status", "")).lower() in ("deprecated", "sunset", "removed"):
                    verified = True
                    status = "DEPRECATED"
                else:
                    verified = False
                    status = "ACTIVE_NOT_DEPRECATED"

        # 2. Invariant Actions
        elif action in ("add_invariant", "patch_invariant"):
            if target in invariants:
                verified = True
                status = "FOUND"
            else:
                verified = False
                status = "MISSING"

        elif action == "remove_invariant":
            if target not in invariants:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"

        # 3. Architecture Modification
        elif action == "modify_architecture":
            arch_exists = any(d_type == "tech/architecture" for _, d_type, _ in specs_by_id.values()) or target in specs_by_id
            if arch_exists:
                verified = True
                status = "FOUND"
            else:
                verified = False
                status = "MISSING"

        # 4. Standard Addition & Modification Actions
        else:
            found = (
                target in specs_by_id
                or target in container_components
                or target_path_exists
            )
            if found:
                verified = True
                status = "FOUND"
            else:
                verified = False
                status = "MISSING"

        items.append(
            MutationVerificationItem(
                action=action,
                target=target,
                verified=verified,
                status=status,
                details=details,
            )
        )

    verified_count = sum(1 for it in items if it.verified)
    total_mutations = len(items)
    is_verified = (verified_count == total_mutations)

    report = DeltaVerificationReport(
        delta_id=doc.doc_id,
        delta_file=file_path,
        total_mutations=total_mutations,
        verified_count=verified_count,
        is_verified=is_verified,
        mutations=items,
    )

    msg = (
        f"All {total_mutations} mutation(s) verified for SpecDelta '{doc.doc_id}'."
        if is_verified
        else f"{verified_count}/{total_mutations} mutation(s) verified for SpecDelta '{doc.doc_id}' ({total_mutations - verified_count} unfulfilled)."
    )
    return is_verified, msg, report


def execute_delta_mutations(
    eidos_dir: Path, delta_id_or_path: str
) -> tuple[bool, str, list[Path]]:
    """Deterministically scaffold and execute missing specifications declared in a SpecDelta."""
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", []

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", []

    raw_mutations = doc.frontmatter.get("mutations", [])
    if not isinstance(raw_mutations, list):
        raw_mutations = []

    specs_by_id, container_components, _ = _build_spec_catalog(eidos_dir)
    mutated_files: list[Path] = []

    for m in raw_mutations:
        action = str(m.get("action", "")).strip().lower()
        target = _extract_mutation_target(m)
        details = str(m.get("details", "")).strip()

        # 1. Add Capability
        if action == "add_capability":
            target_id = m.get("new_capability_id") or m.get("target_capability") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                domain = m.get("target_domain")
                if not domain:
                    domains = [did for did, (_, dt, _) in specs_by_id.items() if dt == "product/domain"]
                    domain = domains[0] if domains else "DOM-SPEC-ENGINE"
                title = m.get("title") or target_id.replace("CAP-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="capability",
                    raw_id=target_id,
                    title=title,
                    domain=domain,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "product/capability", {})

        # 2. Add Policy
        elif action == "add_policy":
            target_id = m.get("target_policy") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("POL-", "").replace("-", " ").title()
                rule = details or f"Rule statement for {target_id}."
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="policy",
                    raw_id=target_id,
                    title=title,
                    rule=rule,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "product/policy", {})

        # 3. Add Component / Container
        elif action == "add_component":
            target_id = m.get("target_component") or target
            if target_id.startswith("CNT-") and target_id not in specs_by_id:
                title = m.get("title") or target_id.replace("CNT-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="container",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "tech/container", {})
            elif target_id.startswith("CMP-") and target_id not in container_components:
                target_cnt = m.get("target_container")
                cnt_file = None
                if target_cnt and target_cnt in specs_by_id:
                    cnt_file = specs_by_id[target_cnt][0]
                else:
                    for cid, (p, dt, _) in specs_by_id.items():
                        if dt == "tech/container":
                            cnt_file = p
                            target_cnt = cid
                            break
                if cnt_file and cnt_file.exists():
                    cnt_content = cnt_file.read_text(encoding="utf-8")
                    cnt_fm, cnt_body, _ = extract_frontmatter(cnt_content)
                    if cnt_fm is not None:
                        comps = cnt_fm.setdefault("components", [])
                        new_comp = {
                            "id": target_id,
                            "name": target_id.replace("CMP-", "").replace("-", " ").title(),
                            "responsibility": details or f"Component responsible for {target_id}.",
                        }
                        if not any(c.get("id") == target_id for c in comps):
                            comps.append(new_comp)
                            new_yaml = yaml.dump(cnt_fm, sort_keys=False, default_flow_style=False)
                            cnt_file.write_text(f"---\n{new_yaml}---\n{cnt_body}", encoding="utf-8")
                            container_components[target_id] = target_cnt or "CNT-CORE"
                            if cnt_file not in mutated_files:
                                mutated_files.append(cnt_file)

        # 4. Remove Component
        elif action == "remove_component":
            target_id = m.get("target_component") or target
            if target_id in container_components:
                cnt_id = container_components[target_id]
                if cnt_id in specs_by_id:
                    cnt_file = specs_by_id[cnt_id][0]
                    if cnt_file.exists():
                        cnt_content = cnt_file.read_text(encoding="utf-8")
                        cnt_fm, cnt_body, _ = extract_frontmatter(cnt_content)
                        if cnt_fm and "components" in cnt_fm:
                            cnt_fm["components"] = [c for c in cnt_fm["components"] if c.get("id") != target_id]
                            new_yaml = yaml.dump(cnt_fm, sort_keys=False, default_flow_style=False)
                            cnt_file.write_text(f"---\n{new_yaml}---\n{cnt_body}", encoding="utf-8")
                            del container_components[target_id]
                            if cnt_file not in mutated_files:
                                mutated_files.append(cnt_file)
            elif target_id in specs_by_id:
                cnt_file = specs_by_id[target_id][0]
                if cnt_file.exists():
                    cnt_file.unlink()
                    del specs_by_id[target_id]
                    if cnt_file not in mutated_files:
                        mutated_files.append(cnt_file)

        # 5. Add Contract
        elif action == "add_contract":
            target_id = m.get("target_contract") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("CTR-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="contract",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "tech/contract", {})

        # 6. Remove Contract
        elif action == "remove_contract":
            target_id = m.get("target_contract") or target
            if target_id in specs_by_id:
                ctr_file = specs_by_id[target_id][0]
                if ctr_file.exists():
                    ctr_file.unlink()
                    del specs_by_id[target_id]
                    if ctr_file not in mutated_files:
                        mutated_files.append(ctr_file)

        # 7. Add Screen
        elif action == "add_screen":
            target_id = m.get("target_screen") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("SCR-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="screen",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "design/screen", {})

        # 8. Add UI Component
        elif action == "add_ui_component":
            target_id = m.get("target_ui_component") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("UIC-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="ui-component",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "design/ui-component", {})

    return True, f"Successfully executed mutations for '{doc.doc_id}' ({len(mutated_files)} files created/modified).", mutated_files


def apply_delta(
    eidos_dir: Path,
    delta_id_or_path: str,
    execute: bool = False,
    verify: bool = False,
) -> tuple[bool, str, Path | None, list[Path]]:
    """Transition a SpecDelta to applied status in-place and sync gitignored symlink views.

    Args:
        eidos_dir: Path to the eidos specifications root.
        delta_id_or_path: ID, filename, or Path of the delta to apply.
        execute: If True, deterministically scaffold missing specs and apply structural mutations.
        verify: If True, verify that all mutations are fulfilled in living specs before applying.
    """
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None, []

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None, []

    mutated_files: list[Path] = []
    if execute:
        exec_ok, exec_msg, mutated_files = execute_delta_mutations(eidos_dir, delta_id_or_path)
        if not exec_ok:
            return False, f"Failed executing delta mutations: {exec_msg}", file_path, mutated_files

    if verify:
        ver_ok, ver_msg, report = verify_delta_mutations(eidos_dir, delta_id_or_path)
        if not ver_ok:
            unfulfilled = [
                f"{m.action}:{m.target}" for m in (report.mutations if report else []) if not m.verified
            ]
            return (
                False,
                f"Verification failed before apply: {ver_msg} Unfulfilled: {', '.join(unfulfilled)}",
                file_path,
                mutated_files,
            )

    current_status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
    if current_status == "applied":
        sync_delta_views(eidos_dir)
        return True, f"SpecDelta '{doc.doc_id}' is already marked as applied.", file_path, mutated_files

    # Read and update frontmatter status to applied in-place
    content = file_path.read_text(encoding="utf-8")
    fm, body, err = extract_frontmatter(content)
    if err or not fm:
        return False, f"Failed to extract frontmatter: {err}", None, mutated_files

    fm["status"] = "applied"
    new_yaml = yaml.dump(fm, sort_keys=False, default_flow_style=False)
    new_content = f"---\n{new_yaml}---\n{body}"

    file_path.write_text(new_content, encoding="utf-8")

    # Validate applied document in-place
    doc_applied = parse_spec_file(file_path)
    validator = SpecValidator()
    errors = validator.validate_document(doc_applied)
    if errors:
        return False, f"Delta updated to applied but failed validation: {'; '.join(errors)}", file_path, mutated_files

    # Synchronize gitignored symlink views
    sync_delta_views(eidos_dir)

    return True, f"Successfully applied SpecDelta '{doc.doc_id}' in-place -> {file_path.name}.", file_path, mutated_files


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
        eidos_dir / "architecture" / "components",
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
