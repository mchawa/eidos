"""Git-native team operational memory lifecycle engine (CMP-MEMORY-ENGINE).

Manages ``type: agent/memory`` (``MEM-*``) files under ``eidos/memory/`` and
``eidos/memory/archive/``: collision-resistant IDs, zero-warning AsciiDoc
sanitization, calibrated BM25 / pre-computed Harrier overlap detection,
freshness verification, non-blocking auditing, archiving, and graduation into
formal ``DEC-*``, ``INV-*``, or ``POL-*`` specifications.
"""

from __future__ import annotations

import contextlib
import re
import secrets
import sqlite3
import struct
from datetime import date
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.context_memories import (
    _resolve_git_head_sha,
    evaluate_memory_freshness,
    extract_memory_sections,
    resolve_memory_freshness_batch,
)
from eidos.core.index.fts_query import FTS_COLUMN_WEIGHTS, build_fts_match_query, execute_fts_query
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.spec.models import SpecDocument
from eidos.core.spec.parser import dump_spec_document, parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import find_repo_root

VALID_MEMORY_CATEGORIES: tuple[str, ...] = ("feedback", "project", "reference")
VALID_MEMORY_STATUSES: tuple[str, ...] = ("draft", "active", "promoted", "archived")
VALID_MEMORY_PROMOTE_KINDS: tuple[str, ...] = ("decision", "invariant", "policy")

_PROMOTE_KIND_PREFIX: dict[str, str] = {
    "decision": "DEC-",
    "invariant": "INV-",
    "policy": "POL-",
}
_PROMOTE_KIND_ENTITY_TYPE: dict[str, str] = {
    "invariant": "invariant",
    "policy": "product/policy",
}

# Empirically calibrated thresholds from EXP-2026-012 Spike 3.
DUP_MEMORY_BM25_THRESHOLD = 13.5
SPEC_OVERLAP_BM25_THRESHOLD = 26.0
DUP_MEMORY_SEMANTIC_THRESHOLD = 0.56
SPEC_OVERLAP_SEMANTIC_THRESHOLD = 0.59

_MD_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*([^\s`~]*)(.*)$")
_PREPROC_RE = re.compile(r"^(\s*)(include|ifdef|ifndef|ifeval|endif)::", re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
_ADOC_HEADING_RE = re.compile(r"^={1,6}\s+(.+)$")
_BULLET_RE = re.compile(r"^(\s*)([-*])\s+(.+)$")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s+(.+)$")
_CALLOUT_LINE_RE = re.compile(r"^<(\d+)>\s+(.*)$")
_VALID_LANG_RE = re.compile(r"^[A-Za-z0-9_+-]+$")
_ADOC_DELIM_RE = re.compile(r"^(?:={4,}|\|={3,}|\+{4,}|/{4,}|\*{4,}|_{4,}|--)$")
_DASH_RUN_RE = re.compile(r"^-{4,}$")


def _resolve_repo_root(eidos_dir: Path) -> Path:
    resolved = eidos_dir.resolve()
    return resolved.parent if resolved.name == "eidos" else find_repo_root(resolved)


def _memory_dirs(eidos_dir: Path) -> tuple[Path, Path]:
    mem_dir = eidos_dir / "memory"
    return mem_dir, mem_dir / "archive"


def _validate_iso_date(raw: str, field_name: str) -> str:
    cleaned = raw.strip()
    try:
        date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} '{raw}': expected ISO date YYYY-MM-DD.") from exc
    return cleaned


def generate_memory_id(
    title: str,
    eidos_dir: Path | None = None,
    *,
    max_slug_len: int = 36,
) -> tuple[str, str]:
    """Generate a collision-resistant ``(MEM-<SLUG>-<4HEX>, mem-<slug>-<4hex>.adoc)`` pair."""
    raw_slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    if len(raw_slug) > max_slug_len:
        window = raw_slug[: max_slug_len + 1]
        cut = window.rfind("-")
        slug = window[:cut].strip("-") if cut >= 6 else raw_slug[:max_slug_len].strip("-")
    else:
        slug = raw_slug
    slug = slug or "note"

    existing_names: set[str] = set()
    if eidos_dir is not None:
        mem_dir, arch_dir = _memory_dirs(eidos_dir)
        for d in (mem_dir, arch_dir):
            if d.is_dir():
                existing_names.update(p.name.lower() for p in d.glob("*.adoc"))

    for _ in range(64):
        suffix = secrets.token_hex(2)
        filename = f"mem-{slug}-{suffix}.adoc"
        if filename.lower() not in existing_names:
            return f"MEM-{slug.upper()}-{suffix.upper()}", filename
    suffix = secrets.token_hex(3)
    return f"MEM-{slug.upper()}-{suffix.upper()}", f"mem-{slug}-{suffix}.adoc"


def _safe_target_lists(fm: dict[str, Any]) -> tuple[list[str], list[str]]:
    targets = fm.get("targets") if isinstance(fm.get("targets"), dict) else {}
    raw_entities = targets.get("entities")
    raw_paths = targets.get("paths")
    entities = (
        [str(e).strip() for e in raw_entities if isinstance(e, str) and e.strip()]
        if isinstance(raw_entities, list)
        else []
    )
    paths = (
        [str(p).strip() for p in raw_paths if isinstance(p, str) and p.strip()] if isinstance(raw_paths, list) else []
    )
    return entities, paths


def _has_symlink_component(path: Path, stop_at: Path) -> bool:
    """Return True if ``path`` or any ancestor up to ``stop_at`` is a symbolic link."""
    cur = path
    while True:
        if cur.is_symlink():
            return True
        if cur == stop_at or cur.parent == cur:
            return False
        cur = cur.parent


def _normalize_code_lang(raw_lang: str) -> str:
    cleaned = (raw_lang or "").strip()
    if cleaned.lower() == "c#":
        return "csharp"
    if cleaned.lower() == "f#":
        return "fsharp"
    return cleaned if cleaned and _VALID_LANG_RE.match(cleaned) else "text"


def _escape_code_line(line: str) -> str:
    if _PREPROC_RE.match(line):
        return f"\\{line}"
    return line


def sanitize_memory_asciidoc_body(raw_text: str) -> str:
    """Normalize agent-supplied Markdown or AsciiDoc prose into zero-warning AsciiDoc (INV-ASCIIDOC-ZERO-WARNING-HYGIENE)."""
    lines = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    out: list[str] = []
    prev_list_depth = 0
    idx = 0
    total = len(lines)

    while idx < total:
        line = lines[idx]
        stripped = line.strip()

        # 1. Markdown fenced code blocks (``` or ~~~ with optional trailing info/metadata)
        m_fence = _MD_FENCE_RE.match(stripped)
        if m_fence:
            prev_list_depth = 0
            fence_token = m_fence.group(1)
            fence_char = fence_token[0]
            fence_len = len(fence_token)
            lang = _normalize_code_lang(m_fence.group(2))
            close_re = re.compile(rf"^{re.escape(fence_char)}{{{fence_len},}}\s*$")
            buf: list[str] = []
            max_dash = 0
            idx += 1
            while idx < total:
                inner = lines[idx]
                if close_re.match(inner.strip()):
                    idx += 1
                    break
                escaped = _escape_code_line(inner)
                buf.append(escaped)
                if _DASH_RUN_RE.match(escaped.strip()):
                    max_dash = max(max_dash, len(escaped.strip()))
                idx += 1
            delim = "-" * max(4, max_dash + 1)
            if out and out[-1] != "":
                out.append("")
            out.append(f"[source,{lang}]")
            out.append(delim)
            out.extend(buf)
            out.append(delim)
            out.append("")
            continue

        # 2. Native AsciiDoc listing blocks (exact-length ---- / ----- pairing)
        if _DASH_RUN_RE.match(stripped):
            prev_list_depth = 0
            delim_len = len(stripped)
            close_idx: int | None = None
            for scan in range(idx + 1, total):
                if lines[scan].strip() == stripped:
                    close_idx = scan
                    break
            if close_idx is not None:
                inner_lines = [_escape_code_line(lines[k]) for k in range(idx + 1, close_idx)]
                out.append(stripped)
                out.extend(inner_lines)
                out.append(stripped)
                out.append("")
                idx = close_idx + 1
                continue
            # Standalone ---- / Setext underline (no closing delimiter) -> safe horizontal rule
            if out and out[-1] != "":
                out.append("")
            if delim_len >= 4:
                out.append("'''")
                out.append("")
            idx += 1
            continue

        # 3. Preprocessor directives (include::, ifdef::, ifndef::, ifeval::, endif::)
        if _PREPROC_RE.match(line):
            prev_list_depth = 0
            out.append(f"`{stripped}`")
            idx += 1
            continue

        # 4. AsciiDoc delimited block markers & Setext === underlines
        if _ADOC_DELIM_RE.match(stripped):
            prev_list_depth = 0
            close_idx = None
            if stripped in ("====", "****", "____", "////"):
                for scan in range(idx + 1, total):
                    if lines[scan].strip() == stripped:
                        close_idx = scan
                        break
            if close_idx is not None:
                out.append(stripped)
                for k in range(idx + 1, close_idx):
                    inner_k = lines[k]
                    out.append(f"`{inner_k.strip()}`" if _PREPROC_RE.match(inner_k) else inner_k)
                out.append(stripped)
                out.append("")
                idx = close_idx + 1
                continue
            if re.fullmatch(r"={4,}", stripped):
                if out and out[-1] != "":
                    out.append("")
                out.append("'''")
                out.append("")
                idx += 1
                continue
            out.append(f"`{stripped}`")
            idx += 1
            continue

        if stripped == "---":
            prev_list_depth = 0
            if out and out[-1] != "":
                out.append("")
            out.append("'''")
            out.append("")
            idx += 1
            continue

        m_md_h = _MD_HEADING_RE.match(stripped)
        if m_md_h:
            prev_list_depth = 0
            if out and out[-1] != "":
                out.append("")
            out.append(f"*{m_md_h.group(1).strip()}*")
            out.append("")
            idx += 1
            continue

        m_adoc_h = _ADOC_HEADING_RE.match(stripped)
        if m_adoc_h:
            prev_list_depth = 0
            if out and out[-1] != "":
                out.append("")
            out.append(f"*{m_adoc_h.group(1).strip()}*")
            out.append("")
            idx += 1
            continue

        m_list = _BULLET_RE.match(line)
        if m_list:
            indent_len = len(m_list.group(1).replace("\t", "  "))
            desired_depth = min(3, (indent_len // 2) + 1)
            depth = min(desired_depth, prev_list_depth + 1) if prev_list_depth > 0 else 1
            if prev_list_depth == 0 and out and out[-1] != "":
                out.append("")
            out.append(f"{'*' * depth} {m_list.group(3).strip()}")
            prev_list_depth = depth
            idx += 1
            continue

        m_ord = _ORDERED_LIST_RE.match(line)
        if m_ord:
            if prev_list_depth == 0 and out and out[-1] != "":
                out.append("")
            out.append(f". {m_ord.group(1).strip()}")
            prev_list_depth = 1
            idx += 1
            continue

        m_callout = _CALLOUT_LINE_RE.match(stripped)
        if m_callout:
            prev_list_depth = 0
            out.append(f"\\<{m_callout.group(1)}> {m_callout.group(2)}")
            idx += 1
            continue

        if stripped:
            prev_list_depth = 0
        out.append(line)
        idx += 1

    return "\n".join(out).strip()


def _render_memory_body(title: str, guidance: str, why: str) -> str:
    safe_title = " ".join((title or "").split()).strip()
    clean_guidance = sanitize_memory_asciidoc_body(guidance)
    clean_why = sanitize_memory_asciidoc_body(why)
    parts = [f"= Memory: {safe_title}", ":toc: left"]
    if clean_guidance:
        parts.append(f"\n== Guidance\n\n{clean_guidance}")
    if clean_why:
        parts.append(f"\n== Why\n\n{clean_why}")
    return "\n".join(parts).rstrip() + "\n"


def _iter_memory_files(eidos_dir: Path, *, include_archived: bool = True) -> list[tuple[Path, bool]]:
    mem_dir, arch_dir = _memory_dirs(eidos_dir)
    found: list[tuple[Path, bool]] = []
    if mem_dir.is_symlink() or not mem_dir.is_dir():
        return found
    mem_root = mem_dir.resolve()
    for p in sorted(mem_dir.glob("*.adoc")):
        if p.is_symlink() or not p.is_file():
            continue
        if p.name.startswith("_") or p.name.lower() == "readme.adoc":
            continue
        if not p.resolve().is_relative_to(mem_root):
            continue
        found.append((p, False))
    if include_archived and arch_dir.is_dir() and not arch_dir.is_symlink():
        arch_root = arch_dir.resolve()
        if arch_root.is_relative_to(mem_root):
            for p in sorted(arch_dir.glob("*.adoc")):
                if p.is_symlink() or not p.is_file():
                    continue
                if p.name.startswith("_") or p.name.lower() == "readme.adoc":
                    continue
                if not p.resolve().is_relative_to(arch_root):
                    continue
                found.append((p, True))
    return found


def find_memory_by_id_or_path(
    eidos_dir: Path,
    target: str,
    *,
    include_archived: bool = True,
) -> Path:
    """Resolve a ``MEM-*`` ID, unambiguous prefix, filename, or contained path under ``eidos/memory/``."""
    raw = (target or "").strip()
    if not raw:
        raise ValueError("Memory identifier or path cannot be empty.")

    mem_dir, arch_dir = _memory_dirs(eidos_dir)
    mem_root = mem_dir.resolve()
    arch_root = arch_dir.resolve()
    repo_root = _resolve_repo_root(eidos_dir)

    def _validate_contained(cand: Path) -> Path:
        if mem_dir.is_symlink() or cand.is_symlink() or _has_symlink_component(cand, mem_dir):
            raise ValueError(f"Memory path '{target}' cannot traverse symlinks (not 'agent/memory').")
        resolved = cand.resolve()
        if not resolved.is_relative_to(mem_root):
            raise ValueError(f"'{target}' resolves outside '{mem_dir}' and is not 'agent/memory'.")
        if not include_archived and resolved.is_relative_to(arch_root):
            raise ValueError(f"Memory '{target}' is archived under '{arch_dir}'. Pass --include-archived to inspect.")
        return resolved

    cand = Path(raw)
    if cand.is_symlink() or cand.is_file():
        return _validate_contained(cand)
    bases: list[Path] = [repo_root, eidos_dir, mem_dir]
    if include_archived:
        bases.append(arch_dir)
    for base in bases:
        rel_cand = base / raw
        if rel_cand.is_symlink() or rel_cand.is_file():
            return _validate_contained(rel_cand)

    upper = raw.upper()
    id_exact_matches: list[Path] = []
    name_exact_matches: list[Path] = []
    prefix_matches: list[tuple[str, Path]] = []
    for p, _is_arch in _iter_memory_files(eidos_dir, include_archived=include_archived):
        try:
            doc = parse_spec_file(p)
        except OSError:
            continue
        doc_id_upper = str(doc.doc_id).upper() if doc.doc_id else ""
        did = doc_id_upper or p.stem.upper()
        if doc_id_upper == upper:
            id_exact_matches.append(p)
        elif p.name.upper() == upper or p.stem.upper() == upper:
            name_exact_matches.append(p)
        elif did.startswith(upper):
            prefix_matches.append((did, p))

    exact_matches = id_exact_matches or name_exact_matches
    if len(exact_matches) == 1:
        return _validate_contained(exact_matches[0])
    if len(exact_matches) > 1:
        raise ValueError(f"Multiple memories match '{target}': {', '.join(str(p) for p in exact_matches)}")
    if len(prefix_matches) == 1:
        return _validate_contained(prefix_matches[0][1])
    if len(prefix_matches) > 1:
        ids = ", ".join(mid for mid, _ in prefix_matches[:5])
        raise ValueError(f"Ambiguous memory prefix '{target}' matched multiple memories: {ids}")
    raise ValueError(f"Memory '{target}' not found under '{eidos_dir / 'memory'}'.")


def _sync_index_silently(eidos_dir: Path) -> Any:
    repo_root = _resolve_repo_root(eidos_dir)
    idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
    idx.sync(force=False, auto_semantic=False)
    return idx


def resolve_promotion_target(
    eidos_dir: Path,
    spec_id: str,
    *,
    kind: str | None = None,
) -> dict[str, Any]:
    """Resolve a standalone (``POL-*``) or child (``INV-*``, embedded ``DEC-*``) promotion target."""
    tid = (spec_id or "").strip().upper()
    if not tid:
        raise ValueError("Promotion target --spec-id cannot be empty.")

    norm_kind: str | None = None
    if kind is not None:
        norm_kind = kind.strip().lower()
        if norm_kind not in VALID_MEMORY_PROMOTE_KINDS:
            raise ValueError(
                f"Invalid promotion kind '{kind}'. Expected one of: {', '.join(VALID_MEMORY_PROMOTE_KINDS)}."
            )
        expected_prefix = _PROMOTE_KIND_PREFIX[norm_kind]
        if not tid.startswith(expected_prefix):
            raise ValueError(f"Promotion kind '{norm_kind}' expects a '{expected_prefix}*' spec ID, got '{spec_id}'.")

    if not eidos_dir.is_dir():
        raise ValueError(
            f"Promotion target '{spec_id}' does not exist: specification directory '{eidos_dir}' not found."
        )

    idx = _sync_index_silently(eidos_dir)
    conn = idx._get_connection()

    if norm_kind != "decision":
        row = conn.execute(
            "SELECT id, type, title, file_path FROM entities WHERE UPPER(id) = ?;",
            (tid,),
        ).fetchone()
        if row is not None:
            row_type = str(row["type"] or "")
            allowed_types = (
                {_PROMOTE_KIND_ENTITY_TYPE[norm_kind]}
                if norm_kind in _PROMOTE_KIND_ENTITY_TYPE
                else {"product/policy", "invariant"}
            )
            if row_type in allowed_types:
                return {
                    "id": str(row["id"]),
                    "kind": row_type,
                    "title": str(row["title"] or row["id"]),
                    "file_path": str(row["file_path"] or ""),
                }

    if tid.startswith("DEC-") and norm_kind in (None, "decision"):
        for erow in conn.execute(
            """
            SELECT id, type, file_path, data_json
            FROM entities
            WHERE type NOT IN ('delta/mutation', 'agent/memory')
              AND LOWER(COALESCE(status, 'active')) != 'rejected'
              AND data_json LIKE ?;
            """,
            (f"%{tid}%",),
        ).fetchall():
            try:
                data = orjson.loads(erow["data_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            for dec in data.get("decisions") or []:
                if isinstance(dec, dict) and str(dec.get("id") or "").upper() == tid:
                    return {
                        "id": str(dec["id"]),
                        "kind": "decision",
                        "title": str(dec.get("title") or dec["id"]),
                        "owner_id": str(erow["id"]),
                        "file_path": str(erow["file_path"] or ""),
                    }

    raise ValueError(
        f"Promotion target '{spec_id}' does not exist in the living specification catalog. "
        "Record the DEC-*, INV-*, or POL-* in its owning spec first, then run 'eidos memory promote'."
    )


def _scan_precomputed_semantic_overlaps(
    conn: Any,
    eidos_dir: Path,
    query_text: str,
    *,
    exclude_id: str | None = None,
) -> dict[str, list[dict[str, Any]]] | None:
    """Scan pre-computed ``spec_embeddings`` BLOBs (encoding only 1 query) without ever re-embedding specs."""
    try:
        import numpy as np

        from eidos.core.index.semantic import (
            get_semantic_engine,
            is_semantic_index_complete,
            unpack_embedding_f32,
        )
    except ImportError:  # pragma: no cover
        return None

    if not is_semantic_index_complete(conn):
        return None
    engine = get_semantic_engine(eidos_dir)
    if engine is None:
        return None

    try:
        q_blob = engine.encode_texts([query_text], is_query=True)[0]
        expected_blob_len = len(q_blob)
        q_vec = np.asarray(unpack_embedding_f32(q_blob), dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        if q_norm <= 1e-12:
            return None
        q_vec = q_vec / q_norm
    except Exception:  # pragma: no cover
        return None

    expected_rows = conn.execute(
        """
        SELECT COUNT(*) FROM entities e
        JOIN specs_fts f ON f.entity_id = e.id
        WHERE e.type IN ('agent/memory', 'product/policy', 'invariant')
          AND (e.type != 'agent/memory' OR e.status = 'active')
          AND (? IS NULL OR e.id != ?);
        """,
        (exclude_id, exclude_id),
    ).fetchone()
    expected_count = int(expected_rows[0]) if expected_rows else 0

    rows = conn.execute(
        """
        SELECT s.entity_id, s.embedding, e.type, e.title, e.file_path
        FROM spec_embeddings s
        JOIN entities e ON e.id = s.entity_id
        WHERE e.type IN ('agent/memory', 'product/policy', 'invariant')
          AND (e.type != 'agent/memory' OR e.status = 'active')
          AND (? IS NULL OR e.id != ?);
        """,
        (exclude_id, exclude_id),
    ).fetchall()

    # Fall back cleanly to BM25 whenever newly added memories or specs lack pre-computed embeddings
    if len(rows) < expected_count:
        return None

    dup_memories: list[dict[str, Any]] = []
    spec_overlaps: list[dict[str, Any]] = []
    for row in rows:
        eid = str(row["entity_id"])
        blob = row["embedding"]
        if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) != expected_blob_len:
            return None
        try:
            vec = np.asarray(unpack_embedding_f32(blob), dtype=np.float32)
        except (ValueError, TypeError, struct.error):
            return None
        if vec.shape != q_vec.shape:
            return None
        v_norm = float(np.linalg.norm(vec))
        if v_norm <= 1e-12:
            continue
        cos = float(np.dot(vec / v_norm, q_vec))
        etype = str(row["type"])
        if etype == "agent/memory" and cos >= DUP_MEMORY_SEMANTIC_THRESHOLD:
            dup_memories.append(
                {
                    "id": eid,
                    "title": str(row["title"] or eid),
                    "file_path": str(row["file_path"] or ""),
                    "score": round(cos, 4),
                    "method": "semantic",
                }
            )
        elif etype in ("product/policy", "invariant") and cos >= SPEC_OVERLAP_SEMANTIC_THRESHOLD:
            spec_overlaps.append(
                {
                    "id": eid,
                    "type": etype,
                    "title": str(row["title"] or eid),
                    "file_path": str(row["file_path"] or ""),
                    "score": round(cos, 4),
                    "method": "semantic",
                }
            )

    dup_memories.sort(key=lambda r: (-r["score"], r["id"]))
    spec_overlaps.sort(key=lambda r: (-r["score"], r["id"]))
    return {"duplicate_memories": dup_memories[:3], "spec_overlaps": spec_overlaps[:3]}


def _scan_bm25_overlaps(
    conn: Any,
    query_text: str,
    *,
    exclude_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan raw FTS5 BM25 scores (without synthetic -1000/-50 search boosts) against calibrated Spike 3 thresholds."""
    fts_query = build_fts_match_query(query_text, operator="OR", expand_synonyms=False)
    if not fts_query:
        return {"duplicate_memories": [], "spec_overlaps": []}

    weights_sql = ", ".join(str(w) for w in FTS_COLUMN_WEIGHTS)
    dup_memories: list[dict[str, Any]] = []
    spec_overlaps: list[dict[str, Any]] = []

    try:
        mem_rows = conn.execute(
            f"""
            SELECT e.id, e.title, e.file_path, bm25(specs_fts, {weights_sql}) AS raw_rank
            FROM specs_fts
            JOIN entities e ON e.id = specs_fts.entity_id
            WHERE specs_fts MATCH ?
              AND e.type = 'agent/memory'
              AND e.status = 'active'
              AND (? IS NULL OR e.id != ?)
            ORDER BY raw_rank ASC
            LIMIT 5;
            """,
            (fts_query, exclude_id, exclude_id),
        ).fetchall()
    except sqlite3.Error:
        mem_rows = []

    for row in mem_rows:
        hid = str(row["id"] or "")
        if not hid:
            continue
        score = abs(float(row["raw_rank"] or 0.0))
        if score >= DUP_MEMORY_BM25_THRESHOLD:
            dup_memories.append(
                {
                    "id": hid,
                    "title": str(row["title"] or hid),
                    "file_path": str(row["file_path"] or ""),
                    "score": round(score, 2),
                    "method": "bm25",
                }
            )

    try:
        spec_rows = conn.execute(
            f"""
            SELECT e.id, e.type, e.title, e.file_path, bm25(specs_fts, {weights_sql}) AS raw_rank
            FROM specs_fts
            JOIN entities e ON e.id = specs_fts.entity_id
            WHERE specs_fts MATCH ?
              AND e.type IN ('product/policy', 'invariant')
            ORDER BY raw_rank ASC
            LIMIT 5;
            """,
            (fts_query,),
        ).fetchall()
    except sqlite3.Error:
        spec_rows = []

    for row in spec_rows:
        hid = str(row["id"] or "")
        if not hid:
            continue
        score = abs(float(row["raw_rank"] or 0.0))
        if score >= SPEC_OVERLAP_BM25_THRESHOLD:
            spec_overlaps.append(
                {
                    "id": hid,
                    "type": str(row["type"] or ""),
                    "title": str(row["title"] or hid),
                    "file_path": str(row["file_path"] or ""),
                    "score": round(score, 2),
                    "method": "bm25",
                }
            )

    return {"duplicate_memories": dup_memories[:3], "spec_overlaps": spec_overlaps[:3]}


def detect_memory_overlaps(
    eidos_dir: Path,
    title: str,
    summary: str,
    *,
    exclude_id: str | None = None,
    use_semantic: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Detect potential duplicate ``MEM-*`` notes and overlaps with formal ``POL-*``/``INV-*`` specs."""
    query_text = f"{title.strip()} {summary.strip()}".strip()
    if not query_text:
        return {"duplicate_memories": [], "spec_overlaps": []}

    idx = _sync_index_silently(eidos_dir)
    conn = idx._get_connection()

    if use_semantic:
        sem_result = _scan_precomputed_semantic_overlaps(conn, eidos_dir, query_text, exclude_id=exclude_id)
        if sem_result is not None:
            return sem_result
    return _scan_bm25_overlaps(conn, query_text, exclude_id=exclude_id)


def add_memory(
    eidos_dir: Path,
    *,
    title: str,
    category: str,
    summary: str,
    guidance: str = "",
    why: str = "",
    entities: list[str] | None = None,
    paths: list[str] | None = None,
    expires_at: str | None = None,
    status: str = "active",
    use_semantic: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """Create a schema-validated ``agent/memory`` file under ``eidos/memory/`` and report non-blocking overlap hints."""
    clean_title = " ".join((title or "").split()).strip()
    if not clean_title:
        raise ValueError("Memory --title cannot be empty.")

    clean_cat = (category or "").strip().lower()
    if clean_cat == "user":
        raise ValueError(
            "Category 'user' is excluded from Git-tracked eidos/memory/. "
            "Keep personal developer preferences in client-native memory; use feedback, project, or reference for shared team knowledge."
        )
    if clean_cat not in VALID_MEMORY_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Expected one of: {', '.join(VALID_MEMORY_CATEGORIES)}.")

    clean_summary = " ".join((summary or "").split()).strip()
    if not clean_summary:
        raise ValueError("Memory --summary cannot be empty.")
    if len(clean_summary) > 220:
        raise ValueError(
            f"Memory --summary exceeds 220 characters ({len(clean_summary)} chars); keep summaries concise."
        )

    clean_status = (status or "active").strip().lower()
    if clean_status not in ("active", "draft"):
        raise ValueError(f"Invalid initial status '{status}'. Expected 'active' or 'draft'.")

    clean_expires: str | None = None
    if expires_at is not None:
        clean_expires = _validate_iso_date(expires_at, "--expires")

    clean_entities = list(dict.fromkeys(e.strip().upper() for e in (entities or ()) if e and e.strip()))
    clean_paths = list(dict.fromkeys(p.strip().replace("\\", "/") for p in (paths or ()) if p and p.strip()))

    # Check overlaps before writing so the newly written file does not self-match
    overlaps = detect_memory_overlaps(eidos_dir, clean_title, clean_summary, use_semantic=use_semantic)

    repo_root = _resolve_repo_root(eidos_dir)
    head_sha = _resolve_git_head_sha(repo_root)
    verified_at = (today or date.today()).isoformat()
    mem_id, filename = generate_memory_id(clean_title, eidos_dir)

    fm: dict[str, Any] = {
        "type": "agent/memory",
        "id": mem_id,
        "title": clean_title,
        "category": clean_cat,
        "status": clean_status,
        "summary": clean_summary,
    }
    targets: dict[str, list[str]] = {}
    if clean_entities:
        targets["entities"] = clean_entities
    if clean_paths:
        targets["paths"] = clean_paths
    if targets:
        fm["targets"] = targets
    fm["verified_at"] = verified_at
    if head_sha:
        fm["verified_commit"] = head_sha
    if clean_expires:
        fm["expires_at"] = clean_expires

    body = _render_memory_body(clean_title, guidance, why)
    mem_dir, _ = _memory_dirs(eidos_dir)
    target_file = mem_dir / filename

    # Validate before writing to disk
    doc = SpecDocument(
        file_path=target_file,
        frontmatter=fm,
        body=body,
        doc_type="agent/memory",
        doc_id=mem_id,
    )
    errors = SpecValidator().validate_document(doc)
    if errors:
        raise ValueError(f"Memory failed agent/memory schema validation: {'; '.join(errors)}")

    mem_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text(dump_spec_document(fm, body), encoding="utf-8")
    _sync_index_silently(eidos_dir)

    try:
        rel_path = target_file.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel_path = target_file.as_posix()

    return {
        "id": mem_id,
        "title": clean_title,
        "category": clean_cat,
        "status": clean_status,
        "summary": clean_summary,
        "targets": targets,
        "verified_at": verified_at,
        "verified_commit": head_sha or None,
        "expires_at": clean_expires,
        "path": str(target_file),
        "relative_path": rel_path,
        "warnings": overlaps,
    }


def list_memories(
    eidos_dir: Path,
    *,
    category: str | None = None,
    status: str | None = None,
    entity: str | None = None,
    include_archived: bool = False,
    include_freshness: bool = True,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """List team operational memories with optional category/status/entity filters and computed freshness."""
    cat_filter = category.strip().lower() if category else None
    if cat_filter and cat_filter not in VALID_MEMORY_CATEGORIES:
        raise ValueError(
            f"Invalid category filter '{category}'. Expected one of: {', '.join(VALID_MEMORY_CATEGORIES)}."
        )
    status_filter = status.strip().lower() if status else None
    if status_filter and status_filter not in VALID_MEMORY_STATUSES:
        raise ValueError(f"Invalid status filter '{status}'. Expected one of: {', '.join(VALID_MEMORY_STATUSES)}.")
    entity_filter = entity.strip().upper() if entity else None

    repo_root = _resolve_repo_root(eidos_dir)
    eff_include_archived = include_archived or status_filter in ("archived", "promoted")
    files = _iter_memory_files(eidos_dir, include_archived=eff_include_archived)
    if not files:
        return []

    parsed: list[tuple[Path, bool, dict[str, Any], str]] = []
    for p, is_arch in files:
        try:
            doc = parse_spec_file(p)
        except OSError:
            continue
        fm = doc.frontmatter or {}
        if doc.doc_type != "agent/memory":
            continue
        mem_status = str(fm.get("status") or "active").strip().lower()
        if not eff_include_archived and mem_status in ("archived", "promoted"):
            continue
        mem_cat = str(fm.get("category") or "feedback").strip().lower()
        if cat_filter and mem_cat != cat_filter:
            continue
        if status_filter and mem_status != status_filter:
            continue
        raw_entities, _ = _safe_target_lists(fm)
        t_entities = [e.upper() for e in raw_entities]
        if entity_filter and entity_filter not in t_entities:
            continue
        mem_id = str(doc.doc_id or p.stem)
        parsed.append((p, is_arch, fm, mem_id))

    freshness_map: dict[str, tuple[str, str]] = {}
    if include_freshness and parsed:
        active_batch = [
            (mid, fm)
            for _p, is_arch, fm, mid in parsed
            if not is_arch and str(fm.get("status") or "").strip().lower() == "active"
        ]
        if active_batch:
            idx = _sync_index_silently(eidos_dir)
            conn = idx._get_connection()
            catalog_ids = {str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall()}
            freshness_map = resolve_memory_freshness_batch(
                repo_root,
                active_batch,
                catalog_ids,
                conn=conn,
                eidos_dir=eidos_dir,
                db_path=getattr(idx, "db_path", None),
                today=today,
            )

    out: list[dict[str, Any]] = []
    for p, is_arch, fm, mem_id in parsed:
        try:
            rel_path = p.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel_path = p.as_posix()
        mem_status = str(fm.get("status") or "active").strip().lower()
        default_fresh = "archived" if is_arch else (mem_status if mem_status != "active" else "fresh")
        fresh, reason = freshness_map.get(mem_id, (default_fresh, ""))
        t_entities, t_paths = _safe_target_lists(fm)
        out.append(
            {
                "id": mem_id,
                "title": str(fm.get("title") or mem_id),
                "category": str(fm.get("category") or "feedback"),
                "status": mem_status,
                "summary": str(fm.get("summary") or ""),
                "targets": {
                    "entities": t_entities,
                    "paths": t_paths,
                },
                "verified_at": str(fm.get("verified_at") or ""),
                "verified_commit": fm.get("verified_commit"),
                "expires_at": fm.get("expires_at"),
                "promoted_to": fm.get("promoted_to"),
                "freshness": fresh,
                "freshness_reason": reason,
                "is_archived": is_arch,
                "path": str(p),
                "relative_path": rel_path,
            }
        )
    return out


def search_memories(
    eidos_dir: Path,
    query: str,
    *,
    limit: int = 10,
    use_semantic: bool = True,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Search active team memories via FTS5 BM25 (with optional Harrier Hybrid RRF fusion)."""
    q = (query or "").strip()
    if not q or not eidos_dir.is_dir():
        return []
    repo_root = _resolve_repo_root(eidos_dir)
    idx = _sync_index_silently(eidos_dir)
    conn = idx._get_connection()

    hits = execute_fts_query(
        conn,
        text=q,
        limit=max(1, limit),
        include_types=("agent/memory",),
        use_semantic=use_semantic,
        eidos_dir=eidos_dir,
    )
    if not hits:
        return []

    batch_inputs: list[tuple[str, dict[str, Any]]] = []
    for h in hits:
        data = h.get("data") if isinstance(h.get("data"), dict) else {}
        batch_inputs.append((str(h["id"]), data))
    catalog_ids = {str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall()}
    freshness_map = resolve_memory_freshness_batch(
        repo_root,
        batch_inputs,
        catalog_ids,
        conn=conn,
        eidos_dir=eidos_dir,
        db_path=getattr(idx, "db_path", None),
        today=today,
    )

    results: list[dict[str, Any]] = []
    for h in hits:
        mid = str(h["id"])
        data = h.get("data") if isinstance(h.get("data"), dict) else {}
        fresh, reason = freshness_map.get(mid, ("fresh", ""))
        results.append(
            {
                "id": mid,
                "title": str(h.get("title") or mid),
                "category": str(data.get("category") or "feedback"),
                "status": str(data.get("status") or "active"),
                "summary": str(data.get("summary") or ""),
                "freshness": fresh,
                "freshness_reason": reason,
                "file_path": str(h.get("file_path") or ""),
                "provenance": str(h.get("provenance") or "fts_bm25"),
                "confidence": str(h.get("confidence") or "observed"),
                "rank": round(float(h.get("rank") or 0.0), 4),
            }
        )
    return results


def show_memory(eidos_dir: Path, target: str, *, today: date | None = None) -> dict[str, Any]:
    """Load full frontmatter, computed freshness, and AsciiDoc body of a team memory."""
    mem_path = find_memory_by_id_or_path(eidos_dir, target, include_archived=True)
    doc = parse_spec_file(mem_path)
    fm = doc.frontmatter or {}
    if doc.doc_type != "agent/memory":
        raise ValueError(f"'{target}' is type '{doc.doc_type}', not 'agent/memory'.")

    repo_root = _resolve_repo_root(eidos_dir)
    _, arch_dir = _memory_dirs(eidos_dir)
    is_archived = mem_path.resolve().is_relative_to(arch_dir.resolve())
    mem_id = str(doc.doc_id or mem_path.stem)
    mem_status = str(fm.get("status") or "active").strip().lower()

    fresh = "archived" if is_archived else (mem_status if mem_status != "active" else "fresh")
    reason = ""
    if not is_archived and mem_status == "active":
        idx = _sync_index_silently(eidos_dir)
        conn = idx._get_connection()
        catalog_ids = {str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall()}
        fresh, reason = resolve_memory_freshness_batch(
            repo_root,
            [(mem_id, fm)],
            catalog_ids,
            conn=conn,
            eidos_dir=eidos_dir,
            db_path=getattr(idx, "db_path", None),
            today=today,
        ).get(mem_id, ("fresh", ""))

    sections = extract_memory_sections(doc.body or "")
    try:
        rel_path = mem_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel_path = mem_path.as_posix()

    t_entities, t_paths = _safe_target_lists(fm)
    return {
        "id": mem_id,
        "title": str(fm.get("title") or mem_id),
        "category": str(fm.get("category") or "feedback"),
        "status": mem_status,
        "summary": str(fm.get("summary") or ""),
        "targets": {"entities": t_entities, "paths": t_paths},
        "verified_at": str(fm.get("verified_at") or ""),
        "verified_commit": fm.get("verified_commit"),
        "expires_at": fm.get("expires_at"),
        "promoted_to": fm.get("promoted_to"),
        "freshness": fresh,
        "freshness_reason": reason,
        "is_archived": is_archived,
        "path": str(mem_path),
        "relative_path": rel_path,
        "guidance": sections.get("guidance", ""),
        "why": sections.get("why", ""),
        "body": doc.body or "",
    }


def _unique_archive_dest(arch_dir: Path, mem_path: Path) -> Path:
    """Resolve an archive destination path inside ``arch_dir`` without overwriting a distinct file."""
    if arch_dir.is_symlink():
        raise ValueError(f"Archive directory '{arch_dir}' cannot be a symlink.")
    arch_dir.mkdir(parents=True, exist_ok=True)
    arch_root = arch_dir.resolve()
    cand = (arch_dir / mem_path.name).resolve()
    if cand == mem_path.resolve():
        return cand
    if not cand.exists() and not cand.is_symlink():
        return cand
    for _ in range(32):
        alt = arch_dir / f"{mem_path.stem}-{secrets.token_hex(2)}.adoc"
        if not alt.exists() and not alt.is_symlink():
            alt_res = alt.resolve()
            if alt_res.is_relative_to(arch_root):
                return alt_res
    raise ValueError(f"Archive destination collision for '{mem_path.name}' under '{arch_dir}'.")


def verify_memory(
    eidos_dir: Path,
    target: str,
    *,
    expires_at: str | None = None,
    clear_expires: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Refresh ``verified_at`` and ``verified_commit`` on a memory after re-confirming it against live code."""
    if clear_expires and expires_at is not None:
        raise ValueError("Cannot combine --clear-expires with --expires.")

    mem_path = find_memory_by_id_or_path(eidos_dir, target, include_archived=True)
    doc = parse_spec_file(mem_path)
    fm = dict(doc.frontmatter or {})
    if doc.doc_type != "agent/memory":
        raise ValueError(f"'{target}' is type '{doc.doc_type}', not 'agent/memory'.")

    _, arch_dir = _memory_dirs(eidos_dir)
    mem_status = str(fm.get("status") or "active").strip().lower()
    if mem_path.resolve().is_relative_to(arch_dir.resolve()) or mem_status in ("archived", "promoted"):
        raise ValueError(f"Cannot verify archived or promoted memory '{doc.doc_id or target}' (status: {mem_status}).")

    effective_today = today or date.today()
    repo_root = _resolve_repo_root(eidos_dir)
    head_sha = _resolve_git_head_sha(repo_root)

    fm["verified_at"] = effective_today.isoformat()
    if head_sha:
        fm["verified_commit"] = head_sha
    else:
        fm.pop("verified_commit", None)

    if clear_expires:
        fm.pop("expires_at", None)
    elif expires_at is not None:
        fm["expires_at"] = _validate_iso_date(expires_at, "--expires")

    updated_doc = SpecDocument(
        file_path=mem_path,
        frontmatter=fm,
        body=doc.body,
        doc_type="agent/memory",
        doc_id=str(fm.get("id") or doc.doc_id),
    )
    errors = SpecValidator().validate_document(updated_doc, root_dir=eidos_dir)
    if errors:
        raise ValueError(f"Updated memory failed schema validation: {'; '.join(errors)}")

    mem_path.write_text(dump_spec_document(fm, doc.body), encoding="utf-8")
    idx = _sync_index_silently(eidos_dir)
    conn = idx._get_connection()
    catalog_ids = {str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall()}
    fresh, reason = (
        ("draft", "")
        if mem_status == "draft"
        else evaluate_memory_freshness(
            repo_root, fm, catalog_ids, today=effective_today, head_sha=head_sha, eidos_dir=eidos_dir
        )
    )

    try:
        rel_path = mem_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel_path = mem_path.as_posix()

    return {
        "id": str(fm.get("id") or doc.doc_id),
        "verified_at": fm["verified_at"],
        "verified_commit": fm.get("verified_commit"),
        "expires_at": fm.get("expires_at"),
        "freshness": fresh,
        "freshness_reason": reason,
        "path": str(mem_path),
        "relative_path": rel_path,
    }


def archive_memory(
    eidos_dir: Path,
    target: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Transition a team memory to ``status: archived`` and move it into ``eidos/memory/archive/``."""
    mem_path = find_memory_by_id_or_path(eidos_dir, target, include_archived=True)
    doc = parse_spec_file(mem_path)
    fm = dict(doc.frontmatter or {})
    if doc.doc_type != "agent/memory":
        raise ValueError(f"'{target}' is type '{doc.doc_type}', not 'agent/memory'.")

    if str(fm.get("status") or "").strip().lower() == "promoted":
        raise ValueError(
            f"Memory '{doc.doc_id or target}' is already promoted to '{fm.get('promoted_to')}'. "
            "Keep its promoted status rather than overwriting it with archived."
        )

    fm["status"] = "archived"
    body = doc.body or ""
    if reason and reason.strip():
        clean_reason = sanitize_memory_asciidoc_body(reason.strip())
        if clean_reason:
            if re.search(r"^==[ \t]+Archive Note\s*$", body, re.MULTILINE):
                body = body.rstrip() + f"\n\n{clean_reason}\n"
            else:
                body = body.rstrip() + f"\n\n== Archive Note\n\n{clean_reason}\n"

    _, arch_dir = _memory_dirs(eidos_dir)
    dest_path = _unique_archive_dest(arch_dir, mem_path)

    updated_doc = SpecDocument(
        file_path=dest_path,
        frontmatter=fm,
        body=body,
        doc_type="agent/memory",
        doc_id=str(fm.get("id") or doc.doc_id),
    )
    errors = SpecValidator().validate_document(updated_doc, root_dir=eidos_dir)
    if errors:
        raise ValueError(f"Archived memory failed schema validation: {'; '.join(errors)}")

    dest_path.write_text(dump_spec_document(fm, body), encoding="utf-8")
    if mem_path.resolve() != dest_path and mem_path.exists():
        mem_path.unlink()

    _sync_index_silently(eidos_dir)
    repo_root = _resolve_repo_root(eidos_dir)
    try:
        rel_path = dest_path.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel_path = dest_path.as_posix()

    return {
        "id": str(fm.get("id") or doc.doc_id),
        "status": "archived",
        "path": str(dest_path),
        "relative_path": rel_path,
        "moved": mem_path.resolve() != dest_path,
    }


def promote_memory(
    eidos_dir: Path,
    target: str,
    *,
    to_kind: str,
    spec_id: str,
    archive: bool = True,
) -> dict[str, Any]:
    """Mark a team memory ``status: promoted`` linked to a verified ``DEC-*``, ``INV-*``, or ``POL-*`` spec."""
    resolved_target = resolve_promotion_target(eidos_dir, spec_id, kind=to_kind)
    mem_path = find_memory_by_id_or_path(eidos_dir, target, include_archived=True)
    doc = parse_spec_file(mem_path)
    fm = dict(doc.frontmatter or {})
    if doc.doc_type != "agent/memory":
        raise ValueError(f"'{target}' is type '{doc.doc_type}', not 'agent/memory'.")

    fm["status"] = "promoted"
    fm["promoted_to"] = resolved_target["id"]

    _, arch_dir = _memory_dirs(eidos_dir)
    dest_path = _unique_archive_dest(arch_dir, mem_path) if archive else mem_path.resolve()

    updated_doc = SpecDocument(
        file_path=dest_path,
        frontmatter=fm,
        body=doc.body,
        doc_type="agent/memory",
        doc_id=str(fm.get("id") or doc.doc_id),
    )
    errors = SpecValidator().validate_document(updated_doc, root_dir=eidos_dir)
    if errors:
        raise ValueError(f"Promoted memory failed schema validation: {'; '.join(errors)}")

    dest_path.write_text(dump_spec_document(fm, doc.body), encoding="utf-8")
    if dest_path != mem_path.resolve() and mem_path.exists():
        mem_path.unlink()

    _sync_index_silently(eidos_dir)
    repo_root = _resolve_repo_root(eidos_dir)
    try:
        rel_path = dest_path.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel_path = dest_path.as_posix()

    return {
        "id": str(fm.get("id") or doc.doc_id),
        "status": "promoted",
        "promoted_to": resolved_target["id"],
        "target": resolved_target,
        "archived": dest_path.is_relative_to(arch_dir.resolve()),
        "path": str(dest_path),
        "relative_path": rel_path,
    }


def _audit_overlaps_for_active_memories(
    conn: Any,
    active_entries: list[dict[str, Any]],
    *,
    use_semantic: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute non-blocking duplicate memory pairs and formal spec overlaps across active memories."""
    if not active_entries:
        return [], []

    # Fast pre-computed semantic BLOB scan (0 ONNX inference calls!) when embeddings are complete
    if use_semantic:
        with contextlib.suppress(ImportError, Exception):
            import numpy as np

            from eidos.core.index.semantic import is_semantic_index_complete, unpack_embedding_f32

            if is_semantic_index_complete(conn):
                expected_spec_row = conn.execute(
                    """
                    SELECT COUNT(*) FROM entities e
                    JOIN specs_fts f ON f.entity_id = e.id
                    WHERE e.type IN ('product/policy', 'invariant');
                    """
                ).fetchone()
                expected_spec_count = int(expected_spec_row[0]) if expected_spec_row else 0
                rows = conn.execute(
                    """
                    SELECT s.entity_id, s.embedding, e.type, e.title
                    FROM spec_embeddings s
                    JOIN entities e ON e.id = s.entity_id
                    WHERE e.type IN ('agent/memory', 'product/policy', 'invariant')
                      AND (e.type != 'agent/memory' OR e.status = 'active');
                    """
                ).fetchall()
                mem_vecs: list[tuple[str, str, Any]] = []
                spec_vecs: list[tuple[str, str, str, Any]] = []
                active_ids = {e["id"] for e in active_entries}
                expected_dim: int | None = None
                valid_blobs = True
                for row in rows:
                    blob = row["embedding"]
                    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) == 0 or len(blob) % 4 != 0:
                        valid_blobs = False
                        break
                    try:
                        vec = np.asarray(unpack_embedding_f32(blob), dtype=np.float32)
                    except (ValueError, TypeError, struct.error):
                        valid_blobs = False
                        break
                    if expected_dim is None:
                        expected_dim = int(vec.shape[0])
                    elif int(vec.shape[0]) != expected_dim:
                        valid_blobs = False
                        break
                    norm = float(np.linalg.norm(vec))
                    if norm <= 1e-12:
                        continue
                    uvec = vec / norm
                    if row["type"] == "agent/memory" and row["entity_id"] in active_ids:
                        mem_vecs.append((str(row["entity_id"]), str(row["title"] or row["entity_id"]), uvec))
                    elif row["type"] in ("product/policy", "invariant"):
                        spec_vecs.append(
                            (str(row["entity_id"]), str(row["type"]), str(row["title"] or row["entity_id"]), uvec)
                        )
                if valid_blobs and len(mem_vecs) == len(active_entries) and len(spec_vecs) >= expected_spec_count:
                    dup_pairs: list[dict[str, Any]] = []
                    for i in range(len(mem_vecs)):
                        for j in range(i + 1, len(mem_vecs)):
                            cos = float(np.dot(mem_vecs[i][2], mem_vecs[j][2]))
                            if cos >= DUP_MEMORY_SEMANTIC_THRESHOLD:
                                a_id, b_id = sorted((mem_vecs[i][0], mem_vecs[j][0]))
                                dup_pairs.append(
                                    {"memory_a": a_id, "memory_b": b_id, "score": round(cos, 4), "method": "semantic"}
                                )
                    spec_overlaps: list[dict[str, Any]] = []
                    for mid, _mtitle, mvec in mem_vecs:
                        for sid, stype, stitle, svec in spec_vecs:
                            cos = float(np.dot(mvec, svec))
                            if cos >= SPEC_OVERLAP_SEMANTIC_THRESHOLD:
                                spec_overlaps.append(
                                    {
                                        "memory_id": mid,
                                        "spec_id": sid,
                                        "spec_type": stype,
                                        "spec_title": stitle,
                                        "score": round(cos, 4),
                                        "method": "semantic",
                                    }
                                )
                    dup_pairs.sort(key=lambda r: (-r["score"], r["memory_a"], r["memory_b"]))
                    spec_overlaps.sort(key=lambda r: (-r["score"], r["memory_id"], r["spec_id"]))
                    return dup_pairs, spec_overlaps

    # Deterministic FTS5 BM25 fallback
    seen_pairs: set[tuple[str, str]] = set()
    dup_pairs = []
    spec_overlaps = []
    for entry in active_entries:
        mid = entry["id"]
        q = f"{entry['title']} {entry['summary']}".strip()
        if not q:
            continue
        overlaps = _scan_bm25_overlaps(conn, q, exclude_id=mid)
        for dup in overlaps["duplicate_memories"]:
            pair = (min(mid, dup["id"]), max(mid, dup["id"]))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                dup_pairs.append({"memory_a": pair[0], "memory_b": pair[1], "score": dup["score"], "method": "bm25"})
        for ov in overlaps["spec_overlaps"]:
            spec_overlaps.append(
                {
                    "memory_id": mid,
                    "spec_id": ov["id"],
                    "spec_type": ov["type"],
                    "spec_title": ov["title"],
                    "score": ov["score"],
                    "method": "bm25",
                }
            )
    dup_pairs.sort(key=lambda r: (-r["score"], r["memory_a"], r["memory_b"]))
    spec_overlaps.sort(key=lambda r: (-r["score"], r["memory_id"], r["spec_id"]))
    return dup_pairs, spec_overlaps


def audit_memories(
    eidos_dir: Path,
    *,
    check_overlaps: bool = True,
    use_semantic: bool = False,
    today: date | None = None,
    entries: list[dict[str, Any]] | None = None,
    conn: Any = None,
) -> dict[str, Any]:
    """Run a deterministic, strictly non-blocking freshness, duplicate, and spec-overlap audit over ``eidos/memory/``."""
    resolved_entries = (
        list(entries)
        if entries is not None
        else list_memories(eidos_dir, include_archived=True, include_freshness=True, today=today)
    )
    active_entries = [e for e in resolved_entries if not e["is_archived"] and e["status"] == "active"]

    stale_list = [
        {"id": e["id"], "title": e["title"], "reason": e["freshness_reason"], "relative_path": e["relative_path"]}
        for e in active_entries
        if e["freshness"] == "stale"
    ]
    expired_list = [
        {"id": e["id"], "title": e["title"], "reason": e["freshness_reason"], "relative_path": e["relative_path"]}
        for e in active_entries
        if e["freshness"] == "expired"
    ]
    unverifiable_list = [
        {"id": e["id"], "title": e["title"], "reason": e["freshness_reason"], "relative_path": e["relative_path"]}
        for e in active_entries
        if e["freshness"] == "unverifiable"
    ]

    dup_pairs: list[dict[str, Any]] = []
    spec_overlaps: list[dict[str, Any]] = []
    if check_overlaps and active_entries:
        active_conn = conn
        if active_conn is None:
            idx = _sync_index_silently(eidos_dir)
            active_conn = idx._get_connection()
        dup_pairs, spec_overlaps = _audit_overlaps_for_active_memories(
            active_conn,
            active_entries,
            use_semantic=use_semantic,
        )

    promoted_count = sum(1 for e in resolved_entries if e["status"] == "promoted")
    draft_count = sum(1 for e in resolved_entries if e["status"] == "draft")
    archived_count = sum(
        1
        for e in resolved_entries
        if e["status"] == "archived" or (e["is_archived"] and e["status"] not in ("promoted", "draft"))
    )

    return {
        "total": len(resolved_entries),
        "active": len(active_entries),
        "draft": draft_count,
        "promoted": promoted_count,
        "archived": archived_count,
        "fresh": sum(1 for e in active_entries if e["freshness"] == "fresh"),
        "stale": len(stale_list),
        "expired": len(expired_list),
        "unverifiable": len(unverifiable_list),
        "stale_memories": stale_list,
        "expired_memories": expired_list,
        "unverifiable_memories": unverifiable_list,
        "duplicate_pairs": dup_pairs,
        "spec_overlaps": spec_overlaps,
    }
