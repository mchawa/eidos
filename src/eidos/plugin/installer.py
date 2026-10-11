"""Project-scoped installer and uninstaller for Eidos Agent Plugins across AI agent environments."""

import enum
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class PluginInstallTarget(str, enum.Enum):
    PROJECT = "project"          # Installs/removes .agents/plugins/eidos and configures project MCPs
    WORKSPACE = "workspace"      # Installs/removes .agents/plugins/eidos
    ANTIGRAVITY = "antigravity"  # Installs/removes .agents/plugins/eidos
    CLAUDE = "claude"            # Configures/removes local .claude/mcp.json
    CURSOR = "cursor"            # Configures/removes local .cursor/mcp.json
    ALL = "all"                  # Same as project


@dataclass
class InstallResult:
    target: str
    destination: Path
    success: bool
    message: str
    created_files: List[Path] = field(default_factory=list)


@dataclass
class UninstallResult:
    target: str
    destination: Path
    success: bool
    message: str
    removed: bool = True


def locate_plugin_bundle() -> Path:
    """Find the bundled Eidos plugin package."""
    # 1. Check embedded in package
    pkg_bundle = Path(__file__).parent.parent / "plugin_bundle"
    if pkg_bundle.exists() and (pkg_bundle / "plugin.json").exists():
        return pkg_bundle

    # 2. Check repo root plugins/eidos
    repo_bundle = Path.cwd() / "plugins" / "eidos"
    if repo_bundle.exists() and (repo_bundle / "plugin.json").exists():
        return repo_bundle

    raise FileNotFoundError(
        "Could not locate Eidos plugin bundle in package resources or repo root."
    )


def _copy_plugin_tree(source_dir: Path, target_dir: Path, force: bool = False) -> List[Path]:
    """Copy all plugin files to target destination within the project."""
    target_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for item in source_dir.rglob("*"):
        if item.is_file():
            rel = item.relative_to(source_dir)
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            created.append(dest)
    return created


def _update_project_mcp_json(file_path: Path, server_name: str, command: str, args: List[str]) -> bool:
    """Safely update a project-level MCP configuration file with the eidos stdio server."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        data["mcpServers"] = {}

    data["mcpServers"][server_name] = {
        "command": command,
        "args": args
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True


def _remove_project_mcp_server(file_path: Path, server_name: str) -> bool:
    """Safely remove a server entry from a project MCP configuration file."""
    if not file_path.exists():
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False

    servers = data.get("mcpServers", {})
    if server_name not in servers:
        return False

    del servers[server_name]
    data["mcpServers"] = servers

    if not servers and len(data.keys()) == 1:
        # If mcpServers was the only key and is now empty, delete the file
        file_path.unlink()
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            try:
                parent.rmdir()
            except OSError:
                pass
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return True


def install_plugin(
    target: PluginInstallTarget = PluginInstallTarget.PROJECT,
    project_dir: Optional[Path] = None,
    dest_path: Optional[Path] = None,
    force: bool = True
) -> List[InstallResult]:
    """Install the Eidos agent plugin strictly inside project scope (.agents/plugins/eidos)."""
    source_dir = locate_plugin_bundle()
    root = (project_dir or Path.cwd()).resolve()
    results = []

    # If explicit destination provided, install there directly
    if dest_path is not None:
        target_path = dest_path if dest_path.is_absolute() else (root / dest_path)
        created = _copy_plugin_tree(source_dir, target_path, force=force)
        results.append(
            InstallResult(
                target="custom",
                destination=target_path,
                success=True,
                message=f"Installed plugin bundle to custom project path: {target_path}",
                created_files=created
            )
        )
        return results

    # Determine project targets
    targets_to_run = []
    if target in (PluginInstallTarget.PROJECT, PluginInstallTarget.ALL):
        targets_to_run = [
            PluginInstallTarget.PROJECT,
            PluginInstallTarget.CURSOR,
            PluginInstallTarget.CLAUDE,
        ]
    else:
        targets_to_run = [target]

    for t in targets_to_run:
        if t in (PluginInstallTarget.PROJECT, PluginInstallTarget.WORKSPACE, PluginInstallTarget.ANTIGRAVITY):
            plugin_dir = root / ".agents" / "plugins" / "eidos"
            created = _copy_plugin_tree(source_dir, plugin_dir, force=force)
            results.append(
                InstallResult(
                    target=t.value,
                    destination=plugin_dir,
                    success=True,
                    message="Installed to project scope .agents/plugins/eidos (agent-plugins.org v1.0.0).",
                    created_files=created
                )
            )

        elif t == PluginInstallTarget.CURSOR:
            cursor_config = root / ".cursor" / "mcp.json"
            _update_project_mcp_json(cursor_config, "eidos", "eidos", ["mcp"])
            results.append(
                InstallResult(
                    target="cursor",
                    destination=cursor_config,
                    success=True,
                    message="Configured project MCP server in .cursor/mcp.json.",
                    created_files=[cursor_config]
                )
            )

        elif t == PluginInstallTarget.CLAUDE:
            claude_config = root / ".claude" / "mcp.json"
            _update_project_mcp_json(claude_config, "eidos", "eidos", ["mcp"])
            results.append(
                InstallResult(
                    target="claude",
                    destination=claude_config,
                    success=True,
                    message="Configured project MCP server in .claude/mcp.json.",
                    created_files=[claude_config]
                )
            )

    return results


def uninstall_plugin(
    target: PluginInstallTarget = PluginInstallTarget.PROJECT,
    project_dir: Optional[Path] = None,
    dest_path: Optional[Path] = None,
) -> List[UninstallResult]:
    """Uninstall / remove the Eidos agent plugin strictly from project scope."""
    root = (project_dir or Path.cwd()).resolve()
    results = []

    # If explicit destination provided, remove there directly
    if dest_path is not None:
        target_path = dest_path if dest_path.is_absolute() else (root / dest_path)
        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            results.append(
                UninstallResult(
                    target="custom",
                    destination=target_path,
                    success=True,
                    message=f"Removed custom plugin path: {target_path}",
                    removed=True
                )
            )
        else:
            results.append(
                UninstallResult(
                    target="custom",
                    destination=target_path,
                    success=True,
                    message=f"Custom path '{target_path}' does not exist.",
                    removed=False
                )
            )
        return results

    targets_to_run = []
    if target in (PluginInstallTarget.PROJECT, PluginInstallTarget.ALL):
        targets_to_run = [
            PluginInstallTarget.PROJECT,
            PluginInstallTarget.CURSOR,
            PluginInstallTarget.CLAUDE,
        ]
    else:
        targets_to_run = [target]

    for t in targets_to_run:
        if t in (PluginInstallTarget.PROJECT, PluginInstallTarget.WORKSPACE, PluginInstallTarget.ANTIGRAVITY):
            plugin_dir = root / ".agents" / "plugins" / "eidos"
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
                # Clean up empty parent directories
                plugins_dir = plugin_dir.parent
                if plugins_dir.exists() and not any(plugins_dir.iterdir()):
                    try:
                        plugins_dir.rmdir()
                    except OSError:
                        pass
                agents_dir = plugins_dir.parent
                if agents_dir.exists() and not any(agents_dir.iterdir()):
                    try:
                        agents_dir.rmdir()
                    except OSError:
                        pass
                rel = plugin_dir.relative_to(root) if plugin_dir.is_relative_to(root) else plugin_dir
                results.append(
                    UninstallResult(
                        target=t.value,
                        destination=plugin_dir,
                        success=True,
                        message=f"Removed plugin directory at {rel}.",
                        removed=True
                    )
                )
            else:
                rel = plugin_dir.relative_to(root) if plugin_dir.is_relative_to(root) else plugin_dir
                results.append(
                    UninstallResult(
                        target=t.value,
                        destination=plugin_dir,
                        success=True,
                        message=f"No plugin found at {rel} (already removed).",
                        removed=False
                    )
                )

        elif t == PluginInstallTarget.CURSOR:
            cursor_config = root / ".cursor" / "mcp.json"
            removed = _remove_project_mcp_server(cursor_config, "eidos")
            results.append(
                UninstallResult(
                    target="cursor",
                    destination=cursor_config,
                    success=True,
                    message="Removed 'eidos' from .cursor/mcp.json." if removed else "No 'eidos' entry in .cursor/mcp.json.",
                    removed=removed
                )
            )

        elif t == PluginInstallTarget.CLAUDE:
            claude_config = root / ".claude" / "mcp.json"
            removed = _remove_project_mcp_server(claude_config, "eidos")
            results.append(
                UninstallResult(
                    target="claude",
                    destination=claude_config,
                    success=True,
                    message="Removed 'eidos' from .claude/mcp.json." if removed else "No 'eidos' entry in .claude/mcp.json.",
                    removed=removed
                )
            )

    return results
