"""Tests verifying the Deterministic Composable SpecDelta Engine (eidos delta verify & apply)."""

import os
import shutil
import subprocess
from pathlib import Path

import orjson
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.storage import ensure_cache_schema, open_cache_connection
from eidos.core.lifecycle.catalog import (
    _build_spec_catalog,
    _build_spec_catalog_with_invariants,
    _mutate_frontmatter_components,
    _set_frontmatter_scalar,
    _split_yaml_inline_comment,
    find_references_to_spec,
    find_spec_by_id_or_path,
)
from eidos.core.lifecycle.changelog import ChangelogGenerator
from eidos.core.lifecycle.delta_engine import (
    SUPPORTED_MUTATION_OPERATIONS,
    _check_structural_subtarget_precondition,
    _evaluate_declarative_postcondition,
    _extract_mutation_target,
    _resolve_git_baseline_sha,
    _resolve_target_path_and_id,
    _strip_spec_comments,
    apply_delta,
    archive_deltas,
    create_delta,
    generate_collision_free_delta_id,
    get_delta_status_summary,
    list_deltas,
    normalize_delta_mutation,
    set_delta_status,
    verify_delta_mutations,
)
from eidos.core.lifecycle.initializer import _ensure_eidos_gitignore, init_repository
from eidos.core.lifecycle.scaffolder import create_spec, list_specs
from eidos.core.spec.models import resolve_entity_prefix_meta
from eidos.core.spec.parser import _parse_spec_content, dump_spec_document, parse_spec_file
from eidos.core.workspace.assets import get_assets_path

runner = CliRunner()


def _commit_git_baseline(repo_dir: Path) -> None:
    if not (repo_dir / ".git").exists():
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@eidos.dev"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo_dir, check=True, capture_output=True)


def test_delta_verify_all_satisfied(tmp_path):
    """Verify that verify_delta_mutations rejects unmodified modify targets and passes once modified."""
    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-AUTH", title="Auth Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-AUTH-LOGIN", title="User Login")
    cnt_path = create_spec(eidos_dir, "container", "CNT-AUTH-SVC", title="Auth Service")
    pol_path = create_spec(eidos_dir, "policy", "POL-AUTH-PASS", title="Password Policy")
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Normative rule for Password Policy.", "Min 8 chars."),
        encoding="utf-8",
    )
    _commit_git_baseline(tmp_path)

    dlt_path = create_delta(
        eidos_dir,
        "DLT-2026-050",
        title="Auth Hardening",
    )
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - operation: modify
    target: CNT-AUTH-SVC
    details: "Harden auth service."
  - operation: modify
    target: CAP-AUTH-LOGIN
    details: "Add MFA support."
  - operation: modify
    target: POL-AUTH-PASS
    details: "Increase complexity."
  - operation: remove
    target: CNT-OLD-DEPRECATED
    details: "Verify old container is absent."
"""
    dlt_path.write_text(content.replace("mutations: []", mutations_yaml), encoding="utf-8")

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

    res = runner.invoke(app, ["delta", "verify", "DLT-2026-050", "-p", str(eidos_dir)])
    assert res.exit_code == 0
    assert "Verification Passed" in res.output

    res_json = runner.invoke(app, ["delta", "verify", "DLT-2026-050", "-p", str(eidos_dir), "--json"])
    assert res_json.exit_code == 0
    data = orjson.loads(res_json.output)
    assert data["is_verified"] is True
    assert data["total_mutations"] == 4


def test_delta_verify_declarative_expect_and_patch_invariant(tmp_path):
    """Verify declarative expect post-conditions and invariant modification baseline checks."""
    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-AUTH", title="Auth Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-AUTH-001", title="Auth Cap")
    pol_path = create_spec(eidos_dir, "policy", "POL-AUTH-001", title="Password Policy")
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Normative rule for Password Policy.", "Min 8 chars."),
        encoding="utf-8",
    )

    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["technical"]["invariants"] = [
        {
            "id": "INV-SPEC-VALIDATION",
            "rule": "Initial rule",
            "test_binding": "tests/test_delta_engine.py::test_delta_verify_declarative_expect_and_patch_invariant",
        }
    ]
    cap_path.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")
    _commit_git_baseline(tmp_path)

    dlt_path = create_delta(eidos_dir, "DLT-2026-054", title="Declarative & Invariant Patch")
    mutations_yaml = """mutations:
  - operation: modify
    target: INV-POL-AUTH-001  # eidos:fixture
    expect:
      frontmatter:
        rule: "Min 14 chars."
  - operation: modify
    target: INV-SPEC-VALIDATION
"""
    dlt_path.write_text(dlt_path.read_text(encoding="utf-8").replace("mutations: []", mutations_yaml), encoding="utf-8")

    ok1, _, rep1 = verify_delta_mutations(eidos_dir, "DLT-2026-054")
    assert ok1 is False
    assert [m.status for m in rep1.mutations] == ["UNMODIFIED", "UNMODIFIED"]

    pol_path.write_text(pol_path.read_text(encoding="utf-8").replace("Min 8 chars.", "Min 14 chars."), encoding="utf-8")
    cap_path.write_text(cap_path.read_text(encoding="utf-8").replace("Initial rule", "Patched rule"), encoding="utf-8")

    ok2, _, rep2 = verify_delta_mutations(eidos_dir, "DLT-2026-054")
    assert ok2 is True
    assert [m.status for m in rep2.mutations] == ["MODIFIED", "MODIFIED"]


def test_delta_verify_unfulfilled_mutations(tmp_path):
    """Verify that verify_delta_mutations detects missing specs and un-executed removals."""
    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "container", "CNT-STILL-EXISTS", title="Zombie Container")

    dlt_path = create_delta(eidos_dir, "DLT-2026-051", title="Pending Feature")
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - operation: add
    target: CAP-UNIMPLEMENTED-001
    details: "New capability not yet scaffolded."
  - operation: remove
    target: CNT-STILL-EXISTS
    details: "Container was not yet deleted."
"""
    dlt_path.write_text(content.replace("mutations: []", mutations_yaml), encoding="utf-8")

    is_verified, _msg, report = verify_delta_mutations(eidos_dir, "DLT-2026-051")
    assert is_verified is False
    assert report is not None
    assert report.total_mutations == 2
    assert report.verified_count == 0
    assert report.mutations[0].status == "MISSING"
    assert report.mutations[1].status == "STILL_PRESENT"

    res = runner.invoke(app, ["delta", "verify", "DLT-2026-051", "-p", str(eidos_dir)])
    assert res.exit_code == 1
    assert "Verification Incomplete" in res.output


def test_delta_execute_and_verify_lifecycle(tmp_path):
    """Verify INV-MUTATION-REVERSIBILITY: apply_delta enforces default verification and pre-commit SpecValidator checks before committing status: applied."""
    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-BILLING", title="Billing Domain")
    cnt_path = create_spec(eidos_dir, "container", "CNT-BILLING-ENGINE", title="Billing Engine")

    dlt_path = create_delta(eidos_dir, "DLT-2026-052", title="Billing Expansion")
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - operation: add
    target: CAP-INVOICE-GEN
    parent: DOM-BILLING
    details: "Generate PDF invoices."
  - operation: add
    target: POL-INVOICE-TAX
    details: "Invoices must apply regional VAT taxes."
  - operation: add
    target: CMP-INVOICE-RENDERER
    parent: CNT-BILLING-ENGINE
    details: "Renders invoices to PDF format."
  - operation: add
    target: CTR-INVOICE-API
    details: "REST contract for invoicing."
"""
    dlt_path.write_text(content.replace("mutations: []", mutations_yaml), encoding="utf-8")

    # 1. Pre-fulfillment: verify and apply_delta (verify=True by default) must fail and leave status: proposed
    is_ver, _, _report = verify_delta_mutations(eidos_dir, "DLT-2026-052")
    assert is_ver is False
    ok_early, msg_early, _ = apply_delta(eidos_dir, "DLT-2026-052")
    assert ok_early is False
    assert "Verification failed before apply" in msg_early
    assert parse_spec_file(dlt_path).frontmatter.get("status") == "proposed"

    # 2. Fulfill the declared mutations in the living specifications
    create_spec(eidos_dir, "capability", "CAP-INVOICE-GEN", title="Invoice Gen")
    create_spec(eidos_dir, "policy", "POL-INVOICE-TAX", title="Invoice Tax")
    create_spec(eidos_dir, "contract", "CTR-INVOICE-API", title="Invoice API")
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "invoice_renderer.py").write_text("# renderer\n", encoding="utf-8")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-INVOICE-RENDERER",
            "title": "InvoiceRenderer",
            "responsibility": "Renders invoices to PDF format.",
            "source_path": "src/invoice_renderer.py",
        }
    ]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    # 3. Post-fulfillment: verify and apply_delta succeed
    is_ver_after, _, report_after = verify_delta_mutations(eidos_dir, "DLT-2026-052")
    assert is_ver_after is True
    assert report_after.is_verified is True
    assert report_after.verified_count == 4

    ok_apply, _msg_apply, applied_file = apply_delta(eidos_dir, "DLT-2026-052")
    assert ok_apply is True
    assert applied_file == dlt_path.resolve()
    assert parse_spec_file(dlt_path).frontmatter.get("status") == "applied"

    # 4. Pre-commit SpecValidator check in apply_delta: invalid delta schema rejects status: applied transition
    dlt_invalid = create_delta(eidos_dir, "DLT-2026-057", title="Schema Validation Gate")
    dlt_invalid.write_text(
        dlt_invalid.read_text(encoding="utf-8").replace(
            "mutations: []",
            "unknown_top_level_key: invalid\nmutations: []",
        ),
        encoding="utf-8",
    )
    ok_inv, msg_inv, _ = apply_delta(eidos_dir, "DLT-2026-057")
    assert ok_inv is False
    assert "failed validation" in msg_inv
    assert parse_spec_file(dlt_invalid).frontmatter.get("status") == "proposed"


def test_delta_apply_cli_with_verify(tmp_path):
    """Verify 'eidos delta apply' via CLI with --verify and --no-verify."""
    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-NOTIFY", title="Notification Domain")

    dlt_path = create_delta(eidos_dir, "DLT-2026-053", title="Push Notifications")
    content = dlt_path.read_text(encoding="utf-8")
    mutations_yaml = """mutations:
  - operation: add
    target: CAP-PUSH-NOTIFY
    parent: DOM-NOTIFY
    details: "Send web push notifications."
"""
    dlt_path.write_text(content.replace("mutations: []", mutations_yaml), encoding="utf-8")

    # Applying with --verify before CAP-PUSH-NOTIFY exists must fail
    res_fail = runner.invoke(app, ["delta", "apply", "DLT-2026-053", "-p", str(eidos_dir), "--verify"])
    assert res_fail.exit_code == 1
    assert "Verification failed before apply" in res_fail.output

    # Create CAP-PUSH-NOTIFY and apply via CLI with --json
    create_spec(eidos_dir, "capability", "CAP-PUSH-NOTIFY", title="Push Notify")
    res_ok = runner.invoke(
        app,
        ["delta", "apply", "DLT-2026-053", "-p", str(eidos_dir), "--verify", "--json"],
    )
    assert res_ok.exit_code == 0
    data = orjson.loads(res_ok.output)
    assert data["success"] is True

    dlt_doc = parse_spec_file(dlt_path)
    assert dlt_doc.frontmatter.get("status") == "applied"


def test_delta_verify_requires_modification_evidence(tmp_path):
    """INV-DELTA-VERIFY-MODIFICATION-EVIDENCE: verify modify mutations require positive evidence of change."""
    init_repository(tmp_path, name="test-system")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")
    cap_path = create_spec(eidos_dir, "capability", "CAP-CORE-001", title="Core Cap")
    other_cap_path = create_spec(eidos_dir, "capability", "CAP-OTHER-001", title="Other Cap")
    cnt_path = create_spec(eidos_dir, "container", "CNT-CORE-SVC", title="Core Service")
    ctr_path = create_spec(eidos_dir, "contract", "CTR-CORE-API", title="Core API")
    pol_path = create_spec(eidos_dir, "policy", "POL-CORE-001", title="Core Policy")
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Normative rule for Core Policy.", "Initial policy rule."),
        encoding="utf-8",
    )

    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-TARGET",
            "name": "TargetComponent",
            "responsibility": "Initial target responsibility.",
        },
        {
            "id": "CMP-SIBLING",
            "name": "SiblingComponent",
            "responsibility": "Initial sibling responsibility.",
        },
    ]
    cnt_doc.frontmatter["contracts"] = {"provides": ["CTR-CORE-API"], "consumes": []}
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["technical"]["invariants"] = [
        {
            "id": "INV-DELTA-VERIFY-MODIFICATION-EVIDENCE",
            "rule": "Initial invariant rule.",
            "test_binding": "tests/test_delta_engine.py::test_delta_verify_requires_modification_evidence",
        }
    ]
    cap_path.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")
    _ = other_cap_path
    _commit_git_baseline(tmp_path)

    dlt_path = create_delta(eidos_dir, "DLT-2026-060", title="Evidence Verification")
    mutations_yaml = """mutations:
  - operation: modify
    target: POL-CORE-001
  - operation: modify
    target: CMP-TARGET
    parent: CNT-CORE-SVC
  - operation: modify
    target: CTR-CORE-API
    parent: CNT-CORE-SVC
  - operation: modify
    target: INV-DELTA-VERIFY-MODIFICATION-EVIDENCE
    parent: CAP-CORE-001
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
    dlt_mismatch = create_delta(eidos_dir, "DLT-2026-061", title="Subtarget Preconditions")
    mismatch_yaml = """mutations:
  - operation: modify
    target: INV-DELTA-VERIFY-MODIFICATION-EVIDENCE
    parent: CAP-OTHER-001
  - operation: modify
    target: CMP-TARGET
    parent: CNT-NONEXISTENT
  - operation: modify
    target: CTR-CORE-API
    parent: CNT-NONEXISTENT
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
    dlt_applied = create_delta(eidos_dir, "DLT-2026-062", title="Applied Expect Regression")
    applied_yaml = """mutations:
  - operation: modify
    target: POL-CORE-001
    expect:
      frontmatter:
        rule: "Expected post-apply rule."
  - operation: modify
    target: ARCH-TEST-001
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

    # 6. All standalone remove operations verify ABSENT_AS_EXPECTED when absent
    dlt_removals = create_delta(eidos_dir, "DLT-2026-063", title="Standalone Removals")
    removals_yaml = """mutations:
  - operation: remove
    target: DOM-REMOVED
  - operation: remove
    target: PER-REMOVED
  - operation: remove
    target: DS-REMOVED
  - operation: remove
    target: CLT-REMOVED
  - operation: remove
    target: FLOW-REMOVED
"""
    dlt_removals.write_text(
        dlt_removals.read_text(encoding="utf-8").replace("mutations: []", removals_yaml),
        encoding="utf-8",
    )
    ok_rem, _, rep_rem = verify_delta_mutations(eidos_dir, "DLT-2026-063")
    assert ok_rem is True
    assert [m.status for m in rep_rem.mutations] == ["ABSENT_AS_EXPECTED"] * 5

    # 7. Nested dot-path key resolution across dicts and lists of dicts (DLT-2026-103)
    from eidos.core.lifecycle.delta_engine import (
        _evaluate_declarative_postcondition,
        _resolve_frontmatter_path,
    )

    cnt_doc_nested = parse_spec_file(cnt_path)
    cnt_doc_nested.frontmatter["decisions"] = [
        {
            "id": "DEC-CORE-CACHE-103",
            "title": "Use SQLite Cache",
            "decision": "Persist graph index in SQLite WAL.",
            "rationale": "Guarantees zero external runtime dependencies.",
        }
    ]
    cnt_doc_nested.frontmatter["components"][0]["source_path"] = ["src/a.py", "src/b.py"]
    cnt_doc_nested.frontmatter["literal.key"] = "exact_val"
    cnt_path.write_text(dump_spec_document(cnt_doc_nested.frontmatter, cnt_doc_nested.body), encoding="utf-8")

    dlt_nested = create_delta(eidos_dir, "DLT-2026-103", title="Nested Dot-Path Expect")
    dlt_nested_doc = parse_spec_file(dlt_nested)
    dlt_nested_doc.frontmatter["mutations"] = [
        {
            "operation": "modify",
            "target": "CNT-CORE-SVC",
            "expect": {
                "frontmatter": {
                    "contracts.provides": "CTR-CORE-API",
                    "decisions.id": "DEC-CORE-CACHE-103",
                    "decisions.decision": "SQLite WAL",
                    "decisions.rationale": "zero external runtime dependencies",
                    "components.responsibility": ["Updated target responsibility"],
                    "components.source_path": "src/b.py",
                    "literal.key": "exact_val",
                    "absent_top_level_key": None,
                }
            },
        }
    ]
    dlt_nested.write_text(dump_spec_document(dlt_nested_doc.frontmatter, dlt_nested_doc.body), encoding="utf-8")

    ok_nested, msg_nested, rep_nested = verify_delta_mutations(eidos_dir, "DLT-2026-103")
    assert ok_nested is True, msg_nested
    assert rep_nested.mutations[0].status == "MODIFIED"

    # Edge cases: empty dot-path segments ("contracts..provides", ".contracts", "contracts.")
    specs_lookup = {"CNT-CORE-SVC": (cnt_path, "container", cnt_doc_nested.frontmatter)}
    for malformed_key in ("contracts..provides", ".contracts", "contracts.", ".."):
        found_seg, val_seg = _resolve_frontmatter_path(cnt_doc_nested.frontmatter, malformed_key)
        assert found_seg is False
        assert val_seg is None
        diag_seg: list[str] = []
        ok_seg = _evaluate_declarative_postcondition(
            {"expect": {"frontmatter": {malformed_key: "CTR-CORE-API"}}},
            "CNT-CORE-SVC",
            specs_lookup,
            {},
            {},
            diagnostics=diag_seg,
        )
        assert ok_seg is False
        assert any(f"frontmatter.{malformed_key}" in d for d in diag_seg)

    # Edge case: non-dict / scalar intermediate segment (traversing through a string value)
    found_scalar, val_scalar = _resolve_frontmatter_path(cnt_doc_nested.frontmatter, "title.sub")
    assert found_scalar is False
    assert val_scalar is None
    diag_scalar: list[str] = []
    ok_scalar = _evaluate_declarative_postcondition(
        {"expect": {"frontmatter": {"title.sub": "val"}}},
        "CNT-CORE-SVC",
        specs_lookup,
        {},
        {},
        diagnostics=diag_scalar,
    )
    assert ok_scalar is False
    assert any("frontmatter.title.sub expected 'val', got None" in d for d in diag_scalar)

    # Edge case: non-dict target guard in _resolve_frontmatter_path
    assert _resolve_frontmatter_path(None, "a.b") == (False, None)  # type: ignore[arg-type]

    # Edge case: top-level null (None) succeeds when key is absent, but unresolved dot-path null fails
    ok_top_none = _evaluate_declarative_postcondition(
        {"expect": {"frontmatter": {"absent_top_level_key": None}}},
        "CNT-CORE-SVC",
        specs_lookup,
        {},
        {},
    )
    assert ok_top_none is True

    diag_dot_none: list[str] = []
    ok_dot_none = _evaluate_declarative_postcondition(
        {"expect": {"frontmatter": {"absent_parent.child": None}}},
        "CNT-CORE-SVC",
        specs_lookup,
        {},
        {},
        diagnostics=diag_dot_none,
    )
    assert ok_dot_none is False
    assert any("frontmatter.absent_parent.child" in d for d in diag_dot_none)

    # Verify non-matching dot-path and missing intermediate dot-path fail with clear diagnostics
    dlt_nested_doc.frontmatter["mutations"][0]["expect"]["frontmatter"]["decisions.id"] = "DEC-DOES-NOT-EXIST"
    dlt_nested.write_text(dump_spec_document(dlt_nested_doc.frontmatter, dlt_nested_doc.body), encoding="utf-8")
    ok_nested_fail, _, rep_nested_fail = verify_delta_mutations(eidos_dir, "DLT-2026-103")
    assert ok_nested_fail is False
    assert "frontmatter.decisions.id" in (rep_nested_fail.mutations[0].failure_reason or "")

    dlt_nested_doc.frontmatter["mutations"][0]["expect"]["frontmatter"] = {
        "nonexistent.nested.path": "val",
    }
    dlt_nested.write_text(dump_spec_document(dlt_nested_doc.frontmatter, dlt_nested_doc.body), encoding="utf-8")
    ok_nested_missing, _, rep_nested_missing = verify_delta_mutations(eidos_dir, "DLT-2026-103")
    assert ok_nested_missing is False
    assert "frontmatter.nonexistent.nested.path expected 'val', got None" in (
        rep_nested_missing.mutations[0].failure_reason or ""
    )


def test_delta_schema_operation_coverage_and_git_baseline_fallback(tmp_path):
    """Verify 100% composable operation coverage and Git-backed baseline SHA fallback when SQLite cache is absent."""
    # eidos:fixture
    from eidos.core.spec.parser import safe_load_yaml

    delta_schema_path = get_assets_path("schemas/v1/delta/delta.schema.yaml")
    schema_data = safe_load_yaml(delta_schema_path.read_text(encoding="utf-8"))
    schema_ops = set(schema_data["properties"]["mutations"]["items"]["properties"]["operation"]["enum"])
    assert schema_ops == set(SUPPORTED_MUTATION_OPERATIONS)

    init_repository(tmp_path, name="git-baseline-test")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-GIT", title="Git Domain")
    pol_path = create_spec(eidos_dir, "policy", "POL-GIT-001", title="Git Policy")
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Normative rule for Git Policy.", "Initial git rule."),
        encoding="utf-8",
    )
    cnt_path = create_spec(eidos_dir, "container", "CNT-GIT-SVC", title="Git Service")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"] = [{"id": "CMP-GIT-COMP", "name": "GitComp", "responsibility": "Initial comp."}]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cap_path = create_spec(eidos_dir, "capability", "CAP-GIT-001", title="Git Cap")
    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["technical"]["invariants"] = [
        {
            "id": "INV-GIT-001",
            "rule": "Initial inv rule.",
            "test_binding": "tests/test_delta_engine.py::test_delta_schema_operation_coverage_and_git_baseline_fallback",
        }
    ]
    cap_path.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Baseline specs"], cwd=tmp_path, check=True, capture_output=True)

    dlt_path = create_delta(eidos_dir, "DLT-2026-070", title="Git Fallback Verification")
    mutations_yaml = """mutations:
  - operation: modify
    target: POL-GIT-001
  - operation: modify
    target: CMP-GIT-COMP
    parent: CNT-GIT-SVC
  - operation: modify
    target: INV-GIT-001
    parent: CAP-GIT-001
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

    shutil.rmtree(eidos_dir / ".cache", ignore_errors=True)

    ok_git, _, rep_git = verify_delta_mutations(eidos_dir, "DLT-2026-070")
    assert ok_git is True
    assert [m.status for m in rep_git.mutations] == ["MODIFIED", "MODIFIED", "MODIFIED"]


def test_delta_vcs_base_commit_and_collision_free_ids(tmp_path):
    """Verify vcs.base_commit on uncommitted deltas and collision-free delta IDs."""
    init_repository(tmp_path, name="cow-baseline-test")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-COW", title="COW Domain")
    pol_path = create_spec(eidos_dir, "policy", "POL-COW-001", title="COW Policy")
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Normative rule for COW Policy.", "Initial cow rule."),
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial baseline"], cwd=tmp_path, check=True, capture_output=True)

    dlt_auto = create_delta(eidos_dir, raw_id="auto", title="Automated Portable Baseline", slug_suffix=True)
    dlt_doc = parse_spec_file(dlt_auto)
    assert dlt_doc.doc_id.startswith("DLT-")
    assert "AUTOMATED-PORTABLE" in dlt_doc.doc_id
    vcs_meta = dlt_doc.frontmatter.get("vcs", {})
    assert isinstance(vcs_meta, dict) and len(vcs_meta.get("base_commit", "")) >= 7

    next_id = generate_collision_free_delta_id(eidos_dir, raw_id="auto", title="Second Delta")
    assert next_id != dlt_doc.doc_id

    mutations_yaml = """mutations:
  - operation: modify
    target: POL-COW-001
"""
    dlt_auto.write_text(
        dlt_auto.read_text(encoding="utf-8").replace("mutations: []", mutations_yaml),
        encoding="utf-8",
    )
    pol_path.write_text(
        pol_path.read_text(encoding="utf-8").replace("Initial cow rule.", "Modified cow rule on branch."),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(pol_path)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Modify policy before committing delta"], cwd=tmp_path, check=True, capture_output=True
    )
    shutil.rmtree(eidos_dir / ".cache", ignore_errors=True)

    ok_uncommitted, _, rep_uncommitted = verify_delta_mutations(eidos_dir, dlt_doc.doc_id)
    assert ok_uncommitted is True
    assert rep_uncommitted.mutations[0].status == "MODIFIED"


def test_deep_review_subsystem_3_scaffolder_catalog_delta_and_changelog_edge_cases(tmp_path):
    """Verify contract owning_container fallback ignores CMP-*, _set_frontmatter_scalar preserves '#' in quotes,
    set_delta_status('applied') enforces verification, and generate_changelog includes commits in tagless repos."""
    init_repository(tmp_path, name="subsystem-3-deep")
    eidos_dir = tmp_path / "eidos"

    (eidos_dir / "architecture" / "components").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "components" / "cmp-aaa.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-AAA\n"
        "title: Alpha Component\n"
        "responsibility: Sorts before containers.\n"
        "---\n"
        "= Alpha Component\n",
        encoding="utf-8",
    )
    create_spec(eidos_dir, "container", "CNT-ZZZ", title="Zeta Container")
    ctr_path = create_spec(eidos_dir, "contract", "CTR-AUTO-OWNER", title="Auto Owner Contract")
    ctr_doc = parse_spec_file(ctr_path)
    assert ctr_doc.frontmatter.get("container") == "CNT-ZZZ"

    sample_fm = '---\ntitle: "Channel #general & #alerts"\nstatus:\n---\n= Sample\n'
    updated_fm = _set_frontmatter_scalar(sample_fm, "title", '"Updated #channel"')
    updated_fm = _set_frontmatter_scalar(updated_fm, "status", "approved")
    assert 'title: "Updated #channel"' in updated_fm
    assert "status: approved" in updated_fm

    dlt_path = create_delta(eidos_dir, "DLT-2026-088", title="Draft Gate Delta")
    dlt_text = dlt_path.read_text(encoding="utf-8")
    dlt_text = _set_frontmatter_scalar(dlt_text, "status", "in_review")
    dlt_text = dlt_text.replace(
        "mutations: []",
        "mutations:\n  - operation: modify\n    target: CNT-ZZZ\n    details: Require TLS 1.3",
    )
    dlt_path.write_text(dlt_text, encoding="utf-8")

    active_list = list_deltas(eidos_dir)
    assert any(d["id"] == "DLT-2026-088" for d in active_list)
    summary = get_delta_status_summary(eidos_dir)
    assert summary["active_count"] >= 1
    assert any(d["id"] == "DLT-2026-088" for d in summary["by_status"]["in_review"])

    ok_apply, msg_apply, _, _ = set_delta_status(eidos_dir, "DLT-2026-088", "applied")
    assert ok_apply is False
    assert "Cannot transition" in msg_apply

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat(core): add tagless changelog support"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    changelog_md = ChangelogGenerator(repo_root=tmp_path, eidos_dir=eidos_dir).generate_changelog()
    assert "add tagless changelog support" in changelog_md.lower()


def test_delta_engine_all_mutation_families_and_helpers(tmp_path):
    """Verify composable add/modify/remove/deprecate across invariants, components, architecture, policies, and helpers."""
    # eidos:fixture
    init_repository(tmp_path, name="all-mutations-test")
    eidos_dir = tmp_path / "eidos"
    arch_file = eidos_dir / "architecture" / "system-architecture.adoc"
    arch_file.write_text(
        "---\n"
        "type: tech/architecture\n"
        "id: ARCH-TEST-001\n"
        "title: Test Technical Architecture\n"
        "c4_context:\n"
        "  actors:\n"
        "    - PER-DEV\n"
        "---\n"
        "= Test Technical Architecture\n",
        encoding="utf-8",
    )

    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")
    create_spec(eidos_dir, "persona", "PER-DEV", title="Developer")
    cnt_path = create_spec(eidos_dir, "container", "CNT-APP", title="App Container")
    cnt_doc0 = parse_spec_file(cnt_path)
    cnt_doc0.frontmatter["components"] = [
        {
            "id": "CMP-INLINE-WORKER",
            "title": "Inline Worker",
            "responsibility": "Process jobs.",
            "source_path": "src/app/worker.py",
        }
    ]
    cnt_path.write_text(dump_spec_document(cnt_doc0.frontmatter, cnt_doc0.body), encoding="utf-8")

    cap_path = create_spec(eidos_dir, "capability", "CAP-LEGACY-001", title="Legacy Cap")
    pol_path = create_spec(
        eidos_dir,
        "policy",
        "POL-900-TEST",
        title="Test Policy",
    )
    pol_del_path = create_spec(
        eidos_dir,
        "policy",
        "POL-901-DELETE",
        title="Delete Policy",
    )

    dlt1 = create_delta(eidos_dir, "DLT-2026-910", title="Mutation Families Wave 1")
    dlt1.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-910\n"
        "title: Mutation Families Wave 1\n"
        "status: approved\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Test all composable mutation families.\n"
        "mutations:\n"
        "  - operation: add\n"
        "    target: INV-900-A\n"
        "    parent: POL-900-TEST\n"
        "    expect:\n"
        "      frontmatter:\n"
        "        rule: Initial invariant rule.\n"
        "        test_binding: tests/test_inv.py::test_inv_900\n"
        "  - operation: modify\n"
        "    target: CMP-INLINE-WORKER\n"
        "    parent: CNT-APP\n"
        "    expect:\n"
        "      frontmatter:\n"
        "        responsibility: Updated inline worker responsibility for batching.\n"
        "  - operation: modify\n"
        "    target: ARCH-TEST-001\n"
        "    expect:\n"
        "      body:\n"
        "        contains: Documented cross-cutting telemetry architecture.\n"
        "  - operation: deprecate\n"
        "    target: CAP-LEGACY-001\n"
        "  - operation: remove\n"
        "    target: POL-901-DELETE\n"
        "---\n"
        "= Mutation Families Wave 1\n",
        encoding="utf-8",
    )

    # Fulfill Wave 1 mutations on disk
    pol_doc = parse_spec_file(pol_path)
    pol_doc.frontmatter["invariants"] = [
        {"id": "INV-900-A", "rule": "Initial invariant rule.", "test_binding": "tests/test_inv.py::test_inv_900"}
    ]
    pol_path.write_text(dump_spec_document(pol_doc.frontmatter, pol_doc.body), encoding="utf-8")

    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"][0]["responsibility"] = "Updated inline worker responsibility for batching."
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    arch_file.write_text(
        arch_file.read_text(encoding="utf-8") + "\nDocumented cross-cutting telemetry architecture.\n",
        encoding="utf-8",
    )
    cap_path.write_text(
        _set_frontmatter_scalar(cap_path.read_text(encoding="utf-8"), "status", "deprecated"), encoding="utf-8"
    )
    pol_del_path.unlink()

    ver_ok1, ver_msg1, ver_rep1 = verify_delta_mutations(eidos_dir, "DLT-2026-910")
    assert ver_ok1 is True, f"Wave 1 verification failed: {ver_msg1}"
    assert ver_rep1 is not None and len(ver_rep1.mutations) == 5

    # Helper unit tests
    gi_file = eidos_dir / ".gitignore"
    gi_file.write_text("# Partial gitignore\n", encoding="utf-8")
    _ensure_eidos_gitignore(eidos_dir)
    assert ".cache/" in gi_file.read_text(encoding="utf-8")

    assert _extract_mutation_target({"operation": "modify", "target": "CNT-999-CORE"}) == "CNT-999-CORE"
    assert (
        _extract_mutation_target({"operation": "modify", "target": "architecture/containers/svc.adoc"})
        == "architecture/containers/svc.adoc"
    )
    assert _extract_mutation_target({"operation": "modify"}) == "unspecified_target"

    empty_doc = _parse_spec_content(eidos_dir / "system.adoc", "   ")
    assert empty_doc.doc_type == "product/system"
    no_fm_doc = _parse_spec_content(eidos_dir / "bad.adoc", "= No Frontmatter\n")
    assert len(no_fm_doc.errors) == 1
    bad_yaml_doc = _parse_spec_content(eidos_dir / "bad.adoc", "---\n: [broken\n---\n= Body\n")
    assert any("Frontmatter parse error" in e for e in bad_yaml_doc.errors)

    specs_by_id, container_components, _ = _build_spec_catalog(eidos_dir)
    assert (
        _check_structural_subtarget_precondition(
            {"parent": "CAP-MISSING", "target": "INV-1"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"parent": "CNT-MISSING", "target": "CMP-1"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"parent": "CNT-APP", "target": "CMP-NOT-IN-CNT"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"parent": "CNT-APP", "target": "CTR-UNLINKED"}, specs_by_id, container_components
        )
        is False
    )

    rel_sid, rel_p, rel_ok = _resolve_target_path_and_id(f"policies/{pol_path.name}", eidos_dir, specs_by_id)
    assert rel_ok is True and rel_sid == "POL-900-TEST" and rel_p is not None

    val_part, cmt_part = _split_yaml_inline_comment(r'"escaped \" quote # inside" # real comment')
    assert val_part == r'"escaped \" quote # inside"' and "# real comment" in cmt_part
    assert _set_frontmatter_scalar("no frontmatter", "status", "active") == "no frontmatter"
    raw_no_comps = "---\ntype: tech/container\nid: CNT-X\n---\n= X\n"
    appended_comps = _mutate_frontmatter_components(
        raw_no_comps, "append", "CMP-NEW", {"id": "CMP-NEW", "title": "New"}
    )
    assert "CMP-NEW" in appended_comps
    raw_empty_comps = "---\ntype: tech/container\nid: CNT-X\ncomponents: []\n---\n= X\n"
    appended_from_empty = _mutate_frontmatter_components(
        raw_empty_comps, "append", "CMP-NEW", {"id": "CMP-NEW", "title": "New"}
    )
    assert "CMP-NEW" in appended_from_empty
    assert find_spec_by_id_or_path(eidos_dir, str(pol_path)) == pol_path.resolve()
    assert find_spec_by_id_or_path(eidos_dir, f"policies/{pol_path.stem}") == pol_path.resolve()

    ok_apply_missing, _, _ = apply_delta(eidos_dir, "DLT-DOES-NOT-EXIST")
    assert ok_apply_missing is False
    ok_apply_nondelta, _, _ = apply_delta(eidos_dir, "POL-900-TEST")
    assert ok_apply_nondelta is False


def test_delta_engine_git_baseline_and_catalog_helpers(tmp_path):
    """Cover _resolve_git_baseline_sha, _evaluate_declarative_postcondition branches, catalog.py helpers, and initializer.py."""
    # eidos:fixture
    repo_init = tmp_path / "init_full"
    ok_init, _, _ = init_repository(repo_init, lite=False)
    assert ok_init is True
    ok_reinit, msg_reinit, _ = init_repository(repo_init, force=False)
    assert ok_reinit is False and "Use --force" in msg_reinit

    raw_with_trailing_key = (
        "---\n"
        "type: tech/container\n"
        "id: CNT-TRAIL\n"
        "components:\n"
        "  - id: CMP-OLD\n"
        "    title: Old\n"
        "\n"
        "  # comment before next top-level key\n"
        "decisions: []\n"
        "---\n"
        "= Trail\n"
    )
    mutated_trail = _mutate_frontmatter_components(
        raw_with_trailing_key, "append", "CMP-NEW", {"id": "CMP-NEW", "title": "New"}
    )
    assert "CMP-NEW" in mutated_trail and "decisions: []" in mutated_trail

    git_repo = tmp_path / "git_delta_ws"
    eidos_dir = git_repo / "eidos"
    init_repository(git_repo, name="git-delta", title="Git Delta")
    cnt_file = create_spec(eidos_dir, "container", "CNT-BASE", "Base Container")
    doc_cnt = parse_spec_file(cnt_file)
    doc_cnt.frontmatter["components"] = [
        {
            "id": "CMP-INLINE-1",
            "title": "Inline One",
            "responsibility": "Core",
            "source_path": "src/base/inline_one.py",
        }
    ]
    cnt_file.write_text(dump_spec_document(doc_cnt.frontmatter, doc_cnt.body), encoding="utf-8")

    pol_file = create_spec(eidos_dir, "policy", "POL-BASE", "Base Policy")
    doc_pol = parse_spec_file(pol_file)
    doc_pol.frontmatter["invariants"] = [{"id": "INV-BASE-001", "rule": "Rule 1", "test_binding": "N/A"}]
    pol_file.write_text(dump_spec_document(doc_pol.frontmatter, doc_pol.body), encoding="utf-8")

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

    dlt_file = create_delta(eidos_dir, "DLT-910", title="Git Baseline Test")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add delta"], cwd=git_repo, check=True, capture_output=True)

    cnt_file.write_text(cnt_file.read_text(encoding="utf-8") + "\nPost-delta change.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "modify container"], cwd=git_repo, check=True, capture_output=True)

    assert _resolve_git_baseline_sha(eidos_dir, dlt_file, "CMP-INLINE-1", cnt_file) is not None
    assert _resolve_git_baseline_sha(eidos_dir, dlt_file, "INV-BASE-001", pol_file) is not None
    assert _resolve_git_baseline_sha(eidos_dir, dlt_file, "CNT-BASE", cnt_file) is not None

    catalog, comp_map, inv_map = _build_spec_catalog_with_invariants(eidos_dir)
    assert (
        _evaluate_declarative_postcondition(
            {
                "operation": "modify",
                "target": "CMP-INLINE-1",
                "expect": {"frontmatter": {"responsibility": "Core"}},
            },
            "CMP-INLINE-1",
            catalog,
            comp_map,
            inv_map,
        )
        is True
    )
    assert (
        _evaluate_declarative_postcondition(
            {
                "operation": "modify",
                "target": "CMP-INLINE-1",
                "expect": {"frontmatter": {"responsibility": "Other"}},
            },
            "CMP-INLINE-1",
            catalog,
            comp_map,
            inv_map,
        )
        is False
    )

    assert list_deltas(tmp_path / "no_such_eidos") == []
    note_in_deltas = eidos_dir / "deltas" / "note.adoc"
    note_in_deltas.write_text("---\ntype: product/policy\nid: POL-NOTE\n---\n= Note\n", encoding="utf-8")
    assert list_deltas(eidos_dir, status_filter="applied") == []
    note_in_deltas.unlink()


def test_dlt_2026_079_structured_expect_assertions_and_diagnostics(tmp_path):
    """Verify DLT-2026-079: structured body/frontmatter expect post-conditions, multi-string contains/not_contains,
    list-subset matching, comment stripping, baseline SHA verification alongside expect, and failure_reason diagnostics."""
    init_repository(tmp_path, name="dlt-079-ws")
    eidos_dir = tmp_path / "eidos"

    cnt_path = create_spec(eidos_dir, "container", "CNT-API", title="API Service")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-ROUTER",
            "title": "Router",
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

    diag_ok: list[str] = []
    assert (
        _evaluate_declarative_postcondition(
            {
                "operation": "modify",
                "target": "CMP-ROUTER",
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

    _commit_git_baseline(tmp_path)
    dlt_path = create_delta(eidos_dir, "DLT-2026-079", title="Structured Expect Test")
    dlt_doc = parse_spec_file(dlt_path)
    dlt_doc.frontmatter["mutations"] = [
        {
            "operation": "modify",
            "target": "CMP-ROUTER",
            "details": "Add circuit breaker to router.",
            "expect": {
                "frontmatter": {
                    "responsibility": "enforces rate limits",
                }
            },
        }
    ]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")

    ver_before, _, rep_before = verify_delta_mutations(eidos_dir, "DLT-2026-079")
    assert ver_before is False
    assert rep_before is not None
    assert rep_before.mutations[0].status == "UNMODIFIED"
    assert rep_before.mutations[0].failure_reason is not None
    assert "matches pre-delta baseline" in rep_before.mutations[0].failure_reason


def test_dlt_2026_080_cross_ref_collision_resolution_and_cli_slug(tmp_path):
    """Verify DLT-2026-080: cross-ref collision resolution across parallel Git branches, explicit colliding ID resolution, and optional ID + --slug CLI ergonomics."""
    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@eidos.dev"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp_path, check=True, capture_output=True)

    subprocess.run(["git", "checkout", "-b", "feat/parallel-branch"], cwd=tmp_path, check=True, capture_output=True)
    d1 = create_delta(eidos_dir, "DLT-2026-001", title="Parallel Branch Feature")
    assert d1.name == "dlt-2026-001.adoc"
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add DLT-2026-001 on parallel branch"], cwd=tmp_path, check=True, capture_output=True
    )

    subprocess.run(["git", "checkout", "main"], cwd=tmp_path, check=True, capture_output=True)
    assert not (eidos_dir / "deltas" / "dlt-2026-001.adoc").exists()

    resolved_explicit = generate_collision_free_delta_id(eidos_dir, raw_id="DLT-2026-001", title="Main Branch Feature")
    assert resolved_explicit == "DLT-2026-001-MAIN-BRANCH"

    d2 = create_delta(eidos_dir, "DLT-2026-001", title="Main Branch Feature")
    assert d2.name == "dlt-2026-001-main-branch.adoc"

    cli_runner = CliRunner()
    res = cli_runner.invoke(
        app,
        ["delta", "create", "--title", "Zero Config Auth", "--slug", "--path", str(eidos_dir)],
    )
    assert res.exit_code == 0, res.stdout
    assert "dlt-2026-002-zero-config.adoc" in res.stdout.lower()
    assert (eidos_dir / "deltas" / "dlt-2026-002-zero-config.adoc").exists()


def test_dlt_2026_083_baseline_retention_across_schema_rebuild(tmp_path):
    """Verify ensure_cache_schema drops retired/unmanaged tables and rebuilds stateless cache tables."""
    from eidos.core.index.storage import _MANAGED_TABLES, SCHEMA_VERSION

    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "capability", "CAP-AUTH-001", title="Auth Capability")
    create_delta(eidos_dir, "DLT-2026-001", title="Add Second Auth Invariant")

    db_path = eidos_dir / ".cache" / "index.db"
    conn = open_cache_connection(db_path)
    try:
        # 1. Retired table + outdated schema_version -> dropped and rebuilt
        conn.execute("CREATE TABLE retired_legacy_table (k TEXT PRIMARY KEY, v TEXT);")
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '0.0-outdated');")
        conn.commit()
        ensure_cache_schema(conn)
        ver = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()[0]
        assert ver == SCHEMA_VERSION == "2.15.0"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall()}
        assert "retired_legacy_table" not in tables
        assert set(_MANAGED_TABLES) <= tables

        # 2. Retired table present when schema_version already matches SCHEMA_VERSION -> still dropped
        conn.execute("CREATE TABLE retired_same_ver_table (k TEXT PRIMARY KEY, v TEXT);")
        conn.commit()
        ensure_cache_schema(conn)
        tables_after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall()}
        assert "retired_same_ver_table" not in tables_after
        assert set(_MANAGED_TABLES) <= tables_after
    finally:
        conn.close()


def test_catalog_and_delta_engine_edge_cases(tmp_path):
    """Cover status and postcondition branches in delta_engine.py."""
    diag: list[str] = []
    assert (
        _evaluate_declarative_postcondition({"expect": {"bad_top": 1}}, "CNT-1", {}, {}, {}, diagnostics=diag) is False
    )
    assert any("unsupported top-level keys" in d for d in diag)

    diag.clear()
    assert (
        _evaluate_declarative_postcondition({"expect": {"body": "not_dict"}}, "CNT-1", {}, {}, {}, diagnostics=diag)
        is False
    )
    assert "expect.body must be a mapping" in diag

    diag.clear()
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"body": {"bad_body": 1}}}, "CNT-1", {}, {}, {}, diagnostics=diag
        )
        is False
    )
    assert any("unsupported keys" in d for d in diag)

    diag.clear()
    assert (
        _evaluate_declarative_postcondition(
            {"expect": {"frontmatter": "not_dict"}}, "CNT-1", {}, {}, {}, diagnostics=diag
        )
        is False
    )
    assert "expect.frontmatter must be a mapping" in diag

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

    dlt_doc = parse_spec_file(dlt_file)
    dlt_doc.frontmatter["mutations"] = [{"operation": "deprecate", "target": "CAP-API-001"}]
    dlt_file.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")
    v_ok, _, v_rep = verify_delta_mutations(eidos_dir, "DLT-2026-050")
    assert v_ok is False and v_rep is not None and v_rep.mutations[0].status == "ACTIVE_NOT_DEPRECATED"
    ok_app_blocked, msg_app_blocked, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "applied")
    assert ok_app_blocked is False and "unverified mutations" in msg_app_blocked

    cap_path.write_text(
        _set_frontmatter_scalar(cap_path.read_text(encoding="utf-8"), "status", "deprecated"), encoding="utf-8"
    )
    ok_rev, _, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "in_review")
    assert ok_rev is True
    ok_app, _, _, _ = set_delta_status(eidos_dir, "DLT-2026-050", "applied")
    assert ok_app is True
    summary = get_delta_status_summary(eidos_dir)
    assert summary["total"] >= 1
    assert len(summary["by_status"]["applied"]) == 1

    stripped = _strip_spec_comments(
        "---\n# fm comment\nid: CNT-1\n---\n////\nblock comment\n////\n// line comment\nReal body\n"
    )
    assert "# fm comment" not in stripped
    assert "block comment" not in stripped
    assert "Real body" in stripped

    cnt_doc = parse_spec_file(cnt_path)
    inv_cnt_id = "INV-" + "CNT-API-001"
    cnt_doc.frontmatter["invariants"] = [{"id": inv_cnt_id, "rule": "Orig Rule", "test_binding": "tests/t.py::t"}]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    dlt2 = create_delta(eidos_dir, "DLT-2026-051", title="Failure Branches")
    dlt2_doc = parse_spec_file(dlt2)
    dlt2_doc.frontmatter["mutations"] = [
        {"operation": "unknown_op_xyz", "target": "CNT-API"},
        {"operation": "add", "parent": "CNT-API", "target": "INV-" + "MISSING-999"},
        {
            "operation": "add",
            "parent": "CNT-API",
            "target": inv_cnt_id,
            "expect": {"frontmatter": {"rule": "Wrong Rule"}},
        },
        {
            "operation": "modify",
            "parent": "CNT-API",
            "target": inv_cnt_id,
            "expect": {"frontmatter": {"rule": "Wrong Rule"}},
        },
        {"operation": "remove", "parent": "CNT-API", "target": inv_cnt_id},
        {
            "operation": "add",
            "target": "CNT-API",
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


def test_dlt_2026_087_composable_mutation_normalize_and_verify(tmp_path):
    """Verify composable (operation + target + parent) mutations normalize and verify cleanly."""
    # eidos:fixture
    norm_cmp = normalize_delta_mutation({"operation": "modify", "target": "CMP-AUTH", "parent": "CNT-API"})
    assert norm_cmp["operation"] == "modify"
    assert norm_cmp["target"] == "CMP-AUTH"
    assert norm_cmp["parent"] == "CNT-API"
    assert norm_cmp["entity_type"] == "component"

    assert resolve_entity_prefix_meta("CMP-AUTH") == ("CMP-", "component", "tech/component")
    assert resolve_entity_prefix_meta("INV-AUTH-001") == ("INV-", "invariant", "invariant")
    assert resolve_entity_prefix_meta("INVALID") is None


def test_dlt_2026_088_default_verify_apply(tmp_path):
    """Verify apply_delta and 'eidos delta apply' enforce verify=True by default (unless --no-verify)."""
    ws = tmp_path / "dlt_088_ws"
    init_repository(ws)
    eidos_dir = ws / "eidos"

    cnt_ts = create_spec(eidos_dir, "container", "CNT-WEB-UI", title="Web UI")
    cnt_ts_doc = parse_spec_file(cnt_ts)
    cnt_ts_doc.frontmatter["runtime"] = "TypeScript 5 / Vite"
    cnt_ts_doc.frontmatter["components"] = [
        {
            "id": "CMP-HEADER",
            "title": "Header",
            "responsibility": "Renders top navigation bar.",
            "source_path": "src/web/header.ts",
        }
    ]
    cnt_ts.write_text(dump_spec_document(cnt_ts_doc.frontmatter, cnt_ts_doc.body), encoding="utf-8")

    dlt_unverified = create_delta(eidos_dir, "DLT-2026-088", title="Unverified Apply Gate")
    dlt_u_doc = parse_spec_file(dlt_unverified)
    dlt_u_doc.frontmatter["mutations"] = [
        {
            "operation": "modify",
            "target": "CMP-HEADER",
            "parent": "CNT-WEB-UI",
            "details": "Add theme toggle to header.",
            "expect": {"frontmatter": {"responsibility": "Renders top navigation bar with theme toggle."}},
        }
    ]
    dlt_unverified.write_text(dump_spec_document(dlt_u_doc.frontmatter, dlt_u_doc.body), encoding="utf-8")

    ok_default, msg_default, _ = apply_delta(eidos_dir, "DLT-2026-088")
    assert ok_default is False
    assert "Verification failed before apply" in msg_default
    assert parse_spec_file(dlt_unverified).frontmatter["status"] == "proposed"

    res_cli_default = runner.invoke(app, ["delta", "apply", "DLT-2026-088", "--path", str(eidos_dir)])
    assert res_cli_default.exit_code == 1
    assert parse_spec_file(dlt_unverified).frontmatter["status"] == "proposed"

    res_cli_no_verify = runner.invoke(app, ["delta", "apply", "DLT-2026-088", "--no-verify", "--path", str(eidos_dir)])
    assert res_cli_no_verify.exit_code == 0, res_cli_no_verify.output
    assert parse_spec_file(dlt_unverified).frontmatter["status"] == "applied"


def test_inline_component_invariant_git_baseline_and_pre_create_precedence(tmp_path):
    """Verify Bugs 6 & 7: _resolve_git_baseline_sha finds components[].invariants on cold cache,
    and pre-create dirty edits verify consistently with and without expect."""  # eidos:fixture
    import subprocess

    ws = tmp_path / "git_inline_inv_ws"
    init_repository(ws, name="inline-inv-test")
    eidos_dir = ws / "eidos"

    cnt_path = create_spec(eidos_dir, "container", "CNT-API-GW", title="API Gateway")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-AUTH-FILTER",
            "title": "Auth Filter",
            "responsibility": "Validates JWT bearer tokens.",
            "source_path": "src/gw/auth.py",
            "invariants": [
                {
                    "id": "INV-INLINE-JWT-001",
                    "rule": "Rejects expired JWT tokens.",
                    "test_binding": "tests/test_auth.py::test_jwt",
                }
            ],
        }
    ]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    pol_a = create_spec(eidos_dir, "policy", "POL-PRE-A", title="Pre Edit Policy A")
    pol_b = create_spec(eidos_dir, "policy", "POL-PRE-B", title="Pre Edit Policy B")

    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline specs"], cwd=ws, check=True, capture_output=True)

    # Edit inline component invariant AND both policies BEFORE calling create_delta
    cnt_doc2 = parse_spec_file(cnt_path)
    cnt_doc2.frontmatter["components"][0]["invariants"][0]["rule"] = "Rejects expired or revoked JWT tokens with 401."
    cnt_path.write_text(dump_spec_document(cnt_doc2.frontmatter, cnt_doc2.body), encoding="utf-8")

    pol_a_doc = parse_spec_file(pol_a)
    pol_a_doc.frontmatter["severity"] = "warning"
    pol_a.write_text(dump_spec_document(pol_a_doc.frontmatter, pol_a_doc.body), encoding="utf-8")

    pol_b_doc = parse_spec_file(pol_b)
    pol_b_doc.frontmatter["severity"] = "warning"
    pol_b.write_text(dump_spec_document(pol_b_doc.frontmatter, pol_b_doc.body), encoding="utf-8")

    # Create delta AFTER specs were already dirtied in working tree (snapshots dirty state into SQLite)
    dlt_path = create_delta(eidos_dir, "DLT-2026-091", title="Inline Inv & Pre-Create Precedence")
    dlt_doc = parse_spec_file(dlt_path)
    dlt_doc.frontmatter["mutations"] = [
        {
            "operation": "modify",
            "target": "INV-INLINE-JWT-001",
            "parent": "CMP-AUTH-FILTER",
            "details": "Tighten inline invariant rule.",
        },
        {
            "operation": "modify",
            "target": "POL-PRE-A",
            "details": "Update POL-PRE-A without expect block.",
        },
        {
            "operation": "modify",
            "target": "POL-PRE-B",
            "details": "Update POL-PRE-B with expect block.",
            "expect": {"frontmatter": {"severity": "warning"}},
        },
    ]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")

    # 1. Warm SQLite cache (Bug 7: both expect and non-expect mutations pass against vcs.base_commit)
    ok_warm, msg_warm, rep_warm = verify_delta_mutations(eidos_dir, "DLT-2026-091")
    assert ok_warm is True, msg_warm
    assert [m.status for m in rep_warm.mutations] == ["MODIFIED", "MODIFIED", "MODIFIED"]

    # 2. Cold cache in CI (delete .cache/index.db -> Bug 6: inline component invariant resolves from Git vcs.base_commit)
    db_file = eidos_dir / ".cache" / "index.db"
    if db_file.exists():
        db_file.unlink()
    ok_cold, msg_cold, rep_cold = verify_delta_mutations(eidos_dir, "DLT-2026-091")
    assert ok_cold is True, msg_cold
    assert [m.status for m in rep_cold.mutations] == ["MODIFIED", "MODIFIED", "MODIFIED"]


def test_delta_and_exploration_archive_lifecycle(tmp_path: Path):
    """Verify INV-DELTA-EXPLORATION-ARCHIVE-LEDGER: subdirectory partitioning, .adoc slimming, and cross-ref integrity."""
    import pytest

    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.spec.validator import SpecValidator

    ws = tmp_path / "archive-ws"
    init_repository(ws, name="archive-test")
    eidos_dir = ws / "eidos"
    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")

    # Create 4 terminal deltas (DLT-2026-001 .. DLT-2026-004) and 1 active delta (DLT-2026-005)
    for i in range(1, 5):
        dlt_id = f"DLT-2026-00{i}"
        d_file = create_delta(eidos_dir, dlt_id, title=f"Historical Delta {i}")
        doc = parse_spec_file(d_file)
        distinct_tokens = {
            1: "xylophonearchivedalpha",
            2: "quasararchivedbeta",
            3: "zephyrhotgamma",
            4: "pulsarhotdelta",
        }
        doc.frontmatter["status"] = "applied" if i < 4 else "rejected"
        doc.frontmatter["intent"] = {
            "kind": "feature",
            "motivation": f"Motivation for {dlt_id} {distinct_tokens[i]}.",
        }
        doc.frontmatter["mutations"] = [
            {
                "operation": "modify",
                "target": "DOM-CORE",
                "details": f"Long mutation details for {dlt_id} that should be stripped on archive.",
                "expect": {
                    "body": {"contains": ["Domain"]},
                    "frontmatter": {"status": "active"},
                },
            }
        ]
        body = (
            f"= SpecDelta: Historical Delta {i}\n\n"
            f"== Motivation & Problem Statement\nMotivation for {dlt_id} {distinct_tokens[i]}.\n\n"
            f"== Architectural Design\nVerbose design scaffolding for {dlt_id} that gets slimmed.\n"
        )
        d_file.write_text(dump_spec_document(doc.frontmatter, body), encoding="utf-8")

    active_dlt = create_delta(eidos_dir, "DLT-2026-005", title="Active Proposed Delta")

    # Create 3 terminal explorations (EXP-2026-001 .. EXP-2026-003) and 1 active exploration (EXP-2026-004)
    for i in range(1, 4):
        exp_id = f"EXP-2026-00{i}"
        e_file = create_spec(eidos_dir, "exploration", exp_id, title=f"Spike {i}")
        doc = parse_spec_file(e_file)
        doc.frontmatter["status"] = "adopted" if i < 3 else "discarded"
        doc.frontmatter["decision"] = {
            "outcome": doc.frontmatter["status"],
            "resulting_deltas": [f"DLT-2026-00{i}"],
        }
        body = (
            f"= Exploration: Spike {i}\n\n"
            f"== Core Hypothesis\nWe believe spike {i} improves latency.\n\n"
            f"== Experiment Design & Methodology\nVerbose experiment tables and logs to strip.\n\n"
            f"== Findings & Learnings\n* Observed 3x speedup in spike {i}.\n\n"
            f"== Decision & Next Steps\nGraduated to DLT-2026-00{i}.\n"
        )
        e_file.write_text(dump_spec_document(doc.frontmatter, body), encoding="utf-8")

    active_exp = create_spec(eidos_dir, "exploration", "EXP-2026-004", title="In Progress Spike")

    # Commit and tag v0.1.0 so applied deltas have Git commit and tag ancestry
    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: apply initial deltas"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "tag", "v0.1.0"], cwd=ws, check=True, capture_output=True)

    # 1. Dry-run with keep_recent=2 should preview archiving DLT-2026-001, DLT-2026-002 and EXP-2026-001 without moving files
    dry_res = archive_deltas(eidos_dir, keep_recent=2, include_explorations=True, dry_run=True)
    assert dry_res["dry_run"] is True
    assert dry_res["deltas"]["archived_ids"] == ["DLT-2026-001", "DLT-2026-002"]
    assert dry_res["explorations"]["archived_ids"] == ["EXP-2026-001"]
    assert not (eidos_dir / "deltas" / "archive").exists()
    assert not (eidos_dir / "explorations" / "archive").exists()

    # 2. Execute archive via CLI with --keep-recent 2 --json
    cli_res = runner.invoke(app, ["delta", "archive", "--path", str(eidos_dir), "--keep-recent", "2", "--json"])
    assert cli_res.exit_code == 0, cli_res.output
    payload = orjson.loads(cli_res.output)
    assert payload["deltas"]["archived_count"] == 2
    assert payload["explorations"]["archived_count"] == 1

    # Verify archived files exist and were slimmed
    arch_dlt_1 = eidos_dir / "deltas" / "archive" / "dlt-2026-001.adoc"
    arch_dlt_2 = eidos_dir / "deltas" / "archive" / "dlt-2026-002.adoc"
    arch_exp_1 = eidos_dir / "explorations" / "archive" / "exp-2026-001.adoc"
    assert arch_dlt_1.exists() and arch_dlt_2.exists()
    assert arch_exp_1.exists()
    assert not (eidos_dir / "deltas" / "dlt-2026-001.adoc").exists()
    assert not (eidos_dir / "explorations" / "exp-2026-001.adoc").exists()

    # Verify active and recent terminal artifacts remain in hot directories
    assert active_dlt.exists()
    assert (eidos_dir / "deltas" / "dlt-2026-003.adoc").exists()
    assert (eidos_dir / "deltas" / "dlt-2026-004.adoc").exists()
    assert active_exp.exists()
    assert (eidos_dir / "explorations" / "exp-2026-002.adoc").exists()
    assert (eidos_dir / "explorations" / "exp-2026-003.adoc").exists()

    # Verify slimmed SpecDelta frontmatter & body (no applied_commit / release_tag stamped in frontmatter)
    parsed_arch_dlt = parse_spec_file(arch_dlt_1)
    assert "expect" not in parsed_arch_dlt.frontmatter["mutations"][0]
    assert "details" not in parsed_arch_dlt.frontmatter["mutations"][0]
    assert "applied_commit" not in (parsed_arch_dlt.frontmatter.get("vcs") or {})
    assert "release_tag" not in (parsed_arch_dlt.frontmatter.get("vcs") or {})
    assert "Verbose design scaffolding" not in parsed_arch_dlt.body
    assert "xylophonearchivedalpha" in parsed_arch_dlt.frontmatter["intent"]["motivation"]

    # Verify slimmed Exploration body retains Core Hypothesis and Findings & Learnings
    parsed_arch_exp = parse_spec_file(arch_exp_1)
    assert "== Core Hypothesis" in parsed_arch_exp.body
    assert "== Findings & Learnings" in parsed_arch_exp.body
    assert "Verbose experiment tables" not in parsed_arch_exp.body

    # 3. SpecValidator validates all specs (including archived ones and resulting_deltas cross-references)
    report = SpecValidator().validate_directory(eidos_dir)
    assert report.is_clean, f"Schema errors: {report.schema_errors}, Broken refs: {report.broken_references}"

    # 4. Default list_deltas / list_specs exclude archived items; include_archived=True includes them
    hot_deltas = list_deltas(eidos_dir)
    assert [d["id"] for d in hot_deltas] == ["DLT-2026-003", "DLT-2026-004", "DLT-2026-005"]
    all_deltas = list_deltas(eidos_dir, include_archived=True)
    assert [d["id"] for d in all_deltas] == [
        "DLT-2026-001",
        "DLT-2026-002",
        "DLT-2026-003",
        "DLT-2026-004",
        "DLT-2026-005",
    ]

    hot_exps = list_specs(eidos_dir, type_filter="exploration")
    assert [s["id"] for s in hot_exps] == ["EXP-2026-002", "EXP-2026-003", "EXP-2026-004"]
    all_exps = list_specs(eidos_dir, type_filter="exploration", include_archived=True)
    assert [s["id"] for s in all_exps] == ["EXP-2026-001", "EXP-2026-002", "EXP-2026-003", "EXP-2026-004"]

    # 5. Duplicate ID creation guards check archive/ subdirectories
    disambiguated_dlt_id = generate_collision_free_delta_id(eidos_dir, "DLT-2026-001", title="Duplicate Archived Delta")
    assert disambiguated_dlt_id != "DLT-2026-001"
    assert disambiguated_dlt_id.startswith("DLT-2026-001-")
    with pytest.raises(FileExistsError):
        create_spec(eidos_dir, "exploration", "EXP-2026-001", title="Duplicate Archived Exploration")

    # 6. FTS5 search excludes archived SpecDeltas while retaining hot SpecDeltas, and EXP-2026-001 blast-radius reaches DOM-CORE
    idx = get_architectural_index(eidos_dir=eidos_dir, repo_root=ws)
    idx.sync(force=True)
    fts_arch = idx.query_fts("xylophonearchivedalpha")
    assert [r["id"] for r in fts_arch] == []
    fts_hot = idx.query_fts("zephyrhotgamma")
    assert any(r["id"] == "DLT-2026-003" for r in fts_hot)
    exp1_br = {n["id"] for n in idx.get_k_hop_neighborhood("EXP-2026-001", max_depth=2)}
    assert "DOM-CORE" in exp1_br

    # Verify delta status summary reports archived_count
    st_summary = get_delta_status_summary(eidos_dir)
    assert st_summary["archived_count"] == 2

    # 7. ChangelogGenerator includes archived SpecDeltas resolved purely via Git history (both uncommitted & committed archive move)
    cl_gen = ChangelogGenerator(repo_root=ws, eidos_dir=eidos_dir)
    changelog_md = cl_gen.generate_changelog(include_unreleased=True)
    assert "DLT-2026-001" in changelog_md
    assert "DLT-2026-002" in changelog_md
    assert "DLT-2026-003" in changelog_md
    assert "## [0.1.0]" in changelog_md

    # Commit the archive move on top of v0.1.0 and verify DLT-2026-001/002 STILL resolve to v0.1.0 (not Unreleased)
    subprocess.run(["git", "add", "."], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: archive old deltas"], cwd=ws, check=True, capture_output=True)
    cl_gen_after = ChangelogGenerator(repo_root=ws, eidos_dir=eidos_dir)
    changelog_after = cl_gen_after.generate_changelog(include_unreleased=False)
    assert "## [0.1.0]" in changelog_after
    assert "DLT-2026-001" in changelog_after
    assert "DLT-2026-002" in changelog_after
