"""Validator for Eidos specifications against JSON/YAML schemas."""

from importlib.resources import files
from pathlib import Path
from typing import Dict, List, Optional
import jsonschema
import yaml

from eidos.core.models import SpecDocument, ValidationReport
from eidos.core.parser import parse_spec_file

TYPE_TO_SCHEMA_NAME = {
    "product/system": "system.schema.yaml",
    "product/domain": "domain.schema.yaml",
    "product/capability": "capability.schema.yaml",
    "product/policy": "policy.schema.yaml",
    "product/persona": "persona.schema.yaml",
    "product/exploration": "exploration.schema.yaml",
    "delta/mutation": "delta.schema.yaml",
    "tech/architecture": "architecture.schema.yaml",
    "tech/component": "component.schema.yaml",
    "tech/contract": "contract.schema.yaml",
}


class SpecValidator:
    """Validates specification documents using bundled and custom schemas."""

    def __init__(self):
        self._schema_cache: Dict[str, dict] = {}
        self._load_bundled_schemas()

    def _load_bundled_schemas(self):
        """Preload standard schemas from the eidos package data."""
        try:
            schema_dir = files("eidos.schemas.v1")
            for item in schema_dir.iterdir():
                if item.name.endswith(".schema.yaml") or item.name.endswith(".schema.json"):
                    content = item.read_text(encoding="utf-8")
                    data = yaml.safe_load(content)
                    self._schema_cache[item.name] = data
                    self._schema_cache[f"schemas/v1/{item.name}"] = data
        except Exception:
            # Fallback if package is not installed as site-packages yet
            local_schema_dir = Path(__file__).parent.parent / "schemas" / "v1"
            if local_schema_dir.exists():
                for p in local_schema_dir.glob("*.schema.yaml"):
                    data = yaml.safe_load(p.read_text(encoding="utf-8"))
                    self._schema_cache[p.name] = data
                    self._schema_cache[f"schemas/v1/{p.name}"] = data

    def resolve_schema(self, doc: SpecDocument) -> Optional[dict]:
        """Resolve the JSON schema for a document based on its type or explicit schema attribute."""
        # 1. Check explicit schema reference
        if doc.schema_ref:
            norm_ref = doc.schema_ref.replace(".spec/", "").strip()
            if norm_ref in self._schema_cache:
                return self._schema_cache[norm_ref]
            basename = Path(norm_ref).name
            if basename in self._schema_cache:
                return self._schema_cache[basename]

        # 2. Check document type mapping
        if doc.doc_type in TYPE_TO_SCHEMA_NAME:
            schema_name = TYPE_TO_SCHEMA_NAME[doc.doc_type]
            return self._schema_cache.get(schema_name)

        return None

    def validate_document(self, doc: SpecDocument) -> List[str]:
        """Validate a single parsed document against its schema."""
        if not doc.frontmatter:
            return doc.errors

        schema = self.resolve_schema(doc)
        if not schema:
            doc.errors.append(f"No schema found for document type '{doc.doc_type}'")
            return doc.errors

        validator = jsonschema.Draft202012Validator(schema)
        errors = []
        for err in validator.iter_errors(doc.frontmatter):
            loc = ".".join(str(p) for p in err.absolute_path)
            prefix = f"Property '{loc}'" if loc else "Root frontmatter"
            errors.append(f"{prefix}: {err.message}")

        doc.errors.extend(errors)
        return doc.errors

    def validate_directory(self, root_dir: Path) -> ValidationReport:
        """Scan a directory for .adoc files, parse, and validate each against its schema."""
        report = ValidationReport()
        adoc_files = sorted(root_dir.rglob("*.adoc"))

        for file_path in adoc_files:
            # Skip README and partial include files
            if file_path.name.lower() == "readme.adoc" or file_path.name.startswith("_"):
                continue

            doc = parse_spec_file(file_path)
            self.validate_document(doc)
            report.documents.append(doc)
            if not doc.is_valid:
                report.schema_errors[str(file_path)] = doc.errors

        # Cross-reference validation
        self._validate_cross_references(report, root_dir)

        return report

    def _validate_cross_references(self, report: ValidationReport, root_dir: Path):
        """Ensure all relative file links in YAML frontmatters resolve to existing files."""
        for doc in report.documents:
            self._check_dict_references(doc.frontmatter, doc.file_path, root_dir, report)

    def _check_dict_references(self, val, source_file: Path, root_dir: Path, report: ValidationReport):
        """Recursively check string fields ending in .adoc to verify they exist."""
        if isinstance(val, dict):
            for v in val.values():
                self._check_dict_references(v, source_file, root_dir, report)
        elif isinstance(val, list):
            for item in val:
                self._check_dict_references(item, source_file, root_dir, report)
        elif isinstance(val, str) and val.endswith(".adoc"):
            # Resolve relative to repo root or doc folder
            ref_path_from_root = root_dir.parent / val if root_dir.name in ["eidos", ".spec"] else root_dir / val
            ref_path_from_doc = source_file.parent / val
            if not (ref_path_from_root.exists() or ref_path_from_doc.exists()):
                report.broken_references.append(
                    f"Broken reference in {source_file.name}: cannot locate '{val}'"
                )

