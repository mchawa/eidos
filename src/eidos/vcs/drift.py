"""Drift detection, PR verification gate, and living spec reconciliation."""

from __future__ import annotations

import contextlib
import inspect
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.context_globs import expand_brace_globs
from eidos.core.index.context_memories import (
    _extract_target_lists,
    _normalize_target_path,
    resolve_memory_freshness_batch,
)
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.index.storage import get_cached_spec_document
from eidos.core.spec.boundary_validator import evaluate_import_boundaries
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator, ValidationReport
from eidos.core.workspace.config import EidosConfig, load_repo_config_with_errors
from eidos.core.workspace.project import find_git_root, is_generated_or_vendored, match_path_pattern
from eidos.vcs.consumers import find_untouched_consumers
from eidos.vcs.diff import (
    POLYGLOT_TEST_PATTERNS,
    ChangedFilesClassification,
    GitResolutionError,
    _discover_container_package_paths,
    _get_file_versions,
    _get_files_versions_batch,
    _resolve_base_and_merge_base,
    _resolve_base_ref,
    _resolve_merge_base,
    batch_read_git_blobs,
    classify_changed_files,
    collect_polyglot_test_files,
    get_git_diff_files,
    map_code_to_components,
)
from eidos.vcs.invariants import (
    InvariantTrace,
    _load_or_update_test_binding_cache,
    detect_orphan_invariants,
    extract_invariants_and_verify,
    get_living_specs_catalog,
)
from eidos.vcs.report import build_drift_remediation, generate_remediation_prompt
from eidos.vcs.snapshot import (
    _has_eidos_onboarding as _has_eidos_onboarding,
)
from eidos.vcs.snapshot import (
    _load_head_commit_config,
    _materialize_head_snapshot,
    _remap_snapshot_str,
    _remap_validation_report,
    _validate_onboarding_governance,
)
from eidos.vcs.structural import (
    SYSTEM_LEVEL_SPEC_PATHS,
    _collect_1hop_spec_targets,
    _collect_design_subgraph,
    _filter_sibling_relocated_symbols,
    _is_system_level_spec,
    _pair_moved_files,
    _path_in,
    _remap_boundary_records,
    _resolve_bound_design_ids,
    _spec_coverage_verdict,
    _SpecDiffInspector,
    _structural_addition_for,
    _StructuralAddition,
    _SymbolMapCache,
    get_system_level_spec_paths,
)

__all__ = [
    "POLYGLOT_TEST_PATTERNS",
    "SYSTEM_LEVEL_SPEC_PATHS",
    "ChangedFilesClassification",
    "DriftCheckResult",
    "GitResolutionError",
    "InvariantTrace",
    "_resolve_base_and_merge_base",
    "_resolve_base_ref",
    "_resolve_merge_base",
    "batch_read_git_blobs",
    "build_drift_remediation",
    "classify_changed_files",
    "collect_polyglot_test_files",
    "detect_drift",
    "detect_orphan_invariants",
    "extract_invariants_and_verify",
    "find_affected_memories",
    "find_untouched_consumers",
    "generate_remediation_prompt",
    "get_git_diff_files",
    "get_living_specs_catalog",
    "get_system_level_spec_paths",
    "is_generated_or_vendored",
    "map_code_to_components",
]


@dataclass
class DriftCheckResult:
    """Consolidated outcome of the Eidos drift detection analysis."""

    is_clean: bool
    drift_detected: bool
    workflow_mode: str  # 'planned' (with SpecDelta) or 'direct' (without SpecDelta)
    classification: ChangedFilesClassification
    validation_report: ValidationReport
    touched_components: list[str] = field(default_factory=list)
    invariants: list[InvariantTrace] = field(default_factory=list)
    orphan_invariants: list[tuple[str, str]] = field(default_factory=list)
    structural_code_files: list[str] = field(default_factory=list)
    body_only_code_files: list[str] = field(default_factory=list)
    uncovered_design_files: list[str] = field(default_factory=list)
    unmapped_code_files: list[str] = field(default_factory=list)
    trivially_covered_files: list[dict[str, Any]] = field(default_factory=list)
    boundary_violations: list[dict[str, Any]] = field(default_factory=list)
    ratchet_shrink_candidates: list[dict[str, Any]] = field(default_factory=list)
    ready_to_apply_deltas: list[dict[str, Any]] = field(default_factory=list)
    untouched_consumers: list[dict[str, Any]] = field(default_factory=list)
    affected_memories: list[dict[str, Any]] = field(default_factory=list)
    remediation_suggestions: list[str] = field(default_factory=list)
    eval_config: EidosConfig | None = None
    eval_config_errors: list[str] = field(default_factory=list)

    @property
    def validation_passed(self) -> bool:
        """Whether living specification schema and reference validation passed."""
        return bool(self.validation_report.is_clean)

    @property
    def validation_errors(self) -> list[str]:
        """Flattened list of schema validation errors and broken reference messages."""
        errs: list[str] = []
        for path, messages in self.validation_report.schema_errors.items():
            for msg in messages:
                errs.append(f"{path}: {msg}")
        for ref in self.validation_report.broken_references:
            errs.append(f"Broken reference: {ref}")
        return errs

    @property
    def validation_warnings(self) -> list[str]:
        """Non-blocking advisory validation warnings from SpecValidator."""
        return list(self.validation_report.warnings)

    @property
    def uncovered_invariants(self) -> list[InvariantTrace]:
        """Invariants lacking a verified static test binding."""
        return [inv for inv in self.invariants if not inv.has_test_coverage]

    @property
    def code_files(self) -> list[str]:
        """Modified source code files in the evaluated diff."""
        return self.classification.code_files


_AFFECTED_MEMORY_FRESHNESS_ORDER: dict[str, int] = {
    "expired": 0,
    "stale": 1,
    "unverifiable": 2,
    "fresh": 3,
}


def find_affected_memories(
    *,
    eval_root: Path,
    eval_eidos: Path,
    changed_non_memory_paths: list[str],
    touched_components: list[str],
    idx: Any = None,
    catalog_ids: set[str] | None = None,
    git_repo_root: Path | None = None,
    head_ref: str = "HEAD",
    eval_config: EidosConfig | None = None,
    snap_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Deterministically discover active non-archived ``agent/memory`` entries affected by diff paths or touched components.

    Strictly non-blocking advisory signal: never affects ``is_clean``, ``drift_detected``,
    or ``--fail-on-warnings``, and stays isolated to ``eval_root`` / ``eval_eidos`` on commit-range runs.
    """
    mem_dir = eval_eidos / "memory"
    if not mem_dir.is_dir() or mem_dir.is_symlink():
        return []

    norm_changed = list(
        dict.fromkeys(_normalize_target_path(p) for p in changed_non_memory_paths if isinstance(p, str) and p.strip())
    )
    norm_changed = [p for p in norm_changed if p]
    touched_set = {str(c).strip().upper() for c in touched_components if c and str(c).strip()}
    if not norm_changed and not touched_set:
        return []

    conn = None
    eff_catalog_ids = set(catalog_ids) if catalog_ids is not None else set()
    if idx is not None:
        with contextlib.suppress(Exception):
            conn = idx._get_connection()
            eff_catalog_ids.update(str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall())
            # Expand leaf touched CMP-* components to their owning parent CNT-* container IDs
            # and resolve modified spec file paths to their entity IDs.
            if touched_set:
                touched_list = sorted(touched_set)
                for start in range(0, len(touched_list), 400):
                    batch = touched_list[start : start + 400]
                    ph = ",".join("?" for _ in batch)
                    for row in conn.execute(
                        f"SELECT json_extract(data_json, '$.container') AS parent_cnt FROM entities WHERE UPPER(id) IN ({ph});",
                        tuple(batch),
                    ).fetchall():
                        parent_cnt = str(row["parent_cnt"] or "").strip().upper()
                        if parent_cnt:
                            touched_set.add(parent_cnt)
            spec_changed = [p for p in norm_changed if p.lower().endswith(".adoc")]
            if spec_changed:
                for start in range(0, len(spec_changed), 400):
                    batch = spec_changed[start : start + 400]
                    ph = ",".join("?" for _ in batch)
                    for row in conn.execute(
                        f"SELECT id FROM entities WHERE replace(file_path, '\\\\', '/') IN ({ph}) AND type NOT IN ('agent/memory', 'delta/mutation', 'invariant');",
                        tuple(batch),
                    ).fetchall():
                        sid = str(row["id"] or "").strip().upper()
                        if sid:
                            touched_set.add(sid)

    mem_root = mem_dir.resolve()
    eval_root_res = eval_root.resolve()
    matched: list[tuple[str, str, dict[str, Any], list[str], list[str]]] = []

    for p in sorted(mem_dir.glob("*.adoc")):
        if p.is_symlink() or not p.is_file():
            continue
        if p.name.startswith("_") or p.name.lower() == "readme.adoc":
            continue
        p_res = p.resolve()
        if not p_res.is_relative_to(mem_root):
            continue
        try:
            doc = parse_spec_file(p)
        except OSError:
            continue
        if doc.doc_type != "agent/memory":
            continue
        fm = doc.frontmatter or {}
        if str(fm.get("status") or "active").strip().lower() != "active":
            continue

        t_entities, t_paths = _extract_target_lists(fm)
        if not t_entities and not t_paths:
            continue

        hit_entities = sorted(dict.fromkeys(e.upper() for e in t_entities if e.upper() in touched_set))
        hit_paths: list[str] = []
        if norm_changed and t_paths:
            for cf in norm_changed:
                for raw_pat in t_paths:
                    norm_pat = _normalize_target_path(raw_pat)
                    if not norm_pat:
                        continue
                    if any(match_path_pattern(cf, var) > 0 for var in expand_brace_globs(norm_pat)):
                        if cf not in hit_paths:
                            hit_paths.append(cf)
                        break

        if not hit_entities and not hit_paths:
            continue

        mem_id = str(doc.doc_id or fm.get("id") or p.stem)
        try:
            rel_path = p_res.relative_to(eval_root_res).as_posix()
        except ValueError:
            rel_path = _remap_snapshot_str(p.as_posix(), snap_root)
        matched.append((mem_id, rel_path, fm, hit_entities, sorted(hit_paths)))

    if not matched:
        return []

    freshness_map = resolve_memory_freshness_batch(
        eval_root_res,
        [(mid, fm) for mid, _rp, fm, _he, _hp in matched],
        eff_catalog_ids,
        conn=conn,
        db_path=None if snap_root is not None else getattr(idx, "db_path", None),
        commit_drift_threshold=(eval_config.memory.commit_drift_threshold if eval_config is not None else None),
        eidos_dir=eval_eidos,
        git_repo_root=git_repo_root,
        head_ref=head_ref,
    )

    items: list[dict[str, Any]] = []
    for mem_id, rel_path, fm, hit_entities, hit_paths in matched:
        fresh, reason = freshness_map.get(mem_id, ("fresh", ""))
        items.append(
            {
                "id": mem_id,
                "title": str(fm.get("title") or mem_id),
                "category": str(fm.get("category") or "feedback"),
                "summary": str(fm.get("summary") or fm.get("title") or mem_id).strip(),
                "freshness": fresh,
                "freshness_reason": _remap_snapshot_str(reason, snap_root),
                "relative_path": rel_path,
                "matched_entities": hit_entities,
                "matched_paths": hit_paths,
            }
        )

    items.sort(key=lambda item: (_AFFECTED_MEMORY_FRESHNESS_ORDER.get(str(item["freshness"]), 9), str(item["id"])))
    return items


def detect_drift(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
    strict: bool | None = None,
    *,
    repo_config: EidosConfig | None = None,
) -> DriftCheckResult:
    """Run drift detection: evaluate spec-code synchronization, schema validation, and invariant check."""
    import eidos.core.index.graph_index as gi
    from eidos.core.index.ast_diff import STYLE_EXTS, classify_design_changes, has_structural_changes
    from eidos.core.lifecycle.delta_engine import is_delta_ready_to_apply, verify_delta_mutations
    from eidos.core.workspace.project import normalize_rel_path

    root = repo_root.resolve()
    if eidos_dir is None:
        target_eidos = (root / "eidos").resolve()
    elif not eidos_dir.is_absolute():
        target_eidos = (root / eidos_dir).resolve()
    else:
        target_eidos = eidos_dir.resolve()

    try:
        eidos_rel_prefix = str(target_eidos.relative_to(root)).replace("\\", "/").strip("/") or "eidos"
    except ValueError:
        eidos_rel_prefix = target_eidos.name or "eidos"
    system_spec_paths = get_system_level_spec_paths(eidos_rel_prefix)

    git_root: Path | None = find_git_root(root)
    if repo_config is not None:
        pre_config = repo_config
    elif uncommitted or git_root is None:
        pre_config, _ = load_repo_config_with_errors(eidos_dir=target_eidos, repo_root=root)
    else:
        pre_config = _load_head_commit_config(git_root, root, eidos_rel_prefix, head_ref)

    # Single-pass base Git reference and merge-base resolution (DLT-2026-052, DLT-2026-053)
    resolved_base_ref: str | None = None
    resolved_mb: str | None = None
    effective_uncommitted = uncommitted
    if not uncommitted:
        resolved_base_ref, resolved_mb, fell_back = _resolve_base_and_merge_base(
            root,
            base_ref,
            head_ref,
            repo_config=pre_config,
            eidos_dir=target_eidos,
        )
        if fell_back:
            effective_uncommitted = True

    snap_tmpdir: tempfile.TemporaryDirectory[str] | None = None
    snap_root: Path | None = None
    git_sub_prefix = ""
    eval_root = root
    eval_eidos = target_eidos
    if not effective_uncommitted and git_root is not None:
        if root != git_root and root.is_relative_to(git_root):
            git_sub_prefix = str(root.relative_to(git_root)).replace("\\", "/").strip("/")
        snap_tmpdir = _materialize_head_snapshot(
            git_root=git_root,
            repo_root=root,
            target_eidos=target_eidos,
            eidos_rel_prefix=eidos_rel_prefix,
            head_ref=head_ref,
        )
        if snap_tmpdir is not None:
            snap_root = Path(snap_tmpdir.name).resolve()
            eval_root = snap_root
            eval_eidos = (snap_root / eidos_rel_prefix).resolve()

    eval_config_errors: list[str] = []
    if repo_config is not None:
        eval_config = repo_config
    else:
        eval_config, eval_config_errors = load_repo_config_with_errors(eidos_dir=eval_eidos, repo_root=eval_root)
    effective_strict = strict if strict is not None else eval_config.drift.strict

    try:
        diff_sig_params = inspect.signature(get_git_diff_files).parameters
        if "repo_config" in diff_sig_params and "eidos_dir" in diff_sig_params:
            diff_kwargs: dict[str, Any] = {
                "base_ref": resolved_base_ref,
                "head_ref": head_ref,
                "uncommitted": effective_uncommitted,
                "merge_base": resolved_mb,
                "repo_config": eval_config,
                "eidos_dir": eval_eidos,
            }
            if "snapshot_root" in diff_sig_params:
                diff_kwargs["snapshot_root"] = eval_root
            changed_files = get_git_diff_files(root, **diff_kwargs)
        else:
            changed_files = get_git_diff_files(
                root,
                base_ref=resolved_base_ref,
                head_ref=head_ref,
                uncommitted=effective_uncommitted,
                merge_base=resolved_mb,
            )
        head_pkg_paths = _discover_container_package_paths(eval_eidos) if eval_eidos.exists() else None
        classification = classify_changed_files(
            changed_files,
            repo_root=eval_root,
            eidos_dir=eval_eidos,
            package_paths=head_pkg_paths,
            repo_config=eval_config,
        )

        prefetched_versions: dict[str, tuple[str | None, str | None]] = {}
        batch_candidates = list(
            dict.fromkeys(
                [
                    *classification.code_files,
                    *classification.spec_files,
                    *classification.delta_files,
                    *classification.onboarding_files,
                ]
            )
        )
        if batch_candidates:
            prefetched_versions = _get_files_versions_batch(
                root,
                batch_candidates,
                base_ref=resolved_base_ref,
                head_ref=head_ref,
                uncommitted=effective_uncommitted,
                merge_base=resolved_mb,
            )

        def _fetch_versions(rel_p: str) -> tuple[str | None, str | None]:
            if rel_p in prefetched_versions:
                return prefetched_versions[rel_p]
            norm_rel_p = rel_p.replace("\\", "/")
            if norm_rel_p in prefetched_versions:
                return prefetched_versions[norm_rel_p]
            res_ver = _get_file_versions(
                root,
                rel_p,
                base_ref=resolved_base_ref,
                head_ref=head_ref,
                uncommitted=effective_uncommitted,
                merge_base=resolved_mb,
            )
            prefetched_versions[rel_p] = res_ver
            return res_ver

        # 0. Filter code files into structural vs body-only edits (DLT-2026-028)
        #    and classify frontend/styling diffs into design categories (DLT-2026-030)
        structural_code_files: list[str] = []
        body_only_code_files: list[str] = []
        structural_additions: dict[str, _StructuralAddition] = {}
        symbol_diffs: dict[str, Any] = {}
        design_changes: dict[str, tuple[str, list[str]]] = {}
        versions = {cf: _fetch_versions(cf) for cf in classification.code_files}
        smap = _SymbolMapCache(versions)
        # Content-based move detection (DLT-2026-039): an added file paired with a deleted file of the same change set
        moved_from = _pair_moved_files(versions, smap)
        for cf in classification.code_files:
            old_c, new_c = versions[cf]
            symbol_diff = None
            if old_c is not None and new_c is not None:
                if smap.supports(cf):
                    symbol_diff = smap.diff(cf)
                    symbol_diffs[cf] = symbol_diff
                    is_structural = bool(symbol_diff)
                else:
                    is_structural, _ = has_structural_changes(old_c, new_c, cf)
            else:
                # Newly added or deleted file
                is_structural = True

            is_style = Path(cf).suffix.lower() in STYLE_EXTS
            design_cat, design_reasons = classify_design_changes(old_c, new_c, cf)
            if design_cat != "none":
                if is_style:
                    # Stylesheets are structural only when they mutate design tokens
                    is_structural = design_cat != "body_only"
                if design_cat in ("token", "ui_contract"):
                    design_changes[cf] = (design_cat, design_reasons)

            if is_structural:
                structural_code_files.append(cf)
                addition = _structural_addition_for(cf, smap, symbol_diff=symbol_diff, moved_from=moved_from.get(cf))
                if addition is not None:
                    structural_additions[cf] = addition
            else:
                body_only_code_files.append(cf)

        # 1. Component Mapping across all modified code files (structural + body-only)
        raw_comp_mappings = map_code_to_components(eval_eidos, classification.code_files, repo_root=eval_root)
        all_comp_mappings = [
            (cf, cid, _remap_snapshot_str(str(spath), snap_root)) for cf, cid, spath in raw_comp_mappings
        ]
        touched_components = sorted({cid for _, cid, _ in all_comp_mappings})
        structural_set = set(structural_code_files)
        comp_mappings = [m for m in all_comp_mappings if m[0] in structural_set]
        body_comp_mappings = [m for m in all_comp_mappings if m[0] not in structural_set]
        idx = get_architectural_index(repo_root=eval_root, eidos_dir=eval_eidos) if eval_eidos.exists() else None
        if idx is not None and snap_root is not None:
            idx.sync(auto_semantic=False)

        # 2. Extract declared targets from active deltas (excluding rejected or pre-existing applied deltas)
        from eidos.core.spec.models import FrontmatterModel

        preexisting_applied_ids: set[str] = set()
        for df in classification.delta_files:
            old_pre, _ = _fetch_versions(df)
            if old_pre is not None:
                old_fm_pre, _, _ = extract_frontmatter(old_pre)
                if isinstance(old_fm_pre, dict) and str(old_fm_pre.get("status", "")).strip().lower() == "applied":
                    old_id = str(old_fm_pre.get("id", "")).strip().lower()
                    if old_id:
                        preexisting_applied_ids.add(old_id)

        declared_delta_targets: set[str] = set()
        declared_delta_paths: set[str] = set()
        active_delta_files: list[str] = []
        pre_apply_delta_files: list[str] = []
        newly_applied_deltas: list[tuple[str, str, str | None]] = []
        open_deltas_in_diff: list[tuple[str, str, str, str | None]] = []
        for df in classification.delta_files:
            try:
                delta_rel = (
                    Path(df).resolve().relative_to(root).as_posix()
                    if Path(df).is_absolute() and Path(df).resolve().is_relative_to(root)
                    else str(df).replace("\\", "/")
                )
                delta_file = (
                    (eval_root / delta_rel) if not Path(df).is_absolute() or snap_root is not None else Path(df)
                )
                model = None
                old_dc, new_dc = _fetch_versions(df)
                if old_dc is not None and new_dc is None:
                    continue
                if new_dc is not None:
                    fm_head, _, _ = extract_frontmatter(new_dc)
                    if isinstance(fm_head, dict):
                        with contextlib.suppress(ValueError, TypeError):
                            model = FrontmatterModel.model_validate(fm_head)
                if model is None and delta_file.exists():
                    doc, _, _ = get_cached_spec_document(delta_file)
                    if doc is not None:
                        model = doc.model
                if model is None or (model.type and model.type != "delta/mutation"):
                    continue
                status = str(model.status or "proposed").strip().lower()
                if status in ("rejected", "deprecated", "superseded", "archived"):
                    continue
                doc_id_str = str(model.id or "").strip()
                if status == "applied":
                    if old_dc is not None:
                        old_fm, _, _ = extract_frontmatter(old_dc)
                        if isinstance(old_fm, dict) and str(old_fm.get("status", "")).strip().lower() == "applied":
                            continue
                    if doc_id_str and doc_id_str.lower() in preexisting_applied_ids:
                        continue
                    active_delta_files.append(df)
                    newly_applied_deltas.append((delta_rel, doc_id_str or delta_rel, new_dc))
                    # Applied deltas must fulfill living spec updates and pass _spec_coverage_verdict (no continue bypass)
                    continue

                active_delta_files.append(df)
                pre_apply_delta_files.append(df)
                if (
                    status in ("proposed", "in_review", "approved")
                    and model.mutations
                    and "archive" not in Path(delta_rel).parts
                ):
                    open_deltas_in_diff.append((delta_rel, doc_id_str or delta_rel, status, new_dc))
                for m in model.mutations:
                    val = m.target
                    if not val:
                        continue
                    val_str = str(val).strip()
                    if not val_str:
                        continue
                    declared_delta_targets.add(val_str.lower())
                    if (
                        val_str.lower().endswith((".adoc", ".json", ".yaml", ".yml"))
                        or "/" in val_str
                        or "\\" in val_str
                    ):
                        norm_p = val_str.replace("\\", "/").strip("/")
                        declared_delta_paths.add(norm_p.lower())
                        pref_low = eidos_rel_prefix.lower().strip("/")
                        if pref_low and not norm_p.lower().startswith(f"{pref_low}/"):
                            declared_delta_paths.add(f"{pref_low}/{norm_p.lower()}")
                        found_target_doc = False
                        if effective_uncommitted:
                            for cand_p in (eval_root / norm_p, eval_eidos / norm_p):
                                if cand_p.is_file() and cand_p.suffix.lower() == ".adoc":
                                    t_doc, _, _ = get_cached_spec_document(cand_p)
                                    if t_doc is not None and t_doc.doc_id:
                                        declared_delta_targets.add(t_doc.doc_id.strip().lower())
                                        found_target_doc = True
                                    break
                        else:
                            rel_cands = [norm_p]
                            if pref_low and not norm_p.lower().startswith(f"{pref_low}/"):
                                rel_cands.append(f"{pref_low}/{norm_p}")
                            for rel_cand in rel_cands:
                                _, new_tc = _fetch_versions(rel_cand)
                                if new_tc is not None:
                                    fm_tc, _, _ = extract_frontmatter(new_tc)
                                    if isinstance(fm_tc, dict) and fm_tc.get("id"):
                                        declared_delta_targets.add(str(fm_tc["id"]).strip().lower())
                                        found_target_doc = True
                                        break
                            if not found_target_doc:
                                for cand_p in (eval_root / norm_p, eval_eidos / norm_p):
                                    if cand_p.is_file() and cand_p.suffix.lower() == ".adoc":
                                        t_doc, _, _ = get_cached_spec_document(cand_p)
                                        if t_doc is not None and t_doc.doc_id:
                                            declared_delta_targets.add(t_doc.doc_id.strip().lower())
                                            found_target_doc = True
                                        break
                        if not found_target_doc and idx is not None:
                            for ent in idx.get_entities():
                                efp = str(ent.get("file_path", "")).replace("\\", "/").lower()
                                eid = str(ent.get("id", "")).strip().lower()
                                if eid and (efp == norm_p.lower() or efp.endswith("/" + norm_p.lower())):
                                    declared_delta_targets.add(eid)
                                    break
            except OSError:
                pass

        has_deltas = len(active_delta_files) > 0
        has_pre_apply_deltas = len(pre_apply_delta_files) > 0
        workflow_mode = "planned" if has_deltas else "direct"

        # 3. Spec-Code Drift Evaluation (Scoped 1-Hop Neighborhood + Structural Changes)
        code_changed = len(structural_code_files) > 0
        specs_changed = len(classification.spec_files) > 0

        uncovered_components: list[tuple[str, str, str]] = []
        uncovered_direct_components: list[tuple[str, str, str]] = []

        # Graph-scoped design subgraph resolution (DLT-2026-030)
        design_entities: list[dict[str, Any]] = []
        if idx is not None:
            design_entities = [e for e in idx.get_entities() if str(e.get("type", "")).startswith("design/")]
        owners_by_file: dict[str, set[str]] = {}
        owner_specs_by_file: dict[str, set[str]] = {}
        design_owner_mappings = list(comp_mappings)
        unowned_design_files = [f for f in design_changes if f not in {c for c, _, _ in comp_mappings}]
        if unowned_design_files:
            extra_design_mappings = map_code_to_components(eval_eidos, unowned_design_files, repo_root=eval_root)
            design_owner_mappings += [
                (cf, cid, _remap_snapshot_str(str(sp), snap_root)) for cf, cid, sp in extra_design_mappings
            ]
        for cf, cid, spath in design_owner_mappings:
            owners_by_file.setdefault(cf, set()).add(cid)
            owner_specs_by_file.setdefault(cf, set()).add(str(spath).replace("\\", "/").lower())

        design_subgraph_cache: dict[str, tuple[set[str], set[str], set[str]]] = {}

        def _design_subgraph_for(cf: str) -> tuple[set[str], set[str], set[str]]:
            if cf not in design_subgraph_cache:
                bound = _resolve_bound_design_ids(idx, cf, owners_by_file.get(cf, set()))
                if bound:
                    d_paths, d_ids = _collect_design_subgraph(design_entities, bound)
                    d_paths = {_remap_snapshot_str(dp, snap_root) for dp in d_paths}
                else:
                    d_paths, d_ids = set(), set()
                design_subgraph_cache[cf] = (bound, d_paths, d_ids)
            return design_subgraph_cache[cf]

        trivial_records: list[dict[str, Any]] = []
        spec_inspector = _SpecDiffInspector(
            eval_root,
            resolved_base_ref,
            head_ref,
            effective_uncommitted,
            merge_base=resolved_mb,
            versions_fn=_fetch_versions,
        )
        if code_changed:
            for cf, cid, spath in comp_mappings:
                allowed_paths, allowed_ids = _collect_1hop_spec_targets(
                    idx, cf, cid, spath, system_spec_paths=system_spec_paths
                )
                allowed_paths = {_remap_snapshot_str(ap, snap_root) for ap in allowed_paths}
                if cf in design_changes or Path(cf).suffix.lower() in STYLE_EXTS:
                    _, d_paths, d_ids = _design_subgraph_for(cf)
                    allowed_paths |= d_paths
                    allowed_ids |= {i.lower() for i in d_ids}
                if has_pre_apply_deltas and (
                    (declared_delta_targets & allowed_ids)
                    or (declared_delta_paths & allowed_paths)
                    or any(_path_in(dp, allowed_paths) for dp in declared_delta_paths)
                ):
                    continue  # Pre-apply Planned Mode: a proposed/in_review/approved SpecDelta declares the owner
                addition = structural_additions.get(cf)
                effective_removed = (
                    _filter_sibling_relocated_symbols(
                        addition.removed_symbol_names,
                        cf=cf,
                        cid=cid,
                        all_comp_mappings=all_comp_mappings,
                        smap=smap,
                        idx=idx,
                    )
                    if addition is not None and addition.removed_symbol_names
                    else None
                )
                covered_by_spec, rejected_rule, rejected_specs = _spec_coverage_verdict(
                    classification.spec_files,
                    allowed_paths,
                    allowed_ids,
                    addition=addition,
                    removed_symbol_names_override=effective_removed,
                    owner_spec=str(spath),
                    inspector=spec_inspector,
                    system_spec_paths=system_spec_paths,
                )
                if rejected_rule and not any(r["file"] == cf for r in trivial_records):
                    if rejected_rule == "stale_removed_symbol" and effective_removed:
                        additions_list = [f"removed '{s}'" for s in effective_removed]
                    else:
                        additions_list = (
                            list(addition.reasons) if addition is not None else ["modified signature or public symbols"]
                        )
                    trivial_records.append(
                        {
                            "file": cf,
                            "owner": _remap_snapshot_str(str(spath), snap_root),
                            "additions": additions_list,
                            "spec_files": sorted(_remap_snapshot_str(rs, snap_root) for rs in rejected_specs),
                            "rule": rejected_rule,
                        }
                    )
                if not covered_by_spec:
                    (uncovered_components if has_pre_apply_deltas else uncovered_direct_components).append(
                        (cf, cid, spath)
                    )

        if effective_strict and body_only_code_files and not classification.test_files:
            for cf, cid, spath in body_comp_mappings:
                allowed_paths, allowed_ids = _collect_1hop_spec_targets(
                    idx, cf, cid, spath, system_spec_paths=system_spec_paths
                )
                allowed_paths = {_remap_snapshot_str(ap, snap_root) for ap in allowed_paths}
                if has_pre_apply_deltas and (
                    (declared_delta_targets & allowed_ids)
                    or (declared_delta_paths & allowed_paths)
                    or any(_path_in(dp, allowed_paths) for dp in declared_delta_paths)
                ):
                    continue
                covered_by_spec, rejected_rule, rejected_specs = _spec_coverage_verdict(
                    classification.spec_files,
                    allowed_paths,
                    allowed_ids,
                    addition=None,
                    owner_spec=str(spath),
                    inspector=spec_inspector,
                    system_spec_paths=system_spec_paths,
                )
                if rejected_rule and not any(r["file"] == cf for r in trivial_records):
                    trivial_records.append(
                        {
                            "file": cf,
                            "owner": _remap_snapshot_str(str(spath), snap_root),
                            "additions": ["modified implementation body (--strict)"],
                            "spec_files": sorted(_remap_snapshot_str(rs, snap_root) for rs in rejected_specs),
                            "rule": rejected_rule,
                        }
                    )
                if not covered_by_spec:
                    (uncovered_components if has_pre_apply_deltas else uncovered_direct_components).append(
                        (cf, cid, spath)
                    )

        # Unmapped structural code (no CNT-*/CMP-* owner): triggers drift in both Direct and Planned
        # modes unless covered by a substantive update to system-level specs (DLT-2026-051).
        mapped_files = {c for c, _, _ in all_comp_mappings}
        unmapped_candidates = (
            [*structural_code_files, *(body_only_code_files if not classification.test_files else ())]
            if effective_strict
            else structural_code_files
        )
        unmapped_code_files = [cf for cf in unmapped_candidates if cf not in mapped_files]

        changed_sys_specs = [s for s in classification.spec_files if _is_system_level_spec(s, system_spec_paths)]
        system_spec_substantively_updated = any(spec_inspector.is_substantive(s) for s in changed_sys_specs)
        arch_or_system_substantively_updated = system_spec_substantively_updated or any(
            (
                "/architecture/containers/" in f"/{s.replace(chr(92), '/').lower().strip('/')}"
                or "/architecture/components/" in f"/{s.replace(chr(92), '/').lower().strip('/')}"
            )
            and spec_inspector.is_substantive(s)
            for s in classification.spec_files
        )
        uncovered_unmapped_code: list[str] = []
        if not system_spec_substantively_updated:
            for ucf in unmapped_code_files:
                old_uc, new_uc = versions.get(ucf, (None, None))
                is_deleted = old_uc is not None and new_uc is None
                if is_deleted and arch_or_system_substantively_updated:
                    continue
                uncovered_unmapped_code.append(ucf)
                if changed_sys_specs and not any(r["file"] == ucf for r in trivial_records):
                    addition = structural_additions.get(ucf)
                    additions_list = list(addition.reasons) if addition is not None else ["modified unmapped code file"]
                    trivial_records.append(
                        {
                            "file": ucf,
                            "owner": changed_sys_specs[0],
                            "additions": additions_list,
                            "spec_files": sorted(changed_sys_specs),
                            "rule": "trivial_spec_diff",
                        }
                    )
        unmapped_drift = bool(uncovered_unmapped_code)

        # Graph-scoped design drift (DLT-2026-030): token / UI-contract changes must update the bound
        # client's or design system's subgraph (or be declared in an active pre-apply SpecDelta).
        uncovered_design: list[tuple[str, str, list[str], list[str]]] = []
        for cf, (cat, reasons) in sorted(design_changes.items()):
            bound, d_paths, d_ids = _design_subgraph_for(cf)
            if not bound:
                continue  # Container is not bound to any CLT-*/DS-*: no design spec required
            satisfying_paths = set(d_paths)
            satisfying_ids = {i.lower() for i in d_ids}
            matching_design_specs = [s for s in classification.spec_files if _path_in(s, satisfying_paths)]
            covered = any(spec_inspector.is_substantive(s) for s in matching_design_specs)
            if not covered and has_pre_apply_deltas:
                covered = bool(
                    (declared_delta_targets & satisfying_ids)
                    or (declared_delta_paths & satisfying_paths)
                    or any(_path_in(dp, satisfying_paths) for dp in declared_delta_paths)
                )
            if not covered:
                uncovered_design.append((cf, cat, reasons, sorted(bound)))
                if matching_design_specs and not any(r["file"] == cf for r in trivial_records):
                    trivial_records.append(
                        {
                            "file": cf,
                            "owner": matching_design_specs[0],
                            "additions": list(reasons) if reasons else [f"modified {cat}"],
                            "spec_files": sorted(matching_design_specs),
                            "rule": "trivial_spec_diff",
                        }
                    )
        design_drift = bool(uncovered_design)
        frontend_code_files = [cf for cf, _, _, _ in uncovered_design]

        # 4. Specification Schema & Link Integrity Validation + Applied Delta Verification + Onboarding Governance
        validator = SpecValidator()
        validation_report = validator.validate_directory(
            eval_eidos,
            check_import_boundaries=False,
            repo_config=repo_config,
        )
        _remap_validation_report(validation_report, snap_root)

        boundary_check_sev = eval_config.drift.checks.import_boundaries
        if boundary_check_sev == "off" or not eval_eidos.exists():
            boundary_violations = []
            ratchet_shrink_candidates = []
            boundary_drift = False
        else:
            raw_boundary_violations, raw_shrink_candidates = evaluate_import_boundaries(
                eval_eidos,
                repo_root=eval_root,
                documents=validation_report.documents,
            )
            remapped_boundary_violations = _remap_boundary_records(
                raw_boundary_violations,
                lambda s: _remap_snapshot_str(s, snap_root),
            )
            ratchet_shrink_candidates = _remap_boundary_records(
                raw_shrink_candidates,
                lambda s: _remap_snapshot_str(s, snap_root),
            )
            if boundary_check_sev == "warn":
                for v in remapped_boundary_violations:
                    ev_list = v.get("evidence") or []
                    ev_sample = f" (e.g. {ev_list[0]})" if ev_list else ""
                    max_imp = int(v.get("max_imports") or 0)
                    obs_imp = int(v.get("observed_imports") or 0)
                    limit_str = (
                        f"observed {obs_imp} > ratchet max_imports {max_imp}"
                        if max_imp > 0
                        else f"{obs_imp} forbidden coupling(s)"
                    )
                    msg = (
                        f"Import boundary violation [{v['rule']}]: {v['source']} -> {v['target']} "
                        f"({limit_str}){ev_sample}"
                    )
                    if msg not in validation_report.warnings:
                        validation_report.warnings.append(msg)
                boundary_violations = []
                boundary_drift = False
            else:
                boundary_violations = remapped_boundary_violations
                boundary_drift = bool(boundary_violations)

        def _verify_delta_in_eval(
            df_rel: str,
            delta_id_str: str,
            new_dc_val: str | None,
        ) -> tuple[bool, str, Any]:
            delta_eval_path = eval_root / df_rel
            temp_written = False
            try:
                if not delta_eval_path.is_file() and new_dc_val is not None and snap_root is not None:
                    delta_eval_path.parent.mkdir(parents=True, exist_ok=True)
                    delta_eval_path.write_text(new_dc_val, encoding="utf-8")
                    temp_written = True
                lookup_ref = str(delta_eval_path) if delta_eval_path.is_file() else delta_id_str
                return verify_delta_mutations(
                    eval_eidos,
                    lookup_ref,
                    git_repo_root=git_root,
                    snapshot_root=snap_root,
                    git_sub_prefix=git_sub_prefix,
                )
            finally:
                if temp_written:
                    with contextlib.suppress(OSError):
                        delta_eval_path.unlink(missing_ok=True)

        from eidos.core.lifecycle.delta_verifier import _evaluate_declarative_postcondition

        _semver_re = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
        _ver_fail_re = re.compile(r"^frontmatter\.version expected '([^']+)', got '([^']+)'$")

        def _semver_key(raw_v: str) -> tuple[int, int, int] | None:
            m = _semver_re.match((raw_v or "").strip())
            return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None

        # Canonical ID map and spec index from validation_report so stacked deltas targeting
        # either entity IDs or spec paths normalize identically and re-check non-version expects.
        canon_id_map: dict[str, str] = {}
        specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]] = {}
        pref_low_v = eidos_rel_prefix.lower().strip("/")
        for vdoc in validation_report.documents:
            vid = str(vdoc.doc_id or "").strip()
            if not vid:
                continue
            vid_up = vid.upper()
            canon_id_map[vid_up] = vid
            specs_by_id[vid] = (vdoc.file_path, str(vdoc.doc_type or ""), dict(vdoc.frontmatter or {}))
            vfp = vdoc.file_path
            try:
                vrel = vfp.resolve().relative_to(eval_root.resolve()).as_posix()
            except ValueError:
                vrel = _remap_snapshot_str(vfp.as_posix(), snap_root).replace("\\", "/")
            vrel_low = normalize_rel_path(vrel).lower()
            if vrel_low:
                canon_id_map[vrel_low] = vid
                if pref_low_v and vrel_low.startswith(f"{pref_low_v}/"):
                    canon_id_map[vrel_low[len(pref_low_v) + 1 :]] = vid

        def _canon_delta_target(raw_target: str) -> str:
            t = str(raw_target or "").strip()
            if not t:
                return ""
            if t.upper() in canon_id_map:
                return canon_id_map[t.upper()]
            norm_t = normalize_rel_path(t).lower()
            if norm_t in canon_id_map:
                return canon_id_map[norm_t]
            if pref_low_v and f"{pref_low_v}/{norm_t}" in canon_id_map:
                return canon_id_map[f"{pref_low_v}/{norm_t}"]
            return t

        def _load_delta_declared_mutations(df_rel_val: str, new_dc_val: str | None) -> list[dict[str, Any]]:
            fm_d = None
            if new_dc_val is not None:
                fm_d, _, _ = extract_frontmatter(new_dc_val)
            elif (eval_root / df_rel_val).is_file():
                with contextlib.suppress(OSError):
                    fm_d, _, _ = extract_frontmatter((eval_root / df_rel_val).read_text(encoding="utf-8"))
            if isinstance(fm_d, dict) and isinstance(fm_d.get("mutations"), list):
                return [m for m in fm_d["mutations"] if isinstance(m, dict)]
            return []

        applied_verifications: list[tuple[str, str, str | None, bool, str, Any, list[dict[str, Any]]]] = [
            (
                df_rel,
                delta_id_str,
                new_dc_val,
                *_verify_delta_in_eval(df_rel, delta_id_str, new_dc_val),
                _load_delta_declared_mutations(df_rel, new_dc_val),
            )
            for df_rel, delta_id_str, new_dc_val in newly_applied_deltas
        ]
        # Collect verified target versions per verified individual mutation across all newly applied deltas
        # so multi-delta chains (D1 -> D2 -> D3) and mixed path/ID targets resolve cleanly.
        verified_target_versions: dict[str, set[str]] = {}
        if len(applied_verifications) > 1:
            for _df_rel, _did, _new_dc_val, _ok_ver, _vmsg, ver_rep, decl_muts in applied_verifications:
                if ver_rep is None:
                    continue
                for m_idx, vm in enumerate(ver_rep.mutations):
                    if not vm.verified or m_idx >= len(decl_muts):
                        continue
                    m_decl = decl_muts[m_idx]
                    exp_obj = m_decl.get("expect")
                    exp_fm = exp_obj.get("frontmatter") if isinstance(exp_obj, dict) else None
                    if isinstance(exp_fm, dict) and isinstance(exp_fm.get("version"), str):
                        canon_t = _canon_delta_target(str(m_decl.get("target") or vm.target))
                        if canon_t:
                            verified_target_versions.setdefault(canon_t.upper(), set()).add(exp_fm["version"].strip())

        applied_delta_drift = False
        for df_rel, _delta_id_str, _new_dc_val, ok_ver, ver_msg, ver_rep, decl_muts in applied_verifications:
            if not ok_ver or (ver_rep is not None and not ver_rep.is_verified):
                if ver_rep is not None:
                    failed_muts = [(idx_m, m) for idx_m, m in enumerate(ver_rep.mutations) if not m.verified]
                    unresolved_reasons: list[tuple[Any, str]] = []
                    for idx_m, fm_item in failed_muts:
                        reason_raw = (fm_item.failure_reason or "").strip()
                        m_vf = _ver_fail_re.match(reason_raw)
                        waived = False
                        recheck_err = ""
                        if m_vf and verified_target_versions and idx_m < len(decl_muts):
                            exp_v, act_v = m_vf.group(1).strip(), m_vf.group(2).strip()
                            exp_t, act_t = _semver_key(exp_v), _semver_key(act_v)
                            canon_t = _canon_delta_target(str(fm_item.target))
                            if (
                                exp_t is not None
                                and act_t is not None
                                and act_t > exp_t
                                and act_v in verified_target_versions.get(canon_t.upper(), set())
                            ):
                                # Re-verify every remaining expect.frontmatter & expect.body key on this mutation
                                # with only frontmatter.version updated to the verified superseding version.
                                m_decl_copy = dict(decl_muts[idx_m])
                                orig_exp = m_decl_copy.get("expect")
                                if isinstance(orig_exp, dict):
                                    exp_copy = dict(orig_exp)
                                    orig_fm = exp_copy.get("frontmatter")
                                    if isinstance(orig_fm, dict):
                                        fm_copy = dict(orig_fm)
                                        fm_copy["version"] = act_v
                                        exp_copy["frontmatter"] = fm_copy
                                    m_decl_copy["expect"] = exp_copy
                                recheck_diags: list[str] = []
                                t_file = specs_by_id[canon_t][0] if canon_t in specs_by_id else None
                                recheck_ok = _evaluate_declarative_postcondition(
                                    m_decl_copy,
                                    canon_t,
                                    specs_by_id,
                                    {},
                                    target_file=t_file,
                                    diagnostics=recheck_diags,
                                )
                                if recheck_ok is True:
                                    waived = True
                                elif recheck_diags:
                                    recheck_err = recheck_diags[0]
                        if not waived:
                            reason = recheck_err or fm_item.failure_reason or fm_item.details or fm_item.status
                            unresolved_reasons.append((fm_item, reason))
                    if failed_muts and not unresolved_reasons:
                        continue
                    err_bucket = validation_report.schema_errors.setdefault(df_rel, [])
                    if unresolved_reasons:
                        for fm_item, reason in unresolved_reasons:
                            err_bucket.append(
                                f"Unfulfilled SpecDelta mutation [{fm_item.action} {fm_item.target}] in '{ver_rep.delta_id}': {_remap_snapshot_str(reason, snap_root)}"
                            )
                    else:
                        err_bucket.append(_remap_snapshot_str(ver_msg, snap_root))
                else:
                    err_bucket = validation_report.schema_errors.setdefault(df_rel, [])
                    err_bucket.append(_remap_snapshot_str(ver_msg, snap_root))
                applied_delta_drift = True

        # Detect diff-touched and spec-overlapping open SpecDeltas already 100% verified on disk (advisory)
        schema_error_paths = tuple(validation_report.schema_errors.keys())
        candidate_open_deltas: list[tuple[str, str, str, str | None]] = []
        seen_open_delta_keys: set[str] = set()
        for df_rel, delta_id_str, delta_status, new_dc_val in open_deltas_in_diff:
            norm_path_key = normalize_rel_path(df_rel).lower()
            id_key = delta_id_str.strip().lower()
            if norm_path_key in seen_open_delta_keys or (id_key and id_key in seen_open_delta_keys):
                continue
            seen_open_delta_keys.add(norm_path_key)
            if id_key:
                seen_open_delta_keys.add(id_key)
            candidate_open_deltas.append((df_rel, delta_id_str, delta_status, new_dc_val))

        if classification.spec_files and eval_eidos.exists():
            pref_low = eidos_rel_prefix.lower().strip("/")
            touched_spec_keys: set[str] = set()
            for sf in classification.spec_files:
                norm_sf = normalize_rel_path(str(sf)).lower()
                if not norm_sf:
                    continue
                touched_spec_keys.add(norm_sf)
                if pref_low and norm_sf.startswith(f"{pref_low}/"):
                    touched_spec_keys.add(norm_sf[len(pref_low) + 1 :])
                old_sc, new_sc = _fetch_versions(sf)
                for raw_sc in (new_sc, old_sc):
                    if not raw_sc:
                        continue
                    fm_sc, _, _ = extract_frontmatter(raw_sc)
                    if not isinstance(fm_sc, dict):
                        continue
                    sc_id = str(fm_sc.get("id") or "").strip().lower()
                    if sc_id:
                        touched_spec_keys.add(sc_id)
                    invs_raw = fm_sc.get("invariants")
                    invs_sc = list(invs_raw) if isinstance(invs_raw, list) else []
                    tech_sc = fm_sc.get("technical")
                    if isinstance(tech_sc, dict) and isinstance(tech_sc.get("invariants"), list):
                        invs_sc.extend(tech_sc["invariants"])
                    for inv_item in invs_sc:
                        if isinstance(inv_item, dict) and inv_item.get("id"):
                            touched_spec_keys.add(str(inv_item["id"]).strip().lower())

            for doc in validation_report.documents:
                if doc.doc_type != "delta/mutation":
                    continue
                if "archive" in doc.file_path.parts:
                    continue
                d_status = str(doc.frontmatter.get("status") or "proposed").strip().lower()
                if d_status not in ("proposed", "in_review", "approved"):
                    continue
                d_muts = doc.model.mutations if doc.model is not None else []
                if not d_muts:
                    continue
                doc_fp = doc.file_path
                if not doc_fp.is_absolute():
                    d_rel = normalize_rel_path(doc_fp.as_posix())
                else:
                    resolved_fp = doc_fp.resolve()
                    if resolved_fp.is_relative_to(eval_root):
                        d_rel = resolved_fp.relative_to(eval_root).as_posix()
                    elif resolved_fp.is_relative_to(root):
                        d_rel = resolved_fp.relative_to(root).as_posix()
                    else:
                        d_rel = _remap_snapshot_str(str(doc_fp), snap_root).replace("\\", "/")
                d_id = str(doc.doc_id or d_rel).strip()
                norm_path_key = normalize_rel_path(d_rel).lower()
                id_key = d_id.lower()
                if norm_path_key in seen_open_delta_keys or (id_key and id_key in seen_open_delta_keys):
                    continue
                overlaps = False
                for m in d_muts:
                    t_norm = normalize_rel_path(str(m.target or "")).lower()
                    if not t_norm:
                        continue
                    if t_norm in touched_spec_keys or (pref_low and f"{pref_low}/{t_norm}" in touched_spec_keys):
                        overlaps = True
                        break
                if overlaps:
                    seen_open_delta_keys.add(norm_path_key)
                    if id_key:
                        seen_open_delta_keys.add(id_key)
                    candidate_open_deltas.append((d_rel, d_id, d_status, None))

        ready_to_apply_deltas: list[dict[str, Any]] = []
        for df_rel, delta_id_str, delta_status, new_dc_val in candidate_open_deltas:
            delta_eval_path = eval_root / df_rel
            temp_written = False
            try:
                if not delta_eval_path.is_file() and new_dc_val is not None and snap_root is not None:
                    delta_eval_path.parent.mkdir(parents=True, exist_ok=True)
                    delta_eval_path.write_text(new_dc_val, encoding="utf-8")
                    temp_written = True
                lookup_ref = str(delta_eval_path) if delta_eval_path.is_file() else delta_id_str
                ready_item = is_delta_ready_to_apply(
                    eval_eidos,
                    lookup_ref,
                    delta_rel_path=df_rel,
                    delta_status=delta_status,
                    schema_error_paths=schema_error_paths,
                    git_repo_root=git_root,
                    snapshot_root=snap_root,
                    git_sub_prefix=git_sub_prefix,
                )
            finally:
                if temp_written:
                    with contextlib.suppress(OSError):
                        delta_eval_path.unlink(missing_ok=True)
            if ready_item is not None:
                ready_to_apply_deltas.append(ready_item)
        ready_to_apply_deltas.sort(key=lambda item: (str(item["id"]), str(item["path"])))

        onboarding_drift = _validate_onboarding_governance(
            onboarding_files=classification.onboarding_files,
            repo_root=root,
            eval_root=eval_root,
            effective_uncommitted=effective_uncommitted,
            fetch_versions_fn=_fetch_versions,
            validation_report=validation_report,
        )

        strict_body_changed = effective_strict and bool(body_only_code_files) and not classification.test_files
        base_direct_drift = (
            (code_changed or strict_body_changed) and not specs_changed and not has_pre_apply_deltas
        ) or (not has_pre_apply_deltas and bool(uncovered_direct_components))
        uncovered_delta_drift = bool(uncovered_components)
        drift_detected = (
            base_direct_drift
            or design_drift
            or uncovered_delta_drift
            or unmapped_drift
            or applied_delta_drift
            or onboarding_drift
            or boundary_drift
        )

        # 5. Invariant Traceability & Orphan Invariant Detection (DLT-2026-027, DLT-2026-068)
        cached_tests = _load_or_update_test_binding_cache(
            eval_root,
            eidos_dir=eval_eidos,
            repo_config=eval_config,
        )
        invariants = extract_invariants_and_verify(
            eval_eidos,
            eval_root,
            cached_tests=cached_tests,
            repo_config=eval_config,
        )
        for inv in invariants:
            inv.spec_path = _remap_snapshot_str(inv.spec_path, snap_root)
        uncovered_invariants = [inv for inv in invariants if not inv.has_test_coverage]
        raw_orphans = detect_orphan_invariants(
            eval_root,
            {inv.invariant_id for inv in invariants},
            eidos_dir=eval_eidos,
            cached_tests=cached_tests,
            repo_config=eval_config,
        )
        orphan_invariants = [(oid, _remap_snapshot_str(loc, snap_root)) for oid, loc in raw_orphans]

        # Overall clean status
        is_clean = (
            (not drift_detected)
            and validation_report.is_clean
            and len(uncovered_invariants) == 0
            and len(orphan_invariants) == 0
        )

        # 5b. Advisory Untouched Sibling Consumers (DLT-2026-177)
        untouched_consumers = find_untouched_consumers(
            idx=idx,
            code_files=classification.code_files,
            versions=versions,
            smap=smap,
            symbol_diffs=symbol_diffs,
            all_changed_files=changed_files,
        )

        # 5c. Advisory Diff-Affected Team Operational Memories (DLT-2026-182; strictly non-blocking)
        mem_dir_prefix_low = f"{eidos_rel_prefix.lower().strip('/')}/memory/"
        non_memory_changed_paths = [
            p
            for p in (
                *classification.code_files,
                *classification.test_files,
                *classification.spec_files,
                *classification.doc_files,
                *classification.other_files,
            )
            if not (
                normalize_rel_path(str(p)).lower().startswith(mem_dir_prefix_low)
                or normalize_rel_path(str(p)).lower() == mem_dir_prefix_low.rstrip("/")
            )
        ]
        doc_catalog_ids = {str(d.doc_id) for d in validation_report.documents if d.doc_id}
        affected_memories = find_affected_memories(
            eval_root=eval_root,
            eval_eidos=eval_eidos,
            changed_non_memory_paths=non_memory_changed_paths,
            touched_components=touched_components,
            idx=idx,
            catalog_ids=doc_catalog_ids or None,
            git_repo_root=root,
            head_ref=head_ref,
            eval_config=eval_config,
            snap_root=snap_root,
        )

        # 6. Remediation suggestions (CMP-DRIFT-DETECTOR report module)
        remediation = build_drift_remediation(
            base_direct_drift=base_direct_drift,
            unmapped_drift=unmapped_drift,
            design_drift=design_drift,
            uncovered_delta_drift=uncovered_delta_drift,
            classification=classification,
            structural_code_files=structural_code_files,
            body_only_code_files=body_only_code_files,
            strict=effective_strict,
            comp_mappings=comp_mappings or body_comp_mappings,
            uncovered_direct_components=uncovered_direct_components,
            uncovered_components=uncovered_components,
            uncovered_unmapped_code=uncovered_unmapped_code,
            uncovered_design=uncovered_design,
            trivial_records=trivial_records,
            validation_report=validation_report,
            uncovered_invariants=uncovered_invariants,
            orphan_invariants=orphan_invariants,
            eidos_rel_prefix=eidos_rel_prefix,
            boundary_violations=boundary_violations,
        )

        all_eval_cfg_errors = list(eval_config_errors)
        for err_path, errs in validation_report.schema_errors.items():
            if err_path == "eidos.yaml" or err_path.endswith("/eidos.yaml"):
                for em in errs:
                    if em not in all_eval_cfg_errors:
                        all_eval_cfg_errors.append(em)

        return DriftCheckResult(
            is_clean=is_clean,
            drift_detected=drift_detected,
            workflow_mode=workflow_mode,
            classification=classification,
            validation_report=validation_report,
            touched_components=touched_components,
            invariants=invariants,
            orphan_invariants=orphan_invariants,
            structural_code_files=structural_code_files,
            body_only_code_files=body_only_code_files,
            uncovered_design_files=frontend_code_files,
            unmapped_code_files=uncovered_unmapped_code,
            trivially_covered_files=trivial_records,
            boundary_violations=boundary_violations,
            ratchet_shrink_candidates=ratchet_shrink_candidates,
            ready_to_apply_deltas=ready_to_apply_deltas,
            untouched_consumers=untouched_consumers,
            affected_memories=affected_memories,
            remediation_suggestions=remediation,
            eval_config=eval_config,
            eval_config_errors=all_eval_cfg_errors,
        )
    finally:
        if snap_root is not None:
            snap_key = eval_eidos.resolve()
            with gi._INDEX_LOCK:
                snap_idx = gi._INDEX_INSTANCES.pop(snap_key, None)
                if gi._INDEX_INSTANCE is not None and (
                    gi._INDEX_INSTANCE is snap_idx or gi._INDEX_INSTANCE.root == snap_root
                ):
                    snap_idx = snap_idx or gi._INDEX_INSTANCE
                    gi._INDEX_INSTANCE = None
            if snap_idx is not None:
                snap_idx.close()
        if snap_tmpdir is not None:
            snap_tmpdir.cleanup()
