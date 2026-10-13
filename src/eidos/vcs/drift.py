"""Drift detection, PR verification gate, and living spec reconciliation."""

import contextlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from eidos.core.parser import parse_spec_file
from eidos.core.project import (
    extract_entity_paths,
    is_generated_or_vendored,
    matches_owned_paths,
)
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
    orphan_invariants: list[tuple[str, str]] = field(default_factory=list)
    body_only_code_files: list[str] = field(default_factory=list)
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

        # Ignore .gitkeep, eidos/.cache/**, and eidos/deltas/by-status/** symlinks
        if f.name == ".gitkeep" or (
            parts
            and parts[0] == "eidos"
            and (
                any(p.startswith(".") for p in parts[1:])
                or ("deltas" in parts and "by-status" in parts)
            )
        ):
            classification.other_files.append(f_str)
            continue

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

        # Code files (excluding generated or vendored artifacts)
        if (
            f.suffix in code_extensions
            and not any(
                skip in parts
                for skip in [".venv", "venv", "node_modules", "dist", "build", "__pycache__"]
            )
            and not is_generated_or_vendored(effective_root, str(rel_f))
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
        # 1. AST / symbol / graph ownership reverse mapping
        matches = idx.resolve_symbol_or_path(cf)
        for m in matches:
            if m.get("type") in ("tech/container", "tech/component"):
                mappings.append((cf, m["entity_id"], m["file_path"]))
                matched = True
                break

        # 2. Owned paths (package_path / source_path strings or globs)
        if not matched:
            for c in components:
                cid = c.get("id", "")
                path = c.get("file_path", "")
                owned = extract_entity_paths(c)
                if owned and matches_owned_paths(cf, owned):
                    mappings.append((cf, cid, str(path)))
                    matched = True
                    break

        # 3. Generic component ID slug fallback on path segments
        if not matched:
            parts = [p.lower() for p in Path(cf).parts]
            for c in components:
                cid = c.get("id", "")
                path = c.get("file_path", "")
                slug = re.sub(r"^(?:cnt|cmp)-(?:[a-z0-9]+-)?", "", cid.lower())
                if slug and (slug in parts or any(slug == part.split(".")[0] for part in parts)):
                    mappings.append((cf, cid, str(path)))
                    matched = True
                    break

    return mappings


def _verify_single_invariant(
    iid: str,
    tb: str | None,
    repo_root: Path,
    all_tests_content: dict[str, str],
    inv_to_test_funcs: dict[str, list[str]],
    ast_idx,
) -> list[str]:
    """Strictly verify static invariant coverage against test_binding (file::func or file) or test suite."""
    pattern = re.compile(rf"\b{re.escape(iid)}\b")
    if tb:
        parts = [p.strip() for p in tb.split("::") if p.strip()]
        tb_file = parts[0]
        tb_func = parts[-1] if len(parts) > 1 else None
        tb_path = repo_root / tb_file
        if not tb_path.exists():
            return []
        if tb_file not in all_tests_content:
            with contextlib.suppress(Exception):
                all_tests_content[tb_file] = tb_path.read_text(encoding="utf-8", errors="replace")
        file_content = all_tests_content.get(tb_file, "")
        if tb_func:
            funcs = ast_idx.list_test_functions(tb_file, file_content)
            if tb_func not in funcs:
                return []
            if not pattern.search(funcs[tb_func]):
                return []
            return list(dict.fromkeys([f"{tb_file}::{tb_func}", *inv_to_test_funcs.get(iid, [])]))
        if pattern.search(file_content):
            return list(dict.fromkeys([tb_file, *inv_to_test_funcs.get(iid, [])]))
        return []

    matching = list(inv_to_test_funcs.get(iid, []))
    if not matching:
        matching = [tpath for tpath, content in all_tests_content.items() if pattern.search(content)]
    return matching


def detect_orphan_invariants(
    repo_root: Path,
    known_invariant_ids: set[str],
    all_tests_content: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Find INV-* tokens referenced in tests that do not exist in any living specification."""
    if all_tests_content is None:
        all_tests_content = collect_polyglot_test_files(repo_root)

    from eidos.core.ast_indexer import get_ast_indexer

    ast_idx = get_ast_indexer()
    orphans: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for tpath, tcontent in sorted(all_tests_content.items()):
        if "# eidos:fixture" in tcontent or "// eidos:fixture" in tcontent:
            # Check per-function or skip fixture-marked functions
            funcs = ast_idx.list_test_functions(tpath, tcontent)
            for fname, fslice in funcs.items():
                if "eidos:fixture" in fslice:
                    continue
                for line in fslice.splitlines():
                    if "eidos:fixture" in line:
                        continue
                    for inv_id in re.findall(r"\b(INV-[A-Z0-9-]+)\b", line):
                        if inv_id not in known_invariant_ids:
                            key = (inv_id, f"{tpath}::{fname}")
                            if key not in seen:
                                seen.add(key)
                                orphans.append(key)
            continue

        for binding in ast_idx.extract_test_bindings(tpath, tcontent):
            for inv_id in binding.invariants:
                if inv_id not in known_invariant_ids:
                    key = (inv_id, binding.identifier)
                    if key not in seen:
                        seen.add(key)
                        orphans.append(key)

    return orphans


def extract_invariants_and_verify(
    eidos_dir: Path,
    repo_root: Path,
) -> list[InvariantTrace]:
    """Extract all invariants declared in capabilities and policies, and strictly verify static test bindings."""
    invariants: list[InvariantTrace] = []

    # 1. Collect tests content across Python, TS/JS, Go, Rust, Java/Kotlin
    all_tests_content = collect_polyglot_test_files(repo_root)

    # 1.5 Extract granular AST test function bindings
    from eidos.core.ast_indexer import get_ast_indexer

    ast_idx = get_ast_indexer()
    inv_to_test_funcs: dict[str, list[str]] = {}
    for tpath, tcontent in all_tests_content.items():
        for binding in ast_idx.extract_test_bindings(tpath, tcontent):
            for ref in binding.invariants + binding.policies:
                inv_to_test_funcs.setdefault(ref, []).append(binding.identifier)

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
                            matching = _verify_single_invariant(
                                iid, tb, repo_root, all_tests_content, inv_to_test_funcs, ast_idx
                            )
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
                                matching = _verify_single_invariant(
                                    iid, tb, repo_root, all_tests_content, inv_to_test_funcs, ast_idx
                                )
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


def _get_file_versions(
    repo_root: Path,
    rel_path: str,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
) -> tuple[str | None, str | None]:
    """Retrieve (old_content, new_content) for a modified file to evaluate structural AST diffs."""
    repo = repo_root.resolve()
    norm_rel = rel_path.replace("\\", "/")

    def _git_show(ref: str, path_str: str) -> str | None:
        try:
            res = subprocess.run(
                ["git", "show", f"{ref}:{path_str}"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass
        return None

    if uncommitted:
        old_content = _git_show("HEAD", norm_rel)
        abs_p = repo / norm_rel
        new_content = None
        if abs_p.exists() and abs_p.is_file():
            with contextlib.suppress(Exception):
                new_content = abs_p.read_text(encoding="utf-8", errors="replace")
        return old_content, new_content

    resolved_base = base_ref
    if not resolved_base:
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
        return None, None

    old_content = _git_show(resolved_base, norm_rel)
    new_content = _git_show(head_ref, norm_rel)
    return old_content, new_content


def _collect_1hop_spec_targets(
    idx,
    cf: str,
    cid: str,
    spath: str,
) -> tuple[set[str], set[str], bool]:
    """Return (allowed_spec_paths, allowed_entity_ids, is_design_linked) within 1 hop of a code file's owners."""
    allowed_paths: set[str] = {
        spath.replace("\\", "/").lower(),
        "eidos/system.adoc",
        "eidos/architecture/system-architecture.adoc",
    }
    slug = re.sub(r"^(?:cnt|cmp)-(?:[a-z0-9]+-)?", "", cid.lower())
    allowed_ids: set[str] = {cid.lower()}
    if slug:
        allowed_ids.add(slug)

    is_design_linked = cf.endswith(
        (".css", ".scss", ".sass", ".less", ".tsx", ".jsx", ".vue", ".svelte", ".html")
    )

    if idx is None:
        return allowed_paths, allowed_ids, is_design_linked

    seed_ids: set[str] = {cid}
    for m in idx.resolve_symbol_or_path(cf):
        eid = m.get("entity_id")
        fp = m.get("file_path")
        mtype = m.get("type", "")
        if eid:
            seed_ids.add(eid)
            allowed_ids.add(eid.lower())
        if fp:
            allowed_paths.add(str(fp).replace("\\", "/").lower())
        if mtype in ("design/client", "design/system"):
            is_design_linked = True

    for sid in seed_ids:
        for n in idx.get_k_hop_neighborhood(sid, max_hops=1):
            nid = n.get("id", "")
            ntype = n.get("type", "")
            ndata = n.get("data", {}) or {}
            if ntype in ("tech/container", "tech/component") and nid not in seed_ids:
                continue
            if nid:
                allowed_ids.add(nid.lower())
            nfp = n.get("file_path")
            if nfp:
                allowed_paths.add(str(nfp).replace("\\", "/").lower())
            for pkey in ("file_path", "path", "tokens_path"):
                pval = ndata.get(pkey)
                if pval:
                    allowed_paths.add(str(pval).replace("\\", "/").lower())
            if ntype in ("design/client", "design/system"):
                is_design_linked = True

    return allowed_paths, allowed_ids, is_design_linked


def _is_covered_by_changed_specs(
    changed_specs: list[str],
    allowed_paths: set[str],
    allowed_ids: set[str],
    is_design_linked: bool,
) -> bool:
    """Check if any modified spec file satisfies the 1-hop neighborhood of a modified code file."""
    for s in changed_specs:
        s_norm = s.replace("\\", "/").lower()
        if any(ap and (s_norm.endswith(ap) or ap.endswith(s_norm)) for ap in allowed_paths):
            return True
        s_stem = Path(s_norm).stem
        if any(aid and len(aid) >= 3 and aid in s_stem for aid in allowed_ids):
            return True
        if is_design_linked and (
            "eidos/design" in s_norm or "/design/" in s_norm or s_norm.endswith("tokens.json")
        ):
            return True
    return False


def detect_drift(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
) -> DriftCheckResult:
    """Run drift detection: evaluate spec-code synchronization, schema validation, and invariant check."""
    from eidos.core.ast_indexer import has_structural_changes
    from eidos.core.graph_index import get_architectural_index

    root = repo_root.resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    changed_files = get_git_diff_files(root, base_ref=base_ref, head_ref=head_ref, uncommitted=uncommitted)
    classification = classify_changed_files(changed_files, repo_root=root)

    # Determine workflow mode: Planned (with SpecDelta) vs Direct (pure co-evolution)
    has_deltas = len(classification.delta_files) > 0
    workflow_mode = "planned" if has_deltas else "direct"

    # 0. Filter code files into structural vs body-only edits (DLT-2026-028)
    structural_code_files: list[str] = []
    body_only_code_files: list[str] = []
    for cf in classification.code_files:
        old_c, new_c = _get_file_versions(
            root, cf, base_ref=base_ref, head_ref=head_ref, uncommitted=uncommitted
        )
        if old_c is not None and new_c is not None:
            if old_c == new_c:
                # Synthetic/mocked diff where git blobs are identical
                is_structural = True
            else:
                is_structural, _ = has_structural_changes(old_c, new_c, cf)
        else:
            # Newly added or deleted file
            is_structural = True

        if is_structural:
            structural_code_files.append(cf)
        else:
            body_only_code_files.append(cf)

    # 1. Component Mapping (for structural code changes)
    comp_mappings = map_code_to_components(target_eidos, structural_code_files, repo_root=root)
    touched_components = sorted({cid for _, cid, _ in comp_mappings})
    idx = get_architectural_index(repo_root=root, eidos_dir=target_eidos) if target_eidos.exists() else None

    # 2. Extract declared targets from deltas if deltas exist
    declared_delta_targets: set[str] = set()
    if has_deltas:
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
                        for sub in (
                            "components",
                            "domains",
                            "capabilities",
                            "screens",
                            "design_systems",
                            "impacted_components",
                        ):
                            for val in br.get(sub, []) or []:
                                declared_delta_targets.add(str(val).lower())
            except Exception:
                pass

    # 3. Spec-Code Drift Evaluation (Scoped 1-Hop Neighborhood + Structural Changes)
    code_changed = len(structural_code_files) > 0
    specs_changed = len(classification.spec_files) > 0

    uncovered_components: list[tuple[str, str, str]] = []
    uncovered_direct_components: list[tuple[str, str, str]] = []

    if code_changed:
        for cf, cid, spath in comp_mappings:
            allowed_paths, allowed_ids, is_design_linked = _collect_1hop_spec_targets(
                idx, cf, cid, spath
            )
            covered_by_spec = _is_covered_by_changed_specs(
                classification.spec_files, allowed_paths, allowed_ids, is_design_linked
            )
            if has_deltas:
                covered_by_delta = any(
                    any(target in aid or aid in target for aid in allowed_ids if len(aid) >= 3)
                    for target in declared_delta_targets
                )
                if not covered_by_delta and not covered_by_spec:
                    uncovered_components.append((cf, cid, spath))
            else:
                if not covered_by_spec:
                    uncovered_direct_components.append((cf, cid, spath))

    # Domain-specific drift: Design systems & UI styling files
    design_drift = False
    styling_ui_exts = (".css", ".scss", ".sass", ".less", ".tsx", ".jsx", ".vue", ".svelte", ".html")
    frontend_code_files = [
        cf for cf in structural_code_files if cf.endswith(styling_ui_exts)
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

    base_direct_drift = (
        (code_changed and not specs_changed and not has_deltas)
        or (not has_deltas and bool(uncovered_direct_components))
    )
    uncovered_delta_drift = bool(uncovered_components)
    drift_detected = base_direct_drift or design_drift or uncovered_delta_drift

    # 4. Specification Schema & Link Integrity Validation
    validator = SpecValidator()
    validation_report = validator.validate_directory(target_eidos)

    # 5. Invariant Traceability & Orphan Invariant Detection (DLT-2026-027)
    invariants = extract_invariants_and_verify(target_eidos, root)
    uncovered_invariants = [inv for inv in invariants if not inv.has_test_coverage]
    orphan_invariants = detect_orphan_invariants(
        root, {inv.invariant_id for inv in invariants}
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

    if uncovered_invariants:
        remediation.append(
            f"Uncovered Invariants: {len(uncovered_invariants)} invariant(s) lack verified static test bindings (file::func + # INV-* marker)."
        )
        for uinv in uncovered_invariants[:5]:
            remediation.append(
                f"• {uinv.invariant_id} ({uinv.spec_path}) -> test_binding='{uinv.test_binding}'"
            )

    if orphan_invariants:
        remediation.append(
            f"Orphan Invariant References: {len(orphan_invariants)} test reference(s) point to non-existent INV-* IDs."
        )
        for oid, loc in orphan_invariants[:5]:
            remediation.append(f"• {oid} referenced in '{loc}' is not declared in any capability or policy.")

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
