"""Shared low-level helpers for the lifecycle components: spec lookup, ID normalization, YAML frontmatter
surgery, and catalog indexes. This module sits at the bottom of the core import graph (DLT-2026-038)."""

import re
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.storage import get_cached_spec_document
from eidos.core.spec.models import DOC_TYPE_TO_PREFIX, VALID_SPEC_TYPES
from eidos.core.spec.parser import (
    YamlError,
    dump_spec_document,
    extract_frontmatter,
    safe_load_yaml,
    split_frontmatter_and_body,
)


def normalize_id(doc_type_key: str, raw_id: str) -> str:
    """Ensure document ID follows standard prefix conventions."""
    cleaned = raw_id.strip().upper().replace(" ", "-")
    canonical_type = VALID_SPEC_TYPES.get(doc_type_key.lower())
    pfx = DOC_TYPE_TO_PREFIX.get(canonical_type or "")
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
    val = str(value)
    if (
        len(val) >= 2
        and "\n" not in val
        and "\r" not in val
        and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'"))
    ):
        try:
            loaded = safe_load_yaml(val)
            if isinstance(loaded, (str, int, float, bool)):
                return val
        except YamlError:
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
            parsed_scalar = safe_load_yaml(val)
            if not isinstance(parsed_scalar, str) or parsed_scalar != val:
                needs_quoting = True
        except YamlError:
            needs_quoting = True
    if needs_quoting:
        return orjson.dumps(val).decode("utf-8")
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
    stem_only = Path(target_stem_lower).name.replace("_", "-")
    prefix_needle = f"{stem_only}-" if stem_only.startswith("dlt-") else ""
    prefix_candidates: list[Path] = []
    for p in sorted(eidos_dir.rglob("*.adoc")):
        if p.is_symlink():
            continue
        if p.name.lower() == target_lower or p.stem.lower() == target_stem_lower:
            return p.resolve()
        matched_prefix = bool(prefix_needle and p.stem.lower().replace("_", "-").startswith(prefix_needle))
        try:
            doc, _, _ = get_cached_spec_document(p)
            if doc is not None and doc.doc_id:
                doc_id_lower = doc.doc_id.lower()
                if doc_id_lower in (target_lower, target_stem_lower):
                    return p.resolve()
                if prefix_needle and doc_id_lower.replace("_", "-").startswith(prefix_needle):
                    matched_prefix = True
        except OSError:
            pass
        if matched_prefix:
            prefix_candidates.append(p.resolve())

    if len(prefix_candidates) == 1:
        return prefix_candidates[0]
    if len(prefix_candidates) > 1:
        active_candidates = [c for c in prefix_candidates if "archive" not in c.parts]
        if len(active_candidates) == 1:
            return active_candidates[0]

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
) -> tuple[dict[str, tuple[Path, str, dict[str, Any]]], set[str]]:
    """Build fast lookup indexes across living specs: specs_by_id, invariants."""
    specs_by_id, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
    return specs_by_id, set(invariant_map.keys())


def _build_spec_catalog_with_invariants(
    eidos_dir: Path,
) -> tuple[dict[str, tuple[Path, str, dict[str, Any]]], dict[str, tuple[Path, dict[str, Any]]]]:
    """Build lookup indexes including owning file and dict for each invariant via ArchitecturalIndex."""
    if not eidos_dir.exists():
        return {}, {}

    resolved_eidos = eidos_dir.resolve()
    repo_root = resolved_eidos.parent if resolved_eidos.name == "eidos" else resolved_eidos
    from eidos.core.index.graph_index import get_architectural_index

    idx = get_architectural_index(eidos_dir=resolved_eidos, repo_root=repo_root)
    return idx.get_spec_catalog(eidos_dir=eidos_dir)
