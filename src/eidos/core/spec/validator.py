"""Validator for Eidos specifications against JSON/YAML schemas."""

import ast
import contextlib
import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, ClassVar

import jsonschema
import orjson
from ast_grep_py import SgRoot

from eidos.core.index.ast_indexer import (
    AST_DIFF_EXTS,
    _extract_python_dunder_all_from_source,
    _is_component_surface_symbol,
    _is_public_symbol,
    get_ast_indexer,
)
from eidos.core.spec.models import (
    ENTITY_ID_PREFIXES,
    TYPE_TO_SCHEMA_NAME,
    SpecDocument,
    ValidationReport,
    infer_entity_type_from_path,
    resolve_entity_prefix_meta,
)
from eidos.core.spec.parser import YamlError, safe_load_yaml
from eidos.core.workspace.assets import get_v1_schemas_dir
from eidos.core.workspace.project import (
    STANDARD_EXCLUDED_DIRS,
    expand_brace_globs,
    is_dotnet_bin_dir,
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


def _parse_python_ast(source: str) -> ast.Module:
    """Parse Python source into an AST, normalizing PEP 695/696 type aliases and type parameters on Python >=3.11."""
    root = SgRoot(source, "python").root()
    edits: list[tuple[int, int, str]] = []
    for ta in root.find_all(kind="type_alias_statement"):
        left = ta.field("left")
        alias_ident = left.text().split("[", 1)[0].strip() if left is not None else ""
        if alias_ident.isidentifier():
            rng = ta.range()
            s_idx, e_idx = rng.start.index, rng.end.index
            nl_pad = "\n" * source[s_idx:e_idx].count("\n")
            edits.append((s_idx, e_idx, f"{alias_ident} = None{nl_pad}"))
    for tp in root.find_all(kind="type_parameter"):
        parent = tp.parent()
        if parent is not None and parent.kind() in ("class_definition", "function_definition"):
            rng = tp.range()
            edits.append((rng.start.index, rng.end.index, ""))
    if edits:
        edits.sort(key=lambda item: item[0], reverse=True)
        parts: list[str] = []
        cursor = len(source)
        for s_idx, e_idx, repl in edits:
            if e_idx <= cursor:
                parts.append(source[e_idx:cursor])
                parts.append(repl)
                cursor = s_idx
        parts.append(source[:cursor])
        source = "".join(reversed(parts))
    return ast.parse(source)


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
                msg = f"Import boundary violation [{v['rule']}]: {v['source']} -> {v['target']} ({limit_str}){ev_sample}"
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
                    report.broken_references.append(
                        f"Duplicate entity ID '{dec_id}' found in {doc.file_path.name} (already defined in {prev_name})"
                    )
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

    def _resolve_source_path(self, repo_root: Path, root_dir: Path, raw_path: str) -> Path | None:
        """Resolve a non-.adoc source path or glob pattern (including {a,b} brace globs) against repo_root or root_dir."""
        p = raw_path.strip()
        if not p or p.upper() in ("N/A", "TBD", "NONE") or p.endswith(".adoc"):
            return None
        for expanded in expand_brace_globs(p):
            if any(ch in expanded for ch in ("*", "?", "[")):
                matches = sorted(repo_root.glob(expanded)) or sorted(root_dir.glob(expanded))
                if matches:
                    return matches[0]
                continue
            for base in (repo_root, root_dir):
                cand = base / expanded
                if cand.exists():
                    return cand
        return None

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
                    self._verify_entity_public_surface(doc, repo_root, root_dir, report)
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
                    self._verify_entity_public_surface(doc, repo_root, root_dir, report)
            elif doc.doc_type == "tech/contract":
                sf = model.specification_file
                if (
                    isinstance(sf, str)
                    and sf.strip()
                    and sf.strip().upper() not in ("N/A", "TBD", "NONE")
                    and not sf.strip().endswith(".adoc")
                ):
                    resolved_sf = self._resolve_source_path(repo_root, root_dir, sf)
                    if resolved_sf is None:
                        report.broken_references.append(
                            f"Broken specification_file in {doc.file_path.name}: "
                            f"Contract '{doc.doc_id}' references '{sf}' which does not exist on disk"
                        )
                    else:
                        protocol = str(model.protocol or "").strip().lower()
                        self._verify_contract_protocol(protocol, doc, resolved_sf, report)

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
        """Validate design/system W3C DTCG tokens.manifest files and acyclic extends chains."""
        spec_root, repo_root = resolve_spec_and_repo_roots(root_dir)
        inactive_statuses = {
            "planned",
            "proposed",
            "draft",
            "deprecated",
            "retired",
            "discarded",
            "rejected",
        }

        ds_docs_by_path: dict[Path, str] = {
            d.file_path.resolve(): d.doc_id
            for d in report.documents
            if d.doc_type == "design/system" and isinstance(d.doc_id, str) and d.doc_id
        }

        extends_map: dict[str, tuple[str, SpecDocument]] = {}
        for doc in report.documents:
            if doc.doc_type != "design/system":
                continue
            fm = doc.frontmatter or {}
            parent_ref = str(fm.get("extends") or "").strip()

            if parent_ref and isinstance(doc.doc_id, str) and doc.doc_id:
                parent_id = parent_ref
                if parent_ref.endswith(".adoc"):
                    for base in (doc.file_path.parent, spec_root, root_dir, repo_root):
                        cand = (base / parent_ref).resolve()
                        if cand in ds_docs_by_path:
                            parent_id = ds_docs_by_path[cand]
                            break
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

            resolved_repo = repo_root.resolve()
            raw_norm = manifest_rel.replace("\\", "/")
            if Path(raw_norm).is_absolute() or ".." in Path(raw_norm).parts:
                report.broken_references.append(
                    f"Broken tokens.manifest in {doc.file_path.name}: "
                    f"Design System '{doc.doc_id}' references '{manifest_rel}' outside repository root"
                )
                continue

            manifest_path: Path | None = None
            escaped_repo = False
            for base in (doc.file_path.parent, spec_root, root_dir, repo_root):
                cand = base / manifest_rel
                if cand.exists() or cand.is_symlink():
                    try:
                        resolved_cand = cand.resolve()
                    except OSError:
                        continue
                    if not resolved_cand.is_relative_to(resolved_repo):
                        escaped_repo = True
                        break
                    if resolved_cand.is_file():
                        manifest_path = resolved_cand
                        break

            if escaped_repo:
                report.broken_references.append(
                    f"Broken tokens.manifest in {doc.file_path.name}: "
                    f"Design System '{doc.doc_id}' references '{manifest_rel}' which resolves outside repository root"
                )
                continue

            if manifest_path is None:
                report.broken_references.append(
                    f"Broken tokens.manifest in {doc.file_path.name}: "
                    f"Design System '{doc.doc_id}' references '{manifest_rel}' which does not exist on disk"
                )
                continue

            if spec_fmt == "w3c_design_tokens":
                try:
                    token_data = orjson.loads(manifest_path.read_text(encoding="utf-8"))
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

                def _walk_tokens(
                    node: dict[str, Any],
                    path_prefix: str,
                    doc_name: str = doc.file_path.name,
                    m_rel: str = manifest_rel,
                ) -> None:
                    nonlocal token_count
                    if "$value" in node:
                        token_count += 1
                        tok_type = node.get("$type")
                        if tok_type is not None and (
                            not isinstance(tok_type, str) or tok_type not in self._VALID_DTCG_TYPES
                        ):
                            report.broken_references.append(
                                f"Invalid W3C DTCG token $type in {doc_name} ({m_rel}): "
                                f"token '{path_prefix}' has unsupported $type '{tok_type}'"
                            )
                        return

                    child_dicts = {k: v for k, v in node.items() if not str(k).startswith("$") and isinstance(v, dict)}
                    non_meta_scalars = [
                        k for k, v in node.items() if not str(k).startswith("$") and not isinstance(v, dict)
                    ]
                    if path_prefix and (non_meta_scalars or not child_dicts):
                        report.broken_references.append(
                            f"Invalid W3C DTCG token in {doc_name} ({m_rel}): "
                            f"token '{path_prefix}' is missing required '$value' property"
                        )
                        if not child_dicts:
                            return
                    elif not path_prefix and non_meta_scalars:
                        for bad_key in non_meta_scalars:
                            report.broken_references.append(
                                f"Invalid W3C DTCG token in {doc_name} ({m_rel}): "
                                f"token '{bad_key}' is missing required '$value' property"
                            )

                    for k, v in child_dicts.items():
                        sub = f"{path_prefix}.{k}" if path_prefix else str(k)
                        _walk_tokens(v, sub, doc_name, m_rel)

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
                    cycle_start = chain.index(curr)
                    cycle_key = frozenset(chain[cycle_start:])
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

    def _verify_cli_contract_commands(self, doc: SpecDocument, cli_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional CLI command parity across Typer (including sub-apps), Click, and argparse entrypoints (DLT-2026-082)."""
        try:
            source = cli_file.read_text(encoding="utf-8")
            tree = _parse_python_ast(source)
        except (OSError, SyntaxError, ValueError):
            return

        source_lower = source.lower()
        has_typer = "typer" in source_lower
        has_click = "click" in source_lower
        has_argparse = "argparse" in source_lower
        if not (has_typer or has_click or has_argparse):
            return

        body = doc.body or ""
        _, cli_repo_root = resolve_spec_and_repo_roots(None, cli_file)

        typer_module_aliases: set[str] = set()
        typer_class_aliases: set[str] = set()
        click_module_aliases: set[str] = set()
        click_group_decorators: set[str] = set()
        click_cmd_decorators: set[str] = set()

        def _register_typer_aliases(mod_ast: ast.AST) -> None:
            for n_imp in ast.walk(mod_ast):
                if isinstance(n_imp, ast.Import):
                    for al in n_imp.names:
                        if al.name == "typer" or al.name.startswith("typer."):
                            typer_module_aliases.add(al.asname or al.name.split(".")[0])
                elif (
                    isinstance(n_imp, ast.ImportFrom)
                    and n_imp.module
                    and (n_imp.module == "typer" or n_imp.module.startswith("typer."))
                ):
                    for al in n_imp.names:
                        if al.name == "Typer":
                            typer_class_aliases.add(al.asname or al.name)
                        elif al.name == "typer":
                            typer_module_aliases.add(al.asname or al.name)

        _register_typer_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "click" or alias.name.startswith("click."):
                        click_module_aliases.add(alias.asname or alias.name.split(".")[0])
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "click" or node.module.startswith("click."))
            ):
                for alias in node.names:
                    if alias.name == "group":
                        click_group_decorators.add(alias.asname or alias.name)
                    elif alias.name == "command":
                        click_cmd_decorators.add(alias.asname or alias.name)

        def _is_typer_ctor(call_node: ast.AST | None) -> bool:
            if not isinstance(call_node, ast.Call):
                return False
            func = call_node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                return func.value.id in typer_module_aliases and func.attr == "Typer"
            if isinstance(func, ast.Name):
                return func.id in typer_class_aliases
            return False

        def _extract_typer_ctor_name(call_node: ast.AST | None) -> str | None:
            if not isinstance(call_node, ast.Call):
                return None
            for kw in call_node.keywords:
                if (
                    kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and kw.value.value.strip()
                ):
                    return kw.value.value.strip()
            if (
                call_node.args
                and isinstance(call_node.args[0], ast.Constant)
                and isinstance(call_node.args[0].value, str)
                and call_node.args[0].value.strip()
            ):
                return call_node.args[0].value.strip()
            return None

        def _collect_typer_vars(scope_node: ast.AST) -> tuple[set[str], dict[str, str]]:
            vars_found: set[str] = set()
            ctor_names: dict[str, str] = {}
            for n in ast.walk(scope_node):
                if isinstance(n, ast.Assign) and _is_typer_ctor(n.value):
                    c_name = _extract_typer_ctor_name(n.value)
                    for target in n.targets:
                        if isinstance(target, ast.Name):
                            vars_found.add(target.id)
                            if c_name:
                                ctor_names[target.id] = c_name
                elif isinstance(n, ast.AnnAssign) and _is_typer_ctor(n.value) and isinstance(n.target, ast.Name):
                    vars_found.add(n.target.id)
                    c_name = _extract_typer_ctor_name(n.value)
                    if c_name:
                        ctor_names[n.target.id] = c_name
            return vars_found, ctor_names

        typer_app_vars, root_ctor_names = (
            _collect_typer_vars(tree) if (typer_module_aliases or typer_class_aliases) else (set(), {})
        )

        # Preserve DLT-2026-049 compatibility: for non-Typer-app files (Click/argparse), only enforce CLI command
        # parity when the contract body declares command sections (===), tables (|===), or backtick command literals.
        if not typer_app_vars and not any(tok in body for tok in ("===", "|===", "`")):
            return

        def _extract_command_name_from_call(cmd_call: ast.Call, fallback_func_name: str | None) -> str | None:
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
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
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

        commands: set[str] = set()
        subcommands_by_group: dict[str, set[str]] = {}
        nested_subcommands_by_group: dict[tuple[str, str], set[str]] = {}
        cmd_pos_args: dict[str, list[tuple[str, str | None, bool]]] = {}
        sub_pos_args: dict[tuple[str, ...], list[tuple[str, str | None, bool]]] = {}

        def _scan_module_imports(
            mod_ast: ast.AST,
        ) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
            mod_aliases: dict[str, str] = {}
            sym_imports: dict[str, tuple[str, str]] = {}
            for n_i in ast.walk(mod_ast):
                if isinstance(n_i, ast.ImportFrom):
                    full_mod = (n_i.module or "").strip(".")
                    for al in n_i.names:
                        local_name = al.asname or al.name
                        cand_submod = f"{full_mod}.{al.name}".strip(".") if full_mod else al.name
                        mod_aliases[local_name] = cand_submod
                        if full_mod:
                            sym_imports[local_name] = (full_mod, al.name)
                elif isinstance(n_i, ast.Import):
                    for al in n_i.names:
                        local_name = al.asname or al.name.rsplit(".", 1)[-1]
                        mod_aliases[local_name] = al.name
            return mod_aliases, sym_imports

        sibling_module_aliases, imported_symbol_modules = _scan_module_imports(tree)
        sibling_trees: dict[str, ast.Module] = {}

        def _get_sibling_tree(mod_ref: str) -> ast.Module | None:
            if not mod_ref:
                return None
            if mod_ref in sibling_trees:
                return sibling_trees[mod_ref]
            mod_clean = mod_ref.strip(".")
            mod_parts = [p for p in mod_clean.split(".") if p]
            if not mod_parts:
                return None
            mod_leaf = mod_parts[-1]
            candidates: list[Path] = [
                cli_file.parent / f"{mod_leaf}.py",
                cli_file.parent / mod_leaf / "__init__.py",
            ]
            for i in range(len(mod_parts)):
                rel_sub = Path(*mod_parts[i:])
                for search_base in (cli_file.parent, cli_repo_root / "src", cli_repo_root):
                    candidates.append(search_base / rel_sub.with_suffix(".py"))
                    candidates.append(search_base / rel_sub / "__init__.py")
            seen_cands: set[Path] = set()
            for cand_py in candidates:
                if cand_py in seen_cands:
                    continue
                seen_cands.add(cand_py)
                if cand_py.is_file() and cand_py.resolve() != cli_file.resolve():
                    with contextlib.suppress(OSError, SyntaxError, ValueError):
                        parsed = _parse_python_ast(cand_py.read_text(encoding="utf-8"))
                        _register_typer_aliases(parsed)
                        sibling_trees[mod_ref] = parsed
                        sibling_trees[mod_leaf] = parsed
                        return parsed
            return None

        if typer_app_vars:

            def _resolve_sub_typer_name(
                call_node: ast.Call,
                sub_var: str | None,
                ctor_names_map: dict[str, str],
                imp_map: dict[str, tuple[str, str]],
                attr_target: tuple[str, str] | None = None,
            ) -> str | None:
                sub_name = next(
                    (
                        kw.value.value
                        for kw in call_node.keywords
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                    ),
                    None,
                )
                if (
                    not sub_name
                    and len(call_node.args) >= 2
                    and isinstance(call_node.args[1], ast.Constant)
                    and isinstance(call_node.args[1].value, str)
                ):
                    sub_name = call_node.args[1].value
                if not sub_name and attr_target is not None:
                    sib_mod_ref, orig_attr = attr_target
                    sib_ast = _get_sibling_tree(sib_mod_ref)
                    if sib_ast is not None:
                        _, sib_ctors = _collect_typer_vars(sib_ast)
                        sub_name = sib_ctors.get(orig_attr)
                if not sub_name and sub_var:
                    sub_name = ctor_names_map.get(sub_var)
                    if not sub_name and sub_var in imp_map:
                        sib_mod, orig_var = imp_map[sub_var]
                        sib_ast = _get_sibling_tree(sib_mod)
                        if sib_ast is not None:
                            _, sib_ctors = _collect_typer_vars(sib_ast)
                            sub_name = sib_ctors.get(orig_var)
                return sub_name

            mounted_sub_apps: dict[str, str | None] = {}  # var_name -> group_name (or None if unnamed)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in typer_app_vars
                    and node.func.attr == "add_typer"
                    and node.args
                ):
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Name):
                        sub_var = arg0.id
                        sub_name = _resolve_sub_typer_name(node, sub_var, root_ctor_names, imported_symbol_modules)
                        mounted_sub_apps[sub_var] = sub_name
                    elif isinstance(arg0, ast.Attribute) and isinstance(arg0.value, ast.Name):
                        mod_ref = sibling_module_aliases.get(arg0.value.id, arg0.value.id)
                        sub_name = _resolve_sub_typer_name(
                            node, None, root_ctor_names, imported_symbol_modules, attr_target=(mod_ref, arg0.attr)
                        )
                        mounted_sub_apps[f"{arg0.value.id}.{arg0.attr}"] = sub_name

            root_app_vars = typer_app_vars - set(mounted_sub_apps) or (
                {"app"} if "app" in typer_app_vars else typer_app_vars
            )

            def _find_typer_param_call(node: ast.AST | None, attr_name: str = "Argument") -> ast.Call | None:
                if node is None:
                    return None
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        func_node = child.func
                        if (isinstance(func_node, ast.Attribute) and func_node.attr == attr_name) or (
                            isinstance(func_node, ast.Name) and func_node.id == attr_name
                        ):
                            return child
                return None

            def _is_typer_context_annotation(node: ast.AST | None) -> bool:
                if node is None:
                    return False
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and child.id == "Context":
                        return True
                    if isinstance(child, ast.Attribute) and child.attr == "Context":
                        return True
                return False

            def _extract_typer_positional_args(
                fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
            ) -> list[tuple[str, str | None, bool]]:
                pos_out: list[tuple[str, str | None, bool]] = []
                args_list = list(fn_node.args.args)
                defaults_list = list(fn_node.args.defaults)
                default_offset = len(args_list) - len(defaults_list)
                for idx_a, arg_node in enumerate(args_list):
                    if arg_node.arg in ("self", "cls") or _is_typer_context_annotation(arg_node.annotation):
                        continue
                    def_idx = idx_a - default_offset
                    def_val = defaults_list[def_idx] if def_idx >= 0 else None
                    arg_call = _find_typer_param_call(def_val, "Argument") if isinstance(def_val, ast.Call) else None
                    from_annotation = False
                    if arg_call is None:
                        arg_call = _find_typer_param_call(arg_node.annotation, "Argument")
                        from_annotation = arg_call is not None
                    if arg_call is None:
                        opt_call = (
                            _find_typer_param_call(def_val, "Option") if isinstance(def_val, ast.Call) else None
                        ) or _find_typer_param_call(arg_node.annotation, "Option")
                        if opt_call is None and def_idx < 0:
                            pos_out.append((arg_node.arg, None, True))
                        continue
                    call_has_default = bool(
                        (
                            arg_call.args
                            and not (isinstance(arg_call.args[0], ast.Constant) and arg_call.args[0].value is Ellipsis)
                        )
                        or any(
                            kw.arg == "default"
                            and not (isinstance(kw.value, ast.Constant) and kw.value.value is Ellipsis)
                            for kw in arg_call.keywords
                        )
                    )
                    if from_annotation:
                        param_has_default = def_val is not None and not (
                            isinstance(def_val, ast.Constant) and def_val.value is Ellipsis
                        )
                        is_req = not (param_has_default or call_has_default)
                    else:
                        is_req = not call_has_default
                    metavar_val = next(
                        (
                            kw.value.value
                            for kw in arg_call.keywords
                            if kw.arg == "metavar"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                        ),
                        None,
                    )
                    pos_out.append((arg_node.arg, metavar_val, is_req))
                return pos_out

            def _collect_commands_from_ast(
                scope_node: ast.AST,
                target_vars: set[str],
                full_module_node: ast.AST | None = None,
                local_imports: dict[str, tuple[str, str]] | None = None,
                local_mod_aliases: dict[str, str] | None = None,
                _visited: set[tuple[int, str]] | None = None,
            ) -> tuple[
                set[str],
                dict[str, set[str]],
                dict[str, list[tuple[str, str | None, bool]]],
                dict[tuple[str, ...], list[tuple[str, str | None, bool]]],
                dict[tuple[str, str], set[str]],
            ]:
                found: set[str] = set()
                sub_found: dict[str, set[str]] = {}
                found_pos: dict[str, list[tuple[str, str | None, bool]]] = {}
                sub_found_pos: dict[tuple[str, ...], list[tuple[str, str | None, bool]]] = {}
                nested_sub_found: dict[tuple[str, str], set[str]] = {}
                search_root = full_module_node or scope_node
                imp_map = local_imports if local_imports is not None else imported_symbol_modules
                mod_alias_map = local_mod_aliases if local_mod_aliases is not None else sibling_module_aliases
                visited = set(_visited) if _visited is not None else set()
                for tv in target_vars:
                    visited.add((id(search_root), tv))
                _, scope_ctor_names = _collect_typer_vars(search_root)
                fn_defs_in_scope = {
                    n.name: n for n in ast.walk(search_root) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                for node in ast.walk(scope_node):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in target_vars
                        and node.func.attr == "add_typer"
                    ):
                        arg0 = node.args[0] if node.args else None
                        child_var = arg0.id if isinstance(arg0, ast.Name) else None
                        attr_target: tuple[str, str] | None = None
                        if isinstance(arg0, ast.Attribute) and isinstance(arg0.value, ast.Name):
                            attr_mod_ref = mod_alias_map.get(arg0.value.id, arg0.value.id)
                            attr_target = (attr_mod_ref, arg0.attr)
                        sub_name = _resolve_sub_typer_name(
                            node, child_var, scope_ctor_names, imp_map, attr_target=attr_target
                        )
                        child_cmds: set[str] = set()
                        grandchild_map: dict[str, set[str]] = {}
                        child_pos: dict[str, list[tuple[str, str | None, bool]]] = {}
                        grandchild_pos: dict[tuple[str, ...], list[tuple[str, str | None, bool]]] = {}
                        if child_var:
                            if (id(search_root), child_var) not in visited:
                                c_cmds, gc_map, c_pos, gc_pos, _ = _collect_commands_from_ast(
                                    search_root,
                                    {child_var},
                                    search_root,
                                    imp_map,
                                    mod_alias_map,
                                    _visited=visited,
                                )
                                child_cmds.update(c_cmds)
                                child_pos.update(c_pos)
                                grandchild_pos.update(gc_pos)
                                for gk, gv in gc_map.items():
                                    grandchild_map.setdefault(gk, set()).update(gv)
                            if child_var in imp_map:
                                sib_mod, orig_var = imp_map[child_var]
                                sib_ast = _get_sibling_tree(sib_mod)
                                if sib_ast is not None and (id(sib_ast), orig_var) not in visited:
                                    sib_mods, sib_imp = _scan_module_imports(sib_ast)
                                    ext_cmds, ext_gc, ext_pos, ext_gc_pos, _ = _collect_commands_from_ast(
                                        sib_ast,
                                        {orig_var},
                                        sib_ast,
                                        sib_imp,
                                        sib_mods,
                                        _visited=visited,
                                    )
                                    child_cmds.update(ext_cmds)
                                    child_pos.update(ext_pos)
                                    grandchild_pos.update(ext_gc_pos)
                                    for gk, gv in ext_gc.items():
                                        grandchild_map.setdefault(gk, set()).update(gv)
                        elif attr_target is not None:
                            sib_mod, orig_var = attr_target
                            sib_ast = _get_sibling_tree(sib_mod)
                            if sib_ast is not None and (id(sib_ast), orig_var) not in visited:
                                sib_mods, sib_imp = _scan_module_imports(sib_ast)
                                ext_cmds, ext_gc, ext_pos, ext_gc_pos, _ = _collect_commands_from_ast(
                                    sib_ast,
                                    {orig_var},
                                    sib_ast,
                                    sib_imp,
                                    sib_mods,
                                    _visited=visited,
                                )
                                child_cmds.update(ext_cmds)
                                child_pos.update(ext_pos)
                                grandchild_pos.update(ext_gc_pos)
                                for gk, gv in ext_gc.items():
                                    grandchild_map.setdefault(gk, set()).update(gv)
                        if sub_name:
                            found.add(sub_name)
                            grp_set = sub_found.setdefault(sub_name, set())
                            grp_set.update(child_cmds)
                            for c_name, p_list in child_pos.items():
                                sub_found_pos[(sub_name, c_name)] = p_list
                            for gc_grp, gc_set in grandchild_map.items():
                                grp_set.update(gc_set)
                                nested_sub_found.setdefault((sub_name, gc_grp), set()).update(gc_set)
                            for gc_key, p_list in grandchild_pos.items():
                                sub_found_pos[(sub_name, *gc_key)] = p_list
                                if (sub_name, gc_key[-1]) not in sub_found_pos:
                                    sub_found_pos[(sub_name, gc_key[-1])] = p_list
                        else:
                            # Unnamed add_typer(sub_app) mounts sub_app's commands at the parent level
                            found.update(child_cmds)
                            found_pos.update(child_pos)
                            for gk, gv in grandchild_map.items():
                                sub_found.setdefault(gk, set()).update(gv)
                            sub_found_pos.update(grandchild_pos)
                    elif (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Call)
                        and isinstance(node.func.func, ast.Attribute)
                        and isinstance(node.func.func.value, ast.Name)
                        and node.func.func.value.id in target_vars
                        and node.func.func.attr == "command"
                    ):
                        fn_arg_name = node.args[0].id if node.args and isinstance(node.args[0], ast.Name) else None
                        cmd_name = _extract_command_name_from_call(node.func, fn_arg_name)
                        if cmd_name:
                            found.add(cmd_name)
                            if fn_arg_name and fn_arg_name in fn_defs_in_scope:
                                found_pos[cmd_name] = _extract_typer_positional_args(fn_defs_in_scope[fn_arg_name])
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
                                    found_pos[cmd_name] = _extract_typer_positional_args(node)
                            elif (
                                isinstance(dec, ast.Attribute)
                                and isinstance(dec.value, ast.Name)
                                and dec.value.id in target_vars
                                and dec.attr == "command"
                            ):
                                c_name = node.name.replace("_", "-")
                                found.add(c_name)
                                found_pos[c_name] = _extract_typer_positional_args(node)
                return found, sub_found, found_pos, sub_found_pos, nested_sub_found

            (
                commands,
                subcommands_by_group,
                cmd_pos_args,
                sub_pos_args,
                nested_subcommands_by_group,
            ) = _collect_commands_from_ast(tree, root_app_vars, tree)

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
                    sib_tree = _get_sibling_tree(mod_name)
                    if sib_tree is not None:
                        sib_mods_map, sib_imp_map = _scan_module_imports(sib_tree)
                        for sib_node in sib_tree.body:
                            if (
                                isinstance(sib_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and sib_node.name == "register"
                                and sib_node.args.args
                            ):
                                app_param = sib_node.args.args[0].arg
                                sib_cmds, sib_subs, sib_c_pos, sib_s_pos, sib_nested = _collect_commands_from_ast(
                                    sib_node, {app_param}, sib_tree, sib_imp_map, sib_mods_map
                                )
                                commands.update(sib_cmds)
                                cmd_pos_args.update(sib_c_pos)
                                sub_pos_args.update(sib_s_pos)
                                for g_name, g_cmds in sib_subs.items():
                                    subcommands_by_group.setdefault(g_name, set()).update(g_cmds)
                                for nk, nv in sib_nested.items():
                                    nested_subcommands_by_group.setdefault(nk, set()).update(nv)
                                # Also check if sibling register() mounts subcommands onto an imported sub-typer (e.g. spec_app.add_typer(component_app, ...))
                                for sub_call in ast.walk(sib_node):
                                    if (
                                        isinstance(sub_call, ast.Call)
                                        and isinstance(sub_call.func, ast.Attribute)
                                        and isinstance(sub_call.func.value, ast.Name)
                                        and sub_call.func.attr == "add_typer"
                                    ):
                                        parent_var = sub_call.func.value.id
                                        parent_group = mounted_sub_apps.get(parent_var)
                                        if parent_group:
                                            extra_c, extra_sub, extra_c_pos, extra_s_pos, _ = (
                                                _collect_commands_from_ast(
                                                    sib_node, {parent_var}, sib_tree, sib_imp_map, sib_mods_map
                                                )
                                            )
                                            grp_s = subcommands_by_group.setdefault(parent_group, set())
                                            grp_s.update(extra_c)
                                            for ec_name, ec_pos in extra_c_pos.items():
                                                sub_pos_args[(parent_group, ec_name)] = ec_pos
                                            for es_key, es_pos in extra_s_pos.items():
                                                sub_pos_args[(parent_group, *es_key)] = es_pos
                                                if (parent_group, es_key[-1]) not in sub_pos_args:
                                                    sub_pos_args[(parent_group, es_key[-1])] = es_pos
                                            for gc_grp, gc_s in extra_sub.items():
                                                grp_s.update(gc_s)
                                                nested_subcommands_by_group.setdefault(
                                                    (parent_group, gc_grp), set()
                                                ).update(gc_s)

        # Click CLI extraction (@click.group / @click.command / @<group>.group / @<group>.command / <group>.add_command)
        if not commands and (click_module_aliases or click_group_decorators or click_cmd_decorators):
            root_click_groups: set[str] = set()
            sub_click_groups: dict[str, str] = {}  # func_name -> group_command_name
            standalone_click_funcs: dict[str, str] = {}  # func_name -> default_command_name

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        dec_call = dec if isinstance(dec, ast.Call) else None
                        dec_target = dec_call.func if dec_call else dec
                        is_root_grp = (
                            isinstance(dec_target, ast.Attribute)
                            and isinstance(dec_target.value, ast.Name)
                            and dec_target.value.id in click_module_aliases
                            and dec_target.attr == "group"
                        ) or (isinstance(dec_target, ast.Name) and dec_target.id in click_group_decorators)
                        if is_root_grp:
                            root_click_groups.add(node.name)
                            grp_cmd = (
                                _extract_command_name_from_call(dec_call, node.name)
                                if dec_call
                                else node.name.replace("_", "-")
                            )
                            if grp_cmd:
                                standalone_click_funcs[node.name] = grp_cmd

            # Iteratively discover nested @<group>.group() definitions and <group>.add_command(<sub_group>) mounts
            changed = True
            while changed:
                changed = False
                known_groups = root_click_groups | set(sub_click_groups)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in known_groups:
                        for dec in node.decorator_list:
                            dec_call = dec if isinstance(dec, ast.Call) else None
                            dec_target = dec_call.func if dec_call else dec
                            if (
                                isinstance(dec_target, ast.Attribute)
                                and isinstance(dec_target.value, ast.Name)
                                and dec_target.value.id in known_groups
                                and dec_target.attr == "group"
                            ):
                                grp_cmd = (
                                    _extract_command_name_from_call(dec_call, node.name)
                                    if dec_call
                                    else node.name.replace("_", "-")
                                )
                                if grp_cmd:
                                    sub_click_groups[node.name] = grp_cmd
                                    parent_grp = dec_target.value.id
                                    if parent_grp in sub_click_groups:
                                        subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(
                                            grp_cmd
                                        )
                                    else:
                                        commands.add(grp_cmd)
                                    changed = True
                    elif (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in known_groups
                        and node.func.attr == "add_command"
                        and node.args
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id in root_click_groups
                    ):
                        parent_grp = node.func.value.id
                        target_var = node.args[0].id
                        explicit_name = next(
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
                            not explicit_name
                            and len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and isinstance(node.args[1].value, str)
                        ):
                            explicit_name = node.args[1].value
                        grp_cmd = (
                            explicit_name or standalone_click_funcs.get(target_var) or target_var.replace("_", "-")
                        )
                        root_click_groups.discard(target_var)
                        sub_click_groups[target_var] = grp_cmd
                        if parent_grp in sub_click_groups:
                            subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(grp_cmd)
                        else:
                            commands.add(grp_cmd)
                        changed = True

            all_click_groups = root_click_groups | set(sub_click_groups)

            # Collect @click.command / @<group>.command functions
            decorated_Group_cmds: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        dec_call = dec if isinstance(dec, ast.Call) else None
                        dec_target = dec_call.func if dec_call else dec
                        is_Group_cmd = (
                            isinstance(dec_target, ast.Attribute)
                            and isinstance(dec_target.value, ast.Name)
                            and dec_target.value.id in all_click_groups
                            and dec_target.attr == "command"
                        )
                        is_standalone_cmd = (
                            isinstance(dec_target, ast.Attribute)
                            and isinstance(dec_target.value, ast.Name)
                            and dec_target.value.id in click_module_aliases
                            and dec_target.attr == "command"
                        ) or (isinstance(dec_target, ast.Name) and dec_target.id in click_cmd_decorators)
                        if is_Group_cmd or (is_standalone_cmd and node.name not in all_click_groups):
                            cmd_name = (
                                _extract_command_name_from_call(dec_call, node.name)
                                if dec_call
                                else node.name.replace("_", "-")
                            )
                            if cmd_name:
                                standalone_click_funcs[node.name] = cmd_name
                                if is_Group_cmd:
                                    decorated_Group_cmds.add(node.name)
                                    parent_grp = dec_target.value.id  # type: ignore[union-attr]
                                    if parent_grp in sub_click_groups:
                                        subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(
                                            cmd_name
                                        )
                                    else:
                                        commands.add(cmd_name)

            # Inspect <group>.add_command(cmd_obj, name=...) calls
            mounted_via_add_cmd: set[str] = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in all_click_groups
                    and node.func.attr == "add_command"
                    and node.args
                ):
                    parent_grp = node.func.value.id
                    explicit_name = next(
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
                        not explicit_name
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)
                    ):
                        explicit_name = node.args[1].value
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Name):
                        target_var = first_arg.id
                        mounted_via_add_cmd.add(target_var)
                        resolved_name = (
                            explicit_name or standalone_click_funcs.get(target_var) or target_var.replace("_", "-")
                        )
                        if resolved_name:
                            if target_var in root_click_groups:
                                root_click_groups.discard(target_var)
                                sub_click_groups[target_var] = resolved_name
                            if parent_grp in sub_click_groups:
                                subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(resolved_name)
                            else:
                                commands.add(resolved_name)
                    elif explicit_name:
                        if parent_grp in sub_click_groups:
                            subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(explicit_name)
                        else:
                            commands.add(explicit_name)

            # Include standalone @click.command functions when not mounted via add_command
            for fn_name, cmd_name in standalone_click_funcs.items():
                if (
                    fn_name not in all_click_groups
                    and fn_name not in decorated_Group_cmds
                    and fn_name not in mounted_via_add_cmd
                ):
                    commands.add(cmd_name)

        # argparse CLI extraction (subparsers.add_parser("cmd"))
        if not commands and has_argparse:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_parser"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    commands.add(node.args[0].value)

        if not commands:
            return

        clean_body = self._strip_asciidoc_comments(body)

        # 1. Forward verification (Code -> Spec): every discovered top-level CLI command must be documented
        for cmd_name in sorted(commands):
            if not re.search(rf"(?:`|\b){re.escape(cmd_name)}\b", clean_body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: CLI command '{cmd_name}' defined in '{cli_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        def _parse_doc_positional_placeholders(sig_expr: str, cmd_token: str) -> list[tuple[str, bool]]:
            """Extract (placeholder_name, is_required) after cmd_token, stripping [--opt ...] and --opt <val>."""
            cleaned = re.sub(r"\[-[^\]]*\]", " ", sig_expr)
            toks = cleaned.strip().split()
            if cmd_token in toks:
                toks = toks[toks.index(cmd_token) + 1 :]
            out: list[tuple[str, bool]] = []
            idx_t = 0
            while idx_t < len(toks):
                t = toks[idx_t]
                if t.startswith("-"):
                    if (
                        idx_t + 1 < len(toks)
                        and not toks[idx_t + 1].startswith("-")
                        and (toks[idx_t + 1].startswith("<") or toks[idx_t + 1].isupper())
                    ):
                        idx_t += 2
                        continue
                    idx_t += 1
                    continue
                t_clean = t.rstrip(".")
                if t_clean.startswith("<") and t_clean.endswith(">") and len(t_clean) > 2:
                    inner = t_clean[1:-1].rstrip(".").strip()
                    if inner:
                        out.append((inner, True))
                elif (
                    t_clean.startswith("[")
                    and t_clean.endswith("]")
                    and len(t_clean) > 2
                    and not t_clean[1:-1].startswith("-")
                ):
                    inner = t_clean[1:-1].rstrip(".").strip()
                    if inner:
                        out.append((inner, False))
                idx_t += 1
            return out

        def _verify_positional_parity(
            cmd_label: str,
            doc_pos: list[tuple[str, bool]],
            code_pos: list[tuple[str, str | None, bool]],
        ) -> None:
            if not doc_pos and not code_pos:
                return
            doc_req_cnt = sum(1 for _, r in doc_pos if r)
            code_req_cnt = sum(1 for _, _, r in code_pos if r)
            if len(doc_pos) != len(code_pos) or doc_req_cnt != code_req_cnt:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: CLI command '{cmd_label}' in contract '{doc.doc_id}' documents "
                    f"{len(doc_pos)} positional argument(s) ({doc_req_cnt} required) but '{cli_file.name}' defines {len(code_pos)} ({code_req_cnt} required)"
                )
                return
            for (doc_ph, _), (param_name, metavar, _) in zip(doc_pos, code_pos, strict=False):
                doc_norm = doc_ph.lower().replace("-", "_").strip()
                param_norm = param_name.lower().replace("-", "_").strip()
                meta_norm = (metavar or "").lower().replace("-", "_").strip()
                doc_parts = {p for p in doc_norm.split("_") if p and p not in ("or", "and")}
                param_parts = {p for p in param_norm.split("_") if p and p not in ("or", "and")}
                meta_parts = {p for p in meta_norm.split("_") if p and p not in ("or", "and")}
                aligned = (
                    doc_norm in (param_norm, meta_norm)
                    or bool(doc_parts & (param_parts | meta_parts))
                    or (param_norm == "target" and doc_norm in ("id_or_path", "target"))
                )
                if not aligned:
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: CLI command '{cmd_label}' positional placeholder '{doc_ph}' "
                        f"in contract '{doc.doc_id}' does not align with parameter '{param_name}' in '{cli_file.name}'"
                    )

        # 2. Reverse verification (Spec -> Code) & Subcommand verification (DLT-2026-082, DLT-2026-114)
        heading_matches = list(re.finditer(r"^===\s+`([^`]+)`(?:\s*/\s*`([^`]+)`)?", clean_body, re.MULTILINE))
        for idx, hm in enumerate(heading_matches):
            sec_start = hm.end()
            sec_end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(clean_body)
            next_h2 = re.search(r"^==\s+", clean_body[sec_start:sec_end], re.MULTILINE)
            if next_h2:
                sec_end = sec_start + next_h2.start()
            sec_body = clean_body[sec_start:sec_end]

            candidates = [hm.group(1)]
            if hm.group(2):
                candidates.append(hm.group(2))
            resolved_cmd: str | None = None
            matched_cand_expr = candidates[0]
            for cand_expr in candidates:
                tokens = cand_expr.strip().split()
                if not tokens:
                    continue
                tok = tokens[0]
                if len(tokens) >= 2 and tok not in commands and not tokens[1].startswith(("[", "<", "{")):
                    tok = tokens[1]
                if tok.startswith(("-", "[", "<", "{")) or any(ch in tok for ch in ("*", "|", ".", "/")):
                    continue
                resolved_cmd = tok
                matched_cand_expr = cand_expr
                if tok in commands:
                    break

            if resolved_cmd and resolved_cmd not in commands:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented CLI command '{resolved_cmd}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
                )
            elif resolved_cmd and resolved_cmd in cmd_pos_args and resolved_cmd not in subcommands_by_group:
                doc_pos_top = _parse_doc_positional_placeholders(matched_cand_expr, resolved_cmd)
                _verify_positional_parity(resolved_cmd, doc_pos_top, cmd_pos_args[resolved_cmd])
            elif resolved_cmd and resolved_cmd in subcommands_by_group and "Sub-commands" in sec_body:
                expected_subs = subcommands_by_group[resolved_cmd]
                for sub_cmd in sorted(expected_subs):
                    if not re.search(rf"(?:`|\b){re.escape(sub_cmd)}\b", sec_body):
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: CLI subcommand '{resolved_cmd} {sub_cmd}' is not documented under '{resolved_cmd}' in contract '{doc.doc_id}'"
                        )
                sub_sec_m = re.search(
                    r"\*\s+\*\*Sub-commands\*\*\s*:(.*?)(?=^\*\s+\*\*|^={2,}\s+|\Z)",
                    sec_body,
                    re.MULTILINE | re.DOTALL,
                )
                sub_bullets_text = sub_sec_m.group(1) if sub_sec_m else sec_body
                for bullet_m in re.finditer(
                    r"^\s*-\s+`([^`]+)`",
                    sub_bullets_text,
                    re.MULTILINE,
                ):
                    raw_bullet_sig = bullet_m.group(1).strip()
                    bullet_tokens = [
                        t
                        for t in raw_bullet_sig.split()
                        if not t.startswith(("-", "[", "<", "{")) and not any(ch in t for ch in ("*", "|", ".", "/"))
                    ]
                    if not bullet_tokens:
                        continue
                    if resolved_cmd in bullet_tokens:
                        rc_idx = bullet_tokens.index(resolved_cmd)
                        if rc_idx + 1 < len(bullet_tokens):
                            doc_sub = bullet_tokens[rc_idx + 1]
                        else:
                            continue
                    else:
                        rc_idx = -1
                        doc_sub = bullet_tokens[0]
                    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", doc_sub):
                        continue
                    nested_leaf: str | None = None
                    if (
                        (resolved_cmd, doc_sub) in nested_subcommands_by_group
                        and rc_idx >= 0
                        and rc_idx + 2 < len(bullet_tokens)
                        and re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", bullet_tokens[rc_idx + 2])
                    ):
                        nested_leaf = bullet_tokens[rc_idx + 2]
                    if doc_sub not in expected_subs:
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented CLI subcommand '{resolved_cmd} {doc_sub}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
                        )
                    elif nested_leaf is not None:
                        if nested_leaf not in nested_subcommands_by_group[(resolved_cmd, doc_sub)]:
                            report.broken_references.append(
                                f"Contract drift in {doc.file_path.name}: documented CLI subcommand '{resolved_cmd} {doc_sub} {nested_leaf}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
                            )
                        elif (resolved_cmd, doc_sub, nested_leaf) in sub_pos_args:
                            doc_pos_nested = _parse_doc_positional_placeholders(raw_bullet_sig, nested_leaf)
                            _verify_positional_parity(
                                f"{resolved_cmd} {doc_sub} {nested_leaf}",
                                doc_pos_nested,
                                sub_pos_args[(resolved_cmd, doc_sub, nested_leaf)],
                            )
                    elif (resolved_cmd, doc_sub) in sub_pos_args:
                        doc_pos_sub = _parse_doc_positional_placeholders(raw_bullet_sig, doc_sub)
                        _verify_positional_parity(
                            f"{resolved_cmd} {doc_sub}",
                            doc_pos_sub,
                            sub_pos_args[(resolved_cmd, doc_sub)],
                        )

    @staticmethod
    def _strip_asciidoc_comments(body: str) -> str:
        """Strip AsciiDoc block comments (//// ... ////) and single-line comments (// ...) from contract body."""
        cleaned = re.sub(
            r"^[ \t]*/{4,}[ \t]*\r?\n(?:.*?\r?\n)?[ \t]*/{4,}[ \t]*$",
            "",
            body,
            flags=re.MULTILINE | re.DOTALL,
        )
        return re.sub(r"^[ \t]*//[^\r\n]*$", "", cleaned, flags=re.MULTILINE)

    def _verify_python_protocol_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between Python Protocol / ABC classes/methods in specification_file and the contract (DLT-2026-082)."""
        if spec_file.suffix != ".py":
            return
        try:
            tree = _parse_python_ast(spec_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
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

        protocol_symbols = list(dict.fromkeys(protocol_symbols))
        body = self._strip_asciidoc_comments(doc.body or "")
        for sym in protocol_symbols:
            if not re.search(rf"(?:`|\b){re.escape(sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: Python protocol symbol '{sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        if protocol_symbols:
            proto_set = set(protocol_symbols)
            seen_doc_syms: set[str] = set()
            for m in re.finditer(
                r"`(?:(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^`]*\)|([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(?:self\b|[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_])[^`]*\))`",
                body,
            ):
                doc_sym = m.group(1) or m.group(2)
                if doc_sym and doc_sym not in proto_set and doc_sym not in seen_doc_syms:
                    seen_doc_syms.add(doc_sym)
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: documented Python protocol symbol '{doc_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                    )

    def _verify_rest_openapi_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between OpenAPI specification_file paths and documented routes in the contract (DLT-2026-082)."""
        try:
            data = safe_load_yaml(spec_file.read_text(encoding="utf-8"))
        except (OSError, YamlError) as exc:
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
        body = self._strip_asciidoc_comments(doc.body or "")
        defined_routes = {str(k) for k in paths}
        for route in sorted(defined_routes):
            if not re.search(rf"(?<![A-Za-z0-9_./:~-]){re.escape(route)}(?=$|[\s`\"'|),;\]>])", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: OpenAPI path '{route}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        http_verbs = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        stripped_for_rev = re.sub(
            r"(?ms)^(?:----|```)[^\r\n]*\r?\n.*?^(?:----|```)\s*$",
            "",
            body,
        )
        non_iface_heading_re = re.compile(
            r"\b(?:architectural\s+boundary|boundary|context|background|non[- ]goals?|out[- ]of[- ]scope|exclusions?|excluded|notes?|operational\s+notes|design\s+constraints|quality\s+attributes|migration|deprecated|history|rationale|alternatives?)\b",
            re.IGNORECASE,
        )
        contrast_cue_re = re.compile(
            r"\b(?:unlike|out[- ]of[- ]scope|exclud(?:ed|es|ing)|instead\s+of|rather\s+than|does\s+not\s+expose|not\s+exposed|do\s+not\s+(?:call|expose|use)|not\s+part\s+of|handled\s+by|owned\s+by|belongs?\s+to|delegated\s+to|legacy|deprecated)\b",
            re.IGNORECASE,
        )
        h2_matches = list(re.finditer(r"^==\s+([^\r\n]+)", stripped_for_rev, re.MULTILINE))
        sections_to_scan: list[str] = []
        if not h2_matches:
            sections_to_scan.append(stripped_for_rev)
        else:
            if h2_matches[0].start() > 0:
                sections_to_scan.append(stripped_for_rev[: h2_matches[0].start()])
            for idx_h, hm in enumerate(h2_matches):
                sec_end = h2_matches[idx_h + 1].start() if idx_h + 1 < len(h2_matches) else len(stripped_for_rev)
                heading_txt = hm.group(1).strip()
                if len(h2_matches) > 1 and non_iface_heading_re.search(heading_txt):
                    continue
                sections_to_scan.append(stripped_for_rev[hm.end() : sec_end])

        doc_route_candidates: list[str] = []
        for sec_chunk in sections_to_scan:
            for raw_line in sec_chunk.splitlines():
                stripped_line = raw_line.strip()
                if stripped_line.startswith("|") and not stripped_line.startswith("|==="):
                    cells = [c.strip().strip("`*") for c in stripped_line.split("|")[1:]]
                    if any(c.upper() in http_verbs for c in cells):
                        for cell in cells:
                            if re.fullmatch(r"/[A-Za-z0-9._~/{}:-]*", cell):
                                doc_route_candidates.append(cell)
                for m in re.finditer(
                    r"`\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9._~/{}:-]*)(?:\s[^`\r\n]*)?`",
                    raw_line,
                ):
                    if not stripped_line.startswith("|"):
                        clause_before = re.split(r"[.;!?]", raw_line[: m.start()])[-1]
                        if contrast_cue_re.search(clause_before):
                            continue
                    doc_route_candidates.append(m.group(1))

        seen_doc_routes: set[str] = set()
        for doc_route in doc_route_candidates:
            if "..." in doc_route or "*" in doc_route:
                continue
            if doc_route not in defined_routes and doc_route not in seen_doc_routes:
                seen_doc_routes.add(doc_route)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented OpenAPI path '{doc_route}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

    def _verify_json_schema_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify that JSON Schema specification_file is valid YAML/JSON and its required properties are documented in the contract."""
        try:
            data = safe_load_yaml(spec_file.read_text(encoding="utf-8"))
        except (OSError, YamlError) as exc:
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
        raw_req = data.get("required")
        req_source = raw_req if isinstance(raw_req, list) else props.keys()
        required_props = list(dict.fromkeys(str(p) for p in req_source if str(p) in props))
        body = self._strip_asciidoc_comments(doc.body or "")
        for prop_name in required_props:
            if not re.search(rf"(?:`|\b){re.escape(prop_name)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: JSON Schema property '{prop_name}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    @staticmethod
    def _extract_proto_messages_and_enums(stripped_text: str) -> tuple[list[str], set[str]]:
        """Return (top_level_types, all_defined_types) for message and enum declarations in a .proto file."""
        top_level: list[str] = []
        all_types: set[str] = set()
        depth = 0
        token_re = re.compile(r"\b(message|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b|[{}]")
        for tok in token_re.finditer(stripped_text):
            raw = tok.group(0)
            if raw == "{":
                depth += 1
            elif raw == "}":
                depth = max(0, depth - 1)
            else:
                name = tok.group(2)
                if name:
                    all_types.add(name)
                    if depth == 0 and name not in top_level:
                        top_level.append(name)
        return top_level, all_types

    def _verify_grpc_proto_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between gRPC/Protobuf declarations in .proto specification_file and the contract (DLT-2026-082, DLT-2026-135)."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        stripped_text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        stripped_text = re.sub(r"//[^\r\n]*", "", stripped_text)
        protocol = str(doc.model.protocol or "").strip().lower()
        services = re.findall(r"\bservice\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped_text)
        rpcs = re.findall(r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped_text)
        ordered_syms = list(dict.fromkeys([*services, *rpcs]))
        defined_syms = set(ordered_syms)
        body = self._strip_asciidoc_comments(doc.body or "")
        for sym in ordered_syms:
            if not re.search(rf"(?:`|\b){re.escape(sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: gRPC symbol '{sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        seen_doc_syms: set[str] = set()
        for m in re.finditer(r"`\s*(?:rpc|service)\s+([A-Za-z_][A-Za-z0-9_]*)\b", body):
            doc_sym = m.group(1)
            if doc_sym not in defined_syms and doc_sym not in seen_doc_syms:
                seen_doc_syms.add(doc_sym)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented gRPC symbol '{doc_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

        if protocol == "protobuf":
            top_proto_types, all_proto_types = self._extract_proto_messages_and_enums(stripped_text)
            for p_sym in top_proto_types:
                if not re.search(rf"(?:`|\b){re.escape(p_sym)}\b", body):
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: Protobuf symbol '{p_sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                    )
            seen_proto_doc: set[str] = set()
            for pm in re.finditer(r"`\s*(?:message|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b", body):
                doc_p_sym = pm.group(1)
                if doc_p_sym not in all_proto_types and doc_p_sym not in seen_proto_doc:
                    seen_proto_doc.add(doc_p_sym)
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: documented Protobuf symbol '{doc_p_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                    )

    def _verify_graphql_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between root Query/Mutation/Subscription fields in GraphQL specification_file and the contract (DLT-2026-082)."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        operations: list[str] = []
        for block_match in re.finditer(r"\btype\s+(?:Query|Mutation|Subscription)\s*\{([^}]*)\}", text, re.DOTALL):
            block = block_match.group(1)
            paren_depth = 0
            for line in block.splitlines():
                cleaned = line.split("#", 1)[0].strip()
                if not cleaned:
                    continue
                if paren_depth == 0:
                    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|:)", cleaned)
                    if m:
                        operations.append(m.group(1))
                paren_depth += cleaned.count("(") - cleaned.count(")")
                if paren_depth < 0:
                    paren_depth = 0
        operations = list(dict.fromkeys(operations))
        body = self._strip_asciidoc_comments(doc.body or "")
        defined_ops = set(operations)
        for op in operations:
            if not re.search(rf"(?:`|\b){re.escape(op)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: GraphQL operation '{op}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        gql_keywords = {"type", "query", "mutation", "subscription", "input", "enum", "scalar", "interface", "union"}
        seen_doc_ops: set[str] = set()
        for m in re.finditer(
            r"`(?:(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)\b[^`]*|([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_\[][^`]*\)(?:\s*:\s*[A-Za-z_\[][^`]*)?|([A-Za-z_][A-Za-z0-9_]*)\s*\([^`]*\)\s*:\s*[A-Za-z_\[][^`]*)`",
            body,
        ):
            doc_op = m.group(1) or m.group(2) or m.group(3)
            if (
                doc_op
                and doc_op.lower() not in gql_keywords
                and doc_op not in defined_ops
                and doc_op not in seen_doc_ops
            ):
                seen_doc_ops.add(doc_op)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented GraphQL operation '{doc_op}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

    _CONTRACT_PROTOCOL_VERIFIERS: ClassVar[dict[str, str]] = {
        "cli_args": "_verify_cli_contract_commands",
        "python_protocol": "_verify_python_protocol_contract",
        "rest_openapi": "_verify_rest_openapi_contract",
        "openapi": "_verify_rest_openapi_contract",
        "json_schema": "_verify_json_schema_contract",
        "grpc_proto": "_verify_grpc_proto_contract",
        "protobuf": "_verify_grpc_proto_contract",
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

    _PUBLIC_SURFACE_CACHE_MAX_ENTRIES: ClassVar[int] = 8192

    _PUBLIC_SURFACE_SYMBOL_CACHE: ClassVar[
        dict[
            tuple[str, int, int],
            tuple[
                tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
                tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
            ],
        ]
    ] = {}

    _PUBLIC_SURFACE_SKIP_EXTENSIONS: ClassVar[tuple[str, ...]] = (
        ".md",
        ".toml",
        ".adoc",
        ".json",
        ".yaml",
        ".yml",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".rs",
        ".go",
        ".cs",
        ".csproj",
        ".fsproj",
        ".vbproj",
        ".sln",
        ".props",
        ".targets",
        ".config",
        ".xml",
        ".xsd",
        ".wsdl",
        ".svc",
        ".aspx",
        ".ascx",
        ".ashx",
        ".asmx",
        ".cshtml",
        ".razor",
        ".rdlc",
        ".resx",
        ".dll",
        ".exe",
        ".pdb",
        ".proto",
        ".graphql",
        ".gql",
        ".java",
        ".kt",
        ".kts",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
        ".sql",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".css",
        ".scss",
        ".html",
        ".svg",
        ".png",
        ".pdf",
        ".lock",
        ".env",
        ".ini",
        ".cfg",
        ".txt",
        ".csv",
        ".db",
        ".sqlite",
        ".onnx",
    )

    _PUBLIC_SURFACE_NON_SYMBOL_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "true",
            "false",
            "null",
            "None",
            "True",
            "False",
            "void",
            "bool",
            "boolean",
            "int",
            "int8",
            "int16",
            "int32",
            "int64",
            "uint",
            "uint32",
            "uint64",
            "long",
            "short",
            "byte",
            "sbyte",
            "float",
            "double",
            "decimal",
            "string",
            "str",
            "char",
            "object",
            "dynamic",
            "any",
            "never",
            "unknown",
            "undefined",
            "self",
            "cls",
            "this",
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
            "SOAP",
            "REST",
            "HTTP",
            "HTTPS",
            "JSON",
            "XML",
            "GRPC",
            "WCF",
        }
    )

    @staticmethod
    def _extract_ast_base_name(base_node: ast.AST) -> str | None:
        if isinstance(base_node, ast.Name):
            return base_node.id
        if isinstance(base_node, ast.Attribute):
            return base_node.attr
        if isinstance(base_node, ast.Subscript):
            return SpecValidator._extract_ast_base_name(base_node.value)
        return None

    @staticmethod
    def _strip_balanced_angle_and_paren_args(text: str) -> str:
        """Strip balanced <...>, (...), and [...] type/call argument clauses from inheritance specs."""
        out: list[str] = []
        angle_depth = 0
        paren_depth = 0
        bracket_depth = 0
        for ch in text:
            if ch == "<":
                angle_depth += 1
            elif ch == ">" and angle_depth > 0:
                angle_depth -= 1
            elif ch == "(":
                paren_depth += 1
            elif ch == ")" and paren_depth > 0:
                paren_depth -= 1
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]" and bracket_depth > 0:
                bracket_depth -= 1
            elif angle_depth == 0 and paren_depth == 0 and bracket_depth == 0:
                out.append(ch)
        return "".join(out)

    @classmethod
    def _extract_polyglot_base_classes(cls, source: str, suffix: str) -> dict[str, set[str]]:
        """Extract class/interface base type names across ':', 'extends', 'implements', and '<' syntax."""
        bases_by_class: dict[str, set[str]] = {}
        lang_map = {
            ".ts": "typescript",
            ".tsx": "tsx",
            ".js": "javascript",
            ".jsx": "jsx",
            ".rs": "rust",
            ".java": "java",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "cpp",
            ".hpp": "cpp",
            ".swift": "swift",
            ".php": "php",
            ".rb": "ruby",
        }
        sg_lang = lang_map.get(suffix)
        if sg_lang and any(tok in source for tok in (":", "extends", "implements", "<")):
            cls_kinds = (
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
                "class_specifier",
                "struct_specifier",
                "protocol_declaration",
                "trait_item",
                "class",
            )
            base_clause_kinds = frozenset(
                {
                    "base_list",
                    "superclass",
                    "super_interfaces",
                    "extends_interfaces",
                    "class_heritage",
                    "extends_clause",
                    "implements_clause",
                    "extends_type_clause",
                    "delegation_specifier",
                    "base_class_clause",
                    "inheritance_specifier",
                    "base_clause",
                    "class_interface_clause",
                    "trait_bounds",
                }
            )
            with contextlib.suppress(RuntimeError, ValueError):
                root = SgRoot(source, sg_lang).root()
                for ck in cls_kinds:
                    with contextlib.suppress(RuntimeError, ValueError):
                        for cnode in root.find_all(kind=ck):
                            name_node = cnode.field("name")
                            if name_node is None:
                                name_node = next(
                                    (
                                        ch
                                        for ch in cnode.children()
                                        if ch.kind() in ("type_identifier", "simple_identifier", "identifier", "constant")
                                    ),
                                    None,
                                )
                            cname = name_node.text().strip() if name_node is not None else ""
                            if not cname:
                                continue
                            for ch in cnode.children():
                                if ch.kind() not in base_clause_kinds:
                                    continue
                                cleaned = cls._strip_balanced_angle_and_paren_args(ch.text())
                                for item in re.split(r"[,+&]", cleaned):
                                    stripped_item = re.sub(
                                        r"^(?::|<|\b(?:extends|implements|public|protected|private|internal|virtual|dyn)\b|\s)+",
                                        "",
                                        item.strip(),
                                    )
                                    if not stripped_item or stripped_item.startswith(("'", "?")):
                                        continue
                                    m_b = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", stripped_item)
                                    if m_b:
                                        bases_by_class.setdefault(cname, set()).add(m_b.group(1))
                if bases_by_class:
                    return bases_by_class

        for ext_m in re.finditer(
            r"\b(?:class|interface)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]*>)?\s+extends\s+([A-Za-z_][A-Za-z0-9_.]*)",
            source,
        ):
            bases_by_class.setdefault(ext_m.group(1), set()).add(ext_m.group(2).rsplit(".", 1)[-1])
        return bases_by_class

    @classmethod
    def _extract_cached_public_surface_symbols(
        cls,
        code_file: Path,
        *,
        include_component_internal: bool = True,
    ) -> tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]]:
        """Return cached (defined_symbols, defined_classes, defined_methods, methods_by_class, base_classes_by_class)."""
        try:
            resolved_file = code_file.resolve()
            st = resolved_file.stat()
        except OSError:
            return set(), set(), set(), {}, {}

        cache_key = (str(resolved_file), st.st_mtime_ns, st.st_size)
        cached = cls._PUBLIC_SURFACE_SYMBOL_CACHE.get(cache_key)
        if cached is not None:
            cls._PUBLIC_SURFACE_SYMBOL_CACHE.pop(cache_key, None)
            cls._PUBLIC_SURFACE_SYMBOL_CACHE[cache_key] = cached
            return cached[1] if include_component_internal else cached[0]

        try:
            source = resolved_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set(), set(), set(), {}, {}

        indexer = get_ast_indexer()
        pub_syms: set[str] = set()
        pub_clss: set[str] = set()
        pub_meths: set[str] = set()
        pub_cls_meths: dict[str, set[str]] = {}
        pub_bases: dict[str, set[str]] = {}

        cmp_syms: set[str] = set()
        cmp_clss: set[str] = set()
        cmp_meths: set[str] = set()
        cmp_cls_meths: dict[str, set[str]] = {}
        cmp_bases: dict[str, set[str]] = {}

        def _record_symbol(
            syms_out: set[str],
            clss_out: set[str],
            meths_out: set[str],
            cls_meths_out: dict[str, set[str]],
            sym_obj: Any,
        ) -> None:
            if sym_obj.name:
                syms_out.add(sym_obj.name)
            if sym_obj.kind in ("class", "struct", "interface", "trait", "enum") and sym_obj.name:
                clss_out.add(sym_obj.name)
                cls_meths_out.setdefault(sym_obj.name, set())
            if sym_obj.parent_symbol and sym_obj.name:
                p_leaf = sym_obj.parent_symbol.split("<", 1)[0].split("[", 1)[0].split("::")[-1].split(".")[-1].strip()
                if p_leaf:
                    syms_out.add(f"{p_leaf}.{sym_obj.name}")
                    meths_out.add(f".{sym_obj.name}")
                    clss_out.add(p_leaf)
                    cls_meths_out.setdefault(p_leaf, set()).add(sym_obj.name)

        extracted_syms = indexer.extract_symbols(resolved_file, content=source)
        interface_names = {s.name for s in extracted_syms if s.kind == "interface" and s.name}
        type_kinds = ("class", "struct", "interface", "trait", "enum")
        declared_type_names = {s.name for s in extracted_syms if s.kind in type_kinds and s.name}
        visible_cmp_classes = {
            s.name
            for s in extracted_syms
            if s.kind in type_kinds
            and s.name
            and _is_component_surface_symbol(s, interface_names=interface_names)
        }
        hidden_cmp_classes = declared_type_names - visible_cmp_classes
        for sym in extracted_syms:
            if _is_public_symbol(sym):
                _record_symbol(pub_syms, pub_clss, pub_meths, pub_cls_meths, sym)
            if _is_component_surface_symbol(
                sym,
                interface_names=interface_names,
                hidden_classes=hidden_cmp_classes,
            ):
                _record_symbol(cmp_syms, cmp_clss, cmp_meths, cmp_cls_meths, sym)

        suffix = resolved_file.suffix.lower()
        if suffix == ".py":
            py_dunder_all = _extract_python_dunder_all_from_source(source)
            with contextlib.suppress(SyntaxError, ValueError):
                tree = _parse_python_ast(source)

                def _py_top_visible(name: str) -> bool:
                    return not name.startswith("_")

                def _py_member_visible(cls_name: str, member_name: str) -> bool:
                    return not cls_name.startswith("_") and (
                        not member_name.startswith("_") or member_name in ("__init__", "__call__")
                    )

                def _py_is_container_top(name: str) -> bool:
                    if not _py_top_visible(name):
                        return False
                    return py_dunder_all is None or name in py_dunder_all

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _py_top_visible(node.name):
                            cmp_syms.add(node.name)
                            if _py_is_container_top(node.name):
                                pub_syms.add(node.name)
                    elif isinstance(node, ast.ClassDef):
                        if node.name.startswith("_"):
                            continue
                        cmp_syms.add(node.name)
                        cmp_clss.add(node.name)
                        c_meths = cmp_cls_meths.setdefault(node.name, set())
                        c_bases = cmp_bases.setdefault(node.name, set())
                        in_cnt_all = py_dunder_all is None or node.name in py_dunder_all
                        if in_cnt_all:
                            pub_syms.add(node.name)
                            pub_clss.add(node.name)
                            p_meths = pub_cls_meths.setdefault(node.name, set())
                            p_bases = pub_bases.setdefault(node.name, set())
                        else:
                            p_meths = None
                            p_bases = None
                        for base_expr in node.bases:
                            b_name = cls._extract_ast_base_name(base_expr)
                            if b_name:
                                c_bases.add(b_name)
                                if p_bases is not None:
                                    p_bases.add(b_name)
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                if _py_member_visible(node.name, item.name):
                                    cmp_syms.add(f"{node.name}.{item.name}")
                                    cmp_meths.add(f".{item.name}")
                                    c_meths.add(item.name)
                                    if p_meths is not None:
                                        pub_syms.add(f"{node.name}.{item.name}")
                                        pub_meths.add(f".{item.name}")
                                        p_meths.add(item.name)
                                for sub in ast.walk(item):
                                    if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                                        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                                        for tgt in targets:
                                            if (
                                                isinstance(tgt, ast.Attribute)
                                                and isinstance(tgt.value, ast.Name)
                                                and tgt.value.id in ("self", "cls")
                                                and _py_member_visible(node.name, tgt.attr)
                                            ):
                                                cmp_syms.add(f"{node.name}.{tgt.attr}")
                                                cmp_meths.add(f".{tgt.attr}")
                                                c_meths.add(tgt.attr)
                                                if p_meths is not None:
                                                    pub_syms.add(f"{node.name}.{tgt.attr}")
                                                    pub_meths.add(f".{tgt.attr}")
                                                    p_meths.add(tgt.attr)
                            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                if _py_member_visible(node.name, item.target.id):
                                    cmp_syms.add(f"{node.name}.{item.target.id}")
                                    cmp_meths.add(f".{item.target.id}")
                                    c_meths.add(item.target.id)
                                    if p_meths is not None:
                                        pub_syms.add(f"{node.name}.{item.target.id}")
                                        pub_meths.add(f".{item.target.id}")
                                        p_meths.add(item.target.id)
                            elif isinstance(item, ast.Assign):
                                for t in item.targets:
                                    if isinstance(t, ast.Name) and _py_member_visible(node.name, t.id):
                                        cmp_syms.add(f"{node.name}.{t.id}")
                                        cmp_meths.add(f".{t.id}")
                                        c_meths.add(t.id)
                                        if p_meths is not None:
                                            pub_syms.add(f"{node.name}.{t.id}")
                                            pub_meths.add(f".{t.id}")
                                            p_meths.add(t.id)
                    elif isinstance(node, ast.Assign):
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                if _py_top_visible(t.id):
                                    cmp_syms.add(t.id)
                                    if _py_is_container_top(t.id):
                                        pub_syms.add(t.id)
                                if t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                                    for elt in node.value.elts:
                                        if (
                                            isinstance(elt, ast.Constant)
                                            and isinstance(elt.value, str)
                                            and not elt.value.startswith("_")
                                        ):
                                            cmp_syms.add(elt.value)
                                            pub_syms.add(elt.value)
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        if _py_top_visible(node.target.id):
                            cmp_syms.add(node.target.id)
                            if _py_is_container_top(node.target.id):
                                pub_syms.add(node.target.id)
                    elif isinstance(node, ast.ImportFrom):
                        for al in node.names:
                            aname = al.asname or al.name
                            if _py_top_visible(aname):
                                cmp_syms.add(aname)
                                if _py_is_container_top(aname):
                                    pub_syms.add(aname)
                    elif isinstance(node, ast.Import):
                        for al in node.names:
                            aname = al.asname or al.name.split(".")[0]
                            if _py_top_visible(aname):
                                cmp_syms.add(aname)
                                if _py_is_container_top(aname):
                                    pub_syms.add(aname)
        else:
            extracted_bases = cls._extract_polyglot_base_classes(source, suffix)
            for cn, bset in extracted_bases.items():
                cmp_bases.setdefault(cn, set()).update(bset)
                pub_bases.setdefault(cn, set()).update(bset)

        while len(cls._PUBLIC_SURFACE_SYMBOL_CACHE) >= cls._PUBLIC_SURFACE_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(cls._PUBLIC_SURFACE_SYMBOL_CACHE))
            cls._PUBLIC_SURFACE_SYMBOL_CACHE.pop(oldest_key, None)

        pub_result = (pub_syms, pub_clss, pub_meths, pub_cls_meths, pub_bases)
        cmp_result = (cmp_syms, cmp_clss, cmp_meths, cmp_cls_meths, cmp_bases)
        cls._PUBLIC_SURFACE_SYMBOL_CACHE[cache_key] = (pub_result, cmp_result)
        return cmp_result if include_component_internal else pub_result

    @staticmethod
    def _strip_public_surface_generics(expr: str) -> str:
        """Strip generic type parameter brackets (<...> and [...]) from the leading symbol path before '(' or ':' or '='."""
        out: list[str] = []
        i = 0
        n = len(expr)
        while i < n:
            ch = expr[i]
            if ch in ("(", "=") or ch.isspace():
                out.append(expr[i:])
                break
            if ch == ":" and not (i + 1 < n and expr[i + 1] == ":"):
                out.append(expr[i:])
                break
            if ch == "<":
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if expr[j] == "<":
                        depth += 1
                    elif expr[j] == ">":
                        depth -= 1
                    j += 1
                if depth == 0:
                    i = j
                    continue
            elif ch == "[":
                inner_start = i + 1
                while inner_start < n and expr[inner_start].isspace():
                    inner_start += 1
                if inner_start < n and expr[inner_start] not in (
                    "'",
                    '"',
                    "0",
                    "1",
                    "2",
                    "3",
                    "4",
                    "5",
                    "6",
                    "7",
                    "8",
                    "9",
                ):
                    depth = 1
                    j = i + 1
                    while j < n and depth > 0:
                        if expr[j] == "[":
                            depth += 1
                        elif expr[j] == "]":
                            depth -= 1
                        j += 1
                    if depth == 0:
                        i = j
                        continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _iter_public_surface_code_files(directory: Path) -> list[Path]:
        """Walk ``directory`` returning AST source files while pruning build/cache/venv outputs."""
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(str(directory)):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in STANDARD_EXCLUDED_DIRS
                and not d.startswith(".")
                and not is_dotnet_bin_dir(Path(dirpath) / d)
            )
            for fn in sorted(filenames):
                fp = Path(dirpath) / fn
                if fp.suffix.lower() in AST_DIFF_EXTS:
                    found.append(fp)
        return found

    @staticmethod
    def _split_list_item_head_and_prose(item_body: str) -> tuple[str, str]:
        """Split a list item into declaration head and trailing prose tail without matching separators inside backticks."""
        in_bt = False
        n = len(item_body)
        i = 0
        while i < n:
            ch = item_body[i]
            if ch == "`":
                in_bt = not in_bt
                i += 1
                continue
            if not in_bt and item_body.startswith((" \u2014 ", " \u2013 ", " -- ", " - ", ": "), i):
                return item_body[:i], item_body[i:]
            i += 1
        return item_body, ""

    @classmethod
    def _is_explicit_code_span(cls, s: str) -> bool:
        """Return True if a backtick span carries explicit code syntax rather than a bare single-word prose token."""
        if any(ch in s for ch in ("(", "<", "[", "=", ">")) or "::" in s:
            return True
        if "." in s and not s.lower().endswith(cls._PUBLIC_SURFACE_SKIP_EXTENSIONS):
            return True
        return bool(
            re.match(
                r"^(?:pub(?:\s*\([^)]*\))?|export|default|async|unsafe|const|static|abstract|readonly|override|virtual|inline|def|class|fn|func|function|struct|enum|trait|interface|type|let|var|impl|public|internal)\s+",
                s,
            )
        )

    @classmethod
    def _iter_public_surface_candidate_spans(cls, section_body: str) -> list[str]:
        """Extract symbol candidate backtick spans from '== Public Surface', filtering narrative prose tokens."""
        stripped_blocks = re.sub(
            r"(?ms)^(?:----|```)[^\r\n]*\r?\n.*?^(?:----|```)\s*$",
            "",
            section_body,
        )
        candidates: list[str] = []
        bt_re = re.compile(r"`([^`]+)`")

        def _maybe_add(raw_span: str, *, require_code_syntax: bool) -> None:
            s = raw_span.strip()
            if (
                not s
                or "/" in s
                or "\\" in s
                or s.startswith(("[", "-", '"', "'", "$", "@", "<"))
                or s in cls._PUBLIC_SURFACE_NON_SYMBOL_KEYWORDS
            ):
                return
            if "(" not in s and "::" not in s and s.lower().endswith(cls._PUBLIC_SURFACE_SKIP_EXTENSIONS):
                return
            if require_code_syntax and not cls._is_explicit_code_span(s):
                return
            candidates.append(s)

        for raw_line in stripped_blocks.splitlines():
            line = raw_line.strip()
            if not line or line == "|===" or (line.startswith(":") and ":" in line[1:]) or line.startswith("["):
                continue
            if re.match(r"^===+\s+", line):
                for m in bt_re.finditer(line):
                    _maybe_add(m.group(1), require_code_syntax=False)
                continue
            list_m = re.match(r"^(?:[*.-]+|\d+\.)\s+(.*)$", line)
            if list_m:
                head, tail = cls._split_list_item_head_and_prose(list_m.group(1))
                for m in bt_re.finditer(head):
                    _maybe_add(m.group(1), require_code_syntax=False)
                if tail:
                    for m in bt_re.finditer(tail):
                        _maybe_add(m.group(1), require_code_syntax=True)
                continue
            if line.startswith("|"):
                cells = line.split("|")[1:]
                if cells:
                    for m in bt_re.finditer(cells[0]):
                        _maybe_add(m.group(1), require_code_syntax=False)
                    for cell in cells[1:]:
                        for m in bt_re.finditer(cell):
                            _maybe_add(m.group(1), require_code_syntax=True)
                continue
            for m in bt_re.finditer(line):
                _maybe_add(m.group(1), require_code_syntax=True)

        return candidates

    def _verify_entity_public_surface(
        self,
        doc: SpecDocument,
        repo_root: Path,
        root_dir: Path,
        report: ValidationReport,
    ) -> None:
        """Verify that backticked code symbols under '== Public Surface' in CMP-*/CNT-* specs exist in their source_path."""
        clean_body = self._strip_asciidoc_comments(doc.body or "")
        sec_m = re.search(
            r"^==\s+Public Surface\b[^\r\n]*\r?\n(.*?)(?=^==\s+|\Z)",
            clean_body,
            re.MULTILINE | re.DOTALL,
        )
        if not sec_m:
            return

        code_files: list[Path] = []
        for raw_sp in doc.model.source_paths():
            p = raw_sp.strip()
            if (
                not p
                or p.upper() in ("N/A", "TBD", "NONE")
                or p.endswith((".adoc", ".md", ".toml", ".yaml", ".yml", ".json"))
            ):
                continue
            for expanded in expand_brace_globs(p):
                if any(ch in expanded for ch in ("*", "?", "[")):
                    matches = sorted(repo_root.glob(expanded)) or sorted(root_dir.glob(expanded))
                    for m_path in matches:
                        if m_path.is_file() and m_path.suffix.lower() in AST_DIFF_EXTS:
                            code_files.append(m_path)
                        elif m_path.is_dir():
                            code_files.extend(self._iter_public_surface_code_files(m_path))
                    continue
                resolved = self._resolve_source_path(repo_root, root_dir, expanded)
                if resolved is None:
                    continue
                if resolved.is_file() and resolved.suffix.lower() in AST_DIFF_EXTS:
                    code_files.append(resolved)
                elif resolved.is_dir():
                    code_files.extend(self._iter_public_surface_code_files(resolved))

        if not code_files:
            return

        include_cmp_internal = doc.doc_type == "tech/component"
        defined_symbols: set[str] = set()
        defined_classes: set[str] = set()
        defined_methods: set[str] = set()
        methods_by_class: dict[str, set[str]] = {}
        base_classes_by_class: dict[str, set[str]] = {}
        for cf in dict.fromkeys(code_files):
            f_syms, f_clss, f_meths, f_cls_meths, f_cls_bases = self._extract_cached_public_surface_symbols(
                cf,
                include_component_internal=include_cmp_internal,
            )
            defined_symbols.update(f_syms)
            defined_classes.update(f_clss)
            defined_methods.update(f_meths)
            for cn, mset in f_cls_meths.items():
                methods_by_class.setdefault(cn, set()).update(mset)
            for cn, bset in f_cls_bases.items():
                base_classes_by_class.setdefault(cn, set()).update(bset)

        neutral_bases = {
            "object",
            "ABC",
            "Protocol",
            "Generic",
            "Object",
            "IDisposable",
            "IAsyncDisposable",
            "IEquatable",
            "IComparable",
            "ICloneable",
            "Exception",
            "Attribute",
            "Send",
            "Sync",
            "Sized",
            "Copy",
            "Clone",
            "Debug",
            "Default",
        }
        resolved_methods_by_class: dict[str, set[str]] = {}
        classes_with_external_bases: set[str] = set()
        for cn in defined_classes | set(methods_by_class) | set(base_classes_by_class):
            acc_meths = set(methods_by_class.get(cn, set()))
            stack = list(base_classes_by_class.get(cn, set()))
            seen_anc: set[str] = {cn}
            has_ext = False
            while stack:
                anc = stack.pop()
                if anc in seen_anc or anc in neutral_bases:
                    continue
                seen_anc.add(anc)
                if anc in defined_classes or anc in methods_by_class:
                    acc_meths.update(methods_by_class.get(anc, set()))
                    stack.extend(base_classes_by_class.get(anc, set()))
                else:
                    has_ext = True
            resolved_methods_by_class[cn] = acc_meths
            if has_ext:
                classes_with_external_bases.add(cn)

        def _class_has_member(cls_name: str, member_name: str) -> bool:
            if f"{cls_name}.{member_name}" in defined_symbols or member_name in resolved_methods_by_class.get(
                cls_name, set()
            ):
                return True
            return cls_name in classes_with_external_bases and f".{member_name}" in defined_methods

        seen_missing: set[str] = set()
        for s in self._iter_public_surface_candidate_spans(sec_m.group(1)):
            # Normalize Go receiver headers: func (s *Server) Start(...) -> Server.Start(...)
            go_m = re.match(
                r"^func\s*\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)?\*?\s*([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\s*\)\s*([A-Za-z_][A-Za-z0-9_]*.*)$",
                s,
            )
            if go_m:
                s = f"{go_m.group(1)}.{go_m.group(2)}"
            else:
                while True:
                    next_s = re.sub(
                        r"^(?:pub(?:\s*\([^)]*\))?|export|default|async|unsafe|const|static|abstract|readonly|override|virtual|inline|public|internal|sealed|partial|record|event|extern(?:\s+\"[^\"]*\")?|def|class|fn|func|function|struct|enum|trait|interface|type|let|var|impl)\s+",
                        "",
                        s,
                    )
                    if next_s == s:
                        break
                    s = next_s
            s = self._strip_public_surface_generics(s)
            ms = re.match(
                r"^([A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*)\s*(?:\(|:(?!:)|=|$)",
                s,
            )
            if not ms:
                continue
            ident = ms.group(1)
            if ident in seen_missing:
                continue
            exists = ident in defined_symbols
            if not exists and "::" in ident:
                ns_parts = ident.split("::")
                penult, leaf = ns_parts[-2], ns_parts[-1]
                if penult[0].isupper() or penult in defined_classes or penult in methods_by_class:
                    exists = (penult in defined_symbols or penult in defined_classes) and _class_has_member(
                        penult, leaf
                    )
                else:
                    exists = leaf in defined_symbols
            elif not exists and "." in ident:
                dot_parts = ident.split(".")
                c_part, m_part = dot_parts[-2], dot_parts[-1]
                if c_part in defined_classes or c_part in methods_by_class:
                    exists = _class_has_member(c_part, m_part)
            if not exists:
                seen_missing.add(ident)
                kind_label = "Component" if doc.doc_type == "tech/component" else "Container"
                report.broken_references.append(
                    f"Public surface drift in {doc.file_path.name}: {kind_label} '{doc.doc_id}' documents symbol '{ident}' under '== Public Surface' which does not exist in its source_path"
                )

    def _validate_import_boundary_declarations(self, report: ValidationReport, root_dir: Path) -> None:
        """Validate disjoint allow/deny sets and resolved tier slugs on import_boundary / import_boundaries."""
        _, repo_root = resolve_spec_and_repo_roots(root_dir)
        path_to_id = _build_doc_path_to_id_map(report.documents, repo_root)
        valid_tiers: set[str] = {"presentation", "service", "core", "persistence", "extensibility"}
        for doc in report.documents:
            if doc.doc_type == "tech/container" and doc.model.tier:
                valid_tiers.add(doc.model.tier.strip())

        def _norm_ref(ref: str) -> str:
            clean = ref.strip().replace("\\", "/").lstrip("./")
            return path_to_id.get(clean, clean)

        for doc in report.documents:
            err_key = self._schema_error_key(doc.file_path, root_dir=root_dir, repo_root=repo_root)

            def _record_decl_err(msg: str, _doc: SpecDocument = doc, _key: str = err_key) -> None:
                if msg not in _doc.errors:
                    _doc.errors.append(msg)
                bucket = report.schema_errors.setdefault(_key, [])
                if msg not in bucket:
                    bucket.append(msg)

            ib = doc.model.import_boundary
            if ib is not None and doc.doc_type in ("tech/container", "tech/component"):
                allow_set = {_norm_ref(x) for x in ib.allow if x}
                deny_set = {_norm_ref(x) for x in ib.deny if x}
                overlap = sorted(allow_set & deny_set)
                if overlap:
                    _record_decl_err(f"import_boundary has overlapping allow and deny targets: {', '.join(overlap)}")
            ibs = doc.model.import_boundaries
            if ibs is not None and doc.doc_type == "tech/architecture":
                for src_tier, rule in ibs.tier_rules.items():
                    if src_tier not in valid_tiers:
                        _record_decl_err(f"import_boundaries.tier_rules references unresolved tier '{src_tier}'")
                    t_overlap = sorted(set(rule.allow) & set(rule.deny))
                    if t_overlap:
                        _record_decl_err(
                            f"import_boundaries.tier_rules.{src_tier} has overlapping allow and deny tiers: {', '.join(t_overlap)}"
                        )
                    for target_t in (*rule.allow, *rule.deny):
                        if target_t not in valid_tiers:
                            _record_decl_err(
                                f"import_boundaries.tier_rules.{src_tier} references unresolved tier '{target_t}'"
                            )


def _build_doc_path_to_id_map(documents: list[SpecDocument], repo_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for doc in documents:
        if not doc.doc_id:
            continue
        mapping[doc.doc_id] = doc.doc_id
        with contextlib.suppress(ValueError, OSError):
            rel_repo = doc.file_path.resolve().relative_to(repo_root.resolve()).as_posix()
            mapping[rel_repo] = doc.doc_id
            if rel_repo.startswith("eidos/"):
                mapping[rel_repo.removeprefix("eidos/")] = doc.doc_id
    return mapping


def _rel_doc_path(fp: Path, spec_root: Path, repo_root: Path) -> str:
    res = fp.resolve()
    for base in (repo_root.resolve(), spec_root.resolve()):
        with contextlib.suppress(ValueError):
            return res.relative_to(base).as_posix()
    return res.as_posix()


def evaluate_import_boundaries(
    eidos_dir: Path,
    *,
    repo_root: Path | None = None,
    documents: list[SpecDocument] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate ARCH-*.import_boundaries and CNT-*/CMP-*.import_boundary against synced AST/HTTP code_couplings."""
    spec_root, inferred_repo_root = resolve_spec_and_repo_roots(eidos_dir)
    eff_spec = Path(eidos_dir).resolve() if Path(eidos_dir).is_dir() else spec_root.resolve()
    eff_repo = Path(repo_root).resolve() if repo_root is not None else inferred_repo_root.resolve()
    if not eff_spec.is_dir():
        return [], []

    if documents is None:
        from eidos.core.index.storage import get_cached_spec_document

        docs_loaded: list[SpecDocument] = []
        for fp in sorted(eff_spec.rglob("*.adoc")):
            if fp.name.lower() == "readme.adoc" or fp.name.startswith("_") or fp.is_symlink():
                continue
            doc, _, _ = get_cached_spec_document(fp)
            if doc is not None:
                docs_loaded.append(doc)
        documents = docs_loaded

    path_to_id = _build_doc_path_to_id_map(documents, eff_repo)

    def _res_id(raw_ref: str) -> str:
        clean = (raw_ref or "").strip().replace("\\", "/").lstrip("./")
        return path_to_id.get(clean, clean)

    arch_specs: list[tuple[str, str, Any]] = []
    cnt_bounds: dict[str, tuple[str, Any]] = {}
    cmp_bounds: dict[str, tuple[str, Any]] = {}
    cnt_tiers: dict[str, str] = {}
    cnt_rel_paths: dict[str, str] = {}
    cmp_rel_paths: dict[str, str] = {}
    cmp_to_cnt: dict[str, str] = {}

    for doc in documents:
        status = (doc.model.status or "active").strip().lower()
        if status in ("deprecated", "retired") or not doc.doc_id:
            continue
        rel_p = _rel_doc_path(doc.file_path, eff_spec, eff_repo)
        if doc.doc_type == "tech/architecture" and doc.model.import_boundaries is not None:
            ibs = doc.model.import_boundaries
            if ibs.tier_rules or ibs.enforce_declared_containers or ibs.ratchet_baseline:
                arch_specs.append((doc.doc_id, rel_p, ibs))
        elif doc.doc_type == "tech/container":
            cnt_rel_paths[doc.doc_id] = rel_p
            if doc.model.tier:
                cnt_tiers[doc.doc_id] = doc.model.tier.strip()
            if doc.model.import_boundary is not None and (
                doc.model.import_boundary.allow or doc.model.import_boundary.deny
            ):
                cnt_bounds[doc.doc_id] = (rel_p, doc.model.import_boundary)
        elif doc.doc_type == "tech/component":
            cmp_rel_paths[doc.doc_id] = rel_p
            if doc.model.container:
                cmp_to_cnt[doc.doc_id] = _res_id(doc.model.container)
            if doc.model.import_boundary is not None and (
                doc.model.import_boundary.allow or doc.model.import_boundary.deny
            ):
                cmp_bounds[doc.doc_id] = (rel_p, doc.model.import_boundary)

    if not arch_specs and not cnt_bounds and not cmp_bounds:
        return [], []

    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.index.sync import sync_index

    idx = get_architectural_index(repo_root=eff_repo, eidos_dir=eff_spec)
    conn = idx._get_connection()
    has_entities = conn.execute("SELECT 1 FROM entities LIMIT 1;").fetchone() is not None
    if not has_entities or idx.is_dirty()[0]:
        sync_index(idx, auto_semantic=False)
        conn = idx._get_connection()

    declared_ctr_pairs: set[tuple[str, str]] = {
        (str(r[0]), str(r[1]))
        for r in conn.execute(
            """
            SELECT DISTINCT
                COALESCE(p_src.target_id, c_edge.source_id) AS src_cnt,
                COALESCE(p_prov.target_id, p_edge.source_id) AS dst_cnt
            FROM edges c_edge
            LEFT JOIN edges p_src ON p_src.source_id = c_edge.source_id AND p_src.rel_type = 'part_of'
            JOIN edges p_edge ON p_edge.target_id = c_edge.target_id AND p_edge.rel_type = 'provides'
            LEFT JOIN edges p_prov ON p_prov.source_id = p_edge.source_id AND p_prov.rel_type = 'part_of'
            WHERE c_edge.rel_type = 'consumes'
              AND c_edge.provenance = 'declared_spec';
            """
        ).fetchall()
        if r[0] and r[1]
    }

    coupling_rows = conn.execute(
        """
        SELECT source_entity_id, target_entity_id, source_cnt_id, target_cnt_id, provenance, src_file, line_start, target_ref
        FROM code_couplings
        ORDER BY src_file, line_start, target_ref;
        """
    ).fetchall()

    ratchet_map: dict[tuple[str, str], tuple[int, str, str]] = {}
    for _arch_id, arch_rel, ibs in arch_specs:
        for rb in ibs.ratchet_baseline:
            r_src, r_dst = _res_id(rb.source), _res_id(rb.target)
            if r_src and r_dst:
                ratchet_map[(r_src, r_dst)] = (int(rb.max_imports), str(rb.reason or ""), arch_rel)

    violations: list[dict[str, Any]] = []
    shrink_candidates: list[dict[str, Any]] = []
    handled_ratchet_pairs: set[tuple[str, str]] = set()

    def _evaluate_pair(
        src_id: str,
        dst_id: str,
        rule_name: str,
        spec_rel: str,
        ev_items: list[str],
    ) -> None:
        obs = len(ev_items)
        r_entry = ratchet_map.get((src_id, dst_id))
        handled_ratchet_pairs.add((src_id, dst_id))
        if r_entry is None:
            if obs > 0:
                violations.append(
                    {
                        "source": src_id,
                        "target": dst_id,
                        "rule": rule_name,
                        "observed_imports": obs,
                        "max_imports": 0,
                        "reason": "",
                        "spec_path": spec_rel,
                        "evidence": ev_items[:10],
                    }
                )
            return
        max_imp, r_reason, r_spec = r_entry
        if obs > max_imp:
            violations.append(
                {
                    "source": src_id,
                    "target": dst_id,
                    "rule": rule_name,
                    "observed_imports": obs,
                    "max_imports": max_imp,
                    "reason": r_reason,
                    "spec_path": r_spec,
                    "evidence": ev_items[:10],
                }
            )
        elif obs < max_imp:
            shrink_candidates.append(
                {
                    "source": src_id,
                    "target": dst_id,
                    "observed_imports": obs,
                    "max_imports": max_imp,
                    "suggested_max_imports": obs,
                    "action": "remove_entry" if obs == 0 else f"lower max_imports to {obs}",
                    "spec_path": r_spec,
                }
            )

    # (a) Component-level CMP-*.import_boundary rules
    for src_cmp, (cmp_rel, ib) in sorted(cmp_bounds.items()):
        allow_ids = {_res_id(x) for x in ib.allow if x}
        deny_ids = {_res_id(x) for x in ib.deny if x}
        allow_has_cmp = any(x.startswith("CMP-") for x in allow_ids)
        src_parent_cnt = cmp_to_cnt.get(src_cmp, "")
        grouped_cmp: dict[tuple[str, str], list[str]] = {}
        for r in coupling_rows:
            if r["source_entity_id"] != src_cmp:
                continue
            dst_ent = str(r["target_entity_id"] or "")
            dst_cnt = str(r["target_cnt_id"] or "")
            ev = f"{r['src_file']}:{r['line_start']} -> {r['target_ref']}"
            if dst_ent in deny_ids:
                grouped_cmp.setdefault((dst_ent, f"{src_cmp}.import_boundary.deny"), []).append(ev)
            elif dst_cnt and dst_cnt != src_parent_cnt and dst_cnt in deny_ids:
                grouped_cmp.setdefault((dst_cnt, f"{src_cmp}.import_boundary.deny"), []).append(ev)
            elif allow_ids:
                same_cnt = bool(src_parent_cnt and dst_cnt == src_parent_cnt)
                if same_cnt and not allow_has_cmp:
                    continue
                if dst_ent not in allow_ids and (not dst_cnt or dst_cnt not in allow_ids):
                    if dst_cnt and (src_cmp, dst_cnt) in ratchet_map and (src_cmp, dst_ent) not in ratchet_map:
                        target_key = dst_cnt
                    else:
                        target_key = dst_ent if (allow_has_cmp or not dst_cnt) else dst_cnt
                    grouped_cmp.setdefault((target_key, f"{src_cmp}.import_boundary.allow"), []).append(ev)
        for (dst_target, r_name), ev_list in sorted(grouped_cmp.items()):
            _evaluate_pair(src_cmp, dst_target, r_name, cmp_rel, ev_list)

    # (b), (c), (d) Cross-container rules: CNT-*.import_boundary -> ARCH-*.tier_rules -> enforce_declared_containers
    cnt_couplings: dict[tuple[str, str], list[str]] = {}
    for r in coupling_rows:
        s_cnt = str(r["source_cnt_id"] or "")
        d_cnt = str(r["target_cnt_id"] or "")
        if s_cnt.startswith("CNT-") and d_cnt.startswith("CNT-") and s_cnt != d_cnt:
            cnt_couplings.setdefault((s_cnt, d_cnt), []).append(
                f"{r['src_file']}:{r['line_start']} -> {r['target_ref']}"
            )

    candidate_cnt_pairs = sorted(
        set(cnt_couplings)
        | {(s, t) for (s, t) in ratchet_map if s.startswith("CNT-") and t.startswith("CNT-")}
    )
    for s_cnt, d_cnt in candidate_cnt_pairs:
        violated_rule = ""
        violated_spec = ""
        if s_cnt in cnt_bounds:
            c_rel, c_ib = cnt_bounds[s_cnt]
            c_allow = {_res_id(x) for x in c_ib.allow if x}
            c_deny = {_res_id(x) for x in c_ib.deny if x}
            if d_cnt in c_deny:
                violated_rule = f"{s_cnt}.import_boundary.deny"
                violated_spec = c_rel
            elif c_allow and d_cnt not in c_allow:
                violated_rule = f"{s_cnt}.import_boundary.allow"
                violated_spec = c_rel

        if not violated_rule:
            s_tier = cnt_tiers.get(s_cnt, "")
            d_tier = cnt_tiers.get(d_cnt, "")
            for arch_id, arch_rel, ibs in arch_specs:
                if s_tier and d_tier and s_tier in ibs.tier_rules:
                    t_rule = ibs.tier_rules[s_tier]
                    if d_tier in t_rule.deny:
                        violated_rule = f"{arch_id}.tier_rules.{s_tier}.deny({d_tier})"
                        violated_spec = arch_rel
                        break
                    if t_rule.allow and d_tier not in t_rule.allow:
                        violated_rule = f"{arch_id}.tier_rules.{s_tier}.allow({d_tier})"
                        violated_spec = arch_rel
                        break
                if ibs.enforce_declared_containers and (s_cnt, d_cnt) not in declared_ctr_pairs:
                    violated_rule = f"{arch_id}.enforce_declared_containers"
                    violated_spec = arch_rel
                    break

        if violated_rule:
            _evaluate_pair(s_cnt, d_cnt, violated_rule, violated_spec, cnt_couplings.get((s_cnt, d_cnt), []))

    # Emit ratchet_shrink_candidates for any remaining ratchet_baseline entries with fewer observed couplings
    for (r_src, r_dst), (max_imp, _r_reason, r_spec) in sorted(ratchet_map.items()):
        if (r_src, r_dst) in handled_ratchet_pairs:
            continue
        if r_src.startswith("CMP-"):
            obs_rem = sum(
                1
                for r in coupling_rows
                if r["source_entity_id"] == r_src
                and (r["target_entity_id"] == r_dst or r["target_cnt_id"] == r_dst)
            )
        else:
            obs_rem = len(cnt_couplings.get((r_src, r_dst), []))
        if obs_rem < max_imp:
            shrink_candidates.append(
                {
                    "source": r_src,
                    "target": r_dst,
                    "observed_imports": obs_rem,
                    "max_imports": max_imp,
                    "suggested_max_imports": obs_rem,
                    "action": "remove_entry" if obs_rem == 0 else f"lower max_imports to {obs_rem}",
                    "spec_path": r_spec,
                }
            )

    return violations, shrink_candidates
