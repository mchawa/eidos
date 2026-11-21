"""Declarative architectural import boundary & monotonic ratchet evaluator (CMP-ARCHITECTURE-BOUNDARY-VALIDATOR)."""

import contextlib
from pathlib import Path
from typing import Any

from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.workspace.project import resolve_spec_and_repo_roots


def validate_import_boundary_declarations(
    report: ValidationReport,
    root_dir: Path,
    schema_error_key_fn: Any,
) -> None:
    """Validate disjoint allow/deny sets and resolved tier slugs on import_boundary / import_boundaries."""
    _, repo_root = resolve_spec_and_repo_roots(root_dir)
    path_to_id = _build_doc_path_to_id_map(report.documents, repo_root)
    valid_tiers: set[str] = {"presentation", "service", "core", "persistence", "extensibility"}
    for doc in report.documents:
        if doc.doc_type == "tech/container" and doc.model.tier:
            valid_tiers.add(doc.model.tier.strip())

    def _norm_ref(ref: str) -> str:
        clean = ref.strip().replace("\\", "/").lstrip("./")
        return path_to_id.get(clean, clean)

    declared_rule_tiers: set[str] = set()
    for doc in report.documents:
        err_key = schema_error_key_fn(doc.file_path, root_dir=root_dir, repo_root=repo_root)

        def _record_decl_err(msg: str, _doc: SpecDocument = doc, _key: str = err_key) -> None:
            if msg not in _doc.errors:
                _doc.errors.append(msg)
            bucket = report.schema_errors.setdefault(_key, [])
            if msg not in bucket:
                bucket.append(msg)

        ib = doc.model.import_boundary
        if ib is not None and doc.doc_type in ("tech/container", "tech/component"):
            allow_set = {_norm_ref(x) for x in ib.allow if x}
            deny_set = {_norm_ref(x) for x in ib.deny if x}
            overlap = sorted(allow_set & deny_set)
            if overlap:
                _record_decl_err(f"import_boundary has overlapping allow and deny targets: {', '.join(overlap)}")
        ibs = doc.model.import_boundaries
        if ibs is not None and doc.doc_type == "tech/architecture":
            is_arch_active = (doc.model.status or "active").strip().lower() not in ("deprecated", "retired")
            for src_tier, rule in ibs.tier_rules.items():
                if is_arch_active:
                    declared_rule_tiers.add(src_tier)
                    declared_rule_tiers.update(rule.allow)
                    declared_rule_tiers.update(rule.deny)
                if src_tier not in valid_tiers:
                    _record_decl_err(f"import_boundaries.tier_rules references unresolved tier '{src_tier}'")
                t_overlap = sorted(set(rule.allow) & set(rule.deny))
                if t_overlap:
                    _record_decl_err(
                        f"import_boundaries.tier_rules.{src_tier} has overlapping allow and deny tiers: {', '.join(t_overlap)}"
                    )
                for target_t in (*rule.allow, *rule.deny):
                    if target_t not in valid_tiers:
                        _record_decl_err(
                            f"import_boundaries.tier_rules.{src_tier} references unresolved tier '{target_t}'"
                        )

    if declared_rule_tiers:
        known_tiers_str = ", ".join(sorted(declared_rule_tiers))
        for doc in report.documents:
            if doc.doc_type != "tech/container":
                continue
            if (doc.model.status or "active").strip().lower() != "active":
                continue
            tier_slug = (doc.model.tier or "").strip()
            if tier_slug and tier_slug not in declared_rule_tiers:
                warn = (
                    f"Undeclared tier warning in {doc.file_path.name}: "
                    f"active container '{doc.doc_id}' declares tier '{tier_slug}' which is not referenced in "
                    f"ARCH-*.import_boundaries.tier_rules (known tiers: {known_tiers_str})"
                )
                if warn not in report.warnings:
                    report.warnings.append(warn)


def _build_doc_path_to_id_map(documents: list[SpecDocument], repo_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for doc in documents:
        if not doc.doc_id:
            continue
        mapping[doc.doc_id] = doc.doc_id
        with contextlib.suppress(ValueError, OSError):
            rel_repo = doc.file_path.resolve().relative_to(repo_root.resolve()).as_posix()
            mapping[rel_repo] = doc.doc_id
            if rel_repo.startswith("eidos/"):
                mapping[rel_repo.removeprefix("eidos/")] = doc.doc_id
    return mapping


def _rel_doc_path(fp: Path, spec_root: Path, repo_root: Path) -> str:
    res = fp.resolve()
    for base in (repo_root.resolve(), spec_root.resolve()):
        with contextlib.suppress(ValueError):
            return res.relative_to(base).as_posix()
    return res.as_posix()


def evaluate_import_boundaries(
    eidos_dir: Path,
    *,
    repo_root: Path | None = None,
    documents: list[SpecDocument] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate ARCH-*.import_boundaries and CNT-*/CMP-*.import_boundary against synced AST/HTTP code_couplings."""
    spec_root, inferred_repo_root = resolve_spec_and_repo_roots(eidos_dir)
    eff_spec = Path(eidos_dir).resolve() if Path(eidos_dir).is_dir() else spec_root.resolve()
    eff_repo = Path(repo_root).resolve() if repo_root is not None else inferred_repo_root.resolve()
    if not eff_spec.is_dir():
        return [], []

    if documents is None:
        from eidos.core.index.storage import get_cached_spec_document

        docs_loaded: list[SpecDocument] = []
        for fp in sorted(eff_spec.rglob("*.adoc")):
            if fp.name.lower() == "readme.adoc" or fp.name.startswith("_") or fp.is_symlink():
                continue
            doc, _, _ = get_cached_spec_document(fp)
            if doc is not None:
                docs_loaded.append(doc)
        documents = docs_loaded

    path_to_id = _build_doc_path_to_id_map(documents, eff_repo)

    def _res_id(raw_ref: str) -> str:
        clean = (raw_ref or "").strip().replace("\\", "/").lstrip("./")
        return path_to_id.get(clean, clean)

    arch_specs: list[tuple[str, str, Any]] = []
    cnt_bounds: dict[str, tuple[str, Any]] = {}
    cmp_bounds: dict[str, tuple[str, Any]] = {}
    cnt_tiers: dict[str, str] = {}
    cnt_rel_paths: dict[str, str] = {}
    cmp_rel_paths: dict[str, str] = {}
    cmp_to_cnt: dict[str, str] = {}

    for doc in documents:
        status = (doc.model.status or "active").strip().lower()
        if status in ("deprecated", "retired") or not doc.doc_id:
            continue
        rel_p = _rel_doc_path(doc.file_path, eff_spec, eff_repo)
        if doc.doc_type == "tech/architecture" and doc.model.import_boundaries is not None:
            ibs = doc.model.import_boundaries
            if ibs.tier_rules or ibs.enforce_declared_containers or ibs.ratchet_baseline:
                arch_specs.append((doc.doc_id, rel_p, ibs))
        elif doc.doc_type == "tech/container":
            cnt_rel_paths[doc.doc_id] = rel_p
            if doc.model.tier:
                cnt_tiers[doc.doc_id] = doc.model.tier.strip()
            if doc.model.import_boundary is not None and (
                doc.model.import_boundary.allow or doc.model.import_boundary.deny
            ):
                cnt_bounds[doc.doc_id] = (rel_p, doc.model.import_boundary)
        elif doc.doc_type == "tech/component":
            cmp_rel_paths[doc.doc_id] = rel_p
            if doc.model.container:
                cmp_to_cnt[doc.doc_id] = _res_id(doc.model.container)
            if doc.model.import_boundary is not None and (
                doc.model.import_boundary.allow or doc.model.import_boundary.deny
            ):
                cmp_bounds[doc.doc_id] = (rel_p, doc.model.import_boundary)

    if not arch_specs and not cnt_bounds and not cmp_bounds:
        return [], []

    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.index.sync import sync_index

    idx = get_architectural_index(repo_root=eff_repo, eidos_dir=eff_spec)
    conn = idx._get_connection()
    pre_dv_row = conn.execute("PRAGMA data_version;").fetchone()
    pre_dv = int(pre_dv_row[0]) if pre_dv_row else 0
    has_entities = conn.execute("SELECT 1 FROM entities LIMIT 1;").fetchone() is not None
    dirty_state = idx.is_dirty()
    if not has_entities or dirty_state[0]:
        # Preserve strict freshness: wait via sync_index (with stderr lock-wait notice)
        # instead of evaluating code_couplings against a stale WAL snapshot.
        sync_index(
            idx,
            dirty_state=dirty_state if has_entities else None,
            auto_semantic=False,
            _pre_data_version=pre_dv,
        )
        conn = idx._get_connection()

    declared_ctr_pairs: set[tuple[str, str]] = {
        (str(r[0]), str(r[1]))
        for r in conn.execute(
            """
            SELECT DISTINCT
                COALESCE(p_src.target_id, c_edge.source_id) AS src_cnt,
                COALESCE(p_prov.target_id, p_edge.source_id) AS dst_cnt
            FROM edges c_edge
            LEFT JOIN edges p_src ON p_src.source_id = c_edge.source_id AND p_src.rel_type = 'part_of'
            JOIN edges p_edge ON p_edge.target_id = c_edge.target_id AND p_edge.rel_type = 'provides'
            LEFT JOIN edges p_prov ON p_prov.source_id = p_edge.source_id AND p_prov.rel_type = 'part_of'
            WHERE c_edge.rel_type = 'consumes'
              AND c_edge.provenance = 'declared_spec';
            """
        ).fetchall()
        if r[0] and r[1]
    }

    coupling_rows = conn.execute(
        """
        SELECT source_entity_id, target_entity_id, source_cnt_id, target_cnt_id, provenance, src_file, line_start, target_ref
        FROM code_couplings
        ORDER BY src_file, line_start, target_ref;
        """
    ).fetchall()

    ratchet_map: dict[tuple[str, str], tuple[int, str, str]] = {}
    for _arch_id, arch_rel, ibs in arch_specs:
        for rb in ibs.ratchet_baseline:
            r_src, r_dst = _res_id(rb.source), _res_id(rb.target)
            if r_src and r_dst:
                ratchet_map[(r_src, r_dst)] = (int(rb.max_imports), str(rb.reason or ""), arch_rel)

    violations: list[dict[str, Any]] = []
    shrink_candidates: list[dict[str, Any]] = []
    handled_ratchet_pairs: set[tuple[str, str]] = set()

    def _evaluate_pair(
        src_id: str,
        dst_id: str,
        rule_name: str,
        spec_rel: str,
        ev_items: list[str],
    ) -> None:
        obs = len(ev_items)
        r_entry = ratchet_map.get((src_id, dst_id))
        handled_ratchet_pairs.add((src_id, dst_id))
        if r_entry is None:
            if obs > 0:
                violations.append(
                    {
                        "source": src_id,
                        "target": dst_id,
                        "rule": rule_name,
                        "observed_imports": obs,
                        "max_imports": 0,
                        "reason": "",
                        "spec_path": spec_rel,
                        "evidence": ev_items[:10],
                    }
                )
            return
        max_imp, r_reason, r_spec = r_entry
        if obs > max_imp:
            violations.append(
                {
                    "source": src_id,
                    "target": dst_id,
                    "rule": rule_name,
                    "observed_imports": obs,
                    "max_imports": max_imp,
                    "reason": r_reason,
                    "spec_path": r_spec,
                    "evidence": ev_items[:10],
                }
            )
        elif obs < max_imp:
            shrink_candidates.append(
                {
                    "source": src_id,
                    "target": dst_id,
                    "observed_imports": obs,
                    "max_imports": max_imp,
                    "suggested_max_imports": obs,
                    "action": "remove_entry" if obs == 0 else f"lower max_imports to {obs}",
                    "spec_path": r_spec,
                }
            )

    # (a) Component-level CMP-*.import_boundary rules
    for src_cmp, (cmp_rel, ib) in sorted(cmp_bounds.items()):
        allow_ids = {_res_id(x) for x in ib.allow if x}
        deny_ids = {_res_id(x) for x in ib.deny if x}
        allow_has_cmp = any(x.startswith("CMP-") for x in allow_ids)
        src_parent_cnt = cmp_to_cnt.get(src_cmp, "")
        grouped_cmp: dict[tuple[str, str], list[str]] = {}
        for r in coupling_rows:
            if r["source_entity_id"] != src_cmp:
                continue
            dst_ent = str(r["target_entity_id"] or "")
            dst_cnt = str(r["target_cnt_id"] or "")
            ev = f"{r['src_file']}:{r['line_start']} -> {r['target_ref']}"
            if dst_ent in deny_ids:
                grouped_cmp.setdefault((dst_ent, f"{src_cmp}.import_boundary.deny"), []).append(ev)
            elif dst_cnt and dst_cnt != src_parent_cnt and dst_cnt in deny_ids:
                grouped_cmp.setdefault((dst_cnt, f"{src_cmp}.import_boundary.deny"), []).append(ev)
            elif allow_ids:
                same_cnt = bool(src_parent_cnt and dst_cnt == src_parent_cnt)
                if same_cnt and not allow_has_cmp:
                    continue
                if dst_ent not in allow_ids and (not dst_cnt or dst_cnt not in allow_ids):
                    if dst_cnt and (src_cmp, dst_cnt) in ratchet_map and (src_cmp, dst_ent) not in ratchet_map:
                        target_key = dst_cnt
                    else:
                        target_key = dst_ent if (allow_has_cmp or not dst_cnt) else dst_cnt
                    grouped_cmp.setdefault((target_key, f"{src_cmp}.import_boundary.allow"), []).append(ev)
        for (dst_target, r_name), ev_list in sorted(grouped_cmp.items()):
            _evaluate_pair(src_cmp, dst_target, r_name, cmp_rel, ev_list)

    # (b), (c), (d) Cross-container rules: CNT-*.import_boundary -> ARCH-*.tier_rules -> enforce_declared_containers
    cnt_couplings: dict[tuple[str, str], list[str]] = {}
    for r in coupling_rows:
        s_cnt = str(r["source_cnt_id"] or "")
        d_cnt = str(r["target_cnt_id"] or "")
        if s_cnt.startswith("CNT-") and d_cnt.startswith("CNT-") and s_cnt != d_cnt:
            cnt_couplings.setdefault((s_cnt, d_cnt), []).append(
                f"{r['src_file']}:{r['line_start']} -> {r['target_ref']}"
            )

    candidate_cnt_pairs = sorted(
        set(cnt_couplings) | {(s, t) for (s, t) in ratchet_map if s.startswith("CNT-") and t.startswith("CNT-")}
    )
    for s_cnt, d_cnt in candidate_cnt_pairs:
        violated_rule = ""
        violated_spec = ""
        if s_cnt in cnt_bounds:
            c_rel, c_ib = cnt_bounds[s_cnt]
            c_allow = {_res_id(x) for x in c_ib.allow if x}
            c_deny = {_res_id(x) for x in c_ib.deny if x}
            if d_cnt in c_deny:
                violated_rule = f"{s_cnt}.import_boundary.deny"
                violated_spec = c_rel
            elif c_allow and d_cnt not in c_allow:
                violated_rule = f"{s_cnt}.import_boundary.allow"
                violated_spec = c_rel

        if not violated_rule:
            s_tier = cnt_tiers.get(s_cnt, "")
            d_tier = cnt_tiers.get(d_cnt, "")
            for arch_id, arch_rel, ibs in arch_specs:
                if s_tier and d_tier and s_tier in ibs.tier_rules:
                    t_rule = ibs.tier_rules[s_tier]
                    if d_tier in t_rule.deny:
                        violated_rule = f"{arch_id}.tier_rules.{s_tier}.deny({d_tier})"
                        violated_spec = arch_rel
                        break
                    if t_rule.allow and d_tier not in t_rule.allow:
                        violated_rule = f"{arch_id}.tier_rules.{s_tier}.allow({d_tier})"
                        violated_spec = arch_rel
                        break
                if ibs.enforce_declared_containers and (s_cnt, d_cnt) not in declared_ctr_pairs:
                    violated_rule = f"{arch_id}.enforce_declared_containers"
                    violated_spec = arch_rel
                    break

        if violated_rule:
            _evaluate_pair(s_cnt, d_cnt, violated_rule, violated_spec, cnt_couplings.get((s_cnt, d_cnt), []))

    # Emit ratchet_shrink_candidates for any remaining ratchet_baseline entries with fewer observed couplings
    for (r_src, r_dst), (max_imp, _r_reason, r_spec) in sorted(ratchet_map.items()):
        if (r_src, r_dst) in handled_ratchet_pairs:
            continue
        if r_src.startswith("CMP-"):
            obs_rem = sum(
                1
                for r in coupling_rows
                if r["source_entity_id"] == r_src and (r["target_entity_id"] == r_dst or r["target_cnt_id"] == r_dst)
            )
        else:
            obs_rem = len(cnt_couplings.get((r_src, r_dst), []))
        if obs_rem < max_imp:
            shrink_candidates.append(
                {
                    "source": r_src,
                    "target": r_dst,
                    "observed_imports": obs_rem,
                    "max_imports": max_imp,
                    "suggested_max_imports": obs_rem,
                    "action": "remove_entry" if obs_rem == 0 else f"lower max_imports to {obs_rem}",
                    "spec_path": r_spec,
                }
            )

    return violations, shrink_candidates
