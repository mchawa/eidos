"""Tests verifying Agent Plugin conformance to agent-plugins.org v1.0.0 and project-scoped CLI installer."""

import subprocess
from pathlib import Path

import orjson
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
    results = install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)

    assert len(results) == 1
    assert results[0].target == "antigravity"

    # Check .agents/plugins/eidos
    agent_dir = tmp_path / ".agents" / "plugins" / "eidos"
    assert (agent_dir / "plugin.json").exists()
    assert not (agent_dir / "mcp_config.json").exists()
    assert not (agent_dir / "mcp.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    agent_report = validate_plugin_package(agent_dir)
    assert agent_report.is_valid

    # Isolated antigravity target must not touch .cursor or .claude
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".claude").exists()


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
    """Verify 'eidos plugin install <target> --project-dir' CLI command requires an explicit target."""
    res_missing = runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    assert res_missing.exit_code != 0

    res = runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()


def test_plugin_uninstall_project_scope(tmp_path):
    """Verify uninstalling plugin from project scope removes .agents/plugins/eidos without deleting .cursor or .claude."""
    # Install first
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    cursor_rules = tmp_path / ".cursor" / "rules" / "eidos.mdc"
    cursor_rules.parent.mkdir(parents=True, exist_ok=True)
    cursor_rules.write_text("# Pre-existing Cursor rule\n", encoding="utf-8")

    assert plugin_dir.exists()
    assert cursor_rules.exists()

    # Uninstall antigravity target only
    results = uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path)
    assert len(results) == 1
    assert results[0].success

    assert not plugin_dir.exists()
    assert cursor_rules.exists()


def test_cli_plugin_uninstall_project(tmp_path):
    """Verify 'eidos plugin uninstall <target> --project-dir' CLI command requires an explicit target."""
    res_missing = runner.invoke(app, ["plugin", "uninstall", "--project-dir", str(tmp_path)])
    assert res_missing.exit_code != 0

    # Install
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "plugins" / "eidos").exists()

    # Uninstall
    res = runner.invoke(app, ["plugin", "uninstall", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".agents" / "plugins" / "eidos").exists()


def test_cli_plugin_remove_alias(tmp_path):
    """Verify 'eidos plugin remove' alias is removed in favor of 'eidos plugin uninstall'."""
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "plugins" / "eidos").exists()

    res_removed = runner.invoke(app, ["plugin", "remove", "antigravity", "--project-dir", str(tmp_path)])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["plugin", "uninstall", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".agents" / "plugins" / "eidos").exists()


def test_plugin_install_to_claude_plugin(tmp_path):
    """Verify installing Claude Code integration into project-native .claude/skills/ and .claude/settings.json."""
    # Seed legacy .claude/plugins/eidos directory (with untracked commands/) and legacy keys in .claude/settings.json
    legacy_dir = tmp_path / ".claude" / "plugins" / "eidos"
    (legacy_dir / "commands").mkdir(parents=True)
    (legacy_dir / "plugin.json").write_text('{"name": "eidos", "version": "1.0.0"}', encoding="utf-8")
    (legacy_dir / "commands" / "validate.md").write_text("# Legacy validate\n", encoding="utf-8")
    settings_file = tmp_path / ".claude" / "settings.json"
    settings_file.write_bytes(
        orjson.dumps(
            {
                "plugins": [{"path": "./.claude/plugins/eidos"}],
                "enabledPlugins": {"eidos": True, "eidos@plugins-dir": True, "custom-plugin": True},
            }
        )
    )

    results = install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)

    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "claude"

    claude_plugin_dir = tmp_path / ".claude" / "plugins" / "eidos"
    assert not claude_plugin_dir.exists()
    assert not (tmp_path / ".claude" / "plugins").exists()
    assert not (tmp_path / ".claude" / "commands").exists()

    # Direct flat skills in .claude/skills/
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-spec" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-plan" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-ops" / "SKILL.md").exists()
    assert len([d for d in (tmp_path / ".claude" / "skills").iterdir() if d.is_dir()]) == 4

    # Verify .claude/.eidos-manifest.json tracks skills but NOT settings.json
    manifest_file = tmp_path / ".claude" / ".eidos-manifest.json"
    assert manifest_file.exists()
    manifest = orjson.loads(manifest_file.read_bytes())
    assert manifest["version"] == get_bundled_plugin_version()
    assert "settings.json" not in manifest["files"]
    assert "skills/eidos/SKILL.md" in manifest["files"]

    # Verify settings.json registers Stop hook and cleans legacy Eidos plugin keys while preserving user keys
    assert settings_file.exists()
    settings = orjson.loads(settings_file.read_bytes())
    assert "plugins" not in settings
    assert settings.get("enabledPlugins") == {"custom-plugin": True}
    stop_hooks = settings.get("hooks", {}).get("Stop", [])
    assert len(stop_hooks) == 1
    assert stop_hooks[0] == {"hooks": [{"type": "command", "command": "eidos hook", "timeout": 30}]}

    # Re-installing with launcher="uvx" updates the Stop hook in-place without duplicating entries
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, launcher="uvx")
    settings_uvx = orjson.loads(settings_file.read_bytes())
    stop_hooks_uvx = settings_uvx.get("hooks", {}).get("Stop", [])
    assert len(stop_hooks_uvx) == 1
    assert stop_hooks_uvx[0]["hooks"][0]["command"] == f"uvx --from eidos-ai=={get_bundled_plugin_version()} eidos hook"

    # Verify project-level .claude/mcp.json is NOT created
    claude_mcp = tmp_path / ".claude" / "mcp.json"
    assert not claude_mcp.exists()

    # Verify get_installed_plugin_version detects Claude installation via .claude/.eidos-manifest.json
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()


def test_plugin_uninstall_claude_plugin(tmp_path):
    """Verify uninstalling Claude Code cleans .claude/skills/ and .claude/settings.json."""
    # Install first
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)
    settings_file = tmp_path / ".claude" / "settings.json"
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert settings_file.exists()

    # Uninstall when no custom user settings exist -> removes .claude completely
    results = uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path)
    assert len(results) == 1
    assert results[0].success

    assert not (tmp_path / ".claude" / "skills" / "eidos-spec").exists()
    assert not settings_file.exists()
    assert not (tmp_path / ".claude").exists()

    # Install again with a coexisting user Stop sub-hook in the same matcher block
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)
    settings_data = orjson.loads(settings_file.read_bytes())
    settings_data["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "echo user-stop"})
    settings_file.write_bytes(orjson.dumps(settings_data))

    # Re-installing updates Eidos Stop hook in-place without dropping the coexisting user Stop sub-hook
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, launcher="uvx")
    reinstalled_data = orjson.loads(settings_file.read_bytes())
    assert len(reinstalled_data["hooks"]["Stop"][0]["hooks"]) == 2
    assert any(h.get("command") == "echo user-stop" for h in reinstalled_data["hooks"]["Stop"][0]["hooks"])

    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path)
    assert settings_file.exists()
    preserved = orjson.loads(settings_file.read_bytes())
    assert preserved["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo user-stop"}]}]


def test_cli_install_and_uninstall_shortcut_claude(tmp_path):
    """Verify 'eidos plugin install claude' and 'eidos plugin uninstall claude' work and root shortcuts are removed."""
    # Root shortcuts are removed
    assert runner.invoke(app, ["install", "claude", "--project-dir", str(tmp_path)]).exit_code != 0
    assert runner.invoke(app, ["uninstall", "claude", "--project-dir", str(tmp_path)]).exit_code != 0

    # Install with positional argument under 'plugin'
    res = runner.invoke(app, ["plugin", "install", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".claude" / "plugins" / "eidos").exists()

    # Uninstall with positional argument under 'plugin'
    res = runner.invoke(app, ["plugin", "uninstall", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".claude" / "skills" / "eidos").exists()
    assert not (tmp_path / ".claude").exists()


def test_cli_install_rejects_workspace_target(tmp_path):
    """Verify 'workspace' is rejected as target following strict agent-plugins standard."""
    res = runner.invoke(app, ["plugin", "install", "workspace", "--project-dir", str(tmp_path)])
    assert res.exit_code != 0
    # Output should contain error indication about invalid choice
    assert "Invalid value" in res.output or "invalid" in res.output.lower()


def test_plugin_install_and_uninstall_aider(tmp_path):
    """Verify Aider installation installs skills, the root AGENTS.md block, and AGENTS.md under read: (DLT-2026-121)."""
    results = install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "aider"

    aider_conf = tmp_path / ".aider.conf.yml"
    aider_skills = tmp_path / ".aider" / "skills"
    agents_md = tmp_path / "AGENTS.md"
    assert not (tmp_path / ".aider.conventions.md").exists()
    assert aider_conf.exists()
    assert "AGENTS.md" in aider_conf.read_text(encoding="utf-8")
    assert (aider_skills / "eidos" / "SKILL.md").exists()
    assert len([d for d in aider_skills.iterdir() if d.is_dir()]) == 4
    agents_text = agents_md.read_text(encoding="utf-8")
    assert "targets=aider:.aider/skills" in agents_text
    assert "`.aider/skills/<skill>/SKILL.md`" in agents_text

    # Uninstall removes skills only; shared AGENTS.md / .aider.conf.yml are intentionally left in place.
    uninst = uninstall_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not (aider_skills / "eidos").exists()
    assert agents_md.read_text(encoding="utf-8") == agents_text
    assert aider_conf.exists()


def test_plugin_install_and_uninstall_cline(tmp_path):
    """Verify Cline installation creates shared .agents/skills and root AGENTS.md block without .cline/ or .clinerules/ hooks."""
    results = install_plugin(target=PluginInstallTarget.CLINE, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "cline"
    assert results[0].destination == tmp_path / ".agents" / "skills"

    assert not (tmp_path / ".clinerules").exists()
    assert not (tmp_path / ".cline").exists()
    assert not (tmp_path / ".cline" / "plugins").exists()
    assert not (tmp_path / ".cline" / "skills").exists()
    skills_dir = tmp_path / ".agents" / "skills"
    assert (skills_dir / "eidos" / "SKILL.md").exists()
    assert (skills_dir / "eidos-spec" / "SKILL.md").exists()
    assert (skills_dir / "eidos-plan" / "SKILL.md").exists()
    assert (skills_dir / "eidos-ops" / "SKILL.md").exists()
    assert len([d for d in skills_dir.iterdir() if d.is_dir()]) == 4
    assert "targets=cline:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Uninstall removes .agents/skills and prunes empty .agents/
    uninst = uninstall_plugin(target=PluginInstallTarget.CLINE, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not (skills_dir / "eidos").exists()
    assert not (tmp_path / ".agents").exists()


def test_plugin_install_and_uninstall_opencode(tmp_path):
    """Verify OpenCode installation creates shared .agents/skills and root AGENTS.md block without .opencode/."""
    results = install_plugin(target=PluginInstallTarget.OPEN_CODE, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "open-code"
    assert results[0].destination == tmp_path / ".agents" / "skills"

    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / ".opencode").exists()
    assert not (tmp_path / ".opencode" / "plugins").exists()
    assert not (tmp_path / ".opencode" / "skills").exists()
    skills_dir = tmp_path / ".agents" / "skills"
    assert (skills_dir / "eidos" / "SKILL.md").exists()
    assert len([d for d in skills_dir.iterdir() if d.is_dir()]) == 4
    assert "targets=open-code:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.OPEN_CODE, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not (skills_dir / "eidos").exists()
    assert not (tmp_path / ".agents").exists()


def test_plugin_install_and_uninstall_codex(tmp_path):
    """Verify OpenAI Codex installation creates canonical .agents/plugins/eidos bundle and the root AGENTS.md block."""
    results = install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "codex"

    assert not (tmp_path / ".codex" / "plugin.json").exists()
    assert not (tmp_path / ".codex" / "skills").exists()
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    assert (plugin_dir / "plugin.json").exists()
    assert (plugin_dir / "hooks.json").exists()
    assert (plugin_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert "targets=codex:.agents/plugins/eidos/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert len([d for d in (plugin_dir / "skills").iterdir() if d.is_dir()]) == 4

    # Verify version detection
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not plugin_dir.exists()


def test_plugin_install_and_uninstall_copilot(tmp_path):
    """Verify GitHub Copilot installation creates canonical .agents/plugins/eidos bundle and the root AGENTS.md block."""
    results = install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "copilot"

    assert not (tmp_path / ".github" / "plugin").exists()
    assert not (tmp_path / ".github" / "skills").exists()
    assert not (tmp_path / ".github" / "copilot").exists()
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    assert (plugin_dir / "plugin.json").exists()
    assert (plugin_dir / "hooks.json").exists()
    assert (plugin_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert len([d for d in (plugin_dir / "skills").iterdir() if d.is_dir()]) == 4

    # Copilot loads root AGENTS.md natively: no copilot-instructions.md bridge (DLT-2026-121)
    instructions = tmp_path / ".github" / "copilot-instructions.md"
    assert not instructions.exists()
    assert "targets=copilot:.agents/plugins/eidos/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Verify get_installed_plugin_version detects Copilot plugin
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not plugin_dir.exists()

    # A user-authored copilot-instructions.md is never touched by install or uninstall
    custom_header = "# Team Instructions\nFollow our team rules.\n"
    instructions.parent.mkdir(parents=True, exist_ok=True)
    instructions.write_text(custom_header, encoding="utf-8")
    install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True)
    assert instructions.read_text(encoding="utf-8") == custom_header
    uninstall_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path)
    assert instructions.read_text(encoding="utf-8") == custom_header


def test_plugin_install_and_uninstall_all_targets(tmp_path):
    """Verify installing target 'all' configures all 9 AI agent environments."""
    results = install_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path, force=True)
    targets = [r.target for r in results]
    assert len(targets) == 9
    assert "antigravity" in targets
    assert "cursor" in targets
    assert "claude" in targets
    assert "aider" in targets
    assert "cline" in targets
    assert "kilocode" in targets
    assert "open-code" in targets
    assert "codex" in targets
    assert "copilot" in targets

    # Uninstall all
    uninst = uninstall_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path)
    assert len(uninst) == 9
    for u in uninst:
        assert u.success


def test_cli_install_new_agent_shortcuts(tmp_path):
    """Verify 'eidos plugin install' and 'eidos plugin uninstall' for aider, cline, kilocode, open-code, codex, and copilot."""
    # Aider
    res = runner.invoke(app, ["plugin", "install", "aider", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".aider" / "skills" / "eidos" / "SKILL.md").exists()
    assert "targets=aider:.aider/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Cline (shared .agents/skills layout for Cline, Kilo Code, and OpenCode; no .cline/ or .clinerules/ hooks)
    res = runner.invoke(app, ["plugin", "install", "cline", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (tmp_path / ".cline" / "plugins").exists()
    assert not (tmp_path / ".cline" / "skills").exists()
    assert not (tmp_path / ".clinerules").exists()
    assert "targets=aider:.aider/skills,cline:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Kilo Code
    res = runner.invoke(app, ["plugin", "install", "kilocode", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
    assert "targets=aider:.aider/skills,cline:.agents/skills,kilocode:.agents/skills" in (
        tmp_path / "AGENTS.md"
    ).read_text(encoding="utf-8")

    # Coexistence with antigravity (.agents/plugins/eidos): uninstalling either preserves the other under .agents/
    res = runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    res = runner.invoke(app, ["plugin", "uninstall", "cline", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert not (tmp_path / ".agents" / "skills").exists()
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()
    res = runner.invoke(app, ["plugin", "install", "cline", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    res = runner.invoke(app, ["plugin", "uninstall", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert not (tmp_path / ".agents" / "plugins").exists()
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()

    # Open-code
    res = runner.invoke(app, ["plugin", "install", "open-code", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (tmp_path / ".opencode" / "skills").exists()

    # Codex
    res = runner.invoke(app, ["plugin", "install", "codex", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "skills" / "eidos" / "SKILL.md").exists()

    # Copilot
    res = runner.invoke(app, ["plugin", "install", "copilot", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert not (tmp_path / ".github" / "copilot-instructions.md").exists()
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "skills" / "eidos" / "SKILL.md").exists()

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
    (plugin_dir / "plugin.json").write_bytes(
        orjson.dumps({"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "test-plugin"})
    )
    report_missing = validate_plugin_package(plugin_dir)
    assert not report_missing.is_valid
    assert any("missing required 'version' field" in err for err in report_missing.errors)

    # Invalid SemVer
    (plugin_dir / "plugin.json").write_bytes(
        orjson.dumps(
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
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path)
    status_installed = check_plugin_version_status(project_dir=tmp_path)
    assert status_installed["status"] == "up_to_date"
    assert status_installed["is_installed"]
    assert status_installed["installed_version"] == get_bundled_plugin_version()

    # 3. Outdated
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    data = orjson.loads(plugin_json.read_bytes())
    data["version"] = "0.0.9"
    plugin_json.write_bytes(orjson.dumps(data))

    status_outdated = check_plugin_version_status(project_dir=tmp_path)
    assert status_outdated["status"] == "outdated"
    assert status_outdated["installed_version"] == "0.0.9"

    # 4. Newer
    data["version"] = "9.9.9"
    plugin_json.write_bytes(orjson.dumps(data))

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
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    res_inst = runner.invoke(app, ["plugin", "version", "--project-dir", str(tmp_path)])
    assert res_inst.exit_code == 0
    assert "Up to date" in res_inst.stdout
    assert get_bundled_plugin_version() in res_inst.stdout

    # Outdated
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    data = orjson.loads(plugin_json.read_bytes())
    data["version"] = "0.0.8"
    plugin_json.write_bytes(orjson.dumps(data))

    res_outdated = runner.invoke(app, ["plugin", "version", "--project-dir", str(tmp_path)])
    assert res_outdated.exit_code == 0
    assert "Outdated" in res_outdated.stdout
    assert "eidos plugin update" in res_outdated.stdout


def test_cli_plugin_update(tmp_path):
    """Verify 'eidos plugin update' CLI command upgrades older plugin versions (CTR-EIDOS-CLI)."""
    # Install initially
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])

    # Mock older installed version
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    data = orjson.loads(plugin_json.read_bytes())
    data["version"] = "0.0.5"
    plugin_json.write_bytes(orjson.dumps(data))

    # Run update
    res = runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Upgraded Eidos Agent Plugin" in res.stdout
    assert "0.0.5" in res.stdout
    assert get_bundled_plugin_version() in res.stdout

    # Verify plugin.json was updated
    updated_data = orjson.loads(plugin_json.read_bytes())
    assert updated_data["version"] == get_bundled_plugin_version()


def test_cli_plugin_upgrade_alias(tmp_path):
    """Verify 'eidos plugin upgrade' alias is removed in favor of 'eidos plugin update' (CTR-EIDOS-CLI)."""
    res_removed = runner.invoke(app, ["plugin", "upgrade", "antigravity", "--project-dir", str(tmp_path)])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(tmp_path)])
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
    hooks_data = orjson.loads(hooks_file.read_bytes())

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

    stop_payload = orjson.dumps(
        {
            "executionNum": 1,
            "terminationReason": "model_stop",
            "workspacePaths": [str(tmp_path)],
        }
    ).decode("utf-8")
    res = runner.invoke(app, ["hook"], input=stop_payload)
    assert res.exit_code == 0
    data = orjson.loads(res.stdout)
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
    data_invalid = orjson.loads(res_invalid.stdout)
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

    stop_payload = orjson.dumps(
        {
            "executionNum": 1,
            "terminationReason": "model_stop",
            "workspacePaths": [str(tmp_path)],
        }
    ).decode("utf-8")
    res = runner.invoke(app, ["hook"], input=stop_payload)
    assert res.exit_code == 0
    data = orjson.loads(res.stdout)
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
    data = orjson.loads(res_json.stdout)
    assert "healthy" in data
    assert "checks" in data
    assert "cli_binary" in data["checks"]
    assert "knowledge_graph_index" in data["checks"]
    assert data["checks"]["living_specs"]["exists"] is True

    # Virtualenv execution when 'eidos' is not on global PATH still succeeds (healthy=True, on_path=False)
    monkeypatch.setattr("eidos.cli.doctor_hook_commands.shutil.which", lambda _cmd: None)
    res_venv = runner.invoke(app, ["doctor", "--json"])
    assert res_venv.exit_code == 0
    venv_data = orjson.loads(res_venv.stdout)
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
status: active
archetypes:
  - cli_tool
---
= Custom System
""",
        encoding="utf-8",
    )
    res_custom = runner.invoke(app, ["doctor", "--path", str(custom_specs), "--json"])
    assert res_custom.exit_code == 0
    custom_data = orjson.loads(res_custom.stdout)
    assert custom_data["healthy"] is True
    assert Path(custom_data["checks"]["living_specs"]["eidos_dir"]).resolve() == custom_specs.resolve()
    assert custom_data["checks"]["living_specs"]["valid_docs"] == 1


def test_cli_doctor_update_check(monkeypatch):
    """Verify INV-DOCTOR-UPDATE-CHECK: eidos doctor checks PyPI via check_cli_update_status and reports updates non-blockingly."""
    import io
    import urllib.error

    import eidos
    from eidos.plugin.version import check_cli_update_status

    class _FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    # 1. Update available: warns user in Rich table and --json while keeping healthy=True and exit_code=0
    monkeypatch.setattr(
        "eidos.plugin.version.urllib.request.urlopen",
        lambda req, timeout=2.0: _FakeResp(orjson.dumps({"info": {"version": "99.0.0"}})),
    )
    res_table = runner.invoke(app, ["doctor"])
    assert res_table.exit_code == 0
    assert "CLI Update Check" in res_table.stdout
    assert "WARN" in res_table.stdout
    assert "99.0.0" in res_table.stdout
    assert "uv tool upgrade eidos-ai" in res_table.stdout

    res_json = runner.invoke(app, ["doctor", "--json"])
    assert res_json.exit_code == 0
    payload = orjson.loads(res_json.stdout)
    assert payload["healthy"] is True
    cli_update = payload["checks"]["cli_update"]
    assert cli_update["ok"] is True
    assert cli_update["status"] == "update_available"
    assert cli_update["latest_version"] == "99.0.0"
    assert cli_update["update_available"] is True
    assert cli_update["upgrade_command"] == "uv tool upgrade eidos-ai"

    # 2. Up to date: PASS in Rich table and status == "up_to_date" in --json
    current_norm = check_cli_update_status(current_version=eidos.__version__, pypi_url="invalid://")
    assert current_norm["status"] == "offline"
    monkeypatch.setattr(
        "eidos.plugin.version.urllib.request.urlopen",
        lambda req, timeout=2.0: _FakeResp(orjson.dumps({"info": {"version": eidos.__version__}})),
    )
    res_uptodate = runner.invoke(app, ["doctor", "--json"])
    assert res_uptodate.exit_code == 0
    uptodate_data = orjson.loads(res_uptodate.stdout)["checks"]["cli_update"]
    assert uptodate_data["status"] == "up_to_date"
    assert uptodate_data["update_available"] is False
    assert uptodate_data["upgrade_command"] is None

    # 3. Newer local build ahead of PyPI release, and PEP 440 .devN build older than final release of same X.Y.Z
    newer_status = check_cli_update_status(current_version="99.9.9")
    assert newer_status["status"] == "newer"
    assert newer_status["update_available"] is False

    monkeypatch.setattr(
        "eidos.plugin.version.urllib.request.urlopen",
        lambda req, timeout=2.0: _FakeResp(orjson.dumps({"info": {"version": "0.20.2"}})),
    )
    dev_outdated = check_cli_update_status(current_version="0.20.2.dev3")
    assert dev_outdated["status"] == "update_available"
    assert dev_outdated["update_available"] is True

    # 4. Offline / URLError / TimeoutError / malformed payload / EIDOS_NO_UPDATE_CHECK fallback
    def _raise_url_error(req, timeout=2.0):
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr("eidos.plugin.version.urllib.request.urlopen", _raise_url_error)
    res_offline_table = runner.invoke(app, ["doctor"])
    assert res_offline_table.exit_code == 0
    assert "SKIPPED" in res_offline_table.stdout

    res_offline_json = runner.invoke(app, ["doctor", "--json"])
    assert res_offline_json.exit_code == 0
    offline_payload = orjson.loads(res_offline_json.stdout)
    assert offline_payload["healthy"] is True
    assert offline_payload["checks"]["cli_update"]["status"] == "offline"
    assert offline_payload["checks"]["cli_update"]["update_available"] is False

    monkeypatch.setattr(
        "eidos.plugin.version.urllib.request.urlopen",
        lambda req, timeout=2.0: _FakeResp(b'{"info": {"version": "not-a-semver"}}'),
    )
    assert check_cli_update_status(current_version="1.0.0")["status"] == "offline"

    monkeypatch.setenv("EIDOS_NO_UPDATE_CHECK", "1")
    skipped = check_cli_update_status(current_version="1.0.0")
    assert skipped["status"] == "offline"
    assert "skipped" in skipped["status_text"].lower()


def test_hooks_are_cli_native():
    """Verify INV-HOOK-CLI-NATIVE: all hooks in hooks.json invoke 'eidos hook' directly with zero wrapper scripts."""
    ref_plugin = Path("assets/plugin_reference")
    assert not (ref_plugin / "scripts").exists(), "Legacy scripts/ wrapper directory must not exist"
    hooks_data = orjson.loads((ref_plugin / "hooks.json").read_bytes())

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
    flat_skills = tmp_path / ".agents" / "skills"
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
    uninstall_plugin(target=PluginInstallTarget.CLINE, project_dir=tmp_path)
    for active in ACTIVE_EIDOS_SKILLS:
        assert not (flat_skills / active).exists()
    assert (custom_user_skill / "SKILL.md").exists()


def test_plugin_install_creates_manifest_with_hashes(tmp_path):
    """Verify that installing a plugin generates .eidos-manifest.json with SHA-256 hashes (DLT-2026-046)."""
    import hashlib

    from eidos.plugin.installer import MANIFEST_FILENAME

    # 1. Test ANTIGRAVITY target manifest
    results = install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success

    dest = tmp_path / ".agents" / "plugins" / "eidos"
    manifest_file = dest / MANIFEST_FILENAME
    assert manifest_file.exists(), f"Expected manifest file at {manifest_file}"

    manifest_data = orjson.loads(manifest_file.read_bytes())

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
    codex_manifest_file = codex_root / ".agents" / "plugins" / "eidos" / MANIFEST_FILENAME
    assert codex_manifest_file.exists()
    codex_manifest = orjson.loads(codex_manifest_file.read_bytes())
    assert "plugin.json" in codex_manifest["files"]
    assert "rules.md" not in codex_manifest["files"]
    assert "skills/eidos/SKILL.md" in codex_manifest["files"]

    # 3. Test CLINE target manifest
    cline_root = tmp_path / "cline_project"
    cline_root.mkdir()
    install_plugin(target=PluginInstallTarget.CLINE, project_dir=cline_root, force=True)
    cline_manifest_file = cline_root / ".agents" / MANIFEST_FILENAME
    assert cline_manifest_file.exists()
    cline_manifest = orjson.loads(cline_manifest_file.read_bytes())
    assert "skills/eidos/SKILL.md" in cline_manifest["files"]


def test_plugin_uninstall_preserves_unmanifested_user_files(tmp_path):
    """Verify non-destructive uninstall removes only manifest-tracked files, preserving user files in .agents/plugins/eidos and .agents (DLT-2026-046, DLT-2026-127)."""
    from eidos.plugin.installer import MANIFEST_FILENAME

    # 1. Non-destructive uninstall in .agents/plugins/eidos
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    user_file = plugin_dir / "user_custom_config.json"
    user_file.write_text('{"user_custom": true}\n', encoding="utf-8")
    user_skill_file = plugin_dir / "skills" / "my-team-skill" / "SKILL.md"
    user_skill_file.parent.mkdir(parents=True, exist_ok=True)
    user_skill_file.write_text("# My Team Skill\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert (plugin_dir / MANIFEST_FILENAME).exists()
    assert (plugin_dir / "plugin.json").exists()
    assert user_file.exists()
    assert user_skill_file.exists()

    uninst = uninstall_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success

    # Manifest-tracked files removed
    assert not (plugin_dir / MANIFEST_FILENAME).exists()
    assert not (plugin_dir / "plugin.json").exists()
    assert not (plugin_dir / "skills" / "eidos").exists()

    # User-created files and parent directory preserved!
    assert plugin_dir.exists(), ".agents/plugins/eidos directory should NOT be removed when user files exist"
    assert user_file.exists(), "User file must be preserved"
    assert user_file.read_text(encoding="utf-8") == '{"user_custom": true}\n'
    assert user_skill_file.exists(), "User skill must be preserved"

    # 2. Non-destructive uninstall in .agents (skills-only target)
    cline_ws = tmp_path / "cline_preserve"
    agents_dir = cline_ws / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    user_dest_file = agents_dir / "untracked_user_notes.txt"
    user_dest_file.write_text("keep this safe\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.CLINE, project_dir=cline_ws, force=True)
    assert (agents_dir / MANIFEST_FILENAME).exists()
    assert (agents_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert user_dest_file.exists()

    uninst_dest = uninstall_plugin(target=PluginInstallTarget.CLINE, project_dir=cline_ws)
    assert len(uninst_dest) == 1
    assert uninst_dest[0].success
    assert not (agents_dir / MANIFEST_FILENAME).exists()
    assert not (agents_dir / "skills" / "eidos").exists()
    assert agents_dir.exists(), ".agents directory should NOT be removed when user files exist"
    assert user_dest_file.exists(), "User file in .agents must be preserved"
    assert user_dest_file.read_text(encoding="utf-8") == "keep this safe\n"


def test_plugin_install_force_false_prevents_overwriting(tmp_path):
    """Verify force=False (default) prevents overwriting pre-existing user files without --force (DLT-2026-046, DLT-2026-121)."""
    # 1. Pre-existing, user-modified Codex skill file
    codex_skill = tmp_path / ".agents" / "plugins" / "eidos" / "skills" / "eidos" / "SKILL.md"
    codex_skill.parent.mkdir(parents=True, exist_ok=True)
    codex_skill.write_text("# Custom User Skill\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=False)
    assert codex_skill.read_text(encoding="utf-8") == "# Custom User Skill\n", (
        "SKILL.md should NOT be overwritten when force=False"
    )

    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert "name: eidos" in codex_skill.read_text(encoding="utf-8"), "SKILL.md SHOULD be overwritten when force=True"

    # 2. User-authored legacy-named files are never written by install, with or without force
    rules_file = tmp_path / ".codex" / "rules.md"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text("# Custom User Rules\n", encoding="utf-8")
    conv_file = tmp_path / ".aider.conventions.md"
    conv_file.write_text("# Custom User Aider Conventions\n", encoding="utf-8")
    for force in (False, True):
        install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=force)
        install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=force)
        assert rules_file.read_text(encoding="utf-8") == "# Custom User Rules\n"
        assert conv_file.read_text(encoding="utf-8") == "# Custom User Aider Conventions\n"

    # 3. CLI default force=False
    cli_root = tmp_path / "cli_test"
    cli_skill = cli_root / ".agents" / "plugins" / "eidos" / "skills" / "eidos" / "SKILL.md"
    cli_skill.parent.mkdir(parents=True, exist_ok=True)
    cli_skill.write_text("# User Skill CLI\n", encoding="utf-8")

    res = runner.invoke(app, ["plugin", "install", "codex", "-p", str(cli_root)])
    assert res.exit_code == 0
    assert cli_skill.read_text(encoding="utf-8") == "# User Skill CLI\n"

    res_force = runner.invoke(app, ["plugin", "install", "codex", "-p", str(cli_root), "--force"])
    assert res_force.exit_code == 0
    assert "name: eidos" in cli_skill.read_text(encoding="utf-8")


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
    assert not (codex_dir / "hooks.json").exists()

    uninstall_plugin(project_dir=tmp_path, target=PluginInstallTarget.CODEX)
    assert rules_file.exists(), "Pre-existing user .codex/rules.md must still exist after uninstall!"
    assert rules_file.read_text(encoding="utf-8") == "# User Pre-existing Codex Rules\n"
    assert not (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()

    # Also verify user scripts/ directory in .agents/plugins/eidos is preserved when force=False
    custom_ws = tmp_path / "custom_with_scripts"
    user_script = custom_ws / ".agents" / "plugins" / "eidos" / "scripts" / "deploy.sh"
    user_script.parent.mkdir(parents=True, exist_ok=True)
    user_script.write_text("#!/bin/sh\necho deploy\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=custom_ws, force=False)
    assert user_script.exists(), "User scripts/ directory must not be deleted when force=False"


def test_plugin_install_refuses_symlink_destination(tmp_path):
    """Verify install_plugin refuses destination symlinks and parent symlinks to prevent symlink traversal (DLT-2026-046)."""
    # 1. Symlinked destination directory
    real_target_dir = tmp_path / "real_target"
    real_target_dir.mkdir()
    symlink_ws = tmp_path / "symlink_dest_ws"
    (symlink_ws / ".agents" / "plugins").mkdir(parents=True)
    (symlink_ws / ".agents" / "plugins" / "eidos").symlink_to(real_target_dir)

    with pytest.raises(FileExistsError, match="symlink"):
        install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=symlink_ws)

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
        install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=project_dir, force=True)

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
    hooks_file.write_bytes(
        orjson.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"command": "eidos hook lint --event PreToolUse"}],
                    "PostToolUse": [{"command": "uvx --from eidos-ai eidos hook lint --event PostToolUse"}],
                    "Stop": [{"command": "uvx --from eidos-ai==0.1.0 eidos hook drift"}],
                }
            },
            option=orjson.OPT_INDENT_2,
        )
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
    dest.mkdir()
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=dest, force=True, launcher="uvx")
    installed_hooks = dest / ".agents" / "plugins" / "eidos" / "hooks.json"
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
status: active
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
    """Verify DLT-2026-071 & DLT-2026-117: project-native Claude skills/Stop hook without commands, Path-typed AIDER created_files, accurate fallback plugin_dir, and line-anchored SKILL.md frontmatter."""
    # 1. Claude install configures .claude/skills/ and .claude/settings.json Stop hook without .claude/plugins/eidos or commands
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)
    assert not (tmp_path / ".claude" / "plugins").exists()
    assert not (tmp_path / ".claude" / "commands").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()

    # 2. Aider created_files contains only Path instances (no bool)
    aider_res = install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    assert len(aider_res) == 1
    assert all(isinstance(p, Path) and not isinstance(p, bool) for p in aider_res[0].created_files)
    assert (tmp_path / ".aider.conf.yml") in aider_res[0].created_files

    # 3. Fallback version check reports actual matched plugin directory (.agents for CLINE, .agents/plugins/eidos for CODEX)
    cline_proj = tmp_path / "cline_only"
    cline_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CLINE, project_dir=cline_proj, force=True)
    st_cline = check_plugin_version_status(project_dir=cline_proj)
    assert st_cline["is_installed"] is True
    assert Path(st_cline["plugin_dir"]).resolve() == (cline_proj / ".agents").resolve()

    codex_proj = tmp_path / "codex_only"
    codex_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=codex_proj, force=True)
    st = check_plugin_version_status(project_dir=codex_proj)
    assert st["is_installed"] is True
    assert Path(st["plugin_dir"]).resolve() == (codex_proj / ".agents" / "plugins" / "eidos").resolve()

    # 4. Line-anchored SKILL.md frontmatter allows '---' inside YAML scalar values
    custom_plugin = tmp_path / "custom_plugin"
    skill_dir = custom_plugin / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (custom_plugin / "plugin.json").write_bytes(
        orjson.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "Demo plugin",
            }
        )
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
    (ws / ".claude" / "settings.json").write_bytes(
        orjson.dumps(
            {"theme": "dark", "hooks": {"CustomHook": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
        )
    )
    (ws / ".vscode" / "settings.json").write_bytes(
        orjson.dumps({"editor.tabSize": 2, "chat.promptFilesLocations": {"custom/prompts": True}})
    )
    (ws / ".aider.conf.yml").write_text("model: sonnet\nread:\n  - CONVENTIONS.md\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.ALL, project_dir=ws, force=True)
    uninstall_plugin(target=PluginInstallTarget.ALL, project_dir=ws)

    claude_after = orjson.loads((ws / ".claude" / "settings.json").read_bytes())
    assert claude_after.get("theme") == "dark"
    assert "CustomHook" in claude_after.get("hooks", {})

    vscode_after = orjson.loads((ws / ".vscode" / "settings.json").read_bytes())
    assert vscode_after.get("editor.tabSize") == 2
    assert vscode_after.get("chat.promptFilesLocations", {}).get("custom/prompts") is True

    aider_after = (ws / ".aider.conf.yml").read_text(encoding="utf-8")
    assert "CONVENTIONS.md" in aider_after
    assert "model: sonnet" in aider_after
    # DLT-2026-121: the AGENTS.md read: entry is intentionally left in place on uninstall
    assert "AGENTS.md" in aider_after

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
        _remove_project_claude_settings,
        _uninstall_from_manifest,
        _update_project_aider_conf,
        _update_project_claude_settings,
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
    (pdir / "plugin.json").write_bytes(
        orjson.dumps(
            {
                "name": "BAD--NAME",
                "version": "not_semver",
                "description": "Desc",
                "extensions": {"com.example": {}},
            }
        )
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
    (pdir / "hooks.json").write_bytes(
        orjson.dumps(
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
        )
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
    (pdir / "mcp.json").write_bytes(
        orjson.dumps({"mcpServers": {"srv": {"type": "stdio", "command": "python -m my_server"}}})
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

    # 5. plugin/installer.py helper edge cases: _uninstall_from_manifest when .eidos-manifest.json is absent
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
        (cwd_bundle_ws / "assets" / "plugin_reference" / "plugin.json").write_bytes(
            orjson.dumps({"name": "eidos", "version": "0.1.0"})
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

    assert _resolve_installed_plugin_manifest(project_dir=tmp_path) == (
        (tmp_path / ".agents" / "plugins" / "eidos").resolve(),
        None,
    )

    # 6b. plugin/validator.py: empty plugin.json {}, valid mcp_config.json, invalid mcp.json, invalid hooks.json
    pdir2 = tmp_path / "pdir2"
    pdir2.mkdir()
    (pdir2 / "plugin.json").write_text("{}", encoding="utf-8")
    (pdir2 / "mcp_config.json").write_bytes(orjson.dumps({"mcpServers": {"eidos": {}}}))
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
    res_upd1 = cli_runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(upd_ws)])
    assert res_upd1.exit_code == 0
    res_upd2 = cli_runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(upd_ws)])
    assert res_upd2.exit_code == 0 and "already at latest version" in res_upd2.output

    sym_target = tmp_path / "sym_target_dir"
    sym_target.mkdir()
    sym_ws = tmp_path / "sym_link_ws"
    sym_ws.mkdir()
    (sym_ws / ".agents").symlink_to(sym_target)
    res_sym = cli_runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(sym_ws)])
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
    init_repository(hook_ws, title="Hook Exec")
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

    # 6e. plugin/installer.py: _update_project_aider_conf / _update_project_claude_settings / _remove_project_claude_settings branches
    c_settings = tmp_path / "claude_settings_edge" / "settings.json"
    assert _remove_project_claude_settings(c_settings) is False
    c_settings.parent.mkdir(parents=True, exist_ok=True)
    c_settings.write_text("{bad", encoding="utf-8")
    assert _remove_project_claude_settings(c_settings) is False
    _update_project_claude_settings(c_settings, launcher="eidos")
    assert _remove_project_claude_settings(c_settings) is True
    assert not c_settings.exists()

    # _update_project_aider_conf with missing file, invalid YAML, scalar read:, and an existing AGENTS.md entry
    from eidos.core.spec.parser import safe_load_yaml

    aider_f = tmp_path / ".aider.conf.yml"
    _update_project_aider_conf(aider_f)
    assert safe_load_yaml(aider_f.read_text(encoding="utf-8")) == {"read": ["AGENTS.md"]}
    aider_f.write_text(": [broken", encoding="utf-8")
    _update_project_aider_conf(aider_f)
    assert safe_load_yaml(aider_f.read_text(encoding="utf-8")) == {"read": ["AGENTS.md"]}
    aider_f.write_text("read: CONVENTIONS.md\nmodel: sonnet\n", encoding="utf-8")
    _update_project_aider_conf(aider_f)
    _update_project_aider_conf(aider_f)  # idempotent: never duplicates the entry
    loaded_aider = safe_load_yaml(aider_f.read_text(encoding="utf-8"))
    assert loaded_aider["read"] == ["CONVENTIONS.md", "AGENTS.md"]
    assert loaded_aider["model"] == "sonnet"

    # 6f. Newer installed version in `plugin version` & `plugin update`, execute_hook with drift,
    #     and corrupt manifest in _copy_plugin_tree
    (bad_ver_dir / "plugin.json").write_text('{"name": "eidos", "version": "99.0.0"}', encoding="utf-8")
    res_newer = cli_runner.invoke(app, ["plugin", "version", "--project-dir", str(bad_ver_ws)])
    assert res_newer.exit_code == 0 and "Newer" in res_newer.output
    res_upd_newer = cli_runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(bad_ver_ws)])
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
        target=PluginInstallTarget.ANTIGRAVITY,
        name="custom-antigravity",
        plugin_subdir=".agents/plugins/eidos",
        post_install_hook=_custom_post_hook,
    )
    res = adapter.install(tmp_path, locate_plugin_bundle(), force=True)
    assert res.success is True

    manifest_path = tmp_path / ".agents" / "plugins" / "eidos" / MANIFEST_FILENAME
    assert manifest_path.exists()
    manifest_data = orjson.loads(manifest_path.read_bytes())
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
        "---\ntype: product/domain\nid: DOM-GOOD\ntitle: Good Domain\nstatus: active\nclassification: core\n---\n= Good Domain\n",
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
    assert not (inst_root / ".claude" / "plugins").exists()
    assert (inst_root / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (inst_root / ".cline" / "plugins").exists()
    assert not (inst_root / ".cline" / "skills").exists()
    assert (inst_root / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (inst_root / ".opencode" / "plugins").exists()
    assert not (inst_root / ".opencode" / "skills").exists()
    assert not (inst_root / ".github" / "plugin").exists()
    assert not (inst_root / ".github" / "skills").exists()
    assert (inst_root / ".agents" / "plugins" / "eidos" / "skills" / "eidos" / "SKILL.md").exists()


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
    doc_payload = orjson.loads(res_doc.stdout)
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
    broken_out = orjson.loads(res_hook_broken.stdout)
    assert broken_out["decision"] == "allow"
    assert "Specification validation failed" in broken_out["systemMessage"]

    res_hook_missing = runner.invoke(app, ["hook", "--path", str(broken_ws / "nonexistent")])
    assert res_hook_missing.exit_code == 0
    assert orjson.loads(res_hook_missing.stdout) == {"decision": "allow"}

    # --- D2.1 & D2.2: drift --path . and subdirectory auto-discovery for validate, drift, and context ---
    repo_ws = tmp_path / "sub_ws"
    repo_ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_ws, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=repo_ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_ws, check=True)
    assert runner.invoke(app, ["init", str(repo_ws / "eidos"), "--title", "Sub Test"]).exit_code == 0
    sub_dir = repo_ws / "src" / "pkg"
    sub_dir.mkdir(parents=True)
    (sub_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo_ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo_ws, check=True)

    # Running drift -u --path <repo_root> resolves repo_root/eidos properly
    res_drift_dot = runner.invoke(app, ["drift", "-u", "--path", str(repo_ws), "--json"])
    assert res_drift_dot.exit_code == 0
    assert orjson.loads(res_drift_dot.stdout)["is_clean"] is True

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

    # --- D3.1: Copilot install appends the Eidos block to an existing user AGENTS.md and leaves copilot-instructions.md alone ---
    copilot_ws = tmp_path / "copilot_ws"
    (copilot_ws / ".github").mkdir(parents=True)
    copilot_file = copilot_ws / ".github" / "copilot-instructions.md"
    copilot_file.write_text("# Team Custom Instructions\nAlways write tests.\n", encoding="utf-8")
    (copilot_ws / "AGENTS.md").write_text("# Team Agents\nUse pnpm.\n", encoding="utf-8")
    install_plugin(target=PluginInstallTarget.COPILOT, project_dir=copilot_ws, force=False)
    assert copilot_file.read_text(encoding="utf-8") == "# Team Custom Instructions\nAlways write tests.\n"
    merged_agents = (copilot_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert merged_agents.startswith("# Team Agents\nUse pnpm.\n")
    assert "<!-- eidos:begin agents-md " in merged_agents

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


def test_normalize_to_semver_and_bundled_version_sync(monkeypatch):
    """Verify normalize_to_semver handles PEP 440 / v-prefixed strings and get_bundled_plugin_version stays in sync."""
    import eidos
    import eidos.core.workspace.assets as ws_assets
    from eidos.plugin.version import normalize_to_semver

    assert normalize_to_semver("0.12.0") == "0.12.0"
    assert normalize_to_semver("v0.12.0") == "0.12.0"
    assert normalize_to_semver("V1.2.3-rc.1") == "1.2.3-rc.1"
    assert normalize_to_semver("0.12.1.dev13") == "0.12.1"
    assert normalize_to_semver("0.12.0.post1") == "0.12.0"
    assert normalize_to_semver("invalid") is None
    assert normalize_to_semver(None) is None  # type: ignore[arg-type]

    # When eidos.__version__ is a newer clean SemVer release than plugin.json, get_bundled_plugin_version upgrades to it
    monkeypatch.setattr(eidos, "__version__", "99.1.0")
    assert get_bundled_plugin_version() == "99.1.0"

    # When plugin.json lookup fails and eidos.__version__ is PEP 440 .devN, fallback normalizes instead of returning 0.2.0
    def _fail_dir():
        raise OSError("missing assets")

    monkeypatch.setattr(ws_assets, "get_plugin_reference_dir", _fail_dir)
    monkeypatch.setattr(eidos, "__version__", "0.12.1.dev7")
    assert get_bundled_plugin_version() == "0.12.1"

    monkeypatch.setattr(eidos, "__version__", "not-a-version")
    assert get_bundled_plugin_version() == "0.2.0"


def test_sdist_and_multi_root_plugin_reference_resolution(tmp_path, monkeypatch):
    """Verify pyproject.toml sdist configuration preserves assets/plugin_reference and get_assets_path probes all candidate roots."""
    import tomllib

    import eidos.core.workspace.assets as ws_assets

    repo_root = Path(__file__).resolve().parent.parent
    pyproject_data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    sdist_cfg = pyproject_data["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert sdist_cfg.get("skip-excluded-dirs") is True
    assert "/.agents" in sdist_cfg.get("exclude", [])
    assert "/plugins" in sdist_cfg.get("exclude", [])
    assert sdist_cfg.get("force-include", {}).get("assets") == "assets"

    # Verify get_assets_root and get_assets_path skip unrelated empty ancestor 'assets/' dirs when a valid root follows
    fake_pkg = tmp_path / "decoy_ws" / "sub" / "pkg" / "core" / "workspace" / "assets.py"
    fake_pkg.parent.mkdir(parents=True, exist_ok=True)
    # Create an unrelated empty assets/ directory at parents[3] ("decoy_ws/sub/assets")
    (tmp_path / "decoy_ws" / "sub" / "assets").mkdir(parents=True, exist_ok=True)
    # Create a valid plugin_reference in CWD assets/
    valid_cwd = tmp_path / "valid_cwd"
    valid_plugin_ref = valid_cwd / "assets" / "plugin_reference"
    valid_plugin_ref.mkdir(parents=True, exist_ok=True)
    (valid_plugin_ref / "plugin.json").write_text('{"name": "eidos", "version": "0.12.0"}', encoding="utf-8")

    monkeypatch.setattr(ws_assets, "__file__", str(fake_pkg))
    monkeypatch.chdir(valid_cwd)
    assert ws_assets.get_assets_root() == (valid_cwd / "assets").resolve()
    assert ws_assets.get_plugin_reference_dir() == valid_plugin_ref.resolve()
    assert locate_plugin_bundle() == valid_plugin_ref.resolve()


# ---------------------------------------------------------------------------
# DLT-2026-121: skill guardrails and the canonical AGENTS.md managed block
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path(__file__).parent.parent / "assets" / "plugin_reference" / "skills"
_AGENTS_MD_TEMPLATE = Path(__file__).parent.parent / "assets" / "plugin_reference" / "agents-md" / "block.md"

# No-growth word ceilings (post-tightening size rounded up to the next 50; never above the pre-DLT-2026-121 size).
# Raising a ceiling is a deliberate decision: edit this table in the same change that grows the skill.
_SKILL_WORD_CEILINGS = {"eidos": 2050, "eidos-spec": 2250, "eidos-plan": 1650, "eidos-ops": 850}


def _skill_frontmatter_and_body(skill_md: Path) -> tuple[dict, str]:
    from eidos.core.spec.parser import safe_load_yaml, split_frontmatter_and_body

    fm_raw, body = split_frontmatter_and_body(skill_md.read_text(encoding="utf-8"))
    return safe_load_yaml(fm_raw), body


def test_bundled_skill_size_ceilings():
    """Verify INV-PLUGIN-SKILL-SIZE-CEILING: bundled skills, descriptions, and the AGENTS.md block cannot grow unchecked."""
    from eidos.plugin.installer import TARGET_ADAPTERS, render_agents_md_block

    skill_dirs = sorted(d.name for d in _SKILLS_DIR.iterdir() if d.is_dir())
    assert skill_dirs == sorted(_SKILL_WORD_CEILINGS), "every bundled skill needs an explicit word ceiling"
    for name, ceiling in _SKILL_WORD_CEILINGS.items():
        fm, body = _skill_frontmatter_and_body(_SKILLS_DIR / name / "SKILL.md")
        words = len(body.split())
        assert words <= ceiling, f"{name}/SKILL.md body has {words} words (ceiling {ceiling})"
        assert len(fm["description"]) <= 400, f"{name} description is {len(fm['description'])} chars (max 400)"

    template = _AGENTS_MD_TEMPLATE.read_text(encoding="utf-8")
    assert len(template.split()) <= 250, "agents-md/block.md template exceeds 250 words"
    all_targets = {a.name: a.skills_dir() for a in TARGET_ADAPTERS.values() if a.skills_dir()}
    assert len(all_targets) == len(TARGET_ADAPTERS)
    assert len(render_agents_md_block(template, all_targets).split()) <= 320


def _cli_parity_violations(text: str) -> list[str]:
    """Return eidos command paths / long options in ``text`` that do not resolve against the live Typer app."""
    import re

    import typer

    root = typer.main.get_command(app)
    violations: list[str] = []
    for line in text.splitlines():
        for match in re.finditer(r"(?<![\w/.-])eidos ([^`|\n]*)", line):
            tokens = match.group(1).split()
            cmd, path = root, []
            for tok in tokens:
                subs = getattr(cmd, "commands", None)
                if subs is not None and tok in subs:
                    cmd, path = subs[tok], [*path, tok]
                else:
                    break
            if not path:
                continue  # prose such as "eidos living specs" or "eidos/"
            rest = tokens[len(path) :]
            if getattr(cmd, "commands", None) is not None:
                if rest and re.fullmatch(r"[a-z][a-z0-9-]*", rest[0]):
                    violations.append(f"unknown subcommand: eidos {' '.join(path)} {rest[0]}")
                continue
            valid = {"--help"}
            for param in cmd.params:
                valid.update(getattr(param, "opts", []))
                valid.update(getattr(param, "secondary_opts", []))
            for tok in rest:
                opt = tok.split("=", 1)[0].rstrip(",.;:)")
                if re.fullmatch(r"--[a-z][a-z0-9-]*", opt) and opt not in valid:
                    violations.append(f"unknown option: eidos {' '.join(path)} {opt}")
    return violations


def test_bundled_skill_cli_parity():
    """Verify INV-PLUGIN-SKILL-CLI-PARITY: every eidos command and option in skills and the AGENTS.md block exists."""
    sources = [*sorted(_SKILLS_DIR.glob("*/SKILL.md")), _AGENTS_MD_TEMPLATE]
    checked = 0
    for src in sources:
        text = src.read_text(encoding="utf-8")
        checked += text.count("eidos ")
        assert _cli_parity_violations(text) == [], f"stale CLI references in {src}"
    assert checked > 100, "parity scan unexpectedly found almost no eidos invocations"

    # Negative cases: removed flags and subcommands are caught; prose, placeholders, and other tools are ignored.
    bad = "Run `eidos context -f x --no-such-flag` then `eidos index frobnicate`."
    assert _cli_parity_violations(bad) == [
        "unknown option: eidos context --no-such-flag",
        "unknown subcommand: eidos index frobnicate",
    ]
    ok = "Use `uv run eidos drift --uncommitted --json`, `uv tool install --python 3.14 eidos-ai`, eidos living specs."
    assert _cli_parity_violations(ok) == []


@pytest.mark.parametrize(
    "case",
    [
        "create",
        "append",
        "move_and_update",
        "idempotent",
        "user_edit",
        "crlf",
        "bom",
        "opt_out",
        "fenced_example",
        "malformed",
        "symlink",
    ],
)
def test_managed_block_preserves_user_content(tmp_path, case):
    """Verify INV-PLUGIN-MANAGED-BLOCK-SAFETY: Eidos only touches text between its own markers in shared files."""
    from eidos.plugin.managed_block import BlockStatus, content_hash, read_managed_block, upsert_managed_block

    path = tmp_path / "AGENTS.md"

    def render(targets):
        return "rules\n" + "".join(f"- {d}\n" for d in targets.values())

    if case == "create":
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.CREATED
        text = path.read_text(encoding="utf-8")
        assert text.startswith("<!-- eidos:begin agents-md sha256:") and text.endswith("<!-- eidos:end agents-md -->\n")
        assert "targets=claude:.claude/skills" in text

    elif case == "append":
        user = "# Team\nRun pnpm test.\n\n"
        path.write_text(user, encoding="utf-8")
        res = upsert_managed_block(path, "agents-md", render, target="aider", skills_dir=".aider/skills")
        assert res.status is BlockStatus.APPENDED
        assert path.read_text(encoding="utf-8").startswith(user)

    elif case == "move_and_update":
        upsert_managed_block(path, "agents-md", render, target="aider", skills_dir=".aider/skills")
        block = path.read_text(encoding="utf-8")
        before, after = "# Intro\nuser line 1\n\n", "\n## Team notes\nuser line 2\n"
        path.write_text(before + block + after, encoding="utf-8")
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.UPDATED
        text = path.read_text(encoding="utf-8")
        assert text.startswith(before) and text.endswith(after)
        assert "targets=aider:.aider/skills,claude:.claude/skills" in text
        assert res.targets == {"aider": ".aider/skills", "claude": ".claude/skills"}

    elif case == "idempotent":
        upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        first = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.UNCHANGED
        assert path.read_bytes() == first and path.stat().st_mtime_ns == mtime

    elif case == "user_edit":
        upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        edited = path.read_text(encoding="utf-8").replace("rules\n", "rules (team tweak)\n")
        path.write_text(edited, encoding="utf-8")
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.MODIFIED
        assert path.read_text(encoding="utf-8") == edited
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills", force=True)
        assert res.status is BlockStatus.UPDATED
        assert "team tweak" not in path.read_text(encoding="utf-8")

    elif case == "crlf":
        upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        crlf = ("# Team\r\n\r\n" + path.read_text(encoding="utf-8")).replace("\n", "\r\n").replace("\r\r\n", "\r\n")
        path.write_bytes(crlf.encode("utf-8"))
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.UNCHANGED, "a CRLF conversion must not be mistaken for a user edit"
        res = upsert_managed_block(path, "agents-md", render, target="cline", skills_dir=".agents/skills")
        assert res.status is BlockStatus.UPDATED
        out = path.read_bytes().decode("utf-8")
        assert out.startswith("# Team\r\n\r\n") and "\n" not in out.replace("\r\n", "")
        block = read_managed_block(path, "agents-md")
        assert block is not None and block.sha == content_hash(block.inner)

    elif case == "bom":
        path.write_bytes(b"\xef\xbb\xbf# Team\n")
        upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert path.read_bytes().startswith(b"\xef\xbb\xbf# Team\n\n<!-- eidos:begin agents-md")

    elif case == "opt_out":
        user = "# Team\n<!-- eidos:agents-md off -->\n"
        path.write_text(user, encoding="utf-8")
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.OPTED_OUT
        assert path.read_text(encoding="utf-8") == user

    elif case == "fenced_example":
        fenced = (
            "# Docs\n"
            "Add `<!-- eidos:agents-md off -->` on its own line to opt out.\n"
            "```markdown\n"
            "<!-- eidos:begin agents-md sha256:000000000000 -->\n"
            "example\n"
            "<!-- eidos:end agents-md -->\n"
            "<!-- eidos:agents-md off -->\n"
            "```\n"
        )
        path.write_text(fenced, encoding="utf-8")
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills", force=True)
        assert res.status is BlockStatus.APPENDED
        out = path.read_text(encoding="utf-8")
        assert out.startswith(fenced)
        assert out.count("<!-- eidos:begin agents-md ") == 2

    elif case == "malformed":
        for user in (
            "# Team\n<!-- eidos:begin agents-md sha256:000000000000 -->\nno end marker\n",
            "<!-- eidos:end agents-md -->\n<!-- eidos:begin agents-md -->\n",
            "<!-- eidos:begin agents-md -->\na\n<!-- eidos:end agents-md -->\n<!-- eidos:begin agents-md -->\n",
        ):
            path.write_text(user, encoding="utf-8")
            res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
            assert res.status is BlockStatus.MALFORMED and "line" in res.detail
            assert path.read_text(encoding="utf-8") == user

    elif case == "symlink":
        real = tmp_path / "real.md"
        real.write_text("# Real\n", encoding="utf-8")
        path.symlink_to(real)
        with pytest.raises(FileExistsError):
            upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert real.read_text(encoding="utf-8") == "# Real\n"


@pytest.mark.parametrize("target", [t for t in PluginInstallTarget if t is not PluginInstallTarget.ALL])
def test_agents_md_fresh_install_all_targets(tmp_path, target):
    """Verify every target's fresh install projects exactly one AGENTS.md block and no retired bridge files."""
    from eidos.plugin.installer import MANIFEST_FILENAME, TARGET_ADAPTERS
    from eidos.plugin.managed_block import read_managed_block

    skills_dir = TARGET_ADAPTERS[target].skills_dir()
    for user_content in (None, "# Team rules\nRun make test.\n"):
        ws = tmp_path / ("with_user" if user_content else "empty")
        ws.mkdir()
        agents_md = ws / "AGENTS.md"
        if user_content:
            agents_md.write_text(user_content, encoding="utf-8")

        install_plugin(target=target, project_dir=ws, force=True)
        text = agents_md.read_text(encoding="utf-8")
        assert text.count("<!-- eidos:begin agents-md ") == 1
        if user_content:
            assert text.startswith(user_content)
        block = read_managed_block(agents_md, "agents-md")
        assert block is not None and block.targets == {target.value: skills_dir}
        assert f"`{skills_dir}/<skill>/SKILL.md`" in block.inner
        assert (ws / skills_dir / "eidos" / "SKILL.md").exists()

        # Idempotent re-install and update
        snapshot = agents_md.read_bytes()
        install_plugin(target=target, project_dir=ws, force=False)
        runner.invoke(app, ["plugin", "update", target.value, "--project-dir", str(ws)])
        assert agents_md.read_bytes() == snapshot

        # Retired per-harness bridges are never created
        for retired in (".cursor/rules/eidos.mdc", ".codex/rules.md", ".aider.conventions.md"):
            assert not (ws / retired).exists(), retired
        assert not (ws / ".github" / "copilot-instructions.md").exists()

        # Shared user files are never recorded in any installation manifest
        for manifest in ws.rglob(MANIFEST_FILENAME):
            files = orjson.loads(manifest.read_bytes()).get("files", {})
            assert not {"AGENTS.md", "CLAUDE.md", ".aider.conf.yml"} & set(files)


def test_agents_md_shared_across_targets(tmp_path):
    """Verify several targets share one deterministic AGENTS.md block regardless of install order (DLT-2026-121)."""
    order_a, order_b = tmp_path / "a", tmp_path / "b"
    for ws, order in ((order_a, ["aider", "claude"]), (order_b, ["claude", "aider"])):
        ws.mkdir()
        for name in order:
            install_plugin(target=PluginInstallTarget(name), project_dir=ws, force=True)
    text_a = (order_a / "AGENTS.md").read_text(encoding="utf-8")
    assert text_a == (order_b / "AGENTS.md").read_text(encoding="utf-8")
    assert "targets=aider:.aider/skills,claude:.claude/skills" in text_a
    assert text_a.index("`.aider/skills/") < text_a.index("`.claude/skills/")

    all_ws = tmp_path / "all"
    all_ws.mkdir()
    install_plugin(target=PluginInstallTarget.ALL, project_dir=all_ws, force=True)
    all_text = (all_ws / "AGENTS.md").read_text(encoding="utf-8")
    for skills_dir in (".agents/plugins/eidos/skills", ".agents/skills", ".claude/skills", ".aider/skills"):
        assert f"`{skills_dir}/<skill>/SKILL.md`" in all_text

    # Shared skills directory is rendered once, and uninstalled targets are pruned on next install
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=all_ws, force=True)
    shared_text = (all_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert shared_text.count("`.agents/plugins/eidos/skills/<skill>/SKILL.md`") == 1
    uninstall_plugin(target=PluginInstallTarget.AIDER, project_dir=order_a)
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=order_a, force=True)
    pruned_text = (order_a / "AGENTS.md").read_text(encoding="utf-8")
    assert "targets=claude:.claude/skills " in pruned_text and "aider:" not in pruned_text
    assert "`.aider/skills/" not in pruned_text

    # Opt-out is a silent user preference (never a Warning in the install message)
    opt_ws = tmp_path / "opt_ws"
    opt_ws.mkdir()
    (opt_ws / "AGENTS.md").write_text("# Team\n<!-- eidos:agents-md off -->\n", encoding="utf-8")
    opt_res = install_plugin(target=PluginInstallTarget.CURSOR, project_dir=opt_ws, force=True)
    assert "Warning:" not in opt_res[0].message

    # update_plugin never forces a hand-edited block, while an explicit install --force does
    all_text = (all_ws / "AGENTS.md").read_text(encoding="utf-8")
    edited = all_text.replace("## Eidos Living Specifications", "## Eidos Living Specifications (team edit)")
    (all_ws / "AGENTS.md").write_text(edited, encoding="utf-8")
    res_update = runner.invoke(app, ["plugin", "update", "claude", "--project-dir", str(all_ws)])
    assert res_update.exit_code == 0
    assert (all_ws / "AGENTS.md").read_text(encoding="utf-8") == edited
    res_force = runner.invoke(app, ["plugin", "install", "claude", "--project-dir", str(all_ws), "--force"])
    assert res_force.exit_code == 0
    assert (all_ws / "AGENTS.md").read_text(encoding="utf-8") == all_text


def test_claude_import_block(tmp_path):
    """Verify the Claude target imports @AGENTS.md from CLAUDE.md without clobbering user content (DLT-2026-121)."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=fresh, force=True)
    claude_md = (fresh / "CLAUDE.md").read_text(encoding="utf-8")
    assert "<!-- eidos:begin claude-import " in claude_md and "\n@AGENTS.md\n" in claude_md

    user_ws = tmp_path / "user"
    user_ws.mkdir()
    user_claude = "# Project memory\nPrefer small PRs.\n"
    (user_ws / "CLAUDE.md").write_text(user_claude, encoding="utf-8")
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=user_ws, force=True)
    merged = (user_ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert merged.startswith(user_claude) and "\n@AGENTS.md\n" in merged
    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=user_ws)
    assert (user_ws / "CLAUDE.md").read_text(encoding="utf-8") == merged  # left in place by design

    already = tmp_path / "already"
    already.mkdir()
    existing = "# Memory\n@AGENTS.md\n"
    (already / "CLAUDE.md").write_text(existing, encoding="utf-8")
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=already, force=True)
    assert (already / "CLAUDE.md").read_text(encoding="utf-8") == existing


def test_aider_read_entry(tmp_path):
    """Verify the Aider target adds AGENTS.md to read: once and preserves user keys and entries (DLT-2026-121)."""
    from eidos.core.spec.parser import safe_load_yaml

    conf = tmp_path / ".aider.conf.yml"
    conf.write_text("model: sonnet\nread:\n  - CONVENTIONS.md\n", encoding="utf-8")
    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    data = safe_load_yaml(conf.read_text(encoding="utf-8"))
    assert data["model"] == "sonnet"
    assert data["read"] == ["CONVENTIONS.md", "AGENTS.md"]


def test_agents_md_symlink_layouts_never_abort_install(tmp_path):
    """Verify symlinked AGENTS.md / CLAUDE.md layouts are left untouched and never abort an install (DLT-2026-121)."""
    # CLAUDE.md -> AGENTS.md: the block lands in AGENTS.md and Claude Code already loads it, so no import is needed
    ws = tmp_path / "claude_link"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# Team\n", encoding="utf-8")
    (ws / "CLAUDE.md").symlink_to("AGENTS.md")
    res = install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=ws, force=True)
    assert res[0].success and "Warning" not in res[0].message
    assert (ws / "CLAUDE.md").is_symlink()
    text = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Team\n") and "claude-import" not in text and "<!-- eidos:begin agents-md " in text

    # AGENTS.md -> elsewhere: never written through; install still succeeds and reports a warning
    ws2 = tmp_path / "agents_link"
    ws2.mkdir()
    real = tmp_path / "shared_agents.md"
    real.write_text("# Shared\n", encoding="utf-8")
    (ws2 / "AGENTS.md").symlink_to(real)
    res2 = install_plugin(target=PluginInstallTarget.CURSOR, project_dir=ws2, force=True)
    assert res2[0].success
    assert "Warning: AGENTS.md block not updated (skipped" in res2[0].message
    assert real.read_text(encoding="utf-8") == "# Shared\n"
    assert (ws2 / ".agents" / "plugins" / "eidos" / "skills" / "eidos" / "SKILL.md").exists()
