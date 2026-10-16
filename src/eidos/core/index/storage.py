"""Embedded SQLite storage, schema initialization, and connection lifecycle (CMP-GRAPH-STORAGE).

Single canonical authority for SCHEMA_VERSION, cache DDL, schema migrations,
and connection pooling with WAL mode and corruption recovery.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

# Canonical schema version shared across ArchitecturalIndex, SpecValidator, and DriftDetector (DLT-2026-048).
SCHEMA_VERSION = "2.3.0"

# Every table owned by the derived index cache (dropped and rebuilt on schema version mismatch).
_MANAGED_TABLES = (
    "code_symbols",
    "symbol_mappings",
    "edges",
    "entities",
    "indexed_files",
    "spec_validation_cache",
    "test_binding_cache",
    "specs_fts",
    "metadata",
)

_CACHE_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indexed_files (
    rel_path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    sha256 TEXT NOT NULL,
    doc_id TEXT,
    doc_type TEXT,
    last_indexed REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_validation_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    doc_json TEXT NOT NULL,
    schema_errors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_binding_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    has_fixture INTEGER NOT NULL DEFAULT 0,
    functions_json TEXT NOT NULL,
    bindings_json TEXT NOT NULL,
    file_tokens_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT,
    domain TEXT,
    package_path TEXT,
    runtime TEXT,
    container_type TEXT,
    file_path TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities(domain);

CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    metadata_json TEXT,
    PRIMARY KEY (source_id, target_id, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel_type);

CREATE TABLE IF NOT EXISTS symbol_mappings (
    symbol TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (symbol, entity_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_symbols_symbol ON symbol_mappings(symbol);
CREATE INDEX IF NOT EXISTS idx_symbols_entity ON symbol_mappings(entity_id);

-- AST symbol coordinates (DLT-2026-034), one row set per owning entity of the file ('is_primary' marks the
-- resolved primary owner; files declared by several entities keep rows for each). 'id' preserves AST order.
CREATE TABLE IF NOT EXISTS code_symbols (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_symbol TEXT NOT NULL DEFAULT '',
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL,
    component_id TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 1,
    UNIQUE (file_path, entity_id, kind, parent_symbol, symbol, line_start)
);
CREATE INDEX IF NOT EXISTS idx_code_symbols_symbol ON code_symbols(symbol);
CREATE INDEX IF NOT EXISTS idx_code_symbols_file ON code_symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_code_symbols_entity ON code_symbols(entity_id, kind, symbol);

-- FTS5 full-text search table for BM25 ranking
CREATE VIRTUAL TABLE IF NOT EXISTS specs_fts USING fts5(
    entity_id UNINDEXED,
    title,
    jtbd_or_rule,
    body_text,
    keywords,
    symbols,
    tokenize='porter unicode61'
);
"""


def _unlink_cache_files(db_path: Path) -> None:
    """Remove a corrupted SQLite cache database and its WAL/SHM sidecar files."""
    for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        with contextlib.suppress(OSError):
            if candidate.exists():
                candidate.unlink()


def ensure_cache_schema(conn: sqlite3.Connection) -> None:
    """Ensure ``conn`` has the canonical ``SCHEMA_VERSION`` tables, dropping stale managed tables on version mismatch."""
    existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
    if existing_tables >= set(_MANAGED_TABLES) and "metadata" in existing_tables:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
        if row and row[0] == SCHEMA_VERSION:
            return
    if existing_tables & set(_MANAGED_TABLES):
        stored_version = None
        if "metadata" in existing_tables:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
            stored_version = row[0] if row else None
        if stored_version != SCHEMA_VERSION:
            with conn:
                for table in _MANAGED_TABLES:
                    conn.execute(f"DROP TABLE IF EXISTS {table};")
    with conn:
        conn.executescript(_CACHE_SCHEMA_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?);",
            (SCHEMA_VERSION,),
        )


def open_cache_connection(db_path: Path) -> sqlite3.Connection:
    """Open a WAL-configured SQLite cache connection with unified schema versioning and corruption recovery."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            jm_row = conn.execute("PRAGMA journal_mode;").fetchone()
            if not jm_row or str(jm_row[0]).lower() != "wal":
                conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA mmap_size = 268435456;")
            ensure_cache_schema(conn)
            return conn
        except sqlite3.DatabaseError:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            if attempt == 0:
                _unlink_cache_files(db_path)
                continue
            raise
