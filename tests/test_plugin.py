"""Tests verifying Agent Plugin conformance to agent-plugins.org v1.0.0 and project-scoped CLI installer."""

from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from eidos.cli.main import app
from eidos.plugin.validator import validate_plugin_package, PLUGIN_NAME_PATTERN
from eidos.plugin.installer import install_plugin, PluginInstallTarget, locate_plugin_bundle

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
    assert report.plugin_version == "0.1.0"
    assert "eidos" in report.skills
    assert "eidos" in report.mcp_servers
    assert "com.google.antigravity" in report.extensions


def test_repo_plugin_conformance():
    """Verify that plugins/eidos passes agent-plugins.org v1.0.0 validation."""
    plugin_dir = Path("plugins/eidos")
    assert plugin_dir.exists(), "plugins/eidos directory missing"

    report = validate_plugin_package(plugin_dir)
    assert report.is_valid, f"Repo plugin validation failed: {report.errors}"
    assert len(report.errors) == 0


def test_plugin_install_to_project_scope(tmp_path):
    """Verify project-scoped installation into workspace directories."""
    results = install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path, force=True)

    assert len(results) == 4
    targets = [r.target for r in results]
    assert "workspace" in targets
    assert "antigravity" in targets
    assert "cursor" in targets
    assert "claude" in targets

    # Check .agent-plugins/eidos
    ws_dir = tmp_path / ".agent-plugins" / "eidos"
    assert (ws_dir / "plugin.json").exists()
    assert (ws_dir / "mcp.json").exists()
    assert (ws_dir / "skills" / "eidos" / "SKILL.md").exists()
    ws_report = validate_plugin_package(ws_dir)
    assert ws_report.is_valid

    # Check .gemini/plugins/eidos
    gemini_dir = tmp_path / ".gemini" / "plugins" / "eidos"
    assert (gemini_dir / "plugin.json").exists()
    assert (gemini_dir / "rules" / "AGENTS.md").exists()

    # Check project-level MCP files
    cursor_mcp = tmp_path / ".cursor" / "mcp.json"
    assert cursor_mcp.exists()
    with open(cursor_mcp, "r", encoding="utf-8") as f:
        cursor_data = json.load(f)
    assert "eidos" in cursor_data["mcpServers"]

    claude_mcp = tmp_path / ".claude" / "mcp.json"
    assert claude_mcp.exists()
    with open(claude_mcp, "r", encoding="utf-8") as f:
        claude_data = json.load(f)
    assert "eidos" in claude_data["mcpServers"]


def test_plugin_install_to_custom_directory(tmp_path):
    """Verify installing plugin to a custom project-relative path."""
    dest = tmp_path / "installed_plugin"
    results = install_plugin(dest_path=dest, force=True)

    assert len(results) == 1
    assert results[0].success
    assert (dest / "plugin.json").exists()
    assert (dest / "mcp.json").exists()
    assert (dest / "skills" / "eidos" / "SKILL.md").exists()
    assert (dest / "rules" / "AGENTS.md").exists()

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
    assert "project" in res.stdout
    assert (tmp_path / ".agent-plugins" / "eidos" / "plugin.json").exists()
    assert (tmp_path / ".gemini" / "plugins" / "eidos" / "plugin.json").exists()
