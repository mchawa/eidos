"""Full-text lexical search and recursive CTE graph traversal query engine (CMP-FTS-QUERY-ENGINE, CMP-GRAPH-TRAVERSAL-ENGINE).

Provides column-weighted BM25 search with de-compounded identifiers, recursive CTE traversal,
symbol/path resolution, and structural repo map generation for sub-millisecond graph queries.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from eidos.core.index.ast_indexer import CodeSymbol, get_ast_indexer
from eidos.core.index.sync import _NON_AST_SYMBOL_KINDS
from eidos.core.workspace.project import match_path_pattern

# FTS5 ``bm25()`` weights are positional over *every* declared column of ``specs_fts``, including the UNINDEXED
# ``entity_id`` column, so the first weight must be a placeholder. Order: entity_id, title, jtbd_or_rule, body_text,
# keywords, symbols. Short, high-signal fields outrank long prose bodies (DEC-INDEX-FTS-RETRIEVAL-HYGIENE).
FTS_COLUMN_WEIGHTS: tuple[float, ...] = (0.0, 4.0, 2.0, 1.0, 2.0, 2.0)

# Entity types that get_tiered_context_slice can surface from a task prompt. Restricting the BM25 candidate pool to
# these types keeps historical SpecDeltas, explorations, personas, and screens from consuming the top-k slots.
TASK_SLICE_ENTITY_TYPES: tuple[str, ...] = (
    "tech/container",
    "tech/component",
    "tech/contract",
    "product/capability",
    "product/policy",
    "invariant",
    "product/domain",
    "design/system",
)

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def split_identifier(token: str) -> list[str]:
    """Split a snake_case / camelCase / PascalCase identifier into lowercase sub-tokens.

    ``get_tiered_context_slice`` -> ``[get, tiered, context, slice]``; ``ArchitecturalIndex`` ->
    ``[architectural, index]``; ``HTTPServer`` -> ``[http, server]``. Plain words return ``[word]``.
    """
    parts: list[str] = []
    for chunk in token.split("_"):
        if chunk:
            parts.extend(p.lower() for p in _CAMEL_BOUNDARY_RE.split(chunk) if p)
    return parts


def compound_identifier_subtokens(text: str) -> str:
    """Return the de-compounded sub-tokens of every camelCase / snake_case identifier found in ``text``.

    Only compound identifiers contribute (plain words are already tokenized by ``unicode61``), so the output is a
    compact, order-preserving, de-duplicated supplement for the FTS ``symbols`` column.
    """
    seen: dict[str, None] = {}
    for ident in _IDENTIFIER_RE.findall(text or ""):
        parts = split_identifier(ident)
        if len(parts) > 1:
            for part in parts:
                seen.setdefault(part, None)
    return " ".join(seen)


def build_fts_match_query(text: str) -> str:
    """Translate a free-text prompt into a deterministic FTS5 ``OR`` prefix query with de-compounded terms.

    Each ``\\w+`` token contributes its identifier sub-tokens plus, for non-snake_case tokens, the whole lowercased
    token (so ``ArchitecturalIndex`` still prefix-matches the atomic ``architecturalindex`` index token). snake_case
    tokens are *not* passed through whole: FTS5 would re-tokenize them into an order-sensitive phrase query.
    """
    terms: dict[str, None] = {}
    for tok in re.findall(r"\w+", text or ""):
        for part in split_identifier(tok):
            terms.setdefault(part, None)
        if "_" not in tok:
            terms.setdefault(tok.lower(), None)
    return " OR ".join(f'"{term}"*' for term in terms)


def execute_fts_query(
    conn: sqlite3.Connection,
    text: str,
    limit: int = 10,
    include_types: tuple[str, ...] | list[str] | None = None,
    exclude_types: tuple[str, ...] | list[str] = (),
) -> list[dict[str, Any]]:
    """Execute a column-weighted BM25 ranked full-text query using FTS5."""
    if not text or not text.strip():
        return []

    fts_query = build_fts_match_query(text)
    if not fts_query:
        return []

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

    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT
                e.id, e.type, e.title, e.domain, e.package_path, e.data_json,
                bm25(specs_fts, {weights_sql}) as rank
            FROM specs_fts f
            JOIN entities e ON e.id = f.entity_id
            WHERE specs_fts MATCH ?{type_sql}
            ORDER BY rank, e.id
            LIMIT ?;
            """,
            (fts_query, *type_params, limit),
        )
        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "domain": row["domain"],
                    "package_path": row["package_path"],
                    "rank": row["rank"],
                    "data": json.loads(row["data_json"]),
                }
            )
        return results
    except sqlite3.OperationalError:
        return []


def execute_k_hop_traversal(
    conn: sqlite3.Connection,
    entity_id: str,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """Traverse graph up to k hops in both directions using recursive CTE."""
    cursor = conn.cursor()
    cursor.execute(
        """
        WITH RECURSIVE graph_walk(node_id, depth, path) AS (
            SELECT ?, 0, ?
            UNION
            SELECT
                CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END,
                gw.depth + 1,
                gw.path || '->' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END
            FROM edges e
            JOIN graph_walk gw ON (e.source_id = gw.node_id OR e.target_id = gw.node_id)
            WHERE gw.depth < ?
              AND ('->' || gw.path || '->') NOT LIKE '%->' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END || '->%'
        )
        SELECT DISTINCT gw.node_id, gw.depth, ent.type, ent.title, ent.domain, ent.file_path, ent.data_json
        FROM graph_walk gw
        JOIN entities ent ON ent.id = gw.node_id
        ORDER BY gw.depth ASC;
        """,
        (entity_id, entity_id, max_depth),
    )

    results = []
    for row in cursor.fetchall():
        results.append(
            {
                "id": row["node_id"],
                "depth": row["depth"],
                "type": row["type"],
                "title": row["title"],
                "domain": row["domain"],
                "file_path": row["file_path"],
                "data": json.loads(row["data_json"]),
            }
        )
    return results


def execute_incoming_references(
    conn: sqlite3.Connection,
    target_id_or_path: str,
) -> list[dict[str, Any]]:
    """Find all incoming graph edges pointing to an entity ID or file path."""
    target = target_id_or_path
    row = conn.execute(
        "SELECT doc_id FROM indexed_files WHERE rel_path = ? OR 'eidos/' || rel_path = ?;",
        (target, target),
    ).fetchone()
    if row:
        target = row["doc_id"]
    rows = conn.execute(
        """
        SELECT e.source_id, e.rel_type, ent.type, ent.title, ent.file_path
        FROM edges e
        JOIN entities ent ON ent.id = e.source_id
        WHERE e.target_id = ?
        ORDER BY ent.type, e.source_id;
        """,
        (target,),
    ).fetchall()
    return [
        {
            "source_id": r["source_id"],
            "rel_type": r["rel_type"],
            "type": r["type"],
            "title": r["title"],
            "file_path": r["file_path"],
        }
        for r in rows
    ]


def resolve_symbol_or_path_in_db(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    """Find entity IDs matching source path or symbol."""
    cursor = conn.cursor()

    clean = query.strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]

    cursor.execute(
        """
        SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
        FROM symbol_mappings s
        JOIN entities e ON e.id = s.entity_id
        WHERE s.symbol = ?
           OR (? LIKE s.symbol || '/%' AND s.kind IN ('package_path', 'inferred_path'))
           OR (? LIKE '%' || s.symbol AND s.kind IN ('source_path', 'inferred_path', 'specification_file', 'component_ref', 'interface_method', 'class', 'function', 'method', 'interface', 'struct', 'enum'))
           OR (? LIKE s.symbol || '%' AND s.kind = 'test_binding')
        LIMIT 25;
        """,
        (clean, clean, clean, clean),
    )

    results = []
    seen_entities: set[str] = set()
    for row in cursor.fetchall():
        seen_entities.add(row["entity_id"])
        results.append(
            {
                "symbol": row["symbol"],
                "entity_id": row["entity_id"],
                "kind": row["kind"],
                "type": row["type"],
                "title": row["title"],
                "file_path": row["file_path"],
                "data": json.loads(row["data_json"]),
            }
        )

    # Also check owned_path and inferred_path glob patterns if query looks like a file path
    if "/" in clean or "." in clean:
        cursor.execute(
            """
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.kind IN ('owned_path', 'inferred_path');
            """
        )
        scored_matches: list[tuple[int, dict[str, Any]]] = []
        for row in cursor.fetchall():
            if row["entity_id"] in seen_entities:
                continue
            score = match_path_pattern(clean, row["symbol"])
            if score > 0:
                scored_matches.append(
                    (
                        score,
                        {
                            "symbol": row["symbol"],
                            "entity_id": row["entity_id"],
                            "kind": row["kind"],
                            "type": row["type"],
                            "title": row["title"],
                            "file_path": row["file_path"],
                            "data": json.loads(row["data_json"]),
                        },
                    )
                )
        scored_matches.sort(key=lambda item: (-item[0], item[1]["entity_id"]))
        for _, item in scored_matches:
            if item["entity_id"] not in seen_entities:
                seen_entities.add(item["entity_id"])
                results.append(item)

    return results


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


def find_symbol_in_db(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Locate symbol matches and their associated living component specs across the Code Graph."""
    cursor = conn.cursor()
    q_norm = query.strip()
    q_alt = q_norm.replace("-", "_") if "-" in q_norm else q_norm.replace("_", "-")
    cursor.execute(
        """
        SELECT s.symbol, s.entity_id, s.kind, e.title, e.type, e.file_path
        FROM symbol_mappings s
        JOIN entities e ON e.id = s.entity_id
        WHERE s.symbol = ?
           OR s.symbol = ?
           OR s.symbol LIKE ?
           OR s.symbol LIKE ?
        ORDER BY CASE WHEN s.symbol IN (?, ?) THEN 0 ELSE 1 END, s.symbol ASC
        LIMIT ?;
        """,
        (q_norm, q_alt, f"%{q_norm}%", f"%{q_alt}%", q_norm, q_alt, limit),
    )
    results: list[dict[str, Any]] = []
    for r in cursor.fetchall():
        base = {
            "symbol": r["symbol"],
            "entity_id": r["entity_id"],
            "kind": r["kind"],
            "title": r["title"],
            "type": r["type"],
            # Spec path of the owning entity (unchanged meaning); source coordinates live in 'location'.
            "file_path": r["file_path"],
        }
        locations = (
            []
            if r["kind"] in _NON_AST_SYMBOL_KINDS
            else resolve_symbol_locations_in_db(conn, r["entity_id"], r["kind"], r["symbol"])
        )
        if locations:
            results.extend({**base, "location": loc} for loc in locations)
        else:
            results.append({**base, "location": None})
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
