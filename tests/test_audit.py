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
  kind: enabler
  motivation: "Testing leak detection"
mutations:
  - operation: remove
    target: CNT-MOCK-DELETED
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
  kind: enabler
  motivation: "Testing tombstone exemptions"
mutations:
  - operation: remove
    target: CNT-DEPRECATED-DB
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


def test_delta_audit_word_bounded_multi_term_polyglot_and_fail_loud(tmp_path, monkeypatch):
    """DLT-2026-062: word-bounded multi-term matching, .gz exclusion, polyglot negative tests, and fail-loud graph check."""
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    delta_content = """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-997
title: "Remove Dual Components"
status: proposed
intent:
  kind: enabler
  motivation: "Test word bounds and multi-term line matching"
mutations:
  - operation: remove
    target: CMP-CLI
  - operation: remove
    target: CMP-LEAKED
tombstone:
  allowed_reasons:
    - path: "src/app.py"
      pattern: "CMP-CLI"
      reason: "Exempt only CMP-CLI on lines matching pattern"
---
= SpecDelta: Remove Dual Components
"""
    (deltas_dir / "dlt-2026-997.adoc").write_text(delta_content, encoding="utf-8")

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    # 1. Substring CMP-CLI-APP must NOT match removed CMP-CLI
    (src_dir / "safe.py").write_text("active = 'CMP-CLI-APP'\n", encoding="utf-8")
    # 2a. Co-occurring terms in src/app.py (which has tombstone pattern "CMP-CLI"):
    #     CMP-CLI must be approved (tombstone_exempt), while CMP-LEAKED on the SAME line must be flagged!
    (src_dir / "app.py").write_text("pair = ('CMP-CLI', 'CMP-LEAKED')\n", encoding="utf-8")
    # 2b. Co-occurring terms in src/other.py (no tombstone): both must be flagged
    (src_dir / "other.py").write_text("pair = ('CMP-CLI', 'CMP-LEAKED')\n", encoding="utf-8")
    # 3. Compressed .gz archive must be skipped
    (src_dir / "bundle.html.gz").write_text("CMP-CLI CMP-LEAKED\n", encoding="utf-8")
    # 4. Co-located TypeScript test file with polyglot negative assertion (.toThrow) must be classified as negative_test
    (src_dir / "client.test.ts").write_text("expect(() => load('CMP-CLI')).toThrow();\n", encoding="utf-8")

    engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
    res = engine.audit_delta("DLT-2026-997")
    assert res.status == "LEAKS_DETECTED"
    # safe.py (CMP-CLI-APP) and bundle.html.gz must NOT be flagged
    flagged_files = {f.file_path for f in res.unacknowledged_findings + res.approved_findings}
    assert "src/safe.py" not in flagged_files
    assert "src/bundle.html.gz" not in flagged_files
    # In src/app.py, CMP-CLI is tombstone_exempt while CMP-LEAKED on the same line is unacknowledged!
    app_unack = {f.matched_term for f in res.unacknowledged_findings if f.file_path == "src/app.py"}
    app_approved = {
        (f.matched_term, f.classification) for f in res.approved_findings if f.file_path == "src/app.py"
    }
    assert app_unack == {"CMP-LEAKED"}
    assert app_approved == {("CMP-CLI", "tombstone_exempt")}
    # Both CMP-CLI and CMP-LEAKED on the same line in src/other.py were evaluated as unacknowledged
    other_terms = {f.matched_term for f in res.unacknowledged_findings if f.file_path == "src/other.py"}
    assert other_terms == {"CMP-CLI", "CMP-LEAKED"}
    # Co-located TypeScript test was classified as negative_test
    assert any(
        f.file_path == "src/client.test.ts" and f.classification == "negative_test" and f.is_approved
        for f in res.approved_findings
    )

    # 5. Fail-loud graph integrity check when get_architectural_index raises RuntimeError
    def _boom(*args, **kwargs):
        raise RuntimeError("Simulated corrupt index.db")

    monkeypatch.setattr("eidos.core.lifecycle.audit.get_architectural_index", _boom)
    err_res = engine.audit_delta("DLT-2026-997")
    assert err_res.status == "ERROR"
    assert err_res.is_clean is False
    assert "Simulated corrupt index.db" in (err_res.error_message or "")


def test_delta_audit_classifications_graph_violation_and_strict_cli(tmp_path):
    """Verify active GraphViolation detection, inline_exempt, historical_ledger, cleanup_routine, active_docs_leak, and --strict CLI exit code."""
    import eidos.core.index.graph_index as gi

    eidos_dir = tmp_path / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    docs_dir = tmp_path / "docs"
    src_dir = tmp_path / "src" / "eidos" / "core" / "lifecycle"
    for d in (cnt_dir, deltas_dir, docs_dir, src_dir):
        d.mkdir(parents=True)

    # Active container CNT-KEEP has a contract-derived dependency edge pointing to CNT-OLD
    (cnt_dir / "cnt-keep.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-KEEP\n"
        "title: Keep Service\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: src/keep\n"
        "contracts:\n"
        "  consumes:\n"
        "    - CTR-OLD\n"
        "---\n"
        "= Keep Service\n\n"
        "Calls xref:cnt-old.adoc[CNT-OLD].\n",
        encoding="utf-8",
    )
    (cnt_dir / "cnt-old.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-OLD\n"
        "title: Old Service\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: src/old\n"
        "contracts:\n"
        "  provides:\n"
        "    - CTR-OLD\n"
        "---\n"
        "= Old Service\n",
        encoding="utf-8",
    )
    (deltas_dir / "dlt-2026-996.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-996\n"
        "title: Remove Old Container\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Retire CNT-OLD\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: CNT-OLD\n"
        "---\n"
        "= Remove Old Container\n",
        encoding="utf-8",
    )
    # Historical ledger reference in another delta
    (deltas_dir / "dlt-2026-001.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-001\n"
        "title: Earlier Delta\n"
        "status: applied\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Introduced CNT-OLD\n"
        "mutations: []\n"
        "---\n"
        "= Earlier Delta mentioning CNT-OLD\n",
        encoding="utf-8",
    )
    # Inline exemption comment in source code
    (tmp_path / "src" / "keep.py").write_text(
        "LEGACY_ID = 'CNT-OLD'  # eidos:allow-retired\n",
        encoding="utf-8",
    )
    # Cleanup routine in src/eidos/core/lifecycle/audit.py
    (src_dir / "audit.py").write_text(
        "def remove_legacy(): return 'CNT-OLD'\n",
        encoding="utf-8",
    )
    # Unacknowledged documentation leak in docs/guide.md
    (docs_dir / "guide.md").write_text(
        "See CNT-OLD architecture details.\n",
        encoding="utf-8",
    )

    try:
        engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
        res = engine.audit_delta("DLT-2026-996")
        assert res.is_clean is False
        assert res.status == "LEAKS_DETECTED"
        assert any(
            v.source_id == "CNT-KEEP" and v.target_id == "CNT-OLD" for v in res.graph_violations
        )
        approved_by_class = {f.classification for f in res.approved_findings}
        assert "tombstone_exempt" in approved_by_class
        assert "historical_ledger" in approved_by_class
        assert "cleanup_routine" in approved_by_class

        unack_by_class = {f.classification for f in res.unacknowledged_findings}
        assert "active_docs_leak" in unack_by_class
        assert "active_spec_leak" in unack_by_class

        # CLI --strict exits with code 1 when leaks/violations are detected
        res_strict = runner.invoke(
            app, ["delta", "audit", "DLT-2026-996", "--path", str(eidos_dir), "--strict"]
        )
        assert res_strict.exit_code == 1

        # CLI on missing delta exits with code 1 and outputs JSON error payload
        res_missing = runner.invoke(
            app, ["delta", "audit", "DLT-2026-404", "--path", str(eidos_dir), "--json"]
        )
        assert res_missing.exit_code == 1
        missing_payload = json.loads(res_missing.stdout)
        assert missing_payload["status"] == "ERROR"
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None


def test_dlt_2026_083_audit_ignores_gitignored_files(tmp_path):
    """Verify DLT-2026-083: AuditEngine._scan_and_classify_references skips gitignored directories."""
    import subprocess

    from eidos.core.lifecycle.audit import AuditEngine
    from eidos.core.lifecycle.initializer import init_repository

    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True, exist_ok=True)
    (deltas_dir / "dlt-2026-001.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-001\n"
        "title: Remove Old Container\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test gitignore exclusion\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: CNT-OLD-LEGACY\n"
        "---\n"
        "= SpecDelta\n",
        encoding="utf-8",
    )

    (tmp_path / ".gitignore").write_text("custom_scratch_build/\n", encoding="utf-8")
    ignored_dir = tmp_path / "custom_scratch_build"
    ignored_dir.mkdir(parents=True, exist_ok=True)
    (ignored_dir / "stale.py").write_text("client = 'CNT-OLD-LEGACY'\n", encoding="utf-8")

    tracked_dir = tmp_path / "src"
    tracked_dir.mkdir(parents=True, exist_ok=True)
    (tracked_dir / "service.py").write_text("client = 'CNT-OLD-LEGACY'\n", encoding="utf-8")

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)

    engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
    result = engine.audit_delta("DLT-2026-001")
    files_scanned = {f.file_path for f in result.unacknowledged_findings}
    assert "src/service.py" in files_scanned
    assert "custom_scratch_build/stale.py" not in files_scanned


def test_dlt_2026_087_audit_composable_remove_and_deprecate_mutations(tmp_path):
    """Verify AuditEngine extracts removed/deprecated targets and parent context from composable (operation + target + parent) mutations."""
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    delta_file = deltas_dir / "dlt-2026-087-audit.adoc"
    delta_file.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-087\n"
        "title: Composable Removal Audit\n"
        "status: applied\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Remove legacy component.\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: CMP-OLD-WORKER\n"
        "    parent: CNT-KEEP-PARENT\n"
        "    details: Remove CMP-OLD-WORKER from CNT-KEEP-PARENT.\n"
        "---\n"
        "= Composable Removal Audit\n",
        encoding="utf-8",
    )

    engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
    res = engine.audit_delta("DLT-2026-087")
    assert res.is_clean
    assert "CMP-OLD-WORKER" in res.targets_audited
    assert "CNT-KEEP-PARENT" not in res.targets_audited


def test_audit_does_not_flag_replacement_entities_in_remove_details(tmp_path):
    """Verify Bug 10: replacement/successor entity IDs mentioned in remove/deprecate details are not flagged as leaks."""
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    src_dir = tmp_path / "src"
    deltas_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    (deltas_dir / "dlt-2026-092.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-092\n"
        "title: Consolidate Legacy Component\n"
        "status: applied\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Consolidate components.\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: CMP-LEGACY-PARSER\n"
        "    parent: CNT-CORE-ENGINE\n"
        "    details: Remove CMP-LEGACY-PARSER; consolidated into CMP-UNIFIED-PARSER and replaced by CMP-FAST-LEXER -> CMP-AST-WALKER.\n"
        "  - operation: modify\n"
        "    target: CMP-UNIFIED-PARSER\n"
        "    parent: CNT-CORE-ENGINE\n"
        "    details: Absorb legacy parser duties.\n"
        "---\n"
        "= Consolidate Legacy Component\n",
        encoding="utf-8",
    )

    # Code references the replacement entities, NOT the removed CMP-LEGACY-PARSER
    (src_dir / "parser.py").write_text(
        "# Owned by CMP-UNIFIED-PARSER, CMP-FAST-LEXER, and CMP-AST-WALKER inside CNT-CORE-ENGINE\n"
        "def parse():\n"
        "    return True\n",
        encoding="utf-8",
    )

    engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
    res = engine.audit_delta("DLT-2026-092")
    assert res.is_clean is True
    assert "CMP-LEGACY-PARSER" in res.targets_audited
    assert "CMP-UNIFIED-PARSER" not in res.targets_audited
    assert "CMP-FAST-LEXER" not in res.targets_audited
    assert "CMP-AST-WALKER" not in res.targets_audited
    assert "CNT-CORE-ENGINE" not in res.targets_audited


def test_a9_audit_path_targets_explorations_rejected_deltas_and_tombstone_patterns(tmp_path):
    """A9: path-based remove targets, explorations & rejected deltas as historical ledgers, and safe tombstone pattern matching."""
    import eidos.core.index.graph_index as gi

    eidos_dir = tmp_path / "eidos"
    cmp_dir = eidos_dir / "architecture" / "components"
    deltas_dir = eidos_dir / "deltas"
    exp_dir = eidos_dir / "explorations"
    src_dir = tmp_path / "src"
    for d in (cmp_dir, deltas_dir, exp_dir, src_dir):
        d.mkdir(parents=True)

    # 1. Exploration spike and rejected delta mentioning removed CMP-OLD-AUTH must be approved as historical_ledger
    (exp_dir / "exp-001.adoc").write_text(
        "---\n"
        "type: product/exploration\n"
        "schema: schemas/v1/product/exploration.schema.yaml\n"
        "id: EXP-001\n"
        "title: Spike Old Auth\n"
        "status: concluded\n"
        "---\n"
        "= Spike mentioning CMP-OLD-AUTH\n",
        encoding="utf-8",
    )
    (deltas_dir / "dlt-2026-010.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-010\n"
        "title: Rejected Old Auth Delta\n"
        "status: rejected\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Mention CMP-OLD-AUTH.\n"
        "mutations: []\n"
        "---\n"
        "= Rejected Delta mentioning CMP-OLD-AUTH\n",
        encoding="utf-8",
    )

    # 2. Delta under audit uses a path target (`eidos/architecture/components/old-auth.adoc`)
    #    and has tombstone patterns with multi-word context and invalid regex syntax (`foo[bar`)
    (deltas_dir / "dlt-2026-099.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-099\n"
        "title: Remove Old Auth via Path\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: removal\n"
        "  motivation: Retire CMP-OLD-AUTH.\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: eidos/architecture/components/old-auth.adoc\n"
        "tombstone:\n"
        "  search_tokens:\n"
        "    - old_auth_helper\n"
        "  allowed_reasons:\n"
        "    - path: src/compat.py\n"
        "      pattern: \"deprecated CMP-OLD-AUTH shim\"\n"
        "      reason: Compat shim line\n"
        "    - path: src/brackets.py\n"
        "      pattern: \"legacy[CMP-OLD-AUTH\"\n"
        "      reason: Unclosed bracket literal pattern\n"
        "---\n"
        "= Remove Old Auth\n",
        encoding="utf-8",
    )

    (src_dir / "compat.py").write_text(
        "# deprecated CMP-OLD-AUTH shim calling old_auth_helper\n",
        encoding="utf-8",
    )
    (src_dir / "brackets.py").write_text(
        "# legacy[CMP-OLD-AUTH marker\n",
        encoding="utf-8",
    )

    try:
        engine = AuditEngine(repo_root=tmp_path, eidos_dir=eidos_dir)
        res = engine.audit_delta("DLT-2026-099")
        assert "CMP-OLD-AUTH" in res.targets_audited
        assert "architecture/components/old-auth.adoc" in res.search_tokens
        assert res.is_clean is True, f"Unacknowledged: {res.unacknowledged_findings}, Graph: {res.graph_violations}"
        approved_files = {f.file_path: f.classification for f in res.approved_findings}
        assert approved_files.get("eidos/explorations/exp-001.adoc") == "historical_ledger"
        assert approved_files.get("eidos/deltas/dlt-2026-010.adoc") == "historical_ledger"
        assert approved_files.get("src/compat.py") == "tombstone_exempt"
        assert approved_files.get("src/brackets.py") == "tombstone_exempt"
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None

