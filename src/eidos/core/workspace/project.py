"""Zero-configuration project discovery, Git-native file enumeration, and path ownership matching."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eidos.core.spec.parser import _NON_ROOT_SPEC_SUBDIRS, parse_spec_file

STANDARD_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "third_party",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    "htmlcov",
    "__pycache__",
    ".eidos",
    ".cache",
    ".spec",
    "site",
    ".gradle",
    ".idea",
    ".vscode",
    ".vs",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".eggs",
    ".next",
    ".nyc_output",
    ".sass-cache",
}

_SPEC_SUBDIRS = frozenset(
    {
        "architecture",
        "domains",
        "product",
        "policies",
        "personas",
        "deltas",
        "explorations",
        "design",
    }
)
_REPO_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod")

_NESTED_TEST_DIR_NAMES = {"test", "tests", "__tests__"}
_ROOT_TEST_DIR_NAMES = {"test", "tests", "__tests__", "testing", "spec", "specs"}
_POLYGLOT_TEST_CODE_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".js",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
    }
)


@dataclass(frozen=True)
class ProjectConfig:
    """Resolved project layout and metadata derived from Git and eidos/system.adoc."""

    repo_root: Path
    eidos_dir: Path
    cache_dir: Path
    system_id: str | None
    system_title: str
    base_ref_candidates: tuple[str, ...] = (
        "origin/main",
        "main",
        "origin/master",
        "master",
        "HEAD~1",
    )


def _is_inside_plugin_or_assets(path: Path) -> bool:
    """Return True if path is inside a .agents, plugins, or assets bundle directory."""
    parts = path.parts
    for idx, part in enumerate(parts):
        if part == ".agents":
            return True
        if part == "plugins" and idx > 0 and parts[idx - 1] in (".agents", ".claude", ".cursor"):
            return True
        if part == "assets" and idx + 1 < len(parts) and parts[idx + 1] in ("plugin_reference", "schemas", "templates"):
            return True
    return False


def _has_eidos_markers(cand: Path) -> bool:
    """Return True if cand is an initialized Eidos specification directory (ignoring cache-only directories)."""
    if not cand.is_dir():
        return False
    if (cand / "system.adoc").is_file() or any((cand / sub).is_dir() for sub in _SPEC_SUBDIRS):
        return True
    return any(
        p.is_file() and not p.name.startswith("_") and p.name.lower() != "readme.adoc"
        for p in cand.glob("*.adoc")
    )


def find_git_root(start_dir: Path | None = None) -> Path | None:
    """Find the enclosing Git worktree root (.git) across ancestor directories, or None if not in a Git repo."""
    cwd = (start_dir or Path.cwd()).resolve()
    if cwd.is_file():
        cwd = cwd.parent

    for parent in (cwd, *cwd.parents):
        if (parent / ".git").exists():
            return parent

    return None


def discover_nested_subproject_roots(repo_root: Path) -> list[Path]:
    """Return resolved child directories under repo_root that contain their own independent eidos/ specification root."""
    root = repo_root.resolve()
    if not root.is_dir():
        return []
    nested: list[Path] = []
    skip_dirs = STANDARD_EXCLUDED_DIRS | {"eidos", "assets", "docs", "tests", "test", "__tests__"}
    for dirpath, dirnames, _filenames in os.walk(root):
        cur = Path(dirpath)
        if cur != root:
            eidos_cand = cur / "eidos"
            if _has_eidos_markers(eidos_cand) and not _is_inside_plugin_or_assets(eidos_cand):
                nested.append(cur.resolve())
                dirnames[:] = []
                continue
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
    return sorted(nested)


def find_repo_root(start_dir: Path | None = None) -> Path:
    """Find the nearest owning project/sub-project root containing eidos/ living specifications, or the enclosing .git root."""
    cwd = (start_dir or Path.cwd()).resolve()
    if cwd.is_file():
        cwd = cwd.parent

    for parent in (cwd, *cwd.parents):
        if parent.name == "eidos" and not _is_inside_plugin_or_assets(parent) and _has_eidos_markers(parent):
            return parent.parent
        eidos_cand = parent / "eidos"
        if eidos_cand.is_dir() and not _is_inside_plugin_or_assets(eidos_cand) and _has_eidos_markers(eidos_cand):
            return parent
        if (parent / ".git").exists():
            return parent

    if not _is_inside_plugin_or_assets(cwd) and (cwd.name == "eidos" or _has_eidos_markers(cwd)):
        return cwd.parent

    git_root = find_git_root(cwd)
    if git_root is not None:
        return git_root

    return cwd


def resolve_spec_and_repo_roots(
    root_dir: Path | None = None,
    doc_path: Path | None = None,
) -> tuple[Path, Path]:
    """Resolve (spec_root, repo_root) for standard ('eidos/', '.spec/'), custom ('specs/'), or subdirectory paths."""
    if root_dir is None and doc_path is None:
        repo = find_repo_root(None)
        return (repo / "eidos").resolve(), repo

    base = (root_dir if root_dir is not None else (doc_path.parent if doc_path is not None else Path.cwd())).resolve()
    if _is_inside_plugin_or_assets(base):
        repo = find_repo_root(base)
        return (repo / "eidos").resolve(), repo

    # 1. If base itself is a repo root containing a standard child spec directory ('eidos', '.spec', 'specs')
    for child_name in ("eidos", ".spec", "specs"):
        child = base / child_name
        if child.is_dir() and (child_name == ".spec" or _has_eidos_markers(child)):
            return child.resolve(), base

    if base.name in ("eidos", ".spec", "specs", "spec") and not any(
        p.name in ("eidos", ".spec", "specs", "spec") for p in base.parents
    ):
        return base, base.parent

    def _is_spec_root(cand: Path) -> bool:
        if _is_inside_plugin_or_assets(cand):
            return False
        if any(
            (cand / sub_name).is_dir() and (sub_name == ".spec" or _has_eidos_markers(cand / sub_name))
            for sub_name in ("eidos", ".spec")
        ):
            return False
        if cand.name == ".spec":
            return True
        if cand.name == "eidos":
            return _has_eidos_markers(cand)
        if (cand / "system.adoc").is_file():
            cand_lower = cand.name.lower()
            ancestor_lowers = [p.lower() for p in cand.parts[:-1]]
            if cand_lower not in _NON_ROOT_SPEC_SUBDIRS and (
                cand_lower == "eidos"
                or (
                    "eidos" not in ancestor_lowers
                    and (not cand.parent or cand.parent.name.lower() not in _NON_ROOT_SPEC_SUBDIRS)
                )
            ):
                return True
        if (cand / "architecture").is_dir():
            arch = cand / "architecture"
            if (
                any((arch / sub).is_dir() for sub in ("containers", "components", "contracts"))
                or any(arch.glob("*.adoc"))
                or any((cand / sub).is_dir() for sub in _SPEC_SUBDIRS - {"architecture"})
            ):
                return True
        return bool(cand.name in ("specs", "spec") and any((cand / sub).is_dir() for sub in _SPEC_SUBDIRS))

    def _repo_root_for_spec_root(spec_cand: Path) -> Path:
        if spec_cand.name in ("eidos", ".spec", "specs", "spec"):
            return spec_cand.parent
        if (spec_cand / "src").is_dir() or any((spec_cand / m).exists() for m in _REPO_MARKERS):
            return spec_cand
        if spec_cand.parent.name.startswith("pytest-"):
            return spec_cand
        return spec_cand.parent

    # 2. Walk (base, *base.parents) (and doc_path parents if provided) to find spec_root
    search_roots = [base]
    if doc_path is not None:
        res_doc_parent = doc_path.resolve().parent
        if res_doc_parent != base:
            search_roots.append(res_doc_parent)

    for start in search_roots:
        for depth, cand in enumerate((start, *start.parents)):
            if depth > 8:
                break
            if cand != start:
                try:
                    rel_first = start.relative_to(cand).parts[0]
                except ValueError:
                    break
                if rel_first in _SPEC_SUBDIRS and _is_spec_root(cand):
                    return cand, _repo_root_for_spec_root(cand)
                eidos_child = cand / "eidos"
                if (
                    "eidos" not in start.name.lower()
                    and "spec" not in start.name.lower()
                    and rel_first not in _SPEC_SUBDIRS
                    and eidos_child.is_dir()
                    and not _is_inside_plugin_or_assets(eidos_child)
                    and _has_eidos_markers(eidos_child)
                    and (
                        find_git_root(cand) is not None
                        or rel_first in ("src", "lib", "app", "tests", "test", "pkg", "cmd", "internal")
                    )
                ):
                    return eidos_child.resolve(), cand
            elif _is_spec_root(cand):
                return cand, _repo_root_for_spec_root(cand)
            if (cand / ".git").exists():
                break

    if base.name in ("eidos", ".spec", "specs", "spec"):
        return base, base.parent

    # 3. Check if an immediate or ancestor directory is a repo root with markers
    for depth, cand in enumerate((base.parent, *base.parents)):
        if depth > 5:
            break
        if any((cand / m).exists() for m in _REPO_MARKERS):
            try:
                rel_parts = base.relative_to(cand).parts
            except ValueError:
                break
            if rel_parts and rel_parts[0] not in ("src", "tests", "services", "packages"):
                spec_root = (cand / rel_parts[0]).resolve()
                return spec_root, cand
            break

    # 4. Fallback
    if (base.parent / "src").is_dir() or (base.parent / ".git").exists():
        return base, base.parent
    return base, base


def load_project(
    start_dir: Path | None = None,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> ProjectConfig:
    """Resolve project root, eidos directory, cache directory, and system metadata."""
    if repo_root is not None:
        root = Path(repo_root).resolve()
        resolved_eidos = (Path(eidos_dir) if eidos_dir is not None else (root / "eidos")).resolve()
    elif eidos_dir is not None:
        ed = Path(eidos_dir).resolve()
        spec_root, root = resolve_spec_and_repo_roots(ed)
        resolved_eidos = ed if ed.name == "eidos" or not spec_root.exists() else spec_root
    else:
        root = find_repo_root(start_dir)
        resolved_eidos = (root / "eidos").resolve()

    cache_dir = resolved_eidos / ".cache"

    system_id: str | None = None
    system_title: str = root.name

    sys_file = resolved_eidos / "system.adoc"
    if sys_file.is_file():
        try:
            doc = parse_spec_file(sys_file)
            fm = doc.frontmatter or {}
            if fm.get("id"):
                system_id = str(fm["id"]).strip()
            if fm.get("title") or doc.title:
                system_title = str(fm.get("title") or doc.title).strip()
        except OSError:
            pass

    return ProjectConfig(
        repo_root=root,
        eidos_dir=resolved_eidos,
        cache_dir=cache_dir,
        system_id=system_id,
        system_title=system_title,
    )


def is_generated_or_vendored(repo_root: Path, rel_paths: list[str] | str | Path) -> set[str]:
    """Return subset of rel_paths marked linguist-generated or linguist-vendored in .gitattributes."""
    rel_paths = [str(rel_paths)] if isinstance(rel_paths, (str, Path)) else [str(p) for p in rel_paths]

    if not rel_paths or find_git_root(repo_root) is None:
        return set()

    skipped: set[str] = set()
    try:
        norm_paths = [p.replace("\\", "/") for p in rel_paths]
        input_data = "\0".join(norm_paths)
        res = subprocess.run(
            [
                "git",
                "check-attr",
                "--stdin",
                "-z",
                "linguist-generated",
                "linguist-vendored",
            ],
            cwd=repo_root,
            input=input_data,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            tokens = res.stdout.split("\0")
            # Output format with -z: <path>\0<attr>\0<value>\0
            idx = 0
            while idx + 2 < len(tokens):
                path_str = tokens[idx]
                val_str = tokens[idx + 2].lower()
                if val_str in ("true", "set"):
                    skipped.add(path_str)
                idx += 3
    except OSError:
        pass

    return skipped


def _has_excluded_dir_part(rel_path: str) -> bool:
    """Return True if any directory component of rel_path is in STANDARD_EXCLUDED_DIRS."""
    parts = rel_path.replace("\\", "/").split("/")
    return any(part in STANDARD_EXCLUDED_DIRS for part in parts[:-1])


def get_tracked_files(repo_root: Path, include_untracked: bool = True) -> list[str]:
    """List repository files respecting .gitignore, .gitattributes generated/vendored attributes, and nested sub-project boundaries."""
    root = repo_root.resolve()
    if not root.exists():
        return []
    nested_roots = discover_nested_subproject_roots(root)
    nested_prefixes: tuple[str, ...] = tuple(
        f"{nr.relative_to(root).as_posix().strip('/')}/"
        for nr in nested_roots
        if nr.is_relative_to(root)
    )

    if find_git_root(root) is not None:
        files: set[str] = set()
        try:
            cmd = (
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
                if include_untracked
                else ["git", "ls-files", "-z"]
            )
            tracked = subprocess.run(
                cmd,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if tracked.returncode == 0:
                for item in tracked.stdout.split("\0"):
                    if not item or _has_excluded_dir_part(item):
                        continue
                    norm_item = item.replace("\\", "/")
                    if nested_prefixes and norm_item.startswith(nested_prefixes):
                        continue
                    if (root / item).is_file():
                        files.add(norm_item)

                if not files:
                    return []
                file_list = sorted(files)
                ignored_attrs = is_generated_or_vendored(root, file_list)
                return [f for f in file_list if f not in ignored_attrs]
        except OSError:
            pass

    # Fallback filesystem walk for non-git directories (e.g., temporary test fixtures)
    collected: list[str] = []
    nested_set = set(nested_roots)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in STANDARD_EXCLUDED_DIRS
            and not d.startswith(".")
            and (Path(dirpath) / d).resolve() not in nested_set
        ]
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                rel = str(full.relative_to(root)).replace("\\", "/")
                if nested_prefixes and rel.startswith(nested_prefixes):
                    continue
                collected.append(rel)
            except ValueError:
                continue
    return sorted(collected)


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
        in_test_dir = any(d in _ROOT_TEST_DIR_NAMES for d in parent_names)
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
                bool(dir_parts and dir_parts[0] in _ROOT_TEST_DIR_NAMES)
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
        return bool(nested_test_segment and p.suffix.lower() in _POLYGLOT_TEST_CODE_EXTS)

    if p.suffix == ".py" and (name.startswith("test_") or name.endswith("_test.py")):
        return True

    return bool(in_test_dir and p.suffix.lower() in _POLYGLOT_TEST_CODE_EXTS)


_PLACEHOLDER_PATHS = frozenset({"N/A", "TBD", "NONE"})


def _append_path_values(target: list[str], raw_val: Any) -> None:
    if isinstance(raw_val, str) and raw_val.strip():
        clean = raw_val.strip().replace("\\", "/")
        if clean.upper() not in _PLACEHOLDER_PATHS and clean not in target:
            target.append(clean)
    elif isinstance(raw_val, list):
        for item in raw_val:
            if isinstance(item, str) and item.strip():
                clean = item.strip().replace("\\", "/")
                if clean.upper() not in _PLACEHOLDER_PATHS and clean not in target:
                    target.append(clean)


def extract_entity_paths(entity_data: dict[str, Any]) -> list[str]:
    """Extract all declared ownership path patterns from source_path or specification_file."""
    patterns: list[str] = []

    for key in ("source_path", "specification_file"):
        _append_path_values(patterns, entity_data.get(key))

    return patterns


def _normalize_rel_path(raw: str) -> str:
    """Normalize a relative path or pattern without stripping leading dots from dotfiles."""
    s = raw.strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def _glob_to_anchored_regex(glob_pat: str) -> str:
    """Convert a POSIX path glob pattern into a full-path anchored regex where '*' does not cross '/'."""
    import re

    i = 0
    n = len(glob_pat)
    out = ["^"]
    while i < n:
        if glob_pat[i : i + 4] == "/**/":
            out.append("(?:/|/.+/)")
            i += 4
        elif glob_pat[i : i + 3] == "**/":
            out.append("(?:.+/)?")
            i += 3
        elif glob_pat[i : i + 3] == "/**":
            out.append("(?:/.*)?")
            i += 3
        elif glob_pat[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif glob_pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob_pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob_pat[i] == "[":
            j = i + 1
            if j < n and glob_pat[j] in ("!", "^"):
                j += 1
            if j < n and glob_pat[j] == "]":
                j += 1
            while j < n and glob_pat[j] != "]":
                j += 1
            if j < n:
                cls_body = glob_pat[i + 1 : j]
                if cls_body.startswith("!"):
                    cls_body = "^" + cls_body[1:]
                out.append(f"[{cls_body}]")
                i = j + 1
            else:
                out.append(re.escape("["))
                i += 1
        else:
            out.append(re.escape(glob_pat[i]))
            i += 1
    out.append("$")
    return "".join(out)


def match_path_pattern(rel_file: str, pattern: str) -> int:
    """Return specificity score (>0) if rel_file matches pattern, or 0 if no match.

    Higher scores indicate more specific ownership matches:
    - Exact file path match: 2000 + len(pattern)
    - Directory prefix or glob with leading directory prefix: 500 + len(leading_dir_prefix) + len(tail_literals)
    - Wildcard-only or root-level glob match (e.g. '**/*.css', '*.py'): 100 + len(tail_literals)
    """
    import re

    norm_file = _normalize_rel_path(rel_file)
    norm_pat = _normalize_rel_path(pattern)
    if not norm_file or not norm_pat:
        return 0

    # 1. Exact file match
    if not any(ch in norm_pat for ch in ("*", "?", "[")) and norm_file == norm_pat:
        return 2000 + len(norm_pat)

    # 2. Directory prefix without glob wildcards
    if not any(ch in norm_pat for ch in ("*", "?", "[")):
        prefix = norm_pat.rstrip("/")
        if norm_file.startswith(prefix + "/"):
            return 500 + len(prefix)
        return 0

    # 3. Glob pattern matching: compute literal directory prefix before the first wildcard segment
    parts = [p for p in norm_pat.split("/") if p]
    leading_parts: list[str] = []
    for idx, seg in enumerate(parts):
        if any(ch in seg for ch in ("*", "?", "[")):
            break
        # A single segment with no '/' has no directory prefix (it's a filename)
        if idx < len(parts) - 1:
            leading_parts.append(seg)
    leading_dir_prefix = "/".join(leading_parts)
    has_leading_dir = bool(leading_dir_prefix) and "/" in norm_pat
    tail_segments = parts[len(leading_parts) :]
    tail_literals = "".join(ch for seg in tail_segments for ch in seg if ch not in "*?[]/")

    # Handle 'dir/**' recursive directory prefix
    if norm_pat.endswith("/**") and not any(ch in norm_pat[:-3] for ch in ("*", "?", "[")):
        base_dir = norm_pat[:-3].rstrip("/")
        if norm_file == base_dir or norm_file.startswith(base_dir + "/"):
            return (500 if has_leading_dir else 100) + len(base_dir)
        return 0

    regex_str = _glob_to_anchored_regex(norm_pat)
    try:
        matched = bool(re.match(regex_str, norm_file))
    except re.error:
        matched = fnmatch.fnmatchcase(norm_file, norm_pat)

    if matched:
        if has_leading_dir:
            return 500 + len(leading_dir_prefix) + len(tail_literals)
        return 100 + len(tail_literals)

    return 0


def matches_owned_paths(rel_file: str, patterns: list[str]) -> tuple[bool, int]:
    """Check if rel_file matches any pattern in patterns; returns (matched, best_specificity_score)."""
    best = 0
    for pat in patterns:
        score = match_path_pattern(rel_file, pat)
        if score > best:
            best = score
    return (best > 0), best
