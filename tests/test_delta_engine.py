"""Tests verifying the Deterministic Semantic SpecDelta Engine (eidos delta verify & apply --execute)."""

import json

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.lifecycle.scaffolder import (
    create_spec,
    execute_delta_mutations,
    init_repository,
    verify_delta_mutations,
)
from eidos.core.spec.parser import parse_spec_file

runner = CliRunner()


def test_delta_verify_all_satisfied(tmp_path):
    """Verify that verify_delta_mutations rejects unmodified modify_*/extend_* targets and passes once modified."""
    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    # 1. Create baseline container, capability, and policy
    create_spec(eidos_dir, "domain", "DOM-AUTH", title="Auth Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-AUTH-LOGIN", title="User Login", domain="DOM-AUTH")
    cnt_path = create_spec(eidos_dir, "container", "CNT-AUTH-SVC", title="Auth Service")
    pol_path = create_spec(eidos_dir, "policy", "POL-AUTH-PASS", title="Password Policy", rule="Min 8 chars.")

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
  - action: modify_container
    target_container: CNT-AUTH-SVC
    details: "Harden auth service."
  - action: extend_capability
    target_capability: CAP-AUTH-LOGIN
    details: "Add MFA support."
  - action: modify_policy
    target_policy: POL-AUTH-PASS
    details: "Increase complexity."
  - action: remove_container
    target_container: CNT-OLD-DEPRECATED
    details: "Verify old container is absent."
"""
    new_content = content.replace("mutations: []", mutations_yaml)
    dlt_path.write_text(new_content, encoding="utf-8")

    # 3. Before touching the target specs, modify_*/extend_* must report UNMODIFIED (only remove_container is satisfied)
    pre_verified, _pre_msg, pre_report = verify_delta_mutations(eidos_dir, "DLT-2026-050")
    assert pre_verified is False
    assert pre_report is not None
    assert pre_report.verified_count == 1
    assert [m.status for m in pre_report.mutations] == [
        "UNMODIFIED",
        "UNMODIFIED",
        "UNMODIFIED",
        "ABSENT_AS_EXPECTED",
    ]

    # 4. Now actually modify the three target living specifications
    cnt_path.write_text(cnt_path.read_text(encoding="utf-8") + "\n// Hardened auth service\n", encoding="utf-8")
    cap_path.write_text(cap_path.read_text(encoding="utf-8") + "\n// Added MFA support\n", encoding="utf-8")
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Min 8 chars.", "Min 12 chars."),
        encoding="utf-8",
    )

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


def test_delta_verify_declarative_expect_and_patch_invariant(tmp_path):
    """Verify Option 3: declarative expect post-conditions, structural sub-targets, and patch_invariant baseline checks."""
    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-AUTH", title="Auth Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-AUTH-001", title="Auth Cap", domain="DOM-AUTH")
    pol_path = create_spec(eidos_dir, "policy", "POL-AUTH-001", title="Password Policy", rule="Min 8 chars.")

    # Add an initial invariant to CAP-AUTH-001 before creating the delta
    cap_text = cap_path.read_text(encoding="utf-8").replace(
        "invariants: []",
        'invariants:\n    - id: INV-SPEC-VALIDATION\n      rule: "Initial rule"\n      test_binding: "tests/test_delta_engine.py::test_delta_verify_declarative_expect_and_patch_invariant"',
    )
    cap_path.write_text(cap_text, encoding="utf-8")

    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-054", title="Declarative & Invariant Patch")
    mutations_yaml = """mutations:
  - action: modify_policy
    target_policy: POL-AUTH-001
    expect:
      rule: "Min 14 chars."
  - action: patch_invariant
    target_invariant_id: INV-SPEC-VALIDATION
"""
    dlt_path.write_text(dlt_path.read_text(encoding="utf-8").replace("mutations: []", mutations_yaml), encoding="utf-8")

    # 1. Both fail initially with UNMODIFIED
    ok1, _, rep1 = verify_delta_mutations(eidos_dir, "DLT-2026-054")
    assert ok1 is False
    assert [m.status for m in rep1.mutations] == ["UNMODIFIED", "UNMODIFIED"]

    # 2. Update policy rule to satisfy expect, and patch invariant rule
    pol_path.write_text(pol_path.read_text(encoding="utf-8").replace("Min 8 chars.", "Min 14 chars."), encoding="utf-8")
    cap_path.write_text(cap_path.read_text(encoding="utf-8").replace("Initial rule", "Patched rule"), encoding="utf-8")

    ok2, _, rep2 = verify_delta_mutations(eidos_dir, "DLT-2026-054")
    assert ok2 is True
    assert [m.status for m in rep2.mutations] == ["MODIFIED", "MODIFIED"]



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
  - action: remove_container
    target_container: CNT-STILL-EXISTS
    details: "Container was not yet deleted."
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
    """Verify INV-MUTATION-REVERSIBILITY: execute_delta_mutations deterministically scaffolds missing specs and updates components."""
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


def test_delta_verify_requires_modification_evidence(tmp_path):
    """INV-DELTA-VERIFY-MODIFICATION-EVIDENCE: verify modify_*/extend_*/patch_* require positive evidence of change."""
    import os
    import shutil

    init_repository(tmp_path, name="test-system", with_plugin=False)
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-CORE-001", title="Core Cap", domain="DOM-CORE")
    other_cap_path = create_spec(eidos_dir, "capability", "CAP-OTHER-001", title="Other Cap", domain="DOM-CORE")
    cnt_path = create_spec(eidos_dir, "container", "CNT-CORE-SVC", title="Core Service")
    ctr_path = create_spec(eidos_dir, "contract", "CTR-CORE-API", title="Core API")
    pol_path = create_spec(eidos_dir, "policy", "POL-CORE-001", title="Core Policy", rule="Initial policy rule.")

    # Populate container with two inline components and a contract binding
    cnt_text = cnt_path.read_text(encoding="utf-8").replace(
        "components: []",
        (
            "components:\n"
            "  - id: CMP-TARGET\n"
            '    name: "TargetComponent"\n'
            '    responsibility: "Initial target responsibility."\n'
            "  - id: CMP-SIBLING\n"
            '    name: "SiblingComponent"\n'
            '    responsibility: "Initial sibling responsibility."'
        ),
    ).replace(
        "provides: []",
        "provides:\n    - CTR-CORE-API",
    )
    cnt_path.write_text(cnt_text, encoding="utf-8")

    # Populate capability with an invariant
    cap_text = cap_path.read_text(encoding="utf-8").replace(
        "invariants: []",
        (
            "invariants:\n"
            "    - id: INV-DELTA-VERIFY-MODIFICATION-EVIDENCE\n"
            '      rule: "Initial invariant rule."\n'
            '      test_binding: "tests/test_delta_engine.py::test_delta_verify_requires_modification_evidence"'
        ),
    )
    cap_path.write_text(cap_text, encoding="utf-8")
    _ = other_cap_path

    # Create delta (records initial SQLite baselines)
    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-060", title="Evidence Verification")
    mutations_yaml = """mutations:
  - action: modify_policy
    target_policy: POL-CORE-001
  - action: modify_component
    target_container: CNT-CORE-SVC
    target_component: CMP-TARGET
  - action: modify_contract
    target_container: CNT-CORE-SVC
    target_contract: CTR-CORE-API
  - action: patch_invariant
    target_capability: CAP-CORE-001
    target_invariant_id: INV-DELTA-VERIFY-MODIFICATION-EVIDENCE
"""
    dlt_path.write_text(dlt_path.read_text(encoding="utf-8").replace("mutations: []", mutations_yaml), encoding="utf-8")

    # 1. Remove local SQLite cache and bump mtimes on all target files so target.mtime > delta.mtime
    shutil.rmtree(eidos_dir / ".cache", ignore_errors=True)
    future_ns = dlt_path.stat().st_mtime_ns + 10_000_000_000
    for p in (pol_path, cnt_path, ctr_path, cap_path):
        os.utime(p, ns=(future_ns, future_ns))

    ok1, _, rep1 = verify_delta_mutations(eidos_dir, "DLT-2026-060")
    assert ok1 is False
    assert [m.target for m in rep1.mutations] == [
        "POL-CORE-001",
        "CMP-TARGET",
        "CTR-CORE-API",
        "INV-DELTA-VERIFY-MODIFICATION-EVIDENCE",
    ]
    assert [m.status for m in rep1.mutations] == [
        "UNMODIFIED",
        "UNMODIFIED",
        "UNMODIFIED",
        "UNMODIFIED",
    ]

    # 2. Editing a sibling component in the same container must leave CMP-TARGET as UNMODIFIED
    cnt_path.write_text(
        cnt_path.read_text(encoding="utf-8").replace(
            "Initial sibling responsibility.", "Updated sibling responsibility."
        ),
        encoding="utf-8",
    )
    ok2, _, rep2 = verify_delta_mutations(eidos_dir, "DLT-2026-060")
    assert ok2 is False
    assert rep2.mutations[1].target == "CMP-TARGET"
    assert rep2.mutations[1].status == "UNMODIFIED"

    # 3. Structural sub-target mismatch reports MISSING even when target entity exists elsewhere
    dlt_mismatch = create_spec(eidos_dir, "delta", "DLT-2026-061", title="Subtarget Preconditions")
    mismatch_yaml = """mutations:
  - action: patch_invariant
    target_capability: CAP-OTHER-001
    target_invariant_id: INV-DELTA-VERIFY-MODIFICATION-EVIDENCE
  - action: modify_component
    target_container: CNT-NONEXISTENT
    target_component: CMP-TARGET
  - action: modify_contract
    target_container: CNT-NONEXISTENT
    target_contract: CTR-CORE-API
"""
    dlt_mismatch.write_text(
        dlt_mismatch.read_text(encoding="utf-8").replace("mutations: []", mismatch_yaml),
        encoding="utf-8",
    )
    ok_mis, _, rep_mis = verify_delta_mutations(eidos_dir, "DLT-2026-061")
    assert ok_mis is False
    assert [m.status for m in rep_mis.mutations] == ["MISSING", "MISSING", "MISSING"]

    # 4. Now modify each targeted entity in DLT-2026-060 -> all four report MODIFIED
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Initial policy rule.", "Updated policy rule."),
        encoding="utf-8",
    )
    cnt_path.write_text(
        cnt_path.read_text(encoding="utf-8").replace(
            "Initial target responsibility.", "Updated target responsibility."
        ),
        encoding="utf-8",
    )
    ctr_path.write_text(
        ctr_path.read_text(encoding="utf-8") + "\n// Added endpoint details\n",
        encoding="utf-8",
    )
    cap_path.write_text(
        cap_path.read_text(encoding="utf-8").replace("Initial invariant rule.", "Updated invariant rule."),
        encoding="utf-8",
    )

    ok3, _, rep3 = verify_delta_mutations(eidos_dir, "DLT-2026-060")
    assert ok3 is True
    assert [m.status for m in rep3.mutations] == [
        "MODIFIED",
        "MODIFIED",
        "MODIFIED",
        "MODIFIED",
    ]

    # 5. Applied delta with an expect: post-condition still evaluates expect: and catches regressions
    dlt_applied = create_spec(eidos_dir, "delta", "DLT-2026-062", title="Applied Expect Regression")
    applied_yaml = """mutations:
  - action: modify_policy
    target_policy: POL-CORE-001
    expect:
      rule: "Expected post-apply rule."
  - action: modify_architecture
    target_architecture: ARCH-TEST-001
"""
    dlt_applied.write_text(
        dlt_applied.read_text(encoding="utf-8")
        .replace("status: proposed", "status: applied")
        .replace("mutations: []", applied_yaml),
        encoding="utf-8",
    )
    ok_reg, _, rep_reg = verify_delta_mutations(eidos_dir, "DLT-2026-062")
    assert ok_reg is False
    assert rep_reg.mutations[0].status == "UNMODIFIED"
    assert rep_reg.mutations[1].target == "ARCH-TEST-001"


