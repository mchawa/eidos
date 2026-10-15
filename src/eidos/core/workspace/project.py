"""Zero-configuration project discovery, Git-native file enumeration, and path ownership matching."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from eidos.core.spec.parser import parse_spec_file

STANDARD_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".eidos",
    ".cache",
    ".spec",
    "site",
    ".idea",
    ".vscode",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "htmlcov",
}


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


def find_repo_root(start_dir: Path | None = None) -> Path:
    """Find the repository or workspace root containing .git or eidos/ living specifications."""
    cwd = (start_dir or Path.cwd()).resolve()
    if cwd.is_file():
        cwd = cwd.parent

    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
        eidos_cand = parent / "eidos"
        if (
            eidos_cand.is_dir()
            and not any(p in eidos_cand.parts for p in (".agents", "plugins", "assets"))
            and ((eidos_cand / "system.adoc").exists() or (eidos_cand / "architecture").is_dir())
        ):
            return parent

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip()).resolve()
    except OSError:
        pass

    return cwd


def load_project(
    start_dir: Path | None = None,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
) -> ProjectConfig:
    """Resolve project root, eidos directory, cache directory, and system metadata."""
    if repo_root is not None:
        root = Path(repo_root).resolve()
    elif eidos_dir is not None:
        ed = Path(eidos_dir).resolve()
        if (ed / "system.adoc").exists() or (
            ed.name == "eidos" and not any(p in ed.parts for p in (".agents", "plugins", "assets"))
        ):
            root = ed.parent
        else:
            root = find_repo_root(ed)
    else:
        root = find_repo_root(start_dir)

    resolved_eidos = (Path(eidos_dir) if eidos_dir is not None else (root / "eidos")).resolve()
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
            if fm.get("title") or fm.get("name") or doc.title:
                system_title = str(fm.get("title") or fm.get("name") or doc.title).strip()
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

    if not rel_paths or not (repo_root / ".git").exists():
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


def get_tracked_files(repo_root: Path, include_untracked: bool = True) -> list[str]:
    """List repository files respecting .gitignore and .gitattributes generated/vendored attributes."""
    root = repo_root.resolve()
    if (root / ".git").exists():
        files: set[str] = set()
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if tracked.returncode == 0:
                for item in tracked.stdout.split("\0"):
                    if item and (root / item).is_file():
                        files.add(item)

            if include_untracked:
                untracked = subprocess.run(
                    ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if untracked.returncode == 0:
                    for item in untracked.stdout.split("\0"):
                        if item and (root / item).is_file():
                            files.add(item)

            if files:
                file_list = sorted(files)
                ignored_attrs = is_generated_or_vendored(root, file_list)
                return [f for f in file_list if f not in ignored_attrs]
        except OSError:
            pass

    # Fallback filesystem walk for non-git directories (e.g., temporary test fixtures)
    collected: list[str] = []
    if not root.exists():
        return collected
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in STANDARD_EXCLUDED_DIRS and not d.startswith(".")]
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                rel = str(full.relative_to(root)).replace("\\", "/")
                collected.append(rel)
            except ValueError:
                continue
    return sorted(collected)


def _append_path_values(target: list[str], raw_val: Any) -> None:
    if isinstance(raw_val, str) and raw_val.strip():
        clean = raw_val.strip().replace("\\", "/")
        if clean not in target:
            target.append(clean)
    elif isinstance(raw_val, list):
        for item in raw_val:
            if isinstance(item, str) and item.strip():
                clean = item.strip().replace("\\", "/")
                if clean not in target:
                    target.append(clean)


def extract_entity_paths(entity_data: dict[str, Any]) -> list[str]:
    """Extract all declared ownership path patterns from package_path, source_path, or specification_file."""
    patterns: list[str] = []

    for key in ("package_path", "source_path", "specification_file"):
        _append_path_values(patterns, entity_data.get(key))

    # Also inspect inline C4 Level 3 component source_paths
    for comp in entity_data.get("components", []) or entity_data.get("c4_internal_components", []) or []:
        if isinstance(comp, dict):
            _append_path_values(patterns, comp.get("source_path"))
            _append_path_values(patterns, comp.get("package_path"))

    return patterns


def match_path_pattern(rel_file: str, pattern: str) -> int:
    """Return specificity score (>0) if rel_file matches pattern, or 0 if no match.

    Higher scores indicate more specific ownership matches:
    - Exact file path match: 2000 + len(pattern)
    - Glob match with directory prefix: 1000 + len(literal_prefix)
    - Directory prefix match: 500 + len(prefix)
    - Wildcard-only glob match (e.g. '**/*.css'): 100 + len(literal_chars)
    """
    norm_file = rel_file.strip().replace("\\", "/").lstrip("./")
    norm_pat = pattern.strip().replace("\\", "/").lstrip("./")
    if not norm_file or not norm_pat:
        return 0

    # 1. Exact file match
    if norm_file == norm_pat:
        return 2000 + len(norm_pat)

    # 2. Directory prefix without glob wildcards
    if not any(ch in norm_pat for ch in ("*", "?", "[")):
        prefix = norm_pat.rstrip("/")
        if norm_file.startswith(prefix + "/"):
            return 500 + len(prefix)
        return 0

    # 3. Glob pattern matching
    literal_chars = "".join(ch for ch in norm_pat if ch not in "*?[]/")
    has_leading_dir = not norm_pat.startswith("*")

    # Handle 'dir/**' recursive prefix
    if norm_pat.endswith("/**"):
        base_dir = norm_pat[:-3].rstrip("/")
        if norm_file == base_dir or norm_file.startswith(base_dir + "/"):
            return (1000 if has_leading_dir else 100) + len(base_dir)

    # Handle 'dir/**/*' or general glob
    posix_file = PurePosixPath(norm_file)
    matched = False
    try:
        if posix_file.match(norm_pat) or fnmatch.fnmatchcase(norm_file, norm_pat):
            matched = True
        elif "**/" in norm_pat:
            # PurePosixPath.match('a/**/b.py') in Python 3.12 matches 1+ subdirs; also check 0 subdirs ('a/b.py')
            collapsed = norm_pat.replace("/**/", "/")
            if posix_file.match(collapsed) or fnmatch.fnmatchcase(norm_file, collapsed):
                matched = True
    except ValueError:
        matched = fnmatch.fnmatchcase(norm_file, norm_pat)

    if matched:
        base_score = 1000 if has_leading_dir else 100
        return base_score + len(literal_chars)

    return 0


def matches_owned_paths(rel_file: str, patterns: list[str]) -> tuple[bool, int]:
    """Check if rel_file matches any pattern in patterns; returns (matched, best_specificity_score)."""
    best = 0
    for pat in patterns:
        score = match_path_pattern(rel_file, pat)
        if score > best:
            best = score
    return (best > 0), best
