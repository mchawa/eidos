"""Static HTTP contract & server-to-client route linking into ast_http edges and code_couplings (CMP-HTTP-TOPOLOGY-LINKER)."""

from __future__ import annotations

import contextlib
import importlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.ast_http import normalize_http_route_path
from eidos.core.index.storage import (
    _resolve_conn_db_path,
    build_spec_path_resolver,
    upsert_edge,
)


def _normalize_rel_path(raw: str) -> str:
    clean = raw.replace("\\", "/").strip()
    while clean.startswith("./"):
        clean = clean[2:]
    return clean


_HTTP_DOC_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_HTTP_JSON_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+)\|\s*`?(/[^|\s`]*)`?\s*\|", re.MULTILINE)


def _extract_contract_http_routes(
    conn: sqlite3.Connection,
    eff_root: Path | None,
) -> dict[tuple[str, str], list[tuple[str, str, str]]]:
    """Extract normalized (method, path) -> [(ctr_id, ctr_rel_path, owning_cnt_id)] from openapi and http_json CTR-* specs."""
    routes: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    if eff_root is None or not eff_root.is_dir():
        return routes

    resolve_ref = build_spec_path_resolver(conn)
    for row in conn.execute(
        "SELECT id, file_path, data_json FROM entities WHERE type = 'tech/contract' ORDER BY id;"
    ).fetchall():
        ctr_id = str(row["id"] or "")
        ctr_fp = _normalize_rel_path(str(row["file_path"] or ""))
        try:
            fm = orjson.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(fm, dict) or not ctr_id:
            continue
        protocol = str(fm.get("protocol") or "").strip().lower()
        owning_cnt = resolve_ref(fm.get("container") or "")
        spec_rel = _normalize_rel_path(str(fm.get("specification_file") or ""))

        if protocol in ("openapi", "rest_openapi") and spec_rel.lower().endswith((".yaml", ".yml", ".json")):
            spec_abs = (eff_root / spec_rel).resolve()
            if spec_abs.is_file() and spec_abs.is_relative_to(eff_root):
                parsed_spec: Any = None
                with contextlib.suppress(OSError, ValueError, TypeError):
                    raw_txt = spec_abs.read_text(encoding="utf-8", errors="replace")
                    if spec_rel.lower().endswith(".json"):
                        parsed_spec = orjson.loads(raw_txt)
                    else:
                        import yaml

                        parsed_spec = yaml.safe_load(raw_txt)
                if isinstance(parsed_spec, dict) and isinstance(parsed_spec.get("paths"), dict):
                    srv_prefixes = [""]
                    servers = parsed_spec.get("servers")
                    if isinstance(servers, list):
                        for srv in servers:
                            if isinstance(srv, dict) and isinstance(srv.get("url"), str):
                                sp = normalize_http_route_path(srv["url"])
                                if sp and sp != "/" and sp not in srv_prefixes:
                                    srv_prefixes.append(sp)
                    for raw_p, ops in parsed_spec["paths"].items():
                        if not isinstance(ops, dict):
                            continue
                        norm_raw = normalize_http_route_path(str(raw_p))
                        if not norm_raw:
                            continue
                        for verb_k in ops:
                            verb_u = str(verb_k).strip().upper()
                            if verb_u in _HTTP_DOC_METHODS:
                                for pref in srv_prefixes:
                                    full_p = (
                                        normalize_http_route_path(f"{pref.rstrip('/')}{norm_raw}") if pref else norm_raw
                                    )
                                    entry = (ctr_id, spec_rel, owning_cnt)
                                    bucket = routes.setdefault((verb_u, full_p), [])
                                    if entry not in bucket:
                                        bucket.append(entry)

        elif protocol == "http_json" and ctr_fp:
            adoc_abs = (eff_root / ctr_fp).resolve()
            if not adoc_abs.is_file() and not ctr_fp.startswith("eidos/"):
                adoc_abs = (eff_root / "eidos" / ctr_fp).resolve()
            if adoc_abs.is_file() and adoc_abs.is_relative_to(eff_root):
                with contextlib.suppress(OSError):
                    adoc_txt = adoc_abs.read_text(encoding="utf-8", errors="replace")
                    for m_row in _HTTP_JSON_TABLE_ROW_RE.finditer(adoc_txt):
                        methods_col, path_col = m_row.group(1), m_row.group(2)
                        norm_p = normalize_http_route_path(path_col)
                        if not norm_p:
                            continue
                        for tok in re.findall(r"[A-Z]{3,7}", methods_col):
                            if tok in _HTTP_DOC_METHODS:
                                entry = (ctr_id, ctr_fp, owning_cnt)
                                bucket = routes.setdefault((tok, norm_p), [])
                                if entry not in bucket:
                                    bucket.append(entry)
    return routes


def _route_segments_match(req_path: str, cand_path: str) -> bool:
    """Return True if concrete/parameterized ``req_path`` matches parameterized ``cand_path`` segment-by-segment."""
    r_segs = [s for s in req_path.strip("/").split("/") if s]
    c_segs = [s for s in cand_path.strip("/").split("/") if s]
    if not r_segs or len(r_segs) != len(c_segs):
        return False
    has_static_match = False
    for rs, cs in zip(r_segs, c_segs, strict=False):
        if rs == cs:
            if cs != "{*}":
                has_static_match = True
        elif cs == "{*}" or rs == "{*}":
            continue
        else:
            return False
    return has_static_match


def _derive_http_request_edges(
    conn: sqlite3.Connection,
    resolver: Any = None,
    repo_root: Path | None = None,
) -> None:
    """Match client http_request symbols against AST http_endpoint routes and OpenAPI/http_json CTR-* contracts."""
    if resolver is None:
        resolver = importlib.import_module("eidos.core.index.code_graph")._CodeOwnershipResolver(conn)

    eff_root = Path(repo_root).resolve() if repo_root is not None else None
    if eff_root is None:
        db_file = _resolve_conn_db_path(conn)
        if db_file is not None and db_file.parent.name == ".cache" and db_file.parent.parent.name == "eidos":
            eff_root = db_file.parents[2]

    comp_to_cnt: dict[str, str] = {}
    for src_id, dst_id in conn.execute("SELECT source_id, target_id FROM edges WHERE rel_type = 'part_of';"):
        comp_to_cnt[src_id] = dst_id

    def _cnt_and_cmp(rel_path: str, ent_id: str = "") -> tuple[str, str]:
        if not ent_id:
            ent_id = resolver.resolve(rel_path) or ""
        if ent_id.startswith("CMP-"):
            return comp_to_cnt.get(ent_id, ""), ent_id
        return ent_id, ""

    req_rows = conn.execute(
        """
        SELECT file_path, symbol, line_start, entity_id
        FROM code_symbols
        WHERE kind = 'http_request' AND is_primary = 1 AND is_test = 0
        ORDER BY file_path, line_start, symbol;
        """
    ).fetchall()
    if not req_rows:
        return

    endpoints_by_route: dict[tuple[str, str], list[tuple[str, int, str, str]]] = {}
    for ep_row in conn.execute(
        """
        SELECT file_path, symbol, line_start, entity_id
        FROM code_symbols
        WHERE kind = 'http_endpoint' AND is_primary = 1 AND is_test = 0
        ORDER BY file_path, line_start, symbol;
        """
    ).fetchall():
        ep_sym = str(ep_row["symbol"] or "").strip()
        if " " not in ep_sym:
            continue
        verb, norm_p = ep_sym.split(" ", 1)
        ep_file = _normalize_rel_path(ep_row["file_path"])
        dst_cnt, dst_cmp = _cnt_and_cmp(ep_file, str(ep_row["entity_id"] or ""))
        endpoints_by_route.setdefault((verb.upper(), norm_p.strip()), []).append(
            (ep_file, max(1, int(ep_row["line_start"] or 1)), dst_cnt, dst_cmp)
        )

    contracts_by_route = _extract_contract_http_routes(conn, eff_root)
    all_route_keys = sorted(set(endpoints_by_route) | set(contracts_by_route))

    for rq in req_rows:
        cli_file = _normalize_rel_path(rq["file_path"])
        rq_sym = str(rq["symbol"] or "").strip()
        cli_line = max(1, int(rq["line_start"] or 1))
        if not cli_file or " " not in rq_sym:
            continue
        verb, req_path = rq_sym.split(" ", 1)
        verb = verb.upper().strip()
        req_path = req_path.strip()
        src_cnt, src_cmp = _cnt_and_cmp(cli_file, str(rq["entity_id"] or ""))
        if not src_cnt and not src_cmp:
            continue

        matched_key: tuple[str, str] | None = (verb, req_path) if (verb, req_path) in all_route_keys else None
        if matched_key is None and req_path != "/":
            seg_candidates = [
                (cand_v, cand_p)
                for cand_v, cand_p in all_route_keys
                if cand_v == verb and _route_segments_match(req_path, cand_p)
            ]
            if len(seg_candidates) == 1:
                matched_key = seg_candidates[0]
        if matched_key is None and req_path != "/":
            suffix_candidates = [
                (cand_v, cand_p)
                for cand_v, cand_p in all_route_keys
                if cand_v == verb and len(cand_p) > len(req_path) and cand_p.endswith(req_path)
            ]
            cand_owners: set[str] = set()
            for ck in suffix_candidates:
                for srv_f, _sl, d_cnt, d_cmp in endpoints_by_route.get(ck, ()):
                    if srv_f != cli_file:
                        cand_owners.add(d_cmp or d_cnt)
                for c_id, _cf, c_cnt in contracts_by_route.get(ck, ()):
                    cand_owners.add(c_cnt or c_id)
            if len(suffix_candidates) == 1 and len(cand_owners) == 1:
                matched_key = suffix_candidates[0]

        if matched_key is None:
            continue

        matched_verb, matched_path = matched_key
        coupled_containers_for_call: set[str] = set()

        for srv_file, srv_line, dst_cnt, dst_cmp in endpoints_by_route.get(matched_key, ()):
            if srv_file == cli_file:
                continue
            ev = f"{cli_file}:{cli_line} -> {matched_verb} {matched_path} ({srv_file}:{srv_line})"
            src_ent = src_cmp or src_cnt
            dst_ent = dst_cmp or dst_cnt
            if (
                ((src_cmp and dst_cmp and src_cmp != dst_cmp) or (src_cnt and dst_cnt and src_cnt != dst_cnt))
                and src_ent
                and dst_ent
                and (src_ent != dst_ent or src_cnt != dst_cnt)
            ):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO code_couplings
                    (source_entity_id, target_entity_id, source_cnt_id, target_cnt_id, provenance, src_file, line_start, target_ref)
                    VALUES (?, ?, ?, ?, 'ast_http', ?, ?, ?);
                    """,
                    (
                        src_ent,
                        dst_ent,
                        src_cnt,
                        dst_cnt,
                        cli_file,
                        cli_line,
                        f"{matched_verb} {matched_path} ({srv_file}:{srv_line})",
                    ),
                )
            if src_cmp and dst_cmp and src_cmp != dst_cmp:
                upsert_edge(conn, src_cmp, dst_cmp, "consumes_api", "ast_http", ev)
            if src_cnt and dst_cnt and src_cnt != dst_cnt and src_cnt.startswith("CNT-") and dst_cnt.startswith("CNT-"):
                upsert_edge(conn, src_cnt, dst_cnt, "depends_on", "ast_http", ev)
                coupled_containers_for_call.add(dst_cnt)

        for ctr_id, _ctr_fp, ctr_cnt in contracts_by_route.get(matched_key, ()):
            ev_ctr = f"{cli_file}:{cli_line} -> {matched_verb} {matched_path} ({ctr_id})"
            client_owner = src_cmp or src_cnt
            if client_owner and not (not src_cmp and client_owner == ctr_cnt):
                upsert_edge(conn, client_owner, ctr_id, "consumes", "ast_http", ev_ctr)
            if src_cnt and ctr_cnt and src_cnt != ctr_cnt and src_cnt.startswith("CNT-") and ctr_cnt.startswith("CNT-"):
                upsert_edge(conn, src_cnt, ctr_cnt, "depends_on", "ast_http", ev_ctr)
                if ctr_cnt not in coupled_containers_for_call and client_owner:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO code_couplings
                        (source_entity_id, target_entity_id, source_cnt_id, target_cnt_id, provenance, src_file, line_start, target_ref)
                        VALUES (?, ?, ?, ?, 'ast_http', ?, ?, ?);
                        """,
                        (
                            client_owner,
                            ctr_cnt,
                            src_cnt,
                            ctr_cnt,
                            cli_file,
                            cli_line,
                            f"{matched_verb} {matched_path} ({ctr_id})",
                        ),
                    )
