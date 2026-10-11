"""Validator for Agent Plugins conforming to agent-plugins.org v1.0.0."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import jsonschema
import yaml

PLUGIN_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


@dataclass
class PluginValidationReport:
    """Summary of plugin package validation."""
    plugin_path: Path
    is_valid: bool = True
    plugin_name: Optional[str] = None
    plugin_version: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)
    extensions: List[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def _load_bundled_schema(schema_name: str) -> Dict[str, Any]:
    """Load bundled JSON schema from package."""
    schema_path = Path(__file__).parent.parent / "schemas" / "agent_plugins" / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Bundled schema '{schema_name}' not found at {schema_path}")
    with open(schema_path, "r", encoding="utf-8") as f:
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
        with open(manifest_path, "r", encoding="utf-8") as f:
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

    report.plugin_version = manifest.get("version")

    # Discover extensions in manifest
    if "extensions" in manifest and isinstance(manifest["extensions"], dict):
        report.extensions = list(manifest["extensions"].keys())

    # 2. Validate MCP configuration: mcp.json (optional)
    mcp_path = plugin_dir / "mcp.json"
    if mcp_path.exists():
        try:
            with open(mcp_path, "r", encoding="utf-8") as f:
                mcp_data = json.load(f)

            mcp_schema = _load_bundled_schema("mcp.schema.json")
            mcp_validator = jsonschema.Draft202012Validator(mcp_schema)
            mcp_errors = list(mcp_validator.iter_errors(mcp_data))
            for err in mcp_errors:
                report.add_error(f"mcp.json schema violation: {err.message} at path '{err.json_path}'")

            servers = mcp_data.get("mcpServers", {})
            if isinstance(servers, dict):
                report.mcp_servers = list(servers.keys())
                for s_name, s_conf in servers.items():
                    if isinstance(s_conf, dict):
                        # Verify stdio command token rule
                        if s_conf.get("type") == "stdio":
                            cmd = s_conf.get("command", "")
                            if " " in cmd.strip() and not (cmd.startswith("./") or cmd.startswith("/")):
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
                                    report.add_warning(f"Skill '{item.name}' SKILL.md missing 'description' in frontmatter.")
                                report.skills.append(s_name or item.name)
                            else:
                                report.add_error(f"Skill '{item.name}' SKILL.md frontmatter is not a dictionary.")
                        else:
                            report.add_error(f"Skill '{item.name}' SKILL.md has malformed YAML frontmatter delimiters.")
                    else:
                        report.add_error(f"Skill '{item.name}' SKILL.md missing required YAML frontmatter.")
                except Exception as e:
                    report.add_error(f"Error parsing skill '{item.name}' SKILL.md: {e}")

    return report
