"""Recursive CTE k-hop blast-radius traversal, symbol/path resolution, catalog & delta impact (CMP-GRAPH-TRAVERSAL-ENGINE)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import AstGrepIndexer
from eidos.core.index.storage import (
    allowed_provenances_for_confidence,
    build_spec_path_resolver,
    get_cached_spec_document,
    provenance_to_confidence,
)
from eidos.core.spec.models import resolve_entity_prefix_meta
from eidos.core.workspace.project import _POLYGLOT_TEST_CODE_EXTS, extract_entity_paths, match_path_pattern


def resolve_blast_radius_targets(
    conn: sqlite3.Connection,
    symbol_or_path: str,
) -> list[str]:
    """Resolve an entity ID, specification path, source file path, or AST symbol to target entity ID(s)."""
    raw_q = (symbol_or_path or "").strip()
    if not raw_q:
        return []

    row = conn.execute(
        "SELECT id FROM entities WHERE UPPER(id) = UPPER(?) LIMIT 1;",
        (raw_q,),
    ).fetchone()
    if row and row["id"]:
        return [str(row["id"])]

    clean_path = raw_q.replace("\\", "/")
    while clean_path.startswith("./"):
        clean_path = clean_path[2:]
    clean_path = clean_path.strip("/")
    if clean_path and clean_path.lower().endswith(".adoc"):
        spec_rows = conn.execute(
            """
            SELECT doc_id,
                   CASE
                       WHEN LOWER(rel_path) = LOWER(?)
                         OR LOWER('eidos/' || rel_path) = LOWER(?)
                         OR LOWER(rel_path) = LOWER('eidos/' || ?)
                       THEN 1
                       ELSE 0
                   END AS is_exact
            FROM indexed_files
            WHERE (
                LOWER(rel_path) = LOWER(?)
                OR LOWER('eidos/' || rel_path) = LOWER(?)
                OR LOWER(rel_path) = LOWER('eidos/' || ?)
                OR LOWER(rel_path) LIKE '%/' || LOWER(?)
            )
              AND doc_type NOT IN ('code/source', 'code/test')
              AND doc_id IS NOT NULL AND doc_id != ''
            ORDER BY is_exact DESC, length(rel_path) ASC;
            """,
            (clean_path, clean_path, clean_path, clean_path, clean_path, clean_path, clean_path),
        ).fetchall()
        if spec_rows:
            if any(r["is_exact"] for r in spec_rows):
                spec_rows = [r for r in spec_rows if r["is_exact"]]
            return list(dict.fromkeys(str(r["doc_id"]) for r in spec_rows if r["doc_id"]))

        ent_rows = conn.execute(
            """
            SELECT id,
                   CASE
                       WHEN LOWER(replace(file_path, '\\', '/')) = LOWER(?)
                         OR LOWER('eidos/' || replace(file_path, '\\', '/')) = LOWER(?)
                         OR LOWER(replace(file_path, '\\', '/')) = LOWER('eidos/' || ?)
                       THEN 1
                       ELSE 0
                   END AS is_exact
            FROM entities
            WHERE type != 'invariant'
              AND file_path IS NOT NULL AND file_path != ''
              AND (
                  LOWER(replace(file_path, '\\', '/')) = LOWER(?)
                  OR LOWER('eidos/' || replace(file_path, '\\', '/')) = LOWER(?)
                  OR LOWER(replace(file_path, '\\', '/')) = LOWER('eidos/' || ?)
                  OR LOWER(replace(file_path, '\\', '/')) LIKE '%/' || LOWER(?)
              )
            ORDER BY is_exact DESC, length(file_path) ASC, id ASC;
            """,
            (clean_path, clean_path, clean_path, clean_path, clean_path, clean_path, clean_path),
        ).fetchall()
        if ent_rows:
            if any(r["is_exact"] for r in ent_rows):
                ent_rows = [r for r in ent_rows if r["is_exact"]]
            return list(dict.fromkeys(str(r["id"]) for r in ent_rows if r["id"]))

    matches = resolve_symbol_or_path_in_db(conn, clean_path or raw_q)
    if not matches:
        return []
    non_path_kinds = {"class", "function", "method", "interface", "struct", "enum", "type", "constant", "variable"}
    has_exact_symbol = any(
        m.get("kind") in non_path_kinds and str(m.get("symbol") or "") == clean_path for m in matches
    )
    if has_exact_symbol:
        matches = [
            m for m in matches if m.get("kind") not in non_path_kinds or str(m.get("symbol") or "") == clean_path
        ]
    has_component = any(m.get("type") == "tech/component" for m in matches)
    if has_component:
        matches = [m for m in matches if m.get("type") == "tech/component"]
        has_exact_comp_path = any(
            m.get("kind") in ("source_path", "owned_path", "inferred_path")
            and str(m.get("symbol") or "").rstrip("/") == clean_path
            for m in matches
        )
        if has_exact_comp_path:
            matches = [m for m in matches if str(m.get("symbol") or "").rstrip("/") == clean_path]
    return list(dict.fromkeys(str(m["entity_id"]) for m in matches if m.get("entity_id")))


_WALK_CERTAINTY_RANK: dict[str, int] = {
    "inferred_ownership": 1,
    "ast_http": 2,
    "ast_import": 3,
    "ast_symbol": 4,
    "declared_spec": 5,
}


def execute_k_hop_traversal(
    conn: sqlite3.Connection,
    entity_id: str | Sequence[str],
    max_depth: int = 2,
    min_confidence: str = "inferred",
) -> list[dict[str, Any]]:
    """Traverse graph up to k hops in both directions using recursive CTE while filtering edges below min_confidence."""
    allowed_provs = allowed_provenances_for_confidence(min_confidence)
    if isinstance(entity_id, str):
        seed_ids = resolve_blast_radius_targets(conn, entity_id)
    else:
        seed_ids = []
        for item in entity_id:
            for r_id in resolve_blast_radius_targets(conn, str(item)):
                if r_id not in seed_ids:
                    seed_ids.append(r_id)
    if not seed_ids or not allowed_provs:
        return []

    seed_select_sql = " UNION ALL ".join("SELECT ?, 0, ?, 'declared_spec', '', ''" for _ in seed_ids)
    seed_params: list[Any] = []
    for sid in seed_ids:
        seed_params.extend((sid, sid))
    prov_ph = ",".join("?" for _ in allowed_provs)

    rank_case_e = (
        "CASE e.provenance "
        "WHEN 'declared_spec' THEN 5 WHEN 'ast_symbol' THEN 4 WHEN 'ast_import' THEN 3 "
        "WHEN 'ast_http' THEN 2 WHEN 'inferred_ownership' THEN 1 ELSE 5 END"
    )
    rank_case_gw = (
        "CASE gw.walk_prov "
        "WHEN 'declared_spec' THEN 5 WHEN 'ast_symbol' THEN 4 WHEN 'ast_import' THEN 3 "
        "WHEN 'ast_http' THEN 2 WHEN 'inferred_ownership' THEN 1 ELSE 5 END"
    )

    cursor = conn.cursor()
    cursor.execute(
        f"""
        WITH RECURSIVE
        graph_walk(node_id, depth, path, walk_prov, via_rel_type, evidence) AS (
            {seed_select_sql}
            UNION
            SELECT
                CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END,
                gw.depth + 1,
                gw.path || '->' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END,
                CASE
                    WHEN gw.depth = 0 THEN e.provenance
                    WHEN ({rank_case_e}) < ({rank_case_gw}) THEN e.provenance
                    ELSE gw.walk_prov
                END,
                e.rel_type,
                CASE WHEN e.evidence != '' THEN e.evidence ELSE gw.evidence END
            FROM edges e
            JOIN graph_walk gw ON (e.source_id = gw.node_id OR e.target_id = gw.node_id)
            JOIN entities cur_ent ON cur_ent.id = gw.node_id
            WHERE gw.depth < ?
              AND e.provenance IN ({prov_ph})
              AND (
                  gw.depth = 0
                  OR cur_ent.type NOT IN (
                      'product/domain', 'design/system', 'product/persona',
                      'delta/mutation', 'tech/architecture', 'product/system', 'product/exploration'
                  )
              )
              AND CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END NOT LIKE 'DLT-%'
              AND ('->' || gw.path || '->') NOT LIKE '%->' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END || '->%'
        )
        SELECT gw.node_id, gw.depth, gw.walk_prov, gw.via_rel_type, gw.evidence,
               ent.type, ent.title, ent.domain, ent.file_path, ent.data_json
        FROM graph_walk gw
        JOIN entities ent ON ent.id = gw.node_id
        ORDER BY gw.depth ASC, gw.node_id ASC;
        """,
        (*seed_params, max_depth, *allowed_provs),
    )

    best_by_node: dict[str, dict[str, Any]] = {}
    for row in cursor.fetchall():
        nid = row["node_id"]
        depth = int(row["depth"])
        prov = str(row["walk_prov"] or "declared_spec")
        rank = _WALK_CERTAINTY_RANK.get(prov, 5)
        existing = best_by_node.get(nid)
        if existing is not None:
            if depth > existing["depth"]:
                continue
            if depth == existing["depth"] and rank <= _WALK_CERTAINTY_RANK.get(existing["provenance"], 0):
                continue
        data = orjson.loads(row["data_json"]) if row["data_json"] else {}
        comp_domain = str(data.get("domain") or "").strip() if isinstance(data, dict) else ""
        best_by_node[nid] = {
            "id": nid,
            "depth": depth,
            "type": row["type"],
            "title": row["title"],
            "domain": comp_domain or row["domain"],
            "file_path": row["file_path"],
            "provenance": prov,
            "confidence": provenance_to_confidence(prov),
            "via_rel_type": str(row["via_rel_type"] or ""),
            "evidence": str(row["evidence"] or ""),
            "data": data,
        }
    return sorted(best_by_node.values(), key=lambda item: (item["depth"], item["id"]))


def execute_incoming_references(
    conn: sqlite3.Connection,
    target_id_or_path: str,
) -> list[dict[str, Any]]:
    """Find all incoming graph edges pointing to an entity ID or file path."""
    target = target_id_or_path.strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    row = conn.execute(
        """
        SELECT doc_id FROM indexed_files
        WHERE (rel_path = ? OR 'eidos/' || rel_path = ? OR rel_path = 'eidos/' || ?)
          AND doc_type NOT IN ('code/source', 'code/test')
        LIMIT 1;
        """,
        (target, target, target),
    ).fetchone()
    if row and row["doc_id"]:
        target = row["doc_id"]
    rows = conn.execute(
        """
        SELECT e.source_id, e.rel_type, e.provenance, e.evidence, ent.type, ent.title, ent.file_path
        FROM edges e
        JOIN entities ent ON ent.id = e.source_id
        WHERE e.target_id = ?
          AND e.rel_type NOT IN ('contains_container', 'describes_system')
        ORDER BY ent.type, e.source_id;
        """,
        (target,),
    ).fetchall()
    return [
        {
            "source_id": r["source_id"],
            "rel_type": r["rel_type"],
            "via_rel_type": r["rel_type"],
            "provenance": str(r["provenance"] or "declared_spec"),
            "confidence": provenance_to_confidence(str(r["provenance"] or "declared_spec")),
            "evidence": str(r["evidence"] or ""),
            "type": r["type"],
            "title": r["title"],
            "file_path": r["file_path"],
        }
        for r in rows
    ]


_FILE_QUERY_EXTS = frozenset(
    set(AstGrepIndexer.LANGUAGE_MAP.keys())
    | {
        ".cc",
        ".cxx",
        ".hxx",
        ".adoc",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".md",
        ".sh",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".proto",
        ".graphql",
        ".gql",
    }
)


def resolve_symbol_or_path_in_db(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    """Find entity IDs matching source path or symbol."""
    cursor = conn.cursor()

    clean = query.strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]

    is_file_query = "::" not in clean and ("/" in clean or Path(clean).suffix.lower() in _FILE_QUERY_EXTS)

    esc_sym = "replace(replace(replace(s.symbol, '\\', '\\\\'), '%', '\\%'), '_', '\\_')"
    if is_file_query:
        cursor.execute(
            f"""
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.symbol = ?
               OR (? LIKE {esc_sym} || '/%' ESCAPE '\\' AND s.kind IN ('package_path', 'inferred_path'))
               OR (? LIKE '%/' || {esc_sym} ESCAPE '\\' AND s.kind IN ('source_path', 'inferred_path', 'specification_file'))
               OR (? LIKE {esc_sym} || '%' ESCAPE '\\' AND s.kind = 'test_binding')
            ORDER BY CASE WHEN s.symbol = ? THEN 0 ELSE 1 END, length(s.symbol) DESC, s.entity_id ASC
            LIMIT 25;
            """,
            (clean, clean, clean, clean, clean),
        )
    else:
        cursor.execute(
            f"""
            WITH all_mappings(symbol, entity_id, kind) AS (
                SELECT symbol, entity_id, kind FROM symbol_mappings
                UNION
                SELECT symbol, entity_id, kind FROM code_symbols WHERE kind NOT IN ('import', 'namespace') AND is_test = 0
                UNION
                SELECT parent_symbol || '.' || symbol, entity_id, 'method'
                FROM code_symbols
                WHERE kind NOT IN ('import', 'namespace', 'http_endpoint', 'http_request') AND is_test = 0 AND parent_symbol != ''
            )
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
            FROM all_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.symbol = ?
               OR (? LIKE {esc_sym} || '/%' ESCAPE '\\' AND s.kind IN ('package_path', 'inferred_path'))
               OR (? LIKE '%/' || {esc_sym} ESCAPE '\\' AND s.kind IN ('source_path', 'inferred_path', 'specification_file'))
               OR ((? LIKE '%.' || {esc_sym} ESCAPE '\\' OR ? LIKE '%::' || {esc_sym} ESCAPE '\\')
                   AND s.kind IN ('class', 'function', 'method', 'interface', 'struct', 'enum'))
               OR (? LIKE {esc_sym} || '%' ESCAPE '\\' AND s.kind = 'test_binding')
            ORDER BY CASE WHEN s.symbol = ? THEN 0 ELSE 1 END, length(s.symbol) DESC, s.entity_id ASC
            LIMIT 25;
            """,
            (clean, clean, clean, clean, clean, clean, clean),
        )

    raw_rows = cursor.fetchall()
    pkg_prefix_lengths = [
        len(str(r["symbol"]).rstrip("/"))
        for r in raw_rows
        if r["kind"] in ("package_path", "inferred_path") and r["symbol"] != clean
    ]
    max_pkg_prefix_len = max(pkg_prefix_lengths) if pkg_prefix_lengths else 0

    exact_or_direct: list[dict[str, Any]] = []
    pkg_prefix_items: list[tuple[int, dict[str, Any]]] = []
    for row in raw_rows:
        item = {
            "symbol": row["symbol"],
            "entity_id": row["entity_id"],
            "kind": row["kind"],
            "type": row["type"],
            "title": row["title"],
            "file_path": row["file_path"],
            "_data_json": row["data_json"],
        }
        if row["kind"] in ("package_path", "inferred_path") and row["symbol"] != clean:
            sym_len = len(str(row["symbol"]).rstrip("/"))
            if sym_len < max_pkg_prefix_len:
                continue
            pkg_score = match_path_pattern(clean, f"{str(row['symbol']).rstrip('/')}/**")
            pkg_prefix_items.append((pkg_score, item))
        else:
            exact_or_direct.append(item)

    glob_scored_matches: list[tuple[int, dict[str, Any]]] = []
    if is_file_query or "*" in clean:
        cursor.execute(
            """
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.kind IN ('owned_path', 'inferred_path');
            """
        )
        for row in cursor.fetchall():
            score = match_path_pattern(clean, row["symbol"])
            if score > 0:
                glob_scored_matches.append(
                    (
                        score,
                        {
                            "symbol": row["symbol"],
                            "entity_id": row["entity_id"],
                            "kind": row["kind"],
                            "type": row["type"],
                            "title": row["title"],
                            "file_path": row["file_path"],
                            "_data_json": row["data_json"],
                        },
                    )
                )

    combined_pattern_matches = pkg_prefix_items + glob_scored_matches
    if combined_pattern_matches:
        max_pat_score = max(s for s, _ in combined_pattern_matches)
        max_dir_score = max((s for s, _ in combined_pattern_matches if 500 <= s < 1000), default=0)
        # Keep only matches at the highest pattern specificity tier when package_path/owned_path compete
        combined_pattern_matches.sort(
            key=lambda entry: (
                -entry[0],
                0 if entry[1]["type"] == "tech/component" else 1,
                -len(str(entry[1]["symbol"])),
                entry[1]["entity_id"],
            )
        )
        filtered_pattern_items = [
            item
            for score, item in combined_pattern_matches
            if (
                score >= max_dir_score
                if 500 <= score < 1000
                else (score >= max_pat_score or item["kind"] != "package_path")
            )
        ]
    else:
        filtered_pattern_items = []

    results: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    for item in exact_or_direct + filtered_pattern_items:
        if item["entity_id"] not in seen_entities:
            seen_entities.add(item["entity_id"])
            raw_json = item.pop("_data_json", None)
            item["data"] = orjson.loads(raw_json) if raw_json else {}
            results.append(item)

    return results


def query_spec_catalog_in_db(
    conn: sqlite3.Connection,
    eidos_dir: Path,
    repo_root: Path | None = None,
) -> tuple[dict[str, tuple[Path, str, dict[str, Any]]], dict[str, tuple[Path, dict[str, Any]]]]:
    """Query the specification catalog (specs_by_id, invariant_map) from indexed entities."""
    from eidos.core.spec.models import FrontmatterModel

    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    invariant_map: dict[str, tuple[Path, dict[str, Any]]] = {}

    rows = conn.execute(
        """
        SELECT id, type, file_path, data_json
        FROM entities
        WHERE type NOT IN ('invariant', 'delta/mutation')
        ORDER BY file_path, id;
        """
    ).fetchall()

    for row in rows:
        doc_id = str(row["id"] or "").strip()
        doc_type = str(row["type"] or "").strip()
        rel_fp = str(row["file_path"] or "").replace("\\", "/").strip().lstrip("./")
        if not doc_id or not rel_fp:
            continue
        try:
            fm = orjson.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(fm, dict):
            continue

        if rel_fp.startswith("eidos/") and eidos_dir.name == "eidos":
            p = eidos_dir / rel_fp[6:]
        elif repo_root is not None and (repo_root / rel_fp).exists():
            p = repo_root / rel_fp
        else:
            p = eidos_dir / rel_fp

        specs_by_id[doc_id] = (p, doc_type, fm)
        model = FrontmatterModel.model_validate(fm)
        for inv in model.all_invariants():
            if inv.id:
                invariant_map[inv.id] = (p, inv.model_dump(exclude_none=True))

    return specs_by_id, invariant_map


def compute_delta_impact_in_db(
    conn: sqlite3.Connection,
    mutations: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Compute SpecDelta (affects, blast_radius) from persisted entities and edges in the knowledge graph."""
    from eidos.core.lifecycle.delta_engine import normalize_delta_mutation

    def _add_unique(lst: list[str], item: str) -> None:
        if item and item not in lst:
            lst.append(item)

    _resolve_ref_id = build_spec_path_resolver(conn)

    comp_to_container: dict[str, str] = {}
    contract_to_container: dict[str, str] = {}
    cnt_dependents: dict[str, list[str]] = {}
    comp_dependents: dict[str, list[str]] = {}
    entity_to_caps: dict[str, list[str]] = {}
    cap_to_domain: dict[str, str] = {}
    cap_containers: dict[str, list[str]] = {}
    cap_components: dict[str, list[str]] = {}
    cap_personas: dict[str, list[str]] = {}
    cap_screens: dict[str, list[str]] = {}
    raw_asserts: dict[str, list[str]] = {}

    cap_ids = {
        str(r[0]) for r in conn.execute("SELECT id FROM entities WHERE type = 'product/capability';").fetchall() if r[0]
    }
    pol_ids = [
        str(r[0])
        for r in conn.execute("SELECT id FROM entities WHERE type = 'product/policy' ORDER BY id;").fetchall()
        if r[0]
    ]
    ds_rows = [
        str(r[0])
        for r in conn.execute("SELECT id FROM entities WHERE type = 'design/system' ORDER BY id;").fetchall()
        if r[0]
    ]
    default_ds_id = ds_rows[0] if ds_rows else "DS-001"
    clt_ids = [
        str(r[0])
        for r in conn.execute("SELECT id FROM entities WHERE type = 'design/client' ORDER BY id;").fetchall()
        if r[0]
    ]

    clt_container: dict[str, str] = {}
    clt_ds: dict[str, str] = {}
    clt_screens: dict[str, list[str]] = {}

    for src_id, dst_id, rel_type in conn.execute("SELECT source_id, target_id, rel_type FROM edges;").fetchall():
        src = str(src_id or "")
        dst = str(dst_id or "")
        if not src or not dst:
            continue
        if rel_type == "part_of":
            comp_to_container[src] = dst
        elif rel_type == "contains_component":
            comp_to_container.setdefault(dst, src)
        elif rel_type == "owned_by" and dst.startswith("CNT-"):
            contract_to_container[src] = dst
        elif rel_type == "depends_on" and src.startswith("CNT-") and dst.startswith("CNT-") and src != dst:
            _add_unique(cnt_dependents.setdefault(dst, []), src)
        elif rel_type == "consumes_api" and src != dst:
            _add_unique(comp_dependents.setdefault(dst, []), src)
        elif rel_type == "implements" and src in cap_ids:
            _add_unique(entity_to_caps.setdefault(dst, []), src)
            if dst.startswith("CNT-"):
                _add_unique(cap_containers.setdefault(src, []), dst)
            elif dst.startswith("CMP-"):
                _add_unique(cap_components.setdefault(src, []), dst)
        elif rel_type == "realizes" and src in cap_ids:
            cap_to_domain[src] = dst
        elif rel_type == "serves_persona" and src in cap_ids:
            _add_unique(cap_personas.setdefault(src, []), dst)
        elif rel_type == "delivered_by" and src in cap_ids and dst.startswith("SCR-"):
            _add_unique(cap_screens.setdefault(src, []), dst)
        elif rel_type == "implemented_by" and src in clt_ids:
            clt_container[src] = dst
        elif rel_type == "styled_by" and src in clt_ids:
            clt_ds[src] = dst
        elif rel_type == "has_screen" and src in clt_ids:
            _add_unique(clt_screens.setdefault(src, []), dst)
        elif rel_type == "asserts" and "::" not in src:
            _add_unique(raw_asserts.setdefault(src, []), dst)

    # Filter out component-scoped invariants that were also rolled up onto parent containers in edges
    entity_invariants: dict[str, list[str]] = {}
    for owner_id, inv_list in raw_asserts.items():
        if owner_id.startswith("CNT-"):
            comp_invs = {
                inv_id
                for cmp_id, cnt_id in comp_to_container.items()
                if cnt_id == owner_id
                for inv_id in raw_asserts.get(cmp_id, [])
            }
            filtered = [inv_id for inv_id in inv_list if inv_id not in comp_invs]
            if filtered:
                entity_invariants[owner_id] = filtered
        else:
            entity_invariants[owner_id] = list(inv_list)

    for pid in pol_ids:
        if pid not in entity_invariants or not entity_invariants[pid]:
            entity_invariants[pid] = [pid]

    inv_to_owners: dict[str, list[str]] = {}
    for owner_id, inv_ids in entity_invariants.items():
        for inv_id in inv_ids:
            if inv_id != owner_id:
                _add_unique(inv_to_owners.setdefault(inv_id, []), owner_id)

    client_by_id: dict[str, dict[str, Any]] = {}
    container_to_clients: dict[str, list[dict[str, Any]]] = {}
    for cl_id in clt_ids:
        cnt_ref = clt_container.get(cl_id, "N/A")
        ds_ref = clt_ds.get(cl_id) or default_ds_id
        cl_obj = {
            "id": cl_id,
            "container": cnt_ref,
            "design_system": ds_ref,
            "screens": clt_screens.get(cl_id, []),
        }
        client_by_id[cl_id] = cl_obj
        if cnt_ref and cnt_ref != "N/A":
            container_to_clients.setdefault(cnt_ref, []).append(cl_obj)

    muts = [normalize_delta_mutation(m) if isinstance(m, dict) else m for m in (mutations or [])]

    aff_lists: dict[str, list[str]] = {
        "containers": [],
        "components": [],
        "contracts": [],
        "capabilities": [],
        "domains": [],
        "policies": [],
        "personas": [],
        "design_systems": [],
        "screens": [],
        "ui_components": [],
        "clients": [],
        "flows": [],
    }
    br_lists: dict[str, list[str]] = {
        "impacted_containers": [],
        "impacted_components": [],
        "impacted_design_systems": [],
        "impacted_screens": [],
        "impacted_personas": [],
        "invariants_to_enforce": [],
    }

    kind_to_cat = {
        "container": "containers",
        "component": "components",
        "contract": "contracts",
        "capability": "capabilities",
        "domain": "domains",
        "policy": "policies",
        "persona": "personas",
        "design_system": "design_systems",
        "screen": "screens",
        "ui_component": "ui_components",
        "client": "clients",
        "flow": "flows",
    }
    seed_entities: list[str] = []
    for m in muts:
        if not isinstance(m, dict):
            continue
        raw_t = m.get("target")
        if not raw_t or not isinstance(raw_t, str) or not raw_t.strip():
            continue
        resolved_id = _resolve_ref_id(raw_t.strip())
        p_meta = resolve_entity_prefix_meta(resolved_id)
        if p_meta is None:
            continue
        entity_kind = p_meta[1]
        if entity_kind == "invariant":
            _add_unique(br_lists["invariants_to_enforce"], resolved_id)
            for owner_id in inv_to_owners.get(resolved_id, []):
                o_meta = resolve_entity_prefix_meta(owner_id)
                o_cat = kind_to_cat.get(o_meta[1]) if o_meta else None
                if o_cat:
                    _add_unique(aff_lists[o_cat], owner_id)
                    _add_unique(seed_entities, owner_id)
                    if o_cat == "components" and owner_id in comp_to_container:
                        _add_unique(aff_lists["containers"], comp_to_container[owner_id])
                    elif o_cat == "capabilities" and owner_id in cap_to_domain:
                        _add_unique(aff_lists["domains"], cap_to_domain[owner_id])
            continue
        cat = kind_to_cat.get(entity_kind)
        if not cat:
            continue
        _add_unique(aff_lists[cat], resolved_id)
        _add_unique(seed_entities, resolved_id)
        if cat == "components" and resolved_id in comp_to_container:
            _add_unique(aff_lists["containers"], comp_to_container[resolved_id])
        elif cat == "contracts" and resolved_id in contract_to_container:
            _add_unique(aff_lists["containers"], contract_to_container[resolved_id])
        elif cat == "capabilities" and resolved_id in cap_to_domain:
            _add_unique(aff_lists["domains"], cap_to_domain[resolved_id])

    for cnt_id in aff_lists["containers"]:
        _add_unique(br_lists["impacted_containers"], cnt_id)
        for dep_cnt in cnt_dependents.get(cnt_id, []):
            _add_unique(br_lists["impacted_containers"], dep_cnt)
        for cl in container_to_clients.get(cnt_id, []):
            if cl.get("design_system"):
                _add_unique(br_lists["impacted_design_systems"], str(cl["design_system"]))
            for scr_id in cl.get("screens", []) or []:
                _add_unique(br_lists["impacted_screens"], _resolve_ref_id(scr_id))

    for cmp_id in aff_lists["components"]:
        _add_unique(br_lists["impacted_components"], cmp_id)
        for dep_cmp in comp_dependents.get(cmp_id, []):
            _add_unique(br_lists["impacted_components"], dep_cmp)
            if dep_cmp in comp_to_container:
                _add_unique(br_lists["impacted_containers"], comp_to_container[dep_cmp])
        if cmp_id in comp_to_container:
            _add_unique(br_lists["impacted_containers"], comp_to_container[cmp_id])

    for ds_id in aff_lists["design_systems"]:
        _add_unique(br_lists["impacted_design_systems"], ds_id)
    for scr_id in aff_lists["screens"]:
        _add_unique(br_lists["impacted_screens"], scr_id)
    for per_id in aff_lists["personas"]:
        _add_unique(br_lists["impacted_personas"], per_id)
    for cl_id in aff_lists["clients"]:
        cl_obj = client_by_id.get(cl_id)
        if cl_obj:
            if cl_obj.get("container") and cl_obj["container"] != "N/A":
                _add_unique(br_lists["impacted_containers"], str(cl_obj["container"]))
            if cl_obj.get("design_system"):
                _add_unique(br_lists["impacted_design_systems"], str(cl_obj["design_system"]))
            for scr_id in cl_obj.get("screens", []) or []:
                _add_unique(br_lists["impacted_screens"], _resolve_ref_id(scr_id))

    related_caps = list(aff_lists["capabilities"])
    for ent_id in seed_entities + aff_lists["containers"] + aff_lists["components"]:
        for cid in entity_to_caps.get(ent_id, []):
            _add_unique(related_caps, cid)

    for cap_id in related_caps:
        if cap_id not in cap_ids:
            continue
        if cap_id in aff_lists["capabilities"]:
            for cnt_id in cap_containers.get(cap_id, []):
                _add_unique(br_lists["impacted_containers"], cnt_id)
            for cmp_id in cap_components.get(cap_id, []):
                _add_unique(br_lists["impacted_components"], cmp_id)
        for per_id in cap_personas.get(cap_id, []):
            _add_unique(br_lists["impacted_personas"], per_id)
        for scr_id in cap_screens.get(cap_id, []):
            _add_unique(br_lists["impacted_screens"], scr_id)

    for seed_id in seed_entities + aff_lists["containers"] + br_lists["impacted_components"]:
        for inv_id in entity_invariants.get(seed_id, []):
            _add_unique(br_lists["invariants_to_enforce"], inv_id)

    derived_affects = {k: v for k, v in aff_lists.items() if v}
    derived_blast_radius = {k: v for k, v in br_lists.items() if v}
    return derived_affects, derived_blast_radius


_PUBLIC_SURFACE_HEADING_RE = re.compile(r"^==+\s+Public\s+Surface\b", re.MULTILINE | re.IGNORECASE)


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator > 0 else 100.0


def _gitignored_paths(repo_root: Path, rel_paths: list[str]) -> set[str]:
    """Return subset of ``rel_paths`` ignored by Git (``.gitignore`` / exclude rules)."""
    from eidos.core.workspace.project import find_git_root

    if not rel_paths or find_git_root(repo_root) is None:
        return set()
    import subprocess

    ignored: set[str] = set()
    try:
        res = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=repo_root,
            input="\0".join(p.replace("\\", "/") for p in rel_paths),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode in (0, 1) and res.stdout:
            for item in res.stdout.split("\0"):
                if item:
                    ignored.add(item.replace("\\", "/"))
    except OSError:
        pass
    return ignored


def compute_governance_health(
    conn: sqlite3.Connection,
    repo_root: Path,
    eidos_dir: Path,
    *,
    validation_report: Any = None,
) -> dict[str, Any]:
    """Compute a deterministic architecture governance health scorecard from the synced knowledge graph."""
    from eidos.core.workspace.project import is_generated_or_vendored

    root_res = repo_root.resolve()
    eidos_res = eidos_dir.resolve()

    if validation_report is None:
        from eidos.core.spec.validator import SpecValidator

        validation_report = SpecValidator().validate_directory(eidos_res)

    # 1. Active entities by type
    ent_rows = conn.execute(
        "SELECT id, type, status, file_path, data_json FROM entities WHERE type != 'invariant';"
    ).fetchall()

    owned_patterns: list[str] = []
    comp_total = 0
    comp_documented = 0
    cnt_total = 0
    cnt_documented = 0
    contracts_total = 0
    contracts_verified = 0
    caps_total = 0
    caps_with_bound = 0
    cap_invs_total = 0
    cap_invs_bound = 0
    ratchet_entries = 0
    ratchet_max_imports = 0

    for r in ent_rows:
        etype = str(r["type"] or "")
        status = str(r["status"] or "active").strip().lower()
        if status in ("deprecated", "retired"):
            continue
        data = orjson.loads(r["data_json"]) if r["data_json"] else {}
        if not isinstance(data, dict):
            data = {}

        if etype in ("tech/container", "tech/component"):
            owned_patterns.extend(extract_entity_paths(data))
            is_component = etype == "tech/component"
            if is_component:
                comp_total += 1
            else:
                cnt_total += 1
            raw_fp = str(r["file_path"] or "").strip()
            spec_p = Path(raw_fp)
            if not spec_p.is_absolute():
                cand = root_res / spec_p
                spec_p = cand if cand.is_file() else (eidos_res / spec_p)
            if spec_p.is_file():
                doc, _, _ = get_cached_spec_document(spec_p)
                body_txt = (doc.body if doc is not None else "") or ""
                if _PUBLIC_SURFACE_HEADING_RE.search(body_txt):
                    if is_component:
                        comp_documented += 1
                    else:
                        cnt_documented += 1

        elif etype == "tech/contract":
            contracts_total += 1
            if extract_entity_paths({"specification_file": data.get("specification_file")}):
                contracts_verified += 1

        elif etype == "product/capability":
            caps_total += 1
            tech = data.get("technical") if isinstance(data.get("technical"), dict) else {}
            invs = tech.get("invariants") if isinstance(tech.get("invariants"), list) else []
            has_bound = False
            for inv in invs:
                if not isinstance(inv, dict):
                    continue
                cap_invs_total += 1
                tb = str(inv.get("test_binding") or "").strip()
                if tb and tb.upper() != "N/A":
                    cap_invs_bound += 1
                    has_bound = True
            if has_bound:
                caps_with_bound += 1

        elif etype == "tech/architecture":
            ib = data.get("import_boundaries") if isinstance(data.get("import_boundaries"), dict) else {}
            ratchet = ib.get("ratchet_baseline") if isinstance(ib.get("ratchet_baseline"), list) else []
            for entry in ratchet:
                if isinstance(entry, dict):
                    ratchet_entries += 1
                    ratchet_max_imports += int(entry.get("max_imports") or 0)

    # 2. Source file ownership across indexed production code files (excluding gitignored & linguist-generated/vendored)
    code_rows = conn.execute(
        "SELECT rel_path FROM indexed_files WHERE doc_type = 'code/source' ORDER BY rel_path ASC;"
    ).fetchall()
    candidate_code_paths = [
        str(cr["rel_path"] or "").strip().replace("\\", "/")
        for cr in code_rows
        if str(cr["rel_path"] or "").strip()
        and Path(str(cr["rel_path"]).strip()).suffix.lower() in _POLYGLOT_TEST_CODE_EXTS
    ]
    excluded_code_paths = _gitignored_paths(root_res, candidate_code_paths) | is_generated_or_vendored(
        root_res, candidate_code_paths
    )
    total_code_files = 0
    owned_code_files = 0
    unowned_files: list[str] = []
    for rel_p in candidate_code_paths:
        if rel_p in excluded_code_paths:
            continue
        total_code_files += 1
        if any(match_path_pattern(rel_p, pat) > 0 for pat in owned_patterns):
            owned_code_files += 1
        else:
            unowned_files.append(rel_p)

    surface_total = comp_total + cnt_total
    surface_documented = comp_documented + cnt_documented
    surface_pct = _pct(comp_documented, comp_total) if comp_total > 0 else _pct(surface_documented, surface_total)

    schema_errs = getattr(validation_report, "schema_errors", None) or {}
    broken_refs = getattr(validation_report, "broken_references", None) or []
    warnings = getattr(validation_report, "warnings", None) or []
    error_count = sum(len(v) for v in schema_errs.values()) + len(broken_refs)

    return {
        "source_ownership": {
            "total_code_files": total_code_files,
            "owned_code_files": owned_code_files,
            "coverage_pct": _pct(owned_code_files, total_code_files),
            "unowned_files": unowned_files,
        },
        "public_surface": {
            "total_components": comp_total,
            "documented_components": comp_documented,
            "total_containers": cnt_total,
            "documented_containers": cnt_documented,
            "total_entities": surface_total,
            "documented_entities": surface_documented,
            "coverage_pct": surface_pct,
        },
        "contracts": {
            "total_active": contracts_total,
            "verified_with_spec_file": contracts_verified,
            "coverage_pct": _pct(contracts_verified, contracts_total),
        },
        "capabilities": {
            "total_active": caps_total,
            "with_bound_invariants": caps_with_bound,
            "total_invariants": cap_invs_total,
            "bound_invariants": cap_invs_bound,
            "coverage_pct": _pct(caps_with_bound, caps_total),
        },
        "validation": {
            "is_clean": bool(getattr(validation_report, "is_clean", False)),
            "error_count": error_count,
            "warning_count": len(warnings),
        },
        "ratchet_debt": {
            "entry_count": ratchet_entries,
            "total_max_imports": ratchet_max_imports,
        },
    }
