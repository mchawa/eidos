"""Installer for Eidos Agent Plugin across AI agent clients and environments."""

import enum
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class PluginInstallTarget(str, enum.Enum):
    ANTIGRAVITY = "antigravity"
    WORKSPACE = "workspace"
    CLAUDE = "claude"
    CURSOR = "cursor"
    AUTO = "auto"


@dataclass
class InstallResult:
    target: str
    destination: Path
    success: bool
    message: str
    created_files: List[Path] = field(default_factory=list)


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
    """Copy all plugin files to target destination."""
    if target_dir.exists() and not force:
        # Overwrite files without deleting whole directory
        pass
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


def _update_mcp_json_file(file_path: Path, server_name: str, command: str, args: List[str]) -> bool:
    """Safely update an MCP configuration file with the eidos stdio server."""
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


def _enable_antigravity_plugin_live(plugin_dir_name: str) -> bool:
    """Notify running Antigravity Language Server to enable the plugin live."""
    import os
    import urllib.request

    ls_address = os.environ.get("ANTIGRAVITY_LS_ADDRESS", "localhost:5387")
    csrf_token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN", "")
    url = f"http://{ls_address}/exa.language_server_pb.LanguageServerService/JetboxWriteState"
    payload = json.dumps({"userConfig": {"plugins": {plugin_dir_name: {"enabled": True}}}}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-codeium-csrf-token": csrf_token
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def install_plugin(
    target: PluginInstallTarget = PluginInstallTarget.AUTO,
    dest_path: Optional[Path] = None,
    force: bool = True
) -> List[InstallResult]:
    """Install the Eidos agent plugin into the specified target client environment."""
    source_dir = locate_plugin_bundle()
    results = []

    # If explicit destination provided, install there directly
    if dest_path is not None:
        created = _copy_plugin_tree(source_dir, dest_path, force=force)
        results.append(
            InstallResult(
                target="custom",
                destination=dest_path,
                success=True,
                message=f"Installed plugin bundle to custom path: {dest_path}",
                created_files=created
            )
        )
        return results

    targets_to_run = []
    if target == PluginInstallTarget.AUTO:
        targets_to_run = [PluginInstallTarget.ANTIGRAVITY, PluginInstallTarget.WORKSPACE]
    else:
        targets_to_run = [target]

    home = Path.home()

    for t in targets_to_run:
        if t == PluginInstallTarget.ANTIGRAVITY:
            antigravity_plugin_dir = home / ".gemini" / "config" / "plugins" / "eidos"
            created = _copy_plugin_tree(source_dir, antigravity_plugin_dir, force=force)
            live_enabled = _enable_antigravity_plugin_live("eidos")
            msg = "Installed to Antigravity global plugins (~/.gemini/config/plugins/eidos)."
            if live_enabled:
                msg += " Enabled live via Antigravity Language Server."
            else:
                msg += " Restart Antigravity IDE to discover."
            results.append(
                InstallResult(
                    target="antigravity",
                    destination=antigravity_plugin_dir,
                    success=True,
                    message=msg,
                    created_files=created
                )
            )

        elif t == PluginInstallTarget.WORKSPACE:
            ws_plugin_dir = Path.cwd() / ".agent-plugins" / "eidos"
            created = _copy_plugin_tree(source_dir, ws_plugin_dir, force=force)
            results.append(
                InstallResult(
                    target="workspace",
                    destination=ws_plugin_dir,
                    success=True,
                    message="Installed to workspace .agent-plugins/eidos conforming to agent-plugins.org v1.0.0.",
                    created_files=created
                )
            )

        elif t == PluginInstallTarget.CLAUDE:
            claude_config = home / ".claude.json"
            _update_mcp_json_file(claude_config, "eidos", "eidos", ["mcp"])
            results.append(
                InstallResult(
                    target="claude",
                    destination=claude_config,
                    success=True,
                    message="Configured Eidos MCP server in ~/.claude.json",
                    created_files=[claude_config]
                )
            )

        elif t == PluginInstallTarget.CURSOR:
            cursor_config = Path.cwd() / ".cursor" / "mcp.json"
            _update_mcp_json_file(cursor_config, "eidos", "eidos", ["mcp"])
            results.append(
                InstallResult(
                    target="cursor",
                    destination=cursor_config,
                    success=True,
                    message="Configured Eidos MCP server in .cursor/mcp.json",
                    created_files=[cursor_config]
                )
            )

    return results
