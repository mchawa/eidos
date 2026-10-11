"""Tests verifying specification scaffolding, reference checking, and delta lifecycle management (CTR-EIDOS-CLI, POL-002)."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.scaffolder import (
    create_spec,
    delete_spec,
    list_specs,
    list_deltas,
    apply_delta,
    find_spec_by_id_or_path,
    find_references_to_spec,
)
from eidos.core.parser import parse_spec_file
from eidos.core.validator import SpecValidator
from eidos.mcp.server import handle_tool_call

runner = CliRunner()


def assert_valid_spec(validator: SpecValidator, path: Path):
    doc = parse_spec_file(path)
    errors = validator.validate_document(doc)
    assert len(errors) == 0, f"Validation errors in {path.name}: {errors}"


def test_scaffold_all_spec_types(tmp_path):
    """Verify that create_spec scaffolds syntactically valid documents for every schema type (CTR-EIDOS-CLI, POL-002)."""
    validator = SpecValidator()

    # 1. Domain
    dom_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="domain",
        raw_id="DOM-TEST-AUTH",
        title="Authentication Domain",
    )
    assert dom_file.exists()
    assert_valid_spec(validator, dom_file)

    # 2. Capability
    cap_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="capability",
        raw_id="CAP-AUTH-001",
        title="Password Login",
        domain="DOM-TEST-AUTH",
    )
    assert cap_file.exists()
    assert_valid_spec(validator, cap_file)

    # 3. Policy
    pol_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="policy",
        raw_id="POL-003-AUTH-LOCK",
        title="Account Lockout Policy",
        category="security",
        rule="Lock account after 5 failed attempts.",
    )
    assert pol_file.exists()
    assert_valid_spec(validator, pol_file)

    # 4. Contract
    ctr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="contract",
        raw_id="CTR-AUTH-REST",
        title="Auth REST API",
        protocol="openapi",
        owning_component="CMP-AUTH-SERVICE",
        specification_file="src/auth.yaml",
    )
    assert ctr_file.exists()
    assert_valid_spec(validator, ctr_file)

    # 5. Component
    cmp_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="component",
        raw_id="CMP-AUTH-SERVICE",
        title="Auth Microservice",
        container_type="service",
        runtime="Rust / Axum",
        package_path="packages/auth",
    )
    assert cmp_file.exists()
    assert_valid_spec(validator, cmp_file)

    # 6. Persona
    per_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="persona",
        raw_id="PER-SECURITY-AUDITOR",
        title="Security Auditor",
        role_type="auditor",
    )
    assert per_file.exists()
    assert_valid_spec(validator, per_file)

    # 7. Delta
    dlt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="delta",
        raw_id="DLT-2026-999",
        title="MFA Integration",
        kind="feature_addition",
        motivation="Require TOTP MFA.",
    )
    assert dlt_file.exists()
    assert_valid_spec(validator, dlt_file)

    # 8. Exploration
    exp_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="exploration",
        raw_id="EXP-PASSKEY-SPIKE",
        title="WebAuthn Passkey Spike",
    )
    assert exp_file.exists()
    assert_valid_spec(validator, exp_file)


def test_list_specs_and_filtering(tmp_path):
    """Verify list_specs returns all documents with accurate metadata and supports filtering (CTR-EIDOS-CLI)."""
    create_spec(tmp_path, "policy", "POL-001", "Policy 1")
    create_spec(tmp_path, "policy", "POL-002", "Policy 2")
    create_spec(tmp_path, "contract", "CTR-CLI", "Contract 1")

    all_specs = list_specs(tmp_path)
    assert len(all_specs) == 3

    policy_specs = list_specs(tmp_path, type_filter="policy")
    assert len(policy_specs) == 2
    assert all("policy" in s["type"] for s in policy_specs)

    contract_specs = list_specs(tmp_path, type_filter="contract")
    assert len(contract_specs) == 1
    assert contract_specs[0]["id"] == "CTR-CLI"


def test_spec_deletion_safety_with_references(tmp_path):
    """Verify delete_spec blocks deletion if referenced by other documents, unless forced (CTR-EIDOS-CLI, POL-001)."""
    # Create component and contract that references it
    create_spec(tmp_path, "component", "CMP-CORE", "Core Component")
    create_spec(tmp_path, "contract", "CTR-API", "API Contract", owning_component="CMP-CORE")

    # Attempt delete CMP-CORE without force
    success, msg, refs = delete_spec(tmp_path, "CMP-CORE", force=False)
    assert not success
    assert "referenced by" in msg
    assert len(refs) == 1

    # Attempt delete with force
    success_force, msg_force, _ = delete_spec(tmp_path, "CMP-CORE", force=True)
    assert success_force
    assert not find_spec_by_id_or_path(tmp_path, "CMP-CORE")


def test_delta_lifecycle_apply(tmp_path):
    """Verify SpecDelta transition in flat storage with in-place status update and symlink views (CTR-EIDOS-CLI, CAP-AGENT-001)."""
    dlt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="delta",
        raw_id="DLT-2026-100",
        title="Rate Limiter",
        motivation="DDoS mitigation.",
    )
    assert dlt_file.exists()
    assert dlt_file.name == "dlt-2026-100.adoc"
    assert dlt_file.parent == tmp_path / "deltas"

    # Verify initial symlink in active/ and proposed/
    assert (tmp_path / "deltas" / "by-status" / "active" / "dlt-2026-100.adoc").exists()
    assert (tmp_path / "deltas" / "by-status" / "proposed" / "dlt-2026-100.adoc").exists()

    # Apply delta
    success, msg, applied_path = apply_delta(tmp_path, "DLT-2026-100")
    assert success
    assert applied_path is not None
    assert applied_path.resolve() == dlt_file.resolve()

    # Verify content status is updated in-place
    doc = parse_spec_file(applied_path)
    assert doc.frontmatter.get("status") == "applied"

    # Verify symlink moved to applied/ and removed from active/
    assert (tmp_path / "deltas" / "by-status" / "applied" / "dlt-2026-100.adoc").exists()
    assert not (tmp_path / "deltas" / "by-status" / "active" / "dlt-2026-100.adoc").exists()

    # Verify content status is updated and valid
    validator = SpecValidator()
    assert_valid_spec(validator, applied_path)


def test_cli_spec_commands(tmp_path):
    """Verify 'eidos spec' CLI command suite (CTR-EIDOS-CLI)."""
    # 1. Create capability
    res_create = runner.invoke(app, [
        "spec", "create", "capability", "CAP-TEST-001",
        "--title", "Test Capability",
        "--path", str(tmp_path),
    ])
    assert res_create.exit_code == 0
    assert "Created and validated" in res_create.stdout

    # 2. List
    res_list = runner.invoke(app, ["spec", "list", "--path", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "CAP-TEST-001" in res_list.stdout

    # 3. Show
    res_show = runner.invoke(app, ["spec", "show", "CAP-TEST-001", "--path", str(tmp_path)])
    assert res_show.exit_code == 0
    assert "CAP-TEST-001" in res_show.stdout

    # 4. Delete
    res_delete = runner.invoke(app, ["spec", "delete", "CAP-TEST-001", "--path", str(tmp_path)])
    assert res_delete.exit_code == 0
    assert "Successfully deleted" in res_delete.stdout


def test_cli_delta_commands(tmp_path):
    """Verify 'eidos delta' CLI command suite (CTR-EIDOS-CLI)."""
    # 1. Create delta
    res_create = runner.invoke(app, [
        "delta", "create", "DLT-2026-555",
        "--title", "CLI Expansion",
        "--kind", "feature_addition",
        "--motivation", "Add delta management.",
        "--path", str(tmp_path),
    ])
    assert res_create.exit_code == 0
    assert "Created SpecDelta" in res_create.stdout

    # 2. List deltas
    res_list = runner.invoke(app, ["delta", "list", "--path", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "DLT-2026-555" in res_list.stdout
    assert "ACTIVE" in res_list.stdout

    # 3. Status dashboard
    res_status = runner.invoke(app, ["delta", "status", "--path", str(tmp_path)])
    assert res_status.exit_code == 0
    assert "SpecDelta Lifecycle Status Dashboard" in res_status.stdout
    assert "DLT-2026-555" in res_status.stdout

    # 4. Sync views
    res_sync = runner.invoke(app, ["delta", "sync-views", "--path", str(tmp_path)])
    assert res_sync.exit_code == 0
    assert "Synchronized" in res_sync.stdout

    # 5. Apply delta
    res_apply = runner.invoke(app, ["delta", "apply", "DLT-2026-555", "--path", str(tmp_path)])
    assert res_apply.exit_code == 0
    assert "Successfully applied" in res_apply.stdout

    # 6. Delete applied delta
    res_delete = runner.invoke(app, ["delta", "delete", "DLT-2026-555", "--path", str(tmp_path)])
    assert res_delete.exit_code == 0
    assert "Successfully deleted" in res_delete.stdout


def test_cli_top_level_shortcuts(tmp_path):
    """Verify top-level 'eidos create', 'eidos list', and 'eidos delete' shortcuts (CTR-EIDOS-CLI)."""
    # Create shortcut
    res_create = runner.invoke(app, [
        "create", "policy", "POL-009-RULE",
        "--title", "Shortcut Policy",
        "--path", str(tmp_path),
    ])
    assert res_create.exit_code == 0
    assert "POL-009-RULE" in res_create.stdout

    # List shortcut
    res_list = runner.invoke(app, ["list", "--path", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "POL-009-RULE" in res_list.stdout

    # Delete shortcut
    res_delete = runner.invoke(app, ["delete", "POL-009-RULE", "--path", str(tmp_path)])
    assert res_delete.exit_code == 0
    assert "Successfully deleted" in res_delete.stdout


def test_mcp_spec_lifecycle_tools(tmp_path):
    """Verify MCP tools for spec creation, listing, applying delta, and deletion (CTR-EIDOS-CLI)."""
    # 1. eidos_create_spec
    create_res = handle_tool_call("eidos_create_spec", {
        "path": str(tmp_path),
        "type": "policy",
        "id": "POL-100-MCP",
        "title": "MCP Governed Policy",
        "rule": "MCP tools must be tested."
    })
    assert "Created and validated" in create_res[0]["text"]

    # 2. eidos_list_specs
    list_res = handle_tool_call("eidos_list_specs", {"path": str(tmp_path)})
    specs = json.loads(list_res[0]["text"])
    assert any(s["id"] == "POL-100-MCP" for s in specs)

    # 3. eidos_create_delta & eidos_apply_delta
    handle_tool_call("eidos_create_spec", {
        "path": str(tmp_path),
        "type": "delta",
        "id": "DLT-2026-888",
        "title": "MCP Delta",
        "kind": "feature_addition",
        "motivation": "Test MCP delta."
    })
    apply_res = handle_tool_call("eidos_apply_delta", {
        "path": str(tmp_path),
        "target": "DLT-2026-888"
    })
    apply_data = json.loads(apply_res[0]["text"])
    assert apply_data["success"]

    # 4. eidos_delete_spec
    del_res = handle_tool_call("eidos_delete_spec", {
        "path": str(tmp_path),
        "target": "POL-100-MCP"
    })
    del_data = json.loads(del_res[0]["text"])
    assert del_data["success"]


def test_all_five_skills_in_bundle():
    """Verify that the plugin contains all 5 specialized skills (CAP-AGENT-001)."""
    from eidos.plugin.installer import locate_plugin_bundle
    from eidos.plugin.validator import validate_plugin_package

    bundle_dir = locate_plugin_bundle()
    report = validate_plugin_package(bundle_dir)

    assert report.is_valid
    expected_skills = {
        "eidos",
        "eidos-delta",
        "eidos-architecture",
        "eidos-invariants",
        "eidos-extract",
    }
    assert expected_skills.issubset(set(report.skills))


def test_self_contained_eidos_gitignore(tmp_path):
    """Verify eidos/.gitignore is automatically created and isolates status views from project root."""
    create_spec(
        eidos_dir=tmp_path,
        doc_type_key="delta",
        raw_id="DLT-2026-999",
        title="Gitignore Isolation",
        motivation="Self-contained eidos/.gitignore",
    )
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert "deltas/by-status/" in content
