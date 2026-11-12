"""Drift detection, PR verification gate, and living spec reconciliation."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.graph_index import get_architectural_index
from eidos.core.index.storage import get_cached_spec_document
from eidos.core.spec.parser import extract_frontmatter
from eidos.core.spec.validator import SpecValidator, ValidationReport
from eidos.core.workspace.project import find_git_root, is_generated_or_vendored
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


def _materialize_head_snapshot(
    git_root: Path,
    repo_root: Path,
    target_eidos: Path,
    eidos_rel_prefix: str,
    head_ref: str,
) -> tempfile.TemporaryDirectory[str] | None:
    """Materialize `--head` Git commit tree into an isolated TemporaryDirectory for clean commit-range drift checks."""
    if not repo_root.is_relative_to(git_root) or not target_eidos.is_relative_to(repo_root):
        return None
    tree_spec = head_ref
    if repo_root != git_root:
        sub_rel = str(repo_root.relative_to(git_root)).replace("\\", "/").strip("/")
        if sub_rel:
            tree_spec = f"{head_ref}:{sub_rel}"

    tmp_dir = tempfile.TemporaryDirectory(prefix="eidos_head_")
    snap_root = Path(tmp_dir.name)
    try:
        proc = subprocess.Popen(
            ["git", "archive", "--format=tar", tree_spec],
            cwd=git_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            tf.extractall(path=snap_root, filter="data")
        proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        if proc.wait() != 0:
            tmp_dir.cleanup()
            return None
    except (OSError, tarfile.TarError):
        tmp_dir.cleanup()
        return None

    src_cache_db = target_eidos / ".cache" / "index.db"
    snap_eidos = snap_root / eidos_rel_prefix
    if src_cache_db.is_file() and snap_eidos.is_dir():
        with contextlib.suppress(OSError):
            dst_cache_dir = snap_eidos / ".cache"
            dst_cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_cache_db, dst_cache_dir / "index.db")
    return tmp_dir


def _remap_snapshot_str(text: str, snap_root: Path | None) -> str:
    """Replace any temporary snapshot directory prefix with clean repo-relative paths."""
    if snap_root is None or not text:
        return text
    for cand in (snap_root.resolve(), snap_root):
        prefix_posix = cand.as_posix().rstrip("/")
        if prefix_posix:
            text = text.replace(f"{prefix_posix}/", "")
            text = text.replace(prefix_posix, ".")
        prefix_os = str(cand).rstrip("/\\")
        if prefix_os and prefix_os != prefix_posix:
            text = text.replace(f"{prefix_os}\\", "").replace(f"{prefix_os}/", "")
            text = text.replace(prefix_os, ".")
    return text


def _remap_validation_report(report: ValidationReport, snap_root: Path | None) -> None:
    """Normalize schema_errors and broken_references in-place so snapshot paths report as repo-relative."""
    if snap_root is None:
        return
    remapped_errors: dict[str, list[str]] = {}
    for err_key, msgs in report.schema_errors.items():
        clean_key = _remap_snapshot_str(str(err_key), snap_root)
        clean_msgs = [_remap_snapshot_str(m, snap_root) for m in msgs]
        remapped_errors.setdefault(clean_key, []).extend(clean_msgs)
    report.schema_errors = remapped_errors
    report.broken_references = [_remap_snapshot_str(ref, snap_root) for ref in report.broken_references]


def _has_eidos_onboarding(eval_root: Path) -> bool:
    """Return True if Eidos Agent Skills or installation manifests exist in eval_root."""
    skills_dir = eval_root / ".agents" / "skills"
    if skills_dir.exists() or skills_dir.is_symlink():
        return True
    for marker_dir in (".agents", ".claude", ".cursor", ".github", ".windsurf", ".aider", ".clinerules"):
        if (eval_root / marker_dir / ".eidos-manifest.json").is_file():
            return True
    return False


def _validate_onboarding_governance(
    *,
    onboarding_files: list[str],
    repo_root: Path,
    eval_root: Path,
    effective_uncommitted: bool,
    fetch_versions_fn: Any,
    validation_report: ValidationReport,
) -> bool:
    """Validate modified onboarding governance artifacts (AGENTS.md, CLAUDE.md, .aider.conf.yml, SKILL.md)."""
    if not onboarding_files:
        return False

    from eidos.plugin.managed_block import find_managed_block, read_managed_block

    onboarding_drift = False
    repo_has_onboarding = _has_eidos_onboarding(eval_root)

    for ob_raw in onboarding_files:
        ob_p = Path(ob_raw)
        if ob_p.is_absolute():
            try:
                ob_rel = ob_p.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                ob_rel = ob_p.as_posix()
        else:
            ob_rel = ob_p.as_posix()

        old_ob, new_ob = fetch_versions_fn(ob_raw)
        disk_ob = eval_root / ob_rel
        disk_text: str | None = None
        if disk_ob.is_file():
            with contextlib.suppress(OSError):
                disk_text = disk_ob.read_text(encoding="utf-8", errors="replace")
        if new_ob is None and effective_uncommitted and disk_text is not None:
            new_ob = disk_text

        low_rel = ob_rel.lower()
        if low_rel == "agents.md":
            had_managed_block = bool(old_ob and "<!-- eidos:begin agents-md" in old_ob)
            if not (repo_has_onboarding or had_managed_block):
                continue
            eff_text = new_ob if new_ob is not None else disk_text
            if eff_text and "<!-- eidos:agents-md off -->" in eff_text:
                continue
            try:
                if disk_ob.is_file() and (new_ob is None or disk_text == new_ob):
                    block = read_managed_block(disk_ob, "agents-md")
                elif eff_text is not None:
                    block = find_managed_block(eff_text, "agents-md")
                else:
                    block = None
            except ValueError as exc:
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    f"Root AGENTS.md managed block is malformed ({exc}). Run 'eidos plugin install <target>' or add '<!-- eidos:agents-md off -->'."
                )
                onboarding_drift = True
                continue
            if block is None or not block.inner.strip():
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    "Root AGENTS.md is missing a valid non-empty '<!-- eidos:begin agents-md ... -->' managed block. Restore via 'eidos plugin install <target>' or add '<!-- eidos:agents-md off -->'."
                )
                onboarding_drift = True

        elif low_rel.endswith("/skill.md"):
            if not repo_has_onboarding:
                continue
            eff_skill = new_ob if new_ob is not None else (disk_text if effective_uncommitted else None)
            if eff_skill is not None:
                fm_sk, _, fm_err = extract_frontmatter(eff_skill)
                if (
                    fm_err
                    or not isinstance(fm_sk, dict)
                    or not str(fm_sk.get("name", "")).strip()
                    or not str(fm_sk.get("description", "")).strip()
                ):
                    validation_report.schema_errors.setdefault(ob_rel, []).append(
                        "Modified Agent Skill file is missing valid YAML frontmatter ('name' and 'description')."
                    )
                    onboarding_drift = True
            else:
                top_dir = Path(ob_rel).parts[0] if Path(ob_rel).parts else ""
                if top_dir and (eval_root / top_dir / ".eidos-manifest.json").is_file():
                    validation_report.schema_errors.setdefault(ob_rel, []).append(
                        "Installed Agent Skill file was deleted while '.eidos-manifest.json' remains active."
                    )
                    onboarding_drift = True

        elif low_rel == "claude.md":
            had_claude = bool(old_ob and "<!-- eidos:begin claude-import" in old_ob)
            claude_active = (eval_root / ".claude" / "skills").exists() or (
                eval_root / ".claude" / ".eidos-manifest.json"
            ).is_file()
            if not (had_claude or claude_active):
                continue
            eff_claude = new_ob if new_ob is not None else disk_text
            if eff_claude and ("<!-- eidos:agents-md off -->" in eff_claude or "<!-- eidos:claude-import off -->" in eff_claude):
                continue
            try:
                c_block = (
                    read_managed_block(disk_ob, "claude-import")
                    if disk_ob.is_file() and (new_ob is None or disk_text == new_ob)
                    else (find_managed_block(eff_claude, "claude-import") if eff_claude is not None else None)
                )
            except ValueError as exc:
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    f"Root CLAUDE.md managed block is malformed ({exc})."
                )
                onboarding_drift = True
                continue
            if had_claude and (c_block is None or not c_block.inner.strip()) and "@AGENTS.md" not in (eff_claude or ""):
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    "Root CLAUDE.md wiped its 'claude-import' (@AGENTS.md) managed block while Claude skills are active."
                )
                onboarding_drift = True

        elif low_rel == ".aider.conf.yml":
            had_aider = bool(old_ob and "AGENTS.md" in old_ob)
            aider_active = (eval_root / ".aider" / "skills").exists() or (
                eval_root / ".aider" / ".eidos-manifest.json"
            ).is_file()
            eff_aider = new_ob if new_ob is not None else disk_text
            if had_aider and aider_active and (eff_aider is None or "AGENTS.md" not in eff_aider):
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    "Modified .aider.conf.yml removed 'AGENTS.md' read configuration while Aider skills are active."
                )
                onboarding_drift = True

    return onboarding_drift


def detect_drift(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
    strict: bool = False,
) -> DriftCheckResult:
    """Run drift detection: evaluate spec-code synchronization, schema validation, and invariant check."""
    import eidos.core.index.graph_index as gi
    from eidos.core.index.ast_indexer import STYLE_EXTS, classify_design_changes, has_structural_changes
    from eidos.core.lifecycle.delta_engine import verify_delta_mutations

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

    snap_tmpdir: tempfile.TemporaryDirectory[str] | None = None
    snap_root: Path | None = None
    git_root: Path | None = None
    git_sub_prefix = ""
    eval_root = root
    eval_eidos = target_eidos
    if not effective_uncommitted:
        git_root = find_git_root(root)
        if git_root is not None:
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

    try:
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
            repo_root=root,
            eidos_dir=target_eidos,
            package_paths=head_pkg_paths,
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
        for df in classification.delta_files:
            try:
                delta_rel = (
                    Path(df).resolve().relative_to(root).as_posix()
                    if Path(df).is_absolute() and Path(df).resolve().is_relative_to(root)
                    else str(df).replace("\\", "/")
                )
                delta_file = (eval_root / delta_rel) if not Path(df).is_absolute() or snap_root is not None else Path(df)
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
                if model is None:
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
                for m in model.mutations:
                    val = m.target
                    if not val:
                        continue
                    val_str = str(val).strip()
                    if not val_str:
                        continue
                    declared_delta_targets.add(val_str.lower())
                    if val_str.lower().endswith((".adoc", ".json", ".yaml", ".yml")) or "/" in val_str or "\\" in val_str:
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
                    if rejected_rule == "stale_removed_symbol" and addition is not None and addition.removed_symbol_names:
                        additions_list = [f"removed '{s}'" for s in addition.removed_symbol_names]
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

        if strict and body_only_code_files and not classification.test_files:
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
            if strict
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
        validation_report = validator.validate_directory(eval_eidos)
        _remap_validation_report(validation_report, snap_root)

        applied_delta_drift = False
        for df_rel, delta_id_str, new_dc_val in newly_applied_deltas:
            delta_eval_path = eval_root / df_rel
            temp_written = False
            try:
                if not delta_eval_path.is_file() and new_dc_val is not None and snap_root is not None:
                    delta_eval_path.parent.mkdir(parents=True, exist_ok=True)
                    delta_eval_path.write_text(new_dc_val, encoding="utf-8")
                    temp_written = True
                lookup_ref = str(delta_eval_path) if delta_eval_path.is_file() else delta_id_str
                ok_ver, ver_msg, ver_rep = verify_delta_mutations(
                    eval_eidos,
                    lookup_ref,
                    git_repo_root=git_root,
                    snapshot_root=snap_root,
                    git_sub_prefix=git_sub_prefix,
                )
                if not ok_ver or (ver_rep is not None and not ver_rep.is_verified):
                    err_bucket = validation_report.schema_errors.setdefault(df_rel, [])
                    if ver_rep is not None:
                        failed_muts = [m for m in ver_rep.mutations if not m.verified]
                        if failed_muts:
                            for fm_item in failed_muts:
                                reason = fm_item.failure_reason or fm_item.details or fm_item.status
                                err_bucket.append(
                                    f"Unfulfilled SpecDelta mutation [{fm_item.action} {fm_item.target}] in '{ver_rep.delta_id}': {_remap_snapshot_str(reason, snap_root)}"
                                )
                        else:
                            err_bucket.append(_remap_snapshot_str(ver_msg, snap_root))
                    else:
                        err_bucket.append(_remap_snapshot_str(ver_msg, snap_root))
                    applied_delta_drift = True
            finally:
                if temp_written:
                    with contextlib.suppress(OSError):
                        delta_eval_path.unlink(missing_ok=True)

        onboarding_drift = _validate_onboarding_governance(
            onboarding_files=classification.onboarding_files,
            repo_root=root,
            eval_root=eval_root,
            effective_uncommitted=effective_uncommitted,
            fetch_versions_fn=_fetch_versions,
            validation_report=validation_report,
        )

        strict_body_changed = strict and bool(body_only_code_files) and not classification.test_files
        base_direct_drift = (
            ((code_changed or strict_body_changed) and not specs_changed and not has_pre_apply_deltas)
            or (not has_pre_apply_deltas and bool(uncovered_direct_components))
        )
        uncovered_delta_drift = bool(uncovered_components)
        drift_detected = (
            base_direct_drift
            or design_drift
            or uncovered_delta_drift
            or unmapped_drift
            or applied_delta_drift
            or onboarding_drift
        )

        # 5. Invariant Traceability & Orphan Invariant Detection (DLT-2026-027, DLT-2026-068)
        cached_tests = _load_or_update_test_binding_cache(eval_root, eidos_dir=eval_eidos)
        invariants = extract_invariants_and_verify(eval_eidos, eval_root, cached_tests=cached_tests)
        for inv in invariants:
            inv.spec_path = _remap_snapshot_str(inv.spec_path, snap_root)
        uncovered_invariants = [inv for inv in invariants if not inv.has_test_coverage]
        raw_orphans = detect_orphan_invariants(
            eval_root,
            {inv.invariant_id for inv in invariants},
            eidos_dir=eval_eidos,
            cached_tests=cached_tests,
        )
        orphan_invariants = [(oid, _remap_snapshot_str(loc, snap_root)) for oid, loc in raw_orphans]

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
            body_only_code_files=body_only_code_files,
            strict=strict,
            comp_mappings=comp_mappings or body_comp_mappings,
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
            structural_code_files=structural_code_files,
            body_only_code_files=body_only_code_files,
            uncovered_design_files=frontend_code_files,
            unmapped_code_files=uncovered_unmapped_code,
            trivially_covered_files=trivial_records,
            remediation_suggestions=remediation,
            summary_markdown=summary_markdown,
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
