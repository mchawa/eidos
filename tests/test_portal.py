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

    # Components verification
    comp_ids = {c["id"] for c in data["components"]}
    assert "CMP-EIDOS-CORE" in comp_ids
    assert "CMP-EIDOS-CLI" in comp_ids
    assert "CMP-EIDOS-MCP" in comp_ids
    assert "CMP-EIDOS-PLUGIN" in comp_ids
    assert "CMP-EIDOS-PORTAL" in comp_ids
    assert "CMP-EIDOS-DOCS" in comp_ids

    # Design systems, UI components, and screens verification
    assert ov["design_systems_count"] >= 1
    assert ov["ui_components_count"] >= 3
    assert ov["screens_count"] >= 6
    assert ov["explorations_count"] >= 1

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
    assert "CMP_EIDOS_CORE" in data["diagrams"]["c4_containers"]
    assert "CMP_EIDOS_PORTAL" in data["diagrams"]["c4_containers"]

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

    # Verify Sidebar and IA Views in compiled SPA
    assert 'class="sidebar"' in index_html
    assert 'id="sidebarCollapseBtn"' in index_html
    assert "toggleSidebarCollapse" in index_html
    assert "initSidebarState" in index_html
    assert "eidos_sidebar_collapsed" in index_html
    assert 'id="tab-home"' in index_html
    assert 'id="tab-business"' in index_html
    assert 'id="tab-design"' in index_html
    assert 'id="tab-architecture"' in index_html
    assert 'id="tab-deltas"' in index_html
    assert 'id="tab-catalog"' in index_html
    assert 'id="tokenSwatchesGrid"' in index_html
    assert "📊" not in index_html  # Replaced with modern SVG home icon

    # Verify Master-Detail Tree Explorer in Catalog View (SCR-PORTAL-CATALOG)
    assert 'id="catalogTreeLayout"' in index_html
    assert 'id="catalogTreeContainer"' in index_html
    assert 'id="catalogDetailPane"' in index_html
    assert 'id="catalogSearchInput"' in index_html
    assert "buildPathTree" in index_html
    assert "selectCatalogSpec" in index_html
    assert "toggleFolderNode" in index_html
    assert "setCatalogViewMode" in index_html

    with open(data_file, encoding="utf-8") as f:
        json_data = json.load(f)
    assert json_data["overview"]["system_id"] == "SYS-EIDOS-001"


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
