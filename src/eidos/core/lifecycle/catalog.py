"""Shared low-level helpers for the lifecycle components: spec lookup, ID normalization, YAML frontmatter
surgery, and catalog indexes. This module sits at the bottom of the core import graph (DLT-2026-038)."""

import re
from pathlib import Path
from typing import Any

import yaml

from eidos.core.index.storage import (
    get_cached_spec_document,
    load_spec_validation_cache,
)
from eidos.core.spec.parser import (
    dump_spec_document,
    extract_frontmatter,
    split_frontmatter_and_body,
)

VALID_SPEC_TYPES = {
    "system": "product/system",
    "product/system": "product/system",
    "architecture": "tech/architecture",
    "tech/architecture": "tech/architecture",
    "delta": "delta/mutation",
    "mutation": "delta/mutation",
    "delta/mutation": "delta/mutation",
    "capability": "product/capability",
    "product/capability": "product/capability",
    "domain": "product/domain",
    "product/domain": "product/domain",
    "policy": "product/policy",
    "product/policy": "product/policy",
    "contract": "tech/contract",
    "tech/contract": "tech/contract",
    "container": "tech/container",
    "tech/container": "tech/container",
    "component": "tech/component",
    "tech/component": "tech/component",
    "persona": "product/persona",
    "product/persona": "product/persona",
    "exploration": "product/exploration",
    "product/exploration": "product/exploration",
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
        "system": "SYS-",
        "product/system": "SYS-",
        "architecture": "ARCH-",
        "tech/architecture": "ARCH-",
        "delta": "DLT-",
        "mutation": "DLT-",
        "delta/mutation": "DLT-",
        "capability": "CAP-",
        "product/capability": "CAP-",
        "domain": "DOM-",
        "product/domain": "DOM-",
        "policy": "POL-",
        "product/policy": "POL-",
        "contract": "CTR-",
        "tech/contract": "CTR-",
        "container": "CNT-",
        "tech/container": "CNT-",
        "component": "CMP-",
        "tech/component": "CMP-",
        "persona": "PER-",
        "product/persona": "PER-",
        "exploration": "EXP-",
        "product/exploration": "EXP-",
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
    return dump_spec_document(frontmatter, body)


def _split_raw_frontmatter(raw_content: str) -> tuple[str, str] | None:
    """Split a spec document into (raw_frontmatter_lines, body_text) without re-serializing YAML."""
    raw_yaml, body = split_frontmatter_and_body(raw_content)
    if raw_yaml is None:
        return None
    return raw_yaml, body


_YAML_KEYWORD_SCALARS: frozenset[str] = frozenset(
    {"true", "false", "null", "~", "yes", "no", "on", "off", "y", "n", ".inf", "-.inf", "+.inf", ".nan"}
)


def _format_yaml_scalar_value(value: str) -> str:
    """Safely format a scalar string for in-place single-line YAML insertion without corrupting frontmatter."""
    import json

    val = str(value)
    if (
        len(val) >= 2
        and "\n" not in val
        and "\r" not in val
        and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'"))
    ):
        try:
            loaded = yaml.safe_load(val)
            if isinstance(loaded, (str, int, float, bool)):
                return val
        except yaml.YAMLError:
            pass
    needs_quoting = (
        not val
        or val != val.strip()
        or val.lower() in _YAML_KEYWORD_SCALARS
        or val.startswith(("- ", "? "))
        or any(
            ch in val
            for ch in (":", "#", "\n", "\r", '"', "'", "{", "}", "[", "]", "&", "*", "!", "|", ">", "%", "@", "`", ",")
        )
    )
    if not needs_quoting:
        try:
            parsed_scalar = yaml.safe_load(val)
            if not isinstance(parsed_scalar, str) or parsed_scalar != val:
                needs_quoting = True
        except yaml.YAMLError:
            needs_quoting = True
    if needs_quoting:
        return json.dumps(val, ensure_ascii=False)
    return val


def _split_yaml_inline_comment(val_and_comment: str) -> tuple[str, str]:
    """Split a single-line YAML scalar value from a trailing '# ...' comment while respecting quotes."""
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(val_and_comment):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (idx == 0 or val_and_comment[idx - 1] in (" ", "\t")):
            ws_start = idx
            while ws_start > 0 and val_and_comment[ws_start - 1] in (" ", "\t"):
                ws_start -= 1
            return val_and_comment[:ws_start], val_and_comment[ws_start:]
    return val_and_comment.rstrip(" \t"), ""


def _set_frontmatter_scalar(raw_content: str, key: str, value: str) -> str:
    """Update a top-level scalar key in YAML frontmatter while preserving comments, quoting, and blank lines."""
    parts = _split_raw_frontmatter(raw_content)
    if parts is None:
        return raw_content
    raw_yaml, body = parts
    safe_val = _format_yaml_scalar_value(value)
    key_re = re.compile(rf"^({re.escape(key)}[ \t]*:[ \t]*)([^\r\n]*)$")
    lines = raw_yaml.splitlines()
    found = False
    out_lines: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = key_re.match(line) if not found else None
        if m is not None:
            found = True
            key_indent = len(line) - len(line.lstrip(" \t"))
            _old_val, trailing_comment = _split_yaml_inline_comment(m.group(2))
            prefix = m.group(1) if m.group(1).endswith((" ", "\t")) else f"{m.group(1)} "
            out_lines.append(f"{prefix}{safe_val}{trailing_comment}")
            i += 1
            trailing_blanks: list[str] = []
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    trailing_blanks.append(nxt)
                    i += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" \t"))
                if nxt_indent > key_indent:
                    trailing_blanks.clear()
                    i += 1
                    continue
                break
            out_lines.extend(trailing_blanks)
            continue
        out_lines.append(line)
        i += 1

    new_yaml = "\n".join(out_lines) if found else f"{raw_yaml.rstrip()}\n{key}: {safe_val}"
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

    def _fallback_mutate_parsed() -> str:
        try:
            parsed_fm = yaml.safe_load(raw_yaml)
        except yaml.YAMLError:
            return raw_content
        if not isinstance(parsed_fm, dict):
            return raw_content
        comps = parsed_fm.get("components")
        if not isinstance(comps, list):
            comps = []
            parsed_fm["components"] = comps
        if op == "append" and item_dict is not None:
            comps.append(item_dict)
        elif op in ("replace", "replace_with_ref", "replace_inline") and item_dict is not None:
            for idx_c, c in enumerate(comps):
                if isinstance(c, dict) and c.get("id") == target_id:
                    comps[idx_c] = item_dict
                    break
        elif op == "remove":
            parsed_fm["components"] = [
                c for c in comps if not (isinstance(c, dict) and c.get("id") == target_id)
            ]
        return dump_spec_document(parsed_fm, body)

    lines = raw_yaml.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    comp_idx = next(
        (i for i, ln in enumerate(lines) if re.match(r"^components\s*:", ln)),
        None,
    )
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

    header_after_colon, header_comment = _split_yaml_inline_comment(
        lines[comp_idx].split(":", 1)[1].rstrip("\r\n")
    )
    if header_after_colon.strip().startswith("["):
        return _fallback_mutate_parsed()

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

    if not item_starts and op in ("replace", "replace_with_ref", "replace_inline", "remove"):
        return _fallback_mutate_parsed()

    if op == "append" and item_dict is not None:
        lines.insert(end_idx, _format_component_item(item_dict, base_indent))
        return f"---\n{''.join(lines).rstrip(chr(10))}\n---\n{body}"

    mutated = False
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
            mutated = True
            if op in ("replace", "replace_with_ref", "replace_inline") and item_dict is not None:
                lines[start_line:stop_line] = [_format_component_item(item_dict, base_indent)]
            elif op == "remove":
                if len(item_starts) == 1:
                    del lines[comp_idx + 1 : end_idx]
                    lines[comp_idx] = f"components: []{header_comment}\n"
                else:
                    del lines[start_line:stop_line]
            break

    if not mutated and op in ("replace", "replace_with_ref", "replace_inline", "remove"):
        return _fallback_mutate_parsed()

    return f"---\n{''.join(lines).rstrip(chr(10))}\n---\n{body}"


def _single_line(text: str) -> str:
    """Collapse whitespace/newlines so user input cannot break AsciiDoc headings."""
    return " ".join(str(text).split())


def find_spec_by_id_or_path(eidos_dir: Path, target: str) -> Path | None:
    """Resolve a document ID, relative path, or filename to an existing spec path."""
    target_clean = target.strip()
    if not target_clean:
        return None
    eidos_resolved = eidos_dir.resolve()

    # 1. Exact path or relative path (must be a .adoc file inside eidos_dir)
    direct_path = Path(target_clean)
    if direct_path.suffix.lower() == ".adoc" and direct_path.exists() and direct_path.is_file():
        resolved_direct = direct_path.resolve()
        if resolved_direct.is_relative_to(eidos_resolved):
            return resolved_direct

    candidates = [target_clean]
    if eidos_resolved.name and target_clean.replace("\\", "/").startswith(f"{eidos_resolved.name}/"):
        candidates.append(target_clean.replace("\\", "/").removeprefix(f"{eidos_resolved.name}/"))

    for cand_str in candidates:
        rel_path = eidos_dir / cand_str
        if rel_path.suffix.lower() == ".adoc" and rel_path.exists() and rel_path.is_file():
            resolved_rel = rel_path.resolve()
            if resolved_rel.is_relative_to(eidos_resolved):
                return resolved_rel

        if not cand_str.endswith(".adoc"):
            rel_adoc = eidos_dir / f"{cand_str}.adoc"
            if rel_adoc.exists() and rel_adoc.is_file():
                resolved_adoc = rel_adoc.resolve()
                if resolved_adoc.is_relative_to(eidos_resolved):
                    return resolved_adoc

    # 2. Search by ID or filename across all .adoc files in eidos_dir
    target_lower = target_clean.lower()
    target_stem_lower = target_lower.removesuffix(".adoc")
    for p in eidos_dir.rglob("*.adoc"):
        if p.is_symlink():
            continue
        if p.name.lower() == target_lower or p.stem.lower() == target_stem_lower:
            return p.resolve()
        try:
            doc, _, _ = get_cached_spec_document(p)
            if doc is not None and doc.doc_id and doc.doc_id.lower() in (target_lower, target_stem_lower):
                return p.resolve()
        except OSError:
            continue

    return None


def find_references_to_spec(eidos_dir: Path, target_id: str, target_path: Path) -> list[Path]:
    """Find other specification documents that reference target_id or target_path using word-bounded matching."""
    referencing_files: list[Path] = []
    seen_resolved: set[Path] = set()
    target_resolved = target_path.resolve()
    eidos_resolved = eidos_dir.resolve()
    rel_path_str = (
        str(target_resolved.relative_to(eidos_resolved)).replace("\\", "/")
        if target_resolved.is_relative_to(eidos_resolved)
        else target_path.name
    )
    id_pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(target_id)}(?![A-Za-z0-9_-])", re.IGNORECASE)
    eidos_prefix = rf"(?:{re.escape(eidos_resolved.name)}/|eidos/)?" if eidos_resolved.name else r"(?:eidos/)?"
    path_pattern = re.compile(rf"(?<![A-Za-z0-9_./-]){eidos_prefix}{re.escape(rel_path_str)}(?![A-Za-z0-9_./-])")

    status_re = re.compile(
        r"^status[ \t]*:[ \t]*['\"]?(applied|rejected)['\"]?[ \t]*(?:#.*)?$",
        re.MULTILINE | re.IGNORECASE,
    )
    for p in sorted(eidos_dir.rglob("*.adoc")):
        if p.is_symlink():
            continue
        resolved_p = p.resolve()
        if resolved_p == target_resolved or resolved_p in seen_resolved:
            continue
        try:
            content = p.read_text(encoding="utf-8")
            if "deltas" in p.parts:
                fm_dict, _, _ = extract_frontmatter(content)
                if isinstance(fm_dict, dict):
                    if str(fm_dict.get("status", "")).strip().lower() in ("applied", "rejected"):
                        continue
                else:
                    raw_yaml, _ = split_frontmatter_and_body(content)
                    if raw_yaml and status_re.search(raw_yaml):
                        continue
            if id_pattern.search(content) or path_pattern.search(content):
                referencing_files.append(resolved_p)
                seen_resolved.add(resolved_p)
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
    """Build lookup indexes including owning file and dict for each invariant via shared SpecDocument cache and FrontmatterModel."""
    adoc_files: list[Path] = []
    if eidos_dir.exists():
        for p in sorted(eidos_dir.rglob("*.adoc")):
            if "deltas" in p.parts:
                continue
            adoc_files.append(p)

    resolved_eidos = eidos_dir.resolve()
    repo_root = resolved_eidos.parent if resolved_eidos.name == "eidos" else resolved_eidos
    db_path = resolved_eidos / ".cache" / "index.db"
    cached_rows = load_spec_validation_cache(db_path) if db_path.exists() else {}

    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    container_components: dict[str, str] = {}  # comp_id -> container_id
    invariant_map: dict[str, tuple[Path, dict[str, Any]]] = {}

    for p in adoc_files:
        try:
            st = p.stat()
            res_p = p.resolve()
            rel_key: str | None = None
            for base in (repo_root, resolved_eidos):
                try:
                    cand = str(res_p.relative_to(base)).replace("\\", "/")
                    if cand in cached_rows:
                        rel_key = cand
                        break
                except ValueError:
                    continue
            doc, _, _ = get_cached_spec_document(
                p,
                mtime_ns=st.st_mtime_ns,
                st_size=st.st_size,
                cached_row=cached_rows.get(rel_key) if rel_key else None,
            )
            if doc is not None:
                model = doc.model
                explicit_id = model.id
                if explicit_id:
                    specs_by_id[explicit_id] = (p, doc.doc_type, doc.frontmatter)
                    if doc.doc_type == "tech/container":
                        for comp in model.components:
                            if comp.id:
                                container_components[comp.id] = explicit_id
                    for inv in model.all_invariants():
                        if inv.id:
                            invariant_map[inv.id] = (p, inv.model_dump(exclude_none=True))
        except OSError:
            continue

    return specs_by_id, container_components, invariant_map
