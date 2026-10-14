"""Tests for Eidos PR verification gate, drift detection, and spec reconciliation."""

from pathlib import Path

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.vcs.drift import (
    classify_changed_files,
    detect_drift,
    reconcile_specs,
)

runner = CliRunner()


def test_classify_changed_files():
    """Verify categorization of code, living specs, deltas, tests, and documentation."""
    files = [
        "src/eidos/cli/main.py",
        "packages/billing/src/index.ts",
        "eidos/architecture/containers/cli.adoc",
        "eidos/deltas/dlt-2026-001.adoc",
        "tests/test_drift.py",
        "docs/index.md",
        "pyproject.toml",
    ]
    classification = classify_changed_files(files)

    assert "src/eidos/cli/main.py" in classification.code_files
    assert "packages/billing/src/index.ts" in classification.code_files
    assert "eidos/architecture/containers/cli.adoc" in classification.spec_files
    assert "eidos/deltas/dlt-2026-001.adoc" in classification.delta_files
    assert "tests/test_drift.py" in classification.test_files
    assert "docs/index.md" in classification.doc_files
    assert "pyproject.toml" in classification.other_files
    assert classification.total_changed == 7


def test_drift_detection_code_without_specs(monkeypatch):
    """Verify INV-DRIFT-DETECTION-ACCURACY: drift is detected when code changes without living spec updates."""
    # Mock get_git_diff_files to simulate un-synchronized code edits
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
        ],
    )

    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert result.is_clean is False
    assert result.workflow_mode == "direct"
    assert len(result.remediation_suggestions) > 0
    assert "DRIFT DETECTED" in result.summary_markdown


def test_pr_check_gate_pass_direct(monkeypatch):
    """Verify INV-DRIFT-DETECTION-ACCURACY: gate passes when code and living specs co-evolve together directly."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
            "eidos/architecture/containers/cli.adoc",
        ],
    )

    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.is_clean is True
    assert result.workflow_mode == "direct"
    assert "PASSED" in result.summary_markdown


def test_pr_check_gate_pass_planned(monkeypatch):
    """Verify gate recognizes planned mode when SpecDelta is present in diff."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/core/parser.py",
            "eidos/deltas/dlt-2026-010.adoc",
        ],
    )

    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.workflow_mode == "planned"
    assert "Mode: **Planned (SpecDelta)**" in result.summary_markdown


def test_reconcile_specs_catalog_and_prompt(monkeypatch):
    """Verify reconcile_specs extracts catalog and generates structured AI prompt."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
        ],
    )

    result = reconcile_specs(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert "src/eidos/cli/main.py" in result.changed_code_files
    assert len(result.suggestions) == 1
    assert "CNT-EIDOS-CLI" in result.suggestions[0].candidate_specs[0]
    assert "Instructions for the AI Agent:" in result.ai_prompt
    assert "Think deeply" in result.ai_prompt
    assert len(result.catalog["capabilities"]) > 0


def test_cli_drift_and_reconcile_commands(monkeypatch, tmp_path):
    """Verify CLI drift, reconcile, and sync commands."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
            "eidos/architecture/containers/cli.adoc",
        ],
    )

    # 1. drift passing
    md_file = tmp_path / "drift_summary.md"
    res_drift = runner.invoke(app, ["drift", "--markdown", str(md_file)])
    assert res_drift.exit_code == 0
    assert "Verification Passed!" in res_drift.output
    assert md_file.exists()
    assert "PASSED" in md_file.read_text(encoding="utf-8")

    # 2. drift json output
    res_json = runner.invoke(app, ["drift", "--json"])
    assert res_json.exit_code == 0
    assert '"is_clean": true' in res_json.output

    # 3. check-pr is removed from CLI
    res_removed = runner.invoke(app, ["check-pr"])
    assert res_removed.exit_code != 0

    # 3. reconcile command
    res_rec = runner.invoke(app, ["reconcile"])
    assert res_rec.exit_code == 0
    assert "Code Modifications & Candidate Specs" in res_rec.output

    # 4. reconcile --prompt
    res_prompt = runner.invoke(app, ["reconcile", "--prompt"])
    assert res_prompt.exit_code == 0
    assert "Instructions for the AI Agent:" in res_prompt.output

    # 5. sync alias
    res_sync = runner.invoke(app, ["sync"])
    assert res_sync.exit_code == 0


def test_polyglot_test_file_classification():
    """Verify Initiative D: classify_changed_files recognizes polyglot test files."""
    poly_files = [
        "src/app.ts",
        "packages/auth/src/index.test.ts",
        "packages/auth/src/session.spec.ts",
        "packages/ui/button.test.tsx",
        "packages/ui/card.spec.jsx",
        "pkg/server/server_test.go",
        "crates/core/src/parser_test.rs",
        "tests/integration.rs",
        "src/test/java/UserServiceTest.java",
        "src/test/kotlin/OrderServiceSpec.kt",
        "src/test/kotlin/PaymentServiceTest.kt",
        "tests/test_something.py",
        "src/utils/test_helper.py",
    ]
    classification = classify_changed_files(poly_files)
    assert "src/app.ts" in classification.code_files
    assert len(classification.test_files) == 12
    assert "packages/auth/src/index.test.ts" in classification.test_files
    assert "packages/ui/button.test.tsx" in classification.test_files
    assert "pkg/server/server_test.go" in classification.test_files
    assert "crates/core/src/parser_test.rs" in classification.test_files
    assert "tests/integration.rs" in classification.test_files
    assert "src/test/java/UserServiceTest.java" in classification.test_files
    assert "src/test/kotlin/OrderServiceSpec.kt" in classification.test_files
    assert "tests/test_something.py" in classification.test_files


def test_polyglot_invariant_test_scanning(tmp_path):
    """Verify Initiative D: extract_invariants_and_verify accurately traces invariants across polyglot test suites."""  # eidos:fixture
    from eidos.vcs.drift import extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "polyglot"
    cap_dir.mkdir(parents=True)

    # Capability declaring invariants verified across languages
    (cap_dir / "cap-polyglot.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-POLY-001\n"
        "title: Polyglot Test Coverage\n"
        "domain: DOM-TEST\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Verify polyglot suites\n"
        "technical:\n"
        "  containers: []\n"
        "  invariants:\n"
        "    - id: INV-TS-UNIT\n"
        "      rule: TS unit tests\n"
        "    - id: INV-GO-HANDLER\n"
        "      rule: Go handler tests\n"
        "    - id: INV-RS-INTEGRATION\n"
        "      rule: Rust integration tests\n"
        "    - id: INV-JAVA-SERVICE\n"
        "      rule: Java service tests\n"
        "    - id: INV-KT-SPEC\n"
        "      rule: Kotlin spec tests\n"
        "    - id: INV-UNCOVERED\n"
        "      rule: Should be uncovered\n"
        "---\n"
        "= Cap\n"
    )

    # Create polyglot test suite in tmp_path
    ts_file = tmp_path / "packages" / "client" / "src" / "client.test.ts"
    ts_file.parent.mkdir(parents=True)
    ts_file.write_text("// Test implementation verifying INV-TS-UNIT\n")

    go_file = tmp_path / "cmd" / "server" / "handler_test.go"
    go_file.parent.mkdir(parents=True)
    go_file.write_text("// Test verifying INV-GO-HANDLER\n")

    rs_file = tmp_path / "tests" / "integration.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text("// Test verifying INV-RS-INTEGRATION\n")

    java_file = tmp_path / "src" / "test" / "java" / "OrderServiceTest.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text("// Test verifying INV-JAVA-SERVICE\n")

    kt_file = tmp_path / "src" / "test" / "kotlin" / "OrderSpec.kt"
    kt_file.parent.mkdir(parents=True)
    kt_file.write_text("// Test verifying INV-KT-SPEC\n")

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {inv.invariant_id: inv for inv in invariants}

    assert inv_map["INV-TS-UNIT"].has_test_coverage
    assert "packages/client/src/client.test.ts" in inv_map["INV-TS-UNIT"].matching_tests

    assert inv_map["INV-GO-HANDLER"].has_test_coverage
    assert "cmd/server/handler_test.go" in inv_map["INV-GO-HANDLER"].matching_tests

    assert inv_map["INV-RS-INTEGRATION"].has_test_coverage
    assert "tests/integration.rs" in inv_map["INV-RS-INTEGRATION"].matching_tests

    assert inv_map["INV-JAVA-SERVICE"].has_test_coverage
    assert "src/test/java/OrderServiceTest.java" in inv_map["INV-JAVA-SERVICE"].matching_tests

    assert inv_map["INV-KT-SPEC"].has_test_coverage
    assert "src/test/kotlin/OrderSpec.kt" in inv_map["INV-KT-SPEC"].matching_tests

    assert not inv_map["INV-UNCOVERED"].has_test_coverage
    assert len(inv_map["INV-UNCOVERED"].matching_tests) == 0


def test_is_polyglot_test_file_ancestor_robustness():
    """Verify is_polyglot_test_file does not falsely classify code files when ancestor path has 'test'."""
    from pathlib import Path

    from eidos.vcs.drift import is_polyglot_test_file

    repo = Path("/home/testuser/work/test-project")
    code_file = repo / "src" / "app.py"
    ts_code = repo / "src" / "index.ts"
    go_code = repo / "cmd" / "server.go"
    rs_code = repo / "crates" / "core" / "src" / "lib.rs"
    java_code = repo / "src" / "main" / "java" / "App.java"

    # Should all be recognized as non-tests
    assert not is_polyglot_test_file(code_file, repo_root=repo)
    assert not is_polyglot_test_file(ts_code, repo_root=repo)
    assert not is_polyglot_test_file(go_code, repo_root=repo)
    assert not is_polyglot_test_file(rs_code, repo_root=repo)
    assert not is_polyglot_test_file(java_code, repo_root=repo)

    # Real tests inside the repo should be recognized
    assert is_polyglot_test_file(repo / "tests" / "test_app.py", repo_root=repo)
    assert is_polyglot_test_file(repo / "tests" / "integration.rs", repo_root=repo)
    assert is_polyglot_test_file(repo / "cmd" / "server_test.go", repo_root=repo)
    assert is_polyglot_test_file(repo / "src" / "app.spec.ts", repo_root=repo)


def test_extract_invariants_none_frontmatter(tmp_path):
    """Verify extract_invariants_and_verify does not crash on None or empty technical frontmatter."""
    from eidos.vcs.drift import extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "sub"
    pol_dir = eidos_dir / "policies"
    cap_dir.mkdir(parents=True)
    pol_dir.mkdir(parents=True)

    # Capability with empty technical section
    (cap_dir / "cap-empty.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-EMPTY-001\n"
        "title: Empty Tech\n"
        "domain: DOM-TEST\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Test\n"
        "technical:\n"
        "---\n"
        "= Cap\n"
    )

    # Policy with empty technical section
    (pol_dir / "pol-empty.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-EMPTY-001\n"
        "title: Empty Policy\n"
        "category: testing\n"
        "rule: Rule\n"
        "technical:\n"
        "---\n"
        "= Policy\n"
    )

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    assert isinstance(invariants, list)


def test_no_circular_import():
    """Verify importing eidos.vcs does not cause circular import with eidos.core."""
    import importlib
    import sys

    # Save existing modules to restore after test
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("eidos.") or k == "eidos"}

    try:
        # Evict modules to test clean import order
        for mod in list(sys.modules.keys()):
            if mod.startswith("eidos.") or mod == "eidos":
                del sys.modules[mod]

        vcs = importlib.import_module("eidos.vcs")
        assert hasattr(vcs, "detect_drift")
        assert hasattr(vcs, "reconcile_specs")
    finally:
        sys.modules.update(saved_modules)


def test_is_polyglot_test_file_string_and_ancestor_test():
    """Verify is_polyglot_test_file handles str inputs and ignores ancestor /test/ directory components."""
    from pathlib import Path

    from eidos.vcs.drift import is_polyglot_test_file

    # String input support
    assert is_polyglot_test_file("tests/test_auth.py")
    assert is_polyglot_test_file("src/client.test.ts")
    assert not is_polyglot_test_file("src/client.ts")

    # Ancestor user directory named 'test' should not make code files tests
    assert not is_polyglot_test_file(Path("/home/test/myproject/src/main.py"))
    assert not is_polyglot_test_file(Path("/home/test/myproject/src/index.ts"))


def test_classify_changed_files_repo_named_eidos(tmp_path):
    """Verify repo root named eidos does not falsely classify package.json or setup.py as living specs."""
    from eidos.vcs.drift import classify_changed_files

    repo_dir = tmp_path / "eidos"
    repo_dir.mkdir()

    # Files at root of repo
    pkg_json = repo_dir / "package.json"
    code_file = repo_dir / "src" / "index.js"
    real_spec = repo_dir / "eidos" / "system.adoc"

    classified = classify_changed_files(
        [str(pkg_json), str(code_file), str(real_spec)],
        repo_root=repo_dir,
    )

    assert str(pkg_json) in classified.other_files
    assert str(pkg_json) not in classified.spec_files
    assert str(code_file) in classified.code_files
    assert str(real_spec) in classified.spec_files


def test_classify_changed_files_ancestor_path_named_docs(tmp_path):
    """Verify paths containing 'docs' above the repo root do not classify code files as doc files."""
    from eidos.vcs.drift import classify_changed_files

    repo_dir = tmp_path / "docs" / "myproject"
    repo_dir.mkdir(parents=True)
    code_file = repo_dir / "src" / "app.py"

    classified = classify_changed_files([str(code_file)], repo_root=repo_dir)
    assert str(code_file) in classified.code_files
    assert str(code_file) not in classified.doc_files


def test_design_system_drift_detection(monkeypatch):
    """Verify frontend code or styling modifications require eidos/design/ living spec updates."""
    # 1. Frontend CSS changed without eidos/design updates -> drift detected
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/portal/frontend/src/theme/theme.css",
        ],
    )
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert any("Design System Drift Detected" in r for r in result.remediation_suggestions)

    # 2. Frontend CSS changed WITH tokens.json updated -> clean!
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/portal/frontend/src/theme/theme.css",
            "eidos/design/systems/core/tokens.json",
        ],
    )
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.is_clean is True


def test_uncovered_delta_drift_detection(monkeypatch):
    """Verify that presence of an unrelated SpecDelta does not silence drift for uncovered components."""
    # dlt-2026-001 targets CLI, but portal frontend code was modified without portal/design spec updates
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "eidos/deltas/dlt-2026-001.adoc",
            "src/eidos/portal/frontend/src/theme/theme.css",
        ],
    )
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert result.workflow_mode == "planned"
    assert any("Design System Drift Detected" in r for r in result.remediation_suggestions)


def test_strict_static_invariant_binding_and_orphans(tmp_path):
    """Verify DLT-2026-027: strict file::func existence, exact word-boundary matching, decorator/comment binding, and orphan detection."""  # eidos:fixture
    from eidos.vcs.drift import detect_orphan_invariants, extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "auth"
    cap_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    (cap_dir / "cap-auth.adoc").write_text(
        """---
type: product/capability
id: CAP-AUTH-001
title: Auth Capability
domain: DOM-AUTH
status: active
business:
  personas: []
  jtbd: Authenticate users
technical:
  containers: []
  invariants:
    - id: INV-AUTH-OK
      rule: Must verify auth token
      test_binding: tests/test_auth.py::test_valid_token
    - id: INV-AUTH-MISSING-FUNC
      rule: Points to non-existent function
      test_binding: tests/test_auth.py::test_does_not_exist
    - id: INV-AUTH-UNTAGGED-FUNC
      rule: Points to existing function that lacks invariant tag
      test_binding: tests/test_auth.py::test_untagged_helper
    - id: INV-AUTH-1
      rule: Must not false-match INV-AUTH-10
      test_binding: tests/test_auth.py::test_prefix_collision
---
= Auth Capability
""",
        encoding="utf-8",
    )

    (tests_dir / "test_auth.py").write_text(
        """# Preceding comment binding INV-AUTH-OK
def test_valid_token():
    assert True


def test_untagged_helper():
    assert True


def test_prefix_collision():
    # Mentions INV-AUTH-10 and an orphan INV-ORPHAN-999 only
    assert True
""",
        encoding="utf-8",
    )

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {inv.invariant_id: inv for inv in invariants}

    # 1. Preceding comment above bound function satisfies file::func binding
    assert inv_map["INV-AUTH-OK"].has_test_coverage is True
    assert "tests/test_auth.py::test_valid_token" in inv_map["INV-AUTH-OK"].matching_tests

    # 2. Missing function fails even though tests/test_auth.py exists
    assert inv_map["INV-AUTH-MISSING-FUNC"].has_test_coverage is False

    # 3. Existing function without INV-* tag fails
    assert inv_map["INV-AUTH-UNTAGGED-FUNC"].has_test_coverage is False

    # 4. Exact word-boundary regex prevents INV-AUTH-1 from matching INV-AUTH-10
    assert inv_map["INV-AUTH-1"].has_test_coverage is False

    # 5. Orphan invariant detection catches INV-AUTH-10 and INV-ORPHAN-999
    orphans = detect_orphan_invariants(tmp_path, set(inv_map.keys()))
    orphan_ids = {oid for oid, _ in orphans}
    assert "INV-AUTH-10" in orphan_ids
    assert "INV-ORPHAN-999" in orphan_ids


def test_structural_vs_body_only_drift_detection(monkeypatch):
    """Verify DLT-2026-028: body-only edits inside existing functions do not trigger drift, while structural API/signature changes do."""
    old_code = "def calculate_total(items: list[int]) -> int:\n    return sum(items)\n"
    body_only_new_code = (
        "def calculate_total(items: list[int]) -> int:\n"
        "    # Internal optimization without signature or symbol change\n"
        "    total = 0\n"
        "    for x in items:\n"
        "        total += x\n"
        "    return total\n"
    )
    structural_new_code = (
        "def calculate_total(items: list[int], tax_rate: float = 0.0) -> float:\n"
        "    return float(sum(items)) * (1.0 + tax_rate)\n"
    )

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
        ],
    )

    # 1. Body-only edit -> NO drift!
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False: (
            old_code,
            body_only_new_code,
        ),
    )
    res_body = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_body.drift_detected is False
    assert res_body.is_clean is True
    assert "src/eidos/cli/main.py" in res_body.body_only_code_files

    # 2. Signature change (structural) without spec update -> DRIFT detected!
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False: (
            old_code,
            structural_new_code,
        ),
    )
    res_struct = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_struct.drift_detected is True
    assert res_struct.is_clean is False


def test_scoped_1hop_neighborhood_drift_verification(monkeypatch):
    """Verify DLT-2026-028: touching an unrelated spec does not silence component drift, while a 1-hop linked spec satisfies it."""
    # 1. CLI code changed + unrelated Portal container spec changed -> DRIFT detected!
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
            "eidos/architecture/containers/portal.adoc",
        ],
    )
    res_unrelated = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_unrelated.drift_detected is True
    assert res_unrelated.is_clean is False

    # 2. CLI code changed + 1-hop neighbor contract (eidos/architecture/contracts/cli.adoc) changed -> CLEAN!
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
            "eidos/architecture/contracts/cli.adoc",
        ],
    )
    res_1hop = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_1hop.drift_detected is False
    assert res_1hop.is_clean is True


def test_system_spec_does_not_mask_component_drift(monkeypatch):
    """Verify DLT-2026-030: editing system-level specs cannot silence structural drift inside a mapped container."""
    for system_spec in ("eidos/system.adoc", "eidos/architecture/system-architecture.adoc"):
        monkeypatch.setattr(
            "eidos.vcs.drift.get_git_diff_files",
            lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, _s=system_spec: [
                "src/eidos/cli/main.py",
                _s,
            ],
        )
        result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
        assert result.drift_detected is True, system_spec
        assert result.is_clean is False


THEME = "src/eidos/portal/frontend/src/theme/theme.css"
_OLD_CSS = ":root {\n  --md-sys-color-primary: #6750a4;\n}\n.card {\n  padding: 8px;\n}\n"
_BODY_CSS = ":root {\n  --md-sys-color-primary: #6750a4;\n}\n.card {\n  padding: 12px;\n  gap: 4px;\n}\n"
_TOKEN_CSS = ":root {\n  --md-sys-color-primary: #7c4dff;\n}\n.card {\n  padding: 8px;\n}\n"


def _mock_versions(monkeypatch, new_css):
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False: (_OLD_CSS, new_css),
    )


def _mock_diff(monkeypatch, files):
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: list(files),
    )


def test_body_only_css_tweak_has_no_design_drift(monkeypatch):
    """Verify DLT-2026-030: layout tweaks that do not touch design tokens produce zero drift."""
    _mock_diff(monkeypatch, [THEME])
    _mock_versions(monkeypatch, _BODY_CSS)
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.uncovered_design_files == []
    assert THEME in result.body_only_code_files


def test_graph_scoped_design_token_drift(monkeypatch):
    """Verify DLT-2026-030: token changes are satisfied only by the bound DS-*/CLT-* design subgraph."""
    _mock_versions(monkeypatch, _TOKEN_CSS)

    # 1. Token change without design spec updates -> design drift
    _mock_diff(monkeypatch, [THEME])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.drift_detected is True
    assert THEME in res.uncovered_design_files

    # 2. Token change + bound DS-CORE token manifest -> clean
    _mock_diff(monkeypatch, [THEME, "eidos/design/systems/core/tokens.json"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.drift_detected is False
    assert res.uncovered_design_files == []

    # 3. Token change + UI component of the bound design system -> clean
    _mock_diff(monkeypatch, [THEME, "eidos/design/systems/core/components/portal-reader.adoc"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.uncovered_design_files == []

    # 4. Token change + design spec outside the bound subgraph -> still design drift
    _mock_diff(monkeypatch, [THEME, "eidos/design/flows/cli-serve-and-browse.adoc"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert THEME in res.uncovered_design_files
    assert any("Design System Drift Detected" in r for r in res.remediation_suggestions)


def test_planned_mode_design_target_satisfies_design_drift(monkeypatch):
    """Verify DLT-2026-030: an active SpecDelta declaring the bound design system satisfies design drift."""
    _mock_versions(monkeypatch, _TOKEN_CSS)
    # dlt-2026-019 declares target_design_system: DS-CORE
    _mock_diff(monkeypatch, [THEME, "eidos/deltas/dlt-2026-019.adoc"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.workflow_mode == "planned"
    assert res.uncovered_design_files == []


def test_component_invariants_are_verified(tmp_path):
    """Verify DLT-2026-031: tech/component and inline container component invariants are statically verified."""  # eidos:fixture
    from eidos.vcs.drift import detect_orphan_invariants, extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir.mkdir(parents=True)
    cnts_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    (comps_dir / "billing.adoc").write_text(
        """---
type: tech/component
id: CMP-BILLING
name: Billing
container: CNT-APP
source_path: src/billing
responsibility: Billing
invariants:
  - id: INV-CMP-BILLING-OK
    rule: Covered component invariant
    test_binding: tests/test_billing.py::test_billing_ok
  - id: INV-CMP-BILLING-BROKEN
    rule: Broken binding
    test_binding: tests/test_billing.py::test_missing
---
= Billing
""",
        encoding="utf-8",
    )
    (cnts_dir / "app.adoc").write_text(
        """---
type: tech/container
id: CNT-APP
name: App
package_path: src
components:
  - id: CMP-INLINE
    name: Inline
    source_path: src/inline.py
    invariants:
      - id: INV-CMP-INLINE-OK
        rule: Inline component invariant
        test_binding: tests/test_billing.py::test_inline_ok
---
= App
""",
        encoding="utf-8",
    )
    (tests_dir / "test_billing.py").write_text(
        "# INV-CMP-BILLING-OK\ndef test_billing_ok():\n    assert True\n\n\n"
        "# INV-CMP-INLINE-OK\ndef test_inline_ok():\n    assert True\n",
        encoding="utf-8",
    )

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {inv.invariant_id: inv for inv in invariants}
    assert inv_map["INV-CMP-BILLING-OK"].has_test_coverage is True
    assert inv_map["INV-CMP-BILLING-OK"].spec_id == "CMP-BILLING"
    assert inv_map["INV-CMP-BILLING-BROKEN"].has_test_coverage is False
    assert inv_map["INV-CMP-INLINE-OK"].has_test_coverage is True
    assert inv_map["INV-CMP-INLINE-OK"].spec_id == "CMP-INLINE"

    # Component invariants referenced by tests are no longer reported as orphans
    orphans = {oid for oid, _ in detect_orphan_invariants(tmp_path, set(inv_map))}
    assert "INV-CMP-BILLING-OK" not in orphans
    assert "INV-CMP-INLINE-OK" not in orphans


def test_exact_delta_target_matching_rejects_substring_and_slug_collisions(tmp_path, monkeypatch):
    """Verify A2: delta targets match full entity IDs only (no slug shortening or substring false positives)."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("def run_engine(x: int) -> int:\n    return x\n", encoding="utf-8")

    (cnts_dir / "core.adoc").write_text(
        """---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-EIDOS-CORE
name: Core Engine
package_path: src/core
---
= Core Engine
""",
        encoding="utf-8",
    )
    # Delta targets CMP-SCORE-ENGINE (contains 'core' as substring) and CNT-API (prefix of CNT-API-GATEWAY)
    (deltas_dir / "dlt-2026-099.adoc").write_text(
        """---
type: vcs/spec-delta
schema: schemas/v1/vcs/spec-delta.schema.yaml
id: DLT-2026-099
title: Unrelated Score Engine Delta
status: in_progress
intent:
  kind: feature
  motivation: Modify score engine only.
mutations:
  - action: modify_component
    target_component: CMP-SCORE-ENGINE
    summary: Update score engine
blast_radius:
  components:
    - CMP-SCORE-ENGINE
    - CNT-API
---
= Unrelated Delta
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/core/engine.py",
            "eidos/deltas/dlt-2026-099.adoc",
        ],
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False: (
            "def run_engine() -> int:\n    return 0\n",
            "def run_engine(x: int) -> int:\n    return x\n",
        ),
    )

    res = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    # CNT-EIDOS-CORE must NOT match CMP-SCORE-ENGINE via 'core' slug/substring -> drift detected!
    assert res.drift_detected is True
    assert any("CNT-EIDOS-CORE" in r for r in res.remediation_suggestions)


def test_rejected_and_preexisting_applied_deltas_do_not_activate_planned_mode(tmp_path, monkeypatch):
    """Verify rejected deltas and edits to already-applied deltas do not switch detect_drift into Planned Mode."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("def run_engine(x: int) -> int:\n    return x\n", encoding="utf-8")

    (cnts_dir / "core.adoc").write_text(
        """---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-EIDOS-CORE
name: Core Engine
package_path: src/core
components: []
---
= Core Engine
""",
        encoding="utf-8",
    )
    rejected_delta = """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-101
title: Rejected Core Delta
status: rejected
intent:
  kind: refactor
  motivation: Rejected change.
mutations:
  - action: modify_component
    target_component: CNT-EIDOS-CORE
    details: Touch core
---
= Rejected Delta
"""
    (deltas_dir / "dlt-2026-101.adoc").write_text(rejected_delta, encoding="utf-8")

    applied_old = """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-102
title: Old Applied Delta
status: applied
intent:
  kind: refactor
  motivation: Previously applied.
mutations:
  - action: modify_component
    target_component: CNT-EIDOS-CORE
    details: Touch core
---
= Old Applied
"""
    applied_new = applied_old + "\n// Minor typo fix in historical delta\n"
    (deltas_dir / "dlt-2026-102.adoc").write_text(applied_new, encoding="utf-8")

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/core/engine.py",
            "eidos/deltas/dlt-2026-101.adoc",
            "eidos/deltas/dlt-2026-102.adoc",
        ],
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False: (
            (applied_old, applied_new)
            if "dlt-2026-102" in rel_path
            else ("def run_engine() -> int:\n    return 0\n", "def run_engine(x: int) -> int:\n    return x\n")
        ),
    )

    res = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert res.workflow_mode == "direct"
    assert res.drift_detected is True


def test_path_in_requires_separator_boundary_and_no_slug_fallback(tmp_path):
    """Verify _path_in rejects non-boundary suffix collisions and map_code_to_components does not fallback to slug matching."""
    from eidos.vcs.drift import _path_in, map_code_to_components

    assert _path_in("eidos/architecture/containers/other-core.adoc", {"core.adoc"}) is False
    assert _path_in("eidos/architecture/containers/core.adoc", {"architecture/containers/core.adoc"}) is True

    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    cnts_dir.mkdir(parents=True)
    (cnts_dir / "core.adoc").write_text(
        """---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-EIDOS-CORE
name: Core Engine
package_path: src/core
components: []
---
= Core Engine
""",
        encoding="utf-8",
    )

    mappings = map_code_to_components(eidos_dir, ["src/unrelated/core.py"], repo_root=tmp_path)
    assert mappings == []


