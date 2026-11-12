"""Tests verifying Agent Skills conformance to agentskills.io and project-scoped CLI installer."""

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
    """Verify agentskills.io skill naming constraints."""
    valid_names = ["eidos", "my-plugin", "acme.tools", "lint3r", "a", "eidos-cli"]
    invalid_names = ["My-Plugin", "-start", "has--double", "too..dots", "end-", ""]

    for name in valid_names:
        assert PLUGIN_NAME_PATTERN.match(name), f"Expected valid name: {name}"

    for name in invalid_names:
        assert not PLUGIN_NAME_PATTERN.match(name), f"Expected invalid name: {name}"


def test_bundled_plugin_conformance():
    """Verify that the bundled Eidos Agent Skills package passes agentskills.io validation."""
    bundle_dir = locate_plugin_bundle()
    report = validate_plugin_package(bundle_dir)

    assert report.is_valid, f"Skill package validation failed: {report.errors}"
    assert report.plugin_name == "eidos-agent-skills"
    assert report.plugin_version == get_bundled_plugin_version()
    assert set(report.skills) == {"eidos", "eidos-spec", "eidos-plan", "eidos-ops"}
    assert len(report.skills) == 4
    assert len(report.mcp_servers) == 0


def test_repo_plugin_conformance():
    """Verify that assets/plugin_reference passes agentskills.io validation."""
    plugin_dir = Path("assets/plugin_reference")
    assert plugin_dir.exists(), "assets/plugin_reference directory missing"

    report = validate_plugin_package(plugin_dir)
    assert report.is_valid, f"Repo skill package validation failed: {report.errors}"
    assert len(report.errors) == 0
    assert len(report.skills) == 4


def test_plugin_install_to_project_scope(tmp_path):
    """Verify project-scoped installation into .agents/skills/ without mutating .cursor or .claude."""
    results = install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)

    assert len(results) == 1
    assert results[0].target == "antigravity"

    # Check .agents/skills
    agent_dir = tmp_path / ".agents"
    assert not (agent_dir / "hooks.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    agent_report = validate_plugin_package(agent_dir)
    assert agent_report.is_valid

    # Isolated antigravity target must not touch .cursor or .claude
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".claude").exists()

    # Verify non-destructive merge and uninstall preservation on .agents/hooks.json
    hooks_file = agent_dir / "hooks.json"
    hooks_file.write_bytes(orjson.dumps({"custom-gate": {"Stop": [{"type": "command", "command": "echo hi"}]}}))
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True, hooks=True)
    merged_hooks = orjson.loads(hooks_file.read_bytes())
    assert "custom-gate" in merged_hooks
    assert "eidos-drift-gate" in merged_hooks

    uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path)
    assert hooks_file.is_file()
    remaining_hooks = orjson.loads(hooks_file.read_bytes())
    assert remaining_hooks == {"custom-gate": {"Stop": [{"type": "command", "command": "echo hi"}]}}


def test_cli_plugin_validate():
    """Verify 'eidos plugin validate' CLI command."""
    res = runner.invoke(app, ["plugin", "validate", "assets/plugin_reference"])
    assert res.exit_code == 0
    assert "CONFORMANT" in res.stdout
    assert "eidos" in res.stdout


def test_cli_plugin_info():
    """Verify 'eidos plugin info' CLI command."""
    res = runner.invoke(app, ["plugin", "info", "assets/plugin_reference"])
    assert res.exit_code == 0
    assert "agentskills.io" in res.stdout
    assert "eidos" in res.stdout


def test_cli_plugin_install_project(tmp_path):
    """Verify 'eidos plugin install <target> --project-dir' CLI command requires an explicit target."""
    res_missing = runner.invoke(app, ["plugin", "install", "--project-dir", str(tmp_path)])
    assert res_missing.exit_code != 0

    res = runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()


def test_plugin_uninstall_project_scope(tmp_path):
    """Verify uninstalling skills from project scope removes .agents/skills without deleting .cursor or .claude."""
    # Install first
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)
    skills_dir = tmp_path / ".agents" / "skills"
    cursor_rules = tmp_path / ".cursor" / "rules" / "eidos.mdc"
    cursor_rules.parent.mkdir(parents=True, exist_ok=True)
    cursor_rules.write_text("# Pre-existing Cursor rule\n", encoding="utf-8")

    assert skills_dir.exists()
    assert cursor_rules.exists()

    # Uninstall antigravity target only
    results = uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path)
    assert len(results) == 1
    assert results[0].success

    assert not skills_dir.exists()
    assert cursor_rules.exists()


def test_cli_plugin_uninstall_project(tmp_path):
    """Verify 'eidos plugin uninstall <target> --project-dir' CLI command requires an explicit target."""
    res_missing = runner.invoke(app, ["plugin", "uninstall", "--project-dir", str(tmp_path)])
    assert res_missing.exit_code != 0

    # Install
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()

    # Uninstall
    res = runner.invoke(app, ["plugin", "uninstall", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".agents" / "skills").exists()


def test_cli_plugin_remove_alias(tmp_path):
    """Verify 'eidos plugin remove' alias is removed in favor of 'eidos plugin uninstall'."""
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()

    res_removed = runner.invoke(app, ["plugin", "remove", "antigravity", "--project-dir", str(tmp_path)])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["plugin", "uninstall", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".agents" / "skills").exists()


def test_plugin_install_to_claude_plugin(tmp_path):
    """Verify installing Claude Code integration into project-native .claude/skills/ and opt-in .claude/settings.json."""
    settings_file = tmp_path / ".claude" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_bytes(orjson.dumps({"theme": "dark"}))

    # Default install (hooks=False) installs skills without registering Stop hook
    results_no_hooks = install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True)
    assert len(results_no_hooks) == 1
    assert results_no_hooks[0].success
    assert "Stop" not in orjson.loads(settings_file.read_bytes()).get("hooks", {})

    # Install with hooks=True registers Stop hook
    results = install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, hooks=True)

    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "claude"

    assert not (tmp_path / ".claude" / "plugins").exists()
    assert not (tmp_path / ".claude" / "commands").exists()

    # Direct flat skills in .claude/skills/
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-spec" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-plan" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos-ops" / "SKILL.md").exists()
    assert len([d for d in (tmp_path / ".claude" / "skills").iterdir() if d.is_dir()]) == 4

    # Verify .claude/.eidos-manifest.json tracks skills and target hook intent, but NOT settings.json
    manifest_file = tmp_path / ".claude" / ".eidos-manifest.json"
    assert manifest_file.exists()
    manifest = orjson.loads(manifest_file.read_bytes())
    assert manifest["version"] == get_bundled_plugin_version()
    assert "settings.json" not in manifest["files"]
    assert "skills/eidos/SKILL.md" in manifest["files"]
    assert manifest["targets"]["claude"]["hooks"] is True

    # Verify settings.json registers Stop hook while preserving user keys
    assert settings_file.exists()
    settings = orjson.loads(settings_file.read_bytes())
    assert settings.get("theme") == "dark"
    stop_hooks = settings.get("hooks", {}).get("Stop", [])
    assert len(stop_hooks) == 1
    assert stop_hooks[0] == {"hooks": [{"type": "command", "command": "eidos hook", "timeout": 30}]}

    # Re-installing with launcher="uvx" and hooks=True updates the Stop hook in-place without duplicating entries
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, launcher="uvx", hooks=True)
    settings_uvx = orjson.loads(settings_file.read_bytes())
    stop_hooks_uvx = settings_uvx.get("hooks", {}).get("Stop", [])
    assert len(stop_hooks_uvx) == 1
    assert stop_hooks_uvx[0]["hooks"][0]["command"] == f"uvx --from eidos-ai=={get_bundled_plugin_version()} eidos hook"

    # Verify get_installed_plugin_version detects Claude installation via .claude/.eidos-manifest.json
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()


def test_plugin_uninstall_claude_plugin(tmp_path):
    """Verify uninstalling Claude Code cleans .claude/skills/ and .claude/settings.json."""
    # Install first with hooks=True
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, hooks=True)
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
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, hooks=True)
    settings_data = orjson.loads(settings_file.read_bytes())
    settings_data["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "echo user-stop"})
    settings_file.write_bytes(orjson.dumps(settings_data))

    # Re-installing updates Eidos Stop hook in-place without dropping the coexisting user Stop sub-hook
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, launcher="uvx", hooks=True)
    reinstalled_data = orjson.loads(settings_file.read_bytes())
    assert len(reinstalled_data["hooks"]["Stop"][0]["hooks"]) == 2
    assert any(h.get("command") == "echo user-stop" for h in reinstalled_data["hooks"]["Stop"][0]["hooks"])

    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path)
    assert settings_file.exists()
    preserved = orjson.loads(settings_file.read_bytes())
    assert preserved["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo user-stop"}]}]


def test_cli_install_and_uninstall_shortcut_claude(tmp_path):
    """Verify 'eidos plugin install claude [--hooks]' and 'eidos plugin uninstall claude' work and root shortcuts are removed."""
    # Root shortcuts are removed
    assert runner.invoke(app, ["install", "claude", "--project-dir", str(tmp_path)]).exit_code != 0
    assert runner.invoke(app, ["uninstall", "claude", "--project-dir", str(tmp_path)]).exit_code != 0

    # Default install without --hooks does not create .claude/settings.json
    res_no_hooks = runner.invoke(app, ["plugin", "install", "claude", "--project-dir", str(tmp_path)])
    assert res_no_hooks.exit_code == 0
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()

    # Install with --hooks creates .claude/settings.json
    res = runner.invoke(app, ["plugin", "install", "claude", "--hooks", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".claude" / "plugins").exists()

    # Uninstall with positional argument under 'plugin'
    res = runner.invoke(app, ["plugin", "uninstall", "claude", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Success!" in res.stdout
    assert not (tmp_path / ".claude" / "skills" / "eidos").exists()
    assert not (tmp_path / ".claude").exists()


def test_cli_install_rejects_workspace_target(tmp_path):
    """Verify 'workspace' target alias is removed in favor of 'antigravity'."""
    res = runner.invoke(app, ["plugin", "install", "workspace", "--project-dir", str(tmp_path)])
    assert res.exit_code != 0
    assert "Invalid value" in res.output or "invalid" in res.output.lower()


def test_plugin_install_and_uninstall_cursor(tmp_path):
    """Verify Cursor installation creates .agents/skills, root AGENTS.md block, and opt-in .cursor/hooks.json."""
    # Default install (hooks=False): no .cursor/hooks.json and no .agents/hooks.json
    results = install_plugin(target=PluginInstallTarget.CURSOR, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "cursor"
    agent_dir = tmp_path / ".agents"
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert not (agent_dir / "hooks.json").exists()
    assert not (tmp_path / ".cursor" / "hooks.json").exists()
    assert "targets=cursor:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Opt-in hooks=True creates .cursor/hooks.json with version: 1, sessionStart, and stop (loop_limit: 1)
    install_plugin(target=PluginInstallTarget.CURSOR, project_dir=tmp_path, force=True, hooks=True)
    cursor_hooks_file = tmp_path / ".cursor" / "hooks.json"
    assert cursor_hooks_file.exists()
    cursor_hooks = orjson.loads(cursor_hooks_file.read_bytes())
    assert cursor_hooks == {
        "version": 1,
        "hooks": {
            "sessionStart": [{"command": "eidos hook"}],
            "stop": [{"command": "eidos hook", "loop_limit": 1}],
        },
    }

    # Coexistence with user hook in .cursor/hooks.json and uvx launcher upgrade
    cursor_hooks["hooks"]["stop"].append({"command": "echo custom-cursor", "loop_limit": 2})
    cursor_hooks_file.write_bytes(orjson.dumps(cursor_hooks))
    install_plugin(target=PluginInstallTarget.CURSOR, project_dir=tmp_path, force=True, launcher="uvx", hooks=True)
    cursor_uvx = orjson.loads(cursor_hooks_file.read_bytes())
    assert len(cursor_uvx["hooks"]["stop"]) == 2
    assert cursor_uvx["hooks"]["stop"][0] == {
        "command": f"uvx --from eidos-ai=={get_bundled_plugin_version()} eidos hook",
        "loop_limit": 1,
    }
    assert cursor_uvx["hooks"]["stop"][1] == {"command": "echo custom-cursor", "loop_limit": 2}

    # Uninstall removes Eidos stop entry while preserving custom user entry
    uninst = uninstall_plugin(target=PluginInstallTarget.CURSOR, project_dir=tmp_path)
    assert len(uninst) == 1 and uninst[0].success
    assert not (agent_dir / "skills").exists()
    assert orjson.loads(cursor_hooks_file.read_bytes()) == {
        "version": 1,
        "hooks": {"stop": [{"command": "echo custom-cursor", "loop_limit": 2}]},
    }


def test_plugin_install_and_uninstall_aider(tmp_path):
    """Verify Aider installation creates .aider/skills, root AGENTS.md block, and .aider.conf.yml read: entry."""
    results = install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "aider"

    assert (tmp_path / ".aider" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (tmp_path / ".aider.conventions.md").exists()
    assert "targets=aider:.aider/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in (tmp_path / ".aider.conf.yml").read_text(encoding="utf-8")

    uninst = uninstall_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path)
    assert len(uninst) == 1 and uninst[0].success
    assert not (tmp_path / ".aider").exists()
    assert not (tmp_path / ".aider.conf.yml").exists()


def test_plugin_install_and_uninstall_cline_kilocode_opencode(tmp_path):
    """Verify Cline, Kilo Code, and OpenCode install skills into .agents/skills/ and update root AGENTS.md (plus opt-in Cline PreToolUse hook)."""
    for target in (PluginInstallTarget.CLINE, PluginInstallTarget.KILOCODE, PluginInstallTarget.OPEN_CODE):
        ws = tmp_path / target.value
        ws.mkdir()
        results = install_plugin(target=target, project_dir=ws, force=True)
        assert len(results) == 1 and results[0].success
        assert (ws / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
        assert not (ws / ".clinerules" / "hooks" / "PreToolUse").exists()
        assert f"targets={target.value}:.agents/skills" in (ws / "AGENTS.md").read_text(encoding="utf-8")

        if target == PluginInstallTarget.CLINE:
            install_plugin(target=target, project_dir=ws, force=True, hooks=True)
            cline_hook = ws / ".clinerules" / "hooks" / "PreToolUse"
            assert cline_hook.is_file()
            assert "exec eidos hook" in cline_hook.read_text(encoding="utf-8")
            assert (ws / ".clinerules" / "hooks" / "TaskStart").is_file()

        uninst = uninstall_plugin(target=target, project_dir=ws)
        assert len(uninst) == 1 and uninst[0].success
        assert not (ws / ".agents").exists()
        assert not (ws / ".clinerules").exists()


def test_plugin_install_and_uninstall_codex(tmp_path):
    """Verify OpenAI Codex installation creates .agents/skills, root AGENTS.md block, and opt-in .codex/hooks.json."""
    results = install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "codex"

    assert not (tmp_path / ".codex" / "skills").exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
    agent_dir = tmp_path / ".agents"
    assert not (agent_dir / "hooks.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert "targets=codex:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert len([d for d in (agent_dir / "skills").iterdir() if d.is_dir()]) == 4

    # Opt-in hooks=True creates .codex/hooks.json
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True, hooks=True)
    codex_hooks_file = tmp_path / ".codex" / "hooks.json"
    assert codex_hooks_file.exists()
    assert orjson.loads(codex_hooks_file.read_bytes()) == {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "eidos hook", "timeout": 10}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "eidos hook", "timeout": 30}]}],
        }
    }

    # Verify version detection
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()

    # Uninstall removes .agents/skills and .codex/hooks.json
    uninst = uninstall_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not (agent_dir / "skills").exists()
    assert not codex_hooks_file.exists()


def test_plugin_install_and_uninstall_copilot(tmp_path):
    """Verify GitHub Copilot installation creates .agents/skills, root AGENTS.md block, and opt-in .github/hooks/hooks.json."""
    results = install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success
    assert results[0].target == "copilot"

    assert not (tmp_path / ".github" / "plugin").exists()
    assert not (tmp_path / ".github" / "skills").exists()
    assert not (tmp_path / ".github" / "copilot").exists()
    assert not (tmp_path / ".github" / "hooks" / "hooks.json").exists()
    agent_dir = tmp_path / ".agents"
    assert not (agent_dir / "hooks.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert len([d for d in (agent_dir / "skills").iterdir() if d.is_dir()]) == 4

    # Opt-in hooks=True creates .github/hooks/hooks.json
    install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True, hooks=True)
    copilot_hooks_file = tmp_path / ".github" / "hooks" / "hooks.json"
    assert copilot_hooks_file.exists()
    assert orjson.loads(copilot_hooks_file.read_bytes()) == {
        "version": 1,
        "hooks": {
            "sessionStart": [
                {
                    "type": "command",
                    "bash": "eidos hook",
                    "powershell": "eidos hook",
                    "timeoutSec": 10,
                }
            ],
            "agentStop": [
                {
                    "type": "command",
                    "bash": "eidos hook",
                    "powershell": "eidos hook",
                    "timeoutSec": 30,
                }
            ],
        },
    }

    # Copilot loads root AGENTS.md natively: no copilot-instructions.md bridge (DLT-2026-121)
    instructions = tmp_path / ".github" / "copilot-instructions.md"
    assert not instructions.exists()
    assert "targets=copilot:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Verify get_installed_plugin_version detects Copilot installation
    installed_ver = get_installed_plugin_version(project_dir=tmp_path)
    assert installed_ver == get_bundled_plugin_version()

    # Uninstall
    uninst = uninstall_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success
    assert not (agent_dir / "skills").exists()
    assert not copilot_hooks_file.exists()

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
    results = install_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path, force=True, hooks=True)
    targets = [r.target for r in results]
    assert targets == ["antigravity", "cursor", "claude", "aider", "cline", "kilocode", "open-code", "codex", "copilot"]
    assert (tmp_path / ".agents" / "hooks.json").exists()
    assert (tmp_path / ".cursor" / "hooks.json").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert (tmp_path / ".clinerules" / "hooks" / "PreToolUse").exists()
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".github" / "hooks" / "hooks.json").exists()
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
    assert (tmp_path / ".aider" / "skills" / "eidos" / "SKILL.md").exists()

    # Uninstall all
    uninst = uninstall_plugin(target=PluginInstallTarget.ALL, project_dir=tmp_path)
    assert len(uninst) == 9
    for u in uninst:
        assert u.success
    assert not (tmp_path / ".agents" / "hooks.json").exists()
    assert not (tmp_path / ".cursor" / "hooks.json").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".clinerules" / "hooks" / "PreToolUse").exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
    assert not (tmp_path / ".github" / "hooks" / "hooks.json").exists()
    assert not (tmp_path / ".agents" / "skills").exists()
    assert not (tmp_path / ".aider").exists()
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".aider.conf.yml").exists()


def test_cli_install_new_agent_shortcuts(tmp_path):
    """Verify 'eidos plugin install' and 'eidos plugin uninstall' across antigravity, cursor, codex, and copilot sharing .agents/skills."""
    # Cursor with --hooks
    res = runner.invoke(app, ["plugin", "install", "cursor", "--hooks", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (tmp_path / ".agents" / "hooks.json").exists()
    assert (tmp_path / ".cursor" / "hooks.json").exists()
    assert "targets=cursor:.agents/skills" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    # Antigravity with --hooks writes .agents/hooks.json; uninstalling antigravity removes .agents/hooks.json
    res = runner.invoke(app, ["plugin", "install", "antigravity", "--hooks", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "hooks.json").exists()
    res = runner.invoke(app, ["plugin", "uninstall", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert not (tmp_path / ".agents" / "hooks.json").exists()

    # Codex
    res = runner.invoke(app, ["plugin", "install", "codex", "--hooks", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()

    # Copilot
    res = runner.invoke(app, ["plugin", "install", "copilot", "--hooks", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".github" / "hooks" / "hooks.json").exists()
    assert not (tmp_path / ".github" / "copilot-instructions.md").exists()
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()

    # Uninstall all
    res = runner.invoke(app, ["plugin", "uninstall", "all", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert not (tmp_path / ".agents" / "skills").exists()


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


def test_plugin_manifest_semver_enforcement(tmp_path, monkeypatch):
    """Verify validate_plugin_package rejects missing skills/ or invalid SemVer version (CTR-EIDOS-CLI)."""
    plugin_dir = tmp_path / "test_plugin"
    plugin_dir.mkdir()

    # Missing skills/ directory
    report_missing = validate_plugin_package(plugin_dir)
    assert not report_missing.is_valid
    assert any("Missing required 'skills' directory" in err for err in report_missing.errors)

    # Valid skill directory but invalid SemVer from get_bundled_plugin_version
    skill_dir = plugin_dir / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo\n---\n# Demo\n", encoding="utf-8")
    monkeypatch.setattr("eidos.plugin.validator.get_bundled_plugin_version", lambda: "v1.0")
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
    manifest_path = tmp_path / ".agents" / ".eidos-manifest.json"
    data = orjson.loads(manifest_path.read_bytes())
    data["version"] = "0.0.9"
    manifest_path.write_bytes(orjson.dumps(data))

    status_outdated = check_plugin_version_status(project_dir=tmp_path)
    assert status_outdated["status"] == "outdated"
    assert status_outdated["installed_version"] == "0.0.9"

    # 4. Newer
    data["version"] = "9.9.9"
    manifest_path.write_bytes(orjson.dumps(data))

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
    manifest_path = tmp_path / ".agents" / ".eidos-manifest.json"
    data = orjson.loads(manifest_path.read_bytes())
    data["version"] = "0.0.8"
    manifest_path.write_bytes(orjson.dumps(data))

    res_outdated = runner.invoke(app, ["plugin", "version", "--project-dir", str(tmp_path)])
    assert res_outdated.exit_code == 0
    assert "Outdated" in res_outdated.stdout
    assert "eidos plugin update" in res_outdated.stdout


def test_cli_plugin_update(tmp_path):
    """Verify 'eidos plugin update' CLI command upgrades older plugin versions (CTR-EIDOS-CLI)."""
    # Install initially
    runner.invoke(app, ["plugin", "install", "antigravity", "--project-dir", str(tmp_path)])

    # Mock older installed version
    manifest_path = tmp_path / ".agents" / ".eidos-manifest.json"
    data = orjson.loads(manifest_path.read_bytes())
    data["version"] = "0.0.5"
    manifest_path.write_bytes(orjson.dumps(data))

    # Run update
    res = runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Upgraded Eidos Agent Plugin" in res.stdout
    assert "0.0.5" in res.stdout
    assert get_bundled_plugin_version() in res.stdout

    # Verify manifest version was updated
    updated_data = orjson.loads(manifest_path.read_bytes())
    assert updated_data["version"] == get_bundled_plugin_version()


def test_cli_plugin_upgrade_alias(tmp_path):
    """Verify 'eidos plugin upgrade' alias is removed in favor of 'eidos plugin update' (CTR-EIDOS-CLI)."""
    res_removed = runner.invoke(app, ["plugin", "upgrade", "antigravity", "--project-dir", str(tmp_path)])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["plugin", "update", "antigravity", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md").exists()


def test_no_assets_in_src():
    """Verify schemas and plugin bundles are relocated out of src/ into assets/."""
    assert not Path("src/eidos/schemas").exists(), "src/eidos/schemas should not exist"
    assert not Path("src/eidos/plugin_bundle").exists(), "src/eidos/plugin_bundle should not exist"


def test_assets_plugin_reference_conformance():
    """Verify that assets/plugin_reference satisfies agentskills.io."""
    ref_plugin = Path("assets/plugin_reference")
    assert ref_plugin.exists(), "assets/plugin_reference missing"
    report = validate_plugin_package(ref_plugin)
    assert report.is_valid, f"assets/plugin_reference invalid: {report.errors}"


def test_assets_and_installed_plugin_parity():
    """Verify assets/plugin_reference/skills and .agents/skills have identical structure and content."""
    ref_skills = Path("assets/plugin_reference/skills")
    installed_skills = Path(".agents/skills")
    assert ref_skills.exists()
    if not installed_skills.exists():
        pytest.skip(".agents/skills not installed in this environment")

    ref_files = {p.relative_to(ref_skills) for p in ref_skills.rglob("*") if p.is_file() and ".eidos" not in p.parts}
    inst_files = {
        p.relative_to(installed_skills) for p in installed_skills.rglob("*") if p.is_file() and ".eidos" not in p.parts
    }
    assert ref_files == inst_files, f"File mismatch: {ref_files ^ inst_files}"

    for rel in sorted(ref_files):
        f1 = (ref_skills / rel).read_text(encoding="utf-8")
        f2 = (installed_skills / rel).read_text(encoding="utf-8")
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
    assert set(drift_gate.keys()) == {"SessionStart", "Stop"}
    assert drift_gate["SessionStart"] == [{"type": "command", "command": "eidos hook", "timeout": 10}]
    assert drift_gate["Stop"] == [{"type": "command", "command": "eidos hook", "timeout": 30}]


def test_cli_lint_command(tmp_path):
    """Verify 'eidos validate' CLI command executes and root 'eidos lint' shortcut is removed (CTR-EIDOS-CLI)."""
    from eidos.core.lifecycle.initializer import init_repository

    init_repository(tmp_path, title="Validate CLI Test")
    res_removed = runner.invoke(app, ["lint", str(tmp_path / "eidos")])
    assert res_removed.exit_code != 0

    res = runner.invoke(app, ["validate", str(tmp_path / "eidos")])
    assert res.exit_code == 0
    assert "Success! All" in res.stdout


def test_drift_hook_stop_event_drift_detected(tmp_path, monkeypatch):
    """Verify INV-AGENT-DRIFT-HOOK: Stop hook honors camelCase stopHookActive, gates Cline PreToolUse strictly on attempt_completion, and short-circuits zero-edit Q&A turns via session baselines."""
    import eidos.vcs.drift as drift_mod
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
    (src_dir / "feature.py").write_text("def do_feature(): pass\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "agent@eidos.ai"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Agent"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "eidos"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init specs"], cwd=tmp_path, capture_output=True, check=True)

    stop_payload = orjson.dumps(
        {
            "executionNum": 1,
            "terminationReason": "NO_TOOL_CALL",
            "workspacePaths": [str(tmp_path)],
        }
    ).decode("utf-8")
    res = runner.invoke(app, ["hook"], input=stop_payload)
    assert res.exit_code == 0
    data = orjson.loads(res.stdout)
    assert data["decision"] == "continue"
    assert "⚠️ [Eidos Stop Hook]" in data["reason"]
    assert "src/feature.py" in data["reason"]
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()

    # On retry turn (executionNum > 1), Antigravity receives {"decision": "allow"}
    retry_payload = orjson.dumps(
        {
            "executionNum": 2,
            "terminationReason": "NO_TOOL_CALL",
            "workspacePaths": [str(tmp_path)],
        }
    ).decode("utf-8")
    res_retry = runner.invoke(app, ["hook"], input=retry_payload)
    assert res_retry.exit_code == 0
    assert orjson.loads(res_retry.stdout) == {"decision": "allow"}

    # GitHub Copilot camelCase stopHookActive circuit-breaker prevents infinite agentStop retry loops
    copilot_first = {"stopHookActive": False, "cwd": str(tmp_path)}
    copilot_retry = {"stopHookActive": True, "cwd": str(tmp_path)}
    assert handle_stop_hook(copilot_first, tmp_path)["decision"] == "block"
    assert handle_stop_hook(copilot_retry, tmp_path) == {}

    # Cline PreToolUse: non-attempt_completion tool calls return {"cancel": False} WITHOUT calling detect_drift
    real_detect_drift = drift_mod.detect_drift
    drift_calls = 0

    def _counted_detect_drift(*args, **kwargs):
        nonlocal drift_calls
        drift_calls += 1
        return real_detect_drift(*args, **kwargs)

    monkeypatch.setattr("eidos.plugin.hooks.detect_drift", _counted_detect_drift)
    cline_read = {
        "clineVersion": "3.36.0",
        "hookName": "PreToolUse",
        "taskId": "cline-task-1",
        "preToolUse": {"toolName": "read_file"},
        "workspaceRoots": [str(tmp_path)],
    }
    assert handle_stop_hook(cline_read, tmp_path) == {"cancel": False}
    assert drift_calls == 0

    # Cline PreToolUse on attempt_completion blocks on turn 1 and allows on consecutive retry of same state
    cline_complete = {
        "clineVersion": "3.36.0",
        "hookName": "PreToolUse",
        "taskId": "cline-task-1",
        "preToolUse": {"tool": "attempt_completion"},
        "workspaceRoots": [str(tmp_path)],
    }
    res_cline_1 = handle_stop_hook(cline_complete, tmp_path)
    assert res_cline_1["cancel"] is True
    assert "⚠️ [Eidos Stop Hook]" in res_cline_1["errorMessage"]
    assert drift_calls == 1
    assert handle_stop_hook(cline_complete, tmp_path) == {"cancel": False}

    # Explicit client empty or doc-only modified_files / editedFiles short-circuits without running detect_drift
    drift_calls = 0
    assert handle_stop_hook({"stop_hook_active": False, "cwd": str(tmp_path), "modified_files": []}, tmp_path) == {}
    assert handle_stop_hook({"stop_hook_active": False, "cwd": str(tmp_path), "editedFiles": ["   "]}, tmp_path) == {}
    assert (
        handle_stop_hook({"stop_hook_active": False, "cwd": str(tmp_path), "modified_files": ["README.md"]}, tmp_path)
        == {}
    )
    assert drift_calls == 0

    # Session-scoped baseline (SessionStart / UserPromptSubmit): pre-existing dirty worktree Q&A turn passes through,
    # whereas editing a governed file during the session triggers the Stop drift gate.
    import os

    from eidos.plugin.hooks import _prune_stale_baselines, capture_session_baseline

    _prune_stale_baselines(tmp_path / "nonexistent_baselines_dir")
    assert capture_session_baseline({"cwd": str(tmp_path / "no_eidos")}, tmp_path / "no_eidos") == {}

    sess_start = orjson.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-qa-dirty",
            "cwd": str(tmp_path),
        }
    ).decode("utf-8")
    res_sess = runner.invoke(app, ["hook"], input=sess_start)
    assert res_sess.exit_code == 0
    assert orjson.loads(res_sess.stdout) == {}
    baseline_dir = eidos_dir / ".cache" / "hook_baselines"
    assert any(baseline_dir.glob("*.json"))

    # Stale baseline file (>24h old) is pruned automatically
    stale_file = baseline_dir / "session_stale.json"
    stale_file.write_text("{}", encoding="utf-8")
    old_ts = 1000.0
    os.utime(stale_file, (old_ts, old_ts))
    _prune_stale_baselines(baseline_dir)
    assert not stale_file.exists()

    drift_calls = 0
    res_qa_stop = handle_stop_hook(
        {"hook_event_name": "Stop", "session_id": "sess-qa-dirty", "stop_hook_active": False, "cwd": str(tmp_path)},
        tmp_path,
    )
    assert res_qa_stop == {}
    assert drift_calls == 0

    # Doc-only change after SessionStart still short-circuits without blocking
    (tmp_path / "README.md").write_text("# Notes\n", encoding="utf-8")
    assert (
        handle_stop_hook(
            {"hook_event_name": "Stop", "session_id": "sess-qa-dirty", "stop_hook_active": False, "cwd": str(tmp_path)},
            tmp_path,
        )
        == {}
    )
    assert drift_calls == 0

    # Corrupt baseline file falls back safely to full detect_drift; symlink/deleted/renamed files snapshot cleanly
    (tmp_path / "link_note.md").symlink_to("README.md")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-qm", "readme"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "mv", "README.md", "GUIDE.md"], cwd=tmp_path, capture_output=True, check=True)
    capture_session_baseline({"session_id": "sess-edge"}, tmp_path)
    (tmp_path / "GUIDE.md").unlink()
    capture_session_baseline({"session_id": "sess-edge-del"}, tmp_path)

    corrupt_bf = baseline_dir / "session_default.json"
    corrupt_bf.write_text("{bad-json", encoding="utf-8")
    assert handle_stop_hook({"stop_hook_active": False, "cwd": str(tmp_path)}, tmp_path)["decision"] == "block"

    # Non-git Cline workspace uses stat-based state digest & recovers from corrupt retry marker
    non_git_ws = tmp_path / "non_git_cline"
    (non_git_ws / "eidos").mkdir(parents=True)
    (non_git_ws / "eidos" / "bad.adoc").write_text("---\ntype: product/policy\nid: BAD\n---\n", encoding="utf-8")
    ng_payload = {
        "clineVersion": "3.36.0",
        "hookName": "PreToolUse",
        "preToolUse": {"toolName": "attempt_completion"},
        "workspaceRoots": [str(non_git_ws)],
    }
    from eidos.plugin import hooks as hooks_mod
    from eidos.plugin.hooks import _cline_retry_filename

    orig_snap = hooks_mod._snapshot_dirty_worktree
    monkeypatch.setattr(hooks_mod, "_snapshot_dirty_worktree", lambda _r: None)
    assert handle_stop_hook(ng_payload, non_git_ws)["cancel"] is True
    assert handle_stop_hook(ng_payload, non_git_ws) == {"cancel": False}
    ng_marker_dir = non_git_ws / "eidos" / ".cache" / "hook_baselines"
    ng_marker_dir.mkdir(parents=True, exist_ok=True)
    (ng_marker_dir / _cline_retry_filename(ng_payload, non_git_ws.resolve())).write_text("{corrupt", encoding="utf-8")
    assert handle_stop_hook(ng_payload, non_git_ws)["cancel"] is True
    monkeypatch.setattr(hooks_mod, "_snapshot_dirty_worktree", orig_snap)

    # Now the agent modifies a governed file during the session -> Stop blocks!
    (src_dir / "new_edit.py").write_text("def added_in_session(): return 42\n", encoding="utf-8")
    res_edit_stop = handle_stop_hook(
        {"hook_event_name": "Stop", "session_id": "sess-qa-dirty", "stop_hook_active": False, "cwd": str(tmp_path)},
        tmp_path,
    )
    assert res_edit_stop["decision"] == "block"
    assert "src/new_edit.py" in res_edit_stop["reason"]

    # Also verify invalid spec produces a validation reason on turn 1
    (eidos_dir / "invalid.adoc").write_text(
        "---\ntype: product/policy\nid: BAD-POLICY\n---\n",
        encoding="utf-8",
    )
    res_invalid = runner.invoke(app, ["hook"], input=stop_payload)
    assert res_invalid.exit_code == 0
    data_invalid = orjson.loads(res_invalid.stdout)
    assert data_invalid["decision"] == "continue"
    assert "Specification validation failed" in data_invalid["reason"]


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
            "terminationReason": "NO_TOOL_CALL",
            "workspacePaths": [str(tmp_path)],
        }
    ).decode("utf-8")
    res = runner.invoke(app, ["hook"], input=stop_payload)
    assert res.exit_code == 0
    data = orjson.loads(res.stdout)
    assert data == {"decision": "allow"}


def test_stateless_stop_hook_no_cache_or_block_loop(tmp_path):
    """Verify handle_stop_hook enforces stateless 1-turn continuation across all 5 hook-capable clients (DLT-2026-128)."""
    from eidos.plugin.hooks import find_workspace_root, handle_stop_hook

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

    # 1. Antigravity: executionNum <= 1 and terminationReason in ("", "NO_TOOL_CALL") -> continue; otherwise allow
    ag_turn1 = {"executionNum": 1, "terminationReason": "NO_TOOL_CALL", "workspacePaths": [str(tmp_path)]}
    ag_turn2 = {"executionNum": 2, "terminationReason": "NO_TOOL_CALL", "workspacePaths": [str(tmp_path)]}
    ag_max_turns = {"executionNum": 1, "terminationReason": "MAX_TURNS", "workspacePaths": [str(tmp_path)]}
    res_ag1 = handle_stop_hook(ag_turn1, tmp_path)
    assert res_ag1["decision"] == "continue"
    assert "⚠️ [Eidos Stop Hook]" in res_ag1["reason"]
    assert handle_stop_hook(ag_turn2, tmp_path) == {"decision": "allow"}
    assert handle_stop_hook(ag_max_turns, tmp_path) == {"decision": "allow"}

    # 2. Cursor: loop_count == 0 and status == "completed" -> followup_message; otherwise {}
    cur_turn1 = {"loop_count": 0, "status": "completed", "workspace_roots": [str(tmp_path)]}
    cur_turn2 = {"loop_count": 1, "status": "completed", "workspace_roots": [str(tmp_path)]}
    cur_aborted = {"loop_count": 0, "status": "aborted", "workspace_roots": [str(tmp_path)]}
    assert find_workspace_root(cur_turn1) == tmp_path.resolve()
    res_cur1 = handle_stop_hook(cur_turn1, tmp_path)
    assert "followup_message" in res_cur1
    assert "⚠️ [Eidos Stop Hook]" in res_cur1["followup_message"]
    assert handle_stop_hook(cur_turn2, tmp_path) == {}
    assert handle_stop_hook(cur_aborted, tmp_path) == {}

    # 3. Cline: PreToolUse on attempt_completion -> cancel: True; non-attempt_completion or retry -> cancel: False
    cline_turn1 = {
        "clineVersion": "3.36.0",
        "hookName": "PreToolUse",
        "taskId": "stateless-cline-1",
        "preToolUse": {"toolName": "attempt_completion"},
        "workspaceRoots": [str(tmp_path)],
    }
    cline_other_tool = {
        "clineVersion": "3.36.0",
        "hookName": "PreToolUse",
        "taskId": "stateless-cline-1",
        "preToolUse": {"toolName": "write_to_file"},
        "workspaceRoots": [str(tmp_path)],
    }
    cline_cancel_event = {
        "clineVersion": "3.36.0",
        "hookName": "TaskCancel",
        "workspaceRoots": [str(tmp_path)],
    }
    assert find_workspace_root(cline_turn1) == tmp_path.resolve()
    assert handle_stop_hook(cline_other_tool, tmp_path) == {"cancel": False}
    res_cline1 = handle_stop_hook(cline_turn1, tmp_path)
    assert res_cline1["cancel"] is True
    assert "⚠️ [Eidos Stop Hook]" in res_cline1["contextModification"]
    assert "⚠️ [Eidos Stop Hook]" in res_cline1["errorMessage"]
    assert handle_stop_hook(cline_turn1, tmp_path) == {"cancel": False}
    assert handle_stop_hook(cline_cancel_event, tmp_path) == {"cancel": False}

    # 4. Claude Code, GitHub Copilot, OpenAI Codex: stop_hook_active falsy -> block; truthy -> {}
    std_turn1 = {"stop_hook_active": False, "cwd": str(tmp_path)}
    std_turn2 = {"stop_hook_active": True, "cwd": str(tmp_path)}
    res_std1 = handle_stop_hook(std_turn1, tmp_path)
    assert res_std1["decision"] == "block"
    assert "⚠️ [Eidos Stop Hook]" in res_std1["reason"]
    assert handle_stop_hook(std_turn2, tmp_path) == {}
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()


def test_cli_doctor_command(tmp_path, monkeypatch):
    """Verify 'eidos doctor' and 'eidos doctor --json' CLI health check, custom --path, and virtualenv fallback (DLT-2026-024, DLT-2026-070)."""
    from eidos.core.lifecycle.initializer import init_repository

    doc_ws = tmp_path / "doctor_default_ws"
    doc_ws.mkdir(parents=True)
    init_repository(doc_ws, title="Doctor Default WS")
    subprocess.run(["git", "init"], cwd=doc_ws, capture_output=True, check=True)
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=doc_ws, force=True)
    monkeypatch.chdir(doc_ws)

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
    custom_ws = tmp_path / "custom_ws"
    custom_ws.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=custom_ws, capture_output=True, check=True)
    custom_specs = custom_ws / "custom_specs"
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


def test_cli_doctor_update_check(tmp_path, monkeypatch):
    """Verify INV-DOCTOR-UPDATE-CHECK: eidos doctor checks PyPI via check_cli_update_status and resolves editable Git-tag versions accurately."""
    import io
    import urllib.error

    import eidos
    from eidos import _parse_git_describe_version, resolve_runtime_version
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.plugin.version import check_cli_update_status

    doc_ws = tmp_path / "doctor_upd_ws"
    doc_ws.mkdir(parents=True)
    init_repository(doc_ws, title="Doctor Update WS")
    subprocess.run(["git", "init"], cwd=doc_ws, capture_output=True, check=True)
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=doc_ws, force=True)
    monkeypatch.chdir(doc_ws)

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

    # 4. EIDOS_NO_UPDATE_CHECK=1 skips PyPI HTTP checks cleanly (SKIPPED in Rich table, status == "offline")
    def _forbid_network(*_a, **_kw):
        raise AssertionError("EIDOS_NO_UPDATE_CHECK=1 must not make network requests")

    monkeypatch.setattr("eidos.plugin.version.urllib.request.urlopen", _forbid_network)
    monkeypatch.setenv("EIDOS_NO_UPDATE_CHECK", "1")
    res_env_skip_table = runner.invoke(app, ["doctor"])
    assert res_env_skip_table.exit_code == 0
    assert "SKIPPED" in res_env_skip_table.stdout

    res_env_skip_json = runner.invoke(app, ["doctor", "--json"])
    assert res_env_skip_json.exit_code == 0
    env_skip_data = orjson.loads(res_env_skip_json.stdout)
    assert env_skip_data["healthy"] is True
    assert env_skip_data["checks"]["cli_update"]["status"] == "offline"
    assert env_skip_data["checks"]["cli_update"]["update_available"] is False
    monkeypatch.delenv("EIDOS_NO_UPDATE_CHECK", raising=False)

    # 5. Editable Git-tag version accuracy (resolve_runtime_version)
    assert _parse_git_describe_version("v0.23.0") == "0.23.0"
    assert _parse_git_describe_version("v0.23.0-4-g1234abc") == "0.23.1.dev4"
    assert _parse_git_describe_version("v0.24.0-rc.1") == "0.24.0-rc.1"
    assert _parse_git_describe_version("v0.24.0-rc.1-2-gdeadbeef") == "0.24.0-rc.1.dev2"
    assert _parse_git_describe_version("not-a-tag") is None
    assert _parse_git_describe_version("v1.0.0-0-g1234567") == "1.0.0"

    fake_checkout = tmp_path / "editable_repo"
    fake_pkg = fake_checkout / "src" / "eidos"
    fake_pkg.mkdir(parents=True)
    (fake_checkout / "pyproject.toml").write_text("[project]\nname = 'eidos-ai'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=fake_checkout, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=fake_checkout, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=fake_checkout, check=True)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=fake_checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=fake_checkout, check=True)
    subprocess.run(["git", "tag", "v0.23.0"], cwd=fake_checkout, check=True)
    assert resolve_runtime_version(pkg_root=fake_pkg, use_cache=False) == "0.23.0"

    (fake_checkout / "README.md").write_text("next\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=fake_checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "post tag"], cwd=fake_checkout, check=True)
    assert resolve_runtime_version(pkg_root=fake_pkg, use_cache=False) == "0.23.1.dev1"

    # Non-editable directory falls back cleanly without git overhead, and default call uses module cache
    import importlib
    import importlib.metadata

    importlib.reload(eidos)
    non_editable_pkg = tmp_path / "wheel_site" / "eidos"
    non_editable_pkg.mkdir(parents=True)
    assert eidos.resolve_runtime_version(pkg_root=non_editable_pkg, use_cache=False) == eidos._fallback_package_version()
    assert eidos.resolve_runtime_version() == eidos.__version__
    assert eidos._parse_git_describe_version("") is None
    assert eidos._parse_git_describe_version("foo-2-g1234abc") is None

    orig_import_module = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name, *a, **kw: (_ for _ in ()).throw(ImportError("no _version"))
        if name == "eidos._version"
        else orig_import_module(name, *a, **kw),
    )
    assert isinstance(eidos._fallback_package_version(), str)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda _pkg: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError("eidos-ai")),
    )
    assert eidos._fallback_package_version() == "0.1.0"
    monkeypatch.setattr(importlib, "import_module", orig_import_module)

    orig_subproc_run = subprocess.run
    monkeypatch.setattr(
        "eidos.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("git missing")),
    )
    assert eidos.resolve_runtime_version(pkg_root=fake_pkg, use_cache=False) == eidos._fallback_package_version()
    monkeypatch.setattr("eidos.subprocess.run", orig_subproc_run)

    # 6. Offline / URLError / TimeoutError / malformed payload / EIDOS_NO_UPDATE_CHECK fallback
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

    assert commands == ["eidos hook", "eidos hook"]
    for cmd in commands:
        assert cmd == "eidos hook" or cmd.startswith("eidos hook "), f"Expected native 'eidos hook' command, got: {cmd}"
        assert ".py" not in cmd and ".sh" not in cmd


def test_stop_hook_internal_error_emits_warning(tmp_path, monkeypatch):
    """Verify handle_stop_hook catches unexpected engine errors and returns a non-blocking allow response."""
    from eidos.plugin.hooks import handle_stop_hook

    eidos_dir = tmp_path / "eidos"
    eidos_dir.mkdir()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated internal engine failure")

    monkeypatch.setattr("eidos.plugin.hooks.detect_drift", _boom)

    res_ag = handle_stop_hook({"executionNum": 1}, tmp_path)
    assert res_ag == {"decision": "allow"}
    res_std = handle_stop_hook({"stop_hook_active": False}, tmp_path)
    assert res_std == {}


def test_plugin_install_and_uninstall_preserves_custom_skills(tmp_path):
    """Verify install_plugin and uninstall_plugin manage the 4 active Eidos skills while preserving unrelated user skills (DLT-2026-037)."""
    from eidos.plugin.installer import ACTIVE_EIDOS_SKILLS

    tree_skills = tmp_path / ".agents" / "skills"
    flat_skills = tmp_path / ".claude" / "skills"
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
    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path)
    for active in ACTIVE_EIDOS_SKILLS:
        assert not (flat_skills / active).exists()
    assert (custom_user_skill / "SKILL.md").exists()


def test_plugin_install_creates_manifest_with_hashes(tmp_path):
    """Verify that installing a plugin generates .eidos-manifest.json with SHA-256 hashes and target hook metadata (DLT-2026-046, DLT-2026-128, DLT-2026-130)."""
    import hashlib

    from eidos.plugin.installer import MANIFEST_FILENAME

    # 1. Test ANTIGRAVITY target manifest
    results = install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)
    assert len(results) == 1
    assert results[0].success

    dest = tmp_path / ".agents"
    manifest_file = dest / MANIFEST_FILENAME
    assert manifest_file.exists(), f"Expected manifest file at {manifest_file}"

    manifest_data = orjson.loads(manifest_file.read_bytes())

    assert manifest_data["version"] == get_bundled_plugin_version()
    assert manifest_data["targets"]["antigravity"] == {"hooks": False, "launcher": "eidos"}
    assert "files" in manifest_data
    files = manifest_data["files"]
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
    codex_manifest_file = codex_root / ".agents" / MANIFEST_FILENAME
    assert codex_manifest_file.exists()
    codex_manifest = orjson.loads(codex_manifest_file.read_bytes())
    assert "rules.md" not in codex_manifest["files"]
    assert "skills/eidos/SKILL.md" in codex_manifest["files"]
    assert codex_manifest["targets"]["codex"] == {"hooks": False, "launcher": "eidos"}

    # 3. Test CLAUDE target manifest
    claude_root = tmp_path / "claude_project"
    claude_root.mkdir()
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=claude_root, force=True, hooks=True)
    claude_manifest_file = claude_root / ".claude" / MANIFEST_FILENAME
    assert claude_manifest_file.exists()
    claude_manifest = orjson.loads(claude_manifest_file.read_bytes())
    assert "skills/eidos/SKILL.md" in claude_manifest["files"]
    assert claude_manifest["targets"]["claude"] == {"hooks": True, "launcher": "eidos"}


def test_plugin_uninstall_preserves_unmanifested_user_files(tmp_path):
    """Verify non-destructive uninstall removes only manifest-tracked files, preserving user files in .agents and .claude (DLT-2026-046, DLT-2026-127, DLT-2026-130)."""
    from eidos.plugin.installer import MANIFEST_FILENAME

    # 1. Non-destructive uninstall in .agents
    plugin_dir = tmp_path / ".agents"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    user_file = plugin_dir / "user_custom_config.json"
    user_file.write_text('{"user_custom": true}\n', encoding="utf-8")
    user_skill_file = plugin_dir / "skills" / "my-team-skill" / "SKILL.md"
    user_skill_file.parent.mkdir(parents=True, exist_ok=True)
    user_skill_file.write_text("# My Team Skill\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path, force=True)
    assert (plugin_dir / MANIFEST_FILENAME).exists()
    assert (plugin_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert user_file.exists()
    assert user_skill_file.exists()

    uninst = uninstall_plugin(target=PluginInstallTarget.CODEX, project_dir=tmp_path)
    assert len(uninst) == 1
    assert uninst[0].success

    # Manifest-tracked files removed
    assert not (plugin_dir / MANIFEST_FILENAME).exists()
    assert not (plugin_dir / "skills" / "eidos").exists()

    # User-created files and parent directory preserved!
    assert plugin_dir.exists(), ".agents directory should NOT be removed when user files exist"
    assert user_file.exists(), "User file must be preserved"
    assert user_file.read_text(encoding="utf-8") == '{"user_custom": true}\n'
    assert user_skill_file.exists(), "User skill must be preserved"

    # 2. Non-destructive uninstall in .claude (flat skills target)
    claude_ws = tmp_path / "claude_preserve"
    claude_dir = claude_ws / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    user_dest_file = claude_dir / "untracked_user_notes.txt"
    user_dest_file.write_text("keep this safe\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=claude_ws, force=True)
    assert (claude_dir / MANIFEST_FILENAME).exists()
    assert (claude_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert user_dest_file.exists()

    uninst_dest = uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=claude_ws)
    assert len(uninst_dest) == 1
    assert uninst_dest[0].success
    assert not (claude_dir / MANIFEST_FILENAME).exists()
    assert not (claude_dir / "skills" / "eidos").exists()
    assert claude_dir.exists(), ".claude directory should NOT be removed when user files exist"
    assert user_dest_file.exists(), "User file in .claude must be preserved"
    assert user_dest_file.read_text(encoding="utf-8") == "keep this safe\n"


def test_plugin_install_force_false_prevents_overwriting(tmp_path):
    """Verify force=False (default) prevents overwriting pre-existing user files without --force (DLT-2026-046, DLT-2026-121, DLT-2026-130)."""
    # 1. Pre-existing, user-modified Codex skill file
    codex_skill = tmp_path / ".agents" / "skills" / "eidos" / "SKILL.md"
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
        assert rules_file.read_text(encoding="utf-8") == "# Custom User Rules\n"
        assert conv_file.read_text(encoding="utf-8") == "# Custom User Aider Conventions\n"

    # 3. CLI default force=False
    cli_root = tmp_path / "cli_test"
    cli_skill = cli_root / ".agents" / "skills" / "eidos" / "SKILL.md"
    cli_skill.parent.mkdir(parents=True, exist_ok=True)
    cli_skill.write_text("# User Skill CLI\n", encoding="utf-8")

    res = runner.invoke(app, ["plugin", "install", "codex", "-p", str(cli_root)])
    assert res.exit_code == 0
    assert cli_skill.read_text(encoding="utf-8") == "# User Skill CLI\n"

    res_force = runner.invoke(app, ["plugin", "install", "codex", "-p", str(cli_root), "--force"])
    assert res_force.exit_code == 0
    assert "name: eidos" in cli_skill.read_text(encoding="utf-8")


def test_plugin_reinstall_force_false_preserves_manifest_and_uninstalls_cleanly(tmp_path):
    """Verify install_plugin(..., force=False) on an already-installed target retains manifest entries so uninstall_plugin removes all Eidos files (DLT-2026-046, DLT-2026-130)."""
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=True)
    plugin_dir = tmp_path / ".agents"
    assert (plugin_dir / "skills" / "eidos" / "SKILL.md").exists()

    # Re-run install with force=False
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path, force=False)

    # Subsequent uninstall must still remove all manifest-tracked Eidos files and directory
    uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=tmp_path)
    assert not plugin_dir.exists()


def test_plugin_install_force_false_and_uninstall_preserves_preexisting_user_files(tmp_path):
    """Verify pre-existing .codex/rules.md and user scripts/ are preserved across install(force=False) and uninstall() (DLT-2026-046, DLT-2026-130)."""
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
    assert not (tmp_path / ".agents" / "skills" / "eidos").exists()

    # Also verify user scripts/ directory in .agents is preserved when force=False
    custom_ws = tmp_path / "custom_with_scripts"
    user_script = custom_ws / ".agents" / "scripts" / "deploy.sh"
    user_script.parent.mkdir(parents=True, exist_ok=True)
    user_script.write_text("#!/bin/sh\necho deploy\n", encoding="utf-8")

    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=custom_ws, force=False)
    assert user_script.exists(), "User scripts/ directory must not be deleted when force=False"


def test_plugin_install_refuses_symlink_destination(tmp_path):
    """Verify install_plugin refuses destination symlinks and parent symlinks to prevent symlink traversal (DLT-2026-046, DLT-2026-130)."""
    # 1. Symlinked destination directory (.agents/skills)
    real_target_dir = tmp_path / "real_target"
    real_target_dir.mkdir()
    symlink_ws = tmp_path / "symlink_dest_ws"
    (symlink_ws / ".agents").mkdir(parents=True)
    (symlink_ws / ".agents" / "skills").symlink_to(real_target_dir)

    with pytest.raises(FileExistsError, match="symlink"):
        install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=symlink_ws)

    # 2. Symlinked file inside target directory (.agents/skills/eidos/SKILL.md)
    project_dir = tmp_path / "symlink_file_project"
    project_dir.mkdir()
    secret_file = tmp_path / "secret_outside.txt"
    secret_file.write_text("original secret content\n", encoding="utf-8")

    fake_skill_dir = project_dir / ".agents" / "skills" / "eidos"
    fake_skill_dir.mkdir(parents=True, exist_ok=True)
    symlinked_skill = fake_skill_dir / "SKILL.md"
    symlinked_skill.symlink_to(secret_file)

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
    """Verify uvx hook command writing pins eidos-ai package version and upgrades older pinned versions (DLT-2026-046, DLT-2026-130)."""
    from eidos.plugin.installer import _update_project_antigravity_hooks

    bundled_ver = get_bundled_plugin_version()

    # 1. Direct function update (replaces older pinned uvx while preserving user hooks)
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_bytes(
        orjson.dumps(
            {
                "user-custom-hook": {
                    "Stop": [{"type": "command", "command": "echo custom"}],
                },
                "eidos-drift-gate": {
                    "Stop": [{"type": "command", "command": "uvx --from eidos-ai==0.1.0 eidos hook", "timeout": 30}],
                },
            },
            option=orjson.OPT_INDENT_2,
        )
    )

    success = _update_project_antigravity_hooks(hooks_file, launcher="uvx")
    assert success
    rewritten = hooks_file.read_text(encoding="utf-8")
    assert f'"command": "uvx --from eidos-ai=={bundled_ver} eidos hook"' in rewritten
    assert "eidos-ai==0.1.0" not in rewritten
    assert "user-custom-hook" in rewritten

    # 2. End-to-end install with launcher="uvx" and hooks=True
    dest = tmp_path / "installed_uvx"
    dest.mkdir()
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=dest, force=True, launcher="uvx", hooks=True)
    installed_hooks = dest / ".agents" / "hooks.json"
    assert installed_hooks.exists()
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

    # 2. handle_stop_hook emits continuation reason with cause-specific invariant message (not "0 code file(s) were modified")
    stop_res = handle_stop_hook({"executionNum": 1, "terminationReason": "NO_TOOL_CALL"}, tmp_path)
    assert stop_res["decision"] == "continue"
    assert "0 code file(s) were modified" not in stop_res["reason"]
    assert "Invariant test binding verification failed" in stop_res["reason"]

    # 3. execute_hook exits 0 and emits JSON protocol output
    rc = execute_hook(path=tmp_path, stdin_data="")
    assert rc == 0


def test_dlt_2026_071_plugin_installer_and_validator_fixes(tmp_path):
    """Verify DLT-2026-071, DLT-2026-128 & DLT-2026-130: project-native Claude skills/Stop hook without commands, Path-typed created_files, accurate fallback plugin_dir, and line-anchored SKILL.md frontmatter."""
    # 1. Claude install configures .claude/skills/ and opt-in .claude/settings.json Stop hook without .claude/plugins/eidos or commands
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=tmp_path, force=True, hooks=True)
    assert not (tmp_path / ".claude" / "plugins").exists()
    assert not (tmp_path / ".claude" / "commands").exists()
    assert (tmp_path / ".claude" / "skills" / "eidos" / "SKILL.md").exists()

    # 2. Copilot created_files contains only Path instances (no bool)
    copilot_res = install_plugin(target=PluginInstallTarget.COPILOT, project_dir=tmp_path, force=True, hooks=True)
    assert len(copilot_res) == 1
    assert all(isinstance(p, Path) and not isinstance(p, bool) for p in copilot_res[0].created_files)
    assert (tmp_path / ".github" / "hooks" / "hooks.json") in copilot_res[0].created_files

    # 3. Fallback version check reports actual matched plugin directory (.claude for CLAUDE-only, .agents for CODEX)
    claude_proj = tmp_path / "claude_only"
    claude_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=claude_proj, force=True)
    st_claude = check_plugin_version_status(project_dir=claude_proj)
    assert st_claude["is_installed"] is True
    assert Path(st_claude["plugin_dir"]).resolve() == (claude_proj / ".claude").resolve()

    codex_proj = tmp_path / "codex_only"
    codex_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=codex_proj, force=True)
    st = check_plugin_version_status(project_dir=codex_proj)
    assert st["is_installed"] is True
    assert Path(st["plugin_dir"]).resolve() == (codex_proj / ".agents").resolve()

    # 4. Line-anchored SKILL.md frontmatter allows '---' inside YAML scalar values
    custom_plugin = tmp_path / "custom_plugin"
    skill_dir = custom_plugin / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
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

    # 1. Invalid domain spec triggers handle_stop_hook 1-turn continuation statelessly without writing stop-gate.json
    bad_dom = dom_dir / "bad.adoc"
    bad_dom.write_text("---\ntype: product/domain\nid: BAD\n---\n= Bad\n", encoding="utf-8")

    lint1 = handle_stop_hook({"executionNum": 1, "terminationReason": "NO_TOOL_CALL"}, tmp_path)
    assert lint1.get("decision") == "continue"
    assert "Specification validation failed" in lint1.get("reason", "")
    lint2 = handle_stop_hook({"executionNum": 2, "terminationReason": "NO_TOOL_CALL"}, tmp_path)
    assert lint2 == {"decision": "allow"}
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()

    # 2. uninstall_plugin preserves coexisting user settings in .agents/hooks.json, .claude/settings.json, .cursor/hooks.json, .codex/hooks.json, .github/hooks/hooks.json
    ws = tmp_path / "uninstall_ws"
    (ws / ".agents").mkdir(parents=True)
    (ws / ".claude").mkdir(parents=True)
    (ws / ".cursor").mkdir(parents=True)
    (ws / ".codex").mkdir(parents=True)
    (ws / ".github" / "hooks").mkdir(parents=True)
    (ws / ".agents" / "hooks.json").write_bytes(
        orjson.dumps({"user-hook": {"Stop": [{"type": "command", "command": "echo ag-user"}]}})
    )
    (ws / ".claude" / "settings.json").write_bytes(
        orjson.dumps(
            {"theme": "dark", "hooks": {"CustomHook": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}
        )
    )
    (ws / ".cursor" / "hooks.json").write_bytes(
        orjson.dumps({"version": 1, "hooks": {"stop": [{"command": "echo cursor-user", "loop_limit": 2}]}})
    )
    (ws / ".codex" / "hooks.json").write_bytes(
        orjson.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo codex-user"}]}]}})
    )
    (ws / ".github" / "hooks" / "hooks.json").write_bytes(
        orjson.dumps({"version": 1, "hooks": {"agentStop": [{"type": "command", "bash": "echo copilot-user"}]}})
    )

    install_plugin(target=PluginInstallTarget.ALL, project_dir=ws, force=True, hooks=True)
    uninstall_plugin(target=PluginInstallTarget.ALL, project_dir=ws)

    ag_after = orjson.loads((ws / ".agents" / "hooks.json").read_bytes())
    assert "user-hook" in ag_after
    assert "eidos-drift-gate" not in ag_after

    claude_after = orjson.loads((ws / ".claude" / "settings.json").read_bytes())
    assert claude_after.get("theme") == "dark"
    assert "CustomHook" in claude_after.get("hooks", {})
    assert "Stop" not in claude_after.get("hooks", {})

    cursor_after = orjson.loads((ws / ".cursor" / "hooks.json").read_bytes())
    assert cursor_after["hooks"]["stop"] == [{"command": "echo cursor-user", "loop_limit": 2}]

    codex_after = orjson.loads((ws / ".codex" / "hooks.json").read_bytes())
    assert codex_after["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo codex-user"}]}]

    copilot_after = orjson.loads((ws / ".github" / "hooks" / "hooks.json").read_bytes())
    assert copilot_after["hooks"]["agentStop"] == [{"type": "command", "bash": "echo copilot-user"}]

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
        _copy_skills_flat,
        _remove_project_antigravity_hooks,
        _remove_project_claude_settings,
        _remove_project_codex_hooks,
        _remove_project_copilot_hooks,
        _remove_project_cursor_hooks,
        _uninstall_from_manifest,
        _update_project_antigravity_hooks,
        _update_project_claude_settings,
        _update_project_codex_hooks,
        _update_project_copilot_hooks,
        _update_project_cursor_hooks,
        locate_plugin_bundle,
    )
    from eidos.plugin.validator import validate_plugin_package
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
    bad_ver_dir = bad_ver_ws / ".agents"
    bad_ver_dir.mkdir(parents=True)
    (bad_ver_dir / ".eidos-manifest.json").write_text("{corrupt", encoding="utf-8")
    assert get_installed_plugin_version(project_dir=bad_ver_ws) is None
    (bad_ver_dir / ".eidos-manifest.json").write_text('{"version": "not-semver"}', encoding="utf-8")
    st_inv = check_plugin_version_status(project_dir=bad_ver_ws)
    assert st_inv["status"] == "invalid"

    # 2. plugin/validator.py: missing dir, file instead of dir, missing skills/, skill naming & frontmatter edge cases, hooks.json edge cases
    assert not validate_plugin_package(tmp_path / "no_such_plugin").is_valid
    file_not_dir = tmp_path / "file.txt"
    file_not_dir.write_text("hi", encoding="utf-8")
    assert not validate_plugin_package(file_not_dir).is_valid

    pdir = tmp_path / "bad_plugin"
    pdir.mkdir()
    assert not validate_plugin_package(pdir).is_valid  # missing skills/

    s1 = pdir / "skills" / "empty-skill"
    s2 = pdir / "skills" / "no-fm"
    s3 = pdir / "skills" / "non-dict-fm"
    s4 = pdir / "skills" / "missing-fields"
    s5 = pdir / "skills" / "mismatch-dir"
    for d in (s1, s2, s3, s4, s5):
        d.mkdir(parents=True)
    (s2 / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")
    (s3 / "SKILL.md").write_text("---\n- list_item\n---\n# Body\n", encoding="utf-8")
    (s4 / "SKILL.md").write_text("---\nother: 1\n---\n# Body\n", encoding="utf-8")
    (s5 / "SKILL.md").write_text("---\nname: BAD--NAME\ndescription: Desc\n---\n# Body\n", encoding="utf-8")
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
    assert any("does not match directory name" in e for e in rep_bad.errors)
    assert any("missing 'SKILL.md'" in w for w in rep_bad.warnings)
    assert any("missing required YAML frontmatter" in e for e in rep_bad.errors)
    assert any("frontmatter is not a dictionary" in e for e in rep_bad.errors)
    assert any("missing 'name' in frontmatter" in e for e in rep_bad.errors)
    assert any("non-existent script" in e for e in rep_bad.errors)
    assert any("event 'PostInvocation' is not permitted" in e for e in rep_bad.errors)

    # Also test non-dict hooks.json
    (pdir / "hooks.json").write_text("[1, 2, 3]", encoding="utf-8")
    rep_hooks_list = validate_plugin_package(pdir)
    assert any("root must be a dictionary" in e for e in rep_hooks_list.errors)

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
    assert handle_stop_hook({"stop_hook_active": False}, tmp_path / "missing_eidos_dir") == {}
    assert execute_hook(path=tmp_path / "missing_eidos_dir", stdin_data="") == 0

    # 5. plugin/installer.py helper edge cases: _uninstall_from_manifest when .eidos-manifest.json is absent
    no_manifest_dir = tmp_path / "no_manifest_dir"
    _copy_skills_flat(locate_plugin_bundle(), no_manifest_dir / "skills", force=True)
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

        # locate_plugin_bundle CWD fallback (assets/plugin_reference/skills) and FileNotFoundError
        cwd_bundle_ws = tmp_path / "cwd_bundle_ws"
        (cwd_bundle_ws / "assets" / "plugin_reference" / "skills").mkdir(parents=True)
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
        (tmp_path / ".agents").resolve(),
        None,
    )

    # 6b. plugin/validator.py: skills as file instead of directory, and invalid JSON in hooks.json
    pdir2 = tmp_path / "pdir2"
    pdir2.mkdir()
    (pdir2 / "skills").write_text("not a dir", encoding="utf-8")
    (pdir2 / "hooks.json").write_text("{invalid_hooks", encoding="utf-8")
    rep_p2 = validate_plugin_package(pdir2)
    assert not rep_p2.is_valid
    assert any("Missing required 'skills' directory" in e for e in rep_p2.errors)
    assert any("Invalid JSON in 'hooks.json'" in e for e in rep_p2.errors)

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

    # 6e. plugin/installer.py: hook helper branches across Antigravity, Claude, Cursor, Copilot, and Codex
    c_settings = tmp_path / "claude_settings_edge" / "settings.json"
    assert _remove_project_claude_settings(c_settings) is False
    c_settings.parent.mkdir(parents=True, exist_ok=True)
    c_settings.write_text("{bad", encoding="utf-8")
    assert _remove_project_claude_settings(c_settings) is False
    _update_project_claude_settings(c_settings, launcher="eidos")
    assert _remove_project_claude_settings(c_settings) is True
    assert not c_settings.exists()

    for update_fn, remove_fn, sub_rel in (
        (_update_project_antigravity_hooks, _remove_project_antigravity_hooks, "antigravity_edge/hooks.json"),
        (_update_project_cursor_hooks, _remove_project_cursor_hooks, "cursor_edge/hooks.json"),
        (_update_project_copilot_hooks, _remove_project_copilot_hooks, "copilot_edge/hooks/hooks.json"),
        (_update_project_codex_hooks, _remove_project_codex_hooks, "codex_edge/hooks.json"),
    ):
        h_file = tmp_path / sub_rel
        assert remove_fn(h_file) is False
        h_file.parent.mkdir(parents=True, exist_ok=True)
        h_file.write_text("{bad", encoding="utf-8")
        assert remove_fn(h_file) is False
        update_fn(h_file, launcher="eidos")
        update_fn(h_file, launcher="uvx")
        assert remove_fn(h_file) is True
        assert not h_file.exists()

    # 6f. Newer installed version in `plugin version` & `plugin update`, execute_hook with drift,
    #     and corrupt manifest in _copy_skills_flat
    (bad_ver_dir / ".eidos-manifest.json").write_text('{"version": "99.0.0"}', encoding="utf-8")
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
    _copy_skills_flat(locate_plugin_bundle(), tree_cleanup_dir / "skills", force=True)


def test_target_adapter_manifest_includes_post_install_hook_files(tmp_path):
    """Verify Bug 16: TargetAdapter.install writes .eidos-manifest.json after post_install_hook and skills_subdir copies."""
    from eidos.plugin.installer import MANIFEST_FILENAME, PluginInstallTarget, TargetAdapter, locate_plugin_bundle

    def _custom_post_hook(root: Path, _source_dir: Path, _force: bool, _launcher: str) -> list[Path]:
        extra_file = root / ".agents" / "post_hook_generated.json"
        extra_file.parent.mkdir(parents=True, exist_ok=True)
        extra_file.write_text('{"generated_by": "post_install_hook"}\n', encoding="utf-8")
        return [extra_file]

    adapter = TargetAdapter(
        target=PluginInstallTarget.ANTIGRAVITY,
        name="custom-antigravity",
        plugin_subdir=".agents",
        skills_subdir=".agents/skills",
        post_install_hook=_custom_post_hook,
    )
    res = adapter.install(tmp_path, locate_plugin_bundle(), force=True)
    assert res.success is True

    manifest_path = tmp_path / ".agents" / MANIFEST_FILENAME
    assert manifest_path.exists()
    manifest_data = orjson.loads(manifest_path.read_bytes())
    assert "post_hook_generated.json" in manifest_data["files"]


def test_hook_schema_validation_and_non_duplicate_skills(tmp_path):
    """Verify stateless handle_stop_hook validation and non-duplicate skills across targets (ANTIGRAVITY, CLAUDE, CURSOR, CODEX, COPILOT)."""
    from eidos.plugin.hooks import handle_stop_hook

    eidos_dir = tmp_path / "eidos"
    dom_dir = eidos_dir / "domains"
    dom_dir.mkdir(parents=True)
    bad1 = dom_dir / "bad1.adoc"
    bad2 = dom_dir / "bad2.adoc"
    bad1.write_text("---\ntype: product/domain\nid: BAD-1\n---\n= Bad 1\n", encoding="utf-8")
    bad2.write_text("---\ntype: product/domain\nid: BAD-2\n---\n= Bad 2\n", encoding="utf-8")

    stop1 = handle_stop_hook({"stop_hook_active": False}, tmp_path)
    assert stop1["decision"] == "block"
    assert "Specification validation failed" in stop1.get("reason", "")

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

    clean_stop = handle_stop_hook({"stop_hook_active": False}, tmp_path)
    assert clean_stop == {}

    # Targets with flat skills_subdir (.claude/skills, .agents/skills, .aider/skills) do not create duplicate dirs
    inst_root = tmp_path / "no_dup_skills"
    inst_root.mkdir()
    install_plugin(target=PluginInstallTarget.ALL, project_dir=inst_root, force=True)
    assert not (inst_root / ".claude" / "plugins").exists()
    assert (inst_root / ".claude" / "skills" / "eidos" / "SKILL.md").exists()
    assert not (inst_root / ".cursor" / "skills").exists()
    assert not (inst_root / ".codex" / "skills").exists()
    assert not (inst_root / ".github" / "plugin").exists()
    assert not (inst_root / ".github" / "skills").exists()
    assert (inst_root / ".agents" / "skills" / "eidos" / "SKILL.md").exists()


def test_group_d1_d2_d3_regressions(tmp_path, monkeypatch):  # eidos:fixture
    """Regression suite for Group D1 (doctor/hook CLI), D2 (CLI path & output bugs), and D3 (installer & hooks)."""  # eidos:fixture
    # --- D1.1: eidos doctor exits 1 when agent_plugin check is not ok (e.g. outdated) ---
    from eidos.core.lifecycle.initializer import init_repository

    d1_ws = tmp_path / "d1_doctor_ws"
    d1_ws.mkdir(parents=True)
    init_repository(d1_ws, title="D1 Doctor WS")
    subprocess.run(["git", "init"], cwd=d1_ws, capture_output=True, check=True)
    monkeypatch.setattr(
        "eidos.cli.doctor_hook_commands.check_plugin_version_status",
        lambda project_dir=None: {
            "status": "outdated",
            "installed_version": "0.0.1",
            "bundled_version": "0.8.2",
        },
    )
    res_doc = runner.invoke(app, ["doctor", "--path", str(d1_ws), "--json"])
    assert res_doc.exit_code == 1
    doc_payload = orjson.loads(res_doc.stdout)
    assert doc_payload["healthy"] is False
    assert doc_payload["checks"]["agent_plugin"]["ok"] is False

    # --- D1.2: eidos hook --path runs JSON hook protocol (exit code 0) and emits 1-turn continuation on failure ---
    broken_ws = tmp_path / "broken_hook_ws"
    broken_eidos = broken_ws / "eidos" / "domains"
    broken_eidos.mkdir(parents=True)
    (broken_eidos / "invalid.adoc").write_text(
        "---\ntype: product/domain\nid: BAD\n---\n= Invalid\n",
        encoding="utf-8",
    )
    res_hook_broken = runner.invoke(
        app,
        ["hook", "--path", str(broken_ws)],
        input='{"executionNum": 1, "terminationReason": "NO_TOOL_CALL"}',
    )
    assert res_hook_broken.exit_code == 0
    broken_out = orjson.loads(res_hook_broken.stdout)
    assert broken_out["decision"] == "continue"
    assert "Specification validation failed" in broken_out["reason"]

    res_hook_missing = runner.invoke(
        app,
        ["hook", "--path", str(broken_ws / "nonexistent")],
        input='{"executionNum": 1}',
    )
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

    # --- D3.2: Re-running install_plugin(force=False) preserves manifest entries so uninstall removes skill files ---
    reinstall_ws = tmp_path / "reinstall_ws"
    reinstall_ws.mkdir()
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=reinstall_ws, force=False)
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=reinstall_ws, force=False)
    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=reinstall_ws)
    assert not (reinstall_ws / ".claude" / "skills" / "eidos" / "SKILL.md").exists()

    # --- D3.3: find_workspace_root honours payload["cwd"] ---
    from eidos.plugin.hooks import find_workspace_root

    monkeypatch.chdir(uninit_dir)
    assert find_workspace_root({"cwd": str(repo_ws)}) == repo_ws.resolve()


def test_normalize_to_semver_and_bundled_version_sync(monkeypatch):
    """Verify normalize_to_semver handles PEP 440 / v-prefixed strings and get_bundled_plugin_version stays in sync."""
    import eidos
    from eidos.plugin.version import normalize_to_semver

    assert normalize_to_semver("0.12.0") == "0.12.0"
    assert normalize_to_semver("v0.12.0") == "0.12.0"
    assert normalize_to_semver("V1.2.3-rc.1") == "1.2.3-rc.1"
    assert normalize_to_semver("0.12.1.dev13") == "0.12.1"
    assert normalize_to_semver("0.12.0.post1") == "0.12.0"
    assert normalize_to_semver("invalid") is None
    assert normalize_to_semver(None) is None  # type: ignore[arg-type]

    # When eidos.__version__ is a clean SemVer release, get_bundled_plugin_version returns it
    monkeypatch.setattr(eidos, "__version__", "99.1.0")
    assert get_bundled_plugin_version() == "99.1.0"

    # When eidos.__version__ is PEP 440 .devN, get_bundled_plugin_version normalizes it to SemVer 2.0.0
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
    (valid_plugin_ref / "skills").mkdir(parents=True, exist_ok=True)

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
        res = upsert_managed_block(
            path, "agents-md", render, target="antigravity", skills_dir=".agents/skills"
        )
        assert res.status is BlockStatus.APPENDED
        assert path.read_text(encoding="utf-8").startswith(user)

    elif case == "move_and_update":
        upsert_managed_block(path, "agents-md", render, target="antigravity", skills_dir=".agents/skills")
        block = path.read_text(encoding="utf-8")
        before, after = "# Intro\nuser line 1\n\n", "\n## Team notes\nuser line 2\n"
        path.write_text(before + block + after, encoding="utf-8")
        res = upsert_managed_block(path, "agents-md", render, target="claude", skills_dir=".claude/skills")
        assert res.status is BlockStatus.UPDATED
        text = path.read_text(encoding="utf-8")
        assert text.startswith(before) and text.endswith(after)
        assert "targets=antigravity:.agents/skills,claude:.claude/skills" in text
        assert res.targets == {"antigravity": ".agents/skills", "claude": ".claude/skills"}

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
        res = upsert_managed_block(
            path, "agents-md", render, target="cursor", skills_dir=".agents/skills"
        )
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
            assert not {"AGENTS.md", "CLAUDE.md"} & set(files)


def test_agents_md_shared_across_targets(tmp_path):
    """Verify several targets share one deterministic AGENTS.md block regardless of install order (DLT-2026-121, DLT-2026-128, DLT-2026-130)."""
    order_a, order_b = tmp_path / "a", tmp_path / "b"
    for ws, order in ((order_a, ["antigravity", "claude"]), (order_b, ["claude", "antigravity"])):
        ws.mkdir()
        for name in order:
            install_plugin(target=PluginInstallTarget(name), project_dir=ws, force=True)
    text_a = (order_a / "AGENTS.md").read_text(encoding="utf-8")
    assert text_a == (order_b / "AGENTS.md").read_text(encoding="utf-8")
    assert "targets=antigravity:.agents/skills,claude:.claude/skills" in text_a
    assert text_a.index("`.agents/skills/") < text_a.index("`.claude/skills/")

    all_ws = tmp_path / "all"
    all_ws.mkdir()
    install_plugin(target=PluginInstallTarget.ALL, project_dir=all_ws, force=True)
    all_text = (all_ws / "AGENTS.md").read_text(encoding="utf-8")
    for skills_dir in (".claude/skills", ".agents/skills", ".aider/skills"):
        assert f"`{skills_dir}/<skill>/SKILL.md`" in all_text

    # Shared skills directory is rendered once, and uninstalled targets are pruned on next install
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=all_ws, force=True)
    shared_text = (all_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert shared_text.count("`.agents/skills/<skill>/SKILL.md`") == 1
    uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=order_a)
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=order_a, force=True)
    pruned_text = (order_a / "AGENTS.md").read_text(encoding="utf-8")
    assert "targets=claude:.claude/skills " in pruned_text and "antigravity:" not in pruned_text
    assert "`.agents/skills/" not in pruned_text

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
    assert (user_ws / "CLAUDE.md").read_text(encoding="utf-8") == user_claude
    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=fresh)
    assert not (fresh / "CLAUDE.md").exists()

    already = tmp_path / "already"
    already.mkdir()
    existing = "# Memory\n@AGENTS.md\n"
    (already / "CLAUDE.md").write_text(existing, encoding="utf-8")
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=already, force=True)
    assert (already / "CLAUDE.md").read_text(encoding="utf-8") == existing


def test_aider_read_entry(tmp_path):
    """Verify Aider adds AGENTS.md to read: in .aider.conf.yml without duplicating or clobbering user settings."""
    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    conf = tmp_path / ".aider.conf.yml"
    assert conf.read_text(encoding="utf-8") == "read:\n  - AGENTS.md\n"

    # Existing scalar read: and other keys are preserved and not duplicated
    conf.write_text("# comment\nmodel: sonnet\nread: CONVENTIONS.md\n", encoding="utf-8")
    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    install_plugin(target=PluginInstallTarget.AIDER, project_dir=tmp_path, force=True)
    updated = conf.read_text(encoding="utf-8")
    assert "model: sonnet" in updated
    assert "CONVENTIONS.md" in updated
    assert updated.count("AGENTS.md") == 1


def test_opt_in_hooks_flag_and_doctor_hook_health_reporting(tmp_path):
    """Verify opt-in --hooks on install, state preservation on update, and doctor hook health reporting (DLT-2026-128)."""
    from eidos.core.lifecycle.initializer import init_repository

    init_repository(tmp_path, title="Hook Health Test")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    # --no-hooks is not a valid option on install, and --hooks is not accepted on update
    assert runner.invoke(app, ["plugin", "install", "cursor", "--no-hooks", "--project-dir", str(tmp_path)]).exit_code != 0
    assert runner.invoke(app, ["plugin", "update", "cursor", "--hooks", "--project-dir", str(tmp_path)]).exit_code != 0

    # 1. Default install without --hooks: hooks=False -> PASS with (hooks: disabled)
    res_no_hooks = runner.invoke(app, ["plugin", "install", "cursor", "--project-dir", str(tmp_path)])
    assert res_no_hooks.exit_code == 0
    assert not (tmp_path / ".cursor" / "hooks.json").exists()

    doc_disabled = runner.invoke(app, ["doctor", "--path", str(tmp_path), "--json"])
    assert doc_disabled.exit_code == 0
    doc_disabled_json = orjson.loads(doc_disabled.stdout)
    assert doc_disabled_json["healthy"] is True
    assert doc_disabled_json["checks"]["agent_plugin"]["ok"] is True
    assert doc_disabled_json["checks"]["agent_plugin"]["hooks_ok"] is True
    assert doc_disabled_json["checks"]["agent_plugin"]["hooks_status"] == "disabled"

    doc_disabled_txt = runner.invoke(app, ["doctor", "--path", str(tmp_path)])
    assert doc_disabled_txt.exit_code == 0
    assert "(hooks: disabled)" in doc_disabled_txt.stdout

    # 2. Install with --hooks: hooks=True -> PASS with (hooks: active)
    res_hooks = runner.invoke(app, ["plugin", "install", "cursor", "--hooks", "--project-dir", str(tmp_path)])
    assert res_hooks.exit_code == 0
    cursor_hooks = tmp_path / ".cursor" / "hooks.json"
    assert cursor_hooks.exists()

    # Update preserves recorded hooks=True setting automatically without taking --hooks
    res_upd = runner.invoke(app, ["plugin", "update", "cursor", "--project-dir", str(tmp_path)])
    assert res_upd.exit_code == 0
    assert cursor_hooks.exists()

    doc_active = runner.invoke(app, ["doctor", "--path", str(tmp_path), "--json"])
    assert doc_active.exit_code == 0
    doc_active_json = orjson.loads(doc_active.stdout)
    assert doc_active_json["healthy"] is True
    assert doc_active_json["checks"]["agent_plugin"]["ok"] is True
    assert doc_active_json["checks"]["agent_plugin"]["hooks_ok"] is True
    assert doc_active_json["checks"]["agent_plugin"]["hooks_status"] == "active"

    doc_active_txt = runner.invoke(app, ["doctor", "--path", str(tmp_path)])
    assert doc_active_txt.exit_code == 0
    assert "(hooks: active)" in doc_active_txt.stdout

    # 3. Missing hook file when originally installed with --hooks -> FAIL (exit code 1, status: hook_error)
    cursor_hooks.unlink()
    doc_broken = runner.invoke(app, ["doctor", "--path", str(tmp_path), "--json"])
    assert doc_broken.exit_code == 1
    doc_broken_json = orjson.loads(doc_broken.stdout)
    assert doc_broken_json["healthy"] is False
    assert doc_broken_json["checks"]["agent_plugin"]["ok"] is False
    assert doc_broken_json["checks"]["agent_plugin"]["status"] == "hook_error"
    assert doc_broken_json["checks"]["agent_plugin"]["hooks_ok"] is False
    assert doc_broken_json["checks"]["agent_plugin"]["hooks_status"] == "broken"
    assert any(".cursor/hooks.json" in err for err in doc_broken_json["checks"]["agent_plugin"]["hook_errors"])

    doc_broken_txt = runner.invoke(app, ["doctor", "--path", str(tmp_path)])
    assert doc_broken_txt.exit_code == 1
    assert "FAIL" in doc_broken_txt.stdout
    assert "Hook error:" in doc_broken_txt.stdout

    # 4. Re-running install without --hooks disables hooks cleanly and restores doctor PASS (hooks: disabled)
    res_reinstall_disable = runner.invoke(app, ["plugin", "install", "cursor", "--project-dir", str(tmp_path)])
    assert res_reinstall_disable.exit_code == 0
    assert not cursor_hooks.exists()
    doc_after_disable = runner.invoke(app, ["doctor", "--path", str(tmp_path), "--json"])
    assert doc_after_disable.exit_code == 0
    assert orjson.loads(doc_after_disable.stdout)["checks"]["agent_plugin"]["hooks_status"] == "disabled"

    # 5. Cline with --hooks installs executable PreToolUse hook, preserves it on update, and fails doctor if corrupted
    runner.invoke(app, ["plugin", "uninstall", "cursor", "--project-dir", str(tmp_path)])
    res_cline_hooks = runner.invoke(app, ["plugin", "install", "cline", "--hooks", "--project-dir", str(tmp_path)])
    assert res_cline_hooks.exit_code == 0
    cline_hook = tmp_path / ".clinerules" / "hooks" / "PreToolUse"
    assert cline_hook.is_file()
    assert runner.invoke(app, ["plugin", "update", "cline", "--project-dir", str(tmp_path)]).exit_code == 0
    assert cline_hook.is_file()
    doc_cline_active = runner.invoke(app, ["doctor", "--path", str(tmp_path), "--json"])
    assert doc_cline_active.exit_code == 0
    assert orjson.loads(doc_cline_active.stdout)["checks"]["agent_plugin"]["hooks_status"] == "active"

    cline_hook.write_text("#!/usr/bin/env sh\necho noop\n", encoding="utf-8")
    doc_cline_corrupted = runner.invoke(app, ["doctor", "--path", str(tmp_path), "--json"])
    assert doc_cline_corrupted.exit_code == 1
    assert orjson.loads(doc_cline_corrupted.stdout)["checks"]["agent_plugin"]["status"] == "hook_error"


def test_agents_md_symlink_layouts_never_abort_install(tmp_path):
    """Verify symlinked AGENTS.md / CLAUDE.md layouts are left untouched and never abort an install (DLT-2026-121, DLT-2026-130)."""
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
    assert (ws2 / ".agents" / "skills" / "eidos" / "SKILL.md").exists()


def test_eidos_plan_skill_blast_radius_reconciliation_rules():
    """Verify INV-PLUGIN-SKILL-DELTA-RECONCILIATION: bundled eidos-plan and eidos skills (and canonical DLT-2026-015)
    enforce bottom-up Implementation Plan file/glob reconciliation, SYS-*/ARCH-* non-coverage, 1-hop parent CNT-* and
    design/* coverage, UNMODIFIED-avoidance annotations, INV-* test pairing, live CLI/workspace verification, and
    autonomous pre-presentation 5-Dimension self-review."""
    import re

    from eidos.core.spec.models import SpecDocument
    from eidos.core.spec.parser import safe_load_yaml, split_frontmatter_and_body
    from eidos.core.spec.validator import SpecValidator

    _, plan_body = _skill_frontmatter_and_body(_SKILLS_DIR / "eidos-plan" / "SKILL.md")
    _, core_body = _skill_frontmatter_and_body(_SKILLS_DIR / "eidos" / "SKILL.md")

    # 1. Both eidos-plan and eidos skills enforce bottom-up quoted-glob/comma reconciliation, 1-hop + design/*
    #    coverage, SYS-*/ARCH-* non-coverage, UNMODIFIED-avoidance annotations, INV-* test pairing, and
    #    autonomous pre-presentation 5-Dimension review.
    for label, text in (("eidos-plan", plan_body), ("eidos", core_body)):
        assert 'eidos context -f "apps/*/Dockerfile" --tier guardrails --json' in text, label
        assert "1-hop" in text, label
        assert "SYS-*" in text and "ARCH-*" in text and "unmapped" in text, label
        assert "(covered via parent CNT-*)" in text, label
        assert "(body-only; no spec mutation)" in text, label
        assert "UNMODIFIED" in text, label
        assert "expect.frontmatter: { rule, test_binding }" in text, label
        assert "5-Dimension Review" in text and "presenting" in text, label

    assert "eidos context -f src/billing/verifier.py,src/billing/routes.py --tier guardrails --json" in plan_body
    assert "DS-*" in plan_body and "CLT-*" in plan_body and "ui_contract" in plan_body
    assert "POL-*" in plan_body
    assert "eidos drift --strict" in plan_body
    assert "symlinks" in plan_body and "templates" in plan_body

    # 2. Programmatically extract, schema-validate, and reconcile the canonical DLT-2026-015 template in eidos-plan
    dlt_blocks = re.findall(r"```yaml\n(---\ntype: delta/mutation\nid: DLT-2026-015\n.*?)```", plan_body, flags=re.S)
    assert len(dlt_blocks) == 1, "Expected exactly one canonical DLT-2026-015 template block in eidos-plan/SKILL.md"
    dlt_fm_raw, dlt_body = split_frontmatter_and_body(dlt_blocks[0])
    dlt_fm = safe_load_yaml(dlt_fm_raw)

    validator = SpecValidator()
    dlt_doc = SpecDocument(
        file_path=Path("eidos/deltas/dlt-2026-015.adoc"),
        frontmatter=dlt_fm,
        body=dlt_body,
        doc_type="delta/mutation",
        doc_id=dlt_fm["id"],
        schema_ref="delta/mutation.schema.yaml",
    )
    assert validator.validate_document(dlt_doc) == [], f"DLT-2026-015 schema errors: {dlt_doc.errors}"

    sample_inv = "INV-" + "BILL-WEBHOOK-SIG"
    by_target = {m["target"]: m for m in dlt_fm["mutations"]}
    assert {"CMP-WEBHOOK-VERIFIER", "CTR-BILLING-API", "CAP-BILL-001", sample_inv} <= set(by_target)

    cap_mut = by_target["CAP-BILL-001"]
    assert cap_mut["expect"]["frontmatter"]["technical.invariants.id"] == [sample_inv]
    assert sample_inv in cap_mut["expect"]["body"]["contains"]

    inv_mut = by_target[sample_inv]
    assert inv_mut["expect"]["frontmatter"]["rule"] == "HMAC-SHA256"
    assert (
        inv_mut["expect"]["frontmatter"]["test_binding"]
        == "tests/billing/test_webhooks.py::test_webhook_hmac_signature"
    )

    # Step 2 in == Implementation Plan explicitly reconciles CNT-BILLING-API via 1-hop child CMP-WEBHOOK-VERIFIER
    plan_section = dlt_body.split("== Implementation Plan", 1)[1]
    assert "CNT-BILLING-API" in plan_section
    assert "covered via `CMP-WEBHOOK-VERIFIER`" in plan_section or "covered via CMP-WEBHOOK-VERIFIER" in plan_section


def test_installer_truthful_version_comments_symlinks_and_uninstall(tmp_path):
    """Verify INV-PLUGIN-INSTALL-TRUTHFULNESS: non-force upgrades report 'modified' when SKILL.md files are skipped,
    .aider.conf.yml preserves YAML comments, canonical .agents/skills symlinks work across 'install all' and 'update all'
    with per-target fault isolation, and uninstall prunes references cleanly."""
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.plugin.installer import (
        _compute_sha256,
        _remove_project_aider_conf,
        _update_project_aider_conf,
        locate_plugin_bundle,
        update_plugin,
    )

    # 1. Non-force install skipping locally modified SKILL.md reports 'modified' (never 'up_to_date')
    ws = tmp_path / "truthful_ws"
    ws.mkdir()
    init_repository(ws, title="Truthful WS")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True)
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=ws, force=True)
    assert check_plugin_version_status(project_dir=ws)["status"] == "up_to_date"

    skill_md = ws / ".agents" / "skills" / "eidos" / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n# Local developer customization\n", encoding="utf-8")
    custom_sha = _compute_sha256(skill_md)

    # On-disk divergence immediately flips status to 'modified'
    diverged_st = check_plugin_version_status(project_dir=ws)
    assert diverged_st["status"] == "modified"
    assert ".agents/skills/eidos/SKILL.md" in diverged_st["skipped_files"]
    assert "--force" in diverged_st["status_text"]

    # Non-force reinstall skips the modified file, records skipped_files + actual hash, and keeps status == 'modified'
    non_force_res = install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=ws, force=False)
    assert len(non_force_res) == 1
    assert "--force" in non_force_res[0].message
    manifest = orjson.loads((ws / ".agents" / ".eidos-manifest.json").read_bytes())
    assert manifest["skipped_files"] == ["skills/eidos/SKILL.md"]
    assert manifest["files"]["skills/eidos/SKILL.md"] == custom_sha

    st_after_skip = check_plugin_version_status(project_dir=ws)
    assert st_after_skip["status"] == "modified"
    assert st_after_skip["status"] != "up_to_date"

    doc_mod = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert doc_mod.exit_code == 1
    doc_mod_data = orjson.loads(doc_mod.stdout)
    assert doc_mod_data["checks"]["agent_plugin"]["status"] == "modified"
    assert ".agents/skills/eidos/SKILL.md" in doc_mod_data["checks"]["agent_plugin"]["skipped_files"]

    doc_mod_txt = runner.invoke(app, ["doctor", "--path", str(ws)])
    assert doc_mod_txt.exit_code == 1
    assert "MODIFIED" in doc_mod_txt.stdout
    assert "--force" in doc_mod_txt.stdout

    ver_mod_txt = runner.invoke(app, ["plugin", "version", "--project-dir", str(ws)])
    assert ver_mod_txt.exit_code == 0
    assert "Modified" in ver_mod_txt.stdout
    assert "--force" in ver_mod_txt.stdout

    # Force install reconciles the skill, clears skipped_files, and restores 'up_to_date'
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=ws, force=True)
    manifest_reconciled = orjson.loads((ws / ".agents" / ".eidos-manifest.json").read_bytes())
    assert manifest_reconciled["skipped_files"] == []
    assert check_plugin_version_status(project_dir=ws)["status"] == "up_to_date"

    # 2. Comment-preserving .aider.conf.yml updates across scalar, flow-list, empty, and block-list forms + cleanup
    aider_conf = tmp_path / "aider_comments" / ".aider.conf.yml"
    aider_conf.parent.mkdir(parents=True)
    initial_aider = (
        "# Team Aider defaults\n"
        "model: claude-3-7-sonnet # inline model comment\n\n"
        "# Existing read docs\n"
        "read: [CONVENTIONS.md, ARCHITECTURE.md] # keep inline comment\n"
        "auto-commits: false\n"
    )
    aider_conf.write_text(initial_aider, encoding="utf-8")
    _update_project_aider_conf(aider_conf, "AGENTS.md")
    after_flow = aider_conf.read_text(encoding="utf-8")
    assert "# Team Aider defaults" in after_flow
    assert "# inline model comment" in after_flow
    assert "# Existing read docs" in after_flow
    assert "# keep inline comment" in after_flow
    assert "CONVENTIONS.md" in after_flow and "ARCHITECTURE.md" in after_flow and "AGENTS.md" in after_flow
    assert "auto-commits: false" in after_flow

    # Removing AGENTS.md preserves comments and remaining flow-list entries
    assert _remove_project_aider_conf(aider_conf, "AGENTS.md") is True
    removed_flow = aider_conf.read_text(encoding="utf-8")
    assert "AGENTS.md" not in removed_flow
    assert "# Team Aider defaults" in removed_flow
    assert "# keep inline comment" in removed_flow
    assert "CONVENTIONS.md" in removed_flow and "ARCHITECTURE.md" in removed_flow

    # Empty read: key + comment preservation on add & remove
    aider_conf.write_text("# Header\nread: # trailing\nlint: true\n", encoding="utf-8")
    _update_project_aider_conf(aider_conf, "AGENTS.md")
    txt_empty_added = aider_conf.read_text(encoding="utf-8")
    assert "# Header" in txt_empty_added and "AGENTS.md" in txt_empty_added and "lint: true" in txt_empty_added
    assert _remove_project_aider_conf(aider_conf, "AGENTS.md") is True
    txt_empty_removed = aider_conf.read_text(encoding="utf-8")
    assert "# Header" in txt_empty_removed and "AGENTS.md" not in txt_empty_removed and "lint: true" in txt_empty_removed

    # Sole read: [AGENTS.md] unlinks file cleanly; also cover quoted scalars, CRLF, comments, & idempotent re-runs
    sole_conf = tmp_path / "sole_aider" / ".aider.conf.yml"
    sole_conf.parent.mkdir(parents=True)
    sole_conf.write_text("read: [AGENTS.md]\n", encoding="utf-8")
    assert _remove_project_aider_conf(sole_conf, "AGENTS.md") is True
    assert not sole_conf.exists()
    assert _remove_project_aider_conf(sole_conf, "AGENTS.md") is False

    quoted_conf = tmp_path / "quoted_aider" / ".aider.conf.yml"
    quoted_conf.parent.mkdir(parents=True)
    quoted_conf.write_text('model: "sonnet \\"#1\\"" # keep\r\nread: \'AGENTS.md\' # inline\r\n', encoding="utf-8")
    _update_project_aider_conf(quoted_conf, "AGENTS.md")  # already present as quoted scalar
    assert _remove_project_aider_conf(quoted_conf, "OTHER.md") is False
    assert _remove_project_aider_conf(quoted_conf, "AGENTS.md") is True
    assert "# inline" in quoted_conf.read_text(encoding="utf-8")

    flow_comment_conf = tmp_path / "flow_comment" / ".aider.conf.yml"
    flow_comment_conf.parent.mkdir(parents=True)
    flow_comment_conf.write_text("read: [AGENTS.md] # sole-with-comment\n", encoding="utf-8")
    _update_project_aider_conf(flow_comment_conf, "AGENTS.md")  # already in flow list
    assert _remove_project_aider_conf(flow_comment_conf, "OTHER.md") is False
    assert _remove_project_aider_conf(flow_comment_conf, "AGENTS.md") is True
    assert "# sole-with-comment" in flow_comment_conf.read_text(encoding="utf-8")

    block_conf = tmp_path / "block_aider" / ".aider.conf.yml"
    block_conf.parent.mkdir(parents=True)
    block_conf.write_text("model: opus\n", encoding="utf-8")  # no read: key yet
    _update_project_aider_conf(block_conf, "CONVENTIONS.md")
    # Insert a comment inside the block list and append AGENTS.md
    block_conf.write_text("read: # list-comment\n  # item-comment\n  - CONVENTIONS.md\nmodel: opus\n", encoding="utf-8")
    _update_project_aider_conf(block_conf, "AGENTS.md")
    _update_project_aider_conf(block_conf, "AGENTS.md")  # already in block list
    assert _remove_project_aider_conf(block_conf, "MISSING.md") is False
    assert _remove_project_aider_conf(block_conf, "CONVENTIONS.md") is True
    assert _remove_project_aider_conf(block_conf, "AGENTS.md") is True
    assert "# list-comment" in block_conf.read_text(encoding="utf-8")

    # Also exercise managed_block.remove_managed_block & check_target_hook_health edge branches
    from eidos.plugin.installer import _resolve_saved_hook_preference, check_target_hook_health
    from eidos.plugin.managed_block import BlockStatus, remove_managed_block, upsert_managed_block

    mb_dir = tmp_path / "mb_edges"
    mb_dir.mkdir()
    with pytest.raises(ValueError, match="Invalid managed block name"):
        remove_managed_block(mb_dir / "AGENTS.md", "Bad Name")
    assert remove_managed_block(mb_dir / "missing.md", "agents-md").status is BlockStatus.UNCHANGED

    opted_file = mb_dir / "opted.md"
    opted_file.write_text("<!-- eidos:agents-md off -->\n", encoding="utf-8")
    assert remove_managed_block(opted_file, "agents-md").status is BlockStatus.OPTED_OUT

    mal_file = mb_dir / "malformed.md"
    mal_file.write_text("<!-- eidos:begin agents-md v=1 -->\n", encoding="utf-8")
    assert remove_managed_block(mal_file, "agents-md").status is BlockStatus.MALFORMED

    plain_file = mb_dir / "plain.md"
    plain_file.write_text("# Plain\n", encoding="utf-8")
    assert remove_managed_block(plain_file, "agents-md").status is BlockStatus.UNCHANGED

    bom_file = mb_dir / "bom.md"
    upsert_managed_block(bom_file, "agents-md", "Body\n", target="cursor", skills_dir=".agents/skills")
    upsert_managed_block(bom_file, "agents-md", "Body\n", target="claude", skills_dir=".claude/skills")
    upsert_managed_block(bom_file, "agents-md", "Body\n", target="codex", skills_dir=".agents/skills")
    bom_raw = b"\xef\xbb\xbf# Header\r\n\r\n" + bom_file.read_bytes().replace(b"\n", b"\r\n") + b"\r\n# Footer\r\n"
    bom_file.write_bytes(bom_raw)
    assert (
        remove_managed_block(bom_file, "agents-md", content=lambda _t: "Body\n", remove_target="claude").status
        is BlockStatus.UPDATED
    )
    # Hand-edited block refuses prune without force
    bom_file.write_bytes(bom_file.read_bytes().replace(b"Body", b"Edited"))
    assert (
        remove_managed_block(bom_file, "agents-md", content=lambda _t: "Body\n", remove_target="cursor").status
        is BlockStatus.MODIFIED
    )
    assert remove_managed_block(bom_file, "agents-md").status is BlockStatus.REMOVED
    assert "# Header" in bom_file.read_text(encoding="utf-8") and "# Footer" in bom_file.read_text(encoding="utf-8")

    keep_empty = mb_dir / "keep_empty.md"
    upsert_managed_block(keep_empty, "agents-md", "Body\n")
    assert remove_managed_block(keep_empty, "agents-md", unlink_if_empty=False).status is BlockStatus.REMOVED
    assert keep_empty.is_file() and keep_empty.read_text(encoding="utf-8") == ""

    # Hook health diagnostics across all JSON hook targets + unmanifested Cline PreToolUse hook preference detection
    hh_ws = tmp_path / "hook_health_edges"
    install_plugin(target=PluginInstallTarget.ALL, project_dir=hh_ws, force=True, hooks=True)
    for t_name, rel_h in (
        ("antigravity", ".agents/hooks.json"),
        ("claude", ".claude/settings.json"),
        ("codex", ".codex/hooks.json"),
        ("cursor", ".cursor/hooks.json"),
        ("copilot", ".github/hooks/hooks.json"),
    ):
        assert check_target_hook_health(hh_ws, t_name, hooks_expected=True)["status"] == "active"
        hp = hh_ws / rel_h
        hp.write_text("[]", encoding="utf-8")
        assert check_target_hook_health(hh_ws, t_name, hooks_expected=True)["status"] == "invalid"
        hp.write_text("{}", encoding="utf-8")
        assert check_target_hook_health(hh_ws, t_name, hooks_expected=True)["status"] == "corrupted"

    cline_pref_ws = tmp_path / "cline_pref_ws"
    cline_hook = cline_pref_ws / ".clinerules" / "hooks" / "PreToolUse"
    cline_hook.parent.mkdir(parents=True)
    cline_hook.write_text("#!/usr/bin/env sh\nexec eidos hook\n", encoding="utf-8")
    cline_hook.chmod(0o755)
    assert _resolve_saved_hook_preference(cline_pref_ws, "cline") is True

    cline_full_ws = tmp_path / "cline_full_ws"
    install_plugin(target=PluginInstallTarget.CLINE, project_dir=cline_full_ws, force=True, hooks=True)
    assert check_target_hook_health(cline_full_ws, "cline", hooks_expected=True)["status"] == "active"
    ts_hook = cline_full_ws / ".clinerules" / "hooks" / "TaskStart"
    ts_hook.write_text("#!/usr/bin/env sh\necho custom\n", encoding="utf-8")
    ts_hook.chmod(0o755)
    assert check_target_hook_health(cline_full_ws, "cline", hooks_expected=True)["status"] == "corrupted"
    ts_hook.unlink()
    assert check_target_hook_health(cline_full_ws, "cline", hooks_expected=True)["status"] == "missing"

    # 3. Canonical self-hosted .agents/skills symlink succeeds in 'install all' and 'update all',
    #    while external symlinks fail per-target without aborting sibling targets ('claude', 'aider').
    self_ws = tmp_path / "self_hosted_ws"
    (self_ws / ".agents").mkdir(parents=True)
    bundle_skills = locate_plugin_bundle() / "skills"
    (self_ws / ".agents" / "skills").symlink_to(bundle_skills)

    all_res = install_plugin(target=PluginInstallTarget.ALL, project_dir=self_ws, force=False, hooks=True)
    assert len(all_res) == 9
    assert (self_ws / ".agents" / "skills").is_symlink()
    assert "<!-- eidos:begin agents-md " in (self_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert (self_ws / ".claude" / "skills" / "eidos" / "SKILL.md").is_file()
    assert (self_ws / ".aider" / "skills" / "eidos" / "SKILL.md").is_file()

    _prev_v, _curr_v, upd_res = update_plugin(target=PluginInstallTarget.ALL, project_dir=self_ws)
    assert len(upd_res) == 9
    assert (self_ws / ".agents" / "skills").is_symlink()

    ext_ws = tmp_path / "external_symlink_ws"
    outside_skills = tmp_path / "outside_skills"
    outside_skills.mkdir()
    (ext_ws / ".agents").mkdir(parents=True)
    (ext_ws / ".agents" / "skills").symlink_to(outside_skills)

    with pytest.raises(FileExistsError):
        install_plugin(target=PluginInstallTarget.ALL, project_dir=ext_ws, force=True)
    # Fault isolation: claude and aider still installed despite .agents/skills external symlink failure!
    assert (ext_ws / ".claude" / "skills" / "eidos" / "SKILL.md").is_file()
    assert (ext_ws / ".aider" / "skills" / "eidos" / "SKILL.md").is_file()

    with pytest.raises(FileExistsError):
        update_plugin(target=PluginInstallTarget.ALL, project_dir=ext_ws)

    # 4. Clean uninstall_plugin target pruning across AGENTS.md, CLAUDE.md, .aider.conf.yml, and Cline hooks
    prune_ws = tmp_path / "prune_ws"
    prune_ws.mkdir()
    for t in (
        PluginInstallTarget.ANTIGRAVITY,
        PluginInstallTarget.CLAUDE,
        PluginInstallTarget.AIDER,
        PluginInstallTarget.CLINE,
    ):
        install_plugin(target=t, project_dir=prune_ws, force=True, hooks=True)

    agents_before = (prune_ws / "AGENTS.md").read_text(encoding="utf-8")
    for t_key in ("antigravity", "claude", "aider", "cline"):
        assert f"{t_key}:" in agents_before

    uninstall_plugin(target=PluginInstallTarget.CLAUDE, project_dir=prune_ws)
    assert not (prune_ws / "CLAUDE.md").exists()
    assert "claude:" not in (prune_ws / "AGENTS.md").read_text(encoding="utf-8")

    uninstall_plugin(target=PluginInstallTarget.AIDER, project_dir=prune_ws)
    assert not (prune_ws / ".aider.conf.yml").exists()
    assert "aider:" not in (prune_ws / "AGENTS.md").read_text(encoding="utf-8")

    uninstall_plugin(target=PluginInstallTarget.CLINE, project_dir=prune_ws)
    assert not (prune_ws / ".clinerules" / "hooks" / "PreToolUse").exists()
    assert not (prune_ws / ".clinerules" / "hooks" / "TaskStart").exists()
    assert "cline:" not in (prune_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "antigravity:.agents/skills" in (prune_ws / "AGENTS.md").read_text(encoding="utf-8")

    uninstall_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=prune_ws)
    assert not (prune_ws / "AGENTS.md").exists()


def test_doctor_detects_wiped_agents_md(tmp_path):
    """Verify INV-DOCTOR-ONBOARDING-INTEGRITY: eidos doctor verifies the root AGENTS.md managed block whenever
    Agent Skills or .eidos-manifest.json exist (unless opted out via <!-- eidos:agents-md off -->)."""
    from eidos.core.lifecycle.initializer import init_repository

    ws = tmp_path / "onboarding_doc_ws"
    ws.mkdir()
    init_repository(ws, title="Onboarding Doctor WS")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True)

    # Before installing skills: agents_md status is not_installed and doctor is healthy
    res_pre = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_pre.exit_code == 0
    pre_data = orjson.loads(res_pre.stdout)
    assert pre_data["healthy"] is True
    assert pre_data["checks"]["agents_md"]["status"] == "not_installed"
    assert pre_data["checks"]["agents_md"]["agents_md_ok"] is True

    # Install skills -> healthy with valid managed block
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=ws, force=True)
    res_ok = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_ok.exit_code == 0
    ok_data = orjson.loads(res_ok.stdout)
    assert ok_data["healthy"] is True
    assert ok_data["checks"]["agents_md"]["ok"] is True
    assert ok_data["checks"]["agents_md"]["status"] == "valid"
    assert ok_data["checks"]["agents_md"]["agents_md_ok"] is True

    # 1. Wiped AGENTS.md ("project\n") -> fails doctor in both JSON and Rich table modes
    (ws / "AGENTS.md").write_text("project\n", encoding="utf-8")
    res_wiped = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_wiped.exit_code == 1
    wiped_data = orjson.loads(res_wiped.stdout)
    assert wiped_data["healthy"] is False
    assert wiped_data["checks"]["agents_md"]["ok"] is False
    assert wiped_data["checks"]["agents_md"]["agents_md_ok"] is False
    assert wiped_data["checks"]["agents_md"]["status"] == "missing_block"

    res_wiped_txt = runner.invoke(app, ["doctor", "--path", str(ws)])
    assert res_wiped_txt.exit_code == 1
    assert "Onboarding Governance" in res_wiped_txt.stdout
    assert "FAIL" in res_wiped_txt.stdout

    # 2. Missing AGENTS.md file -> fails doctor with missing_file
    (ws / "AGENTS.md").unlink()
    res_missing = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_missing.exit_code == 1
    assert orjson.loads(res_missing.stdout)["checks"]["agents_md"]["status"] == "missing_file"

    # 3. Malformed AGENTS.md markers -> fails doctor with malformed
    (ws / "AGENTS.md").write_text("<!-- eidos:begin agents-md v=1 -->\ndangling\n", encoding="utf-8")
    res_malformed = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_malformed.exit_code == 1
    assert orjson.loads(res_malformed.stdout)["checks"]["agents_md"]["status"] == "malformed"

    # 4. Explicit opt-out <!-- eidos:agents-md off --> -> passes doctor cleanly
    (ws / "AGENTS.md").write_text("# Custom Team Guide\n<!-- eidos:agents-md off -->\n", encoding="utf-8")
    res_optout = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_optout.exit_code == 0
    optout_data = orjson.loads(res_optout.stdout)
    assert optout_data["healthy"] is True
    assert optout_data["checks"]["agents_md"]["status"] == "opted_out"
    assert optout_data["checks"]["agents_md"]["agents_md_ok"] is True

    res_optout_txt = runner.invoke(app, ["doctor", "--path", str(ws)])
    assert res_optout_txt.exit_code == 0
    assert "OPTED OUT" in res_optout_txt.stdout

    # 5. Restoring canonical AGENTS.md restores full health
    (ws / "AGENTS.md").unlink()
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=ws, force=True)
    res_clean = runner.invoke(app, ["doctor", "--path", str(ws), "--json"])
    assert res_clean.exit_code == 0
    assert orjson.loads(res_clean.stdout)["healthy"] is True

    # 6. Session-baseline Stop hook blocks wiped AGENTS.md (onboarding_files) while allowing pure README.md edits
    from eidos.plugin.hooks import capture_session_baseline, handle_stop_hook

    subprocess.run(["git", "config", "user.email", "test@eidos.dev"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Test"], cwd=ws, check=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=ws, check=True)

    sess_payload = {"session_id": "sess-onboarding-guard"}
    capture_session_baseline(sess_payload, ws)

    (ws / "README.md").write_text("# Docs note\n", encoding="utf-8")
    assert handle_stop_hook(sess_payload, ws) == {}

    (ws / "AGENTS.md").write_text("project\n", encoding="utf-8")
    blocked = handle_stop_hook(sess_payload, ws)
    assert blocked.get("decision") == "block"
    assert "AGENTS.md" in blocked.get("reason", "")


def test_plugin_cli_error_and_partial_failure_branches(tmp_path, monkeypatch):
    """Cover plugin_commands.py error/failure exit paths across install, uninstall, and update."""
    import types

    import eidos.cli.plugin_commands as plugin_cmd_mod

    # Partial failure result (success=False) in install and update
    monkeypatch.setattr(
        plugin_cmd_mod,
        "install_plugin",
        lambda **_kw: [
            types.SimpleNamespace(
                target="claude",
                scope="project",
                destination=tmp_path,
                success=False,
                message="simulated target skip",
            )
        ],
    )
    res_inst_partial = runner.invoke(app, ["plugin", "install", "claude", "-p", str(tmp_path)])
    assert res_inst_partial.exit_code == 0
    assert "simulated target skip" in res_inst_partial.stdout

    monkeypatch.setattr(
        plugin_cmd_mod,
        "update_plugin",
        lambda **_kw: (
            "0.22.0",
            "0.23.0",
            [
                types.SimpleNamespace(
                    target="claude",
                    scope="project",
                    destination=tmp_path,
                    success=False,
                    message="simulated update skip",
                )
            ],
        ),
    )
    res_upd_partial = runner.invoke(app, ["plugin", "update", "claude", "-p", str(tmp_path)])
    assert res_upd_partial.exit_code == 0
    assert "simulated update skip" in res_upd_partial.stdout

    # Generic Exception / FileExistsError exit code 1 paths
    def _raise_runtime(**_kw):
        raise RuntimeError("boom-plugin")

    def _raise_exists(**_kw):
        raise FileExistsError("already-there")

    monkeypatch.setattr(plugin_cmd_mod, "install_plugin", _raise_runtime)
    assert runner.invoke(app, ["plugin", "install", "claude", "-p", str(tmp_path)]).exit_code == 1

    monkeypatch.setattr(plugin_cmd_mod, "uninstall_plugin", _raise_runtime)
    assert runner.invoke(app, ["plugin", "uninstall", "claude", "-p", str(tmp_path)]).exit_code == 1

    monkeypatch.setattr(plugin_cmd_mod, "update_plugin", _raise_exists)
    assert runner.invoke(app, ["plugin", "update", "claude", "-p", str(tmp_path)]).exit_code == 1

    monkeypatch.setattr(plugin_cmd_mod, "update_plugin", _raise_runtime)
    assert runner.invoke(app, ["plugin", "update", "claude", "-p", str(tmp_path)]).exit_code == 1


