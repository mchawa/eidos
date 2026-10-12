"""Tests for the Eidos Living Web Portal, generator, HTTP server, and static site builder."""

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.portal.builder import build_static_portal
from eidos.portal.generator import get_portal_data
from eidos.portal.server import run_portal_server

runner = CliRunner()


def test_portal_data_generation():
    """Verify portal data model synthesis across living specifications (INV-LIVING-PORTAL-VISUALIZATION)."""
    data = get_portal_data(Path("eidos"))

    ov = data["overview"]
    assert ov["total_specs"] >= 30
    assert ov["valid_specs"] == ov["total_specs"]
    assert ov["is_clean"] is True
    assert ov["components_count"] >= 5
    assert ov["deltas_count"] >= 9

    # Containers verification
    comp_ids = {c["id"] for c in data["components"]}
    assert "CNT-EIDOS-CORE" in comp_ids
    assert "CNT-EIDOS-CLI" in comp_ids
    assert "CNT-EIDOS-MCP" in comp_ids
    assert "CNT-EIDOS-PLUGIN" in comp_ids
    assert "CNT-EIDOS-PORTAL" in comp_ids
    assert "CNT-EIDOS-DOCS" in comp_ids

    # Design systems, UI components, clients, flows, and screens verification
    assert ov["design_systems_count"] >= 1
    assert ov["ui_components_count"] >= 3
    assert ov["screens_count"] >= 6
    assert ov["clients_count"] >= 1
    assert ov["flows_count"] >= 2
    assert ov["explorations_count"] >= 1

    clt_ids = {c["id"] for c in data["clients"]}
    assert "CLT-WEB-PORTAL" in clt_ids

    flow_ids = {f["id"] for f in data["flows"]}
    assert "FLOW-PORTAL-EXPLORATION" in flow_ids
    assert "FLOW-CLI-SERVE-AND-BROWSE" in flow_ids

    uic_ids = {u["id"] for u in data["ui_components"]}
    assert "UIC-PORTAL-SHELL" in uic_ids
    assert "UIC-PORTAL-SIDEBAR" in uic_ids
    assert "UIC-PORTAL-METRIC-CARD" in uic_ids

    scr_ids = {s["id"] for s in data["screens"]}
    assert "SCR-PORTAL-HOME" in scr_ids
    assert "SCR-PORTAL-BUSINESS" in scr_ids
    assert "SCR-PORTAL-DESIGN" in scr_ids
    assert "SCR-PORTAL-ARCHITECTURE" in scr_ids
    assert "SCR-PORTAL-DELTAS" in scr_ids
    assert "SCR-PORTAL-CATALOG" in scr_ids

    # Mermaid diagrams verification
    assert data["diagrams"]["c4_context"].startswith("graph TD")
    assert data["diagrams"]["c4_containers"].startswith("graph TD")
    assert "CNT_EIDOS_CORE" in data["diagrams"]["c4_containers"]
    assert "CNT_EIDOS_PORTAL" in data["diagrams"]["c4_containers"]

    # C4 Level 3 Component Whitebox diagrams verification
    c4_components = data["diagrams"]["c4_components"]
    assert isinstance(c4_components, dict)
    assert len(c4_components) >= 5
    for cid in ["CNT-EIDOS-CORE", "CNT-EIDOS-CLI", "CNT-EIDOS-PORTAL", "CNT-EIDOS-MCP"]:
        assert cid in c4_components
        assert c4_components[cid].startswith("graph TD")
        assert cid.replace("-", "_") in c4_components[cid]

    # Deltas status tracking
    deltas = data["deltas"]
    assert "applied" in deltas
    assert len(deltas["applied"]) >= 8


def test_static_portal_builder(tmp_path: Path):
    """Verify compiling the standalone static portal folder for GitHub/GitLab Pages."""
    out_dir = tmp_path / "docs_site"
    res_dir = build_static_portal(Path("eidos"), out_dir)

    assert res_dir == out_dir
    index_file = out_dir / "index.html"
    data_file = out_dir / "data.json"

    assert index_file.exists()
    assert data_file.exists()

    index_html = index_file.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in index_html
    assert "window.__EIDOS_DATA__ =" in index_html
    assert "SYS-EIDOS-001" in index_html

    # Verify Modernized Single-File React 19 / Tailwind v4 / MD3 SPA in compiled output
    assert '<div id="root"></div>' in index_html
    assert "<script" in index_html
    assert "<style" in index_html
    assert "mermaid" in index_html
    # Self-contained zero-external asset invariants
    assert '<link rel="stylesheet"' not in index_html
    assert '<script src="./' not in index_html
    # Bundle size under 1.5MB uncompressed
    assert len(index_html) < 1_500_000

    with open(data_file, encoding="utf-8") as f:
        json_data = json.load(f)
    assert json_data["overview"]["system_id"] == "SYS-EIDOS-001"
    assert json_data["overview"]["clients_count"] >= 1
    assert json_data["overview"]["flows_count"] >= 2
    assert any(c["id"] == "CLT-WEB-PORTAL" for c in json_data["clients"])
    assert any(f["id"] == "FLOW-PORTAL-EXPLORATION" for f in json_data["flows"])


def test_portal_http_server():
    """Verify local HTTP server endpoints and JSON responses (CTR-EIDOS-PORTAL)."""
    test_port = 8991
    thread = threading.Thread(
        target=run_portal_server,
        args=(Path("eidos"), "127.0.0.1", test_port, False),
        daemon=True,
    )
    thread.start()
    time.sleep(0.4)

    base_url = f"http://127.0.0.1:{test_port}"

    # 1. GET /
    with urllib.request.urlopen(f"{base_url}/") as res:
        assert res.status == 200
        assert "text/html" in res.headers.get("Content-Type", "")
        body = res.read().decode("utf-8")
        assert "Eidos Living Web Portal" in body

    # 2. GET /api/data
    with urllib.request.urlopen(f"{base_url}/api/data") as res:
        assert res.status == 200
        assert "application/json" in res.headers.get("Content-Type", "")
        payload = json.loads(res.read().decode("utf-8"))
        assert payload["overview"]["system_id"] == "SYS-EIDOS-001"

    # 3. GET /api/overview
    with urllib.request.urlopen(f"{base_url}/api/overview") as res:
        assert res.status == 200
        ov = json.loads(res.read().decode("utf-8"))
        assert ov["total_specs"] >= 30

    # 4. GET /api/validate
    with urllib.request.urlopen(f"{base_url}/api/validate") as res:
        assert res.status == 200
        val = json.loads(res.read().decode("utf-8"))
        assert val["is_clean"] is True

    # 5. GET /health
    with urllib.request.urlopen(f"{base_url}/health") as res:
        assert res.status == 200
        h = json.loads(res.read().decode("utf-8"))
        assert h["status"] == "ok"

    # 6. GET /unknown -> 404
    try:
        urllib.request.urlopen(f"{base_url}/unknown-endpoint")
        pytest.fail("Expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_cli_build_site_command(tmp_path: Path):
    """Verify 'eidos build-site' CLI command generates static assets."""
    target_out = tmp_path / "public"
    res = runner.invoke(app, ["build-site", "--path", "eidos", "--out", str(target_out)])
    assert res.exit_code == 0
    assert "Successfully generated static portal" in res.stdout
    assert (target_out / "index.html").exists()
    assert (target_out / "data.json").exists()
