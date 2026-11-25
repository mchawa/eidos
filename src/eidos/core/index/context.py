"""Tiered Context Compressor graph assembly and public facade (CMP-TIERED-CONTEXT-COMPRESSOR)."""

from __future__ import annotations

import posixpath
import time
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.code_graph import (
    _normalize_rel_path,
    build_module_to_file_map,
    discover_go_module_roots,
    discover_rust_crate_map,
    resolve_import_target_files,
)
from eidos.core.index.context_globs import (
    _expand_context_file_pattern,
)
from eidos.core.index.context_globs import (
    _has_glob_magic as _has_glob_magic,
)
from eidos.core.index.context_globs import (
    expand_brace_globs as expand_brace_globs,
)
from eidos.core.index.context_globs import (
    split_brace_aware_csv as split_brace_aware_csv,
)
from eidos.core.index.context_memories import (
    DEFAULT_MEMORY_COMMIT_DRIFT_THRESHOLD as DEFAULT_MEMORY_COMMIT_DRIFT_THRESHOLD,
)
from eidos.core.index.context_memories import (
    attach_context_memories as attach_context_memories,
)
from eidos.core.index.context_memories import (
    evaluate_memory_freshness as evaluate_memory_freshness,
)
from eidos.core.index.context_memories import (
    extract_memory_sections as extract_memory_sections,
)
from eidos.core.index.context_memories import (
    resolve_memory_freshness_batch as resolve_memory_freshness_batch,
)
from eidos.core.index.context_models import (
    STOP_WORDS as STOP_WORDS,
)
from eidos.core.index.context_models import (
    TIER_TOKEN_BUDGETS as TIER_TOKEN_BUDGETS,
)
from eidos.core.index.context_models import (
    ArchitecturalContextSlice as ArchitecturalContextSlice,
)
from eidos.core.index.context_models import (
    TieredContextSlice as TieredContextSlice,
)
from eidos.core.index.context_models import (
    _tokenize_text,
)
from eidos.core.index.context_models import (
    collect_component_decisions as collect_component_decisions,
)
from eidos.core.index.context_models import (
    format_decisions_markdown as format_decisions_markdown,
)
from eidos.core.index.fts_query import (
    PRIMARY_TASK_SLICE_ENTITY_TYPES,
    SECONDARY_TASK_SLICE_ENTITY_TYPES,
    TASK_SLICE_ENTITY_TYPES,
    execute_fts_query,
)
from eidos.core.index.storage import CONFIDENCE_RANK, EDGE_PROVENANCE_PRECEDENCE, provenance_to_confidence
from eidos.core.index.traversal import resolve_symbol_or_path_in_db


def get_context_slice(
    files: list[str] | str | None = None,
    component: str | None = None,
    task: str | None = None,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
    tier: str = "guardrails",
) -> ArchitecturalContextSlice:
    """Extract an architectural subgraph context slice tailored for an AI agent's current task."""
    from eidos.core.index.graph_index import get_architectural_index

    root = (repo_root or Path.cwd()).resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    index = get_architectural_index(eidos_dir=target_eidos, repo_root=root)
    return index.get_tiered_context_slice(
        files=files,
        component=component,
        task=task,
        tier=tier,
    )


def build_tiered_context_slice(
    index: Any,
    files: list[str] | str | None = None,
    component: str | None = None,
    task: str | None = None,
    tier: str = "guardrails",
    use_semantic: bool = True,
) -> TieredContextSlice:
    """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
    t0 = time.perf_counter()
    index._ensure_synced()

    matched_components: dict[str, dict[str, Any]] = {}
    matched_contracts: dict[str, dict[str, Any]] = {}
    matched_capabilities: dict[str, dict[str, Any]] = {}
    matched_invariants: dict[str, dict[str, Any]] = {}
    matched_policies: dict[str, dict[str, Any]] = {}
    matched_reasons: dict[str, list[str]] = {}
    provenance_tags: dict[str, dict[str, str]] = {}
    direct_inv_ids: list[str] = []
    direct_cnt_ids: list[str] = []
    expanded_cnt_ids: list[str] = []
    direct_cap_ids: list[str] = []
    expanded_cap_ids: list[str] = []
    direct_anchor_ids: set[str] = set()

    def _add_reason(eid: str, reason: str) -> None:
        bucket = matched_reasons.setdefault(eid, [])
        if reason not in bucket and len(bucket) < 3:
            bucket.append(reason)

    def _record_prov(eid: str, provenance: str, evidence: str = "") -> None:
        if not eid:
            return
        conf = provenance_to_confidence(provenance)
        new_score = (CONFIDENCE_RANK.get(conf, 0), EDGE_PROVENANCE_PRECEDENCE.get(provenance, 0))
        cur = provenance_tags.get(eid)
        if cur is not None:
            cur_score = (
                CONFIDENCE_RANK.get(cur.get("confidence", ""), 0),
                EDGE_PROVENANCE_PRECEDENCE.get(cur.get("provenance", ""), 0),
            )
            if new_score < cur_score:
                return
            if new_score == cur_score and cur.get("evidence"):
                return
        entry = {"provenance": provenance, "confidence": conf}
        if evidence:
            entry["evidence"] = evidence
        provenance_tags[eid] = entry

    conn = index._get_connection()
    cursor = conn.cursor()

    def _expand_cap_to_components(cap_ids: list[str], member_reason: str, member_ids: list[str] | None = None) -> None:
        if not cap_ids:
            return
        ph_sc = ",".join("?" for _ in cap_ids)
        cap_comp_rows = cursor.execute(
            f"""
            SELECT DISTINCT e.id, e.title, e.type, e.file_path, e.data_json
            FROM edges ed
            JOIN entities e ON e.id = ed.target_id
            WHERE ed.source_id IN ({ph_sc}) AND ed.rel_type = 'implements'
              AND e.type IN ('tech/container', 'tech/component')
            ORDER BY CASE WHEN e.type = 'tech/container' THEN 0 ELSE 1 END, e.id
            LIMIT 4;
            """,
            tuple(cap_ids),
        ).fetchall()
        for row in cap_comp_rows:
            m_data = orjson.loads(row["data_json"])
            m_data["id"] = row["id"]
            m_data["title"] = row["title"]
            m_data.setdefault("file_path", row["file_path"])
            matched_components.setdefault(row["id"], m_data)
            if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                expanded_cnt_ids.append(row["id"])
            if member_ids is not None and row["id"] not in member_ids:
                member_ids.append(row["id"])
            _add_reason(row["id"], member_reason)
            _record_prov(row["id"], "declared_spec", row["file_path"] or "")

    def _ingest_entity(
        eid: str,
        etype: str,
        data: dict[str, Any],
        reason: str,
        hit_ranks: dict[str, int] | None = None,
        *,
        expand_cap_components: bool = False,
    ) -> None:
        ranks = hit_ranks or {}
        if eid:
            direct_anchor_ids.add(eid)
        if etype in ("tech/container", "tech/component"):
            matched_components[eid] = data
            if eid not in direct_cnt_ids:
                direct_cnt_ids.append(eid)
            _add_reason(eid, reason)
        elif etype == "tech/contract":
            matched_contracts[eid] = data
            _add_reason(eid, reason)
        elif etype == "product/capability":
            matched_capabilities[eid] = data
            if eid not in direct_cap_ids:
                direct_cap_ids.append(eid)
            _add_reason(eid, reason)
            if expand_cap_components:
                _expand_cap_to_components([eid], f"Implemented by matched capability {eid}")
        elif etype == "product/policy":
            matched_policies[eid] = data
            _add_reason(eid, reason)
        elif etype == "invariant":
            matched_invariants[eid] = data
            if eid not in direct_inv_ids:
                direct_inv_ids.append(eid)
            _add_reason(eid, reason)
        elif etype in ("product/domain", "design/system"):
            member_reason = (
                f"Member of FTS-matched domain {eid}"
                if etype == "product/domain"
                else f"Styled by FTS-matched design system {eid}"
            )
            member_sql = (
                """
                SELECT e.id, e.title, e.type, e.file_path, e.data_json FROM edges ed
                JOIN entities e ON e.id = ed.source_id
                WHERE ed.target_id = ? AND ed.rel_type = 'realizes'
                ORDER BY e.id;
                """
                if etype == "product/domain"
                else """
                SELECT e.id, e.title, e.type, e.file_path, e.data_json FROM edges ed
                JOIN entities e ON e.id = ed.target_id
                WHERE ed.source_id = ? AND ed.rel_type = 'styles_container'
                ORDER BY e.id;
                """
            )
            member_rows = sorted(
                cursor.execute(member_sql, (eid,)).fetchall(),
                key=lambda r: (0 if r["id"] in ranks else 1, ranks.get(r["id"], 999), r["id"]),
            )
            member_ids: list[str] = []
            for row in member_rows:
                m_data = orjson.loads(row["data_json"])
                m_data["id"] = row["id"]
                m_data["title"] = row["title"]
                m_data.setdefault("file_path", row["file_path"])
                if row["type"] in ("tech/container", "tech/component"):
                    matched_components.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                        expanded_cnt_ids.append(row["id"])
                elif row["type"] == "product/capability":
                    matched_capabilities.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cap_ids and row["id"] not in expanded_cap_ids:
                        expanded_cap_ids.append(row["id"])
                else:
                    continue
                member_ids.append(row["id"])
                _add_reason(row["id"], member_reason)
            expanded = f"; expanded to {', '.join(member_ids)}" if member_ids else ""
            _add_reason(eid, f"{reason}{expanded}")
        elif etype in SECONDARY_TASK_SLICE_ENTITY_TYPES:
            member_reason = (
                f"Serves FTS-matched persona {eid}"
                if etype == "product/persona"
                else f"Delivers FTS-matched design entity {eid}"
            )
            sec_rows = sorted(
                cursor.execute(
                    """
                    SELECT DISTINCT e.id, e.title, e.type, e.file_path, e.data_json
                    FROM entities e
                    WHERE e.type IN ('product/capability', 'tech/container', 'tech/component', 'invariant')
                      AND (
                          e.id IN (
                              SELECT source_id FROM edges
                              WHERE target_id = ? AND rel_type IN ('serves_persona', 'delivered_by', 'styled_by')
                          )
                          OR e.id IN (
                              SELECT target_id FROM edges
                              WHERE source_id = ? AND rel_type IN ('implemented_by', 'styles_container', 'asserts')
                          )
                          OR e.id IN (
                              SELECT ed2.target_id FROM edges ed1
                              JOIN edges ed2 ON ed2.source_id = ed1.source_id
                              WHERE ed1.target_id = ? AND ed1.rel_type IN ('has_screen', 'has_flow')
                                AND ed2.rel_type = 'implemented_by'
                          )
                          OR e.id IN (
                              SELECT ed2.source_id FROM edges ed1
                              JOIN edges ed2 ON ed2.target_id = ed1.source_id
                              WHERE ed1.target_id = ? AND ed1.rel_type IN ('has_screen', 'has_flow')
                                AND ed2.rel_type = 'delivered_by'
                          )
                          OR e.id IN (
                              SELECT ed2.source_id FROM edges ed1
                              JOIN edges ed2 ON ed2.target_id = ed1.target_id
                              WHERE ed1.source_id = ? AND ed1.rel_type IN ('uses_screen', 'styled_by')
                                AND ed2.rel_type = 'delivered_by'
                          )
                          OR e.id IN (
                              SELECT ed2.target_id FROM edges ed1
                              JOIN edges ed2 ON ed2.source_id = ed1.target_id
                              WHERE ed1.source_id = ? AND ed1.rel_type = 'styled_by'
                                AND ed2.rel_type = 'styles_container'
                          )
                      )
                    ORDER BY e.id;
                    """,
                    (eid, eid, eid, eid, eid, eid),
                ).fetchall(),
                key=lambda r: (0 if r["id"] in ranks else 1, ranks.get(r["id"], 999), r["id"]),
            )
            sec_cap_ids: list[str] = []
            member_ids = []
            for row in sec_rows:
                m_data = orjson.loads(row["data_json"])
                m_data["id"] = row["id"]
                m_data["title"] = row["title"]
                if row["file_path"]:
                    m_data.setdefault("file_path", row["file_path"])
                if row["type"] in ("tech/container", "tech/component"):
                    matched_components.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                        expanded_cnt_ids.append(row["id"])
                elif row["type"] == "product/capability":
                    matched_capabilities.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cap_ids and row["id"] not in expanded_cap_ids:
                        expanded_cap_ids.append(row["id"])
                    sec_cap_ids.append(row["id"])
                elif row["type"] == "invariant":
                    matched_invariants.setdefault(row["id"], m_data)
                else:
                    continue
                member_ids.append(row["id"])
                _add_reason(row["id"], member_reason)

            if sec_cap_ids and not any(r["type"] in ("tech/container", "tech/component") for r in sec_rows):
                _expand_cap_to_components(sec_cap_ids, member_reason, member_ids)
            expanded = f"; expanded to {', '.join(member_ids)}" if member_ids else ""
            _add_reason(eid, f"{reason}{expanded}")

    # 1. Resolve by Component / Container / Entity parameter
    if component:
        c_clean = component.strip().upper()
        comp_rows = cursor.execute(
            """
            SELECT id, title, type, domain, file_path, data_json
            FROM entities
            WHERE type IN ('tech/container', 'tech/component') AND (id = ? OR id LIKE '%' || ? || '%');
            """,
            (c_clean, c_clean),
        ).fetchall()
        if not comp_rows:
            ph_types = ",".join("?" for _ in TASK_SLICE_ENTITY_TYPES)
            comp_rows = cursor.execute(
                f"""
                SELECT id, title, type, domain, file_path, data_json
                FROM entities
                WHERE type IN ({ph_types}) AND (id = ? OR id LIKE '%' || ? || '%');
                """,
                (*TASK_SLICE_ENTITY_TYPES, c_clean, c_clean),
            ).fetchall()
        for row in comp_rows:
            cid = row["id"]
            data = orjson.loads(row["data_json"])
            data["id"] = cid
            data["title"] = row["title"]
            data.setdefault("file_path", row["file_path"])
            _ingest_entity(
                cid,
                row["type"],
                data,
                f"Explicit component parameter '{component}'",
                expand_cap_components=True,
            )
            _record_prov(cid, "declared_spec", str(row["file_path"] or cid))

    # 2. Resolve by File paths
    raw_files = [files] if isinstance(files, str) else (files or [])
    requested_files: list[str] = []
    clean_files: list[str] = []
    seen_clean_files: set[str] = set()
    for f in raw_files:
        if not f:
            continue
        for sub in split_brace_aware_csv(str(f)):
            sub_clean = sub.strip()
            if not sub_clean:
                continue
            if _has_glob_magic(sub_clean):
                norm_pat = sub_clean.replace("\\", "/")
                while norm_pat.startswith("./"):
                    norm_pat = norm_pat[2:]
                if norm_pat not in requested_files:
                    requested_files.append(norm_pat)
                for expanded in _expand_context_file_pattern(index.root, conn, norm_pat):
                    if expanded not in seen_clean_files:
                        seen_clean_files.add(expanded)
                        clean_files.append(expanded)
                continue
            p = Path(sub_clean)
            escaped_root = False
            if p.is_absolute():
                try:
                    norm_f = str(p.resolve().relative_to(index.root.resolve())).replace("\\", "/")
                except ValueError:
                    norm_f = sub_clean.replace("\\", "/")
                    escaped_root = True
            else:
                norm_f = sub_clean.replace("\\", "/")
                while norm_f.startswith("./"):
                    norm_f = norm_f[2:]
                if ".." in norm_f.split("/"):
                    try:
                        norm_f = str((index.root / norm_f).resolve().relative_to(index.root.resolve())).replace(
                            "\\", "/"
                        )
                    except ValueError:
                        escaped_root = True
            if norm_f not in requested_files:
                requested_files.append(norm_f)
            if not escaped_root and norm_f not in seen_clean_files:
                seen_clean_files.add(norm_f)
                clean_files.append(norm_f)

    for fpath in clean_files:
        matches = resolve_symbol_or_path_in_db(conn, fpath)
        primary_file_matches = [
            m for m in matches if m["type"] in ("tech/container", "tech/component", "invariant", "tech/contract")
        ]
        if primary_file_matches:
            for m in primary_file_matches:
                eid = m["entity_id"]
                etype = m["type"]
                data = m["data"]
                data["id"] = eid
                data["title"] = m["title"]
                if m.get("file_path"):
                    data.setdefault("file_path", m["file_path"])
                if etype in ("tech/container", "tech/component"):
                    file_reason = f"Source file match on '{fpath}'"
                elif etype == "invariant":
                    file_reason = f"Test binding match on '{fpath}'"
                else:
                    file_reason = f"Contract file match on '{fpath}'"
                _ingest_entity(eid, etype, data, file_reason)
                mk = str(m.get("kind") or "")
                m_prov = (
                    "inferred_ownership"
                    if mk == "inferred_path"
                    else ("ast_symbol" if mk == "test_binding" else "declared_spec")
                )
                _record_prov(eid, m_prov, fpath)
            continue

        if not matches:
            spec_rows = list(
                cursor.execute(
                    "SELECT id, title, type, file_path, data_json FROM entities WHERE file_path = ? AND type != 'invariant';",
                    (fpath,),
                ).fetchall()
            )
            if not spec_rows and fpath.endswith(".json"):
                for ds_row in cursor.execute(
                    "SELECT id, title, type, file_path, data_json FROM entities WHERE type = 'design/system';"
                ).fetchall():
                    ds_data = orjson.loads(ds_row["data_json"])
                    tokens = ds_data.get("tokens") if isinstance(ds_data.get("tokens"), dict) else {}
                    tok = tokens.get("manifest")
                    if isinstance(tok, str) and tok.strip():
                        ds_fp = str(ds_row["file_path"] or "").replace("\\", "/")
                        tok_path = tok.strip() if "/" in tok.strip() else f"{ds_fp.rsplit('/', 1)[0]}/{tok.strip()}"
                        if tok_path.replace("\\", "/") == fpath:
                            spec_rows.append(ds_row)
            matches = [
                {
                    "entity_id": r["id"],
                    "title": r["title"],
                    "type": r["type"],
                    "file_path": r["file_path"],
                    "data": orjson.loads(r["data_json"]),
                }
                for r in spec_rows
            ]
        for m in matches:
            eid = m["entity_id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]
            if m.get("file_path"):
                data.setdefault("file_path", m["file_path"])
            _ingest_entity(eid, etype, data, f"Specification file match on '{fpath}'", expand_cap_components=True)
            _record_prov(eid, "declared_spec", fpath)

    # 3. Resolve by Task prompt (FTS5 lexical + optional Harrier Hybrid RRF search restricted to sliceable entity types,
    #    retaining up to 8 primary structural matches and up to 4 secondary persona/design matches so secondary entities
    #    never displace primary C4/capability/invariant candidates)
    if task and task.strip():
        raw_matches = execute_fts_query(
            conn,
            text=task,
            limit=12,
            include_types=TASK_SLICE_ENTITY_TYPES,
            use_semantic=use_semantic,
            eidos_dir=getattr(index, "eidos_dir", None),
        )
        primary_hits = [m for m in raw_matches if m["type"] in PRIMARY_TASK_SLICE_ENTITY_TYPES][:8]
        if len(primary_hits) < 8 and len(raw_matches) >= 12:
            seen_primary = {m["id"] for m in primary_hits}
            for m in execute_fts_query(
                conn,
                text=task,
                limit=8,
                include_types=PRIMARY_TASK_SLICE_ENTITY_TYPES,
                use_semantic=use_semantic,
                eidos_dir=getattr(index, "eidos_dir", None),
            ):
                if m["id"] not in seen_primary:
                    primary_hits.append(m)
                    seen_primary.add(m["id"])
                    if len(primary_hits) >= 8:
                        break
        secondary_hits = [
            m
            for idx, m in enumerate(raw_matches)
            if (idx < 8 or len(primary_hits) < 8) and m["type"] in SECONDARY_TASK_SLICE_ENTITY_TYPES
        ][:4]
        fts_matches = primary_hits + secondary_hits
        fts_hit_ranks = {m["id"]: idx for idx, m in enumerate(fts_matches)}
        for m in fts_matches:
            eid = m["id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]
            if m.get("file_path"):
                data.setdefault("file_path", m["file_path"])

            match_source = m.get("match_source")
            if match_source == "hybrid":
                lex_rank = int(m.get("bm25_rank") or m.get("lexical_rank") or 0)
                reason = (
                    f"Hybrid task match (RRF: {float(m.get('rrf_score') or 0.0):.3f}, "
                    f"BM25 #{lex_rank}, cos: {float(m.get('semantic_score') or 0.0):.2f})"
                )
            elif match_source == "semantic":
                reason = (
                    f"Semantic task match (RRF: {float(m.get('rrf_score') or 0.0):.3f}, "
                    f"cos: {float(m.get('semantic_score') or 0.0):.2f})"
                )
            else:
                reason = f"FTS lexical match on task (BM25 score: {m['rank']:.2f})"

            _ingest_entity(eid, etype, data, reason, fts_hit_ranks)
            _record_prov(eid, str(m.get("provenance") or "fts_bm25"), f"task:{task.strip()[:40]}")

    for iid in direct_inv_ids:
        inv_data = matched_invariants[iid]
        parent_cap = inv_data.get("capability_id")
        if parent_cap:
            if parent_cap not in matched_capabilities:
                row = cursor.execute(
                    "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                    (parent_cap,),
                ).fetchone()
                if row:
                    c_data = orjson.loads(row["data_json"])
                    c_data["id"] = row["id"]
                    c_data["title"] = row["title"]
                    c_data.setdefault("file_path", row["file_path"])
                    matched_capabilities[row["id"]] = c_data
            if parent_cap in matched_capabilities:
                if parent_cap not in direct_cap_ids:
                    direct_cap_ids.append(parent_cap)
                _add_reason(parent_cap, f"Owning capability of matched invariant {iid}")
        parent_pol = inv_data.get("policy_id")
        if parent_pol and parent_pol not in matched_policies:
            row = cursor.execute(
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_pol,),
            ).fetchone()
            if row:
                p_data = orjson.loads(row["data_json"])
                p_data["id"] = row["id"]
                p_data["title"] = row["title"]
                p_data.setdefault("file_path", row["file_path"])
                matched_policies[row["id"]] = p_data
                _add_reason(row["id"], f"Owning policy of matched invariant {iid}")
        for parent_comp_key in ("component_id", "container_id"):
            parent_comp = inv_data.get(parent_comp_key)
            if parent_comp and parent_comp not in matched_components:
                row = cursor.execute(
                    "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                    (parent_comp,),
                ).fetchone()
                if row:
                    comp_data = orjson.loads(row["data_json"])
                    comp_data["id"] = row["id"]
                    comp_data["title"] = row["title"]
                    comp_data.setdefault("file_path", row["file_path"])
                    matched_components[row["id"]] = comp_data
                    if row["id"] not in direct_cnt_ids:
                        direct_cnt_ids.append(row["id"])
                    _add_reason(row["id"], f"Owning component/container of matched invariant {iid}")
        parent_uic = inv_data.get("design_component_id")
        if parent_uic:
            row = cursor.execute(
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_uic,),
            ).fetchone()
            if row:
                u_data = orjson.loads(row["data_json"])
                u_data["id"] = row["id"]
                u_data["title"] = row["title"]
                u_data.setdefault("file_path", row["file_path"])
                _ingest_entity(row["id"], row["type"], u_data, f"Owning UI component of matched invariant {iid}")

    has_explicit_query = bool(
        requested_files or clean_files or (component and component.strip()) or (task and task.strip())
    )
    any_matched = bool(
        matched_components or matched_capabilities or matched_contracts or matched_invariants or matched_policies
    )

    # 4. Full catalog overview ONLY when no query filters were provided at all.
    if not any_matched and not has_explicit_query:
        cursor.execute(
            "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE type IN ('tech/container', 'tech/component');"
        )
        for row in cursor.fetchall():
            c_data = orjson.loads(row["data_json"])
            c_data["id"] = row["id"]
            c_data["title"] = row["title"]
            c_data.setdefault("file_path", row["file_path"])
            matched_components[row["id"]] = c_data

    # 4b. Ensure every matched standalone tech/component includes its owning parent tech/container,
    #     interleaving the owning container of any direct component hit immediately after that component
    #     in direct_cnt_ids so token-budget tail eviction never drops the #1 component's parent container.
    prioritized_direct_cnt_ids: list[str] = []
    for cid in list(direct_cnt_ids):
        if cid not in prioritized_direct_cnt_ids:
            prioritized_direct_cnt_ids.append(cid)
        c_data = matched_components.get(cid) or {}
        parent_cnt = str(c_data.get("container") or "").strip()
        if parent_cnt:
            if parent_cnt not in matched_components:
                row = cursor.execute(
                    "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                    (parent_cnt,),
                ).fetchone()
                if row:
                    cnt_data = orjson.loads(row["data_json"])
                    cnt_data["id"] = row["id"]
                    cnt_data["title"] = row["title"]
                    cnt_data.setdefault("file_path", row["file_path"])
                    matched_components[row["id"]] = cnt_data
            if parent_cnt in matched_components:
                _add_reason(parent_cnt, f"Owning container of {cid}")
                _record_prov(parent_cnt, "declared_spec", f"{cid}:container")
                if parent_cnt not in prioritized_direct_cnt_ids:
                    prioritized_direct_cnt_ids.append(parent_cnt)
    direct_cnt_ids = prioritized_direct_cnt_ids

    for cid, c_data in list(matched_components.items()):
        parent_cnt = str(c_data.get("container") or "").strip()
        if parent_cnt and parent_cnt not in matched_components:
            row = cursor.execute(
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_cnt,),
            ).fetchone()
            if row:
                cnt_data = orjson.loads(row["data_json"])
                cnt_data["id"] = row["id"]
                cnt_data["title"] = row["title"]
                cnt_data.setdefault("file_path", row["file_path"])
                matched_components[row["id"]] = cnt_data
                if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                    expanded_cnt_ids.append(row["id"])
                _add_reason(row["id"], f"Owning container of {cid}")
                _record_prov(row["id"], "declared_spec", f"{cid}:container")

    # 5. Expand 1-hop internal dependencies via graph edges (depends_on, consumes_api)
    primary_cids = list(
        dict.fromkeys([*direct_cnt_ids, *[cid for cid in matched_components if cid not in direct_cnt_ids]])
    )
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        dep_rows = cursor.execute(
            f"""
            SELECT target_id, provenance, evidence FROM edges
            WHERE source_id IN ({ph}) AND rel_type IN ('depends_on', 'consumes_api')
            """,
            tuple(primary_cids),
        ).fetchall()
        dep_prov_by_target: dict[str, tuple[str, str]] = {}
        for d_tid, d_prov, d_ev in dep_rows:
            if not d_tid:
                continue
            p_str = str(d_prov or "declared_spec")
            e_str = str(d_ev or "")
            prev = dep_prov_by_target.get(d_tid)
            if prev is None or EDGE_PROVENANCE_PRECEDENCE.get(p_str, 0) > EDGE_PROVENANCE_PRECEDENCE.get(prev[0], 0):
                dep_prov_by_target[d_tid] = (p_str, e_str)
        missing_dep_ids = [tid for tid in dep_prov_by_target if tid not in matched_components]
        if missing_dep_ids:
            ph_dep = ",".join("?" for _ in missing_dep_ids)
            cursor.execute(
                f"SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id IN ({ph_dep}) "
                "ORDER BY CASE WHEN type = 'tech/container' THEN 0 ELSE 1 END, id;",
                tuple(missing_dep_ids),
            )
            for row in cursor.fetchall():
                c_data = orjson.loads(row["data_json"])
                c_data["id"] = row["id"]
                c_data["title"] = row["title"]
                c_data.setdefault("file_path", row["file_path"])
                matched_components[row["id"]] = c_data
                _add_reason(row["id"], "Internal dependency")
                d_prov, d_ev = dep_prov_by_target.get(row["id"], ("declared_spec", ""))
                _record_prov(row["id"], d_prov, d_ev)

    # 6. Include contracts owned by or provided/consumed by primary components
    direct_ctr_ids = list(matched_contracts.keys())
    direct_pol_ids = list(matched_policies.keys())
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        cursor.execute(
            f"""
            SELECT id, title, type, domain, file_path, data_json FROM entities
            WHERE type = 'tech/contract' AND (
                id IN (SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type IN ('provides', 'consumes'))
                OR id IN (SELECT source_id FROM edges WHERE target_id IN ({ph}) AND rel_type = 'provides')
            );
            """,
            tuple(primary_cids + primary_cids),
        )
        for row in cursor.fetchall():
            ctr_data = orjson.loads(row["data_json"])
            ctr_data["id"] = row["id"]
            ctr_data["title"] = row["title"]
            ctr_data.setdefault("file_path", row["file_path"])
            matched_contracts.setdefault(row["id"], ctr_data)

    # 7. Include capabilities directly implementing/implemented by primary components,
    # then fall back to domain capabilities when no direct capability edges exist or when domain capabilities match.
    direct_edge_cap_ids: set[str] = set()
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        cursor.execute(
            f"""
            SELECT id, title, type, domain, file_path, data_json FROM entities
            WHERE type = 'product/capability' AND (
                id IN (SELECT source_id FROM edges WHERE target_id IN ({ph}) AND rel_type IN ('implemented_by', 'implements'))
                OR id IN (SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type IN ('implements', 'implemented_by'))
            );
            """,
            tuple(primary_cids + primary_cids),
        )
        for row in cursor.fetchall():
            cap_data = orjson.loads(row["data_json"])
            cap_data["id"] = row["id"]
            cap_data["title"] = row["title"]
            cap_data.setdefault("file_path", row["file_path"])
            matched_capabilities.setdefault(row["id"], cap_data)
            direct_edge_cap_ids.add(row["id"])

    domains = {c.get("domain") for c in [matched_components[cid] for cid in primary_cids]}
    domains.discard("")
    domains.discard(None)
    if domains:
        ph = ",".join("?" for _ in domains)
        cursor.execute(
            f"SELECT id, title, type, domain, file_path, data_json FROM entities WHERE type = 'product/capability' AND domain IN ({ph});",
            tuple(domains),
        )
        all_matched_comp_ids = set(matched_components.keys())
        for row in cursor.fetchall():
            cap_data = orjson.loads(row["data_json"])
            cap_data["id"] = row["id"]
            cap_data["title"] = row["title"]
            cap_data.setdefault("file_path", row["file_path"])
            if direct_edge_cap_ids and row["id"] not in direct_edge_cap_ids:
                tech_block = cap_data.get("technical") if isinstance(cap_data.get("technical"), dict) else {}
                bound_targets = {
                    str(t)
                    for t in (tech_block.get("containers") or []) + (tech_block.get("components") or [])
                    if isinstance(t, str) and t.strip()
                }
                if bound_targets and not (bound_targets & all_matched_comp_ids):
                    continue
            matched_capabilities.setdefault(row["id"], cap_data)

    # 7b. Include invariants declared directly on primary components
    primary_comp_inv_ids: set[str] = set()
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type = 'asserts'
            );
            """,
            tuple(primary_cids),
        )
        for row in cursor.fetchall():
            inv_data = orjson.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)
            primary_comp_inv_ids.add(row["id"])

    # 8. Invariants & Policies expansion from capabilities
    if matched_capabilities:
        cap_ids = list(matched_capabilities.keys())
        ph = ",".join("?" for _ in cap_ids)
        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type = 'asserts'
            );
            """,
            tuple(cap_ids),
        )
        for row in cursor.fetchall():
            inv_data = orjson.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)

        pol_cap_refs: dict[str, set[str]] = {}
        cursor.execute(
            f"""
            SELECT ed.source_id, e.id, e.title, e.file_path, e.data_json
            FROM edges ed
            JOIN entities e ON e.id = ed.target_id
            WHERE e.type = 'product/policy'
              AND ed.source_id IN ({ph})
              AND ed.rel_type = 'governed_by';
            """,
            tuple(cap_ids),
        )
        for row in cursor.fetchall():
            pol_data = orjson.loads(row["data_json"])
            pol_data["id"] = row["id"]
            pol_data["title"] = row["title"]
            pol_data.setdefault("file_path", row["file_path"])
            matched_policies.setdefault(row["id"], pol_data)
            pol_cap_refs.setdefault(row["id"], set()).add(row["source_id"])
    else:
        pol_cap_refs = {}

    # 9. Fallback governing policies if none matched
    if not matched_policies and (any_matched or not has_explicit_query):
        for row in cursor.execute(
            """
            SELECT id, title, type, domain, file_path, data_json FROM entities
            WHERE type = 'product/policy'
            ORDER BY CASE lower(coalesce(json_extract(data_json, '$.severity'), ''))
                WHEN 'blocking' THEN 0
                WHEN 'warning' THEN 1
                WHEN 'advisory' THEN 2
                ELSE 3
            END, id
            LIMIT 2;
            """
        ).fetchall():
            p_data = orjson.loads(row["data_json"])
            p_data["id"] = row["id"]
            p_data["title"] = row["title"]
            p_data.setdefault("file_path", row["file_path"])
            matched_policies[row["id"]] = p_data

    # 10. Invariants from governing policies
    if matched_policies and tier != "anchor":
        pol_ids = list(matched_policies.keys())
        ph_pol = ",".join("?" for _ in pol_ids)
        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph_pol}) AND rel_type = 'asserts'
            );
            """,
            tuple(pol_ids),
        )
        for row in cursor.fetchall():
            inv_data = orjson.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)

    # Ensure embedded decisions, domain_path, and bound design pointers are surfaced on each matched component
    comp_domain_ids: set[str] = set()
    for comp_entry in matched_components.values():
        collected_decs = collect_component_decisions(comp_entry)
        if collected_decs:
            comp_entry["decisions"] = collected_decs
        dom_id = str(comp_entry.get("domain") or "").strip()
        if not dom_id:
            parent_cnt = str(comp_entry.get("container") or "").strip()
            if parent_cnt and parent_cnt in matched_components:
                dom_id = str(matched_components[parent_cnt].get("domain") or "").strip()
                if dom_id:
                    comp_entry["domain"] = dom_id
        if dom_id:
            comp_domain_ids.add(dom_id)

    if tier != "anchor" and matched_components:
        domain_path_by_id: dict[str, str] = {}
        if comp_domain_ids:
            ph_dom = ",".join("?" for _ in comp_domain_ids)
            for row in cursor.execute(
                f"SELECT id, file_path FROM entities WHERE type = 'product/domain' AND id IN ({ph_dom});",
                tuple(comp_domain_ids),
            ).fetchall():
                if row["file_path"]:
                    domain_path_by_id[row["id"]] = row["file_path"]

        all_scope_ids = sorted(
            {
                sid
                for cid, comp_entry in matched_components.items()
                for sid in (cid, str(comp_entry.get("container") or "").strip())
                if sid
            }
        )
        design_by_scope: dict[str, dict[str, dict[str, str]]] = {}
        if all_scope_ids:
            ph_sc = ",".join("?" for _ in all_scope_ids)
            for r in cursor.execute(
                f"""
                SELECT ed.target_id AS scope_id, e.id, e.type, e.file_path
                FROM edges ed
                JOIN entities e ON e.id = ed.source_id
                WHERE ed.target_id IN ({ph_sc})
                  AND ed.rel_type IN ('implemented_by', 'styled_by', 'styles_container', 'delivered_by')
                  AND e.type IN ('design/client', 'design/system')
                UNION
                SELECT ed.source_id AS scope_id, e.id, e.type, e.file_path
                FROM edges ed
                JOIN entities e ON e.id = ed.target_id
                WHERE ed.source_id IN ({ph_sc})
                  AND ed.rel_type IN ('implemented_by', 'styled_by', 'styles_container', 'delivered_by')
                  AND e.type IN ('design/client', 'design/system')
                UNION
                SELECT ed1.target_id AS scope_id, e.id, e.type, e.file_path
                FROM edges ed1
                JOIN edges ed2 ON ed2.source_id = ed1.source_id
                JOIN entities e ON e.id = ed2.target_id
                WHERE ed1.target_id IN ({ph_sc})
                  AND ed1.rel_type = 'implemented_by'
                  AND ed2.rel_type = 'styled_by'
                  AND e.type IN ('design/client', 'design/system')
                ORDER BY e.id;
                """,
                all_scope_ids * 3,
            ).fetchall():
                if r["id"]:
                    design_by_scope.setdefault(r["scope_id"], {})[r["id"]] = {
                        "id": r["id"],
                        "type": r["type"],
                        "file_path": r["file_path"] or "",
                    }

        for cid, comp_entry in matched_components.items():
            dom_id = str(comp_entry.get("domain") or "").strip()
            if dom_id and dom_id in domain_path_by_id:
                comp_entry.setdefault("domain_path", domain_path_by_id[dom_id])
            parent_cnt = str(comp_entry.get("container") or "").strip()
            merged_design: dict[str, dict[str, str]] = {}
            for sid in (cid, parent_cnt):
                if sid and sid in design_by_scope:
                    merged_design.update(design_by_scope[sid])
            if merged_design:
                comp_entry.setdefault("design", [merged_design[k] for k in sorted(merged_design)])

    # Assemble and rank-order lists so token-budget truncation retains highest-priority entities first
    primary_set = set(primary_cids)
    direct_cnt_set = set(direct_cnt_ids)
    direct_cnt_rank = {cid: idx for idx, cid in enumerate(direct_cnt_ids)}
    expanded_cnt_rank = {cid: idx for idx, cid in enumerate(expanded_cnt_ids)}
    primary_rank = {cid: idx for idx, cid in enumerate(primary_cids)}
    direct_ctr_rank = {cid: idx for idx, cid in enumerate(direct_ctr_ids)}
    direct_cap_rank = {cid: idx for idx, cid in enumerate(direct_cap_ids)}
    expanded_cap_rank = {cid: idx for idx, cid in enumerate(expanded_cap_ids)}
    direct_pol_rank = {pid: idx for idx, pid in enumerate(direct_pol_ids)}

    file_tokens: set[str] = set()
    for f in clean_files:
        file_tokens.update(_tokenize_text(Path(f).stem.replace("_", " ").replace("-", " ")))
    for cid in primary_cids:
        if cid.startswith("CMP-"):
            file_tokens.update(_tokenize_text(cid.removeprefix("CMP-").replace("_", " ").replace("-", " ")))

    def _cap_binds_matched_comp(cap_entry: dict[str, Any]) -> bool:
        if not primary_set:
            return False
        tech = cap_entry.get("technical") if isinstance(cap_entry.get("technical"), dict) else {}
        comp_refs = {str(t).strip() for t in (tech.get("components") or []) if isinstance(t, str)}
        return bool(comp_refs & primary_set)

    slice_tech_ids = set(matched_components.keys()) | set(matched_contracts.keys())

    def _cap_relevance_tuple(cap_entry: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
        tech = cap_entry.get("technical") if isinstance(cap_entry.get("technical"), dict) else {}
        cnt_refs = [str(t).strip() for t in (tech.get("containers") or []) if isinstance(t, str) and str(t).strip()]
        primary_cnt_match = bool(cnt_refs and cnt_refs[0] in primary_set)
        binds_comp = _cap_binds_matched_comp(cap_entry)
        in_domain = cap_entry.get("domain", "") in domains
        overlap = 0
        if file_tokens:
            cap_tokens = set(_tokenize_text(str(cap_entry.get("title", "")).replace("_", " ").replace("-", " ")))
            overlap = len(file_tokens & cap_tokens)
        if (primary_cnt_match and overlap > 0) or (binds_comp and in_domain):
            tier_score = 0
        elif in_domain or binds_comp:
            tier_score = 1
        else:
            tier_score = 2
        bound_tech_refs = {
            str(t).strip()
            for key in ("containers", "components", "contracts")
            for t in (tech.get(key) if isinstance(tech.get(key), list) else ())
            if isinstance(t, str) and str(t).strip()
        }
        shared_links = len(bound_tech_refs & slice_tech_ids)
        inv_count = len(tech.get("invariants") or []) if isinstance(tech.get("invariants"), list) else 0
        return (
            tier_score,
            -overlap,
            0 if in_domain else 1,
            -shared_links,
            -inv_count,
            str(cap_entry.get("id", "")),
        )

    sorted_comps = sorted(
        matched_components.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_cnt_set else (1 if x.get("id", "") in primary_set else 2),
            direct_cnt_rank.get(
                x.get("id", ""),
                100 + expanded_cnt_rank.get(x.get("id", ""), primary_rank.get(x.get("id", ""), 999)),
            ),
            -len(matched_reasons.get(x.get("id", ""), ())),
            0 if x.get("type") == "tech/container" else 1,
            x.get("id", ""),
        ),
    )
    sorted_ctrs = sorted(
        matched_contracts.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_ctr_rank else 1,
            direct_ctr_rank.get(x.get("id", ""), 999),
            x.get("id", ""),
        ),
    )
    sorted_caps = sorted(
        matched_capabilities.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_cap_rank else (1 if x.get("id", "") in expanded_cap_rank else 2),
            direct_cap_rank.get(x.get("id", ""), 100 + expanded_cap_rank.get(x.get("id", ""), 999)),
            *_cap_relevance_tuple(x),
        ),
    )
    pol_severity_rank = {"blocking": 0, "warning": 1, "advisory": 2}
    top_cap_id = str(sorted_caps[0].get("id", "")) if sorted_caps else ""
    sorted_pols = sorted(
        matched_policies.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_pol_rank else 1,
            direct_pol_rank.get(x.get("id", ""), 999),
            pol_severity_rank.get(str(x.get("severity") or "").strip().lower(), 3),
            0 if (top_cap_id and top_cap_id in pol_cap_refs.get(x.get("id", ""), ())) else 1,
            -len(pol_cap_refs.get(x.get("id", ""), ())),
            x.get("id", ""),
        ),
    )

    ordered_invs: list[dict[str, Any]] = []
    if tier != "anchor":
        max_invs = max(len(direct_inv_ids) + 12, 16) if tier == "guardrails" else 1000
        seen_inv_ids: set[str] = set()
        for iid in direct_inv_ids:
            if iid in matched_invariants and iid not in seen_inv_ids:
                ordered_invs.append(matched_invariants[iid])
                seen_inv_ids.add(iid)
        for iid in sorted(primary_comp_inv_ids):
            if len(ordered_invs) >= max_invs:
                break
            if iid in matched_invariants and iid not in seen_inv_ids:
                ordered_invs.append(matched_invariants[iid])
                seen_inv_ids.add(iid)

        inv_focus_tokens: set[str] = set()
        inv_leaf_tokens: set[str] = set()
        tb_coupling_tier: dict[str, int] = {}

        def _inv_test_files(inv_obj: dict[str, Any]) -> list[str]:
            files_out: list[str] = []
            tb_raw_val = str(inv_obj.get("test_binding", "") or "").strip()
            if tb_raw_val and tb_raw_val.upper() != "TBD":
                norm_tb = _normalize_rel_path(tb_raw_val.split("::", 1)[0])
                if norm_tb:
                    files_out.append(norm_tb)
            if not files_out:
                m_inv = matched_invariants.get(str(inv_obj.get("id", "")))
                if isinstance(m_inv, dict):
                    for mt in m_inv.get("matching_tests") or ():
                        norm_mt = _normalize_rel_path(str(mt).split("::", 1)[0])
                        if norm_mt and norm_mt not in files_out:
                            files_out.append(norm_mt)
            return files_out

        if clean_files or component:
            for f in clean_files:
                inv_focus_tokens.update(
                    _tokenize_text(f.replace("/", " ").replace("_", " ").replace("-", " ").replace(".", " "))
                )
                leaf_stem = f.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                inv_leaf_tokens.update(_tokenize_text(leaf_stem.replace("_", " ").replace("-", " ")))
            for cid in primary_cids:
                inv_focus_tokens.update(
                    _tokenize_text(cid.removeprefix("CNT-").removeprefix("CMP-").replace("_", " ").replace("-", " "))
                )
            _focus_noise = {"eidos", "src", "py", "md", "adoc", "json", "ts", "js", "go", "rs", "main", "init", "index"}
            inv_focus_tokens -= _focus_noise
            inv_leaf_tokens -= _focus_noise

            edited_file_set = {_normalize_rel_path(f) for f in clean_files if f and f.strip()}
            primary_cmp_ids = [cid for cid in primary_cids if cid.startswith("CMP-")]
            coupling_owner_ids = primary_cmp_ids or list(primary_cids)
            primary_comp_file_set: set[str] = set()
            if coupling_owner_ids:
                ph_own = ",".join("?" for _ in coupling_owner_ids)
                for (sym_fp,) in cursor.execute(
                    f"""
                    SELECT DISTINCT file_path FROM code_symbols WHERE is_test = 0 AND entity_id IN ({ph_own})
                    UNION
                    SELECT DISTINCT symbol FROM symbol_mappings WHERE kind = 'source_path' AND entity_id IN ({ph_own});
                    """,
                    tuple(coupling_owner_ids) * 2,
                ).fetchall():
                    if sym_fp and not _has_glob_magic(str(sym_fp)):
                        norm_sf = _normalize_rel_path(str(sym_fp))
                        if norm_sf:
                            primary_comp_file_set.add(norm_sf)

            candidate_tb_files: set[str] = set()
            for cap_src in sorted_caps:
                raw_cap_invs = (cap_src.get("technical") or {}).get("invariants") or cap_src.get("invariants") or []
                for inv_d in raw_cap_invs:
                    if isinstance(inv_d, dict):
                        candidate_tb_files.update(_inv_test_files(inv_d))

            target_source_files = edited_file_set | primary_comp_file_set
            if candidate_tb_files and target_source_files:
                sorted_tb_files = sorted(candidate_tb_files)
                test_imp_rows: list[Any] = []
                for batch_start in range(0, len(sorted_tb_files), 400):
                    batch = sorted_tb_files[batch_start : batch_start + 400]
                    ph_tb = ",".join("?" for _ in batch)
                    test_imp_rows.extend(
                        cursor.execute(
                            f"SELECT file_path, symbol FROM code_symbols WHERE kind = 'import' AND is_test = 1 AND file_path IN ({ph_tb});",
                            tuple(batch),
                        ).fetchall()
                    )
                if test_imp_rows:
                    all_known_files: set[str] = set(target_source_files)
                    for (kf_row,) in cursor.execute(
                        "SELECT DISTINCT file_path FROM code_symbols WHERE is_test = 0 UNION SELECT rel_path FROM indexed_files WHERE doc_type = 'code/source';"
                    ).fetchall():
                        if kf_row:
                            all_known_files.add(_normalize_rel_path(str(kf_row)))
                    target_mod_map = build_module_to_file_map(all_known_files)
                    idx_root = getattr(index, "root", None)
                    go_mods = discover_go_module_roots(idx_root, all_known_files)
                    go_files_by_dir: dict[str, list[str]] = {}
                    for kf in sorted(all_known_files):
                        if kf.endswith(".go") and not kf.endswith("_test.go"):
                            go_files_by_dir.setdefault(posixpath.dirname(kf), []).append(kf)
                    rs_crates = discover_rust_crate_map(idx_root, all_known_files)
                    for tb_fp, imp_sym in test_imp_rows:
                        norm_tb = _normalize_rel_path(str(tb_fp or ""))
                        resolved_list = resolve_import_target_files(
                            norm_tb,
                            str(imp_sym or "").strip(),
                            all_known_files,
                            target_mod_map,
                            fallback_parent=True,
                            repo_root=idx_root,
                            go_modules=go_mods,
                            go_files_by_dir=go_files_by_dir,
                            rust_crates=rs_crates,
                        )
                        for resolved in resolved_list:
                            if resolved in edited_file_set:
                                tb_coupling_tier[norm_tb] = 2
                                break
                            if resolved in primary_comp_file_set and tb_coupling_tier.get(norm_tb, 0) < 1:
                                tb_coupling_tier[norm_tb] = 1

        def _cap_inv_rank_key(
            item: tuple[int, dict[str, Any]],
        ) -> tuple[int, int, int, float, int, int, int, int]:
            inv_obj = item[1]
            id_toks = _tokenize_text(str(inv_obj.get("id", "")).replace("_", " ").replace("-", " "))
            id_core_toks = id_toks - {"inv"}
            tb_raw = str(inv_obj.get("test_binding", "") or "")
            tb_files = _inv_test_files(inv_obj)
            tb_file_path = tb_files[0] if tb_files else _normalize_rel_path(tb_raw.split("::", 1)[0])
            ast_coupling = max((tb_coupling_tier.get(fp, 0) for fp in tb_files), default=0)
            tb_toks = _tokenize_text(
                tb_raw.replace("/", " ").replace("_", " ").replace("-", " ").replace(":", " ").replace(".", " ")
            )
            tb_file_stem = tb_file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            tb_file_toks = _tokenize_text(tb_file_stem.replace("_", " ").replace("-", " "))
            tb_file_core = tb_file_toks - {"test", "tests"}
            leaf_id_hits = len(inv_leaf_tokens & id_toks)
            focus_id_hits = len(inv_focus_tokens & id_toks)
            id_specificity = round(focus_id_hits / max(len(id_core_toks), 1), 6)
            exact_tb_file = 1 if (inv_leaf_tokens and tb_file_core == inv_leaf_tokens) else 0
            leaf_tb_hits = len(inv_leaf_tokens & tb_file_toks)
            tb_tiebreak = len(inv_focus_tokens & tb_toks) if (leaf_id_hits or focus_id_hits or leaf_tb_hits) else 0
            return (
                -ast_coupling,
                -leaf_id_hits,
                -focus_id_hits,
                -id_specificity,
                -exact_tb_file,
                -leaf_tb_hits,
                -tb_tiebreak,
                item[0],
            )

        for source_list in (sorted_comps, sorted_caps, sorted_pols):
            if len(ordered_invs) >= max_invs:
                break
            inv_lists: list[list[str]] = []
            is_cap_group = source_list is sorted_caps
            for src in source_list:
                raw_invs = list((src.get("technical") or {}).get("invariants") or src.get("invariants") or [])
                valid_invs = [inv for inv in raw_invs if isinstance(inv, dict) and inv.get("id")]
                if is_cap_group and (inv_focus_tokens or tb_coupling_tier):
                    valid_invs = [inv_d for _, inv_d in sorted(enumerate(valid_invs), key=_cap_inv_rank_key)]
                inv_lists.append([inv["id"] for inv in valid_invs])
            # Give the top-ranked capability up to 2 leading invariants before round-robining across lower-ranked capabilities
            if is_cap_group and inv_lists:
                for iid in inv_lists[0][:2]:
                    if len(ordered_invs) >= max_invs:
                        break
                    if iid in matched_invariants and iid not in seen_inv_ids:
                        ordered_invs.append(matched_invariants[iid])
                        seen_inv_ids.add(iid)
            max_len = max((len(lst) for lst in inv_lists), default=0)
            for idx in range(max_len):
                if len(ordered_invs) >= max_invs:
                    break
                for lst in inv_lists:
                    if idx < len(lst):
                        iid = lst[idx]
                        if iid in matched_invariants and iid not in seen_inv_ids:
                            ordered_invs.append(matched_invariants[iid])
                            seen_inv_ids.add(iid)
                            if len(ordered_invs) >= max_invs:
                                break

        if len(ordered_invs) < max_invs:
            for iid in sorted(matched_invariants.keys()):
                if iid not in seen_inv_ids:
                    ordered_invs.append(matched_invariants[iid])
                    seen_inv_ids.add(iid)
                    if len(ordered_invs) >= max_invs:
                        break

    for cid, comp_entry in matched_components.items():
        if cid not in provenance_tags:
            _record_prov(cid, "declared_spec", str(comp_entry.get("file_path") or cid))

    all_matched_entity_ids = (
        set(matched_components.keys())
        | set(matched_contracts.keys())
        | set(matched_capabilities.keys())
        | set(matched_invariants.keys())
        | set(matched_policies.keys())
        | {str(d) for d in domains if d}
        | comp_domain_ids
    )
    attached_memories = attach_context_memories(
        conn,
        index.root,
        tier=tier,
        clean_files=clean_files,
        requested_files=requested_files,
        direct_entity_ids=direct_anchor_ids,
        expanded_entity_ids=all_matched_entity_ids - direct_anchor_ids,
        task=task,
        has_explicit_query=has_explicit_query,
        has_architectural_matches=bool(sorted_comps or sorted_ctrs or sorted_caps or ordered_invs or sorted_pols),
        use_semantic=use_semantic,
        eidos_dir=getattr(index, "eidos_dir", None),
        db_path=getattr(index, "db_path", None),
    )

    slice_obj = TieredContextSlice(
        tier=tier,
        query={"files": requested_files, "component": component, "task": task},
        components=sorted_comps,
        contracts=sorted_ctrs,
        capabilities=sorted_caps,
        invariants=ordered_invs,
        policies=sorted_pols,
        memories=attached_memories,
        matched_reasons=matched_reasons,
        provenance_tags=provenance_tags,
        query_latency_ms=0.0,
        token_estimate=0,
    )
    slice_obj._enforce_token_budget(
        direct_cnt_set if direct_cnt_set else primary_set,
        direct_cap_ids=set(direct_cap_ids),
        direct_inv_ids=set(direct_inv_ids),
        direct_pol_ids=set(direct_pol_ids),
    )
    slice_obj.query_latency_ms = (time.perf_counter() - t0) * 1000
    return slice_obj
