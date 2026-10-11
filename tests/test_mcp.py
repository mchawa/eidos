"""Tests verifying Eidos Model Context Protocol (MCP) tool execution."""

import json
from pathlib import Path
import pytest
from eidos.mcp.server import handle_tool_call, TOOLS


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
    assert len(data) >= 1
    component = data[0]
    assert component["id"] == "CMP-EIDOS-ENGINE"
    assert component["package_path"] == "src/eidos"


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
            "motivation": "Testing MCP delta creation"
        }
    )
    assert len(res) == 1
    assert "Created new SpecDelta" in res[0]["text"]

    delta_file = tmp_path / "eidos" / "deltas" / "active" / "dlt-test-001.adoc"
    assert delta_file.exists()
    content = delta_file.read_text(encoding="utf-8")
    assert "DLT-TEST-001" in content
    assert "feature_addition" in content
