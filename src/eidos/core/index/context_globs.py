"""Brace-aware CSV splitting and workspace/planned --file glob expansion (CMP-TIERED-CONTEXT-COMPRESSOR)."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

_GLOB_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".cache",
    }
)


def split_brace_aware_csv(raw: str) -> list[str]:
    """Split comma-separated file patterns only on commas outside balanced ``{a,b}`` brace groups."""
    if not raw:
        return []
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for idx, ch in enumerate(raw):
        if ch == "{":
            if "}" in raw[idx + 1 :]:
                depth += 1
            buf.append(ch)
        elif ch == "}" and depth > 0:
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                parts.append(item)
            buf.clear()
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def expand_brace_globs(pattern: str) -> list[str]:
    """Deterministically expand balanced ``{a,b}`` brace groups containing commas."""
    if "{" not in pattern or "}" not in pattern:
        return [pattern]
    depth = 0
    start_idx = -1
    options: list[str] = []
    opt_start = 0
    for idx, ch in enumerate(pattern):
        if ch == "{":
            if depth == 0:
                start_idx = idx
                opt_start = idx + 1
                options = []
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start_idx != -1:
                options.append(pattern[opt_start:idx])
                if len(options) >= 2:
                    prefix = pattern[:start_idx]
                    suffix = pattern[idx + 1 :]
                    expanded: list[str] = []
                    for opt in options:
                        for sub in expand_brace_globs(f"{prefix}{opt}{suffix}"):
                            if sub not in expanded:
                                expanded.append(sub)
                    return expanded
                start_idx = -1
        elif ch == "," and depth == 1:
            options.append(pattern[opt_start:idx])
            opt_start = idx + 1
    return [pattern]


def _has_glob_magic(path_str: str) -> bool:
    return any(ch in path_str for ch in "*?[") or len(expand_brace_globs(path_str)) > 1


def _concretize_glob_segment(seg: str) -> str:
    if seg == "**":
        return "_planned_dir"
    cleaned = re.sub(r"\[[^\]]*\]", "_p", seg)
    cleaned = re.sub(r"[*?]+", "_planned", cleaned)
    return cleaned or "_planned"


def _expand_context_file_pattern(root: Path, conn: Any, pattern: str) -> list[str]:
    """Deterministically expand a repo-relative glob pattern across existing files and planned governed paths."""
    brace_variants = expand_brace_globs(pattern)
    if len(brace_variants) > 1:
        resolved_root = root.resolve()
        existing_union: list[str] = []
        for variant in brace_variants:
            for m in _expand_single_context_file_pattern(resolved_root, conn, variant, existing_only=True):
                if m not in existing_union:
                    existing_union.append(m)
        if existing_union:
            return sorted(existing_union)
        planned_union: list[str] = []
        for variant in brace_variants:
            for m in _expand_single_context_file_pattern(resolved_root, conn, variant, existing_only=False):
                if m not in planned_union:
                    planned_union.append(m)
        return sorted(planned_union)
    return _expand_single_context_file_pattern(root.resolve(), conn, pattern, existing_only=False)


def _expand_single_context_file_pattern(
    resolved_root: Path,
    conn: Any,
    pattern: str,
    *,
    existing_only: bool = False,
) -> list[str]:
    """Expand a single brace-free glob pattern against existing files or planned governed paths."""
    import posixpath

    root_posix = resolved_root.as_posix().rstrip("/")
    root_prefix = f"{root_posix}/"
    raw_pat = pattern.strip().replace("\\", "/")
    if raw_pat.startswith("/"):
        norm_abs = posixpath.normpath(raw_pat)
        if norm_abs.startswith(root_prefix):
            pat = norm_abs[len(root_prefix) :]
        else:
            return []
    else:
        pat = raw_pat
        while pat.startswith("./"):
            pat = pat[2:]
    norm_segs: list[str] = []
    for seg in pat.split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            if not norm_segs or _has_glob_magic(norm_segs[-1]):
                return []
            norm_segs.pop()
        else:
            norm_segs.append(seg)
    if not norm_segs:
        return []
    pat = "/".join(norm_segs)

    existing_matches: list[str] = []
    try:
        for candidate in resolved_root.glob(pat):
            if not candidate.is_file():
                continue
            try:
                rel_parts = candidate.resolve().relative_to(resolved_root).parts
            except ValueError:
                continue
            if any(part in _GLOB_EXCLUDED_DIRS for part in rel_parts):
                continue
            existing_matches.append("/".join(rel_parts))
    except (OSError, ValueError, NotImplementedError):
        existing_matches = []

    if existing_matches or existing_only:
        return sorted(dict.fromkeys(existing_matches))

    # Fallback for planned not-yet-created files in proposed SpecDeltas:
    # 1) Walk existing parent directory segments on disk when at least one directory segment matches.
    synthesized: list[str] = []
    segs = norm_segs
    if len(segs) >= 2:
        dir_segs = segs[:-1]
        leaf_concrete = _concretize_glob_segment(segs[-1])

        def _walk_dirs(cur_dir: Path, idx: int, matched_depth: int, rel_prefix: tuple[str, ...]) -> None:
            if idx >= len(dir_segs):
                if matched_depth > 0:
                    synthesized.append("/".join((*rel_prefix, leaf_concrete)))
                return
            seg = dir_segs[idx]
            if seg == "**":
                _walk_dirs(cur_dir, idx + 1, matched_depth, rel_prefix)
                try:
                    children = sorted(cur_dir.iterdir(), key=lambda p: p.name)
                except OSError:
                    children = []
                for child in children:
                    if child.is_dir() and child.name not in _GLOB_EXCLUDED_DIRS:
                        _walk_dirs(child, idx, matched_depth + 1, (*rel_prefix, child.name))
                return

            try:
                children = sorted(cur_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                children = []
            matched_dirs = [
                c
                for c in children
                if c.is_dir() and c.name not in _GLOB_EXCLUDED_DIRS and fnmatch.fnmatchcase(c.name, seg)
            ]
            if matched_dirs:
                for child in matched_dirs:
                    _walk_dirs(child, idx + 1, matched_depth + 1, (*rel_prefix, child.name))
            elif matched_depth > 0 and not _has_glob_magic(seg):
                rest = tuple(_concretize_glob_segment(s) for s in dir_segs[idx:])
                synthesized.append("/".join((*rel_prefix, *rest, leaf_concrete)))

        _walk_dirs(resolved_root, 0, 0, ())

    # 2) Match indexed symbol_mappings paths and governed source_path / owned_path / package_path prefixes.
    for row in conn.execute(
        "SELECT DISTINCT symbol, kind FROM symbol_mappings WHERE symbol IS NOT NULL AND symbol != '';"
    ).fetchall():
        sym = str(row["symbol"]).strip().replace("\\", "/").strip("/")
        kind = str(row["kind"] or "")
        if not sym:
            continue
        if not _has_glob_magic(sym) and fnmatch.fnmatchcase(sym, pat):
            synthesized.append(sym)
            continue
        if kind in ("package_path", "owned_path", "inferred_path", "source_path"):
            sym_parts = [s for s in sym.split("/") if s and s != "."]
            if (
                sym_parts
                and len(segs) >= len(sym_parts)
                and "**" not in segs[: len(sym_parts)]
                and "**" not in sym_parts
                and all(
                    fnmatch.fnmatchcase(sp, pp) or fnmatch.fnmatchcase(pp, sp)
                    for sp, pp in zip(sym_parts, segs[: len(sym_parts)], strict=False)
                )
            ):
                head = [
                    sp if not _has_glob_magic(sp) else (pp if not _has_glob_magic(pp) else _concretize_glob_segment(pp))
                    for sp, pp in zip(sym_parts, segs[: len(sym_parts)], strict=False)
                ]
                tail = [_concretize_glob_segment(s) for s in segs[len(sym_parts) :]]
                synthesized.append("/".join((*head, *tail)))

    return sorted(dict.fromkeys(synthesized))
