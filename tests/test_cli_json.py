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


def test_cli_index_blast_radius_json_output():
    """Verify 'eidos index blast-radius <entity> --depth <n> --json' outputs structured JSON."""
    result = runner.invoke(app, ["index", "blast-radius", "CNT-EIDOS-CORE", "--depth", "1", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["entity_id"] == "CNT-EIDOS-CORE"
    assert data["depth"] == 1
    assert data["count"] > 0
    assert isinstance(data["neighbors"], list)
    assert any(n["id"] == "CNT-EIDOS-CORE" for n in data["neighbors"])


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


def test_cli_contract_json_and_create_flags(tmp_path):
    """DLT-2026-077: verify CTR-EIDOS-CLI JSON output flags and creation options."""
    eidos_dir = tmp_path / "eidos"

    # 1. Create container, client (--platform), contract (--spec-file), and delta (--branch)
    res_cnt = runner.invoke(
        app,
        ["spec", "create", "container", "CNT-API", "--title", "API Service", "--path", str(eidos_dir)],
    )
    assert res_cnt.exit_code == 0

    res_clt = runner.invoke(
        app,
        [
            "spec",
            "create",
            "client",
            "CLT-MOBILE",
            "--title",
            "Mobile Client",
            "--container",
            "CNT-API",
            "--platform",
            "mobile-ios",
            "--path",
            str(eidos_dir),
        ],
    )
    assert res_clt.exit_code == 0
    clt_content = (eidos_dir / "design" / "clients" / "mobile" / "client.adoc").read_text(encoding="utf-8")
    assert "platform: mobile-ios" in clt_content

    res_ctr = runner.invoke(
        app,
        [
            "spec",
            "create",
            "contract",
            "CTR-OPENAPI",
            "--title",
            "OpenAPI Spec",
            "--container",
            "CNT-API",
            "--spec-file",
            "api/openapi.yaml",
            "--path",
            str(eidos_dir),
        ],
    )
    assert res_ctr.exit_code == 0
    ctr_content = (eidos_dir / "architecture" / "contracts" / "ctr-openapi.adoc").read_text(encoding="utf-8")
    assert "specification_file: api/openapi.yaml" in ctr_content

    res_dlt = runner.invoke(
        app,
        [
            "delta",
            "create",
            "DLT-2026-901",
            "--title",
            "Branch Test Delta",
            "--branch",
            "feat/dlt-901",
            "--path",
            str(eidos_dir),
        ],
    )
    assert res_dlt.exit_code == 0
    dlt_content = (eidos_dir / "deltas" / "dlt-2026-901.adoc").read_text(encoding="utf-8")
    assert "branch: feat/dlt-901" in dlt_content

    # 2. spec list --json
    res_spec_list = runner.invoke(app, ["spec", "list", "--path", str(eidos_dir), "--json"])
    assert res_spec_list.exit_code == 0
    spec_data = json.loads(res_spec_list.stdout)
    assert spec_data["count"] == 4
    assert {s["id"] for s in spec_data["specs"]} == {"CNT-API", "CLT-MOBILE", "CTR-OPENAPI", "DLT-2026-901"}

    # 3. delta list --json
    res_delta_list = runner.invoke(app, ["delta", "list", "--path", str(eidos_dir), "--json"])
    assert res_delta_list.exit_code == 0
    delta_list_data = json.loads(res_delta_list.stdout)
    assert delta_list_data["count"] == 1
    assert delta_list_data["deltas"][0]["id"] == "DLT-2026-901"
    assert delta_list_data["deltas"][0]["status"] == "proposed"

    # 4. delta status <ID> <STATUS> --json and dashboard --json
    res_set_status = runner.invoke(
        app,
        ["delta", "status", "DLT-2026-901", "in_review", "--path", str(eidos_dir), "--json"],
    )
    assert res_set_status.exit_code == 0
    set_status_data = json.loads(res_set_status.stdout)
    assert set_status_data["success"] is True
    assert set_status_data["status"] == "in_review"

    res_status_dash = runner.invoke(app, ["delta", "status", "--path", str(eidos_dir), "--json"])
    assert res_status_dash.exit_code == 0
    dash_data = json.loads(res_status_dash.stdout)
    assert dash_data["active_count"] == 1
    assert len(dash_data["by_status"]["in_review"]) == 1

    # 5. index search --json
    runner.invoke(app, ["index", "sync", "--path", str(eidos_dir)])
    res_search = runner.invoke(app, ["index", "search", "OpenAPI", "--path", str(eidos_dir), "--json"])
    assert res_search.exit_code == 0
    search_data = json.loads(res_search.stdout)
    assert search_data["query"] == "OpenAPI"
    assert search_data["count"] >= 1
    assert any(r["id"] == "CTR-OPENAPI" for r in search_data["results"])

