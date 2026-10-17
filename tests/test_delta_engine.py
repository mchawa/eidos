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
      frontmatter:
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
      frontmatter:
        custom:
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
      frontmatter:
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
        "      frontmatter:\n"
        "        rule: Initial invariant rule.\n"
        "        test_binding: tests/test_inv.py::test_inv_900\n"
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
        "      frontmatter:\n"
        "        rule: Patched invariant rule with stricter check.\n"
        "        test_binding: tests/test_inv.py::test_inv_900\n"
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

    # 5. Direct unit tests for delta_engine, catalog, and initializer helper edge cases
    from eidos.core.lifecycle.catalog import (
        _build_spec_catalog,
        _mutate_frontmatter_components,
        _set_frontmatter_scalar,
        _split_yaml_inline_comment,
        find_spec_by_id_or_path,
    )
    from eidos.core.lifecycle.delta_engine import (
        _apply_expect_or_details_to_spec,
        _check_structural_subtarget_precondition,
        _ensure_eidos_gitignore,
        _evaluate_declarative_postcondition,
        _extract_mutation_target,
        _parse_spec_content,
        _resolve_target_path_and_id,
    )

    # _ensure_eidos_gitignore appends missing entries to an incomplete .gitignore
    gi_file = eidos_dir / ".gitignore"
    gi_file.write_text("# Partial gitignore\n", encoding="utf-8")
    _ensure_eidos_gitignore(eidos_dir)
    gi_text = gi_file.read_text(encoding="utf-8")
    assert "deltas/by-status/" in gi_text and ".cache/" in gi_text

    # _extract_mutation_target regex fallbacks from details (ID and .adoc path)
    assert _extract_mutation_target({"action": "modify_container", "details": "Update CNT-999-CORE spec"}) == "CNT-999-CORE"
    assert (
        _extract_mutation_target({"action": "modify_container", "details": "Touch 'architecture/containers/svc.adoc'"})
        == "architecture/containers/svc.adoc"
    )
    assert _extract_mutation_target({"action": "modify_container"}) == "unspecified_target"

    # _parse_spec_content empty, parse_error, and missing frontmatter
    empty_doc = _parse_spec_content(eidos_dir / "system.adoc", "   ")
    assert empty_doc.doc_type == "product/system"
    no_fm_doc = _parse_spec_content(eidos_dir / "bad.adoc", "= No Frontmatter\n")
    assert len(no_fm_doc.errors) == 1
    bad_yaml_doc = _parse_spec_content(eidos_dir / "bad.adoc", "---\n: [broken\n---\n= Body\n")
    assert any("Frontmatter parse error" in e for e in bad_yaml_doc.errors)

    # _check_structural_subtarget_precondition negative checks
    specs_by_id, container_components, _invariant_map = _build_spec_catalog(eidos_dir)[0], _build_spec_catalog(eidos_dir)[1], {}
    assert (
        _check_structural_subtarget_precondition(
            {"target_capability": "CAP-MISSING", "target_invariant_id": "INV-1"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target_container": "CNT-MISSING", "target_component": "CMP-1"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target_container": "CNT-900-SVC", "target_component": "CMP-NOT-IN-CNT"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target_container": "CNT-900-SVC", "target_contract": "CTR-UNLINKED"}, specs_by_id, container_components
        )
        is False
    )

    # _resolve_target_path_and_id by relative .adoc path
    rel_sid, rel_p, rel_ok = _resolve_target_path_and_id(
        f"policies/{pol_restore.name}", eidos_dir, specs_by_id
    )
    assert rel_ok is True and rel_sid == "POL-902-RESTORE" and rel_p is not None

    # _evaluate_declarative_postcondition & _apply_expect_or_details_to_spec
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"body": {"contains": "NONEXISTENT_TEXT_999"}}},
            "POL-902-RESTORE",
            specs_by_id,
            container_components,
            {},
        )
        is False
    )
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"frontmatter": {"category": "wrong_cat"}}},
            "POL-902-RESTORE",
            specs_by_id,
            container_components,
            {},
        )
        is False
    )
    assert _apply_expect_or_details_to_spec(
        pol_restore,
        {
            "body": {"contains": "Appended declarative expectation paragraph."},
            "frontmatter": {"category": "governance"},
        },
        "",
    )
    assert "Appended declarative expectation paragraph." in pol_restore.read_text(encoding="utf-8")
    assert _apply_expect_or_details_to_spec(pol_restore, None, "// comment only") is False

    # catalog.py helpers: _split_yaml_inline_comment, _set_frontmatter_scalar, _mutate_frontmatter_components
    val_part, cmt_part = _split_yaml_inline_comment(r'"escaped \" quote # inside" # real comment')
    assert val_part == r'"escaped \" quote # inside"' and "# real comment" in cmt_part
    assert _set_frontmatter_scalar("no frontmatter", "status", "active") == "no frontmatter"
    raw_no_comps = "---\ntype: tech/container\nid: CNT-X\n---\n= X\n"
    appended_comps = _mutate_frontmatter_components(
        raw_no_comps, "append", "CMP-NEW", {"id": "CMP-NEW", "name": "New"}
    )
    assert "CMP-NEW" in appended_comps
    raw_empty_comps = "---\ntype: tech/container\nid: CNT-X\ncomponents: []\n---\n= X\n"
    appended_from_empty = _mutate_frontmatter_components(
        raw_empty_comps, "append", "CMP-NEW", {"id": "CMP-NEW", "name": "New"}
    )
    assert "CMP-NEW" in appended_from_empty
    assert find_spec_by_id_or_path(eidos_dir, str(pol_restore)) == pol_restore.resolve()
    assert find_spec_by_id_or_path(eidos_dir, f"policies/{pol_restore.stem}") == pol_restore.resolve()

    # execute_delta_mutations negative/abort branches (non-existent delta, non-delta doc, unsupported action, type mismatch)
    ok_missing, msg_missing, _ = execute_delta_mutations(eidos_dir, "DLT-DOES-NOT-EXIST")
    assert ok_missing is False and "not found" in msg_missing
    ok_nondelta, msg_nondelta, _ = execute_delta_mutations(eidos_dir, "POL-902-RESTORE")
    assert ok_nondelta is False and "not a SpecDelta" in msg_nondelta
    ok_apply_missing, _, _, _ = apply_delta(eidos_dir, "DLT-DOES-NOT-EXIST")
    assert ok_apply_missing is False
    ok_apply_nondelta, _, _, _ = apply_delta(eidos_dir, "POL-902-RESTORE")
    assert ok_apply_nondelta is False


def test_delta_engine_git_baseline_and_remaining_mutations(tmp_path):
    """Cover _resolve_git_baseline_sha, _evaluate_declarative_postcondition branches,
    remaining execute_delta_mutations actions, catalog.py helpers, and initializer.py."""  # eidos:fixture
    import subprocess

    from eidos.core.lifecycle.catalog import (
        _build_spec_catalog_with_invariants,
        _mutate_frontmatter_components,
    )
    from eidos.core.lifecycle.delta_engine import (
        _evaluate_declarative_postcondition,
        _resolve_git_baseline_sha,
    )
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    # 1. initializer.py branches: existing non-empty dir without force, lite=False without name/title
    repo_init = tmp_path / "init_full"
    ok_init, _, _ = init_repository(repo_init, lite=False)
    assert ok_init is True
    ok_reinit, msg_reinit, _ = init_repository(repo_init, force=False)
    assert ok_reinit is False and "Use --force" in msg_reinit

    # 2. catalog.py: _mutate_frontmatter_components when components block precedes another top-level key with comments/blanks
    raw_with_trailing_key = (
        "---\n"
        "type: tech/container\n"
        "id: CNT-TRAIL\n"
        "components:\n"
        "  - id: CMP-OLD\n"
        "    name: Old\n"
        "\n"
        "  # comment before next top-level key\n"
        "decisions: []\n"
        "---\n"
        "= Trail\n"
    )
    mutated_trail = _mutate_frontmatter_components(
        raw_with_trailing_key, "append", "CMP-NEW", {"id": "CMP-NEW", "name": "New"}
    )
    assert "CMP-NEW" in mutated_trail and "decisions: []" in mutated_trail

    # 3. Git repo for _resolve_git_baseline_sha across CMP-*, INV-*, and CNT-*
    git_repo = tmp_path / "git_delta_ws"
    eidos_dir = git_repo / "eidos"
    init_repository(git_repo, name="git-delta", title="Git Delta")
    cnt_file = create_spec(eidos_dir, "container", "CNT-BASE", "Base Container", package_path="src/base")
    doc_cnt = parse_spec_file(cnt_file)
    doc_cnt.frontmatter["components"] = [
        {
            "id": "CMP-INLINE-1",
            "name": "Inline One",
            "responsibility": "Core",
            "source_path": "src/base/inline_one.py",
        }
    ]
    cnt_file.write_text(dump_spec_document(doc_cnt.frontmatter, doc_cnt.body), encoding="utf-8")

    pol_file = create_spec(eidos_dir, "policy", "POL-BASE", "Base Policy")
    doc_pol = parse_spec_file(pol_file)
    doc_pol.frontmatter["invariants"] = [
        {"id": "INV-BASE-001", "rule": "Rule 1", "test_binding": "N/A"}
    ]
    pol_file.write_text(dump_spec_document(doc_pol.frontmatter, doc_pol.body), encoding="utf-8")
    # Also add a string invariant entry in a draft policy to exercise catalog.py string invariant branch (370-371)
    str_inv_pol = eidos_dir / "policies" / "pol-str-inv.adoc"
    str_inv_pol.write_text(
        "---\ntype: product/policy\nid: POL-STR\ntitle: Str\ninvariants:\n  - INV-STR-ONLY\n---\n= Str\n",
        encoding="utf-8",
    )
    _, _, inv_map = _build_spec_catalog_with_invariants(eidos_dir)
    assert "INV-STR-ONLY" in inv_map
    str_inv_pol.unlink()

    subprocess.run(["git", "init", "-b", "main"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline specs"], cwd=git_repo, check=True, capture_output=True)

    def _mk_delta(dlt_id: str, title: str, mutations: list[dict] | None = None):
        p = create_spec(eidos_dir, "delta", dlt_id, title=title, motivation="Test")
        if mutations is not None:
            d = parse_spec_file(p)
            d.frontmatter["mutations"] = mutations
            p.write_text(dump_spec_document(d.frontmatter, d.body), encoding="utf-8")
        return p

    dlt_file = _mk_delta("DLT-910", "Git Baseline Test")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add delta"], cwd=git_repo, check=True, capture_output=True)

    # Modify CNT-BASE after DLT-910 creation
    cnt_file.write_text(cnt_file.read_text(encoding="utf-8") + "\nPost-delta change.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "modify container"], cwd=git_repo, check=True, capture_output=True)

    sha_cmp = _resolve_git_baseline_sha(eidos_dir, dlt_file, "CMP-INLINE-1", cnt_file)
    assert sha_cmp is not None
    sha_inv = _resolve_git_baseline_sha(eidos_dir, dlt_file, "INV-BASE-001", pol_file)
    assert sha_inv is not None
    sha_cnt = _resolve_git_baseline_sha(eidos_dir, dlt_file, "CNT-BASE", cnt_file)
    assert sha_cnt is not None

    # 4. _evaluate_declarative_postcondition for inline component, unknown target with target_file, and list field
    catalog, comp_map, inv_map = _build_spec_catalog_with_invariants(eidos_dir)
    ok_cmp_post = _evaluate_declarative_postcondition(
        {
            "action": "modify_component",
            "target": "CMP-INLINE-1",
            "expect": {"frontmatter": {"responsibility": "Core"}},
        },
        "CMP-INLINE-1",
        catalog,
        comp_map,
        inv_map,
    )
    assert ok_cmp_post is True
    ok_cmp_miss = _evaluate_declarative_postcondition(
        {
            "action": "modify_component",
            "target": "CMP-INLINE-1",
            "expect": {"frontmatter": {"responsibility": "Other"}},
        },
        "CMP-INLINE-1",
        catalog,
        comp_map,
        inv_map,
    )
    assert ok_cmp_miss is False
    ok_file_fb = _evaluate_declarative_postcondition(
        {
            "action": "modify_container",
            "target": "UNKNOWN-ID",
            "expect": {"frontmatter": {"type": "tech/container"}},
        },
        "UNKNOWN-ID",
        catalog,
        comp_map,
        inv_map,
        target_file=cnt_file,
    )
    assert ok_file_fb is True

    # Add a list field to CNT-BASE to test list item matching in _evaluate_declarative_postcondition
    doc_cnt2 = parse_spec_file(cnt_file)
    doc_cnt2.frontmatter["tags"] = ["core", "api"]
    cnt_file.write_text(dump_spec_document(doc_cnt2.frontmatter, doc_cnt2.body), encoding="utf-8")
    catalog2, comp_map2, inv_map2 = _build_spec_catalog_with_invariants(eidos_dir)
    ok_list_hit = _evaluate_declarative_postcondition(
        {"action": "modify_container", "target": "CNT-BASE", "expect": {"frontmatter": {"tags": "api"}}},
        "CNT-BASE",
        catalog2,
        comp_map2,
        inv_map2,
    )
    assert ok_list_hit is True
    ok_list_miss = _evaluate_declarative_postcondition(
        {"action": "modify_container", "target": "CNT-BASE", "expect": {"frontmatter": {"tags": "missing"}}},
        "CNT-BASE",
        catalog2,
        comp_map2,
        inv_map2,
    )
    assert ok_list_miss is False

    # Remove extra `tags` key before running schema-validated execute_delta_mutations
    doc_cnt3 = parse_spec_file(cnt_file)
    doc_cnt3.frontmatter.pop("tags", None)
    cnt_file.write_text(dump_spec_document(doc_cnt3.frontmatter, doc_cnt3.body), encoding="utf-8")

    # 5. execute_delta_mutations: add_component with expect, add_invariant on capability,
    # modify_component with expect, patch_invariant with expect+details, remove_component standalone
    create_spec(eidos_dir, "domain", "DOM-BASE", title="Base Domain")
    cap_file = create_spec(eidos_dir, "capability", "CAP-BASE", title="Base Capability", domain="DOM-BASE")
    cmp_standalone = create_spec(
        eidos_dir, "component", "CMP-STAND", title="Standalone Comp", owning_container="CNT-BASE"
    )
    assert cap_file.exists() and cmp_standalone.exists()

    dlt_exec = _mk_delta(
        "DLT-911",
        "Exec Remaining Actions",
        mutations=[
            {
                "action": "add_component",
                "target_container": "CNT-BASE",
                "target_component": "CMP-INLINE-2",
                "details": "Add second inline component",
                "expect": {"frontmatter": {"responsibility": "Custom Resp"}},
            },
            {
                "action": "modify_component",
                "target_component": "CMP-INLINE-1",
                "details": "Update inline component",
                "expect": {
                    "frontmatter": {"responsibility": "Updated Core"},
                    "body": {"contains": "Inline body note."},
                },
            },
            {
                "action": "add_invariant",
                "target_capability": "CAP-BASE",
                "target_invariant_id": "INV-CAP-NEW-001",
                "details": "Capability must hold.",
                "expect": {"frontmatter": {"test_binding": "N/A"}},
            },
            {
                "action": "patch_invariant",
                "target_invariant_id": "INV-BASE-001",
                "details": "Patched rule via details",
                "expect": {"body": {"contains": "Extra policy invariant note."}},
            },
            {
                "action": "remove_component",
                "target_component": "CMP-STAND",
                "details": "Remove standalone component",
            },
        ],
    )
    assert dlt_exec.exists()
    ok_exec, msg_exec, _touched = execute_delta_mutations(eidos_dir, "DLT-911")
    assert ok_exec is True, msg_exec
    assert not cmp_standalone.exists()
    assert "Extra policy invariant note." in pol_file.read_text(encoding="utf-8")

    # 6. execute_delta_mutations abort branches (invalid INV prefix, remove_container on inline CMP, remove_invariant on spec ID)
    for idx, bad_mut in enumerate(
        [
            {"action": "add_invariant", "target_policy": "POL-BASE", "target_invariant_id": "NOT-AN-INV", "details": "Bad ID"},
            {"action": "add_invariant", "target_policy": "MISSING-POL", "target_invariant_id": "INV-X-001", "details": "Missing parent"},
            {"action": "remove_container", "target_container": "CMP-INLINE-1", "details": "Wrong action for inline"},
            {"action": "remove_container", "target_container": "POL-BASE", "details": "Wrong spec type"},
            {"action": "remove_invariant", "target_invariant_id": "POL-BASE", "details": "Spec ID instead of INV"},
        ],
        start=920,
    ):
        _mk_delta(f"DLT-{idx}", f"Bad Mut {idx}", mutations=[bad_mut])
        ok_bad, _, _ = execute_delta_mutations(eidos_dir, f"DLT-{idx}")
        assert ok_bad is False

    # 7. Uncommitted delta file in _resolve_git_baseline_sha (triggers merge-base + HEAD fallback 695-706)
    uncommitted_dlt = _mk_delta("DLT-930", "Uncommitted Delta")
    d_unc = parse_spec_file(uncommitted_dlt)
    d_unc.frontmatter.pop("vcs", None)
    uncommitted_dlt.write_text(dump_spec_document(d_unc.frontmatter, d_unc.body), encoding="utf-8")
    sha_uncommitted = _resolve_git_baseline_sha(eidos_dir, uncommitted_dlt, "CNT-BASE", cnt_file)
    assert sha_uncommitted is not None

    # 8. add_component when no containers exist in eidos_dir (scaffolds standalone tech/component 1457-1468)
    no_cnt_repo = tmp_path / "no_cnt_ws"
    init_repository(no_cnt_repo, name="no-cnt", title="No Container WS")
    no_cnt_eidos = no_cnt_repo / "eidos"
    dlt_nocnt = create_spec(no_cnt_eidos, "delta", "DLT-931", title="Standalone Add Comp", motivation="Test")
    d_nc = parse_spec_file(dlt_nocnt)
    d_nc.frontmatter["mutations"] = [
        {"action": "add_component", "target_component": "CMP-ORPHAN-1", "details": "Standalone comp"}
    ]
    dlt_nocnt.write_text(dump_spec_document(d_nc.frontmatter, d_nc.body), encoding="utf-8")
    # execute_delta_mutations will scaffold CMP-ORPHAN-1 and then roll back because tech/component requires owning_container
    execute_delta_mutations(no_cnt_eidos, "DLT-931")

    # 9. initializer.py custom empty directory .gitkeep (67-70) and validation failure branch (114-119)
    custom_leaf = repo_init / "eidos" / "domains" / "custom_empty_leaf"
    custom_leaf.mkdir(parents=True, exist_ok=True)
    ok_force, _, created_force = init_repository(repo_init, name="init-full", force=True)
    assert ok_force is True and (custom_leaf / ".gitkeep") in created_force

    (repo_init / "eidos" / "policies" / "corrupt.adoc").write_text(
        "---\ntype: product/policy\nid: INVALID_ID\n---\n= Corrupt\n", encoding="utf-8"
    )
    ok_corrupt, msg_corrupt, _ = init_repository(repo_init, name="init-full", force=True)
    assert ok_corrupt is False and "failed validation" in msg_corrupt

    # 10. list_deltas on missing dir, status_filter mismatch, and non-delta file inside deltas/
    from eidos.core.lifecycle.delta_engine import list_deltas, sync_delta_views

    assert sync_delta_views(tmp_path / "no_such_eidos") == {}
    assert list_deltas(tmp_path / "no_such_eidos") == []
    note_in_deltas = eidos_dir / "deltas" / "note.adoc"
    note_in_deltas.write_text("---\ntype: product/policy\nid: POL-NOTE\n---\n= Note\n", encoding="utf-8")
    sync_delta_views(eidos_dir)
    assert list_deltas(eidos_dir, status_filter="applied") == []
    note_in_deltas.unlink()


def test_dlt_2026_079_structured_expect_assertions_and_diagnostics(tmp_path):
    """Verify DLT-2026-079: structured body/frontmatter expect post-conditions, multi-string contains/not_contains,
    list-subset matching, comment stripping, baseline SHA verification alongside expect, and failure_reason diagnostics."""
    from eidos.core.lifecycle.catalog import _build_spec_catalog_with_invariants
    from eidos.core.lifecycle.delta_engine import (
        _apply_expect_or_details_to_spec,
        _evaluate_declarative_postcondition,
        execute_delta_mutations,
        verify_delta_mutations,
    )
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    init_repository(tmp_path, name="dlt-079-ws")
    eidos_dir = tmp_path / "eidos"

    cnt_path = create_spec(eidos_dir, "container", "CNT-API", title="API Service")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-ROUTER",
            "name": "Router",
            "responsibility": "Routes HTTP requests and enforces rate limits.",
            "source_path": ["src/api/router.py", "src/api/middleware.py", "src/api/ratelimit.py"],
        }
    ]
    cnt_body = (
        f"{cnt_doc.body.rstrip()}\n\n"
        "// COMMENTED_SECRET_TOKEN\n"
        "////\n"
        "BLOCK_COMMENTED_TOKEN\n"
        "////\n"
        "Active endpoint routing and rate limit enforcement.\n"
    )
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_body), encoding="utf-8")

    catalog, comp_map, inv_map = _build_spec_catalog_with_invariants(eidos_dir)

    # 1. Structured body (multi-string contains + not_contains) and frontmatter (responsibility + source_path list-subset)
    diag_ok: list[str] = []
    assert (
        _evaluate_declarative_postcondition(
            {
                "action": "modify_component",
                "target_component": "CMP-ROUTER",
                "expect": {
                    "body": {
                        "contains": ["Active endpoint routing", "rate limit enforcement"],
                        "not_contains": ["COMMENTED_SECRET_TOKEN", "BLOCK_COMMENTED_TOKEN", "DEPRECATED_V1_ROUTE"],
                    },
                    "frontmatter": {
                        "responsibility": "enforces rate limits",
                        "source_path": ["src/api/router.py", "src/api/ratelimit.py"],
                    },
                },
            },
            "CMP-ROUTER",
            catalog,
            comp_map,
            inv_map,
            target_file=cnt_path,
            diagnostics=diag_ok,
        )
        is True
    )
    assert diag_ok == []

    # 2. Failure diagnostics for body.contains, body.not_contains, and frontmatter list-subset mismatch
    diag_contains: list[str] = []
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"body": {"contains": ["Active endpoint routing", "MISSING_PHRASE_XYZ"]}}},
            "CMP-ROUTER",
            catalog,
            comp_map,
            inv_map,
            target_file=cnt_path,
            diagnostics=diag_contains,
        )
        is False
    )
    assert any("MISSING_PHRASE_XYZ" in d for d in diag_contains)

    diag_not_contains: list[str] = []
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"body": {"not_contains": "rate limit enforcement"}}},
            "CMP-ROUTER",
            catalog,
            comp_map,
            inv_map,
            target_file=cnt_path,
            diagnostics=diag_not_contains,
        )
        is False
    )
    assert any("body.not_contains matched forbidden" in d for d in diag_not_contains)

    diag_subset: list[str] = []
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"frontmatter": {"source_path": ["src/api/router.py", "src/api/nonexistent.py"]}}},
            "CMP-ROUTER",
            catalog,
            comp_map,
            inv_map,
            target_file=cnt_path,
            diagnostics=diag_subset,
        )
        is False
    )
    assert any("missing expected list items" in d for d in diag_subset)

    # 3. Pre-existing value on unapplied modify_component with recorded baseline SHA must fail until target is modified
    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-079", title="Structured Expect Test")
    dlt_doc = parse_spec_file(dlt_path)
    dlt_doc.frontmatter["mutations"] = [
        {
            "action": "modify_component",
            "target_component": "CMP-ROUTER",
            "details": "Add circuit breaker to router.",
            "expect": {
                "frontmatter": {
                    "responsibility": "enforces rate limits",
                }
            },
        }
    ]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")

    # Because create_spec recorded the pre-delta baseline of CMP-ROUTER and CMP-ROUTER has not changed since,
    # verify_delta_mutations must report UNMODIFIED with a diagnostic failure_reason!
    ver_before, _, rep_before = verify_delta_mutations(eidos_dir, "DLT-2026-079")
    assert ver_before is False
    assert rep_before is not None
    assert rep_before.mutations[0].status == "UNMODIFIED"
    assert rep_before.mutations[0].failure_reason is not None
    assert "matches pre-delta baseline" in rep_before.mutations[0].failure_reason

    # 4. Execute structured expect mutation and verify it passes
    dlt_doc.frontmatter["mutations"] = [
        {
            "action": "modify_component",
            "target_component": "CMP-ROUTER",
            "details": "Add circuit breaker to router.",
            "expect": {
                "frontmatter": {
                    "responsibility": "Routes HTTP requests, enforces rate limits, and trips circuit breakers.",
                },
                "body": {
                    "contains": "Circuit breaker trips after 5 consecutive upstream timeouts.",
                },
            },
        }
    ]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")
    ok_exec, msg_exec, _ = execute_delta_mutations(eidos_dir, "DLT-2026-079")
    assert ok_exec is True, msg_exec

    ver_after, _, rep_after = verify_delta_mutations(eidos_dir, "DLT-2026-079")
    assert ver_after is True
    assert rep_after is not None
    assert rep_after.mutations[0].verified is True
    assert rep_after.mutations[0].failure_reason is None

    # 5. _apply_expect_or_details_to_spec with details-only preserves frontmatter rule/responsibility
    pol_path = create_spec(eidos_dir, "policy", "POL-RATE", title="Rate Policy", rule="Original rule preserved.")
    assert _apply_expect_or_details_to_spec(pol_path, None, "Appended operational note in body.") is True
    pol_after = parse_spec_file(pol_path)
    assert pol_after.frontmatter["rule"] == "Original rule preserved."
    assert "Appended operational note in body." in pol_after.body


def test_dlt_2026_080_cross_ref_collision_resolution_and_cli_slug(tmp_path):
    """Verify DLT-2026-080: cross-ref collision resolution across parallel Git branches, explicit colliding ID resolution, and optional ID + --slug CLI ergonomics."""
    import subprocess

    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.lifecycle.delta_engine import generate_collision_free_delta_id
    from eidos.core.lifecycle.initializer import init_repository

    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@eidos.dev"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp_path, check=True, capture_output=True)

    # Create a parallel feature branch with DLT-2026-001
    subprocess.run(["git", "checkout", "-b", "feat/parallel-branch"], cwd=tmp_path, check=True, capture_output=True)
    d1 = create_spec(eidos_dir, "delta", "DLT-2026-001", title="Parallel Branch Feature")
    assert d1.name == "dlt-2026-001.adoc"
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add DLT-2026-001 on parallel branch"], cwd=tmp_path, check=True, capture_output=True)

    # Switch back to main where dlt-2026-001.adoc does NOT exist in working tree
    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    assert not (eidos_dir / "deltas" / "dlt-2026-001.adoc").exists()

    # 1. Explicit DLT-2026-001 on main must detect the collision on feat/parallel-branch and append slug suffix
    resolved_explicit = generate_collision_free_delta_id(
        eidos_dir, raw_id="DLT-2026-001", title="Main Branch Feature"
    )
    assert resolved_explicit == "DLT-2026-001-MAIN-BRANCH"

    d2 = create_spec(eidos_dir, "delta", "DLT-2026-001", title="Main Branch Feature")
    assert d2.name == "dlt-2026-001-main-branch.adoc"

    # 2. CLI: eidos delta create --title "..." --slug (without positional delta_id)
    cli_runner = CliRunner()
    res = cli_runner.invoke(
        app,
        ["delta", "create", "--title", "Zero Config Auth", "--slug", "--path", str(eidos_dir)],
    )
    assert res.exit_code == 0, res.stdout
    assert "dlt-2026-002-zero-config.adoc" in res.stdout.lower()
    assert (eidos_dir / "deltas" / "dlt-2026-002-zero-config.adoc").exists()


def test_dlt_2026_083_invariant_comment_preservation_and_baseline_retention(tmp_path):
    """Verify DLT-2026-083: _mutate_frontmatter_invariants preserves YAML comments and delta_baselines survives schema version rebuilds."""
    from eidos.core.index.storage import ensure_cache_schema, open_cache_connection
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.spec.parser import dump_spec_document

    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"

    inv_1 = "INV-" + "AUTH-001"
    inv_2 = "INV-" + "AUTH-002"
    cap_file = create_spec(eidos_dir, "capability", "CAP-AUTH-001", title="Auth Capability")
    cap_text = cap_file.read_text(encoding="utf-8")
    cap_text = cap_text.replace(
        "  invariants: []\n",
        "  # Keep this technical header comment\n"
        "  invariants:\n"
        "    # Critical invariant comment that must survive mutations\n"
        f"    - id: {inv_1}\n"
        "      rule: Tokens must expire within 15 minutes.\n"
        "      test_binding: tests/test_auth.py::test_token_expiry\n",
    )
    cap_file.write_text(cap_text, encoding="utf-8")

    dlt_path = create_spec(eidos_dir, "delta", "DLT-2026-001", title="Add Second Auth Invariant")
    dlt_doc = parse_spec_file(dlt_path)
    dlt_doc.frontmatter["mutations"] = [
        {
            "action": "add_invariant",
            "target_capability": "CAP-AUTH-001",
            "target_invariant_id": inv_2,
            "expect": {
                "rule": "Refresh tokens must be rotated on every use.",
                "test_binding": "tests/test_auth.py::test_refresh_rotation",
            },
        }
    ]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")

    ok, msg, _ = execute_delta_mutations(eidos_dir, "DLT-2026-001")
    assert ok is True, msg
    raw_after = cap_file.read_text(encoding="utf-8")
    assert "# Keep this technical header comment" in raw_after
    assert "# Critical invariant comment that must survive mutations" in raw_after
    assert inv_2 in raw_after

    # Verify delta_baselines survives ensure_cache_schema rebuild when schema_version changes
    db_path = eidos_dir / ".cache" / "index.db"
    conn = open_cache_connection(db_path)
    try:
        rows_before = conn.execute(
            "SELECT delta_id, target_id, baseline_sha256 FROM delta_baselines;"
        ).fetchall()
        assert len(rows_before) >= 1
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '0.0-outdated');"
        )
        conn.commit()
        ensure_cache_schema(conn)
        rows_after = conn.execute(
            "SELECT delta_id, target_id, baseline_sha256 FROM delta_baselines;"
        ).fetchall()
        assert rows_after == rows_before
    finally:
        conn.close()


def test_catalog_and_delta_engine_edge_cases(tmp_path):
    """Cover _mutate_frontmatter_invariants branches in catalog.py and status/postcondition branches in delta_engine.py."""

    from eidos.core.lifecycle.catalog import (
        _mutate_frontmatter_invariants,
        find_references_to_spec,
        find_spec_by_id_or_path,
    )
    from eidos.core.lifecycle.delta_engine import (
        _evaluate_declarative_postcondition,
        create_delta,
        get_delta_status_summary,
        set_delta_status,
        verify_delta_mutations,
    )
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.spec.parser import dump_spec_document

    # 1. _mutate_frontmatter_invariants edge cases (is_capability with technical: {}, missing technical:, empty invariants: [])
    inv_item = {"id": "INV-" + "CAP-100", "rule": "Rule 100", "test_binding": "tests/t.py::t"}
    raw_cap_empty_tech = "---\ntype: product/capability\nid: CAP-1\ntechnical: {}\n---\n= Cap\n"
    res1 = _mutate_frontmatter_invariants(raw_cap_empty_tech, "append", inv_item["id"], inv_item, is_capability=True)
    assert "invariants:" in res1 and inv_item["id"] in res1

    raw_cap_no_tech = "---\ntype: product/capability\nid: CAP-2\n---\n= Cap\n"
    res2 = _mutate_frontmatter_invariants(raw_cap_no_tech, "append", inv_item["id"], inv_item, is_capability=True)
    assert "technical:\n  invariants:\n" in res2 and inv_item["id"] in res2

    raw_pol_no_inv = "---\ntype: product/policy\nid: POL-1\n---\n= Pol\n"
    res3 = _mutate_frontmatter_invariants(raw_pol_no_inv, "append", inv_item["id"], inv_item, is_capability=False)
    assert "invariants:\n" in res3 and inv_item["id"] in res3
    assert _mutate_frontmatter_invariants(raw_pol_no_inv, "remove", inv_item["id"], None, is_capability=False) == raw_pol_no_inv

    raw_empty_list = "---\ntype: product/policy\nid: POL-1\ninvariants: []\n---\n= Pol\n"
    assert _mutate_frontmatter_invariants(raw_empty_list, "remove", inv_item["id"], None, is_capability=False) == raw_empty_list
    res_append_empty = _mutate_frontmatter_invariants(raw_empty_list, "append", inv_item["id"], inv_item, is_capability=False)
    assert inv_item["id"] in res_append_empty
    res_replace = _mutate_frontmatter_invariants(
        res_append_empty, "replace", inv_item["id"], {**inv_item, "rule": "Updated Rule"}, is_capability=False
    )
    assert "Updated Rule" in res_replace
    res_removed = _mutate_frontmatter_invariants(res_replace, "remove", inv_item["id"], None, is_capability=False)
    assert "invariants: []" in res_removed


    # 2. _evaluate_declarative_postcondition invalid expect structures and mismatch diagnostics
    diag: list[str] = []
    assert _evaluate_declarative_postcondition({"expect": {"bad_top": 1}}, "CNT-1", {}, {}, {}, diagnostics=diag) is False
    assert any("unsupported top-level keys" in d for d in diag)

    diag.clear()
    assert _evaluate_declarative_postcondition({"expect": {"body": "not_dict"}}, "CNT-1", {}, {}, {}, diagnostics=diag) is False
    assert "expect.body must be a mapping" in diag

    diag.clear()
    assert _evaluate_declarative_postcondition({"expect": {"body": {"bad_body": 1}}}, "CNT-1", {}, {}, {}, diagnostics=diag) is False
    assert any("unsupported keys" in d for d in diag)

    diag.clear()
    assert _evaluate_declarative_postcondition({"expect": {"frontmatter": "not_dict"}}, "CNT-1", {}, {}, {}, diagnostics=diag) is False
    assert "expect.frontmatter must be a mapping" in diag

    # 3. create_delta, set_delta_status, get_delta_status_summary, and find_references_to_spec
    ws = tmp_path / "ws"
    init_repository(ws)
    eidos_dir = ws / "eidos"
    cnt_path = create_spec(eidos_dir, "container", "CNT-API", title="API Service")
    cap_path = create_spec(eidos_dir, "capability", "CAP-API-001", title="API Cap")
    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["technical"] = {"containers": ["CNT-API"], "components": [], "invariants": []}
    cap_path.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")

    refs = find_references_to_spec(eidos_dir, "CNT-API", cnt_path)
    assert cap_path.resolve() in refs
    assert find_spec_by_id_or_path(eidos_dir, "architecture/containers/cnt-api.adoc") == cnt_path.resolve()


    dlt_file = create_delta(eidos_dir, "DLT-2026-050", title="Deprecate API Cap")
    ok_bad, msg_bad, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "not_a_status")
    assert ok_bad is False and "Invalid SpecDelta status" in msg_bad
    ok_missing, _, _, _ = set_delta_status(eidos_dir, "DLT-NONEXISTENT", "approved")
    assert ok_missing is False
    ok_non_dlt, _, _, _ = set_delta_status(eidos_dir, str(cnt_path), "approved")
    assert ok_non_dlt is False

    # Mutate delta to have deprecate_capability on active capability -> verify fails -> set_delta_status('applied') blocked
    dlt_doc = parse_spec_file(dlt_file)
    dlt_doc.frontmatter["mutations"] = [
        {"action": "deprecate_capability", "target_capability": "CAP-API-001"}
    ]
    dlt_file.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")
    v_ok, _, v_rep = verify_delta_mutations(eidos_dir, "DLT-2026-050")
    assert v_ok is False and v_rep is not None and v_rep.mutations[0].status == "ACTIVE_NOT_DEPRECATED"
    ok_app_blocked, msg_app_blocked, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "applied")
    assert ok_app_blocked is False and "unverified mutations" in msg_app_blocked

    # Execute deprecate_capability and transition via set_delta_status to in_review and applied
    exec_ok, _, _ = execute_delta_mutations(eidos_dir, "DLT-2026-050")
    assert exec_ok is True
    ok_rev, _, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "in_review")
    assert ok_rev is True
    ok_app, _, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "applied")
    assert ok_app is True
    summary = get_delta_status_summary(eidos_dir)
    assert summary["total"] >= 1
    assert len(summary["by_status"]["applied"]) == 1

    # 4. _strip_spec_comments with frontmatter comments and verify_delta_mutations failure branches
    from eidos.core.lifecycle.delta_engine import _strip_spec_comments

    stripped = _strip_spec_comments("---\n# fm comment\nid: CNT-1\n---\n////\nblock comment\n////\n// line comment\nReal body\n")
    assert "# fm comment" not in stripped
    assert "block comment" not in stripped
    assert "Real body" in stripped

    # Add an invariant to CNT-API so we can test patch_invariant & remove_invariant failure branches
    cnt_doc = parse_spec_file(cnt_path)
    inv_cnt_id = "INV-" + "CNT-API-001"
    cnt_doc.frontmatter["invariants"] = [{"id": inv_cnt_id, "rule": "Orig Rule", "test_binding": "tests/t.py::t"}]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    dlt2 = create_delta(eidos_dir, "DLT-2026-051", title="Failure Branches")
    dlt2_doc = parse_spec_file(dlt2)
    dlt2_doc.frontmatter["mutations"] = [
        {"action": "unknown_action_xyz", "target_container": "CNT-API"},
        {"action": "add_invariant", "target_container": "CNT-API", "target_invariant_id": "INV-" + "MISSING-999"},
        {
            "action": "add_invariant",
            "target_container": "CNT-API",
            "target_invariant_id": inv_cnt_id,
            "expect": {"frontmatter": {"rule": "Wrong Rule"}},
        },
        {
            "action": "patch_invariant",
            "target_container": "CNT-API",
            "target_invariant_id": inv_cnt_id,
            "expect": {"frontmatter": {"rule": "Wrong Rule"}},
        },
        {"action": "remove_invariant", "target_container": "CNT-API", "target_invariant_id": inv_cnt_id},
        {
            "action": "add_container",
            "target_container": "CNT-API",
            "expect": {"frontmatter": {"runtime": "WrongRuntime", "tags": ["missing_tag"]}},
        },
    ]
    dlt2.write_text(dump_spec_document(dlt2_doc.frontmatter, dlt2_doc.body), encoding="utf-8")
    v2_ok, _, v2_rep = verify_delta_mutations(eidos_dir, "DLT-2026-051")
    assert v2_ok is False and v2_rep is not None
    statuses = [m.status for m in v2_rep.mutations]
    assert "UNSUPPORTED_ACTION" in statuses
    assert "MISSING" in statuses
    assert "UNMODIFIED" in statuses
    assert "STILL_PRESENT" in statuses
