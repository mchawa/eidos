"""Native multi-harness lifecycle hook adapters and health verification (CMP-HOOK-MANAGER)."""

import contextlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import orjson

MANIFEST_FILENAME = ".eidos-manifest.json"


def _resolve_hook_command(launcher: str, bundled_version: str | None = None) -> str:
    """Resolve the CLI-native hook command for direct or pinned uvx execution."""
    if launcher != "uvx":
        return "eidos hook"
    raw_ver = bundled_version
    if not raw_ver:
        import importlib

        raw_ver = importlib.import_module("eidos.plugin.version").get_cli_release_semver()
    m = re.match(r"^(\d+\.\d+\.\d+)", str(raw_ver or "0.0.0").strip())
    ver = m.group(1) if m else "0.0.0"
    return f"uvx --from eidos-ai=={ver} eidos hook"


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


def _upsert_nested_command_hook(hooks_dict: dict[str, Any], event_name: str, eidos_sub_hook: dict[str, Any]) -> None:
    """Upsert an Eidos command sub-hook inside a Claude/Codex `hooks[event_name]` matcher list."""
    ev_list = hooks_dict.get(event_name)
    if not isinstance(ev_list, list):
        ev_list = []

    new_list: list[Any] = []
    replaced = False
    for entry in ev_list:
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
                new_list.append({**entry, "hooks": new_sub_hooks})
        else:
            new_list.append(entry)

    if not replaced:
        new_list.append({"hooks": [eidos_sub_hook]})
    hooks_dict[event_name] = new_list


def _remove_nested_command_hook(hooks_dict: dict[str, Any], event_name: str) -> bool:
    """Remove Eidos command sub-hooks from a Claude/Codex `hooks[event_name]` matcher list."""
    ev_list = hooks_dict.get(event_name)
    if not isinstance(ev_list, list):
        return False

    changed = False
    new_list: list[Any] = []
    for entry in ev_list:
        if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            kept_sub = [
                h for h in entry["hooks"] if not (isinstance(h, dict) and _is_eidos_hook_command(h.get("command")))
            ]
            if len(kept_sub) != len(entry["hooks"]):
                changed = True
            if kept_sub:
                new_list.append({**entry, "hooks": kept_sub})
        elif _is_eidos_stop_hook_entry(entry):
            changed = True
        else:
            new_list.append(entry)

    if new_list:
        hooks_dict[event_name] = new_list
    else:
        hooks_dict.pop(event_name, None)
        if ev_list:
            changed = True
    return changed


def _update_project_claude_settings(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .claude/settings.json to register Eidos SessionStart and Stop hooks."""
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

    hook_cmd = _resolve_hook_command(launcher)
    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    _upsert_nested_command_hook(hooks_dict, "SessionStart", {"type": "command", "command": hook_cmd, "timeout": 10})
    _upsert_nested_command_hook(hooks_dict, "Stop", {"type": "command", "command": hook_cmd, "timeout": 30})
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return file_path


def _remove_project_claude_settings(file_path: Path) -> bool:
    """Safely remove Eidos SessionStart and Stop hooks from project-level .claude/settings.json."""
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
        for ev_name in ("SessionStart", "UserPromptSubmit", "Stop"):
            if _remove_nested_command_hook(hooks_dict, ev_name):
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
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return True


def _is_eidos_cursor_hook_entry(entry: Any) -> bool:
    """Return True if a Cursor hook entry invokes 'eidos hook'."""
    return isinstance(entry, dict) and _is_eidos_hook_command(entry.get("command"))


def _is_eidos_copilot_hook_entry(entry: Any) -> bool:
    """Return True if a GitHub Copilot hook entry invokes 'eidos hook'."""
    if not isinstance(entry, dict):
        return False
    return any(_is_eidos_hook_command(entry.get(k)) for k in ("bash", "powershell", "command"))


def _upsert_flat_command_hook(
    hooks_dict: dict[str, Any],
    event_name: str,
    eidos_entry: dict[str, Any],
    match_fn: Callable[[Any], bool],
) -> None:
    """Upsert an Eidos hook entry in a flat event array (Cursor or GitHub Copilot)."""
    ev_list = hooks_dict.get(event_name)
    if not isinstance(ev_list, list):
        ev_list = []

    new_list: list[Any] = []
    replaced = False
    for entry in ev_list:
        if match_fn(entry) and isinstance(entry, dict):
            if not replaced:
                new_list.append({**entry, **eidos_entry})
                replaced = True
        else:
            new_list.append(entry)
    if not replaced:
        new_list.append(eidos_entry)
    hooks_dict[event_name] = new_list


def _remove_flat_command_hook(
    hooks_dict: dict[str, Any],
    event_name: str,
    match_fn: Callable[[Any], bool],
) -> bool:
    """Remove Eidos hook entries from a flat event array (Cursor or GitHub Copilot)."""
    ev_list = hooks_dict.get(event_name)
    if not isinstance(ev_list, list):
        return False
    new_list = [entry for entry in ev_list if not match_fn(entry)]
    changed = len(new_list) != len(ev_list)
    if new_list:
        hooks_dict[event_name] = new_list
    else:
        hooks_dict.pop(event_name, None)
    return changed


def _update_project_cursor_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .cursor/hooks.json to register Eidos sessionStart and stop hooks."""
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

    hook_cmd = _resolve_hook_command(launcher)
    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    _upsert_flat_command_hook(hooks_dict, "sessionStart", {"command": hook_cmd}, _is_eidos_cursor_hook_entry)
    _upsert_flat_command_hook(hooks_dict, "stop", {"command": hook_cmd, "loop_limit": 1}, _is_eidos_cursor_hook_entry)
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return file_path


def _remove_project_cursor_hooks(file_path: Path) -> bool:
    """Safely remove Eidos hooks from project-level .cursor/hooks.json."""
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
        for ev_name in ("sessionStart", "beforeSubmitPrompt", "stop"):
            if _remove_flat_command_hook(hooks_dict, ev_name, _is_eidos_cursor_hook_entry):
                changed = True
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
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return True


def _update_project_copilot_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .github/hooks/hooks.json to register Eidos sessionStart and agentStop hooks."""
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

    hook_cmd = _resolve_hook_command(launcher)
    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    _upsert_flat_command_hook(
        hooks_dict,
        "sessionStart",
        {"type": "command", "bash": hook_cmd, "powershell": hook_cmd, "timeoutSec": 10},
        _is_eidos_copilot_hook_entry,
    )
    _upsert_flat_command_hook(
        hooks_dict,
        "agentStop",
        {"type": "command", "bash": hook_cmd, "powershell": hook_cmd, "timeoutSec": 30},
        _is_eidos_copilot_hook_entry,
    )
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return file_path


def _remove_project_copilot_hooks(file_path: Path) -> bool:
    """Safely remove Eidos hooks from project-level .github/hooks/hooks.json."""
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
        for ev_name in ("sessionStart", "userPromptSubmitted", "agentStop"):
            if _remove_flat_command_hook(hooks_dict, ev_name, _is_eidos_copilot_hook_entry):
                changed = True
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
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return True


def _update_project_codex_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely update project-level .codex/hooks.json to register Eidos SessionStart and Stop hooks."""
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

    hook_cmd = _resolve_hook_command(launcher)
    if not isinstance(data.get("hooks"), dict):
        data["hooks"] = {}
    hooks_dict: dict[str, Any] = data["hooks"]

    _upsert_nested_command_hook(hooks_dict, "SessionStart", {"type": "command", "command": hook_cmd, "timeout": 10})
    _upsert_nested_command_hook(hooks_dict, "Stop", {"type": "command", "command": hook_cmd, "timeout": 30})
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return file_path


def _remove_project_codex_hooks(file_path: Path) -> bool:
    """Safely remove Eidos SessionStart and Stop hooks from project-level .codex/hooks.json."""
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
        for ev_name in ("SessionStart", "UserPromptSubmit", "Stop"):
            if _remove_nested_command_hook(hooks_dict, ev_name):
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
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return True


def _is_eidos_cline_hook_script(text: str) -> bool:
    """Return True if a Cline hook script invokes 'eidos hook' on any non-comment line."""
    return any(
        _is_eidos_hook_command(line.strip())
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _update_project_cline_hooks(file_path: Path, launcher: str = "eidos") -> Path:
    """Safely write or update project-level .clinerules/hooks/PreToolUse (attempt_completion) executable script."""
    _check_not_symlink(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cmd = _resolve_hook_command(launcher)
    script = f"#!/usr/bin/env sh\n# Managed by Eidos (eidos-drift-gate / attempt_completion)\nexec {hook_cmd}\n"
    file_path.write_text(script, encoding="utf-8")
    with contextlib.suppress(OSError):
        file_path.chmod(0o755)
    return file_path


def _remove_project_cline_hooks(file_path: Path) -> bool:
    """Safely remove an Eidos-managed Cline hook script from .clinerules/hooks/."""
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
    """Safely update project-level .agents/hooks.json to register Eidos SessionStart and Stop hooks."""
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

    hook_cmd = _resolve_hook_command(launcher)
    data["eidos-drift-gate"] = {
        "SessionStart": [
            {
                "type": "command",
                "command": hook_cmd,
                "timeout": 10,
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": hook_cmd,
                "timeout": 30,
            }
        ],
    }
    file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
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
        file_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
    return True


TARGET_HOOK_PATHS: dict[str, str] = {
    "antigravity": ".agents/hooks.json",
    "claude": ".claude/settings.json",
    "cursor": ".cursor/hooks.json",
    "cline": ".clinerules/hooks/PreToolUse",
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
        task_start_rel = ".clinerules/hooks/TaskStart"
        task_start_path = root / task_start_rel
        manifest_file = root / ".agents" / MANIFEST_FILENAME
        task_start_expected = False
        if manifest_file.is_file():
            with contextlib.suppress(OSError, orjson.JSONDecodeError, ValueError):
                m_data = orjson.loads(manifest_file.read_bytes())
                if isinstance(m_data, dict):
                    t_meta = (m_data.get("targets") or {}).get("cline")
                    if isinstance(t_meta, dict) and bool(t_meta.get("hooks")):
                        task_start_expected = True
        if task_start_expected or task_start_path.exists():
            if not task_start_path.is_file():
                return {
                    "ok": False,
                    "expected": True,
                    "status": "missing",
                    "hook_file": task_start_rel,
                    "detail": f"{target_name}: TaskStart baseline hook '{task_start_rel}' is missing (originally installed with --hooks)",
                }
            ts_text = ""
            with contextlib.suppress(OSError):
                ts_text = task_start_path.read_text(encoding="utf-8", errors="replace")
            ts_exec = os.name == "nt" or os.access(task_start_path, os.X_OK)
            if not _is_eidos_cline_hook_script(ts_text) or not ts_exec:
                return {
                    "ok": False,
                    "expected": True,
                    "status": "corrupted",
                    "hook_file": task_start_rel,
                    "detail": f"{target_name}: Eidos TaskStart baseline hook non-executable or misconfigured in '{task_start_rel}'",
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
    hooks_dir = root / ".clinerules" / "hooks"
    pre_tool_hook = hooks_dir / "PreToolUse"
    task_start_hook = hooks_dir / "TaskStart"
    if hooks:
        _update_project_cline_hooks(pre_tool_hook, launcher=launcher)
        _update_project_cline_hooks(task_start_hook, launcher=launcher)
        return [pre_tool_hook, task_start_hook]
    _remove_project_cline_hooks(pre_tool_hook)
    _remove_project_cline_hooks(task_start_hook)
    return []


def _cline_extra_uninstall(root: Path) -> bool:
    hooks_dir = root / ".clinerules" / "hooks"
    r1 = _remove_project_cline_hooks(hooks_dir / "PreToolUse")
    r2 = _remove_project_cline_hooks(hooks_dir / "TaskStart")
    return bool(r1 or r2)
