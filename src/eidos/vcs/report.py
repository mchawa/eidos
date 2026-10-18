"""Drift remediation suggestion synthesis and Markdown report rendering (CMP-DRIFT-DETECTOR).

Formats actionable remediation messages and PR verification Markdown tables
from structural drift, invariant verification, and schema validation outcomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from eidos.core.spec.validator import ValidationReport
    from eidos.vcs.diff import ChangedFilesClassification
    from eidos.vcs.invariants import InvariantTrace


def build_drift_remediation_and_summary(
    *,
    workflow_mode: str,
    is_clean: bool,
    drift_detected: bool,
    base_direct_drift: bool,
    unmapped_drift: bool,
    design_drift: bool,
    uncovered_delta_drift: bool,
    specs_changed: bool,
    has_deltas: bool,
    classification: ChangedFilesClassification,
    structural_code_files: list[str],
    comp_mappings: list[tuple[str, str, str]],
    uncovered_direct_components: list[tuple[str, str, str]],
    uncovered_components: list[tuple[str, str, str]],
    uncovered_unmapped_code: list[str],
    uncovered_design: list[tuple[str, str, list[str], list[str]]],
    trivial_records: list[dict[str, Any]],
    validation_report: ValidationReport,
    invariants: list[InvariantTrace],
    uncovered_invariants: list[InvariantTrace],
    orphan_invariants: list[tuple[str, str]],
    touched_components: list[str],
    eidos_rel_prefix: str = "eidos",
) -> tuple[list[str], str]:
    """Build actionable remediation suggestions and Markdown summary report for drift detection."""
    remediation: list[str] = []
    if base_direct_drift:
        target_list = uncovered_direct_components or comp_mappings
        remediation.append(
            f"Drift Detected: {len(structural_code_files)} structural code file(s) changed without corresponding living specification updates in {eidos_rel_prefix}/ (within 1-hop graph neighborhood)."
        )
        for cf, cid, spath in target_list[:5]:
            remediation.append(f"• '{cf}' relates to {cid} -> Update '{spath}' (or a 1-hop linked capability/contract)")
        if not target_list:
            remediation.append(
                f"• Run 'eidos drift' or ask your AI agent to update corresponding living specifications in {eidos_rel_prefix}/."
            )

    if unmapped_drift:
        remediation.append(
            f"Unmapped Code Drift Detected: {len(uncovered_unmapped_code)} structural code file(s) are not owned by any container or component."
        )
        for ucf in uncovered_unmapped_code[:5]:
            remediation.append(
                f"• '{ucf}' -> declare ownership via 'package_path'/'source_path' in {eidos_rel_prefix}/architecture/ (or substantively update {eidos_rel_prefix}/system.adoc)."
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
        if classification.code_files:
            summary_lines.append("### ⚠️ Status: DRIFT DETECTED (Code modified without living spec updates)")
        else:
            summary_lines.append("### ⚠️ Status: DRIFT DETECTED (Specification or SpecDelta synchronization required)")
    elif uncovered_invariants or orphan_invariants:
        summary_lines.append("### ❌ Status: INVARIANT VERIFICATION FAILED (Broken or orphan invariant test bindings)")
    else:
        summary_lines.append("### ❌ Status: VALIDATION FAILED")

    summary_lines.extend(
        [
            "",
            "| Metric | Count | Status |",
            "|---|---|---|",
            f"| Modified Code Files | `{len(classification.code_files)}` (`{len(structural_code_files)}` structural) | {'⚠️ Needs Spec Update' if (drift_detected and classification.code_files) else '✓'} |",
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

    return remediation, "\n".join(summary_lines)


def generate_remediation_prompt(report: Any) -> str:
    """Format an actionable AI remediation prompt from a DriftCheckResult, handling non-code drift accurately."""
    lines = ["# Eidos Drift & Governance Remediation Prompt", ""]
    code_files = list(getattr(report, "code_files", None) or getattr(report.classification, "code_files", []))
    if code_files:
        lines.append("Modified Source Code Files (causing drift):")
        for cf in code_files:
            lines.append(f"- `{cf}`")
        lines.append("")

    gov_failures: list[str] = []
    if not getattr(report, "validation_passed", True):
        for err in getattr(report, "validation_errors", []):
            gov_failures.append(f"- Validation error: {err}")
    for uinv in getattr(report, "uncovered_invariants", []):
        gov_failures.append(
            f"- Broken invariant binding: `{uinv.invariant_id}` ({uinv.spec_path}) -> `{uinv.test_binding}`"
        )
    for oid, loc in getattr(report, "orphan_invariants", []):
        gov_failures.append(f"- Orphan invariant reference: `{oid}` in `{loc}`")

    if gov_failures or not code_files:
        header = (
            "Governance & Verification Failures:"
            if code_files
            else "Non-Code Drift / Governance Verification Failures (0 code files modified):"
        )
        lines.append(header)
        lines.extend(gov_failures)
        lines.append("")

    remediation = list(getattr(report, "remediation_suggestions", []))
    if remediation:
        lines.append("Recommended Actions:")
        for rem in remediation:
            lines.append(f"- {rem}")
    return "\n".join(lines)

