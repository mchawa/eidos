"""Tests verifying Eidos Model Context Protocol (MCP) tool execution."""

import json

from eidos.mcp.server import TOOLS, handle_tool_call


def test_mcp_tools_declared():
    """Verify that expected MCP tools are declared."""
    tool_names = [t["name"] for t in TOOLS]
    assert "eidos_info" in tool_names
    assert "eidos_validate" in tool_names
    assert "eidos_list_components" in tool_names
    assert "eidos_create_delta" in tool_names
    assert "eidos_read_spec" in tool_names
    assert "eidos_list_specs" in tool_names
    assert "eidos_create_spec" in tool_names
    assert "eidos_delete_spec" in tool_names
    assert "eidos_apply_delta" in tool_names
    assert "eidos_detect_drift" in tool_names
    assert "eidos_reconcile" in tool_names
    assert "eidos_get_task_context" in tool_names
    assert "eidos_context" in tool_names


def test_mcp_detect_drift_and_reconcile_tools():
    """Verify eidos_detect_drift and eidos_reconcile execution via MCP."""
    res_drift = handle_tool_call("eidos_detect_drift", {"path": "eidos"})
    assert len(res_drift) == 1
    data_drift = json.loads(res_drift[0]["text"])
    assert "is_clean" in data_drift
    assert "drift_detected" in data_drift
    assert "workflow_mode" in data_drift
    assert "summary_markdown" in data_drift

    res_rec = handle_tool_call("eidos_reconcile", {"path": "eidos"})
    assert len(res_rec) == 1
    data_rec = json.loads(res_rec[0]["text"])
    assert "changed_code_files" in data_rec
    assert "suggestions" in data_rec
    assert "ai_prompt" in data_rec
    assert "catalog" in data_rec


def test_mcp_info_tool():
    """Verify eidos_info tool returns engine metadata."""
    res = handle_tool_call("eidos_info", {})
    assert len(res) == 1
    data = json.loads(res[0]["text"])
    assert "engine_version" in data
    assert "bundled_schemas" in data
    assert len(data["bundled_schemas"]) >= 10


def test_mcp_validate_tool():
    """Verify eidos_validate tool validates the living specs."""
    res = handle_tool_call("eidos_validate", {"path": "eidos"})
    assert len(res) == 1
    data = json.loads(res[0]["text"])
    assert data["is_clean"] is True
    assert data["total_documents"] > 0
    assert len(data["broken_references"]) == 0


def test_mcp_list_components_tool():
    """Verify eidos_list_components finds C4 components."""
    res = handle_tool_call("eidos_list_components", {"path": "eidos/architecture"})
    assert len(res) == 1
    data = json.loads(res[0]["text"])
    assert len(data) >= 4
    comp_ids = {c["id"] for c in data}
    assert "CMP-EIDOS-CORE" in comp_ids
    assert "CMP-EIDOS-CLI" in comp_ids
    assert "CMP-EIDOS-MCP" in comp_ids
    assert "CMP-EIDOS-PLUGIN" in comp_ids
    assert "CMP-EIDOS-PORTAL" in comp_ids


def test_mcp_read_spec_tool():
    """Verify eidos_read_spec extracts structured frontmatter."""
    res = handle_tool_call("eidos_read_spec", {"path": "eidos/system.adoc"})
    assert len(res) == 1
    data = json.loads(res[0]["text"])
    assert data["id"] == "SYS-EIDOS-001"
    assert data["type"] == "product/system"
    assert "Eidos" in data["title"]
    assert "frontmatter" in data


def test_mcp_create_delta_tool(tmp_path, monkeypatch):
    """Verify eidos_create_delta scaffolds a new SpecDelta."""
    monkeypatch.chdir(tmp_path)
    res = handle_tool_call(
        "eidos_create_delta",
        {
            "delta_id": "DLT-TEST-001",
            "title": "Test Mutation",
            "kind": "feature_addition",
            "motivation": "Testing MCP delta creation",
        },
    )
    assert len(res) == 1
    assert "Created new SpecDelta" in res[0]["text"]

    delta_file = tmp_path / "eidos" / "deltas" / "dlt-test-001.adoc"
    assert delta_file.exists()
    assert (tmp_path / "eidos" / "deltas" / "by-status" / "active" / "dlt-test-001.adoc").exists()
    content = delta_file.read_text(encoding="utf-8")
    assert "DLT-TEST-001" in content
    assert "feature_addition" in content


def test_mcp_context_tool():
    """Verify Initiative G: eidos_get_task_context and eidos_context via MCP."""
    # 1. Markdown mode via eidos_get_task_context
    res_md = handle_tool_call("eidos_get_task_context", {"files": ["src/eidos/cli/main.py"]})
    assert len(res_md) == 1
    assert "CMP-EIDOS-CLI" in res_md[0]["text"]
    assert "C4 Level 2/3 Components" in res_md[0]["text"]

    # 2. JSON mode via alias eidos_context
    res_json = handle_tool_call("eidos_context", {"component": "CMP-EIDOS-CLI", "format": "json"})
    assert len(res_json) == 1
    data = json.loads(res_json[0]["text"])
    assert len(data["components"]) >= 1
    assert any(c["id"] == "CMP-EIDOS-CLI" for c in data["components"])
    assert len(data["contracts"]) >= 1
    assert len(data["capabilities"]) >= 1
    assert len(data["invariants"]) >= 1
    assert len(data["policies"]) >= 1

