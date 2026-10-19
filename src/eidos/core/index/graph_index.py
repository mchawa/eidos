"""Embedded SQLite Architectural Knowledge Graph & Hybrid Context Retrieval Index.

Implements EXP-2026-002 and DLT-2026-050:
- Standard library `sqlite3` backed graph store (eidos/.cache/index.db) via `storage.py`.
- Incremental dirty-checking, spec edge rebuilding, and code ownership resolution via `sync.py`.
- Column-weighted FTS5 BM25 search, recursive CTE graph traversal, and symbol lookup via `query.py`.
- Tiered context output (Anchor, Guardrails, Deep) for optimal token compression via `context.py`.
"""

from __future__ import annotations

import atexit
import contextlib
import sqlite3
import time
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import get_ast_indexer, is_ast_grep_available
from eidos.core.index.context import (
    TIER_TOKEN_BUDGETS,
    TieredContextSlice,
    build_tiered_context_slice,
)
from eidos.core.index.query import (
    FTS_COLUMN_WEIGHTS,
    TASK_SLICE_ENTITY_TYPES,
    build_fts_match_query,
    compound_identifier_subtokens,
    execute_fts_query,
    execute_incoming_references,
    execute_k_hop_traversal,
    find_symbol_in_db,
    generate_repo_map_from_db,
    resolve_symbol_locations_in_db,
    resolve_symbol_or_path_in_db,
    split_identifier,
)
from eidos.core.index.storage import (
    SCHEMA_VERSION,
    clone_spec_document,
    ensure_cache_schema,
    get_cached_spec_document,
    load_spec_validation_cache,
    load_test_binding_cache,
    open_cache_connection,
    save_spec_validation_cache,
    save_test_binding_cache,
)
from eidos.core.index.sync import (
    IndexSyncStats,
    check_index_dirty,
    sync_index,
)
from eidos.core.workspace.project import find_repo_root

__all__ = [
    "FTS_COLUMN_WEIGHTS",
    "SCHEMA_VERSION",
    "TASK_SLICE_ENTITY_TYPES",
    "TIER_TOKEN_BUDGETS",
    "ArchitecturalIndex",
    "IndexSyncStats",
    "TieredContextSlice",
    "build_fts_match_query",
    "clone_spec_document",
    "compound_identifier_subtokens",
    "ensure_cache_schema",
    "get_architectural_index",
    "get_cached_spec_document",
    "load_spec_validation_cache",
    "load_test_binding_cache",
    "open_cache_connection",
    "save_spec_validation_cache",
    "save_test_binding_cache",
    "split_identifier",
]


class ArchitecturalIndex:
    """Embedded SQLite Architectural Knowledge Graph & Hybrid Retrieval Index."""

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(
        self,
        db_path: Path | None = None,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
    ) -> None:
        if repo_root is not None:
            self.root = Path(repo_root).resolve()
        elif (eidos_dir is not None and (Path(eidos_dir) / "system.adoc").exists()) or (
            eidos_dir is not None
            and Path(eidos_dir).name == "eidos"
            and not any(p in Path(eidos_dir).parts for p in (".agents", "plugins", "assets"))
        ):
            self.root = Path(eidos_dir).resolve().parent
        else:
            self.root = find_repo_root(Path(eidos_dir).resolve() if eidos_dir else None)

        self.eidos_dir = (eidos_dir or (self.root / "eidos")).resolve()

        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            cache_dir = self.eidos_dir / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_dir / "index.db"

        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_cache_connection(self.db_path)
        return self._conn

    def _init_db(self) -> None:
        """Create tables, indexes, and FTS5 virtual tables with corruption recovery."""
        self._get_connection()

    def close(self) -> None:
        if self._conn:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def __enter__(self) -> ArchitecturalIndex:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Incremental Sync & Code Ownership Delegation (sync.py)
    # ------------------------------------------------------------------

    def is_dirty(self) -> tuple[bool, list[Path], list[Path]]:
        """Perform sub-5ms dirty checking comparing filesystem mtime with indexed state."""
        return check_index_dirty(self)

    def _ensure_synced(self) -> None:
        """Lazily ensure the index is synchronized (<2ms check when clean)."""
        now = time.monotonic()
        last_check = getattr(self, "_last_synced_check", 0.0)
        if now - last_check < 0.2:
            return
        dirty_state = self.is_dirty()
        if dirty_state[0]:
            self.sync(dirty_state=dirty_state)
        self._last_synced_check = time.monotonic()

    def sync(
        self,
        force: bool = False,
        dirty_state: tuple[bool, list[Path], list[Path]] | None = None,
        strict: bool = False,
    ) -> IndexSyncStats:
        """Incrementally synchronize the index with specifications and code on disk."""
        return sync_index(self, force=force, dirty_state=dirty_state, strict=strict)

    # ------------------------------------------------------------------
    # Query & Graph Traversal Delegation (query.py & context.py)
    # ------------------------------------------------------------------

    def query_fts(
        self,
        text: str,
        limit: int = 10,
        include_types: tuple[str, ...] | list[str] | None = None,
        exclude_types: tuple[str, ...] | list[str] = (),
    ) -> list[dict[str, Any]]:
        """Execute a column-weighted BM25 ranked full-text query using FTS5."""
        if not text or not text.strip():
            return []
        self._ensure_synced()
        return execute_fts_query(
            self._get_connection(),
            text=text,
            limit=limit,
            include_types=include_types,
            exclude_types=exclude_types,
        )

    def get_k_hop_neighborhood(self, entity_id: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Traverse graph up to k hops in both directions using recursive CTE."""
        self._ensure_synced()
        return execute_k_hop_traversal(self._get_connection(), entity_id=entity_id, max_depth=max_depth)

    def resolve_symbol_or_path(self, query: str) -> list[dict[str, Any]]:
        """Find entity IDs matching source path or symbol."""
        self._ensure_synced()
        return resolve_symbol_or_path_in_db(self._get_connection(), query)

    def find_symbol(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Locate symbol matches and their associated living component specs across the Code Graph."""
        self._ensure_synced()
        return find_symbol_in_db(self._get_connection(), query, limit=limit)

    def _symbol_locations(self, entity_id: str, kind: str, symbol: str) -> list[dict[str, Any]]:
        """Resolve persisted source coordinates (one entry per definition) for an AST symbol mapping."""
        return resolve_symbol_locations_in_db(self._get_connection(), entity_id, kind, symbol)

    def get_repo_map(self, max_tokens: int = 1500) -> str:
        """Generate a token-budgeted structural repo map outline across all indexed components for AI coding agents."""
        self._ensure_synced()
        return generate_repo_map_from_db(self._get_connection(), max_tokens=max_tokens)

    def get_tiered_context_slice(
        self,
        files: list[str] | str | None = None,
        component: str | None = None,
        task: str | None = None,
        tier: str = "guardrails",
    ) -> TieredContextSlice:
        """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
        return build_tiered_context_slice(
            self,
            files=files,
            component=component,
            task=task,
            tier=tier,
        )

    def get_incoming_references(self, target_id_or_path: str) -> list[dict[str, Any]]:
        """Find all incoming graph edges pointing to an entity ID or file path."""
        self._ensure_synced()
        conn = self._get_connection()
        return execute_incoming_references(conn, target_id_or_path)

    # ------------------------------------------------------------------
    # Catalog Inspection & Telemetry
    # ------------------------------------------------------------------

    def get_index_status(self) -> dict[str, Any]:
        """Return comprehensive health and statistics of the architectural index."""
        t0 = time.perf_counter()
        dirty, to_update, to_delete = self.is_dirty()
        dirty_ms = (time.perf_counter() - t0) * 1000

        conn = self._get_connection()
        cursor = conn.cursor()

        total_entities = cursor.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
        total_edges = cursor.execute("SELECT COUNT(*) FROM edges;").fetchone()[0]
        total_symbols = cursor.execute("SELECT COUNT(*) FROM symbol_mappings;").fetchone()[0]
        total_files = cursor.execute("SELECT COUNT(*) FROM indexed_files;").fetchone()[0]

        type_counts = {}
        for row in cursor.execute("SELECT type, COUNT(*) FROM entities GROUP BY type;").fetchall():
            type_counts[row[0]] = row[1]

        symbol_counts = {}
        for row in cursor.execute("SELECT kind, COUNT(*) FROM symbol_mappings GROUP BY kind;").fetchall():
            symbol_counts[row[0]] = row[1]

        test_assert_filter = (
            "rel_type = 'asserts' AND instr(source_id, '::') > 0 AND source_id NOT IN (SELECT id FROM entities)"
        )
        test_invariant_assertions = cursor.execute(
            f"SELECT COUNT(*) FROM edges WHERE {test_assert_filter};"
        ).fetchone()[0]
        tested_invariants = cursor.execute(
            f"SELECT COUNT(DISTINCT target_id) FROM edges WHERE {test_assert_filter};"
        ).fetchone()[0]

        ast_avail = is_ast_grep_available()
        ast_idx = get_ast_indexer()

        ast_telemetry = {
            "engine": "ast-grep (Native AST)" if ast_avail else "python-ast (Standard Library Fallback)",
            "engine_class": ast_idx.__class__.__name__,
            "ast_grep_available": ast_avail,
            "total_symbols": total_symbols,
            "classes": symbol_counts.get("class", 0),
            "functions": symbol_counts.get("function", 0),
            "methods": symbol_counts.get("method", 0),
            "test_bindings": symbol_counts.get("test_binding", 0),
            "symbols_by_kind": symbol_counts,
            "test_invariant_assertions": test_invariant_assertions,
            "tested_invariants": tested_invariants,
        }

        db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "db_path": str(self.db_path),
            "schema_version": self.SCHEMA_VERSION,
            "total_entities": total_entities,
            "total_edges": total_edges,
            "total_symbols": total_symbols,
            "total_indexed_files": total_files,
            "db_size_bytes": db_size_bytes,
            "is_dirty": dirty,
            "pending_updates": len(to_update),
            "pending_deletions": len(to_delete),
            "dirty_check_latency_ms": round(dirty_ms, 2),
            "entity_counts_by_type": type_counts,
            "ast": ast_telemetry,
        }

    def get_components(self) -> list[dict[str, Any]]:
        """Retrieve all C4 containers and components from the knowledge graph index."""
        self._ensure_synced()
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT id, title, file_path, data_json FROM entities WHERE type IN ('tech/container', 'tech/component') ORDER BY id;"
        ).fetchall()
        components = []
        for r in rows:
            data = orjson.loads(r["data_json"])
            data["id"] = r["id"]
            data["file_path"] = r["file_path"]
            components.append(data)
        return components

    def get_containers(self) -> list[dict[str, Any]]:
        """Retrieve all C4 containers (Level 2) from the knowledge graph index."""
        return self.get_entities(entity_type="tech/container")

    def get_entities(
        self,
        entity_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve entities from the knowledge graph with optional type and status filtering."""
        self._ensure_synced()
        query = "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE 1=1"
        params: list[Any] = []
        if entity_type:
            query += " AND type = ?"
            params.append(entity_type)
        query += " ORDER BY type, id;"
        conn = self._get_connection()
        rows = conn.execute(query, params).fetchall()
        entities = []
        for r in rows:
            data = orjson.loads(r["data_json"])
            data["id"] = r["id"]
            data["title"] = r["title"]
            data["type"] = r["type"]
            data["domain"] = r["domain"]
            data["file_path"] = r["file_path"]
            ent_status = str(data.get("status") or "active").strip()
            if status and ent_status.lower() != status.strip().lower():
                continue
            entities.append(data)
        return entities


# Global singleton instance for high-speed module reuse
_INDEX_INSTANCE: ArchitecturalIndex | None = None


def get_architectural_index(
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
    db_path: Path | None = None,
    force_new: bool = False,
) -> ArchitecturalIndex:
    """Get or create singleton ArchitecturalIndex instance."""
    global _INDEX_INSTANCE
    if repo_root is not None:
        target_root = Path(repo_root).resolve()
    elif (eidos_dir is not None and (Path(eidos_dir) / "system.adoc").exists()) or (
        eidos_dir is not None
        and Path(eidos_dir).name == "eidos"
        and not any(p in Path(eidos_dir).parts for p in (".agents", "plugins", "assets"))
    ):
        target_root = Path(eidos_dir).resolve().parent
    else:
        target_root = find_repo_root(Path(eidos_dir).resolve() if eidos_dir else None)

    target_eidos = (eidos_dir or (target_root / "eidos")).resolve()
    target_db = Path(db_path).resolve() if db_path is not None else (target_eidos / ".cache" / "index.db").resolve()
    if (
        force_new
        or _INDEX_INSTANCE is None
        or _INDEX_INSTANCE.root != target_root
        or _INDEX_INSTANCE.eidos_dir != target_eidos
        or _INDEX_INSTANCE.db_path != target_db
        or _INDEX_INSTANCE._conn is None
    ):
        if _INDEX_INSTANCE is not None:
            _INDEX_INSTANCE.close()
        _INDEX_INSTANCE = ArchitecturalIndex(db_path=db_path, repo_root=target_root, eidos_dir=target_eidos)
    return _INDEX_INSTANCE


def _close_singleton_index() -> None:
    global _INDEX_INSTANCE
    if _INDEX_INSTANCE is not None:
        _INDEX_INSTANCE.close()
        _INDEX_INSTANCE = None


atexit.register(_close_singleton_index)
