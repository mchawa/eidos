"""Tests for the Eidos Living Web Portal, generator, HTTP server, and static site builder."""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.portal.assets import PORTAL_FAVICON_SVG, PORTAL_HTML_TEMPLATE
from eidos.portal.builder import build_static_portal
from eidos.portal.generator import get_portal_data
from eidos.portal.server import run_portal_server

runner = CliRunner()

# Single-file portal bundle budget: 5 MiB uncompressed (DLT-2026-041, INV-PORTAL-AIR-GAPPED-BUNDLE).
PORTAL_BUNDLE_BUDGET_BYTES = 5 * 1024 * 1024

# A remote (http://, https:// or protocol-relative) URL used as a src=/href= attribute, an @import rule, or a CSS
# url(...) reference. Deliberately not a blanket substring check: inert string literals inside the minified JS
# (e.g. https://react.dev/errors/... links) and XML namespace URIs (xmlns="http://www.w3.org/...") must not match.
REMOTE_SUBRESOURCE_RE = re.compile(
    r"""(?:\b(?:src|href)\s*=\s*["']?\s*(?P<attr>(?:https?:)?//[^\s"'>]+))"""
    r"""|(?:@import\s+(?:url\(\s*)?["']?\s*(?P<imp>(?:https?:)?//[^\s"')]+))"""
    r"""|(?:\burl\(\s*["']?\s*(?P<css>(?:https?:)?//[^\s"')]+))""",
    re.IGNORECASE,
)
XML_NAMESPACE_HOSTS = {"www.w3.org"}


def _document_elements(markup: str) -> "_ElementRefCollector":
    collector = _ElementRefCollector()
    collector.feed(markup)
    collector.close()
    return collector


def _external_asset_elements(markup: str) -> list[str]:
    """Real <link rel=stylesheet> / <script src> elements (JS string literals are not elements)."""
    collector = _document_elements(markup)
    found = []
    for tag, attrs in collector.tags:
        if tag == "script" and attrs.get("src"):
            found.append(f"<script src={attrs['src']!r}>")
        if tag == "link" and "stylesheet" in (attrs.get("rel") or "").lower():
            found.append(f"<link rel=stylesheet href={attrs.get('href')!r}>")
    return found


def _remote_subresources(markup: str) -> list[str]:
    found = []
    for match in REMOTE_SUBRESOURCE_RE.finditer(markup):
        url = match.group("attr") or match.group("imp") or match.group("css")
        host = urlparse(url if "//" in url and not url.startswith("//") else f"https:{url}").hostname
        if host in XML_NAMESPACE_HOSTS:
            continue
        found.append(url)
    return found


class _ElementRefCollector(HTMLParser):
    """Collect real <script>/<link>/<img>/... references; JS string literals are CDATA and thus never parsed as tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str, str]] = []
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        for name, value in attrs:
            if name in {"src", "href", "poster", "data", "action", "srcset"} and value:
                self.refs.append((tag, name, value))


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
    assert _external_asset_elements(index_html) == []
    # The embedded bundle (excluding the per-repository data payload injected below) stays inside the single-file
    # size budget. The former 1.5 MB cap predates the embedded Mermaid / Asciidoctor.js / Geist fonts (DLT-2026-041).
    assert len(PORTAL_HTML_TEMPLATE.encode("utf-8")) <= PORTAL_BUNDLE_BUDGET_BYTES
    assert len(index_html) > len(PORTAL_HTML_TEMPLATE)
    # build-site must inject the data payload as its own <script>, not into an inline JS string literal.
    assert index_html.count("window.__EIDOS_DATA__ =") == 1
    assert "</script>\n</head>" in index_html

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
    """Verify 'eidos portal build' CLI command generates static assets and 'eidos build-site' is removed."""
    target_out = tmp_path / "public"
    res = runner.invoke(app, ["portal", "build", "--path", "eidos", "--out", str(target_out)])
    assert res.exit_code == 0
    assert "Successfully generated static portal" in res.stdout
    assert (target_out / "index.html").exists()
    assert (target_out / "data.json").exists()

    res_removed = runner.invoke(app, ["build-site", "--path", "eidos", "--out", str(target_out)])
    assert res_removed.exit_code != 0


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




def test_portal_bundle_has_no_remote_subresources():
    """INV-PORTAL-AIR-GAPPED-BUNDLE: the served/built portal loads no scripts, stylesheets, fonts or other remote subresources."""
    # 1. Structural check: no real element of the document points at a remote origin.
    collector = _document_elements(PORTAL_HTML_TEMPLATE)
    remote_elements = [
        (tag, name, value[:80])
        for tag, name, value in collector.refs
        if re.match(r"(?:https?:)?//", value.strip(), re.IGNORECASE)
    ]
    assert remote_elements == []

    # 2. Attribute / @import / url(...) scan over the whole template, including inline CSS and JS.
    assert _remote_subresources(PORTAL_HTML_TEMPLATE) == []

    # 3. Mermaid, Asciidoctor.js and the Geist fonts are embedded, not linked.
    assert _external_asset_elements(PORTAL_HTML_TEMPLATE) == []
    assert "font/woff2" in PORTAL_HTML_TEMPLATE or "data:font" in PORTAL_HTML_TEMPLATE or "base64" in PORTAL_HTML_TEMPLATE
    assert "@font-face" in PORTAL_HTML_TEMPLATE
    assert "Geist Mono" in PORTAL_HTML_TEMPLATE
    assert "asciidoctor" in PORTAL_HTML_TEMPLATE.lower()
    assert "mermaid" in PORTAL_HTML_TEMPLATE

    # 4. The favicon asset is self-contained too.
    assert _remote_subresources(PORTAL_FAVICON_SVG) == []


def test_remote_subresource_detector_flags_cdn_references():
    """INV-PORTAL-AIR-GAPPED-BUNDLE: the detector catches the original CDN regressions and ignores inert literals."""
    regressions = [
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>',
        '<link href="https://fonts.googleapis.com/css2?family=Geist" rel="stylesheet">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        "<style>@import url('https://fonts.googleapis.com/css2?family=Inter');</style>",
        '<style>@import "//fonts.example.com/x.css";</style>',
        "<style>@font-face{src:url(https://cdn.example.com/geist.woff2)}</style>",
    ]
    for snippet in regressions:
        assert _remote_subresources(snippet), snippet

    inert = [
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\'%3E">',
        "<script>throw Error('see https://react.dev/errors/418 for details')</script>",
        '<link rel="alternate icon" href="/favicon.ico">',
    ]
    for snippet in inert:
        assert _remote_subresources(snippet) == [], snippet


def test_portal_bundle_size_budget():
    """INV-PORTAL-AIR-GAPPED-BUNDLE: the embedded single-file bundle stays within the 5 MiB uncompressed budget."""
    size = len(PORTAL_HTML_TEMPLATE.encode("utf-8"))
    assert size <= PORTAL_BUNDLE_BUDGET_BYTES, f"portal bundle is {size} bytes (budget {PORTAL_BUNDLE_BUDGET_BYTES})"


def test_portal_bundle_head_close_is_unique():
    """CTR-EIDOS-PORTAL: build-site injects data before the first '</head>', so inline JS must never contain one."""
    assert PORTAL_HTML_TEMPLATE.count("</head>") == 1
    assert PORTAL_HTML_TEMPLATE.index("</head>") > PORTAL_HTML_TEMPLATE.index('<script type="module"')
    assert "</head>\n<body" in PORTAL_HTML_TEMPLATE


def test_portal_promoted_components_invariants_decisions_and_explorations(tmp_path: Path):
    """Verify promoted component ref resolution, container/component invariants, decisions, delta affects/blast_radius, and exploration normalization."""
    data = get_portal_data(Path("eidos"))

    # 1. Container responsibility/summary and internal components on CNT-EIDOS-CORE (no bare ref stubs)
    core_container = next(c for c in data["components"] if c["id"] == "CNT-EIDOS-CORE")
    assert core_container.get("responsibility") or core_container.get("summary")
    internal_comps = core_container["internal_components"]
    assert all(ic.get("id") is not None for ic in internal_comps), "No bare {'ref': ...} stub with id=None should remain"
    slicer_entries = [ic for ic in internal_comps if ic.get("id") == "CMP-CONTEXT-SLICER"]
    assert len(slicer_entries) == 1

    # 2. Invariants extracted into data["invariants"]
    inv_ids = {inv["id"] for inv in data["invariants"]}
    assert "INV-PORTAL-ZERO-NODE-DEP" in inv_ids
    assert "INV-LIVING-PORTAL-VISUALIZATION" in inv_ids

    # 3. Architectural decisions extracted into data["decisions"]
    decision_ids = {d["id"] for d in data["decisions"]}
    assert "DEC-SYS-ZERO-AI-SUBSTRATE" in decision_ids
    assert "DEC-ARCH-CENTRALIZED-HYBRID-C4" in decision_ids
    assert len(data["decisions"]) >= 4

    # 4. Deltas preserve affects and blast_radius
    all_deltas = (
        data["deltas"]["proposed"]
        + data["deltas"]["in_review"]
        + data["deltas"]["approved"]
        + data["deltas"]["applied"]
        + data["deltas"]["rejected"]
    )
    dlt_054 = next((d for d in all_deltas if d["id"] == "DLT-2026-054"), None)
    assert dlt_054 is not None
    assert "containers" in dlt_054["affects"]
    assert "impacted_containers" in dlt_054["blast_radius"]

    # 5. Live explorations normalize resulting_deltas and findings
    exp_002 = next((e for e in data["explorations"] if e["id"] == "EXP-2026-002"), None)
    assert exp_002 is not None
    assert exp_002["resulting_deltas"] == ["DLT-2026-011"]
    assert exp_002["decision"]["resulting_deltas"] == ["DLT-2026-011"]
    assert exp_002["decision"]["resulting_delta_id"] == "DLT-2026-011"

    exp_006 = next((e for e in data["explorations"] if e["id"] == "EXP-2026-006"), None)
    assert exp_006 is not None
    assert len(exp_006["findings"]) >= 3
    assert len(exp_006["decision"]["learnings"]) >= 3

    # 6. Synthetic fixture: verify container-level invariants, ref: stub resolution, and top-level resulting_deltas/findings
    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "components").mkdir(parents=True)
    (eidos_dir / "explorations").mkdir(parents=True)

    cnt_inv_id = "INV-" + "CNT-SYNTH-001"
    cmp_inv_id = "INV-" + "CMP-SYNTH-001"
    (eidos_dir / "architecture" / "containers" / "api.adoc").write_text(
        "---\n"
        "id: CNT-TEST-API\n"
        "type: tech/container\n"
        "name: Test API Container\n"
        "responsibility: Serves test traffic.\n"
        "invariants:\n"
        f"  - id: {cnt_inv_id}\n"
        "    rule: Container must validate requests.\n"
        "    test_binding: tests/test_portal.py::test_portal_data_generation\n"
        "components:\n"
        "  - ref: architecture/components/auth.adoc\n"
        "---\n"
        "= Test API\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "auth.adoc").write_text(
        "---\n"
        "id: CMP-TEST-AUTH\n"
        "type: tech/component\n"
        "name: Auth Component\n"
        "parent_container: CNT-TEST-API\n"
        "responsibility: Authenticates callers.\n"
        "source_path: src/eidos/portal/generator.py\n"
        "invariants:\n"
        f"  - id: {cmp_inv_id}\n"
        "    rule: Tokens must be verified.\n"
        "    test_binding: tests/test_portal.py::test_portal_data_generation\n"
        "decisions:\n"
        "  - id: DEC-CMP-TEST-001\n"
        "    title: Stateless Tokens\n"
        "    decision: Use signed tokens.\n"
        "---\n"
        "= Auth Component\n",
        encoding="utf-8",
    )
    (eidos_dir / "explorations" / "exp-test.adoc").write_text(
        "---\n"
        "id: EXP-TEST-006\n"
        "type: product/exploration\n"
        "title: Synthetic Spike\n"
        "status: concluded\n"
        "hypothesis: Hypothesis text.\n"
        "findings:\n"
        "  - Finding A\n"
        "  - Finding B\n"
        "recommendation: Proceed with Delta.\n"
        "resulting_deltas:\n"
        "  - DLT-2026-030\n"
        "  - DLT-2026-031\n"
        "  - DLT-2026-032\n"
        "---\n"
        "= Synthetic Spike\n",
        encoding="utf-8",
    )

    syn_data = get_portal_data(eidos_dir)
    syn_cnt = syn_data["components"][0]
    assert len(syn_cnt["internal_components"]) == 1
    syn_sub = syn_cnt["internal_components"][0]
    assert syn_sub["id"] == "CMP-TEST-AUTH"
    assert syn_sub["is_promoted"] is True
    assert syn_sub["name"] == "Auth Component"
    assert len(syn_sub["invariants"]) == 1
    assert len(syn_sub["decisions"]) == 1

    syn_inv_ids = {i["id"] for i in syn_data["invariants"]}
    assert cnt_inv_id in syn_inv_ids
    assert cmp_inv_id in syn_inv_ids

    syn_dec_ids = {d["id"] for d in syn_data["decisions"]}
    assert "DEC-CMP-TEST-001" in syn_dec_ids

    syn_exp = syn_data["explorations"][0]
    assert syn_exp["findings"] == ["Finding A", "Finding B"]
    assert syn_exp["decision"]["learnings"] == ["Finding A", "Finding B"]
    assert syn_exp["recommendation"] == "Proceed with Delta."
    assert syn_exp["decision"]["outcome"] == "Proceed with Delta."
    assert syn_exp["resulting_deltas"] == ["DLT-2026-030", "DLT-2026-031", "DLT-2026-032"]
    assert syn_exp["decision"]["resulting_deltas"] == ["DLT-2026-030", "DLT-2026-031", "DLT-2026-032"]
    assert syn_exp["decision"]["resulting_delta_id"] == "DLT-2026-030"

