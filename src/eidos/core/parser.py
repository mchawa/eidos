"""Parser for AsciiDoc files containing YAML frontmatter."""

from pathlib import Path

import yaml

from eidos.core.models import SpecDocument


def parse_spec_file(path: Path) -> SpecDocument:
    """Parse an .adoc specification file with YAML frontmatter."""
    content = path.read_text(encoding="utf-8")
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
    """Extract YAML frontmatter between leading '---' markers."""
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return None, content, "No opening frontmatter delimiter ('---')"

    # Find the closing '---'
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return None, content, "No closing frontmatter delimiter ('---')"

    yaml_text = parts[1]
    body = parts[2]

    try:
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            return None, body, "Frontmatter must be a YAML mapping (key-value dictionary)"
        return data, body, None
    except yaml.YAMLError as exc:
        return None, body, str(exc)
