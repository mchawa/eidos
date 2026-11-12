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
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import get_ast_indexer
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
    compute_delta_impact_in_db,
    execute_fts_query,
    execute_incoming_references,
    execute_k_hop_traversal,
    find_symbol_in_db,
    generate_repo_map_from_db,
    query_spec_catalog_in_db,
    resolve_blast_radius_targets,
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
from eidos.core.workspace.project import find_repo_root, resolve_spec_and_repo_roots

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


def _resolve_index_paths(
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
    db_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Resolve canonical (repo_root, eidos_dir, db_path) triples uniformly."""
    resolved_eidos_arg = Path(eidos_dir).resolve() if eidos_dir is not None else None
    if repo_root is not None:
        target_root = Path(repo_root).resolve()
        target_eidos = resolved_eidos_arg if resolved_eidos_arg is not None else (target_root / "eidos").resolve()
    elif resolved_eidos_arg is not None:
        spec_root, target_root = resolve_spec_and_repo_roots(resolved_eidos_arg)
        target_eidos = resolved_eidos_arg if resolved_eidos_arg.name == "eidos" or not spec_root.exists() else spec_root
    else:
        target_root = find_repo_root(None)
        target_eidos = (target_root / "eidos").resolve()
    target_db = Path(db_path).resolve() if db_path is not None else (target_eidos / ".cache" / "index.db").resolve()
    return target_root, target_eidos, target_db


class ArchitecturalIndex:
    """Embedded SQLite Architectural Knowledge Graph & Hybrid Retrieval Index."""

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(
        self,
        db_path: Path | None = None,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
    ) -> None:
        self.root, self.eidos_dir, self.db_path = _resolve_index_paths(
            repo_root=repo_root,
            eidos_dir=eidos_dir,
            db_path=db_path,
        )
        if db_path is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._conns: list[sqlite3.Connection] = []
        self._closed: bool = False
        self._generation: int = 0
        self._last_synced_check: float = 0.0
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection | None:
        if self._closed:
            return None
        return self._get_connection()

    @_conn.setter
    def _conn(self, value: sqlite3.Connection | None) -> None:
        if value is None:
            self.close()
        else:
            with self._conns_lock:
                self._closed = False
                self._local._entry = (self._generation, value)
                if value not in self._conns:
                    self._conns.append(value)

    def _get_connection(self) -> sqlite3.Connection:
        entry = getattr(self._local, "_entry", None)
        if not self._closed and entry is not None and entry[0] == self._generation:
            return entry[1]
        conn = open_cache_connection(self.db_path)
        with self._conns_lock:
            self._closed = False
            self._local._entry = (self._generation, conn)
            self._conns.append(conn)
        return conn

    def _init_db(self) -> None:
        """Create tables, indexes, and FTS5 virtual tables with corruption recovery."""
        self._get_connection()

    def close(self) -> None:
        with self._conns_lock:
            self._closed = True
            self._generation += 1
            conns_to_close = list(self._conns)
            self._conns.clear()
        self._local.__dict__.clear()
        for conn in conns_to_close:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

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
        """Lazily ensure the index is synchronized (<2ms check when clean, 50ms in-process debounce)."""
        now = time.monotonic()
        if self._last_synced_check > 0.0 and (now - self._last_synced_check) < 0.05:
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
        use_semantic: bool = True,
        query_embedding: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a column-weighted BM25 ranked full-text query using FTS5 with optional Hybrid RRF."""
        if not text or not text.strip():
            return []
        self._ensure_synced()
        return execute_fts_query(
            self._get_connection(),
            text=text,
            limit=limit,
            include_types=include_types,
            exclude_types=exclude_types,
            use_semantic=use_semantic,
            eidos_dir=self.eidos_dir,
            query_embedding=query_embedding,
        )

    def get_k_hop_neighborhood(self, entity_id: str | Sequence[str], max_depth: int = 2) -> list[dict[str, Any]]:
        """Traverse graph up to k hops in both directions using recursive CTE."""
        self._ensure_synced()
        return execute_k_hop_traversal(self._get_connection(), entity_id=entity_id, max_depth=max_depth)

    def resolve_blast_radius_targets(self, symbol_or_path: str) -> list[str]:
        """Resolve an entity ID, specification path, source file path, or AST symbol to target entity ID(s)."""
        self._ensure_synced()
        return resolve_blast_radius_targets(self._get_connection(), symbol_or_path)

    def resolve_symbol_or_path(self, query: str) -> list[dict[str, Any]]:
        """Find entity IDs matching source path or symbol."""
        self._ensure_synced()
        return resolve_symbol_or_path_in_db(self._get_connection(), query)

    def find_symbol(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Locate symbol matches and their associated living component specs across the Code Graph."""
        self._ensure_synced()
        return find_symbol_in_db(self._get_connection(), query, limit=limit)

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
        use_semantic: bool = True,
    ) -> TieredContextSlice:
        """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
        return build_tiered_context_slice(
            self,
            files=files,
            component=component,
            task=task,
            tier=tier,
            use_semantic=use_semantic,
        )

    def get_semantic_status(self) -> dict[str, Any]:
        """Return telemetry for the optional semantic embedding index (`eidos index semantic status`)."""
        from eidos.core.index.semantic import get_semantic_index_status

        self._ensure_synced()
        return get_semantic_index_status(self._get_connection(), self.eidos_dir)

    def setup_semantic(self, variant: str = "quantized", force: bool = False) -> dict[str, Any]:
        """Provision Harrier-OSS-v1-0.6B ONNX artifacts into ``eidos/.cache/models/harrier-oss-v1-0.6b/``."""
        from eidos.core.index.semantic import setup_semantic_model

        return setup_semantic_model(self.eidos_dir, variant=variant, force=force)

    def sync_semantic(
        self,
        force: bool = False,
        batch_size: int = 8,
        encode_fn: Any | None = None,
    ) -> dict[str, Any]:
        """Synchronize ``spec_embeddings`` using spec-only ``content_sha256`` caching."""
        from eidos.core.index.semantic import sync_spec_embeddings

        self._ensure_synced()
        return sync_spec_embeddings(
            self._get_connection(),
            self.eidos_dir,
            force=force,
            batch_size=batch_size,
            encode_fn=encode_fn,
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

        type_counts = {
            row[0]: row[1] for row in cursor.execute("SELECT type, COUNT(*) FROM entities GROUP BY type;").fetchall()
        }
        total_entities = sum(type_counts.values())

        symbol_counts = {
            row[0]: row[1]
            for row in cursor.execute(
                """
                WITH all_mappings(symbol, entity_id, kind) AS (
                    SELECT symbol, entity_id, kind FROM symbol_mappings
                    UNION
                    SELECT symbol, entity_id, kind FROM code_symbols WHERE kind != 'import'
                    UNION
                    SELECT parent_symbol || '.' || symbol, entity_id, 'method'
                    FROM code_symbols WHERE kind != 'import' AND parent_symbol != ''
                )
                SELECT kind, COUNT(*) FROM all_mappings GROUP BY kind;
                """
            ).fetchall()
        }
        total_symbols = sum(symbol_counts.values())

        total_edges, total_files = cursor.execute(
            "SELECT (SELECT COUNT(*) FROM edges), (SELECT COUNT(*) FROM indexed_files);"
        ).fetchone()

        test_assert_filter = (
            "rel_type = 'asserts' AND instr(source_id, '::') > 0 AND source_id NOT IN (SELECT id FROM entities)"
        )
        test_invariant_assertions, tested_invariants = cursor.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT target_id) FROM edges WHERE {test_assert_filter};"
        ).fetchone()

        ast_idx = get_ast_indexer()

        ast_telemetry = {
            "engine": "ast-grep (Native AST)",
            "engine_class": ast_idx.__class__.__name__,
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
            "SELECT id, title, type, status, domain, file_path, data_json FROM entities WHERE type IN ('tech/container', 'tech/component') ORDER BY id;"
        ).fetchall()
        components = []
        for r in rows:
            data = orjson.loads(r["data_json"])
            data["id"] = r["id"]
            data["title"] = r["title"]
            data["type"] = r["type"]
            data["domain"] = r["domain"]
            data["file_path"] = r["file_path"]
            data["status"] = str(data.get("status") or r["status"] or "active").strip()
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
        query = "SELECT id, title, type, status, domain, file_path, data_json FROM entities WHERE 1=1"
        params: list[Any] = []
        if entity_type:
            query += " AND type = ?"
            params.append(entity_type)
        if status:
            query += " AND LOWER(COALESCE(NULLIF(TRIM(status), ''), 'active')) = LOWER(?)"
            params.append(status.strip())
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
            data["status"] = str(data.get("status") or r["status"] or "active").strip()
            entities.append(data)
        return entities

    def get_spec_catalog(
        self,
        eidos_dir: Path | None = None,
    ) -> tuple[dict[str, tuple[Path, str, dict[str, Any]]], dict[str, tuple[Path, dict[str, Any]]]]:
        """Retrieve (specs_by_id, invariant_map) from the synchronized knowledge graph."""
        self._ensure_synced()
        return query_spec_catalog_in_db(
            self._get_connection(),
            eidos_dir=eidos_dir or self.eidos_dir,
            repo_root=self.root,
        )

    def compute_delta_impact(
        self,
        mutations: list[dict[str, Any]],
        sync: bool = True,
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """Compute SpecDelta (affects, blast_radius) from the synchronized knowledge graph."""
        if sync:
            self._ensure_synced()
        return compute_delta_impact_in_db(
            self._get_connection(),
            mutations=mutations,
        )


# Thread-safe registry keyed by resolved eidos_dir so secondary worktrees never close active instances
_INDEX_INSTANCES: dict[Path, ArchitecturalIndex] = {}
_INDEX_INSTANCE: ArchitecturalIndex | None = None
_INDEX_LOCK = threading.Lock()


def get_architectural_index(
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
    db_path: Path | None = None,
    force_new: bool = False,
) -> ArchitecturalIndex:
    """Get or create a thread-safe ArchitecturalIndex instance keyed by resolved ``eidos_dir``."""
    global _INDEX_INSTANCE
    target_root, target_eidos, target_db = _resolve_index_paths(
        repo_root=repo_root,
        eidos_dir=eidos_dir,
        db_path=db_path,
    )
    key = target_eidos.resolve()
    with _INDEX_LOCK:
        existing = _INDEX_INSTANCES.get(key)
        if (
            force_new
            or existing is None
            or existing._closed
            or existing.root != target_root
            or existing.db_path != target_db
        ):
            if existing is not None:
                existing.close()
            existing = ArchitecturalIndex(db_path=db_path, repo_root=target_root, eidos_dir=target_eidos)
            _INDEX_INSTANCES[key] = existing
        _INDEX_INSTANCE = existing
        return existing


def _close_singleton_index() -> None:
    global _INDEX_INSTANCE
    with _INDEX_LOCK:
        instances = list(_INDEX_INSTANCES.values())
        _INDEX_INSTANCES.clear()
        if _INDEX_INSTANCE is not None and _INDEX_INSTANCE not in instances:
            instances.append(_INDEX_INSTANCE)
        _INDEX_INSTANCE = None
    for inst in instances:
        inst.close()


atexit.register(_close_singleton_index)
