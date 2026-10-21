"""Git diff extraction, commit range resolution, and file classification (CMP-DRIFT-DETECTOR).

Handles deterministic merge-base resolution, fail-closed base Git reference checks,
polyglot test detection, changed files classification, and component mapping.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from eidos.core.workspace.project import (
    STANDARD_EXCLUDED_DIRS,
    extract_entity_paths,
    get_tracked_files,
    is_generated_or_vendored,
    is_polyglot_test_file,
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
_PACKAGE_PATH_LINE_RE = re.compile(r"^source_path:[ \t]*[\"']?([^\"'\n#\[\]-][^\"'\n#]*)", re.MULTILINE)
_PACKAGE_PATH_BLOCK_RE = re.compile(
    r"^source_path:[ \t]*(.*?)$((?:\r?\n[ \t]+-[ \t]+.*$)*)",
    re.MULTILINE,
)


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


def _resolve_merge_base(repo: Path, base_ref: str, head_ref: str = "HEAD", fail_loudly: bool = False) -> str | None:
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
            if candidate in ("origin/main", "main", "origin/master", "master") and head_sha and cand_sha == head_sha:
                prev_check = subprocess.run(
                    ["git", "rev-parse", "--verify", "HEAD~1"],
                    cwd=repo,
                    capture_output=True,
                    check=False,
                )
                if prev_check.returncode == 0 and _resolve_merge_base(repo, "HEAD~1", "HEAD", fail_loudly=False):
                    continue
            return candidate
    return None


def _resolve_base_and_merge_base(
    repo: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
) -> tuple[str | None, str | None, bool]:
    """Resolve (resolved_base, resolved_merge_base, fell_back_to_uncommitted) in a single pass."""
    if base_ref is not None:
        resolved_base = _resolve_base_ref(repo, base_ref)
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
        resolved_mb = _resolve_merge_base(repo, resolved_base, head_ref, fail_loudly=True)
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

    candidate = _resolve_base_ref(repo, None)
    if not candidate:
        if (repo / ".git" / "shallow").exists():
            raise GitResolutionError(
                "Cannot resolve base Git reference in a shallow clone (.git/shallow detected). "
                "Fetch full history (fetch-depth: 0) or pass --uncommitted."
            )
        return None, None, True

    mb = _resolve_merge_base(repo, candidate, head_ref, fail_loudly=False)
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
    start_idx = _CANDIDATE_BASE_REFS.index(candidate) + 1 if candidate in _CANDIDATE_BASE_REFS else 0
    for rem_cand in _CANDIDATE_BASE_REFS[start_idx:]:
        check = subprocess.run(
            ["git", "rev-parse", "--verify", rem_cand],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if check.returncode == 0:
            cand_sha = _stdout_str(check)
            if rem_cand in ("origin/main", "main", "origin/master", "master") and head_sha and cand_sha == head_sha:
                prev_check = subprocess.run(
                    ["git", "rev-parse", "--verify", "HEAD~1"],
                    cwd=repo,
                    capture_output=True,
                    check=False,
                )
                if prev_check.returncode == 0 and _resolve_merge_base(repo, "HEAD~1", head_ref, fail_loudly=False):
                    continue
            rem_mb = _resolve_merge_base(repo, rem_cand, head_ref, fail_loudly=False)
            if rem_mb:
                return rem_cand, rem_mb, False

    if (repo / ".git" / "shallow").exists():
        raise GitResolutionError(
            "Cannot resolve merge base in a shallow clone (.git/shallow detected). "
            "Fetch full history (fetch-depth: 0) or pass --uncommitted."
        )
    return None, None, True


def get_git_diff_files(
    repo_root: Path,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
    *,
    merge_base: str | None = None,
) -> list[str]:
    """Retrieve list of modified and added files using Git with core.quotePath=false."""
    repo = repo_root.resolve()

    if uncommitted:
        # Check uncommitted changes (working tree + staging + untracked)
        files: set[str] = set()
        try:
            diff_res = subprocess.run(
                ["git", "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_res.returncode == 0:
                files.update(line.strip() for line in diff_res.stdout.splitlines() if line.strip())

            staged_res = subprocess.run(
                ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--no-renames"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if staged_res.returncode == 0:
                files.update(line.strip() for line in staged_res.stdout.splitlines() if line.strip())

            status_res = subprocess.run(
                ["git", "-c", "core.quotePath=false", "status", "--porcelain", "--no-renames", "-uall"],
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
            cmd = ["git", "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", resolved_mb, head_ref]
        else:
            cmd = [
                "git",
                "-c",
                "core.quotePath=false",
                "diff",
                "--name-only",
                "--no-renames",
                f"{resolved_base}...{head_ref}",
            ]

        res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return sorted(lines)

        if base_ref:
            raise GitResolutionError(f"Failed to execute git diff against '{diff_base}': {res.stderr.strip()}")
    except OSError as err:
        if base_ref:
            raise GitResolutionError(f"Failed to execute git diff: {err}") from err

    return get_git_diff_files(repo_root, uncommitted=True)


def _has_structural_ast_change(
    old_content: str | None,
    new_content: str | None,
    file_path: Path | str,
) -> bool:
    """Return True if (old_content, new_content) for file_path has structural AST symbol changes."""
    from eidos.core.index.ast_indexer import has_structural_changes

    is_structural, _ = has_structural_changes(old_content, new_content, file_path)
    return is_structural


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


def collect_polyglot_test_files(repo_root: Path) -> dict[str, str]:
    """Scan the repository for test files across Python, TS/JS, Go, Rust, and Java/Kotlin."""
    all_tests_content: dict[str, str] = {}
    root = repo_root.resolve()

    if not root.exists():
        return all_tests_content

    if (root / ".git").exists():
        tracked_rels = get_tracked_files(root)
        if tracked_rels:
            for rel in tracked_rels:
                rel_parts = Path(rel).parts
                if any(d in STANDARD_EXCLUDED_DIRS or d.startswith(".") for d in rel_parts[:-1]):
                    continue
                if is_polyglot_test_file(Path(rel), repo_root=root):
                    try:
                        all_tests_content[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
            return all_tests_content

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in STANDARD_EXCLUDED_DIRS and not d.startswith(".")]

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
            for m in _PACKAGE_PATH_BLOCK_RE.finditer(content):
                inline_val = m.group(1).split("#", 1)[0].strip()
                block_items = m.group(2)
                candidates: list[str] = []
                if inline_val:
                    if inline_val.startswith("[") and inline_val.endswith("]"):
                        candidates.extend(inline_val[1:-1].split(","))
                    else:
                        candidates.append(inline_val)
                if block_items:
                    for line in block_items.splitlines():
                        stripped = line.split("#", 1)[0].strip()
                        if stripped.startswith("-"):
                            candidates.append(stripped[1:].strip())
                for cand in candidates:
                    val = cand.strip().strip("\"'").strip().rstrip("/")
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
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".kts",
    }

    effective_root = (repo_root or Path.cwd()).resolve()
    if eidos_dir is not None:
        resolved_eidos = (effective_root / eidos_dir).resolve() if not eidos_dir.is_absolute() else eidos_dir.resolve()
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
        list(package_paths) if package_paths is not None else _discover_container_package_paths(resolved_eidos)
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
        is_eidos_ignored = f.name == ".gitkeep" or (is_in_eidos and any(p.startswith(".") for p in sub_parts))
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
            and not any(skip in STANDARD_EXCLUDED_DIRS for skip in parts[:-1])
        ):
            candidate_code_paths.append(str(rel_f).replace("\\", "/"))

    vendored_paths = is_generated_or_vendored(effective_root, candidate_code_paths) if candidate_code_paths else set()

    for f_str, f, rel_f, parts, is_in_eidos, sub_parts, is_test in parsed_files:
        if f.name == ".gitkeep" or (is_in_eidos and any(p.startswith(".") for p in sub_parts)):
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
            and not any(skip in STANDARD_EXCLUDED_DIRS for skip in parts[:-1])
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


def batch_read_git_blobs(
    repo_root: Path,
    object_specs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], str | None]:
    """Read multiple (ref, rel_path) blobs in a single `git cat-file --batch` pipe."""
    repo = repo_root.resolve()
    unique_specs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref, raw_path in object_specs:
        norm_rel = raw_path.replace("\\", "/").strip("\r\n")
        key = (ref.strip("\r\n"), norm_rel)
        if key[0] and norm_rel and key not in seen:
            seen.add(key)
            unique_specs.append(key)

    results: dict[tuple[str, str], str | None] = dict.fromkeys(unique_specs)
    if not unique_specs:
        return results

    stdin_bytes = "".join(f"{ref}:{rel}\n" for ref, rel in unique_specs).encode("utf-8")
    try:
        res = subprocess.run(
            ["git", "-c", "core.quotePath=false", "cat-file", "--batch"],
            cwd=repo,
            input=stdin_bytes,
            capture_output=True,
            check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return results
        out = res.stdout
        pos = 0
        out_len = len(out)
        for key in unique_specs:
            if pos >= out_len:
                break
            nl_idx = out.find(b"\n", pos)
            if nl_idx == -1:
                break
            header = out[pos:nl_idx].decode("utf-8", errors="replace")
            pos = nl_idx + 1
            if header.endswith((" missing", " ambiguous", " excluded")) or not header:
                continue
            parts = header.rsplit(" ", 2)
            if len(parts) < 3 or not parts[2].isdigit():
                continue
            obj_type = parts[1]
            size = int(parts[2])
            blob_bytes = out[pos : pos + size]
            pos += size + 1
            if obj_type == "blob":
                results[key] = blob_bytes.decode("utf-8", errors="replace")
    except OSError:
        pass
    return results


def _get_files_versions_batch(
    repo_root: Path,
    rel_paths: Iterable[str],
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
    merge_base: str | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """Retrieve {rel_path: (old_content, new_content)} for multiple files in a single git cat-file --batch pass."""
    path_list = list(dict.fromkeys(rel_paths))
    if not path_list:
        return {}
    repo = repo_root.resolve()
    norm_map = {rp: rp.replace("\\", "/") for rp in path_list}

    if uncommitted:
        blobs = batch_read_git_blobs(repo, [("HEAD", norm_rel) for norm_rel in norm_map.values()])
        out: dict[str, tuple[str | None, str | None]] = {}
        for rp, norm_rel in norm_map.items():
            old_content = blobs.get(("HEAD", norm_rel))
            abs_p = repo / norm_rel
            new_content = None
            if abs_p.exists() and abs_p.is_file():
                with contextlib.suppress(OSError):
                    new_content = abs_p.read_text(encoding="utf-8", errors="replace")
            out[rp] = (old_content, new_content)
        return out

    baseline_ref = merge_base
    if not baseline_ref:
        if base_ref:
            baseline_ref = _resolve_merge_base(repo, base_ref, head_ref, fail_loudly=True)
        else:
            _, baseline_ref, fell_back = _resolve_base_and_merge_base(repo, None, head_ref)
            if fell_back or not baseline_ref:
                return dict.fromkeys(path_list, (None, None))

    if not baseline_ref:
        return dict.fromkeys(path_list, (None, None))

    requests: list[tuple[str, str]] = []
    for norm_rel in norm_map.values():
        requests.append((baseline_ref, norm_rel))
        requests.append((head_ref, norm_rel))
    blobs = batch_read_git_blobs(repo, requests)
    return {
        rp: (blobs.get((baseline_ref, norm_rel)), blobs.get((head_ref, norm_rel))) for rp, norm_rel in norm_map.items()
    }


def _get_file_versions(
    repo_root: Path,
    rel_path: str,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    uncommitted: bool = False,
    merge_base: str | None = None,
) -> tuple[str | None, str | None]:
    """Retrieve (old_content, new_content) for a modified file to evaluate structural AST diffs."""
    return _get_files_versions_batch(
        repo_root,
        [rel_path],
        base_ref=base_ref,
        head_ref=head_ref,
        uncommitted=uncommitted,
        merge_base=merge_base,
    ).get(rel_path, (None, None))
