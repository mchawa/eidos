"""Tests for Eidos PR verification gate, drift detection, and spec reconciliation."""

from pathlib import Path

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.vcs.drift import (
    check_pr_drift,
    classify_changed_files,
    reconcile_specs,
)

runner = CliRunner()


def test_classify_changed_files():
    """Verify categorization of code, living specs, deltas, tests, and documentation."""
    files = [
        "src/eidos/cli/main.py",
        "packages/billing/src/index.ts",
        "eidos/architecture/components/cli.adoc",
        "eidos/deltas/dlt-2026-001.adoc",
        "tests/test_drift.py",
        "docs/index.md",
        "pyproject.toml",
    ]
    classification = classify_changed_files(files)

    assert "src/eidos/cli/main.py" in classification.code_files
    assert "packages/billing/src/index.ts" in classification.code_files
    assert "eidos/architecture/components/cli.adoc" in classification.spec_files
    assert "eidos/deltas/dlt-2026-001.adoc" in classification.delta_files
    assert "tests/test_drift.py" in classification.test_files
    assert "docs/index.md" in classification.doc_files
    assert "pyproject.toml" in classification.other_files
    assert classification.total_changed == 7


def test_drift_detection_code_without_specs(monkeypatch):
    """Verify INV-SPEC-CODE-SYNC: drift is detected when code changes without living spec updates."""
    # Mock get_git_diff_files to simulate un-synchronized code edits
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
        ],
    )

    result = check_pr_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert result.is_clean is False
    assert result.workflow_mode == "direct"
    assert len(result.remediation_suggestions) > 0
    assert "DRIFT DETECTED" in result.summary_markdown


def test_pr_check_gate_pass_direct(monkeypatch):
    """Verify INV-DRIFT-GATE: gate passes when code and living specs co-evolve together directly."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
            "eidos/architecture/components/cli.adoc",
        ],
    )

    result = check_pr_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
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

    result = check_pr_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
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
    assert "CMP-EIDOS-CLI" in result.suggestions[0].candidate_specs[0]
    assert "Instructions for the AI Agent:" in result.ai_prompt
    assert "Think deeply" in result.ai_prompt
    assert len(result.catalog["capabilities"]) > 0


def test_cli_check_pr_and_reconcile_commands(monkeypatch, tmp_path):
    """Verify CLI check-pr, drift, reconcile, and sync commands."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False: [
            "src/eidos/cli/main.py",
            "eidos/architecture/components/cli.adoc",
        ],
    )

    # 1. check-pr passing
    md_file = tmp_path / "pr_summary.md"
    res_pr = runner.invoke(app, ["check-pr", "--markdown", str(md_file)])
    assert res_pr.exit_code == 0
    assert "Verification Passed!" in res_pr.output
    assert md_file.exists()
    assert "PASSED" in md_file.read_text(encoding="utf-8")

    # 2. check-pr json output
    res_json = runner.invoke(app, ["check-pr", "--json"])
    assert res_json.exit_code == 0
    assert '"is_clean": true' in res_json.output

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
