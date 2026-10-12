"""Drift detection, PR verification gate, and living spec reconciliation."""

import contextlib
import os
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
                ["git", "status", "--porcelain", "-uall"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if status_res.returncode == 0:
                for line in status_res.stdout.splitlines():
                    if len(line) > 3:
                        raw_path = line[3:].strip()
                        if " -> " in raw_path:
                            raw_path = raw_path.split(" -> ")[1].strip()
                        files.add(raw_path)
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


POLYGLOT_TEST_PATTERNS = [
    # Python
    "test_*.py",
    "*_test.py",
    # TypeScript / JavaScript
    "*.test.ts",
    "*.spec.ts",
    "*.test.js",
    "*.spec.js",
    "*.test.tsx",
    "*.spec.tsx",
    # Go
    "*_test.go",
    # Rust
    "*_test.rs",
    # Java / Kotlin
    "*Test.java",
    "*Test.kt",
    "*Spec.kt",
]

POLYGLOT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".eidos",
    ".spec",
    "site",
    ".idea",
    ".vscode",
    ".ruff_cache",
    ".pytest_cache",
}


def is_polyglot_test_file(path: Path | str, repo_root: Path | None = None) -> bool:
    """Determine whether a file path corresponds to an automated test file across polyglot languages."""
    p = Path(path)
    name = p.name

    # Determine relative path within project if possible
    if repo_root is not None:
        try:
            rel_path = p.resolve().relative_to(repo_root.resolve())
        except ValueError:
            rel_path = p
    elif p.is_absolute():
        try:
            rel_path = p.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            rel_path = p
    else:
        rel_path = p

    # If rel_path is still absolute, it's outside repo/cwd:
    # only check immediate parent directory names, not the whole filesystem tree
    if rel_path.is_absolute():
        parent_names = [parent.name.lower() for parent in rel_path.parents[:2]]
        in_test_dir = any(d in ("tests", "test", "__tests__") for d in parent_names)
    else:
        dir_parts = [part.lower() for part in rel_path.parent.parts]
        in_test_dir = any(part in ("tests", "test", "__tests__") for part in dir_parts) or any(
            dir_parts[i] == "src" and dir_parts[i + 1] == "test" for i in range(len(dir_parts) - 1)
        )

    # TypeScript / JavaScript tests
    if any(
        name.endswith(ext)
        for ext in (
            ".test.ts",
            ".spec.ts",
            ".test.js",
            ".spec.js",
            ".test.tsx",
            ".spec.tsx",
            ".test.jsx",
            ".spec.jsx",
        )
    ):
        return True

    # Go tests
    if name.endswith("_test.go"):
        return True

    # Rust tests
    if name.endswith("_test.rs") or (in_test_dir and p.suffix == ".rs"):
        return True

    # Java / Kotlin tests
    if any(
        name.endswith(ext)
        for ext in (
            "Test.java",
            "Test.kt",
            "Spec.kt",
        )
    ):
        return True

    # Python tests
    if (name.startswith("test_") or name.endswith("_test.py")) and p.suffix == ".py":
        return True

    # Inside a dedicated test directory, standard code files are considered tests
    return bool(in_test_dir and p.suffix in {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt"})



def collect_polyglot_test_files(repo_root: Path) -> dict[str, str]:
    """Scan the repository for test files across Python, TS/JS, Go, Rust, and Java/Kotlin."""
    all_tests_content: dict[str, str] = {}
    root = repo_root.resolve()

    if not root.exists():
        return all_tests_content

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored and heavy directories in-place
        dirnames[:] = [d for d in dirnames if d not in POLYGLOT_EXCLUDED_DIRS and not d.startswith(".")]

        for fn in filenames:
            file_path = Path(dirpath) / fn
            rel = str(file_path.relative_to(root))
            if is_polyglot_test_file(Path(rel), repo_root=root):
                try:
                    all_tests_content[rel] = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

    return all_tests_content


def classify_changed_files(files: list[str], repo_root: Path | None = None) -> ChangedFilesClassification:
    """Classify file paths into code, living specs, deltas, tests, docs, and other."""
    classification = ChangedFilesClassification()

    code_extensions = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".html",
        ".vue",
        ".svelte",
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

    effective_root = (repo_root or Path.cwd()).resolve()

    for f_str in files:
        f = Path(f_str)
        if f.is_absolute():
            try:
                rel_f = f.resolve().relative_to(effective_root)
            except ValueError:
                rel_f = f
        else:
            rel_f = f

        parts = [p.lower() for p in rel_f.parts]

        # Spec / Delta files (must reside within the project's eidos/ specifications directory)
        if parts and parts[0] == "eidos" and f.suffix in {".adoc", ".json", ".yaml", ".yml"}:
            if "deltas" in parts:
                classification.delta_files.append(f_str)
            else:
                classification.spec_files.append(f_str)
            continue

        # Test files (polyglot test scanning)
        if is_polyglot_test_file(rel_f, repo_root=effective_root):
            classification.test_files.append(f_str)
            continue

        # Documentation files outside eidos
        if f.suffix in {".md", ".rst"} or (parts and parts[0] == "docs") or "docs" in parts[1:]:
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


def map_code_to_components(
    eidos_dir: Path, code_files: list[str], repo_root: Path | None = None
) -> list[tuple[str, str, str]]:
    """Map changed code files to living component specifications (returns list of (code_file, cmp_id, cmp_spec_path))."""
    if not eidos_dir.exists():
        return []

    from eidos.core.graph_index import get_architectural_index

    idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
    components = idx.get_components()

    mappings: list[tuple[str, str, str]] = []
    for cf in code_files:
        matched = False
        # 1. AST / symbol reverse mapping
        matches = idx.resolve_symbol_or_path(cf)
        for m in matches:
            if m.get("type") in ("tech/container", "tech/component"):
                mappings.append((cf, m["entity_id"], m["file_path"]))
                matched = True
                break

        # 2. Specific path prefixes
        if not matched and cf.startswith((".agents/plugins", "assets/plugin_reference")):
            for c in components:
                    if c.get("id") == "CNT-EIDOS-PLUGIN":
                        mappings.append((cf, "CNT-EIDOS-PLUGIN", str(c.get("file_path", "eidos/architecture/containers/plugin.adoc"))))
                        matched = True
                        break

        # 3. Package path prefix
        if not matched:
            for c in components:
                cid = c.get("id", "")
                pkg = c.get("package_path", "")
                path = c.get("file_path", "")
                if pkg and (cf.startswith(pkg) or f"/{pkg}/" in cf or cf.endswith(pkg)):
                    mappings.append((cf, cid, str(path)))
                    matched = True
                    break

        # 4. Component name slug fallback (on path segments, not arbitrary substring)
        if not matched:
            parts = [p.lower() for p in Path(cf).parts]
            for c in components:
                cid = c.get("id", "")
                path = c.get("file_path", "")
                slug = cid.lower().replace("cnt-eidos-", "").replace("cmp-eidos-", "").replace("cnt-", "").replace("cmp-", "")
                if slug and (slug in parts or any(slug == part.split(".")[0] for part in parts)):
                    mappings.append((cf, cid, str(path)))
                    matched = True
                    break

    return mappings


def extract_invariants_and_verify(
    eidos_dir: Path,
    repo_root: Path,
) -> list[InvariantTrace]:
    """Extract all invariants declared in capabilities and policies, and check test coverage across polyglot test suites."""
    invariants: list[InvariantTrace] = []

    # 1. Collect tests content to check mentions across Python, TS/JS, Go, Rust, Java/Kotlin
    all_tests_content = collect_polyglot_test_files(repo_root)

    # 2. Extract invariants from capabilities
    domains_dir = eidos_dir / "domains"
    if domains_dir.exists():
        for cap_path in sorted(domains_dir.rglob("*.adoc")):
            try:
                doc = parse_spec_file(cap_path)
                if doc.doc_type == "product/capability":
                    fm = doc.frontmatter or {}
                    tech = fm.get("technical") if isinstance(fm.get("technical"), dict) else {}
                    inv_list = tech.get("invariants", []) or []
                    for inv in inv_list:
                        if not isinstance(inv, dict):
                            continue
                        iid = inv.get("id")
                        rule = inv.get("rule", "")
                        tb = inv.get("test_binding")
                        if iid:
                            # If test_binding points to a file, ensure it is in all_tests_content
                            if tb:
                                tb_file = tb.split("::")[0].strip()
                                tb_path = repo_root / tb_file
                                if tb_path.exists() and tb_file not in all_tests_content:
                                    with contextlib.suppress(Exception):
                                        all_tests_content[tb_file] = tb_path.read_text(
                                            encoding="utf-8", errors="replace"
                                        )

                            matching = [tpath for tpath, content in all_tests_content.items() if iid in content]
                            if not matching and tb and (repo_root / tb.split("::")[0].strip()).exists():
                                matching = [tb.split("::")[0].strip()]
                            has_cov = bool(matching)
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

    # 3. Extract invariants from policies
    policies_dir = eidos_dir / "policies"
    if policies_dir.exists():
        for pol_path in sorted(policies_dir.rglob("*.adoc")):
            try:
                doc = parse_spec_file(pol_path)
                if doc.doc_type == "product/policy":
                    fm = doc.frontmatter or {}
                    tech = fm.get("technical") if isinstance(fm.get("technical"), dict) else {}
                    inv_list = fm.get("invariants", []) or tech.get("invariants", []) or []
                    for inv in inv_list:
                        if isinstance(inv, dict):
                            iid = inv.get("id")
                            rule = inv.get("rule", "")
                            tb = inv.get("test_binding")
                            if iid:
                                if tb:
                                    tb_file = tb.split("::")[0].strip()
                                    tb_path = repo_root / tb_file
                                    if tb_path.exists() and tb_file not in all_tests_content:
                                        with contextlib.suppress(Exception):
                                            all_tests_content[tb_file] = tb_path.read_text(
                                                encoding="utf-8", errors="replace"
                                            )

                                matching = [tpath for tpath, content in all_tests_content.items() if iid in content]
                                if not matching and tb and (repo_root / tb.split("::")[0].strip()).exists():
                                    matching = [tb.split("::")[0].strip()]
                                has_cov = bool(matching)
                                invariants.append(
                                    InvariantTrace(
                                        invariant_id=iid,
                                        rule=rule,
                                        spec_path=str(
                                            pol_path.relative_to(repo_root)
                                            if pol_path.is_relative_to(repo_root)
                                            else pol_path
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


def get_living_specs_catalog(eidos_dir: Path, repo_root: Path | None = None) -> dict[str, list[dict]]:
    """Generate a clean, token-efficient catalog of living capabilities, components, and policies for agents."""
    catalog: dict[str, list[dict]] = {
        "capabilities": [],
        "components": [],
        "contracts": [],
        "policies": [],
    }

    if not eidos_dir.exists():
        return catalog

    from eidos.core.graph_index import get_architectural_index

    idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)
    entities = idx.get_entities()

    for ent in entities:
        dtype = ent.get("type", "")
        eid = ent.get("id", "")
        title = ent.get("title", "")
        file_p = Path(ent.get("file_path", ""))
        rel = str(file_p.relative_to(eidos_dir)) if file_p.is_relative_to(eidos_dir) else str(file_p)
        spec_path = f"eidos/{rel}" if not rel.startswith("eidos/") else rel

        if dtype == "product/capability":
            biz = ent.get("business", {})
            catalog["capabilities"].append(
                {
                    "id": eid,
                    "title": title,
                    "domain": ent.get("domain", ""),
                    "jtbd": biz.get("jtbd", "") if isinstance(biz, dict) else "",
                    "path": spec_path,
                }
            )
        elif dtype in ("tech/container", "tech/component"):
            catalog["components"].append(
                {
                    "id": eid,
                    "name": ent.get("name", title),
                    "package_path": ent.get("package_path", ""),
                    "runtime": ent.get("runtime", ""),
                    "path": spec_path,
                }
            )
        elif dtype == "tech/contract":
            catalog["contracts"].append(
                {
                    "id": eid,
                    "title": title,
                    "protocol": ent.get("protocol", ""),
                    "owning_component": ent.get("owning_component", ""),
                    "path": spec_path,
                }
            )
        elif dtype == "product/policy":
            catalog["policies"].append(
                {
                    "id": eid,
                    "title": title,
                    "category": ent.get("category", ""),
                    "rule": ent.get("rule", ""),
                    "path": spec_path,
                }
            )

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
    classification = classify_changed_files(changed_files, repo_root=root)

    # Determine workflow mode: Planned (with SpecDelta) vs Direct (pure co-evolution)
    # Determine workflow mode: Planned (with SpecDelta) vs Direct (pure co-evolution)
    has_deltas = len(classification.delta_files) > 0
    workflow_mode = "planned" if has_deltas else "direct"

    # 1. Component Mapping
    comp_mappings = map_code_to_components(target_eidos, classification.code_files, repo_root=root)
    touched_components = sorted({cid for _, cid, _ in comp_mappings})

    # 2. Extract declared targets from deltas if deltas exist
    declared_delta_targets: set[str] = set()
    if has_deltas:
        from eidos.core.parser import parse_spec_file
        for df in classification.delta_files:
            try:
                delta_file = (root / df) if not Path(df).is_absolute() else Path(df)
                if delta_file.exists():
                    doc = parse_spec_file(delta_file)
                    fm = doc.frontmatter or {}
                    for m in fm.get("mutations", []) or []:
                        if isinstance(m, dict):
                            for key in (
                                "target_component",
                                "target_capability",
                                "target_domain",
                                "target_screen",
                                "target_ui_component",
                                "target_design_system",
                                "target_contract",
                                "target_persona",
                            ):
                                val = m.get(key)
                                if val:
                                    declared_delta_targets.add(str(val).lower())
                    br = fm.get("blast_radius", {}) or {}
                    if isinstance(br, dict):
                        for sub in ("components", "domains", "capabilities", "screens", "design_systems", "impacted_components"):
                            for val in br.get(sub, []) or []:
                                declared_delta_targets.add(str(val).lower())
            except Exception:
                pass

    # 3. Spec-Code Drift Evaluation
    code_changed = len(classification.code_files) > 0
    specs_changed = len(classification.spec_files) > 0

    # Uncovered code detection in planned mode
    uncovered_components: list[tuple[str, str, str]] = []
    if has_deltas and code_changed:
        for cf, cid, spath in comp_mappings:
            cid_lower = cid.lower()
            slug = cid_lower.replace("cnt-eidos-", "").replace("cmp-eidos-", "").replace("cnt-", "").replace("cmp-", "")
            covered_by_delta = any(
                target in cid_lower or slug in target or cid_lower in target
                for target in declared_delta_targets
            )
            covered_by_spec = any(cid_lower in s.lower() or slug in s.lower() for s in classification.spec_files)
            if not covered_by_delta and not covered_by_spec:
                uncovered_components.append((cf, cid, spath))

    # Domain-specific drift: Design systems & frontend
    design_drift = False
    frontend_code_files = [
        cf for cf in classification.code_files
        if "frontend" in cf.lower()
        or cf.endswith((".css", ".scss", ".sass", ".less"))
        or cf.endswith((".tsx", ".jsx", ".vue", ".svelte", ".html"))
        or cf.endswith("assets.py")
    ]
    if frontend_code_files:
        has_design_spec_update = any(
            "eidos/design" in s.replace("\\", "/")
            or "/design/" in s.replace("\\", "/")
            or s.endswith("tokens.json")
            for s in classification.spec_files
        )
        if not has_design_spec_update:
            design_drift = True

    base_direct_drift = code_changed and not specs_changed and not has_deltas
    uncovered_delta_drift = bool(uncovered_components)
    drift_detected = base_direct_drift or design_drift or uncovered_delta_drift

    # 4. Specification Schema & Link Integrity Validation
    validator = SpecValidator()
    validation_report = validator.validate_directory(target_eidos)

    # 5. Invariant Traceability
    invariants = extract_invariants_and_verify(target_eidos, root)

    # 6. Remediation suggestions
    remediation: list[str] = []
    if base_direct_drift:
        remediation.append(
            f"Drift Detected: {len(classification.code_files)} code file(s) changed in src/ without living specification updates in eidos/."
        )
        for cf, cid, spath in comp_mappings[:5]:
            remediation.append(f"• '{cf}' relates to {cid} -> Update '{spath}'")
        if not comp_mappings:
            remediation.append(
                "• Run 'eidos reconcile' or ask your AI agent to update corresponding living specifications."
            )

    if design_drift:
        remediation.append(
            f"Design System Drift Detected: {len(frontend_code_files)} frontend/styling code file(s) were modified without corresponding updates in 'eidos/design/' (tokens.json, UIC-* component manifests, or SCR-* screen specs)."
        )
        for fcf in frontend_code_files[:5]:
            remediation.append(
                f"• Frontend change in '{fcf}' requires updating 'eidos/design/systems/' tokens or 'eidos/design/clients/' specs."
            )

    if uncovered_delta_drift:
        for cf, cid, spath in uncovered_components[:5]:
            remediation.append(
                f"Uncovered Delta Drift: '{cf}' relates to {cid}, which is NOT declared in active SpecDelta mutations. Declare it in the SpecDelta or update '{spath}'."
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
    classification = classify_changed_files(changed_files, repo_root=root)
    comp_mappings = map_code_to_components(target_eidos, classification.code_files, repo_root=root)
    catalog = get_living_specs_catalog(target_eidos, repo_root=root)

    suggestions: list[ReconcileSuggestion] = []
    file_to_comps: dict[str, list[str]] = {}
    for cf, cid, spath in comp_mappings:
        file_to_comps.setdefault(cf, []).append(f"{cid} ({spath})")

    from eidos.core.graph_index import get_architectural_index

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
