"""Validator for Agent Plugins conforming to agent-plugins.org v1.0.0."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from eidos.core.workspace.assets import get_agent_plugins_schemas_dir
from eidos.plugin.version import is_valid_semver

PLUGIN_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


@dataclass
class PluginValidationReport:
    """Summary of plugin package validation."""

    plugin_path: Path
    is_valid: bool = True
    plugin_name: str | None = None
    plugin_version: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def _load_bundled_schema(schema_name: str) -> dict[str, Any]:
    """Load bundled JSON schema from assets."""
    try:
        schema_path = get_agent_plugins_schemas_dir() / schema_name
    except Exception as e:
        raise FileNotFoundError(f"Bundled schema '{schema_name}' not found: {e}") from e
    if not schema_path.exists():
        raise FileNotFoundError(f"Bundled schema '{schema_name}' not found at {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def validate_plugin_package(plugin_dir: Path) -> PluginValidationReport:
    """Validate an Agent Plugin directory against agent-plugins.org v1.0.0 specifications."""
    report = PluginValidationReport(plugin_path=plugin_dir)

    if not plugin_dir.exists():
        report.add_error(f"Plugin directory '{plugin_dir}' does not exist.")
        return report

    if not plugin_dir.is_dir():
        report.add_error(f"Plugin path '{plugin_dir}' is not a directory.")
        return report

    # 1. Validate manifest: plugin.json
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        report.add_error("Missing required manifest file 'plugin.json' in plugin root.")
        return report

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        report.add_error(f"Invalid JSON in 'plugin.json': {e}")
        return report
    except Exception as e:
        report.add_error(f"Error reading 'plugin.json': {e}")
        return report

    # Validate against plugin.schema.json
    try:
        plugin_schema = _load_bundled_schema("plugin.schema.json")
        validator = jsonschema.Draft202012Validator(plugin_schema)
        schema_errors = list(validator.iter_errors(manifest))
        for err in schema_errors:
            report.add_error(f"plugin.json schema violation: {err.message} at path '{err.json_path}'")
    except Exception as e:
        report.add_error(f"Schema validator error: {e}")

    # Validate name constraints specifically
    name = manifest.get("name")
    if name:
        report.plugin_name = name
        if not PLUGIN_NAME_PATTERN.match(name) or len(name) > 64:
            report.add_error(
                f"Plugin name '{name}' violates naming constraints: must be 1-64 chars, "
                "lowercase alphanumeric/hyphens/dots, starting/ending with alphanumeric, no '--' or '..'"
            )
    else:
        report.add_error("plugin.json is missing required 'name' field.")

    # Validate version strictly with SemVer 2.0.0
    version = manifest.get("version")
    if version:
        report.plugin_version = version
        if not is_valid_semver(version):
            report.add_error(
                f"Plugin version '{version}' is not a valid Semantic Version (SemVer 2.0.0). "
                "Expected format: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD] (e.g. 0.1.0, 1.0.0-beta.1)"
            )
    else:
        report.add_error("plugin.json is missing required 'version' field.")

    # Discover extensions in manifest
    if "extensions" in manifest and isinstance(manifest["extensions"], dict):
        report.extensions = list(manifest["extensions"].keys())

    # 2. Validate MCP configuration: mcp_config.json (Antigravity) or mcp.json (agent-plugins.org)
    mcp_config_path = plugin_dir / "mcp_config.json"
    mcp_path = plugin_dir / "mcp.json"

    if mcp_config_path.exists():
        try:
            with open(mcp_config_path, encoding="utf-8") as f:
                mcp_data = json.load(f)
            servers = mcp_data.get("mcpServers", {})
            if isinstance(servers, dict):
                report.mcp_servers.extend(list(servers.keys()))
        except json.JSONDecodeError as e:
            report.add_error(f"Invalid JSON in 'mcp_config.json': {e}")
        except Exception as e:
            report.add_error(f"Error reading 'mcp_config.json': {e}")

    elif mcp_path.exists():
        try:
            with open(mcp_path, encoding="utf-8") as f:
                mcp_data = json.load(f)

            mcp_schema = _load_bundled_schema("mcp.schema.json")
            mcp_validator = jsonschema.Draft202012Validator(mcp_schema)
            mcp_errors = list(mcp_validator.iter_errors(mcp_data))
            for err in mcp_errors:
                report.add_error(f"mcp.json schema violation: {err.message} at path '{err.json_path}'")

            servers = mcp_data.get("mcpServers", {})
            if isinstance(servers, dict):
                report.mcp_servers.extend(list(servers.keys()))
                for s_name, s_conf in servers.items():
                    if isinstance(s_conf, dict) and s_conf.get("type") == "stdio":
                        cmd = s_conf.get("command", "")
                        if " " in cmd.strip() and not cmd.startswith(("./", "/")):
                            report.add_warning(
                                f"MCP server '{s_name}' command '{cmd}' contains spaces. "
                                "Spec requires a single executable token, not a shell command string."
                            )
        except json.JSONDecodeError as e:
            report.add_error(f"Invalid JSON in 'mcp.json': {e}")
        except Exception as e:
            report.add_error(f"Error validating 'mcp.json': {e}")

    # 3. Validate Skills: skills/ (optional)
    skills_dir = plugin_dir / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        for item in sorted(skills_dir.iterdir()):
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if not skill_md.exists():
                    report.add_warning(f"Skill directory '{item.name}' missing 'SKILL.md'.")
                    continue

                try:
                    content = skill_md.read_text(encoding="utf-8")
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            fm = yaml.safe_load(parts[1])
                            if isinstance(fm, dict):
                                s_name = fm.get("name")
                                s_desc = fm.get("description")
                                if not s_name:
                                    report.add_error(f"Skill '{item.name}' SKILL.md missing 'name' in frontmatter.")
                                if not s_desc:
                                    report.add_warning(
                                        f"Skill '{item.name}' SKILL.md missing 'description' in frontmatter."
                                    )
                                report.skills.append(s_name or item.name)
                            else:
                                report.add_error(f"Skill '{item.name}' SKILL.md frontmatter is not a dictionary.")
                        else:
                            report.add_error(f"Skill '{item.name}' SKILL.md has malformed YAML frontmatter delimiters.")
                    else:
                        report.add_error(f"Skill '{item.name}' SKILL.md missing required YAML frontmatter.")
                except Exception as e:
                    report.add_error(f"Error parsing skill '{item.name}' SKILL.md: {e}")

    # 4. Validate Lifecycle Hooks: hooks.json (optional)
    hooks_path = plugin_dir / "hooks.json"
    if hooks_path.exists():
        try:
            with open(hooks_path, encoding="utf-8") as f:
                hooks_data = json.load(f)

            if not isinstance(hooks_data, dict):
                report.add_error("Invalid 'hooks.json': root must be a dictionary of hook configurations.")
            else:
                valid_events = {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop", "enabled"}
                for hook_name, hook_spec in hooks_data.items():
                    if not isinstance(hook_spec, dict):
                        report.add_error(f"Hook '{hook_name}' in 'hooks.json' must be an object.")
                        continue
                    report.hooks.append(hook_name)
                    for event_key, handlers in hook_spec.items():
                        if event_key == "enabled":
                            continue
                        if event_key not in valid_events:
                            report.add_warning(f"Hook '{hook_name}' defines unrecognized event '{event_key}'.")
                        if not isinstance(handlers, list):
                            report.add_error(
                                f"Hook '{hook_name}' event '{event_key}' must be a list of handler configs."
                            )
                            continue

                        for handler in handlers:
                            if not isinstance(handler, dict):
                                report.add_error(f"Hook '{hook_name}' in '{event_key}' contains invalid handler item.")
                                continue
                            if "hooks" in handler and isinstance(handler["hooks"], list):
                                for sub_h in handler["hooks"]:
                                    _verify_hook_command(sub_h, hook_name, event_key, plugin_dir, report)
                            else:
                                _verify_hook_command(handler, hook_name, event_key, plugin_dir, report)

        except json.JSONDecodeError as e:
            report.add_error(f"Invalid JSON in 'hooks.json': {e}")
        except Exception as e:
            report.add_error(f"Error reading 'hooks.json': {e}")

    return report


def _verify_hook_command(
    handler: dict[str, Any],
    hook_name: str,
    event_key: str,
    plugin_dir: Path,
    report: PluginValidationReport,
) -> None:
    cmd = handler.get("command")
    if not cmd:
        report.add_error(f"Hook '{hook_name}' event '{event_key}' handler missing required 'command' field.")
        return
    tokens = cmd.split()
    for token in tokens:
        cleaned = token.lstrip("./")
        if cleaned.startswith("scripts/"):
            script_path = plugin_dir / cleaned
            if not script_path.exists():
                report.add_error(f"Hook '{hook_name}' event '{event_key}' references non-existent script: '{token}'")
