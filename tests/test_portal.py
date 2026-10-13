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
from eidos.portal.assets import PORTAL_HTML_TEMPLATE
from eidos.portal.builder import build_static_portal
from eidos.portal.generator import get_portal_data
from eidos.portal.server import run_portal_server

runner = CliRunner()


def test_portal_data_generation():
    """Verify portal data model synthesis across living specifications (INV-LIVING-PORTAL-VISUALIZATION, INV-PORTAL-ZERO-NODE-DEP, INV-W3C-DTCG-COMPLIANCE)."""
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
    for cid in ["CNT-EIDOS-CORE", "CNT-EIDOS-CLI", "CNT-EIDOS-PORTAL", "CNT-EIDOS-PLUGIN"]:
        assert cid in c4_components
        assert c4_components[cid].startswith("graph TD")
        assert cid.replace("-", "_") in c4_components[cid]

    # Deltas status tracking
    deltas = data["deltas"]
    assert "applied" in deltas
    assert len(deltas["applied"]) >= 8


def test_static_portal_builder(tmp_path: Path):
    """Verify compiling the standalone static portal folder for GitHub/GitLab Pages (INV-PORTAL-INTERACTIVE-C4)."""
    out_dir = tmp_path / "docs_site"
    res_dir = build_static_portal(Path("eidos"), out_dir)

    assert res_dir == out_dir
    index_file = out_dir / "index.html"
    data_file = out_dir / "data.json"

    assert index_file.exists()
    assert data_file.exists()
    assert (out_dir / "favicon.svg").exists()
    assert "<svg" in (out_dir / "favicon.svg").read_text(encoding="utf-8")

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

    # 6. GET /favicon.svg
    with urllib.request.urlopen(f"{base_url}/favicon.svg") as res:
        assert res.status == 200
        assert "image/svg+xml" in res.headers.get("Content-Type", "")
        fav = res.read().decode("utf-8")
        assert "<svg" in fav
        assert "eidos-fav-grad" in fav

    # 7. GET /favicon.ico
    with urllib.request.urlopen(f"{base_url}/favicon.ico") as res:
        assert res.status == 200
        assert "image/svg+xml" in res.headers.get("Content-Type", "")

    # 8. GET /unknown -> 404
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


def test_cli_portal_subapp_commands(tmp_path: Path):
    """Verify 'eidos portal' sub-app commands (CTR-EIDOS-PORTAL, CTR-EIDOS-CLI)."""
    # 1. portal --help
    res_help = runner.invoke(app, ["portal", "--help"])
    assert res_help.exit_code == 0
    assert "serve" in res_help.stdout
    assert "build" in res_help.stdout

    # 2. portal build
    target_out = tmp_path / "portal_dist"
    res_build = runner.invoke(app, ["portal", "build", "--path", "eidos", "--out", str(target_out)])
    assert res_build.exit_code == 0
    assert "Successfully generated static portal" in res_build.stdout
    assert (target_out / "index.html").exists()
    assert (target_out / "data.json").exists()


def test_portal_reading_ergonomics():
    """Verify reading-friendly design system tokens and prose reading ergonomics (INV-PORTAL-READING-ERGONOMICS)."""
    # 1. Verify W3C tokens in tokens.json
    tokens_path = Path("eidos/design/systems/core/tokens.json")
    with open(tokens_path, encoding="utf-8") as f:
        tokens = json.load(f)

    prose_tokens = tokens["typography"]["prose"]
    assert prose_tokens["body"]["$value"] == "16px"
    assert prose_tokens["line_height"]["$value"] == "1.75"
    assert prose_tokens["measure"]["$value"] == "72ch"
    assert prose_tokens["h1"]["$value"] == "30px"
    assert prose_tokens["h2"]["$value"] == "22px"

    # 2. Verify pre-compiled portal bundle includes reading ergonomics styles
    assert "prose-reading" in PORTAL_HTML_TEMPLATE
    assert "prose-measure" in PORTAL_HTML_TEMPLATE
    assert "spec-heading" in PORTAL_HTML_TEMPLATE


def test_c4_level2_container_hierarchy_and_connections():
    """Verify C4 Level 2 Container Diagram hierarchy, tiers, and inter-container connections (INV-PORTAL-C4-CONTAINER-HIERARCHY)."""
    data = get_portal_data(Path("eidos"))
    c4_containers = data["diagrams"]["c4_containers"]

    # 1. System Boundary and Tiers Hierarchy Verification
    assert "SYS_BOUNDARY" in c4_containers
    assert "System Boundary:" in c4_containers
    assert "SUB_TIER_PRESENTATION" in c4_containers
    assert "Tier 1: Presentation &amp; Interface Layer" in c4_containers
    assert "SUB_TIER_EXTENSIBILITY" in c4_containers
    assert "Tier 2: Extensibility &amp; Agent Integration Layer" in c4_containers
    assert "SUB_TIER_CORE" in c4_containers
    assert "Tier 3: Core Domain &amp; Specification Engine" in c4_containers
    assert "SUB_TIER_PERSISTENCE" in c4_containers
    assert "Tier 4: Knowledge Graph &amp; Persistence Layer" in c4_containers

    # 2. Component Tier Classifications in Data Model
    comps_by_id = {c["id"]: c for c in data["components"]}
    assert comps_by_id["CNT-EIDOS-CLI"]["tier"] == "tier_presentation"
    assert comps_by_id["CNT-EIDOS-PORTAL"]["tier"] == "tier_presentation"
    assert comps_by_id["CNT-EIDOS-DOCS"]["tier"] == "tier_presentation"
    assert comps_by_id["CNT-EIDOS-PLUGIN"]["tier"] == "tier_extensibility"
    assert comps_by_id["CNT-EIDOS-CORE"]["tier"] == "tier_core"
    assert comps_by_id["CNT-EIDOS-INDEX"]["tier"] == "tier_persistence"

    # 3. Explicit Inter-Container Connections Verification
    assert 'CNT_EIDOS_CLI -->|"In-Process Python API"| CNT_EIDOS_CORE' in c4_containers
    assert 'CNT_EIDOS_CLI -->|"Queries &amp; Stores Data"| CNT_EIDOS_INDEX' in c4_containers
    assert 'CNT_EIDOS_CLI -->|"In-Process Python API"| CNT_EIDOS_PLUGIN' in c4_containers
    assert 'CNT_EIDOS_CLI -->|"HTTP JSON • [CTR-EIDOS-PORTAL]"| CNT_EIDOS_PORTAL' in c4_containers
    assert 'CNT_EIDOS_PORTAL -->|"In-Process Python API"| CNT_EIDOS_CORE' in c4_containers
    assert 'CNT_EIDOS_PORTAL -->|"Queries &amp; Stores Data"| CNT_EIDOS_INDEX' in c4_containers
    assert 'CNT_EIDOS_DOCS -->|"HTTP JSON • Consumes [CTR-EIDOS-PORTAL]"| CNT_EIDOS_PORTAL' in c4_containers
    assert 'CNT_EIDOS_PLUGIN -->|"In-Process Python API"| CNT_EIDOS_CORE' in c4_containers
    assert 'CNT_EIDOS_INDEX -->|"In-Process Python API"| CNT_EIDOS_CORE' in c4_containers

    # 4. External Actors & Platform Service Boundaries dynamically derived
    assert 'PER_AUTONOMOUS_AGENT ==>|"Runs CLI Commands"| CNT_EIDOS_CLI' in c4_containers
    assert 'CNT_EIDOS_CORE -.->|"Tracks &amp; Syncs Files"| EXT_GIT_CLI' in c4_containers
    assert 'CNT_EIDOS_INDEX -.->|"Persists Relational Data"| EXT_SQLITE3' in c4_containers
    assert 'CNT_EIDOS_PLUGIN -.->|"Tracks &amp; Syncs Files"| EXT_HOST_IDE_AGENT_CONFIGS' in c4_containers
    assert 'CNT_EIDOS_DOCS -.->|"Tracks &amp; Syncs Files"| EXT_GITHUB_PAGES' in c4_containers
    assert 'CNT_EIDOS_PORTAL -.->|"Serves Web Application"| EXT_WEB_BROWSERS' in c4_containers


