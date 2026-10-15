"""Project-scoped installer and uninstaller for Eidos Agent Plugins across AI agent environments."""

import contextlib
import enum
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from eidos.core.workspace.assets import get_plugin_reference_dir
from eidos.plugin.version import (
    get_bundled_plugin_version,
    get_installed_plugin_version,
)


class PluginInstallTarget(enum.StrEnum):
    PROJECT = "project"  # Installs .agents/plugins/eidos (agent-plugins.org v1.0.0) and client configurations
    ANTIGRAVITY = "antigravity"  # Installs .agents/plugins/eidos (strict agent-plugins.org v1.0.0)
    CLAUDE = "claude"  # Installs Claude Code plugin (.claude/plugins/eidos & .claude/skills)
    CURSOR = "cursor"  # Configures Cursor rules (.cursor/rules/eidos.mdc) and skills (.cursor/skills)
    AIDER = "aider"  # Configures .aider.conf.yml and .aider.conventions.md
    CLINE = "cline"  # Configures .cline/plugins/eidos and .cline/skills
    OPEN_CODE = "open-code"  # Configures .opencode/plugins/eidos and .opencode/skills
    OPENCODE = "opencode"  # Alias for open-code
    CODEX = "codex"  # Installs OpenAI Codex plugin bundle (.codex/)
    COPILOT = "copilot"  # Configures GitHub Copilot (.github/plugin, .github/skills, .github/copilot-instructions.md)
    ALL = "all"  # Installs all agent plugins and configurations


@dataclass
class InstallResult:
    target: str
    destination: Path
    success: bool
    message: str
    created_files: list[Path] = field(default_factory=list)


@dataclass
class UninstallResult:
    target: str
    destination: Path
    success: bool
    message: str
    removed: bool = True


def locate_plugin_bundle() -> Path:
    """Find the canonical Eidos plugin reference package."""
    # 1. Check assets/plugin_reference via asset locator
    try:
        ref_dir = get_plugin_reference_dir()
        if ref_dir.exists() and (ref_dir / "plugin.json").exists():
            return ref_dir
    except OSError:
        pass

    # 2. Check repo root assets/plugin_reference
    local_assets = Path.cwd() / "assets" / "plugin_reference"
    if local_assets.exists() and (local_assets / "plugin.json").exists():
        return local_assets

    # 3. Check legacy repo root plugins/eidos
    repo_bundle = Path.cwd() / "plugins" / "eidos"
    if repo_bundle.exists() and (repo_bundle / "plugin.json").exists():
        return repo_bundle

    # 4. Check embedded legacy package directory
    pkg_bundle = Path(__file__).parent.parent / "plugin_bundle"
    if pkg_bundle.exists() and (pkg_bundle / "plugin.json").exists():
        return pkg_bundle

    raise FileNotFoundError("Could not locate Eidos plugin reference in assets or repository.")


ACTIVE_EIDOS_SKILLS: frozenset[str] = frozenset(
    {
        "eidos",
        "eidos-spec",
        "eidos-plan",
        "eidos-ops",
    }
)

RETIRED_EIDOS_SKILLS: frozenset[str] = frozenset(
    {
        "eidos-architecture",
        "eidos-context",
        "eidos-delta",
        "eidos-design",
        "eidos-drift",
        "eidos-explore",
        "eidos-extract",
        "eidos-init",
        "eidos-invariants",
        "eidos-portal",
        "eidos-product",
        "eidos-reconcile",
        "eidos-review",
    }
)


MANIFEST_FILENAME = ".eidos-manifest.json"


def _check_not_symlink(path: Path, root: Path | None = None) -> None:
    """Refuse to write through symlinked files or symlinked parent directories."""
    if path.is_symlink():
        raise FileExistsError(f"Destination path '{path}' is a symlink; refusing to write through symlink.")
    stop_roots: set[Path] = set()
    if root is not None:
        if root.is_symlink():
            raise FileExistsError(f"Destination root '{root}' is a symlink; refusing to write through symlink.")
        stop_roots.add(root)
        with contextlib.suppress(OSError):
            stop_roots.add(root.resolve())
    for parent in path.parents:
        if parent in stop_roots:
            break
        if parent.is_symlink():
            raise FileExistsError(
                f"Parent directory '{parent}' of '{path}' is a symlink; refusing to write through symlink."
            )


def _compute_sha256(file_path: Path) -> str:
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _write_installation_manifest(target_dir: Path, created_files: list[Path] | None = None) -> Path:
    """Generate .eidos-manifest.json in target_dir storing relative file paths and SHA-256 digests."""
    _check_not_symlink(target_dir)
    manifest_file = target_dir / MANIFEST_FILENAME
    _check_not_symlink(manifest_file, root=target_dir)

    existing_files: dict[str, str] = {}
    if created_files is not None and manifest_file.exists():
        try:
            with open(manifest_file, encoding="utf-8") as f:
                old_data = json.load(f)
            old_files = old_data.get("files", {})
            if isinstance(old_files, dict):
                for rel_path, digest in old_files.items():
                    candidate = target_dir / rel_path
                    if candidate.is_file() and not candidate.is_symlink():
                        existing_files[rel_path] = str(digest)
        except (OSError, json.JSONDecodeError, ValueError):
            existing_files = {}

    manifest_data: dict[str, Any] = {
        "version": get_bundled_plugin_version(),
        "files": existing_files,
    }
    if created_files is not None:
        file_list = [f for f in created_files if f.is_file() and f.resolve() != manifest_file.resolve()]
    else:
        file_list = [f for f in target_dir.rglob("*") if f.is_file() and f.name != MANIFEST_FILENAME]

    for f in sorted(file_list):
        try:
            rel = str(f.relative_to(target_dir))
        except ValueError:
            continue
        manifest_data["files"][rel] = _compute_sha256(f)

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_file


def _prune_empty_dirs(root_dir: Path) -> None:
    """Recursively remove empty subdirectories inside root_dir (bottom-up)."""
    if not root_dir.exists() or not root_dir.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(root_dir, topdown=False):
        d = Path(dirpath)
        if d != root_dir:
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass


def _rewrite_hook_command_for_target_root(target: Path, version: str | None = None) -> bool:
    """Rewrite hook commands in hooks.json to use version-pinned uvx invocation."""
    target_hooks = target if target.name == "hooks.json" else (target / "hooks.json")
    if not target_hooks.exists():
        return False
    try:
        ver = version or get_bundled_plugin_version()
        hooks_text = target_hooks.read_text(encoding="utf-8")
        replacement = f'"command": "uvx --from eidos-ai=={ver} eidos hook'
        new_text = re.sub(
            r'"command"\s*:\s*"(?:uvx --from eidos-ai(?:==[^\s"]+)?\s+)?eidos hook\b',
            replacement,
            hooks_text,
        )
        target_hooks.write_text(new_text, encoding="utf-8")
        return True
    except OSError:
        return False


def _copy_plugin_tree(
    source_dir: Path, target_dir: Path, force: bool = False, launcher: str = "eidos"
) -> list[Path]:
    """Copy all plugin files to target destination within the project."""
    _check_not_symlink(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    # Remove legacy scripts directory if no longer present in source bundle
    legacy_scripts = target_dir / "scripts"
    if legacy_scripts.exists() and not (source_dir / "scripts").exists():
        scripts_tracked = False
        manifest_path = target_dir / MANIFEST_FILENAME
        if manifest_path.exists() and not manifest_path.is_symlink():
            try:
                with open(manifest_path, encoding="utf-8") as mf:
                    mdata = json.load(mf)
                mfiles = mdata.get("files", {})
                if isinstance(mfiles, dict):
                    scripts_tracked = any(
                        k == "scripts" or k.startswith("scripts/") or k.startswith("scripts\\")
                        for k in mfiles
                    )
            except (OSError, json.JSONDecodeError, ValueError):
                scripts_tracked = False
        if force or scripts_tracked:
            with contextlib.suppress(OSError):
                shutil.rmtree(legacy_scripts)

    # Prune retired legacy Eidos skill directories inside the plugin tree
    target_skills_dir = target_dir / "skills"
    if target_skills_dir.exists():
        for retired_name in RETIRED_EIDOS_SKILLS:
            retired_dir = target_skills_dir / retired_name
            if retired_dir.exists():
                with contextlib.suppress(OSError):
                    shutil.rmtree(retired_dir)

    created = []
    for item in source_dir.rglob("*"):
        if item.is_file():
            rel = item.relative_to(source_dir)
            # Never copy internal cache or assistant-specific directories into plugins
            if any(part in (".claude-plugin", ".eidos", ".cache", ".git") for part in rel.parts):
                continue
            # Never copy legacy MCP configuration files into target plugins
            if rel.name in (".mcp.json", "mcp.json", "mcp_config.json", MANIFEST_FILENAME):
                continue
            dest = target_dir / rel
            _check_not_symlink(dest, root=target_dir)
            if dest.exists() and not force:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            created.append(dest)

    # Ensure plugin.json reflects the bundled plugin version
    target_manifest = target_dir / "plugin.json"
    if target_manifest.exists() and (target_manifest in created or force):
        try:
            with open(target_manifest, encoding="utf-8") as f:
                data = json.load(f)
            bundled_ver = get_bundled_plugin_version()
            if data.get("version") != bundled_ver:
                data["version"] = bundled_ver
                with open(target_manifest, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    # Rewrite hook commands if launcher == "uvx"
    target_hooks = target_dir / "hooks.json"
    if target_hooks.exists() and launcher == "uvx":
        _rewrite_hook_command_for_target_root(target_hooks)

    return created


def _copy_skills_flat(source_dir: Path, target_skills_dir: Path, force: bool = False) -> list[Path]:
    """Copy individual skills from source bundle into target_skills_dir/<skill-name>/ and prune retired Eidos skills."""
    _check_not_symlink(target_skills_dir)

    source_skills = source_dir / "skills" if (source_dir / "skills").exists() else source_dir
    if not source_skills.exists():
        return []

    target_skills_dir.mkdir(parents=True, exist_ok=True)
    # Prune retired legacy Eidos skill directories while preserving unrelated user skills
    for retired_name in RETIRED_EIDOS_SKILLS:
        retired_dir = target_skills_dir / retired_name
        if retired_dir.exists():
            with contextlib.suppress(OSError):
                shutil.rmtree(retired_dir)

    created: list[Path] = []

    for item in sorted(source_skills.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            dest_skill_dir = target_skills_dir / item.name
            _check_not_symlink(dest_skill_dir, root=target_skills_dir)
            dest_skill_dir.mkdir(parents=True, exist_ok=True)
            for file_path in item.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to(item)
                    dest_file = dest_skill_dir / rel
                    _check_not_symlink(dest_file, root=target_skills_dir)
                    if dest_file.exists() and not force:
                        continue
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, dest_file)
                    created.append(dest_file)
    return created


def _remove_skills_flat(source_dir: Path | None, target_skills_dir: Path) -> bool:
    """Remove known active and retired Eidos skills from target_skills_dir without removing unrelated user skills."""
    if not target_skills_dir.exists():
        return False

    known_skills = set(ACTIVE_EIDOS_SKILLS | RETIRED_EIDOS_SKILLS)
    if source_dir:
        source_skills = source_dir / "skills" if (source_dir / "skills").exists() else source_dir
        if source_skills.exists():
            known_skills |= {d.name for d in source_skills.iterdir() if d.is_dir() and not d.name.startswith(".")}

    removed_any = False
    for skill_name in sorted(known_skills):
        skill_dir = target_skills_dir / skill_name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            removed_any = True

    # If target_skills_dir is now empty, remove it safely
    if target_skills_dir.exists() and not any(target_skills_dir.iterdir()):
        with contextlib.suppress(OSError):
            target_skills_dir.rmdir()

    return removed_any


def _remove_project_mcp_server(file_path: Path, server_name: str) -> bool:
    """Safely remove a server entry from a project MCP configuration file."""
    if not file_path.exists():
        return False
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return False

    servers = data.get("mcpServers", {})
    if server_name not in servers:
        return False

    del servers[server_name]
    data["mcpServers"] = servers

    if not servers and len(data) == 1:
        # If mcpServers was the only key and is now empty, delete the file
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return True


def _ensure_claude_plugin_manifest(target_dir: Path, force: bool = False) -> list[Path] | None:
    """Ensure plugin.json exists at root and in .claude-plugin/ of target plugin directory."""
    _check_not_symlink(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = target_dir / "plugin.json"
    _check_not_symlink(manifest_file, root=target_dir)

    if manifest_file.exists() and not force:
        try:
            with open(manifest_file, encoding="utf-8") as f:
                existing_root = json.load(f)
            if not isinstance(existing_root, dict) or existing_root.get("name") != "eidos":
                return None
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    claude_meta_dir = target_dir / ".claude-plugin"
    _check_not_symlink(claude_meta_dir, root=target_dir)
    claude_meta_dir.mkdir(parents=True, exist_ok=True)
    legacy_manifest_file = claude_meta_dir / "plugin.json"
    _check_not_symlink(legacy_manifest_file, root=target_dir)
    bundled_ver = get_bundled_plugin_version()

    manifest_data = {
        "name": "eidos",
        "displayName": "Eidos AI Lifecycle Ecosystem",
        "version": bundled_ver,
        "description": "Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework for Claude Code.",
        "author": {
            "name": "Eidos Contributors",
            "url": "https://github.com/eidos-ai/eidos",
        },
        "homepage": "https://github.com/eidos-ai/eidos",
        "repository": "https://github.com/eidos-ai/eidos",
        "license": "MIT",
        "keywords": [
            "eidos",
            "agent-plugins",
            "arc42",
            "c4-model",
            "specs",
            "ai-orchestration",
        ],
    }

    created: list[Path] = []
    for mfile in (manifest_file, legacy_manifest_file):
        _check_not_symlink(mfile, root=target_dir)
        if mfile.exists():
            try:
                with open(mfile, encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, dict) or existing.get("name") != "eidos":
                    if not force:
                        continue
                    manifest_data_to_write = manifest_data
                else:
                    existing["version"] = bundled_ver
                    manifest_data_to_write = existing
            except (OSError, json.JSONDecodeError, ValueError):
                if not force:
                    continue
                manifest_data_to_write = manifest_data
        else:
            manifest_data_to_write = manifest_data

        with open(mfile, "w", encoding="utf-8") as f:
            json.dump(manifest_data_to_write, f, indent=2)
        created.append(mfile)

    return created if created else None


def _write_claude_commands(target_dir: Path, launcher: str = "eidos", force: bool = False) -> list[Path]:
    """Write Claude Code slash commands to target_dir/commands/ (Layer 2: Commands)."""
    _check_not_symlink(target_dir)
    commands_dir = target_dir / "commands"
    _check_not_symlink(commands_dir, root=target_dir)
    commands_dir.mkdir(parents=True, exist_ok=True)
    created = []
    bundled_ver = get_bundled_plugin_version()
    cmd_prefix = f"uvx --from eidos-ai=={bundled_ver} eidos" if launcher == "uvx" else "eidos"

    commands = {
        "validate.md": (
            "---\n"
            "description: Validate all Eidos living specifications against canonical JSON schemas\n"
            "---\n\n"
            "Run specification validation:\n"
            "```bash\n"
            f"{cmd_prefix} validate eidos\n"
            "```\n"
        ),
        "drift.md": (
            "---\n"
            "description: Detect spec-to-code drift and uncommitted changes against living specs\n"
            "---\n\n"
            "Run drift detection:\n"
            "```bash\n"
            f"{cmd_prefix} drift --uncommitted\n"
            "```\n"
        ),
        "reconcile.md": (
            "---\n"
            "description: Reconcile code changes to living specifications in Direct Mode\n"
            "---\n\n"
            "Run spec reconciliation:\n"
            "```bash\n"
            f"{cmd_prefix} reconcile --uncommitted --prompt\n"
            "```\n"
        ),
        "context.md": (
            "---\n"
            "description: Extract a token-efficient architectural context slice for current task\n"
            "---\n\n"
            "Extract architectural context:\n"
            "```bash\n"
            f"{cmd_prefix} context\n"
            "```\n"
        ),
    }

    for filename, content in commands.items():
        cmd_file = commands_dir / filename
        _check_not_symlink(cmd_file, root=target_dir)
        if cmd_file.exists() and not force:
            continue
        cmd_file.write_text(content, encoding="utf-8")
        created.append(cmd_file)

    return created


def _update_project_claude_settings(file_path: Path, plugin_id: str = "eidos") -> bool:
    """Safely update project-level .claude/settings.json to enable the Eidos plugin."""
    _check_not_symlink(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if file_path.exists():
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            data = {}

    if "plugins" not in data or not isinstance(data["plugins"], list):
        data["plugins"] = []

    has_plugin_entry = any(
        isinstance(p, dict) and p.get("path") in (".claude/plugins/eidos", "./.claude/plugins/eidos")
        for p in data["plugins"]
    )
    if not has_plugin_entry:
        data["plugins"].append({"path": ".claude/plugins/eidos"})

    if "enabledPlugins" not in data or not isinstance(data["enabledPlugins"], dict):
        data["enabledPlugins"] = {}

    data["enabledPlugins"]["eidos"] = True
    data["enabledPlugins"]["eidos@plugins-dir"] = True
    data["enabledPlugins"]["eidos@local"] = True
    if plugin_id:
        data["enabledPlugins"][plugin_id] = True

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True


def _remove_project_claude_settings(file_path: Path, plugin_id: str = "eidos") -> bool:
    """Safely remove a plugin from project-level .claude/settings.json."""
    if not file_path.exists():
        return False
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return False

    changed = False
    plugins = data.get("enabledPlugins", {})
    keys_to_remove = ["eidos", "eidos@plugins-dir", "eidos@local", "eidos@skills-dir"]
    if plugin_id:
        keys_to_remove.append(plugin_id)

    for k in keys_to_remove:
        if k in plugins:
            del plugins[k]
            changed = True
    data["enabledPlugins"] = plugins

    if "plugins" in data and isinstance(data["plugins"], list):
        orig_len = len(data["plugins"])
        data["plugins"] = [
            p
            for p in data["plugins"]
            if not (isinstance(p, dict) and p.get("path") in (".claude/plugins/eidos", "./.claude/plugins/eidos"))
        ]
        if len(data["plugins"]) != orig_len:
            changed = True

    if not changed:
        return False

    has_other_plugins = bool(data.get("plugins"))
    has_other_enabled = bool(data.get("enabledPlugins"))
    has_other_keys = any(k for k in data if k not in ("plugins", "enabledPlugins"))

    if not has_other_plugins and not has_other_enabled and not has_other_keys:
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return True


def _write_cursor_rules(file_path: Path, force: bool = False) -> Path:
    """Write native Cursor .mdc rule (Cursor Rules standard with YAML frontmatter)."""
    _check_not_symlink(file_path)
    if file_path.exists() and not force:
        return file_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        'description: "Eidos living specifications and architectural governance"\n'
        "alwaysApply: true\n"
        "---\n\n"
        "# Eidos Living Specifications\n\n"
        "This repository is governed by Eidos living specifications under `eidos/`.\n\n"
        "## Core Principles\n"
        "- Consult living specifications before modifying code or proposing architectural changes.\n"
        "- Strictly enforce living specifications (NO ADRs). All architectural mutations follow the SpecDelta lifecycle (`eidos/deltas/`).\n"
        "- Preserve bidirectional invariant-test traceability (`POL-*` and `INV-*`).\n"
        "- Verify specification schemas with `eidos validate eidos` (or `eidos validate --json`).\n"
        "- Verify synchronization with `eidos drift --uncommitted` (or `eidos drift --uncommitted --json`) before finalizing tasks.\n"
        "- Use Eidos CLI commands (`eidos context`, `eidos index search`, `eidos drift`) for real-time inspection.\n"
        "- Consult installed Agent Skills in `.cursor/skills/` for specialized architectural workflows.\n"
    )
    file_path.write_text(content, encoding="utf-8")
    return file_path


def _update_project_aider_conf(file_path: Path, conventions_path: str = ".aider.conventions.md") -> bool:
    """Ensure conventions file is listed under 'read' in .aider.conf.yml."""
    _check_not_symlink(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if file_path.exists():
        try:
            with open(file_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except (OSError, yaml.YAMLError):
            data = {}

    read_list = data.get("read", [])
    if not isinstance(read_list, list):
        read_list = [read_list] if read_list else []

    if conventions_path not in read_list:
        read_list.append(conventions_path)

    data["read"] = read_list

    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return True


def _remove_project_aider_conf(file_path: Path, conventions_path: str = ".aider.conventions.md") -> bool:
    """Safely remove conventions file from .aider.conf.yml."""
    if not file_path.exists():
        return False

    try:
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return False
    except (OSError, yaml.YAMLError):
        return False

    read_list = data.get("read", [])
    if isinstance(read_list, list) and conventions_path in read_list:
        read_list.remove(conventions_path)
        data["read"] = read_list

    if not data.get("read") and len(data) == 1:
        file_path.unlink()
        return True
    elif not data.get("read"):
        data.pop("read", None)

    with open(file_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return True


def _write_aider_conventions(file_path: Path, force: bool = False) -> Path:
    """Write .aider.conventions.md guidelines with installed skill mappings."""
    _check_not_symlink(file_path)
    if file_path.exists() and not force:
        return file_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Eidos Living Specifications & Architectural Conventions\n\n"
        "This repository's architecture and product capabilities are governed by Eidos living specifications under `eidos/`.\n\n"
        "## Core Conventions\n"
        "- **Single Source of Truth**: All architectural truths live in `eidos/` living specifications (Arc42/C4 model, system policies, capability contracts).\n"
        "- **No ADRs**: Never author static Architecture Decision Records. Mutate living specs via SpecDeltas (`eidos/deltas/`).\n"
        "- **Bidirectional Invariant-Test Traceability**: Enforce system policies (`POL-*`) and capability invariants (`INV-*`) across tests.\n"
        "- **Specification Validation**: Run `eidos validate eidos` to ensure schema conformance and cross-reference integrity.\n"
        "- **Zero Spec-to-Code Drift**: Run `eidos drift --uncommitted` to verify code changes are synchronized with living specifications before task completion.\n"
        "- **Architectural Context**: Use `eidos context --file <path>` or `eidos context --task \"<prompt>\"` for token-efficient architectural context.\n\n"
        "## Installed Eidos Skills\n"
        "When performing targeted architectural tasks, load the relevant skill using `/read .aider/skills/<skill>/SKILL.md`:\n"
        "- Master Orchestration, Context, Drift & Reconcile: `/read .aider/skills/eidos/SKILL.md`\n"
        "- Living Specs, C4 Architecture, Design Systems & Invariants: `/read .aider/skills/eidos-spec/SKILL.md`\n"
        "- Explorations, SpecDeltas & Staff Architectural Review: `/read .aider/skills/eidos-plan/SKILL.md`\n"
        "- Bootstrap, Brownfield Extraction & Web Portal: `/read .aider/skills/eidos-ops/SKILL.md`\n"
    )
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


COPILOT_START_MARKER = "<!-- EIDOS_COPILOT_START -->"
COPILOT_END_MARKER = "<!-- EIDOS_COPILOT_END -->"


def _write_copilot_instructions(file_path: Path, force: bool = False) -> Path:
    """Safely write or update Eidos instructions in .github/copilot-instructions.md."""
    _check_not_symlink(file_path)
    if file_path.exists() and not force and COPILOT_START_MARKER not in file_path.read_text(encoding="utf-8"):
        return file_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    eidos_block = (
        f"{COPILOT_START_MARKER}\n"
        "# Eidos Living Specifications & Architectural Conventions\n\n"
        "This repository's architecture and product capabilities are governed by Eidos living specifications under `eidos/`.\n\n"
        "## Core Conventions\n"
        "- **Single Source of Truth**: All architectural truths live in `eidos/` living specifications (Arc42/C4 model, system policies, capability contracts).\n"
        "- **No ADRs**: Never author static Architecture Decision Records. Mutate living specs via SpecDeltas (`eidos/deltas/`).\n"
        "- **Bidirectional Invariant-Test Traceability**: Enforce system policies (`POL-*`) and capability invariants (`INV-*`) across tests.\n"
        "- **Specification Validation**: Run `eidos validate eidos` to ensure schema conformance and cross-reference integrity.\n"
        "- **Zero Spec-to-Code Drift**: Run `eidos drift --uncommitted` to verify code changes are synchronized with living specifications before task completion.\n"
        "- **Architectural Context**: Use `eidos context --file <path>` or `eidos context --task \"<prompt>\"` for token-efficient architectural context.\n"
        "- **Agent Skills**: Consult the 4 installed Eidos skills (`eidos`, `eidos-spec`, `eidos-plan`, `eidos-ops`) in `.github/skills/` for specialized architectural workflows.\n"
        f"{COPILOT_END_MARKER}\n"
    )

    if file_path.exists():
        content = file_path.read_text(encoding="utf-8")
        if COPILOT_START_MARKER in content and COPILOT_END_MARKER in content:
            before = content.split(COPILOT_START_MARKER)[0]
            after = content.split(COPILOT_END_MARKER)[1]
            content = before.rstrip() + ("\n\n" if before.strip() else "") + eidos_block + after.lstrip()
        else:
            sep = "\n\n" if content.strip() else ""
            content = content.rstrip() + sep + eidos_block
    else:
        content = eidos_block

    file_path.write_text(content, encoding="utf-8")
    return file_path


def _remove_copilot_instructions(file_path: Path) -> bool:
    """Safely remove Eidos instructions from .github/copilot-instructions.md."""
    if not file_path.exists():
        return False

    content = file_path.read_text(encoding="utf-8")
    if COPILOT_START_MARKER not in content:
        return False

    if COPILOT_START_MARKER in content and COPILOT_END_MARKER in content:
        before = content.split(COPILOT_START_MARKER)[0]
        after = content.split(COPILOT_END_MARKER)[1]
        remaining = (before.rstrip() + "\n" + after.lstrip()).strip()
    else:
        remaining = content.split(COPILOT_START_MARKER)[0].strip()

    if not remaining:
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        file_path.write_text(remaining + "\n", encoding="utf-8")

    return True


def _ensure_copilot_plugin_manifest(target_dir: Path, force: bool = False) -> Path | None:
    """Ensure .github/plugin/plugin.json exists in target directory with Agent Plugins 1.0 schema and GitHub Copilot metadata."""
    _check_not_symlink(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = target_dir / "plugin.json"
    _check_not_symlink(manifest_file, root=target_dir)
    bundled_ver = get_bundled_plugin_version()

    manifest_data = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "eidos",
        "displayName": "Eidos AI Lifecycle Ecosystem",
        "version": bundled_ver,
        "description": "Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework for GitHub Copilot.",
        "author": {
            "name": "Eidos Contributors",
            "url": "https://github.com/eidos-ai/eidos",
        },
        "homepage": "https://github.com/eidos-ai/eidos",
        "repository": "https://github.com/eidos-ai/eidos",
        "license": "MIT",
        "extensions": {
            "com.github.copilot": {
                "type": "agent_plugin",
                "skills": "skills",
            }
        },
    }

    if manifest_file.exists():
        try:
            with open(manifest_file, encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict) or existing.get("name") != "eidos":
                if not force:
                    return None
            else:
                existing["version"] = bundled_ver
                if "extensions" not in existing or not isinstance(existing["extensions"], dict):
                    existing["extensions"] = {}
                existing["extensions"]["com.github.copilot"] = {
                    "type": "agent_plugin",
                    "skills": "skills",
                }
                manifest_data = existing
        except (OSError, json.JSONDecodeError, ValueError):
            if not force:
                return None

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_file


def _update_project_copilot_settings(settings_file: Path) -> bool:
    """Safely update .github/copilot/settings.json to enable the Eidos plugin."""
    _check_not_symlink(settings_file)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_file.exists():
        try:
            with open(settings_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            data = {}

    if "plugins" not in data or not isinstance(data["plugins"], list):
        data["plugins"] = []

    has_plugin_entry = any(
        isinstance(p, dict) and p.get("path") in (".github/plugin", "./.github/plugin")
        for p in data["plugins"]
    )
    if not has_plugin_entry:
        data["plugins"].append({"path": ".github/plugin"})

    if "enabledPlugins" not in data or not isinstance(data["enabledPlugins"], dict):
        data["enabledPlugins"] = {}
    data["enabledPlugins"]["eidos"] = True

    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True


def _remove_project_copilot_settings(settings_file: Path) -> bool:
    """Safely remove Eidos plugin from .github/copilot/settings.json."""
    if not settings_file.exists():
        return False
    try:
        with open(settings_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return False

    changed = False
    if "enabledPlugins" in data and isinstance(data["enabledPlugins"], dict) and "eidos" in data["enabledPlugins"]:
        del data["enabledPlugins"]["eidos"]
        changed = True

    if "plugins" in data and isinstance(data["plugins"], list):
        orig_len = len(data["plugins"])
        data["plugins"] = [
            p
            for p in data["plugins"]
            if not (isinstance(p, dict) and p.get("path") in (".github/plugin", "./.github/plugin"))
        ]
        if len(data["plugins"]) != orig_len:
            changed = True

    if not changed:
        return False

    has_other_plugins = bool(data.get("plugins"))
    has_other_enabled = bool(data.get("enabledPlugins"))
    has_other_keys = any(k for k in data if k not in ("plugins", "enabledPlugins"))

    if not has_other_plugins and not has_other_enabled and not has_other_keys:
        settings_file.unlink()
        parent = settings_file.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return True


def _remove_project_opencode_json(file_path: Path) -> bool:
    """Safely remove Eidos MCP server from opencode.json."""
    if not file_path.exists():
        return False
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return False

    mcp = data.get("mcp", {})
    if "eidos" not in mcp:
        return False

    del mcp["eidos"]
    data["mcp"] = mcp

    # If mcp is now empty and only $schema remains, remove file
    remaining_keys = set(data.keys()) - {"$schema"}
    if not mcp and not any(k for k in remaining_keys if k != "mcp"):
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return True


def _ensure_codex_plugin_manifest(target_dir: Path, force: bool = False) -> Path | None:
    """Ensure .codex/plugin.json exists in target plugin directory with OpenAI Codex metadata."""
    _check_not_symlink(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = target_dir / "plugin.json"
    _check_not_symlink(manifest_file, root=target_dir)
    bundled_ver = get_bundled_plugin_version()

    manifest_data = {
        "name": "eidos",
        "displayName": "Eidos AI Lifecycle Ecosystem",
        "version": bundled_ver,
        "description": "Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework for OpenAI Codex.",
        "author": {
            "name": "Eidos Contributors",
            "url": "https://github.com/eidos-ai/eidos",
        },
        "homepage": "https://github.com/eidos-ai/eidos",
        "repository": "https://github.com/eidos-ai/eidos",
        "license": "MIT",
        "extensions": {
            "com.openai": {
                "type": "agent_plugin",
                "skills": "skills",
            }
        },
    }

    if manifest_file.exists():
        try:
            with open(manifest_file, encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict) or existing.get("name") != "eidos":
                if not force:
                    return None
            else:
                existing["version"] = bundled_ver
                if "extensions" not in existing or not isinstance(existing["extensions"], dict):
                    existing["extensions"] = {}
                existing["extensions"]["com.openai"] = {
                    "type": "agent_plugin",
                    "skills": "skills",
                }
                manifest_data = existing
        except (OSError, json.JSONDecodeError, ValueError):
            if not force:
                return None

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_file


def _write_codex_rules(file_path: Path, force: bool = False) -> Path | None:
    """Write .codex/rules.md."""
    _check_not_symlink(file_path)
    if file_path.exists() and not force:
        return None
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Eidos Architectural Governance for OpenAI Codex\n\n"
        "This repository is governed by Eidos living specifications (`eidos/`).\n"
        "- Adhere to living specifications as the single source of truth.\n"
        "- Never create ADRs; all architectural changes require SpecDeltas (`eidos/deltas/`).\n"
        "- Validate specifications with `eidos validate eidos` and check drift with `eidos drift --uncommitted`.\n"
    )
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


def _uninstall_from_manifest_or_legacy(target_dir: Path) -> bool:
    """Remove manifest-tracked files or fallback to safe legacy cleanup without wiping shared parent."""
    if not target_dir.exists():
        return False

    manifest_file = target_dir / MANIFEST_FILENAME
    removed_any = False

    if manifest_file.exists():
        try:
            with open(manifest_file, encoding="utf-8") as f:
                data = json.load(f)
            files_dict = data.get("files", {})
        except (OSError, json.JSONDecodeError, ValueError):
            files_dict = {}

        for rel_path in sorted(files_dict.keys(), reverse=True):
            f_path = target_dir / rel_path
            if f_path.is_symlink() or f_path.is_file():
                with contextlib.suppress(OSError):
                    f_path.unlink()
                    removed_any = True
            elif f_path.is_dir():
                with contextlib.suppress(OSError):
                    f_path.rmdir()
                    removed_any = True

        if manifest_file.exists():
            with contextlib.suppress(OSError):
                manifest_file.unlink()
                removed_any = True

        _prune_empty_dirs(target_dir)

        if target_dir.exists() and not any(target_dir.iterdir()):
            with contextlib.suppress(OSError):
                target_dir.rmdir()

        return removed_any

    # Legacy install fallback (no .eidos-manifest.json): safely remove known Eidos files/subdirs
    known_eidos_files = {
        "plugin.json",
        "hooks.json",
        "rules.md",
        MANIFEST_FILENAME,
    }
    for item_name in known_eidos_files:
        f_path = target_dir / item_name
        if f_path.is_symlink() or f_path.is_file():
            if item_name == "plugin.json":
                try:
                    with open(f_path, encoding="utf-8") as pf:
                        pdata = json.load(pf)
                    if not isinstance(pdata, dict) or pdata.get("name") != "eidos":
                        continue
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
            elif item_name == "rules.md":
                try:
                    rtext = f_path.read_text(encoding="utf-8")
                    if "Eidos" not in rtext and "# Eidos" not in rtext:
                        continue
                except OSError:
                    continue
            with contextlib.suppress(OSError):
                f_path.unlink()
                removed_any = True

    # Check skills/
    skills_dir = target_dir / "skills"
    if skills_dir.exists() and skills_dir.is_dir() and _remove_skills_flat(None, skills_dir):
        removed_any = True

    # Check commands/
    commands_dir = target_dir / "commands"
    if commands_dir.exists() and commands_dir.is_dir():
        for cmd_name in ("validate.md", "drift.md", "reconcile.md", "context.md"):
            cf = commands_dir / cmd_name
            if cf.exists():
                with contextlib.suppress(OSError):
                    cf.unlink()
                    removed_any = True
        if not any(commands_dir.iterdir()):
            with contextlib.suppress(OSError):
                commands_dir.rmdir()

    # Check .claude-plugin/
    claude_meta_dir = target_dir / ".claude-plugin"
    if claude_meta_dir.exists() and claude_meta_dir.is_dir():
        cf = claude_meta_dir / "plugin.json"
        if cf.exists():
            is_eidos_claude_manifest = False
            try:
                with open(cf, encoding="utf-8") as cpf:
                    cpdata = json.load(cpf)
                if isinstance(cpdata, dict) and cpdata.get("name") == "eidos":
                    is_eidos_claude_manifest = True
            except (OSError, json.JSONDecodeError, ValueError):
                is_eidos_claude_manifest = False
            if is_eidos_claude_manifest:
                with contextlib.suppress(OSError):
                    cf.unlink()
                    removed_any = True
        if not any(claude_meta_dir.iterdir()):
            with contextlib.suppress(OSError):
                claude_meta_dir.rmdir()

    _prune_empty_dirs(target_dir)

    if target_dir.exists() and not any(target_dir.iterdir()):
        with contextlib.suppress(OSError):
            target_dir.rmdir()

    return removed_any


_legacy_safe_cleanup_codex_or_dest = _uninstall_from_manifest_or_legacy


# ---------------------------------------------------------------------------
# Table-Driven Target Adapter Registry (CMP-PLUGIN-INSTALLER)
# ---------------------------------------------------------------------------


def _prune_empty_dirs_list(root: Path, rel_dirs: tuple[str, ...]) -> None:
    """Prune empty directories in order if they exist and contain no entries."""
    for rel in rel_dirs:
        d = root / rel
        if d.exists() and d.is_dir() and not any(d.iterdir()):
            with contextlib.suppress(OSError):
                d.rmdir()


def _claude_pre_manifest_hook(root: Path, plugin_dir: Path, force: bool, launcher: str) -> list[Path]:
    extra: list[Path] = []
    manifests = _ensure_claude_plugin_manifest(plugin_dir, force=force)
    if manifests is not None:
        extra.extend(m for m in manifests if m is not None)
    extra.extend(_write_claude_commands(plugin_dir, launcher=launcher, force=force))
    claude_settings = root / ".claude" / "settings.json"
    _update_project_claude_settings(claude_settings, "eidos@plugins-dir")
    extra.append(claude_settings)
    return extra


def _codex_pre_manifest_hook(root: Path, source_dir: Path, plugin_dir: Path, force: bool) -> list[Path]:
    extra: list[Path] = []
    manifest = _ensure_codex_plugin_manifest(plugin_dir, force=force)
    if manifest is not None:
        extra.append(manifest)
    codex_rules = plugin_dir / "rules.md"
    written_rules = _write_codex_rules(codex_rules, force=force)
    if written_rules is not None:
        extra.append(written_rules)
    extra.extend(_copy_skills_flat(source_dir, plugin_dir / "skills", force=force))
    return extra


def _copilot_pre_manifest_hook(root: Path, plugin_dir: Path, force: bool) -> list[Path]:
    extra: list[Path] = []
    manifest = _ensure_copilot_plugin_manifest(plugin_dir, force=force)
    if manifest is not None:
        extra.append(manifest)
    return extra


def _copilot_post_install_hook(root: Path, force: bool) -> list[Path]:
    copilot_settings = root / ".github" / "copilot" / "settings.json"
    _update_project_copilot_settings(copilot_settings)
    copilot_instructions = root / ".github" / "copilot-instructions.md"
    _write_copilot_instructions(copilot_instructions, force=force)
    return [copilot_settings, copilot_instructions]


def _copilot_extra_uninstall(root: Path) -> bool:
    removed_settings = _remove_project_copilot_settings(root / ".github" / "copilot" / "settings.json")
    removed_instructions = _remove_copilot_instructions(root / ".github" / "copilot-instructions.md")
    return removed_settings or removed_instructions


@dataclass(frozen=True)
class TargetAdapter:
    """Declarative table-driven adapter encapsulating target-specific plugin installation and uninstallation."""

    target: PluginInstallTarget
    name: str
    plugin_subdir: str | None = None
    skills_subdir: str | None = None
    install_destination_subdir: str | None = None
    uninstall_destination_subdir: str | None = None
    install_message: str = ""
    uninstall_removed_message: str = ""
    uninstall_clean_message: str = ""
    copy_plugin_tree: bool = True
    mcp_cleanup_paths: tuple[str, ...] = ()
    extra_cleanup_files: tuple[str, ...] = ()
    legacy_cleanup_dirs: tuple[str, ...] = ()
    prune_dirs: tuple[str, ...] = ()
    pre_manifest_hook: Callable[[Path, Path, Path, bool, str], list[Path]] | None = None
    post_install_hook: Callable[[Path, Path, bool, str], list[Path]] | None = None
    extra_uninstall_hook: Callable[[Path], bool] | None = None

    def install(self, root: Path, source_dir: Path, force: bool = False, launcher: str = "eidos") -> InstallResult:
        """Execute declarative installation pipeline for this target."""
        created: list[Path] = []
        plugin_dir = (root / self.plugin_subdir) if self.plugin_subdir else None

        if plugin_dir is not None:
            _check_not_symlink(plugin_dir, root=root)
            if self.copy_plugin_tree:
                created.extend(_copy_plugin_tree(source_dir, plugin_dir, force=force, launcher=launcher))
            if self.pre_manifest_hook is not None:
                created.extend(self.pre_manifest_hook(root, source_dir, plugin_dir, force, launcher))
            manifest = _write_installation_manifest(plugin_dir, created)
            created.append(manifest)

        if self.post_install_hook is not None:
            created.extend(self.post_install_hook(root, source_dir, force, launcher))

        if self.skills_subdir is not None:
            created.extend(_copy_skills_flat(source_dir, root / self.skills_subdir, force=force))

        dest_rel = self.install_destination_subdir or self.plugin_subdir or ""
        destination = (root / dest_rel) if dest_rel else root
        return InstallResult(
            target=self.name,
            destination=destination,
            success=True,
            message=self.install_message,
            created_files=created,
        )

    def uninstall(self, root: Path, source_dir: Path | None = None) -> UninstallResult:
        """Execute declarative uninstallation pipeline for this target."""
        was_removed = False
        plugin_dir = (root / self.plugin_subdir) if self.plugin_subdir else None

        if (
            plugin_dir is not None
            and plugin_dir.exists()
            and _uninstall_from_manifest_or_legacy(plugin_dir)
        ):
            was_removed = True

        for legacy_rel in self.legacy_cleanup_dirs:
            legacy_dir = root / legacy_rel
            if legacy_dir.exists():
                shutil.rmtree(legacy_dir)
                was_removed = True

        for file_rel in self.extra_cleanup_files:
            f_path = root / file_rel
            if f_path.exists():
                f_path.unlink()
                was_removed = True

        if self.skills_subdir is not None and _remove_skills_flat(source_dir, root / self.skills_subdir):
            was_removed = True

        for mcp_rel in self.mcp_cleanup_paths:
            if _remove_project_mcp_server(root / mcp_rel, "eidos"):
                was_removed = True

        if self.extra_uninstall_hook is not None and self.extra_uninstall_hook(root):
            was_removed = True

        _prune_empty_dirs_list(root, self.prune_dirs)

        dest_rel = self.uninstall_destination_subdir or self.plugin_subdir or self.install_destination_subdir or ""
        destination = (root / dest_rel) if dest_rel else root
        rel_str = str(destination.relative_to(root) if destination.is_relative_to(root) else destination)
        msg_template = self.uninstall_removed_message if was_removed else self.uninstall_clean_message
        return UninstallResult(
            target=self.name,
            destination=destination,
            success=True,
            message=msg_template.format(rel=rel_str),
            removed=was_removed,
        )


def _make_project_adapter(target: PluginInstallTarget, name: str) -> TargetAdapter:
    return TargetAdapter(
        target=target,
        name=name,
        plugin_subdir=".agents/plugins/eidos",
        install_message="Installed to project scope .agents/plugins/eidos (agent-plugins.org v1.0.0).",
        uninstall_removed_message="Removed plugin directory at {rel}.",
        uninstall_clean_message="No plugin found at {rel} (already removed).",
        prune_dirs=(".agents/plugins", ".agents"),
    )


_OPENCODE_ADAPTER_SPEC = dict(
    name="open-code",
    plugin_subdir=".opencode/plugins/eidos",
    skills_subdir=".opencode/skills",
    uninstall_destination_subdir="opencode.json",
    install_message="Configured plugin bundle in .opencode/plugins/eidos and 4 skills in .opencode/skills/.",
    uninstall_removed_message="Removed opencode.json MCP, .opencode/plugins/eidos, and .opencode/skills/.",
    uninstall_clean_message="No OpenCode configuration found (already removed).",
    extra_cleanup_files=(".opencode/rules.md",),
    prune_dirs=(".opencode/plugins", ".opencode"),
    extra_uninstall_hook=lambda root: _remove_project_opencode_json(root / "opencode.json"),
)


TARGET_ADAPTERS: dict[PluginInstallTarget, TargetAdapter] = {
    PluginInstallTarget.PROJECT: _make_project_adapter(PluginInstallTarget.PROJECT, "project"),
    PluginInstallTarget.ANTIGRAVITY: _make_project_adapter(PluginInstallTarget.ANTIGRAVITY, "antigravity"),
    PluginInstallTarget.CURSOR: TargetAdapter(
        target=PluginInstallTarget.CURSOR,
        name="cursor",
        skills_subdir=".cursor/skills",
        install_destination_subdir=".cursor/rules/eidos.mdc",
        uninstall_destination_subdir=".cursor/mcp.json",
        install_message="Configured rules in .cursor/rules/eidos.mdc and 4 skills in .cursor/skills/.",
        uninstall_removed_message="Removed .cursor/rules/eidos.mdc, .cursor/skills/, and updated .cursor/mcp.json.",
        uninstall_clean_message="No Cursor configuration found (already removed).",
        mcp_cleanup_paths=(".cursor/mcp.json",),
        extra_cleanup_files=(".cursor/rules/eidos.mdc",),
        prune_dirs=(".cursor/rules", ".cursor"),
        post_install_hook=lambda root, _src, force, _l: [_write_cursor_rules(root / ".cursor" / "rules" / "eidos.mdc", force=force)],
    ),
    PluginInstallTarget.CLAUDE: TargetAdapter(
        target=PluginInstallTarget.CLAUDE,
        name="claude",
        plugin_subdir=".claude/plugins/eidos",
        skills_subdir=".claude/skills",
        install_message="Installed Claude Code plugin at .claude/plugins/eidos, 4 skills in .claude/skills/, commands in .claude/plugins/eidos/commands/, and configured .claude/settings.json.",
        uninstall_removed_message="Removed Claude Code plugin from .claude/plugins/eidos, skills from .claude/skills/, and cleaned up .claude configuration.",
        uninstall_clean_message="No Claude Code plugin or MCP config found (already removed).",
        mcp_cleanup_paths=(".claude/mcp.json",),
        legacy_cleanup_dirs=(".claude/skills/eidos",),
        prune_dirs=(".claude/plugins", ".claude"),
        pre_manifest_hook=lambda root, _src, plugin_dir, force, launcher: _claude_pre_manifest_hook(
            root, plugin_dir, force, launcher
        ),
        extra_uninstall_hook=lambda root: _remove_project_claude_settings(
            root / ".claude" / "settings.json", "eidos@plugins-dir"
        ),
    ),
    PluginInstallTarget.AIDER: TargetAdapter(
        target=PluginInstallTarget.AIDER,
        name="aider",
        skills_subdir=".aider/skills",
        install_destination_subdir=".aider.conf.yml",
        uninstall_destination_subdir=".aider.conf.yml",
        install_message="Configured .aider.conf.yml, generated .aider.conventions.md, and installed 4 skills into .aider/skills/.",
        uninstall_removed_message="Removed .aider.conventions.md, .aider/skills/, and updated .aider.conf.yml.",
        uninstall_clean_message="No Aider configuration found (already removed).",
        extra_cleanup_files=(".aider.conventions.md",),
        post_install_hook=lambda root, _src, force, _l: [
            _write_aider_conventions(root / ".aider.conventions.md", force=force),
            _update_project_aider_conf(root / ".aider.conf.yml", ".aider.conventions.md"),
        ],
        extra_uninstall_hook=lambda root: _remove_project_aider_conf(root / ".aider.conf.yml", ".aider.conventions.md"),
    ),
    PluginInstallTarget.CLINE: TargetAdapter(
        target=PluginInstallTarget.CLINE,
        name="cline",
        plugin_subdir=".cline/plugins/eidos",
        skills_subdir=".cline/skills",
        uninstall_destination_subdir=".cline",
        install_message="Configured plugin bundle in .cline/plugins/eidos and 4 skills in .cline/skills/.",
        uninstall_removed_message="Removed .cline MCP settings, plugin bundle in .cline/plugins/eidos, and .cline/skills/.",
        uninstall_clean_message="No Cline configurations found (already removed).",
        mcp_cleanup_paths=(".cline/mcp.json", ".cline/cline_mcp_settings.json"),
        extra_cleanup_files=(".clinerules",),
        legacy_cleanup_dirs=(".cline/skills/eidos",),
        prune_dirs=(".cline/plugins", ".cline"),
    ),
    PluginInstallTarget.OPEN_CODE: TargetAdapter(target=PluginInstallTarget.OPEN_CODE, **_OPENCODE_ADAPTER_SPEC),
    PluginInstallTarget.OPENCODE: TargetAdapter(target=PluginInstallTarget.OPENCODE, **_OPENCODE_ADAPTER_SPEC),
    PluginInstallTarget.CODEX: TargetAdapter(
        target=PluginInstallTarget.CODEX,
        name="codex",
        plugin_subdir=".codex",
        copy_plugin_tree=False,
        install_message="Installed OpenAI Codex plugin bundle (.codex/plugin.json, 4 skills, rules.md).",
        uninstall_removed_message="Removed OpenAI Codex plugin bundle (.codex/).",
        uninstall_clean_message="No Codex plugin bundle found (already removed).",
        pre_manifest_hook=lambda root, src, plugin_dir, force, _l: _codex_pre_manifest_hook(
            root, src, plugin_dir, force
        ),
    ),
    PluginInstallTarget.COPILOT: TargetAdapter(
        target=PluginInstallTarget.COPILOT,
        name="copilot",
        plugin_subdir=".github/plugin",
        skills_subdir=".github/skills",
        install_message="Installed GitHub Copilot plugin (.github/plugin/), 4 skills (.github/skills/), settings (.github/copilot/settings.json), and instructions.",
        uninstall_removed_message="Removed GitHub Copilot plugin (.github/plugin/), skills (.github/skills/), and configuration.",
        uninstall_clean_message="No GitHub Copilot configuration found (already removed).",
        mcp_cleanup_paths=(".github/copilot/mcp.json", ".vscode/mcp.json"),
        prune_dirs=(".github/plugin", ".github/copilot"),
        pre_manifest_hook=lambda root, _src, plugin_dir, force, _l: _copilot_pre_manifest_hook(root, plugin_dir, force),
        post_install_hook=lambda root, _src, force, _l: _copilot_post_install_hook(root, force),
        extra_uninstall_hook=_copilot_extra_uninstall,
    ),
}


def install_plugin(
    target: PluginInstallTarget = PluginInstallTarget.PROJECT,
    project_dir: Path | None = None,
    dest_path: Path | None = None,
    force: bool = False,
    launcher: str = "eidos",
) -> list[InstallResult]:
    """Install the Eidos agent plugin strictly inside project scope (.agents/plugins/eidos)."""
    source_dir = locate_plugin_bundle()
    root = (project_dir or Path.cwd()).resolve()
    results = []

    # If explicit destination provided, install there directly
    if dest_path is not None:
        target_path = dest_path if dest_path.is_absolute() else (root / dest_path)
        _check_not_symlink(target_path, root=root if not dest_path.is_absolute() else None)
        created = _copy_plugin_tree(source_dir, target_path, force=force, launcher=launcher)
        manifest = _write_installation_manifest(target_path, created)
        created.append(manifest)
        results.append(
            InstallResult(
                target="custom",
                destination=target_path,
                success=True,
                message=f"Installed plugin bundle to custom project path: {target_path}",
                created_files=created,
            )
        )
        return results

    # Determine project targets
    targets_to_run: list[PluginInstallTarget] = []
    if target == PluginInstallTarget.ALL:
        targets_to_run = [
            PluginInstallTarget.PROJECT,
            PluginInstallTarget.CURSOR,
            PluginInstallTarget.CLAUDE,
            PluginInstallTarget.AIDER,
            PluginInstallTarget.CLINE,
            PluginInstallTarget.OPEN_CODE,
            PluginInstallTarget.CODEX,
            PluginInstallTarget.COPILOT,
        ]
    elif target == PluginInstallTarget.PROJECT:
        targets_to_run = [
            PluginInstallTarget.PROJECT,
            PluginInstallTarget.CURSOR,
            PluginInstallTarget.CLAUDE,
        ]
    else:
        targets_to_run = [target]

    for t in targets_to_run:
        adapter = TARGET_ADAPTERS.get(t)
        if adapter is not None:
            results.append(adapter.install(root, source_dir, force, launcher))

    return results


def uninstall_plugin(
    target: PluginInstallTarget = PluginInstallTarget.PROJECT,
    project_dir: Path | None = None,
    dest_path: Path | None = None,
) -> list[UninstallResult]:
    """Uninstall / remove the Eidos agent plugin strictly from project scope."""
    root = (project_dir or Path.cwd()).resolve()
    results = []

    # If explicit destination provided, remove there directly
    if dest_path is not None:
        target_path = dest_path if dest_path.is_absolute() else (root / dest_path)
        if target_path.exists():
            if target_path.is_file():
                target_path.unlink()
                removed = True
            else:
                removed = _uninstall_from_manifest_or_legacy(target_path)
            results.append(
                UninstallResult(
                    target="custom",
                    destination=target_path,
                    success=True,
                    message=f"Removed custom plugin path: {target_path}" if removed else f"No plugin files found at: {target_path}",
                    removed=removed,
                )
            )
        else:
            results.append(
                UninstallResult(
                    target="custom",
                    destination=target_path,
                    success=True,
                    message=f"Custom path '{target_path}' does not exist.",
                    removed=False,
                )
            )
        return results

    targets_to_run: list[PluginInstallTarget] = []
    if target == PluginInstallTarget.ALL:
        targets_to_run = [
            PluginInstallTarget.PROJECT,
            PluginInstallTarget.CURSOR,
            PluginInstallTarget.CLAUDE,
            PluginInstallTarget.AIDER,
            PluginInstallTarget.CLINE,
            PluginInstallTarget.OPEN_CODE,
            PluginInstallTarget.CODEX,
            PluginInstallTarget.COPILOT,
        ]
    elif target == PluginInstallTarget.PROJECT:
        targets_to_run = [
            PluginInstallTarget.PROJECT,
            PluginInstallTarget.CURSOR,
            PluginInstallTarget.CLAUDE,
        ]
    else:
        targets_to_run = [target]

    try:
        source_dir = locate_plugin_bundle()
    except (FileNotFoundError, OSError):
        source_dir = None

    for t in targets_to_run:
        adapter = TARGET_ADAPTERS.get(t)
        if adapter is not None:
            results.append(adapter.uninstall(root, source_dir))

    return results


def update_plugin(
    target: PluginInstallTarget = PluginInstallTarget.PROJECT,
    project_dir: Path | None = None,
    dest_path: Path | None = None,
    launcher: str = "eidos",
) -> tuple[str | None, str, list[InstallResult]]:
    """Update the installed Eidos agent plugin to the latest bundled version.

    Returns:
        (previous_version, current_version, install_results)
    """
    prev_version = get_installed_plugin_version(project_dir=project_dir, dest_path=dest_path)
    bundled_version = get_bundled_plugin_version()
    results = install_plugin(target=target, project_dir=project_dir, dest_path=dest_path, force=True, launcher=launcher)
    return prev_version, bundled_version, results
