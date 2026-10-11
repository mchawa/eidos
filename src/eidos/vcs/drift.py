"""Drift detection, PR verification gate, and living spec reconciliation."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from eidos.core.parser import parse_spec_file
from eidos.core.validator import SpecValidator, ValidationReport


@dataclass
class ChangedFilesClassification:
    """Categorized files changed in a git diff or commit range."""

    code_files: list[str] = field(default_factory=list)
    spec_files: list[str] = field(default_factory=list)
    delta_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return (
            len(self.code_files)
            + len(self.spec_files)
            + len(self.delta_files)
            + len(self.test_files)
            + len(self.doc_files)
            + len(self.other_files)
        )


@dataclass
class InvariantTrace:
    """Traceability record linking a living spec invariant to automated tests."""

    invariant_id: str
    rule: str
    spec_path: str
    spec_id: str
    test_binding: str | None
    has_test_coverage: bool
    matching_tests: list[str] = field(default_factory=list)


@dataclass
class DriftCheckResult:
    """Consolidated outcome of the Eidos drift detection analysis."""

    is_clean: bool
    drift_detected: bool
    workflow_mode: str  # "planned" (with SpecDelta) or "direct" (without SpecDelta)
    classification: ChangedFilesClassification
    validation_report: ValidationReport
    touched_components: list[str] = field(default_factory=list)
    invariants: list[InvariantTrace] = field(default_factory=list)
    remediation_suggestions: list[str] = field(default_factory=list)
    summary_markdown: str = ""


# Backwards compatibility alias
PRCheckResult = DriftCheckResult


@dataclass
class ReconcileSuggestion:
    """Actionable recommendation mapping code changes to living specifications."""

    code_file: str
    candidate_specs: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ReconcileResult:
    """Result of analyzing code changes for living spec reconciliation."""

    changed_code_files: list[str]
    suggestions: list[ReconcileSuggestion]
    ai_prompt: str
    catalog: dict[str, list[dict]] = field(default_factory=dict)


def get_git_diff_files(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
) -> list[str]:
    """Retrieve list of modified and added files using Git."""
    repo = repo_root.resolve()

    if uncommitted:
        # Check uncommitted changes (working tree + staging + untracked)
        files: set[str] = set()
        try:
            diff_res = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_res.returncode == 0:
                files.update(line.strip() for line in diff_res.stdout.splitlines() if line.strip())

            staged_res = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if staged_res.returncode == 0:
                files.update(line.strip() for line in staged_res.stdout.splitlines() if line.strip())

            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if status_res.returncode == 0:
                for line in status_res.stdout.splitlines():
                    if line.startswith("?? ") or line.startswith("A ") or line.startswith("M "):
                        files.add(line[3:].strip())
        except Exception:
            pass
        return sorted(files)

    # Determine base_ref if not provided
    resolved_base = base_ref
    if not resolved_base:
        # Try origin/main, then main, then HEAD~1
        for candidate in ["origin/main", "main", "origin/master", "master", "HEAD~1"]:
            check = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=repo,
                capture_output=True,
                check=False,
            )
            if check.returncode == 0:
                resolved_base = candidate
                break

    if not resolved_base:
        # Fallback to inspecting uncommitted changes
        return get_git_diff_files(repo_root, uncommitted=True)

    try:
        cmd = ["git", "diff", "--name-only", f"{resolved_base}...{head_ref}"]
        res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if lines:
                return sorted(lines)

        # Fallback to direct range
        cmd_direct = ["git", "diff", "--name-only", resolved_base, head_ref]
        res_direct = subprocess.run(cmd_direct, cwd=repo, capture_output=True, text=True, check=False)
        if res_direct.returncode == 0:
            return sorted(line.strip() for line in res_direct.stdout.splitlines() if line.strip())
    except Exception:
        pass

    return get_git_diff_files(repo_root, uncommitted=True)


def classify_changed_files(files: list[str]) -> ChangedFilesClassification:
    """Classify file paths into code, living specs, deltas, tests, docs, and other."""
    classification = ChangedFilesClassification()

    code_extensions = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
    }

    for f_str in files:
        f = Path(f_str)
        parts = [p.lower() for p in f.parts]

        # Spec / Delta files
        if "eidos" in parts and f.suffix in {".adoc", ".json", ".yaml", ".yml"}:
            if "deltas" in parts:
                classification.delta_files.append(f_str)
            else:
                classification.spec_files.append(f_str)
            continue

        # Test files
        if "tests" in parts or "test" in parts or f.name.startswith("test_") or f.name.endswith("_test.py"):
            classification.test_files.append(f_str)
            continue

        # Documentation files outside eidos
        if f.suffix in {".md", ".rst"} or "docs" in parts:
            classification.doc_files.append(f_str)
            continue

        # Code files
        if f.suffix in code_extensions and not any(
            skip in parts for skip in [".venv", "venv", "node_modules", "dist", "build", "__pycache__"]
        ):
            classification.code_files.append(f_str)
            continue

        classification.other_files.append(f_str)

    return classification


def map_code_to_components(eidos_dir: Path, code_files: list[str]) -> list[tuple[str, str, str]]:
    """Map changed code files to living component specifications (returns list of (code_file, cmp_id, cmp_spec_path))."""
    mappings: list[tuple[str, str, str]] = []
    components: list[tuple[str, str, Path, str]] = []  # (id, name, path, package_path)

    arch_dir = eidos_dir / "architecture" / "components"
    if arch_dir.exists():
        for p in arch_dir.rglob("*.adoc"):
            try:
                doc = parse_spec_file(p)
                if doc.doc_type == "tech/component":
                    cid = doc.doc_id or p.stem
                    name = doc.frontmatter.get("name", p.stem)
                    pkg = doc.frontmatter.get("package_path", "")
                    components.append((cid, name, p, pkg))
            except Exception:
                continue

    for cf in code_files:
        matched = False
        for cid, _name, path, pkg in components:
            if pkg and (cf.startswith(pkg) or pkg in cf):
                mappings.append((cf, cid, str(path)))
                matched = True
                break
        if not matched:
            # Fallback heuristic: check if component name slug is in path
            for cid, _name, path, _ in components:
                slug = cid.lower().replace("cmp-eidos-", "").replace("cmp-", "")
                if slug and slug in cf.lower():
                    mappings.append((cf, cid, str(path)))
                    matched = True
                    break

    return mappings


def extract_invariants_and_verify(
    eidos_dir: Path,
    repo_root: Path,
) -> list[InvariantTrace]:
    """Extract all invariants declared in capabilities and policies, and check test coverage."""
    invariants: list[InvariantTrace] = []

    # 1. Collect tests content to check mentions
    tests_dir = repo_root / "tests"
    all_tests_content: dict[str, str] = {}
    if tests_dir.exists():
        for tp in tests_dir.rglob("*.py"):
            try:
                all_tests_content[str(tp.relative_to(repo_root))] = tp.read_text(encoding="utf-8")
            except Exception:
                continue

    # 2. Extract invariants from capabilities
    domains_dir = eidos_dir / "domains"
    if domains_dir.exists():
        for cap_path in domains_dir.rglob("*.adoc"):
            try:
                doc = parse_spec_file(cap_path)
                if doc.doc_type == "product/capability":
                    tech = doc.frontmatter.get("technical", {})
                    inv_list = tech.get("invariants", [])
                    for inv in inv_list:
                        iid = inv.get("id")
                        rule = inv.get("rule", "")
                        tb = inv.get("test_binding")
                        if iid:
                            matching = [tpath for tpath, content in all_tests_content.items() if iid in content]
                            has_cov = bool(matching) or (tb is not None and (repo_root / tb.split("::")[0]).exists())
                            invariants.append(
                                InvariantTrace(
                                    invariant_id=iid,
                                    rule=rule,
                                    spec_path=str(
                                        cap_path.relative_to(repo_root)
                                        if cap_path.is_relative_to(repo_root)
                                        else cap_path
                                    ),
                                    spec_id=doc.doc_id,
                                    test_binding=tb,
                                    has_test_coverage=has_cov,
                                    matching_tests=matching,
                                )
                            )
            except Exception:
                continue

    return invariants


def get_living_specs_catalog(eidos_dir: Path) -> dict[str, list[dict]]:
    """Generate a clean, token-efficient catalog of living capabilities, components, and policies for agents."""
    catalog: dict[str, list[dict]] = {
        "capabilities": [],
        "components": [],
        "contracts": [],
        "policies": [],
    }

    if not eidos_dir.exists():
        return catalog

    for p in eidos_dir.rglob("*.adoc"):
        try:
            doc = parse_spec_file(p)
            dtype = doc.doc_type
            fm = doc.frontmatter
            rel = str(p.relative_to(eidos_dir))

            if dtype == "product/capability":
                catalog["capabilities"].append(
                    {
                        "id": doc.doc_id,
                        "title": doc.title,
                        "domain": fm.get("domain", ""),
                        "jtbd": fm.get("business", {}).get("jtbd", ""),
                        "path": f"eidos/{rel}",
                    }
                )
            elif dtype == "tech/component":
                catalog["components"].append(
                    {
                        "id": doc.doc_id,
                        "name": fm.get("name", ""),
                        "package_path": fm.get("package_path", ""),
                        "runtime": fm.get("runtime", ""),
                        "path": f"eidos/{rel}",
                    }
                )
            elif dtype == "tech/contract":
                catalog["contracts"].append(
                    {
                        "id": doc.doc_id,
                        "title": doc.title,
                        "protocol": fm.get("protocol", ""),
                        "owning_component": fm.get("owning_component", ""),
                        "path": f"eidos/{rel}",
                    }
                )
            elif dtype == "product/policy":
                catalog["policies"].append(
                    {
                        "id": doc.doc_id,
                        "title": doc.title,
                        "category": fm.get("category", ""),
                        "rule": fm.get("rule", ""),
                        "path": f"eidos/{rel}",
                    }
                )
        except Exception:
            continue

    return catalog


def detect_drift(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
) -> DriftCheckResult:
    """Run drift detection: evaluate spec-code synchronization, schema validation, and invariant check."""
    root = repo_root.resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    changed_files = get_git_diff_files(root, base_ref=base_ref, head_ref=head_ref, uncommitted=uncommitted)
    classification = classify_changed_files(changed_files)

    # Determine workflow mode: Planned (with SpecDelta) vs Direct (pure co-evolution)
    has_deltas = len(classification.delta_files) > 0
    workflow_mode = "planned" if has_deltas else "direct"

    # 1. Spec-Code Drift Evaluation
    code_changed = len(classification.code_files) > 0
    specs_changed = len(classification.spec_files) > 0
    drift_detected = code_changed and not specs_changed and not has_deltas

    # 2. Specification Schema & Link Integrity Validation
    validator = SpecValidator()
    validation_report = validator.validate_directory(target_eidos)

    # 3. Component Mapping
    comp_mappings = map_code_to_components(target_eidos, classification.code_files)
    touched_components = sorted({cid for _, cid, _ in comp_mappings})

    # 4. Invariant Traceability
    invariants = extract_invariants_and_verify(target_eidos, root)

    # 5. Remediation suggestions
    remediation: list[str] = []
    if drift_detected:
        remediation.append(
            f"Drift Detected: {len(classification.code_files)} code file(s) changed in src/ without living specification updates in eidos/."
        )
        for cf, cid, spath in comp_mappings[:5]:
            remediation.append(f"• '{cf}' relates to {cid} -> Update '{spath}'")
        if not comp_mappings:
            remediation.append(
                "• Run 'eidos reconcile' or ask your AI agent to update corresponding living specifications."
            )

    if not validation_report.is_clean:
        remediation.append(
            f"Specification Validation Failed: {len(validation_report.schema_errors)} error(s), {len(validation_report.broken_references)} broken link(s)."
        )

    # Overall clean status
    is_clean = (not drift_detected) and validation_report.is_clean

    # 6. Generate Markdown Summary for reports and PR comments
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
            f"| Modified Code Files | `{len(classification.code_files)}` | {'⚠️ Needs Spec Update' if drift_detected else '✓'} |",
            f"| Modified Living Specs | `{len(classification.spec_files)}` | {'✓ In Sync' if specs_changed else '—'} |",
            f"| Modified SpecDeltas | `{len(classification.delta_files)}` | {'✓ Planned' if has_deltas else '— (Direct Mode)'} |",
            f"| Modified Test Files | `{len(classification.test_files)}` | {'✓' if classification.test_files else '—'} |",
            f"| Living Specs Validated | `{validation_report.valid_docs}/{validation_report.total_docs}` | {'✓ 100% Green' if validation_report.is_clean else '✗ Errors'} |",
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
    classification = classify_changed_files(changed_files)
    comp_mappings = map_code_to_components(target_eidos, classification.code_files)
    catalog = get_living_specs_catalog(target_eidos)

    suggestions: list[ReconcileSuggestion] = []
    file_to_comps: dict[str, list[str]] = {}
    for cf, cid, spath in comp_mappings:
        file_to_comps.setdefault(cf, []).append(f"{cid} ({spath})")

    for cf in classification.code_files:
        candidates = file_to_comps.get(cf, ["eidos/architecture/system-architecture.adoc"])
        suggestions.append(
            ReconcileSuggestion(
                code_file=cf,
                candidate_specs=candidates,
                reason="Code modified; corresponding architecture manifest or contract must reflect new behaviors.",
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
            "4. Verify that 'eidos validate eidos' and 'eidos drift' (or AI tool 'eidos_detect_drift') pass cleanly.",
        ]
    )

    return ReconcileResult(
        changed_code_files=classification.code_files,
        suggestions=suggestions,
        ai_prompt="\n".join(prompt_lines),
        catalog=catalog,
    )
