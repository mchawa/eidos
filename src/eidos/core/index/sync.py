"""Incremental sync engine and serialized WAL synchronization coordinator (CMP-INCREMENTAL-SYNC-ENGINE)."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.index.code_graph import (
    _CODE_EXTS,
    _CodeOwnershipResolver,
    _derive_ast_import_edges,
    _normalize_rel_path,
    _upsert_file_tokens_fts,
)
from eidos.core.index.code_graph import (
    _FILE_TOKEN_RE as _FILE_TOKEN_RE,
)
from eidos.core.index.code_graph import (
    _SOURCE_AST_MEM_CACHE as _SOURCE_AST_MEM_CACHE,
)
from eidos.core.index.code_graph import (
    _TEST_BINDING_MEM_CACHE as _TEST_BINDING_MEM_CACHE,
)
from eidos.core.index.code_graph import (
    index_source_file as index_source_file,
)
from eidos.core.index.code_graph import (
    restore_code_mappings as restore_code_mappings,
)
from eidos.core.index.code_graph import (
    store_code_symbols as store_code_symbols,
)
from eidos.core.index.fts_query import compound_identifier_subtokens, expand_architectural_synonyms
from eidos.core.index.spec_edges import (
    _project_ast_code_symbols_into_fts,
    _reconcile_invariant_coverage,
)
from eidos.core.index.spec_edges import (
    delete_spec_fts_rows as delete_spec_fts_rows,
)
from eidos.core.index.spec_edges import (
    index_test_bindings as index_test_bindings,
)
from eidos.core.index.spec_edges import (
    prune_stale_test_binding_edges as prune_stale_test_binding_edges,
)
from eidos.core.index.spec_edges import (
    rebuild_spec_edges as rebuild_spec_edges,
)
from eidos.core.index.storage import (
    _advisory_db_lock,
    _immediate_write_transaction,
    _resolve_conn_db_path,
    get_cached_spec_document,
    upsert_edge,
)
from eidos.core.index.storage import (
    build_spec_path_resolver as build_spec_path_resolver,
)
from eidos.core.workspace.project import (
    STANDARD_EXCLUDED_DIRS,
    _is_test_dir_segment,
    discover_nested_subproject_roots,
    extract_entity_paths,
    is_dotnet_bin_dir,
    is_polyglot_test_file,
)

_GOVERNED_TEXT_EXTS = _CODE_EXTS | frozenset({".md", ".toml", ".json", ".yaml", ".yml"})

_BINARY_OR_ARCHIVE_EXTS = frozenset(
    {
        ".pyc",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".lock",
        ".png",
        ".jpg",
        ".jpeg",
        ".ico",
        ".wasm",
        ".gz",
        ".tar",
        ".zip",
        ".tgz",
    }
)

_EXCLUDED_FILENAMES = frozenset(
    {
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "Cargo.lock",
        "uv.lock",
        "poetry.lock",
        "composer.lock",
        "Gemfile.lock",
    }
)


_ASCIIDOC_ATTR_LINE_RE = re.compile(r"^:[A-Za-z0-9_-]+:.*$", re.MULTILINE)


def _json_dumps(obj: Any) -> str:
    return orjson.dumps(obj, default=str, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")


def _is_governed_source_file(path_str: str) -> bool:
    """Return True if ``path_str`` has a governed source/text extension and is not a lockfile or archive."""
    slash = path_str.rfind("/")
    if "\\" in path_str:
        slash = max(slash, path_str.rfind("\\"))
    base = path_str[slash + 1 :] if slash >= 0 else path_str
    if not base or base[0] == "." or base in _EXCLUDED_FILENAMES:
        return False
    dot = base.rfind(".")
    if dot <= 0:
        return False
    ext = base[dot:].lower()
    if ext not in _GOVERNED_TEXT_EXTS:
        return False
    if base.find(".") == dot:
        return True
    parts = base.lower().split(".")[1:-1]
    return not any(f".{p}" in _BINARY_OR_ARCHIVE_EXTS for p in parts)


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


def reconcile_code_ownership(
    index: Any,
    conn: sqlite3.Connection,
    ast_idx: Any,
    resolver: _CodeOwnershipResolver,
    processed: set[str],
) -> None:
    """Recursively index every container package directory and re-attribute files whose owners changed."""
    nested_roots = set(discover_nested_subproject_roots(index.root))
    nested_prefixes: tuple[str, ...] = tuple(
        f"{nr.relative_to(index.root).as_posix().strip('/')}/" for nr in nested_roots if nr.is_relative_to(index.root)
    )
    candidates: dict[str, Path] = {}
    pkg_dirs = resolver.package_dirs()
    for pkg in sorted(pkg_dirs):
        abs_pkg = (index.root / pkg).resolve()
        if not abs_pkg.is_dir() or abs_pkg in nested_roots:
            continue
        for dirpath, dirnames, filenames in os.walk(str(abs_pkg)):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in STANDARD_EXCLUDED_DIRS
                and not d.startswith(".")
                and (Path(dirpath) / d).resolve() not in nested_roots
                and not is_dotnet_bin_dir(Path(dirpath) / d)
            )
            for fn in sorted(filenames):
                p_file = Path(dirpath) / fn
                if not _is_governed_source_file(fn):
                    continue
                try:
                    rel_code = str(p_file.relative_to(index.root)).replace("\\", "/")
                except ValueError:
                    continue
                if nested_prefixes and rel_code.startswith(nested_prefixes):
                    continue
                candidates.setdefault(rel_code, p_file)

    manifest = {
        r["rel_path"]: r["doc_id"]
        for r in conn.execute("SELECT rel_path, doc_id FROM indexed_files WHERE doc_type = 'code/source';")
    }
    spec_files = {
        _normalize_rel_path(r[0])
        for r in conn.execute("SELECT DISTINCT symbol FROM symbol_mappings WHERE kind = 'specification_file';")
        if r[0] and not any(ch in str(r[0]) for ch in "*?[")
    }
    for rel_code in sorted(set(manifest) | resolver.declared_files() | spec_files):
        if nested_prefixes and rel_code.startswith(nested_prefixes):
            continue
        if rel_code not in candidates:
            abs_known = (index.root / rel_code).resolve()
            if abs_known.is_file() and _is_governed_source_file(abs_known.name):
                candidates[rel_code] = abs_known

    for rel_code, p_file in sorted(candidates.items()):
        if rel_code in processed:
            continue
        if is_polyglot_test_file(rel_code, repo_root=index.root, package_paths=pkg_dirs):
            if rel_code in manifest:
                store_code_symbols(conn, rel_code, [], [])
                conn.execute("DELETE FROM symbol_mappings WHERE symbol = ? AND kind = 'source_path';", (rel_code,))
                stat = p_file.stat()
                raw_bytes = p_file.read_bytes()
                t_sha = hashlib.sha256(raw_bytes).hexdigest()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type)
                    VALUES (?, ?, ?, 'tests', 'code/test');
                    """,
                    (rel_code, stat.st_mtime, t_sha),
                )
                index_test_bindings(
                    conn, ast_idx, p_file, rel_code, sha=t_sha, raw_bytes=raw_bytes, mtime_ns=stat.st_mtime_ns
                )
            continue
        owners = resolver.owners(rel_code)
        if rel_code in manifest and manifest[rel_code] == (owners[0] if owners else "source"):
            stored = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT entity_id FROM code_symbols WHERE file_path = ? AND is_test = 0;", (rel_code,)
                )
            }
            if not stored or stored == set(owners):
                continue
        stat = p_file.stat()
        raw_bytes = p_file.read_bytes()
        index_source_file(
            conn,
            ast_idx,
            p_file,
            rel_code,
            stat.st_mtime,
            hashlib.sha256(raw_bytes).hexdigest(),
            resolver,
            raw_bytes=raw_bytes,
        )


def _discover_sibling_test_dirs(
    root: Path,
    candidate_dirs: list[Path],
    nested_dir_strs: set[str],
) -> list[str]:
    """Discover root and sibling test directories (e.g. ``*.Tests``, ``*.UnitTests``) near tracked package roots."""
    resolved_root = root.resolve()
    parent_dirs: list[Path] = [resolved_root]
    seen_parents: set[Path] = {resolved_root}
    for cand in candidate_dirs:
        parent = cand.resolve().parent
        if parent not in seen_parents and (parent == resolved_root or parent.is_relative_to(resolved_root)):
            seen_parents.add(parent)
            parent_dirs.append(parent)

    discovered: list[str] = []
    seen_dirs: set[str] = set()
    for pdir in parent_dirs:
        if not pdir.is_dir():
            continue
        with contextlib.suppress(OSError):
            for entry in os.scandir(pdir):
                if entry.name.startswith(".") or entry.name in STANDARD_EXCLUDED_DIRS:
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                norm_dir = os.path.normpath(entry.path)
                if norm_dir in nested_dir_strs or norm_dir in seen_dirs:
                    continue
                if _is_test_dir_segment(entry.name, allow_root_names=True):
                    seen_dirs.add(norm_dir)
                    discovered.append(norm_dir)
    return discovered


def check_index_dirty(index: Any) -> tuple[bool, list[Path], list[Path]]:
    """Perform sub-5ms dirty checking comparing filesystem mtime with indexed state."""
    conn = index._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT rel_path, mtime, sha256 FROM indexed_files;")
    db_files = {row["rel_path"]: (row["mtime"], row["sha256"]) for row in cursor.fetchall()}

    disk_files: set[str] = set()
    to_update: list[Path] = []
    mtime_refreshes: list[tuple[float, str]] = []

    if not index.eidos_dir.exists():
        deleted = [Path(rel) for rel in db_files]
        return len(deleted) > 0, [], deleted

    root_str = str(index.root)
    root_prefix = root_str if root_str.endswith(("/", "\\")) else root_str + os.sep
    root_len = len(root_prefix)
    nested_roots = set(discover_nested_subproject_roots(index.root))
    nested_dir_strs = {os.path.normpath(str(nr)) for nr in nested_roots}
    nested_prefixes: tuple[str, ...] = tuple(
        f"{nr.relative_to(index.root).as_posix().strip('/')}/" for nr in nested_roots if nr.is_relative_to(index.root)
    )

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
                mtime_refreshes.append((st_mtime, rel_key))
                db_files[rel_key] = (st_mtime, cached[1])

    # 1. Living specifications in eidos/ (.adoc)
    def _scan_eidos_dir(dir_path: str) -> None:
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            _scan_eidos_dir(entry.path)
                            continue
                    except OSError:
                        continue
                    if not name.endswith(".adoc") or name.startswith("_") or name.lower() == "readme.adoc":
                        continue
                    full_path = entry.path
                    rel_p = _rel_path(full_path)
                    disk_files.add(rel_p)
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    _check_file_str(full_path, rel_p, st.st_mtime)
        except OSError:
            return

    _scan_eidos_dir(str(index.eidos_dir))

    # 2. Tracked code, test, and governed text files
    excluded_dirs = STANDARD_EXCLUDED_DIRS
    tracked_code_dirs: set[str] = set()
    explicit_code_files: list[str] = []

    for std_dir in ("src", "tests", "test"):
        cand = os.path.normpath(os.path.join(root_str, std_dir))
        if os.path.isdir(cand) and cand not in nested_dir_strs:
            tracked_code_dirs.add(cand)

    package_paths: set[str] = set()
    cursor.execute(
        "SELECT DISTINCT symbol FROM symbol_mappings WHERE kind IN ('package_path', 'source_path', 'owned_path', 'test_binding', 'specification_file');"
    )
    package_paths.update(row[0].strip() for row in cursor.fetchall())
    for raw_pp in sorted(package_paths):
        if not raw_pp:
            continue
        pp = raw_pp.replace("\\", "/")
        if any(ch in pp for ch in "*?["):
            prefix_parts = []
            for part in pp.split("/"):
                if any(ch in part for ch in "*?["):
                    break
                if part:
                    prefix_parts.append(part)
            pp = "/".join(prefix_parts)
        if pp:
            full_pp = os.path.normpath(os.path.join(root_str, pp))
            if full_pp in nested_dir_strs:
                continue
            if os.path.isdir(full_pp):
                tracked_code_dirs.add(full_pp)
            else:
                explicit_code_files.append(full_pp)

    for sibling_test_dir in _discover_sibling_test_dirs(
        index.root,
        [Path(d) for d in sorted(tracked_code_dirs)],
        nested_dir_strs,
    ):
        tracked_code_dirs.add(sibling_test_dir)

    # Deduplicate child directories whose parent directory is already in tracked_code_dirs
    top_code_dirs: list[str] = []
    for d in sorted(tracked_code_dirs):
        if not any(d == parent or d.startswith(parent + os.sep) for parent in top_code_dirs):
            top_code_dirs.append(d)

    top_code_prefixes = tuple(d + os.sep for d in top_code_dirs if os.path.isdir(d))

    def _is_covered_by_top_code_dirs(abs_path: str) -> bool:
        if not _is_governed_source_file(abs_path):
            return False
        for pref in top_code_prefixes:
            if abs_path.startswith(pref):
                sub_parts = abs_path[len(pref) :].split(os.sep)[:-1]
                if not any(part in excluded_dirs or part.startswith(".") for part in sub_parts):
                    return True
        return False

    def _scan_code_dir(dir_path: str) -> None:
        if os.path.normpath(dir_path) in nested_dir_strs:
            return
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith(".") or name in excluded_dirs:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if os.path.normpath(entry.path) in nested_dir_strs or is_dotnet_bin_dir(Path(entry.path)):
                                continue
                            _scan_code_dir(entry.path)
                            continue
                    except OSError:
                        continue
                    if not _is_governed_source_file(name):
                        continue
                    full_file = entry.path
                    rel_code = _rel_path(full_file)
                    if nested_prefixes and rel_code.startswith(nested_prefixes):
                        continue
                    if rel_code in disk_files:
                        continue
                    disk_files.add(rel_code)
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    _check_file_str(full_file, rel_code, st.st_mtime)
        except OSError:
            return

    for code_dir in top_code_dirs:
        if os.path.isdir(code_dir):
            _scan_code_dir(code_dir)

    for full_pp in explicit_code_files:
        rel_code = _rel_path(full_pp)
        if nested_prefixes and rel_code.startswith(nested_prefixes):
            continue
        if rel_code in disk_files:
            continue
        if not _is_covered_by_top_code_dirs(full_pp) and _is_governed_source_file(full_pp) and os.path.isfile(full_pp):
            disk_files.add(rel_code)
            try:
                stat = os.stat(full_pp)
            except OSError:
                continue
            _check_file_str(full_pp, rel_code, stat.st_mtime)

    for rel_tracked in db_files:
        if nested_prefixes and rel_tracked.startswith(nested_prefixes):
            continue
        if not rel_tracked.endswith(".adoc") and rel_tracked not in disk_files:
            abs_tracked = os.path.normpath(os.path.join(root_str, rel_tracked))
            if _is_covered_by_top_code_dirs(abs_tracked):
                continue
            if os.path.isfile(abs_tracked):
                disk_files.add(rel_tracked)
                try:
                    stat = os.stat(abs_tracked)
                except OSError:
                    continue
                _check_file_str(abs_tracked, rel_tracked, stat.st_mtime)

    if mtime_refreshes:
        lock_db = _resolve_conn_db_path(conn, getattr(index, "db_path", None))
        with _advisory_db_lock(lock_db, non_blocking=True) as acquired:
            if acquired:
                with contextlib.suppress(sqlite3.OperationalError), _immediate_write_transaction(conn):
                    conn.executemany(
                        "UPDATE indexed_files SET mtime = ? WHERE rel_path = ?;",
                        mtime_refreshes,
                    )

    to_update.sort()
    deleted = sorted(Path(rel) for rel in db_files.keys() - disk_files)
    needs_test_imports = False
    if not to_update and not deleted and db_files:
        ti_row = cursor.execute("SELECT value FROM metadata WHERE key = 'test_imports_indexed';").fetchone()
        if ti_row is None or ti_row[0] != "1":
            has_tests = cursor.execute("SELECT 1 FROM indexed_files WHERE doc_type = 'code/test' LIMIT 1;").fetchone()
            needs_test_imports = has_tests is not None
    is_dirty = len(to_update) > 0 or len(deleted) > 0 or needs_test_imports
    return is_dirty, to_update, deleted


def sync_index(
    index: Any,
    force: bool = False,
    dirty_state: tuple[bool, list[Path], list[Path]] | None = None,
    strict: bool = False,
    *,
    auto_semantic: bool = True,
    _pre_data_version: int | None = None,
) -> IndexSyncStats:
    """Incrementally synchronize the index with specifications and code on disk."""
    t0 = time.perf_counter()
    stats = IndexSyncStats()
    conn = index._get_connection()
    lock_db = _resolve_conn_db_path(conn, getattr(index, "db_path", None))

    spec_updates: list[Path] = []
    with _advisory_db_lock(lock_db, non_blocking=True) as got_immediate_lock:
        contended = not got_immediate_lock
        with _advisory_db_lock(lock_db, non_blocking=False, notify_wait=contended):
            spec_updates = _sync_index_locked(
                index,
                conn,
                stats,
                force=force,
                dirty_state=dirty_state,
                strict=strict,
                contended=contended,
                _pre_data_version=_pre_data_version,
            )

    if auto_semantic and spec_updates:
        from eidos.core.index.semantic import _trigger_auto_semantic_refresh

        _trigger_auto_semantic_refresh(conn, index.eidos_dir, index.db_path)

    stats.duration_ms = (time.perf_counter() - t0) * 1000
    index._last_synced_check = time.monotonic()
    return stats


def _sync_index_locked(
    index: Any,
    conn: sqlite3.Connection,
    stats: IndexSyncStats,
    *,
    force: bool = False,
    dirty_state: tuple[bool, list[Path], list[Path]] | None = None,
    strict: bool = False,
    contended: bool = False,
    _pre_data_version: int | None = None,
) -> list[Path]:
    """Execute one serialized sync pass while holding ``index.db.lock``."""
    # imported at top-level from fts_query

    if force:
        with _immediate_write_transaction(conn):
            conn.execute("DELETE FROM indexed_files;")
            conn.execute("DELETE FROM entities;")
            conn.execute("DELETE FROM edges;")
            conn.execute("DELETE FROM code_couplings;")
            conn.execute("DELETE FROM symbol_mappings;")
            conn.execute("DELETE FROM code_symbols;")
            conn.execute("DELETE FROM specs_fts;")
            conn.execute("DELETE FROM file_tokens_fts;")
            conn.execute("DELETE FROM memory_freshness_cache;")
            # Content-addressed caches (spec_validation_cache, test_binding_cache, spec_embeddings)
            # are intentionally preserved across force rebuilds; orphaned spec_embeddings rows are
            # pruned after entity reconciliation.

    if force or dirty_state is None or contended:
        dirty, to_update, deleted = index.is_dirty()
    elif _pre_data_version is not None:
        post_dv_row = conn.execute("PRAGMA data_version;").fetchone()
        post_dv = int(post_dv_row[0]) if post_dv_row else _pre_data_version
        if post_dv != _pre_data_version:
            dirty, to_update, deleted = index.is_dirty()
        else:
            dirty, to_update, deleted = dirty_state
    else:
        dirty, to_update, deleted = dirty_state
    if not dirty and not force:
        stats.was_clean = True
        return []

    stats.was_clean = False

    with _immediate_write_transaction(conn):
        specs_deleted = False
        code_deleted = False
        for d in deleted:
            d_rel = str(d).replace("\\", "/")
            row = conn.execute("SELECT doc_id, doc_type FROM indexed_files WHERE rel_path = ?", (d_rel,)).fetchone()
            conn.execute("DELETE FROM file_tokens_fts WHERE rel_path = ?;", (d_rel,))
            if row and row["doc_type"] in ("code/source", "code/test"):
                code_deleted = True
                store_code_symbols(conn, d_rel, [], [])
                conn.execute("DELETE FROM symbol_mappings WHERE symbol = ?;", (d_rel,))
                conn.execute("DELETE FROM test_binding_cache WHERE file_path = ?;", (d_rel,))
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
            elif row and row["doc_id"]:
                doc_id = row["doc_id"]
                specs_deleted = True
                other_owner = conn.execute(
                    "SELECT 1 FROM entities WHERE id = ? AND file_path != ?;",
                    (doc_id, d_rel),
                ).fetchone()
                if other_owner is None:
                    delete_spec_fts_rows(conn, doc_id, d_rel)
                    conn.execute(
                        "DELETE FROM edges WHERE source_id = ? OR target_id = ? "
                        "OR source_id IN (SELECT id FROM entities WHERE file_path = ? AND type = 'invariant') "
                        "OR target_id IN (SELECT id FROM entities WHERE file_path = ? AND type = 'invariant')",
                        (doc_id, doc_id, d_rel, d_rel),
                    )
                    conn.execute(
                        "DELETE FROM symbol_mappings WHERE entity_id = ? OR entity_id IN "
                        "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant')",
                        (doc_id, d_rel),
                    )
                    conn.execute(
                        "DELETE FROM entities WHERE id = ? OR (file_path = ? AND type = 'invariant')",
                        (doc_id, d_rel),
                    )
                    conn.execute("DELETE FROM code_symbols WHERE entity_id = ?", (doc_id,))
                    conn.execute("DELETE FROM memory_freshness_cache WHERE memory_id = ?;", (doc_id,))
                    surviving_rows = conn.execute(
                        "SELECT rel_path FROM indexed_files WHERE doc_id = ? AND rel_path != ? ORDER BY rel_path;",
                        (doc_id, d_rel),
                    ).fetchall()
                    for (surv_rel,) in surviving_rows:
                        surv_path = index.root / surv_rel
                        if surv_path.is_file() and surv_path not in to_update:
                            to_update.append(surv_path)
                else:
                    conn.execute(
                        "DELETE FROM specs_fts WHERE entity_id IN (SELECT id FROM entities WHERE file_path = ?);",
                        (d_rel,),
                    )
                    conn.execute(
                        "DELETE FROM symbol_mappings WHERE entity_id IN "
                        "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant');",
                        (d_rel,),
                    )
                    conn.execute(
                        "DELETE FROM entities WHERE file_path = ?;",
                        (d_rel,),
                    )
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
            else:
                conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
            stats.pruned_deleted += 1

        spec_updates = [p for p in to_update if p.suffix == ".adoc"]
        code_updates = [p for p in to_update if p.suffix != ".adoc"]

        stats.total_specs_scanned = len(spec_updates)
        cached_rows: dict[str, sqlite3.Row] = {}
        if spec_updates:
            try:
                for row in conn.execute("SELECT file_path, mtime_ns, sha256, doc_json FROM spec_validation_cache"):
                    cached_rows[row["file_path"]] = row
            except sqlite3.Error:
                pass
        parsed_specs: list[tuple[Path, str, float, str, Any]] = []
        for p in spec_updates:
            try:
                rel_p = str(p.relative_to(index.root)).replace("\\", "/")
            except ValueError:
                rel_p = str(p).replace("\\", "/")

            try:
                st = p.stat()
                mtime = st.st_mtime
            except OSError:
                continue
            doc, sha, _ = get_cached_spec_document(
                p,
                mtime_ns=st.st_mtime_ns,
                st_size=st.st_size,
                cached_row=cached_rows.get(rel_p),
            )
            if doc is None or not sha:
                continue
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
                    other_old_owner = conn.execute(
                        "SELECT 1 FROM entities WHERE id = ? AND file_path != ?;",
                        (old_doc_id, rel_p),
                    ).fetchone()
                    if other_old_owner is None:
                        delete_spec_fts_rows(conn, old_doc_id, rel_p)
                        conn.execute(
                            "DELETE FROM symbol_mappings WHERE entity_id = ? OR entity_id IN "
                            "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant');",
                            (old_doc_id, rel_p),
                        )
                        conn.execute(
                            "DELETE FROM entities WHERE (id = ? AND file_path = ?) OR (file_path = ? AND type = 'invariant');",
                            (old_doc_id, rel_p, rel_p),
                        )
                        conn.execute("DELETE FROM memory_freshness_cache WHERE memory_id = ?;", (old_doc_id,))
                        if old_doc_id != new_doc_id:
                            any_doc_id_renamed = True
                            conn.execute("DELETE FROM code_symbols WHERE entity_id = ?;", (old_doc_id,))
                            conn.execute(
                                "DELETE FROM edges WHERE source_id = ? OR target_id = ?;",
                                (old_doc_id, old_doc_id),
                            )
                    else:
                        conn.execute(
                            "DELETE FROM specs_fts WHERE entity_id IN (SELECT id FROM entities WHERE file_path = ?);",
                            (rel_p,),
                        )
                        conn.execute(
                            "DELETE FROM symbol_mappings WHERE entity_id IN "
                            "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant');",
                            (rel_p,),
                        )
                        conn.execute(
                            "DELETE FROM entities WHERE file_path = ?;",
                            (rel_p,),
                        )
                if new_doc_id != old_doc_id:
                    any_doc_id_renamed = True
                    other_new_owner = conn.execute(
                        "SELECT 1 FROM entities WHERE id = ? AND file_path != ?;",
                        (new_doc_id, rel_p),
                    ).fetchone()
                    if other_new_owner is None:
                        delete_spec_fts_rows(conn, new_doc_id, rel_p)
                        conn.execute(
                            "DELETE FROM symbol_mappings WHERE entity_id = ? OR entity_id IN "
                            "(SELECT id FROM entities WHERE file_path = ? AND type = 'invariant');",
                            (new_doc_id, rel_p),
                        )
                        conn.execute(
                            "DELETE FROM entities WHERE (id = ? AND file_path = ?) OR (file_path = ? AND type = 'invariant');",
                            (new_doc_id, rel_p, rel_p),
                        )
                        conn.execute("DELETE FROM memory_freshness_cache WHERE memory_id = ?;", (new_doc_id,))

        fresh_base_symbols: dict[str, str] = {}
        for p, rel_p, mtime, sha, doc in parsed_specs:
            doc_id = doc.doc_id or p.stem
            fm = doc.frontmatter or {}
            model = doc.model
            doc_type = doc.doc_type or "unknown"

            if rel_p in updated_rel_paths:
                stats.updated_modified += 1
            else:
                stats.indexed_new += 1

            try:
                spec_raw_text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                spec_raw_text = f"{_json_dumps(fm)}\n{doc.title or ''}\n{doc.body or ''}"

            collision = conn.execute(
                "SELECT file_path FROM entities WHERE id = ? AND file_path != ?;",
                (doc_id, rel_p),
            ).fetchone()
            if collision is not None:
                msg = f"Duplicate entity ID '{doc_id}' in '{rel_p}' (already indexed from '{collision['file_path']}')"
                stats.duplicate_id_collisions.append(msg)
                if strict:
                    raise ValueError(msg)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (rel_p, mtime, sha, doc_id, doc_type),
                )
                _upsert_file_tokens_fts(conn, rel_p, doc_type, spec_raw_text)
                continue

            title = doc.title or model.title or doc_id
            status = model.status or "active"
            domain = model.effective_domain()
            pkg_list = model.source_paths()
            source_path = pkg_list[0] if pkg_list else ""
            container_type = model.container_type or ""

            conn.execute(
                """
                INSERT INTO entities
                (id, type, title, status, domain, file_path, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    doc_id,
                    doc_type,
                    title,
                    status,
                    domain,
                    rel_p,
                    _json_dumps(fm),
                ),
            )

            if doc_type == "tech/contract":
                spec_file = model.specification_file
                if spec_file:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'specification_file');",
                        (spec_file, doc_id),
                    )
                conn.execute("INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'contract_path');", (rel_p, doc_id))

            invariants_to_index = []
            if (
                doc_type
                in ("product/capability", "product/domain", "product/policy", "tech/component", "tech/container")
                or model.invariants
                or model.technical.invariants
            ):
                invariants_to_index = model.all_invariants()

            for inv_model in invariants_to_index:
                iid = inv_model.id
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
                    rule = inv_model.rule or ""
                    test = inv_model.effective_test_binding()
                    test_file = test.split("::")[0] if test else ""
                    inv_data = {
                        "id": iid,
                        "rule": rule,
                        "test_binding": test,
                        "source_id": doc_id,
                        "source_doc_type": doc_type,
                        "category": fm.get("category") if doc_type == "product/policy" else None,
                        "severity": fm.get("severity") if doc_type == "product/policy" else None,
                        "capability_id": doc_id if doc_type == "product/capability" else None,
                        "policy_id": doc_id if doc_type == "product/policy" else None,
                        "container_id": (
                            doc_id
                            if doc_type == "tech/container"
                            else (model.container if doc_type == "tech/component" else None)
                        ),
                        "component_id": doc_id if doc_type == "tech/component" else None,
                        "has_test_coverage": False,
                        "matching_tests": [],
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO entities
                        (id, type, title, status, domain, file_path, data_json)
                        VALUES (?, 'invariant', ?, 'active', ?, ?, ?);
                        """,
                        (iid, rule, domain, rel_p, _json_dumps(inv_data)),
                    )

                    test_str = str(test or "")
                    rule_str = str(rule or "")
                    test_func = test_str.split("::", 1)[1] if "::" in test_str else ""
                    inv_synonyms = expand_architectural_synonyms(rule_str)
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
                            " ".join(str(x) for x in (iid, "invariant", domain, doc_id, inv_synonyms) if x),
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
                conn.execute("INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'owned_path');", (owned_pat, doc_id))

            for pkg_item in pkg_list:
                if any(ch in pkg_item for ch in "*?[") or not (index.root / pkg_item).is_file():
                    continue
                conn.execute("INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'source_path');", (pkg_item, doc_id))

            restore_code_mappings(conn, doc_id)

            jtbd_parts: list[str] = []
            if doc_type == "product/policy":
                jtbd_parts.extend(inv.rule for inv in model.invariants if inv.rule)
            if doc_type == "agent/memory":
                mem_summary = str(fm.get("summary") or "").strip()
                if mem_summary:
                    jtbd_parts.append(mem_summary)
            if model.intent and model.intent.motivation:
                jtbd_parts.append(model.intent.motivation)
            jtbd_or_rule = " ".join(jtbd_parts)
            body_no_attrs = _ASCIIDOC_ATTR_LINE_RE.sub(" ", doc.body or "")
            body_clean = re.sub(r"[{}\[\]=:#*`]", " ", body_no_attrs)
            decision_ids: list[str] = []
            decision_texts: list[str] = []
            for dec in model.all_decisions():
                did = str(dec.id or "").strip()
                if did:
                    decision_ids.append(did)
                grad_ref = str(dec.graduated_from or "").strip()
                if grad_ref:
                    decision_ids.append(grad_ref)
                for fval in (dec.title, dec.decision, dec.rationale):
                    if fval and str(fval).strip():
                        decision_texts.append(str(fval).strip())
                for alt in dec.rejected_alternatives:
                    for aval in (alt.option, alt.reason_rejected):
                        if aval and str(aval).strip():
                            decision_texts.append(str(aval).strip())
            if decision_texts:
                body_clean = f"{body_clean} " + re.sub(r"[{}\[\]=:#*`]", " ", " ".join(decision_texts))
            mem_keyword_parts: list[str] = []
            if doc_type == "agent/memory":
                mem_cat = str(fm.get("category") or "").strip()
                if mem_cat:
                    mem_keyword_parts.append(mem_cat)
                mem_targets = fm.get("targets") if isinstance(fm.get("targets"), dict) else {}
                raw_ent_t = mem_targets.get("entities")
                for ent_t in raw_ent_t if isinstance(raw_ent_t, list) else []:
                    if isinstance(ent_t, str) and ent_t.strip():
                        mem_keyword_parts.append(ent_t.strip())
                raw_path_t = mem_targets.get("paths")
                for path_t in raw_path_t if isinstance(raw_path_t, list) else []:
                    if isinstance(path_t, str) and path_t.strip():
                        mem_keyword_parts.append(path_t.strip())
            spec_synonyms = expand_architectural_synonyms(f"{title or ''} {jtbd_or_rule or ''}")
            keywords = " ".join(
                x
                for x in [
                    doc_id,
                    doc_type,
                    domain,
                    container_type,
                    source_path,
                    *decision_ids,
                    *mem_keyword_parts,
                    spec_synonyms,
                ]
                if x
            )
            symbols = ""
            decompounded = compound_identifier_subtokens(f"{title or ''} {jtbd_or_rule or ''} {body_clean}")
            if decompounded:
                symbols = decompounded.strip()
            norm_rel_slash = f"/{rel_p.lstrip('/')}"
            is_archived_delta = doc_type == "delta/mutation" and "/deltas/archive/" in norm_rel_slash
            is_inactive_memory = doc_type == "agent/memory" and (
                str(status).strip().lower() != "active" or "/memory/archive/" in norm_rel_slash
            )
            if not is_archived_delta and not is_inactive_memory:
                fresh_base_symbols[doc_id] = symbols
                conn.execute(
                    """
                    INSERT INTO specs_fts (entity_id, title, jtbd_or_rule, body_text, keywords, symbols)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (doc_id, title, jtbd_or_rule, body_clean, keywords, symbols),
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type)
                VALUES (?, ?, ?, ?, ?);
                """,
                (rel_p, mtime, sha, doc_id, doc_type),
            )
            _upsert_file_tokens_fts(conn, rel_p, doc_type, spec_raw_text)

        if spec_updates or specs_deleted:
            inc_ids = (
                updated_spec_doc_ids
                if (not force and not specs_deleted and not any_doc_id_renamed and updated_spec_doc_ids)
                else None
            )
            rebuild_spec_edges(conn, updated_doc_ids=inc_ids)

        ast_idx = get_ast_indexer()
        resolver = _CodeOwnershipResolver(conn)
        pkg_dirs = resolver.package_dirs()
        processed_code: set[str] = set()
        for p in code_updates:
            if not p.is_file():
                continue
            try:
                rel_p = str(p.relative_to(index.root)).replace("\\", "/")
            except ValueError:
                rel_p = str(p).replace("\\", "/")

            st = p.stat()
            mtime = st.st_mtime
            raw_bytes = p.read_bytes()
            sha = hashlib.sha256(raw_bytes).hexdigest()

            is_test = is_polyglot_test_file(rel_p, repo_root=index.root, package_paths=pkg_dirs)

            existing = conn.execute("SELECT 1 FROM indexed_files WHERE rel_path = ?", (rel_p,)).fetchone()
            if existing:
                stats.updated_modified += 1
            else:
                stats.indexed_new += 1

            if is_test:
                conn.execute("DELETE FROM symbol_mappings WHERE symbol = ? AND kind = 'source_path';", (rel_p,))
                index_test_bindings(conn, ast_idx, p, rel_p, sha=sha, raw_bytes=raw_bytes, mtime_ns=st.st_mtime_ns)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type)
                    VALUES (?, ?, ?, 'tests', 'code/test');
                    """,
                    (rel_p, mtime, sha),
                )
            else:
                index_source_file(conn, ast_idx, p, rel_p, mtime, sha, resolver, raw_bytes=raw_bytes)
                processed_code.add(rel_p)

        if force or spec_updates or specs_deleted:
            reconcile_code_ownership(index, conn, ast_idx, resolver, processed_code)

        if force or spec_updates or specs_deleted or processed_code or code_deleted:
            _derive_ast_import_edges(conn, resolver, repo_root=index.root)

        if force or spec_updates or specs_deleted or processed_code or code_deleted:
            conn.execute("DELETE FROM symbol_mappings WHERE kind = 'inferred_path';")
            conn.execute(
                """
                DELETE FROM edges
                WHERE rel_type = 'styles_container'
                   OR (
                       rel_type = 'styled_by'
                       AND source_id NOT LIKE 'CLT-%'
                       AND source_id NOT LIKE 'UIC-%'
                       AND source_id NOT IN (SELECT id FROM entities WHERE type IN ('design/client', 'design/ui-component'))
                   );
                """
            )
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
                        WHERE e.rel_type = 'extends'
                    )
                    SELECT DISTINCT ds_id FROM ds_tree;
                    """,
                    (clt_id,),
                ).fetchall()
                for (ds_id,) in ds_rows:
                    upsert_edge(conn, cnt_id, ds_id, "styled_by", "declared_spec", f"client:{clt_id}")
                    upsert_edge(conn, ds_id, cnt_id, "styles_container", "declared_spec", f"client:{clt_id}")
                    for pat in cnt_paths:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'inferred_path');",
                            (pat, ds_id),
                        )

        if force or spec_updates or specs_deleted or processed_code or code_deleted:
            fts_target_ids: set[str] | None = None
            if not (force or specs_deleted or code_deleted or any_doc_id_renamed):
                fts_target_ids = set(updated_spec_doc_ids)
                if processed_code:
                    ph_code = ",".join("?" for _ in processed_code)
                    for (owner_id,) in conn.execute(
                        f"SELECT DISTINCT entity_id FROM code_symbols WHERE file_path IN ({ph_code});",
                        tuple(processed_code),
                    ).fetchall():
                        if owner_id:
                            fts_target_ids.add(owner_id)
                if any(
                    conn.execute(
                        "SELECT 1 FROM entities WHERE id = ? AND type IN ('tech/container', 'tech/component');",
                        (uid,),
                    ).fetchone()
                    for uid in updated_spec_doc_ids
                ):
                    for (cid,) in conn.execute(
                        "SELECT id FROM entities WHERE type IN ('tech/container', 'tech/component');"
                    ).fetchall():
                        fts_target_ids.add(cid)
            _project_ast_code_symbols_into_fts(
                conn,
                target_entity_ids=fts_target_ids,
                fresh_base_symbols=fresh_base_symbols,
            )

        if spec_updates:
            conn.execute(
                """
                INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
                SELECT substr(source_id, 1, instr(source_id, '::') - 1), target_id, 'test_binding'
                FROM edges
                WHERE rel_type = 'asserts'
                  AND instr(source_id, '::') > 0
                  AND target_id IN (SELECT id FROM entities WHERE type = 'invariant');
                """
            )

        ti_meta_row = conn.execute("SELECT value FROM metadata WHERE key = 'test_imports_indexed';").fetchone()
        needs_test_import_backfill = ti_meta_row is None or ti_meta_row[0] != "1"
        needs_unindexed_test_scan = False
        if spec_updates and not force:
            for inv_json, inv_id in conn.execute(
                "SELECT data_json, id FROM entities WHERE type = 'invariant';"
            ).fetchall():
                try:
                    idata = orjson.loads(inv_json or "{}")
                except (TypeError, ValueError):
                    idata = {}
                tb_str = str(idata.get("test_binding") or "").strip() if isinstance(idata, dict) else ""
                if not tb_str:
                    has_edge = conn.execute(
                        "SELECT 1 FROM edges WHERE rel_type = 'asserts' AND target_id = ? AND instr(source_id, '::') > 0 LIMIT 1;",
                        (inv_id,),
                    ).fetchone()
                    if not has_edge:
                        needs_unindexed_test_scan = True
                        break

        nested_roots = set(discover_nested_subproject_roots(index.root))
        nested_dir_strs = {os.path.normpath(str(nr)) for nr in nested_roots}
        nested_prefixes: tuple[str, ...] = tuple(
            f"{nr.relative_to(index.root).as_posix().strip('/')}/"
            for nr in nested_roots
            if nr.is_relative_to(index.root)
        )
        candidate_test_files: dict[str, Path] = {}
        pkg_abs_dirs = [(index.root / pd).resolve() for pd in sorted(pkg_dirs)]
        sibling_test_dirs = _discover_sibling_test_dirs(index.root, pkg_abs_dirs, nested_dir_strs)
        test_scan_roots: list[Path] = []
        seen_scan_roots: set[Path] = set()
        for cand_root in [
            *(index.root / std for std in ("tests", "test", *sorted(pkg_dirs))),
            *(Path(sd) for sd in sibling_test_dirs),
        ]:
            res_cand = cand_root.resolve()
            if res_cand not in seen_scan_roots and res_cand.is_dir() and res_cand not in nested_roots:
                seen_scan_roots.add(res_cand)
                test_scan_roots.append(res_cand)

        for tests_dir in test_scan_roots:
            for dirpath, dirnames, filenames in os.walk(str(tests_dir)):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in STANDARD_EXCLUDED_DIRS
                    and not d.startswith(".")
                    and (Path(dirpath) / d).resolve() not in nested_roots
                    and not is_dotnet_bin_dir(Path(dirpath) / d)
                ]
                for fn in filenames:
                    tp = Path(dirpath) / fn
                    if tp.suffix not in _CODE_EXTS:
                        continue
                    try:
                        rel_t = str(tp.relative_to(index.root)).replace("\\", "/")
                    except ValueError:
                        rel_t = str(tp).replace("\\", "/")
                    if nested_prefixes and rel_t.startswith(nested_prefixes):
                        continue
                    if is_polyglot_test_file(rel_t, repo_root=index.root, package_paths=pkg_dirs):
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
                if nested_prefixes and rel_tb.startswith(nested_prefixes):
                    continue
                candidate_test_files.setdefault(rel_tb, abs_tb)

        for rel_t, tp in sorted(candidate_test_files.items()):
            cached_entry = conn.execute(
                "SELECT mtime, sha256, doc_type FROM indexed_files WHERE rel_path = ?;",
                (rel_t,),
            ).fetchone()
            stat = tp.stat()
            if (
                not cached_entry
                or cached_entry["doc_type"] != "code/test"
                or abs(stat.st_mtime - cached_entry["mtime"]) > 1e-7
                or force
                or needs_unindexed_test_scan
                or needs_test_import_backfill
            ):
                if cached_entry and cached_entry["doc_type"] != "code/test":
                    store_code_symbols(conn, rel_t, [], [])
                    conn.execute("DELETE FROM symbol_mappings WHERE symbol = ? AND kind = 'source_path';", (rel_t,))
                t_raw_bytes = tp.read_bytes()
                t_sha = hashlib.sha256(t_raw_bytes).hexdigest()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type)
                    VALUES (?, ?, ?, 'tests', 'code/test');
                    """,
                    (rel_t, stat.st_mtime, t_sha),
                )
                index_test_bindings(
                    conn, ast_idx, tp, rel_t, sha=t_sha, raw_bytes=t_raw_bytes, mtime_ns=stat.st_mtime_ns
                )

        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('test_imports_indexed', '1');")

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
            WHERE target_id NOT IN (SELECT id FROM entities);
            """
        )
        conn.execute(
            """
            DELETE FROM edges
            WHERE target_id NOT IN (SELECT id FROM entities)
              AND rel_type NOT IN ('consumes_api', 'promoted_to');
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
        conn.execute(
            """
            DELETE FROM spec_embeddings
            WHERE entity_id NOT IN (
                SELECT id FROM entities WHERE type NOT IN ('delta/mutation', 'product/exploration')
            )
               OR entity_id NOT IN (SELECT entity_id FROM specs_fts);
            """
        )
        if specs_deleted or code_deleted:
            conn.execute("DELETE FROM memory_freshness_cache;")
        else:
            conn.execute(
                "DELETE FROM memory_freshness_cache WHERE memory_id NOT IN (SELECT id FROM entities WHERE type = 'agent/memory');"
            )
        prune_stale_test_binding_edges(conn)
        _reconcile_invariant_coverage(index, conn, ast_idx)

    return spec_updates
