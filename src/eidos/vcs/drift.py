"""Drift detection, PR verification gate, and living spec reconciliation."""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator, ValidationReport
from eidos.core.workspace.project import (
    extract_entity_paths,
    is_generated_or_vendored,
    matches_owned_paths,
)


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
    uncovered_design_files: list[str] = field(default_factory=list)
    unmapped_code_files: list[str] = field(default_factory=list)
    trivially_covered_files: list[dict[str, Any]] = field(default_factory=list)
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
                ["git", "diff", "--name-only", "--no-renames"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_res.returncode == 0:
                files.update(line.strip() for line in diff_res.stdout.splitlines() if line.strip())

            staged_res = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--no-renames"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if staged_res.returncode == 0:
                files.update(line.strip() for line in staged_res.stdout.splitlines() if line.strip())

            status_res = subprocess.run(
                ["git", "status", "--porcelain", "--no-renames", "-uall"],
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
                            # Both sides: eidos pairs moves by content itself (DLT-2026-039)
                            src_path, raw_path = (part.strip() for part in raw_path.split(" -> ", 1))
                            files.add(src_path)
                        files.add(raw_path)
        except OSError:
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
        cmd = ["git", "diff", "--name-only", "--no-renames", f"{resolved_base}...{head_ref}"]
        res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if lines:
                return sorted(lines)

        # Fallback to direct range
        cmd_direct = ["git", "diff", "--name-only", "--no-renames", resolved_base, head_ref]
        res_direct = subprocess.run(cmd_direct, cwd=repo, capture_output=True, text=True, check=False)
        if res_direct.returncode == 0:
            return sorted(line.strip() for line in res_direct.stdout.splitlines() if line.strip())
    except OSError:
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
                except OSError:
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
            and (any(p.startswith(".") for p in parts[1:]) or ("deltas" in parts and "by-status" in parts))
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
            and not any(skip in parts for skip in [".venv", "venv", "node_modules", "dist", "build", "__pycache__"])
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

    from eidos.core.index.graph_index import get_architectural_index

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
            best_score = 0
            best_match: tuple[str, str, str] | None = None
            for c in components:
                cid = c.get("id", "")
                path = c.get("file_path", "")
                owned = extract_entity_paths(c)
                if owned:
                    is_match, score = matches_owned_paths(cf, owned)
                    if is_match and score > best_score:
                        best_score = score
                        best_match = (cf, cid, str(path))
            if best_match is not None:
                mappings.append(best_match)
                matched = True

    return mappings


@dataclass
class _CachedTestFile:
    """Hydrated test file metadata from test_binding_cache."""

    file_path: str
    mtime_ns: int
    sha256: str
    has_fixture: bool
    functions: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    file_tokens: set[str] = field(default_factory=set)


_INV_OR_POL_TOKEN_RE = re.compile(r"\b(?:INV|POL)-[A-Za-z0-9_-]+\b")
_INV_FIXTURE_TOKEN_RE = re.compile(r"\b(INV-[A-Z0-9-]+)\b")


def _build_cached_test_file_entry(
    rel: str,
    tcontent: str,
    mtime_ns: int,
    sha256: str,
    ast_idx: Any,
) -> tuple[_CachedTestFile, str, str, str]:
    """Extract test functions, fixture-filtered INV tokens, AST bindings, and file-level tokens for caching."""
    has_fixture = "# eidos:fixture" in tcontent or "// eidos:fixture" in tcontent
    raw_funcs = ast_idx.list_test_functions(rel, tcontent)
    functions: dict[str, dict[str, list[str]]] = {}
    for fname, fslice in raw_funcs.items():
        fn_tokens = list(dict.fromkeys(_INV_OR_POL_TOKEN_RE.findall(fslice)))
        fixture_invs: list[str] = []
        if has_fixture and "eidos:fixture" not in fslice:
            for line in fslice.splitlines():
                if "eidos:fixture" in line:
                    continue
                for inv_id in _INV_FIXTURE_TOKEN_RE.findall(line):
                    if inv_id not in fixture_invs:
                        fixture_invs.append(inv_id)
        functions[fname] = {
            "tokens": fn_tokens,
            "fixture_invs": fixture_invs,
        }

    raw_bindings = ast_idx.extract_test_bindings(rel, tcontent)
    bindings = [
        {
            "identifier": b.identifier,
            "invariants": list(b.invariants),
            "policies": list(b.policies),
        }
        for b in raw_bindings
    ]
    file_tokens_list = list(dict.fromkeys(_INV_OR_POL_TOKEN_RE.findall(tcontent)))
    entry = _CachedTestFile(
        file_path=rel,
        mtime_ns=mtime_ns,
        sha256=sha256,
        has_fixture=has_fixture,
        functions=functions,
        bindings=bindings,
        file_tokens=set(file_tokens_list),
    )
    return (
        entry,
        json.dumps(functions, sort_keys=True),
        json.dumps(bindings, sort_keys=True),
        json.dumps(file_tokens_list),
    )


def _load_or_update_test_binding_cache(
    repo_root: Path,
    eidos_dir: Path | None = None,
) -> dict[str, _CachedTestFile]:
    """Stat polyglot test files and load or incrementally update test_binding_cache in eidos/.cache/index.db."""
    root = repo_root.resolve()
    if not root.exists():
        return {}

    effective_eidos = (eidos_dir or (root / "eidos")).resolve()
    db_path = effective_eidos / ".cache" / "index.db"

    cached_rows: dict[str, sqlite3.Row] = {}
    if effective_eidos.exists():
        with contextlib.suppress(OSError, sqlite3.Error):
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(db_path), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                existing_tables = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")
                }
                if not existing_tables:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO metadata (key, value) VALUES ('schema_version', '2.2.0');"
                    )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS test_binding_cache (
                        file_path TEXT PRIMARY KEY,
                        mtime_ns INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        has_fixture INTEGER NOT NULL DEFAULT 0,
                        functions_json TEXT NOT NULL,
                        bindings_json TEXT NOT NULL,
                        file_tokens_json TEXT NOT NULL
                    );
                    """
                )
                for row in conn.execute(
                    "SELECT file_path, mtime_ns, sha256, has_fixture, functions_json, bindings_json, file_tokens_json FROM test_binding_cache;"
                ):
                    cached_rows[row["file_path"]] = row

    discovered: list[tuple[str, Path, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in POLYGLOT_EXCLUDED_DIRS and not d.startswith(".")]
        for fn in filenames:
            file_path = Path(dirpath) / fn
            rel = str(file_path.relative_to(root)).replace("\\", "/")
            if is_polyglot_test_file(Path(rel), repo_root=root):
                try:
                    mtime_ns = file_path.stat().st_mtime_ns
                except OSError:
                    continue
                discovered.append((rel, file_path, mtime_ns))

    result: dict[str, _CachedTestFile] = {}
    seen_rels: set[str] = set()
    to_upsert: list[tuple[str, int, str, int, str, str, str]] = []
    ast_idx: Any = None

    for rel, file_path, mtime_ns in sorted(discovered, key=lambda item: item[0]):
        seen_rels.add(rel)
        row = cached_rows.get(rel)
        if row is not None and row["mtime_ns"] == mtime_ns:
            with contextlib.suppress(ValueError, TypeError, KeyError):
                result[rel] = _CachedTestFile(
                    file_path=rel,
                    mtime_ns=mtime_ns,
                    sha256=row["sha256"],
                    has_fixture=bool(row["has_fixture"]),
                    functions=json.loads(row["functions_json"]),
                    bindings=json.loads(row["bindings_json"]),
                    file_tokens=set(json.loads(row["file_tokens_json"])),
                )
                continue

        try:
            raw_bytes = file_path.read_bytes()
        except OSError:
            continue
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        if row is not None and row["sha256"] == sha256:
            with contextlib.suppress(ValueError, TypeError, KeyError):
                result[rel] = _CachedTestFile(
                    file_path=rel,
                    mtime_ns=mtime_ns,
                    sha256=sha256,
                    has_fixture=bool(row["has_fixture"]),
                    functions=json.loads(row["functions_json"]),
                    bindings=json.loads(row["bindings_json"]),
                    file_tokens=set(json.loads(row["file_tokens_json"])),
                )
                to_upsert.append(
                    (
                        rel,
                        mtime_ns,
                        sha256,
                        int(bool(row["has_fixture"])),
                        row["functions_json"],
                        row["bindings_json"],
                        row["file_tokens_json"],
                    )
                )
                continue

        if ast_idx is None:
            from eidos.core.index.ast_indexer import get_ast_indexer

            ast_idx = get_ast_indexer()

        tcontent = raw_bytes.decode("utf-8", errors="replace")
        entry, functions_json, bindings_json, file_tokens_json = _build_cached_test_file_entry(
            rel, tcontent, mtime_ns, sha256, ast_idx
        )
        result[rel] = entry
        to_upsert.append(
            (
                rel,
                mtime_ns,
                sha256,
                1 if entry.has_fixture else 0,
                functions_json,
                bindings_json,
                file_tokens_json,
            )
        )

    deleted_keys = [k for k in cached_rows if k not in seen_rels]
    if effective_eidos.exists() and (to_upsert or deleted_keys):
        with contextlib.suppress(OSError, sqlite3.Error):
            with sqlite3.connect(str(db_path), timeout=10.0) as conn:
                if deleted_keys:
                    conn.executemany(
                        "DELETE FROM test_binding_cache WHERE file_path = ?;",
                        [(k,) for k in deleted_keys],
                    )
                if to_upsert:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO test_binding_cache
                        (file_path, mtime_ns, sha256, has_fixture, functions_json, bindings_json, file_tokens_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                        """,
                        to_upsert,
                    )

    return result


def _verify_single_invariant(
    iid: str,
    tb: str | None,
    repo_root: Path,
    all_tests_content: dict[str, Any],
    inv_to_test_funcs: dict[str, list[str]],
    ast_idx: Any = None,
) -> list[str]:
    """Strictly verify static invariant coverage against test_binding (file::func or file) or test suite."""
    uses_cached_entries = any(isinstance(v, _CachedTestFile) for v in all_tests_content.values()) or not all_tests_content
    pattern: re.Pattern[str] | None = None

    if tb:
        parts = [p.strip() for p in tb.split("::") if p.strip()]
        tb_file = parts[0].replace("\\", "/")
        tb_func = parts[-1] if len(parts) > 1 else None
        tb_path = repo_root / tb_file
        if not tb_path.exists():
            return []

        entry = all_tests_content.get(tb_file)
        if entry is None:
            try:
                raw_bytes = tb_path.read_bytes()
                tcontent = raw_bytes.decode("utf-8", errors="replace")
            except OSError:
                return []
            if uses_cached_entries:
                if ast_idx is None:
                    from eidos.core.index.ast_indexer import get_ast_indexer

                    ast_idx = get_ast_indexer()
                st_mtime_ns = tb_path.stat().st_mtime_ns if tb_path.exists() else 0
                sha256 = hashlib.sha256(raw_bytes).hexdigest()
                entry, _, _, _ = _build_cached_test_file_entry(tb_file, tcontent, st_mtime_ns, sha256, ast_idx)
                all_tests_content[tb_file] = entry
            else:
                all_tests_content[tb_file] = tcontent
                entry = tcontent

        if isinstance(entry, _CachedTestFile):
            if tb_func:
                func_meta = entry.functions.get(tb_func)
                if func_meta is None:
                    return []
                if iid not in func_meta.get("tokens", []):
                    return []
                return list(dict.fromkeys([f"{tb_file}::{tb_func}", *inv_to_test_funcs.get(iid, [])]))
            if iid in entry.file_tokens:
                return list(dict.fromkeys([tb_file, *inv_to_test_funcs.get(iid, [])]))
            return []

        file_content = str(entry)
        pattern = re.compile(rf"\b{re.escape(iid)}\b")
        if tb_func:
            if ast_idx is None:
                from eidos.core.index.ast_indexer import get_ast_indexer

                ast_idx = get_ast_indexer()
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
        for tpath, entry in all_tests_content.items():
            if isinstance(entry, _CachedTestFile):
                if iid in entry.file_tokens:
                    matching.append(tpath)
            else:
                if pattern is None:
                    pattern = re.compile(rf"\b{re.escape(iid)}\b")
                if pattern.search(str(entry)):
                    matching.append(tpath)
    return matching


def detect_orphan_invariants(
    repo_root: Path,
    known_invariant_ids: set[str],
    all_tests_content: dict[str, str] | None = None,
    eidos_dir: Path | None = None,
) -> list[tuple[str, str]]:
    """Find INV-* tokens referenced in tests that do not exist in any living specification."""
    orphans: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if all_tests_content is None:
        cached_tests = _load_or_update_test_binding_cache(repo_root, eidos_dir=eidos_dir)
        for tpath, entry in sorted(cached_tests.items()):
            if entry.has_fixture:
                for fname, func_meta in entry.functions.items():
                    for inv_id in func_meta.get("fixture_invs", []):
                        if inv_id not in known_invariant_ids:
                            key = (inv_id, f"{tpath}::{fname}")
                            if key not in seen:
                                seen.add(key)
                                orphans.append(key)
                continue

            for binding in entry.bindings:
                ident = binding["identifier"]
                for inv_id in binding.get("invariants", []):
                    if inv_id not in known_invariant_ids:
                        key = (inv_id, ident)
                        if key not in seen:
                            seen.add(key)
                            orphans.append(key)
        return orphans

    from eidos.core.index.ast_indexer import get_ast_indexer

    ast_idx = get_ast_indexer()

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
    """Extract all invariants declared in capabilities, policies, and C4 components, and strictly verify static test bindings."""
    invariants: list[InvariantTrace] = []

    # 1. Load or incrementally update cached test bindings across Python, TS/JS, Go, Rust, Java/Kotlin
    cached_tests = _load_or_update_test_binding_cache(repo_root, eidos_dir=eidos_dir)

    # 1.5 Map invariant/policy IDs to AST test function bindings from cache
    inv_to_test_funcs: dict[str, list[str]] = {}
    for entry in cached_tests.values():
        for binding in entry.bindings:
            ident = binding["identifier"]
            for ref in list(binding.get("invariants", [])) + list(binding.get("policies", [])):
                inv_to_test_funcs.setdefault(ref, []).append(ident)

    def _spec_rel(path: Path) -> str:
        return str(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)

    def _record(inv: Any, spec_path: Path, spec_id: str) -> None:
        if not isinstance(inv, dict) or not inv.get("id"):
            return
        iid = inv["id"]
        tb = inv.get("test_binding")
        matching = _verify_single_invariant(iid, tb, repo_root, cached_tests, inv_to_test_funcs)
        invariants.append(
            InvariantTrace(
                invariant_id=iid,
                rule=inv.get("rule", ""),
                spec_path=_spec_rel(spec_path),
                spec_id=spec_id,
                test_binding=tb,
                has_test_coverage=bool(matching),
                matching_tests=matching,
            )
        )

    # 2. Scan every invariant-bearing specification type (POL-002):
    #    capabilities (domains/), policies (policies/), and C4 Level 3 components
    #    (architecture/components/ standalone specs + inline container components) (DLT-2026-031).
    resolved_repo_root = repo_root.resolve()
    resolved_eidos_dir = eidos_dir.resolve()
    spec_cache_rows: dict[str, tuple[int, str]] = {}
    db_path = resolved_eidos_dir / ".cache" / "index.db"
    if db_path.exists():
        with contextlib.suppress(OSError, sqlite3.Error):
            with sqlite3.connect(str(db_path), timeout=10.0) as conn:
                for file_path_col, mtime_col, doc_json_col in conn.execute(
                    "SELECT file_path, mtime_ns, doc_json FROM spec_validation_cache;"
                ):
                    spec_cache_rows[file_path_col] = (int(mtime_col), str(doc_json_col))

    scan_roots: list[tuple[str, tuple[str, ...]]] = [
        ("domains", ("product/capability",)),
        ("policies", ("product/policy",)),
        ("architecture", ("tech/component", "tech/container")),
    ]
    for sub_dir, doc_types in scan_roots:
        root_dir = eidos_dir / sub_dir
        if not root_dir.exists():
            continue
        for spec_path in sorted(root_dir.rglob("*.adoc")):
            doc_type: str | None = None
            doc_id: str = "unknown"
            fm: dict[str, Any] = {}
            res_spec = spec_path.resolve()
            rel_key: str | None = None
            for base in (resolved_repo_root, resolved_eidos_dir):
                try:
                    rel_key = str(res_spec.relative_to(base)).replace("\\", "/")
                    break
                except ValueError:
                    continue
            if rel_key and rel_key in spec_cache_rows:
                with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
                    st_mtime_ns = spec_path.stat().st_mtime_ns
                    cached_mtime_ns, doc_json_str = spec_cache_rows[rel_key]
                    if cached_mtime_ns == st_mtime_ns:
                        doc_data = json.loads(doc_json_str)
                        doc_type = str(doc_data.get("doc_type") or "unknown")
                        doc_id = str(doc_data.get("doc_id") or "unknown")
                        fm = doc_data.get("frontmatter") or {}
            if doc_type is None:
                try:
                    doc = parse_spec_file(spec_path)
                except OSError:
                    continue
                doc_type = doc.doc_type
                doc_id = doc.doc_id
                fm = doc.frontmatter or {}
            if doc_type not in doc_types:
                continue
            tech = fm.get("technical") if isinstance(fm.get("technical"), dict) else {}
            if doc_type == "product/capability":
                inv_list = tech.get("invariants", []) or []
            elif doc_type == "tech/container":
                # Inline C4 Level 3 components may declare their own invariants
                for comp in fm.get("components", []) or []:
                    if isinstance(comp, dict):
                        for inv in comp.get("invariants", []) or []:
                            _record(inv, spec_path, str(comp.get("id") or doc_id))
                inv_list = []
            else:
                inv_list = fm.get("invariants", []) or tech.get("invariants", []) or []
            for inv in inv_list:
                _record(inv, spec_path, doc_id)

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

    from eidos.core.index.graph_index import get_architectural_index

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
                    "owning_container": ent.get("owning_container") or ent.get("owning_component", ""),
                    "owning_component": ent.get("owning_container") or ent.get("owning_component", ""),
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
        except OSError:
            pass
        return None

    if uncommitted:
        old_content = _git_show("HEAD", norm_rel)
        abs_p = repo / norm_rel
        new_content = None
        if abs_p.exists() and abs_p.is_file():
            with contextlib.suppress(OSError):
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


SYSTEM_LEVEL_SPEC_PATHS = (
    "eidos/system.adoc",
    "eidos/architecture/system-architecture.adoc",
)


def _collect_1hop_spec_targets(
    idx,
    cf: str,
    cid: str,
    spath: str,
) -> tuple[set[str], set[str]]:
    """Return (allowed_spec_paths, allowed_entity_ids) within 1 hop of a code file's owners.

    System-level specifications are intentionally excluded: editing them must not mask drift
    inside a mapped container or component (DLT-2026-030).
    """
    allowed_paths: set[str] = {spath.replace("\\", "/").lower()}
    allowed_ids: set[str] = {cid.lower()}

    if idx is None:
        return allowed_paths, allowed_ids

    seed_ids: set[str] = {cid}
    for m in idx.resolve_symbol_or_path(cf):
        eid = m.get("entity_id")
        fp = m.get("file_path")
        if eid:
            seed_ids.add(eid)
            allowed_ids.add(eid.lower())
        if fp:
            allowed_paths.add(str(fp).replace("\\", "/").lower())

    for sid in seed_ids:
        for n in idx.get_k_hop_neighborhood(sid, max_hops=1):
            nid = n.get("id", "")
            ntype = n.get("type", "")
            ndata = n.get("data", {}) or {}
            if ntype in ("tech/container", "tech/component") and nid not in seed_ids:
                continue
            if nid:
                allowed_ids.add(nid.lower())
            if ntype == "tech/container" and nid in seed_ids:
                for inline_comp in ndata.get("components", []) or []:
                    if isinstance(inline_comp, dict) and inline_comp.get("id"):
                        allowed_ids.add(str(inline_comp["id"]).strip().lower())
            nfp = n.get("file_path")
            if nfp:
                allowed_paths.add(str(nfp).replace("\\", "/").lower())
            for pkey in ("file_path", "path", "tokens_path"):
                pval = ndata.get(pkey)
                if pval:
                    allowed_paths.add(str(pval).replace("\\", "/").lower())

    allowed_paths.difference_update(SYSTEM_LEVEL_SPEC_PATHS)
    return allowed_paths, allowed_ids


def _path_in(changed_spec: str, allowed_paths: set[str]) -> bool:
    s_norm = changed_spec.replace("\\", "/").lower().strip("/")
    for ap in allowed_paths:
        if not ap:
            continue
        ap_norm = ap.replace("\\", "/").lower().strip("/")
        if s_norm == ap_norm or s_norm.endswith("/" + ap_norm) or ap_norm.endswith("/" + s_norm):
            return True
    return False


def _relevant_changed_specs(
    changed_specs: list[str],
    allowed_paths: set[str],
    allowed_ids: set[str],
) -> list[str]:
    """Modified spec files inside the 1-hop neighborhood of a modified code file (system-level specs excluded)."""
    relevant: list[str] = []
    for s in changed_specs:
        s_norm = s.replace("\\", "/").lower()
        if s_norm in SYSTEM_LEVEL_SPEC_PATHS:
            continue
        if _path_in(s_norm, allowed_paths) or Path(s_norm).stem in allowed_ids:
            relevant.append(s)
    return relevant


# ---------------------------------------------------------------------------
# Diff-aware specification coverage (DLT-2026-039)
# ---------------------------------------------------------------------------

_ADOC_LINE_COMMENT_RE = re.compile(r"^\s*//(?!/)")
_ADOC_COMMENT_DELIM_RE = re.compile(r"^/{4,}$")
# Verbatim blocks (listing, literal, passthrough): their content is never an AsciiDoc comment.
_ADOC_VERBATIM_DELIM_RE = re.compile(r"^(-{4,}|\.{4,}|\+{4,})$")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_OWNERSHIP_KEYS = ("components", "source_path", "owned_path", "package_path")
_MIN_ANCHOR_LEN = 3


@dataclass
class _StructuralAddition:
    """Structural additions of one code file: a new file and/or new public symbols."""

    file: str
    reasons: list[str]
    symbol_names: list[str]
    is_new_file: bool


class _SymbolMapCache:
    """Parses each (file, side) version at most once per drift run."""

    def __init__(self, versions: dict[str, tuple[str | None, str | None]]) -> None:
        self._versions = versions
        self._maps: dict[tuple[str, int], dict[tuple[str, str, str], str]] = {}

    @staticmethod
    def supports(cf: str) -> bool:
        from eidos.core.index.ast_indexer import AST_DIFF_EXTS

        return Path(cf).suffix.lower() in AST_DIFF_EXTS

    def versions_of(self, cf: str) -> tuple[str | None, str | None]:
        return self._versions.get(cf, (None, None))

    def get(self, cf: str, side: int) -> dict[tuple[str, str, str], str]:
        """Public symbol map of ``cf`` at ``side`` (0 = old, 1 = new)."""
        from eidos.core.index.ast_indexer import public_symbol_map

        key = (cf, side)
        if key not in self._maps:
            self._maps[key] = public_symbol_map(self.versions_of(cf)[side], cf)
        return self._maps[key]

    def diff(self, cf: str, old_from: str | None = None) -> Any:
        """Public symbol diff of ``cf`` (old side taken from ``old_from`` for moved files)."""
        from eidos.core.index.ast_indexer import diff_symbol_maps

        return diff_symbol_maps(self.get(old_from or cf, 0), self.get(cf, 1))


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _pair_moved_files(
    versions: dict[str, tuple[str | None, str | None]],
    smap: _SymbolMapCache,
) -> dict[str, str]:
    """Pair added files with deleted files of the same change set that they were moved/renamed from.

    Purely content-based (no VCS rename detection): an added file ``A`` is a move of a deleted file ``D`` with the
    same extension when their whitespace-normalized contents are identical, or when ``D`` had public symbols and
    every one of them still exists in ``A``. Each deleted file pairs with at most one added file; identical
    content wins, then the largest preserved public surface, then path order.
    """
    added = sorted(cf for cf, (old, new) in versions.items() if old is None and new is not None)
    deleted = sorted(cf for cf, (old, new) in versions.items() if old is not None and new is None)
    if not added or not deleted:
        return {}

    candidates: list[tuple[int, int, str, str]] = []
    for a in added:
        a_text = _normalize_ws(versions[a][1] or "")
        for d in deleted:
            if Path(a).suffix.lower() != Path(d).suffix.lower():
                continue
            if a_text == _normalize_ws(versions[d][0] or ""):
                candidates.append((0, 0, a, d))
                continue
            if not smap.supports(a):
                continue
            d_keys = set(smap.get(d, 0))
            if d_keys and d_keys <= set(smap.get(a, 1)):
                candidates.append((1, -len(d_keys), a, d))

    pairs: dict[str, str] = {}
    used: set[str] = set()
    for _, _, a, d in sorted(candidates):
        if a not in pairs and d not in used:
            pairs[a] = d
            used.add(d)
    return pairs


def _structural_addition_for(
    cf: str,
    synthetic: bool,
    smap: _SymbolMapCache,
    *,
    symbol_diff: Any = None,
    moved_from: str | None = None,
) -> _StructuralAddition | None:
    """Return the structural additions of a structurally changed file, or None when it adds nothing.

    Signature changes, removals and deletions are not additions. A moved/renamed file only adds the public symbols
    absent from its source. Synthetic diffs (identical git blobs) never count as additions so existing mocked-diff
    behavior is preserved. ``symbol_diff`` reuses an already computed diff (one AST parse per file version).
    """
    old_content, new_content = smap.versions_of(cf)
    if synthetic or new_content is None:
        return None
    if moved_from is None and old_content is None:
        diff = smap.diff(cf)
        return _StructuralAddition(
            file=cf,
            reasons=[f"added file '{cf}'"],
            symbol_names=[diff.qualified_name(k) for k in diff.added],
            is_new_file=True,
        )
    diff = smap.diff(cf, old_from=moved_from) if moved_from else (symbol_diff or smap.diff(cf))
    if not diff.added:
        return None
    return _StructuralAddition(
        file=cf,
        reasons=[f"added {k[0]} '{diff.qualified_name(k)}'" for k in diff.added],
        symbol_names=[diff.qualified_name(k) for k in diff.added],
        is_new_file=False,
    )


def _split_spec(content: str | None) -> tuple[str | None, str]:
    """Split a spec into (raw frontmatter text or None, AsciiDoc body)."""
    text = content or ""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end() :]


def _normalized_body_lines(body: str) -> list[str]:
    """AsciiDoc body lines without comments or blank lines, inner whitespace collapsed.

    ``//`` line comments and ``////`` comment blocks are dropped, except inside verbatim blocks (``----`` listing,
    ``....`` literal, ``++++`` passthrough) whose content - e.g. ``// ...`` lines of code samples - is kept.
    """
    out: list[str] = []
    open_delim: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if open_delim is not None:
            if line == open_delim:
                if not _ADOC_COMMENT_DELIM_RE.match(open_delim):
                    out.append(line)
                open_delim = None
            elif line and not _ADOC_COMMENT_DELIM_RE.match(open_delim):
                out.append(_normalize_ws(line))
            continue
        if _ADOC_COMMENT_DELIM_RE.match(line) or _ADOC_VERBATIM_DELIM_RE.match(line):
            open_delim = line
            if not _ADOC_COMMENT_DELIM_RE.match(line):
                out.append(line)
            continue
        if not line or _ADOC_LINE_COMMENT_RE.match(raw):
            continue
        out.append(_normalize_ws(line))
    return out


def _parse_frontmatter(raw: str | None) -> dict[str, Any] | None:
    """Parsed frontmatter mapping, ``{}`` when absent, ``None`` when it is not valid YAML."""
    if raw is None:
        return {}
    try:
        import yaml

        data = yaml.safe_load(raw)
    except Exception:
        return None
    if data is None:
        return {}
    return data if isinstance(data, dict) else None


def _spec_frontmatter(content: str | None) -> dict[str, Any]:
    return _parse_frontmatter(_split_spec(content)[0]) or {}


def _canonical(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _declared_source_patterns(frontmatter: dict[str, Any]) -> list[str]:
    """Explicit file/glob ownership declared by a spec (top-level and component ``source_path``/``owned_path``)."""
    patterns: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            patterns.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                _add(item)

    for key in ("source_path", "owned_path"):
        _add(frontmatter.get(key))
    for comp in frontmatter.get("components", []) or frontmatter.get("c4_internal_components", []) or []:
        if isinstance(comp, dict):
            _add(comp.get("source_path"))
            _add(comp.get("owned_path"))
    return patterns


class _SpecDiffInspector:
    """Per-run, cached inspection of how a modified specification changed (DLT-2026-039)."""

    def __init__(self, root: Path, base_ref: str | None, head_ref: str, uncommitted: bool) -> None:
        self._root = root
        self._base_ref = base_ref
        self._head_ref = head_ref
        self._uncommitted = uncommitted
        self._versions: dict[str, tuple[str | None, str | None]] = {}

    def versions(self, spec: str) -> tuple[str | None, str | None]:
        if spec not in self._versions:
            self._versions[spec] = _get_file_versions(
                self._root,
                spec,
                base_ref=self._base_ref,
                head_ref=self._head_ref,
                uncommitted=self._uncommitted,
            )
        return self._versions[spec]

    def is_new_spec(self, spec: str) -> bool:
        old, new = self.versions(spec)
        return old is None and new is not None

    def is_substantive(self, spec: str) -> bool:
        """True when the frontmatter data or the comment/whitespace-normalized body differs.

        Frontmatter is compared as parsed YAML (so YAML comments and formatting are ignored, while ``#`` inside
        block strings is content); invalid YAML falls back to whitespace-normalized text. New, deleted and
        synthetic (identical) versions count as substantive.
        """
        old, new = self.versions(spec)
        if old is None or new is None or old == new:
            return True
        old_raw, old_body = _split_spec(old)
        new_raw, new_body = _split_spec(new)
        old_fm, new_fm = _parse_frontmatter(old_raw), _parse_frontmatter(new_raw)
        if old_fm is None or new_fm is None:
            fm_changed = _normalize_ws(old_raw or "") != _normalize_ws(new_raw or "")
        else:
            fm_changed = _canonical(old_fm) != _canonical(new_fm)
        if fm_changed:
            return True
        return _normalize_ws(" ".join(_normalized_body_lines(old_body))) != _normalize_ws(
            " ".join(_normalized_body_lines(new_body))
        )

    def declares_at_base(self, spec: str, cf: str) -> bool:
        """True when the spec, at the base revision, already declared ``cf`` via source_path/owned_path."""
        from eidos.core.workspace.project import match_path_pattern

        old, _ = self.versions(spec)
        return any(match_path_pattern(cf, p) > 0 for p in _declared_source_patterns(_spec_frontmatter(old)))

    def is_anchored(self, spec: str, cf: str, symbol_names: list[str]) -> bool:
        """True when the spec edit changes ownership keys or its added content names the file or a new symbol.

        Accepted names: the repo-relative path, the file name *with* extension, and the qualified names of the new
        public symbols (``Class.method`` for methods). A bare file stem is not enough, so generic words such as
        "utils" do not anchor ``utils.py``.
        """
        from collections import Counter

        old, new = self.versions(spec)
        if old is None:
            return True
        if new is None:
            return False
        old_raw, old_body = _split_spec(old)
        new_raw, new_body = _split_spec(new)
        old_fm, new_fm = _parse_frontmatter(old_raw) or {}, _parse_frontmatter(new_raw) or {}
        if any(_canonical(old_fm.get(k)) != _canonical(new_fm.get(k)) for k in _OWNERSHIP_KEYS):
            return True

        added = list((Counter(_normalized_body_lines(new_body)) - Counter(_normalized_body_lines(old_body))).elements())
        added += [_canonical(v) for k, v in new_fm.items() if _canonical(v) != _canonical(old_fm.get(k))]
        needles = {cf, Path(cf).name, *symbol_names}
        patterns = [
            re.compile(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])")
            for n in sorted(needles)
            if n and len(n) >= _MIN_ANCHOR_LEN
        ]
        return any(p.search(line) for line in added for p in patterns)


def _spec_coverage_verdict(
    changed_specs: list[str],
    allowed_paths: set[str],
    allowed_ids: set[str],
    *,
    addition: _StructuralAddition | None = None,
    owner_spec: str = "",
    inspector: _SpecDiffInspector | None = None,
) -> tuple[bool, str | None, list[str]]:
    """Decide whether modified specs cover a code file; returns ``(covered, rejection_rule, rejected_specs)``.

    Without structural additions the rule is the classic 1-hop path check. For structural additions only
    *substantive* spec diffs count, and new files outside the owner's declared ``source_path``/``owned_path``
    additionally need an *anchored* owner-spec diff. ``rejection_rule`` is ``"trivial_spec_diff"`` or
    ``"missing_anchor"`` when 1-hop specs were touched but rejected, otherwise ``None``.
    """
    relevant = _relevant_changed_specs(changed_specs, allowed_paths, allowed_ids)
    if not relevant:
        return False, None, []
    if addition is None or inspector is None:
        return True, None, []

    substantive = [s for s in relevant if inspector.is_substantive(s)]
    if not substantive:
        return False, "trivial_spec_diff", relevant

    if addition.is_new_file and owner_spec:
        owner_norm = owner_spec.replace("\\", "/").lower()
        owner_changed = [s for s in substantive if s.replace("\\", "/").lower() == owner_norm]
        owner_is_new = any(inspector.is_new_spec(s) for s in owner_changed)
        needs_anchor = not owner_is_new and not inspector.declares_at_base(owner_spec, addition.file)
        if needs_anchor and not any(
            inspector.is_anchored(s, addition.file, addition.symbol_names) for s in owner_changed
        ):
            return False, "missing_anchor", relevant
    return True, None, []


def _resolve_bound_design_ids(idx, cf: str, owner_ids: set[str]) -> set[str]:
    """Resolve the design clients (CLT-*) and design systems (DS-*) bound to a frontend file via the graph."""
    bound: set[str] = set()
    if idx is None:
        return bound
    for m in idx.resolve_symbol_or_path(cf):
        if m.get("type") in ("design/client", "design/system") and m.get("entity_id"):
            bound.add(m["entity_id"])
    for oid in owner_ids:
        for n in idx.get_k_hop_neighborhood(oid, max_hops=1):
            if n.get("type") in ("design/client", "design/system") and n.get("id"):
                bound.add(n["id"])
    return bound


def _collect_design_subgraph(
    design_entities: list[dict[str, Any]],
    bound_ids: set[str],
) -> tuple[set[str], set[str]]:
    """Return (spec_paths, entity_ids) of the design subgraph owned by the bound CLT-*/DS-* entities.

    Includes the bound client/system specs, their design-system ancestry, design token manifests,
    UI components of the bound systems, and screens/flows of the bound clients.
    """
    by_id = {e.get("id"): e for e in design_entities if e.get("id")}
    ids = {b for b in bound_ids if b in by_id}
    # Expand client -> design system and design system -> extended system ancestry
    changed = True
    while changed:
        changed = False
        for eid in list(ids):
            e = by_id.get(eid, {})
            for key in ("design_system", "extends_system"):
                ref = e.get(key)
                if isinstance(ref, str) and ref in by_id and ref not in ids:
                    ids.add(ref)
                    changed = True

    paths: set[str] = set()
    sub_ids: set[str] = set(ids)
    for eid in ids:
        e = by_id[eid]
        fp = str(e.get("file_path") or "").replace("\\", "/")
        if fp:
            paths.add(fp.lower())
        if e.get("type") == "design/system":
            tokens = e.get("tokens") if isinstance(e.get("tokens"), dict) else {}
            for tok in (tokens.get("manifest"), e.get("tokens_path")):
                if isinstance(tok, str) and tok and fp:
                    tok_path = tok if "/" in tok else str(Path(fp).parent / tok)
                    paths.add(tok_path.replace("\\", "/").lower())
        if e.get("type") == "design/client":
            for key in ("screens", "flows"):
                for ref in e.get(key, []) or []:
                    if isinstance(ref, str):
                        paths.add(ref.replace("\\", "/").lower())

    systems = {i for i in ids if by_id[i].get("type") == "design/system"}
    clients = {i for i in ids if by_id[i].get("type") == "design/client"}
    for e in design_entities:
        eid = e.get("id")
        if not eid or eid in ids:
            continue
        etype = e.get("type")
        member = False
        if etype == "design/ui-component":
            member = e.get("design_system") in systems or e.get("implements_component") in sub_ids
        elif etype in ("design/screen", "design/flow"):
            member = e.get("client") in clients
        if member:
            sub_ids.add(eid)
            fp = str(e.get("file_path") or "").replace("\\", "/")
            if fp:
                paths.add(fp.lower())
    return paths, sub_ids


def detect_drift(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    eidos_dir: Path | None = None,
    uncommitted: bool = False,
) -> DriftCheckResult:
    """Run drift detection: evaluate spec-code synchronization, schema validation, and invariant check."""
    from eidos.core.index.ast_indexer import STYLE_EXTS, classify_design_changes, has_structural_changes
    from eidos.core.index.graph_index import get_architectural_index

    root = repo_root.resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    changed_files = get_git_diff_files(root, base_ref=base_ref, head_ref=head_ref, uncommitted=uncommitted)
    classification = classify_changed_files(changed_files, repo_root=root)

    # 0. Filter code files into structural vs body-only edits (DLT-2026-028)
    #    and classify frontend/styling diffs into design categories (DLT-2026-030)
    structural_code_files: list[str] = []
    body_only_code_files: list[str] = []
    structural_additions: dict[str, _StructuralAddition] = {}
    design_changes: dict[str, tuple[str, list[str]]] = {}
    versions = {
        cf: _get_file_versions(root, cf, base_ref=base_ref, head_ref=head_ref, uncommitted=uncommitted)
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
                    root, df, base_ref=base_ref, head_ref=head_ref, uncommitted=uncommitted
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
    spec_inspector = _SpecDiffInspector(root, base_ref, head_ref, uncommitted)
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
                addition = structural_additions[cf]
                trivial_records.append(
                    {
                        "file": cf,
                        "owner": str(spath),
                        "additions": list(addition.reasons),
                        "spec_files": sorted(rejected_specs),
                        "rule": rejected_rule,
                    }
                )
            if not covered_by_spec:
                (uncovered_components if has_deltas else uncovered_direct_components).append((cf, cid, spath))

    # Unmapped structural code (no CNT-*/CMP-* owner): reported for remediation. System-level specs
    # remain valid synchronization targets for these files only (DLT-2026-030).
    mapped_files = {c for c, _, _ in comp_mappings}
    unmapped_code_files = [cf for cf in structural_code_files if cf not in mapped_files]

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
        covered = any(_path_in(s, satisfying_paths) for s in classification.spec_files)
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
    drift_detected = base_direct_drift or design_drift or uncovered_delta_drift

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
                "• Run 'eidos reconcile' or ask your AI agent to update corresponding living specifications."
            )

    if base_direct_drift and unmapped_code_files:
        remediation.append(
            f"Unmapped Code: {len(unmapped_code_files)} structural code file(s) are not owned by any container or component."
        )
        for ucf in unmapped_code_files[:5]:
            remediation.append(
                f"• '{ucf}' -> declare ownership via 'package_path'/'source_path' in eidos/architecture/ (or update the system architecture spec)."
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
        unmapped_code_files=unmapped_code_files,
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
    classification = classify_changed_files(changed_files, repo_root=root)
    comp_mappings = map_code_to_components(target_eidos, classification.code_files, repo_root=root)
    catalog = get_living_specs_catalog(target_eidos, repo_root=root)

    suggestions: list[ReconcileSuggestion] = []
    file_to_comps: dict[str, list[str]] = {}
    for cf, cid, spath in comp_mappings:
        file_to_comps.setdefault(cf, []).append(f"{cid} ({spath})")

    from eidos.core.index.graph_index import get_architectural_index

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
