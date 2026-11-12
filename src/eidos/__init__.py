"""Eidos: Git-Native, Agent-Agnostic Development AI Ecosystem."""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import subprocess
from pathlib import Path

_RUNTIME_VERSION_CACHE: str | None = None
_GIT_DESCRIBE_DISTANCE_RE = re.compile(r"-(\d+)-g[0-9a-fA-F]+$")
_BASE_RELEASE_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_LEADING_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def _parse_git_describe_version(raw_describe: str) -> str | None:
    """Convert `git describe --tags --match 'v[0-9]*'` output into an exact release or PEP 440 `.devN` version."""
    desc = raw_describe.strip()
    if not desc:
        return None
    m_dist = _GIT_DESCRIBE_DISTANCE_RE.search(desc)
    if m_dist:
        dist = int(m_dist.group(1))
        tag_ver = desc[: m_dist.start()].lstrip("vV")
        if not _LEADING_SEMVER_RE.match(tag_ver):
            return None
        if dist == 0:
            return tag_ver
        m_base = _BASE_RELEASE_RE.fullmatch(tag_ver)
        if m_base:
            return f"{m_base.group(1)}.{m_base.group(2)}.{int(m_base.group(3)) + 1}.dev{dist}"
        return f"{tag_ver}.dev{dist}"

    tag_ver = desc.lstrip("vV")
    if _LEADING_SEMVER_RE.match(tag_ver):
        return tag_ver
    return None


def _fallback_package_version() -> str:
    """Resolve package version from generated `_version.py` or installed package metadata."""
    try:
        mod = importlib.import_module("eidos._version")
        ver = getattr(mod, "__version__", None)
        if isinstance(ver, str) and ver.strip():
            return ver.strip()
    except ImportError:
        pass

    try:
        return importlib.metadata.version("eidos-ai")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def resolve_runtime_version(pkg_root: Path | None = None, *, use_cache: bool = True) -> str:
    """Resolve accurate runtime version, preferring live Git tags in editable source checkouts."""
    global _RUNTIME_VERSION_CACHE
    if pkg_root is None and use_cache and _RUNTIME_VERSION_CACHE is not None:
        return _RUNTIME_VERSION_CACHE

    resolved_pkg_root = (pkg_root or Path(__file__).resolve().parent).resolve()
    repo_root = resolved_pkg_root.parent.parent
    version: str | None = None

    if (repo_root / "pyproject.toml").is_file() and (repo_root / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "describe", "--tags", "--match", "v[0-9]*"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
            if proc.returncode == 0:
                version = _parse_git_describe_version(proc.stdout)
        except (OSError, subprocess.SubprocessError):
            version = None

    final_version = version or _fallback_package_version()
    if pkg_root is None and use_cache:
        _RUNTIME_VERSION_CACHE = final_version
    return final_version


__version__: str = resolve_runtime_version()

__all__ = ["__version__", "resolve_runtime_version"]
