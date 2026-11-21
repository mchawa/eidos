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
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson

if TYPE_CHECKING:
    from eidos.core.spec.models import SpecDocument

# Canonical schema version shared across ArchitecturalIndex, SpecValidator, and DriftDetector (DLT-2026-158).
SCHEMA_VERSION = "2.22.0"

# In-process parsed SpecDocument cache keyed by resolved Path -> (mtime_ns, st_size, sha256, SpecDocument)
_PARSED_SPEC_MEM_CACHE: dict[Path, tuple[int, int, str, SpecDocument]] = {}

# Every table owned by the derived index cache (dropped and rebuilt on schema version mismatch).
_MANAGED_TABLES = (
    "code_symbols",
    "code_couplings",
    "symbol_mappings",
    "edges",
    "entities",
    "indexed_files",
    "spec_validation_cache",
    "test_binding_cache",
    "public_surface_cache",
    "mermaid_validation_cache",
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

CREATE TABLE IF NOT EXISTS public_surface_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    st_size INTEGER NOT NULL,
    extractor_version TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mermaid_validation_cache (
    diagram_sha256 TEXT PRIMARY KEY,
    engine_version TEXT NOT NULL,
    is_valid INTEGER NOT NULL,
    error_message TEXT NOT NULL
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
    provenance TEXT NOT NULL DEFAULT 'declared_spec',
    evidence TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, target_id, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel_type);
CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);

CREATE TABLE IF NOT EXISTS code_couplings (
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    source_cnt_id TEXT NOT NULL DEFAULT '',
    target_cnt_id TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    src_file TEXT NOT NULL,
    line_start INTEGER NOT NULL DEFAULT 0,
    target_ref TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_entity_id, target_entity_id, provenance, src_file, line_start, target_ref)
);
CREATE INDEX IF NOT EXISTS idx_couplings_src ON code_couplings(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_couplings_dst ON code_couplings(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_couplings_cnt ON code_couplings(source_cnt_id, target_cnt_id);

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


_PROCESS_DB_LOCKS_GUARD = threading.Lock()
_PROCESS_DB_LOCKS: dict[Path, threading.RLock] = {}
_HELD_DB_LOCKS = threading.local()


def _get_process_db_lock(lock_path: Path) -> threading.RLock:
    """Return the intra-process re-entrant mutex guarding a resolved advisory ``index.db.lock`` path."""
    with _PROCESS_DB_LOCKS_GUARD:
        lock = _PROCESS_DB_LOCKS.get(lock_path)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_DB_LOCKS[lock_path] = lock
        return lock


def _db_lock_path(db_path: Path) -> Path:
    """Return the advisory lockfile path (e.g. ``index.db.lock``) guarding DDL and WAL recovery."""
    resolved = Path(db_path).resolve()
    return Path(f"{resolved}.lock")


def _read_lock_holder(lock_path: Path) -> str:
    """Read best-effort holder metadata (PID/timestamp) stored in ``index.db.lock``."""
    with contextlib.suppress(OSError):
        raw = lock_path.read_text(encoding="utf-8", errors="replace").strip()
        if raw:
            first_line = raw.splitlines()[0].strip()
            if first_line and first_line != "0":
                return first_line
    return ""


def _write_lock_holder(fd: int) -> None:
    """Record current process PID and timestamp inside the held advisory lockfile."""
    with contextlib.suppress(OSError):
        payload = f"pid={os.getpid()} started={time.time():.3f}\n".encode("ascii")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name != "nt":
            os.ftruncate(fd, 0)
        os.write(fd, payload)


def _format_lock_wait_message(lock_path: Path) -> str:
    """Build a human-readable stderr diagnostic when waiting on a contended ``index.db.lock``."""
    holder = _read_lock_holder(lock_path)
    if holder:
        return f"Waiting for Eidos index lock ({lock_path}, held by {holder})..."
    return f"Waiting for Eidos index lock ({lock_path})..."


def _try_os_lock(fd: int, *, blocking: bool) -> bool:
    """Acquire an OS advisory lock on ``fd``; return False if non-blocking and contended."""
    if os.name == "nt":  # pragma: no cover
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
            return True
        except OSError:
            if not blocking:
                return False
            raise
    import fcntl

    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, flags)
        return True
    except (BlockingIOError, OSError):
        if not blocking:
            return False
        raise


@contextlib.contextmanager
def _advisory_db_lock(
    db_path: Path | None,
    *,
    non_blocking: bool = False,
    notify_wait: bool = False,
) -> Iterator[bool]:
    """Acquire an OS advisory file lock on ``<db_path>.lock`` (re-entrant per thread; ``fcntl.flock`` on POSIX, ``msvcrt`` on Windows).

    Records holder PID metadata in ``index.db.lock`` and emits optional ``sys.stderr`` lock-wait diagnostics before blocking.
    """
    if db_path is None or str(db_path) == ":memory:" or Path(db_path).name == ":memory:":
        yield True
        return

    lock_path = _db_lock_path(db_path)
    held: dict[Path, int] | None = getattr(_HELD_DB_LOCKS, "counts", None)
    if held is None:
        held = {}
        _HELD_DB_LOCKS.counts = held
    if held.get(lock_path, 0) > 0:
        held[lock_path] += 1
        try:
            yield True
        finally:
            remaining = held.get(lock_path, 1) - 1
            if remaining <= 0:
                held.pop(lock_path, None)
            else:
                held[lock_path] = remaining
        return

    notified = False
    proc_lock = _get_process_db_lock(lock_path)
    proc_held = proc_lock.acquire(blocking=False)
    if not proc_held:
        if non_blocking:
            yield False
            return
        if notify_wait:
            sys.stderr.write(f"{_format_lock_wait_message(lock_path)}\n")
            sys.stderr.flush()
            notified = True
        proc_held = proc_lock.acquire(blocking=True)

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    os_locked = False
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        os_locked = _try_os_lock(fd, blocking=False)
        if not os_locked:
            if non_blocking:
                with contextlib.suppress(OSError):
                    os.close(fd)
                fd = -1
                proc_lock.release()
                proc_held = False
                yield False
                return
            if notify_wait and not notified:
                sys.stderr.write(f"{_format_lock_wait_message(lock_path)}\n")
                sys.stderr.flush()
                notified = True
            os_locked = _try_os_lock(fd, blocking=True)
        _write_lock_holder(fd)
        held[lock_path] = 1
        yield True
    finally:
        if os_locked:
            held.pop(lock_path, None)
        if os_locked and fd >= 0:
            with contextlib.suppress(OSError):
                if os.name == "nt":  # pragma: no cover
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        if proc_held:
            proc_lock.release()


def _resolve_conn_db_path(conn: sqlite3.Connection, fallback_db_path: Path | None = None) -> Path | None:
    """Resolve the backing file path of a SQLite connection, returning None for in-memory databases."""
    with contextlib.suppress(sqlite3.Error):
        db_list = conn.execute("PRAGMA database_list;").fetchall()
        main_file = str(db_list[0][2] or "") if db_list else ""
        if main_file == ":memory:":
            return None
        if main_file:
            return Path(main_file).resolve()
    if (
        fallback_db_path is not None
        and str(fallback_db_path) != ":memory:"
        and Path(fallback_db_path).name != ":memory:"
    ):
        return Path(fallback_db_path).resolve()
    return None


@contextlib.contextmanager
def _immediate_write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run SQLite writes inside an explicit ``BEGIN IMMEDIATE`` transaction to avoid deferred-lock deadlocks."""
    if conn.in_transaction:
        with conn:
            yield
        return
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield
        conn.commit()
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        raise


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


_TEST_BINDING_CACHE_VERSION = "2"
_CODE_SYMBOL_CACHE_VERSION = "2"


def _is_schema_current(conn: sqlite3.Connection) -> bool:
    """Fast read-only check verifying ``conn`` already matches ``SCHEMA_VERSION`` and cache format versions with zero DDL statements."""
    existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
    user_tables = {t for t in existing_tables if not t.startswith(("sqlite_", "specs_fts_", "file_tokens_fts_"))}
    if user_tables == set(_MANAGED_TABLES) and "metadata" in existing_tables:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
        if row and row[0] == SCHEMA_VERSION:
            tbc_row = conn.execute("SELECT value FROM metadata WHERE key = 'test_binding_cache_ver';").fetchone()
            csc_row = conn.execute("SELECT value FROM metadata WHERE key = 'code_symbol_cache_ver';").fetchone()
            if (
                tbc_row
                and tbc_row[0] == _TEST_BINDING_CACHE_VERSION
                and csc_row
                and csc_row[0] == _CODE_SYMBOL_CACHE_VERSION
            ):
                return True
    return False


def _apply_cache_schema_ddl(conn: sqlite3.Connection) -> None:
    """Initialize or migrate ``conn`` to ``SCHEMA_VERSION`` (caller holds advisory lock when file-backed)."""
    existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
    user_tables = {t for t in existing_tables if not t.startswith(("sqlite_", "specs_fts_", "file_tokens_fts_"))}
    if user_tables == set(_MANAGED_TABLES) and "metadata" in existing_tables:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
        if row and row[0] == SCHEMA_VERSION:
            tbc_row = conn.execute("SELECT value FROM metadata WHERE key = 'test_binding_cache_ver';").fetchone()
            csc_row = conn.execute("SELECT value FROM metadata WHERE key = 'code_symbol_cache_ver';").fetchone()
            needs_tbc = not tbc_row or tbc_row[0] != _TEST_BINDING_CACHE_VERSION
            needs_csc = not csc_row or csc_row[0] != _CODE_SYMBOL_CACHE_VERSION
            if needs_tbc or needs_csc:
                with _immediate_write_transaction(conn):
                    if needs_tbc:
                        conn.execute("DELETE FROM test_binding_cache;")
                        conn.execute("UPDATE indexed_files SET mtime = -1.0 WHERE doc_type = 'code/test';")
                        conn.execute(
                            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('test_binding_cache_ver', ?);",
                            (_TEST_BINDING_CACHE_VERSION,),
                        )
                    if needs_csc:
                        conn.execute(
                            "UPDATE indexed_files SET mtime = -1.0, sha256 = '' WHERE doc_type = 'code/source';"
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('code_symbol_cache_ver', ?);",
                            (_CODE_SYMBOL_CACHE_VERSION,),
                        )
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
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('test_binding_cache_ver', ?);",
            (_TEST_BINDING_CACHE_VERSION,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('code_symbol_cache_ver', ?);",
            (_CODE_SYMBOL_CACHE_VERSION,),
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
        with _advisory_db_lock(resolved_db, non_blocking=False, notify_wait=True):
            _apply_cache_schema_ddl(conn)
    else:
        _apply_cache_schema_ddl(conn)


def open_cache_connection(
    db_path: Path,
    *,
    allow_ddl: bool = True,
    sqlite_timeout: float = 10.0,
) -> sqlite3.Connection:
    """Open a WAL-configured SQLite cache connection with file-locked DDL and safe corruption recovery.

    When ``allow_ddl=False``, never acquires ``index.db.lock`` for WAL setup or schema migrations and raises
    ``sqlite3.OperationalError`` if the database file is missing or mid-migration so opportunistic cache
    readers can fall back immediately to live disk parsing without blocking.
    """
    db_path = Path(db_path).resolve()
    if not allow_ddl and not db_path.is_file():
        raise sqlite3.OperationalError(f"SQLite cache does not exist yet at {db_path}")
    if allow_ddl:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=sqlite_timeout, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if not allow_ddl:
                conn.execute("PRAGMA query_only = ON;")
                if not _is_schema_current(conn):
                    conn.close()
                    raise sqlite3.OperationalError(f"SQLite cache schema is not initialized at {db_path}")
                return conn
            jm_row = conn.execute("PRAGMA journal_mode;").fetchone()
            if not jm_row or str(jm_row[0]).lower() != "wal":
                with _advisory_db_lock(db_path, non_blocking=False, notify_wait=True):
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
            if not allow_ddl:
                raise
            err_msg = str(exc).lower()
            if _is_corrupt_database_error(exc) and attempt == 0:
                with _advisory_db_lock(db_path, non_blocking=True) as acquired:
                    if acquired:
                        _unlink_cache_files(db_path)
                        continue
            if "schema" in err_msg and attempt < 2:
                with _advisory_db_lock(db_path, non_blocking=False, notify_wait=True):
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
    """Load cached spec validation rows from ``db_path`` without blocking on DDL locks (falls back to live disk parsing)."""
    cached_rows: dict[str, sqlite3.Row] = {}
    with (
        contextlib.suppress(OSError, sqlite3.Error),
        contextlib.closing(open_cache_connection(db_path, allow_ddl=False, sqlite_timeout=0.5)) as conn,
    ):
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
    """Opportunistically upsert and prune ``spec_validation_cache`` entries without blocking on contended locks."""
    if not to_upsert and not deleted_keys:
        return
    with contextlib.suppress(OSError, sqlite3.Error), _advisory_db_lock(db_path, non_blocking=True) as acquired:
        if not acquired:
            return
        with (
            contextlib.closing(open_cache_connection(db_path, sqlite_timeout=0.5)) as conn,
            _immediate_write_transaction(conn),
        ):
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
    """Load cached test binding rows from ``db_path`` without blocking on DDL locks (falls back to live disk parsing)."""
    cached_rows: dict[str, sqlite3.Row] = {}
    with (
        contextlib.suppress(OSError, sqlite3.Error),
        contextlib.closing(open_cache_connection(db_path, allow_ddl=False, sqlite_timeout=0.5)) as conn,
    ):
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
    """Opportunistically upsert and prune ``test_binding_cache`` entries without blocking on contended locks."""
    if not to_upsert and not deleted_keys:
        return
    with contextlib.suppress(OSError, sqlite3.Error), _advisory_db_lock(db_path, non_blocking=True) as acquired:
        if not acquired:
            return
        with (
            contextlib.closing(open_cache_connection(db_path, sqlite_timeout=0.5)) as conn,
            _immediate_write_transaction(conn),
        ):
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
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('test_binding_cache_ver', ?);",
                (_TEST_BINDING_CACHE_VERSION,),
            )


def load_public_surface_cache(
    db_path: Path,
    *,
    extractor_version: str | None = None,
) -> dict[str, tuple[int, int, str, str]]:
    """Load cached Public Surface AST symbol rows from ``db_path`` without blocking on DDL locks."""
    cached_rows: dict[str, tuple[int, int, str, str]] = {}
    with (
        contextlib.suppress(OSError, sqlite3.Error),
        contextlib.closing(open_cache_connection(db_path, allow_ddl=False, sqlite_timeout=0.5)) as conn,
    ):
        if extractor_version is not None:
            cursor = conn.execute(
                "SELECT file_path, mtime_ns, st_size, extractor_version, payload_json "
                "FROM public_surface_cache WHERE extractor_version = ?;",
                (extractor_version,),
            )
        else:
            cursor = conn.execute(
                "SELECT file_path, mtime_ns, st_size, extractor_version, payload_json FROM public_surface_cache;"
            )
        for row in cursor:
            cached_rows[str(row["file_path"])] = (
                int(row["mtime_ns"]),
                int(row["st_size"]),
                str(row["extractor_version"]),
                str(row["payload_json"]),
            )
    return cached_rows


def save_public_surface_cache(
    db_path: Path,
    to_upsert: list[tuple[str, int, int, str, str]],
    deleted_keys: list[str] | None = None,
) -> None:
    """Opportunistically upsert and prune ``public_surface_cache`` entries without blocking on contended locks."""
    if not to_upsert and not deleted_keys:
        return
    with contextlib.suppress(OSError, sqlite3.Error), _advisory_db_lock(db_path, non_blocking=True) as acquired:
        if not acquired:
            return
        with (
            contextlib.closing(open_cache_connection(db_path, sqlite_timeout=0.5)) as conn,
            _immediate_write_transaction(conn),
        ):
            if deleted_keys:
                conn.executemany(
                    "DELETE FROM public_surface_cache WHERE file_path = ?;",
                    [(k,) for k in deleted_keys],
                )
            if to_upsert:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO public_surface_cache
                    (file_path, mtime_ns, st_size, extractor_version, payload_json)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    to_upsert,
                )


def load_mermaid_validation_cache(
    db_path: Path,
    engine_version: str,
) -> dict[str, tuple[bool, str]]:
    """Load cached Mermaid syntax validation verdicts from ``db_path`` without blocking on DDL locks."""
    cached_rows: dict[str, tuple[bool, str]] = {}
    with (
        contextlib.suppress(OSError, sqlite3.Error),
        contextlib.closing(open_cache_connection(db_path, allow_ddl=False, sqlite_timeout=0.5)) as conn,
    ):
        for row in conn.execute(
            "SELECT diagram_sha256, is_valid, error_message FROM mermaid_validation_cache WHERE engine_version = ?;",
            (engine_version,),
        ):
            cached_rows[str(row["diagram_sha256"])] = (bool(row["is_valid"]), str(row["error_message"] or ""))
    return cached_rows


def save_mermaid_validation_cache(
    db_path: Path,
    rows: list[tuple[str, str, int, str]],
) -> None:
    """Opportunistically persist ``mermaid_validation_cache`` verdicts without blocking on contended locks."""
    if not rows:
        return
    with contextlib.suppress(OSError, sqlite3.Error), _advisory_db_lock(db_path, non_blocking=True) as acquired:
        if not acquired:
            return
        with (
            contextlib.closing(open_cache_connection(db_path, sqlite_timeout=0.5)) as conn,
            _immediate_write_transaction(conn),
        ):
            conn.executemany(
                """
                INSERT OR REPLACE INTO mermaid_validation_cache
                (diagram_sha256, engine_version, is_valid, error_message)
                VALUES (?, ?, ?, ?);
                """,
                rows,
            )


EDGE_PROVENANCE_PRECEDENCE: dict[str, int] = {
    "inferred_ownership": 1,
    "ast_symbol": 2,
    "ast_http": 3,
    "ast_import": 4,
    "declared_spec": 5,
}

PROVENANCE_TO_CONFIDENCE: dict[str, str] = {
    "declared_spec": "observed",
    "ast_symbol": "observed",
    "fts_bm25": "observed",
    "ast_import": "derived",
    "ast_http": "derived",
    "inferred_ownership": "inferred",
    "semantic_rrf": "inferred",
    "hybrid_rrf": "inferred",
}

CONFIDENCE_RANK: dict[str, int] = {
    "inferred": 1,
    "derived": 2,
    "observed": 3,
}

VALID_CONFIDENCE_LEVELS: tuple[str, ...] = ("inferred", "derived", "observed")

_PROVENANCE_RANK_SQL_EXPR = (
    "CASE {col} "
    "WHEN 'declared_spec' THEN 5 "
    "WHEN 'ast_import' THEN 4 "
    "WHEN 'ast_http' THEN 3 "
    "WHEN 'ast_symbol' THEN 2 "
    "WHEN 'inferred_ownership' THEN 1 "
    "ELSE 0 END"
)

_UPSERT_EDGE_SQL = f"""
INSERT INTO edges (source_id, target_id, rel_type, provenance, evidence)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(source_id, target_id, rel_type) DO UPDATE SET
    evidence = CASE
        WHEN {_PROVENANCE_RANK_SQL_EXPR.format(col="excluded.provenance")} > {_PROVENANCE_RANK_SQL_EXPR.format(col="edges.provenance")}
            THEN CASE WHEN excluded.evidence != '' THEN excluded.evidence ELSE edges.evidence END
        WHEN edges.evidence = '' AND excluded.evidence != ''
            THEN excluded.evidence
        ELSE edges.evidence
    END,
    provenance = CASE
        WHEN {_PROVENANCE_RANK_SQL_EXPR.format(col="excluded.provenance")} > {_PROVENANCE_RANK_SQL_EXPR.format(col="edges.provenance")}
            THEN excluded.provenance
        ELSE edges.provenance
    END;
"""


def provenance_to_confidence(provenance: str) -> str:
    """Map a canonical provenance tag to its 3-tier epistemic certainty ('observed' | 'derived' | 'inferred')."""
    return PROVENANCE_TO_CONFIDENCE.get((provenance or "").strip().lower(), "observed")


def normalize_confidence_level(min_confidence: str | None) -> str:
    """Validate and normalize a confidence threshold ('inferred' | 'derived' | 'observed')."""
    clean = (min_confidence or "inferred").strip().lower()
    if clean not in CONFIDENCE_RANK:
        raise ValueError(
            f"Invalid min_confidence '{min_confidence}' (expected one of: {', '.join(VALID_CONFIDENCE_LEVELS)})."
        )
    return clean


def allowed_provenances_for_confidence(min_confidence: str | None) -> tuple[str, ...]:
    """Return edge provenance tags meeting or exceeding ``min_confidence``."""
    threshold = CONFIDENCE_RANK[normalize_confidence_level(min_confidence)]
    return tuple(
        prov
        for prov in ("declared_spec", "ast_symbol", "ast_import", "ast_http", "inferred_ownership")
        if CONFIDENCE_RANK[PROVENANCE_TO_CONFIDENCE[prov]] >= threshold
    )


def upsert_edge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    rel_type: str,
    provenance: str = "declared_spec",
    evidence: str = "",
) -> None:
    """Insert or update an edge enforcing canonical provenance precedence."""
    conn.execute(_UPSERT_EDGE_SQL, (source_id, target_id, rel_type, provenance, evidence or ""))


NON_AST_SYMBOL_KINDS: frozenset[str] = frozenset(
    {
        "specification_file",
        "contract_path",
        "test_binding",
        "package_path",
        "owned_path",
        "source_path",
        "inferred_path",
    }
)


def build_spec_path_resolver(conn: sqlite3.Connection) -> Any:
    """Build a callable resolving relative .adoc paths (with or without 'eidos/' prefix) to canonical entity IDs."""
    path_to_id: dict[str, str] = {}
    for p_id, p_fp in conn.execute("SELECT id, file_path FROM entities WHERE type != 'invariant';").fetchall():
        if p_id and p_fp:
            clean_fp = str(p_fp).replace("\\", "/").strip().lstrip("./").lower()
            if clean_fp:
                path_to_id[clean_fp] = str(p_id)
                if clean_fp.startswith("eidos/"):
                    path_to_id[clean_fp[6:]] = str(p_id)
                else:
                    path_to_id[f"eidos/{clean_fp}"] = str(p_id)

    def _resolve_ref(raw_val: Any) -> str:
        val = str(raw_val or "").strip()
        if not val:
            return ""
        norm = val.replace("\\", "/").lstrip("./").lower()
        if norm in path_to_id:
            return path_to_id[norm]
        if "/" in norm or norm.endswith(".adoc"):
            for cand_path, cand_id in path_to_id.items():
                if cand_path.endswith(f"/{norm}"):
                    return cand_id
        return val

    return _resolve_ref


def resolve_symbol_locations_in_db(
    conn: sqlite3.Connection,
    entity_id: str,
    kind: str,
    symbol: str,
) -> list[dict[str, Any]]:
    """Resolve persisted source coordinates (one entry per definition) for an AST symbol mapping."""
    cursor = conn.cursor()
    if kind == "method" and "." in symbol:
        parent, name = symbol.rsplit(".", 1)
        cursor.execute(
            """
            SELECT file_path, line_start, line_end, signature, parent_symbol FROM code_symbols
            WHERE entity_id = ? AND kind = 'method' AND parent_symbol = ? AND symbol = ?
            ORDER BY file_path, line_start;
            """,
            (entity_id, parent, name),
        )
    else:
        cursor.execute(
            """
            SELECT file_path, line_start, line_end, signature, parent_symbol FROM code_symbols
            WHERE entity_id = ? AND kind = ? AND symbol = ?
            ORDER BY file_path, line_start;
            """,
            (entity_id, kind, symbol),
        )
    return [
        {
            "file": row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "signature": row["signature"],
            "parent_symbol": row["parent_symbol"] or None,
        }
        for row in cursor.fetchall()
    ]
