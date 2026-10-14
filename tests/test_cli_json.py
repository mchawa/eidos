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




def _walk_command_tree(cmd, path: list[str]) -> list[dict]:
    """Flatten a click/Typer command tree into JSON-friendly rows, preserving command registration order."""
    import re

    def norm(value):
        if isinstance(value, str):
            return re.sub(r" at 0x[0-9a-f]+", "", value)
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, list | tuple):
            return [norm(v) for v in value]
        if type(value).__name__ == "DefaultPlaceholder":
            return norm(value.value)
        return re.sub(r" at 0x[0-9a-f]+", "", str(value))

    params = [
        {
            "name": p.name,
            "opts": list(p.opts),
            "secondary": list(p.secondary_opts),
            "kind": type(p).__name__,
            "required": p.required,
            "default": str(p.default),
            "help": getattr(p, "help", None),
            "panel": getattr(p, "rich_help_panel", None),
            "envvar": str(getattr(p, "envvar", None)),
            "type": str(p.type),
        }
        for p in cmd.params
    ]
    subs = getattr(cmd, "commands", None) or {}
    rows = [
        {
            "order": list(subs),
            "path": path,
            "help": cmd.help,
            "hidden": cmd.hidden,
            "panel": getattr(cmd, "rich_help_panel", None),
            "params": params,
        }
    ]
    for name, sub in subs.items():
        rows += _walk_command_tree(sub, [*path, name])
    return json.loads(json.dumps(rows, default=norm))


def test_cli_command_tree_unchanged():
    """DLT-2026-038: the recursive Typer command tree (names, order, options, help panels) matches the pre-split snapshot."""
    import re
    from pathlib import Path

    import typer

    snapshot = json.loads(
        (Path(__file__).parent / "fixtures" / "cli_command_tree.json").read_text(encoding="utf-8")
    )
    current = _walk_command_tree(typer.main.get_command(app), [])
    normalized = json.loads(re.sub(r" at 0x[0-9a-f]+", "", json.dumps(current)))
    assert normalized == snapshot
