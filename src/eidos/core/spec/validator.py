"""Validator for Eidos specifications against JSON/YAML schemas."""

import ast
import contextlib
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, ClassVar

import jsonschema
import yaml

from eidos.core.index.ast_indexer import AST_DIFF_EXTS, get_ast_indexer
from eidos.core.spec.models import SpecDocument, ValidationReport
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
    "INV-",
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
_NON_ROOT_SPEC_SUBDIRS = _SPEC_SUBDIRS | frozenset(
    {
        "systems",
        "components",
        "containers",
        "contracts",
        "capabilities",
        "clients",
        "screens",
        "flows",
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
            cand_lower = cand.name.lower()
            ancestor_lowers = [p.lower() for p in cand.parts[:-1]]
            if cand_lower not in _NON_ROOT_SPEC_SUBDIRS and (
                cand_lower == "eidos"
                or (
                    "eidos" not in ancestor_lowers
                    and (not cand.parent or cand.parent.name.lower() not in _NON_ROOT_SPEC_SUBDIRS)
                )
            ):
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
        schema_ref_str = doc.schema_ref if isinstance(doc.schema_ref, str) else None
        doc_type_str = doc.doc_type if isinstance(doc.doc_type, str) else None

        # 1. Check explicit relative schema reference (containing a directory component),
        # unless it is an ambiguous root system.schema.yaml reference without product/ or design/.
        if schema_ref_str:
            norm_ref = schema_ref_str.replace(".spec/", "").strip().replace("\\", "/")
            if (
                "/" in norm_ref
                and norm_ref not in ("system.schema.yaml", "schemas/v1/system.schema.yaml")
                and norm_ref in self._schema_cache
            ):
                return self._schema_cache[norm_ref]

        # 2. Check document type mapping (disambiguates colliding basenames like system.schema.yaml)
        if doc_type_str and doc_type_str in TYPE_TO_SCHEMA_NAME:
            schema_name = TYPE_TO_SCHEMA_NAME[doc_type_str]
            return self._schema_cache.get(schema_name)

        # 3. Fallback to basename lookup for custom/unmapped types
        if schema_ref_str:
            basename = Path(schema_ref_str.replace(".spec/", "").strip()).name
            if basename in self._schema_cache:
                return self._schema_cache[basename]

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
        to_upsert: list[tuple[str, int, str, str, str, str]] = []
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
            if (
                fp.name.lower() == "readme.adoc"
                or fp.name.startswith("_")
                or fp.is_symlink()
            ):
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
                    schema_errs = list(json.loads(row["schema_errors_json"]))
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

            doc, sha256, mtime_ns = get_cached_spec_document(
                fp, mtime_ns=mtime_ns, st_size=st.st_size, cached_row=row
            )
            if doc is None or not sha256:
                return None
            if is_root_system and not doc.frontmatter and not doc.body.strip() and not doc.errors:
                return None

            if row is not None and row["sha256"] == sha256 and row["schema_hash"] == effective_schema_hash:
                with contextlib.suppress(ValueError, TypeError, KeyError):
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

        deleted_keys = (
            [k for k in cached_rows if k not in seen_rel_keys] if resolved_root == eidos_cache_root else []
        )
        if eidos_cache_root.exists() and (to_upsert or deleted_keys):
            save_spec_validation_cache(db_path, to_upsert, deleted_keys)

        # Cross-reference validation
        self._validate_cross_references(report, root_dir, fast_pathed_applied_deltas=fast_pathed_applied_deltas)

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

        # Also register inline component IDs and embedded invariant IDs declared within documents in catalog_ids
        # and enforce global uniqueness across the catalog.
        comp_owners: dict[str, SpecDocument] = {}
        inv_owners: dict[str, SpecDocument] = {}
        for doc in report.documents:
            if doc.frontmatter:
                if doc.doc_type == "tech/container":
                    for comp in doc.model.components:
                        if not comp.id:
                            continue
                        if comp.id in comp_owners or comp.id in report.entity_index:
                            prev_doc = comp_owners.get(comp.id) or report.entity_index.get(comp.id)
                            prev_name = prev_doc.file_path.name if prev_doc else doc.file_path.name
                            report.broken_references.append(
                                f"Duplicate entity ID '{comp.id}' found in {doc.file_path.name} (already defined in {prev_name})"
                            )
                        else:
                            comp_owners[comp.id] = doc
                            catalog_ids.add(comp.id)
                            entity_types.setdefault(comp.id, "tech/component")
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

        # When validating a subdirectory of an existing spec_root (e.g. eidos/domains), seed
        # catalog_ids and entity_types from sibling specs in spec_root so cross-directory
        # references (such as SYS-* or CNT-*) do not falsely fail.
        spec_root, _ = _resolve_spec_and_repo_roots(root_dir)
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
                    if ext_doc.doc_type == "tech/container":
                        for comp in ext_doc.model.components:
                            if comp.id:
                                catalog_ids.add(comp.id)
                                entity_types.setdefault(comp.id, "tech/component")
                    for inv in ext_doc.model.all_invariants():
                        if inv.id:
                            catalog_ids.add(inv.id)
                            entity_types.setdefault(inv.id, "invariant")

        for doc in report.documents:
            # 2. Check relative file paths ending in .adoc
            self._check_dict_references(
                doc.frontmatter, doc.file_path, root_dir, report, doc_type=doc.doc_type
            )

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
            self._validate_schema_references(
                schema, fm, "", doc, catalog_ids, report, entity_types, root_dir=root_dir
            )
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
                    self._validate_schema_references(
                        resolved_def, value, field_path, doc, catalog_ids, report, entity_types, root_dir=root_dir
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
                    if isinstance(branch, dict) and (
                        "$ref" in branch or "items" in branch or "x-eidos-ref" in branch
                    ):
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

                spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir, doc.file_path)
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

        if actual_type and field_path in ("mutations.target", "mutations.parent"):
            from eidos.core.lifecycle.delta_engine import resolve_entity_prefix_meta

            p_meta = resolve_entity_prefix_meta(val)
            if p_meta is not None and actual_type != p_meta[2]:
                label = ref_meta.get("entity_label") or "entity"
                report.broken_references.append(
                    f"Invalid entity reference type in {doc.file_path.name}: "
                    f"{label} '{val}' in {field_path} has type '{actual_type}', expected {p_meta[2]}"
                )

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
                bucket = report.schema_errors.setdefault(str(doc.file_path), [])
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
                    norm_parts = [p.lower() for p in Path(target_val.replace("\\", "/")).parts]
                    stem_upper = Path(target_val).stem.upper()
                    if stem_upper.startswith(
                        (
                            "CAP-",
                            "POL-",
                            "ARCH-",
                            "INV-",
                            "CNT-",
                            "CMP-",
                            "CTR-",
                            "DOM-",
                            "PER-",
                            "DS-",
                            "UIC-",
                            "SCR-",
                            "FLOW-",
                            "CLT-",
                            "EXP-",
                        )
                    ):
                        effective_target_upper = stem_upper
                    elif "capabilities" in norm_parts:
                        effective_target_upper = f"CAP-{stem_upper}"
                    elif "policies" in norm_parts:
                        effective_target_upper = f"POL-{stem_upper}"
                    elif "containers" in norm_parts:
                        effective_target_upper = f"CNT-{stem_upper}"
                    elif "contracts" in norm_parts:
                        effective_target_upper = f"CTR-{stem_upper}"
                    elif "architecture" in norm_parts and "components" in norm_parts:
                        effective_target_upper = f"CMP-{stem_upper}"
                    elif "architecture" in norm_parts and stem_upper in (
                        "SYSTEM-ARCHITECTURE",
                        "OVERVIEW",
                    ):
                        effective_target_upper = f"ARCH-{stem_upper}"
                    elif "domains" in norm_parts:
                        effective_target_upper = f"DOM-{stem_upper}"
                if op_val == "deprecate" and not effective_target_upper.startswith(("CAP-", "POL-")):
                    _record_err(
                        f"Invalid composable mutation in {doc.file_path.name}: "
                        f"operation 'deprecate' is only supported for CAP-* and POL-* targets (got '{target_val}')."
                    )
                elif effective_target_upper.startswith("ARCH-") and op_val != "modify":
                    _record_err(
                        f"Invalid composable mutation in {doc.file_path.name}: "
                        f"ARCH-* targets only support operation 'modify' (got '{op_val}')."
                    )
                elif effective_target_upper.startswith("INV-") and op_val == "add":
                    has_parent = bool(str(mut.get("parent") or "").strip())
                    if not has_parent:
                        _record_err(
                            f"Invalid composable mutation in {doc.file_path.name}: "
                            f"adding invariant '{target_val}' requires a 'parent' specification ID."
                        )
        elif dtype == "tech/contract":
            owning_ref = str(fm.get("container") or "").strip()
            if owning_ref:
                ref_upper = owning_ref.upper()
                is_cmp_ref = ref_upper.startswith("CMP-") or (
                    ref_upper.endswith(".ADOC")
                    and (
                        Path(owning_ref).stem.upper().startswith("CMP-")
                        or "components"
                        in [p.lower() for p in Path(owning_ref.replace("\\", "/")).parts]
                    )
                )
                if is_cmp_ref:
                    _record_err(
                        f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Contract '{doc.doc_id}' specifies in-process component '{owning_ref}' as owner. "
                        f"Boundary Protocol Contracts (CTR-*) must be owned by a Container (CNT-*)."
                    )
        elif dtype == "tech/component":
            if doc.file_path.suffix == ".adoc":
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
                    _record_err(
                        f"INV-COMPONENT-CENTRALIZATION-MANDATE violation: Component manifest '{doc.doc_id}' must reside "
                        f"centrally in 'eidos/architecture/components/'. Eidos specifications must never be placed inside source code directories."
                    )

            if fm.get("contracts"):
                _record_err(
                    f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Component '{doc.doc_id}' cannot declare boundary protocol contracts. "
                    f"Use 'interfaces.provides' for in-process APIs."
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
                                        if op not in ("modify", "deprecate") and mk != "parent":
                                            continue
                                        self._check_dict_references(
                                            mv, source_file, root_dir, report, doc_type=doc_type
                                        )
                                else:
                                    self._check_dict_references(
                                        mut, source_file, root_dir, report, doc_type=doc_type
                                    )
                        continue
                self._check_dict_references(v, source_file, root_dir, report, doc_type=doc_type)
        elif isinstance(val, list):
            for item in val:
                self._check_dict_references(item, source_file, root_dir, report, doc_type=doc_type)
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
                tree = ast.parse(source)
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
        """Verify container and component source_path, contract specification_file, UI component test_bindings, and invariant test_binding references when source/test trees exist on disk."""
        spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir)

        candidate_paths: list[str] = []
        candidate_test_files: list[str] = []
        for doc in report.documents:
            fm = doc.frontmatter or {}
            model = doc.model
            if doc.doc_type == "tech/container":
                candidate_paths.extend(model.source_paths())
                for comp in model.components:
                    candidate_paths.extend(comp.source_paths())
            elif doc.doc_type == "tech/component":
                candidate_paths.extend(model.source_paths())
            elif doc.doc_type == "tech/contract":
                sf = model.specification_file
                if isinstance(sf, str) and sf.strip() and not sf.strip().endswith(".adoc"):
                    candidate_paths.append(sf.strip())
            elif doc.doc_type == "design/ui-component":
                tb = fm.get("test_bindings")
                if isinstance(tb, dict):
                    for v in tb.values():
                        if isinstance(v, str) and v.strip():
                            candidate_test_files.append(v.split("::", 1)[0].strip())
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
                            cname.lower() in _code_manifest_files
                            or child.suffix.lower() in AST_DIFF_EXTS
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
                and any(
                    tf and not tf.replace("\\", "/").startswith(("tests/", "test/"))
                    for tf in candidate_test_files
                )
            )
        )

        if not has_source_tree and not has_test_tree:
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
            fm = doc.frontmatter or {}
            model = doc.model
            status = str(model.status or "active").strip().lower()
            if status in inactive_statuses:
                continue

            if has_source_tree:
                if doc.doc_type == "tech/container":
                    for raw_p in model.source_paths():
                        if (
                            raw_p.strip().upper() not in ("N/A", "TBD", "NONE")
                            and self._resolve_source_path(repo_root, root_dir, raw_p) is None
                        ):
                            report.broken_references.append(
                                f"Broken source_path in {doc.file_path.name}: "
                                f"Container '{doc.doc_id}' references '{raw_p}' which does not exist on disk"
                            )
                    for comp in model.components:
                        comp_status = str(comp.status or "active").strip().lower()
                        if comp_status in inactive_statuses:
                            continue
                        comp_id = str(comp.id or "unknown")
                        for raw_sp in comp.source_paths():
                            if (
                                raw_sp.strip().upper() not in ("N/A", "TBD", "NONE")
                                and self._resolve_source_path(repo_root, root_dir, raw_sp) is None
                            ):
                                report.broken_references.append(
                                    f"Broken source_path in {doc.file_path.name}: "
                                    f"Component '{comp_id}' references '{raw_sp}' which does not exist on disk"
                                )
                elif doc.doc_type == "tech/component":
                    for raw_sp in model.source_paths():
                        if (
                            raw_sp.strip().upper() not in ("N/A", "TBD", "NONE")
                            and self._resolve_source_path(repo_root, root_dir, raw_sp) is None
                        ):
                            report.broken_references.append(
                                f"Broken source_path in {doc.file_path.name}: "
                                f"Component '{doc.doc_id}' references '{raw_sp}' which does not exist on disk"
                            )
                elif doc.doc_type == "tech/contract":
                    sf = model.specification_file
                    if isinstance(sf, str) and sf.strip() and sf.strip().upper() not in ("N/A", "TBD", "NONE") and not sf.strip().endswith(".adoc"):
                        resolved_sf = self._resolve_source_path(repo_root, root_dir, sf)
                        if resolved_sf is None:
                            report.broken_references.append(
                                f"Broken specification_file in {doc.file_path.name}: "
                                f"Contract '{doc.doc_id}' references '{sf}' which does not exist on disk"
                            )
                        else:
                            protocol = str(model.protocol or "").strip().lower()
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
                            if not parts:
                                report.broken_references.append(
                                    f"Broken test_bindings.{tb_key} in {doc.file_path.name}: "
                                    f"UI Component '{doc.doc_id}' has invalid test binding '{tb_val}'"
                                )
                                continue
                            if "::" in tb_val and any(not p.strip() for p in tb_val.split("::")):
                                report.broken_references.append(
                                    f"Broken test_bindings.{tb_key} in {doc.file_path.name}: "
                                    f"UI Component '{doc.doc_id}' has Malformed test_binding '{tb_val}' (empty symbol after '::')"
                                )
                                continue
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

                # Collect invariants from top-level, technical.invariants, and active inline components via FrontmatterModel
                invariants_to_check = list(model.invariants)
                if model.technical and model.technical.invariants:
                    invariants_to_check.extend(model.technical.invariants)
                for comp in model.components:
                    comp_status = str(comp.status or "active").strip().lower()
                    if comp_status not in inactive_statuses and comp.invariants:
                        invariants_to_check.extend(comp.invariants)
                for inv in invariants_to_check:
                    inv_id = str(inv.id or "unknown")
                    raw_tb = inv.effective_test_binding()
                    if (
                        not isinstance(raw_tb, str)
                        or not raw_tb.strip()
                        or raw_tb.strip().upper() in ("N/A", "TBD", "NONE")
                    ):
                        continue
                    parts = [p.strip() for p in raw_tb.split("::") if p.strip()]
                    if not parts:
                        report.broken_references.append(
                            f"Broken test_binding in {doc.file_path.name}: "
                            f"Invariant '{inv_id}' has invalid test_binding '{raw_tb}'"
                        )
                        continue
                    if "::" in raw_tb and any(not p.strip() for p in raw_tb.split("::")):
                        report.broken_references.append(
                            f"Broken test_binding in {doc.file_path.name}: "
                            f"Invariant '{inv_id}' has Malformed test_binding '{raw_tb}' (empty symbol after '::')"
                        )
                        continue
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
        spec_root, repo_root = _resolve_spec_and_repo_roots(root_dir)
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

                    child_dicts = {
                        k: v for k, v in node.items() if not str(k).startswith("$") and isinstance(v, dict)
                    }
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

    def _verify_cli_contract_commands(
        self, doc: SpecDocument, cli_file: Path, report: ValidationReport
    ) -> None:
        """Verify bidirectional CLI command parity across Typer (including sub-apps), Click, and argparse entrypoints (DLT-2026-082)."""
        try:
            source = cli_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            return

        source_lower = source.lower()
        has_typer = "typer" in source_lower
        has_click = "click" in source_lower
        has_argparse = "argparse" in source_lower
        if not (has_typer or has_click or has_argparse):
            return

        body = doc.body or ""
        # Preserve DLT-2026-049 compatibility: for non-Typer files (Click/argparse), only enforce CLI command
        # parity when the contract body declares command sections (===), tables (|===), or backtick command literals.
        if not has_typer and not any(tok in body for tok in ("===", "|===", "`")):
            return

        typer_module_aliases: set[str] = set()
        typer_class_aliases: set[str] = set()
        click_module_aliases: set[str] = set()
        click_group_decorators: set[str] = set()
        click_cmd_decorators: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "typer" or alias.name.startswith("typer."):
                        typer_module_aliases.add(alias.asname or alias.name.split(".")[0])
                    elif alias.name == "click" or alias.name.startswith("click."):
                        click_module_aliases.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "typer" or node.module.startswith("typer."):
                    for alias in node.names:
                        if alias.name == "Typer":
                            typer_class_aliases.add(alias.asname or alias.name)
                        elif alias.name == "typer":
                            typer_module_aliases.add(alias.asname or alias.name)
                elif node.module == "click" or node.module.startswith("click."):
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

        commands: set[str] = set()
        subcommands_by_group: dict[str, set[str]] = {}

        sibling_module_aliases: dict[str, str] = {}
        imported_symbol_modules: dict[str, tuple[str, str]] = {}  # local_name -> (module_leaf, orig_name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod_leaf = (node.module or "").rsplit(".", 1)[-1]
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    sibling_module_aliases[local_name] = alias.name
                    if mod_leaf:
                        imported_symbol_modules[local_name] = (mod_leaf, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod_leaf = alias.name.rsplit(".", 1)[-1]
                    sibling_module_aliases[alias.asname or mod_leaf] = mod_leaf

        sibling_trees: dict[str, ast.Module] = {}

        def _get_sibling_tree(mod_leaf: str) -> ast.Module | None:
            if mod_leaf in sibling_trees:
                return sibling_trees[mod_leaf]
            sibling_py = cli_file.parent / f"{mod_leaf}.py"
            if sibling_py.is_file():
                with contextlib.suppress(OSError, SyntaxError):
                    parsed = ast.parse(sibling_py.read_text(encoding="utf-8"))
                    sibling_trees[mod_leaf] = parsed
                    return parsed
            return None

        if typer_module_aliases or typer_class_aliases:
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
                    elif (
                        isinstance(n, ast.AnnAssign)
                        and _is_typer_ctor(n.value)
                        and isinstance(n.target, ast.Name)
                    ):
                        vars_found.add(n.target.id)
                        c_name = _extract_typer_ctor_name(n.value)
                        if c_name:
                            ctor_names[n.target.id] = c_name
                return vars_found, ctor_names

            typer_app_vars, root_ctor_names = _collect_typer_vars(tree)
            if not typer_app_vars:
                return

            def _resolve_sub_typer_name(
                call_node: ast.Call,
                sub_var: str | None,
                ctor_names_map: dict[str, str],
                imp_map: dict[str, tuple[str, str]],
            ) -> str | None:
                sub_name = next(
                    (
                        kw.value.value
                        for kw in call_node.keywords
                        if kw.arg == "name"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
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
                    and isinstance(node.args[0], ast.Name)
                ):
                    sub_var = node.args[0].id
                    sub_name = _resolve_sub_typer_name(
                        node, sub_var, root_ctor_names, imported_symbol_modules
                    )
                    mounted_sub_apps[sub_var] = sub_name

            root_app_vars = (
                typer_app_vars - set(mounted_sub_apps)
                or ({"app"} if "app" in typer_app_vars else typer_app_vars)
            )

            def _collect_commands_from_ast(
                scope_node: ast.AST,
                target_vars: set[str],
                full_module_node: ast.AST | None = None,
                local_imports: dict[str, tuple[str, str]] | None = None,
                _visited: set[tuple[int, str]] | None = None,
            ) -> tuple[set[str], dict[str, set[str]]]:
                found: set[str] = set()
                sub_found: dict[str, set[str]] = {}
                search_root = full_module_node or scope_node
                imp_map = local_imports if local_imports is not None else imported_symbol_modules
                visited = set(_visited) if _visited is not None else set()
                for tv in target_vars:
                    visited.add((id(search_root), tv))
                _, scope_ctor_names = _collect_typer_vars(search_root)
                for node in ast.walk(scope_node):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in target_vars
                        and node.func.attr == "add_typer"
                    ):
                        child_var = (
                            node.args[0].id
                            if node.args and isinstance(node.args[0], ast.Name)
                            else None
                        )
                        sub_name = _resolve_sub_typer_name(
                            node, child_var, scope_ctor_names, imp_map
                        )
                        child_cmds: set[str] = set()
                        grandchild_map: dict[str, set[str]] = {}
                        if child_var:
                            if (id(search_root), child_var) not in visited:
                                c_cmds, gc_map = _collect_commands_from_ast(
                                    search_root,
                                    {child_var},
                                    search_root,
                                    imp_map,
                                    _visited=visited,
                                )
                                child_cmds.update(c_cmds)
                                for gk, gv in gc_map.items():
                                    grandchild_map.setdefault(gk, set()).update(gv)
                            if child_var in imp_map:
                                sib_mod, orig_var = imp_map[child_var]
                                sib_ast = _get_sibling_tree(sib_mod)
                                if sib_ast is not None and (id(sib_ast), orig_var) not in visited:
                                    sib_imp: dict[str, tuple[str, str]] = {}
                                    for sn in ast.walk(sib_ast):
                                        if isinstance(sn, ast.ImportFrom) and sn.module:
                                            sm_leaf = sn.module.rsplit(".", 1)[-1]
                                            for sal in sn.names:
                                                sib_imp[sal.asname or sal.name] = (sm_leaf, sal.name)
                                    ext_cmds, ext_gc = _collect_commands_from_ast(
                                        sib_ast,
                                        {orig_var},
                                        sib_ast,
                                        sib_imp,
                                        _visited=visited,
                                    )
                                    child_cmds.update(ext_cmds)
                                    for gk, gv in ext_gc.items():
                                        grandchild_map.setdefault(gk, set()).update(gv)
                        if sub_name:
                            found.add(sub_name)
                            grp_set = sub_found.setdefault(sub_name, set())
                            grp_set.update(child_cmds)
                            for gc_set in grandchild_map.values():
                                grp_set.update(gc_set)
                        else:
                            # Unnamed add_typer(sub_app) mounts sub_app's commands at the parent level
                            found.update(child_cmds)
                            for gk, gv in grandchild_map.items():
                                sub_found.setdefault(gk, set()).update(gv)
                    elif (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Call)
                        and isinstance(node.func.func, ast.Attribute)
                        and isinstance(node.func.func.value, ast.Name)
                        and node.func.func.value.id in target_vars
                        and node.func.func.attr == "command"
                    ):
                        fn_arg_name = (
                            node.args[0].id
                            if node.args and isinstance(node.args[0], ast.Name)
                            else None
                        )
                        cmd_name = _extract_command_name_from_call(node.func, fn_arg_name)
                        if cmd_name:
                            found.add(cmd_name)
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
                            elif (
                                isinstance(dec, ast.Attribute)
                                and isinstance(dec.value, ast.Name)
                                and dec.value.id in target_vars
                                and dec.attr == "command"
                            ):
                                found.add(node.name.replace("_", "-"))
                return found, sub_found

            commands, subcommands_by_group = _collect_commands_from_ast(tree, root_app_vars, tree)

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
                        sib_imp_map: dict[str, tuple[str, str]] = {}
                        for sn in ast.walk(sib_tree):
                            if isinstance(sn, ast.ImportFrom) and sn.module:
                                sm_leaf = sn.module.rsplit(".", 1)[-1]
                                for sal in sn.names:
                                    sib_imp_map[sal.asname or sal.name] = (sm_leaf, sal.name)
                        for sib_node in sib_tree.body:
                            if (
                                isinstance(sib_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and sib_node.name == "register"
                                and sib_node.args.args
                            ):
                                app_param = sib_node.args.args[0].arg
                                sib_cmds, sib_subs = _collect_commands_from_ast(
                                    sib_node, {app_param}, sib_tree, sib_imp_map
                                )
                                commands.update(sib_cmds)
                                for g_name, g_cmds in sib_subs.items():
                                    subcommands_by_group.setdefault(g_name, set()).update(g_cmds)
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
                                            extra_c, extra_sub = _collect_commands_from_ast(
                                                sib_node, {parent_var}, sib_tree, sib_imp_map
                                            )
                                            grp_s = subcommands_by_group.setdefault(parent_group, set())
                                            grp_s.update(extra_c)
                                            for gc_s in extra_sub.values():
                                                grp_s.update(gc_s)

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
                                        subcommands_by_group.setdefault(
                                            sub_click_groups[parent_grp], set()
                                        ).add(grp_cmd)
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
                            explicit_name
                            or standalone_click_funcs.get(target_var)
                            or target_var.replace("_", "-")
                        )
                        root_click_groups.discard(target_var)
                        sub_click_groups[target_var] = grp_cmd
                        if parent_grp in sub_click_groups:
                            subcommands_by_group.setdefault(
                                sub_click_groups[parent_grp], set()
                            ).add(grp_cmd)
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
                            (
                                isinstance(dec_target, ast.Attribute)
                                and isinstance(dec_target.value, ast.Name)
                                and dec_target.value.id in click_module_aliases
                                and dec_target.attr == "command"
                            )
                            or (isinstance(dec_target, ast.Name) and dec_target.id in click_cmd_decorators)
                        )
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
                                        subcommands_by_group.setdefault(
                                            sub_click_groups[parent_grp], set()
                                        ).add(cmd_name)
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
                            explicit_name
                            or standalone_click_funcs.get(target_var)
                            or target_var.replace("_", "-")
                        )
                        if resolved_name:
                            if target_var in root_click_groups:
                                root_click_groups.discard(target_var)
                                sub_click_groups[target_var] = resolved_name
                            if parent_grp in sub_click_groups:
                                subcommands_by_group.setdefault(
                                    sub_click_groups[parent_grp], set()
                                ).add(resolved_name)
                            else:
                                commands.add(resolved_name)
                    elif explicit_name:
                        if parent_grp in sub_click_groups:
                            subcommands_by_group.setdefault(
                                sub_click_groups[parent_grp], set()
                            ).add(explicit_name)
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

        # 2. Reverse verification (Spec -> Code) & Subcommand verification (DLT-2026-082)
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
                if tok in commands:
                    break

            if resolved_cmd and resolved_cmd not in commands:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented CLI command '{resolved_cmd}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
                )
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
                    bullet_tokens = [
                        t
                        for t in bullet_m.group(1).strip().split()
                        if not t.startswith(("-", "[", "<", "{"))
                        and not any(ch in t for ch in ("*", "|", ".", "/"))
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
                        doc_sub = bullet_tokens[0]
                    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", doc_sub):
                        continue
                    if doc_sub not in expected_subs:
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented CLI subcommand '{resolved_cmd} {doc_sub}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
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

    def _verify_python_protocol_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify bidirectional parity between Python Protocol / ABC classes/methods in specification_file and the contract (DLT-2026-082)."""
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

    def _verify_rest_openapi_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify bidirectional parity between OpenAPI specification_file paths and documented routes in the contract (DLT-2026-082)."""
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
        body = self._strip_asciidoc_comments(doc.body or "")
        defined_routes = {str(k) for k in paths}
        for route in sorted(defined_routes):
            if not re.search(rf"{re.escape(route)}(?=$|[\s`\"'|),;\]>])", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: OpenAPI path '{route}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        seen_doc_routes: set[str] = set()
        for m in re.finditer(
            r"`(?:(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(/[A-Za-z0-9_{}][A-Za-z0-9._~/{}-]*)`",
            body,
        ):
            doc_route = m.group(1)
            if "..." in doc_route or "*" in doc_route:
                continue
            if doc_route not in defined_routes and doc_route not in seen_doc_routes:
                seen_doc_routes.add(doc_route)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented OpenAPI path '{doc_route}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
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
        raw_req = data.get("required")
        req_source = raw_req if isinstance(raw_req, list) else props.keys()
        required_props = list(dict.fromkeys(str(p) for p in req_source if str(p) in props))
        body = self._strip_asciidoc_comments(doc.body or "")
        for prop_name in required_props:
            if not re.search(rf"(?:`|\b){re.escape(prop_name)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: JSON Schema property '{prop_name}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

    def _verify_grpc_proto_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Verify bidirectional parity between gRPC service/rpc declarations in .proto specification_file and the contract (DLT-2026-082)."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        stripped_text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        stripped_text = re.sub(r"//[^\r\n]*", "", stripped_text)
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
        for m in re.finditer(r"(?:`|\b)(?:rpc|service)\s+([A-Za-z_][A-Za-z0-9_]*)\b", body):
            doc_sym = m.group(1)
            if doc_sym not in defined_syms and doc_sym not in seen_doc_syms:
                seen_doc_syms.add(doc_sym)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented gRPC symbol '{doc_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

    def _verify_graphql_contract(
        self, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
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
            if doc_op and doc_op.lower() not in gql_keywords and doc_op not in defined_ops and doc_op not in seen_doc_ops:
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


