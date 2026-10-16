"""Validator for Eidos specifications against JSON/YAML schemas."""

import ast
import contextlib
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.spec.parser import parse_spec_file
from eidos.core.workspace.assets import get_v1_schemas_dir

ENTITY_ID_PREFIXES = (
    "CNT-",
    "CMP-",
    "CTR-",
    "CAP-",
    "DOM-",
    "POL-",
    "PER-",
    "DLT-",
    "DS-",
    "UIC-",
    "SCR-",
    "CLT-",
    "FLOW-",
    "EXP-",
    "SYS-",
    "ARCH-",
    "DEC-",
)


_ID_PATTERN_PREFIX_RE = re.compile(r"^\^?([A-Z][A-Z0-9]*-)")
_DYNAMIC_ENTITY_ID_PREFIXES: set[str] = set(ENTITY_ID_PREFIXES)

_SPEC_SUBDIRS = frozenset(
    {
        "architecture",
        "domains",
        "product",
        "policies",
        "personas",
        "deltas",
        "explorations",
        "design",
    }
)
_REPO_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod")


def _extract_prefixes_from_schema(schema: dict[str, Any]) -> set[str]:
    """Extract entity ID prefixes from a schema's top-level id.pattern and all x-eidos-ref annotations."""
    found: set[str] = set()
    if not isinstance(schema, dict):
        return found
    props = schema.get("properties")
    if isinstance(props, dict) and isinstance(props.get("id"), dict):
        pat = props["id"].get("pattern")
        if isinstance(pat, str):
            m = _ID_PATTERN_PREFIX_RE.match(pat.strip())
            if m:
                found.add(m.group(1).upper())

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            ref_meta = node.get("x-eidos-ref")
            if isinstance(ref_meta, dict):
                for p in ref_meta.get("prefixes") or ():
                    if isinstance(p, str) and p.strip():
                        found.add(p.strip().upper())
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)
    return found


def _resolve_spec_and_repo_roots(
    root_dir: Path | None, doc_path: Path | None = None
) -> tuple[Path, Path]:
    """Resolve (spec_root, repo_root) for standard ('eidos/', '.spec/'), custom ('specs/'), or subdirectory paths."""
    base = (
        root_dir
        if root_dir is not None
        else (doc_path.parent if doc_path is not None else Path.cwd())
    ).resolve()

    # 1. If base itself is a repo root containing a standard child spec directory ('eidos', '.spec', 'specs')
    for child_name in ("eidos", ".spec", "specs"):
        child = base / child_name
        if child.is_dir() and (
            child_name in ("eidos", ".spec")
            or (child / "system.adoc").is_file()
            or any((child / s).is_dir() for s in _SPEC_SUBDIRS)
        ):
            return child.resolve(), base

    def _is_spec_root(cand: Path) -> bool:
        if (cand / "eidos").is_dir() or (cand / ".spec").is_dir():
            return False
        if cand.name in ("eidos", ".spec"):
            return True
        if (cand / "system.adoc").is_file():
            return True
        if (cand / "architecture").is_dir():
            arch = cand / "architecture"
            if (
                any((arch / sub).is_dir() for sub in ("containers", "components", "contracts"))
                or any(arch.glob("*.adoc"))
                or any((cand / sub).is_dir() for sub in _SPEC_SUBDIRS - {"architecture"})
            ):
                return True
        return bool(cand.name in ("specs", "spec") and any((cand / sub).is_dir() for sub in _SPEC_SUBDIRS))

    def _repo_root_for_spec_root(spec_cand: Path) -> Path:
        if spec_cand.name in ("eidos", ".spec", "specs", "spec"):
            return spec_cand.parent
        if (spec_cand / "src").is_dir() or any((spec_cand / m).exists() for m in _REPO_MARKERS):
            return spec_cand
        if spec_cand.parent.name.startswith("pytest-"):
            return spec_cand
        return spec_cand.parent

    # 2. Walk (base, *base.parents) (and doc_path parents if provided) to find spec_root
    search_roots = [base]
    if doc_path is not None:
        res_doc_parent = doc_path.resolve().parent
        if res_doc_parent != base:
            search_roots.append(res_doc_parent)

    for start in search_roots:
        for depth, cand in enumerate((start, *start.parents)):
            if depth > 5:
                break
            if cand != start:
                try:
                    rel_first = start.relative_to(cand).parts[0]
                except ValueError:
                    break
                if rel_first in _SPEC_SUBDIRS and _is_spec_root(cand):
                    return cand, _repo_root_for_spec_root(cand)
            elif _is_spec_root(cand):
                return cand, _repo_root_for_spec_root(cand)
            if any((cand / m).exists() for m in _REPO_MARKERS):
                break

    # 3. Check if an immediate or ancestor directory is a repo root with markers
    for depth, cand in enumerate((base.parent, *base.parents)):
        if depth > 5:
            break
        if any((cand / m).exists() for m in _REPO_MARKERS):
            try:
                rel_parts = base.relative_to(cand).parts
            except ValueError:
                break
            if rel_parts and rel_parts[0] not in ("src", "tests", "services", "packages"):
                spec_root = (cand / rel_parts[0]).resolve()
                return spec_root, cand
            break

    # 4. Fallback
    if base.name in ("eidos", ".spec", "specs", "spec"):
        return base, base.parent
    if (base.parent / "src").is_dir() or (base.parent / ".git").exists():
        return base, base.parent
    return base, base


def is_entity_id(val: Any) -> bool:
    """Return True if val looks like a standardized Eidos entity ID."""
    if not isinstance(val, str):
        return False
    val_clean = val.strip().upper()
    return any(val_clean.startswith(prefix) for prefix in _DYNAMIC_ENTITY_ID_PREFIXES)


TYPE_TO_SCHEMA_NAME = {
    "product/system": "product/system.schema.yaml",
    "product/domain": "product/domain.schema.yaml",
    "product/capability": "product/capability.schema.yaml",
    "product/policy": "product/policy.schema.yaml",
    "product/persona": "product/persona.schema.yaml",
    "product/exploration": "product/exploration.schema.yaml",
    "delta/mutation": "delta/delta.schema.yaml",
    "tech/architecture": "tech/architecture.schema.yaml",
    "tech/container": "tech/container.schema.yaml",
    "tech/component": "tech/component.schema.yaml",
    "tech/contract": "tech/contract.schema.yaml",
    "design/system": "design/system.schema.yaml",
    "design/ui-component": "design/ui-component.schema.yaml",
    "design/screen": "design/screen.schema.yaml",
    "design/client": "design/client.schema.yaml",
    "design/flow": "design/flow.schema.yaml",
}


class SpecValidator:
    """Validates specification documents using bundled and custom schemas."""

    def __init__(self):
        self._schema_cache: dict[str, dict] = {}
        self._schema_hash: str = ""
        self.schema_hash: str = ""
        self.entity_id_prefixes: tuple[str, ...] = ENTITY_ID_PREFIXES
        self._load_bundled_schemas()

    def _compute_schema_hash(self) -> str:
        """Compute a deterministic SHA-256 digest across loaded bundled schemas."""
        canonical = {
            k: self._schema_cache[k]
            for k in sorted(self._schema_cache)
            if k.startswith("schemas/v1/") and "/" in k.removeprefix("schemas/v1/")
        }
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _load_bundled_schemas(self):
        """Preload standard schemas recursively from the assets directory or package data."""
        try:
            schema_dir = get_v1_schemas_dir()
            common_defs_path = schema_dir / "common" / "defs.yaml"
            shared_defs: dict[str, Any] = {}
            if common_defs_path.is_file():
                defs_doc = yaml.safe_load(common_defs_path.read_text(encoding="utf-8")) or {}
                if isinstance(defs_doc, dict) and isinstance(defs_doc.get("$defs"), dict):
                    shared_defs = defs_doc["$defs"]

            extracted_prefixes: set[str] = set(ENTITY_ID_PREFIXES)
            for defs_item in shared_defs.values():
                if isinstance(defs_item, dict):
                    extracted_prefixes.update(_extract_prefixes_from_schema(defs_item))

            all_schema_paths = sorted(
                p for ext in ("*.schema.yaml", "*.schema.json") for p in schema_dir.rglob(ext)
            )

            for p in all_schema_paths:
                content = p.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    if shared_defs and '"#/$defs/' in json.dumps(data):
                        merged_defs = dict(shared_defs)
                        if isinstance(data.get("$defs"), dict):
                            merged_defs.update(data["$defs"])
                        data["$defs"] = merged_defs
                    extracted_prefixes.update(_extract_prefixes_from_schema(data))
                rel_path = str(p.relative_to(schema_dir)).replace("\\", "/")
                self._schema_cache[rel_path] = data
                self._schema_cache[f"schemas/v1/{rel_path}"] = data
                self._schema_cache[p.name] = data
                self._schema_cache[f"schemas/v1/{p.name}"] = data

            _DYNAMIC_ENTITY_ID_PREFIXES.update(extracted_prefixes)
            self.entity_id_prefixes = tuple(sorted(extracted_prefixes))
            self._schema_hash = self._compute_schema_hash()
            self.schema_hash = self._schema_hash
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"Failed to load bundled Eidos schemas: {exc}") from exc

    def resolve_schema(self, doc: SpecDocument) -> dict | None:
        """Resolve the JSON schema for a document based on its type or explicit schema attribute."""
        # 1. Check explicit relative schema reference (containing a directory component)
        if doc.schema_ref:
            norm_ref = doc.schema_ref.replace(".spec/", "").strip().replace("\\", "/")
            if "/" in norm_ref and norm_ref in self._schema_cache:
                return self._schema_cache[norm_ref]

        # 2. Check document type mapping (disambiguates colliding basenames like system.schema.yaml)
        if doc.doc_type in TYPE_TO_SCHEMA_NAME:
            schema_name = TYPE_TO_SCHEMA_NAME[doc.doc_type]
            return self._schema_cache.get(schema_name)

        # 3. Fallback to basename lookup for custom/unmapped types
        if doc.schema_ref:
            basename = Path(doc.schema_ref.replace(".spec/", "").strip()).name
            if basename in self._schema_cache:
                return self._schema_cache[basename]

        return None

    def validate_document(self, doc: SpecDocument) -> list[str]:
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

        effective_schema_hash = (
            self.schema_hash if self.schema_hash != self._schema_hash else self._compute_schema_hash()
        )
        self.schema_hash = effective_schema_hash

        resolved_root = root_dir.resolve()
        spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir)
        eidos_cache_root = spec_root
        resolved_repo_root = repo_root
        db_path = eidos_cache_root / ".cache" / "index.db"

        cached_rows: dict[str, sqlite3.Row] = {}
        if eidos_cache_root.exists():
            from eidos.core.index.graph_index import open_cache_connection

            with contextlib.suppress(OSError, sqlite3.Error), open_cache_connection(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS spec_validation_cache (
                        file_path TEXT PRIMARY KEY,
                        mtime_ns INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        schema_hash TEXT NOT NULL,
                        doc_json TEXT NOT NULL,
                        schema_errors_json TEXT NOT NULL
                    );
                    """
                )
                for row in conn.execute(
                    "SELECT file_path, mtime_ns, sha256, schema_hash, doc_json, schema_errors_json FROM spec_validation_cache;"
                ):
                    cached_rows[row["file_path"]] = row

        seen_rel_keys: set[str] = set()
        to_upsert: list[tuple[str, int, str, str, str, str]] = []

        def _rel_key(fp: Path) -> str:
            res_fp = fp.resolve()
            for base in (resolved_repo_root, spec_root, resolved_root):
                try:
                    return str(res_fp.relative_to(base)).replace("\\", "/")
                except ValueError:
                    continue
            return str(res_fp).replace("\\", "/")

        def _load_or_validate(fp: Path) -> SpecDocument | None:
            try:
                rel_fp_parts = fp.resolve().relative_to(spec_root).parts
            except ValueError:
                try:
                    rel_fp_parts = fp.resolve().relative_to(resolved_repo_root).parts
                except ValueError:
                    rel_fp_parts = (fp.name,)
            # Skip README, partial include files, symlinks, generated by-status views, and empty files
            if (
                fp.name.lower() == "readme.adoc"
                or fp.name.startswith("_")
                or "by-status" in rel_fp_parts
                or fp.is_symlink()
            ):
                return None
            try:
                st = fp.stat()
            except OSError:
                return None
            is_root_system = fp.name == "system.adoc" and rel_fp_parts == ("system.adoc",)
            if st.st_size == 0 and is_root_system:
                return None

            mtime_ns = st.st_mtime_ns
            rel_key = _rel_key(fp)
            seen_rel_keys.add(rel_key)
            row = cached_rows.get(rel_key)

            if row is not None and row["mtime_ns"] == mtime_ns and row["schema_hash"] == effective_schema_hash:
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    doc_data = json.loads(row["doc_json"])
                    schema_errs = list(json.loads(row["schema_errors_json"]))
                    return SpecDocument(
                        file_path=fp,
                        frontmatter=doc_data.get("frontmatter") or {},
                        body=doc_data.get("body") or "",
                        doc_type=doc_data.get("doc_type") or "unknown",
                        doc_id=doc_data.get("doc_id") or "unknown",
                        schema_ref=doc_data.get("schema_ref"),
                        title=doc_data.get("title"),
                        errors=list(schema_errs),
                    )

            try:
                raw_bytes = fp.read_bytes()
            except OSError:
                return None
            content = raw_bytes.decode("utf-8", errors="replace")
            if not content.strip() and is_root_system:
                return None
            sha256 = hashlib.sha256(raw_bytes).hexdigest()

            if row is not None and row["sha256"] == sha256 and row["schema_hash"] == effective_schema_hash:
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    doc_data = json.loads(row["doc_json"])
                    schema_errs = list(json.loads(row["schema_errors_json"]))
                    to_upsert.append(
                        (
                            rel_key,
                            mtime_ns,
                            sha256,
                            effective_schema_hash,
                            row["doc_json"],
                            row["schema_errors_json"],
                        )
                    )
                    return SpecDocument(
                        file_path=fp,
                        frontmatter=doc_data.get("frontmatter") or {},
                        body=doc_data.get("body") or "",
                        doc_type=doc_data.get("doc_type") or "unknown",
                        doc_id=doc_data.get("doc_id") or "unknown",
                        schema_ref=doc_data.get("schema_ref"),
                        title=doc_data.get("title"),
                        errors=list(schema_errs),
                    )

            doc = parse_spec_file(fp)
            self.validate_document(doc)
            schema_errs = list(doc.errors)
            doc_payload = {
                "doc_id": doc.doc_id,
                "doc_type": doc.doc_type,
                "schema_ref": doc.schema_ref,
                "title": doc.title,
                "frontmatter": doc.frontmatter,
                "body": doc.body,
                "errors": schema_errs,
            }
            to_upsert.append(
                (
                    rel_key,
                    mtime_ns,
                    sha256,
                    effective_schema_hash,
                    json.dumps(doc_payload, sort_keys=True, default=str),
                    json.dumps(schema_errs),
                )
            )
            return doc

        for file_path in adoc_files:
            doc = _load_or_validate(file_path)
            if doc is None:
                continue
            report.documents.append(doc)
            if not doc.is_valid:
                report.schema_errors[str(file_path)] = list(doc.errors)

        # Discover dedicated component specs referenced by containers via 'components[].ref'. Refs resolving
        # outside 'eidos/architecture/components/' are still loaded so INV-COMPONENT-CENTRALIZATION-MANDATE
        # can report them as violations (DLT-2026-032).
        scanned_set = {str(d.file_path.resolve()) for d in report.documents}

        for doc in list(report.documents):
            if doc.doc_type == "tech/container" and doc.frontmatter:
                doc.frontmatter.get("package_path")
                # 1. Check explicit component refs in components array
                for comp in doc.frontmatter.get("components", []):
                    if isinstance(comp, dict) and comp.get("ref"):
                        ref_str = comp["ref"]
                        ref_file = (repo_root / ref_str).resolve()
                        if not ref_file.exists():
                            ref_file = (spec_root / ref_str).resolve()
                        if not ref_file.exists():
                            ref_file = (root_dir / ref_str).resolve()
                        if not ref_file.exists():
                            ref_file = (spec_root / "architecture" / "components" / Path(ref_str).name).resolve()
                        if not ref_file.exists():
                            report.broken_references.append(
                                f"Broken component ref in {doc.file_path.name}: cannot locate '{ref_str}'"
                            )
                        elif str(ref_file) not in scanned_set:
                            comp_doc = _load_or_validate(ref_file)
                            if comp_doc is not None:
                                report.documents.append(comp_doc)
                                scanned_set.add(str(ref_file))
                                if not comp_doc.is_valid:
                                    report.schema_errors[str(ref_file)] = list(comp_doc.errors)

        deleted_keys = (
            [k for k in cached_rows if k not in seen_rel_keys] if resolved_root == eidos_cache_root else []
        )
        if eidos_cache_root.exists() and (to_upsert or deleted_keys):
            from eidos.core.index.graph_index import open_cache_connection

            with contextlib.suppress(OSError, sqlite3.Error), open_cache_connection(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS spec_validation_cache (
                        file_path TEXT PRIMARY KEY,
                        mtime_ns INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        schema_hash TEXT NOT NULL,
                        doc_json TEXT NOT NULL,
                        schema_errors_json TEXT NOT NULL
                    );
                    """
                )
                if deleted_keys:
                    conn.executemany(
                        "DELETE FROM spec_validation_cache WHERE file_path = ?;",
                        [(k,) for k in deleted_keys],
                    )
                if to_upsert:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO spec_validation_cache
                        (file_path, mtime_ns, sha256, schema_hash, doc_json, schema_errors_json)
                        VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        to_upsert,
                    )
                conn.commit()

        # Cross-reference validation
        self._validate_cross_references(report, root_dir)

        return report

    def _validate_cross_references(self, report: ValidationReport, root_dir: Path):
        """Ensure all relative file links in YAML frontmatters and entity ID cross-references resolve."""
        # 1. Index all document IDs across the specification catalog and detect duplicate IDs
        report.entity_index = {}
        for doc in report.documents:
            if doc.doc_id:
                if doc.doc_id in report.entity_index:
                    prev_doc = report.entity_index[doc.doc_id]
                    report.broken_references.append(
                        f"Duplicate entity ID '{doc.doc_id}' found in {doc.file_path.name} (already defined in {prev_doc.file_path.name})"
                    )
                else:
                    report.entity_index[doc.doc_id] = doc

        catalog_ids = set(report.entity_index.keys())
        entity_types: dict[str, str] = {
            doc_id: doc.doc_type for doc_id, doc in report.entity_index.items() if doc.doc_type
        }

        # Also register inline component IDs declared directly within containers in catalog_ids
        for doc in report.documents:
            if doc.doc_type == "tech/container" and doc.frontmatter:
                for comp in doc.frontmatter.get("components", []):
                    if isinstance(comp, dict) and comp.get("id"):
                        cid = str(comp["id"]).strip()
                        catalog_ids.add(cid)
                        entity_types.setdefault(cid, "tech/component")

        # Register embedded architectural decision IDs (DEC-*) and enforce global uniqueness across the catalog
        decision_owners: dict[str, SpecDocument] = {}
        for doc in report.documents:
            if not doc.frontmatter:
                continue
            doc_decisions: list[dict[str, Any]] = []
            if isinstance(doc.frontmatter.get("decisions"), list):
                doc_decisions.extend(d for d in doc.frontmatter["decisions"] if isinstance(d, dict))
            if doc.doc_type == "tech/container":
                for comp in doc.frontmatter.get("components", []) or []:
                    if isinstance(comp, dict) and isinstance(comp.get("decisions"), list):
                        doc_decisions.extend(d for d in comp["decisions"] if isinstance(d, dict))
            for dec in doc_decisions:
                dec_id = str(dec.get("id") or "").strip()
                if not dec_id:
                    continue
                if dec_id in decision_owners or dec_id in catalog_ids:
                    prev_doc = decision_owners.get(dec_id) or report.entity_index.get(dec_id)
                    prev_name = prev_doc.file_path.name if prev_doc else doc.file_path.name
                    report.broken_references.append(
                        f"Duplicate entity ID '{dec_id}' found in {doc.file_path.name} (already defined in {prev_name})"
                    )
                else:
                    decision_owners[dec_id] = doc
                    catalog_ids.add(dec_id)
                    entity_types[dec_id] = "tech/decision"

        for doc in report.documents:
            # 2. Check relative file paths ending in .adoc
            self._check_dict_references(doc.frontmatter, doc.file_path, root_dir, report)

            # 3. Check semantic entity ID cross-references
            self._validate_semantic_references(doc, catalog_ids, report, root_dir, entity_types)

        # 4. Check source/contract file paths on disk and CLI contract command parity
        self._validate_source_and_contract_paths(report, root_dir)

        # 5. Check design/system W3C DTCG token manifests and acyclic DS-* inheritance
        self._validate_design_systems(report, root_dir)

    def _validate_semantic_references(
        self,
        doc: SpecDocument,
        catalog_ids: set[str],
        report: ValidationReport,
        root_dir: Path,
        entity_types: dict[str, str] | None = None,
    ):
        """Validate semantic entity ID references via schema-driven x-eidos-ref annotations."""
        fm = doc.frontmatter or {}
        schema = self.resolve_schema(doc)
        if schema:
            self._validate_schema_references(schema, fm, "", doc, catalog_ids, report, entity_types)
        self._validate_architectural_invariants(doc, fm, report, root_dir=root_dir)

    def _validate_schema_references(
        self,
        schema_node: dict[str, Any],
        value: Any,
        field_path: str,
        doc: SpecDocument,
        catalog_ids: set[str],
        report: ValidationReport,
        entity_types: dict[str, str] | None = None,
    ) -> None:
        """Recursively walk schema and frontmatter to enforce x-eidos-ref reference rules."""
        if value is None or not isinstance(schema_node, dict):
            return

        if "$ref" in schema_node:
            ref_val = str(schema_node["$ref"])
            if ref_val.startswith("#/$defs/"):
                def_name = ref_val.split("/")[-1]
                root_schema = self.resolve_schema(doc) or {}
                resolved_def = (root_schema.get("$defs") or {}).get(def_name)
                if isinstance(resolved_def, dict):
                    self._validate_schema_references(
                        resolved_def, value, field_path, doc, catalog_ids, report, entity_types
                    )
            return

        ref_meta = schema_node.get("x-eidos-ref")
        if isinstance(ref_meta, dict):
            if isinstance(value, str):
                self._check_eidos_ref(ref_meta, value, field_path, doc, catalog_ids, report, entity_types)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        self._check_eidos_ref(ref_meta, item, field_path, doc, catalog_ids, report, entity_types)

        if isinstance(value, dict):
            props = schema_node.get("properties")
            if isinstance(props, dict):
                for prop_key, prop_val in value.items():
                    if prop_key in props:
                        sub_path = f"{field_path}.{prop_key}" if field_path else prop_key
                        self._validate_schema_references(
                            props[prop_key], prop_val, sub_path, doc, catalog_ids, report, entity_types
                        )
            for combiner in ("oneOf", "anyOf", "allOf"):
                for branch in schema_node.get(combiner, []) or []:
                    if isinstance(branch, dict) and isinstance(branch.get("properties"), dict):
                        for prop_key, prop_val in value.items():
                            if prop_key in branch["properties"]:
                                sub_path = f"{field_path}.{prop_key}" if field_path else prop_key
                                self._validate_schema_references(
                                    branch["properties"][prop_key],
                                    prop_val,
                                    sub_path,
                                    doc,
                                    catalog_ids,
                                    report,
                                    entity_types,
                                )
        elif isinstance(value, list):
            items_schema = schema_node.get("items")
            if isinstance(items_schema, dict):
                for item in value:
                    self._validate_schema_references(items_schema, item, field_path, doc, catalog_ids, report, entity_types)
                    if not isinstance(item, dict):
                        for combiner in ("oneOf", "anyOf", "allOf"):
                            for branch in items_schema.get(combiner, []) or []:
                                if isinstance(branch, dict):
                                    self._validate_schema_references(
                                        branch, item, field_path, doc, catalog_ids, report, entity_types
                                    )

    def _check_eidos_ref(
        self,
        ref_meta: dict[str, Any],
        raw_val: str,
        field_path: str,
        doc: SpecDocument,
        catalog_ids: set[str],
        report: ValidationReport,
        entity_types: dict[str, str] | None = None,
    ) -> None:
        """Check a single string reference against x-eidos-ref metadata."""
        val = raw_val.strip()
        if not val or val.endswith(".adoc"):
            return

        prefixes = tuple(str(p).upper() for p in (ref_meta.get("prefixes") or ()))
        target_types = tuple(str(t) for t in (ref_meta.get("target_types") or ()))
        allow_non_id = bool(ref_meta.get("allow_non_id_string", False))
        val_upper = val.upper()
        matches_id = (
            is_entity_id(val)
            or val_upper.startswith(self.entity_id_prefixes)
            or (bool(prefixes) and val_upper.startswith(prefixes))
        )

        if allow_non_id and not matches_id:
            return
        if not allow_non_id and not matches_id and ("/" in val or " " in val):
            return

        if matches_id and prefixes and not val_upper.startswith(prefixes):
            label = ref_meta.get("entity_label") or "entity"
            expected_prefixes = ", ".join(prefixes)
            actual_type = (entity_types or {}).get(val)
            type_detail = f" (type '{actual_type}')" if actual_type else ""
            report.broken_references.append(
                f"Invalid entity reference type in {doc.file_path.name}: "
                f"{label} '{val}'{type_detail} in {field_path} must start with {expected_prefixes}"
            )
            return

        if val not in catalog_ids:
            if doc.doc_type == "delta/mutation":
                fm = doc.frontmatter or {}
                delta_status = str(fm.get("status") or "").strip().lower()
                if delta_status in ("applied", "rejected"):
                    return
                for mut in fm.get("mutations") or []:
                    if isinstance(mut, dict):
                        act = str(mut.get("action") or "").strip().lower()
                        if act.startswith(("add_", "remove_", "deprecate_")) and val in {
                            str(v).strip() for k, v in mut.items() if k.startswith(("target_", "new_")) and v
                        }:
                            return
            rule_id = ref_meta.get("rule_id")
            label = ref_meta.get("entity_label")
            if rule_id:
                report.broken_references.append(
                    f"{rule_id} violation in {doc.file_path.name}: "
                    f"Component '{doc.doc_id}' references {label or 'entity'} '{val}' which does not exist in catalog"
                )
            elif label:
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"{label} '{val}' in {field_path} does not exist in catalog"
                )
            else:
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"'{val}' referenced in {field_path} does not exist in catalog"
                )
            return

        actual_type = (entity_types or {}).get(val)
        if target_types and actual_type and actual_type not in target_types:
            label = ref_meta.get("entity_label") or "entity"
            expected_types = ", ".join(target_types)
            report.broken_references.append(
                f"Invalid entity reference type in {doc.file_path.name}: "
                f"{label} '{val}' in {field_path} has type '{actual_type}', expected {expected_types}"
            )

    def _validate_architectural_invariants(
        self,
        doc: SpecDocument,
        fm: dict[str, Any],
        report: ValidationReport,
        root_dir: Path | None = None,
    ) -> None:
        """Enforce structural boundary invariants across contracts and dedicated components."""
        dtype = doc.doc_type
        if dtype == "tech/contract":
            owning_ref = fm.get("owning_container") or fm.get("owning_component")
            if owning_ref and str(owning_ref).startswith("CMP-"):
                err_msg = (
                    f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Contract '{doc.doc_id}' specifies in-process component '{owning_ref}' as owner. "
                    f"Boundary Protocol Contracts (CTR-*) must be owned by a Container (CNT-*)."
                )
                doc.errors.append(err_msg)
                report.schema_errors.setdefault(str(doc.file_path), []).append(err_msg)
        elif dtype == "tech/component":
            spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir, doc.file_path)
            res_doc = doc.file_path.resolve()
            try:
                rel_to_spec = res_doc.relative_to(spec_root.resolve()).parts
            except ValueError:
                rel_to_spec = ()

            try:
                rel_to_repo = res_doc.relative_to(repo_root.resolve()).parts
            except ValueError:
                rel_to_repo = rel_to_spec

            is_centralized = (
                len(rel_to_spec) >= 2
                and rel_to_spec[0] == "architecture"
                and rel_to_spec[1] == "components"
            )
            if not is_centralized or "src" in rel_to_repo:
                err_msg = (
                    f"INV-COMPONENT-CENTRALIZATION-MANDATE violation: Component manifest '{doc.doc_id}' must reside "
                    f"centrally in 'eidos/architecture/components/'. Eidos specifications must never be placed inside source code directories."
                )
                doc.errors.append(err_msg)
                report.schema_errors.setdefault(str(doc.file_path), []).append(err_msg)

            if fm.get("contracts"):
                err_msg = (
                    f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Component '{doc.doc_id}' cannot declare boundary protocol contracts. "
                    f"Use 'interfaces.provides' for in-process APIs."
                )
                doc.errors.append(err_msg)
                report.schema_errors.setdefault(str(doc.file_path), []).append(err_msg)

    def _check_dict_references(self, val, source_file: Path, root_dir: Path, report: ValidationReport):
        """Recursively check string fields ending in .adoc to verify they exist."""
        if isinstance(val, dict):
            for v in val.values():
                self._check_dict_references(v, source_file, root_dir, report)
        elif isinstance(val, list):
            for item in val:
                self._check_dict_references(item, source_file, root_dir, report)
        elif isinstance(val, str) and val.endswith(".adoc"):
            spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir, source_file)
            ref_path_from_repo = repo_root / val
            ref_path_from_spec = spec_root / val
            ref_path_from_root = root_dir / val
            ref_path_from_doc = source_file.parent / val
            if not (
                ref_path_from_repo.exists()
                or ref_path_from_spec.exists()
                or ref_path_from_root.exists()
                or ref_path_from_doc.exists()
            ):
                report.broken_references.append(f"Broken reference in {source_file.name}: cannot locate '{val}'")

    def _resolve_source_path(self, repo_root: Path, root_dir: Path, raw_path: str) -> Path | None:
        """Resolve a non-.adoc source path or glob pattern against repo_root or root_dir."""
        p = raw_path.strip()
        if not p or p.upper() in ("N/A", "TBD", "NONE") or p.endswith(".adoc"):
            return None
        if any(ch in p for ch in ("*", "?", "[")):
            matches = list(repo_root.glob(p)) or list(root_dir.glob(p))
            return matches[0] if matches else None
        for base in (repo_root, root_dir):
            cand = base / p
            if cand.exists():
                return cand
        return None

    @staticmethod
    def _test_symbol_exists(test_file: Path, symbol_parts: list[str]) -> bool:
        """Return True if the target test function/method symbol exists inside test_file."""
        if not symbol_parts:
            return True
        try:
            source = test_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        leaf_sym = symbol_parts[-1]
        if test_file.suffix == ".py":
            try:
                tree = ast.parse(source)
                if len(symbol_parts) >= 2:
                    cls_name = symbol_parts[0]
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef) and node.name == cls_name:
                            for item in ast.walk(node):
                                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == leaf_sym:
                                    return True
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == leaf_sym:
                        return True
                return False
            except SyntaxError:
                pass
        return bool(re.search(rf"\b{re.escape(leaf_sym)}\b", source))

    def _validate_source_and_contract_paths(self, report: ValidationReport, root_dir: Path) -> None:
        """Verify container package_path, component source_path, contract specification_file, UI component test_bindings, and invariant test_binding references when source/test trees exist on disk."""
        spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir)

        candidate_paths: list[str] = []
        candidate_test_files: list[str] = []
        for doc in report.documents:
            fm = doc.frontmatter or {}
            if doc.doc_type == "tech/container":
                pkg = fm.get("package_path")
                if isinstance(pkg, str) and pkg.strip():
                    candidate_paths.append(pkg.strip())
                elif isinstance(pkg, list):
                    candidate_paths.extend(str(x).strip() for x in pkg if isinstance(x, str) and str(x).strip())
                for comp in fm.get("components") or ():
                    if isinstance(comp, dict) and "ref" not in comp:
                        sp = comp.get("source_path")
                        if isinstance(sp, str) and sp.strip():
                            candidate_paths.append(sp.strip())
                        elif isinstance(sp, list):
                            candidate_paths.extend(str(x).strip() for x in sp if isinstance(x, str) and str(x).strip())
            elif doc.doc_type == "tech/component":
                sp = fm.get("source_path")
                if isinstance(sp, str) and sp.strip():
                    candidate_paths.append(sp.strip())
                elif isinstance(sp, list):
                    candidate_paths.extend(str(x).strip() for x in sp if isinstance(x, str) and str(x).strip())
            elif doc.doc_type == "tech/contract":
                sf = fm.get("specification_file")
                if isinstance(sf, str) and sf.strip() and not sf.strip().endswith(".adoc"):
                    candidate_paths.append(sf.strip())
            elif doc.doc_type == "design/ui-component":
                tb = fm.get("test_bindings")
                if isinstance(tb, dict):
                    for v in tb.values():
                        if isinstance(v, str) and v.strip():
                            candidate_test_files.append(v.split("::", 1)[0].strip())

        has_source_tree = (
            (repo_root != spec_root or repo_root != root_dir.resolve()) and (repo_root / "src").exists()
        ) or any(self._resolve_source_path(repo_root, root_dir, p) is not None for p in candidate_paths)

        has_test_tree = (
            (repo_root / "tests").exists()
            or (root_dir / "tests").exists()
            or any(self._resolve_source_path(repo_root, root_dir, tf) is not None for tf in candidate_test_files)
        )

        if not has_source_tree and not has_test_tree:
            return

        inactive_statuses = {"planned", "proposed", "draft", "deprecated", "sunset", "rejected"}
        for doc in report.documents:
            fm = doc.frontmatter or {}
            status = str(fm.get("status", "active")).strip().lower()
            if status in inactive_statuses:
                continue

            if has_source_tree:
                if doc.doc_type == "tech/container":
                    pkg = fm.get("package_path")
                    pkg_list = [pkg] if isinstance(pkg, str) else (pkg if isinstance(pkg, list) else [])
                    for raw_p in pkg_list:
                        if (
                            isinstance(raw_p, str)
                            and raw_p.strip()
                            and raw_p.strip().upper() not in ("N/A", "TBD", "NONE")
                            and self._resolve_source_path(repo_root, root_dir, raw_p) is None
                        ):
                            report.broken_references.append(
                                f"Broken package_path in {doc.file_path.name}: "
                                f"Container '{doc.doc_id}' references '{raw_p}' which does not exist on disk"
                            )
                    for comp in fm.get("components") or ():
                        if not isinstance(comp, dict) or "ref" in comp:
                            continue
                        comp_status = str(comp.get("status", "active")).strip().lower()
                        if comp_status in inactive_statuses:
                            continue
                        comp_id = str(comp.get("id", "unknown"))
                        sp = comp.get("source_path")
                        sp_list = [sp] if isinstance(sp, str) else (sp if isinstance(sp, list) else [])
                        for raw_sp in sp_list:
                            if (
                                isinstance(raw_sp, str)
                                and raw_sp.strip()
                                and raw_sp.strip().upper() not in ("N/A", "TBD", "NONE")
                                and self._resolve_source_path(repo_root, root_dir, raw_sp) is None
                            ):
                                report.broken_references.append(
                                    f"Broken source_path in {doc.file_path.name}: "
                                    f"Component '{comp_id}' references '{raw_sp}' which does not exist on disk"
                                )
                elif doc.doc_type == "tech/component":
                    sp = fm.get("source_path")
                    sp_list = [sp] if isinstance(sp, str) else (sp if isinstance(sp, list) else [])
                    for raw_sp in sp_list:
                        if (
                            isinstance(raw_sp, str)
                            and raw_sp.strip()
                            and raw_sp.strip().upper() not in ("N/A", "TBD", "NONE")
                            and self._resolve_source_path(repo_root, root_dir, raw_sp) is None
                        ):
                            report.broken_references.append(
                                f"Broken source_path in {doc.file_path.name}: "
                                f"Component '{doc.doc_id}' references '{raw_sp}' which does not exist on disk"
                            )
                elif doc.doc_type == "tech/contract":
                    sf = fm.get("specification_file")
                    if isinstance(sf, str) and sf.strip() and sf.strip().upper() not in ("N/A", "TBD", "NONE") and not sf.strip().endswith(".adoc"):
                        resolved_sf = self._resolve_source_path(repo_root, root_dir, sf)
                        if resolved_sf is None:
                            report.broken_references.append(
                                f"Broken specification_file in {doc.file_path.name}: "
                                f"Contract '{doc.doc_id}' references '{sf}' which does not exist on disk"
                            )
                        else:
                            protocol = str(fm.get("protocol", "")).strip().lower()
                            self._verify_contract_protocol(protocol, doc, resolved_sf, report)

            if has_test_tree:
                if doc.doc_type == "design/ui-component":
                    tb_map = fm.get("test_bindings")
                    if isinstance(tb_map, dict):
                        for tb_key, tb_val in tb_map.items():
                            if (
                                not isinstance(tb_val, str)
                                or not tb_val.strip()
                                or tb_val.strip().upper() in ("N/A", "TBD", "NONE")
                            ):
                                continue
                            parts = [p.strip() for p in tb_val.split("::") if p.strip()]
                            file_part = parts[0]
                            sym_parts = parts[1:]
                            resolved_tf = self._resolve_source_path(repo_root, root_dir, file_part)
                            if resolved_tf is None:
                                report.broken_references.append(
                                    f"Broken test_bindings.{tb_key} in {doc.file_path.name}: "
                                    f"UI Component '{doc.doc_id}' references '{file_part}' which does not exist on disk"
                                )
                            elif sym_parts and not self._test_symbol_exists(resolved_tf, sym_parts):
                                report.broken_references.append(
                                    f"Broken test_bindings.{tb_key} in {doc.file_path.name}: "
                                    f"UI Component '{doc.doc_id}' references symbol '{'::'.join(sym_parts)}' which does not exist in '{file_part}'"
                                )

                # Collect invariants from top-level, technical.invariants, and inline components
                inv_entries: list[dict[str, Any]] = []
                if isinstance(fm.get("invariants"), list):
                    inv_entries.extend(i for i in fm["invariants"] if isinstance(i, dict))
                tech_block = fm.get("technical")
                if isinstance(tech_block, dict) and isinstance(tech_block.get("invariants"), list):
                    inv_entries.extend(i for i in tech_block["invariants"] if isinstance(i, dict))
                for comp in fm.get("components") or ():
                    if isinstance(comp, dict) and isinstance(comp.get("invariants"), list):
                        inv_entries.extend(i for i in comp["invariants"] if isinstance(i, dict))

                for inv in inv_entries:
                    inv_id = str(inv.get("id", "unknown"))
                    raw_tb = inv.get("test_binding")
                    if (
                        not isinstance(raw_tb, str)
                        or not raw_tb.strip()
                        or raw_tb.strip().upper() in ("N/A", "TBD", "NONE")
                    ):
                        continue
                    parts = [p.strip() for p in raw_tb.split("::") if p.strip()]
                    file_part = parts[0]
                    sym_parts = parts[1:]
                    resolved_tf = self._resolve_source_path(repo_root, root_dir, file_part)
                    if resolved_tf is None:
                        report.broken_references.append(
                            f"Broken test_binding in {doc.file_path.name}: "
                            f"Invariant '{inv_id}' references '{file_part}' which does not exist on disk"
                        )
                    elif sym_parts and not self._test_symbol_exists(resolved_tf, sym_parts):
                        report.broken_references.append(
                            f"Broken test_binding in {doc.file_path.name}: "
                            f"Invariant '{inv_id}' references symbol '{'::'.join(sym_parts)}' which does not exist in '{file_part}'"
                        )

    _VALID_DTCG_TYPES = frozenset(
        {
            "color",
            "dimension",
            "fontFamily",
            "fontWeight",
            "duration",
            "cubicBezier",
            "number",
            "strokeStyle",
            "border",
            "transition",
            "shadow",
            "gradient",
            "typography",
        }
    )

    def _validate_design_systems(self, report: ValidationReport, root_dir: Path) -> None:
        """Validate design/system W3C DTCG tokens.manifest files and acyclic inheritance.extends chains."""
        spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir)
        inactive_statuses = {"planned", "proposed", "draft", "deprecated", "sunset", "rejected"}

        extends_map: dict[str, tuple[str, SpecDocument]] = {}
        for doc in report.documents:
            if doc.doc_type != "design/system":
                continue
            fm = doc.frontmatter or {}
            inh = fm.get("inheritance")
            if isinstance(inh, dict):
                parent_id = str(inh.get("extends") or "").strip()
                if parent_id and doc.doc_id:
                    extends_map[doc.doc_id] = (parent_id, doc)

            status = str(fm.get("status", "active")).strip().lower()
            if status in inactive_statuses:
                continue

            tokens_cfg = fm.get("tokens")
            if not isinstance(tokens_cfg, dict):
                continue
            manifest_rel = str(tokens_cfg.get("manifest") or "").strip()
            spec_fmt = str(tokens_cfg.get("spec_format") or "w3c_design_tokens").strip().lower()
            if not manifest_rel or manifest_rel.upper() in ("N/A", "TBD", "NONE"):
                continue

            manifest_path: Path | None = None
            for base in (doc.file_path.parent, spec_root, root_dir, repo_root):
                cand = base / manifest_rel
                if cand.is_file():
                    manifest_path = cand
                    break

            if manifest_path is None:
                report.broken_references.append(
                    f"Broken tokens.manifest in {doc.file_path.name}: "
                    f"Design System '{doc.doc_id}' references '{manifest_rel}' which does not exist on disk"
                )
                continue

            if spec_fmt == "w3c_design_tokens":
                try:
                    token_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    report.broken_references.append(
                        f"Invalid W3C DTCG tokens.manifest in {doc.file_path.name}: "
                        f"'{manifest_rel}' is not valid JSON ({exc})"
                    )
                    continue

                if not isinstance(token_data, dict):
                    report.broken_references.append(
                        f"Invalid W3C DTCG tokens.manifest in {doc.file_path.name}: "
                        f"'{manifest_rel}' root must be a JSON object"
                    )
                    continue

                token_count = 0

                def _walk_tokens(node: dict[str, Any], path_prefix: str) -> None:
                    nonlocal token_count
                    if "$value" in node:
                        token_count += 1
                        tok_type = node.get("$type")
                        if tok_type is not None and (
                            not isinstance(tok_type, str) or tok_type not in self._VALID_DTCG_TYPES
                        ):
                            report.broken_references.append(
                                f"Invalid W3C DTCG token $type in {doc.file_path.name} ({manifest_rel}): "
                                f"token '{path_prefix}' has unsupported $type '{tok_type}'"
                            )
                        return

                    child_dicts = {
                        k: v for k, v in node.items() if not str(k).startswith("$") and isinstance(v, dict)
                    }
                    non_meta_scalars = [
                        k for k, v in node.items() if not str(k).startswith("$") and not isinstance(v, dict)
                    ]
                    if path_prefix and not child_dicts and non_meta_scalars:
                        report.broken_references.append(
                            f"Invalid W3C DTCG token in {doc.file_path.name} ({manifest_rel}): "
                            f"token '{path_prefix}' is missing required '$value' property"
                        )
                        return

                    for k, v in child_dicts.items():
                        sub = f"{path_prefix}.{k}" if path_prefix else str(k)
                        _walk_tokens(v, sub)

                _walk_tokens(token_data, "")
                if token_count == 0:
                    report.broken_references.append(
                        f"Invalid W3C DTCG tokens.manifest in {doc.file_path.name} ({manifest_rel}): "
                        f"no tokens with '$value' found"
                    )

        # Detect circular inheritance.extends chains across DS-* specifications
        reported_cycles: set[frozenset[str]] = set()
        for start_id, (first_parent, start_doc) in extends_map.items():
            chain = [start_id]
            curr: str | None = first_parent
            while curr:
                if curr in chain:
                    cycle_key = frozenset(chain)
                    if cycle_key not in reported_cycles:
                        reported_cycles.add(cycle_key)
                        cycle_str = " -> ".join([*chain, curr])
                        report.broken_references.append(
                            f"Circular design system inheritance in {start_doc.file_path.name}: {cycle_str}"
                        )
                    break
                chain.append(curr)
                nxt = extends_map.get(curr)
                curr = nxt[0] if nxt else None

    def _verify_cli_contract_commands(
        self, doc: SpecDocument, cli_file: Path, report: ValidationReport
    ) -> None:
        """Verify that top-level non-hidden Typer CLI commands in specification_file are documented in the contract body."""
        try:
            source = cli_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            return

        # Scoped parity: only verify Typer CLI parity if the target source imports Typer and instantiates a Typer app
        if "typer" not in source.lower():
            return
        typer_module_aliases: set[str] = set()
        typer_class_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "typer" or alias.name.startswith("typer."):
                        typer_module_aliases.add(alias.asname or alias.name.split(".")[0])
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "typer" or node.module.startswith("typer."))
            ):
                for alias in node.names:
                    if alias.name == "Typer":
                        typer_class_aliases.add(alias.asname or alias.name)
                    elif alias.name == "typer":
                        typer_module_aliases.add(alias.asname or alias.name)
        if not typer_module_aliases and not typer_class_aliases:
            return

        def _is_typer_ctor(call_node: ast.AST | None) -> bool:
            if not isinstance(call_node, ast.Call):
                return False
            func = call_node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                return func.value.id in typer_module_aliases and func.attr == "Typer"
            if isinstance(func, ast.Name):
                return func.id in typer_class_aliases
            return False

        typer_app_vars: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _is_typer_ctor(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        typer_app_vars.add(target.id)
            elif (
                isinstance(node, ast.AnnAssign)
                and _is_typer_ctor(node.value)
                and isinstance(node.target, ast.Name)
            ):
                typer_app_vars.add(node.target.id)

        if not typer_app_vars:
            return

        mounted_sub_apps: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in typer_app_vars
                and node.func.attr == "add_typer"
                and node.args
                and isinstance(node.args[0], ast.Name)
            ):
                mounted_sub_apps.add(node.args[0].id)

        root_app_vars = typer_app_vars - mounted_sub_apps or typer_app_vars

        def _extract_command_name_from_call(
            cmd_call: ast.Call, fallback_func_name: str | None
        ) -> str | None:
            hidden = any(
                kw.arg == "hidden" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in cmd_call.keywords
            )
            if hidden:
                return None
            name = next(
                (
                    kw.value.value
                    for kw in cmd_call.keywords
                    if kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ),
                None,
            )
            if (
                not name
                and cmd_call.args
                and isinstance(cmd_call.args[0], ast.Constant)
                and isinstance(cmd_call.args[0].value, str)
            ):
                name = cmd_call.args[0].value
            if name:
                return name
            if fallback_func_name:
                return fallback_func_name.replace("_", "-")
            return None

        def _collect_commands_from_ast(scope_node: ast.AST, target_vars: set[str]) -> set[str]:
            found: set[str] = set()
            for node in ast.walk(scope_node):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in target_vars
                    and node.func.attr == "add_typer"
                ):
                    sub_name = next(
                        (
                            kw.value.value
                            for kw in node.keywords
                            if kw.arg == "name"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                        ),
                        None,
                    )
                    if (
                        not sub_name
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)
                    ):
                        sub_name = node.args[1].value
                    if sub_name:
                        found.add(sub_name)
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Call)
                    and isinstance(node.func.func, ast.Attribute)
                    and isinstance(node.func.func.value, ast.Name)
                    and node.func.func.value.id in target_vars
                    and node.func.func.attr == "command"
                ):
                    # Functional registration: app.command("version", ...)(version_cmd) or app.command(...)(info)
                    fn_arg_name = (
                        node.args[0].id
                        if node.args and isinstance(node.args[0], ast.Name)
                        else None
                    )
                    cmd_name = _extract_command_name_from_call(node.func, fn_arg_name)
                    if cmd_name:
                        found.add(cmd_name)
                elif isinstance(node, ast.FunctionDef):
                    for dec in node.decorator_list:
                        if (
                            isinstance(dec, ast.Call)
                            and isinstance(dec.func, ast.Attribute)
                            and isinstance(dec.func.value, ast.Name)
                            and dec.func.value.id in target_vars
                            and dec.func.attr == "command"
                        ):
                            cmd_name = _extract_command_name_from_call(dec, node.name)
                            if cmd_name:
                                found.add(cmd_name)
                        elif (
                            isinstance(dec, ast.Attribute)
                            and isinstance(dec.value, ast.Name)
                            and dec.value.id in target_vars
                            and dec.attr == "command"
                        ):
                            found.add(node.name.replace("_", "-"))
            return found

        commands: set[str] = _collect_commands_from_ast(tree, root_app_vars)

        # Follow delegated `<sibling_module>.register(app)` calls in the same package directory
        sibling_module_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    sibling_module_aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod_leaf = alias.name.rsplit(".", 1)[-1]
                    sibling_module_aliases[alias.asname or mod_leaf] = mod_leaf

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
                and isinstance(node.func.value, ast.Name)
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in root_app_vars
            ):
                mod_name = sibling_module_aliases.get(node.func.value.id, node.func.value.id)
                sibling_py = cli_file.parent / f"{mod_name}.py"
                if sibling_py.is_file():
                    with contextlib.suppress(OSError, SyntaxError):
                        sib_tree = ast.parse(sibling_py.read_text(encoding="utf-8"))
                        for sib_node in sib_tree.body:
                            if (
                                isinstance(sib_node, ast.FunctionDef)
                                and sib_node.name == "register"
                                and sib_node.args.args
                            ):
                                app_param = sib_node.args.args[0].arg
                                commands.update(_collect_commands_from_ast(sib_node, {app_param}))

        if not commands:
            return

        body = doc.body or ""
        for cmd_name in sorted(commands):
            if not re.search(rf"(?:`|\b){re.escape(cmd_name)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: CLI command '{cmd_name}' defined in '{cli_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    def _verify_python_protocol_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify that Python Protocol / ABC classes and public methods in specification_file are documented in the contract."""
        if spec_file.suffix != ".py":
            return
        try:
            tree = ast.parse(spec_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return

        protocol_symbols: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                is_proto = any(
                    (isinstance(b, ast.Name) and b.id in ("Protocol", "ABC"))
                    or (isinstance(b, ast.Attribute) and b.attr in ("Protocol", "ABC"))
                    or (
                        isinstance(b, ast.Subscript)
                        and (
                            (isinstance(b.value, ast.Name) and b.value.id in ("Protocol", "ABC"))
                            or (isinstance(b.value, ast.Attribute) and b.value.attr in ("Protocol", "ABC"))
                        )
                    )
                    for b in node.bases
                )
                if is_proto:
                    protocol_symbols.append(node.name)
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                            protocol_symbols.append(item.name)

        body = doc.body or ""
        for sym in protocol_symbols:
            if not re.search(rf"(?:`|\b){re.escape(sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: Python protocol symbol '{sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    def _verify_rest_openapi_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify that OpenAPI specification_file is valid YAML/JSON and its paths are documented in the contract."""
        try:
            data = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            report.broken_references.append(
                f"Invalid OpenAPI specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
            )
            return
        if not isinstance(data, dict):
            report.broken_references.append(
                f"Invalid OpenAPI specification_file in {doc.file_path.name}: '{spec_file.name}' root must be a mapping"
            )
            return
        paths = data.get("paths")
        if not isinstance(paths, dict):
            return
        body = doc.body or ""
        for route in sorted(str(k) for k in paths.keys()):
            if route not in body:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: OpenAPI path '{route}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    def _verify_json_schema_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify that JSON Schema specification_file is valid YAML/JSON and its required properties are documented in the contract."""
        try:
            data = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            report.broken_references.append(
                f"Invalid JSON Schema specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
            )
            return
        if not isinstance(data, dict):
            report.broken_references.append(
                f"Invalid JSON Schema specification_file in {doc.file_path.name}: '{spec_file.name}' root must be a mapping"
            )
            return
        props = data.get("properties")
        if not isinstance(props, dict) or not props:
            return
        required_props = [str(p) for p in (data.get("required") or props.keys()) if str(p) in props]
        body = doc.body or ""
        for prop_name in required_props:
            if not re.search(rf"(?:`|\b){re.escape(prop_name)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: JSON Schema property '{prop_name}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    def _verify_grpc_proto_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify that gRPC service and rpc declarations in .proto specification_file are documented in the contract."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        services = re.findall(r"\bservice\s+([A-Za-z_][A-Za-z0-9_]*)\b", text)
        rpcs = re.findall(r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
        body = doc.body or ""
        for sym in [*services, *rpcs]:
            if not re.search(rf"(?:`|\b){re.escape(sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: gRPC symbol '{sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    def _verify_graphql_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify that root Query / Mutation / Subscription fields in GraphQL specification_file are documented in the contract."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        operations: list[str] = []
        for block_match in re.finditer(r"\btype\s+(?:Query|Mutation|Subscription)\s*\{([^}]*)\}", text, re.DOTALL):
            block = block_match.group(1)
            for line in block.splitlines():
                cleaned = line.split("#", 1)[0].strip()
                if not cleaned:
                    continue
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|:)", cleaned)
                if m:
                    operations.append(m.group(1))
        body = doc.body or ""
        for op in operations:
            if not re.search(rf"(?:`|\b){re.escape(op)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: GraphQL operation '{op}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    _CONTRACT_PROTOCOL_VERIFIERS: dict[str, str] = {
        "cli_args": "_verify_cli_contract_commands",
        "python_protocol": "_verify_python_protocol_contract",
        "rest_openapi": "_verify_rest_openapi_contract",
        "json_schema": "_verify_json_schema_contract",
        "grpc_proto": "_verify_grpc_proto_contract",
        "graphql": "_verify_graphql_contract",
    }

    def _verify_contract_protocol(
        self, protocol: str, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Dispatch contract-to-specification_file verification via _CONTRACT_PROTOCOL_VERIFIERS."""
        method_name = self._CONTRACT_PROTOCOL_VERIFIERS.get(protocol)
        if not method_name:
            return
        if protocol == "cli_args" and spec_file.suffix != ".py":
            return
        verifier = getattr(self, method_name, None)
        if callable(verifier):
            verifier(doc, spec_file, report)


