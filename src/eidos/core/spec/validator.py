"""Validator for Eidos specifications against JSON/YAML schemas."""

import ast
import contextlib
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, ClassVar

import jsonschema
import orjson

from eidos.core.index.ast_diff import AST_DIFF_EXTS
from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.spec.boundary_validator import (
    _build_doc_path_to_id_map as _build_doc_path_to_id_map,
)
from eidos.core.spec.boundary_validator import (
    _rel_doc_path as _rel_doc_path,
)
from eidos.core.spec.boundary_validator import (
    evaluate_import_boundaries as _evaluate_import_boundaries,
)
from eidos.core.spec.boundary_validator import (
    validate_import_boundary_declarations,
)
from eidos.core.spec.cli_contract_parity import _parse_python_ast as _parse_python_ast
from eidos.core.spec.contract_parity import ContractParityValidator
from eidos.core.spec.design_validation import VALID_DTCG_TYPES, validate_design_systems
from eidos.core.spec.models import (
    ENTITY_ID_PREFIXES,
    TYPE_TO_SCHEMA_NAME,
    SpecDocument,
    ValidationReport,
    infer_entity_type_from_path,
    resolve_entity_prefix_meta,
)
from eidos.core.spec.parser import YamlError, safe_load_yaml
from eidos.core.spec.surface_validator import PublicSurfaceValidator
from eidos.core.workspace.assets import get_v1_schemas_dir
from eidos.core.workspace.project import (
    expand_brace_globs,
    resolve_spec_and_repo_roots,
)

_ID_PATTERN_PREFIX_RE = re.compile(r"^\^?([A-Z][A-Z0-9]*-)")
_DYNAMIC_ENTITY_ID_PREFIXES: set[str] = set(ENTITY_ID_PREFIXES)


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


def is_entity_id(val: Any) -> bool:
    """Return True if val looks like a standardized Eidos entity ID."""
    if not isinstance(val, str):
        return False
    val_clean = val.strip().upper()
    return any(val_clean.startswith(prefix) for prefix in _DYNAMIC_ENTITY_ID_PREFIXES)


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
            orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)
        ).hexdigest()

    def _load_bundled_schemas(self):
        """Preload standard schemas recursively from the assets directory or package data."""
        try:
            schema_dir = get_v1_schemas_dir()
            common_defs_path = schema_dir / "common" / "defs.yaml"
            shared_defs: dict[str, Any] = {}
            if common_defs_path.is_file():
                defs_doc = safe_load_yaml(common_defs_path.read_text(encoding="utf-8")) or {}
                if isinstance(defs_doc, dict) and isinstance(defs_doc.get("$defs"), dict):
                    shared_defs = defs_doc["$defs"]

            extracted_prefixes: set[str] = set(ENTITY_ID_PREFIXES)
            for defs_item in shared_defs.values():
                if isinstance(defs_item, dict):
                    extracted_prefixes.update(_extract_prefixes_from_schema(defs_item))

            all_schema_paths = sorted(p for ext in ("*.schema.yaml", "*.schema.json") for p in schema_dir.rglob(ext))

            for p in all_schema_paths:
                content = p.read_text(encoding="utf-8")
                data = safe_load_yaml(content)
                if isinstance(data, dict):
                    if shared_defs and b'"#/$defs/' in orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS, default=str):
                        merged_defs = dict(shared_defs)
                        if isinstance(data.get("$defs"), dict):
                            merged_defs.update(data["$defs"])
                        data["$defs"] = merged_defs
                    extracted_prefixes.update(_extract_prefixes_from_schema(data))
                rel_path = str(p.relative_to(schema_dir)).replace("\\", "/")
                self._schema_cache[rel_path] = data
                self._schema_cache[f"schemas/v1/{rel_path}"] = data

            _DYNAMIC_ENTITY_ID_PREFIXES.update(extracted_prefixes)
            self.entity_id_prefixes = tuple(sorted(extracted_prefixes))
            self._schema_hash = self._compute_schema_hash()
            self.schema_hash = self._schema_hash
        except (OSError, YamlError) as exc:
            raise RuntimeError(f"Failed to load bundled Eidos schemas: {exc}") from exc

    def resolve_schema(self, doc: SpecDocument) -> dict | None:
        """Resolve the JSON schema for a document deterministically from its type."""
        doc_type_str = doc.doc_type if isinstance(doc.doc_type, str) else None
        if doc_type_str and doc_type_str in TYPE_TO_SCHEMA_NAME:
            schema_name = TYPE_TO_SCHEMA_NAME[doc_type_str]
            return self._schema_cache.get(schema_name)
        return None

    def validate_document(self, doc: SpecDocument, root_dir: Path | None = None) -> list[str]:
        """Validate a single parsed document against its schema and document-local architectural invariants."""
        if not doc.frontmatter:
            return doc.errors

        schema = self.resolve_schema(doc)
        if not schema:
            no_schema_msg = f"No schema found for document type '{doc.doc_type}'"
            if no_schema_msg not in doc.errors:
                doc.errors.append(no_schema_msg)
            return doc.errors

        validator = jsonschema.Draft202012Validator(schema)
        errors = []
        for err in validator.iter_errors(doc.frontmatter):
            loc = ".".join(str(p) for p in err.absolute_path)
            prefix = f"Property '{loc}'" if loc else "Root frontmatter"
            errors.append(f"{prefix}: {err.message}")

        for err_msg in errors:
            if err_msg not in doc.errors:
                doc.errors.append(err_msg)
        self._validate_architectural_invariants(doc, doc.frontmatter, report=None, root_dir=root_dir)
        return doc.errors

    def validate_directory(self, root_dir: Path, *, check_import_boundaries: bool = True) -> ValidationReport:
        """Scan a directory for .adoc files, parse, and validate each against its schema."""
        report = ValidationReport()
        adoc_files = sorted(root_dir.rglob("*.adoc"))

        effective_schema_hash = (
            self.schema_hash if self.schema_hash != self._schema_hash else self._compute_schema_hash()
        )
        self.schema_hash = effective_schema_hash

        resolved_root = root_dir.resolve()
        spec_root, repo_root = resolve_spec_and_repo_roots(root_dir)
        eidos_cache_root = spec_root
        resolved_repo_root = repo_root
        db_path = eidos_cache_root / ".cache" / "index.db"

        cached_rows: dict[str, sqlite3.Row] = {}
        if eidos_cache_root.exists():
            from eidos.core.index.storage import (
                get_cached_spec_document,
                load_spec_validation_cache,
                save_spec_validation_cache,
            )

            cached_rows = load_spec_validation_cache(db_path)
        else:
            from eidos.core.index.storage import (
                get_cached_spec_document,
                save_spec_validation_cache,
            )

        seen_rel_keys: set[str] = set()
        to_upsert: list[tuple[str, int, str, str, str]] = []
        fast_pathed_applied_deltas: set[str] = set()

        def _rel_key(fp: Path) -> str:
            res_fp = fp.resolve()
            for base in (resolved_repo_root, spec_root, resolved_root):
                try:
                    return str(res_fp.relative_to(base)).replace("\\", "/")
                except ValueError:
                    continue
            return str(res_fp).replace("\\", "/")

        from eidos.core.spec.parser import _NON_ROOT_SPEC_SUBDIRS

        def _load_or_validate(fp: Path) -> SpecDocument | None:
            try:
                rel_fp_parts = fp.resolve().relative_to(spec_root).parts
            except ValueError:
                try:
                    rel_fp_parts = fp.resolve().relative_to(resolved_repo_root).parts
                except ValueError:
                    rel_fp_parts = (fp.name,)
            # Skip README, partial include files, symlinks, and empty files
            if fp.name.lower() == "readme.adoc" or fp.name.startswith("_") or fp.is_symlink():
                return None
            try:
                st = fp.stat()
            except OSError:
                return None
            parent_lower = fp.parent.name.lower()
            ancestor_lowers = [p.lower() for p in fp.parts[:-1]]
            is_root_system = (
                fp.name == "system.adoc"
                and rel_fp_parts == ("system.adoc",)
                and parent_lower not in _NON_ROOT_SPEC_SUBDIRS
                and (
                    parent_lower == "eidos"
                    or (
                        "eidos" not in ancestor_lowers
                        and (not fp.parent.parent or fp.parent.parent.name.lower() not in _NON_ROOT_SPEC_SUBDIRS)
                    )
                )
            )
            if st.st_size == 0 and is_root_system:
                return None

            mtime_ns = st.st_mtime_ns
            rel_key = _rel_key(fp)
            seen_rel_keys.add(rel_key)
            row = cached_rows.get(rel_key)

            if row is not None and row["mtime_ns"] == mtime_ns and row["schema_hash"] == effective_schema_hash:
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    doc_data = orjson.loads(row["doc_json"])
                    schema_errs = list(doc_data.get("errors", [])) if isinstance(doc_data, dict) else []
                    cached_doc, _, _ = get_cached_spec_document(
                        fp, mtime_ns=mtime_ns, st_size=st.st_size, cached_row=row
                    )
                    if cached_doc is not None:
                        cached_doc.errors = list(schema_errs)
                        if (
                            cached_doc.doc_type == "delta/mutation"
                            and (cached_doc.model.status or "").lower() == "applied"
                            and not schema_errs
                        ):
                            fast_pathed_applied_deltas.add(str(fp.resolve()))
                        return cached_doc

            doc, sha256, mtime_ns = get_cached_spec_document(fp, mtime_ns=mtime_ns, st_size=st.st_size, cached_row=row)
            if doc is None or not sha256:
                return None
            if is_root_system and not doc.frontmatter and not doc.body.strip() and not doc.errors:
                return None

            if row is not None and row["sha256"] == sha256 and row["schema_hash"] == effective_schema_hash:
                with contextlib.suppress(ValueError, TypeError, KeyError):
                    doc_data = orjson.loads(row["doc_json"])
                    schema_errs = list(doc_data.get("errors", [])) if isinstance(doc_data, dict) else []
                    to_upsert.append(
                        (
                            rel_key,
                            mtime_ns,
                            sha256,
                            effective_schema_hash,
                            row["doc_json"],
                        )
                    )
                    doc.errors = list(schema_errs)
                    if (
                        doc.doc_type == "delta/mutation"
                        and (doc.model.status or "").lower() == "applied"
                        and not schema_errs
                    ):
                        fast_pathed_applied_deltas.add(str(fp.resolve()))
                    return doc
            parse_errs = list(doc.errors)
            self.validate_document(doc, root_dir=root_dir)
            schema_errs = list(doc.errors)
            doc_payload = {
                "doc_id": doc.doc_id,
                "doc_type": doc.doc_type,
                "schema_ref": doc.schema_ref,
                "title": doc.title,
                "frontmatter": doc.frontmatter,
                "body": doc.body,
                "parse_errors": parse_errs,
                "errors": schema_errs,
            }
            to_upsert.append(
                (
                    rel_key,
                    mtime_ns,
                    sha256,
                    effective_schema_hash,
                    orjson.dumps(
                        doc_payload, default=str, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS
                    ).decode("utf-8"),
                )
            )
            return doc

        for file_path in adoc_files:
            doc = _load_or_validate(file_path)
            if doc is None:
                continue
            report.documents.append(doc)
            if not doc.is_valid:
                err_key = self._schema_error_key(file_path, root_dir=root_dir, repo_root=resolved_repo_root)
                report.schema_errors[err_key] = list(doc.errors)

        deleted_keys = [k for k in cached_rows if k not in seen_rel_keys] if resolved_root == eidos_cache_root else []
        if eidos_cache_root.exists() and (to_upsert or deleted_keys):
            save_spec_validation_cache(db_path, to_upsert, deleted_keys)

        # Cross-reference validation
        self._validate_cross_references(report, root_dir, fast_pathed_applied_deltas=fast_pathed_applied_deltas)

        if check_import_boundaries and resolved_root == spec_root:
            violations, _shrink = evaluate_import_boundaries(
                spec_root,
                repo_root=resolved_repo_root,
                documents=report.documents,
            )
            doc_by_key = {
                self._schema_error_key(d.file_path, root_dir=root_dir, repo_root=resolved_repo_root): d
                for d in report.documents
            }
            for v in violations:
                spec_key = str(v.get("spec_path") or "eidos/architecture/system-architecture.adoc")
                ev_list = v.get("evidence") or []
                ev_sample = f" (e.g. {ev_list[0]})" if ev_list else ""
                max_imp = int(v.get("max_imports") or 0)
                obs_imp = int(v.get("observed_imports") or 0)
                limit_str = (
                    f"observed {obs_imp} > ratchet max_imports {max_imp}"
                    if max_imp > 0
                    else f"{obs_imp} forbidden coupling(s)"
                )
                msg = (
                    f"Import boundary violation [{v['rule']}]: {v['source']} -> {v['target']} ({limit_str}){ev_sample}"
                )
                bucket = report.schema_errors.setdefault(spec_key, [])
                if msg not in bucket:
                    bucket.append(msg)
                target_doc = doc_by_key.get(spec_key)
                if target_doc is not None and msg not in target_doc.errors:
                    target_doc.errors.append(msg)

        return report

    def _validate_cross_references(
        self,
        report: ValidationReport,
        root_dir: Path,
        fast_pathed_applied_deltas: set[str] | None = None,
    ):
        """Ensure all relative file links in YAML frontmatters and entity ID cross-references resolve."""
        # 1. Index all document IDs across the specification catalog and detect duplicate IDs
        report.entity_index = {}
        for doc in report.documents:
            if isinstance(doc.doc_id, str) and doc.doc_id:
                if doc.doc_id in report.entity_index:
                    prev_doc = report.entity_index[doc.doc_id]
                    report.broken_references.append(
                        f"Duplicate entity ID '{doc.doc_id}' found in {doc.file_path.name} (already defined in {prev_doc.file_path.name})"
                    )
                else:
                    report.entity_index[doc.doc_id] = doc

        catalog_ids = set(report.entity_index.keys())
        entity_types: dict[str, str] = {
            doc_id: doc.doc_type
            for doc_id, doc in report.entity_index.items()
            if isinstance(doc.doc_type, str) and doc.doc_type
        }

        # Register embedded invariant IDs declared within documents in catalog_ids
        # and enforce global uniqueness across the catalog.
        inv_owners: dict[str, SpecDocument] = {}
        for doc in report.documents:
            if doc.frontmatter:
                for inv in doc.model.all_invariants():
                    if not inv.id:
                        continue
                    if inv.id in inv_owners or inv.id in catalog_ids:
                        prev_doc = inv_owners.get(inv.id) or report.entity_index.get(inv.id)
                        prev_name = prev_doc.file_path.name if prev_doc else doc.file_path.name
                        report.broken_references.append(
                            f"Duplicate entity ID '{inv.id}' found in {doc.file_path.name} (already defined in {prev_name})"
                        )
                    else:
                        inv_owners[inv.id] = doc
                        catalog_ids.add(inv.id)
                        entity_types.setdefault(inv.id, "invariant")

        # Register embedded architectural decision IDs (DEC-*) and enforce global uniqueness across the catalog
        decision_owners: dict[str, SpecDocument] = {}
        for doc in report.documents:
            if not doc.frontmatter:
                continue
            for dec in doc.model.all_decisions():
                dec_id = dec.id
                if not dec_id:
                    continue
                if dec_id in decision_owners or dec_id in catalog_ids:
                    prev_doc = decision_owners.get(dec_id) or report.entity_index.get(dec_id)
                    prev_name = prev_doc.file_path.name if prev_doc else doc.file_path.name
                    msg = (
                        f"Duplicate entity ID '{dec_id}' found in {doc.file_path.name} (already defined in {prev_name})"
                    )
                    if doc.doc_type == "delta/mutation" or (
                        prev_doc is not None and prev_doc.doc_type == "delta/mutation"
                    ):
                        msg += (
                            " — Hint: record planned DEC-* decisions solely on the owning CNT-*/CMP-* specification "
                            "(not duplicated in the SpecDelta's decisions frontmatter)."
                        )
                    report.broken_references.append(msg)
                else:
                    decision_owners[dec_id] = doc
                    catalog_ids.add(dec_id)
                    entity_types[dec_id] = "tech/decision"

        # When validating a subdirectory of an existing spec_root (eidos/domains), seed
        # catalog_ids and entity_types from sibling specs in spec_root so cross-directory
        # references (such as SYS-* or CNT-*) do not falsely fail.
        spec_root, _ = resolve_spec_and_repo_roots(root_dir)
        resolved_root = root_dir.resolve()
        if (
            spec_root != resolved_root
            and resolved_root.is_relative_to(spec_root)
            and ((spec_root / "system.adoc").is_file() or (spec_root / "architecture").is_dir())
        ):
            from eidos.core.index.storage import get_cached_spec_document

            scanned_paths = {d.file_path.resolve() for d in report.documents}
            for ext_fp in sorted(spec_root.rglob("*.adoc")):
                if (
                    ext_fp.name.lower() == "readme.adoc"
                    or ext_fp.name.startswith("_")
                    or ext_fp.is_symlink()
                    or ext_fp.resolve() in scanned_paths
                ):
                    continue
                with contextlib.suppress(OSError):
                    ext_doc, _, _ = get_cached_spec_document(ext_fp)
                    if ext_doc is None:
                        continue
                    if isinstance(ext_doc.doc_id, str) and ext_doc.doc_id:
                        catalog_ids.add(ext_doc.doc_id)
                        if isinstance(ext_doc.doc_type, str) and ext_doc.doc_type:
                            entity_types.setdefault(ext_doc.doc_id, ext_doc.doc_type)
                    for inv in ext_doc.model.all_invariants():
                        if inv.id:
                            catalog_ids.add(inv.id)
                            entity_types.setdefault(inv.id, "invariant")

        for doc in report.documents:
            # 2. Check relative file paths ending in .adoc
            self._check_dict_references(doc.frontmatter, doc.file_path, root_dir, report, doc_type=doc.doc_type)

            # 3. Check semantic entity ID cross-references
            self._validate_semantic_references(doc, catalog_ids, report, root_dir, entity_types)

        # 4. Check source/contract file paths on disk and CLI contract command parity
        self._validate_source_and_contract_paths(report, root_dir)

        # 5. Check design/system W3C DTCG token manifests and acyclic DS-* inheritance
        self._validate_design_systems(report, root_dir)

        # 6. Check static import_boundary / import_boundaries disjointness and tier resolution
        self._validate_import_boundary_declarations(report, root_dir)

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
            self._validate_schema_references(schema, fm, "", doc, catalog_ids, report, entity_types, root_dir=root_dir)
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
        root_dir: Path | None = None,
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
                    merged_node = {**resolved_def, **{k: v for k, v in schema_node.items() if k != "$ref"}}
                    self._validate_schema_references(
                        merged_node, value, field_path, doc, catalog_ids, report, entity_types, root_dir=root_dir
                    )
            return

        ref_meta = schema_node.get("x-eidos-ref")
        if isinstance(ref_meta, dict):
            if isinstance(value, str):
                self._check_eidos_ref(
                    ref_meta, value, field_path, doc, catalog_ids, report, entity_types, root_dir=root_dir
                )
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        self._check_eidos_ref(
                            ref_meta, item, field_path, doc, catalog_ids, report, entity_types, root_dir=root_dir
                        )

        if isinstance(value, str):
            for combiner in ("oneOf", "anyOf", "allOf"):
                for branch in schema_node.get(combiner, []) or []:
                    if isinstance(branch, dict):
                        self._validate_schema_references(
                            branch,
                            value,
                            field_path,
                            doc,
                            catalog_ids,
                            report,
                            entity_types,
                            root_dir=root_dir,
                        )
        elif isinstance(value, dict):
            props = schema_node.get("properties")
            if isinstance(props, dict):
                for prop_key, prop_val in value.items():
                    if prop_key in props:
                        sub_path = f"{field_path}.{prop_key}" if field_path else prop_key
                        self._validate_schema_references(
                            props[prop_key],
                            prop_val,
                            sub_path,
                            doc,
                            catalog_ids,
                            report,
                            entity_types,
                            root_dir=root_dir,
                        )
            for combiner in ("oneOf", "anyOf", "allOf"):
                for branch in schema_node.get(combiner, []) or []:
                    if isinstance(branch, dict):
                        if "$ref" in branch:
                            self._validate_schema_references(
                                branch,
                                value,
                                field_path,
                                doc,
                                catalog_ids,
                                report,
                                entity_types,
                                root_dir=root_dir,
                            )
                        elif isinstance(branch.get("properties"), dict):
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
                                        root_dir=root_dir,
                                    )
        elif isinstance(value, list):
            items_schema = schema_node.get("items")
            if isinstance(items_schema, dict):
                for item in value:
                    self._validate_schema_references(
                        items_schema, item, field_path, doc, catalog_ids, report, entity_types, root_dir=root_dir
                    )
            for combiner in ("oneOf", "anyOf", "allOf"):
                for branch in schema_node.get(combiner, []) or []:
                    if isinstance(branch, dict) and ("$ref" in branch or "items" in branch or "x-eidos-ref" in branch):
                        self._validate_schema_references(
                            branch,
                            value,
                            field_path,
                            doc,
                            catalog_ids,
                            report,
                            entity_types,
                            root_dir=root_dir,
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
        root_dir: Path | None = None,
    ) -> None:
        """Check a single string reference against x-eidos-ref metadata."""
        val = raw_val.strip()
        if not val:
            return
        if val.endswith(".adoc"):
            target_types = tuple(str(t) for t in (ref_meta.get("target_types") or ()))
            if target_types and root_dir is not None:
                from eidos.core.index.storage import get_cached_spec_document

                spec_root, repo_root = resolve_spec_and_repo_roots(root_dir, doc.file_path)
                for cand in (
                    repo_root / val,
                    spec_root / val,
                    root_dir / val,
                    doc.file_path.parent / val,
                ):
                    if cand.is_file():
                        ref_doc, _, _ = get_cached_spec_document(cand)
                        actual_type = ref_doc.doc_type if ref_doc is not None else None
                        if actual_type and actual_type not in target_types:
                            label = ref_meta.get("entity_label") or "entity"
                            expected_types = ", ".join(target_types)
                            report.broken_references.append(
                                f"Invalid entity reference type in {doc.file_path.name}: "
                                f"{label} '{val}' in {field_path} has type '{actual_type}', expected {expected_types}"
                            )
                        break
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
                exempt_targets: set[str] = set()
                for mut in fm.get("mutations") or []:
                    if not isinstance(mut, dict):
                        continue
                    op = str(mut.get("operation") or "").strip().lower()
                    if op in ("add", "remove") and mut.get("target"):
                        exempt_targets.add(str(mut["target"]).strip())
                if val in exempt_targets:
                    return

            rule_id = ref_meta.get("rule_id") or (
                "INV-COMPONENT-CONTAINER-AFFINITY"
                if doc.doc_type == "tech/component" and field_path == "container"
                else None
            )
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
            return

        if actual_type and field_path == "mutations.target":
            p_meta = resolve_entity_prefix_meta(val)
            if p_meta is not None and actual_type != p_meta[2]:
                label = ref_meta.get("entity_label") or "entity"
                report.broken_references.append(
                    f"Invalid entity reference type in {doc.file_path.name}: "
                    f"{label} '{val}' in {field_path} has type '{actual_type}', expected {p_meta[2]}"
                )

    @staticmethod
    def _schema_error_key(doc_path: Path, root_dir: Path | None = None, repo_root: Path | None = None) -> str:
        """Return a repo-relative POSIX path key for ValidationReport.schema_errors."""
        eff_repo = (
            repo_root.resolve()
            if repo_root is not None
            else resolve_spec_and_repo_roots(root_dir, doc_path)[1].resolve()
        )
        try:
            return doc_path.resolve().relative_to(eff_repo).as_posix()
        except ValueError:
            return str(doc_path)

    def _validate_architectural_invariants(
        self,
        doc: SpecDocument,
        fm: dict[str, Any],
        report: ValidationReport | None = None,
        root_dir: Path | None = None,
    ) -> None:
        """Enforce structural boundary invariants across contracts, dedicated components, and composable SpecDeltas."""

        def _record_err(msg: str) -> None:
            if msg not in doc.errors:
                doc.errors.append(msg)
            if report is not None:
                err_key = self._schema_error_key(doc.file_path, root_dir=root_dir)
                bucket = report.schema_errors.setdefault(err_key, [])
                if msg not in bucket:
                    bucket.append(msg)

        dtype = doc.doc_type
        if dtype == "delta/mutation":
            for mut in fm.get("mutations") or []:
                if not isinstance(mut, dict):
                    continue
                op_val = str(mut.get("operation") or "").strip().lower()
                target_val = str(mut.get("target") or "").strip()
                if not op_val or not target_val:
                    continue
                target_upper = target_val.upper()
                effective_target_upper = target_upper
                if target_upper.endswith(".ADOC"):
                    inferred = infer_entity_type_from_path(target_val)
                    if inferred is not None:
                        effective_target_upper = inferred[2]
                if op_val == "deprecate" and effective_target_upper.startswith("INV-"):
                    _record_err(
                        f"Invalid composable mutation in {doc.file_path.name}: "
                        f"operation 'deprecate' is not supported for INV-* targets (got '{target_val}')."
                    )
        elif dtype == "tech/contract":
            owning_ref = str(fm.get("container") or "").strip()
            if owning_ref:
                ref_upper = owning_ref.upper()
                is_cmp_ref = ref_upper.startswith("CMP-") or (
                    ref_upper.endswith(".ADOC")
                    and (
                        Path(owning_ref).stem.upper().startswith("CMP-")
                        or "components" in [p.lower() for p in Path(owning_ref.replace("\\", "/")).parts]
                    )
                )
                if is_cmp_ref:
                    _record_err(
                        f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Contract '{doc.doc_id}' specifies in-process component '{owning_ref}' as owner. "
                        f"Boundary Protocol Contracts (CTR-*) must be owned by a Container (CNT-*)."
                    )
        elif dtype == "tech/component":
            if doc.file_path.suffix == ".adoc":
                spec_root, repo_root = resolve_spec_and_repo_roots(root_dir, doc.file_path)
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
                    len(rel_to_spec) >= 2 and rel_to_spec[0] == "architecture" and rel_to_spec[1] == "components"
                )
                if not is_centralized or "src" in rel_to_repo:
                    _record_err(
                        f"INV-COMPONENT-CENTRALIZATION-MANDATE violation: Component manifest '{doc.doc_id}' must reside "
                        f"centrally in 'eidos/architecture/components/'. Eidos specifications must never be placed inside source code directories."
                    )

            if fm.get("contracts"):
                _record_err(
                    f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Component '{doc.doc_id}' cannot declare boundary protocol contracts. "
                    f"Boundary contracts (CTR-*) must be declared on a Container (CNT-*)."
                )
        elif dtype == "design/screen" and doc.file_path.suffix == ".adoc":
            spec_root, _ = resolve_spec_and_repo_roots(root_dir, doc.file_path)
            res_doc = doc.file_path.resolve()
            try:
                rel_to_spec = tuple(p.lower() for p in res_doc.relative_to(spec_root.resolve()).parts)
            except ValueError:
                rel_to_spec = ()
            if not rel_to_spec or rel_to_spec[0] != "design":
                lower_parts = tuple(p.lower() for p in res_doc.parts)
                if "design" in lower_parts:
                    design_idx = len(lower_parts) - 1 - lower_parts[::-1].index("design")
                    rel_to_spec = lower_parts[design_idx:]
            is_client_scoped = (
                len(rel_to_spec) >= 5
                and rel_to_spec[0] == "design"
                and rel_to_spec[1] == "clients"
                and bool(rel_to_spec[2])
                and rel_to_spec[3] == "screens"
            )
            if not is_client_scoped:
                _record_err(
                    f"INV-DESIGN-SCREEN-CLIENT-OWNERSHIP violation: Screen manifest '{doc.doc_id}' must reside "
                    f"under 'eidos/design/clients/<client>/screens/<screen>.adoc'."
                )

    def _check_dict_references(
        self,
        val,
        source_file: Path,
        root_dir: Path,
        report: ValidationReport,
        doc_type: str | None = None,
    ):
        """Recursively check string fields ending in .adoc to verify they exist."""
        if doc_type is None and isinstance(val, dict) and str(val.get("type") or "").strip() == "delta/mutation":
            doc_type = "delta/mutation"
        is_delta = doc_type == "delta/mutation"
        if isinstance(val, dict):
            delta_status = str(val.get("status") or "").strip().lower() if is_delta else ""
            for k, v in val.items():
                if is_delta:
                    if k in ("tombstone", "expect"):
                        continue
                    if k == "mutations":
                        if delta_status in ("applied", "rejected"):
                            continue
                        if isinstance(v, list):
                            for mut in v:
                                if isinstance(mut, dict):
                                    op = str(mut.get("operation") or "").strip().lower()
                                    for mk, mv in mut.items():
                                        if mk in ("expect", "details"):
                                            continue
                                        if op not in ("modify", "deprecate"):
                                            continue
                                        self._check_dict_references(
                                            mv, source_file, root_dir, report, doc_type=doc_type
                                        )
                                else:
                                    self._check_dict_references(mut, source_file, root_dir, report, doc_type=doc_type)
                        continue
                self._check_dict_references(v, source_file, root_dir, report, doc_type=doc_type)
        elif isinstance(val, list):
            for item in val:
                self._check_dict_references(item, source_file, root_dir, report, doc_type=doc_type)
        elif isinstance(val, str) and val.endswith(".adoc"):
            spec_root, repo_root = resolve_spec_and_repo_roots(root_dir, source_file)
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

    def _resolve_all_source_paths(self, repo_root: Path, root_dir: Path, raw_path: str) -> list[Path]:
        """Resolve a non-.adoc source path or glob pattern (including {a,b} brace globs) to all matching paths."""
        p = raw_path.strip()
        if not p or p.upper() in ("N/A", "TBD", "NONE") or p.endswith(".adoc"):
            return []
        resolved: list[Path] = []
        seen: set[Path] = set()
        for expanded in expand_brace_globs(p):
            if any(ch in expanded for ch in ("*", "?", "[")):
                matches = sorted(repo_root.glob(expanded)) or sorted(root_dir.glob(expanded))
                for m in matches:
                    if m not in seen:
                        seen.add(m)
                        resolved.append(m)
                continue
            for base in (repo_root, root_dir):
                cand = base / expanded
                if cand.exists():
                    if cand not in seen:
                        seen.add(cand)
                        resolved.append(cand)
                    break
        return resolved

    def _resolve_source_path(self, repo_root: Path, root_dir: Path, raw_path: str) -> Path | None:
        """Resolve a non-.adoc source path or glob pattern (including {a,b} brace globs) against repo_root or root_dir."""
        matches = self._resolve_all_source_paths(repo_root, root_dir, raw_path)
        return matches[0] if matches else None

    _TEST_SYMBOL_CACHE: ClassVar[dict[tuple[str, int, str], tuple[set[str], set[str]]]] = {}

    @classmethod
    def _extract_cached_test_symbols(cls, test_file: Path, raw_bytes: bytes, source: str) -> tuple[set[str], set[str]]:
        """Return cached (unqualified_names, qualified_names) for test_file via the polyglot AST indexer."""
        digest = hashlib.sha256(raw_bytes).hexdigest()
        cache_key = (str(test_file), len(raw_bytes), digest)
        cached = cls._TEST_SYMBOL_CACHE.get(cache_key)
        if cached is not None:
            return cached

        indexer = get_ast_indexer()
        test_funcs = indexer.list_test_functions(test_file, content=source)
        symbols = indexer.extract_symbols(test_file, content=source)

        unqualified_names: set[str] = {sym.name for sym in symbols if sym.name}
        qualified_names: set[str] = set()
        for k in test_funcs:
            if "::" in k or "/" in k:
                qualified_names.add(k)
                qualified_names.add(k.replace("/", "::"))
                qualified_names.add(k.replace("::", "/"))
                unqualified_names.add(k.replace("/", "::").rsplit("::", 1)[-1])
            else:
                unqualified_names.add(k)
        for sym in symbols:
            if sym.parent_symbol and sym.name:
                qualified_names.add(f"{sym.parent_symbol}::{sym.name}")
                qualified_names.add(f"{sym.parent_symbol}/{sym.name}")

        if test_file.suffix.lower() == ".py":
            with contextlib.suppress(SyntaxError, ValueError):
                tree = _parse_python_ast(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        unqualified_names.add(node.name)
                    elif isinstance(node, ast.ClassDef):
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                qualified_names.add(f"{node.name}::{item.name}")
                                qualified_names.add(f"{node.name}/{item.name}")

        if len(cls._TEST_SYMBOL_CACHE) >= 512:
            cls._TEST_SYMBOL_CACHE.clear()
        cls._TEST_SYMBOL_CACHE[cache_key] = (unqualified_names, qualified_names)
        return unqualified_names, qualified_names

    @classmethod
    def _test_symbol_exists(cls, test_file: Path, symbol_parts: list[str]) -> bool:
        """Return True if the target test function/method/subtest symbol exists inside test_file."""
        if not symbol_parts:
            return True
        try:
            raw_bytes = test_file.read_bytes()
            source = raw_bytes.decode("utf-8", errors="replace")
        except OSError:
            return False

        suffix = test_file.suffix.lower()
        leaf_sym = symbol_parts[-1]
        if suffix in AST_DIFF_EXTS:
            unqualified_names, qualified_names = cls._extract_cached_test_symbols(test_file, raw_bytes, source)
            joined_colons = "::".join(symbol_parts)
            joined_slashes = "/".join(symbol_parts)
            if len(symbol_parts) >= 2:
                return bool(
                    joined_colons in qualified_names
                    or joined_slashes in qualified_names
                    or joined_colons in unqualified_names
                    or joined_slashes in unqualified_names
                )
            return bool(leaf_sym in unqualified_names or leaf_sym in qualified_names)

        stripped_source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
        stripped_source = re.sub(r"(?:^|\s)(?://|#)[^\r\n]*", " ", stripped_source)
        return bool(re.search(rf"\b{re.escape(leaf_sym)}\b", stripped_source))

    def _validate_source_and_contract_paths(self, report: ValidationReport, root_dir: Path) -> None:
        """Verify container and component source_path, contract specification_file, and invariant test_binding references when source/test trees exist on disk."""
        spec_root, repo_root = resolve_spec_and_repo_roots(root_dir)

        candidate_paths: list[str] = []
        candidate_test_files: list[str] = []
        for doc in report.documents:
            model = doc.model
            if doc.doc_type in ("tech/container", "tech/component"):
                candidate_paths.extend(model.source_paths())
            elif doc.doc_type == "tech/contract":
                sf = model.specification_file
                if isinstance(sf, str) and sf.strip() and not sf.strip().endswith(".adoc"):
                    candidate_paths.append(sf.strip())
            for inv in model.all_invariants():
                raw_bind = inv.effective_test_binding()
                if isinstance(raw_bind, str):
                    bind_val = raw_bind.strip()
                    if (
                        bind_val
                        and bind_val.upper() not in ("N/A", "TBD", "NONE")
                        and not bind_val.endswith(".adoc")
                        and "::" in bind_val
                    ):
                        tf = bind_val.split("::", 1)[0].strip()
                        if tf:
                            candidate_test_files.append(tf)

        _non_code_top_dirs = {
            "eidos",
            ".spec",
            "tests",
            "test",
            "docs",
            "doc",
            "assets",
            "schemas",
        }
        _code_manifest_files = {
            "go.mod",
            "cargo.toml",
            "package.json",
            "pyproject.toml",
            "setup.py",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "cmakelists.txt",
            "mix.exs",
            "gemfile",
            "composer.json",
        }
        has_repo_code_footprint = False
        if repo_root != spec_root or repo_root != root_dir.resolve():
            if (repo_root / "src").exists():
                has_repo_code_footprint = True
            else:
                with contextlib.suppress(OSError):
                    for child in repo_root.iterdir():
                        cname = child.name
                        if cname.startswith("."):
                            continue
                        if child.is_dir() and cname.lower() not in _non_code_top_dirs:
                            has_repo_code_footprint = True
                            break
                        if child.is_file() and (
                            cname.lower() in _code_manifest_files or child.suffix.lower() in AST_DIFF_EXTS
                        ):
                            has_repo_code_footprint = True
                            break

        has_source_tree = has_repo_code_footprint or any(
            self._resolve_source_path(repo_root, root_dir, p) is not None for p in candidate_paths
        )

        has_test_tree = (
            (repo_root / "tests").exists()
            or (root_dir / "tests").exists()
            or (repo_root / "test").exists()
            or (root_dir / "test").exists()
            or any(self._resolve_source_path(repo_root, root_dir, tf) is not None for tf in candidate_test_files)
            or (
                has_source_tree
                and any(tf and not tf.replace("\\", "/").startswith(("tests/", "test/")) for tf in candidate_test_files)
            )
        )

        self._validate_test_bindings(report, repo_root, root_dir, has_test_tree)

        if not has_source_tree:
            return

        inactive_statuses = {
            "planned",
            "proposed",
            "draft",
            "deprecated",
            "retired",
            "discarded",
            "rejected",
        }
        active_arch_docs: list[SpecDocument] = []
        surface_validator = PublicSurfaceValidator()
        repo_symbol_acc = surface_validator._new_repo_symbol_accumulator()
        for doc in report.documents:
            model = doc.model
            status = str(model.status or "active").strip().lower()
            if status in inactive_statuses:
                continue

            if doc.doc_type == "tech/container":
                missing_sp = False
                for raw_p in model.source_paths():
                    if (
                        raw_p.strip().upper() not in ("N/A", "TBD", "NONE")
                        and self._resolve_source_path(repo_root, root_dir, raw_p) is None
                    ):
                        missing_sp = True
                        report.broken_references.append(
                            f"Broken source_path in {doc.file_path.name}: "
                            f"Container '{doc.doc_id}' references '{raw_p}' which does not exist on disk"
                        )
                if not missing_sp:
                    active_arch_docs.append(doc)
                    surface_validator._verify_entity_public_surface(
                        doc, repo_root, root_dir, report, repo_symbol_acc=repo_symbol_acc
                    )
            elif doc.doc_type == "tech/component":
                missing_sp = False
                for raw_sp in model.source_paths():
                    if (
                        raw_sp.strip().upper() not in ("N/A", "TBD", "NONE")
                        and self._resolve_source_path(repo_root, root_dir, raw_sp) is None
                    ):
                        missing_sp = True
                        report.broken_references.append(
                            f"Broken source_path in {doc.file_path.name}: "
                            f"Component '{doc.doc_id}' references '{raw_sp}' which does not exist on disk"
                        )
                if not missing_sp:
                    active_arch_docs.append(doc)
                    surface_validator._verify_entity_public_surface(
                        doc, repo_root, root_dir, report, repo_symbol_acc=repo_symbol_acc
                    )
            elif doc.doc_type == "tech/contract":
                sf = model.specification_file
                if (
                    isinstance(sf, str)
                    and sf.strip()
                    and sf.strip().upper() not in ("N/A", "TBD", "NONE")
                    and not sf.strip().endswith(".adoc")
                ):
                    protocol = str(model.protocol or "").strip().lower()
                    if protocol in ("rest_http", "http_json"):
                        resolved_targets = self._resolve_all_source_paths(repo_root, root_dir, sf)
                        if not resolved_targets:
                            report.broken_references.append(
                                f"Broken specification_file in {doc.file_path.name}: "
                                f"Contract '{doc.doc_id}' references '{sf}' which does not exist on disk"
                            )
                        else:
                            target_arg: Path | list[Path] = (
                                resolved_targets[0] if len(resolved_targets) == 1 else resolved_targets
                            )
                            self._verify_contract_protocol(protocol, doc, target_arg, report)
                    else:
                        resolved_sf = self._resolve_source_path(repo_root, root_dir, sf)
                        if resolved_sf is None:
                            report.broken_references.append(
                                f"Broken specification_file in {doc.file_path.name}: "
                                f"Contract '{doc.doc_id}' references '{sf}' which does not exist on disk"
                            )
                        else:
                            self._verify_contract_protocol(protocol, doc, resolved_sf, report)

        if active_arch_docs:
            surface_validator._verify_prose_symbol_drift(active_arch_docs, repo_root, root_dir, report, repo_symbol_acc)

    def _validate_test_bindings(
        self,
        report: ValidationReport,
        repo_root: Path,
        root_dir: Path,
        has_test_tree: bool,
    ) -> None:
        """Enforce concrete file::symbol test_binding rules on status: active specs (CAP-*, POL-*, CNT-*, CMP-*, UIC-*)."""
        sunset_statuses = {
            "proposed",
            "deprecated",
            "retired",
            "discarded",
            "rejected",
        }
        for doc in report.documents:
            if doc.doc_type == "delta/mutation":
                continue
            model = doc.model
            status = str(model.status or "active").strip().lower()
            if status in sunset_statuses:
                continue
            invariants_to_check = model.all_invariants()
            if not invariants_to_check:
                continue

            if status in ("draft", "planned"):
                for inv in invariants_to_check:
                    inv_id = str(inv.id or "unknown")
                    raw_tb = inv.effective_test_binding()
                    if raw_tb and raw_tb.upper() in ("N/A", "NONE"):
                        report.broken_references.append(
                            f"Broken test_binding in {doc.file_path.name}: "
                            f"Invariant '{inv_id}' has invalid test_binding '{raw_tb}' (N/A is not permitted; use TBD while {status})"
                        )
                continue

            for inv in invariants_to_check:
                inv_id = str(inv.id or "unknown")
                raw_tb = inv.effective_test_binding()
                if not raw_tb or raw_tb.upper() in ("N/A", "TBD", "NONE"):
                    report.broken_references.append(
                        f"Broken test_binding in {doc.file_path.name}: "
                        f"Invariant '{inv_id}' on status: active specification '{doc.doc_id}' has invalid test_binding '{raw_tb or '<empty>'}' (must declare 'file::symbol')"
                    )
                    continue
                if "::" not in raw_tb:
                    report.broken_references.append(
                        f"Broken test_binding in {doc.file_path.name}: "
                        f"Invariant '{inv_id}' on status: active specification '{doc.doc_id}' has bare file test_binding '{raw_tb}' (missing '::symbol')"
                    )
                    continue
                parts = [p.strip() for p in raw_tb.split("::") if p.strip()]
                if not parts:
                    report.broken_references.append(
                        f"Broken test_binding in {doc.file_path.name}: "
                        f"Invariant '{inv_id}' has invalid test_binding '{raw_tb}'"
                    )
                    continue
                if any(not p.strip() for p in raw_tb.split("::")):
                    report.broken_references.append(
                        f"Broken test_binding in {doc.file_path.name}: "
                        f"Invariant '{inv_id}' has Malformed test_binding '{raw_tb}' (empty symbol after '::')"
                    )
                    continue
                if has_test_tree:
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

    _VALID_DTCG_TYPES = VALID_DTCG_TYPES
    _CONTRACT_PROTOCOL_VERIFIERS = ContractParityValidator._CONTRACT_PROTOCOL_VERIFIERS
    _PUBLIC_SURFACE_CACHE_MAX_ENTRIES = PublicSurfaceValidator._PUBLIC_SURFACE_CACHE_MAX_ENTRIES
    _PUBLIC_SURFACE_SYMBOL_CACHE = PublicSurfaceValidator._PUBLIC_SURFACE_SYMBOL_CACHE

    def _validate_design_systems(self, report: ValidationReport, root_dir: Path) -> None:
        validate_design_systems(report, root_dir)

    def _verify_cli_contract_commands(self, doc: SpecDocument, cli_file: Path, report: ValidationReport) -> None:
        ContractParityValidator()._verify_cli_contract_commands(doc, cli_file, report)

    @staticmethod
    def _strip_asciidoc_comments(body: str) -> str:
        return ContractParityValidator._strip_asciidoc_comments(body)

    def _verify_python_protocol_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        ContractParityValidator()._verify_python_protocol_contract(doc, spec_file, report)

    def _verify_rest_openapi_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        ContractParityValidator()._verify_rest_openapi_contract(doc, spec_file, report)

    def _verify_json_schema_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        ContractParityValidator()._verify_json_schema_contract(doc, spec_file, report)

    def _verify_grpc_proto_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        ContractParityValidator()._verify_grpc_proto_contract(doc, spec_file, report)

    def _verify_graphql_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        ContractParityValidator()._verify_graphql_contract(doc, spec_file, report)

    def _verify_contract_protocol(
        self, protocol: str, doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport
    ) -> None:
        ContractParityValidator()._verify_contract_protocol(protocol, doc, spec_file, report)

    @classmethod
    def _extract_cached_public_surface_symbols(cls, file_path: Path, *, is_component: bool = False):
        return PublicSurfaceValidator._extract_cached_public_surface_symbols(file_path, is_component=is_component)

    def _verify_entity_public_surface(
        self,
        doc: SpecDocument,
        repo_root: Path,
        root_dir: Path,
        report: ValidationReport,
        *,
        repo_symbol_acc: dict[str, Any] | None = None,
    ) -> None:
        PublicSurfaceValidator()._verify_entity_public_surface(
            doc, repo_root, root_dir, report, repo_symbol_acc=repo_symbol_acc
        )

    def _validate_import_boundary_declarations(self, report: ValidationReport, root_dir: Path) -> None:
        validate_import_boundary_declarations(report, root_dir, self._schema_error_key)


def evaluate_import_boundaries(
    eidos_dir: Path,
    *,
    repo_root: Path | None = None,
    documents: list[SpecDocument] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate ARCH-*.import_boundaries and CNT-*/CMP-*.import_boundary against synced AST/HTTP code_couplings."""
    return _evaluate_import_boundaries(eidos_dir, repo_root=repo_root, documents=documents)
