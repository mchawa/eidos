"""Git diff extraction, commit range resolution, and file classification (CMP-DRIFT-DETECTOR).

Handles deterministic merge-base resolution, fail-closed base Git reference checks,
polyglot test detection, changed files classification, and component mapping.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from collections.abc import Iterable
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


_CANDIDATE_BASE_REFS = ("origin/main", "main", "origin/master", "master", "HEAD~1")
_NESTED_TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs"}
_PACKAGE_PATH_LINE_RE = re.compile(r"^package_path:\s*[\"']?([^\"'\n#]+)", re.MULTILINE)


def _stdout_str(res: object) -> str:
    out = getattr(res, "stdout", None)
    if isinstance(out, bytes):
        return out.decode("utf-8", errors="replace").strip()
    if isinstance(out, str):
        return out.strip()
    return ""


def _query_merge_base(repo: Path, base_ref: str, head_ref: str = "HEAD") -> str | None:
    """Run git merge-base query for base_ref and head_ref."""
    try:
        check = subprocess.run(
            ["git", "merge-base", base_ref, head_ref],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode == 0:
            sha = _stdout_str(check)
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

    head_check = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    head_sha = _stdout_str(head_check) if head_check.returncode == 0 else ""

    for candidate in _CANDIDATE_BASE_REFS:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            cand_sha = _stdout_str(check)
            if candidate in ("main", "master") and head_sha and cand_sha == head_sha:
                prev_check = subprocess.run(
                    ["git", "rev-parse", "--verify", "HEAD~1"],
                    cwd=repo,
                    capture_output=True,
                    check=False,
                )
                if prev_check.returncode == 0 and _resolve_merge_base(
                    repo, "HEAD~1", "HEAD", fail_loudly=False
                ):
                    continue
            return candidate
    return None


def _resolve_base_and_merge_base(
    repo: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
) -> tuple[str | None, str | None, bool]:
    """Resolve (resolved_base, resolved_merge_base, fell_back_to_uncommitted) in a single pass."""
    drift_mod = sys.modules.get("eidos.vcs.drift")
    resolve_base_fn = (
        getattr(drift_mod, "_resolve_base_ref", _resolve_base_ref) if drift_mod else _resolve_base_ref
    )
    resolve_mb_fn = (
        getattr(drift_mod, "_resolve_merge_base", _resolve_merge_base) if drift_mod else _resolve_merge_base
    )

    if base_ref is not None:
        resolved_base = resolve_base_fn(repo, base_ref)
        if not resolved_base:
            raise GitResolutionError(
                f"Cannot resolve base Git reference '{base_ref}'. Ensure the reference exists and is fetched (e.g. git fetch origin <branch>)."
            )
        if head_ref != "HEAD":
            head_check = subprocess.run(
                ["git", "rev-parse", "--verify", head_ref],
                cwd=repo,
                capture_output=True,
                check=False,
            )
            if head_check.returncode != 0:
                raise GitResolutionError(
                    f"Cannot resolve head Git reference '{head_ref}'. Ensure the reference exists and is fetched."
                )
        resolved_mb = resolve_mb_fn(repo, resolved_base, head_ref, fail_loudly=True)
        return resolved_base, resolved_mb, False

    if head_ref != "HEAD":
        head_check = subprocess.run(
            ["git", "rev-parse", "--verify", head_ref],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if head_check.returncode != 0:
            raise GitResolutionError(
                f"Cannot resolve head Git reference '{head_ref}'. Ensure the reference exists and is fetched."
            )

    candidate = resolve_base_fn(repo, None)
    if not candidate:
        return None, None, True

    mb = resolve_mb_fn(repo, candidate, head_ref, fail_loudly=False)
    if mb:
        return candidate, mb, False

    # Fallback scan if the initial candidate had no common ancestor with head_ref
    head_check = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    head_sha = _stdout_str(head_check) if head_check.returncode == 0 else ""
    start_idx = (
        _CANDIDATE_BASE_REFS.index(candidate) + 1
        if candidate in _CANDIDATE_BASE_REFS
        else 0
    )
    for rem_cand in _CANDIDATE_BASE_REFS[start_idx:]:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", rem_cand],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            cand_sha = _stdout_str(check)
            if rem_cand in ("main", "master") and head_sha and cand_sha == head_sha:
                prev_check = subprocess.run(
                    ["git", "rev-parse", "--verify", "HEAD~1"],
                    cwd=repo,
                    capture_output=True,
                    check=False,
                )
                if prev_check.returncode == 0 and resolve_mb_fn(
                    repo, "HEAD~1", head_ref, fail_loudly=False
                ):
                    continue
            rem_mb = resolve_mb_fn(repo, rem_cand, head_ref, fail_loudly=False)
            if rem_mb:
                return rem_cand, rem_mb, False

    return None, None, True


def get_git_diff_files(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
    *,
    merge_base: str | None = None,
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

    if merge_base is not None:
        resolved_mb: str | None = merge_base
        resolved_base: str | None = base_ref or merge_base
    else:
        resolved_base, resolved_mb, fell_back = _resolve_base_and_merge_base(repo, base_ref, head_ref)
        if fell_back or not resolved_base:
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


def is_polyglot_test_file(
    path: Path | str,
    repo_root: Path | None = None,
    package_paths: Iterable[str] | None = None,
) -> bool:
    """Determine whether a file path corresponds to an automated test file across polyglot languages."""
    p = Path(path)
    name = p.name

    if repo_root is not None:
        root_res = repo_root.resolve()
        target_p = (root_res / p).resolve() if not p.is_absolute() else p.resolve()
        try:
            rel_path = target_p.relative_to(root_res)
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
        in_test_dir = any(d in _NESTED_TEST_DIR_NAMES for d in parent_names)
        is_under_src = False
        nested_test_segment = False
    else:
        dir_parts = [part.lower() for part in rel_path.parent.parts]
        is_under_src = bool(dir_parts and dir_parts[0] == "src")
        nested_dir_parts: list[str] = dir_parts[1:] if is_under_src else []

        if not is_under_src and package_paths:
            for raw_pkg in package_paths:
                norm_pkg = str(raw_pkg).strip().replace("\\", "/").strip("/").lower()
                if not norm_pkg or norm_pkg == ".":
                    continue
                pkg_parts = [pt for pt in norm_pkg.split("/") if pt]
                if pkg_parts and len(dir_parts) >= len(pkg_parts) and dir_parts[: len(pkg_parts)] == pkg_parts:
                    is_under_src = True
                    nested_dir_parts = dir_parts[len(pkg_parts) :]
                    break

        nested_test_segment = any(seg in _NESTED_TEST_DIR_NAMES for seg in nested_dir_parts)
        in_test_dir = (
            nested_test_segment
            if is_under_src
            else (
                bool(dir_parts and dir_parts[0] in _NESTED_TEST_DIR_NAMES)
                or any(part in _NESTED_TEST_DIR_NAMES for part in dir_parts)
            )
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

    if is_under_src:
        return bool(
            nested_test_segment
            and p.suffix in {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt"}
        )

    if p.suffix == ".py" and (name.startswith("test_") or name.endswith("_test.py")):
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


def _discover_container_package_paths(eidos_dir: Path) -> list[str]:
    """Fast scan of container specs for declared package_path prefixes."""
    cnt_dir = eidos_dir / "architecture" / "containers"
    if not cnt_dir.is_dir():
        return []
    pkg_paths: list[str] = []
    for adoc in cnt_dir.glob("*.adoc"):
        with contextlib.suppress(OSError):
            content = adoc.read_text(encoding="utf-8", errors="replace")
            for m in _PACKAGE_PATH_LINE_RE.finditer(content):
                val = m.group(1).strip()
                if val and val not in pkg_paths:
                    pkg_paths.append(val)
    return pkg_paths


def classify_changed_files(
    files: list[str],
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
    package_paths: Iterable[str] | None = None,
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
        resolved_eidos = (
            (effective_root / eidos_dir).resolve()
            if not eidos_dir.is_absolute()
            else eidos_dir.resolve()
        )
        try:
            eff_eidos = resolved_eidos.relative_to(effective_root)
            eidos_rel_parts = [p.lower() for p in eff_eidos.parts if p not in ("", ".")]
        except ValueError:
            eidos_rel_parts = [p.lower() for p in eidos_dir.parts if p not in ("", ".")]
    else:
        resolved_eidos = (effective_root / "eidos").resolve()
        eidos_rel_parts = ["eidos"]
    prefix_len = len(eidos_rel_parts)

    eff_pkg_paths = (
        list(package_paths)
        if package_paths is not None
        else _discover_container_package_paths(resolved_eidos)
    )

    candidate_code_paths: list[str] = []
    parsed_files: list[tuple[str, Path, Path, list[str], bool, list[str], bool]] = []
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
        is_in_eidos = prefix_len > 0 and len(parts) >= prefix_len and parts[:prefix_len] == eidos_rel_parts
        sub_parts = parts[prefix_len:] if is_in_eidos else []
        is_eidos_ignored = f.name == ".gitkeep" or (
            is_in_eidos
            and (any(p.startswith(".") for p in sub_parts) or ("deltas" in sub_parts and "by-status" in sub_parts))
        )
        is_eidos_spec = is_in_eidos and f.suffix in {".adoc", ".json", ".yaml", ".yml"}
        is_test = is_polyglot_test_file(rel_f, repo_root=effective_root, package_paths=eff_pkg_paths)
        is_doc = f.suffix in {".md", ".rst"} or (bool(parts and parts[0] == "docs"))
        parsed_files.append((f_str, f, rel_f, parts, is_in_eidos, sub_parts, is_test))

        if (
            not is_eidos_ignored
            and not is_eidos_spec
            and not is_test
            and not is_doc
            and f.suffix in code_extensions
            and not any(skip in parts for skip in [".venv", "venv", "node_modules", "dist", "build", "__pycache__"])
        ):
            candidate_code_paths.append(str(rel_f).replace("\\", "/"))

    drift_mod = sys.modules.get("eidos.vcs.drift")
    vendored_fn = (
        getattr(drift_mod, "is_generated_or_vendored", is_generated_or_vendored)
        if drift_mod
        else is_generated_or_vendored
    )
    vendored_paths = (
        vendored_fn(effective_root, candidate_code_paths)
        if candidate_code_paths
        else set()
    )

    for f_str, f, rel_f, parts, is_in_eidos, sub_parts, is_test in parsed_files:
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

        if is_test:
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
            baseline_ref = _resolve_merge_base(repo, base_ref, head_ref, fail_loudly=True)
        else:
            _, baseline_ref, fell_back = _resolve_base_and_merge_base(repo, None, head_ref)
            if fell_back or not baseline_ref:
                return None, None

    if not baseline_ref:
        return None, None

    old_content = _git_show(baseline_ref, norm_rel)
    new_content = _git_show(head_ref, norm_rel)
    return old_content, new_content
