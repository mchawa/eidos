"""Full-text FTS5 BM25 lexical search, symbol lookup, and repo-map projection (CMP-FTS-QUERY-ENGINE)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import CodeSymbol, get_ast_indexer
from eidos.core.index.storage import (
    NON_AST_SYMBOL_KINDS as _NON_AST_SYMBOL_KINDS,
)
from eidos.core.index.storage import (
    provenance_to_confidence,
)
from eidos.core.index.storage import (
    resolve_symbol_locations_in_db as resolve_symbol_locations_in_db,
)
from eidos.core.spec.models import ENTITY_PREFIX_REGISTRY

# FTS5 ``bm25()`` weights are positional over *every* declared column of ``specs_fts``, including the UNINDEXED
# ``entity_id`` column, so the first weight must be a placeholder. Order: entity_id, title, jtbd_or_rule, body_text,
# keywords, symbols. Short, high-signal fields outrank long prose bodies (DEC-INDEX-FTS-RETRIEVAL-HYGIENE).
FTS_COLUMN_WEIGHTS: tuple[float, ...] = (0.0, 4.0, 2.0, 1.0, 2.0, 2.0)

# Entity types that get_tiered_context_slice can surface from a task prompt. Restricting the BM25 candidate pool to
# these types keeps historical SpecDeltas and explorations from consuming the top-k slots while allowing personas
# and design entities to expand to their governing capabilities and containers.
PRIMARY_TASK_SLICE_ENTITY_TYPES: tuple[str, ...] = (
    "tech/container",
    "tech/component",
    "tech/contract",
    "product/capability",
    "product/policy",
    "invariant",
    "product/domain",
    "design/system",
)

SECONDARY_TASK_SLICE_ENTITY_TYPES: tuple[str, ...] = (
    "product/persona",
    "design/client",
    "design/ui-component",
    "design/screen",
    "design/flow",
)

TASK_SLICE_ENTITY_TYPES: tuple[str, ...] = (
    *PRIMARY_TASK_SLICE_ENTITY_TYPES,
    *SECONDARY_TASK_SLICE_ENTITY_TYPES,
)

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_ENTITY_ID_TOKEN_RE = re.compile(
    rf"\b(?:{'|'.join(re.escape(p.rstrip('-')) for p in ENTITY_PREFIX_REGISTRY)})-[A-Za-z0-9_-]+\b"
)

_FTS_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "both",
        "but",
        "by",
        "can",
        "do",
        "does",
        "each",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "make",
        "more",
        "must",
        "no",
        "not",
        "of",
        "on",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "per",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "up",
        "use",
        "using",
        "very",
        "via",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "will",
        "with",
        "within",
        "without",
        "would",
    }
)


def split_identifier(token: str) -> list[str]:
    """Split a snake_case / camelCase / PascalCase identifier into lowercase sub-tokens.

    ``get_tiered_context_slice`` -> ``[get, tiered, context, slice]``; ``ArchitecturalIndex`` ->
    ``[architectural, index]``; ``HTTPServer`` -> ``[http, server]``. Plain words return ``[word]``.
    """
    if "_" not in token and token and token.isascii() and (len(token) == 1 or token.islower() or token[1:].islower()):
        return [token.lower()]
    parts: list[str] = []
    for chunk in token.split("_"):
        if chunk:
            for p in _CAMEL_BOUNDARY_RE.split(chunk):
                if p:
                    parts.append(p.lower())
    return parts


def compound_identifier_subtokens(text: str) -> str:
    """Return the de-compounded sub-tokens of every camelCase / snake_case identifier found in ``text``.

    Only compound identifiers contribute (plain words are already tokenized by ``unicode61``), so the output is a
    compact, order-preserving, de-duplicated supplement for the FTS ``symbols`` column.
    """
    seen: dict[str, None] = {}
    for ident in dict.fromkeys(_IDENTIFIER_RE.findall(text or "")):
        if "_" not in ident and (len(ident) == 1 or ident.islower() or ident[1:].islower()):
            continue
        parts = split_identifier(ident)
        if len(parts) > 1:
            for part in parts:
                seen.setdefault(part, None)
    return " ".join(seen)


_ARCHITECTURAL_SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    "adr": ("decision", "decisions", "pol", "002"),
    "adrs": ("decision", "decisions", "pol", "002"),
    "airgapped": ("offline", "local", "zero", "ai", "call", "dependency", "pol", "004"),
    "offline": ("airgapped", "local", "zero", "ai", "call", "dependency", "pol", "004"),
    "llm": ("ai", "call", "dependency", "pol", "004"),
    "neural": ("semantic", "embedding", "onnx", "harrier"),
    "vector": ("semantic", "embedding", "onnx", "cosine"),
    "embeddings": ("semantic", "embedding", "onnx", "harrier"),
    "cyclic": ("circular", "cycle", "dag", "acyclic"),
    "circular": ("cyclic", "cycle", "dag", "acyclic"),
    "cycle": ("cyclic", "circular", "dag", "acyclic"),
    "acyclic": ("cyclic", "circular", "cycle", "dag"),
    "dag": ("cyclic", "circular", "cycle", "acyclic"),
    "orphan": ("unmapped", "untracked", "tombstone", "drift", "deletion"),
    "orphaned": ("unmapped", "untracked", "tombstone", "drift", "deletion"),
    "dangling": ("orphan", "tombstone", "broken", "reference"),
    "tombstone": ("orphan", "orphaned", "dangling", "deletion"),
    "stale": ("outdated", "dirty", "mtime", "sha256", "sync"),
    "invalidation": ("stale", "dirty", "mtime", "sha256", "cache"),
    "mtime": ("stale", "invalidation", "dirty", "cache"),
    "truncation": ("budget", "token", "guardrails", "trim", "evict"),
    "eviction": ("budget", "token", "guardrails", "trim", "pop"),
    "breaking": ("contract", "compatibility", "version", "interface"),
    "semver": ("version", "contract", "compatibility"),
}


def expand_architectural_synonyms(text: str) -> str:
    """Return deterministic Eidos architectural synonym tokens for conceptual query or keyword terms."""
    seen: dict[str, None] = {}
    for tok in re.findall(r"\w+", text or ""):
        for part in split_identifier(tok):
            for syn in _ARCHITECTURAL_SYNONYM_MAP.get(part, ()):
                seen.setdefault(syn, None)
        lowered = tok.lower()
        for syn in _ARCHITECTURAL_SYNONYM_MAP.get(lowered, ()):
            seen.setdefault(syn, None)
    return " ".join(seen)


def build_fts_match_query(text: str, operator: str = "OR", expand_synonyms: bool = False) -> str:
    """Translate a free-text prompt into a deterministic FTS5 prefix query with stop-word filtering.

    Supports ``operator="OR"`` (default) or ``operator="AND"``. Each ``\\w+`` token contributes its identifier
    sub-tokens plus, for non-snake_case tokens in ``OR`` mode, the whole lowercased token.
    When ``expand_synonyms=True`` and ``operator="OR"``, deterministic architectural synonym tokens from
    ``_ARCHITECTURAL_SYNONYM_MAP`` are appended so zero-keyword-overlap conceptual queries resolve cleanly.
    """
    op_norm = operator.strip().upper()
    if op_norm not in ("OR", "AND"):
        op_norm = "OR"
    raw_terms: dict[str, None] = {}
    filtered_terms: dict[str, None] = {}
    for tok in re.findall(r"\w+", text or ""):
        parts = split_identifier(tok)
        for part in parts:
            raw_terms.setdefault(part, None)
            if len(part) > 1 and part not in _FTS_STOP_WORDS:
                filtered_terms.setdefault(part, None)
        if "_" not in tok and (op_norm == "OR" or len(parts) <= 1):
            lowered = tok.lower()
            raw_terms.setdefault(lowered, None)
            if len(lowered) > 1 and lowered not in _FTS_STOP_WORDS:
                filtered_terms.setdefault(lowered, None)
    if expand_synonyms and op_norm == "OR":
        for syn in expand_architectural_synonyms(text).split():
            if len(syn) > 1 and syn not in _FTS_STOP_WORDS:
                filtered_terms.setdefault(syn, None)
                raw_terms.setdefault(syn, None)
    active_terms = filtered_terms if filtered_terms else raw_terms
    joiner = f" {op_norm} "
    return joiner.join(f'"{term}"*' for term in active_terms)


def execute_fts_query(
    conn: sqlite3.Connection,
    text: str,
    limit: int = 10,
    include_types: tuple[str, ...] | list[str] | None = None,
    exclude_types: tuple[str, ...] | list[str] = (),
    use_semantic: bool = True,
    eidos_dir: Path | None = None,
    query_embedding: Any | None = None,
) -> list[dict[str, Any]]:
    """Execute a column-weighted BM25 ranked full-text query using FTS5 with exact entity ID, multi-term AND boosting, and optional gated Hybrid RRF."""
    if not text or not text.strip():
        return []

    fts_query = build_fts_match_query(text, operator="OR", expand_synonyms=False)
    if not fts_query:
        return []
    and_query = build_fts_match_query(text, operator="AND", expand_synonyms=False)
    syn_query = build_fts_match_query(text, operator="OR", expand_synonyms=True)

    explicit_ids = list(dict.fromkeys(m.upper() for m in _ENTITY_ID_TOKEN_RE.findall(text)))

    type_clauses: list[str] = []
    type_params: list[str] = []
    if include_types is not None:
        include = list(include_types)
        if not include:
            return []
        type_clauses.append(f"e.type IN ({','.join('?' for _ in include)})")
        type_params.extend(include)
    exclude = list(exclude_types or ())
    if exclude:
        type_clauses.append(f"e.type NOT IN ({','.join('?' for _ in exclude)})")
        type_params.extend(exclude)
    type_sql = "".join(f" AND {clause}" for clause in type_clauses)
    weights_sql = ", ".join(repr(float(w)) for w in FTS_COLUMN_WEIGHTS)

    id_boost_sql = ""
    id_boost_params: list[str] = []
    if explicit_ids:
        ph = ",".join("?" for _ in explicit_ids)
        kw_clauses = " OR ".join("(' ' || UPPER(f.keywords) || ' ') LIKE '% ' || ? || ' %'" for _ in explicit_ids)
        id_boost_sql = f"(CASE WHEN UPPER(e.id) IN ({ph}) OR ({kw_clauses}) THEN -1000.0 ELSE 0.0 END) + "
        id_boost_params.extend(explicit_ids)
        id_boost_params.extend(explicit_ids)

    cursor = conn.cursor()
    try:
        has_embeddings = False
        if use_semantic:
            try:
                from eidos.core.index.semantic import is_semantic_index_complete

                has_embeddings = is_semantic_index_complete(conn)
            except sqlite3.OperationalError:
                has_embeddings = False

        lex_fetch_limit = max(limit * 3, 24) if has_embeddings else limit

        and_matched_ids: set[str] = set()
        if and_query and and_query != fts_query:
            cursor.execute(
                f"""
                SELECT e.id
                FROM specs_fts f
                JOIN entities e ON e.id = f.entity_id
                WHERE specs_fts MATCH ?{type_sql};
                """,
                (and_query, *type_params),
            )
            and_matched_ids = {row["id"] for row in cursor.fetchall()}

        and_boost_sql = ""
        and_boost_params: list[str] = []
        if and_matched_ids:
            sorted_and_ids = sorted(and_matched_ids)
            ph_and = ",".join("?" for _ in sorted_and_ids)
            and_boost_sql = f"(CASE WHEN e.id IN ({ph_and}) THEN -50.0 ELSE 0.0 END) + "
            and_boost_params.extend(sorted_and_ids)

        def _fetch_fts_rows(match_expr: str, row_limit: int) -> list[dict[str, Any]]:
            cursor.execute(
                f"""
                SELECT
                    e.id, e.type, e.title, e.domain, e.file_path, e.data_json,
                    {id_boost_sql}{and_boost_sql}bm25(specs_fts, {weights_sql}) as rank
                FROM specs_fts f
                JOIN entities e ON e.id = f.entity_id
                WHERE specs_fts MATCH ?{type_sql}
                ORDER BY rank, e.id
                LIMIT ?;
                """,
                (*id_boost_params, *and_boost_params, match_expr, *type_params, row_limit),
            )
            rows_out: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                data = orjson.loads(row["data_json"])
                raw_sp = data.get("source_path") or ""
                source_path = (
                    raw_sp[0] if isinstance(raw_sp, list) and raw_sp else (raw_sp if isinstance(raw_sp, str) else "")
                )
                rows_out.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "domain": row["domain"],
                        "source_path": source_path,
                        "file_path": row["file_path"],
                        "rank": row["rank"],
                        "provenance": "fts_bm25",
                        "confidence": "observed",
                        "data": data,
                    }
                )
            return rows_out

        results = _fetch_fts_rows(fts_query, lex_fetch_limit)

        if len(results) < limit and syn_query and syn_query != fts_query:
            seen_ids = {r["id"] for r in results}
            for syn_row in _fetch_fts_rows(syn_query, lex_fetch_limit):
                if syn_row["id"] not in seen_ids:
                    results.append(syn_row)
                    seen_ids.add(syn_row["id"])
                    if len(results) >= lex_fetch_limit:
                        break

        if has_embeddings:
            from eidos.core.index.semantic import (
                DEFAULT_RRF_K0,
                DEFAULT_RRF_W_LEX,
                DEFAULT_RRF_W_SEM,
                DEFAULT_SEMANTIC_MIN_COSINE,
                _refresh_stale_embeddings_for_query,
                encode_semantic_query,
                fuse_hybrid_rrf,
                query_semantic_embeddings,
            )

            if query_embedding is None:
                _refresh_stale_embeddings_for_query(conn, eidos_dir)
            q_vec = query_embedding if query_embedding is not None else encode_semantic_query(text, eidos_dir)
            if q_vec is not None:
                sem_hits = query_semantic_embeddings(
                    conn,
                    query_embedding=q_vec,
                    limit=max(limit * 3, 24),
                    include_types=include_types,
                    exclude_types=exclude_types,
                    tau_min=DEFAULT_SEMANTIC_MIN_COSINE,
                )
                if sem_hits:
                    fused_rows = fuse_hybrid_rrf(
                        results,
                        sem_hits,
                        explicit_ids=explicit_ids,
                        and_matched_ids=and_matched_ids,
                        limit=limit,
                        k0=DEFAULT_RRF_K0,
                        w_lex=DEFAULT_RRF_W_LEX,
                        w_sem=DEFAULT_RRF_W_SEM,
                        tau_min=DEFAULT_SEMANTIC_MIN_COSINE,
                    )
                    for f_row in fused_rows:
                        msrc = str(f_row.get("match_source") or "lexical").lower()
                        if msrc == "semantic":
                            prov = "semantic_rrf"
                        elif msrc == "hybrid":
                            prov = "hybrid_rrf"
                        else:
                            prov = "fts_bm25"
                        f_row["provenance"] = prov
                        f_row["confidence"] = provenance_to_confidence(prov)
                    return fused_rows

        return results[:limit]
    except sqlite3.OperationalError:
        return []


def _escape_like(val: str) -> str:
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_symbol_in_db(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Locate symbol matches and their associated living component or automated test specs across the Code Graph."""
    q_norm = (query or "").strip()
    if not q_norm:
        return []
    cursor = conn.cursor()
    q_alt = q_norm.replace("-", "_") if "-" in q_norm else q_norm.replace("_", "-")
    q_norm_like = f"%{_escape_like(q_norm)}%"
    q_alt_like = f"%{_escape_like(q_alt)}%"
    cursor.execute(
        """
        WITH all_mappings(symbol, entity_id, kind, is_test) AS (
            SELECT symbol, entity_id, kind, 0 AS is_test FROM symbol_mappings
            UNION
            SELECT symbol, entity_id, kind, is_test FROM code_symbols WHERE kind NOT IN ('import', 'namespace')
            UNION
            SELECT parent_symbol || '.' || symbol, entity_id, 'method', is_test
            FROM code_symbols
            WHERE kind NOT IN ('import', 'namespace', 'http_endpoint', 'http_request') AND parent_symbol != ''
        )
        SELECT s.symbol, s.entity_id, s.kind, s.is_test, e.title, e.type, e.file_path
        FROM all_mappings s
        LEFT JOIN entities e ON e.id = s.entity_id
        WHERE (e.id IS NOT NULL OR s.is_test = 1)
          AND (
              s.symbol = ?
              OR s.symbol = ?
              OR s.symbol LIKE ? ESCAPE '\\'
              OR s.symbol LIKE ? ESCAPE '\\'
          )
        ORDER BY CASE WHEN s.symbol IN (?, ?) THEN 0 ELSE 1 END, s.is_test ASC, length(s.symbol) ASC, s.symbol ASC
        LIMIT ?;
        """,
        (q_norm, q_alt, q_norm_like, q_alt_like, q_norm, q_alt, limit),
    )
    results: list[dict[str, Any]] = []
    seen_loc_keys: set[tuple[str, str, str, int]] = set()
    for r in cursor.fetchall():
        is_test_flag = bool(r["is_test"])
        rk = str(r["kind"] or "")
        sym_prov = (
            "inferred_ownership"
            if rk == "inferred_path"
            else ("declared_spec" if rk in _NON_AST_SYMBOL_KINDS else "ast_symbol")
        )
        base = {
            "symbol": r["symbol"],
            "entity_id": r["entity_id"],
            "kind": rk,
            "title": r["title"] or ("Automated Test Suite" if is_test_flag else r["entity_id"]),
            "type": r["type"] or ("code/test" if is_test_flag else ""),
            # Spec path of the owning entity (or test file path for standalone automated test symbols);
            # source coordinates live in 'location'.
            "file_path": r["file_path"],
            "is_test": is_test_flag,
            "provenance": sym_prov,
            "confidence": provenance_to_confidence(sym_prov),
        }
        locations = (
            [] if rk in _NON_AST_SYMBOL_KINDS else resolve_symbol_locations_in_db(conn, r["entity_id"], rk, r["symbol"])
        )
        if locations:
            for loc in locations:
                loc_key = (r["entity_id"], rk, str(loc.get("file") or ""), int(loc.get("line_start") or 0))
                if loc_key not in seen_loc_keys:
                    seen_loc_keys.add(loc_key)
                    row_fp = base["file_path"] or str(loc.get("file") or "")
                    results.append({**base, "file_path": row_fp, "location": loc})
        else:
            results.append({**base, "file_path": base["file_path"] or "", "location": None})
    return results[:limit]


def generate_repo_map_from_db(conn: sqlite3.Connection, max_tokens: int = 1500) -> str:
    """Generate a token-budgeted structural repo map outline across all indexed components for AI coding agents.

    Reconstructed entirely from the persisted ``code_symbols`` table; source files are never re-parsed.
    """
    indexer = get_ast_indexer()
    rows = conn.execute(
        """
        SELECT file_path, symbol, kind, parent_symbol, line_start, line_end, signature
        FROM code_symbols
        WHERE is_primary = 1
          AND is_test = 0
          AND kind NOT IN ('import', 'namespace', 'http_endpoint', 'http_request')
        ORDER BY file_path, id;
        """
    ).fetchall()

    symbols_by_file: dict[str, list[Any]] = {}
    for r in rows:
        symbols_by_file.setdefault(r["file_path"], []).append(
            CodeSymbol(
                name=r["symbol"],
                kind=r["kind"],
                file_path=r["file_path"],
                line_start=r["line_start"],
                line_end=r["line_end"],
                parent_symbol=r["parent_symbol"] or None,
                signature=r["signature"] or None,
            )
        )

    return indexer.generate_repo_map(symbols_by_file, max_tokens=max_tokens)
