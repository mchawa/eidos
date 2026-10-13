"""Tests verifying Agent Plugin conformance to agent-plugins.org v1.0.0 and project-scoped CLI installer."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.plugin.installer import (
    PluginInstallTarget,
    install_plugin,
    locate_plugin_bundle,
    uninstall_plugin,
)
from eidos.plugin.validator import PLUGIN_NAME_PATTERN, validate_plugin_package
from eidos.plugin.version import (
    check_plugin_version_status,
    compare_semver,
    get_bundled_plugin_version,
    get_installed_plugin_version,
    is_valid_semver,
    parse_semver,
)

runner = CliRunner()


def test_plugin_naming_regex():
    """Verify agent-plugins.org naming constraints."""
    valid_names = ["eidos", "my-plugin", "acme.tools", "lint3r", "a", "eidos-cli"]
    invalid_names = ["My-Plugin", "-start", "has--double", "too..dots", "end-", ""]

    for name in valid_names:
        assert PLUGIN_NAME_PATTERN.match(name), f"Expected valid name: {name}"

    for name in invalid_names:
        assert not PLUGIN_NAME_PATTERN.match(name), f"Expected invalid name: {name}"


def test_bundled_plugin_conformance():
    """Verify that the bundled Eidos plugin passes agent-plugins.org v1.0.0 validation."""
    bundle_dir = locate_plugin_bundle()
    report = validate_plugin_package(bundle_dir)

    assert report.is_valid, f"Plugin validation failed: {report.errors}"
    assert report.plugin_name == "eidos"
    assert report.plugin_version == get_bundled_plugin_version()
    assert "eidos" in report.skills
    assert "eidos-context" in report.skills
    assert len(report.skills) == 14
    assert len(report.mcp_servers) == 0
    assert "com.google.antigravity" in report.extensions


def test_repo_plugin_conformance():
    """Verify that plugins/eidos passes agent-plugins.org v1.0.0 validation."""
    plugin_dir = Path("plugins/eidos")
    assert plugin_dir.exists(), "plugins/eidos directory missing"

    report = validate_plugin_package(plugin_dir)
    assert report.is_valid, f"Repo plugin validation failed: {report.errors}"
    assert len(report.errors) == 0
    assert len(report.skills) == 14


def test_plugin_install_to_project_scope(tmp_path):
    """Verify project-scoped installation into workspace directories (.agents)."""
    results = install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path, force=True)

    assert len(results) == 3
    targets = [r.target for r in results]
    assert "project" in targets
    assert "cursor" in targets
    assert "claude" in targets

    # Check .agents/plugins/eidos
    agent_dir = tmp_path / ".agents" / "plugins" / "eidos"
    assert (agent_dir / "plugin.json").exists()
    assert not (agent_dir / "mcp_config.json").exists()
    assert not (agent_dir / "mcp.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    agent_report = validate_plugin_package(agent_dir)
    assert agent_report.is_valid

    # Check project-level configs
    cursor_mcp = tmp_path / ".cursor" / "mcp.json"
    cursor_rules = tmp_path / ".cursor" / "rules" / "eidos.mdc"
    assert not cursor_mcp.exists()
    assert cursor_rules.exists()
    assert "alwaysApply: true" in cursor_rules.read_text(encoding="utf-8")

    claude_mcp = tmp_path / ".claude" / "mcp.json"
    assert not claude_mcp.exists()
    claude_settings = tmp_path / ".claude" / "settings.json"
    assert claude_settings.exists()


def test_plugin_install_to_custom_directory(tmp_path):
    """Verify installing plugin to a custom project-relative path."""
    dest = tmp_path / "installed_plugin"
    results = install_plugin(dest_path=dest, force=True)

    assert len(results) == 1
    assert results[0].success
    assert (dest / "plugin.json").exists()
    assert not (dest / "mcp_config.json").exists()
    assert not (dest / "mcp.json").exists()
    assert (dest / "skills" / "eidos" / "SKILL.md").exists()
    report = validate_plugin_package(dest)
    assert report.is_valid


def test_cli_plugin_validate():
    """Verify 'eidos plugin validate' CLI command."""
    res = runner.invoke(app, ["plugin", "validate", "plugins/eidos"])
    assert res.exit_code == 0
    assert "CONFORMANT" in res.stdout
    assert "eidos" in res.stdout


def test_cli_plugin_info():
    """Verify 'eidos plugin info' CLI command."""
    res = runner.invoke(app, ["plugin", "info", "plugins/eidos"])
    assert res.exit_code == 0
    assert "agent-plugins.org v1.0.0" in res.stdout
    assert "eidos" in res.stdout


def test_cli_plugin_install_project(tmp_path):
    """Verify 'eidos plugin install --project-dir' CLI command."""
    res = runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()


def test_plugin_uninstall_project_scope(tmp_path):
    """Verify uninstalling plugin from project scope removes plugin dir and configs."""
    # Install first
    install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path, force=True)
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    cursor_rules = tmp_path / ".cursor" / "rules" / "eidos.mdc"

    assert plugin_dir.exists()
    assert cursor_rules.exists()

    # Uninstall
    results = uninstall_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path)
    assert len(results) == 3
    for r in results:
        assert r.success

    assert not plugin_dir.exists()
    assert not cursor_rules.exists()


def test_cli_plugin_uninstall_project(tmp_path):
    """Verify 'eidos plugin uninstall --project-dir' CLI command."""
    # Install
    runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "plugins" / "eidos").exists()

    # Uninstall
    res = runner.invoke(app, ["plugin", "uninstall", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".agents" / "plugins" / "eidos").exists()


def test_cli_plugin_remove_alias(tmp_path):
    """Verify 'eidos plugin remove --project-dir' CLI alias command."""
    # Install
    runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "plugins" / "eidos").exists()

    # Remove via alias
    res = runner.invoke(app, ["plugin", "remove", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".agents" / "plugins" / "eidos").exists()


def test_plugin_install_to_claude_plugin(tmp_path):
    """Verify installing plugin specifically as Claude Code plugin into .claude/plugins/eidos."""
    results = install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)

    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "claude"

    claude_plugin_dir = tmp_path / ".claude" / "plugins" / "eidos"
    assert (claude_plugin_dir / "plugin.json").exists()
    assert (claude_plugin_dir / ".claude-plugin" / "plugin.json").exists()
    assert not (claude_plugin_dir / ".mcp.json").exists()
    assert not (claude_plugin_dir / "mcp.json").exists()
    assert (claude_plugin_dir / "commands" / "validate.md").exists()
    assert (claude_plugin_dir / "commands" / "drift.md").exists()
    assert (claude_plugin_dir / "skills" / "eidos" / "SKILL.md").exists()
    # Direct flat skills in .claude/skills/
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-architecture" / "SKILL.md").exists()
    assert len([d for d in (tmp_path / ".claude" / "skills").iterdir() if d.is_dir()]) == 14

    with open(claude_plugin_dir / "plugin.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["name"] == "eidos"

    # Verify settings.json
    settings_file = tmp_path / ".claude" / "settings.json"
    assert settings_file.exists()
    with open(settings_file, encoding="utf-8") as f:
        settings = json.load(f)
    assert settings["enabledPlugins"].get("eidos@plugins-dir") is True
    assert any(p.get("path") in (".claude/plugins/eidos", "./.claude/plugins/eidos") for p in settings.get("plugins", []))

    # Verify project-level .claude/mcp.json is NOT created
    claude_mcp = tmp_path / ".claude" / "mcp.json"
    assert not claude_mcp.exists()

    # Verify get_installed_plugin_version detects Claude plugin
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()


def test_plugin_uninstall_claude_plugin(tmp_path):
    """Verify uninstalling Claude Code plugin cleans .claude/plugins/eidos, skills, and settings.json."""
    # Install first
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)
    claude_plugin_dir = tmp_path / ".claude" / "plugins" / "eidos"
    settings_file = tmp_path / ".claude" / "settings.json"
    assert claude_plugin_dir.exists()

    # Uninstall
    results = uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path)
    assert len(results) == 1
    assert results[0].success

    assert not claude_plugin_dir.exists()
    assert not (tmp_path / ".claude" / "skills" / "eidos-architecture").exists()
    if settings_file.exists():
        with open(settings_file, encoding="utf-8") as f:
            settings = json.load(f)
        assert "eidos@plugins-dir" not in settings.get("enabledPlugins", {})


def test_cli_install_and_uninstall_shortcut_claude(tmp_path):
    """Verify 'eidos install claude' and 'eidos uninstall claude' shortcuts work."""
    # Install with positional argument
    res = runner.invoke(app, ["install", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".claude" / "plugins" / "eidos" / "plugin.json").exists()

    # Uninstall with positional argument
    res = runner.invoke(app, ["uninstall", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".claude" / "plugins" / "eidos").exists()


def test_cli_install_rejects_workspace_target(tmp_path):
    """Verify 'workspace' is rejected as target following strict agent-plugins standard."""
    res = runner.invoke(app, ["install", "workspace", "--project-dir", str(tmp_path)])
    assert res.exit_code != 0
    # Output should contain error indication about invalid choice
    assert "Invalid value" in res.output or "invalid" in res.output.lower()


def test_plugin_install_and_uninstall_aider(tmp_path):
    """Verify Aider installation creates .aider.conventions.md, skills, and updates .aider.conf.yml."""
    results = install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "aider"

    conventions = tmp_path / ".aider.conventions.md"
    aider_conf = tmp_path / ".aider.conf.yml"
    aider_skills = tmp_path / ".aider" / "skills"
    assert conventions.exists()
    assert aider_conf.exists()
    assert (aider_skills / "eidos" / "SKILL.md").exists()
    assert len([d for d in aider_skills.iterdir() if d.is_dir()]) == 14
    assert "Single Source of Truth" in conventions.read_text(encoding="utf-8")

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not conventions.exists()
    assert not aider_conf.exists()
    assert not (aider_skills / "eidos").exists()


def test_plugin_install_and_uninstall_cline(tmp_path):
    """Verify Cline installation creates plugin bundle and flat skills without MCP configs."""
    results = install_plugin(target=PluginInstallTarget.CLINE, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "cline"

    rules = tmp_path / ".clinerules"
    mcp_file = tmp_path / ".cline" / "mcp.json"
    settings_file = tmp_path / ".cline" / "cline_mcp_settings.json"
    plugin_dir = tmp_path / ".cline" / "plugins" / "eidos"
    skills_dir = tmp_path / ".cline" / "skills"
    assert not rules.exists()
    assert not mcp_file.exists()
    assert not settings_file.exists()
    assert plugin_dir.exists()
    assert (skills_dir / "eidos" / "SKILL.md").exists()
    assert (skills_dir / "eidos-architecture" / "SKILL.md").exists()
    assert (skills_dir / "eidos-drift" / "SKILL.md").exists()
    assert len([d for d in skills_dir.iterdir() if d.is_dir()]) == 14

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.CLINE, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not plugin_dir.exists()
    assert not (skills_dir / "eidos").exists()


def test_plugin_install_and_uninstall_opencode(tmp_path):
    """Verify OpenCode installation creates plugin bundle and flat skills."""
    results = install_plugin(target=PluginInstallTarget.OPEN_CODE, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "open-code"

    cfg = tmp_path / "opencode.json"
    rules = tmp_path / ".opencode" / "rules.md"
    plugin_dir = tmp_path / ".opencode" / "plugins" / "eidos"
    skills_dir = tmp_path / ".opencode" / "skills"
    assert not cfg.exists()
    assert not rules.exists()
    assert plugin_dir.exists()
    assert (skills_dir / "eidos" / "SKILL.md").exists()
    assert len([d for d in skills_dir.iterdir() if d.is_dir()]) == 14

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.OPEN_CODE, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not plugin_dir.exists()
    assert not (skills_dir / "eidos").exists()


def test_plugin_install_and_uninstall_codex(tmp_path):
    """Verify OpenAI Codex installation creates .codex/plugin.json, rules, and skills."""
    results = install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "codex"

    codex_dir = tmp_path / ".codex"
    assert (codex_dir / "plugin.json").exists()
    assert not (codex_dir / "mcp.json").exists()
    assert (codex_dir / "rules.md").exists()
    assert (codex_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert len([d for d in (codex_dir / "skills").iterdir() if d.is_dir()]) == 14

    with open(codex_dir / "plugin.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["name"] == "eidos"
    assert "com.openai" in manifest["extensions"]

    # Verify version detection
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not codex_dir.exists()


def test_plugin_install_and_uninstall_copilot(tmp_path):
    """Verify GitHub Copilot installation creates native plugin (.github/plugin/), skills (.github/skills/), settings, and instructions."""
    results = install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "copilot"

    # 1. Native plugin bundle
    copilot_plugin = tmp_path / ".github" / "plugin"
    assert (copilot_plugin / "plugin.json").exists()
    assert not (copilot_plugin / "mcp.json").exists()
    with open(copilot_plugin / "plugin.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["name"] == "eidos"
    assert "com.github.copilot" in manifest["extensions"]

    # 2. Native Copilot Agent Skills
    copilot_skills = tmp_path / ".github" / "skills"
    assert (copilot_skills / "eidos" / "SKILL.md").exists()
    assert (copilot_skills / "eidos-architecture" / "SKILL.md").exists()
    assert (copilot_skills / "eidos-delta" / "SKILL.md").exists()
    assert len([d for d in copilot_skills.iterdir() if d.is_dir()]) == 14

    # 3. Copilot settings
    copilot_settings = tmp_path / ".github" / "copilot" / "settings.json"
    assert copilot_settings.exists()
    with open(copilot_settings, encoding="utf-8") as f:
        settings = json.load(f)
    assert settings["enabledPlugins"].get("eidos") is True

    # 4. MCP servers are NOT created
    assert not (tmp_path / ".github" / "copilot" / "mcp.json").exists()
    assert not (tmp_path / ".vscode" / "mcp.json").exists()

    # 5. Instructions bridge
    instructions = tmp_path / ".github" / "copilot-instructions.md"
    assert instructions.exists()
    text = instructions.read_text(encoding="utf-8")
    assert "<!-- EIDOS_COPILOT_START -->" in text
    assert "Single Source of Truth" in text

    # Verify get_installed_plugin_version detects Copilot plugin
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not copilot_plugin.exists()
    assert not (copilot_skills / "eidos").exists()
    assert not copilot_settings.exists()
    assert not instructions.exists()

    # Test preserving custom instructions
    custom_header = "# Team Instructions\nFollow our team rules.\n"
    instructions.parent.mkdir(parents=True, exist_ok=True)
    instructions.write_text(custom_header, encoding="utf-8")

    install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True)
    content = instructions.read_text(encoding="utf-8")
    assert "Follow our team rules." in content
    assert "<!-- EIDOS_COPILOT_START -->" in content

    uninstall_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path)
    assert instructions.exists()
    clean_content = instructions.read_text(encoding="utf-8")
    assert "Follow our team rules." in clean_content
    assert "<!-- EIDOS_COPILOT_START -->" not in clean_content


def test_plugin_install_and_uninstall_all_targets(tmp_path):
    """Verify installing target 'all' configures all 8 AI agent environments."""
    results = install_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path, force=True)
    targets = [r.target for r in results]
    assert len(targets) == 8
    assert "project" in targets
    assert "cursor" in targets
    assert "claude" in targets
    assert "aider" in targets
    assert "cline" in targets
    assert "open-code" in targets
    assert "codex" in targets
    assert "copilot" in targets

    # Uninstall all
    uninst = uninstall_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path)
    assert len(uninst) == 8
    for u in uninst:
        assert u.success


def test_cli_install_new_agent_shortcuts(tmp_path):
    """Verify CLI shortcuts for aider, cline, open-code, opencode, codex, and copilot."""
    # Aider
    res = runner.invoke(app, ["install", "aider", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".aider.conventions.md").exists()

    # Cline
    res = runner.invoke(app, ["install", "cline", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".cline" / "skills" / "eidos" / "SKILL.md").exists()

    # Open-code
    res = runner.invoke(app, ["install", "open-code", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".opencode" / "skills" / "eidos" / "SKILL.md").exists()

    # Opencode (alias)
    res = runner.invoke(app, ["install", "opencode", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0

    # Codex
    res = runner.invoke(app, ["install", "codex", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".codex" / "plugin.json").exists()
    assert (tmp_path / ".codex" / "skills" / "eidos" / "SKILL.md").exists()

    # Copilot
    res = runner.invoke(app, ["install", "copilot", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()
    assert (tmp_path / ".github" / "skills" / "eidos" / "SKILL.md").exists()

    # Uninstall all
    res = runner.invoke(app, ["uninstall", "all", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0


def test_semver_validation_and_parsing():
    """Verify SemVer 2.0.0 validation and tuple extraction (CTR-EIDOS-CLI)."""
    valid_versions = [
        "0.1.0",
        "1.0.0",
        "1.2.3-alpha.1",
        "2.0.0+build.184",
        "0.0.1-rc1",
        "1.0.0-0.3.7",
        "1.0.0-x.7.z.92",
    ]
    invalid_versions = [
        "v1.0.0",
        "1.0",
        "1",
        "1.0.0.0",
        "1.0.0-",
        "01.1.0",
        "alpha",
        "",
    ]
    for v in valid_versions:
        assert is_valid_semver(v), f"Expected valid semver: {v}"
        parsed = parse_semver(v)
        assert parsed is not None

    for v in invalid_versions:
        assert not is_valid_semver(v), f"Expected invalid semver: {v}"
        assert parse_semver(v) is None


def test_semver_comparison():
    """Verify SemVer 2.0.0 precedence rules (CTR-EIDOS-CLI)."""
    assert compare_semver("0.1.0", "0.1.0") == 0
    assert compare_semver("0.1.0", "0.2.0") == -1
    assert compare_semver("0.2.0", "0.1.0") == 1
    assert compare_semver("1.0.0-alpha", "1.0.0") == -1
    assert compare_semver("1.0.0", "1.0.0-alpha") == 1
    assert compare_semver("1.0.0-alpha.1", "1.0.0-alpha.2") == -1
    assert compare_semver("1.0.0", "1.0.1") == -1


def test_plugin_manifest_semver_enforcement(tmp_path):
    """Verify validate_plugin_package rejects invalid SemVer in plugin.json (CTR-EIDOS-CLI)."""
    plugin_dir = tmp_path / "test_plugin"
    plugin_dir.mkdir()

    # Missing version
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "test-plugin"})
    )
    report_missing = validate_plugin_package(plugin_dir)
    assert not report_missing.is_valid
    assert any("missing required 'version' field" in err for err in report_missing.errors)

    # Invalid SemVer
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "test-plugin",
                "version": "v1.0",
            }
        )
    )
    report_invalid = validate_plugin_package(plugin_dir)
    assert not report_invalid.is_valid
    assert any("not a valid Semantic Version" in err for err in report_invalid.errors)


def test_plugin_version_status_check(tmp_path):
    """Verify version status resolution (not installed, up to date, outdated, newer) (CTR-EIDOS-CLI)."""
    # 1. Not installed
    status = check_plugin_version_status(project_dir=tmp_path)
    assert status["status"] == "not_installed"
    assert not status["is_installed"]

    # 2. Installed up-to-date
    install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path)
    status_installed = check_plugin_version_status(project_dir=tmp_path)
    assert status_installed["status"] == "up_to_date"
    assert status_installed["is_installed"]
    assert status_installed["installed_version"] == get_bundled_plugin_version()

    # 3. Outdated
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    with open(plugin_json, encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = "0.0.9"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    status_outdated = check_plugin_version_status(project_dir=tmp_path)
    assert status_outdated["status"] == "outdated"
    assert status_outdated["installed_version"] == "0.0.9"

    # 4. Newer
    data["version"] = "9.9.9"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    status_newer = check_plugin_version_status(project_dir=tmp_path)
    assert status_newer["status"] == "newer"
    assert status_newer["installed_version"] == "9.9.9"


def test_cli_plugin_version(tmp_path):
    """Verify 'eidos plugin version' CLI command across different states (CTR-EIDOS-CLI)."""
    # Not installed
    res_not_inst = runner.invoke(app, ["plugin", "version", "--project-dir", str(tmp_path)])
    assert res_not_inst.exit_code == 0
    assert "Not installed" in res_not_inst.stdout

    # Installed
    runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    res_inst = runner.invoke(app, ["plugin", "version", "--project-dir", str(tmp_path)])
    assert res_inst.exit_code == 0
    assert "Up to date" in res_inst.stdout
    assert get_bundled_plugin_version() in res_inst.stdout

    # Outdated
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    with open(plugin_json, encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = "0.0.8"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    res_outdated = runner.invoke(app, ["plugin", "version", "--project-dir", str(tmp_path)])
    assert res_outdated.exit_code == 0
    assert "Outdated" in res_outdated.stdout
    assert "eidos plugin update" in res_outdated.stdout


def test_cli_plugin_update(tmp_path):
    """Verify 'eidos plugin update' CLI command upgrades older plugin versions (CTR-EIDOS-CLI)."""
    # Install initially
    runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])

    # Mock older installed version
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    with open(plugin_json, encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = "0.0.5"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # Run update
    res = runner.invoke(app, ["plugin", "update", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Upgraded Eidos Agent Plugin" in res.stdout
    assert "0.0.5" in res.stdout
    assert get_bundled_plugin_version() in res.stdout

    # Verify plugin.json was updated
    with open(plugin_json, encoding="utf-8") as f:
        updated_data = json.load(f)
    assert updated_data["version"] == get_bundled_plugin_version()


def test_cli_plugin_upgrade_alias(tmp_path):
    """Verify 'eidos plugin upgrade' alias CLI command (CTR-EIDOS-CLI)."""
    res = runner.invoke(app, ["plugin", "upgrade", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()


def test_no_assets_in_src():
    """Verify schemas and plugin bundles are relocated out of src/ into assets/."""
    assert not Path("src/eidos/schemas").exists(), "src/eidos/schemas should not exist"
    assert not Path("src/eidos/plugin_bundle").exists(), "src/eidos/plugin_bundle should not exist"


def test_assets_plugin_reference_conformance():
    """Verify that assets/plugin_reference satisfies agent-plugins.org v1.0.0."""
    ref_plugin = Path("assets/plugin_reference")
    assert ref_plugin.exists(), "assets/plugin_reference missing"
    report = validate_plugin_package(ref_plugin)
    assert report.is_valid, f"assets/plugin_reference invalid: {report.errors}"


def test_assets_and_installed_plugin_parity():
    """Verify assets/plugin_reference and .agents/plugins/eidos have identical structure and content."""
    ref_plugin = Path("assets/plugin_reference")
    installed_plugin = Path(".agents/plugins/eidos")
    assert ref_plugin.exists()
    if not installed_plugin.exists():
        pytest.skip(".agents/plugins/eidos not installed in this environment")

    ref_files = {p.relative_to(ref_plugin) for p in ref_plugin.rglob("*") if p.is_file() and ".eidos" not in p.parts}
    inst_files = {p.relative_to(installed_plugin) for p in installed_plugin.rglob("*") if p.is_file() and ".eidos" not in p.parts}
    assert ref_files == inst_files, f"File mismatch: {ref_files ^ inst_files}"

    for rel in sorted(ref_files):
        f1 = (ref_plugin / rel).read_text(encoding="utf-8")
        f2 = (installed_plugin / rel).read_text(encoding="utf-8")
        assert f1 == f2, f"Content mismatch in {rel}"


def test_plugin_hooks_conformance():
    """Verify that hooks.json in assets/plugin_reference satisfies agent plugin hook specifications."""
    ref_plugin = Path("assets/plugin_reference")
    report = validate_plugin_package(ref_plugin)
    assert report.is_valid, f"Plugin validation failed: {report.errors}"
    assert "eidos-doc-linter" in report.hooks
    assert "eidos-drift-gate" in report.hooks


def test_doc_lint_hook_pre_tool_use():
    """Verify pre_tool linting intercepts and blocks malformed YAML frontmatters."""
    script_path = Path("assets/plugin_reference/scripts/lint_docs.py")
    assert script_path.exists()

    # 1. Invalid YAML
    bad_payload = json.dumps(
        {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": "eidos/policies/test.adoc",
                    "CodeContent": "---\ntype: [unclosed flow\n---\n",
                },
            }
        }
    )
    res_bad = subprocess.run(
        [sys.executable, str(script_path), "--event", "pre_tool"],
        input=bad_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res_bad.returncode == 0
    data_bad = json.loads(res_bad.stdout)
    assert data_bad["decision"] == "deny"
    assert "YAML Syntax Error" in data_bad["reason"]

    # 2. Missing required fields
    incomplete_payload = json.dumps(
        {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": "eidos/policies/test.adoc",
                    "CodeContent": "---\nfoo: bar\n---\n",
                },
            }
        }
    )
    res_inc = subprocess.run(
        [sys.executable, str(script_path), "--event", "pre_tool"],
        input=incomplete_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res_inc.returncode == 0
    data_inc = json.loads(res_inc.stdout)
    assert data_inc["decision"] == "deny"
    assert "missing required fields" in data_inc["reason"]

    # 3. Valid frontmatter
    good_payload = json.dumps(
        {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": "eidos/policies/test.adoc",
                    "CodeContent": "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-999\ntitle: Sample Policy\n---\n",
                },
            }
        }
    )
    res_good = subprocess.run(
        [sys.executable, str(script_path), "--event", "pre_tool"],
        input=good_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res_good.returncode == 0
    data_good = json.loads(res_good.stdout)
    assert data_good["decision"] == "allow"


def test_doc_lint_hook_stop_event(tmp_path):
    """Verify stop hook blocks completion if eidos/ specifications fail validation (INV-AGENT-LINT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/lint_docs.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    bad_spec = eidos_dir / "invalid.adoc"
    bad_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: BAD-POLICY\n---\n",
        encoding="utf-8",
    )

    stop_payload = json.dumps(
        {
            "executionNum": 1,
            "terminationReason": "model_stop",
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "stop"],
        input=stop_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["decision"] == "continue"
    assert "failed validation" in data["reason"] or "validation failed" in data["reason"]


def test_doc_lint_hook_post_invocation(tmp_path):
    """Verify post_invocation hook injects ephemeral warning message when living docs are invalid."""
    script_path = Path("assets/plugin_reference/scripts/lint_docs.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    bad_spec = eidos_dir / "invalid.adoc"
    bad_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: BAD-POLICY\n---\n",
        encoding="utf-8",
    )

    inv_payload = json.dumps(
        {
            "invocationNum": 2,
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "post_invocation"],
        input=inv_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "injectSteps" in data
    assert len(data["injectSteps"]) == 1
    assert "ephemeralMessage" in data["injectSteps"][0]
    assert "Living specification" in data["injectSteps"][0]["ephemeralMessage"]


def test_cli_lint_command():
    """Verify 'eidos lint' CLI command executes and reports clean validation (CTR-EIDOS-CLI)."""
    res = runner.invoke(app, ["lint", "eidos"])
    assert res.exit_code == 0
    assert "Success! All" in res.stdout


def test_doc_lint_hook_stop_event_ignores_code_modifications(tmp_path):
    """Verify stop hook allows task completion when code changes, as lint_docs solely validates eidos docs (INV-AGENT-LINT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/lint_docs.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nrule: Must execute safely\nenforcement:\n  mechanism: lint\n  severity: blocking\n---\n",
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("def do_feature(): pass\n", encoding="utf-8")

    stop_payload = json.dumps(
        {
            "executionNum": 1,
            "terminationReason": "model_stop",
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "stop"],
        input=stop_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["decision"] == "allow"


def test_doc_lint_hook_post_invocation_ignores_code_modifications(tmp_path):
    """Verify post_invocation hook does not emit drift notices for code modifications (INV-AGENT-LINT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/lint_docs.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nrule: Must execute safely\nenforcement:\n  mechanism: lint\n  severity: blocking\n---\n",
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("def do_feature(): pass\n", encoding="utf-8")

    inv_payload = json.dumps(
        {
            "invocationNum": 2,
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "post_invocation"],
        input=inv_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "injectSteps" in data
    assert len(data["injectSteps"]) == 0


def test_doc_lint_hook_pre_tool_ignores_non_eidos_files(tmp_path):
    """Verify pre_tool hook ignores documentation or config files outside the eidos/ directory."""
    script_path = Path("assets/plugin_reference/scripts/lint_docs.py")

    # 1. Non-eidos .adoc file with arbitrary or missing frontmatter
    non_eidos_payload = json.dumps(
        {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": str(tmp_path / "docs" / "guide.adoc"),
                    "CodeContent": "= My General Guide\nSome text without frontmatter.\n",
                },
            },
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "pre_tool"],
        input=non_eidos_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["decision"] == "allow"

    # 2. Non-eidos .json file (e.g. package.json)
    package_json_payload = json.dumps(
        {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": str(tmp_path / "package.json"),
                    "CodeContent": "{\n  \"name\": \"my-app\"\n}\n",
                },
            },
            "workspacePaths": [str(tmp_path)],
        }
    )
    res_pkg = subprocess.run(
        [sys.executable, str(script_path), "--event", "pre_tool"],
        input=package_json_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res_pkg.returncode == 0
    data_pkg = json.loads(res_pkg.stdout)
    assert data_pkg["decision"] == "allow"


def test_drift_hook_stop_event_drift_detected(tmp_path):
    """Verify drift hook blocks task completion when code changes without living spec updates (INV-AGENT-DRIFT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/check_drift.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nrule: Must execute safely\nenforcement:\n  mechanism: lint\n  severity: blocking\n---\n",
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("def do_feature(): pass\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "agent@eidos.ai"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Agent"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "eidos"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init specs"], cwd=tmp_path, capture_output=True, check=True)

    stop_payload = json.dumps(
        {
            "executionNum": 1,
            "terminationReason": "model_stop",
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "stop"],
        input=stop_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["decision"] == "continue"
    assert "Drift Gate" in data["reason"] or "drift detected" in data["reason"].lower()
    assert "src/feature.py" in data["reason"]


def test_drift_hook_stop_event_synchronized(tmp_path):
    """Verify drift hook allows task completion when code and living specs are co-evolved (INV-AGENT-DRIFT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/check_drift.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nrule: Must execute safely\nenforcement:\n  mechanism: lint\n  severity: blocking\n---\n",
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("def do_feature(): pass\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "agent@eidos.ai"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Agent"], cwd=tmp_path, capture_output=True, check=True)

    stop_payload = json.dumps(
        {
            "executionNum": 1,
            "terminationReason": "model_stop",
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "stop"],
        input=stop_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["decision"] == "allow"


def test_drift_hook_post_invocation_drift_notice(tmp_path):
    """Verify drift hook emits ephemeral notice during in-flight turns when drift is detected (INV-AGENT-DRIFT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/check_drift.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nrule: Must execute safely\nenforcement:\n  mechanism: lint\n  severity: blocking\n---\n",
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("def do_feature(): pass\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "agent@eidos.ai"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Agent"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "eidos"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init specs"], cwd=tmp_path, capture_output=True, check=True)

    inv_payload = json.dumps(
        {
            "invocationNum": 2,
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "post_invocation"],
        input=inv_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "injectSteps" in data
    assert len(data["injectSteps"]) == 1
    assert "ephemeralMessage" in data["injectSteps"][0]
    assert "Eidos Sync Notice" in data["injectSteps"][0]["ephemeralMessage"]


def test_drift_hook_post_invocation_clean(tmp_path):
    """Verify drift hook emits no messages when code and specs are clean (INV-AGENT-DRIFT-HOOK)."""
    script_path = Path("assets/plugin_reference/scripts/check_drift.py")
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()

    inv_payload = json.dumps(
        {
            "invocationNum": 2,
            "workspacePaths": [str(tmp_path)],
        }
    )
    res = subprocess.run(
        [sys.executable, str(script_path), "--event", "post_invocation"],
        input=inv_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert "injectSteps" in data
    assert len(data["injectSteps"]) == 0



