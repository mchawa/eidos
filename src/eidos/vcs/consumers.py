"""Deterministic untouched sibling consumer detection for diff review (CMP-DRIFT-DETECTOR).

Intersects modified, removed, and body-changed AST symbols in a Git diff with
SQLite-indexed polyglot reverse imports (`code_symbols`) and file token tables
(`file_tokens_fts`), surfacing non-blocking advisory consumers outside the diff.
"""

from __future__ import annotations

import ast
import contextlib
import difflib
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.ast_diff import AST_DIFF_EXTS
from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.index.ast_preprocess import _parse_python_ast
from eidos.core.index.code_graph import (
    build_module_to_file_map,
    discover_go_module_roots,
    discover_rust_crate_map,
    resolve_import_target_files,
)
from eidos.core.workspace.project import normalize_rel_path
from eidos.vcs.structural import _clean_symbol_name

_MIN_SYMBOL_TOKEN_LEN = 3
_SQLITE_IN_CHUNK = 400
_NAMESPACE_EXTS = (".cs", ".java", ".kt", ".kts")
_BARREL_FILENAMES = frozenset(
    {
        "__init__.py",
        "index.ts",
        "index.tsx",
        "index.mts",
        "index.cts",
        "index.js",
        "index.jsx",
        "index.mjs",
        "index.cjs",
        "mod.rs",
        "lib.rs",
    }
)
_WILDCARD_REEXPORT_RE = re.compile(r"(?:export\s+\*\s+from\b|import\s+\*|::\s*\*)")
_CHANGE_KIND_SEVERITY: dict[str, int] = {
    "file_deleted": 0,
    "symbol_removed": 1,
    "signature_changed": 2,
    "body_changed": 3,
    "symbol_added": 4,
}
_TOP_LEVEL_ASSIGN_RE = re.compile(
    r"^\s*(?:export\s+(?:const|let|var)\s+|pub(?:\([^)]*\))?\s+(?:const|static)\s+|const\s+|let\s+|var\s+|final\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]{2,})\s*(?::[^=\n]+)?=(?!=)"
)
_TOP_LEVEL_SKIP_NAMES = frozenset({"return", "yield", "raise", "assert", "import", "from", "elif", "while", "if"})


@dataclass
class _FileChangeSummary:
    """Extracted symbol-level change metadata for a single modified code file."""

    is_deleted: bool = False
    deleted_namespaces: list[str] = field(default_factory=list)
    symbol_kinds: dict[str, set[str]] = field(default_factory=dict)
    symbol_tokens: dict[str, str] = field(default_factory=dict)

    def add_symbol(self, display_name: str, token: str, kind: str) -> None:
        if not display_name or len(token) < _MIN_SYMBOL_TOKEN_LEN:
            return
        self.symbol_tokens.setdefault(display_name, token.lower())
        self.symbol_kinds.setdefault(display_name, set()).add(kind)


def _norm_path(path: str) -> str:
    return normalize_rel_path(path, strip_leading_slash=False, strip_trailing_slash=True)


def _token_for_symbol(parent: str, name: str) -> tuple[str, str]:
    """Return ``(display_name, lookup_token)`` resolving dunders/constructors/short methods to innermost parent class."""
    raw_parent = (parent or "").strip()
    clean_parent = raw_parent.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
    clean_name = (name or "").strip()
    display = f"{raw_parent}.{clean_name}" if raw_parent and clean_name else (clean_name or raw_parent)
    is_dunder = clean_name.startswith("__") and clean_name.endswith("__") and len(clean_name) >= 4
    if is_dunder or clean_name in ("constructor", "new") or len(clean_name) < _MIN_SYMBOL_TOKEN_LEN:
        token = clean_parent if len(clean_parent) >= _MIN_SYMBOL_TOKEN_LEN else ""
    else:
        token = clean_name
    return display, token.lower()


def _changed_line_ranges(old_text: str, new_text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return 1-based inclusive changed line ranges ``(old_ranges, new_ranges)`` via difflib."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    old_ranges: list[tuple[int, int]] = []
    new_ranges: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            old_ranges.append((i1 + 1, i2))
        if j2 > j1:
            new_ranges.append((j1 + 1, j2))
    return old_ranges, new_ranges


def _innermost_overlapping_symbols(symbols: list[Any], start: int, end: int) -> list[Any]:
    """Return overlapping non-import symbols, preferring innermost nested definitions over enclosing classes."""
    hits = [
        sym
        for sym in symbols
        if sym.kind not in ("import", "namespace") and sym.line_start <= end and sym.line_end >= start
    ]
    if len(hits) <= 1:
        return hits
    innermost = [
        cand
        for cand in hits
        if not any(
            other is not cand
            and other.line_start >= cand.line_start
            and other.line_end <= cand.line_end
            and (other.line_start > cand.line_start or other.line_end < cand.line_end)
            for other in hits
        )
    ]
    return innermost or hits


def _is_python_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) >= 4


def _extract_python_top_level_assigns(source: str) -> list[tuple[str, int, int]] | None:
    """Return ``[(name, line_start, line_end), ...]`` for Python module-scope assignments via ``_parse_python_ast``."""
    tree = _parse_python_ast(source)
    if tree is None:
        return None
    assigns: list[tuple[str, int, int]] = []
    for node in tree.body:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start) or start
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and len(tgt.id) >= _MIN_SYMBOL_TOKEN_LEN and not _is_python_dunder(tgt.id):
                    assigns.append((tgt.id, start, end))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and len(node.target.id) >= _MIN_SYMBOL_TOKEN_LEN
            and not _is_python_dunder(node.target.id)
        ):
            assigns.append((node.target.id, start, end))
    return assigns


def _extract_top_level_identifiers(lines: list[str], start: int, end: int) -> list[str]:
    """Extract non-Python module-scope constant/variable names modified outside any function or class body."""
    names: list[str] = []
    for line_no in range(max(1, start), min(len(lines), end) + 1):
        raw = lines[line_no - 1]
        if raw != raw.lstrip(" \t"):
            continue
        m = _TOP_LEVEL_ASSIGN_RE.match(raw)
        if m:
            ident = m.group(1)
            if ident.lower() not in _TOP_LEVEL_SKIP_NAMES and not _is_python_dunder(ident) and ident not in names:
                names.append(ident)
    return names


def _extract_file_change_summary(
    cf: str,
    old_c: str | None,
    new_c: str | None,
    *,
    smap: Any,
    symbol_diff: Any,
) -> _FileChangeSummary:
    """Summarize deleted, signature-changed, body-changed, and added symbols for one modified code file."""
    summary = _FileChangeSummary()
    indexer = get_ast_indexer()

    if old_c is not None and new_c is None:
        summary.is_deleted = True
        if cf.endswith(_NAMESPACE_EXTS):
            summary.deleted_namespaces = indexer.extract_declared_namespaces(cf, old_c)
        if cf.endswith((*_NAMESPACE_EXTS, ".go")):
            for s in indexer.extract_symbols(cf, old_c):
                display, tok = _token_for_symbol(s.parent_symbol or "", s.name)
                summary.add_symbol(display, tok, "file_deleted")
            if cf.endswith(".go"):
                old_lines = old_c.splitlines()
                for ident in _extract_top_level_identifiers(old_lines, 1, len(old_lines)):
                    if ident not in summary.symbol_kinds:
                        summary.add_symbol(ident, ident, "file_deleted")
        return summary

    if old_c is None and new_c is not None:
        if smap.supports(cf):
            for key in smap.get(cf, 1):
                parent = str(key[1]) if len(key) >= 3 and key[1] else ""
                base = _clean_symbol_name(None, (key[0], "", key[2])) if len(key) >= 3 else str(key[-1])
                display, tok = _token_for_symbol(parent, base)
                summary.add_symbol(display, tok, "symbol_added")
        return summary

    if old_c is None or new_c is None:
        return summary

    if symbol_diff is not None:
        for attr, kind_label in (
            ("removed", "symbol_removed"),
            ("changed", "signature_changed"),
            ("added", "symbol_added"),
        ):
            for key in getattr(symbol_diff, attr, ()) or ():
                parent = str(key[1]) if len(key) >= 3 and key[1] else ""
                base_clean = _clean_symbol_name(symbol_diff, (key[0], "", key[2])) if len(key) >= 3 else str(key[-1])
                display, tok = _token_for_symbol(parent, base_clean)
                summary.add_symbol(display, tok, kind_label)

    if Path(cf).suffix.lower() not in AST_DIFF_EXTS:
        return summary

    old_ranges, new_ranges = _changed_line_ranges(old_c, new_c)
    if not old_ranges and not new_ranges:
        return summary

    new_symbols = indexer.extract_symbols(cf, new_c)
    old_symbols = indexer.extract_symbols(cf, old_c)
    new_symbol_keys = {(s.parent_symbol or "", s.name) for s in new_symbols}
    old_lines = old_c.splitlines()
    new_lines = new_c.splitlines()

    # Detect removed private/internal symbols not present in public symbol_diff.
    for s in old_symbols:
        if (s.parent_symbol or "", s.name) not in new_symbol_keys:
            display, tok = _token_for_symbol(s.parent_symbol or "", s.name)
            if display not in summary.symbol_kinds:
                summary.add_symbol(display, tok, "symbol_removed")

    # Python AST module-level assignments handle multi-line constants accurately and ignore multiline strings.
    is_py = cf.lower().endswith(".py")
    py_old_assigns = _extract_python_top_level_assigns(old_c) if is_py else None
    py_new_assigns = _extract_python_top_level_assigns(new_c) if is_py else None
    py_assigns_ready = py_old_assigns is not None and py_new_assigns is not None
    if py_assigns_ready:
        new_assign_names = {name for name, _, _ in py_new_assigns or ()}
        for name, a_start, a_end in py_new_assigns or ():
            if any(a_start <= r_end and a_end >= r_start for r_start, r_end in new_ranges) and (
                name not in summary.symbol_kinds
            ):
                summary.add_symbol(name, name, "body_changed")
        for name, a_start, a_end in py_old_assigns or ():
            if any(a_start <= r_end and a_end >= r_start for r_start, r_end in old_ranges) and (
                name not in summary.symbol_kinds
            ):
                kind = "body_changed" if name in new_assign_names else "symbol_removed"
                summary.add_symbol(name, name, kind)

    # Map changed line ranges in new_c to enclosing AST symbols (and regex fallback for non-Python constants).
    for r_start, r_end in new_ranges:
        hits = _innermost_overlapping_symbols(new_symbols, r_start, r_end)
        if hits:
            for sym in hits:
                display, tok = _token_for_symbol(sym.parent_symbol or "", sym.name)
                existing = summary.symbol_kinds.get(display)
                if not existing or not (existing & {"symbol_removed", "signature_changed", "symbol_added"}):
                    summary.add_symbol(display, tok, "body_changed")
        elif not py_assigns_ready:
            for ident in _extract_top_level_identifiers(new_lines, r_start, r_end):
                if ident not in summary.symbol_kinds:
                    summary.add_symbol(ident, ident, "body_changed")

    # Map old-side changed/deleted ranges to surviving enclosing symbols or deleted non-Python constants.
    new_top_idents = (
        set(_extract_top_level_identifiers(new_lines, 1, len(new_lines))) if not py_assigns_ready else set()
    )
    for r_start, r_end in old_ranges:
        old_hits = _innermost_overlapping_symbols(old_symbols, r_start, r_end)
        if old_hits:
            for sym in old_hits:
                if (sym.parent_symbol or "", sym.name) not in new_symbol_keys:
                    continue
                display, tok = _token_for_symbol(sym.parent_symbol or "", sym.name)
                existing = summary.symbol_kinds.get(display)
                if not existing or not (existing & {"symbol_removed", "signature_changed", "symbol_added"}):
                    summary.add_symbol(display, tok, "body_changed")
        elif not py_assigns_ready:
            for ident in _extract_top_level_identifiers(old_lines, r_start, r_end):
                if ident not in summary.symbol_kinds:
                    kind = "body_changed" if ident in new_top_idents else "symbol_removed"
                    summary.add_symbol(ident, ident, kind)

    return summary


def _load_file_tokens(conn: Any, file_paths: Iterable[str]) -> dict[str, set[str]]:
    """Load lowercased identifier token sets from ``file_tokens_fts`` for the requested files."""
    target_set = {_norm_path(p) for p in file_paths if p}
    if not target_set:
        return {}
    tokens_by_file: dict[str, set[str]] = {}
    if len(target_set) <= _SQLITE_IN_CHUNK:
        chunk = sorted(target_set)
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT rel_path, tokens FROM file_tokens_fts WHERE rel_path IN ({placeholders});",
            chunk,
        ).fetchall()
    else:
        rows = conn.execute("SELECT rel_path, tokens FROM file_tokens_fts WHERE doc_type = 'code/source';").fetchall()
    for row in rows:
        rel = _norm_path(str(row["rel_path"] or ""))
        if rel not in target_set:
            continue
        raw_tokens = str(row["tokens"] or "")
        if raw_tokens:
            tokens_by_file[rel] = set(raw_tokens.split())
    return tokens_by_file


def _is_facade_candidate(mid_file: str, mid_owner: str, target_file: str, target_owner: str) -> bool:
    """True when ``mid_file`` is an eligible 1-hop re-export barrel or same-package/component facade."""
    if posixpath.basename(mid_file).lower() in _BARREL_FILENAMES:
        return True
    if mid_owner and target_owner and mid_owner == target_owner:
        return True
    return posixpath.dirname(mid_file) == posixpath.dirname(target_file)


def _is_wildcard_barrel(idx: Any, mid_file: str) -> bool:
    """True when a barrel file contains wildcard re-export syntax (``export * from``, ``import *``, ``::*``)."""
    if posixpath.basename(mid_file).lower() not in _BARREL_FILENAMES:
        return False
    root = getattr(idx, "root", None)
    if root is None:
        return False
    barrel_path = Path(root) / mid_file
    if not barrel_path.is_file():
        return False
    with contextlib.suppress(OSError):
        return bool(_WILDCARD_REEXPORT_RE.search(barrel_path.read_text(encoding="utf-8", errors="replace")))
    return False


def find_untouched_consumers(
    *,
    idx: Any,
    code_files: list[str],
    versions: dict[str, tuple[str | None, str | None]],
    smap: Any,
    symbol_diffs: dict[str, Any],
    all_changed_files: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Find untouched governed source files outside the diff importing and referencing modified symbols.

    Returns a deterministic, severity-sorted advisory list of dicts:
    ``{file, line, owner, target_file, symbols, change_kinds, via}``.
    """
    if idx is None or not code_files:
        return []

    excluded_diff_files = {_norm_path(f) for f in (*code_files, *all_changed_files) if f}
    summaries: dict[str, _FileChangeSummary] = {}
    deleted_files: set[str] = set()
    for cf in code_files:
        norm_cf = _norm_path(cf)
        if not norm_cf:
            continue
        old_c, new_c = versions.get(cf, (None, None))
        summary = _extract_file_change_summary(
            cf,
            old_c,
            new_c,
            smap=smap,
            symbol_diff=symbol_diffs.get(cf),
        )
        if summary.is_deleted or summary.symbol_kinds:
            summaries[norm_cf] = summary
            if summary.is_deleted:
                deleted_files.add(norm_cf)

    if not summaries:
        return []

    idx._ensure_synced()
    conn = idx._get_connection()

    known_files: set[str] = set(summaries.keys()) | deleted_files
    file_owner: dict[str, str] = {}
    for row in conn.execute(
        "SELECT DISTINCT file_path, entity_id FROM code_symbols WHERE is_primary = 1 AND is_test = 0;"
    ).fetchall():
        fp = _norm_path(str(row["file_path"] or ""))
        if fp:
            known_files.add(fp)
            ent = str(row["entity_id"] or "")
            if ent and fp not in file_owner:
                file_owner[fp] = ent
    for row in conn.execute("SELECT rel_path, doc_id FROM indexed_files WHERE doc_type = 'code/source';").fetchall():
        norm_fp = _norm_path(str(row["rel_path"] or ""))
        if norm_fp:
            known_files.add(norm_fp)
            doc_id = str(row["doc_id"] or "").strip()
            if doc_id and doc_id != "source" and norm_fp not in file_owner:
                file_owner[norm_fp] = doc_id

    module_to_file = build_module_to_file_map(known_files)
    idx_root = Path(idx.root) if getattr(idx, "root", None) is not None else None
    go_modules = discover_go_module_roots(idx_root, known_files)
    go_files_by_dir: dict[str, list[str]] = {}
    for kf in sorted(known_files):
        if kf.endswith(".go") and not kf.endswith("_test.go"):
            go_files_by_dir.setdefault(posixpath.dirname(kf), []).append(kf)
    rust_crates = discover_rust_crate_map(idx_root, known_files)

    # Namespace index for C#, Java, and Kotlin files (including namespaces extracted from deleted files).
    ns_to_files: dict[str, set[str]] = {}
    file_declared_ns: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT symbol, file_path FROM code_symbols WHERE kind = 'namespace' AND is_primary = 1 AND is_test = 0;"
    ).fetchall():
        ns_name = str(row["symbol"] or "").strip()
        ns_file = _norm_path(str(row["file_path"] or ""))
        if ns_name and ns_file:
            ns_to_files.setdefault(ns_name, set()).add(ns_file)
            file_declared_ns.setdefault(ns_file, set()).add(ns_name)
    for norm_cf, summary in summaries.items():
        for ns_name in summary.deleted_namespaces:
            ns_to_files.setdefault(ns_name, set()).add(norm_cf)
            file_declared_ns.setdefault(norm_cf, set()).add(ns_name)

    # Build reverse import adjacency: target_file -> {src_file: (earliest_line, owner_id, via_namespace)}.
    importers_by_target: dict[str, dict[str, tuple[int, str, bool]]] = {}
    for row in conn.execute(
        """
        SELECT file_path, symbol, entity_id, line_start
        FROM code_symbols
        WHERE kind = 'import' AND is_primary = 1 AND is_test = 0
        ORDER BY file_path, line_start, symbol;
        """
    ).fetchall():
        src_file = _norm_path(str(row["file_path"] or ""))
        imp = str(row["symbol"] or "").strip()
        line_no = max(1, int(row["line_start"] or 1))
        src_ent = str(row["entity_id"] or "") or file_owner.get(src_file, "")
        if not src_file or not imp:
            continue

        resolved_targets: list[tuple[str, bool]] = []
        dst_files = resolve_import_target_files(
            src_file,
            imp,
            known_files,
            module_to_file,
            repo_root=idx_root,
            go_modules=go_modules,
            go_files_by_dir=go_files_by_dir,
            rust_crates=rust_crates,
        )
        if dst_files:
            is_pkg_import = src_file.endswith(".go")
            resolved_targets.extend((_norm_path(d), is_pkg_import) for d in dst_files)
        elif src_file.endswith(_NAMESPACE_EXTS):
            probe = imp[:-2] if imp.endswith(".*") else imp
            matched_ns = ""
            while probe:
                if probe in ns_to_files:
                    matched_ns = probe
                    break
                if "." not in probe:
                    break
                probe = probe.rsplit(".", 1)[0]
            if matched_ns and matched_ns not in file_declared_ns.get(src_file, ()):
                resolved_targets.extend((t, True) for t in sorted(ns_to_files[matched_ns]))

        for dst, is_ns in resolved_targets:
            if not dst or dst == src_file:
                continue
            bucket = importers_by_target.setdefault(dst, {})
            prev = bucket.get(src_file)
            if prev is None or line_no < prev[0]:
                bucket[src_file] = (line_no, src_ent, is_ns)

    # Collect candidate files (1-hop direct importers + 2-hop barrel/facade importers) for one batched token query.
    candidate_token_files: set[str] = set()
    facade_candidates: dict[str, list[tuple[str, int, str]]] = {}
    for target_file, summary in summaries.items():
        direct_importers = importers_by_target.get(target_file, {})
        target_owner = file_owner.get(target_file, "")
        for src_file, (line_no, src_ent, _is_ns) in direct_importers.items():
            candidate_token_files.add(src_file)
            if (
                not summary.is_deleted
                and _is_facade_candidate(src_file, src_ent, target_file, target_owner)
                and src_file in importers_by_target
            ):
                facade_candidates.setdefault(target_file, []).append((src_file, line_no, src_ent))
                for hop2_file in importers_by_target[src_file]:
                    if hop2_file not in excluded_diff_files and hop2_file != target_file:
                        candidate_token_files.add(hop2_file)

    if not candidate_token_files:
        return []

    tokens_by_file = _load_file_tokens(conn, candidate_token_files)

    # Accumulate unique (consumer_file, target_file) records.
    results_map: dict[tuple[str, str], dict[str, Any]] = {}

    def _record_consumer(
        consumer_file: str,
        line_no: int,
        owner: str,
        target_file: str,
        matched_syms: list[str],
        change_kinds: set[str],
        via: str | None,
    ) -> None:
        key = (consumer_file, target_file)
        existing = results_map.get(key)
        if existing is None:
            results_map[key] = {
                "file": consumer_file,
                "line": line_no,
                "owner": owner,
                "target_file": target_file,
                "symbols": set(matched_syms),
                "change_kinds": set(change_kinds),
                "via": via,
            }
            return
        existing["symbols"].update(matched_syms)
        existing["change_kinds"].update(change_kinds)

    for target_file, summary in summaries.items():
        direct_importers = importers_by_target.get(target_file, {})
        for src_file, (line_no, src_ent, is_ns) in direct_importers.items():
            if src_file in excluded_diff_files:
                continue
            src_tokens = tokens_by_file.get(src_file, set())
            if summary.is_deleted:
                if is_ns:
                    if summary.symbol_tokens:
                        matched_del = [name for name, tok in summary.symbol_tokens.items() if tok in src_tokens]
                        if matched_del:
                            _record_consumer(
                                src_file, line_no, src_ent, target_file, matched_del, {"file_deleted"}, None
                            )
                    else:
                        # Avoid blasting all package/namespace importers when a deleted doc-only Go/C# file has
                        # surviving sibling files in the same package/namespace.
                        has_surviving_sibling = False
                        if target_file.endswith(".go"):
                            sibs = go_files_by_dir.get(posixpath.dirname(target_file), ())
                            has_surviving_sibling = any(s != target_file and s not in deleted_files for s in sibs)
                        elif summary.deleted_namespaces:
                            has_surviving_sibling = any(
                                sf != target_file and sf not in deleted_files
                                for ns in summary.deleted_namespaces
                                for sf in ns_to_files.get(ns, ())
                            )
                        if not has_surviving_sibling:
                            _record_consumer(src_file, line_no, src_ent, target_file, [], {"file_deleted"}, None)
                else:
                    _record_consumer(src_file, line_no, src_ent, target_file, [], {"file_deleted"}, None)
                continue
            matched_syms: list[str] = []
            matched_kinds: set[str] = set()
            for sym_name, sym_tok in summary.symbol_tokens.items():
                if sym_tok in src_tokens:
                    matched_syms.append(sym_name)
                    matched_kinds.update(summary.symbol_kinds[sym_name])
            if matched_syms:
                _record_consumer(src_file, line_no, src_ent, target_file, matched_syms, matched_kinds, None)

        # Follow 1-hop re-export barrels / same-package facades (including wildcard barrels) for non-deleted targets.
        for mid_file, _mid_line, _mid_ent in facade_candidates.get(target_file, ()):
            mid_tokens = tokens_by_file.get(mid_file, set())
            reexported_syms = [
                (sym_name, sym_tok) for sym_name, sym_tok in summary.symbol_tokens.items() if sym_tok in mid_tokens
            ]
            if not reexported_syms and _is_wildcard_barrel(idx, mid_file):
                reexported_syms = list(summary.symbol_tokens.items())
            if not reexported_syms:
                continue
            for hop2_file, (hop2_line, hop2_ent, _hop2_ns) in importers_by_target.get(mid_file, {}).items():
                if hop2_file in excluded_diff_files or hop2_file == target_file or hop2_file in direct_importers:
                    continue
                hop2_tokens = tokens_by_file.get(hop2_file, set())
                matched_syms = []
                matched_kinds = set()
                for sym_name, sym_tok in reexported_syms:
                    if sym_tok in hop2_tokens:
                        matched_syms.append(sym_name)
                        matched_kinds.update(summary.symbol_kinds[sym_name])
                if matched_syms:
                    _record_consumer(hop2_file, hop2_line, hop2_ent, target_file, matched_syms, matched_kinds, mid_file)

    output: list[dict[str, Any]] = []
    for entry in results_map.values():
        sorted_kinds = sorted(entry["change_kinds"], key=lambda k: (_CHANGE_KIND_SEVERITY.get(k, 99), k))
        output.append(
            {
                "file": entry["file"],
                "line": entry["line"],
                "owner": entry["owner"],
                "target_file": entry["target_file"],
                "symbols": sorted(entry["symbols"]),
                "change_kinds": sorted_kinds,
                "via": entry["via"],
            }
        )

    output.sort(
        key=lambda item: (
            min((_CHANGE_KIND_SEVERITY.get(k, 99) for k in item["change_kinds"]), default=99),
            item["target_file"],
            item["file"],
            item["line"],
        )
    )
    return output
