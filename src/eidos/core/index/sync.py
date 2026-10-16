"""Incremental sync engine and code ownership resolution (CMP-INCREMENTAL-SYNC-ENGINE).

Provides SHA-256 and mtime dirty checking, spec-order-independent source ownership
attribution, AST symbol persistence, and incremental sync passes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.spec.parser import _parse_spec_content
from eidos.core.workspace.context import collect_component_decisions
from eidos.core.workspace.project import extract_entity_paths, match_path_pattern

_CODE_EXTS = frozenset({".py", ".ts", ".tsx", ".go", ".rs", ".js", ".jsx", ".java", ".kt", ".kts"})

_EXCLUDED_CODE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".eidos",
        "htmlcov",
        "site",
    }
)

_NON_AST_SYMBOL_KINDS = frozenset(
    {
        "specification_file",
        "contract_path",
        "interface_method",
        "interface_signature",
        "component_ref",
        "test_binding",
        "package_path",
        "owned_path",
        "module_name",
        "source_path",
        "component_id",
        "interface_command",
        "inferred_path",
    }
)


def _normalize_rel_path(path: str) -> str:
    norm = path.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


@dataclass
class IndexSyncStats:
    """Statistics from an incremental index sync pass."""

    total_specs_scanned: int = 0
    indexed_new: int = 0
    updated_modified: int = 0
    pruned_deleted: int = 0
    duration_ms: float = 0.0
    was_clean: bool = True
    duplicate_id_collisions: list[str] = field(default_factory=list)


class _CodeOwnershipResolver:
    """Deterministic, spec-order-independent attribution of source files to owning containers/components.

    Built once per sync from the persisted specification state, so every file resolves identically no matter
    which spec was processed first. Precedence (DLT-2026-034):

    1. exact component ``source_path`` declared by any container (lowest container id wins; every other declaring
       entity is kept as a secondary owner, see ``owners()``);
    2. highest-scoring ``owned_path`` pattern (``match_path_pattern``);
    3. longest ``package_path`` prefix (path-boundary aware) across all entries of all containers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._component_sources: dict[str, tuple[str, str]] = {}
        self._declared_by: dict[str, set[str]] = {}
        self._declared_components: dict[tuple[str, str], str] = {}
        for row in conn.execute("SELECT id, data_json FROM entities WHERE type = 'tech/container' ORDER BY id;"):
            try:
                fm = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            for comp in fm.get("components", []) or fm.get("c4_internal_components", []) or []:
                if not isinstance(comp, dict):
                    continue
                raw = comp.get("source_path")
                paths = raw if isinstance(raw, list) else ([raw] if raw else [])
                for sp in paths:
                    norm = _normalize_rel_path(str(sp))
                    if not norm or any(ch in norm for ch in "*?["):
                        continue
                    self._component_sources.setdefault(norm, (row["id"], str(comp.get("id") or "")))
                    self._declared_by.setdefault(norm, set()).add(row["id"])
                    self._declared_components.setdefault((norm, row["id"]), str(comp.get("id") or ""))

        self._owned: list[tuple[str, str]] = [
            (r["symbol"], r["entity_id"])
            for r in conn.execute(
                "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'owned_path' ORDER BY entity_id, symbol;"
            )
        ]
        self._packages: list[tuple[str, str]] = []
        for r in conn.execute(
            "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'package_path' ORDER BY entity_id, symbol;"
        ):
            norm = _normalize_rel_path(r["symbol"])
            if not norm or any(ch in norm for ch in "*?["):
                continue
            self._packages.append((norm, r["entity_id"]))
            if "." in norm.rsplit("/", 1)[-1]:  # literal file declared as package_path
                self._declared_by.setdefault(norm, set()).add(r["entity_id"])

    def package_dirs(self) -> set[str]:
        """All literal ``package_path`` entries (callers filter for existing directories)."""
        return {pp for pp, _ in self._packages}

    def declared_owners(self, rel_path: str) -> set[str]:
        """Entities that explicitly declare ``rel_path`` as a source/package file."""
        return self._declared_by.get(_normalize_rel_path(rel_path), set())

    def declared_files(self) -> set[str]:
        """Every literal source file explicitly declared by any entity."""
        return set(self._declared_by)

    def owners(self, rel_path: str) -> list[tuple[str, str]]:
        """``(entity_id, component_id)`` owners of a file: the resolved primary owner first, then every other entity
        that explicitly declares the file (sorted by id). Empty when the file is unowned."""
        primary, component_id = self.resolve(rel_path)
        if not primary:
            return []
        rel = _normalize_rel_path(rel_path)
        result = [(primary, component_id)]
        for entity_id in sorted(self._declared_by.get(rel, set()) - {primary}):
            result.append((entity_id, self._declared_components.get((rel, entity_id), "")))
        return result

    def resolve(self, rel_path: str) -> tuple[str | None, str]:
        """Return ``(owning entity id or None, component id or '')`` for a repo-relative source path."""
        rel = _normalize_rel_path(rel_path)
        hit = self._component_sources.get(rel)
        if hit:
            return hit

        best_score = 0
        owner: str | None = None
        for pattern, entity_id in self._owned:
            score = match_path_pattern(rel, pattern)
            if score > best_score:
                best_score, owner = score, entity_id
        if owner:
            return owner, ""

        best_len = -1
        for pkg, entity_id in self._packages:
            if (rel == pkg or rel.startswith(pkg + "/")) and len(pkg) > best_len:
                best_len, owner = len(pkg), entity_id
        return owner, ""


_POLYGLOT_TEST_SUFFIXES = (
    "_test.py",
    ".test.ts",
    ".spec.ts",
    ".test.js",
    ".spec.js",
    ".test.tsx",
    ".spec.tsx",
    ".test.jsx",
    ".spec.jsx",
    "_test.go",
    "_test.rs",
    "Test.java",
    "Test.kt",
    "Spec.kt",
)


def is_test_code_path(rel_path: str) -> bool:
    """Return True for test files (by polyglot test naming convention or by a tests/test/__tests__ directory segment)."""
    parts = rel_path.replace("\\", "/").split("/")
    name = parts[-1]
    return (
        name.startswith("test_")
        or any(name.endswith(sfx) for sfx in _POLYGLOT_TEST_SUFFIXES)
        or any(part in ("tests", "test", "__tests__") for part in parts[:-1])
    )


def store_code_symbols(
    conn: sqlite3.Connection,
    rel_path: str,
    owners: list[tuple[str, str]],
    symbols: list[Any],
) -> None:
    """Replace all persisted symbols of one source file (delete-then-insert) and keep lookups consistent."""
    previous = conn.execute(
        "SELECT DISTINCT entity_id, kind, symbol, parent_symbol FROM code_symbols WHERE file_path = ?;",
        (rel_path,),
    ).fetchall()
    conn.execute("DELETE FROM code_symbols WHERE file_path = ?;", (rel_path,))

    for position, (entity_id, component_id) in enumerate(owners):
        for sym in symbols:
            parent = sym.parent_symbol or ""
            conn.execute(
                """
                INSERT OR IGNORE INTO code_symbols
                (file_path, symbol, kind, parent_symbol, line_start, line_end, signature, entity_id,
                 component_id, is_primary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    rel_path,
                    sym.name,
                    sym.kind,
                    parent,
                    sym.line_start,
                    sym.line_end,
                    sym.signature or "",
                    entity_id,
                    component_id,
                    1 if position == 0 else 0,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, ?);",
                (sym.name, entity_id, sym.kind),
            )
            if parent:
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, 'method');",
                    (f"{parent}.{sym.name}", entity_id),
                )

    for old in previous:
        old_entity, old_kind, old_symbol, old_parent = (
            old["entity_id"],
            old["kind"],
            old["symbol"],
            old["parent_symbol"],
        )
        if not conn.execute(
            "SELECT 1 FROM code_symbols WHERE entity_id = ? AND kind = ? AND symbol = ? LIMIT 1;",
            (old_entity, old_kind, old_symbol),
        ).fetchone():
            conn.execute(
                "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = ?;",
                (old_symbol, old_entity, old_kind),
            )
        if (
            old_parent
            and not conn.execute(
                "SELECT 1 FROM code_symbols WHERE entity_id = ? AND parent_symbol = ? AND symbol = ? LIMIT 1;",
                (old_entity, old_parent, old_symbol),
            ).fetchone()
        ):
            conn.execute(
                "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = 'method';",
                (f"{old_parent}.{old_symbol}", old_entity),
            )


def restore_code_mappings(conn: sqlite3.Connection, entity_id: str) -> None:
    """Re-create ``symbol_mappings`` rows for an entity from persisted code state (no source re-parsing)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
        SELECT DISTINCT file_path, entity_id, 'source_path' FROM code_symbols WHERE entity_id = ?
        UNION
        SELECT rel_path, doc_id, 'source_path' FROM indexed_files
        WHERE doc_type = 'code/source' AND doc_id = ?;
        """,
        (entity_id, entity_id),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
        SELECT symbol, entity_id, kind FROM code_symbols WHERE entity_id = ?;
        """,
        (entity_id,),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
        SELECT parent_symbol || '.' || symbol, entity_id, 'method' FROM code_symbols
        WHERE entity_id = ? AND parent_symbol != '';
        """,
        (entity_id,),
    )


def index_source_file(
    conn: sqlite3.Connection,
    ast_idx: Any,
    path: Path,
    rel_path: str,
    mtime: float,
    sha: str,
    resolver: _CodeOwnershipResolver,
) -> None:
    """Index one non-test source file for all its resolved owners (one AST parse)."""
    owners = resolver.owners(rel_path)
    owner_ids = {entity_id for entity_id, _ in owners}
    keep = resolver.declared_owners(rel_path) | owner_ids
    for stale in conn.execute(
        "SELECT entity_id FROM symbol_mappings WHERE symbol = ? AND kind = 'source_path';", (rel_path,)
    ).fetchall():
        if stale["entity_id"] not in keep:
            conn.execute(
                "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = 'source_path';",
                (rel_path, stale["entity_id"]),
            )
    for entity_id in sorted(owner_ids):
        conn.execute(
            "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, 'source_path');",
            (rel_path, entity_id),
        )
    store_code_symbols(conn, rel_path, owners, ast_idx.extract_symbols(path) if owners else [])
    conn.execute(
        """
        INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
        VALUES (?, ?, ?, ?, 'code/source', ?);
        """,
        (rel_path, mtime, sha, owners[0][0] if owners else "source", time.time()),
    )


def reconcile_code_ownership(
    index: Any,
    conn: sqlite3.Connection,
    ast_idx: Any,
    resolver: _CodeOwnershipResolver,
    processed: set[str],
) -> None:
    """Recursively index every container package directory and re-attribute files whose owners changed."""
    candidates: dict[str, Path] = {}
    for pkg in sorted(resolver.package_dirs()):
        abs_pkg = (index.root / pkg).resolve()
        if not abs_pkg.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(abs_pkg)):
            dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_CODE_DIRS and not d.startswith("."))
            for fn in sorted(filenames):
                p_file = Path(dirpath) / fn
                if p_file.suffix not in _CODE_EXTS:
                    continue
                try:
                    rel_code = str(p_file.relative_to(index.root)).replace("\\", "/")
                except ValueError:
                    continue
                candidates.setdefault(rel_code, p_file)

    manifest = {
        r["rel_path"]: r["doc_id"]
        for r in conn.execute("SELECT rel_path, doc_id FROM indexed_files WHERE doc_type = 'code/source';")
    }
    for rel_code in sorted(set(manifest) | resolver.declared_files()):
        if rel_code not in candidates:
            abs_known = (index.root / rel_code).resolve()
            if abs_known.is_file() and abs_known.suffix in _CODE_EXTS:
                candidates[rel_code] = abs_known

    for rel_code, p_file in sorted(candidates.items()):
        if rel_code in processed or is_test_code_path(rel_code):
            continue
        owners = resolver.owners(rel_code)
        if rel_code in manifest and manifest[rel_code] == (owners[0][0] if owners else "source"):
            stored = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT entity_id FROM code_symbols WHERE file_path = ?;", (rel_code,)
                )
            }
            if not stored or stored == {entity_id for entity_id, _ in owners}:
                continue
        stat = p_file.stat()
        index._index_source_file(
            conn,
            ast_idx,
            p_file,
            rel_code,
            stat.st_mtime,
            hashlib.sha256(p_file.read_bytes()).hexdigest(),
            resolver,
        )


def check_index_dirty(index: Any) -> tuple[bool, list[Path], list[Path]]:
    """Perform sub-5ms dirty checking comparing filesystem mtime with indexed state."""
    conn = index._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT rel_path, mtime, sha256 FROM indexed_files;")
    db_files = {row["rel_path"]: (row["mtime"], row["sha256"]) for row in cursor.fetchall()}

    disk_files: set[str] = set()
    to_update: list[Path] = []
    mtime_refreshes: list[tuple[float, float, str]] = []
    now_ts = time.time()

    if not index.eidos_dir.exists():
        deleted = [Path(rel) for rel in db_files]
        return len(deleted) > 0, [], deleted

    root_str = str(index.root)
    root_prefix = root_str if root_str.endswith(("/", "\\")) else root_str + os.sep
    root_len = len(root_prefix)

    def _rel_path(full_str: str) -> str:
        if full_str.startswith(root_prefix):
            return full_str[root_len:].replace("\\", "/")
        try:
            return os.path.relpath(full_str, root_str).replace("\\", "/")
        except ValueError:
            return full_str.replace("\\", "/")

    def _check_file_str(full_str: str, rel_key: str, st_mtime: float) -> None:
        cached = db_files.get(rel_key)
        if not cached:
            to_update.append(Path(full_str))
        elif abs(st_mtime - cached[0]) > 1e-7:
            try:
                with open(full_str, "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                return
            if digest != cached[1]:
                to_update.append(Path(full_str))
            else:
                mtime_refreshes.append((st_mtime, now_ts, rel_key))
                db_files[rel_key] = (st_mtime, cached[1])

    # 1. Living specifications in eidos/ (.adoc)
    for dirpath, dirnames, filenames in os.walk(str(index.eidos_dir)):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "by-status"]
        for fn in filenames:
            if not fn.endswith(".adoc") or fn.startswith("_") or fn.lower() == "readme.adoc":
                continue
            full_path = os.path.join(dirpath, fn)
            rel_p = _rel_path(full_path)
            disk_files.add(rel_p)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue
            _check_file_str(full_path, rel_p, stat.st_mtime)

    # 2. Tracked code and test files
    code_exts = _CODE_EXTS
    tracked_code_dirs: set[str] = set()

    package_paths: set[str] = set()
    cursor.execute("SELECT DISTINCT package_path FROM entities WHERE package_path != '';")
    package_paths.update(row[0].strip() for row in cursor.fetchall())
    cursor.execute(
        "SELECT DISTINCT symbol FROM symbol_mappings WHERE kind IN ('package_path', 'source_path', 'test_binding');"
    )
    package_paths.update(row[0].strip() for row in cursor.fetchall())
    for pp in sorted(package_paths):
        if pp:
            full_pp = os.path.normpath(os.path.join(root_str, pp))
            if os.path.isdir(full_pp):
                tracked_code_dirs.add(full_pp)
            elif os.path.isfile(full_pp):
                _, ext = os.path.splitext(full_pp)
                if ext in code_exts:
                    rel_code = _rel_path(full_pp)
                    disk_files.add(rel_code)
                    try:
                        stat = os.stat(full_pp)
                    except OSError:
                        continue
                    _check_file_str(full_pp, rel_code, stat.st_mtime)

    for std_dir in ("src", "tests", "test"):
        cand = os.path.normpath(os.path.join(root_str, std_dir))
        if os.path.isdir(cand):
            tracked_code_dirs.add(cand)

    for rel_tracked in list(db_files.keys()):
        if not rel_tracked.endswith(".adoc") and rel_tracked not in disk_files:
            abs_tracked = os.path.normpath(os.path.join(root_str, rel_tracked))
            if os.path.isfile(abs_tracked):
                disk_files.add(rel_tracked)
                try:
                    stat = os.stat(abs_tracked)
                except OSError:
                    continue
                _check_file_str(abs_tracked, rel_tracked, stat.st_mtime)

    # Deduplicate child directories whose parent directory is already in tracked_code_dirs
    top_code_dirs: list[str] = []
    for d in sorted(tracked_code_dirs):
        if not any(d == parent or d.startswith(parent + os.sep) for parent in top_code_dirs):
            top_code_dirs.append(d)

    excluded_dirs = _EXCLUDED_CODE_DIRS
    for code_dir in top_code_dirs:
        if not os.path.exists(code_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(code_dir):
            dirnames[:] = [d for d in dirnames if d not in excluded_dirs and not d.startswith(".")]
            for fn in filenames:
                _, ext = os.path.splitext(fn)
                if ext not in code_exts:
                    continue
                full_file = os.path.join(dirpath, fn)
                rel_code = _rel_path(full_file)
                if rel_code in disk_files:
                    continue
                disk_files.add(rel_code)

                try:
                    stat = os.stat(full_file)
                except OSError:
                    continue
                _check_file_str(full_file, rel_code, stat.st_mtime)

    if mtime_refreshes:
        with conn:
            conn.executemany(
                "UPDATE indexed_files SET mtime = ?, last_indexed = ? WHERE rel_path = ?;",
                mtime_refreshes,
            )

    deleted = [Path(rel) for rel in db_files.keys() - disk_files]
    is_dirty = len(to_update) > 0 or len(deleted) > 0
    return is_dirty, to_update, deleted


def rebuild_spec_edges(conn: sqlite3.Connection, updated_doc_ids: set[str] | None = None) -> None:
    """Rebuild specification-declared relational edges from persisted entity frontmatter.

    When ``updated_doc_ids`` is provided on incremental single-spec edits, only non-test edges touching
    ``updated_doc_ids`` (and their child invariants/components) are evicted and rebuilt alongside their
    1-hop neighbors and any previously unresolved edge sources.
    """
    clean_ids = sorted(d for d in (updated_doc_ids or ()) if d)
    if updated_doc_ids is not None and clean_ids:
        ph = ",".join("?" for _ in clean_ids)
        child_rows = conn.execute(
            f"""
            SELECT target_id FROM edges
            WHERE source_id IN ({ph}) AND rel_type IN ('asserts', 'contains_component', 'implemented_by', 'styled_by')
            UNION
            SELECT source_id FROM edges
            WHERE target_id IN ({ph}) AND rel_type IN ('implements_client', 'styles_client')
            UNION
            SELECT id FROM entities
            WHERE type = 'invariant' AND file_path IN (SELECT file_path FROM entities WHERE id IN ({ph}));
            """,
            (*clean_ids, *clean_ids, *clean_ids),
        ).fetchall()
        evict_ids = sorted(set(clean_ids) | {r[0] for r in child_rows if r[0]})
        evict_ph = ",".join("?" for _ in evict_ids)

        neighbor_rows = conn.execute(
            f"""
            SELECT DISTINCT id FROM entities
            WHERE type != 'invariant' AND (
                id IN ({evict_ph})
                OR id IN (SELECT source_id FROM edges WHERE target_id IN ({evict_ph}))
                OR id IN (SELECT target_id FROM edges WHERE source_id IN ({evict_ph}))
                OR id IN (SELECT source_id FROM edges WHERE target_id NOT IN (SELECT id FROM entities))
            );
            """,
            (*evict_ids, *evict_ids, *evict_ids),
        ).fetchall()
        scope_set = {r[0] for r in neighbor_rows} | set(clean_ids) | (set(evict_ids) & {
            r[0] for r in conn.execute("SELECT id FROM entities WHERE type != 'invariant';").fetchall()
        })

        # Also include any existing entity whose frontmatter JSON mentions one of clean_ids (or its file stem),
        # so previously dangling references to a newly created target spec are linked on incremental sync.
        like_clauses = " OR ".join("instr(data_json, ?) > 0" for _ in clean_ids)
        if like_clauses:
            ref_rows = conn.execute(
                f"SELECT id FROM entities WHERE type != 'invariant' AND ({like_clauses});",
                tuple(clean_ids),
            ).fetchall()
            scope_set.update(r[0] for r in ref_rows if r[0])

        # If any design/client or design/system is in scope, include all design/client entities so indirect
        # (CNT, DS, 'styled_by') / (DS, CNT, 'styles_container') edges stay consistent.
        clt_rows = conn.execute("SELECT id FROM entities WHERE type = 'design/client';").fetchall()
        if clt_rows:
            scope_set.update(r[0] for r in clt_rows if r[0])

        scope_ids = sorted(scope_set)
        conn.execute(
            f"""
            DELETE FROM edges
            WHERE (source_id IN ({evict_ph}) OR target_id IN ({evict_ph}))
              AND NOT (
                  (rel_type = 'asserts' AND instr(source_id, '::') > 0)
                  OR (rel_type = 'bound_to' AND instr(target_id, '::') > 0)
              );
            """,
            (*evict_ids, *evict_ids),
        )
        scope_ph = ",".join("?" for _ in scope_ids)
        rows = conn.execute(
            f"SELECT id, type, data_json FROM entities WHERE type != 'invariant' AND id IN ({scope_ph}) ORDER BY id;",
            tuple(scope_ids),
        ).fetchall()
    else:
        conn.execute(
            """
            DELETE FROM edges
            WHERE NOT (
                (rel_type = 'asserts' AND instr(source_id, '::') > 0)
                OR (rel_type = 'bound_to' AND instr(target_id, '::') > 0)
            );
            """
        )
        rows = conn.execute(
            "SELECT id, type, data_json FROM entities WHERE type != 'invariant' ORDER BY id;"
        ).fetchall()
    for row in rows:
        doc_id = row["id"]
        doc_type = row["type"]
        try:
            fm = json.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(fm, dict):
            continue

        domain = fm.get("domain", fm.get("domain_binding", ""))
        if domain:
            conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'realizes', NULL);", (doc_id, domain))
            conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'domain_member', NULL);", (domain, doc_id))

        deps = fm.get("dependencies", {})
        if isinstance(deps, dict):
            dep_list = deps.get("internal_containers", []) or deps.get("internal_components", [])
            for dep in dep_list:
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'depends_on', NULL);", (doc_id, dep))

        contracts = fm.get("contracts", {})
        if isinstance(contracts, dict):
            for prov in contracts.get("provides", []):
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'provides', NULL);", (doc_id, prov))
            for req in contracts.get("requires") or contracts.get("consumes") or []:
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'consumes', NULL);", (doc_id, req))

        if doc_type == "tech/contract":
            owning = fm.get("owning_container") or fm.get("owning_component")
            if owning:
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'provides', NULL);", (owning, doc_id))
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'owned_by', NULL);", (doc_id, owning))

        if doc_type == "design/client":
            cnt_ref = fm.get("container")
            ds_ref = fm.get("design_system")
            if cnt_ref and isinstance(cnt_ref, str):
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implemented_by', NULL);",
                    (doc_id, cnt_ref.strip()),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implements_client', NULL);",
                    (cnt_ref.strip(), doc_id),
                )
            if ds_ref and isinstance(ds_ref, str):
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);", (doc_id, ds_ref.strip())
                )
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_client', NULL);",
                    (ds_ref.strip(), doc_id),
                )
            if cnt_ref and isinstance(cnt_ref, str) and ds_ref and isinstance(ds_ref, str):
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);",
                    (cnt_ref.strip(), ds_ref.strip()),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_container', NULL);",
                    (ds_ref.strip(), cnt_ref.strip()),
                )

        if doc_type == "design/system":
            for clt_ref in fm.get("clients", []) or []:
                if isinstance(clt_ref, str) and clt_ref.strip():
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);",
                        (clt_ref.strip(), doc_id),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_client', NULL);",
                        (doc_id, clt_ref.strip()),
                    )
            inh = fm.get("inheritance")
            if isinstance(inh, dict) and inh.get("extends"):
                parent_ds = str(inh["extends"]).strip()
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'extends', NULL);", (doc_id, parent_ds))
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'extended_by', NULL);", (parent_ds, doc_id))

        biz = fm.get("business", {})
        if isinstance(biz, dict):
            for pol in biz.get("policies", []):
                pol_ref = str(pol).strip()
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'governed_by', NULL);", (doc_id, pol_ref))
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'governs', NULL);", (pol_ref, doc_id))

        tech = fm.get("technical", {})
        invariants = []
        if isinstance(tech, dict):
            tech_targets = list(tech.get("containers", []) or []) + list(tech.get("components", []) or [])
            for comp_ref in tech_targets:
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'implements', NULL);", (doc_id, comp_ref))
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implemented_by', NULL);", (comp_ref, doc_id)
                )
            invariants = tech.get("invariants", []) or []

        if doc_type == "product/policy":
            invariants = fm.get("invariants", []) or []

        if doc_type == "tech/component":
            cnt_id = fm.get("container")
            if cnt_id:
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'contains_component', NULL);", (cnt_id, doc_id)
                )
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'part_of', NULL);", (doc_id, cnt_id))
            ifaces = fm.get("interfaces", {})
            if isinstance(ifaces, dict):
                for consumed in ifaces.get("consumes", []):
                    if isinstance(consumed, str):
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'consumes_api', NULL);",
                            (doc_id, consumed.strip()),
                        )
            invariants = fm.get("invariants", []) or []

        if doc_type == "tech/container":
            invariants = list(fm.get("invariants", []) or [])
            for comp_entry in fm.get("components", []) or fm.get("c4_internal_components", []):
                if not isinstance(comp_entry, dict):
                    continue
                cid = comp_entry.get("id")
                if comp_entry.get("ref") and cid:
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'contains_component', NULL);",
                        (doc_id, cid),
                    )
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'part_of', NULL);", (cid, doc_id))
                for c_inv in comp_entry.get("invariants", []) or []:
                    if isinstance(c_inv, dict) and c_inv.get("id"):
                        invariants.append(c_inv)
                        if cid:
                            c_iid = c_inv["id"]
                            conn.execute(
                                "INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);",
                                (cid, c_iid),
                            )
                            conn.execute(
                                "INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);",
                                (c_iid, cid),
                            )

        for inv in invariants:
            iid = inv.get("id") if isinstance(inv, dict) else None
            if iid:
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);", (doc_id, iid))
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);", (iid, doc_id))


def index_test_bindings(conn: sqlite3.Connection, ast_idx: Any, test_path: Path, rel_test: str) -> None:
    """Persist 'file::func' -> INV-* test-binding edges for a single test file (DLT-2026-029)."""
    prefix_like = rel_test.replace("%", "\\%").replace("_", "\\_") + "::%"
    conn.execute(
        "DELETE FROM edges WHERE rel_type = 'asserts' AND source_id LIKE ? ESCAPE '\\';",
        (prefix_like,),
    )
    conn.execute(
        "DELETE FROM edges WHERE rel_type = 'bound_to' AND target_id LIKE ? ESCAPE '\\';",
        (prefix_like,),
    )
    conn.execute(
        "DELETE FROM symbol_mappings WHERE symbol = ? AND kind = 'test_binding';",
        (rel_test,),
    )
    for binding in ast_idx.extract_test_bindings(test_path):
        identifier = f"{rel_test}::{binding.test_name}"
        for inv_id in binding.invariants:
            conn.execute(
                "INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);",
                (identifier, inv_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);",
                (inv_id, identifier),
            )
            conn.execute(
                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                (rel_test, inv_id),
            )
            inv_row = conn.execute("SELECT data_json FROM entities WHERE id = ?", (inv_id,)).fetchone()
            if inv_row:
                idata = json.loads(inv_row["data_json"])
                idata["has_test_coverage"] = True
                if identifier not in idata.get("matching_tests", []):
                    idata.setdefault("matching_tests", []).append(identifier)
                conn.execute(
                    "UPDATE entities SET data_json = ? WHERE id = ?",
                    (json.dumps(idata), inv_id),
                )


def prune_stale_test_binding_edges(conn: sqlite3.Connection) -> None:
    """Drop test-binding edges whose invariant entity or test file no longer exists."""
    conn.execute(
        """
        DELETE FROM edges
        WHERE rel_type = 'asserts'
          AND source_id NOT IN (SELECT id FROM entities)
          AND source_id NOT IN (SELECT symbol FROM symbol_mappings WHERE kind = 'component_id')
          AND (
            target_id NOT IN (SELECT id FROM entities)
            OR instr(source_id, '::') = 0
            OR substr(source_id, 1, instr(source_id, '::') - 1) NOT IN (
                SELECT rel_path FROM indexed_files WHERE doc_type = 'code/test'
            )
          );
        """
    )
    conn.execute(
        """
        DELETE FROM edges
        WHERE rel_type = 'bound_to'
          AND target_id NOT IN (SELECT id FROM entities)
          AND target_id NOT IN (SELECT symbol FROM symbol_mappings WHERE kind = 'component_id')
          AND (
            source_id NOT IN (SELECT id FROM entities)
            OR instr(target_id, '::') = 0
            OR substr(target_id, 1, instr(target_id, '::') - 1) NOT IN (
                SELECT rel_path FROM indexed_files WHERE doc_type = 'code/test'
            )
          );
        """
    )


def delete_spec_fts_rows(conn: sqlite3.Connection, doc_id: str, rel_path: str) -> None:
    """Delete the FTS rows of a specification and of every child entity (e.g. ``INV-*``) declared in its file."""
    conn.execute(
        "DELETE FROM specs_fts WHERE entity_id = ? OR entity_id IN (SELECT id FROM entities WHERE file_path = ?);",
        (doc_id, rel_path),
    )


def _reconcile_invariant_coverage(
    index: Any,
    conn: sqlite3.Connection,
    ast_idx: Any,
) -> None:
    """Reconcile invariant entity has_test_coverage and matching_tests against AST-verified test bindings."""
    inv_rows = conn.execute(
        "SELECT id, data_json FROM entities WHERE type = 'invariant';"
    ).fetchall()
    if not inv_rows:
        return

    assert_edges: dict[str, list[str]] = {}
    for source_id, target_id in conn.execute(
        "SELECT source_id, target_id FROM edges WHERE rel_type = 'asserts' AND instr(source_id, '::') > 0;"
    ).fetchall():
        assert_edges.setdefault(target_id, []).append(source_id)

    test_funcs_cache: dict[str, dict[str, str]] = {}
    test_content_cache: dict[str, str] = {}

    for row in inv_rows:
        iid = row["id"]
        try:
            inv_data = json.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(inv_data, dict):
            continue

        ast_matches = list(dict.fromkeys(assert_edges.get(iid, [])))
        test = str(inv_data.get("test_binding") or "").strip()

        if test:
            parts = [p.strip() for p in test.split("::") if p.strip()]
            tb_file = parts[0].replace("\\", "/") if parts else ""
            tb_func = "::".join(parts[1:]) if len(parts) > 1 else ""
            abs_tb = (index.root / tb_file) if tb_file else None
            tb_verified = False

            if tb_func:
                target_ref = f"{tb_file}::{tb_func}"
                if target_ref in ast_matches:
                    tb_verified = True
                elif abs_tb is not None and abs_tb.is_file():
                    if tb_file not in test_funcs_cache:
                        test_funcs_cache[tb_file] = ast_idx.list_test_functions(abs_tb)
                    funcs = test_funcs_cache[tb_file]
                    candidate_bodies: list[str] = []
                    if tb_func in funcs:
                        candidate_bodies.append(funcs[tb_func])
                    elif "::" not in tb_func:
                        suffix = f"::{parts[-1]}"
                        candidate_bodies.extend(
                            body for k, body in funcs.items() if k == parts[-1] or k.endswith(suffix)
                        )
                    if candidate_bodies:
                        pat = re.compile(rf"\b{re.escape(iid)}\b")
                        tb_verified = any(pat.search(body) for body in candidate_bodies)
                    if tb_verified:
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);",
                            (target_ref, iid),
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);",
                            (iid, target_ref),
                        )
                matching = list(dict.fromkeys([target_ref, *ast_matches])) if tb_verified else []
                has_coverage = tb_verified
            else:
                if any(m.startswith(f"{tb_file}::") for m in ast_matches):
                    tb_verified = True
                elif abs_tb is not None and abs_tb.is_file():
                    if tb_file not in test_content_cache:
                        try:
                            test_content_cache[tb_file] = abs_tb.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            test_content_cache[tb_file] = ""
                    pat = re.compile(rf"\b{re.escape(iid)}\b")
                    tb_verified = bool(pat.search(test_content_cache[tb_file]))
                matching = list(dict.fromkeys([tb_file, *ast_matches])) if tb_verified else []
                has_coverage = tb_verified
        else:
            matching = ast_matches
            has_coverage = bool(matching)

        if inv_data.get("has_test_coverage") != has_coverage or inv_data.get("matching_tests") != matching:
            inv_data["has_test_coverage"] = has_coverage
            inv_data["matching_tests"] = matching
            conn.execute(
                "UPDATE entities SET data_json = ? WHERE id = ?;",
                (json.dumps(inv_data), iid),
            )


def sync_index(
    index: Any,
    force: bool = False,
    dirty_state: tuple[bool, list[Path], list[Path]] | None = None,
    strict: bool = False,
) -> IndexSyncStats:
    """Incrementally synchronize the index with specifications and code on disk."""
    from eidos.core.index.query import compound_identifier_subtokens

    t0 = time.perf_counter()
    stats = IndexSyncStats()

    if force:
        conn = index._get_connection()
        with conn:
            conn.execute("DELETE FROM indexed_files;")
            conn.execute("DELETE FROM entities;")
            conn.execute("DELETE FROM edges;")
            conn.execute("DELETE FROM symbol_mappings;")
            conn.execute("DELETE FROM code_symbols;")
            conn.execute("DELETE FROM specs_fts;")

    if force or dirty_state is None:
        dirty, to_update, deleted = index.is_dirty()
    else:
        dirty, to_update, deleted = dirty_state
    if not dirty and not force:
        stats.duration_ms = (time.perf_counter() - t0) * 1000
        stats.was_clean = True
        index._last_synced_check = time.monotonic()
        return stats

    stats.was_clean = False
    conn = index._get_connection()

    with conn:
        specs_deleted = False
        code_deleted = False
        for d in deleted:
            d_rel = str(d).replace("\\", "/")
            row = conn.execute("SELECT doc_id, doc_type FROM indexed_files WHERE rel_path = ?", (d_rel,)).fetchone()
            if row and row["doc_type"] in ("code/source", "code/test"):
                code_deleted = True
                index._store_code_symbols(conn, d_rel, [], [])
                conn.execute("DELETE FROM symbol_mappings WHERE symbol = ?;", (d_rel,))
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
            elif row and row["doc_id"]:
                doc_id = row["doc_id"]
                specs_deleted = True
                index._delete_spec_fts_rows(conn, doc_id, d_rel)
                conn.execute(
                    "DELETE FROM symbol_mappings WHERE entity_id = ? OR entity_id IN "
                    "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant')",
                    (doc_id, d_rel),
                )
                conn.execute(
                    "DELETE FROM edges WHERE source_id = ? OR target_id = ? "
                    "OR source_id IN (SELECT id FROM entities WHERE file_path = ? AND type = 'invariant') "
                    "OR target_id IN (SELECT id FROM entities WHERE file_path = ? AND type = 'invariant')",
                    (doc_id, doc_id, d_rel, d_rel),
                )
                conn.execute(
                    "DELETE FROM entities WHERE id = ? OR (file_path = ? AND type = 'invariant')",
                    (doc_id, d_rel),
                )
                conn.execute("DELETE FROM code_symbols WHERE entity_id = ?", (doc_id,))
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
            else:
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
            stats.pruned_deleted += 1

        spec_updates = [p for p in to_update if p.suffix == ".adoc"]
        code_updates = [p for p in to_update if p.suffix != ".adoc"]

        stats.total_specs_scanned = len(spec_updates)
        parsed_specs: list[tuple[Path, str, float, str, Any]] = []
        for p in spec_updates:
            try:
                rel_p = str(p.relative_to(index.root)).replace("\\", "/")
            except ValueError:
                rel_p = str(p).replace("\\", "/")

            try:
                mtime = p.stat().st_mtime
                raw_bytes = p.read_bytes()
                doc = _parse_spec_content(p, raw_bytes.decode("utf-8", errors="replace"))
            except OSError:
                continue
            sha = hashlib.sha256(raw_bytes).hexdigest()
            parsed_specs.append((p, rel_p, mtime, sha, doc))

        updated_rel_paths: set[str] = set()
        updated_spec_doc_ids: set[str] = set()
        any_doc_id_renamed = False
        for _p, rel_p, _mtime, _sha, doc in parsed_specs:
            new_doc_id = doc.doc_id or _p.stem
            updated_spec_doc_ids.add(new_doc_id)
            existing_row = conn.execute(
                "SELECT doc_id FROM indexed_files WHERE rel_path = ?;",
                (rel_p,),
            ).fetchone()
            if existing_row is not None:
                updated_rel_paths.add(rel_p)
                old_doc_id = existing_row["doc_id"]
                if old_doc_id:
                    updated_spec_doc_ids.add(old_doc_id)
                    index._delete_spec_fts_rows(conn, old_doc_id, rel_p)
                    conn.execute(
                        "DELETE FROM symbol_mappings WHERE entity_id = ? OR entity_id IN "
                        "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant');",
                        (old_doc_id, rel_p),
                    )
                    conn.execute(
                        "DELETE FROM entities WHERE (id = ? AND file_path = ?) OR (file_path = ? AND type = 'invariant');",
                        (old_doc_id, rel_p, rel_p),
                    )
                    if old_doc_id != new_doc_id:
                        any_doc_id_renamed = True
                        conn.execute("DELETE FROM code_symbols WHERE entity_id = ?;", (old_doc_id,))
                        conn.execute(
                            "DELETE FROM edges WHERE source_id = ? OR target_id = ?;",
                            (old_doc_id, old_doc_id),
                        )
                if new_doc_id != old_doc_id:
                    any_doc_id_renamed = True
                    index._delete_spec_fts_rows(conn, new_doc_id, rel_p)
                    conn.execute(
                        "DELETE FROM symbol_mappings WHERE entity_id = ? OR entity_id IN "
                        "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant');",
                        (new_doc_id, rel_p),
                    )
                    conn.execute(
                        "DELETE FROM entities WHERE (id = ? AND file_path = ?) OR (file_path = ? AND type = 'invariant');",
                        (new_doc_id, rel_p, rel_p),
                    )

        for p, rel_p, mtime, sha, doc in parsed_specs:
            doc_id = doc.doc_id or p.stem
            fm = doc.frontmatter or {}
            doc_type = doc.doc_type or "unknown"

            if rel_p in updated_rel_paths:
                stats.updated_modified += 1
            else:
                stats.indexed_new += 1

            collision = conn.execute(
                "SELECT file_path FROM entities WHERE id = ? AND file_path != ?;",
                (doc_id, rel_p),
            ).fetchone()
            if collision is not None:
                msg = (
                    f"Duplicate entity ID '{doc_id}' in '{rel_p}' "
                    f"(already indexed from '{collision['file_path']}')"
                )
                stats.duplicate_id_collisions.append(msg)
                if strict:
                    raise ValueError(msg)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (rel_p, mtime, sha, doc_id, doc_type, time.time()),
                )
                continue

            title = doc.title or fm.get("title", doc_id)
            status = fm.get("status", "active")
            domain = fm.get("domain", fm.get("domain_binding", ""))
            raw_pkg = fm.get("package_path", fm.get("source_path", ""))
            pkg_list = (
                [str(x).strip() for x in raw_pkg if str(x).strip()]
                if isinstance(raw_pkg, list)
                else ([str(raw_pkg).strip()] if str(raw_pkg).strip() else [])
            )
            package_path = pkg_list[0] if pkg_list else ""
            runtime = fm.get("runtime", "")
            container_type = fm.get("container_type", "")

            conn.execute(
                """
                INSERT INTO entities
                (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    doc_id,
                    doc_type,
                    title,
                    status,
                    domain,
                    package_path,
                    runtime,
                    container_type,
                    rel_p,
                    json.dumps(fm),
                ),
            )

            if doc_type == "tech/contract":
                spec_file = fm.get("specification_file")
                if spec_file:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'specification_file');",
                        (spec_file, doc_id),
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'contract_path');", (rel_p, doc_id)
                )

            biz = fm.get("business", {})
            tech = fm.get("technical", {})
            invariants = []
            if isinstance(tech, dict):
                invariants = tech.get("invariants", []) or []

            if doc_type == "product/policy":
                invariants = fm.get("invariants", []) or []

            if doc_type == "tech/component":
                ifaces = fm.get("interfaces", {})
                if isinstance(ifaces, dict):
                    for prov in ifaces.get("provides", []):
                        if isinstance(prov, dict) and prov.get("signature"):
                            sig = prov["signature"]
                            meth = sig.split("(")[0].strip()
                            conn.execute(
                                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_method');",
                                (meth, doc_id),
                            )
                            conn.execute(
                                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_signature');",
                                (sig, doc_id),
                            )
                invariants = fm.get("invariants", []) or []

            if doc_type == "tech/container":
                invariants = list(fm.get("invariants", []) or [])
                for comp_entry in fm.get("components", []) or fm.get("c4_internal_components", []):
                    if not isinstance(comp_entry, dict):
                        continue
                    cid = comp_entry.get("id")
                    if comp_entry.get("ref") and cid:
                        ref_path = comp_entry["ref"]
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'component_ref');",
                            (ref_path, cid),
                        )
                    for c_inv in comp_entry.get("invariants", []) or []:
                        if isinstance(c_inv, dict) and c_inv.get("id"):
                            inv_copy = dict(c_inv)
                            if cid:
                                inv_copy["_inline_component_id"] = cid
                            invariants.append(inv_copy)

            for inv in invariants:
                if not isinstance(inv, dict):
                    continue
                iid = inv.get("id")
                if iid:
                    inv_collision = conn.execute(
                        "SELECT file_path FROM entities WHERE id = ? AND file_path != ?;",
                        (iid, rel_p),
                    ).fetchone()
                    if inv_collision is not None:
                        inv_msg = (
                            f"Duplicate invariant ID '{iid}' in '{rel_p}' "
                            f"(already indexed from '{inv_collision['file_path']}')"
                        )
                        stats.duplicate_id_collisions.append(inv_msg)
                        if strict:
                            raise ValueError(inv_msg)
                        continue
                    rule = inv.get("rule", "")
                    test = inv.get("test_binding") or inv.get("verification") or ""
                    test_file = test.split("::")[0] if test else ""
                    inline_comp_id = inv.get("_inline_component_id")
                    inv_data = {
                        "id": iid,
                        "rule": rule,
                        "test_binding": test,
                        "source_id": doc_id,
                        "capability_id": doc_id if doc_type == "product/capability" else None,
                        "policy_id": doc_id if doc_type == "product/policy" else None,
                        "container_id": (
                            doc_id
                            if doc_type == "tech/container"
                            else (fm.get("container") if doc_type == "tech/component" else None)
                        ),
                        "component_id": doc_id if doc_type == "tech/component" else inline_comp_id,
                        "has_test_coverage": False,
                        "matching_tests": [],
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO entities
                        (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                        VALUES (?, 'invariant', ?, 'active', ?, '', '', '', ?, ?);
                        """,
                        (iid, rule, domain, rel_p, json.dumps(inv_data)),
                    )

                    test_str = str(test or "")
                    rule_str = str(rule or "")
                    test_func = test_str.split("::", 1)[1] if "::" in test_str else ""
                    conn.execute("DELETE FROM specs_fts WHERE entity_id = ?", (iid,))
                    conn.execute(
                        """
                        INSERT INTO specs_fts (entity_id, title, jtbd_or_rule, body_text, keywords, symbols)
                        VALUES (?, '', ?, ?, ?, ?);
                        """,
                        (
                            iid,
                            rule_str,
                            str(title or ""),
                            " ".join(str(x) for x in (iid, "invariant", domain, doc_id, inline_comp_id) if x),
                            " ".join(
                                x for x in (test_str, compound_identifier_subtokens(f"{test_func} {rule_str}")) if x
                            ),
                        ),
                    )

                    if test:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                            (test_file, iid),
                        )

            for pkg_entry in pkg_list:
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'package_path');", (pkg_entry, doc_id)
                )
            for owned_pat in extract_entity_paths(fm):
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'owned_path');", (owned_pat, doc_id)
                )

            all_modules = list(fm.get("components", []) or fm.get("c4_internal_components", []))
            for module in all_modules:
                mname = module.get("name")
                raw_mpath = module.get("source_path")
                mpath_list = (
                    [str(x).strip() for x in raw_mpath if str(x).strip()]
                    if isinstance(raw_mpath, list)
                    else ([str(raw_mpath).strip()] if raw_mpath and str(raw_mpath).strip() else [])
                )
                mid = module.get("id")
                if mname:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'module_name');", (mname, doc_id)
                    )
                for mpath in mpath_list:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'source_path');", (mpath, doc_id)
                    )
                if mid:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'component_id');", (mid, doc_id)
                    )

            for pkg_item in pkg_list:
                if any(ch in pkg_item for ch in "*?[") or not (index.root / pkg_item).is_file():
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'source_path');", (pkg_item, doc_id)
                )

            index._restore_code_mappings(conn, doc_id)

            if isinstance(fm.get("contracts"), dict):
                for iface in fm["contracts"].get("interfaces", []):
                    cmd = iface.split()[0]
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_command');", (cmd, doc_id)
                    )

            jtbd_or_rule = biz.get("jtbd", fm.get("rule", "")) if isinstance(biz, dict) else fm.get("rule", "")
            body_clean = re.sub(r"[{}\[\]=:#*`]", " ", doc.body or "")
            embedded_decisions = collect_component_decisions(fm)
            decision_ids: list[str] = []
            decision_texts: list[str] = []
            for dec in embedded_decisions:
                did = str(dec.get("id", "")).strip()
                if did:
                    decision_ids.append(did)
                grad_ref = str(dec.get("graduated_from", "")).strip()
                if grad_ref:
                    decision_ids.append(grad_ref)
                for field_key in ("title", "decision", "rationale"):
                    fval = str(dec.get(field_key, "")).strip()
                    if fval:
                        decision_texts.append(fval)
                for alt in dec.get("rejected_alternatives") or []:
                    if isinstance(alt, dict):
                        for alt_key in ("option", "reason_rejected"):
                            aval = str(alt.get(alt_key, "")).strip()
                            if aval:
                                decision_texts.append(aval)
            if decision_texts:
                body_clean = f"{body_clean} " + re.sub(r"[{}\[\]=:#*`]", " ", " ".join(decision_texts))
            keywords = " ".join([doc_id, doc_type, domain, container_type, package_path, *decision_ids])
            symbol_parts: list[str] = [
                str(m.get("name", ""))
                for m in (fm.get("components", []) or fm.get("c4_internal_components", []))
                if isinstance(m, dict) and m.get("name")
            ]
            symbol_parts.extend(
                str(m.get("id", ""))
                for m in (fm.get("components", []) or fm.get("c4_internal_components", []))
                if isinstance(m, dict) and m.get("id")
            )
            if doc_type == "tech/component" and isinstance(fm.get("interfaces"), dict):
                for sig in (fm["interfaces"].get("provides") or []) + (fm["interfaces"].get("consumes") or []):
                    if isinstance(sig, str) and sig.strip():
                        symbol_parts.append(sig.split("(")[0].strip())
            symbols = " ".join(symbol_parts)
            decompounded = compound_identifier_subtokens(
                f"{title or ''} {jtbd_or_rule or ''} {body_clean} {symbols}"
            )
            if decompounded:
                symbols = f"{symbols} {decompounded}".strip()

            conn.execute(
                """
                INSERT INTO specs_fts (entity_id, title, jtbd_or_rule, body_text, keywords, symbols)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (doc_id, title, jtbd_or_rule, body_clean, keywords, symbols),
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (rel_p, mtime, sha, doc_id, doc_type, time.time()),
            )

        if spec_updates or specs_deleted:
            inc_ids = (
                updated_spec_doc_ids
                if (not force and not specs_deleted and not any_doc_id_renamed and updated_spec_doc_ids)
                else None
            )
            index._rebuild_spec_edges(conn, updated_doc_ids=inc_ids)
            conn.execute("DELETE FROM symbol_mappings WHERE kind = 'inferred_path';")
            client_cnt_rows = conn.execute(
                "SELECT source_id, target_id FROM edges WHERE rel_type = 'implemented_by' AND source_id LIKE 'CLT-%';"
            ).fetchall()
            for clt_id, cnt_id in client_cnt_rows:
                cnt_paths = [
                    r[0]
                    for r in conn.execute(
                        """
                        SELECT DISTINCT symbol FROM symbol_mappings
                        WHERE kind IN ('owned_path', 'package_path', 'source_path')
                          AND (entity_id = ? OR entity_id IN (
                              SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'contains_component'
                          ));
                        """,
                        (cnt_id, cnt_id),
                    ).fetchall()
                ]
                if not cnt_paths:
                    continue
                for pat in cnt_paths:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'inferred_path');",
                        (pat, clt_id),
                    )
                ds_rows = conn.execute(
                    """
                    WITH RECURSIVE ds_tree(ds_id) AS (
                        SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'styled_by'
                        UNION
                        SELECT e.target_id FROM edges e JOIN ds_tree d ON e.source_id = d.ds_id
                        WHERE e.rel_type IN ('extends', 'extended_by')
                    )
                    SELECT DISTINCT ds_id FROM ds_tree;
                    """,
                    (clt_id,),
                ).fetchall()
                for (ds_id,) in ds_rows:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);", (cnt_id, ds_id))
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_container', NULL);", (ds_id, cnt_id)
                    )
                    for pat in cnt_paths:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'inferred_path');",
                            (pat, ds_id),
                        )

        ast_idx = get_ast_indexer()
        resolver = _CodeOwnershipResolver(conn)
        processed_code: set[str] = set()
        for p in code_updates:
            if not p.is_file():
                continue
            try:
                rel_p = str(p.relative_to(index.root)).replace("\\", "/")
            except ValueError:
                rel_p = str(p).replace("\\", "/")

            mtime = p.stat().st_mtime
            sha = hashlib.sha256(p.read_bytes()).hexdigest()

            is_test = index._is_test_code_path(rel_p)

            existing = conn.execute("SELECT 1 FROM indexed_files WHERE rel_path = ?", (rel_p,)).fetchone()
            if existing:
                stats.updated_modified += 1
            else:
                stats.indexed_new += 1

            if is_test:
                index._index_test_bindings(conn, ast_idx, p, rel_p)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                    VALUES (?, ?, ?, 'tests', 'code/test', ?);
                    """,
                    (rel_p, mtime, sha, time.time()),
                )
            else:
                index._index_source_file(conn, ast_idx, p, rel_p, mtime, sha, resolver)
                processed_code.add(rel_p)

        if force or spec_updates or specs_deleted:
            index._reconcile_code_ownership(conn, ast_idx, resolver, processed_code)

        if force or spec_updates or specs_deleted or processed_code or code_deleted:
            _project_ast_code_symbols_into_fts(conn)

        candidate_test_files: dict[str, Path] = {}
        for std_test_dir in ("tests", "test", *sorted(resolver.package_dirs())):
            tests_dir = (index.root / std_test_dir).resolve()
            if tests_dir.is_dir():
                for dirpath, dirnames, filenames in os.walk(str(tests_dir)):
                    dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_CODE_DIRS and not d.startswith(".")]
                    for fn in filenames:
                        tp = Path(dirpath) / fn
                        if tp.suffix not in _CODE_EXTS:
                            continue
                        try:
                            rel_t = str(tp.relative_to(index.root)).replace("\\", "/")
                        except ValueError:
                            rel_t = str(tp).replace("\\", "/")
                        if index._is_test_code_path(rel_t):
                            candidate_test_files.setdefault(rel_t, tp)

        for (tb_sym,) in conn.execute(
            "SELECT DISTINCT symbol FROM symbol_mappings WHERE kind = 'test_binding';"
        ).fetchall():
            if not tb_sym:
                continue
            abs_tb = (index.root / str(tb_sym).strip()).resolve()
            if abs_tb.is_file() and abs_tb.suffix in _CODE_EXTS:
                try:
                    rel_tb = str(abs_tb.relative_to(index.root)).replace("\\", "/")
                except ValueError:
                    rel_tb = str(abs_tb).replace("\\", "/")
                candidate_test_files.setdefault(rel_tb, abs_tb)

        for rel_t, tp in sorted(candidate_test_files.items()):
            cached_entry = conn.execute(
                "SELECT mtime, sha256 FROM indexed_files WHERE rel_path = ? AND doc_type = 'code/test';",
                (rel_t,),
            ).fetchone()
            stat = tp.stat()
            if not cached_entry or abs(stat.st_mtime - cached_entry["mtime"]) > 1e-7 or force or spec_updates:
                t_sha = hashlib.sha256(tp.read_bytes()).hexdigest()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                    VALUES (?, ?, ?, 'tests', 'code/test', ?);
                    """,
                    (rel_t, stat.st_mtime, t_sha, time.time()),
                )
                index._index_test_bindings(conn, ast_idx, tp, rel_t)

        conn.execute(
            """
            UPDATE OR IGNORE edges
            SET target_id = (
                SELECT doc_id FROM indexed_files
                WHERE rel_path = edges.target_id
                   OR 'eidos/' || rel_path = edges.target_id
                   OR rel_path = 'eidos/' || edges.target_id
                   OR rel_path LIKE '%/' || edges.target_id
                ORDER BY length(rel_path) ASC
                LIMIT 1
            )
            WHERE target_id NOT IN (SELECT id FROM entities)
              AND rel_type != 'bound_to';
            """
        )
        conn.execute(
            """
            DELETE FROM edges
            WHERE target_id NOT IN (SELECT id FROM entities)
              AND rel_type != 'bound_to';
            """
        )
        conn.execute(
            """
            UPDATE OR IGNORE edges
            SET source_id = (
                SELECT doc_id FROM indexed_files
                WHERE rel_path = edges.source_id
                   OR 'eidos/' || rel_path = edges.source_id
                   OR rel_path = 'eidos/' || edges.source_id
                   OR rel_path LIKE '%/' || edges.source_id
                ORDER BY length(rel_path) ASC
                LIMIT 1
            )
            WHERE source_id NOT IN (SELECT id FROM entities)
              AND rel_type != 'asserts';
            """
        )
        conn.execute(
            """
            DELETE FROM edges
            WHERE source_id NOT IN (SELECT id FROM entities)
              AND rel_type != 'asserts';
            """
        )
        index._prune_stale_test_binding_edges(conn)
        _reconcile_invariant_coverage(index, conn, ast_idx)

    stats.duration_ms = (time.perf_counter() - t0) * 1000
    index._last_synced_check = time.monotonic()
    return stats


def _project_ast_code_symbols_into_fts(conn: sqlite3.Connection) -> None:
    """Deterministically project primary AST code_symbols (class, function, interface, struct, enum) into specs_fts.symbols."""
    from eidos.core.index.query import compound_identifier_subtokens

    by_entity: dict[str, list[str]] = {}
    for row in conn.execute(
        """
        SELECT DISTINCT entity_id, symbol FROM code_symbols
        WHERE is_primary = 1 AND kind IN ('class', 'function', 'interface', 'struct', 'enum')
        ORDER BY entity_id, symbol;
        """
    ).fetchall():
        by_entity.setdefault(row[0], []).append(row[1])

    spec_rows = conn.execute(
        """
        SELECT f.rowid, f.entity_id, f.title, f.jtbd_or_rule, f.body_text, f.symbols, e.type, e.data_json
        FROM specs_fts f
        JOIN entities e ON e.id = f.entity_id
        WHERE e.type != 'invariant';
        """
    ).fetchall()

    for srow in spec_rows:
        rowid = srow[0]
        entity_id = srow[1]
        title = str(srow[2] or "")
        jtbd_or_rule = str(srow[3] or "")
        body_clean = str(srow[4] or "")
        cur_symbols = str(srow[5] or "")
        doc_type = str(srow[6] or "")
        try:
            fm = json.loads(srow[7] or "{}")
        except (TypeError, ValueError):
            fm = {}
        if not isinstance(fm, dict):
            fm = {}

        symbol_parts: list[str] = [
            str(m.get("name", ""))
            for m in (fm.get("components", []) or fm.get("c4_internal_components", []))
            if isinstance(m, dict) and m.get("name")
        ]
        symbol_parts.extend(
            str(m.get("id", ""))
            for m in (fm.get("components", []) or fm.get("c4_internal_components", []))
            if isinstance(m, dict) and m.get("id")
        )
        if doc_type == "tech/component" and isinstance(fm.get("interfaces"), dict):
            for sig in (fm["interfaces"].get("provides") or []) + (fm["interfaces"].get("consumes") or []):
                if isinstance(sig, str) and sig.strip():
                    symbol_parts.append(sig.split("(")[0].strip())
        base_symbols = " ".join(symbol_parts)
        decompounded = compound_identifier_subtokens(
            f"{title} {jtbd_or_rule} {body_clean} {base_symbols}"
        )
        if decompounded:
            base_symbols = f"{base_symbols} {decompounded}".strip()

        existing_tokens = set(base_symbols.split())
        new_parts: list[str] = []
        for sym in by_entity.get(entity_id, ()):
            if sym and sym not in existing_tokens:
                new_parts.append(sym)
                existing_tokens.add(sym)
            sub = compound_identifier_subtokens(sym)
            if sub:
                for st in sub.split():
                    if st not in existing_tokens:
                        new_parts.append(st)
                        existing_tokens.add(st)

        recomputed = f"{base_symbols} {' '.join(new_parts)}".strip()
        if recomputed != cur_symbols:
            conn.execute("UPDATE specs_fts SET symbols = ? WHERE rowid = ?;", (recomputed, rowid))
