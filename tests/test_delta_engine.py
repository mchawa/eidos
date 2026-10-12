"""Tests verifying the Deterministic Semantic SpecDelta Engine (eidos delta verify & apply --execute)."""

import json

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.parser import parse_spec_file
from eidos.core.scaffolder import (
    create_spec,
    execute_delta_mutations,
    init_repository,
    verify_delta_mutations,
)

runner = CliRunner()


def test_delta_verify_all_satisfied(tmp_path):
    """Verify that verify_delta_mutations reports is_verified=True when all mutations exist."""
    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    # 1. Create baseline container, capability, and policy
    create_spec(eidos_dir, "domain", "DOM-AUTH", title="Auth Domain")
    create_spec(eidos_dir, "capability", "CAP-AUTH-LOGIN", title="User Login", domain="DOM-AUTH")
    create_spec(eidos_dir, "container", "CNT-AUTH-SVC", title="Auth Service")
    create_spec(eidos_dir, "policy", "POL-AUTH-PASS", title="Password Policy", rule="Min 8 chars.")

    # 2. Create a SpecDelta declaring mutations on those entities and a removal of an absent entity
    dlt_path = create_spec(
        eidos_dir,
        "delta",
        "DLT-2026-050",
        title="Auth Hardening",
        motivation="Harden authentication logic.",
    )
    # Inject mutations into frontmatter
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - action: modify_component
    target_component: CNT-AUTH-SVC
    details: "Harden auth service."
  - action: extend_capability
    target_capability: CAP-AUTH-LOGIN
    details: "Add MFA support."
  - action: modify_policy
    target_policy: POL-AUTH-PASS
    details: "Increase complexity."
  - action: remove_component
    target_component: CNT-OLD-DEPRECATED
    details: "Verify old component is absent."
"""
    new_content = content.replace("mutations: []", mutations_yaml)
    dlt_path.write_text(new_content, encoding="utf-8")

    is_verified, msg, report = verify_delta_mutations(eidos_dir, "DLT-2026-050")
    assert is_verified is True
    assert report is not None
    assert report.total_mutations == 4
    assert report.verified_count == 4
    assert "All 4 mutation(s) verified" in msg

    # CLI test
    res = runner.invoke(app, ["delta", "verify", "DLT-2026-050", "-p", str(eidos_dir)])
    assert res.exit_code == 0
    assert "Verification Passed" in res.output

    # CLI JSON test
    res_json = runner.invoke(app, ["delta", "verify", "DLT-2026-050", "-p", str(eidos_dir), "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert data["is_verified"] is True
    assert data["total_mutations"] == 4


def test_delta_verify_unfulfilled_mutations(tmp_path):
    """Verify that verify_delta_mutations detects missing specs and un-executed removals."""
    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    # Create container that should be removed
    create_spec(eidos_dir, "container", "CNT-STILL-EXISTS", title="Zombie Container")

    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-051", title="Pending Feature")
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - action: add_capability
    new_capability_id: CAP-UNIMPLEMENTED-001
    details: "New capability not yet scaffolded."
  - action: remove_component
    target_component: CNT-STILL-EXISTS
    details: "Component was not yet deleted."
"""
    new_content = content.replace("mutations: []", mutations_yaml)
    dlt_path.write_text(new_content, encoding="utf-8")

    is_verified, _msg, report = verify_delta_mutations(eidos_dir, "DLT-2026-051")
    assert is_verified is False
    assert report is not None
    assert report.total_mutations == 2
    assert report.verified_count == 0
    assert report.mutations[0].status == "MISSING"
    assert report.mutations[1].status == "STILL_PRESENT"

    # CLI verification should fail with exit code 1
    res = runner.invoke(app, ["delta", "verify", "DLT-2026-051", "-p", str(eidos_dir)])
    assert res.exit_code == 1
    assert "Verification Incomplete" in res.output


def test_delta_execute_and_verify_lifecycle(tmp_path):
    """Verify that execute_delta_mutations deterministically scaffolds missing specs and updates components."""
    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-BILLING", title="Billing Domain")
    cnt_path = create_spec(eidos_dir, "container", "CNT-BILLING-ENGINE", title="Billing Engine")

    # Delta requesting: add_capability, add_policy, add_component (to container), and add_contract
    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-052", title="Billing Expansion")
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - action: add_capability
    new_capability_id: CAP-INVOICE-GEN
    target_domain: DOM-BILLING
    details: "Generate PDF invoices."
  - action: add_policy
    target_policy: POL-INVOICE-TAX
    details: "Invoices must apply regional VAT taxes."
  - action: add_component
    target_component: CMP-INVOICE-RENDERER
    target_container: CNT-BILLING-ENGINE
    details: "Renders invoices to PDF format."
  - action: add_contract
    target_contract: CTR-INVOICE-API
    details: "REST contract for invoicing."
"""
    new_content = content.replace("mutations: []", mutations_yaml)
    dlt_path.write_text(new_content, encoding="utf-8")

    # Pre-execution: verify fails
    is_ver, _, _report = verify_delta_mutations(eidos_dir, "DLT-2026-052")
    assert is_ver is False

    # Execute mutations
    ok, _msg, mutated = execute_delta_mutations(eidos_dir, "DLT-2026-052")
    assert ok is True
    assert len(mutated) >= 4

    # Verify newly created files exist
    assert (eidos_dir / "domains" / "billing" / "capabilities" / "cap-invoice-gen.adoc").exists()
    assert (eidos_dir / "policies" / "pol-invoice-tax.adoc").exists()
    assert (eidos_dir / "architecture" / "contracts" / "ctr-invoice-api.adoc").exists()

    # Verify component was inserted into container frontmatter
    cnt_doc = parse_spec_file(cnt_path)
    comps = cnt_doc.frontmatter.get("components", [])
    assert any(c.get("id") == "CMP-INVOICE-RENDERER" for c in comps)

    # Post-execution: verify now passes 100%!
    is_ver_after, _, report_after = verify_delta_mutations(eidos_dir, "DLT-2026-052")
    assert is_ver_after is True
    assert report_after.is_verified is True
    assert report_after.verified_count == 4


def test_delta_apply_cli_with_execute_and_verify(tmp_path):
    """Verify 'eidos delta apply --execute --verify' via CLI."""
    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-NOTIFY", title="Notification Domain")

    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-053", title="Push Notifications")
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - action: add_capability
    new_capability_id: CAP-PUSH-NOTIFY
    target_domain: DOM-NOTIFY
    details: "Send web push notifications."
"""
    new_content = content.replace("mutations: []", mutations_yaml)
    dlt_path.write_text(new_content, encoding="utf-8")

    # Applying with --verify without --execute should fail
    res_fail = runner.invoke(app, ["delta", "apply", "DLT-2026-053", "-p", str(eidos_dir), "--verify"])
    assert res_fail.exit_code == 1
    assert "Verification failed before apply" in res_fail.output

    # Applying with --execute and --verify should execute, verify, and mark applied
    res_ok = runner.invoke(
        app,
        ["delta", "apply", "DLT-2026-053", "-p", str(eidos_dir), "--execute", "--verify", "--json"],
    )
    assert res_ok.exit_code == 0
    data = json.loads(res_ok.output)
    assert data["success"] is True
    assert data["executed"] is True
    assert len(data["mutated_files"]) >= 1

    # Verify delta is now applied
    dlt_doc = parse_spec_file(dlt_path)
    assert dlt_doc.frontmatter.get("status") == "applied"
