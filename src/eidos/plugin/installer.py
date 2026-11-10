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
from eidos.plugin.managed_block import BlockResult, BlockStatus, upsert_managed_block
from eidos.plugin.version import (
    get_bundled_plugin_version,
    get_installed_plugin_version,
)


class PluginInstallTarget(enum.StrEnum):
    # Every target also projects the shared Eidos block into the root AGENTS.md (DEC-PLUGIN-AGENTS-MD-MANAGED-BLOCK).
    ANTIGRAVITY = "antigravity"  # Configures .agents/skills + opt-in .agents/hooks.json
    CLAUDE = "claude"  # Claude Code skills (.claude/skills), opt-in Stop hook (.claude/settings.json), CLAUDE.md @AGENTS.md
    CURSOR = "cursor"  # Configures .agents/skills + opt-in .cursor/hooks.json (loop_limit: 1)
    AIDER = "aider"  # Configures .aider/skills and adds AGENTS.md to .aider.conf.yml read:
    CLINE = "cline"  # Configures .agents/skills + opt-in .clinerules/hooks/TaskComplete
    KILOCODE = "kilocode"  # Configures .agents/skills; reads root AGENTS.md natively
    OPEN_CODE = "open-code"  # Configures .agents/skills; reads root AGENTS.md natively
    CODEX = "codex"  # Configures .agents/skills + opt-in .codex/hooks.json
    COPILOT = "copilot"  # Configures .agents/skills + opt-in .github/hooks/hooks.json
    ALL = "all"  # Installs all agent skills and configurations


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
    """Find the canonical Eidos Agent Skills reference package."""
    # 1. Check assets/plugin_reference via asset locator
    try:
        ref_dir = get_plugin_reference_dir()
        if ref_dir.exists() and (ref_dir / "skills").is_dir():
            return ref_dir
    except OSError:
        pass

    # 2. Check repo root assets/plugin_reference
    local_assets = Path.cwd() / "assets" / "plugin_reference"
    if local_assets.exists() and (local_assets / "skills").is_dir():
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

# Shared, user-owned files that Eidos edits only through managed blocks or merges; they are never recorded in
# (or unlinked from) an installation manifest.
SHARED_USER_FILES: frozenset[str] = frozenset(
    {"AGENTS.md", "CLAUDE.md", ".aider.conf.yml", "settings.json", "hooks.json"}
)

AGENTS_MD_FILENAME = "AGENTS.md"
AGENTS_MD_BLOCK = "agents-md"
CLAUDE_MD_FILENAME = "CLAUDE.md"
CLAUDE_IMPORT_BLOCK = "claude-import"
AGENTS_MD_TEMPLATE_REL = Path("agents-md") / "block.md"
_CLAUDE_AGENTS_IMPORT_RE = re.compile(r"^[ \t]*@(?:\./)?AGENTS\.md[ \t]*\r?$", re.M)


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


def _read_manifest_targets(target_dir: Path) -> dict[str, dict[str, Any]]:
    """Read per-target installation metadata ('targets') from target_dir/.eidos-manifest.json."""
    manifest_file = target_dir / MANIFEST_FILENAME
    if not manifest_file.is_file():
        return {}
    try:
        data = orjson.loads(manifest_file.read_bytes())
        targets = data.get("targets") if isinstance(data, dict) else None
        if isinstance(targets, dict):
            return {
                str(k): dict(v)
                for k, v in targets.items()
                if isinstance(k, str) and isinstance(v, dict)
            }
    except (OSError, orjson.JSONDecodeError, ValueError):
        pass
    return {}


def _write_installation_manifest(
    target_dir: Path,
    created_files: list[Path] | None = None,
    target_name: str | None = None,
    hooks: bool = False,
    launcher: str = "eidos",
) -> Path:
    """Generate .eidos-manifest.json in target_dir storing relative file paths, SHA-256 digests, and target metadata."""
    _check_not_symlink(target_dir)
    manifest_file = target_dir / MANIFEST_FILENAME
    _check_not_symlink(manifest_file, root=target_dir)

    existing_files: dict[str, str] = {}
    existing_targets: dict[str, dict[str, Any]] = {}
    if manifest_file.exists():
        try:
            old_data = orjson.loads(manifest_file.read_bytes())
            if isinstance(old_data, dict):
                old_targets = old_data.get("targets")
                if isinstance(old_targets, dict):
                    for k, v in old_targets.items():
                        if isinstance(k, str) and isinstance(v, dict):
                            existing_targets[k] = dict(v)
                if created_files is not None:
                    old_files = old_data.get("files", {})
                    if isinstance(old_files, dict):
                        for rel_path, digest in old_files.items():
                            candidate = target_dir / rel_path
                            if candidate.is_file() and not candidate.is_symlink():
                                existing_files[rel_path] = str(digest)
        except (OSError, orjson.JSONDecodeError, ValueError):
            existing_files = {}
            existing_targets = {}

    if target_name is not None:
        existing_targets[target_name] = {
            "hooks": bool(hooks),
            "launcher": launcher,
        }

    manifest_data: dict[str, Any] = {
        "version": get_bundled_plugin_version(),
        "files": existing_files,
        "targets": dict(sorted(existing_targets.items())),
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
        if rel in SHARED_USER_FILES:
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
    """Safely remove Eidos Stop hook from project-level .claude/settings.json."""
    if not file_path.exists():
        return False
    try:
        data = orjson.loads(file_path.read_bytes())
        if not isinstance(data, dict):
            return False
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False

    changed = False
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


def _update_project_aider_conf(file_path: Path, conventions_path: str = AGENTS_MD_FILENAME) -> Path:
    """Ensure an instructions file (default: the root AGENTS.md) is listed under 'read' in .aider.conf.yml."""
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


def _is_eidos_copilot_hook_entry(entry: Any) -> bool:
    """Return True if a GitHub Copilot agentStop hook entry invokes 'eidos hook'."""
    if not isinstance(entry, dict):
        return False
    return any(_is_eidos_hook_command(entry.get(k)) for k in ("bash", "powershell", "command"))


def _update_project_cursor_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .cursor/hooks.json to register the Eidos stop hook with loop_limit: 1."""
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

    if not isinstance(data.get("version"), int):
        data["version"] = 1

    bundled_ver = get_bundled_plugin_version()
    hook_cmd = f"uvx --from eidos-ai=={bundled_ver} eidos hook" if launcher == "uvx" else "eidos hook"
    eidos_entry = {
        "command": hook_cmd,
        "loop_limit": 1,
    }

    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    stop_list = hooks_dict.get("stop")
    if not isinstance(stop_list, list):
        stop_list = []

    new_stop_list: list[Any] = []
    replaced = False
    for entry in stop_list:
        if isinstance(entry, dict) and _is_eidos_hook_command(entry.get("command")):
            if not replaced:
                new_stop_list.append({**entry, **eidos_entry})
                replaced = True
        else:
            new_stop_list.append(entry)

    if not replaced:
        new_stop_list.append(eidos_entry)

    hooks_dict["stop"] = new_stop_list
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return file_path


def _remove_project_cursor_hooks(file_path: Path) -> bool:
    """Safely remove Eidos stop hook from project-level .cursor/hooks.json."""
    if not file_path.exists():
        return False
    try:
        data = orjson.loads(file_path.read_bytes())
        if not isinstance(data, dict):
            return False
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False

    changed = False
    hooks_dict = data.get("hooks")
    if isinstance(hooks_dict, dict):
        stop_list = hooks_dict.get("stop")
        if isinstance(stop_list, list):
            new_stop_list = [
                entry
                for entry in stop_list
                if not (isinstance(entry, dict) and _is_eidos_hook_command(entry.get("command")))
            ]
            if len(new_stop_list) != len(stop_list):
                changed = True
            if new_stop_list:
                hooks_dict["stop"] = new_stop_list
            else:
                hooks_dict.pop("stop", None)
        if not hooks_dict:
            data.pop("hooks", None)

    if not changed:
        return False

    if not data or set(data.keys()) <= {"version"}:
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    else:
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return True


def _update_project_copilot_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .github/hooks/hooks.json to register the Eidos agentStop hook."""
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

    if not isinstance(data.get("version"), int):
        data["version"] = 1

    bundled_ver = get_bundled_plugin_version()
    hook_cmd = f"uvx --from eidos-ai=={bundled_ver} eidos hook" if launcher == "uvx" else "eidos hook"
    eidos_entry = {
        "type": "command",
        "bash": hook_cmd,
        "powershell": hook_cmd,
        "timeoutSec": 30,
    }

    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    stop_list = hooks_dict.get("agentStop")
    if not isinstance(stop_list, list):
        stop_list = []

    new_stop_list: list[Any] = []
    replaced = False
    for entry in stop_list:
        if _is_eidos_copilot_hook_entry(entry) and isinstance(entry, dict):
            if not replaced:
                new_stop_list.append({**entry, **eidos_entry})
                replaced = True
        else:
            new_stop_list.append(entry)

    if not replaced:
        new_stop_list.append(eidos_entry)

    hooks_dict["agentStop"] = new_stop_list
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return file_path


def _remove_project_copilot_hooks(file_path: Path) -> bool:
    """Safely remove Eidos agentStop hook from project-level .github/hooks/hooks.json."""
    if not file_path.exists():
        return False
    try:
        data = orjson.loads(file_path.read_bytes())
        if not isinstance(data, dict):
            return False
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False

    changed = False
    hooks_dict = data.get("hooks")
    if isinstance(hooks_dict, dict):
        stop_list = hooks_dict.get("agentStop")
        if isinstance(stop_list, list):
            new_stop_list = [entry for entry in stop_list if not _is_eidos_copilot_hook_entry(entry)]
            if len(new_stop_list) != len(stop_list):
                changed = True
            if new_stop_list:
                hooks_dict["agentStop"] = new_stop_list
            else:
                hooks_dict.pop("agentStop", None)
        if not hooks_dict:
            data.pop("hooks", None)

    if not changed:
        return False

    if not data or set(data.keys()) <= {"version"}:
        file_path.unlink()
        for parent in (file_path.parent, file_path.parent.parent):
            if parent.exists() and not any(parent.iterdir()):
                with contextlib.suppress(OSError):
                    parent.rmdir()
    else:
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return True


def _update_project_codex_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .codex/hooks.json to register the Eidos Stop hook."""
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


def _remove_project_codex_hooks(file_path: Path) -> bool:
    """Safely remove Eidos Stop hook from project-level .codex/hooks.json."""
    if not file_path.exists():
        return False
    try:
        data = orjson.loads(file_path.read_bytes())
        if not isinstance(data, dict):
            return False
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False

    changed = False
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


def _is_eidos_cline_hook_script(text: str) -> bool:
    """Return True if a Cline hook script invokes 'eidos hook' on any non-comment line."""
    return any(
        _is_eidos_hook_command(line.strip())
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _update_project_cline_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely write or update project-level .clinerules/hooks/TaskComplete executable script for Cline."""
    _check_not_symlink(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    bundled_ver = get_bundled_plugin_version()
    hook_cmd = f"uvx --from eidos-ai=={bundled_ver} eidos hook" if launcher == "uvx" else "eidos hook"
    script = f"#!/usr/bin/env sh\n# Managed by Eidos (eidos-drift-gate)\nexec {hook_cmd}\n"
    file_path.write_text(script, encoding="utf-8")
    with contextlib.suppress(OSError):
        file_path.chmod(0o755)
    return file_path


def _remove_project_cline_hooks(file_path: Path) -> bool:
    """Safely remove Eidos TaskComplete hook script from project-level .clinerules/hooks/TaskComplete."""
    if not file_path.exists() or file_path.is_symlink():
        return False
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not _is_eidos_cline_hook_script(text) and "eidos-drift-gate" not in text:
        return False
    file_path.unlink()
    for parent in (file_path.parent, file_path.parent.parent):
        if parent.exists() and not any(parent.iterdir()):
            with contextlib.suppress(OSError):
                parent.rmdir()
    return True


def _update_project_antigravity_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .agents/hooks.json to register the Eidos Stop hook for Antigravity."""
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

    bundled_ver = get_bundled_plugin_version()
    hook_cmd = f"uvx --from eidos-ai=={bundled_ver} eidos hook" if launcher == "uvx" else "eidos hook"
    data["eidos-drift-gate"] = {
        "Stop": [
            {
                "type": "command",
                "command": hook_cmd,
                "timeout": 30,
            }
        ]
    }
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return file_path


def _remove_project_antigravity_hooks(file_path: Path) -> bool:
    """Safely remove Eidos Stop hook from project-level .agents/hooks.json."""
    if not file_path.exists() or file_path.is_symlink():
        return False
    try:
        data = orjson.loads(file_path.read_bytes())
        if not isinstance(data, dict):
            return False
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False

    changed = False
    if "eidos-drift-gate" in data:
        data.pop("eidos-drift-gate", None)
        changed = True

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


TARGET_HOOK_PATHS: dict[str, str] = {
    "antigravity": ".agents/hooks.json",
    "claude": ".claude/settings.json",
    "cursor": ".cursor/hooks.json",
    "cline": ".clinerules/hooks/TaskComplete",
    "copilot": ".github/hooks/hooks.json",
    "codex": ".codex/hooks.json",
}


def check_target_hook_health(root: Path, target_name: str, hooks_expected: bool) -> dict[str, Any]:
    """Verify whether a target's native Stop hook matches its installed hook expectation."""
    hook_rel = TARGET_HOOK_PATHS.get(target_name, ".agents/hooks.json")
    if not hooks_expected:
        return {
            "ok": True,
            "expected": False,
            "status": "disabled",
            "hook_file": hook_rel,
            "detail": f"{target_name}: Stop hook intentionally disabled (installed without --hooks)",
        }

    hook_path = root / hook_rel
    if not hook_path.is_file():
        return {
            "ok": False,
            "expected": True,
            "status": "missing",
            "hook_file": hook_rel,
            "detail": f"{target_name}: Stop hook file '{hook_rel}' is missing (originally installed with --hooks)",
        }

    if target_name == "cline":
        try:
            text = hook_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {
                "ok": False,
                "expected": True,
                "status": "invalid",
                "hook_file": hook_rel,
                "detail": f"{target_name}: Stop hook file '{hook_rel}' cannot be read",
            }
        is_exec = os.name == "nt" or os.access(hook_path, os.X_OK)
        if not _is_eidos_cline_hook_script(text) or not is_exec:
            return {
                "ok": False,
                "expected": True,
                "status": "corrupted",
                "hook_file": hook_rel,
                "detail": f"{target_name}: Eidos Stop hook missing, non-executable, or misconfigured in '{hook_rel}'",
            }
        return {
            "ok": True,
            "expected": True,
            "status": "active",
            "hook_file": hook_rel,
            "detail": f"{target_name}: Stop hook active in '{hook_rel}'",
        }

    try:
        data = orjson.loads(hook_path.read_bytes())
        if not isinstance(data, dict):
            raise ValueError("root is not an object")
    except (OSError, orjson.JSONDecodeError, ValueError):
        return {
            "ok": False,
            "expected": True,
            "status": "invalid",
            "hook_file": hook_rel,
            "detail": f"{target_name}: Stop hook file '{hook_rel}' contains invalid JSON",
        }

    valid_entry = False
    if target_name == "antigravity":
        gate = data.get("eidos-drift-gate")
        stop_list = gate.get("Stop") if isinstance(gate, dict) else None
        if isinstance(stop_list, list):
            valid_entry = any(isinstance(h, dict) and _is_eidos_hook_command(h.get("command")) for h in stop_list)
    elif target_name in ("claude", "codex"):
        hooks_dict = data.get("hooks")
        stop_list = hooks_dict.get("Stop") if isinstance(hooks_dict, dict) else None
        if isinstance(stop_list, list):
            valid_entry = any(_is_eidos_stop_hook_entry(entry) for entry in stop_list)
    elif target_name == "cursor":
        hooks_dict = data.get("hooks")
        stop_list = hooks_dict.get("stop") if isinstance(hooks_dict, dict) else None
        if data.get("version") == 1 and isinstance(stop_list, list):
            valid_entry = any(isinstance(h, dict) and _is_eidos_hook_command(h.get("command")) for h in stop_list)
    elif target_name == "copilot":
        hooks_dict = data.get("hooks")
        stop_list = hooks_dict.get("agentStop") if isinstance(hooks_dict, dict) else None
        if data.get("version") == 1 and isinstance(stop_list, list):
            valid_entry = any(_is_eidos_copilot_hook_entry(entry) for entry in stop_list)

    if not valid_entry:
        return {
            "ok": False,
            "expected": True,
            "status": "corrupted",
            "hook_file": hook_rel,
            "detail": f"{target_name}: Eidos Stop hook missing or misconfigured in '{hook_rel}'",
        }

    return {
        "ok": True,
        "expected": True,
        "status": "active",
        "hook_file": hook_rel,
        "detail": f"{target_name}: Stop hook active in '{hook_rel}'",
    }


def _uninstall_from_manifest(target_dir: Path, target_name: str | None = None) -> bool:
    """Remove manifest-tracked files without wiping shared parent (or remove one target from a shared multi-target manifest)."""
    if not target_dir.exists():
        return False

    manifest_file = target_dir / MANIFEST_FILENAME
    if not manifest_file.exists():
        return False

    removed_any = False
    try:
        data = orjson.loads(manifest_file.read_bytes())
        files_entry = data.get("files", {}) if isinstance(data, dict) else {}
        targets_entry = data.get("targets", {}) if isinstance(data, dict) else {}
    except (OSError, orjson.JSONDecodeError, ValueError):
        data = {}
        files_entry = {}
        targets_entry = {}

    if target_name and isinstance(targets_entry, dict) and target_name in targets_entry:
        targets_entry.pop(target_name, None)
        removed_any = True
        if targets_entry:
            data["targets"] = dict(sorted(targets_entry.items()))
            if isinstance(files_entry, dict):
                data["files"] = files_entry
            with contextlib.suppress(OSError):
                manifest_file.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
            return True

    rel_paths = (
        list(files_entry.keys())
        if isinstance(files_entry, dict)
        else ([str(p) for p in files_entry] if isinstance(files_entry, list) else [])
    )
    for rel_path in sorted(rel_paths, reverse=True):
        if rel_path in SHARED_USER_FILES:
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


def _antigravity_configure_hooks(root: Path, launcher: str, hooks: bool) -> list[Path]:
    antigravity_hooks = root / ".agents" / "hooks.json"
    if hooks:
        _update_project_antigravity_hooks(antigravity_hooks, launcher=launcher)
        return [antigravity_hooks]
    _remove_project_antigravity_hooks(antigravity_hooks)
    return []


def _claude_configure_hooks(root: Path, launcher: str, hooks: bool) -> list[Path]:
    claude_settings = root / ".claude" / "settings.json"
    if hooks:
        _update_project_claude_settings(claude_settings, launcher=launcher)
        return [claude_settings]
    _remove_project_claude_settings(claude_settings)
    return []


def _claude_extra_uninstall(root: Path) -> bool:
    return _remove_project_claude_settings(root / ".claude" / "settings.json")


def _cursor_configure_hooks(root: Path, launcher: str, hooks: bool) -> list[Path]:
    cursor_hooks = root / ".cursor" / "hooks.json"
    if hooks:
        _update_project_cursor_hooks(cursor_hooks, launcher=launcher)
        return [cursor_hooks]
    _remove_project_cursor_hooks(cursor_hooks)
    return []


def _copilot_configure_hooks(root: Path, launcher: str, hooks: bool) -> list[Path]:
    copilot_hooks = root / ".github" / "hooks" / "hooks.json"
    if hooks:
        _update_project_copilot_hooks(copilot_hooks, launcher=launcher)
        return [copilot_hooks]
    _remove_project_copilot_hooks(copilot_hooks)
    return []


def _codex_configure_hooks(root: Path, launcher: str, hooks: bool) -> list[Path]:
    codex_hooks = root / ".codex" / "hooks.json"
    if hooks:
        _update_project_codex_hooks(codex_hooks, launcher=launcher)
        return [codex_hooks]
    _remove_project_codex_hooks(codex_hooks)
    return []


def _cline_configure_hooks(root: Path, launcher: str, hooks: bool) -> list[Path]:
    cline_hook = root / ".clinerules" / "hooks" / "TaskComplete"
    if hooks:
        _update_project_cline_hooks(cline_hook, launcher=launcher)
        return [cline_hook]
    _remove_project_cline_hooks(cline_hook)
    return []


# ---------------------------------------------------------------------------
# Canonical AGENTS.md managed block (DEC-PLUGIN-AGENTS-MD-MANAGED-BLOCK)
# ---------------------------------------------------------------------------


def _load_agents_md_template(source_dir: Path) -> str | None:
    """Load the canonical AGENTS.md block template bundled with the plugin reference (None if absent)."""
    template = source_dir / AGENTS_MD_TEMPLATE_REL
    try:
        return template.read_text(encoding="utf-8")
    except OSError:
        return None


def render_agents_md_block(template: str, targets: dict[str, str]) -> str:
    """Render the AGENTS.md block body, listing each unique recorded skill location (in sorted target order)."""
    if targets:
        unique_dirs = dict.fromkeys(skills_dir for _t, skills_dir in sorted(targets.items()))
        locations = "\n".join(f"- `{skills_dir}/<skill>/SKILL.md`" for skills_dir in unique_dirs)
    else:
        locations = "- (no skill locations recorded)"
    return template.replace("{{skill_locations}}", locations)


def _is_target_still_installed(root: Path, target_name: str, skills_dir: str) -> bool:
    """Return True if target_name still has its installed skills on disk and in any shared manifest."""
    if not (root / skills_dir / "eidos" / "SKILL.md").is_file():
        return False
    if skills_dir == ".agents/skills":
        manifest_targets = _read_manifest_targets(root / ".agents")
        if manifest_targets:
            return target_name in manifest_targets
    return True


def project_agents_md_block(
    root: Path, source_dir: Path, target_name: str, skills_dir: str, force: bool = False
) -> BlockResult | None:
    """Upsert the shared 'agents-md' block into the root AGENTS.md for one install target."""
    template = _load_agents_md_template(source_dir)
    if template is None:
        return None
    agents_md = root / AGENTS_MD_FILENAME
    try:
        return upsert_managed_block(
            agents_md,
            AGENTS_MD_BLOCK,
            lambda targets: render_agents_md_block(template, dict(targets)),
            target=target_name,
            skills_dir=skills_dir,
            keep_target=lambda t, d: _is_target_still_installed(root, t, d),
            force=force,
            root=root,
        )
    except FileExistsError as exc:  # symlinked AGENTS.md (or parent): never write through it, never abort install
        return BlockResult(BlockStatus.SKIPPED, agents_md, detail=str(exc))


def project_claude_import_block(root: Path, force: bool = False) -> BlockResult | None:
    """Upsert a one-line '@AGENTS.md' import block into the root CLAUDE.md (skipped if the user already imports it)."""
    claude_md = root / CLAUDE_MD_FILENAME
    if claude_md.is_symlink():
        # A common layout symlinks CLAUDE.md to AGENTS.md: Claude Code then already loads the AGENTS.md block.
        with contextlib.suppress(OSError):
            if claude_md.resolve() == (root / AGENTS_MD_FILENAME).resolve():
                return None
        return BlockResult(
            BlockStatus.SKIPPED, claude_md, detail="CLAUDE.md is a symlink; refusing to write through it"
        )
    if claude_md.is_file():
        text = claude_md.read_text(encoding="utf-8", errors="replace")
        if CLAUDE_IMPORT_BLOCK not in text and _CLAUDE_AGENTS_IMPORT_RE.search(text):
            return None
    try:
        return upsert_managed_block(claude_md, CLAUDE_IMPORT_BLOCK, "@AGENTS.md\n", force=force, root=root)
    except FileExistsError as exc:
        return BlockResult(BlockStatus.SKIPPED, claude_md, detail=str(exc))


def _block_warning(label: str, result: BlockResult | None) -> str:
    if result is None or result.status.written or result.status is BlockStatus.OPTED_OUT:
        return ""
    return f" Warning: {label} block not updated ({result.status.value}: {result.detail})."


@dataclass(frozen=True)
class TargetAdapter:
    """Declarative table-driven adapter encapsulating target-specific skills installation and uninstallation."""

    target: PluginInstallTarget
    name: str
    plugin_subdir: str | None = None
    skills_subdir: str | None = None
    install_destination_subdir: str | None = None
    uninstall_destination_subdir: str | None = None
    install_message: str = ""
    uninstall_removed_message: str = ""
    uninstall_clean_message: str = ""
    prune_dirs: tuple[str, ...] = ()
    post_install_hook: Callable[[Path, Path, bool, str], list[Path]] | None = None
    configure_hooks: Callable[[Path, str, bool], list[Path]] | None = None
    extra_uninstall_hook: Callable[[Path], bool] | None = None
    claude_import: bool = False

    def skills_dir(self) -> str | None:
        """Return the repository-relative directory where this target installs the Eidos skills."""
        if self.skills_subdir is not None:
            return self.skills_subdir
        if self.plugin_subdir is not None:
            return f"{self.plugin_subdir}/skills"
        return None

    def install(
        self,
        root: Path,
        source_dir: Path,
        force: bool = False,
        launcher: str = "eidos",
        force_blocks: bool | None = None,
        hooks: bool = False,
    ) -> InstallResult:
        """Execute declarative installation pipeline for this target.

        ``force_blocks`` controls whether hand-edited managed blocks (AGENTS.md / CLAUDE.md) may be overwritten;
        it defaults to ``force`` but ``update_plugin`` always passes False.
        ``hooks`` (default False) controls whether the target's native Stop hook is installed.
        """
        overwrite_blocks = force if force_blocks is None else force_blocks
        supports_hooks = self.configure_hooks is not None
        effective_hooks = bool(hooks and supports_hooks)
        created: list[Path] = []
        plugin_dir = (root / self.plugin_subdir) if self.plugin_subdir else None

        if plugin_dir is not None:
            _check_not_symlink(plugin_dir, root=root)

        if self.post_install_hook is not None:
            created.extend(self.post_install_hook(root, source_dir, force, launcher))

        if self.configure_hooks is not None:
            created.extend(self.configure_hooks(root, launcher, effective_hooks))

        if self.skills_subdir is not None:
            created.extend(_copy_skills_flat(source_dir, root / self.skills_subdir, force=force))

        if plugin_dir is not None:
            manifest = _write_installation_manifest(
                plugin_dir,
                created,
                target_name=self.name,
                hooks=effective_hooks,
                launcher=launcher,
            )
            created.append(manifest)

        message = self.install_message
        skills_dir = self.skills_dir()
        if skills_dir is not None:
            agents_result = project_agents_md_block(root, source_dir, self.name, skills_dir, force=overwrite_blocks)
            if agents_result is not None and agents_result.status.written:
                created.append(agents_result.path)
            message += _block_warning("AGENTS.md", agents_result)
        if self.claude_import:
            claude_result = project_claude_import_block(root, force=overwrite_blocks)
            if claude_result is not None and claude_result.status.written:
                created.append(claude_result.path)
            message += _block_warning("CLAUDE.md", claude_result)

        dest_rel = self.install_destination_subdir or self.plugin_subdir or ""
        destination = (root / dest_rel) if dest_rel else root
        return InstallResult(
            target=self.name,
            destination=destination,
            success=True,
            message=message,
            created_files=created,
        )

    def uninstall(self, root: Path) -> UninstallResult:
        """Execute declarative uninstallation pipeline for this target."""
        was_removed = False
        plugin_dir = (root / self.plugin_subdir) if self.plugin_subdir else None

        if plugin_dir is not None and plugin_dir.exists() and _uninstall_from_manifest(plugin_dir, target_name=self.name):
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


def _make_agents_skills_adapter(
    target: PluginInstallTarget,
    name: str,
    label: str,
    configure_hooks: Callable[[Path, str, bool], list[Path]] | None = None,
    extra_uninstall_hook: Callable[[Path], bool] | None = None,
) -> TargetAdapter:
    return TargetAdapter(
        target=target,
        name=name,
        plugin_subdir=".agents",
        skills_subdir=".agents/skills",
        install_destination_subdir=".agents/skills",
        uninstall_destination_subdir=".agents/skills",
        install_message=f"Installed 4 skills in .agents/skills/ and the root AGENTS.md block (read natively by {label}).",
        uninstall_removed_message="Removed .agents/skills/.",
        uninstall_clean_message=f"No {label} configuration found (already removed).",
        prune_dirs=(".agents/skills", ".agents"),
        configure_hooks=configure_hooks,
        extra_uninstall_hook=extra_uninstall_hook,
    )


TARGET_ADAPTERS: dict[PluginInstallTarget, TargetAdapter] = {
    PluginInstallTarget.ANTIGRAVITY: _make_agents_skills_adapter(
        PluginInstallTarget.ANTIGRAVITY,
        "antigravity",
        "Antigravity",
        configure_hooks=_antigravity_configure_hooks,
        extra_uninstall_hook=lambda root: _remove_project_antigravity_hooks(root / ".agents" / "hooks.json"),
    ),
    PluginInstallTarget.CURSOR: _make_agents_skills_adapter(
        PluginInstallTarget.CURSOR,
        "cursor",
        "Cursor",
        configure_hooks=_cursor_configure_hooks,
        extra_uninstall_hook=lambda root: _remove_project_cursor_hooks(root / ".cursor" / "hooks.json"),
    ),
    PluginInstallTarget.CLAUDE: TargetAdapter(
        target=PluginInstallTarget.CLAUDE,
        name="claude",
        plugin_subdir=".claude",
        skills_subdir=".claude/skills",
        install_message="Installed 4 Claude Code skills in .claude/skills/ and imported the root AGENTS.md block from CLAUDE.md.",
        uninstall_removed_message="Removed Claude Code skills from .claude/skills/ and cleaned up .claude/settings.json.",
        uninstall_clean_message="No Claude Code configuration found (already removed).",
        prune_dirs=(".claude/skills", ".claude"),
        configure_hooks=_claude_configure_hooks,
        extra_uninstall_hook=_claude_extra_uninstall,
        claude_import=True,
    ),
    PluginInstallTarget.AIDER: TargetAdapter(
        target=PluginInstallTarget.AIDER,
        name="aider",
        plugin_subdir=".aider",
        skills_subdir=".aider/skills",
        install_destination_subdir=".aider.conf.yml",
        uninstall_destination_subdir=".aider.conf.yml",
        install_message="Installed 4 skills into .aider/skills/, the root AGENTS.md block, and AGENTS.md under read: in .aider.conf.yml.",
        uninstall_removed_message="Removed .aider/skills/.",
        uninstall_clean_message="No Aider configuration found (already removed).",
        prune_dirs=(".aider/skills", ".aider"),
        post_install_hook=lambda root, _src, _force, _l: [
            _update_project_aider_conf(root / ".aider.conf.yml", AGENTS_MD_FILENAME),
        ],
    ),
    PluginInstallTarget.CLINE: _make_agents_skills_adapter(
        PluginInstallTarget.CLINE,
        "cline",
        "Cline",
        configure_hooks=_cline_configure_hooks,
        extra_uninstall_hook=lambda root: _remove_project_cline_hooks(root / ".clinerules" / "hooks" / "TaskComplete"),
    ),
    PluginInstallTarget.KILOCODE: _make_agents_skills_adapter(PluginInstallTarget.KILOCODE, "kilocode", "Kilo Code"),
    PluginInstallTarget.OPEN_CODE: _make_agents_skills_adapter(PluginInstallTarget.OPEN_CODE, "open-code", "OpenCode"),
    PluginInstallTarget.CODEX: _make_agents_skills_adapter(
        PluginInstallTarget.CODEX,
        "codex",
        "OpenAI Codex",
        configure_hooks=_codex_configure_hooks,
        extra_uninstall_hook=lambda root: _remove_project_codex_hooks(root / ".codex" / "hooks.json"),
    ),
    PluginInstallTarget.COPILOT: _make_agents_skills_adapter(
        PluginInstallTarget.COPILOT,
        "copilot",
        "GitHub Copilot",
        configure_hooks=_copilot_configure_hooks,
        extra_uninstall_hook=lambda root: _remove_project_copilot_hooks(root / ".github" / "hooks" / "hooks.json"),
    ),
}


def _resolve_plugin_project_root(project_dir: Path | None) -> Path:
    """Resolve the target project root for plugin operations (redirecting monorepo sub-projects to git_root)."""
    from eidos.core.workspace.project import _has_eidos_markers, find_git_root, find_repo_root

    base_dir = (project_dir or Path.cwd()).resolve()
    git_root = find_git_root(base_dir)
    if project_dir is None:
        return git_root or base_dir
    if git_root is not None and git_root != base_dir:
        sub_root = find_repo_root(base_dir)
        if sub_root != git_root and _has_eidos_markers(sub_root / "eidos"):
            return git_root
    return base_dir


def _all_concrete_targets() -> list[PluginInstallTarget]:
    return [
        PluginInstallTarget.ANTIGRAVITY,
        PluginInstallTarget.CURSOR,
        PluginInstallTarget.CLAUDE,
        PluginInstallTarget.AIDER,
        PluginInstallTarget.CLINE,
        PluginInstallTarget.KILOCODE,
        PluginInstallTarget.OPEN_CODE,
        PluginInstallTarget.CODEX,
        PluginInstallTarget.COPILOT,
    ]


def install_plugin(
    target: PluginInstallTarget,
    project_dir: Path | None = None,
    force: bool = False,
    launcher: str = "eidos",
    force_blocks: bool | None = None,
    hooks: bool = False,
) -> list[InstallResult]:
    """Install the Eidos agent plugin strictly inside project scope (at the enclosing Git root).

    ``force_blocks`` (default: ``force``) controls overwriting hand-edited AGENTS.md / CLAUDE.md managed blocks.
    ``hooks`` (default: ``False``) controls whether the target's native Stop hook is installed.
    """
    source_dir = locate_plugin_bundle()
    root = _resolve_plugin_project_root(project_dir)
    overwrite_blocks = force if force_blocks is None else force_blocks
    results = []

    targets_to_run = _all_concrete_targets() if target == PluginInstallTarget.ALL else [target]

    for t in targets_to_run:
        adapter = TARGET_ADAPTERS.get(t)
        if adapter is not None:
            results.append(
                adapter.install(root, source_dir, force, launcher, force_blocks=overwrite_blocks, hooks=hooks)
            )

    return results


def uninstall_plugin(
    target: PluginInstallTarget,
    project_dir: Path | None = None,
) -> list[UninstallResult]:
    """Uninstall / remove the Eidos agent plugin strictly from project scope (at the enclosing Git root)."""
    root = _resolve_plugin_project_root(project_dir)
    results = []

    targets_to_run = _all_concrete_targets() if target == PluginInstallTarget.ALL else [target]

    for t in targets_to_run:
        adapter = TARGET_ADAPTERS.get(t)
        if adapter is not None:
            results.append(adapter.uninstall(root))

    return results


def _resolve_saved_hook_preference(root: Path, target_name: str) -> bool:
    """Resolve whether a target was previously installed with hooks=True in .eidos-manifest.json."""
    if target_name not in TARGET_HOOK_PATHS:
        return False
    manifest_dir = (root / ".claude") if target_name == "claude" else (root / ".agents")
    targets_meta = _read_manifest_targets(manifest_dir)
    if target_name in targets_meta and "hooks" in targets_meta[target_name]:
        return bool(targets_meta[target_name]["hooks"])
    return bool(check_target_hook_health(root, target_name, hooks_expected=True)["ok"])


def update_plugin(
    target: PluginInstallTarget,
    project_dir: Path | None = None,
    launcher: str = "eidos",
) -> tuple[str | None, str, list[InstallResult]]:
    """Update the installed Eidos agent plugin to the latest bundled version, preserving each target's hook state.

    Returns:
        (previous_version, current_version, install_results)
    """
    prev_version = get_installed_plugin_version(project_dir=project_dir)
    bundled_version = get_bundled_plugin_version()
    root = _resolve_plugin_project_root(project_dir)
    source_dir = locate_plugin_bundle()
    targets_to_run = _all_concrete_targets() if target == PluginInstallTarget.ALL else [target]
    results: list[InstallResult] = []
    for t in targets_to_run:
        adapter = TARGET_ADAPTERS.get(t)
        if adapter is not None:
            effective_hooks = _resolve_saved_hook_preference(root, adapter.name)
            results.append(
                adapter.install(
                    root,
                    source_dir,
                    force=True,
                    launcher=launcher,
                    force_blocks=False,
                    hooks=effective_hooks,
                )
            )
    return prev_version, bundled_version, results
