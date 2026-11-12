"""Embedded SQLite storage, schema initialization, and connection lifecycle (CMP-GRAPH-STORAGE).

Single canonical authority for SCHEMA_VERSION, cache DDL, schema migrations,
and connection pooling with WAL mode and corruption recovery.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson

if TYPE_CHECKING:
    from eidos.core.spec.models import SpecDocument

# Canonical schema version shared across ArchitecturalIndex, SpecValidator, and DriftDetector (DLT-2026-133).
SCHEMA_VERSION = "2.19.0"

# In-process parsed SpecDocument cache keyed by resolved Path -> (mtime_ns, st_size, sha256, SpecDocument)
_PARSED_SPEC_MEM_CACHE: dict[Path, tuple[int, int, str, SpecDocument]] = {}

# Every table owned by the derived index cache (dropped and rebuilt on schema version mismatch).
_MANAGED_TABLES = (
    "code_symbols",
    "symbol_mappings",
    "edges",
    "entities",
    "indexed_files",
    "spec_validation_cache",
    "test_binding_cache",
    "spec_embeddings",
    "specs_fts",
    "file_tokens_fts",
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
    doc_type TEXT
);

CREATE TABLE IF NOT EXISTS spec_validation_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    doc_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_binding_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    functions_json TEXT NOT NULL,
    file_tokens_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_embeddings (
    entity_id TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    model_id TEXT NOT NULL,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT,
    domain TEXT,
    file_path TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities(domain);

CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
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
    is_primary INTEGER NOT NULL DEFAULT 1,
    is_test INTEGER NOT NULL DEFAULT 0,
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

-- FTS5 inverted token table for O(1) governed file reference discovery (DLT-2026-107)
CREATE VIRTUAL TABLE IF NOT EXISTS file_tokens_fts USING fts5(
    rel_path UNINDEXED,
    doc_type UNINDEXED,
    tokens,
    tokenize="unicode61 tokenchars '-_'"
);
"""


def _db_lock_path(db_path: Path) -> Path:
    """Return the advisory lockfile path (e.g. ``index.db.lock``) guarding DDL and WAL recovery."""
    resolved = Path(db_path).resolve()
    return Path(f"{resolved}.lock")


@contextlib.contextmanager
def _advisory_db_lock(db_path: Path, *, non_blocking: bool = False) -> Iterator[bool]:
    """Acquire an OS advisory file lock on ``<db_path>.lock`` (``fcntl.flock`` on POSIX, ``msvcrt`` on Windows)."""
    lock_path = _db_lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    locked = False
    try:
        if os.name == "nt":  # pragma: no cover
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
            mode = msvcrt.LK_NBLCK if non_blocking else msvcrt.LK_LOCK
            try:
                msvcrt.locking(fd, mode, 1)
                locked = True
            except OSError:
                if non_blocking:
                    yield False
                    return
                raise
        else:
            import fcntl

            flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if non_blocking else 0)
            try:
                fcntl.flock(fd, flags)
                locked = True
            except (BlockingIOError, OSError):
                if non_blocking:
                    yield False
                    return
                raise
        yield True
    finally:
        if locked:
            with contextlib.suppress(OSError):
                if os.name == "nt":  # pragma: no cover
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


def _is_corrupt_database_error(exc: BaseException) -> bool:
    """Return True only when ``exc`` indicates genuine SQLite database corruption (never transient locks/DDL)."""
    corrupt_codes = {
        getattr(sqlite3, "SQLITE_CORRUPT", 11),
        getattr(sqlite3, "SQLITE_NOTADB", 26),
    }
    err_code = getattr(exc, "sqlite_errorcode", None)
    if err_code in corrupt_codes:
        return True
    err_msg = str(exc).lower()
    return any(tok in err_msg for tok in ("malformed", "not a database", "file is encrypted", "corrupt"))


def _unlink_cache_files(db_path: Path) -> None:
    """Remove a corrupted SQLite cache database and its WAL/SHM sidecar files."""
    for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        with contextlib.suppress(OSError):
            if candidate.exists():
                candidate.unlink()


def _is_schema_current(conn: sqlite3.Connection) -> bool:
    """Fast read-only check verifying ``conn`` already matches ``SCHEMA_VERSION`` with zero DDL statements."""
    existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
    user_tables = {t for t in existing_tables if not t.startswith(("sqlite_", "specs_fts_", "file_tokens_fts_"))}
    if user_tables == set(_MANAGED_TABLES) and "metadata" in existing_tables:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
        if row and row[0] == SCHEMA_VERSION:
            return True
    return False


def _apply_cache_schema_ddl(conn: sqlite3.Connection) -> None:
    """Initialize or migrate ``conn`` to ``SCHEMA_VERSION`` (caller holds advisory lock when file-backed)."""
    existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
    user_tables = {t for t in existing_tables if not t.startswith(("sqlite_", "specs_fts_", "file_tokens_fts_"))}
    if user_tables == set(_MANAGED_TABLES) and "metadata" in existing_tables:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
        if row and row[0] == SCHEMA_VERSION:
            return
    if user_tables:
        stored_version = None
        if "metadata" in existing_tables:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
            stored_version = row[0] if row else None
        if stored_version != SCHEMA_VERSION or user_tables > set(_MANAGED_TABLES):
            with conn:
                for table in (*_MANAGED_TABLES, *(user_tables - set(_MANAGED_TABLES))):
                    conn.execute(f"DROP TABLE IF EXISTS {table};")
    with conn:
        conn.executescript(_CACHE_SCHEMA_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?);",
            (SCHEMA_VERSION,),
        )


def ensure_cache_schema(conn: sqlite3.Connection, db_path: Path | None = None) -> None:
    """Ensure ``conn`` has the canonical ``SCHEMA_VERSION`` tables, serializing DDL on ``index.db.lock``."""
    if _is_schema_current(conn):
        return
    resolved_db = db_path
    if resolved_db is None:
        db_list = conn.execute("PRAGMA database_list;").fetchall()
        main_file = str(db_list[0][2] or "") if db_list else ""
        if main_file and main_file != ":memory:":
            resolved_db = Path(main_file).resolve()
    if resolved_db is not None:
        with _advisory_db_lock(resolved_db, non_blocking=False):
            _apply_cache_schema_ddl(conn)
    else:
        _apply_cache_schema_ddl(conn)


def open_cache_connection(db_path: Path) -> sqlite3.Connection:
    """Open a WAL-configured SQLite cache connection with file-locked DDL and safe corruption recovery."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            jm_row = conn.execute("PRAGMA journal_mode;").fetchone()
            if not jm_row or str(jm_row[0]).lower() != "wal":
                with _advisory_db_lock(db_path, non_blocking=False):
                    jm_recheck = conn.execute("PRAGMA journal_mode;").fetchone()
                    if not jm_recheck or str(jm_recheck[0]).lower() != "wal":
                        conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            ensure_cache_schema(conn, db_path=db_path)
            return conn
        except sqlite3.DatabaseError as exc:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            err_msg = str(exc).lower()
            if _is_corrupt_database_error(exc) and attempt == 0:
                with _advisory_db_lock(db_path, non_blocking=True) as acquired:
                    if acquired:
                        _unlink_cache_files(db_path)
                        continue
            if "schema" in err_msg and attempt < 2:
                with _advisory_db_lock(db_path, non_blocking=False):
                    pass
                continue
            raise
    raise sqlite3.OperationalError(f"Failed to initialize SQLite cache at {db_path}")  # pragma: no cover


def clone_spec_document(doc: SpecDocument, file_path: Path | None = None) -> SpecDocument:
    """Return a defensive copy of ``doc`` with isolated frontmatter dict and error list."""
    from eidos.core.spec.models import SpecDocument as _SpecDocument

    cloned = _SpecDocument(
        file_path=file_path if file_path is not None else doc.file_path,
        frontmatter=copy.deepcopy(doc.frontmatter),
        body=doc.body,
        doc_type=doc.doc_type,
        doc_id=doc.doc_id,
        schema_ref=doc.schema_ref,
        title=doc.title,
        errors=list(doc.errors),
        _model=None,
    )
    return cloned


def _reconstruct_spec_doc_from_json(file_path: Path, raw_doc_json: str) -> SpecDocument | None:
    """Reconstruct a base SpecDocument (with parse-only errors) from a serialized spec_validation_cache payload."""
    from eidos.core.spec.models import SpecDocument as _SpecDocument

    with contextlib.suppress(ValueError, TypeError, KeyError):
        doc_data = orjson.loads(raw_doc_json)
        if not isinstance(doc_data, dict):
            return None
        parse_errs = list(doc_data.get("parse_errors", []))
        doc = _SpecDocument(
            file_path=file_path,
            frontmatter=doc_data.get("frontmatter") or {},
            body=doc_data.get("body") or "",
            doc_type=doc_data.get("doc_type") or "unknown",
            doc_id=doc_data.get("doc_id") or "unknown",
            schema_ref=doc_data.get("schema_ref"),
            title=doc_data.get("title"),
            errors=parse_errs,
        )
        _ = doc.model
        return doc
    return None


def get_cached_spec_document(
    file_path: Path,
    *,
    mtime_ns: int | None = None,
    st_size: int | None = None,
    sha256: str | None = None,
    raw_bytes: bytes | None = None,
    cached_row: sqlite3.Row | dict[str, Any] | None = None,
) -> tuple[SpecDocument | None, str, int]:
    """Return ``(SpecDocument, sha256, mtime_ns)`` using unified in-memory and SQLite spec_validation_cache state."""
    from eidos.core.spec.parser import _NON_ROOT_SPEC_SUBDIRS, SpecDocument, _parse_spec_content

    try:
        res_fp = file_path.resolve()
        if mtime_ns is None or st_size is None:
            st = file_path.stat()
            mtime_ns = st.st_mtime_ns
            st_size = st.st_size
    except OSError:
        return None, "", 0

    mem_hit = _PARSED_SPEC_MEM_CACHE.get(res_fp)
    if raw_bytes is None and sha256 is None:
        if mem_hit is not None and mem_hit[0] == mtime_ns and mem_hit[1] == st_size:
            return clone_spec_document(mem_hit[3], file_path), mem_hit[2], mtime_ns
        if cached_row is not None and cached_row["mtime_ns"] == mtime_ns:
            reconstructed = _reconstruct_spec_doc_from_json(res_fp, cached_row["doc_json"])
            if reconstructed is not None:
                row_sha = str(cached_row["sha256"])
                _PARSED_SPEC_MEM_CACHE[res_fp] = (mtime_ns, st_size, row_sha, reconstructed)
                return clone_spec_document(reconstructed, file_path), row_sha, mtime_ns

    if st_size == 0 and not raw_bytes:
        empty_sha = hashlib.sha256(b"").hexdigest()
        is_root_system = file_path.name == "system.adoc" and file_path.parent.name.lower() not in _NON_ROOT_SPEC_SUBDIRS
        empty_doc = (
            SpecDocument(
                file_path=file_path,
                frontmatter={},
                body="",
                doc_type="product/system",
                doc_id="SYS-UNINITIALIZED",
            )
            if is_root_system
            else SpecDocument(
                file_path=file_path,
                frontmatter={},
                body="",
                doc_type="unknown",
                doc_id=file_path.stem,
                errors=["Missing YAML frontmatter block (delimited by '---')"],
            )
        )
        _PARSED_SPEC_MEM_CACHE[res_fp] = (mtime_ns, 0, empty_sha, clone_spec_document(empty_doc, res_fp))
        return empty_doc, empty_sha, mtime_ns

    if raw_bytes is None:
        try:
            raw_bytes = file_path.read_bytes()
        except OSError:
            return None, "", 0
    if not sha256:
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

    if mem_hit is not None and mem_hit[2] == sha256:
        _PARSED_SPEC_MEM_CACHE[res_fp] = (mtime_ns, st_size, sha256, mem_hit[3])
        return clone_spec_document(mem_hit[3], file_path), sha256, mtime_ns

    if cached_row is not None and cached_row["sha256"] == sha256:
        reconstructed = _reconstruct_spec_doc_from_json(res_fp, cached_row["doc_json"])
        if reconstructed is not None:
            _PARSED_SPEC_MEM_CACHE[res_fp] = (mtime_ns, st_size, sha256, reconstructed)
            return clone_spec_document(reconstructed, file_path), sha256, mtime_ns

    content = raw_bytes.decode("utf-8", errors="replace")
    doc = _parse_spec_content(file_path, content)
    _PARSED_SPEC_MEM_CACHE[res_fp] = (mtime_ns, st_size, sha256, clone_spec_document(doc, res_fp))
    return doc, sha256, mtime_ns


def load_spec_validation_cache(db_path: Path) -> dict[str, sqlite3.Row]:
    """Load all cached spec validation rows from ``db_path`` via canonical ``open_cache_connection``."""
    cached_rows: dict[str, sqlite3.Row] = {}
    with contextlib.suppress(OSError, sqlite3.Error), contextlib.closing(open_cache_connection(db_path)) as conn:
        for row in conn.execute(
            "SELECT file_path, mtime_ns, sha256, schema_hash, doc_json FROM spec_validation_cache;"
        ):
            cached_rows[row["file_path"]] = row
    return cached_rows


def save_spec_validation_cache(
    db_path: Path,
    to_upsert: list[tuple[str, int, str, str, str]],
    deleted_keys: list[str] | None = None,
) -> None:
    """Upsert and prune ``spec_validation_cache`` entries via canonical ``open_cache_connection``."""
    if not to_upsert and not deleted_keys:
        return
    with contextlib.suppress(OSError, sqlite3.Error), contextlib.closing(open_cache_connection(db_path)) as conn, conn:
        if deleted_keys:
            conn.executemany(
                "DELETE FROM spec_validation_cache WHERE file_path = ?;",
                [(k,) for k in deleted_keys],
            )
        if to_upsert:
            conn.executemany(
                """
                INSERT OR REPLACE INTO spec_validation_cache
                (file_path, mtime_ns, sha256, schema_hash, doc_json)
                VALUES (?, ?, ?, ?, ?);
                """,
                to_upsert,
            )


def load_test_binding_cache(db_path: Path) -> dict[str, sqlite3.Row]:
    """Load all cached test binding rows from ``db_path`` via canonical ``open_cache_connection``."""
    cached_rows: dict[str, sqlite3.Row] = {}
    with contextlib.suppress(OSError, sqlite3.Error), contextlib.closing(open_cache_connection(db_path)) as conn:
        for row in conn.execute(
            "SELECT file_path, mtime_ns, sha256, functions_json, file_tokens_json FROM test_binding_cache;"
        ):
            cached_rows[row["file_path"]] = row
    return cached_rows


def save_test_binding_cache(
    db_path: Path,
    to_upsert: list[tuple[str, int, str, str, str]],
    deleted_keys: list[str] | None = None,
) -> None:
    """Upsert and prune ``test_binding_cache`` entries via canonical ``open_cache_connection``."""
    if not to_upsert and not deleted_keys:
        return
    with contextlib.suppress(OSError, sqlite3.Error), contextlib.closing(open_cache_connection(db_path)) as conn, conn:
        if deleted_keys:
            conn.executemany(
                "DELETE FROM test_binding_cache WHERE file_path = ?;",
                [(k,) for k in deleted_keys],
            )
        if to_upsert:
            conn.executemany(
                """
                INSERT OR REPLACE INTO test_binding_cache
                (file_path, mtime_ns, sha256, functions_json, file_tokens_json)
                VALUES (?, ?, ?, ?, ?);
                """,
                to_upsert,
            )
