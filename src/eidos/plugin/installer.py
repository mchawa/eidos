"""Project-scoped installer and uninstaller for Eidos Agent Plugins across AI agent environments."""

import contextlib
import enum
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

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


def _copy_plugin_tree(
    source_dir: Path, target_dir: Path, force: bool = False, launcher: str = "eidos"
) -> list[Path]:
    """Copy all plugin files to target destination within the project."""
    target_dir.mkdir(parents=True, exist_ok=True)
    # Remove legacy scripts directory if no longer present in source bundle
    legacy_scripts = target_dir / "scripts"
    if legacy_scripts.exists() and not (source_dir / "scripts").exists():
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
            if rel.name in (".mcp.json", "mcp.json", "mcp_config.json"):
                continue
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            created.append(dest)

    # Ensure plugin.json reflects the bundled plugin version
    target_manifest = target_dir / "plugin.json"
    if target_manifest.exists():
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
        try:
            hooks_text = target_hooks.read_text(encoding="utf-8")
            hooks_text = hooks_text.replace('"command": "eidos hook ', '"command": "uvx --from eidos-ai eidos hook ')
            target_hooks.write_text(hooks_text, encoding="utf-8")
        except OSError:
            pass

    return created


def _copy_skills_flat(source_dir: Path, target_skills_dir: Path, force: bool = False) -> list[Path]:
    """Copy individual skills from source bundle into target_skills_dir/<skill-name>/ and prune retired Eidos skills."""
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
            dest_skill_dir.mkdir(parents=True, exist_ok=True)
            for file_path in item.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to(item)
                    dest_file = dest_skill_dir / rel
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


def _ensure_claude_plugin_manifest(target_dir: Path) -> list[Path]:
    """Ensure plugin.json exists at root and in .claude-plugin/ of target plugin directory."""
    target_dir.mkdir(parents=True, exist_ok=True)
    claude_meta_dir = target_dir / ".claude-plugin"
    claude_meta_dir.mkdir(parents=True, exist_ok=True)

    manifest_file = target_dir / "plugin.json"
    legacy_manifest_file = claude_meta_dir / "plugin.json"
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

    created = []
    for mfile in (manifest_file, legacy_manifest_file):
        if mfile.exists():
            try:
                with open(mfile, encoding="utf-8") as f:
                    existing = json.load(f)
                existing["version"] = bundled_ver
                manifest_data_to_write = existing
            except (OSError, json.JSONDecodeError, ValueError):
                manifest_data_to_write = manifest_data
        else:
            manifest_data_to_write = manifest_data

        with open(mfile, "w", encoding="utf-8") as f:
            json.dump(manifest_data_to_write, f, indent=2)
        created.append(mfile)

    return created


def _write_claude_commands(target_dir: Path, launcher: str = "eidos") -> list[Path]:
    """Write Claude Code slash commands to target_dir/commands/ (Layer 2: Commands)."""
    commands_dir = target_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    created = []
    cmd_prefix = "uvx --from eidos-ai eidos" if launcher == "uvx" else "eidos"

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
        cmd_file.write_text(content, encoding="utf-8")
        created.append(cmd_file)

    return created


def _update_project_claude_settings(file_path: Path, plugin_id: str = "eidos") -> bool:
    """Safely update project-level .claude/settings.json to enable the Eidos plugin."""
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


def _write_cursor_rules(file_path: Path) -> Path:
    """Write native Cursor .mdc rule (Cursor Rules standard with YAML frontmatter)."""
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


def _write_aider_conventions(file_path: Path) -> Path:
    """Write .aider.conventions.md guidelines with installed skill mappings."""
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


def _write_copilot_instructions(file_path: Path) -> Path:
    """Safely write or update Eidos instructions in .github/copilot-instructions.md."""
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


def _ensure_copilot_plugin_manifest(target_dir: Path) -> Path:
    """Ensure .github/plugin/plugin.json exists in target directory with Agent Plugins 1.0 schema and GitHub Copilot metadata."""
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = target_dir / "plugin.json"
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
            existing["version"] = bundled_ver
            if "extensions" not in existing or not isinstance(existing["extensions"], dict):
                existing["extensions"] = {}
            existing["extensions"]["com.github.copilot"] = {
                "type": "agent_plugin",
                "skills": "skills",
            }
            manifest_data = existing
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_file


def _update_project_copilot_settings(settings_file: Path) -> bool:
    """Safely update .github/copilot/settings.json to enable the Eidos plugin."""
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



def _ensure_codex_plugin_manifest(target_dir: Path) -> Path:
    """Ensure .codex/plugin.json exists in target plugin directory with OpenAI Codex metadata."""
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = target_dir / "plugin.json"
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
            existing["version"] = bundled_ver
            if "extensions" not in existing or not isinstance(existing["extensions"], dict):
                existing["extensions"] = {}
            existing["extensions"]["com.openai"] = {
                "type": "agent_plugin",
                "skills": "skills",
            }
            manifest_data = existing
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return manifest_file


def _write_codex_rules(file_path: Path) -> Path:
    """Write .codex/rules.md."""
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


def install_plugin(
    target: PluginInstallTarget = PluginInstallTarget.PROJECT,
    project_dir: Path | None = None,
    dest_path: Path | None = None,
    force: bool = True,
    launcher: str = "eidos",
) -> list[InstallResult]:
    """Install the Eidos agent plugin strictly inside project scope (.agents/plugins/eidos)."""
    source_dir = locate_plugin_bundle()
    root = (project_dir or Path.cwd()).resolve()
    results = []

    # If explicit destination provided, install there directly
    if dest_path is not None:
        target_path = dest_path if dest_path.is_absolute() else (root / dest_path)
        created = _copy_plugin_tree(source_dir, target_path, force=force, launcher=launcher)
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
    targets_to_run = []
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
        if t in (PluginInstallTarget.PROJECT, PluginInstallTarget.ANTIGRAVITY):
            plugin_dir = root / ".agents" / "plugins" / "eidos"
            created = _copy_plugin_tree(source_dir, plugin_dir, force=force, launcher=launcher)
            results.append(
                InstallResult(
                    target=t.value,
                    destination=plugin_dir,
                    success=True,
                    message="Installed to project scope .agents/plugins/eidos (agent-plugins.org v1.0.0).",
                    created_files=created,
                )
            )

        elif t == PluginInstallTarget.CURSOR:
            cursor_rules = root / ".cursor" / "rules" / "eidos.mdc"
            _write_cursor_rules(cursor_rules)

            cursor_skills = root / ".cursor" / "skills"
            created_skills = _copy_skills_flat(source_dir, cursor_skills, force=force)

            results.append(
                InstallResult(
                    target="cursor",
                    destination=cursor_rules,
                    success=True,
                    message="Configured rules in .cursor/rules/eidos.mdc and 4 skills in .cursor/skills/.",
                    created_files=[cursor_rules, *created_skills],
                )
            )

        elif t == PluginInstallTarget.CLAUDE:
            claude_plugin_dir = root / ".claude" / "plugins" / "eidos"
            created = _copy_plugin_tree(source_dir, claude_plugin_dir, force=force, launcher=launcher)

            manifests = _ensure_claude_plugin_manifest(claude_plugin_dir)
            created.extend(manifests)

            commands = _write_claude_commands(claude_plugin_dir, launcher=launcher)
            created.extend(commands)

            claude_skills = root / ".claude" / "skills"
            created_skills = _copy_skills_flat(source_dir, claude_skills, force=force)

            claude_settings = root / ".claude" / "settings.json"
            _update_project_claude_settings(claude_settings, "eidos@plugins-dir")
            created.append(claude_settings)

            results.append(
                InstallResult(
                    target="claude",
                    destination=claude_plugin_dir,
                    success=True,
                    message="Installed Claude Code plugin at .claude/plugins/eidos, 4 skills in .claude/skills/, commands in .claude/plugins/eidos/commands/, and configured .claude/settings.json.",
                    created_files=[*created, *created_skills],
                )
            )

        elif t == PluginInstallTarget.AIDER:
            conv_file = root / ".aider.conventions.md"
            _write_aider_conventions(conv_file)
            aider_conf = root / ".aider.conf.yml"
            _update_project_aider_conf(aider_conf, ".aider.conventions.md")
            aider_skills = root / ".aider" / "skills"
            created_skills = _copy_skills_flat(source_dir, aider_skills, force=force)
            results.append(
                InstallResult(
                    target="aider",
                    destination=aider_conf,
                    success=True,
                    message="Configured .aider.conf.yml, generated .aider.conventions.md, and installed 4 skills into .aider/skills/.",
                    created_files=[conv_file, aider_conf, *created_skills],
                )
            )

        elif t == PluginInstallTarget.CLINE:
            cline_plugin = root / ".cline" / "plugins" / "eidos"
            created_plugin = _copy_plugin_tree(source_dir, cline_plugin, force=force, launcher=launcher)

            cline_skills = root / ".cline" / "skills"
            created_skills = _copy_skills_flat(source_dir, cline_skills, force=force)

            results.append(
                InstallResult(
                    target="cline",
                    destination=cline_plugin,
                    success=True,
                    message="Configured plugin bundle in .cline/plugins/eidos and 4 skills in .cline/skills/.",
                    created_files=[*created_plugin, *created_skills],
                )
            )

        elif t in (PluginInstallTarget.OPEN_CODE, PluginInstallTarget.OPENCODE):
            opencode_plugin_dir = root / ".opencode" / "plugins" / "eidos"
            created_plugin = _copy_plugin_tree(source_dir, opencode_plugin_dir, force=force, launcher=launcher)

            opencode_skills = root / ".opencode" / "skills"
            created_skills = _copy_skills_flat(source_dir, opencode_skills, force=force)

            results.append(
                InstallResult(
                    target="open-code",
                    destination=opencode_plugin_dir,
                    success=True,
                    message="Configured plugin bundle in .opencode/plugins/eidos and 4 skills in .opencode/skills/.",
                    created_files=[*created_plugin, *created_skills],
                )
            )

        elif t == PluginInstallTarget.CODEX:
            codex_dir = root / ".codex"
            created_codex = _copy_plugin_tree(source_dir, codex_dir, force=force, launcher=launcher)

            codex_manifest = _ensure_codex_plugin_manifest(codex_dir)
            created_codex.append(codex_manifest)

            codex_rules = codex_dir / "rules.md"
            _write_codex_rules(codex_rules)
            created_codex.append(codex_rules)

            codex_skills = codex_dir / "skills"
            created_skills = _copy_skills_flat(source_dir, codex_skills, force=force)
            created_codex.extend(created_skills)

            results.append(
                InstallResult(
                    target="codex",
                    destination=codex_dir,
                    success=True,
                    message="Installed OpenAI Codex plugin bundle (.codex/plugin.json, 4 skills, rules.md).",
                    created_files=created_codex,
                )
            )

        elif t == PluginInstallTarget.COPILOT:
            copilot_plugin_dir = root / ".github" / "plugin"
            created_plugin = _copy_plugin_tree(source_dir, copilot_plugin_dir, force=force, launcher=launcher)

            copilot_manifest = _ensure_copilot_plugin_manifest(copilot_plugin_dir)
            created_plugin.append(copilot_manifest)

            copilot_skills = root / ".github" / "skills"
            created_skills = _copy_skills_flat(source_dir, copilot_skills, force=force)

            copilot_settings = root / ".github" / "copilot" / "settings.json"
            _update_project_copilot_settings(copilot_settings)

            copilot_instructions = root / ".github" / "copilot-instructions.md"
            _write_copilot_instructions(copilot_instructions)

            results.append(
                InstallResult(
                    target="copilot",
                    destination=copilot_plugin_dir,
                    success=True,
                    message="Installed GitHub Copilot plugin (.github/plugin/), 4 skills (.github/skills/), settings (.github/copilot/settings.json), and instructions.",
                    created_files=[
                        *created_plugin,
                        *created_skills,
                        copilot_settings,
                        copilot_instructions,
                    ],
                )
            )

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
                    removed=True,
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

    targets_to_run = []
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
        if t in (PluginInstallTarget.PROJECT, PluginInstallTarget.ANTIGRAVITY):
            plugin_dir = root / ".agents" / "plugins" / "eidos"
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
                # Clean up empty parent directories
                plugins_dir = plugin_dir.parent
                if plugins_dir.exists() and not any(plugins_dir.iterdir()):
                    with contextlib.suppress(OSError):
                        plugins_dir.rmdir()
                agents_dir = plugins_dir.parent
                if agents_dir.exists() and not any(agents_dir.iterdir()):
                    with contextlib.suppress(OSError):
                        agents_dir.rmdir()
                rel = plugin_dir.relative_to(root) if plugin_dir.is_relative_to(root) else plugin_dir
                results.append(
                    UninstallResult(
                        target=t.value,
                        destination=plugin_dir,
                        success=True,
                        message=f"Removed plugin directory at {rel}.",
                        removed=True,
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
                        removed=False,
                    )
                )

        elif t == PluginInstallTarget.CURSOR:
            cursor_config = root / ".cursor" / "mcp.json"
            removed_mcp = _remove_project_mcp_server(cursor_config, "eidos")

            cursor_rules = root / ".cursor" / "rules" / "eidos.mdc"
            removed_rules = False
            if cursor_rules.exists():
                cursor_rules.unlink()
                removed_rules = True
                rules_dir = cursor_rules.parent
                if rules_dir.exists() and not any(rules_dir.iterdir()):
                    with contextlib.suppress(OSError):
                        rules_dir.rmdir()

            removed_skills = _remove_skills_flat(source_dir, root / ".cursor" / "skills")

            cursor_dir = root / ".cursor"
            if cursor_dir.exists() and not any(cursor_dir.iterdir()):
                with contextlib.suppress(OSError):
                    cursor_dir.rmdir()

            was_removed = removed_mcp or removed_rules or removed_skills
            results.append(
                UninstallResult(
                    target="cursor",
                    destination=cursor_config,
                    success=True,
                    message="Removed .cursor/rules/eidos.mdc, .cursor/skills/, and updated .cursor/mcp.json."
                    if was_removed
                    else "No Cursor configuration found (already removed).",
                    removed=was_removed,
                )
            )

        elif t == PluginInstallTarget.CLAUDE:
            claude_plugin_dir = root / ".claude" / "plugins" / "eidos"
            removed_plugin = False
            if claude_plugin_dir.exists():
                shutil.rmtree(claude_plugin_dir)
                removed_plugin = True
                plugins_parent = claude_plugin_dir.parent
                if plugins_parent.exists() and not any(plugins_parent.iterdir()):
                    with contextlib.suppress(OSError):
                        plugins_parent.rmdir()

            # Also clean legacy .claude/skills/eidos if it existed
            legacy_claude_plugin = root / ".claude" / "skills" / "eidos"
            if legacy_claude_plugin.exists():
                shutil.rmtree(legacy_claude_plugin)
                removed_plugin = True

            removed_skills = _remove_skills_flat(source_dir, root / ".claude" / "skills")

            claude_settings = root / ".claude" / "settings.json"
            removed_settings = _remove_project_claude_settings(claude_settings, "eidos@plugins-dir")

            claude_config = root / ".claude" / "mcp.json"
            removed_mcp = _remove_project_mcp_server(claude_config, "eidos")

            claude_dir = root / ".claude"
            if claude_dir.exists() and not any(claude_dir.iterdir()):
                with contextlib.suppress(OSError):
                    claude_dir.rmdir()

            was_removed = removed_plugin or removed_skills or removed_mcp or removed_settings
            results.append(
                UninstallResult(
                    target="claude",
                    destination=claude_plugin_dir,
                    success=True,
                    message="Removed Claude Code plugin from .claude/plugins/eidos, skills from .claude/skills/, and cleaned up .claude configuration."
                    if was_removed
                    else "No Claude Code plugin or MCP config found (already removed).",
                    removed=was_removed,
                )
            )

        elif t == PluginInstallTarget.AIDER:
            conv_file = root / ".aider.conventions.md"
            removed_conv = False
            if conv_file.exists():
                conv_file.unlink()
                removed_conv = True

            aider_conf = root / ".aider.conf.yml"
            removed_conf = _remove_project_aider_conf(aider_conf, ".aider.conventions.md")
            removed_skills = _remove_skills_flat(source_dir, root / ".aider" / "skills")
            was_removed = removed_conv or removed_conf or removed_skills

            results.append(
                UninstallResult(
                    target="aider",
                    destination=aider_conf,
                    success=True,
                    message="Removed .aider.conventions.md, .aider/skills/, and updated .aider.conf.yml."
                    if was_removed
                    else "No Aider configuration found (already removed).",
                    removed=was_removed,
                )
            )

        elif t == PluginInstallTarget.CLINE:
            cline_rules = root / ".clinerules"
            removed_rules = False
            if cline_rules.exists():
                cline_rules.unlink()
                removed_rules = True

            cline_plugin = root / ".cline" / "plugins" / "eidos"
            removed_plugin = False
            if cline_plugin.exists():
                shutil.rmtree(cline_plugin)
                removed_plugin = True
                parent_plugins = cline_plugin.parent
                if parent_plugins.exists() and not any(parent_plugins.iterdir()):
                    with contextlib.suppress(OSError):
                        parent_plugins.rmdir()

            legacy_cline_skills = root / ".cline" / "skills" / "eidos"
            if legacy_cline_skills.exists():
                shutil.rmtree(legacy_cline_skills)
                removed_plugin = True

            removed_skills = _remove_skills_flat(source_dir, root / ".cline" / "skills")

            removed_mcp = _remove_project_mcp_server(root / ".cline" / "mcp.json", "eidos")
            removed_settings = _remove_project_mcp_server(root / ".cline" / "cline_mcp_settings.json", "eidos")

            cline_dir = root / ".cline"
            if cline_dir.exists() and not any(cline_dir.iterdir()):
                with contextlib.suppress(OSError):
                    cline_dir.rmdir()

            was_removed = removed_rules or removed_plugin or removed_skills or removed_mcp or removed_settings
            results.append(
                UninstallResult(
                    target="cline",
                    destination=root / ".cline",
                    success=True,
                    message="Removed .cline MCP settings, plugin bundle in .cline/plugins/eidos, and .cline/skills/."
                    if was_removed
                    else "No Cline configurations found (already removed).",
                    removed=was_removed,
                )
            )

        elif t in (PluginInstallTarget.OPEN_CODE, PluginInstallTarget.OPENCODE):
            opencode_json = root / "opencode.json"
            removed_mcp = _remove_project_opencode_json(opencode_json)

            opencode_rules = root / ".opencode" / "rules.md"
            removed_rules = False
            if opencode_rules.exists():
                opencode_rules.unlink()
                removed_rules = True

            opencode_plugin_dir = root / ".opencode" / "plugins" / "eidos"
            removed_plugin = False
            if opencode_plugin_dir.exists():
                shutil.rmtree(opencode_plugin_dir)
                removed_plugin = True
                parent_plugins = opencode_plugin_dir.parent
                if parent_plugins.exists() and not any(parent_plugins.iterdir()):
                    with contextlib.suppress(OSError):
                        parent_plugins.rmdir()

            removed_skills = _remove_skills_flat(source_dir, root / ".opencode" / "skills")

            opencode_dir = root / ".opencode"
            if opencode_dir.exists() and not any(opencode_dir.iterdir()):
                with contextlib.suppress(OSError):
                    opencode_dir.rmdir()

            was_removed = removed_mcp or removed_rules or removed_plugin or removed_skills
            results.append(
                UninstallResult(
                    target="open-code",
                    destination=opencode_json,
                    success=True,
                    message="Removed opencode.json MCP, .opencode/plugins/eidos, and .opencode/skills/."
                    if was_removed
                    else "No OpenCode configuration found (already removed).",
                    removed=was_removed,
                )
            )

        elif t == PluginInstallTarget.CODEX:
            codex_dir = root / ".codex"
            removed_codex = False
            if codex_dir.exists():
                shutil.rmtree(codex_dir)
                removed_codex = True

            results.append(
                UninstallResult(
                    target="codex",
                    destination=codex_dir,
                    success=True,
                    message="Removed OpenAI Codex plugin bundle (.codex/)."
                    if removed_codex
                    else "No Codex plugin bundle found (already removed).",
                    removed=removed_codex,
                )
            )

        elif t == PluginInstallTarget.COPILOT:
            copilot_plugin_dir = root / ".github" / "plugin"
            removed_plugin = False
            if copilot_plugin_dir.exists():
                shutil.rmtree(copilot_plugin_dir)
                removed_plugin = True

            removed_skills = _remove_skills_flat(source_dir, root / ".github" / "skills")
            removed_settings = _remove_project_copilot_settings(root / ".github" / "copilot" / "settings.json")
            removed_copilot_mcp = _remove_project_mcp_server(root / ".github" / "copilot" / "mcp.json", "eidos")

            copilot_dir = root / ".github" / "copilot"
            if copilot_dir.exists() and not any(copilot_dir.iterdir()):
                with contextlib.suppress(OSError):
                    copilot_dir.rmdir()

            vscode_mcp = root / ".vscode" / "mcp.json"
            removed_vscode_mcp = _remove_project_mcp_server(vscode_mcp, "eidos")

            copilot_instructions = root / ".github" / "copilot-instructions.md"
            removed_instructions = _remove_copilot_instructions(copilot_instructions)

            was_removed = (
                removed_plugin
                or removed_skills
                or removed_settings
                or removed_copilot_mcp
                or removed_vscode_mcp
                or removed_instructions
            )
            results.append(
                UninstallResult(
                    target="copilot",
                    destination=copilot_plugin_dir,
                    success=True,
                    message="Removed GitHub Copilot plugin (.github/plugin/), skills (.github/skills/), and configuration."
                    if was_removed
                    else "No GitHub Copilot configuration found (already removed).",
                    removed=was_removed,
                )
            )

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
