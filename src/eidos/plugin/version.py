"""Semantic Versioning (SemVer 2.0.0) validation, parsing, and status inspection for Eidos Agent Plugins."""

import json
import re
from pathlib import Path
from typing import Any

import eidos

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


def get_bundled_plugin_version() -> str:
    """Return the bundled plugin version (synchronized with eidos engine package version)."""
    # 1. Check canonical plugin bundle (via locate_plugin_bundle)
    try:
        from eidos.core.workspace.assets import get_plugin_reference_dir

        ref_dir = get_plugin_reference_dir()
        manifest = ref_dir / "plugin.json"
        if manifest.exists():
            with open(manifest, encoding="utf-8") as f:
                data = json.load(f)
                v = data.get("version")
                if v and is_valid_semver(v):
                    return v
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # 2. Check embedded in package bundle
    pkg_bundle = Path(__file__).parent.parent / "plugin_bundle" / "plugin.json"
    if pkg_bundle.exists():
        try:
            with open(pkg_bundle, encoding="utf-8") as f:
                data = json.load(f)
                v = data.get("version")
                if v and is_valid_semver(v):
                    return v
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    # 3. Fall back to eidos package version
    pkg_ver = getattr(eidos, "__version__", "0.2.0")
    if is_valid_semver(pkg_ver):
        return pkg_ver
    return "0.2.0"


def get_installed_plugin_version(project_dir: Path | None = None, dest_path: Path | None = None) -> str | None:
    """Retrieve installed plugin version from project scope or custom destination."""
    root = (project_dir or Path.cwd()).resolve()
    if dest_path is not None:
        target_dir = dest_path if dest_path.is_absolute() else (root / dest_path)
    else:
        target_dir = root / ".agents" / "plugins" / "eidos"

    manifest_path = target_dir / "plugin.json"
    if not manifest_path.exists():
        # Fallback to other agent manifests if installed specifically for them
        fallbacks = [
            root / ".claude" / "plugins" / "eidos" / "plugin.json",
            root / ".claude" / "plugins" / "eidos" / ".claude-plugin" / "plugin.json",
            root / ".claude" / "skills" / "eidos" / ".claude-plugin" / "plugin.json",
            root / ".github" / "plugin" / "plugin.json",
            root / ".codex" / "plugin.json",
            root / ".opencode" / "plugins" / "eidos" / "plugin.json",
            root / ".cline" / "plugins" / "eidos" / "plugin.json",
            root / ".cline" / "skills" / "eidos" / "plugin.json",
        ]
        for fb in fallbacks:
            if fb.exists():
                manifest_path = fb
                break
        else:
            return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("version")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def check_plugin_version_status(project_dir: Path | None = None, dest_path: Path | None = None) -> dict[str, Any]:
    """Check version status between bundled plugin and installed project plugin."""
    root = (project_dir or Path.cwd()).resolve()
    if dest_path is not None:
        target_dir = dest_path if dest_path.is_absolute() else (root / dest_path)
    else:
        target_dir = root / ".agents" / "plugins" / "eidos"

    bundled = get_bundled_plugin_version()
    installed = get_installed_plugin_version(project_dir=project_dir, dest_path=dest_path)

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
