"""Drift detection, PR verification gate, and living spec reconciliation."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.graph_index import get_architectural_index
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator, ValidationReport
from eidos.core.workspace.project import is_generated_or_vendored
from eidos.vcs.diff import (
    POLYGLOT_EXCLUDED_DIRS,
    POLYGLOT_TEST_PATTERNS,
    ChangedFilesClassification,
    GitResolutionError,
    _get_file_versions,
    _resolve_base_ref,
    _resolve_merge_base,
    classify_changed_files,
    collect_polyglot_test_files,
    get_git_diff_files,
    is_polyglot_test_file,
    map_code_to_components,
)
from eidos.vcs.invariants import (
    InvariantTrace,
    detect_orphan_invariants,
    extract_invariants_and_verify,
    get_living_specs_catalog,
)
from eidos.vcs.structural import (
    SYSTEM_LEVEL_SPEC_PATHS,
    ReconcileSuggestion,
    _collect_1hop_spec_targets,
    _collect_design_subgraph,
    _pair_moved_files,
    _path_in,
    _resolve_bound_design_ids,
    _spec_coverage_verdict,
    _SpecDiffInspector,
    _structural_addition_for,
    _StructuralAddition,
    _SymbolMapCache,
)

__all__ = [
    "POLYGLOT_EXCLUDED_DIRS",
    "POLYGLOT_TEST_PATTERNS",
    "ChangedFilesClassification",
    "DriftCheckResult",
    "GitResolutionError",
    "InvariantTrace",
    "ReconcileResult",
    "ReconcileSuggestion",
    "classify_changed_files",
    "collect_polyglot_test_files",
    "detect_drift",
    "detect_orphan_invariants",
    "extract_invariants_and_verify",
    "get_git_diff_files",
    "get_living_specs_catalog",
    "is_generated_or_vendored",
    "is_polyglot_test_file",
    "map_code_to_components",
    "reconcile_specs",
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


@dataclass
class ReconcileResult:
    """Result of analyzing code changes for living spec reconciliation."""

    changed_code_files: list[str]
    suggestions: list[ReconcileSuggestion]
    ai_prompt: str
    catalog: dict[str, list[dict]] = field(default_factory=dict)


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
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    # Single-pass base Git reference and merge-base resolution (DLT-2026-052, DLT-2026-053)
    resolved_base_ref = None
    if not uncommitted:
        if base_ref:
            resolved_base_ref = _resolve_base_ref(root, base_ref)
            _resolve_merge_base(root, resolved_base_ref, head_ref, fail_loudly=True)
        else:
            for candidate in ["origin/main", "main", "origin/master", "master", "HEAD~1"]:
                check = subprocess.run(
                    ["git", "rev-parse", "--verify", candidate],
                    cwd=root,
                    capture_output=True,
                    check=False,
                )
                if check.returncode == 0:
                    mb = _resolve_merge_base(root, candidate, head_ref, fail_loudly=False)
                    if mb:
                        resolved_base_ref = candidate
                        break

    changed_files = get_git_diff_files(root, base_ref=resolved_base_ref, head_ref=head_ref, uncommitted=uncommitted)
    classification = classify_changed_files(changed_files, repo_root=root, eidos_dir=target_eidos)

    # 0. Filter code files into structural vs body-only edits (DLT-2026-028)
    #    and classify frontend/styling diffs into design categories (DLT-2026-030)
    structural_code_files: list[str] = []
    body_only_code_files: list[str] = []
    structural_additions: dict[str, _StructuralAddition] = {}
    design_changes: dict[str, tuple[str, list[str]]] = {}
    versions = {
        cf: _get_file_versions(
            root,
            cf,
            base_ref=resolved_base_ref,
            head_ref=head_ref,
            uncommitted=uncommitted,
        )
        for cf in classification.code_files
    }
    smap = _SymbolMapCache(versions)
    # Content-based move detection (DLT-2026-039): an added file paired with a deleted file of the same change set
    moved_from = _pair_moved_files(versions, smap)
    for cf in classification.code_files:
        old_c, new_c = versions[cf]
        synthetic = old_c is not None and new_c is not None and old_c == new_c
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
    declared_delta_targets: set[str] = set()
    active_delta_files: list[str] = []
    for df in classification.delta_files:
        try:
            delta_file = (root / df) if not Path(df).is_absolute() else Path(df)
            if not delta_file.exists():
                continue
            doc = parse_spec_file(delta_file)
            fm = doc.frontmatter or {}
            status = str(fm.get("status", "proposed")).strip().lower()
            if status in ("rejected", "deprecated", "superseded", "archived"):
                continue
            if status == "applied":
                old_dc, new_dc = _get_file_versions(
                    root,
                    df,
                    base_ref=resolved_base_ref,
                    head_ref=head_ref,
                    uncommitted=uncommitted,
                )
                if old_dc is not None and new_dc is not None and old_dc != new_dc:
                    old_fm, _, _ = extract_frontmatter(old_dc)
                    if isinstance(old_fm, dict) and str(old_fm.get("status", "")).strip().lower() == "applied":
                        continue
            active_delta_files.append(df)
            for m in fm.get("mutations", []) or []:
                if isinstance(m, dict):
                    for key in (
                        "target_component",
                        "target_container",
                        "target_architecture",
                        "target_capability",
                        "target_domain",
                        "target_policy",
                        "target_screen",
                        "target_ui_component",
                        "target_design_system",
                        "target_client",
                        "target_flow",
                        "target_contract",
                        "target_persona",
                    ):
                        val = m.get(key)
                        if val:
                            declared_delta_targets.add(str(val).lower())
            aff = fm.get("affects", {}) or {}
            if isinstance(aff, dict):
                for sub in (
                    "containers",
                    "components",
                    "domains",
                    "capabilities",
                    "contracts",
                    "design_systems",
                    "clients",
                    "flows",
                    "screens",
                    "ui_components",
                ):
                    for val in aff.get(sub, []) or []:
                        declared_delta_targets.add(str(val).lower())
            br = fm.get("blast_radius", {}) or {}
            if isinstance(br, dict):
                for sub in (
                    "components",
                    "containers",
                    "domains",
                    "capabilities",
                    "screens",
                    "design_systems",
                    "impacted_components",
                    "impacted_containers",
                    "impacted_design_systems",
                    "impacted_screens",
                ):
                    for val in br.get(sub, []) or []:
                        declared_delta_targets.add(str(val).lower())
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
    spec_inspector = _SpecDiffInspector(root, resolved_base_ref, head_ref, uncommitted)
    if code_changed:
        for cf, cid, spath in comp_mappings:
            allowed_paths, allowed_ids = _collect_1hop_spec_targets(idx, cf, cid, spath)
            if cf in design_changes or Path(cf).suffix.lower() in STYLE_EXTS:
                _, d_paths, d_ids = _design_subgraph_for(cf)
                allowed_paths |= d_paths
                allowed_ids |= {i.lower() for i in d_ids}
            if has_deltas and (declared_delta_targets & allowed_ids):
                continue  # Planned Mode: an active SpecDelta declares the owner (DLT-2026-039: unchanged)
            covered_by_spec, rejected_rule, rejected_specs = _spec_coverage_verdict(
                classification.spec_files,
                allowed_paths,
                allowed_ids,
                addition=structural_additions.get(cf),
                owner_spec=str(spath),
                inspector=spec_inspector,
            )
            if rejected_rule and not any(r["file"] == cf for r in trivial_records):
                addition = structural_additions.get(cf)
                additions_list = list(addition.reasons) if addition is not None else ["modified signature or public symbols"]
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

    system_spec_substantively_updated = any(
        any(s.replace("\\", "/").lower().endswith(sys_p.lower()) for sys_p in SYSTEM_LEVEL_SPEC_PATHS)
        and spec_inspector.is_substantive(s)
        for s in classification.spec_files
    )
    uncovered_unmapped_code = [] if system_spec_substantively_updated else list(unmapped_code_files)
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
        covered = any(
            _path_in(s, satisfying_paths) and spec_inspector.is_substantive(s)
            for s in classification.spec_files
        )
        if not covered and has_deltas:
            covered = bool(declared_delta_targets & satisfying_ids)
        if not covered:
            uncovered_design.append((cf, cat, reasons, sorted(bound)))
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

    # 5. Invariant Traceability & Orphan Invariant Detection (DLT-2026-027)
    invariants = extract_invariants_and_verify(target_eidos, root)
    uncovered_invariants = [inv for inv in invariants if not inv.has_test_coverage]
    orphan_invariants = detect_orphan_invariants(
        root,
        {inv.invariant_id for inv in invariants},
        eidos_dir=target_eidos,
    )

    # 6. Remediation suggestions
    remediation: list[str] = []
    if base_direct_drift:
        target_list = uncovered_direct_components or comp_mappings
        remediation.append(
            f"Drift Detected: {len(structural_code_files)} structural code file(s) changed without corresponding living specification updates in eidos/ (within 1-hop graph neighborhood)."
        )
        for cf, cid, spath in target_list[:5]:
            remediation.append(f"• '{cf}' relates to {cid} -> Update '{spath}' (or a 1-hop linked capability/contract)")
        if not target_list:
            remediation.append(
                "• Run 'eidos drift' or ask your AI agent to update corresponding living specifications in eidos/."
            )

    if unmapped_drift:
        remediation.append(
            f"Unmapped Code Drift Detected: {len(uncovered_unmapped_code)} structural code file(s) are not owned by any container or component."
        )
        for ucf in uncovered_unmapped_code[:5]:
            remediation.append(
                f"• '{ucf}' -> declare ownership via 'package_path'/'source_path' in eidos/architecture/ (or substantively update eidos/system.adoc)."
            )

    if design_drift:
        remediation.append(
            f"Design System Drift Detected: {len(uncovered_design)} frontend/styling file(s) changed design tokens or UI contracts without updating their bound design subgraph."
        )
        for fcf, cat, reasons, bound in uncovered_design[:5]:
            kind = "design token" if cat == "token" else "UI contract"
            detail = f" ({reasons[0]})" if reasons else ""
            remediation.append(
                f"• {kind} change in '{fcf}'{detail} -> update specs bound to {', '.join(bound)} (tokens.json, UIC-*, SCR-*, FLOW-*, or the CLT-*/DS-* spec)."
            )

    if uncovered_delta_drift:
        for cf, cid, spath in uncovered_components[:5]:
            remediation.append(
                f"Uncovered Delta Drift: '{cf}' relates to {cid}, which is NOT declared in active SpecDelta mutations. Declare it in the SpecDelta or update '{spath}'."
            )

    trivial_edit_records = [r for r in trivial_records if r["rule"] == "trivial_spec_diff"]
    if trivial_edit_records:
        remediation.append(
            f"Trivial Spec Edit: {len(trivial_edit_records)} structural addition(s) are only covered by whitespace/comment-only spec edits — document the additions in the owning specification."
        )
        for rec in trivial_edit_records[:5]:
            remediation.append(
                f"• '{rec['file']}' ({'; '.join(rec['additions'][:3])}) -> document it in {rec['owner']} (spec edits seen: {', '.join(rec['spec_files'])})"
            )
    unanchored_records = [r for r in trivial_records if r["rule"] == "missing_anchor"]
    if unanchored_records:
        remediation.append(
            f"Missing Spec Anchor: {len(unanchored_records)} new source file(s) lie outside any declared source_path/owned_path and are not referenced by the owning spec edit."
        )
        for rec in unanchored_records[:5]:
            remediation.append(
                f"• '{rec['file']}' -> mention it (path, name, or a new public symbol) in {rec['owner']} or declare it under 'components'/'source_path'."
            )

    if not validation_report.is_clean:
        remediation.append(
            f"Specification Validation Failed: {len(validation_report.schema_errors)} error(s), {len(validation_report.broken_references)} broken link(s)."
        )

    if uncovered_invariants:
        remediation.append(
            f"Uncovered Invariants: {len(uncovered_invariants)} invariant(s) lack verified static test bindings (file::func + # INV-* marker)."
        )
        for uinv in uncovered_invariants[:5]:
            remediation.append(f"• {uinv.invariant_id} ({uinv.spec_path}) -> test_binding='{uinv.test_binding}'")

    if orphan_invariants:
        remediation.append(
            f"Orphan Invariant References: {len(orphan_invariants)} test reference(s) point to non-existent INV-* IDs."
        )
        for oid, loc in orphan_invariants[:5]:
            remediation.append(
                f"• {oid} referenced in '{loc}' is not declared in any capability, policy, or component."
            )

    # Overall clean status
    is_clean = (
        (not drift_detected)
        and validation_report.is_clean
        and len(uncovered_invariants) == 0
        and len(orphan_invariants) == 0
    )

    # 7. Generate Markdown Summary for reports and PR comments
    summary_lines = [
        "## 🧭 Eidos Living Specification Drift Detection Summary",
        "",
    ]

    mode_badge = (
        "📋 Mode: **Planned (SpecDelta)**" if workflow_mode == "planned" else "⚡ Mode: **Direct (Co-Evolution)**"
    )
    summary_lines.append(f"{mode_badge}\n")

    if is_clean:
        summary_lines.append("### ✅ Status: PASSED (Specifications and Code are Synchronized)")
    elif drift_detected:
        summary_lines.append("### ⚠️ Status: DRIFT DETECTED (Code modified without living spec updates)")
    else:
        summary_lines.append("### ❌ Status: VALIDATION FAILED")

    summary_lines.extend(
        [
            "",
            "| Metric | Count | Status |",
            "|---|---|---|",
            f"| Modified Code Files | `{len(classification.code_files)}` (`{len(structural_code_files)}` structural) | {'⚠️ Needs Spec Update' if drift_detected else '✓'} |",
            f"| Modified Living Specs | `{len(classification.spec_files)}` | {'✓ In Sync' if specs_changed else '—'} |",
            f"| Modified SpecDeltas | `{len(classification.delta_files)}` | {'✓ Planned' if has_deltas else '— (Direct Mode)'} |",
            f"| Modified Test Files | `{len(classification.test_files)}` | {'✓' if classification.test_files else '—'} |",
            f"| Living Specs Validated | `{validation_report.valid_docs}/{validation_report.total_docs}` | {'✓ 100% Green' if validation_report.is_clean else '✗ Errors'} |",
            f"| Invariants Verified | `{len(invariants) - len(uncovered_invariants)}/{len(invariants)}` | {'✓ 100% Covered' if not uncovered_invariants else f'✗ {len(uncovered_invariants)} Uncovered'} |",
            "",
        ]
    )

    if touched_components:
        summary_lines.append(f"**Impacted Components:** `{', '.join(touched_components)}`\n")

    if classification.spec_files:
        summary_lines.append("### 📄 Touched Living Specifications:")
        for sf in classification.spec_files:
            summary_lines.append(f"- `{sf}`")
        summary_lines.append("")

    if classification.delta_files:
        summary_lines.append("### 📋 Associated SpecDeltas:")
        for df in classification.delta_files:
            summary_lines.append(f"- `{df}`")
        summary_lines.append("")

    if remediation:
        summary_lines.append("### 💡 Recommended Actions:")
        for rem in remediation:
            summary_lines.append(f"- {rem}")
        summary_lines.append("")

    summary_markdown = "\n".join(summary_lines)

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


def reconcile_specs(
    repo_root: Path,
    base_ref: str | None = None,
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
) -> ReconcileResult:
    """Analyze changed code and synthesize recommendations / AI prompt to synchronize living specs."""
    root = repo_root.resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    changed_files = get_git_diff_files(root, base_ref=base_ref, uncommitted=uncommitted)
    classification = classify_changed_files(changed_files, repo_root=root, eidos_dir=target_eidos)
    comp_mappings = map_code_to_components(target_eidos, classification.code_files, repo_root=root)
    catalog = get_living_specs_catalog(target_eidos, repo_root=root)

    suggestions: list[ReconcileSuggestion] = []
    file_to_comps: dict[str, list[str]] = {}
    for cf, cid, spath in comp_mappings:
        file_to_comps.setdefault(cf, []).append(f"{cid} ({spath})")


    idx = get_architectural_index(repo_root=root, eidos_dir=target_eidos) if target_eidos.exists() else None

    for cf in classification.code_files:
        base_candidates = file_to_comps.get(cf, [])
        candidates = list(base_candidates)
        if idx:
            for entry in base_candidates:
                cid = entry.split()[0]
                neighbors = idx.get_k_hop_neighborhood(cid, max_hops=1)
                for n in neighbors:
                    ntype = n.get("type")
                    nid = n.get("id")
                    npath = n.get("data", {}).get("file_path") or n.get("data", {}).get("path")
                    if ntype in ("tech/contract", "product/capability") and npath:
                        cand_str = f"{nid} ({npath})"
                        if cand_str not in candidates:
                            candidates.append(cand_str)
        if not candidates:
            candidates = ["eidos/architecture/system-architecture.adoc"]

        suggestions.append(
            ReconcileSuggestion(
                code_file=cf,
                candidate_specs=candidates,
                reason="Code modified; corresponding architecture manifest, contract, or capability must reflect new behaviors.",
            )
        )

    # Formulate AI prompt
    prompt_lines = [
        "# Eidos Living Specification Synchronization Task",
        "",
        "You are an AI software architect pair-programming within an Eidos-managed repository.",
        "The following source code files were recently modified or added in the codebase:",
        "",
    ]
    for s in suggestions:
        prompt_lines.append(f"- Code File: `{s.code_file}`")
        prompt_lines.append(f"  Candidate Specs: {', '.join(s.candidate_specs)}")

    prompt_lines.extend(
        [
            "",
            "## Current Living Specifications Catalog:",
            f"- Capabilities ({len(catalog['capabilities'])}): "
            + ", ".join(c["id"] for c in catalog["capabilities"][:10]),
            f"- Components ({len(catalog['components'])}): " + ", ".join(c["id"] for c in catalog["components"]),
            f"- Contracts ({len(catalog['contracts'])}): " + ", ".join(c["id"] for c in catalog["contracts"]),
            f"- Policies ({len(catalog['policies'])}): " + ", ".join(c["id"] for c in catalog["policies"]),
            "",
            "## Instructions for the AI Agent:",
            "1. Inspect the Git diff of the modified code files.",
            "2. Think deeply about the architectural implications, touched contracts, capabilities, and invariant tests.",
            "3. Update the appropriate living specifications in 'eidos/' directly (or create a SpecDelta if this is a complex, breaking change).",
            "4. Verify that 'eidos validate eidos' and 'eidos drift --uncommitted' pass cleanly.",
        ]
    )

    return ReconcileResult(
        changed_code_files=classification.code_files,
        suggestions=suggestions,
        ai_prompt="\n".join(prompt_lines),
        catalog=catalog,
    )
