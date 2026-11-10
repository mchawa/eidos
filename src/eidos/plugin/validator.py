"""Validator for Eidos Agent Skills packages (agentskills.io) and lifecycle hooks."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.spec.parser import safe_load_yaml
from eidos.plugin.version import get_bundled_plugin_version, is_valid_semver

PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


@dataclass
class PluginValidationReport:
    """Summary of Agent Skills package validation."""

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


def validate_plugin_package(plugin_dir: Path, version: str | None = None) -> PluginValidationReport:
    """Validate an Agent Skills package directory (skills/*/SKILL.md and optional hooks.json)."""
    report = PluginValidationReport(plugin_path=plugin_dir)

    if not plugin_dir.exists():
        report.add_error(f"Plugin directory '{plugin_dir}' does not exist.")
        return report

    if not plugin_dir.is_dir():
        report.add_error(f"Plugin path '{plugin_dir}' is not a directory.")
        return report

    report.plugin_name = "eidos-agent-skills"
    resolved_version = version if version is not None else get_bundled_plugin_version()
    report.plugin_version = resolved_version
    if not is_valid_semver(resolved_version):
        report.add_error(
            f"Plugin version '{resolved_version}' is not a valid Semantic Version (SemVer 2.0.0). "
            "Expected format: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD] (e.g. 0.1.0, 1.0.0-beta.1)"
        )

    # 1. Validate Skills: skills/*/SKILL.md (or direct <skill>/SKILL.md subdirectories)
    skills_dir: Path | None = plugin_dir / "skills"
    if not (skills_dir.exists() and skills_dir.is_dir()):
        has_direct_skills = any((d / "SKILL.md").is_file() for d in plugin_dir.iterdir() if d.is_dir())
        if has_direct_skills:
            skills_dir = plugin_dir
        else:
            report.add_error("Missing required 'skills' directory in Agent Skills package.")
            skills_dir = None

    if skills_dir is not None:
        for item in sorted(skills_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                skill_md = item / "SKILL.md"
                if not skill_md.exists():
                    report.add_warning(f"Skill directory '{item.name}' missing 'SKILL.md'.")
                    continue

                try:
                    content = skill_md.read_text(encoding="utf-8")
                    if re.match(r"\A---[ \t]*(?:\r?\n|\Z)", content):
                        m_fm = re.match(
                            r"\A---[ \t]*\r?\n(.*?\r?\n)?---[ \t]*(?:\r?\n|\Z)",
                            content,
                            re.DOTALL,
                        )
                        if m_fm:
                            fm = safe_load_yaml(m_fm.group(1) or "")
                            if isinstance(fm, dict):
                                s_name = fm.get("name")
                                s_desc = fm.get("description")
                                if not s_name or not isinstance(s_name, str):
                                    report.add_error(f"Skill '{item.name}' SKILL.md missing 'name' in frontmatter.")
                                else:
                                    if not PLUGIN_NAME_PATTERN.match(s_name) or len(s_name) > 64:
                                        report.add_error(
                                            f"Skill name '{s_name}' violates naming constraints: must be 1-64 chars, "
                                            "lowercase alphanumeric/hyphens/dots, starting/ending with alphanumeric, no '--' or '..'"
                                        )
                                    if s_name != item.name:
                                        report.add_error(
                                            f"Skill '{item.name}' SKILL.md frontmatter name '{s_name}' does not match directory name '{item.name}'."
                                        )
                                if not s_desc:
                                    report.add_warning(
                                        f"Skill '{item.name}' SKILL.md missing 'description' in frontmatter."
                                    )
                                report.skills.append(str(s_name) if s_name else item.name)
                            else:
                                report.add_error(f"Skill '{item.name}' SKILL.md frontmatter is not a dictionary.")
                        else:
                            report.add_error(f"Skill '{item.name}' SKILL.md has malformed YAML frontmatter delimiters.")
                    else:
                        report.add_error(f"Skill '{item.name}' SKILL.md missing required YAML frontmatter.")
                except Exception as e:
                    report.add_error(f"Error parsing skill '{item.name}' SKILL.md: {e}")

        if not report.skills:
            report.add_error("No valid skills found in 'skills' directory.")

    # 2. Validate Lifecycle Hooks: hooks.json (optional)
    hooks_path = plugin_dir / "hooks.json"
    if hooks_path.exists():
        try:
            hooks_data = orjson.loads(hooks_path.read_bytes())

            if not isinstance(hooks_data, dict):
                report.add_error("Invalid 'hooks.json': root must be a dictionary of hook configurations.")
            else:
                valid_events = {"PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop", "enabled"}
                hook_allowed_events = {
                    "eidos-drift-gate": {"Stop"},
                }
                for hook_name, hook_spec in hooks_data.items():
                    if not isinstance(hook_spec, dict):
                        report.add_error(f"Hook '{hook_name}' in 'hooks.json' must be an object.")
                        continue
                    report.hooks.append(hook_name)
                    allowed_for_hook = hook_allowed_events.get(hook_name)
                    for event_key, handlers in hook_spec.items():
                        if event_key == "enabled":
                            continue
                        if event_key not in valid_events:
                            report.add_warning(f"Hook '{hook_name}' defines unrecognized event '{event_key}'.")
                        elif allowed_for_hook is not None and event_key not in allowed_for_hook:
                            allowed_str = ", ".join(sorted(allowed_for_hook))
                            report.add_error(
                                f"Hook '{hook_name}' event '{event_key}' is not permitted; allowed events: {allowed_str}."
                            )
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

        except orjson.JSONDecodeError as e:
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
