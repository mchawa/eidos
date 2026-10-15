"""Full-text lexical search and recursive CTE graph traversal query engine (CMP-FTS-QUERY-ENGINE, CMP-GRAPH-TRAVERSAL-ENGINE).

Provides column-weighted BM25 search with de-compounded identifiers and recursive CTE traversal
for sub-millisecond graph and blast-radius queries.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

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
              AND gw.path NOT LIKE '%' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END || '%'
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
    """Find all entities that reference target_id_or_path."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM entities WHERE id = ? OR file_path = ?;", (target_id_or_path, target_id_or_path))
    row = cursor.fetchone()
    resolved_id = row["id"] if row else target_id_or_path

    cursor.execute(
        """
        SELECT e.id, e.type, e.title, ed.rel_type, ed.metadata_json
        FROM edges ed
        JOIN entities e ON e.id = ed.source_id
        WHERE ed.target_id = ?;
        """,
        (resolved_id,),
    )
    results = []
    for r in cursor.fetchall():
        results.append(
            {
                "id": r["id"],
                "type": r["type"],
                "title": r["title"],
                "rel_type": r["rel_type"],
                "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
            }
        )
    return results
