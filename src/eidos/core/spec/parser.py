"""Parser for AsciiDoc files containing YAML frontmatter."""

import re
from pathlib import Path
from typing import Any

import fast_yaml

from eidos.core.spec.models import TYPE_TO_SCHEMA_NAME, SpecDocument

_OPENING_DELIMITER_RE = re.compile(r"\A---[ \t]*(?:\r?\n|\Z)")
_CLOSING_DELIMITER_RE = re.compile(r"^---[ \t]*(?:\r?\n|\Z)", re.MULTILINE)


class YamlError(ValueError):
    """Raised when fast_yaml fails to parse or emit a YAML document."""


def safe_load_yaml(stream_or_str: str | bytes | Any) -> Any:
    """Parse a YAML string, bytes buffer, or file-like stream into native Python types via fast_yaml."""
    raw = stream_or_str.read() if hasattr(stream_or_str, "read") else stream_or_str
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        raw_str = bytes(raw).decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        raw_str = raw
    else:
        raise YamlError(f"Unsupported YAML input type: {type(raw).__name__}")
    if not raw_str.strip():
        return None
    try:
        if "\n---" in raw_str:
            docs = list(fast_yaml.safe_load_all(raw_str))
            if not docs:
                return None
            if len(docs) > 1:
                raise YamlError("Expected a single document in the YAML stream, found multiple documents")
            return docs[0]
        return fast_yaml.safe_load(raw_str)
    except YamlError:
        raise
    except (fast_yaml.YAMLError, ValueError) as exc:
        raise YamlError(str(exc) or "Invalid YAML syntax") from exc


def safe_dump_yaml(data: Any, stream: Any = None, **kwargs: Any) -> str:
    """Serialize Python data into clean block-style YAML via fast_yaml."""
    sort_keys = bool(kwargs.get("sort_keys", False))
    allow_unicode = bool(kwargs.get("allow_unicode", True))
    default_flow_style = kwargs.get("default_flow_style", False)
    raw_width = kwargs.get("width", 1000)
    width = min(max(int(raw_width), 20), 1000) if raw_width is not None else 1000
    try:
        out: str = (
            fast_yaml.safe_dump(
                data,
                sort_keys=sort_keys,
                allow_unicode=allow_unicode,
                default_flow_style=default_flow_style,
                width=width,
            )
            or ""
        )
    except (fast_yaml.YAMLError, ValueError, TypeError) as exc:
        raise YamlError(str(exc) or "Failed to emit YAML") from exc

    if stream is not None and hasattr(stream, "write"):
        stream.write(out)
    return out


def split_frontmatter_and_body(content: str) -> tuple[str | None, str]:
    """Split an AsciiDoc specification into (raw_frontmatter_yaml_or_None, body_text).

    Anchored strictly at file start (after optional UTF-8 BOM) and ignores indented
    '---' lines inside YAML block scalars or multiline strings.
    """
    if not content:
        return None, ""
    cleaned = content.lstrip("\ufeff")
    open_match = _OPENING_DELIMITER_RE.match(cleaned)
    if not open_match or open_match.end() >= len(cleaned):
        return None, content

    rest = cleaned[open_match.end() :]
    close_matches = list(_CLOSING_DELIMITER_RE.finditer(rest))
    if not close_matches:
        return None, content

    # Try each column-0 '---' delimiter in order; if there are multiple column-0 '---'
    # lines (e.g. an unindented '---' inside a multiline quoted scalar or in the body),
    # prefer the first delimiter whose preceding slice is a valid YAML mapping.
    chosen = close_matches[0]
    if len(close_matches) > 1:
        for cand in close_matches:
            cand_yaml = rest[: cand.start()].rstrip("\r\n")
            try:
                parsed = safe_load_yaml(cand_yaml)
                if isinstance(parsed, dict):
                    chosen = cand
                    break
            except YamlError:
                continue

    raw_yaml = rest[: chosen.start()].rstrip("\r\n")
    body = rest[chosen.end() :]
    return raw_yaml, body


def dump_spec_document(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter mapping with safe_dump_yaml and wrap it with an AsciiDoc body."""
    fm_yaml = safe_dump_yaml(frontmatter)
    return f"---\n{fm_yaml}---\n{body.lstrip(chr(10))}"


def parse_spec_file(path: Path) -> SpecDocument:
    """Parse an .adoc specification file with YAML frontmatter."""
    return _parse_spec_content(path, path.read_text(encoding="utf-8"))


_NON_ROOT_SPEC_SUBDIRS = (
    "systems",
    "design",
    "domains",
    "architecture",
    "containers",
    "components",
    "contracts",
    "policies",
    "personas",
    "deltas",
    "explorations",
    "clients",
    "screens",
    "flows",
    "capabilities",
)


def _parse_spec_content(path: Path, content: str) -> SpecDocument:
    """Parse an .adoc specification from pre-read content."""
    if not content.strip():
        parent_lower = path.parent.name.lower()
        ancestor_lowers = [p.lower() for p in path.parts[:-1]]
        is_root_system = (
            path.name == "system.adoc"
            and parent_lower not in _NON_ROOT_SPEC_SUBDIRS
            and (
                parent_lower == "eidos"
                or (
                    "eidos" not in ancestor_lowers
                    and (not path.parent.parent or path.parent.parent.name.lower() not in _NON_ROOT_SPEC_SUBDIRS)
                )
            )
        )
        if is_root_system:
            return SpecDocument(
                file_path=path,
                frontmatter={},
                body="",
                doc_type="product/system",
                doc_id="SYS-UNINITIALIZED",
            )
        return SpecDocument(
            file_path=path,
            frontmatter={},
            body="",
            doc_type="unknown",
            doc_id=path.stem,
            errors=["Missing YAML frontmatter block (delimited by '---')"],
        )

    frontmatter_dict, body, parse_error = extract_frontmatter(content)

    doc = SpecDocument(
        file_path=path,
        frontmatter=frontmatter_dict or {},
        body=body,
        doc_type="unknown",
        doc_id=path.stem,
    )
    fm_model = doc.model
    doc.doc_type = fm_model.type or "unknown"
    doc.doc_id = fm_model.id or path.stem
    schema_rel = TYPE_TO_SCHEMA_NAME.get(doc.doc_type)
    doc.schema_ref = (
        f"schemas/v1/{schema_rel}"
        if schema_rel is not None
        else (f"schemas/v1/{doc.doc_type}.schema.yaml" if "/" in doc.doc_type else None)
    )
    doc.title = fm_model.title

    if parse_error:
        doc.errors.append(f"Frontmatter parse error: {parse_error}")
    elif not frontmatter_dict:
        doc.errors.append("Missing YAML frontmatter block (delimited by '---')")

    return doc


def extract_frontmatter(content: str) -> tuple[dict | None, str, str | None]:
    """Extract YAML frontmatter between file-start-anchored '---' markers."""
    cleaned = content.lstrip("\ufeff")
    if not _OPENING_DELIMITER_RE.match(cleaned):
        return None, content, "No opening frontmatter delimiter ('---')"

    raw_yaml, body = split_frontmatter_and_body(cleaned)
    if raw_yaml is None:
        return None, content, "No closing frontmatter delimiter ('---')"

    try:
        data = safe_load_yaml(raw_yaml)
        if not isinstance(data, dict):
            return None, body, "Frontmatter must be a YAML mapping (key-value dictionary)"
        return data, body, None
    except YamlError as exc:
        return None, body, str(exc)
