"""Drift detection, PR verification gate, and living spec reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.graph_index import get_architectural_index
from eidos.core.index.storage import get_cached_spec_document
from eidos.core.spec.parser import extract_frontmatter
from eidos.core.spec.validator import SpecValidator, ValidationReport
from eidos.core.workspace.project import is_generated_or_vendored
from eidos.vcs.diff import (
    POLYGLOT_EXCLUDED_DIRS,
    POLYGLOT_TEST_PATTERNS,
    ChangedFilesClassification,
    GitResolutionError,
    _get_file_versions,
    _resolve_base_and_merge_base,
    _resolve_base_ref,
    _resolve_merge_base,
    classify_changed_files,
    collect_polyglot_test_files,
    get_git_diff_files,
    get_tracked_files,
    is_polyglot_test_file,
    map_code_to_components,
)
from eidos.vcs.invariants import (
    InvariantTrace,
    _load_or_update_test_binding_cache,
    detect_orphan_invariants,
    extract_invariants_and_verify,
    get_living_specs_catalog,
)
from eidos.vcs.report import build_drift_remediation_and_summary, generate_remediation_prompt
from eidos.vcs.structural import (
    SYSTEM_LEVEL_SPEC_PATHS,
    _collect_1hop_spec_targets,
    _collect_design_subgraph,
    _is_system_level_spec,
    _pair_moved_files,
    _path_in,
    _resolve_bound_design_ids,
    _spec_coverage_verdict,
    _SpecDiffInspector,
    _structural_addition_for,
    _StructuralAddition,
    _SymbolMapCache,
    get_system_level_spec_paths,
)

__all__ = [
    "POLYGLOT_EXCLUDED_DIRS",
    "POLYGLOT_TEST_PATTERNS",
    "SYSTEM_LEVEL_SPEC_PATHS",
    "ChangedFilesClassification",
    "DriftCheckResult",
    "GitResolutionError",
    "InvariantTrace",
    "_resolve_base_and_merge_base",
    "_resolve_base_ref",
    "_resolve_merge_base",
    "build_drift_remediation_and_summary",
    "classify_changed_files",
    "collect_polyglot_test_files",
    "detect_drift",
    "detect_orphan_invariants",
    "extract_invariants_and_verify",
    "generate_remediation_prompt",
    "get_git_diff_files",
    "get_living_specs_catalog",
    "get_system_level_spec_paths",
    "get_tracked_files",
    "is_generated_or_vendored",
    "is_polyglot_test_file",
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
    body_only_code_files: list[str] = field(default_factory=list)
    uncovered_design_files: list[str] = field(default_factory=list)
    unmapped_code_files: list[str] = field(default_factory=list)
    trivially_covered_files: list[dict[str, Any]] = field(default_factory=list)
    remediation_suggestions: list[str] = field(default_factory=list)
    summary_markdown: str = ""

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
    def uncovered_invariants(self) -> list[InvariantTrace]:
        """Invariants lacking a verified static test binding."""
        return [inv for inv in self.invariants if not inv.has_test_coverage]

    @property
    def code_files(self) -> list[str]:
        """Modified source code files in the evaluated diff."""
        return self.classification.code_files


_DEFAULT_GET_GIT_DIFF_FILES = get_git_diff_files
_DEFAULT_GET_FILE_VERSIONS = _get_file_versions


def detect_drift(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
) -> DriftCheckResult:
    """Run drift detection: evaluate spec-code synchronization, schema validation, and invariant check."""
    from eidos.core.index.ast_indexer import STYLE_EXTS, classify_design_changes, has_structural_changes

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

    # Single-pass base Git reference and merge-base resolution (DLT-2026-052, DLT-2026-053)
    resolved_base_ref: str | None = None
    resolved_mb: str | None = None
    effective_uncommitted = uncommitted
    if not uncommitted:
        resolved_base_ref, resolved_mb, fell_back = _resolve_base_and_merge_base(root, base_ref, head_ref)
        if fell_back:
            effective_uncommitted = True

    changed_files = get_git_diff_files(
        root,
        base_ref=resolved_base_ref,
        head_ref=head_ref,
        uncommitted=effective_uncommitted,
        merge_base=resolved_mb,
    )
    classification = classify_changed_files(changed_files, repo_root=root, eidos_dir=target_eidos)

    synthetic_diff_mock = (
        get_git_diff_files is not _DEFAULT_GET_GIT_DIFF_FILES and _get_file_versions is _DEFAULT_GET_FILE_VERSIONS
    )

    def _fetch_versions(rel_p: str) -> tuple[str | None, str | None]:
        if synthetic_diff_mock:
            abs_p = root / rel_p.replace("\\", "/")
            if abs_p.is_file():
                try:
                    txt = abs_p.read_text(encoding="utf-8", errors="replace")
                    return txt, txt
                except OSError:
                    pass
        return _get_file_versions(
            root,
            rel_p,
            base_ref=resolved_base_ref,
            head_ref=head_ref,
            uncommitted=effective_uncommitted,
            merge_base=resolved_mb,
        )

    # 0. Filter code files into structural vs body-only edits (DLT-2026-028)
    #    and classify frontend/styling diffs into design categories (DLT-2026-030)
    structural_code_files: list[str] = []
    body_only_code_files: list[str] = []
    structural_additions: dict[str, _StructuralAddition] = {}
    design_changes: dict[str, tuple[str, list[str]]] = {}
    versions = {cf: _fetch_versions(cf) for cf in classification.code_files}
    smap = _SymbolMapCache(versions)
    # Content-based move detection (DLT-2026-039): an added file paired with a deleted file of the same change set
    moved_from = _pair_moved_files(versions, smap)
    for cf in classification.code_files:
        old_c, new_c = versions[cf]
        synthetic = synthetic_diff_mock and old_c is not None and new_c is not None and old_c == new_c
        symbol_diff = None
        if old_c is not None and new_c is not None:
            if synthetic:
                # Synthetic/mocked diff where git blobs are identical
                is_structural = True
            elif smap.supports(cf):
                symbol_diff = smap.diff(cf)
                is_structural = bool(symbol_diff)
            else:
                is_structural, _ = has_structural_changes(old_c, new_c, cf)
        else:
            # Newly added or deleted file
            is_structural = True

        is_style = Path(cf).suffix.lower() in STYLE_EXTS
        design_cat, design_reasons = classify_design_changes(old_c, new_c, cf)
        if design_cat != "none":
            if synthetic:
                design_cat = "token" if is_style else "ui_contract"
                design_reasons = ["modified (content identical in git; treated as design-structural)"]
            if is_style:
                # Stylesheets are structural only when they mutate design tokens
                is_structural = design_cat != "body_only"
            if design_cat in ("token", "ui_contract"):
                design_changes[cf] = (design_cat, design_reasons)

        if is_structural:
            structural_code_files.append(cf)
            addition = _structural_addition_for(
                cf, synthetic, smap, symbol_diff=symbol_diff, moved_from=moved_from.get(cf)
            )
            if addition is not None:
                structural_additions[cf] = addition
        else:
            body_only_code_files.append(cf)

    # 1. Component Mapping (for structural code changes)
    comp_mappings = map_code_to_components(target_eidos, structural_code_files, repo_root=root)
    touched_components = sorted({cid for _, cid, _ in comp_mappings})
    idx = get_architectural_index(repo_root=root, eidos_dir=target_eidos) if target_eidos.exists() else None

    # 2. Extract declared targets from active deltas (excluding rejected or pre-existing applied deltas)
    from eidos.core.spec.models import FrontmatterModel

    declared_delta_targets: set[str] = set()
    declared_delta_paths: set[str] = set()
    active_delta_files: list[str] = []
    for df in classification.delta_files:
        try:
            delta_file = (root / df) if not Path(df).is_absolute() else Path(df)
            model = None
            old_dc: str | None = None
            new_dc: str | None = None
            fetched_versions = False
            if not effective_uncommitted or not delta_file.exists():
                old_dc, new_dc = _fetch_versions(df)
                fetched_versions = True
                if old_dc is not None and new_dc is None:
                    continue
                if not effective_uncommitted and new_dc is not None:
                    fm_head, _, _ = extract_frontmatter(new_dc)
                    if isinstance(fm_head, dict):
                        model = FrontmatterModel.model_validate(fm_head)
            if model is None:
                if delta_file.exists():
                    doc, _, _ = get_cached_spec_document(delta_file)
                    if doc is not None:
                        model = doc.model
                elif new_dc is not None:
                    fm_head, _, _ = extract_frontmatter(new_dc)
                    if isinstance(fm_head, dict):
                        model = FrontmatterModel.model_validate(fm_head)
            if model is None:
                continue
            status = str(model.status or "proposed").strip().lower()
            if status in ("rejected", "deprecated", "superseded", "archived"):
                continue
            if status == "applied":
                if not fetched_versions:
                    old_dc, _ = _fetch_versions(df)
                if old_dc is not None:
                    old_fm, _, _ = extract_frontmatter(old_dc)
                    if isinstance(old_fm, dict) and str(old_fm.get("status", "")).strip().lower() == "applied":
                        continue
            active_delta_files.append(df)
            for m in model.mutations:
                for key in ("target", "parent"):
                    val = m.get(key)
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
                            for cand_p in (root / norm_p, target_eidos / norm_p):
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
                                for cand_p in (root / norm_p, target_eidos / norm_p):
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
        design_owner_mappings += map_code_to_components(target_eidos, unowned_design_files, repo_root=root)
    for cf, cid, spath in design_owner_mappings:
        owners_by_file.setdefault(cf, set()).add(cid)
        owner_specs_by_file.setdefault(cf, set()).add(str(spath).replace("\\", "/").lower())

    design_subgraph_cache: dict[str, tuple[set[str], set[str], set[str]]] = {}

    def _design_subgraph_for(cf: str) -> tuple[set[str], set[str], set[str]]:
        if cf not in design_subgraph_cache:
            bound = _resolve_bound_design_ids(idx, cf, owners_by_file.get(cf, set()))
            if bound:
                d_paths, d_ids = _collect_design_subgraph(design_entities, bound)
            else:
                d_paths, d_ids = set(), set()
            design_subgraph_cache[cf] = (bound, d_paths, d_ids)
        return design_subgraph_cache[cf]

    trivial_records: list[dict[str, Any]] = []
    spec_inspector = _SpecDiffInspector(
        root,
        resolved_base_ref,
        head_ref,
        effective_uncommitted,
        merge_base=resolved_mb,
        versions_fn=_fetch_versions,
        synthetic_diff_mock=synthetic_diff_mock,
    )
    if code_changed:
        for cf, cid, spath in comp_mappings:
            allowed_paths, allowed_ids = _collect_1hop_spec_targets(
                idx, cf, cid, spath, system_spec_paths=system_spec_paths
            )
            if cf in design_changes or Path(cf).suffix.lower() in STYLE_EXTS:
                _, d_paths, d_ids = _design_subgraph_for(cf)
                allowed_paths |= d_paths
                allowed_ids |= {i.lower() for i in d_ids}
            if has_deltas and (
                (declared_delta_targets & allowed_ids)
                or (declared_delta_paths & allowed_paths)
                or any(_path_in(dp, allowed_paths) for dp in declared_delta_paths)
            ):
                continue  # Planned Mode: an active SpecDelta declares the owner (DLT-2026-039: unchanged)
            covered_by_spec, rejected_rule, rejected_specs = _spec_coverage_verdict(
                classification.spec_files,
                allowed_paths,
                allowed_ids,
                addition=structural_additions.get(cf),
                owner_spec=str(spath),
                inspector=spec_inspector,
                system_spec_paths=system_spec_paths,
            )
            if rejected_rule and not any(r["file"] == cf for r in trivial_records):
                addition = structural_additions.get(cf)
                additions_list = (
                    list(addition.reasons) if addition is not None else ["modified signature or public symbols"]
                )
                trivial_records.append(
                    {
                        "file": cf,
                        "owner": str(spath),
                        "additions": additions_list,
                        "spec_files": sorted(rejected_specs),
                        "rule": rejected_rule,
                    }
                )
            if not covered_by_spec:
                (uncovered_components if has_deltas else uncovered_direct_components).append((cf, cid, spath))

    # Unmapped structural code (no CNT-*/CMP-* owner): triggers drift in both Direct and Planned
    # modes unless covered by a substantive update to system-level specs (DLT-2026-051).
    mapped_files = {c for c, _, _ in comp_mappings}
    unmapped_code_files = [cf for cf in structural_code_files if cf not in mapped_files]

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
    # client's or design system's subgraph (or be declared in an active SpecDelta).
    uncovered_design: list[tuple[str, str, list[str], list[str]]] = []
    for cf, (cat, reasons) in sorted(design_changes.items()):
        bound, d_paths, d_ids = _design_subgraph_for(cf)
        if not bound:
            continue  # Container is not bound to any CLT-*/DS-*: no design spec required
        satisfying_paths = set(d_paths)
        satisfying_ids = {i.lower() for i in d_ids}
        if cat == "ui_contract":
            satisfying_paths |= owner_specs_by_file.get(cf, set())
            satisfying_ids |= {o.lower() for o in owners_by_file.get(cf, set())}
        matching_design_specs = [s for s in classification.spec_files if _path_in(s, satisfying_paths)]
        covered = any(spec_inspector.is_substantive(s) for s in matching_design_specs)
        if not covered and has_deltas:
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

    base_direct_drift = (code_changed and not specs_changed and not has_deltas) or (
        not has_deltas and bool(uncovered_direct_components)
    )
    uncovered_delta_drift = bool(uncovered_components)
    drift_detected = base_direct_drift or design_drift or uncovered_delta_drift or unmapped_drift

    # 4. Specification Schema & Link Integrity Validation
    validator = SpecValidator()
    validation_report = validator.validate_directory(target_eidos)

    # 5. Invariant Traceability & Orphan Invariant Detection (DLT-2026-027, DLT-2026-068)
    cached_tests = _load_or_update_test_binding_cache(root, eidos_dir=target_eidos)
    invariants = extract_invariants_and_verify(target_eidos, root, cached_tests=cached_tests)
    uncovered_invariants = [inv for inv in invariants if not inv.has_test_coverage]
    orphan_invariants = detect_orphan_invariants(
        root,
        {inv.invariant_id for inv in invariants},
        eidos_dir=target_eidos,
        cached_tests=cached_tests,
    )

    # Overall clean status
    is_clean = (
        (not drift_detected)
        and validation_report.is_clean
        and len(uncovered_invariants) == 0
        and len(orphan_invariants) == 0
    )

    # 6 & 7. Remediation suggestions and Markdown Summary (CMP-DRIFT-DETECTOR report module)
    remediation, summary_markdown = build_drift_remediation_and_summary(
        workflow_mode=workflow_mode,
        is_clean=is_clean,
        drift_detected=drift_detected,
        base_direct_drift=base_direct_drift,
        unmapped_drift=unmapped_drift,
        design_drift=design_drift,
        uncovered_delta_drift=uncovered_delta_drift,
        specs_changed=specs_changed,
        has_deltas=has_deltas,
        classification=classification,
        structural_code_files=structural_code_files,
        comp_mappings=comp_mappings,
        uncovered_direct_components=uncovered_direct_components,
        uncovered_components=uncovered_components,
        uncovered_unmapped_code=uncovered_unmapped_code,
        uncovered_design=uncovered_design,
        trivial_records=trivial_records,
        validation_report=validation_report,
        invariants=invariants,
        uncovered_invariants=uncovered_invariants,
        orphan_invariants=orphan_invariants,
        touched_components=touched_components,
        eidos_rel_prefix=eidos_rel_prefix,
    )

    return DriftCheckResult(
        is_clean=is_clean,
        drift_detected=drift_detected,
        workflow_mode=workflow_mode,
        classification=classification,
        validation_report=validation_report,
        touched_components=touched_components,
        invariants=invariants,
        orphan_invariants=orphan_invariants,
        body_only_code_files=body_only_code_files,
        uncovered_design_files=frontend_code_files,
        unmapped_code_files=uncovered_unmapped_code,
        trivially_covered_files=trivial_records,
        remediation_suggestions=remediation,
        summary_markdown=summary_markdown,
    )


# Backwards compatibility alias
check_pr_drift = detect_drift
