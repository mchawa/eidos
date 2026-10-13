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
    assert any(c["id"] == "CNT-EIDOS-CLI" for c in data["components"])


def test_cli_index_status_json_output():
    """Verify 'eidos index status --json' outputs structured JSON with AST telemetry."""
    result = runner.invoke(app, ["index", "status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "db_path" in data
    assert "schema_version" in data
    assert "total_entities" in data
    assert "total_edges" in data
    assert "total_symbols" in data
    assert "total_indexed_files" in data
    assert "ast" in data
    ast = data["ast"]
    assert "engine" in ast
    assert "engine_class" in ast
    assert "classes" in ast
    assert "functions" in ast
    assert "methods" in ast
    assert "test_bindings" in ast
    assert "symbols_by_kind" in ast
    assert "test_invariant_assertions" in ast
    assert "tested_invariants" in ast


