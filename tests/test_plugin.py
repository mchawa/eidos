"""Tests verifying Agent Plugin conformance to agent-plugins.org v1.0.0 and project-scoped CLI installer."""

from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from eidos.cli.main import app
from eidos.plugin.validator import validate_plugin_package, PLUGIN_NAME_PATTERN
from eidos.plugin.version import (
    SEMVER_PATTERN,
    is_valid_semver,
    parse_semver,
    compare_semver,
    get_bundled_plugin_version,
    get_installed_plugin_version,
    check_plugin_version_status,
)
from eidos.plugin.installer import (
    install_plugin,
    uninstall_plugin,
    update_plugin,
    PluginInstallTarget,
    locate_plugin_bundle,
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
    assert (agent_dir / "mcp_config.json").exists()
    assert not (agent_dir / "mcp.json").exists()
    assert (agent_dir / "skills" / "eidos" / "SKILL.md").exists()
    assert (agent_dir / "rules" / "AGENTS.md").exists()
    agent_report = validate_plugin_package(agent_dir)
    assert agent_report.is_valid

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
    assert (dest / "mcp_config.json").exists()
    assert not (dest / "mcp.json").exists()
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
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()


def test_plugin_uninstall_project_scope(tmp_path):
    """Verify uninstalling plugin from project scope removes plugin dir and MCP configs."""
    # Install first
    install_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path, force=True)
    plugin_dir = tmp_path / ".agents" / "plugins" / "eidos"
    cursor_mcp = tmp_path / ".cursor" / "mcp.json"
    claude_mcp = tmp_path / ".claude" / "mcp.json"

    assert plugin_dir.exists()
    assert cursor_mcp.exists()
    assert claude_mcp.exists()

    # Uninstall
    results = uninstall_plugin(target=PluginInstallTarget.PROJECT, project_dir=tmp_path)
    assert len(results) == 3
    for r in results:
        assert r.success

    assert not plugin_dir.exists()
    # If cursor/claude had only eidos, files are removed or cleaned
    if cursor_mcp.exists():
        with open(cursor_mcp, "r", encoding="utf-8") as f:
            c_data = json.load(f)
        assert "eidos" not in c_data.get("mcpServers", {})

    if claude_mcp.exists():
        with open(claude_mcp, "r", encoding="utf-8") as f:
            cl_data = json.load(f)
        assert "eidos" not in cl_data.get("mcpServers", {})


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
        json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "test-plugin"
        })
    )
    report_missing = validate_plugin_package(plugin_dir)
    assert not report_missing.is_valid
    assert any("missing required 'version' field" in err for err in report_missing.errors)

    # Invalid SemVer
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "test-plugin",
            "version": "v1.0"
        })
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
    assert status_installed["installed_version"] == "0.1.0"

    # 3. Outdated
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    with open(plugin_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = "0.0.9"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    status_outdated = check_plugin_version_status(project_dir=tmp_path)
    assert status_outdated["status"] == "outdated"
    assert status_outdated["installed_version"] == "0.0.9"

    # 4. Newer
    data["version"] = "0.2.0"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    status_newer = check_plugin_version_status(project_dir=tmp_path)
    assert status_newer["status"] == "newer"
    assert status_newer["installed_version"] == "0.2.0"


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
    assert "0.1.0" in res_inst.stdout

    # Outdated
    plugin_json = tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json"
    with open(plugin_json, "r", encoding="utf-8") as f:
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
    with open(plugin_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = "0.0.5"
    with open(plugin_json, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # Run update
    res = runner.invoke(app, ["plugin", "update", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert "Upgraded Eidos Agent Plugin" in res.stdout
    assert "0.0.5" in res.stdout
    assert "0.1.0" in res.stdout

    # Verify plugin.json was updated
    with open(plugin_json, "r", encoding="utf-8") as f:
        updated_data = json.load(f)
    assert updated_data["version"] == "0.1.0"


def test_cli_plugin_upgrade_alias(tmp_path):
    """Verify 'eidos plugin upgrade' alias CLI command (CTR-EIDOS-CLI)."""
    res = runner.invoke(app, ["plugin", "upgrade", "--project-dir", str(tmp_path)])
    assert res.exit_code == 0
    assert (tmp_path / ".agents" / "plugins" / "eidos" / "plugin.json").exists()


