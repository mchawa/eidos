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
    init_repository(tmp_path, name="test-system")
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
    init_repository(tmp_path, name="test-system")
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
    init_repository(tmp_path, name="test-system")
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
    init_repository(tmp_path, name="test-system")
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

    # 2. Type-safe deletion: remove_component passed a capability ID (CAP-INVOICE-GEN) must fail and NOT delete the file
    cap_file = eidos_dir / "domains" / "billing" / "capabilities" / "cap-invoice-gen.adoc"
    pol_file = eidos_dir / "policies" / "pol-invoice-tax.adoc"
    original_pol_bytes = pol_file.read_bytes()

    dlt_bad_del = create_spec(eidos_dir, "delta", "DLT-2026-055", title="Wrong Delete Type")
    bad_del_yaml = """mutations:
  - action: add_policy
    target_policy: POL-SHOULD-ROLLBACK
    details: "Temporary policy that must be rolled back on subsequent failure."
  - action: remove_component
    target_component: CAP-INVOICE-GEN
"""
    dlt_bad_del.write_text(
        dlt_bad_del.read_text(encoding="utf-8").replace("mutations: []", bad_del_yaml),
        encoding="utf-8",
    )
    ok_bad_del, msg_bad_del, mut_bad_del = execute_delta_mutations(eidos_dir, "DLT-2026-055")
    assert ok_bad_del is False
    assert "Type mismatch" in msg_bad_del
    assert mut_bad_del == []
    assert cap_file.exists()
    assert not (eidos_dir / "policies" / "pol-should-rollback.adoc").exists()

    # 3. Unsupported action: must fail and roll back prior mutations
    dlt_unsup = create_spec(eidos_dir, "delta", "DLT-2026-056", title="Unsupported Action Rollback")
    unsup_yaml = """mutations:
  - action: add_policy
    target_policy: POL-UNSUP-ROLLBACK
    details: "Must be rolled back."
  - action: hallucinated_action
    target_policy: POL-INVOICE-TAX
"""
    dlt_unsup.write_text(
        dlt_unsup.read_text(encoding="utf-8").replace("mutations: []", unsup_yaml),
        encoding="utf-8",
    )
    ok_unsup, msg_unsup, _ = execute_delta_mutations(eidos_dir, "DLT-2026-056")
    assert ok_unsup is False
    assert "Unsupported mutation action" in msg_unsup
    assert not (eidos_dir / "policies" / "pol-unsup-rollback.adoc").exists()

    # 4. Pre-commit SpecValidator rollback in apply_delta: invalid frontmatter mutation rolls back all changes
    from eidos.core.lifecycle.delta_engine import apply_delta

    dlt_invalid = create_spec(eidos_dir, "delta", "DLT-2026-057", title="Schema Validation Rollback")
    invalid_yaml = """mutations:
  - action: add_policy
    target_policy: POL-SCHEMA-ROLLBACK
    details: "Must be rolled back on schema validation failure."
  - action: modify_policy
    target_policy: POL-INVOICE-TAX
    expect:
      non_existent_schema_field: "invalid"
"""
    dlt_invalid.write_text(
        dlt_invalid.read_text(encoding="utf-8").replace("mutations: []", invalid_yaml),
        encoding="utf-8",
    )
    ok_apply, msg_apply, _, mut_apply = apply_delta(eidos_dir, "DLT-2026-057", execute=True, verify=True)
    assert ok_apply is False
    assert "Transactional rollback" in msg_apply
    assert mut_apply == []
    assert not (eidos_dir / "policies" / "pol-schema-rollback.adoc").exists()
    assert pol_file.read_bytes() == original_pol_bytes
    assert parse_spec_file(dlt_invalid).frontmatter.get("status") == "proposed"


def test_delta_apply_cli_with_execute_and_verify(tmp_path):
    """Verify 'eidos delta apply --execute --verify' via CLI."""
    init_repository(tmp_path, name="test-system")
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

    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-CORE-001", title="Core Cap", domain="DOM-CORE")
    other_cap_path = create_spec(eidos_dir, "capability", "CAP-OTHER-001", title="Other Cap", domain="DOM-CORE")
    cnt_path = create_spec(eidos_dir, "container", "CNT-CORE-SVC", title="Core Service")
    ctr_path = create_spec(eidos_dir, "contract", "CTR-CORE-API", title="Core API")
    pol_path = create_spec(eidos_dir, "policy", "POL-CORE-001", title="Core Policy", rule="Initial policy rule.")

    # Populate container with two inline components and a contract binding
    cnt_text = (
        cnt_path.read_text(encoding="utf-8")
        .replace(
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
        )
        .replace(
            "provides: []",
            "provides:\n    - CTR-CORE-API",
        )
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

    # 6. All standalone remove_* enum actions verify ABSENT_AS_EXPECTED when absent
    dlt_removals = create_spec(eidos_dir, "delta", "DLT-2026-063", title="Standalone Removals")
    removals_yaml = """mutations:
  - action: remove_domain
    target_domain: DOM-REMOVED
  - action: remove_persona
    target_persona: PER-REMOVED
  - action: remove_design_system
    target_design_system: DS-REMOVED
  - action: remove_client
    target_client: CLT-REMOVED
  - action: remove_flow
    target_flow: FLOW-REMOVED
"""
    dlt_removals.write_text(
        dlt_removals.read_text(encoding="utf-8").replace("mutations: []", removals_yaml),
        encoding="utf-8",
    )
    ok_rem, _, rep_rem = verify_delta_mutations(eidos_dir, "DLT-2026-063")
    assert ok_rem is True
    assert [m.status for m in rep_rem.mutations] == ["ABSENT_AS_EXPECTED"] * 5


def test_delta_schema_action_coverage_and_git_baseline_fallback(tmp_path):
    """Verify 100% schema action coverage # eidos:fixture and Git-backed baseline SHA fallback when SQLite cache is absent."""
    import shutil
    import subprocess

    import yaml

    from eidos.core.lifecycle.delta_engine import SUPPORTED_MUTATION_ACTIONS
    from eidos.core.workspace.assets import get_assets_path

    # 1. Verify SUPPORTED_MUTATION_ACTIONS covers 100% of delta.schema.yaml enum values
    delta_schema_path = get_assets_path("schemas/v1/delta/delta.schema.yaml")
    schema_data = yaml.safe_load(delta_schema_path.read_text(encoding="utf-8"))
    schema_actions = set(schema_data["properties"]["mutations"]["items"]["properties"]["action"]["enum"])
    assert schema_actions == set(SUPPORTED_MUTATION_ACTIONS)

    # 2. Verify Git baseline SHA fallback on fresh clone / cache wipe
    init_repository(tmp_path, name="git-baseline-test")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-GIT", title="Git Domain")
    pol_path = create_spec(eidos_dir, "policy", "POL-GIT-001", title="Git Policy", rule="Initial git rule.")
    cnt_path = create_spec(eidos_dir, "container", "CNT-GIT-SVC", title="Git Service")
    cnt_path.write_text(
        cnt_path.read_text(encoding="utf-8").replace(
            "components: []",
            'components:\n  - id: CMP-GIT-COMP\n    name: "GitComp"\n    responsibility: "Initial comp."',
        ),
        encoding="utf-8",
    )
    cap_path = create_spec(eidos_dir, "capability", "CAP-GIT-001", title="Git Cap", domain="DOM-GIT")
    cap_path.write_text(
        cap_path.read_text(encoding="utf-8").replace(
            "invariants: []",
            (
                "invariants:\n"
                "    - id: INV-GIT-001\n"
                '      rule: "Initial inv rule."\n'
                '      test_binding: "tests/test_delta_engine.py::test_delta_schema_action_coverage_and_git_baseline_fallback"'
            ),
        ),
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Baseline specs"], cwd=tmp_path, check=True, capture_output=True)

    # Create delta and modify specs, then commit on branch and wipe eidos/.cache (simulating CI / fresh clone)
    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-070", title="Git Fallback Verification")
    mutations_yaml = """mutations:
  - action: modify_policy
    target_policy: POL-GIT-001
  - action: modify_component
    target_container: CNT-GIT-SVC
    target_component: CMP-GIT-COMP
  - action: patch_invariant
    target_capability: CAP-GIT-001
    target_invariant_id: INV-GIT-001
"""
    dlt_path.write_text(dlt_path.read_text(encoding="utf-8").replace("mutations: []", mutations_yaml), encoding="utf-8")

    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Initial git rule.", "Updated git rule."),
        encoding="utf-8",
    )
    cnt_path.write_text(
        cnt_path.read_text(encoding="utf-8").replace("Initial comp.", "Updated comp."),
        encoding="utf-8",
    )
    cap_path.write_text(
        cap_path.read_text(encoding="utf-8").replace("Initial inv rule.", "Updated inv rule."),
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Apply delta changes"], cwd=tmp_path, check=True, capture_output=True)

    # Wipe SQLite cache completely so verification must recover pre-delta baselines from Git history (commit~1)
    shutil.rmtree(eidos_dir / ".cache", ignore_errors=True)

    ok_git, _, rep_git = verify_delta_mutations(eidos_dir, "DLT-2026-070")
    assert ok_git is True
    assert [m.status for m in rep_git.mutations] == ["MODIFIED", "MODIFIED", "MODIFIED"]


def test_delta_vcs_base_commit_collision_free_ids_and_cow_transaction(tmp_path):
    """Verify vcs.base_commit on uncommitted deltas, collision-free delta IDs, and copy-on-write _MutationTransaction."""
    import shutil
    import subprocess

    from eidos.core.lifecycle.delta_engine import (
        _MutationTransaction,
        create_delta,
        generate_collision_free_delta_id,
    )

    init_repository(tmp_path, name="cow-baseline-test")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-COW", title="COW Domain")
    pol_path = create_spec(eidos_dir, "policy", "POL-COW-001", title="COW Policy", rule="Initial cow rule.")
    unmodified_pol = create_spec(
        eidos_dir, "policy", "POL-UNTOUCHED-001", title="Untouched Policy", rule="Untouched rule."
    )

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial baseline"], cwd=tmp_path, check=True, capture_output=True)

    # 1. Collision-free auto and slug-qualified delta IDs + vcs.base_commit persistence
    dlt_auto = create_delta(eidos_dir, raw_id="auto", title="Automated Portable Baseline", slug_suffix=True)
    dlt_doc = parse_spec_file(dlt_auto)
    assert dlt_doc.doc_id.startswith("DLT-")
    assert "AUTOMATED-PORTABLE" in dlt_doc.doc_id
    vcs_meta = dlt_doc.frontmatter.get("vcs", {})
    assert isinstance(vcs_meta, dict) and len(vcs_meta.get("base_commit", "")) >= 7

    next_id = generate_collision_free_delta_id(eidos_dir, raw_id="auto", title="Second Delta")
    assert next_id != dlt_doc.doc_id

    # 2. Uncommitted delta + wiped .cache/index.db resolves baseline deterministically via vcs.base_commit
    mutations_yaml = """mutations:
  - action: modify_policy
    target_policy: POL-COW-001
"""
    dlt_auto.write_text(
        dlt_auto.read_text(encoding="utf-8").replace("mutations: []", mutations_yaml),
        encoding="utf-8",
    )
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Initial cow rule.", "Modified cow rule on branch."),
        encoding="utf-8",
    )
    # Even if the policy modification is committed while the delta file remains uncommitted AND .cache is wiped:
    subprocess.run(["git", "add", str(pol_path)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Modify policy before committing delta"], cwd=tmp_path, check=True, capture_output=True)
    shutil.rmtree(eidos_dir / ".cache", ignore_errors=True)

    ok_uncommitted, _, rep_uncommitted = verify_delta_mutations(eidos_dir, dlt_doc.doc_id)
    assert ok_uncommitted is True
    assert rep_uncommitted.mutations[0].status == "MODIFIED"

    # 3. Copy-on-write _MutationTransaction snapshots only targeted files, not untouched specs
    tx = _MutationTransaction(eidos_dir)
    tx.snapshot()
    assert tx.original_files == {}
    assert unmodified_pol.resolve() not in tx.original_files

    orig_pol_bytes = pol_path.read_bytes()
    tx.record_before_write(pol_path)
    assert pol_path.resolve() in tx.original_files
    assert unmodified_pol.resolve() not in tx.original_files

    pol_path.write_text("corrupted content", encoding="utf-8")
    created_tmp = eidos_dir / "policies" / "pol-temp-created.adoc"
    tx.record_before_write(created_tmp)
    created_tmp.write_text("temp created", encoding="utf-8")

    tx.rollback()
    assert pol_path.read_bytes() == orig_pol_bytes
    assert not created_tmp.exists()


def test_apply_expect_or_details_produces_substantive_non_comment_diff(tmp_path):
    """Verify DLT-2026-055: _apply_expect_or_details_to_spec never appends '// {details}' comments stripped by _normalized_body_lines."""
    from eidos.core.lifecycle.delta_engine import _apply_expect_or_details_to_spec
    from eidos.vcs.structural import _normalized_body_lines, _split_spec

    init_repository(tmp_path, name="substantive-apply-test")
    eidos_dir = tmp_path / "eidos"
    cnt_path = create_spec(eidos_dir, "container", "CNT-SUB-SVC", title="Substantive Service")
    before = cnt_path.read_text(encoding="utf-8")

    # 1. Comment-only details are rejected
    assert _apply_expect_or_details_to_spec(cnt_path, None, "// comment only") is False
    assert cnt_path.read_text(encoding="utf-8") == before

    # 2. Substantive details are written as non-comment body content preserved by _normalized_body_lines
    assert _apply_expect_or_details_to_spec(cnt_path, None, "Added gRPC health check endpoint.") is True
    after = cnt_path.read_text(encoding="utf-8")
    assert "// Added gRPC health check endpoint." not in after
    _, old_body = _split_spec(before)
    _, new_body = _split_spec(after)
    assert _normalized_body_lines(old_body) != _normalized_body_lines(new_body)


def test_deep_review_subsystem_3_scaffolder_catalog_delta_and_changelog_edge_cases(tmp_path):
    """Verify contract owning_container fallback ignores CMP-*, _set_frontmatter_scalar preserves '#' in quotes,
    set_delta_status('applied') enforces verification, and generate_changelog includes commits in tagless repos."""
    import subprocess

    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar
    from eidos.core.lifecycle.changelog import ChangelogGenerator
    from eidos.core.lifecycle.delta_engine import get_delta_status_summary, list_deltas, set_delta_status
    from eidos.core.spec.parser import parse_spec_file

    init_repository(tmp_path, name="subsystem-3-deep")
    eidos_dir = tmp_path / "eidos"

    # 1. Contract owning_container fallback selects tech/container even when a promoted component sorts earlier
    (eidos_dir / "architecture" / "components").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "components" / "cmp-aaa.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-AAA\n"
        "name: Alpha Component\n"
        "responsibility: Sorts before containers.\n"
        "---\n"
        "= Alpha Component\n",
        encoding="utf-8",
    )
    create_spec(eidos_dir, "container", "CNT-ZZZ", title="Zeta Container")
    ctr_path = create_spec(eidos_dir, "contract", "CTR-AUTO-OWNER", title="Auto Owner Contract")
    ctr_doc = parse_spec_file(ctr_path)
    assert ctr_doc.frontmatter.get("owning_container") == "CNT-ZZZ"

    # 2. _set_frontmatter_scalar preserves '#' inside quoted YAML strings and handles empty scalar keys
    sample_fm = (
        "---\n"
        'title: "Channel #general & #alerts"\n'
        "status:\n"
        "---\n"
        "= Sample\n"
    )
    updated_fm = _set_frontmatter_scalar(sample_fm, "title", '"Updated #channel"')
    updated_fm = _set_frontmatter_scalar(updated_fm, "status", "accepted")
    assert 'title: "Updated #channel"' in updated_fm
    assert "status: accepted" in updated_fm

    # 3. Draft delta is included in active deltas and set_delta_status(..., 'applied') rejects unverified mutations
    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-088", title="Draft Gate Delta")
    dlt_text = dlt_path.read_text(encoding="utf-8")
    dlt_text = _set_frontmatter_scalar(dlt_text, "status", "draft")
    dlt_text = dlt_text.replace(
        "mutations: []",
        "mutations:\n  - action: modify_container\n    target_container: CNT-ZZZ\n    details: Require TLS 1.3",
    )
    dlt_path.write_text(dlt_text, encoding="utf-8")

    active_list = list_deltas(eidos_dir)
    assert any(d["id"] == "DLT-2026-088" for d in active_list)
    summary = get_delta_status_summary(eidos_dir)
    assert summary["active_count"] >= 1
    assert any(d["id"] == "DLT-2026-088" for d in summary["by_status"]["draft"])

    ok_apply, msg_apply, _, _ = set_delta_status(eidos_dir, "DLT-2026-088", "applied")
    assert ok_apply is False
    assert "Cannot transition" in msg_apply

    # 4. ChangelogGenerator includes conventional commits even when the repo has 0 Git tags
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat(core): add tagless changelog support"], cwd=tmp_path, check=True, capture_output=True)

    changelog_md = ChangelogGenerator(repo_root=tmp_path, eidos_dir=eidos_dir).generate_changelog()
    assert "add tagless changelog support" in changelog_md.lower()


def test_delta_engine_all_mutation_families_and_file_restoration_rollback(tmp_path):
    """Verify add/patch/remove_invariant, modify_component, modify_architecture, deprecate_*, remove_*, and file deletion rollback."""  # eidos:fixture
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar
    from eidos.core.lifecycle.delta_engine import (
        apply_delta,
        execute_delta_mutations,
        verify_delta_mutations,
    )
    from eidos.core.spec.parser import parse_spec_file

    init_repository(tmp_path, name="all-mutations-test")
    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "architecture").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "system-architecture.adoc").write_text(
        "---\n"
        "type: tech/architecture\n"
        "schema: schemas/v1/tech/architecture.schema.yaml\n"
        "id: ARCH-TEST-001\n"
        "title: Test Technical Architecture\n"
        "topology_style: modular_monolith\n"
        "c4_context:\n"
        "  actors:\n"
        "    - PER-DEV\n"
        "c4_containers:\n"
        "  - CNT-APP\n"
        "solution_strategy:\n"
        "  - Deterministic verification\n"
        "cross_cutting_concepts:\n"
        "  telemetry_and_logging: Structured JSON\n"
        "deployment_view:\n"
        "  hosting_model: Local CLI\n"
        "  ci_cd_engine: GitHub Actions\n"
        "---\n"
        "= Test Technical Architecture\n",
        encoding="utf-8",
    )

    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")
    create_spec(eidos_dir, "persona", "PER-DEV", title="Developer", role_type="developer")
    cnt_path = create_spec(eidos_dir, "container", "CNT-APP", title="App Container", package_path="src/app")
    # Add an inline component to CNT-APP
    cnt_text = cnt_path.read_text(encoding="utf-8").replace(
        "components: []",
        "components:\n  - id: CMP-INLINE-WORKER\n    name: Inline Worker\n    responsibility: Process jobs.\n    source_path: src/app/worker.py",
    )
    cnt_path.write_text(cnt_text, encoding="utf-8")

    cap_path = create_spec(eidos_dir, "capability", "CAP-LEGACY-001", title="Legacy Cap", domain="DOM-CORE")
    pol_path = create_spec(
        eidos_dir,
        "policy",
        "POL-900-TEST",
        title="Test Policy",
        category="quality",
        rule="Enforce test rule.",
    )
    pol_del_path = create_spec(
        eidos_dir,
        "policy",
        "POL-901-DELETE",
        title="Delete Policy",
        category="quality",
        rule="To be deleted.",
    )
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_inv.py").write_text(
        "def test_inv_900():\n    # INV-900-A\n    assert True\n",
        encoding="utf-8",
    )

    # 1. Delta exercising add_invariant, modify_component, modify_architecture, deprecate_capability, remove_policy
    dlt1 = create_spec(eidos_dir, "delta", "DLT-2026-910", title="Mutation Families Wave 1")
    dlt1_fm = (
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-910\n"
        "title: Mutation Families Wave 1\n"
        "status: accepted\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Test all mutation families.\n"
        "mutations:\n"
        "  - action: add_invariant\n"
        "    target_invariant_id: INV-900-A\n"
        "    target_policy: POL-900-TEST\n"
        "    expect:\n"
        "      rule: Initial invariant rule.\n"
        "      test_binding: tests/test_inv.py::test_inv_900\n"
        "  - action: modify_component\n"
        "    target_component: CMP-INLINE-WORKER\n"
        "    details: Updated inline worker responsibility for batching.\n"
        "  - action: modify_architecture\n"
        "    details: Documented cross-cutting telemetry architecture.\n"
        "  - action: deprecate_capability\n"
        "    target_capability: CAP-LEGACY-001\n"
        "  - action: remove_policy\n"
        "    target_policy: POL-901-DELETE\n"
        "---\n"
        "= Mutation Families Wave 1\n"
    )
    dlt1.write_text(dlt1_fm, encoding="utf-8")

    exec_ok1, exec_msg1, mutated1 = execute_delta_mutations(eidos_dir, "DLT-2026-910")
    assert exec_ok1 is True, f"Wave 1 execution failed: {exec_msg1}"
    assert len(mutated1) >= 4
    assert not pol_del_path.exists()
    assert cap_path.exists()

    ver_ok1, ver_msg1, ver_rep1 = verify_delta_mutations(eidos_dir, "DLT-2026-910")
    assert ver_ok1 is True, f"Wave 1 verification failed: {ver_msg1}"
    assert ver_rep1 is not None and len(ver_rep1.mutations) == 5

    # 2. Delta exercising patch_invariant, deprecate_policy (retired), and remove_component (inline)
    dlt2 = create_spec(eidos_dir, "delta", "DLT-2026-911", title="Mutation Families Wave 2")
    dlt2_fm = (
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-911\n"
        "title: Mutation Families Wave 2\n"
        "status: accepted\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test patch_invariant, retired policy deprecation, and inline component removal.\n"
        "mutations:\n"
        "  - action: patch_invariant\n"
        "    target_invariant_id: INV-900-A\n"
        "    target_policy: POL-900-TEST\n"
        "    expect:\n"
        "      rule: Patched invariant rule with stricter check.\n"
        "      test_binding: tests/test_inv.py::test_inv_900\n"
        "  - action: deprecate_policy\n"
        "    target_policy: POL-900-TEST\n"
        "  - action: remove_component\n"
        "    target_component: CMP-INLINE-WORKER\n"
        "---\n"
        "= Mutation Families Wave 2\n"
    )
    dlt2.write_text(dlt2_fm, encoding="utf-8")

    exec_ok2, exec_msg2, _ = execute_delta_mutations(eidos_dir, "DLT-2026-911")
    assert exec_ok2 is True, f"Wave 2 execution failed: {exec_msg2}"
    # Also verify that setting status: retired on POL-900-TEST satisfies deprecate_policy verification
    pol_retired = _set_frontmatter_scalar(pol_path.read_text(encoding="utf-8"), "status", "retired")
    pol_path.write_text(pol_retired, encoding="utf-8")
    ver_ok2, ver_msg2, _ = verify_delta_mutations(eidos_dir, "DLT-2026-911")
    assert ver_ok2 is True, f"Wave 2 verification failed: {ver_msg2}"

    # 3. Delta exercising remove_invariant
    dlt3 = create_spec(eidos_dir, "delta", "DLT-2026-912", title="Remove Invariant Wave 3")
    dlt3.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-912\n"
        "title: Remove Invariant Wave 3\n"
        "status: accepted\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Remove obsolete invariant.\n"
        "mutations:\n"
        "  - action: remove_invariant\n"
        "    target_invariant_id: INV-900-A\n"
        "    target_policy: POL-900-TEST\n"
        "---\n"
        "= Remove Invariant Wave 3\n",
        encoding="utf-8",
    )
    exec_ok3, exec_msg3, _ = execute_delta_mutations(eidos_dir, "DLT-2026-912")
    assert exec_ok3 is True, f"Wave 3 execution failed: {exec_msg3}"
    ver_ok3, ver_msg3, _ = verify_delta_mutations(eidos_dir, "DLT-2026-912")
    assert ver_ok3 is True, f"Wave 3 verification failed: {ver_msg3}"
    assert parse_spec_file(pol_path).frontmatter.get("invariants") == []

    # 4. Transactional rollback restores a deleted spec file when a subsequent mutation fails
    pol_restore = create_spec(
        eidos_dir,
        "policy",
        "POL-902-RESTORE",
        title="Restore On Rollback Policy",
        category="security",
        rule="Must survive failed transaction.",
    )
    original_pol_bytes = pol_restore.read_text(encoding="utf-8")
    dlt_fail = create_spec(eidos_dir, "delta", "DLT-2026-913", title="Rollback Deleted File")
    dlt_fail.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-913\n"
        "title: Rollback Deleted File\n"
        "status: accepted\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Verify rollback restores unlinked file.\n"
        "mutations:\n"
        "  - action: remove_policy\n"
        "    target_policy: POL-902-RESTORE\n"
        "  - action: modify_container\n"
        "    target_container: CNT-DOES-NOT-EXIST\n"
        "    details: Trigger failure after deleting POL-902-RESTORE.\n"
        "---\n"
        "= Rollback Deleted File\n",
        encoding="utf-8",
    )
    apply_ok, apply_msg, _, _ = apply_delta(eidos_dir, "DLT-2026-913", execute=True)
    assert apply_ok is False
    assert "cannot execute" in apply_msg.lower() or "not found" in apply_msg.lower()
    assert pol_restore.exists(), "Deleted policy file was NOT restored on transaction rollback!"
    assert pol_restore.read_text(encoding="utf-8") == original_pol_bytes


