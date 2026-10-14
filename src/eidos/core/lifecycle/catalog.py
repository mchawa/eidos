"""Shared low-level helpers for the lifecycle components: spec lookup, ID normalization, YAML frontmatter
surgery, and catalog indexes. This module sits at the bottom of the core import graph (DLT-2026-038)."""

import re
from pathlib import Path
from typing import Any

import yaml

from eidos.core.spec.parser import parse_spec_file

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
