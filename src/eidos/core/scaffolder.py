"""Lifecycle management, scaffolding, and deletion for Eidos living specifications and SpecDeltas."""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
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
    "tech/component": "tech/component",
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
    "client": "design/client",
    "design/client": "design/client",
    "flow": "design/flow",
    "design/flow": "design/flow",
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
        "client": "CLT-",
        "design/client": "CLT-",
        "flow": "FLOW-",
        "design/flow": "FLOW-",
    }
    pfx = prefix_map.get(doc_type_key.lower())
    if pfx and not cleaned.startswith(pfx):
        cleaned = f"{pfx}{cleaned}"
    return cleaned


def _render_spec(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter dict with yaml.safe_dump and wrap it with an AsciiDoc body (DLT-2026-032)."""
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    return f"---\n{fm_yaml}---\n{body.lstrip(chr(10))}"


def _split_raw_frontmatter(raw_content: str) -> tuple[str, str] | None:
    """Split a spec document into (raw_frontmatter_lines, body_text) without re-serializing YAML."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)(.*)", raw_content, re.DOTALL)
    if not match:
        return None
    return match.group(1), match.group(2)


def _set_frontmatter_scalar(raw_content: str, key: str, value: str) -> str:
    """Update a top-level scalar key in YAML frontmatter while preserving comments, quoting, and blank lines."""
    parts = _split_raw_frontmatter(raw_content)
    if parts is None:
        return raw_content
    raw_yaml, body = parts
    pattern = re.compile(rf"^({re.escape(key)}\s*:\s*)([^\n#]*?)(\s+#[^\n]*)?$", re.MULTILINE)
    if pattern.search(raw_yaml):
        new_yaml = pattern.sub(lambda m: f"{m.group(1)}{value}{m.group(3) or ''}", raw_yaml, count=1)
    else:
        new_yaml = f"{raw_yaml.rstrip()}\n{key}: {value}"
    return f"---\n{new_yaml}\n---\n{body}"


def _format_component_item(item: dict[str, Any], indent: str = "  ") -> str:
    """Serialize a single component list item with the given leading indentation."""
    dumped = yaml.safe_dump([item], sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    return "".join((indent + line if line.strip() else line) for line in dumped.splitlines(keepends=True))


def _mutate_frontmatter_components(
    raw_content: str,
    op: str,
    target_id: str,
    item_dict: dict[str, Any] | None = None,
) -> str:
    """Mutate a single item in the top-level 'components:' list while preserving all YAML comments and formatting."""
    parts = _split_raw_frontmatter(raw_content)
    if parts is None:
        return raw_content
    raw_yaml, body = parts
    lines = raw_yaml.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    comp_idx = next((i for i, ln in enumerate(lines) if re.match(r"^components\s*:", ln)), None)
    if comp_idx is None:
        if op == "append" and item_dict is not None:
            lines.append("components:\n")
            lines.append(_format_component_item(item_dict, "  "))
            return f"---\n{''.join(lines).rstrip(chr(10))}\n---\n{body}"
        return raw_content

    header_m = re.match(r"^(components\s*:\s*)\[\s*\](\s*(?:#.*)?\n?)$", lines[comp_idx])
    if header_m:
        if op == "append" and item_dict is not None:
            trailing = header_m.group(2)
            if not trailing.endswith("\n"):
                trailing += "\n"
            lines[comp_idx] = f"components:{trailing}"
            lines.insert(comp_idx + 1, _format_component_item(item_dict, "  "))
            return f"---\n{''.join(lines).rstrip(chr(10))}\n---\n{body}"
        return raw_content

    end_idx = len(lines)
    for i in range(comp_idx + 1, len(lines)):
        ln = lines[i]
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not ln[0].isspace() and not stripped.startswith("-"):
            end_idx = i
            while end_idx > comp_idx + 1 and (
                not lines[end_idx - 1].strip() or lines[end_idx - 1].startswith("#")
            ):
                end_idx -= 1
            break

    item_indents = [
        len(m.group(1))
        for i in range(comp_idx + 1, end_idx)
        if (m := re.match(r"^(\s*)-\s+", lines[i]))
    ]
    base_indent_len = min(item_indents) if item_indents else 2
    base_indent = " " * base_indent_len
    item_starts = [
        i
        for i in range(comp_idx + 1, end_idx)
        if (m := re.match(r"^(\s*)-\s+", lines[i])) and len(m.group(1)) == base_indent_len
    ]

    if op == "append" and item_dict is not None:
        lines.insert(end_idx, _format_component_item(item_dict, base_indent))
        return f"---\n{''.join(lines).rstrip(chr(10))}\n---\n{body}"

    for idx_pos, start_line in enumerate(item_starts):
        next_bound = item_starts[idx_pos + 1] if idx_pos + 1 < len(item_starts) else end_idx
        stop_line = next_bound
        while stop_line > start_line + 1 and (
            not lines[stop_line - 1].strip() or lines[stop_line - 1].lstrip().startswith("#")
        ):
            stop_line -= 1
        chunk_text = "".join(lines[start_line:stop_line])
        try:
            parsed_item = yaml.safe_load(chunk_text)
        except yaml.YAMLError:
            continue
        if (
            isinstance(parsed_item, list)
            and len(parsed_item) == 1
            and isinstance(parsed_item[0], dict)
            and parsed_item[0].get("id") == target_id
        ):
                if op == "replace" and item_dict is not None:
                    lines[start_line:stop_line] = [_format_component_item(item_dict, base_indent)]
                elif op == "remove":
                    del lines[start_line:stop_line]
                    if len(item_starts) == 1:
                        lines[comp_idx] = re.sub(
                            r"^components\s*:[ \t]*", "components: []", lines[comp_idx], count=1
                        )
                break

    return f"---\n{''.join(lines).rstrip(chr(10))}\n---\n{body}"


def _single_line(text: str) -> str:
    """Collapse whitespace/newlines so user input cannot break AsciiDoc headings."""
    return " ".join(str(text).split())


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
    specification_file: str = "src/main.py",
    container_type: str = "service",
    runtime: str = "Application Runtime",
    package_path: str = "src",
    role_type: str = "human_engineer",
    jtbd: str = "When executing lifecycle commands, I want automated scaffolding so that specs are valid.",
    client_id: str | None = None,
    extends_system: str | None = None,
    route: str = "/view",
    design_system: str = "DS-CORE",
    implements_component: str | None = None,
    platform: str = "web",
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

        domain_slug = parent_domain.lower().replace("dom-", "")
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
        dom_slug = slug.replace("dom-", "")
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
        owner_id = owning_container or owning_component
        if not owner_id:
            existing_comps = sorted(
                cid for cid, (_, dt, _) in _catalog().items() if dt in ("tech/container", "tech/component")
            )
            owner_id = existing_comps[0] if existing_comps else "CNT-CORE"
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
        dest_file = dest_dir / f"{slug.replace('cmp-', '', 1)}.adoc"
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
            fm["client_id"] = client_id
            fm["brand_name"] = title
        if extends_system:
            fm["inheritance"] = {"extends": extends_system, "override_mode": "token_and_component"}
        fm["tokens"] = {"spec_format": "w3c_design_tokens", "manifest": "tokens.json"}
        fm["theme_capabilities"] = {"color_modes": ["light", "dark"], "density_modes": ["comfortable"]}
        body = f"""= Design System: {heading}
:toc: left

== Foundations & W3C Tokens
This specification defines visual primitives, brand tokens, and inheritance rules for {heading}.
Tokens are defined in W3C DTCG format at `tokens.json`.
"""

    elif canonical_key == "ui-component":
        clean_comp = slug.replace("uic-", "")
        sys_slug = (design_system or "ds-core").lower().replace("ds-", "")
        dest_dir = eidos_dir / "design" / "systems" / sys_slug / "components"
        dest_file = dest_dir / f"{clean_comp}.adoc"
        fm = {
            "type": "design/ui-component",
            "schema": "schemas/v1/design/ui-component.schema.yaml",
            "id": doc_id,
            "title": title,
            "design_system": design_system,
        }
        if implements_component:
            fm["implements"] = implements_component
        fm.update(
            {
                "status": "active",
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
                "test_bindings": {"storybook": f"packages/ui/src/{re.sub(r'[^A-Za-z0-9]', '', heading)}.stories.tsx"},
            }
        )
        body = f"""= UI Component: {heading}
:toc: left

== Component Contract
Describes variants, interactive states, accessibility contracts, and visual test bindings for {heading}.
"""

    elif canonical_key == "screen":
        clean_screen = slug.replace("scr-", "")
        if client_id:
            client_slug = client_id.lower().replace("clt-", "", 1)
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
        }
        if client_id:
            fm["client"] = client_id
        fm.update(
            {
                "status": "active",
                "capabilities": [],
                "supported_design_systems": [design_system],
                "slots": {"main_content": "Primary view content"},
                "states": [
                    {"name": "loading", "description": "Skeleton loader displayed during data fetch."},
                    {"name": "active", "description": "Standard interactive screen state."},
                    {"name": "error", "description": "Fallback error message if request fails."},
                ],
            }
        )
        body = f"""= Screen: {heading}
:toc: left

== View Layout & State Machine
This specification defines the route, layout slots, and state transitions for {heading}.
"""

    elif canonical_key == "client":
        client_slug = slug.replace("clt-", "", 1)
        dest_dir = eidos_dir / "design" / "clients" / client_slug
        dest_file = dest_dir / "client.adoc"
        fm = {
            "type": "design/client",
            "schema": "schemas/v1/design/client.schema.yaml",
            "id": doc_id,
            "title": title,
            "status": "active",
            "platform": platform,
            "design_system": design_system,
        }
        if owning_container:
            fm["container"] = owning_container
        fm.update({"capabilities": [], "screens": [], "flows": []})
        body = f"""= Client: {heading}
:toc: left

== Client Identity & Platform Archetype
Describe the runtime platform, bound design system, and implementing container of this client surface.
"""

    elif canonical_key == "flow":
        flow_slug = slug.replace("flow-", "", 1)
        if client_id:
            client_slug = client_id.lower().replace("clt-", "", 1)
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
            "flow_type": "client_internal" if client_id else "cross_client",
        }
        if client_id:
            fm["client"] = client_id
        fm.update({"capabilities": [], "steps": [{"step": 1, "title": "Entry point"}]})
        body = f"""= Flow: {heading}
:toc: left

== User Journey
Describe the ordered steps, screen transitions, and outcomes of this interaction flow.
"""

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
        sync_delta_views(eidos_dir)
        _snapshot_delta_baselines(eidos_dir, doc_id)

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
        except OSError:
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
        except OSError:
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
    """Ensure eidos/.gitignore is present and ignores generated symlink views and cache so host projects remain unpolluted."""
    gitignore_path = eidos_dir / ".gitignore"
    default_content = (
        "# Eidos living specification lifecycle views (generated symlinks) and local index cache\n"
        "deltas/by-status/\n"
        ".cache/\n"
    )
    if not gitignore_path.exists():
        with contextlib.suppress(OSError):
            gitignore_path.write_text(default_content, encoding="utf-8")
    else:
        with contextlib.suppress(OSError):
            content = gitignore_path.read_text(encoding="utf-8")
            missing: list[str] = []
            if "deltas/by-status" not in content and "by-status" not in content:
                missing.append("deltas/by-status/")
            if ".cache" not in content:
                missing.append(".cache/")
            if missing:
                extra = "\n".join(missing)
                gitignore_path.write_text(
                    f"{content.rstrip()}\n\n# Eidos generated views and cache\n{extra}\n",
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
                with contextlib.suppress(OSError):
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
            except OSError:
                pass

            if status in ("proposed", "in_review", "approved", "in_progress", "draft"):
                active_link = views_dir / "active" / p.name
                rel_active = os.path.relpath(p, views_dir / "active")
                try:
                    active_link.symlink_to(rel_active)
                    synced["active"].append(p)
                except OSError:
                    pass
        except OSError:
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
        except OSError:
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
    specs_by_id, container_components, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
    return specs_by_id, container_components, set(invariant_map.keys())


def _build_spec_catalog_with_invariants(
    eidos_dir: Path,
) -> tuple[dict[str, tuple[Path, str, dict[str, Any]]], dict[str, str], dict[str, tuple[Path, dict[str, Any]]]]:
    """Build lookup indexes including owning file and dict for each invariant."""
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    container_components: dict[str, str] = {}  # comp_id -> container_id
    invariant_map: dict[str, tuple[Path, dict[str, Any]]] = {}

    for p in eidos_dir.rglob("*.adoc"):
        if "deltas" in p.parts or "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            if doc.doc_id:
                specs_by_id[doc.doc_id] = (p, doc.doc_type, doc.frontmatter)
                if doc.doc_type == "tech/container":
                    comps = (
                        doc.frontmatter.get("components", [])
                        or doc.frontmatter.get("c4_internal_components", [])
                    )
                    if isinstance(comps, list):
                        for comp in comps:
                            if isinstance(comp, dict) and comp.get("id"):
                                container_components[comp["id"]] = doc.doc_id
                invs = doc.frontmatter.get("invariants", []) or (
                    doc.frontmatter.get("technical", {}) or {}
                ).get("invariants", [])
                if isinstance(invs, list):
                    for inv in invs:
                        if isinstance(inv, dict) and inv.get("id"):
                            invariant_map[inv["id"]] = (p, inv)
                        elif isinstance(inv, str):
                            invariant_map[inv] = (p, {"id": inv})
        except OSError:
            continue

    return specs_by_id, container_components, invariant_map


def _snapshot_delta_baselines(eidos_dir: Path, delta_id: str) -> None:
    """Record current SHA-256 digests of all living specs into eidos/.cache/index.db for delta verification."""
    try:
        cache_dir = eidos_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = cache_dir / "index.db"
        specs_by_id, container_components, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
        rows: list[tuple[str, str, str]] = []
        for spec_id, (spec_path, _, _) in specs_by_id.items():
            if spec_path.exists():
                sha = hashlib.sha256(spec_path.read_bytes()).hexdigest()
                rows.append((delta_id, spec_id, sha))
                rows.append((delta_id, str(spec_path.resolve()), sha))
        for comp_id, cnt_id in container_components.items():
            if cnt_id in specs_by_id and specs_by_id[cnt_id][0].exists():
                sha = hashlib.sha256(specs_by_id[cnt_id][0].read_bytes()).hexdigest()
                rows.append((delta_id, comp_id, sha))
        for inv_id, (inv_path, inv_dict) in invariant_map.items():
            if inv_path.exists():
                inv_sha = hashlib.sha256(
                    json.dumps(inv_dict, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                rows.append((delta_id, inv_id, inv_sha))

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS delta_baselines ("
                "delta_id TEXT, target_id TEXT, baseline_sha256 TEXT, "
                "PRIMARY KEY (delta_id, target_id))"
            )
            conn.executemany(
                "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
    except (OSError, sqlite3.Error):
        return


def _check_or_record_baseline_sha(
    eidos_dir: Path,
    delta_id: str,
    delta_file: Path,
    target_key: str,
    target_file: Path | None,
    current_sha: str,
) -> bool:
    """Compare current_sha against SQLite delta_baselines (with mtime fallback when no baseline exists yet)."""
    try:
        cache_dir = eidos_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = cache_dir / "index.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS delta_baselines ("
                "delta_id TEXT, target_id TEXT, baseline_sha256 TEXT, "
                "PRIMARY KEY (delta_id, target_id))"
            )
            row = conn.execute(
                "SELECT baseline_sha256 FROM delta_baselines WHERE delta_id = ? AND target_id = ?",
                (delta_id, target_key),
            ).fetchone()
            if row is not None:
                return current_sha != row[0]

            if (
                target_file is not None
                and target_file.exists()
                and delta_file.exists()
                and target_file.stat().st_mtime_ns > delta_file.stat().st_mtime_ns
            ):
                return True

            conn.execute(
                "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                (delta_id, target_key, current_sha),
            )
            conn.commit()
            return False
    except (OSError, sqlite3.Error):
        if target_file is not None and target_file.exists() and delta_file.exists():
            return target_file.stat().st_mtime_ns > delta_file.stat().st_mtime_ns
        return False


def _evaluate_declarative_postcondition(
    m: dict[str, Any],
    target: str,
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]],
    container_components: dict[str, str],
    invariant_map: dict[str, tuple[Path, dict[str, Any]]],
) -> bool | None:
    """Evaluate declarative expect block or structural sub-target if present on a mutation.

    Returns True if satisfied, False if unsatisfied, or None if no declarative post-condition is specified.
    """
    expect = m.get("expect")
    if isinstance(expect, dict) and expect:
        target_dict: dict[str, Any] = {}
        raw_text = ""
        if target in invariant_map:
            inv_path, inv_data = invariant_map[target]
            target_dict = inv_data
            if inv_path.exists():
                raw_text = inv_path.read_text(encoding="utf-8")
        elif target in specs_by_id:
            spec_path, _, spec_fm = specs_by_id[target]
            target_dict = spec_fm or {}
            if spec_path.exists():
                raw_text = spec_path.read_text(encoding="utf-8")
        elif target in container_components:
            cnt_id = container_components[target]
            if cnt_id in specs_by_id:
                cnt_path, _, cnt_fm = specs_by_id[cnt_id]
                if cnt_path.exists():
                    raw_text = cnt_path.read_text(encoding="utf-8")
                for c in (cnt_fm or {}).get("components", []):
                    if isinstance(c, dict) and c.get("id") == target:
                        target_dict = c
                        break

        for k, exp_v in expect.items():
            if k == "contains":
                if str(exp_v) not in raw_text:
                    return False
                continue
            actual = target_dict.get(k)
            if isinstance(exp_v, str) and isinstance(actual, str):
                if exp_v not in actual and actual != exp_v:
                    return False
            elif isinstance(actual, list) and not isinstance(exp_v, list):
                if exp_v not in actual:
                    return False
            elif actual != exp_v:
                return False
        return True

    # Structural sub-target post-conditions
    t_cap = m.get("target_capability")
    t_inv = m.get("target_invariant_id")
    if t_cap and t_inv and t_cap in specs_by_id:
        _, _, cap_fm = specs_by_id[t_cap]
        invs = (cap_fm or {}).get("invariants", []) or ((cap_fm or {}).get("technical", {}) or {}).get("invariants", [])
        inv_ids = {i.get("id") if isinstance(i, dict) else str(i) for i in (invs or [])}
        return t_inv in inv_ids

    t_cnt = m.get("target_container")
    t_cmp = m.get("target_component")
    if t_cnt and t_cmp and t_cnt != t_cmp and t_cnt in specs_by_id:
        return container_components.get(t_cmp) == t_cnt or (
            t_cmp in specs_by_id and (specs_by_id[t_cmp][2] or {}).get("container") == t_cnt
        )

    owner_cnt = t_cnt or t_cmp
    t_ctr = m.get("target_contract")
    if owner_cnt and t_ctr and owner_cnt != t_ctr and owner_cnt in specs_by_id:
        _, _, cnt_fm = specs_by_id[owner_cnt]
        contracts = (cnt_fm or {}).get("contracts", {}) or {}
        all_ctrs = (
            (contracts.get("provides") or [])
            + (contracts.get("consumes") or [])
            + (contracts.get("requires") or [])
        )
        if t_ctr in all_ctrs:
            return True
        if t_ctr in specs_by_id:
            ctr_fm = specs_by_id[t_ctr][2] or {}
            return (ctr_fm.get("owning_container") or ctr_fm.get("owning_component")) == owner_cnt
        return False

    return None


def verify_delta_mutations(
    eidos_dir: Path, delta_id_or_path: str
) -> tuple[bool, str, DeltaVerificationReport | None]:
    """Verify that all semantic mutations declared in a SpecDelta are fulfilled in living specifications."""
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

    delta_status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
    specs_by_id, container_components, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
    invariants = set(invariant_map.keys())

    items: list[MutationVerificationItem] = []
    for m in raw_mutations:
        action = str(m.get("action", "")).strip().lower()
        target = _extract_mutation_target(m)
        details = str(m.get("details", "")).strip()

        verified = False
        status = "UNKNOWN"

        target_path_resolved: Path | None = None
        target_path_exists = False
        if "/" in target or target.endswith(".adoc"):
            clean_p = target.strip("'\"")
            cand_rel = eidos_dir / clean_p
            cand_abs = Path(clean_p)
            if cand_rel.exists():
                target_path_exists = True
                target_path_resolved = cand_rel.resolve()
            elif cand_abs.is_absolute() and cand_abs.exists():
                target_path_exists = True
                target_path_resolved = cand_abs.resolve()

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
        elif action == "add_invariant":
            if target in invariants:
                verified = True
                status = "FOUND"
            else:
                verified = False
                status = "MISSING"

        elif action == "patch_invariant":
            if target not in invariant_map:
                verified = False
                status = "MISSING"
            elif delta_status == "applied":
                verified = True
                status = "FOUND"
            else:
                decl_ok = _evaluate_declarative_postcondition(
                    m, target, specs_by_id, container_components, invariant_map
                )
                if decl_ok is not None:
                    verified = decl_ok
                    status = "MODIFIED" if decl_ok else "UNMODIFIED"
                else:
                    inv_file, inv_dict = invariant_map[target]
                    inv_sha = hashlib.sha256(
                        json.dumps(inv_dict, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest()
                    mod_ok = _check_or_record_baseline_sha(
                        eidos_dir, doc.doc_id, file_path, target, inv_file, inv_sha
                    )
                    verified = mod_ok
                    status = "MODIFIED" if mod_ok else "UNMODIFIED"

        elif action == "remove_invariant":
            if target not in invariants:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"

        # 3. Modification / Extension / Patch Actions
        elif action == "modify_architecture" or action.startswith(("modify_", "extend_", "patch_")):
            target_file: Path | None = None
            if target in specs_by_id:
                target_file = specs_by_id[target][0]
            elif target in container_components and container_components[target] in specs_by_id:
                target_file = specs_by_id[container_components[target]][0]
            elif target_path_resolved is not None:
                target_file = target_path_resolved
            elif action == "modify_architecture":
                for _, (p, d_type, _) in specs_by_id.items():
                    if d_type == "tech/architecture":
                        target_file = p
                        break

            if target_file is None or not target_file.exists():
                verified = False
                status = "MISSING"
            elif delta_status == "applied":
                verified = True
                status = "FOUND"
            else:
                decl_ok = _evaluate_declarative_postcondition(
                    m, target, specs_by_id, container_components, invariant_map
                )
                if decl_ok is not None:
                    verified = decl_ok
                    status = "MODIFIED" if decl_ok else "UNMODIFIED"
                else:
                    cur_sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
                    mod_ok = _check_or_record_baseline_sha(
                        eidos_dir, doc.doc_id, file_path, target, target_file, cur_sha
                    )
                    verified = mod_ok
                    status = "MODIFIED" if mod_ok else "UNMODIFIED"

        # 4. Standard Addition Actions
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
                    domain = domains[0] if domains else "DOM-CORE"
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
                    cnt_fm, _cnt_body, _ = extract_frontmatter(cnt_content)
                    if cnt_fm is not None:
                        comps = cnt_fm.setdefault("components", [])
                        new_comp = {
                            "id": target_id,
                            "name": target_id.replace("CMP-", "").replace("-", " ").title(),
                            "responsibility": details or f"Component responsible for {target_id}.",
                        }
                        if not any(c.get("id") == target_id for c in comps):
                            updated_content = _mutate_frontmatter_components(
                                cnt_content, "append", target_id, new_comp
                            )
                            cnt_file.write_text(updated_content, encoding="utf-8")
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
                        cnt_fm, _cnt_body, _ = extract_frontmatter(cnt_content)
                        if cnt_fm and "components" in cnt_fm:
                            updated_content = _mutate_frontmatter_components(
                                cnt_content, "remove", target_id
                            )
                            cnt_file.write_text(updated_content, encoding="utf-8")
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

    # Read and update frontmatter status to applied in-place while preserving YAML comments
    content = file_path.read_text(encoding="utf-8")
    fm, _body, err = extract_frontmatter(content)
    if err or not fm:
        return False, f"Failed to extract frontmatter: {err}", None, mutated_files

    new_content = _set_frontmatter_scalar(content, "status", "applied")

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
    with_plugin: bool = False,
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
        if "by-status" in p.parts or ".cache" in p.parts:
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

    return True, f"Successfully initialized empty Eidos living specification skeleton at '{eidos_dir}'.", created_files


def promote_component(
    component_id: str,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Promote an inline C4 Level 3 component to a dedicated specification file in eidos/architecture/components/.

    Returns:
        (component_spec_path, container_path)
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
    source_path_str = target_comp_data.get("source_path", "")

    # Place dedicated component specification centrally in eidos/architecture/components/
    components_dir = edir / "architecture" / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    slug = norm_id.replace("CMP-", "").lower().replace("_", "-")
    comp_file_path = components_dir / f"{slug}.adoc"
    rel_comp_ref = str(comp_file_path.relative_to(root)).replace("\\", "/")

    # Author centralized tech/component specification
    comp_name = target_comp_data.get("name", norm_id)
    responsibility = target_comp_data.get("responsibility", "In-process module logic and domain services.")

    component_content = f"""---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: {norm_id}
name: "{comp_name}"
container: {container_id}
status: active
source_path: "{source_path_str}"
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
    source_path = comp_fm.get("source_path", comp_fm.get("package_path", ""))

    # Update container frontmatter while preserving YAML comments and formatting
    raw_text = target_container_path.read_text(encoding="utf-8")
    fm_dict, _body_raw, _ = extract_frontmatter(raw_text)
    if fm_dict:
        new_c_content = _mutate_frontmatter_components(
            raw_text,
            "replace",
            norm_id,
            {
                "id": norm_id,
                "name": name,
                "responsibility": responsibility,
                "source_path": source_path,
            },
        )
        target_container_path.write_text(new_c_content, encoding="utf-8")

    # Safely remove the dedicated .adoc file
    comp_file.unlink(missing_ok=True)

    return target_container_path, comp_file

