"""Project-scoped installer and uninstaller for Eidos Agent Plugins across AI agent environments."""

import contextlib
import enum
import hashlib
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.spec.parser import YamlError, safe_dump_yaml, safe_load_yaml
from eidos.core.workspace.assets import get_plugin_reference_dir
from eidos.plugin.version import (
    get_bundled_plugin_version,
    get_installed_plugin_version,
)


class PluginInstallTarget(enum.StrEnum):
    PROJECT = "project"  # Installs .agents/plugins/eidos (agent-plugins.org v1.0.0) and client configurations
    ANTIGRAVITY = "antigravity"  # Installs .agents/plugins/eidos (strict agent-plugins.org v1.0.0)
    CLAUDE = "claude"  # Configures Claude Code skills (.claude/skills) and Stop hook (.claude/settings.json)
    CURSOR = "cursor"  # Configures Cursor rules (.cursor/rules/eidos.mdc) and skills (.cursor/skills)
    AIDER = "aider"  # Configures .aider.conf.yml and .aider.conventions.md
    CLINE = "cline"  # Configures .cline/plugins/eidos and .cline/skills
    OPEN_CODE = "open-code"  # Configures .opencode/plugins/eidos and .opencode/skills
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

    raise FileNotFoundError("Could not locate Eidos plugin reference in assets or repository.")


ACTIVE_EIDOS_SKILLS: frozenset[str] = frozenset(
    {
        "eidos",
        "eidos-spec",
        "eidos-plan",
        "eidos-ops",
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
            old_data = orjson.loads(manifest_file.read_bytes())
            old_files = old_data.get("files", {})
            if isinstance(old_files, dict):
                for rel_path, digest in old_files.items():
                    candidate = target_dir / rel_path
                    if candidate.is_file() and not candidate.is_symlink():
                        existing_files[rel_path] = str(digest)
        except (OSError, orjson.JSONDecodeError, ValueError):
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
        if rel == "settings.json":
            continue
        manifest_data["files"][rel] = _compute_sha256(f)

    manifest_file.write_bytes(orjson.dumps(manifest_data, option=orjson.OPT_INDENT_2))

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
    source_dir: Path,
    target_dir: Path,
    force: bool = False,
    launcher: str = "eidos",
    include_skills: bool = True,
) -> list[Path]:
    """Copy all plugin files to target destination within the project."""
    _check_not_symlink(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)

    target_skills_dir = target_dir / "skills"
    if target_skills_dir.exists() and not include_skills:
        _remove_skills_flat(source_dir, target_skills_dir)

    created = []
    for item in source_dir.rglob("*"):
        if item.is_file():
            rel = item.relative_to(source_dir)
            if not include_skills and rel.parts and rel.parts[0] == "skills":
                continue
            # Never copy internal cache or assistant-specific directories into plugins
            if any(part in (".claude-plugin", ".eidos", ".cache", ".git") for part in rel.parts):
                continue
            if rel.name == MANIFEST_FILENAME:
                continue
            dest = target_dir / rel
            _check_not_symlink(dest, root=target_dir)
            if dest.exists() and not force:
                if _compute_sha256(dest) == _compute_sha256(item):
                    created.append(dest)
                elif dest.name == "plugin.json":
                    with contextlib.suppress(OSError, orjson.JSONDecodeError, ValueError):
                        existing_json = orjson.loads(dest.read_bytes())
                        if isinstance(existing_json, dict) and existing_json.get("name") == "eidos":
                            created.append(dest)
                elif dest.name == "hooks.json":
                    with contextlib.suppress(OSError):
                        if "eidos" in dest.read_text(encoding="utf-8", errors="ignore"):
                            created.append(dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            created.append(dest)

    # Ensure plugin.json reflects the bundled plugin version
    target_manifest = target_dir / "plugin.json"
    if target_manifest.exists() and (target_manifest in created or force):
        try:
            data = orjson.loads(target_manifest.read_bytes())
            bundled_ver = get_bundled_plugin_version()
            if isinstance(data, dict) and (force or data.get("name") == "eidos") and data.get("version") != bundled_ver:
                data["version"] = bundled_ver
                target_manifest.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        except (OSError, orjson.JSONDecodeError, ValueError):
            pass

    # Rewrite hook commands if launcher == "uvx"
    target_hooks = target_dir / "hooks.json"
    if target_hooks.exists() and launcher == "uvx":
        _rewrite_hook_command_for_target_root(target_hooks)

    return created


def _copy_skills_flat(source_dir: Path, target_skills_dir: Path, force: bool = False) -> list[Path]:
    """Copy individual skills from source bundle into target_skills_dir/<skill-name>/."""
    _check_not_symlink(target_skills_dir)

    source_skills = source_dir / "skills" if (source_dir / "skills").exists() else source_dir
    if not source_skills.exists():
        return []

    target_skills_dir.mkdir(parents=True, exist_ok=True)

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
                        if _compute_sha256(dest_file) == _compute_sha256(file_path):
                            created.append(dest_file)
                        continue
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, dest_file)
                    created.append(dest_file)
    return created


def _remove_skills_flat(source_dir: Path | None, target_skills_dir: Path) -> bool:
    """Remove known active Eidos skills from target_skills_dir without removing unrelated user skills."""
    if not target_skills_dir.exists():
        return False

    known_skills = set(ACTIVE_EIDOS_SKILLS)
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


def _is_eidos_hook_command(cmd: Any) -> bool:
    """Return True if a hook command string invokes 'eidos hook' directly or via uvx."""
    if not isinstance(cmd, str):
        return False
    return bool(re.search(r"(?:^|\s)eidos\s+hook(?:\s|$)", cmd.strip()))


def _is_eidos_stop_hook_entry(entry: Any) -> bool:
    """Return True if a Claude Code Stop hook entry contains only or any Eidos hook handler."""
    if not isinstance(entry, dict):
        return False
    if _is_eidos_hook_command(entry.get("command")):
        return True
    sub_hooks = entry.get("hooks")
    if isinstance(sub_hooks, list):
        return any(isinstance(h, dict) and _is_eidos_hook_command(h.get("command")) for h in sub_hooks)
    return False


def _cleanup_legacy_claude_plugin_keys(data: dict[str, Any]) -> bool:
    """Remove legacy Eidos 'plugins' and 'enabledPlugins' keys from a Claude settings dict."""
    changed = False
    enabled = data.get("enabledPlugins")
    if isinstance(enabled, dict):
        for k in ("eidos", "eidos@plugins-dir", "eidos@local", "eidos@skills-dir"):
            if k in enabled:
                del enabled[k]
                changed = True
        if not enabled:
            data.pop("enabledPlugins", None)
            changed = True

    plugins_list = data.get("plugins")
    if isinstance(plugins_list, list):
        filtered = [
            p
            for p in plugins_list
            if not (isinstance(p, dict) and p.get("path") in (".claude/plugins/eidos", "./.claude/plugins/eidos"))
        ]
        if len(filtered) != len(plugins_list):
            changed = True
        if filtered:
            data["plugins"] = filtered
        else:
            data.pop("plugins", None)
            changed = True

    return changed


def _update_project_claude_settings(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .claude/settings.json to register the Eidos Stop hook."""
    _check_not_symlink(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if file_path.exists():
        try:
            loaded = orjson.loads(file_path.read_bytes())
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, orjson.JSONDecodeError, ValueError):
            data = {}

    _cleanup_legacy_claude_plugin_keys(data)

    bundled_ver = get_bundled_plugin_version()
    hook_cmd = f"uvx --from eidos-ai=={bundled_ver} eidos hook" if launcher == "uvx" else "eidos hook"
    eidos_sub_hook = {
        "type": "command",
        "command": hook_cmd,
        "timeout": 30,
    }

    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    stop_list = hooks_dict.get("Stop")
    if not isinstance(stop_list, list):
        stop_list = []

    new_stop_list: list[Any] = []
    replaced = False
    for entry in stop_list:
        if _is_eidos_stop_hook_entry(entry) and isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            new_sub_hooks: list[Any] = []
            for h in entry["hooks"]:
                if isinstance(h, dict) and _is_eidos_hook_command(h.get("command")):
                    if not replaced:
                        new_sub_hooks.append(eidos_sub_hook)
                        replaced = True
                else:
                    new_sub_hooks.append(h)
            if new_sub_hooks:
                new_stop_list.append({**entry, "hooks": new_sub_hooks})
        else:
            new_stop_list.append(entry)

    if not replaced:
        new_stop_list.append({"hooks": [eidos_sub_hook]})

    hooks_dict["Stop"] = new_stop_list
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return file_path


def _remove_project_claude_settings(file_path: Path) -> bool:
    """Safely remove Eidos Stop hook and legacy keys from project-level .claude/settings.json."""
    if not file_path.exists():
        return False
    try:
        data = orjson.loads(file_path.read_bytes())
        if not isinstance(data, dict):
            return False
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False

    changed = _cleanup_legacy_claude_plugin_keys(data)

    hooks_dict = data.get("hooks")
    if isinstance(hooks_dict, dict):
        stop_list = hooks_dict.get("Stop")
        if isinstance(stop_list, list):
            new_stop_list: list[Any] = []
            for entry in stop_list:
                if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                    kept_sub = [
                        h
                        for h in entry["hooks"]
                        if not (isinstance(h, dict) and _is_eidos_hook_command(h.get("command")))
                    ]
                    if len(kept_sub) != len(entry["hooks"]):
                        changed = True
                    if kept_sub:
                        new_stop_list.append({**entry, "hooks": kept_sub})
                elif _is_eidos_stop_hook_entry(entry):
                    changed = True
                else:
                    new_stop_list.append(entry)

            if new_stop_list:
                hooks_dict["Stop"] = new_stop_list
            else:
                hooks_dict.pop("Stop", None)
                changed = True

        if not hooks_dict:
            data.pop("hooks", None)

    if not changed:
        return False

    if not data:
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
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


def _update_project_aider_conf(file_path: Path, conventions_path: str = ".aider.conventions.md") -> Path:
    """Ensure conventions file is listed under 'read' in .aider.conf.yml."""
    _check_not_symlink(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if file_path.exists():
        try:
            with open(file_path, encoding="utf-8") as f:
                loaded = safe_load_yaml(f)
                if isinstance(loaded, dict):
                    data = loaded
        except (OSError, YamlError):
            data = {}

    read_list = data.get("read", [])
    if not isinstance(read_list, list):
        read_list = [read_list] if read_list else []

    if conventions_path not in read_list:
        read_list.append(conventions_path)

    data["read"] = read_list

    with open(file_path, "w", encoding="utf-8") as f:
        safe_dump_yaml(data, f)
    return file_path


def _remove_project_aider_conf(file_path: Path, conventions_path: str = ".aider.conventions.md") -> bool:
    """Safely remove conventions file from .aider.conf.yml."""
    if not file_path.exists():
        return False

    try:
        with open(file_path, encoding="utf-8") as f:
            data = safe_load_yaml(f)
        if not isinstance(data, dict):
            return False
    except (OSError, YamlError):
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
        safe_dump_yaml(data, f)
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
        '- **Architectural Context**: Use `eidos context --file <path>` or `eidos context --task "<prompt>"` for token-efficient architectural context.\n\n'
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
    if file_path.exists() and not force and COPILOT_START_MARKER in file_path.read_text(encoding="utf-8"):
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
        '- **Architectural Context**: Use `eidos context --file <path>` or `eidos context --task "<prompt>"` for token-efficient architectural context.\n'
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
            existing = orjson.loads(manifest_file.read_bytes())
            if not isinstance(existing, dict) or existing.get("name") != "eidos":
                if not force:
                    return None
            else:
                merged_ext = dict(existing.get("extensions") if isinstance(existing.get("extensions"), dict) else {})
                merged_ext["com.github.copilot"] = {
                    "type": "agent_plugin",
                    "skills": "skills",
                }
                manifest_data = {**existing, **manifest_data, "extensions": merged_ext}
        except (OSError, orjson.JSONDecodeError, ValueError):
            if not force:
                return None

    manifest_file.write_bytes(orjson.dumps(manifest_data, option=orjson.OPT_INDENT_2))

    return manifest_file


def _update_project_copilot_settings(settings_file: Path) -> bool:
    """Safely update .github/copilot/settings.json to enable the Eidos plugin."""
    _check_not_symlink(settings_file)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_file.exists():
        try:
            data = orjson.loads(settings_file.read_bytes())
        except (OSError, orjson.JSONDecodeError, ValueError):
            data = {}

    if "plugins" not in data or not isinstance(data["plugins"], list):
        data["plugins"] = []

    has_plugin_entry = any(
        isinstance(p, dict) and p.get("path") in (".github/plugin", "./.github/plugin") for p in data["plugins"]
    )
    if not has_plugin_entry:
        data["plugins"].append({"path": ".github/plugin"})

    if "enabledPlugins" not in data or not isinstance(data["enabledPlugins"], dict):
        data["enabledPlugins"] = {}
    data["enabledPlugins"]["eidos"] = True

    settings_file.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return True


def _remove_project_copilot_settings(settings_file: Path) -> bool:
    """Safely remove Eidos plugin from .github/copilot/settings.json."""
    if not settings_file.exists():
        return False
    try:
        data = orjson.loads(settings_file.read_bytes())
    except (OSError, orjson.JSONDecodeError, ValueError):
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
        settings_file.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
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
            existing = orjson.loads(manifest_file.read_bytes())
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
        except (OSError, orjson.JSONDecodeError, ValueError):
            if not force:
                return None

    manifest_file.write_bytes(orjson.dumps(manifest_data, option=orjson.OPT_INDENT_2))

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


def _uninstall_from_manifest(target_dir: Path) -> bool:
    """Remove manifest-tracked files without wiping shared parent."""
    if not target_dir.exists():
        return False

    manifest_file = target_dir / MANIFEST_FILENAME
    if not manifest_file.exists():
        return False

    removed_any = False
    try:
        data = orjson.loads(manifest_file.read_bytes())
        files_entry = data.get("files", {})
    except (OSError, orjson.JSONDecodeError, ValueError):
        files_entry = {}

    rel_paths = (
        list(files_entry.keys())
        if isinstance(files_entry, dict)
        else ([str(p) for p in files_entry] if isinstance(files_entry, list) else [])
    )
    for rel_path in sorted(rel_paths, reverse=True):
        if rel_path == "settings.json":
            continue
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


def _claude_post_install_hook(root: Path, launcher: str) -> list[Path]:
    legacy_plugin_dir = root / ".claude" / "plugins" / "eidos"
    if legacy_plugin_dir.exists():
        _uninstall_from_manifest(legacy_plugin_dir)
        if legacy_plugin_dir.exists() and (legacy_plugin_dir / "plugin.json").exists():
            shutil.rmtree(legacy_plugin_dir, ignore_errors=True)
        _prune_empty_dirs_list(root, (".claude/plugins/eidos", ".claude/plugins"))
    claude_settings = root / ".claude" / "settings.json"
    _update_project_claude_settings(claude_settings, launcher=launcher)
    return [claude_settings]


def _claude_extra_uninstall(root: Path) -> bool:
    removed_legacy = False
    legacy_plugin_dir = root / ".claude" / "plugins" / "eidos"
    if legacy_plugin_dir.exists():
        removed_legacy = _uninstall_from_manifest(legacy_plugin_dir)
        if legacy_plugin_dir.exists() and (legacy_plugin_dir / "plugin.json").exists():
            shutil.rmtree(legacy_plugin_dir, ignore_errors=True)
            removed_legacy = True
    removed_settings = _remove_project_claude_settings(root / ".claude" / "settings.json")
    return removed_legacy or removed_settings


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
    extra_cleanup_files: tuple[str, ...] = ()
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
                created.extend(
                    _copy_plugin_tree(
                        source_dir,
                        plugin_dir,
                        force=force,
                        launcher=launcher,
                        include_skills=(self.skills_subdir is None),
                    )
                )
            if self.pre_manifest_hook is not None:
                created.extend(self.pre_manifest_hook(root, source_dir, plugin_dir, force, launcher))

        if self.post_install_hook is not None:
            created.extend(self.post_install_hook(root, source_dir, force, launcher))

        if self.skills_subdir is not None:
            created.extend(_copy_skills_flat(source_dir, root / self.skills_subdir, force=force))

        if plugin_dir is not None:
            manifest = _write_installation_manifest(plugin_dir, created)
            created.append(manifest)

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

        if plugin_dir is not None and plugin_dir.exists() and _uninstall_from_manifest(plugin_dir):
            was_removed = True

        for file_rel in self.extra_cleanup_files:
            f_path = root / file_rel
            if f_path.exists():
                f_path.unlink()
                was_removed = True

        if self.skills_subdir is not None and _remove_skills_flat(source_dir, root / self.skills_subdir):
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


TARGET_ADAPTERS: dict[PluginInstallTarget, TargetAdapter] = {
    PluginInstallTarget.PROJECT: _make_project_adapter(PluginInstallTarget.PROJECT, "project"),
    PluginInstallTarget.ANTIGRAVITY: _make_project_adapter(PluginInstallTarget.ANTIGRAVITY, "antigravity"),
    PluginInstallTarget.CURSOR: TargetAdapter(
        target=PluginInstallTarget.CURSOR,
        name="cursor",
        skills_subdir=".cursor/skills",
        install_destination_subdir=".cursor/rules/eidos.mdc",
        uninstall_destination_subdir=".cursor/rules/eidos.mdc",
        install_message="Configured rules in .cursor/rules/eidos.mdc and 4 skills in .cursor/skills/.",
        uninstall_removed_message="Removed .cursor/rules/eidos.mdc and .cursor/skills/.",
        uninstall_clean_message="No Cursor configuration found (already removed).",
        extra_cleanup_files=(".cursor/rules/eidos.mdc",),
        prune_dirs=(".cursor/rules", ".cursor"),
        post_install_hook=lambda root, _src, force, _l: [
            _write_cursor_rules(root / ".cursor" / "rules" / "eidos.mdc", force=force)
        ],
    ),
    PluginInstallTarget.CLAUDE: TargetAdapter(
        target=PluginInstallTarget.CLAUDE,
        name="claude",
        plugin_subdir=".claude",
        skills_subdir=".claude/skills",
        copy_plugin_tree=False,
        install_message="Installed 4 Claude Code skills in .claude/skills/ and configured Stop hook in .claude/settings.json.",
        uninstall_removed_message="Removed Claude Code skills from .claude/skills/ and cleaned up .claude/settings.json.",
        uninstall_clean_message="No Claude Code configuration found (already removed).",
        prune_dirs=(".claude/skills", ".claude/plugins", ".claude"),
        post_install_hook=lambda root, _src, _force, launcher: _claude_post_install_hook(root, launcher),
        extra_uninstall_hook=_claude_extra_uninstall,
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
        uninstall_removed_message="Removed plugin bundle in .cline/plugins/eidos and .cline/skills/.",
        uninstall_clean_message="No Cline configurations found (already removed).",
        prune_dirs=(".cline/plugins", ".cline"),
    ),
    PluginInstallTarget.OPEN_CODE: TargetAdapter(
        target=PluginInstallTarget.OPEN_CODE,
        name="open-code",
        plugin_subdir=".opencode/plugins/eidos",
        skills_subdir=".opencode/skills",
        uninstall_destination_subdir=".opencode/plugins/eidos",
        install_message="Configured plugin bundle in .opencode/plugins/eidos and 4 skills in .opencode/skills/.",
        uninstall_removed_message="Removed .opencode/plugins/eidos and .opencode/skills/.",
        uninstall_clean_message="No OpenCode configuration found (already removed).",
        prune_dirs=(".opencode/plugins", ".opencode"),
    ),
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
                removed = _uninstall_from_manifest(target_path)
            results.append(
                UninstallResult(
                    target="custom",
                    destination=target_path,
                    success=True,
                    message=f"Removed custom plugin path: {target_path}"
                    if removed
                    else f"No plugin files found at: {target_path}",
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
    lookup_dest = dest_path
    if lookup_dest is None and target in TARGET_ADAPTERS and TARGET_ADAPTERS[target].plugin_subdir:
        lookup_dest = Path(TARGET_ADAPTERS[target].plugin_subdir)
    prev_version = get_installed_plugin_version(project_dir=project_dir, dest_path=lookup_dest)
    if prev_version is None and lookup_dest is not dest_path:
        prev_version = get_installed_plugin_version(project_dir=project_dir, dest_path=dest_path)
    bundled_version = get_bundled_plugin_version()
    results = install_plugin(target=target, project_dir=project_dir, dest_path=dest_path, force=True, launcher=launcher)
    return prev_version, bundled_version, results
