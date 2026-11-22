"""Declarative specification edge reconstruction, invariant test-binding indexing, and FTS symbol projection (CMP-INCREMENTAL-SYNC-ENGINE)."""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_indexer import extract_test_file_cache_payload
from eidos.core.index.code_graph import (
    _CODE_EXTS,
    _SOURCE_AST_MEM_CACHE,
    _TEST_BINDING_MEM_CACHE,
    _can_use_ast_mem_cache,
    _derive_ast_import_edges,
    _find_import_line,
    _json_dumps,
    _normalize_rel_path,
    _upsert_file_tokens_fts,
    store_code_symbols,
)
from eidos.core.index.fts_query import compound_identifier_subtokens
from eidos.core.index.storage import build_spec_path_resolver, upsert_edge

_SQLITE_ID_BATCH_SIZE = 400
_SPEC_REF_TOKEN_RE = re.compile(r"\b[A-Z]{2,5}-[A-Z0-9_-]+\b")


def _iter_id_chunks(ids: list[str], chunk_size: int = _SQLITE_ID_BATCH_SIZE) -> list[list[str]]:
    """Split ``ids`` into bounded slices well below SQLite's variable and expression-tree limits."""
    return [ids[i : i + chunk_size] for i in range(0, len(ids), chunk_size)]


def rebuild_spec_edges(conn: sqlite3.Connection, updated_doc_ids: set[str] | None = None) -> None:
    """Rebuild specification-declared relational edges from persisted entity frontmatter.

    When ``updated_doc_ids`` is provided on incremental single-spec edits, only non-test edges touching
    ``updated_doc_ids`` (and their child invariants) are evicted and rebuilt alongside their
    1-hop neighbors and any previously unresolved edge sources.
    """
    _resolve_ref = build_spec_path_resolver(conn)

    clean_ids = sorted(d for d in (updated_doc_ids or ()) if d)
    if updated_doc_ids is not None and clean_ids:
        child_id_set: set[str] = set()
        for chunk in _iter_id_chunks(clean_ids):
            ph = ",".join("?" for _ in chunk)
            for (tid,) in conn.execute(
                f"""
                SELECT target_id FROM edges
                WHERE source_id IN ({ph})
                  AND rel_type IN (
                      'asserts', 'contains_container', 'describes_system',
                      'graduated_from', 'graduated_to', 'implemented_by',
                      'styled_by', 'styles_container', 'extends',
                      'serves_persona', 'delivered_by', 'has_screen', 'has_flow', 'uses_screen'
                  );
                """,
                tuple(chunk),
            ).fetchall():
                if tid:
                    child_id_set.add(tid)
            for (sid,) in conn.execute(
                f"""
                SELECT source_id FROM edges
                WHERE target_id IN ({ph})
                  AND rel_type IN (
                      'contains_container', 'describes_system', 'graduated_from', 'graduated_to',
                      'implemented_by', 'styled_by', 'styles_container', 'extends',
                      'serves_persona', 'delivered_by', 'has_screen', 'has_flow', 'uses_screen'
                  );
                """,
                tuple(chunk),
            ).fetchall():
                if sid:
                    child_id_set.add(sid)
            for (inv_id,) in conn.execute(
                f"""
                SELECT id FROM entities
                WHERE type = 'invariant' AND file_path IN (SELECT file_path FROM entities WHERE id IN ({ph}));
                """,
                tuple(chunk),
            ).fetchall():
                if inv_id:
                    child_id_set.add(inv_id)

        evict_ids = sorted(set(clean_ids) | child_id_set)
        non_inv_entities = {
            r[0]: (r[1] or "", r[2] or "")
            for r in conn.execute("SELECT id, file_path, data_json FROM entities WHERE type != 'invariant';").fetchall()
            if r[0]
        }
        non_inv_id_set = set(non_inv_entities)

        neighbor_candidates: set[str] = set()
        linked_targets: set[str] = set()
        for chunk in _iter_id_chunks(evict_ids):
            evict_ph = ",".join("?" for _ in chunk)
            for src_id, dst_id in conn.execute(
                f"SELECT source_id, target_id FROM edges WHERE target_id IN ({evict_ph});",
                tuple(chunk),
            ).fetchall():
                if src_id:
                    neighbor_candidates.add(src_id)
                if dst_id:
                    linked_targets.add(dst_id)
            for (dst_id,) in conn.execute(
                f"SELECT target_id FROM edges WHERE source_id IN ({evict_ph});",
                tuple(chunk),
            ).fetchall():
                if dst_id:
                    neighbor_candidates.add(dst_id)

        for (dangling_src,) in conn.execute(
            "SELECT DISTINCT source_id FROM edges WHERE target_id NOT IN (SELECT id FROM entities);"
        ).fetchall():
            if dangling_src:
                neighbor_candidates.add(dangling_src)

        scope_set = ((neighbor_candidates | set(evict_ids)) & non_inv_id_set) | set(clean_ids)

        # Include any existing entity whose frontmatter JSON mentions a newly added/unlinked target spec
        # in a single O(N) Python pass (zero SQLite expression depth or parameter limits).
        unlinked_evict = set(evict_ids) - linked_targets
        if unlinked_evict:
            unlinked_basenames = {
                Path(non_inv_entities[eid][0]).name
                for eid in unlinked_evict
                if eid in non_inv_entities and non_inv_entities[eid][0]
            }
            for cand_id, (_cand_fp, cand_json) in non_inv_entities.items():
                if cand_id in scope_set or not cand_json:
                    continue
                if unlinked_evict.intersection(_SPEC_REF_TOKEN_RE.findall(cand_json)) or any(
                    bn in cand_json for bn in unlinked_basenames
                ):
                    scope_set.add(cand_id)

        # Include design/client and tech/architecture entities so indirect design and C4 Level 1 edges stay consistent.
        clt_rows = conn.execute(
            "SELECT id FROM entities WHERE type IN ('design/client', 'tech/architecture');"
        ).fetchall()
        if clt_rows:
            scope_set.update(r[0] for r in clt_rows if r[0])

        for chunk in _iter_id_chunks(evict_ids):
            evict_ph = ",".join("?" for _ in chunk)
            conn.execute(
                f"""
                DELETE FROM edges
                WHERE source_id IN ({evict_ph})
                  AND NOT (rel_type = 'asserts' AND instr(source_id, '::') > 0);
                """,
                tuple(chunk),
            )
            conn.execute(
                f"""
                DELETE FROM edges
                WHERE target_id IN ({evict_ph})
                  AND NOT (rel_type = 'asserts' AND instr(source_id, '::') > 0);
                """,
                tuple(chunk),
            )

        scope_ids = sorted(scope_set)
        rows = []
        for chunk in _iter_id_chunks(scope_ids):
            scope_ph = ",".join("?" for _ in chunk)
            rows.extend(
                conn.execute(
                    f"SELECT id, type, file_path, data_json FROM entities WHERE type != 'invariant' AND id IN ({scope_ph});",
                    tuple(chunk),
                ).fetchall()
            )
        rows.sort(key=lambda r: str(r["id"]))
    else:
        conn.execute(
            """
            DELETE FROM edges
            WHERE NOT (rel_type = 'asserts' AND instr(source_id, '::') > 0);
            """
        )
        rows = conn.execute(
            "SELECT id, type, file_path, data_json FROM entities WHERE type != 'invariant' ORDER BY id;"
        ).fetchall()

    sys_ids = [
        r[0]
        for r in conn.execute("SELECT id FROM entities WHERE type = 'product/system' ORDER BY id;").fetchall()
        if r[0]
    ]
    cnt_ids = [
        r[0]
        for r in conn.execute("SELECT id FROM entities WHERE type = 'tech/container' ORDER BY id;").fetchall()
        if r[0]
    ]
    archived_delta_targets: dict[str, set[str]] = {}

    for row in rows:
        doc_id = row["id"]
        doc_type = row["type"]
        spec_fp = _normalize_rel_path(str(row["file_path"] or doc_id))
        try:
            fm = orjson.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(fm, dict):
            continue

        domain = _resolve_ref(fm.get("domain", ""))
        if domain:
            upsert_edge(conn, doc_id, domain, "realizes", "declared_spec", f"{spec_fp}:domain")

        for dec in fm.get("decisions", []) or []:
            if isinstance(dec, dict) and dec.get("graduated_from"):
                grad_id = _resolve_ref(dec["graduated_from"])
                if grad_id:
                    upsert_edge(conn, doc_id, grad_id, "graduated_from", "declared_spec", f"{spec_fp}:graduated_from")

        if doc_type == "tech/architecture":
            c4_ctx = fm.get("c4_context")
            if isinstance(c4_ctx, dict):
                for actor_ref in c4_ctx.get("actors", []) or []:
                    actor_id = _resolve_ref(actor_ref)
                    if actor_id:
                        upsert_edge(
                            conn, doc_id, actor_id, "serves_persona", "declared_spec", f"{spec_fp}:c4_context.actors"
                        )
            for sys_id in sys_ids:
                upsert_edge(conn, doc_id, sys_id, "describes_system", "declared_spec", f"{spec_fp}:type")
            for cnt_id in cnt_ids:
                upsert_edge(conn, doc_id, cnt_id, "contains_container", "declared_spec", f"{spec_fp}:type")

        if doc_type == "product/exploration":
            exp_dec = fm.get("decision")
            if isinstance(exp_dec, dict):
                for dlt_ref in exp_dec.get("resulting_deltas", []) or []:
                    dlt_id = _resolve_ref(dlt_ref)
                    if dlt_id:
                        upsert_edge(
                            conn,
                            doc_id,
                            dlt_id,
                            "graduated_to",
                            "declared_spec",
                            f"{spec_fp}:decision.resulting_deltas",
                        )

        contracts = fm.get("contracts", {})
        if isinstance(contracts, dict):
            for prov in contracts.get("provides", []) or []:
                prov_id = _resolve_ref(prov)
                if prov_id:
                    upsert_edge(conn, doc_id, prov_id, "provides", "declared_spec", f"{spec_fp}:contracts.provides")
            for req in contracts.get("consumes") or []:
                req_id = _resolve_ref(req)
                if req_id:
                    upsert_edge(conn, doc_id, req_id, "consumes", "declared_spec", f"{spec_fp}:contracts.consumes")

        if doc_type == "tech/contract":
            owning = _resolve_ref(fm.get("container"))
            if owning:
                upsert_edge(conn, owning, doc_id, "provides", "declared_spec", f"{spec_fp}:container")
                upsert_edge(conn, doc_id, owning, "owned_by", "declared_spec", f"{spec_fp}:container")

        if doc_type == "design/client":
            cnt_ref = _resolve_ref(fm.get("container"))
            ds_ref = _resolve_ref(fm.get("design_system"))
            if cnt_ref:
                upsert_edge(conn, doc_id, cnt_ref, "implemented_by", "declared_spec", f"{spec_fp}:container")
            if ds_ref:
                upsert_edge(conn, doc_id, ds_ref, "styled_by", "declared_spec", f"{spec_fp}:design_system")
            for scr_ref in fm.get("screens", []) or []:
                scr_id = _resolve_ref(scr_ref)
                if scr_id:
                    upsert_edge(conn, doc_id, scr_id, "has_screen", "declared_spec", f"{spec_fp}:screens")
            for flw_ref in fm.get("flows", []) or []:
                flw_id = _resolve_ref(flw_ref)
                if flw_id:
                    upsert_edge(conn, doc_id, flw_id, "has_flow", "declared_spec", f"{spec_fp}:flows")

        if doc_type == "design/system":
            for clt_ref in fm.get("clients", []) or []:
                clt_id = _resolve_ref(clt_ref)
                if clt_id:
                    upsert_edge(conn, clt_id, doc_id, "styled_by", "declared_spec", f"{spec_fp}:clients")
            if fm.get("extends"):
                parent_ds = _resolve_ref(fm["extends"])
                if parent_ds:
                    upsert_edge(conn, doc_id, parent_ds, "extends", "declared_spec", f"{spec_fp}:extends")

        if doc_type == "design/ui-component":
            ds_ref = _resolve_ref(fm.get("design_system"))
            if ds_ref:
                upsert_edge(conn, doc_id, ds_ref, "styled_by", "declared_spec", f"{spec_fp}:design_system")

        if doc_type == "design/flow":
            for step in fm.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                step_clt = _resolve_ref(step.get("client"))
                if step_clt:
                    upsert_edge(conn, step_clt, doc_id, "has_flow", "declared_spec", f"{spec_fp}:steps.client")
                step_scr = _resolve_ref(step.get("screen"))
                if step_scr:
                    upsert_edge(conn, doc_id, step_scr, "uses_screen", "declared_spec", f"{spec_fp}:steps.screen")

        biz = fm.get("business", {})
        if isinstance(biz, dict):
            for pol in biz.get("policies", []) or []:
                pol_ref = _resolve_ref(pol)
                if pol_ref:
                    upsert_edge(conn, doc_id, pol_ref, "governed_by", "declared_spec", f"{spec_fp}:business.policies")
            for per in biz.get("personas", []) or []:
                per_ref = _resolve_ref(per)
                if per_ref:
                    upsert_edge(
                        conn, doc_id, per_ref, "serves_persona", "declared_spec", f"{spec_fp}:business.personas"
                    )

        exp = fm.get("experience", {})
        if isinstance(exp, dict):
            for exp_key in ("clients", "design_systems", "screens", "flows", "ui_components"):
                for exp_ref in exp.get(exp_key, []) or []:
                    exp_id = _resolve_ref(exp_ref)
                    if exp_id:
                        upsert_edge(
                            conn, doc_id, exp_id, "delivered_by", "declared_spec", f"{spec_fp}:experience.{exp_key}"
                        )

        tech = fm.get("technical", {})
        invariants = list(fm.get("invariants", []) or [])
        if isinstance(tech, dict):
            raw_ctrs = tech.get("contracts")
            ctr_list = raw_ctrs if isinstance(raw_ctrs, list) else []
            tech_targets = (
                list(tech.get("containers", []) or []) + list(tech.get("components", []) or []) + list(ctr_list)
            )
            for comp_ref in tech_targets:
                target_id = _resolve_ref(comp_ref)
                if target_id:
                    upsert_edge(conn, doc_id, target_id, "implements", "declared_spec", f"{spec_fp}:technical")
            invariants.extend(tech.get("invariants", []) or [])

        if doc_type == "tech/component":
            cnt_id = _resolve_ref(fm.get("container"))
            if cnt_id:
                upsert_edge(conn, cnt_id, doc_id, "contains_component", "declared_spec", f"{spec_fp}:container")
                upsert_edge(conn, doc_id, cnt_id, "part_of", "declared_spec", f"{spec_fp}:container")

        if doc_type == "delta/mutation":
            row_fp = f"/{str(row['file_path'] or '').replace(chr(92), '/').lstrip('/')}"
            is_archived_delta_edge = "/deltas/archive/" in row_fp
            for m in fm.get("mutations", []) or []:
                if not isinstance(m, dict):
                    continue
                v = m.get("target")
                if isinstance(v, str) and v.strip():
                    target_val = _resolve_ref(v)
                    if not is_archived_delta_edge:
                        upsert_edge(conn, doc_id, target_val, "affects", "declared_spec", f"{spec_fp}:mutations")
                    elif not str(target_val).startswith("DLT-"):
                        t_set = archived_delta_targets.setdefault(doc_id, set())
                        t_set.add(target_val)

        for inv in invariants:
            iid = inv.get("id") if isinstance(inv, dict) else None
            if iid:
                upsert_edge(conn, doc_id, iid, "asserts", "declared_spec", f"{spec_fp}:invariants")

    for e_src, e_dst, e_ev in conn.execute(
        """
        SELECT DISTINCT e_exp.source_id, e_aff.target_id, e_exp.evidence
        FROM edges e_exp
        JOIN edges e_aff ON e_aff.source_id = e_exp.target_id AND e_aff.rel_type = 'affects'
        WHERE e_exp.rel_type = 'graduated_to'
          AND e_aff.target_id NOT LIKE 'DLT-%';
        """
    ).fetchall():
        upsert_edge(conn, e_src, e_dst, "graduated_to", "declared_spec", e_ev or "")

    for arch_dlt_id, t_vals in archived_delta_targets.items():
        exp_rows = conn.execute(
            "SELECT DISTINCT source_id, evidence FROM edges WHERE target_id = ? AND rel_type = 'graduated_to';",
            (arch_dlt_id,),
        ).fetchall()
        for er in exp_rows:
            for tv in t_vals:
                upsert_edge(conn, er["source_id"], tv, "graduated_to", "declared_spec", er["evidence"] or "")

    _derive_ast_import_edges(conn)


def index_test_bindings(
    conn: sqlite3.Connection,
    ast_idx: Any,
    test_path: Path,
    rel_test: str,
    sha: str = "",
    raw_bytes: bytes | None = None,
    mtime_ns: int | None = None,
) -> None:
    """Persist 'file::func' -> INV-* test-binding edges and test_binding_cache row for a single test file."""
    prefix_like = rel_test.replace("%", "\\%").replace("_", "\\_") + "::%"
    conn.execute(
        "DELETE FROM edges WHERE rel_type = 'asserts' AND source_id LIKE ? ESCAPE '\\';",
        (prefix_like,),
    )
    conn.execute(
        "DELETE FROM symbol_mappings WHERE symbol = ? AND kind = 'test_binding';",
        (rel_test,),
    )
    if mtime_ns is None:
        with contextlib.suppress(OSError):
            mtime_ns = test_path.stat().st_mtime_ns
        if mtime_ns is None:
            mtime_ns = 0
    if raw_bytes is None:
        with contextlib.suppress(OSError):
            raw_bytes = test_path.read_bytes()
    content = raw_bytes.decode("utf-8", errors="replace") if raw_bytes is not None else ""
    if not sha and raw_bytes is not None:
        sha = hashlib.sha256(raw_bytes).hexdigest()

    use_cache = bool(sha) and _can_use_ast_mem_cache(ast_idx)
    cache_key = (type(ast_idx), str(test_path.resolve()), sha) if use_cache else None
    cached_payload = _TEST_BINDING_MEM_CACHE.get(cache_key) if cache_key is not None else None
    functions: dict[str, list[str]] | None = None
    functions_json = ""
    file_tokens_json = ""
    if cached_payload is not None:
        functions, functions_json, file_tokens_json = cached_payload
    elif use_cache:
        row = conn.execute(
            "SELECT sha256, functions_json, file_tokens_json FROM test_binding_cache WHERE file_path = ?;",
            (rel_test,),
        ).fetchone()
        if row is not None and row[0] == sha:
            with contextlib.suppress(ValueError, TypeError):
                functions = orjson.loads(row[1])
                functions_json = row[1]
                file_tokens_json = row[2]
                if cache_key is not None:
                    _TEST_BINDING_MEM_CACHE[cache_key] = (functions, functions_json, file_tokens_json)
    if functions is None:
        functions, _file_tokens_list, functions_json, file_tokens_json = extract_test_file_cache_payload(
            rel_test, content, ast_idx
        )
        if cache_key is not None:
            _TEST_BINDING_MEM_CACHE[cache_key] = (functions, functions_json, file_tokens_json)

    conn.execute(
        """
        INSERT OR REPLACE INTO test_binding_cache
        (file_path, mtime_ns, sha256, functions_json, file_tokens_json)
        VALUES (?, ?, ?, ?, ?);
        """,
        (rel_test, mtime_ns, sha, functions_json, file_tokens_json),
    )
    if test_path.suffix.lower() in _CODE_EXTS:
        src_cache_key = (type(ast_idx), str(test_path.resolve()), sha) if use_cache else None
        src_cached = _SOURCE_AST_MEM_CACHE.get(src_cache_key) if src_cache_key is not None else None
        if src_cached is not None:
            test_symbols = list(src_cached[0])
            test_imports: list[Any] = list(src_cached[1])
        else:
            test_symbols = (
                list(ast_idx.extract_symbols(test_path, content)) if hasattr(ast_idx, "extract_symbols") else []
            )
            raw_test_imports = (
                ast_idx.extract_imports(test_path, content) if hasattr(ast_idx, "extract_imports") else []
            )
            test_imports = [(imp, _find_import_line(content, imp)) for imp in raw_test_imports]
            if src_cache_key is not None:
                _SOURCE_AST_MEM_CACHE[src_cache_key] = (test_symbols, test_imports)
        known_test_names = {s.name for s in test_symbols} | {
            f"{s.parent_symbol}::{s.name}" for s in test_symbols if s.parent_symbol
        }
        if functions:
            from eidos.core.index.ast_indexer import CodeSymbol

            for fn_key in functions:
                bare_fn = fn_key.split("::")[-1] if "::" in fn_key else fn_key
                if fn_key not in known_test_names and bare_fn not in known_test_names:
                    test_symbols.append(
                        CodeSymbol(
                            name=bare_fn,
                            kind="function",
                            file_path=rel_test,
                            line_start=1,
                            line_end=1,
                        )
                    )
                    known_test_names.add(bare_fn)
        store_code_symbols(conn, rel_test, ["tests"], test_symbols, imports=test_imports, is_test=True)
    _upsert_file_tokens_fts(conn, rel_test, "code/test", content)
    for test_name, fn_tokens in functions.items():
        identifier = f"{rel_test}::{test_name}"
        for inv_id in fn_tokens:
            if not inv_id.startswith("INV-"):
                continue
            upsert_edge(conn, identifier, inv_id, "asserts", "ast_symbol", identifier)
            conn.execute(
                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                (rel_test, inv_id),
            )
            inv_row = conn.execute("SELECT data_json FROM entities WHERE id = ?", (inv_id,)).fetchone()
            if inv_row:
                idata = orjson.loads(inv_row["data_json"])
                idata["has_test_coverage"] = True
                if identifier not in idata.get("matching_tests", []):
                    idata.setdefault("matching_tests", []).append(identifier)
                conn.execute(
                    "UPDATE entities SET data_json = ? WHERE id = ?",
                    (_json_dumps(idata), inv_id),
                )


def prune_stale_test_binding_edges(conn: sqlite3.Connection) -> None:
    """Drop test-binding edges whose invariant entity or test file no longer exists."""
    conn.execute(
        """
        DELETE FROM edges
        WHERE rel_type = 'asserts'
          AND source_id NOT IN (SELECT id FROM entities)
          AND (
            target_id NOT IN (SELECT id FROM entities)
            OR instr(source_id, '::') = 0
            OR substr(source_id, 1, instr(source_id, '::') - 1) NOT IN (
                SELECT rel_path FROM indexed_files WHERE doc_type = 'code/test'
            )
          );
        """
    )
    conn.execute(
        """
        DELETE FROM symbol_mappings
        WHERE kind = 'test_binding'
          AND entity_id NOT IN (SELECT id FROM entities WHERE type = 'invariant');
        """
    )


def delete_spec_fts_rows(conn: sqlite3.Connection, doc_id: str, rel_path: str) -> None:
    """Delete the FTS rows of a specification and of every child entity (e.g. ``INV-*``) declared in its file."""
    conn.execute(
        "DELETE FROM specs_fts WHERE entity_id = ? OR entity_id IN (SELECT id FROM entities WHERE file_path = ?);",
        (doc_id, rel_path),
    )


def _reconcile_invariant_coverage(
    index: Any,
    conn: sqlite3.Connection,
    ast_idx: Any,
) -> None:
    """Reconcile invariant entity has_test_coverage and matching_tests against AST-verified test bindings."""
    inv_rows = conn.execute("SELECT id, data_json FROM entities WHERE type = 'invariant';").fetchall()
    if not inv_rows:
        return

    assert_edges: dict[str, list[str]] = {}
    for source_id, target_id in conn.execute(
        "SELECT source_id, target_id FROM edges WHERE rel_type = 'asserts' AND instr(source_id, '::') > 0;"
    ).fetchall():
        assert_edges.setdefault(target_id, []).append(source_id)

    test_cache_by_file: dict[str, tuple[dict[str, list[str]], set[str]]] | None = None

    def _get_cached_test_tokens(tb_file: str, abs_tb: Path | None) -> tuple[dict[str, list[str]], set[str]]:
        nonlocal test_cache_by_file
        if test_cache_by_file is None:
            test_cache_by_file = {}
            for c_fp, c_fjson, c_ftjson in conn.execute(
                "SELECT file_path, functions_json, file_tokens_json FROM test_binding_cache;"
            ).fetchall():
                with contextlib.suppress(ValueError, TypeError):
                    test_cache_by_file[c_fp] = (
                        orjson.loads(c_fjson),
                        set(orjson.loads(c_ftjson)),
                    )
        if tb_file not in test_cache_by_file and abs_tb is not None and abs_tb.is_file():
            index_test_bindings(conn, ast_idx, abs_tb, tb_file)
            c_row = conn.execute(
                "SELECT functions_json, file_tokens_json FROM test_binding_cache WHERE file_path = ?;",
                (tb_file,),
            ).fetchone()
            if c_row is not None:
                with contextlib.suppress(ValueError, TypeError):
                    test_cache_by_file[tb_file] = (
                        orjson.loads(c_row[0]),
                        set(orjson.loads(c_row[1])),
                    )
        return test_cache_by_file.get(tb_file, ({}, set()))

    for row in inv_rows:
        iid = row["id"]
        try:
            inv_data = orjson.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(inv_data, dict):
            continue

        ast_matches = list(dict.fromkeys(assert_edges.get(iid, [])))
        test = str(inv_data.get("test_binding") or "").strip()

        if test:
            parts = [p.strip() for p in test.split("::") if p.strip()]
            tb_file = parts[0].replace("\\", "/") if parts else ""
            tb_func = "::".join(parts[1:]) if len(parts) > 1 else ""
            abs_tb = (index.root / tb_file) if tb_file else None
            tb_verified = False

            if tb_file:
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                    (tb_file, iid),
                )

            if tb_func:
                target_ref = f"{tb_file}::{tb_func}"
                if target_ref in ast_matches:
                    tb_verified = True
                elif abs_tb is not None and abs_tb.is_file():
                    funcs, _ = _get_cached_test_tokens(tb_file, abs_tb)
                    candidate_token_lists: list[list[str]] = []
                    if tb_func in funcs:
                        candidate_token_lists.append(funcs[tb_func])
                    elif "::" not in tb_func:
                        suffix = f"::{parts[-1]}"
                        candidate_token_lists.extend(
                            toks for k, toks in funcs.items() if k == parts[-1] or k.endswith(suffix)
                        )
                    tb_verified = any(iid in toks for toks in candidate_token_lists)
                    if tb_verified:
                        upsert_edge(conn, target_ref, iid, "asserts", "ast_symbol", target_ref)
                matching = list(dict.fromkeys([target_ref, *ast_matches])) if tb_verified else []
                has_coverage = tb_verified
            else:
                if any(m.startswith(f"{tb_file}::") for m in ast_matches):
                    tb_verified = True
                elif abs_tb is not None and abs_tb.is_file():
                    _, file_tokens = _get_cached_test_tokens(tb_file, abs_tb)
                    tb_verified = iid in file_tokens
                matching = list(dict.fromkeys([tb_file, *ast_matches])) if tb_verified else []
                has_coverage = tb_verified
        else:
            matching = ast_matches
            has_coverage = bool(matching)

        if inv_data.get("has_test_coverage") != has_coverage or inv_data.get("matching_tests") != matching:
            inv_data["has_test_coverage"] = has_coverage
            inv_data["matching_tests"] = matching
            conn.execute(
                "UPDATE entities SET data_json = ? WHERE id = ?;",
                (_json_dumps(inv_data), iid),
            )


def _project_ast_code_symbols_into_fts(
    conn: sqlite3.Connection,
    target_entity_ids: set[str] | None = None,
    fresh_base_symbols: dict[str, str] | None = None,
) -> None:
    """Deterministically project primary AST code_symbols (class, function, method, interface, struct, enum) into specs_fts.symbols."""
    # compound_identifier_subtokens imported at top-level from fts_query

    if target_entity_ids is not None and not target_entity_ids:
        return

    local_base_symbols = fresh_base_symbols or {}
    by_entity: dict[str, list[str]] = {}
    if target_entity_ids is not None:
        clean_targets = sorted(t for t in target_entity_ids if t)
        if not clean_targets:
            return
        spec_rows = []
        for chunk in _iter_id_chunks(clean_targets):
            ph = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"""
                SELECT DISTINCT entity_id, symbol FROM code_symbols
                WHERE is_primary = 1
                  AND is_test = 0
                  AND kind IN ('class', 'function', 'method', 'interface', 'struct', 'enum')
                  AND entity_id IN ({ph})
                ORDER BY entity_id, symbol;
                """,
                tuple(chunk),
            ).fetchall():
                by_entity.setdefault(row[0], []).append(row[1])
            spec_rows.extend(
                conn.execute(
                    f"""
                    SELECT f.rowid, f.entity_id, f.title, f.jtbd_or_rule, f.body_text, f.symbols, e.type, e.data_json
                    FROM specs_fts f
                    JOIN entities e ON e.id = f.entity_id
                    WHERE e.type != 'invariant'
                      AND f.entity_id IN ({ph})
                      AND (e.type IN ('tech/container', 'tech/component') OR f.entity_id IN (SELECT DISTINCT entity_id FROM code_symbols WHERE is_test = 0));
                    """,
                    tuple(chunk),
                ).fetchall()
            )
    else:
        for row in conn.execute(
            """
            SELECT DISTINCT entity_id, symbol FROM code_symbols
            WHERE is_primary = 1
              AND is_test = 0
              AND kind IN ('class', 'function', 'method', 'interface', 'struct', 'enum')
            ORDER BY entity_id, symbol;
            """
        ).fetchall():
            by_entity.setdefault(row[0], []).append(row[1])
        spec_rows = conn.execute(
            """
            SELECT f.rowid, f.entity_id, f.title, f.jtbd_or_rule, f.body_text, f.symbols, e.type, e.data_json
            FROM specs_fts f
            JOIN entities e ON e.id = f.entity_id
            WHERE e.type != 'invariant'
              AND (e.type IN ('tech/container', 'tech/component') OR f.entity_id IN (SELECT DISTINCT entity_id FROM code_symbols WHERE is_test = 0));
            """
        ).fetchall()

    for srow in spec_rows:
        rowid = srow[0]
        entity_id = srow[1]
        cur_symbols = str(srow[5] or "")
        if entity_id in local_base_symbols:
            base_symbols = local_base_symbols[entity_id]
        else:
            title = str(srow[2] or "")
            jtbd_or_rule = str(srow[3] or "")
            body_clean = str(srow[4] or "")
            decompounded = compound_identifier_subtokens(f"{title} {jtbd_or_rule} {body_clean}")
            base_symbols = decompounded.strip() if decompounded else ""

        existing_tokens = set(base_symbols.split())
        new_parts: list[str] = []
        for sym in by_entity.get(entity_id, ()):
            if sym and sym not in existing_tokens:
                new_parts.append(sym)
                existing_tokens.add(sym)
            sub = compound_identifier_subtokens(sym)
            if sub:
                for st in sub.split():
                    if st not in existing_tokens:
                        new_parts.append(st)
                        existing_tokens.add(st)

        recomputed = f"{base_symbols} {' '.join(new_parts)}".strip()
        if recomputed != cur_symbols:
            conn.execute("UPDATE specs_fts SET symbols = ? WHERE rowid = ?;", (recomputed, rowid))
