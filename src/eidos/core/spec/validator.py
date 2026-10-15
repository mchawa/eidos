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


def is_entity_id(val: Any) -> bool:
    """Return True if val looks like a standardized Eidos entity ID."""
    if not isinstance(val, str):
        return False
    val_clean = val.strip().upper()
    return any(val_clean.startswith(prefix) for prefix in ENTITY_ID_PREFIXES)

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
        self._load_bundled_schemas()

    def _compute_schema_hash(self) -> str:
        """Compute a deterministic SHA-256 digest across loaded bundled schemas."""
        canonical = {
            k: self._schema_cache[k]
            for k in sorted(self._schema_cache)
            if k.startswith("schemas/v1/") or "/" not in k
        }
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _load_bundled_schemas(self):
        """Preload standard schemas recursively from the assets directory or package data."""
        try:
            schema_dir = get_v1_schemas_dir()
            for p in schema_dir.rglob("*.schema.yaml"):
                content = p.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
                rel_path = str(p.relative_to(schema_dir))
                self._schema_cache[p.name] = data
                self._schema_cache[rel_path] = data
                self._schema_cache[f"schemas/v1/{rel_path}"] = data
                self._schema_cache[f"schemas/v1/{p.name}"] = data
            for p in schema_dir.rglob("*.schema.json"):
                content = p.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
                rel_path = str(p.relative_to(schema_dir))
                self._schema_cache[p.name] = data
                self._schema_cache[rel_path] = data
                self._schema_cache[f"schemas/v1/{rel_path}"] = data
                self._schema_cache[f"schemas/v1/{p.name}"] = data
            self._schema_hash = self._compute_schema_hash()
            self.schema_hash = self._schema_hash
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"Failed to load bundled Eidos schemas: {exc}") from exc

    def _walk_traversable(self, node, rel_prefix: str):
        """Recursively walk an importlib Traversable directory."""
        for item in node.iterdir():
            sub_rel = f"{rel_prefix}/{item.name}".lstrip("/")
            if item.is_dir():
                self._walk_traversable(item, sub_rel)
            elif item.name.endswith(".schema.yaml") or item.name.endswith(".schema.json"):
                data = yaml.safe_load(item.read_text(encoding="utf-8"))
                self._schema_cache[item.name] = data
                self._schema_cache[sub_rel] = data
                self._schema_cache[f"schemas/v1/{sub_rel}"] = data
                self._schema_cache[f"schemas/v1/{item.name}"] = data

    def resolve_schema(self, doc: SpecDocument) -> dict | None:
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
        repo_root = root_dir.parent if root_dir.name in ["eidos", ".spec"] else root_dir
        ancestor_eidos = next(
            (p for p in (resolved_root, *resolved_root.parents) if p.name in ("eidos", ".spec")),
            None,
        )
        if ancestor_eidos is not None:
            eidos_cache_root = ancestor_eidos
            resolved_repo_root = ancestor_eidos.parent
        elif (resolved_root / "eidos").is_dir():
            eidos_cache_root = (resolved_root / "eidos").resolve()
            resolved_repo_root = resolved_root
        else:
            eidos_cache_root = resolved_root
            resolved_repo_root = resolved_root
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
            for base in (resolved_repo_root, resolved_root):
                try:
                    return str(res_fp.relative_to(base)).replace("\\", "/")
                except ValueError:
                    continue
            return str(res_fp).replace("\\", "/")

        def _load_or_validate(fp: Path) -> SpecDocument | None:
            # Skip README, partial include files, symlinks, generated by-status views, and empty files
            if (
                fp.name.lower() == "readme.adoc"
                or fp.name.startswith("_")
                or "by-status" in fp.parts
                or fp.is_symlink()
            ):
                return None
            try:
                st = fp.stat()
            except OSError:
                return None
            if st.st_size == 0:
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
            if not content.strip():
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
                            ref_file = (root_dir / ref_str).resolve()
                        if not ref_file.exists():
                            ref_file = (root_dir / "architecture" / "components" / Path(ref_str).name).resolve()
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
        matches_id = is_entity_id(val) or (bool(prefixes) and val_upper.startswith(prefixes))

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
            if root_dir is not None:
                root_res = root_dir.resolve()
                repo_root = root_res.parent if root_res.name in ["eidos", ".spec"] else root_res
            else:
                ancestor_eidos = next(
                    (p for p in doc.file_path.resolve().parents if p.name in ("eidos", ".spec")),
                    None,
                )
                repo_root = ancestor_eidos.parent if ancestor_eidos is not None else Path.cwd().resolve()

            try:
                rel_parts = doc.file_path.resolve().relative_to(repo_root.resolve()).parts
            except ValueError:
                rel_parts = doc.file_path.parts

            is_centralized = (
                "architecture" in rel_parts
                and "components" in rel_parts
                and any(p in ["eidos", ".spec"] for p in rel_parts)
            )
            if not is_centralized or "src" in rel_parts:
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
            # Resolve relative to repo root or doc folder
            ref_path_from_root = root_dir.parent / val if root_dir.name in ["eidos", ".spec"] else root_dir / val
            ref_path_from_doc = source_file.parent / val
            if not (ref_path_from_root.exists() or ref_path_from_doc.exists()):
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

    def _validate_source_and_contract_paths(self, report: ValidationReport, root_dir: Path) -> None:
        """Verify container package_path, contract specification_file, and CLI contract command parity when source tree exists."""
        repo_root = root_dir.parent if root_dir.name in ("eidos", ".spec") else root_dir

        candidate_paths: list[str] = []
        for doc in report.documents:
            fm = doc.frontmatter or {}
            if doc.doc_type == "tech/container":
                pkg = fm.get("package_path")
                if isinstance(pkg, str) and pkg.strip():
                    candidate_paths.append(pkg.strip())
                elif isinstance(pkg, list):
                    candidate_paths.extend(str(x).strip() for x in pkg if isinstance(x, str) and str(x).strip())
            elif doc.doc_type == "tech/contract":
                sf = fm.get("specification_file")
                if isinstance(sf, str) and sf.strip() and not sf.strip().endswith(".adoc"):
                    candidate_paths.append(sf.strip())

        has_source_tree = (repo_root != root_dir and (repo_root / "src").exists()) or any(
            self._resolve_source_path(repo_root, root_dir, p) is not None for p in candidate_paths
        )
        if not has_source_tree:
            return

        inactive_statuses = {"planned", "proposed", "draft", "deprecated", "sunset", "rejected"}
        for doc in report.documents:
            fm = doc.frontmatter or {}
            status = str(fm.get("status", "active")).strip().lower()
            if status in inactive_statuses:
                continue

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
            elif doc.doc_type == "tech/contract":
                sf = fm.get("specification_file")
                if isinstance(sf, str) and sf.strip() and sf.strip().upper() not in ("N/A", "TBD", "NONE") and not sf.strip().endswith(".adoc"):
                    resolved_sf = self._resolve_source_path(repo_root, root_dir, sf)
                    if resolved_sf is None:
                        report.broken_references.append(
                            f"Broken specification_file in {doc.file_path.name}: "
                            f"Contract '{doc.doc_id}' references '{sf}' which does not exist on disk"
                        )
                    elif str(fm.get("protocol", "")).strip().lower() == "cli_args" and resolved_sf.suffix == ".py":
                        self._verify_cli_contract_commands(doc, resolved_sf, report)

    def _verify_cli_contract_commands(
        self, doc: SpecDocument, cli_file: Path, report: ValidationReport
    ) -> None:
        """Verify that top-level non-hidden Typer CLI commands in specification_file are documented in the contract body."""
        try:
            source = cli_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            return

        # Scoped parity: only verify Typer CLI parity if the target source imports Typer
        if "typer" not in source.lower():
            return
        has_typer = any(
            (isinstance(node, ast.Import) and any(alias.name == "typer" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "typer")
            for node in ast.walk(tree)
        )
        if not has_typer:
            return

        commands: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "app"
                and node.func.attr == "add_typer"
            ):
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        commands.add(kw.value.value)
            elif isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "app"
                        and dec.func.attr == "command"
                    ):
                        hidden = any(
                            kw.arg == "hidden" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                            for kw in dec.keywords
                        )
                        if not hidden:
                            name = next(
                                (
                                    kw.value.value
                                    for kw in dec.keywords
                                    if kw.arg == "name"
                                    and isinstance(kw.value, ast.Constant)
                                    and isinstance(kw.value.value, str)
                                ),
                                None,
                            )
                            if not name and dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                                name = dec.args[0].value
                            commands.add(name or node.name.replace("_", "-"))

        if not commands:
            return

        body = doc.body or ""
        for cmd_name in sorted(commands):
            if not re.search(rf"(?:`|\b){re.escape(cmd_name)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: CLI command '{cmd_name}' defined in '{cli_file.name}' is not documented in contract '{doc.doc_id}'"
                )

