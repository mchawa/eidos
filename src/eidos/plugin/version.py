"""Semantic Versioning (SemVer 2.0.0) validation, parsing, and status inspection for Eidos CLI and Agent Plugins."""

import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import orjson

import eidos

PYPI_PACKAGE_JSON_URL = "https://pypi.org/pypi/eidos-ai/json"
_DEFAULT_URLOPEN = urllib.request.urlopen

# Official SemVer 2.0.0 Regular Expression (RFC 3986 / semver.org)
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def is_valid_semver(version: str) -> bool:
    """Check if a string strictly conforms to Semantic Versioning 2.0.0."""
    if not isinstance(version, str):
        return False
    return bool(SEMVER_PATTERN.match(version.strip()))


def parse_semver(version: str) -> tuple[int, int, int, str | None, str | None] | None:
    """Parse a SemVer string into (major, minor, patch, prerelease, build)."""
    if not isinstance(version, str):
        return None
    m = SEMVER_PATTERN.match(version.strip())
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3))
    prerelease = m.group(4)
    build = m.group(5)
    return (major, minor, patch, prerelease, build)


def compare_semver(v1: str, v2: str) -> int:
    """Compare two SemVer 2.0.0 strings.

    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2
    """
    p1 = parse_semver(v1)
    p2 = parse_semver(v2)
    if not p1 or not p2:
        raise ValueError(f"Cannot compare invalid semver strings: '{v1}', '{v2}'")

    # 1. Compare (major, minor, patch)
    if p1[:3] < p2[:3]:
        return -1
    elif p1[:3] > p2[:3]:
        return 1

    # 2. Compare pre-release
    # 1.0.0 has higher precedence than 1.0.0-alpha
    if p1[3] is None and p2[3] is not None:
        return 1
    if p1[3] is not None and p2[3] is None:
        return -1
    if p1[3] is None and p2[3] is None:
        return 0

    # Both have pre-release strings
    parts1 = p1[3].split(".")
    parts2 = p2[3].split(".")
    for part1, part2 in zip(parts1, parts2, strict=False):
        if part1 == part2:
            continue
        is_num1, is_num2 = part1.isdigit(), part2.isdigit()
        if is_num1 and is_num2:
            return -1 if int(part1) < int(part2) else 1
        elif is_num1 and not is_num2:
            return -1
        elif not is_num1 and is_num2:
            return 1
        else:
            return -1 if part1 < part2 else 1

    if len(parts1) < len(parts2):
        return -1
    elif len(parts1) > len(parts2):
        return 1
    return 0


_PEP440_BASE_SEMVER_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_PEP440_PRERELEASE_PATTERN = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.?((?:dev|a|alpha|b|beta|rc)\d+)$",
    re.IGNORECASE,
)


def normalize_to_semver(version: str) -> str | None:
    """Normalize a SemVer 2.0.0 or PEP 440 version string (e.g. '0.12.1.dev3', 'v0.12.0') to valid SemVer 2.0.0."""
    if not isinstance(version, str):
        return None
    cleaned = version.strip()
    if is_valid_semver(cleaned):
        return cleaned
    if cleaned.startswith(("v", "V")) and is_valid_semver(cleaned[1:]):
        return cleaned[1:]
    m = _PEP440_BASE_SEMVER_PATTERN.match(cleaned)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return None


def _to_comparable_semver(version: str) -> str | None:
    """Convert SemVer 2.0.0 or PEP 440 pre-release/dev strings (e.g. '0.20.2.dev3') into pre-release-preserving SemVer."""
    if not isinstance(version, str):
        return None
    cleaned = version.strip()
    if is_valid_semver(cleaned):
        return cleaned
    if cleaned.startswith(("v", "V")) and is_valid_semver(cleaned[1:]):
        return cleaned[1:]
    m_pre = _PEP440_PRERELEASE_PATTERN.match(cleaned)
    if m_pre:
        tag = re.sub(r"([a-zA-Z]+)(\d+)", lambda m: f"{m.group(1).lower()}.{m.group(2)}", m_pre.group(4))
        cand = f"{m_pre.group(1)}.{m_pre.group(2)}.{m_pre.group(3)}-{tag}"
        if is_valid_semver(cand):
            return cand
    return normalize_to_semver(cleaned)


def get_bundled_plugin_version() -> str:
    """Return the bundled plugin version (synchronized with eidos engine package version)."""
    pkg_raw = getattr(eidos, "__version__", "0.2.0")
    pkg_exact_semver = pkg_raw.strip() if isinstance(pkg_raw, str) and is_valid_semver(pkg_raw) else None

    # 1. Check canonical plugin bundle (via locate_plugin_bundle)
    try:
        from eidos.core.workspace.assets import get_plugin_reference_dir

        ref_dir = get_plugin_reference_dir()
        manifest = ref_dir / "plugin.json"
        if manifest.exists():
            data = orjson.loads(manifest.read_bytes())
            v = data.get("version")
            if v and is_valid_semver(v):
                if pkg_exact_semver and compare_semver(pkg_exact_semver, v) > 0:
                    return pkg_exact_semver
                return v
    except (OSError, orjson.JSONDecodeError, ValueError):
        pass

    # 2. Fall back to normalized eidos package version (supports PEP 440 .devN/.postN)
    normalized_pkg = normalize_to_semver(pkg_raw)
    if normalized_pkg:
        return normalized_pkg
    return "0.2.0"


def _resolve_installed_plugin_manifest(
    project_dir: Path | None = None,
) -> tuple[Path, Path | None]:
    """Resolve (plugin_dir, manifest_path) for an installed project or fallback agent plugin at the enclosing Git root."""
    from eidos.core.workspace.project import _has_eidos_markers, find_git_root, find_repo_root

    base_dir = (project_dir or Path.cwd()).resolve()
    git_root = find_git_root(base_dir)
    if project_dir is None:
        root = git_root or base_dir
    elif git_root is not None and git_root != base_dir:
        sub_root = find_repo_root(base_dir)
        root = git_root if (sub_root != git_root and _has_eidos_markers(sub_root / "eidos")) else base_dir
    else:
        root = base_dir
    target_dir = root / ".agents" / "plugins" / "eidos"

    for cand_name in ("plugin.json", ".eidos-manifest.json", "plugins/eidos/plugin.json"):
        manifest_path = target_dir / cand_name
        if manifest_path.exists():
            return manifest_path.parent, manifest_path

    fallbacks = [
        root / ".claude" / ".eidos-manifest.json",
        root / ".agents" / ".eidos-manifest.json",
        root / ".aider" / ".eidos-manifest.json",
    ]
    for fb in fallbacks:
        if fb.exists():
            return fb.parent, fb

    return target_dir, None


def get_installed_plugin_version(project_dir: Path | None = None) -> str | None:
    """Retrieve installed plugin version from project scope."""
    _plugin_dir, manifest_path = _resolve_installed_plugin_manifest(project_dir=project_dir)
    if manifest_path is None:
        return None
    try:
        data = orjson.loads(manifest_path.read_bytes())
        return data.get("version")
    except (OSError, orjson.JSONDecodeError, ValueError):
        return None


def check_plugin_version_status(project_dir: Path | None = None) -> dict[str, Any]:
    """Check version status between bundled plugin and installed project plugin."""
    from eidos.core.workspace.project import _has_eidos_markers, find_git_root, find_repo_root

    base_dir = (project_dir or Path.cwd()).resolve()
    git_root = find_git_root(base_dir)
    if project_dir is None:
        root = git_root or base_dir
    elif git_root is not None and git_root != base_dir:
        sub_root = find_repo_root(base_dir)
        root = git_root if (sub_root != git_root and _has_eidos_markers(sub_root / "eidos")) else base_dir
    else:
        root = base_dir
    target_dir, _manifest_path = _resolve_installed_plugin_manifest(project_dir=root)

    bundled = get_bundled_plugin_version()
    installed = get_installed_plugin_version(project_dir=root)

    if installed is None:
        status = "not_installed"
        status_text = "Not installed in project"
    elif not is_valid_semver(installed):
        status = "invalid"
        status_text = f"Installed version '{installed}' is invalid SemVer"
    else:
        cmp = compare_semver(installed, bundled)
        if cmp == 0:
            status = "up_to_date"
            status_text = "Up to date"
        elif cmp < 0:
            status = "outdated"
            status_text = f"Outdated (Update available: {bundled})"
        else:
            status = "newer"
            status_text = f"Newer than bundled ({installed} > {bundled})"

    return {
        "project_dir": root,
        "plugin_dir": target_dir,
        "is_installed": installed is not None,
        "bundled_version": bundled,
        "installed_version": installed,
        "status": status,
        "status_text": status_text,
    }


def check_cli_update_status(
    current_version: str | None = None,
    pypi_url: str = PYPI_PACKAGE_JSON_URL,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Check PyPI for newer releases of eidos-ai using stdlib urllib.request and SemVer 2.0.0 comparison."""
    raw_ver = current_version if current_version is not None else getattr(eidos, "__version__", "0.2.0")
    installed_raw = str(raw_ver).strip() or "0.2.0"
    installed_semver = _to_comparable_semver(installed_raw)

    if os.environ.get("EIDOS_NO_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes"):
        return {
            "ok": True,
            "status": "offline",
            "installed_version": installed_raw,
            "latest_version": None,
            "update_available": False,
            "upgrade_command": None,
            "status_text": f"Update check skipped (v{installed_raw} installed)",
        }

    if (
        "PYTEST_CURRENT_TEST" in os.environ
        and urllib.request.urlopen is _DEFAULT_URLOPEN
        and current_version is None
        and pypi_url == PYPI_PACKAGE_JSON_URL
        and not os.environ.get("EIDOS_FORCE_UPDATE_CHECK")
    ):
        return {
            "ok": True,
            "status": "offline",
            "installed_version": installed_raw,
            "latest_version": None,
            "update_available": False,
            "upgrade_command": None,
            "status_text": f"Unable to reach PyPI (v{installed_raw} installed)",
        }

    if not isinstance(pypi_url, str) or not pypi_url.strip().startswith(("https://", "http://")):
        return {
            "ok": True,
            "status": "offline",
            "installed_version": installed_raw,
            "latest_version": None,
            "update_available": False,
            "upgrade_command": None,
            "status_text": f"Invalid update URL scheme (v{installed_raw} installed)",
        }

    try:
        req = urllib.request.Request(
            pypi_url.strip(),
            headers={
                "Accept": "application/json",
                "User-Agent": f"eidos-ai/{installed_raw}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read(2 * 1024 * 1024)
        payload = orjson.loads(raw_body)
        info = payload.get("info") if isinstance(payload, dict) else None
        latest_raw = str(info.get("version", "")).strip() if isinstance(info, dict) else ""
        latest_semver = _to_comparable_semver(latest_raw)
        if not latest_raw or not latest_semver or not installed_semver:
            return {
                "ok": True,
                "status": "offline",
                "installed_version": installed_raw,
                "latest_version": latest_raw or None,
                "update_available": False,
                "upgrade_command": None,
                "status_text": f"Unable to parse latest PyPI version (v{installed_raw} installed)",
            }

        cmp = compare_semver(installed_semver, latest_semver)
        if cmp < 0:
            return {
                "ok": True,
                "status": "update_available",
                "installed_version": installed_raw,
                "latest_version": latest_raw,
                "update_available": True,
                "upgrade_command": "uv tool upgrade eidos-ai",
                "status_text": (
                    f"Update available: v{installed_raw} -> v{latest_raw} "
                    "(Run 'uv tool upgrade eidos-ai' or 'pip install -U eidos-ai')"
                ),
            }
        if cmp == 0:
            return {
                "ok": True,
                "status": "up_to_date",
                "installed_version": installed_raw,
                "latest_version": latest_raw,
                "update_available": False,
                "upgrade_command": None,
                "status_text": f"Up to date (v{installed_raw})",
            }
        return {
            "ok": True,
            "status": "newer",
            "installed_version": installed_raw,
            "latest_version": latest_raw,
            "update_available": False,
            "upgrade_command": None,
            "status_text": f"Ahead of latest PyPI release (v{installed_raw} > v{latest_raw})",
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, orjson.JSONDecodeError):
        return {
            "ok": True,
            "status": "offline",
            "installed_version": installed_raw,
            "latest_version": None,
            "update_available": False,
            "upgrade_command": None,
            "status_text": f"Unable to reach PyPI (v{installed_raw} installed)",
        }
