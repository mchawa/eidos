"""Code ownership resolution, AST symbol persistence, and polyglot import graph derivation (CMP-INCREMENTAL-SYNC-ENGINE)."""

from __future__ import annotations

import contextlib
import os
import posixpath
import re
import sqlite3
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import AstGrepIndexer
from eidos.core.index.ast_preprocess import CodeSymbol
from eidos.core.index.http_linker import _derive_http_request_edges
from eidos.core.index.storage import _resolve_conn_db_path, upsert_edge
from eidos.core.workspace.project import (
    STANDARD_EXCLUDED_DIRS,
    _is_test_dir_segment,
    discover_nested_subproject_roots,
    extract_entity_paths,
    is_dotnet_bin_dir,
    is_polyglot_test_file,
    match_path_pattern,
)

_CODE_EXTS = frozenset(AstGrepIndexer.LANGUAGE_MAP.keys())
_FILE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{3,}")


def _json_dumps(data: Any) -> str:
    return orjson.dumps(data, default=str).decode("utf-8")


def _normalize_rel_path(path: str) -> str:
    from eidos.core.workspace.project import normalize_rel_path

    return normalize_rel_path(path, strip_leading_slash=False, strip_trailing_slash=True)


def _upsert_file_tokens_fts(conn: sqlite3.Connection, rel_path: str, doc_type: str, text: str) -> None:
    """Replace the FTS5 token row for ``rel_path`` with deduplicated identifier/entity tokens."""
    conn.execute("DELETE FROM file_tokens_fts WHERE rel_path = ?;", (rel_path,))
    if not text:
        return
    unique_tokens = dict.fromkeys(_FILE_TOKEN_RE.findall(text.lower()))
    if unique_tokens:
        conn.execute(
            "INSERT INTO file_tokens_fts (rel_path, doc_type, tokens) VALUES (?, ?, ?);",
            (rel_path, doc_type, " ".join(unique_tokens)),
        )


class _CodeOwnershipResolver:
    """Deterministic, spec-order-independent attribution of source files to owning components/containers.

    Built once per sync from the persisted specification state, so every file resolves identically no matter
    which spec was processed first. Precedence (DLT-2026-034, DLT-2026-112):

    1. exact literal ``source_path`` declared by a ``tech/component`` (lowest component id wins; every other declaring
       entity is kept as a secondary owner, see ``owners()``);
    2. highest-scoring ``owned_path`` pattern (``match_path_pattern``), prioritizing ``tech/component`` over ``tech/container``;
    3. longest ``package_path`` prefix (path-boundary aware) across all entries of all components and containers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._component_sources: dict[str, str] = {}
        self._component_patterns: list[tuple[str, str]] = []
        self._declared_by: dict[str, set[str]] = {}
        for row in conn.execute("SELECT id, data_json FROM entities WHERE type = 'tech/component' ORDER BY id;"):
            try:
                fm = orjson.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(fm, dict):
                continue
            raw = fm.get("source_path")
            paths = raw if isinstance(raw, list) else ([raw] if raw else [])
            cid = str(row["id"] or "")
            for sp in paths:
                norm = _normalize_rel_path(str(sp))
                if not norm:
                    continue
                if any(ch in norm for ch in "*?["):
                    if cid:
                        self._component_patterns.append((norm, cid))
                    continue
                if cid and "." not in norm.rsplit("/", 1)[-1]:
                    self._component_patterns.append((norm, cid))
                self._component_sources.setdefault(norm, cid)
                self._declared_by.setdefault(norm, set()).add(cid)

        self._owned: list[tuple[str, str]] = [
            (r["symbol"], r["entity_id"])
            for r in conn.execute(
                """
                SELECT s.symbol, s.entity_id
                FROM symbol_mappings s
                LEFT JOIN entities e ON e.id = s.entity_id
                WHERE s.kind = 'owned_path'
                ORDER BY CASE WHEN e.type = 'tech/component' THEN 0 ELSE 1 END, s.entity_id, s.symbol;
                """
            )
        ]
        self._scan_dirs: set[str] = set()
        for sym, ent_id in self._owned:
            norm = _normalize_rel_path(sym)
            if not norm:
                continue
            if any(ch in norm for ch in "*?["):
                prefix_parts = []
                for part in norm.split("/"):
                    if any(ch in part for ch in "*?["):
                        break
                    prefix_parts.append(part)
                if prefix_parts:
                    self._scan_dirs.add("/".join(prefix_parts))
            else:
                self._scan_dirs.add(norm)
                if "." in norm.rsplit("/", 1)[-1]:
                    self._declared_by.setdefault(norm, set()).add(ent_id)
        self._packages: list[tuple[str, str]] = []
        for r in conn.execute(
            """
            SELECT s.symbol, s.entity_id
            FROM symbol_mappings s
            LEFT JOIN entities e ON e.id = s.entity_id
            WHERE s.kind = 'package_path'
            ORDER BY CASE WHEN e.type = 'tech/component' THEN 0 ELSE 1 END, s.entity_id, s.symbol;
            """
        ):
            norm = _normalize_rel_path(r["symbol"])
            if not norm:
                continue
            if any(ch in norm for ch in "*?["):
                prefix_parts = []
                for part in norm.split("/"):
                    if any(ch in part for ch in "*?["):
                        break
                    prefix_parts.append(part)
                if prefix_parts:
                    self._scan_dirs.add("/".join(prefix_parts))
                continue
            self._packages.append((norm, r["entity_id"]))
            self._scan_dirs.add(norm)
            if "." in norm.rsplit("/", 1)[-1]:  # literal file declared as package_path
                self._declared_by.setdefault(norm, set()).add(r["entity_id"])

    def package_dirs(self) -> set[str]:
        """All literal ``package_path`` entries and non-glob ``owned_path`` prefixes (callers filter for existing directories)."""
        return set(self._scan_dirs) | {pp for pp, _ in self._packages}

    def declared_owners(self, rel_path: str) -> set[str]:
        """Entities that explicitly declare ``rel_path`` as a source/package file."""
        return self._declared_by.get(_normalize_rel_path(rel_path), set())

    def declared_files(self) -> set[str]:
        """Every literal source file explicitly declared by any entity."""
        return set(self._declared_by)

    def owners(self, rel_path: str) -> list[str]:
        """Entity IDs owning a file: the resolved primary owner first, then every other entity
        that explicitly declares the file (sorted by id). Empty when the file is unowned."""
        primary = self.resolve(rel_path)
        if not primary:
            return []
        rel = _normalize_rel_path(rel_path)
        result = [primary]
        for entity_id in sorted(self._declared_by.get(rel, set()) - {primary}):
            result.append(entity_id)
        return result

    def resolve(self, rel_path: str) -> str | None:
        """Return owning entity id or None for a repo-relative source path."""
        rel = _normalize_rel_path(rel_path)
        hit = self._component_sources.get(rel)
        if hit:
            return hit

        best_score = 0
        owner: str | None = None
        for pattern, entity_id in self._component_patterns:
            score = match_path_pattern(rel, pattern)
            if score > best_score:
                best_score, owner = score, entity_id
        if owner:
            return owner

        for pattern, entity_id in self._owned:
            score = match_path_pattern(rel, pattern)
            if score > best_score:
                best_score, owner = score, entity_id
        if owner:
            return owner

        best_len = -1
        for pkg, entity_id in self._packages:
            if (rel == pkg or rel.startswith(pkg + "/")) and len(pkg) > best_len:
                best_len, owner = len(pkg), entity_id
        return owner


def store_code_symbols(
    conn: sqlite3.Connection,
    rel_path: str,
    owners: list[str],
    symbols: list[Any],
    imports: list[str] | None = None,
    *,
    is_test: bool = False,
) -> None:
    """Replace all persisted symbols of one source or test file (delete-then-insert)."""
    conn.execute("DELETE FROM code_symbols WHERE file_path = ?;", (rel_path,))
    is_test_int = 1 if is_test else 0

    for position, entity_id in enumerate(owners):
        is_prim = 1 if position == 0 else 0
        for sym in symbols:
            parent = sym.parent_symbol or ""
            conn.execute(
                """
                INSERT OR IGNORE INTO code_symbols
                (file_path, symbol, kind, parent_symbol, line_start, line_end, signature, entity_id, is_primary, is_test)
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
                    is_prim,
                    is_test_int,
                ),
            )
        for imp_entry in imports or ():
            if isinstance(imp_entry, tuple):
                imp, imp_line = imp_entry
            else:
                imp, imp_line = imp_entry, 0
            if imp:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO code_symbols
                    (file_path, symbol, kind, parent_symbol, line_start, line_end, signature, entity_id, is_primary, is_test)
                    VALUES (?, ?, 'import', '', ?, ?, '', ?, ?, ?);
                    """,
                    (rel_path, imp, imp_line, imp_line, entity_id, is_prim, is_test_int),
                )


def restore_code_mappings(conn: sqlite3.Connection, entity_id: str) -> None:
    """Re-create ``source_path`` rows in ``symbol_mappings`` for an entity from persisted code state."""
    ent_row = conn.execute("SELECT data_json FROM entities WHERE id = ?;", (entity_id,)).fetchone()
    patterns: list[str] = []
    if ent_row and ent_row["data_json"]:
        try:
            fm = orjson.loads(ent_row["data_json"])
            if isinstance(fm, dict):
                patterns = extract_entity_paths(fm)
        except (TypeError, ValueError):
            patterns = []

    cand_files = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT file_path FROM code_symbols WHERE entity_id = ? AND is_test = 0
            UNION
            SELECT rel_path FROM indexed_files WHERE doc_type = 'code/source' AND doc_id = ?;
            """,
            (entity_id, entity_id),
        ).fetchall()
        if r[0]
    ]
    valid_files = [
        fp
        for fp in cand_files
        if os.path.splitext(fp)[1].lower() in _CODE_EXTS and any(match_path_pattern(fp, pat) > 0 for pat in patterns)
    ]
    for fp in valid_files:
        conn.execute(
            "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, 'source_path');",
            (fp, entity_id),
        )


_SOURCE_AST_MEM_CACHE: dict[tuple[type[Any], str, str], tuple[list[Any], list[str]]] = {}
_TEST_BINDING_MEM_CACHE: dict[tuple[type[Any], str, str], tuple[dict[str, list[str]], str, str]] = {}


def _can_use_ast_mem_cache(ast_idx: Any) -> bool:
    cls = type(ast_idx)
    inst_dict = getattr(ast_idx, "__dict__", {})
    return (
        cls.__module__.startswith("eidos.")
        and "extract_symbols" not in inst_dict
        and "extract_imports" not in inst_dict
        and "extract_declared_namespaces" not in inst_dict
        and "extract_http_endpoints" not in inst_dict
        and "extract_http_requests" not in inst_dict
        and "extract_test_bindings" not in inst_dict
        and "list_test_functions" not in inst_dict
    )


def _find_import_line(content: str | None, imp: str) -> int:
    """Locate 1-based line number of an import target in source text (defaults to 1)."""
    if not content or not imp:
        return 1
    clean = imp.lstrip(".")
    leaf = clean.rsplit("/", 1)[-1].rsplit(".", 1)[-1].rsplit("::", 1)[-1].rstrip("*")
    for idx, line in enumerate(content.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith(("#", "//", "/*", "*")):
            continue
        if (clean and clean in s) or (
            leaf
            and leaf in s
            and any(kw in s for kw in ("import", "from", "using", "use ", "mod ", "require", "#include"))
        ):
            return idx
    return 1


def index_source_file(
    conn: sqlite3.Connection,
    ast_idx: Any,
    path: Path,
    rel_path: str,
    mtime: float,
    sha: str,
    resolver: _CodeOwnershipResolver,
    raw_bytes: bytes | None = None,
) -> None:
    """Index one non-test source or governed text file for all its resolved owners (one AST parse)."""
    owners = resolver.owners(rel_path)
    owner_ids = set(owners)
    is_ast_code = path.suffix.lower() in _CODE_EXTS
    keep = (resolver.declared_owners(rel_path) | owner_ids) if is_ast_code else resolver.declared_owners(rel_path)
    for stale in conn.execute(
        "SELECT entity_id FROM symbol_mappings WHERE symbol = ? AND kind = 'source_path';", (rel_path,)
    ).fetchall():
        if stale["entity_id"] not in keep:
            conn.execute(
                "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = 'source_path';",
                (rel_path, stale["entity_id"]),
            )
    if is_ast_code:
        for entity_id in sorted(owner_ids):
            conn.execute(
                "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, 'source_path');",
                (rel_path, entity_id),
            )

    if raw_bytes is not None:
        content: str | None = raw_bytes.decode("utf-8", errors="replace")
    else:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = None

    if owners and is_ast_code:
        use_cache = bool(sha) and _can_use_ast_mem_cache(ast_idx)
        cache_key = (type(ast_idx), str(path.resolve()), sha) if use_cache else None
        cached = _SOURCE_AST_MEM_CACHE.get(cache_key) if cache_key is not None else None
        if cached is not None:
            symbols, imports = cached
        else:
            symbols = list(ast_idx.extract_symbols(path, content))
            if path.suffix.lower() in (".cs", ".java", ".kt", ".kts") and hasattr(
                ast_idx, "extract_declared_namespaces"
            ):
                for ns in ast_idx.extract_declared_namespaces(path, content):
                    symbols.append(
                        CodeSymbol(
                            name=ns,
                            kind="namespace",
                            file_path=rel_path,
                            line_start=1,
                            line_end=1,
                        )
                    )
            base_symbols = list(symbols)
            if hasattr(ast_idx, "extract_http_endpoints"):
                ep_fn = ast_idx.extract_http_endpoints
                ep_syms = (
                    ep_fn(path, content, _symbols=base_symbols)
                    if type(ast_idx) is AstGrepIndexer
                    and "extract_http_endpoints" not in getattr(ast_idx, "__dict__", {})
                    else ep_fn(path, content)
                )
                for ep in ep_syms:
                    ep.file_path = rel_path
                    symbols.append(ep)
            if hasattr(ast_idx, "extract_http_requests"):
                rq_fn = ast_idx.extract_http_requests
                rq_syms = (
                    rq_fn(path, content, _symbols=base_symbols)
                    if type(ast_idx) is AstGrepIndexer
                    and "extract_http_requests" not in getattr(ast_idx, "__dict__", {})
                    else rq_fn(path, content)
                )
                for rq in rq_syms:
                    rq.file_path = rel_path
                    symbols.append(rq)
            raw_imports = ast_idx.extract_imports(path, content) if hasattr(ast_idx, "extract_imports") else []
            imports: list[Any] = [(imp, _find_import_line(content, imp)) for imp in raw_imports]
            if cache_key is not None:
                _SOURCE_AST_MEM_CACHE[cache_key] = (symbols, imports)
    else:
        symbols = []
        imports = []
    store_code_symbols(conn, rel_path, owners, symbols, imports=imports)
    conn.execute(
        """
        INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type)
        VALUES (?, ?, ?, ?, 'code/source');
        """,
        (rel_path, mtime, sha, owners[0] if owners else "source"),
    )
    _upsert_file_tokens_fts(conn, rel_path, "code/source", content or "")


def build_module_to_file_map(known_files: Iterable[str]) -> dict[str, str]:
    """Map polyglot import module names and stem paths to repo-relative source file paths."""
    module_to_file: dict[str, str] = {}
    for kf in sorted(known_files):
        if kf.endswith(".py"):
            no_ext = kf[:-3]
            if no_ext.endswith("/__init__"):
                pkg_dotted = no_ext[:-9].replace("/", ".")
                module_to_file.setdefault(pkg_dotted, kf)
                if pkg_dotted.startswith("src."):
                    module_to_file.setdefault(pkg_dotted[4:], kf)
            mod_dotted = no_ext.replace("/", ".")
            module_to_file.setdefault(mod_dotted, kf)
            if mod_dotted.startswith("src."):
                module_to_file.setdefault(mod_dotted[4:], kf)
        else:
            stem_path, _ = posixpath.splitext(kf)
            module_to_file.setdefault(stem_path, kf)
            if stem_path.endswith("/index"):
                module_to_file.setdefault(stem_path[:-6], kf)
    return module_to_file


_GO_MOD_MODULE_RE = re.compile(r'^\s*module\s+["`]?([^\s"`]+)["`]?')
_RUST_STDLIB_CRATES = frozenset({"std", "core", "alloc", "proc_macro", "test"})


def discover_go_module_roots(
    repo_root: Path | None,
    known_files: Iterable[str],
) -> list[tuple[str, str]]:
    """Discover ``(rel_dir, module_path)`` pairs from ``go.mod`` files covering ``known_files``, longest module first."""
    if repo_root is None:
        return []
    eff_root = repo_root.resolve()
    candidate_dirs: set[str] = {""}
    for kf in known_files:
        norm = _normalize_rel_path(kf)
        if not norm.endswith(".go"):
            continue
        cur = posixpath.dirname(norm)
        while cur:
            candidate_dirs.add(cur)
            parent = posixpath.dirname(cur)
            if parent == cur:
                break
            cur = parent

    modules: list[tuple[str, str]] = []
    for rel_dir in sorted(candidate_dirs):
        go_mod = (eff_root / rel_dir / "go.mod") if rel_dir else (eff_root / "go.mod")
        if not go_mod.is_file():
            continue
        with contextlib.suppress(OSError):
            for raw_line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = raw_line.split("//", 1)[0].strip()
                if not stripped:
                    continue
                m = _GO_MOD_MODULE_RE.match(stripped)
                if m:
                    mod_path = m.group(1).strip().rstrip("/")
                    if mod_path:
                        modules.append((rel_dir, mod_path))
                    break
    modules.sort(key=lambda item: (-len(item[1]), -len(item[0]), item[0]))
    return modules


def discover_rust_crate_map(
    repo_root: Path | None,
    known_files: Iterable[str],
) -> dict[str, str]:
    """Map normalized Rust workspace library crate names (``-`` -> ``_``) to their repo-relative source root directory."""
    norm_known = {_normalize_rel_path(kf) for kf in known_files if kf}
    eff_root = repo_root.resolve() if repo_root is not None else None
    crate_map: dict[str, str] = {}

    # 1. Discover Cargo.toml manifests along ancestor directories of .rs files (handles unindexed lib.rs & custom [lib].path).
    if eff_root is not None:
        pkg_dirs: set[str] = {""}
        for kf in norm_known:
            if not kf.endswith(".rs"):
                continue
            cur = posixpath.dirname(kf)
            while cur:
                pkg_dirs.add(cur)
                parent = posixpath.dirname(cur)
                if parent == cur:
                    break
                cur = parent
        for pkg_dir in sorted(pkg_dirs):
            cargo_toml = (eff_root / pkg_dir / "Cargo.toml") if pkg_dir else (eff_root / "Cargo.toml")
            if not cargo_toml.is_file():
                continue
            with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
                parsed = tomllib.loads(cargo_toml.read_text(encoding="utf-8", errors="replace"))
                pkg_tbl = parsed.get("package") if isinstance(parsed, dict) else None
                lib_tbl = parsed.get("lib") if isinstance(parsed, dict) else None
                if not isinstance(pkg_tbl, dict):
                    continue
                lib_path_rel = str(lib_tbl.get("path", "")).strip() if isinstance(lib_tbl, dict) else ""
                default_lib = f"{pkg_dir}/src/lib.rs" if pkg_dir else "src/lib.rs"
                explicit_lib = (
                    _normalize_rel_path(posixpath.normpath(posixpath.join(pkg_dir, lib_path_rel)))
                    if lib_path_rel
                    else ""
                )
                has_lib = bool(
                    isinstance(lib_tbl, dict)
                    or default_lib in norm_known
                    or (eff_root / default_lib).is_file()
                    or (explicit_lib and (explicit_lib in norm_known or (eff_root / explicit_lib).is_file()))
                )
                if not has_lib:
                    continue
                raw_name = ""
                if isinstance(lib_tbl, dict) and isinstance(lib_tbl.get("name"), str) and lib_tbl["name"].strip():
                    raw_name = lib_tbl["name"].strip()
                elif isinstance(pkg_tbl.get("name"), str) and pkg_tbl["name"].strip():
                    raw_name = pkg_tbl["name"].strip()
                if not raw_name:
                    continue
                crate_slug = raw_name.replace("-", "_")
                src_dir = posixpath.dirname(explicit_lib) if explicit_lib else (f"{pkg_dir}/src" if pkg_dir else "src")
                crate_map.setdefault(crate_slug, src_dir)

    # 2. Fallback for library crates present in known_files without Cargo.toml on disk (never shadow Rust stdlib names).
    known_src_dirs = set(crate_map.values())
    for kf in sorted(norm_known):
        if not (kf.endswith("/lib.rs") or kf == "lib.rs"):
            continue
        src_dir = posixpath.dirname(kf)
        if src_dir in known_src_dirs:
            continue
        pkg_dir = posixpath.dirname(src_dir) if posixpath.basename(src_dir) == "src" else src_dir
        dir_slug = posixpath.basename(pkg_dir).replace("-", "_") if pkg_dir else ""
        if dir_slug and dir_slug not in _RUST_STDLIB_CRATES:
            crate_map.setdefault(dir_slug, src_dir)

    return crate_map


_discover_rust_crate_map = discover_rust_crate_map


def _rust_crate_root_for(src_file: str, known_files: set[str]) -> str:
    """Find nearest enclosing Rust crate source root directory for ``src_file``."""
    cur = posixpath.dirname(src_file)
    src_fallback: str | None = None
    while True:
        lib_cand = f"{cur}/lib.rs" if cur else "lib.rs"
        main_cand = f"{cur}/main.rs" if cur else "main.rs"
        if lib_cand in known_files or main_cand in known_files:
            return cur
        if src_fallback is None and posixpath.basename(cur) == "src":
            src_fallback = cur
        if not cur:
            break
        parent = posixpath.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return src_fallback if src_fallback is not None else ""


def _resolve_rust_module_in_dir(
    base_dir: str,
    rel_segs: list[str],
    known_files: set[str],
    *,
    src_file: str,
    allow_lib: bool = True,
    allow_main: bool = True,
    allow_root_fallback: bool = True,
) -> str | None:
    """Resolve Rust module segments under ``base_dir`` checking ``<path>.rs`` and ``<path>/mod.rs`` longest-first."""
    for k in range(len(rel_segs), 0, -1):
        prefix = "/".join(rel_segs[:k])
        cand_base = f"{base_dir}/{prefix}".strip("/") if base_dir else prefix
        for cand in (f"{cand_base}.rs", f"{cand_base}/mod.rs"):
            if cand in known_files and cand != src_file:
                return cand
    if not allow_root_fallback:
        return None
    fallbacks: list[str] = [
        f"{base_dir}/mod.rs" if base_dir else "mod.rs",
        f"{base_dir}.rs" if base_dir else "",
    ]
    if allow_lib:
        fallbacks.append(f"{base_dir}/lib.rs" if base_dir else "lib.rs")
    if allow_main:
        fallbacks.append(f"{base_dir}/main.rs" if base_dir else "main.rs")
    for fallback in fallbacks:
        if fallback and fallback in known_files and fallback != src_file:
            return fallback
    return None


def _resolve_rust_import_file(
    src_file: str,
    imp: str,
    known_files: set[str],
    rust_crates: dict[str, str] | None = None,
) -> str | None:
    """Resolve a Rust ``use`` or ``mod`` import specifier to a repo-relative ``.rs`` file."""
    norm_src = _normalize_rel_path(src_file)
    clean = imp.lstrip(":").strip()
    segs = [s for s in clean.split("::") if s and s != "*"]
    if not segs:
        return None
    base_name = posixpath.basename(norm_src)
    src_dir = posixpath.dirname(norm_src)
    is_mod_root = base_name in ("lib.rs", "main.rs", "mod.rs")

    if segs[0] == "crate":
        crate_root = _rust_crate_root_for(norm_src, known_files)
        return _resolve_rust_module_in_dir(
            crate_root,
            segs[1:],
            known_files,
            src_file=norm_src,
            allow_lib=(base_name != "main.rs"),
            allow_main=(base_name != "lib.rs"),
        )

    if segs[0] == "self":
        sub_dir = src_dir if is_mod_root else posixpath.splitext(norm_src)[0]
        return _resolve_rust_module_in_dir(
            sub_dir,
            segs[1:],
            known_files,
            src_file=norm_src,
            allow_root_fallback=False,
        )

    if segs[0] == "super":
        n_super = 0
        while n_super < len(segs) and segs[n_super] == "super":
            n_super += 1
        ups = n_super if is_mod_root else (n_super - 1)
        parts = [p for p in src_dir.split("/") if p]
        crate_root = _rust_crate_root_for(norm_src, known_files)
        crate_depth = len([p for p in crate_root.split("/") if p])
        if len(parts) - ups < crate_depth:
            return None
        target_dir = "/".join(parts[: len(parts) - ups])
        return _resolve_rust_module_in_dir(
            target_dir,
            segs[n_super:],
            known_files,
            src_file=norm_src,
            allow_lib=(base_name != "main.rs"),
            allow_main=(base_name != "lib.rs"),
        )

    if rust_crates and segs[0] in rust_crates:
        return _resolve_rust_module_in_dir(
            rust_crates[segs[0]],
            segs[1:],
            known_files,
            src_file=norm_src,
            allow_lib=True,
            allow_main=False,
        )

    return None


def _resolve_go_import_files(
    src_file: str,
    imp: str,
    known_files: set[str],
    go_modules: list[tuple[str, str]],
    go_files_by_dir: dict[str, list[str]] | None = None,
) -> list[str]:
    """Resolve a Go package import path to all non-test ``.go`` files in the target intra-repo package directory."""
    if not imp:
        return []
    norm_src = _normalize_rel_path(src_file)
    if go_files_by_dir is None:
        go_files_by_dir = {}
        for kf in sorted(known_files):
            norm_kf = _normalize_rel_path(kf)
            if norm_kf.endswith(".go") and not norm_kf.endswith("_test.go"):
                go_files_by_dir.setdefault(posixpath.dirname(norm_kf), []).append(norm_kf)

    clean_imp = imp.strip().rstrip("/")
    if go_modules:
        for mod_dir, mod_path in go_modules:
            if clean_imp == mod_path or clean_imp.startswith(f"{mod_path}/"):
                sub = clean_imp[len(mod_path) :].lstrip("/")
                target_dir = f"{mod_dir}/{sub}".strip("/") if mod_dir else sub
                return [f for f in go_files_by_dir.get(target_dir, ()) if f != norm_src]
        return []

    return [f for f in go_files_by_dir.get(clean_imp, ()) if f != norm_src]


def resolve_import_target_files(
    src_file: str,
    imp: str,
    known_files: set[str],
    module_to_file: dict[str, str],
    *,
    fallback_parent: bool = False,
    repo_root: Path | None = None,
    go_modules: list[tuple[str, str]] | None = None,
    go_files_by_dir: dict[str, list[str]] | None = None,
    rust_crates: dict[str, str] | None = None,
) -> list[str]:
    """Resolve one extracted AST import string to one or more known repo-relative source files (Go/Rust aware)."""
    if not src_file or not imp:
        return []
    norm_src = _normalize_rel_path(src_file)
    norm_known = (
        known_files
        if all("\\" not in f and not f.startswith("./") for f in known_files)
        else {_normalize_rel_path(f) for f in known_files}
    )
    if norm_src.endswith(".go"):
        eff_go_mods = go_modules if go_modules is not None else discover_go_module_roots(repo_root, norm_known)
        return _resolve_go_import_files(norm_src, imp, norm_known, eff_go_mods, go_files_by_dir=go_files_by_dir)
    if norm_src.endswith(".rs"):
        eff_crates = rust_crates if rust_crates is not None else discover_rust_crate_map(repo_root, norm_known)
        rs_hit = _resolve_rust_import_file(norm_src, imp, norm_known, rust_crates=eff_crates)
        return [rs_hit] if rs_hit else []
    single = resolve_import_target_file(norm_src, imp, norm_known, module_to_file, fallback_parent=fallback_parent)
    return [single] if single else []


def resolve_import_target_file(
    src_file: str,
    imp: str,
    known_files: set[str],
    module_to_file: dict[str, str],
    *,
    fallback_parent: bool = False,
    repo_root: Path | None = None,
) -> str | None:
    """Resolve one extracted AST import string from ``src_file`` to a known repo-relative source file."""
    if not src_file or not imp:
        return None
    norm_src = _normalize_rel_path(src_file)
    norm_known = (
        known_files
        if all("\\" not in f and not f.startswith("./") for f in known_files)
        else {_normalize_rel_path(f) for f in known_files}
    )
    if norm_src.endswith(".go"):
        go_hits = _resolve_go_import_files(
            norm_src,
            imp,
            norm_known,
            discover_go_module_roots(repo_root, norm_known),
        )
        return go_hits[0] if go_hits else None
    if norm_src.endswith(".rs"):
        return _resolve_rust_import_file(
            norm_src,
            imp,
            norm_known,
            rust_crates=discover_rust_crate_map(repo_root, norm_known),
        )
    if norm_src.endswith(".py"):
        if imp.startswith("."):
            level = len(imp) - len(imp.lstrip("."))
            rem = imp[level:].replace(".", "/")
            parts = norm_src.split("/")[:-1]
            if level - 1 <= len(parts):
                base = "/".join(parts[: len(parts) - (level - 1)])
                cand = f"{base}/{rem}".strip("/") if rem else base
                if f"{cand}.py" in norm_known:
                    return f"{cand}.py"
                if f"{cand}/__init__.py" in norm_known:
                    return f"{cand}/__init__.py"
            return None
        hit = module_to_file.get(imp)
        if hit is not None or not fallback_parent or "." not in imp:
            return hit
        return module_to_file.get(imp.rsplit(".", 1)[0])
    if imp.startswith("."):
        norm_imp = _normalize_rel_path(posixpath.normpath(posixpath.join(posixpath.dirname(norm_src), imp)))
        if norm_imp in norm_known:
            return norm_imp
        base_imp = (
            norm_imp[:-3] if norm_imp.endswith(".js") else (norm_imp[:-4] if norm_imp.endswith(".jsx") else norm_imp)
        )
        return module_to_file.get(base_imp)
    hit = module_to_file.get(imp)
    if hit is not None or not fallback_parent or "/" not in imp:
        return hit
    return module_to_file.get(imp.rsplit("/", 1)[0])


def _derive_ast_import_edges(
    conn: sqlite3.Connection,
    resolver: _CodeOwnershipResolver | None = None,
    repo_root: Path | None = None,
) -> None:
    """Derive component consumes_api and container depends_on edges from AST imports, namespaces, .csproj ProjectReferences, and contract links."""
    if resolver is None:
        resolver = _CodeOwnershipResolver(conn)

    conn.execute("DELETE FROM code_couplings;")
    conn.execute("DELETE FROM edges WHERE rel_type = 'consumes_api';")
    conn.execute("DELETE FROM edges WHERE rel_type = 'consumes' AND provenance = 'ast_http';")

    comp_to_cnt: dict[str, str] = {}
    for src_id, dst_id in conn.execute("SELECT source_id, target_id FROM edges WHERE rel_type = 'part_of';"):
        comp_to_cnt[src_id] = dst_id

    def _effective_cnt_and_comp(rel_path: str, ent_id: str = "") -> tuple[str, str]:
        if not ent_id:
            ent_id = resolver.resolve(rel_path) or ""
        if ent_id.startswith("CMP-"):
            return comp_to_cnt.get(ent_id, ""), ent_id
        return ent_id, ""

    def _record_coupling(
        src_cnt: str,
        src_cmp: str,
        dst_cnt: str,
        dst_cmp: str,
        provenance: str,
        src_file: str,
        line_no: int,
        target_ref: str,
    ) -> None:
        src_ent = src_cmp or src_cnt
        dst_ent = dst_cmp or dst_cnt
        if not src_ent or not dst_ent:
            return
        if src_ent == dst_ent and (not src_cnt or src_cnt == dst_cnt):
            return
        conn.execute(
            """
            INSERT OR IGNORE INTO code_couplings
            (source_entity_id, target_entity_id, source_cnt_id, target_cnt_id, provenance, src_file, line_start, target_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (src_ent, dst_ent, src_cnt, dst_cnt, provenance, src_file, max(1, int(line_no or 1)), target_ref),
        )

    known_files: set[str] = set()
    for (fp,) in conn.execute(
        "SELECT DISTINCT file_path FROM code_symbols WHERE is_test = 0 UNION SELECT rel_path FROM indexed_files WHERE doc_type = 'code/source';"
    ):
        if fp:
            known_files.add(_normalize_rel_path(fp))
    known_files.update(resolver.declared_files())

    eff_root = Path(repo_root).resolve() if repo_root is not None else None
    if eff_root is None:
        db_file = _resolve_conn_db_path(conn)
        if db_file is not None and db_file.parent.name == ".cache" and db_file.parent.parent.name == "eidos":
            eff_root = db_file.parents[2]

    module_to_file = build_module_to_file_map(known_files)
    go_modules = discover_go_module_roots(eff_root, known_files)
    go_files_by_dir: dict[str, list[str]] = {}
    for kf in sorted(known_files):
        if kf.endswith(".go") and not kf.endswith("_test.go"):
            go_files_by_dir.setdefault(posixpath.dirname(kf), []).append(kf)
    rust_crates = _discover_rust_crate_map(eff_root, known_files)

    ns_to_owners: dict[str, set[tuple[str, str]]] = {}
    file_declared_ns: dict[str, set[str]] = {}
    ns_owner_counts: dict[str, dict[tuple[str, str], int]] = {}
    for ns_sym, ns_file, ns_ent in conn.execute(
        "SELECT symbol, file_path, entity_id FROM code_symbols WHERE kind = 'namespace' AND is_primary = 1 AND is_test = 0;"
    ).fetchall():
        ns_name = str(ns_sym or "").strip()
        if ns_name:
            norm_ns_file = _normalize_rel_path(ns_file)
            owner = _effective_cnt_and_comp(norm_ns_file, str(ns_ent or ""))
            ns_to_owners.setdefault(ns_name, set()).add(owner)
            file_declared_ns.setdefault(norm_ns_file, set()).add(ns_name)
            owner_counts = ns_owner_counts.setdefault(ns_name, {})
            owner_counts[owner] = owner_counts.get(owner, 0) + 1

    ast_cnt_deps: dict[tuple[str, str], str] = {}
    csproj_cnt_deps: set[tuple[str, str]] = set()

    if eff_root is not None and eff_root.is_dir():
        nested_roots = set(discover_nested_subproject_roots(eff_root))
        pkg_dirs = resolver.package_dirs()
        seen_csproj: set[Path] = set()
        csproj_files: list[Path] = []
        for pkg_dir in sorted(pkg_dirs):
            cand_dir = (eff_root / pkg_dir).resolve()
            if not cand_dir.is_dir() or cand_dir in nested_roots:
                continue
            for dirpath, dirnames, filenames in os.walk(str(cand_dir)):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in STANDARD_EXCLUDED_DIRS
                    and not d.startswith(".")
                    and (Path(dirpath) / d).resolve() not in nested_roots
                    and not is_dotnet_bin_dir(Path(dirpath) / d)
                ]
                for fn in filenames:
                    if fn.lower().endswith((".csproj", ".fsproj", ".vbproj")):
                        cp = (Path(dirpath) / fn).resolve()
                        if cp not in seen_csproj:
                            seen_csproj.add(cp)
                            csproj_files.append(cp)
        with contextlib.suppress(OSError):
            for entry in os.scandir(eff_root):
                if entry.is_file() and entry.name.lower().endswith((".csproj", ".fsproj", ".vbproj")):
                    cp = Path(entry.path).resolve()
                    if cp not in seen_csproj:
                        seen_csproj.add(cp)
                        csproj_files.append(cp)
                elif (
                    entry.is_dir(follow_symlinks=False)
                    and not entry.name.startswith(".")
                    and entry.name not in STANDARD_EXCLUDED_DIRS
                ):
                    ep = Path(entry.path).resolve()
                    if ep not in nested_roots:
                        with contextlib.suppress(OSError):
                            for sub in os.scandir(ep):
                                if sub.is_file() and sub.name.lower().endswith((".csproj", ".fsproj", ".vbproj")):
                                    cp = Path(sub.path).resolve()
                                    if cp not in seen_csproj:
                                        seen_csproj.add(cp)
                                        csproj_files.append(cp)

        def _resolve_csproj_owner(proj_file: Path) -> tuple[str, str]:
            try:
                rel_proj = _normalize_rel_path(str(proj_file.relative_to(eff_root)))
            except ValueError:
                return "", ""
            cnt_id, cmp_id = _effective_cnt_and_comp(rel_proj)
            if cnt_id or cmp_id:
                return cnt_id, cmp_id
            rel_dir = posixpath.dirname(rel_proj)
            probe_cs = f"{rel_dir}/_.cs" if rel_dir else "_.cs"
            return _effective_cnt_and_comp(probe_cs)

        proj_ref_re = re.compile(r"""<ProjectReference\b[^>]*\bInclude\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
        for cp in sorted(csproj_files):
            try:
                rel_cp = _normalize_rel_path(str(cp.relative_to(eff_root)))
            except ValueError:
                continue
            if is_polyglot_test_file(rel_cp, repo_root=eff_root, package_paths=pkg_dirs) or _is_test_dir_segment(
                cp.parent.name, allow_root_names=True
            ):
                continue
            src_cnt, src_cmp = _resolve_csproj_owner(cp)
            if not src_cnt and not src_cmp:
                continue
            with contextlib.suppress(OSError):
                proj_text = cp.read_text(encoding="utf-8", errors="replace")
                for m_ref in proj_ref_re.finditer(proj_text):
                    inc_raw = m_ref.group(1).strip().replace("\\", "/")
                    if not inc_raw:
                        continue
                    target_cp = (cp.parent / inc_raw).resolve()
                    if not target_cp.is_relative_to(eff_root):
                        continue
                    rel_target = _normalize_rel_path(str(target_cp.relative_to(eff_root)))
                    if is_polyglot_test_file(
                        rel_target, repo_root=eff_root, package_paths=pkg_dirs
                    ) or _is_test_dir_segment(target_cp.parent.name, allow_root_names=True):
                        continue
                    dst_cnt, dst_cmp = _resolve_csproj_owner(target_cp)
                    ref_line = proj_text.count("\n", 0, m_ref.start()) + 1
                    ev = f"{rel_cp}:{ref_line} -> {rel_target}"
                    if (src_cmp and dst_cmp and src_cmp != dst_cmp) or (src_cnt and dst_cnt and src_cnt != dst_cnt):
                        _record_coupling(src_cnt, src_cmp, dst_cnt, dst_cmp, "ast_import", rel_cp, ref_line, rel_target)
                    if src_cmp and dst_cmp and src_cmp != dst_cmp:
                        upsert_edge(conn, src_cmp, dst_cmp, "consumes_api", "ast_import", ev)
                    if (
                        src_cnt
                        and dst_cnt
                        and src_cnt != dst_cnt
                        and src_cnt.startswith("CNT-")
                        and dst_cnt.startswith("CNT-")
                    ):
                        csproj_cnt_deps.add((src_cnt, dst_cnt))
                        ast_cnt_deps.setdefault((src_cnt, dst_cnt), ev)

    import_rows = conn.execute(
        """
        SELECT file_path, symbol, entity_id, line_start
        FROM code_symbols
        WHERE kind = 'import' AND is_primary = 1 AND is_test = 0
        ORDER BY file_path, line_start, symbol;
        """
    ).fetchall()

    for row in import_rows:
        src_file = _normalize_rel_path(row["file_path"])
        imp = str(row["symbol"] or "").strip()
        imp_line = max(1, int(row["line_start"] or 1))
        if not src_file or not imp:
            continue

        dst_files = resolve_import_target_files(
            src_file,
            imp,
            known_files,
            module_to_file,
            repo_root=eff_root,
            go_modules=go_modules,
            go_files_by_dir=go_files_by_dir,
            rust_crates=rust_crates,
        )

        if not dst_files:
            if src_file.endswith((".cs", ".java", ".kt", ".kts")):
                clean_imp = imp[:-2] if imp.endswith(".*") else imp
                matched_ns = ""
                if clean_imp in ns_to_owners:
                    matched_ns = clean_imp
                elif "." in clean_imp:
                    parent_ns = clean_imp.rsplit(".", 1)[0]
                    if parent_ns in ns_to_owners:
                        matched_ns = parent_ns
                if matched_ns and matched_ns not in file_declared_ns.get(src_file, ()):
                    target_owners = ns_to_owners[matched_ns]
                    src_cnt, src_cmp = _effective_cnt_and_comp(src_file, row["entity_id"])
                    src_owner = (src_cnt, src_cmp)
                    src_in_targets = src_owner in target_owners
                    counts_for_ns = ns_owner_counts.get(matched_ns, {})
                    src_ns_count = counts_for_ns.get(src_owner, 0)
                    for dst_cnt, dst_cmp in sorted(target_owners):
                        dst_owner = (dst_cnt, dst_cmp)
                        if dst_owner == src_owner:
                            continue
                        if (dst_cnt, src_cnt) in csproj_cnt_deps:
                            continue
                        if src_in_targets and not (
                            (src_cnt, dst_cnt) in csproj_cnt_deps
                            or (src_cnt == dst_cnt and counts_for_ns.get(dst_owner, 0) > src_ns_count)
                        ):
                            continue
                        ev_ns = f"{src_file}:{imp_line} -> namespace:{matched_ns}"
                        if (src_cmp and dst_cmp and src_cmp != dst_cmp) or (src_cnt and dst_cnt and src_cnt != dst_cnt):
                            _record_coupling(
                                src_cnt,
                                src_cmp,
                                dst_cnt,
                                dst_cmp,
                                "ast_import",
                                src_file,
                                imp_line,
                                f"namespace:{matched_ns}",
                            )
                        if src_cmp and dst_cmp and src_cmp != dst_cmp:
                            upsert_edge(conn, src_cmp, dst_cmp, "consumes_api", "ast_import", ev_ns)
                        if (
                            src_cnt
                            and dst_cnt
                            and src_cnt != dst_cnt
                            and src_cnt.startswith("CNT-")
                            and dst_cnt.startswith("CNT-")
                        ):
                            ast_cnt_deps.setdefault((src_cnt, dst_cnt), ev_ns)
            continue

        src_cnt, src_cmp = _effective_cnt_and_comp(src_file, row["entity_id"])
        seen_go_dst_owners: set[tuple[str, str]] = set()
        for dst_file in dst_files:
            if dst_file == src_file:
                continue
            dst_cnt, dst_cmp = _effective_cnt_and_comp(dst_file)
            if src_file.endswith(".go"):
                # Avoid multiplying identical Go package import couplings across every file in the same target component.
                dst_owner_key = (dst_cnt, dst_cmp)
                if dst_owner_key in seen_go_dst_owners:
                    continue
                seen_go_dst_owners.add(dst_owner_key)
                target_ref = posixpath.dirname(dst_file) or dst_file
            else:
                target_ref = dst_file
            ev_imp = f"{src_file}:{imp_line} -> {target_ref}"

            if (src_cmp and dst_cmp and src_cmp != dst_cmp) or (src_cnt and dst_cnt and src_cnt != dst_cnt):
                _record_coupling(src_cnt, src_cmp, dst_cnt, dst_cmp, "ast_import", src_file, imp_line, target_ref)
            if src_cmp and dst_cmp and src_cmp != dst_cmp:
                upsert_edge(conn, src_cmp, dst_cmp, "consumes_api", "ast_import", ev_imp)
            if src_cnt and dst_cnt and src_cnt != dst_cnt and src_cnt.startswith("CNT-") and dst_cnt.startswith("CNT-"):
                ast_cnt_deps.setdefault((src_cnt, dst_cnt), ev_imp)

    conn.execute("DELETE FROM edges WHERE rel_type = 'depends_on';")

    for src_cnt, dst_cnt, ctr_ev in conn.execute(
        """
        SELECT DISTINCT
            COALESCE(p_src.target_id, c_edge.source_id) AS src_cnt,
            COALESCE(p_prov.target_id, p_edge.source_id) AS dst_cnt,
            'contract:' || c_edge.target_id AS ctr_ev
        FROM edges c_edge
        LEFT JOIN edges p_src ON p_src.source_id = c_edge.source_id AND p_src.rel_type = 'part_of'
        JOIN edges p_edge ON p_edge.target_id = c_edge.target_id AND p_edge.rel_type = 'provides'
        LEFT JOIN edges p_prov ON p_prov.source_id = p_edge.source_id AND p_prov.rel_type = 'part_of'
        WHERE c_edge.rel_type = 'consumes'
          AND c_edge.provenance = 'declared_spec'
          AND COALESCE(p_src.target_id, c_edge.source_id) LIKE 'CNT-%'
          AND COALESCE(p_prov.target_id, p_edge.source_id) LIKE 'CNT-%'
          AND COALESCE(p_src.target_id, c_edge.source_id) != COALESCE(p_prov.target_id, p_edge.source_id)
        UNION
        SELECT DISTINCT
            COALESCE(p_src.target_id, c_edge.source_id) AS src_cnt,
            c_edge.target_id AS dst_cnt,
            c_edge.evidence AS ctr_ev
        FROM edges c_edge
        LEFT JOIN edges p_src ON p_src.source_id = c_edge.source_id AND p_src.rel_type = 'part_of'
        WHERE c_edge.rel_type = 'consumes'
          AND c_edge.provenance = 'declared_spec'
          AND COALESCE(p_src.target_id, c_edge.source_id) LIKE 'CNT-%'
          AND c_edge.target_id LIKE 'CNT-%'
          AND COALESCE(p_src.target_id, c_edge.source_id) != c_edge.target_id;
        """
    ).fetchall():
        upsert_edge(conn, src_cnt, dst_cnt, "depends_on", "declared_spec", ctr_ev or "")

    for (src_cnt, dst_cnt), ev_str in sorted(ast_cnt_deps.items()):
        upsert_edge(conn, src_cnt, dst_cnt, "depends_on", "ast_import", ev_str)

    for src_cnt, dst_cnt, api_ev in conn.execute(
        """
        SELECT DISTINCT
            p_src.target_id AS src_cnt,
            COALESCE(p_dst.target_id, api_edge.target_id) AS dst_cnt,
            api_edge.evidence
        FROM edges api_edge
        JOIN edges p_src ON p_src.source_id = api_edge.source_id AND p_src.rel_type = 'part_of'
        LEFT JOIN edges p_dst ON p_dst.source_id = api_edge.target_id AND p_dst.rel_type = 'part_of'
        WHERE api_edge.rel_type = 'consumes_api'
          AND p_src.target_id LIKE 'CNT-%'
          AND COALESCE(p_dst.target_id, api_edge.target_id) LIKE 'CNT-%'
          AND p_src.target_id != COALESCE(p_dst.target_id, api_edge.target_id);
        """
    ).fetchall():
        upsert_edge(conn, src_cnt, dst_cnt, "depends_on", "ast_import", api_ev or "")

    _derive_http_request_edges(conn, resolver, repo_root=eff_root)
