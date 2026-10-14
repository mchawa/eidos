"""Tests verifying SpecDelta Deletion and Deprecation Blast-Radius Audit (INV-DELTA-DELETION-AUDIT)."""

import json

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.lifecycle.audit import AuditEngine, DeltaAuditResult

runner = CliRunner()


def test_delta_deletion_audit_clean():
    """Verify DLT-2026-016 deletion audit passes with zero unacknowledged leaks (INV-DELTA-DELETION-AUDIT)."""
    engine = AuditEngine()
    result = engine.audit_delta("DLT-2026-016")

    assert isinstance(result, DeltaAuditResult)
    assert result.status == "PASSED"
    assert result.is_clean is True
    assert "CNT-EIDOS-MCP" in result.targets_audited
    assert "CTR-EIDOS-MCP" in result.targets_audited
    assert len(result.graph_violations) == 0
    assert len(result.unacknowledged_findings) == 0
    assert len(result.approved_findings) > 0


def test_delta_deletion_audit_no_deletions():
    """Verify deltas without removal/deprecation mutations are gracefully skipped."""
    engine = AuditEngine()
    result = engine.audit_delta("DLT-2026-009")

    assert result.status == "NO_DELETIONS"
    assert result.is_clean is True
    assert len(result.targets_audited) == 0


def test_delta_audit_detects_unacknowledged_leaks(tmp_path):
    """Verify audit engine flags unacknowledged active leaks in specs and code."""
    # Create mock eidos skeleton
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    delta_content = """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-999
title: "Remove Test Container"
status: proposed
intent:
  kind: feature_removal
  motivation: "Testing leak detection"
mutations:
  - action: remove_container
    target_container: CNT-MOCK-DELETED
---
= SpecDelta: Remove Test Container
"""
    (deltas_dir / "dlt-2026-999.adoc").write_text(delta_content, encoding="utf-8")

    # Add an active leak in an active living spec
    specs_dir = eidos_dir / "architecture"
    specs_dir.mkdir(parents=True)
    (specs_dir / "active.adoc").write_text("Reference to CNT-MOCK-DELETED in living spec.\n", encoding="utf-8")

    # Add an active leak in src/
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "service.py").write_text("client = 'CNT-MOCK-DELETED'\n", encoding="utf-8")

    # Add an approved negative test in tests/
    test_dir = tmp_path / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_reg.py").write_text("assert not exists('CNT-MOCK-DELETED')\n", encoding="utf-8")

    engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
    result = engine.audit_delta("DLT-2026-999")

    assert result.is_clean is False
    assert result.status == "LEAKS_DETECTED"
    assert len(result.unacknowledged_findings) == 2

    classifications = {f.classification for f in result.unacknowledged_findings}
    assert "active_spec_leak" in classifications
    assert "active_code_leak" in classifications

    # Negative test is approved
    approved_classes = {f.classification for f in result.approved_findings}
    assert "negative_test" in approved_classes


def test_delta_audit_tombstone_exemption(tmp_path):
    """Verify INV-REMOVAL-TOMBSTONE: declared tombstones in SpecDelta frontmatter approve matching references."""
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    delta_content = """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-998
title: "Decommission with Tombstone"
status: proposed
intent:
  kind: feature_removal
  motivation: "Testing tombstone exemptions"
mutations:
  - action: remove_container
    target_container: CNT-DEPRECATED-DB
tombstone:
  allowed_reasons:
    - path: "src/legacy/migration.py"
      reason: "Permitted one-time data migration script"
---
= SpecDelta: Decommission
"""
    (deltas_dir / "dlt-2026-998.adoc").write_text(delta_content, encoding="utf-8")

    src_dir = tmp_path / "src" / "legacy"
    src_dir.mkdir(parents=True)
    (src_dir / "migration.py").write_text("def migrate(): target = 'CNT-DEPRECATED-DB'\n", encoding="utf-8")

    engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
    result = engine.audit_delta("DLT-2026-998")

    assert result.is_clean is True
    assert result.status == "PASSED"
    assert len(result.unacknowledged_findings) == 0
    assert len(result.approved_findings) >= 1
    assert any(f.classification == "tombstone_exempt" for f in result.approved_findings)


def test_cli_delta_audit():
    """Verify eidos delta audit command formatting and JSON output."""
    res = runner.invoke(app, ["delta", "audit", "DLT-2026-016"])
    assert res.exit_code == 0
    assert "Eidos SpecDelta Deletion Audit: DLT-2026-016" in res.stdout
    assert "Audit Passed" in res.stdout

    # Test --json flag
    res_json = runner.invoke(app, ["delta", "audit", "DLT-2026-016", "--json"])
    assert res_json.exit_code == 0
    payload = json.loads(res_json.stdout)
    assert payload["status"] == "PASSED"
    assert payload["is_clean"] is True
    assert "CNT-EIDOS-MCP" in payload["targets_audited"]

