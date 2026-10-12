"""Verify machine-readable CLI --json output for AI agents."""

import json

from typer.testing import CliRunner

from eidos.cli.main import app

runner = CliRunner()


def test_cli_validate_json_output():
    """Verify 'eidos validate --json' outputs valid machine-readable JSON."""
    result = runner.invoke(app, ["validate", "eidos", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["is_clean"] is True
    assert data["total_docs"] >= 60
    assert data["valid_docs"] == data["total_docs"]
    assert len(data["broken_references"]) == 0
    assert isinstance(data["documents"], list)
    doc_ids = {d["id"] for d in data["documents"]}
    assert "CNT-EIDOS-CLI" in doc_ids
    assert "CNT-EIDOS-CORE" in doc_ids


def test_cli_drift_json_output():
    """Verify 'eidos drift --uncommitted --json' outputs structured JSON."""
    result = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "drift_detected" in data
    assert "workflow_mode" in data
    assert "valid_specs" in data
    assert "total_specs" in data
    assert isinstance(data["code_files"], list)


def test_cli_context_json_output():
    """Verify 'eidos context -f src/eidos/cli/main.py --format json' outputs valid JSON."""
    result = runner.invoke(app, ["context", "-f", "src/eidos/cli/main.py", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "components" in data
    assert "contracts" in data
    assert "policies" in data
    assert any(c["id"] == "CNT-EIDOS-CLI" for c in data["components"])

