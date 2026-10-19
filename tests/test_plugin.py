"""Tests verifying Agent Plugin conformance to agent-plugins.org v1.0.0 and project-scoped CLI installer."""

import json
import subprocess
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
    assert set(report.skills) == {"eidos", "eidos-spec", "eidos-plan", "eidos-ops"}
    assert len(report.skills) == 4
    assert len(report.mcp_servers) == 0
    assert "com.google.antigravity" in report.extensions


def test_repo_plugin_conformance():
    """Verify that plugins/eidos passes agent-plugins.org v1.0.0 validation."""
    plugin_dir = Path("plugins/eidos")
    assert plugin_dir.exists(), "plugins/eidos directory missing"

    report = validate_plugin_package(plugin_dir)
    assert report.is_valid, f"Repo plugin validation failed: {report.errors}"
    assert len(report.errors) == 0
    assert len(report.skills) == 4


def test_plugin_install_to_project_scope(tmp_path):
    """Verify project-scoped installation into workspace directories (.agents) without mutating .cursor or .claude."""
    results = install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path, force=True)

    assert len(results) == 1
    assert results[0].target == "project"

    # Check .agents/plugins/eidos
    agent_dir = tmp_path / ".agents" / "plugins" / "eidos"
    assert (agent_dir / "plugin.json").exists()
    assert not (agent_dir / "mcp_config.json").exists()
    assert not (agent_dir / "mcp.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    agent_report = validate_plugin_package(agent_dir)
    assert agent_report.is_valid

    # Isolated project target must not touch .cursor or .claude
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".claude").exists()


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
    """Verify uninstalling plugin from project scope removes .agents/plugins/eidos without deleting .cursor or .claude."""
    # Install first
    install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path, force=True)
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    cursor_rules = tmp_path / ".cursor" / "rules" / "eidos.mdc"
    cursor_rules.parent.mkdir(parents=True, exist_ok=True)
    cursor_rules.write_text("# Pre-existing Cursor rule\n", encoding="utf-8")

    assert plugin_dir.exists()
    assert cursor_rules.exists()

    # Uninstall project target only
    results = uninstall_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path)
    assert len(results) == 1
    assert results[0].success

    assert not plugin_dir.exists()
    assert cursor_rules.exists()


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
    """Verify 'eidos plugin remove' alias is removed in favor of 'eidos plugin uninstall'."""
    runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "plugins" / "eidos").exists()

    res_removed = runner.invoke(app, ["plugin", "remove", "--project-dir", str(tmp_path)])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["plugin", "uninstall", "--project-dir", str(tmp_path)])
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
    assert not (claude_plugin_dir / "skills").exists()
    # Direct flat skills in .claude/skills/
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-spec" / "SKILL.md").exists()
    assert len([d for d in (tmp_path / ".claude" / "skills").iterdir() if d.is_dir()]) == 4

    with open(claude_plugin_dir / "plugin.json", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["name"] == "eidos"

    # Verify settings.json
    settings_file = tmp_path / ".claude" / "settings.json"
    assert settings_file.exists()
    with open(settings_file, encoding="utf-8") as f:
        settings = json.load(f)
    assert settings["enabledPlugins"].get("eidos@plugins-dir") is True
    assert any(
        p.get("path") in (".claude/plugins/eidos", "./.claude/plugins/eidos") for p in settings.get("plugins", [])
    )

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
    assert not (tmp_path / ".claude" / "skills" / "eidos-spec").exists()
    if settings_file.exists():
        with open(settings_file, encoding="utf-8") as f:
            settings = json.load(f)
        assert "eidos@plugins-dir" not in settings.get("enabledPlugins", {})


def test_cli_install_and_uninstall_shortcut_claude(tmp_path):
    """Verify 'eidos plugin install claude' and 'eidos plugin uninstall claude' work and root shortcuts are removed."""
    # Root shortcuts are removed
    assert runner.invoke(app, ["install", "claude", "--project-dir", str(tmp_path)]).exit_code != 0
    assert runner.invoke(app, ["uninstall", "claude", "--project-dir", str(tmp_path)]).exit_code != 0

    # Install with positional argument under 'plugin'
    res = runner.invoke(app, ["plugin", "install", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".claude" / "plugins" / "eidos" / "plugin.json").exists()

    # Uninstall with positional argument under 'plugin'
    res = runner.invoke(app, ["plugin", "uninstall", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".claude" / "plugins" / "eidos").exists()


def test_cli_install_rejects_workspace_target(tmp_path):
    """Verify 'workspace' is rejected as target following strict agent-plugins standard."""
    res = runner.invoke(app, ["plugin", "install", "workspace", "--project-dir", str(tmp_path)])
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
    assert len([d for d in aider_skills.iterdir() if d.is_dir()]) == 4
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
    assert (skills_dir / "eidos-spec" / "SKILL.md").exists()
    assert (skills_dir / "eidos-plan" / "SKILL.md").exists()
    assert len([d for d in skills_dir.iterdir() if d.is_dir()]) == 4

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
    assert len([d for d in skills_dir.iterdir() if d.is_dir()]) == 4

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
    assert len([d for d in (codex_dir / "skills").iterdir() if d.is_dir()]) == 4

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
    assert (copilot_skills / "eidos-spec" / "SKILL.md").exists()
    assert (copilot_skills / "eidos-plan" / "SKILL.md").exists()
    assert len([d for d in copilot_skills.iterdir() if d.is_dir()]) == 4

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
    """Verify 'eidos plugin install' and 'eidos plugin uninstall' for aider, cline, open-code, opencode, codex, and copilot."""
    # Aider
    res = runner.invoke(app, ["plugin", "install", "aider", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".aider.conventions.md").exists()

    # Cline
    res = runner.invoke(app, ["plugin", "install", "cline", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".cline" / "skills" / "eidos" / "SKILL.md").exists()

    # Open-code
    res = runner.invoke(app, ["plugin", "install", "open-code", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".opencode" / "skills" / "eidos" / "SKILL.md").exists()

    # Codex
    res = runner.invoke(app, ["plugin", "install", "codex", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".codex" / "plugin.json").exists()
    assert (tmp_path / ".codex" / "skills" / "eidos" / "SKILL.md").exists()

    # Copilot
    res = runner.invoke(app, ["plugin", "install", "copilot", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()
    assert (tmp_path / ".github" / "skills" / "eidos" / "SKILL.md").exists()

    # Uninstall all
    res = runner.invoke(app, ["plugin", "uninstall", "all", "--project-dir", str(tmp_path)])
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
    """Verify 'eidos plugin upgrade' alias is removed in favor of 'eidos plugin update' (CTR-EIDOS-CLI)."""
    res_removed = runner.invoke(app, ["plugin", "upgrade", "--project-dir", str(tmp_path)])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["plugin", "update", "--project-dir", str(tmp_path)])
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
    inst_files = {
        p.relative_to(installed_plugin) for p in installed_plugin.rglob("*") if p.is_file() and ".eidos" not in p.parts
    }
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
    assert report.hooks == ["eidos-drift-gate"]

    hooks_file = ref_plugin / "hooks.json"
    with open(hooks_file, encoding="utf-8") as f:
        hooks_data = json.load(f)

    assert set(hooks_data.keys()) == {"eidos-drift-gate"}
    drift_gate = hooks_data["eidos-drift-gate"]
    assert set(drift_gate.keys()) == {"Stop"}
    assert drift_gate["Stop"] == [{"type": "command", "command": "eidos hook", "timeout": 30}]


def test_cli_lint_command():
    """Verify 'eidos validate' CLI command executes and root 'eidos lint' shortcut is removed (CTR-EIDOS-CLI)."""
    res_removed = runner.invoke(app, ["lint", "eidos"])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["validate", "eidos"])
    assert res.exit_code == 0
    assert "Success! All" in res.stdout


def test_drift_hook_stop_event_drift_detected(tmp_path):
    """Verify stateless Stop hook emits non-blocking systemMessage warning when drift or spec validation issues exist (INV-AGENT-DRIFT-HOOK)."""
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nseverity: blocking\ninvariants:\n  - id: INV-POL-100\n    rule: Must execute safely\n---\n",  # eidos:fixture
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
    res = runner.invoke(app, ["hook"], input=stop_payload)
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["decision"] == "allow"
    assert "⚠️ [Eidos Stop Hook]" in data["systemMessage"]
    assert "src/feature.py" in data["systemMessage"]
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()

    # Also verify invalid spec produces a validation warning in systemMessage
    (eidos_dir / "invalid.adoc").write_text(
        "---\ntype: product/policy\nid: BAD-POLICY\n---\n",
        encoding="utf-8",
    )
    res_invalid = runner.invoke(app, ["hook"], input=stop_payload)
    assert res_invalid.exit_code == 0
    data_invalid = json.loads(res_invalid.stdout)
    assert data_invalid["decision"] == "allow"
    assert "Specification validation failed" in data_invalid["systemMessage"]


def test_drift_hook_stop_event_synchronized(tmp_path):
    """Verify stateless Stop hook allows task completion silently when code and living specs are co-evolved (INV-AGENT-DRIFT-HOOK)."""
    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "container.adoc"
    valid_spec.write_text(
        "---\ntype: tech/container\nid: CNT-APP\ntitle: App\ncontainer_type: service\nruntime: python\nstatus: active\nsource_path: src\n---\n= App\n\nProvides `feature.py` (`do_feature`).\n",
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
    res = runner.invoke(app, ["hook"], input=stop_payload)
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data == {"decision": "allow"}


def test_stateless_stop_hook_no_cache_or_block_loop(tmp_path):
    """Verify handle_stop_hook is 100% stateless across repeated Stop invocations (DLT-2026-094)."""
    from eidos.plugin.hooks import handle_stop_hook

    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()
    valid_spec = eidos_dir / "policy.adoc"
    valid_spec.write_text(
        "---\ntype: product/policy\nid: POL-100\ntitle: Safe Exec\ncategory: governance\nseverity: blocking\ninvariants:\n  - id: INV-POL-100\n    rule: Must execute safely\n---\n",  # eidos:fixture
        encoding="utf-8",
    )

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("def do_feature():\n    return 1\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "agent@eidos.ai"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Agent"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "eidos"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init specs"], cwd=tmp_path, capture_output=True, check=True)

    payload = {
        "executionNum": 1,
        "terminationReason": "model_stop",
        "workspacePaths": [str(tmp_path)],
    }

    res1 = handle_stop_hook(payload, tmp_path)
    res2 = handle_stop_hook(payload, tmp_path)
    assert res1["decision"] == "allow"
    assert res2["decision"] == "allow"
    assert "⚠️ [Eidos Stop Hook]" in res1["systemMessage"]
    assert res1 == res2
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()


def test_cli_doctor_command(tmp_path, monkeypatch):
    """Verify 'eidos doctor' and 'eidos doctor --json' CLI health check, custom --path, and virtualenv fallback (DLT-2026-024, DLT-2026-070)."""
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 0
    assert "Eidos Environment & Workspace Doctor" in res.stdout

    res_json = runner.invoke(app, ["doctor", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert "healthy" in data
    assert "checks" in data
    assert "cli_binary" in data["checks"]
    assert "knowledge_graph_index" in data["checks"]
    assert data["checks"]["living_specs"]["exists"] is True

    # Virtualenv execution when 'eidos' is not on global PATH still succeeds (healthy=True, on_path=False)
    monkeypatch.setattr("eidos.cli.doctor_hook_commands.shutil.which", lambda _cmd: None)
    res_venv = runner.invoke(app, ["doctor", "--json"])
    assert res_venv.exit_code == 0
    venv_data = json.loads(res_venv.stdout)
    assert venv_data["healthy"] is True
    assert venv_data["checks"]["cli_binary"]["ok"] is True
    assert venv_data["checks"]["cli_binary"]["on_path"] is False

    # Custom --path specification directory resolution inside a git repository
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    custom_specs = tmp_path / "custom_specs"
    custom_specs.mkdir(parents=True)
    (custom_specs / "system.adoc").write_text(
        """---
type: product/system
id: SYS-CUSTOM-001
title: Custom System
archetypes:
  - cli_tool
---
= Custom System
""",
        encoding="utf-8",
    )
    res_custom = runner.invoke(app, ["doctor", "--path", str(custom_specs), "--json"])
    assert res_custom.exit_code == 0
    custom_data = json.loads(res_custom.stdout)
    assert custom_data["healthy"] is True
    assert Path(custom_data["checks"]["living_specs"]["eidos_dir"]).resolve() == custom_specs.resolve()
    assert custom_data["checks"]["living_specs"]["valid_docs"] == 1


def test_hooks_are_cli_native():
    """Verify INV-HOOK-CLI-NATIVE: all hooks in hooks.json invoke 'eidos hook' directly with zero wrapper scripts."""
    ref_plugin = Path("assets/plugin_reference")
    assert not (ref_plugin / "scripts").exists(), "Legacy scripts/ wrapper directory must not exist"
    hooks_data = json.loads((ref_plugin / "hooks.json").read_text(encoding="utf-8"))

    commands: list[str] = []
    for _hook_name, events in hooks_data.items():
        if not isinstance(events, dict):
            continue
        for _event_name, entries in events.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if "hooks" in entry and isinstance(entry["hooks"], list):
                    for h in entry["hooks"]:
                        commands.append(h.get("command", ""))
                elif "command" in entry:
                    commands.append(entry["command"])

    assert commands == ["eidos hook"]
    for cmd in commands:
        assert cmd == "eidos hook" or cmd.startswith("eidos hook "), f"Expected native 'eidos hook' command, got: {cmd}"
        assert ".py" not in cmd and ".sh" not in cmd


def test_stop_hook_internal_error_emits_warning(tmp_path, monkeypatch):
    """Verify handle_stop_hook catches unexpected engine errors and returns a non-blocking systemMessage warning."""
    from eidos.plugin.hooks import handle_stop_hook

    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated internal engine failure")

    monkeypatch.setattr("eidos.plugin.hooks.detect_drift", _boom)

    res = handle_stop_hook({"executionNum": 1}, tmp_path)
    assert res["decision"] == "allow"
    assert "simulated internal engine failure" in res["systemMessage"]


def test_plugin_install_and_uninstall_preserves_custom_skills(tmp_path):
    """Verify install_plugin and uninstall_plugin manage the 4 active Eidos skills while preserving unrelated user skills (DLT-2026-037)."""
    from eidos.plugin.installer import ACTIVE_EIDOS_SKILLS

    tree_skills = tmp_path / ".agents" / "plugins" / "eidos" / "skills"
    flat_skills = tmp_path / ".cursor" / "skills"
    custom_user_skill = flat_skills / "custom-team-skill"
    custom_user_skill.mkdir(parents=True, exist_ok=True)
    (custom_user_skill / "SKILL.md").write_text("# Custom Team Skill\n", encoding="utf-8")

    # Run install_plugin across all targets (which powers both install and update)
    install_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path, force=True)

    # Active 4 skills must exist, and custom user skill must be preserved
    for active in ACTIVE_EIDOS_SKILLS:
        assert (tree_skills / active / "SKILL.md").exists()
        assert (flat_skills / active / "SKILL.md").exists()
    assert (custom_user_skill / "SKILL.md").exists()

    # Verify uninstall_plugin removes active Eidos skills while keeping custom skill
    uninstall_plugin(target=PluginInstallTarget.CURSOR, project_dir=tmp_path)
    for active in ACTIVE_EIDOS_SKILLS:
        assert not (flat_skills / active).exists()
    assert (custom_user_skill / "SKILL.md").exists()


def test_plugin_install_creates_manifest_with_hashes(tmp_path):
    """Verify that installing a plugin generates .eidos-manifest.json with SHA-256 hashes (DLT-2026-046)."""
    import hashlib

    from eidos.plugin.installer import MANIFEST_FILENAME

    # 1. Test custom destination
    dest = tmp_path / "custom_dest"
    results = install_plugin(dest_path=dest, force=True)
    assert len(results) == 1
    assert results[0].success

    manifest_file = dest / MANIFEST_FILENAME
    assert manifest_file.exists(), f"Expected manifest file at {manifest_file}"

    with open(manifest_file, encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert manifest_data["version"] == get_bundled_plugin_version()
    assert "files" in manifest_data
    files = manifest_data["files"]
    assert "plugin.json" in files
    assert "skills/eidos/SKILL.md" in files

    # Verify SHA-256 integrity
    for rel_path, digest in files.items():
        assert len(digest) == 64
        real_file = dest / rel_path
        assert real_file.exists()
        computed = hashlib.sha256(real_file.read_bytes()).hexdigest()
        assert computed == digest, f"Digest mismatch for {rel_path}"

    # 2. Test CODEX target manifest
    codex_root = tmp_path / "codex_project"
    codex_root.mkdir()
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=codex_root, force=True)
    codex_manifest_file = codex_root / ".codex" / MANIFEST_FILENAME
    assert codex_manifest_file.exists()
    with open(codex_manifest_file, encoding="utf-8") as f:
        codex_manifest = json.load(f)
    assert "plugin.json" in codex_manifest["files"]
    assert "rules.md" in codex_manifest["files"]
    assert "skills/eidos/SKILL.md" in codex_manifest["files"]

    # 3. Test COPILOT target manifest
    copilot_root = tmp_path / "copilot_project"
    copilot_root.mkdir()
    install_plugin(target=PluginInstallTarget.COPILOT, project_dir=copilot_root, force=True)
    copilot_manifest_file = copilot_root / ".github" / "plugin" / MANIFEST_FILENAME
    assert copilot_manifest_file.exists()
    with open(copilot_manifest_file, encoding="utf-8") as f:
        copilot_manifest = json.load(f)
    assert "plugin.json" in copilot_manifest["files"]


def test_plugin_uninstall_preserves_unmanifested_user_files(tmp_path):
    """Verify non-destructive uninstall removes only manifest-tracked files, preserving user files in .codex and --dest (DLT-2026-046)."""
    from eidos.plugin.installer import MANIFEST_FILENAME

    # 1. Non-destructive uninstall in .codex
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    user_file = codex_dir / "user_custom_config.json"
    user_file.write_text('{"user_custom": true}\n', encoding="utf-8")
    user_skill_file = codex_dir / "skills" / "my-team-skill" / "SKILL.md"
    user_skill_file.parent.mkdir(parents=True, exist_ok=True)
    user_skill_file.write_text("# My Team Skill\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert (codex_dir / MANIFEST_FILENAME).exists()
    assert (codex_dir / "rules.md").exists()
    assert user_file.exists()
    assert user_skill_file.exists()

    uninst = uninstall_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success

    # Manifest-tracked files removed
    assert not (codex_dir / MANIFEST_FILENAME).exists()
    assert not (codex_dir / "rules.md").exists()
    assert not (codex_dir / "plugin.json").exists()
    assert not (codex_dir / "skills" / "eidos").exists()

    # User-created files and parent directory preserved!
    assert codex_dir.exists(), ".codex directory should NOT be removed when user files exist"
    assert user_file.exists(), "User file must be preserved"
    assert user_file.read_text(encoding="utf-8") == '{"user_custom": true}\n'
    assert user_skill_file.exists(), "User skill must be preserved"

    # 2. Non-destructive uninstall in custom --dest
    custom_dest = tmp_path / "custom_dest_preserve"
    custom_dest.mkdir(parents=True, exist_ok=True)
    user_dest_file = custom_dest / "untracked_user_notes.txt"
    user_dest_file.write_text("keep this safe\n", encoding="utf-8")

    install_plugin(dest_path=custom_dest, force=True)
    assert (custom_dest / MANIFEST_FILENAME).exists()
    assert (custom_dest / "plugin.json").exists()
    assert user_dest_file.exists()

    uninst_dest = uninstall_plugin(dest_path=custom_dest)
    assert len(uninst_dest) == 1
    assert uninst_dest[0].success
    assert not (custom_dest / MANIFEST_FILENAME).exists()
    assert not (custom_dest / "plugin.json").exists()
    assert custom_dest.exists(), "Custom dest should NOT be removed when user files exist"
    assert user_dest_file.exists(), "User file in custom dest must be preserved"
    assert user_dest_file.read_text(encoding="utf-8") == "keep this safe\n"


def test_plugin_install_force_false_prevents_overwriting(tmp_path):
    """Verify force=False (default) prevents overwriting pre-existing user files without --force (DLT-2026-046)."""
    # 1. Pre-existing .codex/rules.md
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    rules_file = codex_dir / "rules.md"
    rules_file.write_text("# Custom User Rules\n", encoding="utf-8")

    # Install without force (default force=False)
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=False)
    assert rules_file.read_text(encoding="utf-8") == "# Custom User Rules\n", (
        "rules.md should NOT be overwritten when force=False"
    )

    # Install with force=True
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert "Eidos Architectural Governance" in rules_file.read_text(encoding="utf-8"), (
        "rules.md SHOULD be overwritten when force=True"
    )

    # 2. Pre-existing .aider.conventions.md
    conv_file = tmp_path / ".aider.conventions.md"
    conv_file.write_text("# Custom User Aider Conventions\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=False)
    assert conv_file.read_text(encoding="utf-8") == "# Custom User Aider Conventions\n", (
        ".aider.conventions.md should NOT be overwritten when force=False"
    )

    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    assert "Eidos Living Specifications & Architectural Conventions" in conv_file.read_text(encoding="utf-8")

    # 3. CLI default force=False
    cli_codex = tmp_path / "cli_test"
    cli_rules = cli_codex / ".codex" / "rules.md"
    cli_rules.parent.mkdir(parents=True, exist_ok=True)
    cli_rules.write_text("# User Rules CLI\n", encoding="utf-8")

    res = runner.invoke(app, ["plugin", "install", "codex", "-p", str(cli_codex)])
    assert res.exit_code == 0
    assert cli_rules.read_text(encoding="utf-8") == "# User Rules CLI\n"

    res_force = runner.invoke(app, ["plugin", "install", "codex", "-p", str(cli_codex), "--force"])
    assert res_force.exit_code == 0
    assert "Eidos Architectural Governance" in cli_rules.read_text(encoding="utf-8")


def test_plugin_reinstall_force_false_preserves_manifest_and_uninstalls_cleanly(tmp_path):
    """Verify install_plugin(..., force=False) on an already-installed target retains manifest entries so uninstall_plugin removes all Eidos files (DLT-2026-046)."""
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    assert (plugin_dir / "plugin.json").exists()
    assert (plugin_dir / "skills" / "eidos" / "SKILL.md").exists()

    # Re-run install with force=False
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=False)

    # Subsequent uninstall must still remove all manifest-tracked Eidos files and directory
    uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path)
    assert not plugin_dir.exists()


def test_plugin_install_force_false_and_uninstall_preserves_preexisting_user_files(tmp_path):
    """Verify pre-existing .codex/rules.md and user scripts/ are preserved across install(force=False) and uninstall() (DLT-2026-046)."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    rules_file = codex_dir / "rules.md"
    rules_file.write_text("# User Pre-existing Codex Rules\n", encoding="utf-8")

    install_plugin(project_dir=tmp_path, target=PluginInstallTarget.CODEX, force=False)
    assert rules_file.exists()
    assert rules_file.read_text(encoding="utf-8") == "# User Pre-existing Codex Rules\n"
    assert not (codex_dir / "hooks.json").exists(), "Codex target should not copy hooks.json"

    uninstall_plugin(project_dir=tmp_path, target=PluginInstallTarget.CODEX)
    assert rules_file.exists(), "Pre-existing user .codex/rules.md must still exist after uninstall!"
    assert rules_file.read_text(encoding="utf-8") == "# User Pre-existing Codex Rules\n"
    assert not (codex_dir / "plugin.json").exists()
    assert not (codex_dir / "skills").exists()

    # Also verify user scripts/ directory in custom dest is preserved when force=False
    custom_dest = tmp_path / "custom_with_scripts"
    user_script = custom_dest / "scripts" / "deploy.sh"
    user_script.parent.mkdir(parents=True, exist_ok=True)
    user_script.write_text("#!/bin/sh\necho deploy\n", encoding="utf-8")

    install_plugin(dest_path=custom_dest, force=False)
    assert user_script.exists(), "User scripts/ directory must not be deleted when force=False"


def test_plugin_install_refuses_symlink_destination(tmp_path):
    """Verify install_plugin refuses destination symlinks and parent symlinks to prevent symlink traversal (DLT-2026-046)."""
    # 1. Symlinked destination directory
    real_target_dir = tmp_path / "real_target"
    real_target_dir.mkdir()
    symlink_dir = tmp_path / "symlink_dest"
    symlink_dir.symlink_to(real_target_dir)

    with pytest.raises(FileExistsError, match="symlink"):
        install_plugin(dest_path=symlink_dir)

    # 2. Symlinked file inside target directory
    project_dir = tmp_path / "symlink_file_project"
    project_dir.mkdir()
    secret_file = tmp_path / "secret_outside.txt"
    secret_file.write_text("original secret content\n", encoding="utf-8")

    fake_agent_dir = project_dir / ".agents" / "plugins" / "eidos"
    fake_agent_dir.mkdir(parents=True, exist_ok=True)
    symlinked_manifest = fake_agent_dir / "plugin.json"
    symlinked_manifest.symlink_to(secret_file)

    with pytest.raises(FileExistsError, match="symlink"):
        install_plugin(target=PluginInstallTarget.PROJECT, project_dir=project_dir, force=True)

    # Verify secret_file was not overwritten
    assert secret_file.read_text(encoding="utf-8") == "original secret content\n"

    # 3. Symlinked intermediate parent directory (.agents -> outside dir)
    parent_sym_project = tmp_path / "symlink_parent_project"
    parent_sym_project.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (parent_sym_project / ".agents").symlink_to(outside_dir)

    with pytest.raises(FileExistsError, match="symlink"):
        install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=parent_sym_project, force=True)

    res_cli = runner.invoke(app, ["plugin", "install", "antigravity", "-p", str(parent_sym_project)])
    assert res_cli.exit_code == 1
    assert "✗ Error:" in res_cli.stdout


def test_plugin_uvx_hook_command_version_pinning(tmp_path):
    """Verify uvx hook command rewriting pins eidos-ai package version and upgrades older pinned versions (DLT-2026-046)."""
    from eidos.plugin.installer import _rewrite_hook_command_for_target_root

    bundled_ver = get_bundled_plugin_version()

    # 1. Direct function rewrite (bare, unpinned uvx, and older pinned uvx)
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"command": "eidos hook lint --event PreToolUse"}],
                    "PostToolUse": [{"command": "uvx --from eidos-ai eidos hook lint --event PostToolUse"}],
                    "Stop": [{"command": "uvx --from eidos-ai==0.1.0 eidos hook drift"}],
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    success = _rewrite_hook_command_for_target_root(hooks_file)
    assert success
    rewritten = hooks_file.read_text(encoding="utf-8")
    assert f'"command": "uvx --from eidos-ai=={bundled_ver} eidos hook lint --event PreToolUse"' in rewritten
    assert f'"command": "uvx --from eidos-ai=={bundled_ver} eidos hook lint --event PostToolUse"' in rewritten
    assert f'"command": "uvx --from eidos-ai=={bundled_ver} eidos hook drift"' in rewritten
    assert "eidos-ai==0.1.0" not in rewritten

    # 2. End-to-end install with launcher="uvx"
    dest = tmp_path / "installed_uvx"
    install_plugin(dest_path=dest, force=True, launcher="uvx")
    installed_hooks = dest / "hooks.json"
    if installed_hooks.exists():
        installed_text = installed_hooks.read_text(encoding="utf-8")
        assert f"uvx --from eidos-ai=={bundled_ver} eidos hook" in installed_text
        assert "uvx --from eidos-ai eidos hook" not in installed_text


def test_dlt_2026_075_non_code_drift_diagnostics_in_hooks_report_and_cli(tmp_path):
    """Verify DLT-2026-075: non-code invariant/validation failures in hooks, generate_remediation_prompt, and eidos drift."""  # eidos:fixture
    from eidos.plugin.hooks import execute_hook, handle_stop_hook
    from eidos.vcs.drift import detect_drift, generate_remediation_prompt

    eidos_dir = tmp_path / "eidos"
    pol_dir = eidos_dir / "policies"
    pol_dir.mkdir(parents=True)

    # Policy with an uncovered invariant test_binding and 0 modified code files
    (pol_dir / "pol-001.adoc").write_text(
        """---
type: product/policy
id: POL-001
title: Sample Policy
category: governance
severity: blocking
invariants:
  - id: INV-NONCODE-001
    rule: Bound test must exist
    test_binding: tests/test_missing.py::test_noncode
---
= Sample Policy
""",
        encoding="utf-8",
    )

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    drift_res = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir, uncommitted=True)
    assert drift_res.code_files == []
    assert drift_res.validation_passed is True
    assert len(drift_res.uncovered_invariants) == 1

    # 1. generate_remediation_prompt reports non-code invariant failure without claiming modified source code files
    prompt = generate_remediation_prompt(drift_res)
    assert "Non-Code Drift / Governance Verification Failures (0 code files modified):" in prompt
    assert "INV-NONCODE-001" in prompt

    # 2. handle_stop_hook emits systemMessage warning with cause-specific invariant message (not "0 code file(s) were modified")
    stop_res = handle_stop_hook({"executionNum": 1, "terminationReason": "model_stop"}, tmp_path)
    assert stop_res["decision"] == "allow"
    assert "0 code file(s) were modified" not in stop_res["systemMessage"]
    assert "Invariant test binding verification failed" in stop_res["systemMessage"]

    # 3. execute_hook exits 0 and emits JSON protocol output
    rc = execute_hook(path=tmp_path, stdin_data="")
    assert rc == 0


def test_dlt_2026_071_plugin_installer_and_validator_fixes(tmp_path):
    """Verify DLT-2026-071: no reconcile.md for Claude, Path-typed AIDER created_files, accurate fallback plugin_dir, and line-anchored SKILL.md frontmatter."""
    # 1. Claude install does not create reconcile.md and creates drift.md
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)
    assert not (tmp_path / ".claude" / "plugins" / "eidos" / "commands" / "reconcile.md").exists()
    assert (tmp_path / ".claude" / "plugins" / "eidos" / "commands" / "drift.md").exists()

    # 2. Aider created_files contains only Path instances (no bool)
    aider_res = install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    assert len(aider_res) == 1
    assert all(isinstance(p, Path) and not isinstance(p, bool) for p in aider_res[0].created_files)
    assert (tmp_path / ".aider.conf.yml") in aider_res[0].created_files

    # 3. Fallback version check reports actual matched plugin directory (.codex)
    codex_proj = tmp_path / "codex_only"
    codex_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=codex_proj, force=True)
    st = check_plugin_version_status(project_dir=codex_proj)
    assert st["is_installed"] is True
    assert Path(st["plugin_dir"]).resolve() == (codex_proj / ".codex").resolve()

    # 4. Line-anchored SKILL.md frontmatter allows '---' inside YAML scalar values
    custom_plugin = tmp_path / "custom_plugin"
    skill_dir = custom_plugin / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (custom_plugin / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "Demo plugin",
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        '---\nname: demo-skill\ndescription: "Handles --- separator in YAML scalar"\n---\n# Demo Skill\n',
        encoding="utf-8",
    )
    val_rep = validate_plugin_package(custom_plugin)
    assert val_rep.is_valid, f"Unexpected errors: {val_rep.errors}"
    assert "demo-skill" in val_rep.skills


def test_plugin_stop_hook_validation_uninstall_and_semver(tmp_path):
    """Verify stateless handle_stop_hook validation warnings, uninstall_plugin preservation, and compare_semver."""  # eidos:fixture
    from eidos.plugin.hooks import handle_stop_hook
    from eidos.plugin.version import compare_semver

    eidos_dir = tmp_path / "eidos"
    dom_dir = eidos_dir / "domains"
    dom_dir.mkdir(parents=True)

    # 1. Invalid domain spec triggers handle_stop_hook warning statelessly on every call without writing stop-gate.json
    bad_dom = dom_dir / "bad.adoc"
    bad_dom.write_text("---\ntype: product/domain\nid: BAD\n---\n= Bad\n", encoding="utf-8")

    lint1 = handle_stop_hook({}, tmp_path)
    assert lint1.get("decision") == "allow"
    assert "Specification validation failed" in lint1.get("systemMessage", "")
    lint2 = handle_stop_hook({}, tmp_path)
    assert lint2.get("decision") == "allow"
    assert "Specification validation failed" in lint2.get("systemMessage", "")
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()

    # 2. uninstall_plugin preserves coexisting user settings in .claude/settings.json, .vscode/settings.json, .aider.conf.yml
    ws = tmp_path / "uninstall_ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".vscode").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(
        json.dumps(
            {"theme": "dark", "hooks": {"CustomHook": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
        ),
        encoding="utf-8",
    )
    (ws / ".vscode" / "settings.json").write_text(
        json.dumps({"editor.tabSize": 2, "chat.promptFilesLocations": {"custom/prompts": True}}),
        encoding="utf-8",
    )
    (ws / ".aider.conf.yml").write_text("model: sonnet\nread:\n  - CONVENTIONS.md\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.ALL, project_dir=ws, force=True)
    uninstall_plugin(target=PluginInstallTarget.ALL, project_dir=ws)

    claude_after = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert claude_after.get("theme") == "dark"
    assert "CustomHook" in claude_after.get("hooks", {})

    vscode_after = json.loads((ws / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    assert vscode_after.get("editor.tabSize") == 2
    assert vscode_after.get("chat.promptFilesLocations", {}).get("custom/prompts") is True

    aider_after = (ws / ".aider.conf.yml").read_text(encoding="utf-8")
    assert "CONVENTIONS.md" in aider_after
    assert "AGENTS.md" not in aider_after

    # 3. compare_semver edge cases
    assert compare_semver("0.8.0", "0.7.9") == 1
    assert compare_semver("0.8.0-alpha.1", "0.8.0") == -1
    assert compare_semver("1.2.0", "1.2.0") == 0
    with pytest.raises(ValueError, match="Cannot compare invalid semver strings"):
        compare_semver("invalid", "0.1.0")


def test_plugin_validator_version_cli_hooks_and_installer_edge_cases(tmp_path, monkeypatch):
    """Cover remaining edge branches across plugin/validator.py, plugin/version.py, cli/plugin_commands.py,
    plugin/hooks.py, and plugin/installer.py."""  # eidos:fixture
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.plugin.hooks import (
        execute_hook,
        find_workspace_root,
        handle_stop_hook,
    )
    from eidos.plugin.installer import (
        _copy_plugin_tree,
        _ensure_claude_plugin_manifest,
        _remove_copilot_instructions,
        _remove_project_aider_conf,
        _uninstall_from_manifest,
        _write_copilot_instructions,
        locate_plugin_bundle,
    )
    from eidos.plugin.validator import _load_bundled_schema, validate_plugin_package
    from eidos.plugin.version import (
        check_plugin_version_status,
        compare_semver,
        get_bundled_plugin_version,
        get_installed_plugin_version,
    )

    # 1. plugin/version.py: prerelease comparisons, corrupt manifest, invalid semver status
    assert compare_semver("1.0.0-1", "1.0.0-alpha") == -1
    assert compare_semver("1.0.0-alpha", "1.0.0-1") == 1
    assert compare_semver("1.0.0-alpha", "1.0.0-beta") == -1
    assert compare_semver("1.0.0-alpha", "1.0.0-alpha.1") == -1
    assert compare_semver("1.0.0-alpha.1", "1.0.0-alpha") == 1

    bad_ver_ws = tmp_path / "bad_ver_ws"
    bad_ver_dir = bad_ver_ws / ".agents" / "plugins" / "eidos"
    bad_ver_dir.mkdir(parents=True)
    (bad_ver_dir / "plugin.json").write_text("{corrupt", encoding="utf-8")
    assert get_installed_plugin_version(project_dir=bad_ver_ws) is None
    (bad_ver_dir / "plugin.json").write_text('{"name": "eidos", "version": "not-semver"}', encoding="utf-8")
    st_inv = check_plugin_version_status(project_dir=bad_ver_ws)
    assert st_inv["status"] == "invalid"

    # 2. plugin/validator.py: missing dir, file instead of dir, missing/corrupt plugin.json, bad name/version,
    # mcp_config.json / mcp.json, skills edge cases, hooks.json edge cases
    with pytest.raises(FileNotFoundError):
        _load_bundled_schema("nonexistent_schema_999.json")

    assert not validate_plugin_package(tmp_path / "no_such_plugin").is_valid
    file_not_dir = tmp_path / "file.txt"
    file_not_dir.write_text("hi", encoding="utf-8")
    assert not validate_plugin_package(file_not_dir).is_valid

    pdir = tmp_path / "bad_plugin"
    pdir.mkdir()
    assert not validate_plugin_package(pdir).is_valid  # missing plugin.json

    (pdir / "plugin.json").write_text("{bad json", encoding="utf-8")
    assert not validate_plugin_package(pdir).is_valid

    # Bad name, bad version, extensions, invalid mcp_config.json, skills without SKILL.md / bad frontmatter, hooks.json errors
    (pdir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "BAD--NAME",
                "version": "not_semver",
                "description": "Desc",
                "extensions": {"com.example": {}},
            }
        ),
        encoding="utf-8",
    )
    (pdir / "mcp_config.json").write_text("{bad mcp config", encoding="utf-8")
    s1 = pdir / "skills" / "empty-skill"
    s2 = pdir / "skills" / "no-fm"
    s3 = pdir / "skills" / "non-dict-fm"
    s4 = pdir / "skills" / "missing-fields"
    for d in (s1, s2, s3, s4):
        d.mkdir(parents=True)
    (s2 / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")
    (s3 / "SKILL.md").write_text("---\n- list_item\n---\n# Body\n", encoding="utf-8")
    (s4 / "SKILL.md").write_text("---\nother: 1\n---\n# Body\n", encoding="utf-8")
    (pdir / "hooks.json").write_text(
        json.dumps(
            {
                "bad_hook": "not_a_dict",
                "eidos-drift-gate": {
                    "enabled": True,
                    "PostInvocation": [{"type": "command", "command": "eidos hook drift --event PostInvocation"}],
                },
                "main_hook": {
                    "enabled": True,
                    "UnknownEvent": "not_a_list",
                    "PreToolUse": [
                        "not_a_dict_handler",
                        {"hooks": [{"type": "command"}]},
                        {"type": "command", "command": "bash ./scripts/missing_script.sh"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    rep_bad = validate_plugin_package(pdir)
    assert not rep_bad.is_valid
    assert any("violates naming constraints" in e for e in rep_bad.errors)
    assert any("not a valid Semantic Version" in e for e in rep_bad.errors)
    assert any("Invalid JSON in 'mcp_config.json'" in e for e in rep_bad.errors)
    assert any("missing 'SKILL.md'" in w for w in rep_bad.warnings)
    assert any("missing required YAML frontmatter" in e for e in rep_bad.errors)
    assert any("frontmatter is not a dictionary" in e for e in rep_bad.errors)
    assert any("missing 'name' in frontmatter" in e for e in rep_bad.errors)
    assert any("non-existent script" in e for e in rep_bad.errors)
    assert any("event 'PostInvocation' is not permitted" in e for e in rep_bad.errors)

    # Also test mcp.json with stdio command containing spaces and non-dict hooks.json
    (pdir / "mcp_config.json").unlink()
    (pdir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"srv": {"type": "stdio", "command": "python -m my_server"}}}),
        encoding="utf-8",
    )
    (pdir / "hooks.json").write_text("[1, 2, 3]", encoding="utf-8")
    rep_mcp = validate_plugin_package(pdir)
    assert any("contains spaces" in w for w in rep_mcp.warnings)
    assert any("root must be a dictionary" in e for e in rep_mcp.errors)

    # 3. cli/plugin_commands.py: validate on bad plugin, info command, version command, update command
    cli_runner = CliRunner()
    res_val_fail = cli_runner.invoke(app, ["plugin", "validate", str(pdir)])
    assert res_val_fail.exit_code == 1
    res_info = cli_runner.invoke(app, ["plugin", "info", str(locate_plugin_bundle())])
    assert res_info.exit_code == 0
    res_ver = cli_runner.invoke(app, ["plugin", "version", "--project-dir", str(bad_ver_ws)])
    assert res_ver.exit_code == 0
    assert "Invalid SemVer" in res_ver.output

    # 4. plugin/hooks.py: find_workspace_root, handle_stop_hook, execute_hook
    assert find_workspace_root({"cwd": str(tmp_path)}) == tmp_path.resolve()
    assert handle_stop_hook({}, tmp_path / "missing_eidos_dir") == {"decision": "allow"}
    assert execute_hook(path=tmp_path / "missing_eidos_dir", stdin_data="") == 0

    # 5. plugin/installer.py helper edge cases: _write_copilot_instructions / _remove_copilot_instructions,
    # and _uninstall_from_manifest when .eidos-manifest.json is absent
    cop_file = tmp_path / "cop_test" / "copilot-instructions.md"
    _write_copilot_instructions(cop_file, force=True)
    _write_copilot_instructions(cop_file, force=True)  # updates existing block in-place
    assert _remove_copilot_instructions(cop_file) is True
    assert not cop_file.exists()

    no_manifest_dir = tmp_path / "no_manifest_dir"
    _copy_plugin_tree(locate_plugin_bundle(), no_manifest_dir, force=True)
    (no_manifest_dir / ".eidos-manifest.json").unlink(missing_ok=True)
    assert _uninstall_from_manifest(no_manifest_dir) is False

    # 6. Additional coverage for plugin/version.py, plugin/validator.py, cli/plugin_commands.py,
    #    plugin/hooks.py, and plugin/installer.py
    import os

    import eidos.core.workspace.assets as ws_assets
    import eidos.plugin.installer as inst_mod
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.plugin.version import _resolve_installed_plugin_manifest

    # 6a. plugin/version.py & plugin/installer.py: fallback when get_plugin_reference_dir raises OSError
    orig_get_ref = ws_assets.get_plugin_reference_dir
    orig_inst_get_ref = inst_mod.get_plugin_reference_dir
    orig_cwd = os.getcwd()
    try:

        def _raise_oserror():
            raise OSError("no asset ref")

        ws_assets.get_plugin_reference_dir = _raise_oserror
        inst_mod.get_plugin_reference_dir = _raise_oserror
        ver_fb = get_bundled_plugin_version()
        assert isinstance(ver_fb, str) and len(ver_fb) > 0

        # locate_plugin_bundle CWD fallback (assets/plugin_reference/plugin.json) and FileNotFoundError
        cwd_bundle_ws = tmp_path / "cwd_bundle_ws"
        (cwd_bundle_ws / "assets" / "plugin_reference").mkdir(parents=True)
        (cwd_bundle_ws / "assets" / "plugin_reference" / "plugin.json").write_text(
            json.dumps({"name": "eidos", "version": "0.1.0"}), encoding="utf-8"
        )
        os.chdir(cwd_bundle_ws)
        assert locate_plugin_bundle() == (cwd_bundle_ws / "assets" / "plugin_reference").resolve()

        empty_cwd = tmp_path / "empty_cwd_ws"
        empty_cwd.mkdir(parents=True)
        os.chdir(empty_cwd)
        with pytest.raises(FileNotFoundError, match="Could not locate Eidos plugin"):
            locate_plugin_bundle()
    finally:
        ws_assets.get_plugin_reference_dir = orig_get_ref
        inst_mod.get_plugin_reference_dir = orig_inst_get_ref
        os.chdir(orig_cwd)

    assert _resolve_installed_plugin_manifest(project_dir=tmp_path, dest_path=Path("rel_dest")) == (
        (tmp_path / "rel_dest").resolve(),
        None,
    )

    # 6b. plugin/validator.py: empty plugin.json {}, valid mcp_config.json, invalid mcp.json, invalid hooks.json
    pdir2 = tmp_path / "pdir2"
    pdir2.mkdir()
    (pdir2 / "plugin.json").write_text("{}", encoding="utf-8")
    (pdir2 / "mcp_config.json").write_text(json.dumps({"mcpServers": {"eidos": {}}}), encoding="utf-8")
    (pdir2 / "hooks.json").write_text("{invalid_hooks", encoding="utf-8")
    rep_p2 = validate_plugin_package(pdir2)
    assert not rep_p2.is_valid
    assert any("missing required 'name'" in e for e in rep_p2.errors)
    assert any("missing required 'version'" in e for e in rep_p2.errors)
    assert any("Invalid JSON in 'hooks.json'" in e for e in rep_p2.errors)
    assert "eidos" in rep_p2.mcp_servers

    (pdir2 / "mcp_config.json").unlink()
    (pdir2 / "mcp.json").write_text("{invalid_mcp", encoding="utf-8")
    rep_p2_mcp = validate_plugin_package(pdir2)
    assert any("Invalid JSON in 'mcp.json'" in e for e in rep_p2_mcp.errors)

    # 6c. cli/plugin_commands.py: plugin update twice (prev_ver == current_ver), symlink error on install,
    #     and FileNotFoundError on validate/info when locate_plugin_bundle fails
    upd_ws = tmp_path / "upd_ws"
    upd_ws.mkdir()
    res_upd1 = cli_runner.invoke(app, ["plugin", "update", "--project-dir", str(upd_ws)])
    assert res_upd1.exit_code == 0
    res_upd2 = cli_runner.invoke(app, ["plugin", "update", "--project-dir", str(upd_ws)])
    assert res_upd2.exit_code == 0 and "already at latest version" in res_upd2.output

    sym_target = tmp_path / "sym_target_dir"
    sym_target.mkdir()
    sym_link = tmp_path / "sym_link_dest"
    sym_link.symlink_to(sym_target)
    res_sym = cli_runner.invoke(app, ["plugin", "install", "--dest", str(sym_link)])
    assert res_sym.exit_code == 1

    import eidos.cli.plugin_commands as pcmd_mod

    orig_pcmd_locate = pcmd_mod.locate_plugin_bundle
    try:

        def _missing_bundle():
            raise FileNotFoundError("no bundle")

        pcmd_mod.locate_plugin_bundle = _missing_bundle
        assert cli_runner.invoke(app, ["plugin", "validate"]).exit_code == 0
        assert cli_runner.invoke(app, ["plugin", "info"]).exit_code == 0
    finally:
        pcmd_mod.locate_plugin_bundle = orig_pcmd_locate

    # 6d. plugin/hooks.py: execute_hook on clean & invalid spec repos, and on git repo with drift
    hook_ws = tmp_path / "hook_exec_ws"
    init_repository(hook_ws, name="hook-exec", title="Hook Exec")
    assert execute_hook(path=hook_ws, stdin_data="") == 0
    (hook_ws / "eidos" / "domains" / "bad.adoc").write_text(
        "---\ntype: product/domain\nid: BAD_ID\n---\n= Bad\n", encoding="utf-8"
    )
    assert execute_hook(path=hook_ws, stdin_data="") == 0
    (hook_ws / "eidos" / "domains" / "bad.adoc").unlink()

    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=hook_ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=hook_ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=hook_ws, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=hook_ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "clean init"], cwd=hook_ws, check=True, capture_output=True)
    assert execute_hook(path=hook_ws, stdin_data="") == 0

    # 6e. plugin/installer.py: _remove_project_aider_conf / _ensure_claude_plugin_manifest branches
    c_dir = tmp_path / "claude_manifest_edge"
    c_dir.mkdir()
    (c_dir / "plugin.json").write_text(json.dumps({"name": "not-eidos"}), encoding="utf-8")
    assert _ensure_claude_plugin_manifest(c_dir, force=False) is None
    (c_dir / "plugin.json").write_text("{bad", encoding="utf-8")
    assert _ensure_claude_plugin_manifest(c_dir, force=False) is None
    (c_dir / "plugin.json").write_text(json.dumps({"name": "eidos", "version": "0.1.0"}), encoding="utf-8")
    (c_dir / ".claude-plugin").mkdir(exist_ok=True)
    (c_dir / ".claude-plugin" / "plugin.json").write_text("{bad", encoding="utf-8")
    assert _ensure_claude_plugin_manifest(c_dir, force=True) is not None

    # _remove_project_aider_conf with missing file, invalid YAML, non-dict YAML, and remaining key
    aider_f = tmp_path / ".aider.conf.yml"
    assert _remove_project_aider_conf(aider_f) is False
    aider_f.write_text(": [broken", encoding="utf-8")
    assert _remove_project_aider_conf(aider_f) is False
    aider_f.write_text("- item1\n", encoding="utf-8")
    assert _remove_project_aider_conf(aider_f) is False
    aider_f.write_text("read:\n  - .aider.conventions.md\nmodel: sonnet\n", encoding="utf-8")
    assert _remove_project_aider_conf(aider_f) is True
    assert aider_f.exists() and "model: sonnet" in aider_f.read_text(encoding="utf-8")

    # 6f. Newer installed version in `plugin version` & `plugin update`, execute_hook with drift,
    #     and corrupt manifest in _copy_plugin_tree
    (bad_ver_dir / "plugin.json").write_text('{"name": "eidos", "version": "99.0.0"}', encoding="utf-8")
    res_newer = cli_runner.invoke(app, ["plugin", "version", "--project-dir", str(bad_ver_ws)])
    assert res_newer.exit_code == 0 and "Newer" in res_newer.output
    res_upd_newer = cli_runner.invoke(app, ["plugin", "update", "--project-dir", str(bad_ver_ws)])
    assert res_upd_newer.exit_code == 0

    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    cnt_hook = create_spec(hook_ws / "eidos", "container", "CNT-HOOK", "Hook Svc")
    cnt_hook_doc = parse_spec_file(cnt_hook)
    cnt_hook_doc.frontmatter["source_path"] = "src"
    cnt_hook.write_text(dump_spec_document(cnt_hook_doc.frontmatter, cnt_hook_doc.body), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=hook_ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add container"], cwd=hook_ws, check=True, capture_output=True)
    (hook_ws / "src").mkdir(exist_ok=True)
    (hook_ws / "src" / "drifted.py").write_text("def drifted(): return 1\n", encoding="utf-8")
    assert execute_hook(path=hook_ws / "eidos", stdin_data='{"cwd": "' + str(hook_ws) + '"}') == 0

    tree_cleanup_dir = tmp_path / "tree_cleanup_dir"
    tree_cleanup_dir.mkdir(parents=True, exist_ok=True)
    (tree_cleanup_dir / ".eidos-manifest.json").write_text("{corrupt", encoding="utf-8")
    _copy_plugin_tree(locate_plugin_bundle(), tree_cleanup_dir, force=True)
    assert (tree_cleanup_dir / "plugin.json").exists()


def test_target_adapter_manifest_includes_post_install_hook_files(tmp_path):
    """Verify Bug 16: TargetAdapter.install writes .eidos-manifest.json after post_install_hook and skills_subdir copies."""
    from eidos.plugin.installer import MANIFEST_FILENAME, PluginInstallTarget, TargetAdapter, locate_plugin_bundle

    def _custom_post_hook(root: Path, _source_dir: Path, _force: bool, _launcher: str) -> list[Path]:
        extra_file = root / ".agents" / "plugins" / "eidos" / "post_hook_generated.json"
        extra_file.parent.mkdir(parents=True, exist_ok=True)
        extra_file.write_text('{"generated_by": "post_install_hook"}\n', encoding="utf-8")
        return [extra_file]

    adapter = TargetAdapter(
        target=PluginInstallTarget.PROJECT,
        name="custom-project",
        plugin_subdir=".agents/plugins/eidos",
        post_install_hook=_custom_post_hook,
    )
    res = adapter.install(tmp_path, locate_plugin_bundle(), force=True)
    assert res.success is True

    manifest_path = tmp_path / ".agents" / "plugins" / "eidos" / MANIFEST_FILENAME
    assert manifest_path.exists()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "post_hook_generated.json" in manifest_data["files"]


def test_hook_schema_validation_and_non_duplicate_skills(tmp_path):
    """Verify stateless handle_stop_hook validation and non-duplicate skills across targets (CLAUDE, CLINE, OPEN_CODE, COPILOT)."""
    from eidos.plugin.hooks import handle_stop_hook

    eidos_dir = tmp_path / "eidos"
    dom_dir = eidos_dir / "domains"
    dom_dir.mkdir(parents=True)
    bad1 = dom_dir / "bad1.adoc"
    bad2 = dom_dir / "bad2.adoc"
    bad1.write_text("---\ntype: product/domain\nid: BAD-1\n---\n= Bad 1\n", encoding="utf-8")
    bad2.write_text("---\ntype: product/domain\nid: BAD-2\n---\n= Bad 2\n", encoding="utf-8")

    stop1 = handle_stop_hook({"session_id": "s1"}, tmp_path)
    assert stop1["decision"] == "allow"
    assert "Specification validation failed" in stop1.get("systemMessage", "")

    bad1.unlink()
    bad2.unlink()
    good_dom = dom_dir / "good.adoc"
    good_dom.write_text(
        "---\ntype: product/domain\nid: DOM-GOOD\ntitle: Good Domain\nclassification: core\n---\n= Good Domain\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    clean_stop = handle_stop_hook({"session_id": "s2"}, tmp_path)
    assert clean_stop == {"decision": "allow"}

    # Targets with flat skills_subdir do not copy duplicate skills into plugin_subdir/skills
    inst_root = tmp_path / "no_dup_skills"
    inst_root.mkdir()
    install_plugin(target=PluginInstallTarget.ALL, project_dir=inst_root, force=True)
    for p_sub, s_sub in (
        (".claude/plugins/eidos", ".claude/skills"),
        (".cline/plugins/eidos", ".cline/skills"),
        (".opencode/plugins/eidos", ".opencode/skills"),
        (".github/plugin", ".github/skills"),
    ):
        assert not (inst_root / p_sub / "skills").exists(), f"Unexpected duplicate skills in {p_sub}/skills"
        assert (inst_root / s_sub / "eidos" / "SKILL.md").exists(), f"Missing flat skill in {s_sub}"


def test_group_d1_d2_d3_regressions(tmp_path, monkeypatch):  # eidos:fixture
    """Regression suite for Group D1 (doctor/hook CLI), D2 (CLI path & output bugs), and D3 (installer & hooks)."""  # eidos:fixture
    # --- D1.1: eidos doctor exits 1 when agent_plugin check is not ok (e.g. outdated) ---
    monkeypatch.setattr(
        "eidos.cli.doctor_hook_commands.check_plugin_version_status",
        lambda project_dir=None: {
            "status": "outdated",
            "installed_version": "0.0.1",
            "bundled_version": "0.8.2",
        },
    )
    res_doc = runner.invoke(app, ["doctor", "--json"])
    assert res_doc.exit_code == 1
    doc_payload = json.loads(res_doc.stdout)
    assert doc_payload["healthy"] is False
    assert doc_payload["checks"]["agent_plugin"]["ok"] is False

    # --- D1.2: eidos hook --path runs JSON hook protocol (exit code 0) and emits systemMessage warning on failure ---
    broken_ws = tmp_path / "broken_hook_ws"
    broken_eidos = broken_ws / "eidos" / "domains"
    broken_eidos.mkdir(parents=True)
    (broken_eidos / "invalid.adoc").write_text(
        "---\ntype: product/domain\nid: BAD\n---\n= Invalid\n",
        encoding="utf-8",
    )
    res_hook_broken = runner.invoke(app, ["hook", "--path", str(broken_ws)])
    assert res_hook_broken.exit_code == 0
    broken_out = json.loads(res_hook_broken.stdout)
    assert broken_out["decision"] == "allow"
    assert "Specification validation failed" in broken_out["systemMessage"]

    res_hook_missing = runner.invoke(app, ["hook", "--path", str(broken_ws / "nonexistent")])
    assert res_hook_missing.exit_code == 0
    assert json.loads(res_hook_missing.stdout) == {"decision": "allow"}

    # --- D2.1 & D2.2: drift --path . and subdirectory auto-discovery for validate, drift, and context ---
    repo_ws = tmp_path / "sub_ws"
    repo_ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_ws, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=repo_ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_ws, check=True)
    assert runner.invoke(app, ["init", str(repo_ws / "eidos"), "--name", "Sub Test"]).exit_code == 0
    sub_dir = repo_ws / "src" / "pkg"
    sub_dir.mkdir(parents=True)
    (sub_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo_ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo_ws, check=True)

    # Running drift -u --path <repo_root> resolves repo_root/eidos properly
    res_drift_dot = runner.invoke(app, ["drift", "-u", "--path", str(repo_ws), "--json"])
    assert res_drift_dot.exit_code == 0
    assert json.loads(res_drift_dot.stdout)["is_clean"] is True

    # Running validate, drift, and context from a subdirectory without --path discovers repo_root/eidos
    monkeypatch.chdir(sub_dir)
    assert runner.invoke(app, ["validate", "--json"]).exit_code == 0
    assert runner.invoke(app, ["drift", "-u", "--json"]).exit_code == 0
    assert runner.invoke(app, ["context", "--json"]).exit_code == 0

    # --- D2.3: validate and context in an uninitialized directory exit 1 and do not create .cache/index.db ---
    uninit_dir = tmp_path / "uninitialized_plain_dir"
    uninit_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=uninit_dir, check=True)
    monkeypatch.chdir(uninit_dir)
    assert runner.invoke(app, ["validate"]).exit_code == 1
    assert runner.invoke(app, ["context"]).exit_code == 1
    assert not (uninit_dir / ".cache").exists()

    # --- D2.4 & D2.5: spec create auto prints resolved ID; delta status shows In Review and Approved in-flight deltas ---
    eidos_dir = repo_ws / "eidos"
    res_auto = runner.invoke(
        app,
        ["spec", "create", "domain", "auto", "--title", "Payments Core", "--path", str(eidos_dir)],
    )
    assert res_auto.exit_code == 0
    assert "(DOM-AUTO)" in res_auto.stdout
    assert "(auto)" not in res_auto.stdout

    assert (
        runner.invoke(
            app, ["delta", "create", "DLT-2026-801", "--title", "Review Work", "--path", str(eidos_dir)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["delta", "create", "DLT-2026-802", "--title", "Approved Work", "--path", str(eidos_dir)]
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["delta", "status", "DLT-2026-801", "in_review", "--path", str(eidos_dir)]).exit_code == 0
    assert runner.invoke(app, ["delta", "status", "DLT-2026-802", "approved", "--path", str(eidos_dir)]).exit_code == 0

    res_dash = runner.invoke(app, ["delta", "status", "--path", str(eidos_dir)])
    assert res_dash.exit_code == 0
    assert "In Review" in res_dash.stdout
    assert "Approved" in res_dash.stdout
    assert "DLT-2026-801" in res_dash.stdout
    assert "DLT-2026-802" in res_dash.stdout

    # --- D3.1: _write_copilot_instructions appends Eidos block to existing user copilot-instructions.md when force=False ---
    copilot_ws = tmp_path / "copilot_ws"
    (copilot_ws / ".github").mkdir(parents=True)
    copilot_file = copilot_ws / ".github" / "copilot-instructions.md"
    copilot_file.write_text("# Team Custom Instructions\nAlways write tests.\n", encoding="utf-8")
    install_plugin(target=PluginInstallTarget.COPILOT, project_dir=copilot_ws, force=False)
    merged_copilot = copilot_file.read_text(encoding="utf-8")
    assert "# Team Custom Instructions" in merged_copilot
    assert "<!-- EIDOS_COPILOT_START -->" in merged_copilot

    # --- D3.2: Re-running install_plugin(force=False) preserves manifest entries so uninstall removes plugin files ---
    reinstall_ws = tmp_path / "reinstall_ws"
    reinstall_ws.mkdir()
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=reinstall_ws, force=False)
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=reinstall_ws, force=False)
    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=reinstall_ws)
    assert not (reinstall_ws / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (reinstall_ws / ".claude" / "plugins" / "eidos" / "plugin.json").exists()

    # --- D3.3: find_workspace_root honours payload["cwd"] ---
    from eidos.plugin.hooks import find_workspace_root

    monkeypatch.chdir(uninit_dir)
    assert find_workspace_root({"cwd": str(repo_ws)}) == repo_ws.resolve()
