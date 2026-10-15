"""Git diff extraction, commit range resolution, and file classification (CMP-DRIFT-DETECTOR).

Handles deterministic merge-base resolution, fail-closed base Git reference checks,
polyglot test detection, changed files classification, and component mapping.
"""

from __future__ import annotations

import contextlib
import functools
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from eidos.core.workspace.project import (
    extract_entity_paths,
    is_generated_or_vendored,
    matches_owned_paths,
)


class GitResolutionError(RuntimeError):
    """Raised when an explicit base Git reference cannot be resolved or has no merge base."""


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


@functools.lru_cache(maxsize=64)
def _query_merge_base(repo: Path, base_ref: str, head_ref: str = "HEAD") -> str | None:
    """Cached git merge-base query."""
    try:
        check = subprocess.run(
            ["git", "merge-base", base_ref, head_ref],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode == 0:
            sha = check.stdout.strip()
            if sha:
                return sha
    except OSError:
        pass
    return None


def _resolve_merge_base(
    repo: Path, base_ref: str, head_ref: str = "HEAD", fail_loudly: bool = False
) -> str | None:
    """Resolve the merge-base commit SHA between base_ref and head_ref (DLT-2026-053)."""
    sha = _query_merge_base(repo, base_ref, head_ref)
    if sha:
        return sha
    if fail_loudly:
        raise GitResolutionError(
            f"Cannot find merge base between '{base_ref}' and '{head_ref}'. Ensure commits share a common ancestor."
        )
    return None


def _resolve_base_ref(repo: Path, base_ref: str | None = None) -> str | None:
    """Resolve the candidate base Git commit reference once per drift run (DLT-2026-052, DLT-2026-053)."""
    if base_ref:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            return base_ref
        raise GitResolutionError(
            f"Cannot resolve base Git reference '{base_ref}'. Ensure the reference exists and is fetched (e.g. git fetch origin <branch>)."
        )

    for candidate in ["origin/main", "main", "origin/master", "master", "HEAD~1"]:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            return candidate
    return None


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
                            src_path, raw_path = (part.strip() for part in raw_path.split(" -> ", 1))
                            files.add(src_path)
                        files.add(raw_path)
        except OSError:
            pass
        return sorted(files)

    # Determine base_ref and merge_base (DLT-2026-053)
    resolved_base = base_ref
    resolved_mb: str | None = None

    if resolved_base:
        _resolve_base_ref(repo, resolved_base)
        resolved_mb = _resolve_merge_base(repo, resolved_base, head_ref, fail_loudly=True)
    else:
        for candidate in ["origin/main", "main", "origin/master", "master", "HEAD~1"]:
            check = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=repo,
                capture_output=True,
                check=False,
            )
            if check.returncode == 0:
                mb = _resolve_merge_base(repo, candidate, head_ref, fail_loudly=False)
                if mb:
                    resolved_base = candidate
                    resolved_mb = mb
                    break

    if not resolved_base:
        return get_git_diff_files(repo_root, uncommitted=True)

    try:
        diff_base = resolved_mb or f"{resolved_base}...{head_ref}"
        if resolved_mb:
            cmd = ["git", "diff", "--name-only", "--no-renames", resolved_mb, head_ref]
        else:
            cmd = ["git", "diff", "--name-only", "--no-renames", f"{resolved_base}...{head_ref}"]

        res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return sorted(lines)

        if base_ref:
            raise GitResolutionError(
                f"Failed to execute git diff against '{diff_base}': {res.stderr.strip()}"
            )
    except OSError as err:
        if base_ref:
            raise GitResolutionError(f"Failed to execute git diff: {err}") from err

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

    if rel_path.is_absolute():
        parent_names = [parent.name.lower() for parent in rel_path.parents[:2]]
        in_test_dir = any(d in ("tests", "test", "__tests__") for d in parent_names)
        is_under_src = False
        is_src_test = False
    else:
        dir_parts = [part.lower() for part in rel_path.parent.parts]
        is_under_src = bool(dir_parts and dir_parts[0] == "src")
        is_src_test = any(
            dir_parts[i] == "src" and dir_parts[i + 1] == "test" for i in range(len(dir_parts) - 1)
        )
        in_test_dir = (
            (bool(dir_parts and dir_parts[0] in ("tests", "test", "__tests__")))
            or is_src_test
            or (not is_under_src and any(part in ("tests", "test", "__tests__") for part in dir_parts))
        )

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

    if name.endswith("_test.go"):
        return True

    if name.endswith("_test.rs") or (in_test_dir and p.suffix == ".rs"):
        return True

    if any(
        name.endswith(ext)
        for ext in (
            "Test.java",
            "Test.kt",
            "Spec.kt",
        )
    ):
        return True

    if p.suffix == ".py":
        if is_under_src and not is_src_test:
            return bool(in_test_dir)
        if name.startswith("test_") or name.endswith("_test.py"):
            return True

    return bool(in_test_dir and p.suffix in {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt"})


def collect_polyglot_test_files(repo_root: Path) -> dict[str, str]:
    """Scan the repository for test files across Python, TS/JS, Go, Rust, and Java/Kotlin."""
    all_tests_content: dict[str, str] = {}
    root = repo_root.resolve()

    if not root.exists():
        return all_tests_content

    for dirpath, dirnames, filenames in os.walk(root):
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


def classify_changed_files(
    files: list[str],
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
) -> ChangedFilesClassification:
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
    if eidos_dir is not None:
        try:
            eff_eidos = eidos_dir.resolve().relative_to(effective_root)
            eidos_rel_parts = [p.lower() for p in eff_eidos.parts]
        except ValueError:
            eidos_rel_parts = [p.lower() for p in eidos_dir.parts]
    else:
        eidos_rel_parts = ["eidos"]
    prefix_len = len(eidos_rel_parts)

    candidate_code_paths: list[str] = []
    parsed_files: list[tuple[str, Path, Path, list[str]]] = []
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
        parsed_files.append((f_str, f, rel_f, parts))

        is_in_eidos = len(parts) >= prefix_len and parts[:prefix_len] == eidos_rel_parts
        is_doc = f.suffix in {".md", ".rst"} or (bool(parts and parts[0] == "docs"))
        if (
            not is_in_eidos
            and not is_polyglot_test_file(rel_f, repo_root=effective_root)
            and not is_doc
            and f.suffix in code_extensions
            and not any(skip in parts for skip in [".venv", "venv", "node_modules", "dist", "build", "__pycache__"])
        ):
            candidate_code_paths.append(str(rel_f).replace("\\", "/"))
    import sys
    drift_mod = sys.modules.get("eidos.vcs.drift")
    vendored_fn = getattr(drift_mod, "is_generated_or_vendored", is_generated_or_vendored) if drift_mod else is_generated_or_vendored
    vendored_paths = (
        vendored_fn(effective_root, candidate_code_paths)
        if candidate_code_paths
        else set()
    )

    for f_str, f, rel_f, parts in parsed_files:
        is_in_eidos = len(parts) >= prefix_len and parts[:prefix_len] == eidos_rel_parts
        sub_parts = parts[prefix_len:] if is_in_eidos else []

        if f.name == ".gitkeep" or (
            is_in_eidos
            and (any(p.startswith(".") for p in sub_parts) or ("deltas" in sub_parts and "by-status" in sub_parts))
        ):
            classification.other_files.append(f_str)
            continue

        if is_in_eidos and f.suffix in {".adoc", ".json", ".yaml", ".yml"}:
            if "deltas" in sub_parts:
                classification.delta_files.append(f_str)
            else:
                classification.spec_files.append(f_str)
            continue

        if is_polyglot_test_file(rel_f, repo_root=effective_root):
            classification.test_files.append(f_str)
            continue

        if f.suffix in {".md", ".rst"} or (bool(parts and parts[0] == "docs")):
            classification.doc_files.append(f_str)
            continue

        rel_str_norm = str(rel_f).replace("\\", "/")
        if (
            f.suffix in code_extensions
            and not any(skip in parts for skip in [".venv", "venv", "node_modules", "dist", "build", "__pycache__"])
            and rel_str_norm not in vendored_paths
            and str(rel_f) not in vendored_paths
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
        matches = idx.resolve_symbol_or_path(cf)
        for m in matches:
            if m.get("type") in ("tech/container", "tech/component"):
                mappings.append((cf, m["entity_id"], m["file_path"]))
                matched = True
                break

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

    return mappings


def _get_file_versions(
    repo_root: Path,
    rel_path: str,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
    merge_base: str | None = None,
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

    baseline_ref = merge_base
    if not baseline_ref:
        if base_ref:
            baseline_ref = _resolve_merge_base(repo, base_ref, head_ref, fail_loudly=False) or base_ref
        else:
            resolved_base = _resolve_base_ref(repo, None)
            if not resolved_base:
                return None, None
            baseline_ref = _resolve_merge_base(repo, resolved_base, head_ref, fail_loudly=False) or resolved_base

    old_content = _git_show(baseline_ref, norm_rel)
    new_content = _git_show(head_ref, norm_rel)
    return old_content, new_content
