"""Parser for AsciiDoc files containing YAML frontmatter."""

import re
from pathlib import Path
from typing import Any

import yaml

try:
    from yaml import CSafeLoader as _YamlSafeLoader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _YamlSafeLoader

from eidos.core.spec.models import SpecDocument

_OPENING_DELIMITER_RE = re.compile(r"\A---[ \t]*(?:\r?\n|\Z)")
_CLOSING_DELIMITER_RE = re.compile(r"^---[ \t]*(?:\r?\n|\Z)", re.MULTILINE)


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
                parsed = yaml.load(cand_yaml, Loader=_YamlSafeLoader)
                if isinstance(parsed, dict):
                    chosen = cand
                    break
            except yaml.YAMLError:
                continue

    raw_yaml = rest[: chosen.start()].rstrip("\r\n")
    body = rest[chosen.end() :]
    return raw_yaml, body


def dump_spec_document(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter mapping with yaml.safe_dump and wrap it with an AsciiDoc body."""
    fm_yaml = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=4096,
    )
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
    doc.schema_ref = (
        f"schemas/v1/{doc.doc_type}.schema.yaml"
        if doc.doc_type != "delta/mutation" and "/" in doc.doc_type
        else ("schemas/v1/delta/delta.schema.yaml" if doc.doc_type == "delta/mutation" else None)
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
        data = yaml.load(raw_yaml, Loader=_YamlSafeLoader)
        if not isinstance(data, dict):
            return None, body, "Frontmatter must be a YAML mapping (key-value dictionary)"
        return data, body, None
    except yaml.YAMLError as exc:
        return None, body, str(exc)
