"""Parser for AsciiDoc files containing YAML frontmatter."""

import re
from pathlib import Path

import yaml

try:
    from yaml import CSafeLoader as _YamlSafeLoader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _YamlSafeLoader

from eidos.core.spec.models import SpecDocument

_FRONTMATTER_DELIMITER_RE = re.compile(r"^---\s*$", re.MULTILINE)


def parse_spec_file(path: Path) -> SpecDocument:
    """Parse an .adoc specification file with YAML frontmatter."""
    return _parse_spec_content(path, path.read_text(encoding="utf-8"))


def _parse_spec_content(path: Path, content: str) -> SpecDocument:
    """Parse an .adoc specification from pre-read content."""
    if not content.strip():
        doc_type = "product/system" if path.name == "system.adoc" else "unknown"
        return SpecDocument(
            file_path=path,
            frontmatter={},
            body="",
            doc_type=doc_type,
            doc_id=path.stem,
        )

    frontmatter_dict, body, parse_error = extract_frontmatter(content)

    doc_type = frontmatter_dict.get("type", "unknown") if frontmatter_dict else "unknown"
    doc_id = frontmatter_dict.get("id", path.stem) if frontmatter_dict else path.stem
    schema_ref = frontmatter_dict.get("schema") if frontmatter_dict else None
    title = (frontmatter_dict.get("title") or frontmatter_dict.get("name")) if frontmatter_dict else None

    doc = SpecDocument(
        file_path=path,
        frontmatter=frontmatter_dict or {},
        body=body,
        doc_type=doc_type,
        doc_id=doc_id,
        schema_ref=schema_ref,
        title=title,
    )

    if parse_error:
        doc.errors.append(f"Frontmatter parse error: {parse_error}")
    elif not frontmatter_dict:
        doc.errors.append("Missing YAML frontmatter block (delimited by '---')")

    return doc


def extract_frontmatter(content: str) -> tuple[dict | None, str, str | None]:
    """Extract YAML frontmatter between line-anchored '---' markers."""
    cleaned = content.lstrip("\ufeff")
    stripped = cleaned.lstrip()
    if not stripped.startswith("---"):
        return None, content, "No opening frontmatter delimiter ('---')"

    matches = list(_FRONTMATTER_DELIMITER_RE.finditer(stripped))
    if not matches or matches[0].start() != 0:
        return None, content, "No opening frontmatter delimiter ('---')"
    if len(matches) < 2:
        return None, content, "No closing frontmatter delimiter ('---')"

    yaml_text = stripped[matches[0].end() : matches[1].start()]
    body = stripped[matches[1].end() :]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]

    try:
        data = yaml.load(yaml_text, Loader=_YamlSafeLoader)
        if not isinstance(data, dict):
            return None, body, "Frontmatter must be a YAML mapping (key-value dictionary)"
        return data, body, None
    except yaml.YAMLError as exc:
        return None, body, str(exc)
