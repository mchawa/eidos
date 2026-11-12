"""Tests for the Eidos Living Web Portal, generator, HTTP server, and static site builder."""

import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import orjson
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
    assert '<div id="root"></div>' in index_html
    assert len(index_file.read_bytes()) < 400 * 1024
    assert _remote_subresources(index_html) == []
    assert list((out_dir / "assets").glob("portal-*.js"))
    assert list((out_dir / "assets").glob("portal-*.css"))
    assert len(list((out_dir / "specs").glob("*.json"))) >= 30
    assert index_html.count("window.__EIDOS_DATA__ =") == 1
    assert "</script>\n</head>" in index_html

    # Verify opt-in --single-file mode preserves self-contained single-HTML output for file:// archives
    single_dir = tmp_path / "single_file_site"
    build_static_portal(Path("eidos"), single_dir, single_file=True)
    single_html = (single_dir / "index.html").read_text(encoding="utf-8")
    assert '<div id="root"></div>' in single_html
    assert "<script" in single_html
    assert "<style" in single_html
    assert "mermaid" in single_html
    assert _external_asset_elements(single_html) == []
    assert len(PORTAL_HTML_TEMPLATE.encode("utf-8")) <= PORTAL_BUNDLE_BUDGET_BYTES
    assert len(single_html) > len(PORTAL_HTML_TEMPLATE)
    assert not (single_dir / "specs").exists()

    json_data = orjson.loads(data_file.read_bytes())
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
        payload = orjson.loads(res.read())
        assert payload["overview"]["system_id"] == "SYS-EIDOS-001"

    # 3. GET /api/overview
    with urllib.request.urlopen(f"{base_url}/api/overview") as res:
        assert res.status == 200
        ov = orjson.loads(res.read())
        assert ov["total_specs"] >= 30

    # 4. GET /api/validate
    with urllib.request.urlopen(f"{base_url}/api/validate") as res:
        assert res.status == 200
        val = orjson.loads(res.read())
        assert val["is_clean"] is True

    # 5. GET /health
    with urllib.request.urlopen(f"{base_url}/health") as res:
        assert res.status == 200
        h = orjson.loads(res.read())
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
        exc.close()


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
    tokens = orjson.loads(tokens_path.read_bytes())

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

    # 4. External Actors & System Context Boundaries dynamically derived
    assert 'PER_AUTONOMOUS_AGENT ==>|"Runs CLI Commands"| CNT_EIDOS_CLI' in c4_containers
    c4_context = data["diagrams"]["c4_context"]
    assert "Git CLI / Subprocess" in c4_context
    assert "CLI Subshell / JSON" in c4_context


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
    assert (
        "font/woff2" in PORTAL_HTML_TEMPLATE or "data:font" in PORTAL_HTML_TEMPLATE or "base64" in PORTAL_HTML_TEMPLATE
    )
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
    """Verify promoted component resolution, container/component invariants, decisions, delta affects/blast_radius, and exploration normalization."""
    data = get_portal_data(Path("eidos"))

    # 1. Container responsibility/summary and internal components on CNT-EIDOS-CORE (no bare ref stubs)
    core_container = next(c for c in data["components"] if c["id"] == "CNT-EIDOS-CORE")
    assert core_container.get("responsibility") or core_container.get("summary")
    internal_comps = core_container["internal_components"]
    assert all(ic.get("id") is not None for ic in internal_comps), (
        "No bare {'ref': ...} stub with id=None should remain"
    )
    parser_entries = [ic for ic in internal_comps if ic.get("id") == "CMP-SPEC-PARSER"]
    assert len(parser_entries) == 1

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

    exp_006 = next((e for e in data["explorations"] if e["id"] == "EXP-2026-006"), None)
    assert exp_006 is not None
    assert exp_006["status"] == "adopted"
    assert exp_006["resulting_deltas"] == ["DLT-2026-047"]

    # 6. Synthetic fixture: verify container-level invariants, bottom-up promoted component attachment, and top-level resulting_deltas/findings
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
        "title: Test API Container\n"
        "container_type: service\n"
        "runtime: python\n"
        "invariants:\n"
        f"  - id: {cnt_inv_id}\n"
        "    rule: Container must validate requests.\n"
        "    test_binding: tests/test_portal.py::test_portal_data_generation\n"
        "---\n"
        "= Test API\n\n"
        "== Architectural Boundary\n"
        "Serves test traffic.\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "auth.adoc").write_text(
        "---\n"
        "id: CMP-TEST-AUTH\n"
        "type: tech/component\n"
        "title: Auth Component\n"
        "container: CNT-TEST-API\n"
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
        "= Auth Component\n\n"
        "== Responsibility\n"
        "Authenticates callers.\n",
        encoding="utf-8",
    )
    (eidos_dir / "explorations" / "exp-test.adoc").write_text(
        "---\n"
        "id: EXP-TEST-006\n"
        "type: product/exploration\n"
        "title: Synthetic Spike\n"
        "status: adopted\n"
        "decision:\n"
        "  outcome: adopted\n"
        "  resulting_deltas:\n"
        "    - DLT-2026-030\n"
        "    - DLT-2026-031\n"
        "    - DLT-2026-032\n"
        "---\n"
        "= Synthetic Spike\n\n"
        "== Core Hypothesis\n"
        "Validate synthetic spike hypothesis.\n\n"
        "== Findings & Learnings\n"
        "Finding A and Finding B confirmed.\n",
        encoding="utf-8",
    )

    syn_data = get_portal_data(eidos_dir)
    syn_cnt = syn_data["components"][0]
    assert syn_cnt["responsibility"] == "Serves test traffic."
    assert len(syn_cnt["internal_components"]) == 1
    syn_sub = syn_cnt["internal_components"][0]
    assert syn_sub["id"] == "CMP-TEST-AUTH"
    assert syn_sub["responsibility"] == "Authenticates callers."
    assert syn_sub["title"] == "Auth Component"
    assert len(syn_sub["invariants"]) == 1
    assert len(syn_sub["decisions"]) == 1

    syn_inv_ids = {i["id"] for i in syn_data["invariants"]}
    assert cnt_inv_id in syn_inv_ids
    assert cmp_inv_id in syn_inv_ids

    syn_dec_ids = {d["id"] for d in syn_data["decisions"]}
    assert "DEC-CMP-TEST-001" in syn_dec_ids

    syn_exp = syn_data["explorations"][0]
    assert syn_exp["hypothesis"] == "Validate synthetic spike hypothesis."
    assert syn_exp["findings"] == "Finding A and Finding B confirmed."
    assert syn_exp["decision"]["learnings"] == "Finding A and Finding B confirmed."
    assert syn_exp["decision"]["outcome"] == "adopted"
    assert syn_exp["resulting_deltas"] == ["DLT-2026-030", "DLT-2026-031", "DLT-2026-032"]
    assert syn_exp["decision"]["resulting_deltas"] == ["DLT-2026-030", "DLT-2026-031", "DLT-2026-032"]


def test_dlt_2026_063_hook_digest_short_circuit_and_portal_mtime_cache(tmp_path, monkeypatch, capsys):
    """Verify stateless Stop hook execution (DLT-2026-094) and EidosPortalHandler mtime response caching (DLT-2026-063)."""
    from eidos.plugin.hooks import execute_hook
    from eidos.portal.server import EidosPortalHandler

    eidos_dir = tmp_path / "eidos"
    pol_dir = eidos_dir / "architecture" / "containers"
    pol_dir.mkdir(parents=True)
    pol_file = pol_dir / "cnt-001.adoc"
    pol_file.write_text(
        "---\ntype: tech/container\nid: CNT-APP\ntitle: App\ncontainer_type: service\nruntime: python\nstatus: active\nsource_path: src\n---\n= App\n",
        encoding="utf-8",
    )

    # 1. Clean workspace returns {"decision": "allow"}
    rc = execute_hook(repo_root=tmp_path, stdin_data="{}")
    assert rc == 0
    assert orjson.loads(capsys.readouterr().out) == {"decision": "allow"}

    # 2. EidosPortalHandler caches _get_cached_portal_data until a spec file changes
    calls = {"count": 0}
    orig_get_portal_data = get_portal_data

    def _counted_get_portal_data(d):
        calls["count"] += 1
        return orig_get_portal_data(d)

    monkeypatch.setattr("eidos.portal.server.get_portal_data", _counted_get_portal_data)
    dummy = EidosPortalHandler.__new__(EidosPortalHandler)
    dummy.eidos_dir = eidos_dir
    EidosPortalHandler._cache_key = None
    EidosPortalHandler._cached_portal_data = None

    d1 = dummy._get_cached_portal_data()
    d2 = dummy._get_cached_portal_data()
    assert calls["count"] == 1
    assert d1 is d2

    pol_file.write_text(pol_file.read_text(encoding="utf-8") + "\nUpdated prose.\n", encoding="utf-8")
    d3 = dummy._get_cached_portal_data()
    assert calls["count"] == 2
    assert d3 is not d1


def test_dlt_2026_069_static_portal_builder_escapes_script_tags(tmp_path):
    """Verify DLT-2026-069: build_static_portal escapes '</script>' and U+2028/U+2029 in inline window.__EIDOS_DATA__."""
    eidos_dir = tmp_path / "eidos"
    pol_dir = eidos_dir / "policies"
    pol_dir.mkdir(parents=True)
    (pol_dir / "pol-xss.adoc").write_text(
        "---\ntype: product/policy\nid: POL-XSS\ntitle: Script Escape\ncategory: security\nrule: Escape </script>\nenforcement:\n  mechanism: test\n  severity: blocking\n---\n= Script Escape\n\nBody with <script>alert(1)</script> and \u2028\u2029.\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "site"
    build_static_portal(eidos_dir, out_dir)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "window.__EIDOS_DATA__ =" in html
    assert "<script>alert(1)</script>" not in html
    assert "<\\/script>" in html


def test_dlt_2026_074_portal_genericity_and_complete_extraction(tmp_path):
    """Verify DLT-2026-074: product/system & tech/architecture support, nested policy invariants, product DEC-* decisions, tokens.manifest & extends, dynamic external_systems C4 context, and dict internal_containers # eidos:fixture."""
    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "policies").mkdir(parents=True)
    (eidos_dir / "domains" / "billing" / "capabilities").mkdir(parents=True)
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "design" / "systems" / "brand").mkdir(parents=True)

    (eidos_dir / "system.adoc").write_text(
        """---
type: product/system
id: SYS-ACME
title: Acme Cloud
---
= Acme Cloud
""",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "system-architecture.adoc").write_text(
        """---
type: tech/architecture
id: ARCH-ACME
title: Acme Cloud Architecture
c4_context:
  actors: []
  external_systems:
    - name: Stripe Gateway
      role: Processes card charges
      protocol: external_saas
---
= Acme Cloud Architecture
""",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "pol-sec.adoc").write_text(
        """---
type: product/policy
id: POL-SEC-01
title: Security Policy
category: security
invariants:
  - id: INV-POL-NESTED-01
    rule: All traffic uses TLS 1.3
    test_binding: tests/test_sec.py::test_tls
decisions:
  - id: DEC-POL-01
    title: Mandate TLS 1.3
    decision: Use TLS 1.3 everywhere
    rationale: Modern cipher suites
---
= Security Policy
""",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "billing" / "domain.adoc").write_text(
        """---
type: product/domain
id: DOM-BILLING
title: Billing Domain
decisions:
  - id: DEC-DOM-01
    title: Ledger Double Entry
    decision: Use immutable double-entry ledger
    rationale: Auditability
---
= Billing Domain

== Domain Vision
Manage subscriptions
""",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "billing" / "capabilities" / "invoicing.adoc").write_text(
        """---
type: product/capability
id: CAP-BILL-01
title: Invoicing
domain: DOM-BILLING
decisions:
  - id: DEC-CAP-01
    title: PDF Generation
    decision: Render invoices asynchronously
    rationale: Avoid blocking checkout
---
= Invoicing
""",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "db.adoc").write_text(
        """---
type: tech/container
id: CNT-DB
title: Database Service
container_type: database
runtime: PostgreSQL
contracts:
  provides:
    - CTR-DB
---
= Database Service
""",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "api.adoc").write_text(
        """---
type: tech/container
id: CNT-API
title: API Service
container_type: service
runtime: Go
source_path: cmd/api
contracts:
  consumes:
    - CTR-DB
---
= API Service
""",
        encoding="utf-8",
    )
    (eidos_dir / "design" / "systems" / "brand" / "tokens.json").write_text(
        '{"color": {"brand": {"primary": {"$value": "#ff0055", "$type": "color"}}}}',
        encoding="utf-8",
    )
    (eidos_dir / "design" / "systems" / "brand" / "system.adoc").write_text(
        """---
type: design/system
id: DS-BRAND
title: Brand System
extends: DS-BASE
tokens:
  manifest: eidos/design/systems/brand/tokens.json
---
= Brand System
""",
        encoding="utf-8",
    )

    pdata = get_portal_data(eidos_dir)
    assert pdata["overview"]["system_id"] == "SYS-ACME"
    assert pdata["overview"]["system_title"] == "Acme Cloud"

    inv_ids = {i["id"] for i in pdata["invariants"]}
    assert "INV-POL-NESTED-01" in inv_ids

    dec_ids = {d["id"] for d in pdata["decisions"]}
    assert {"DEC-POL-01", "DEC-DOM-01", "DEC-CAP-01"}.issubset(dec_ids)

    ds = pdata["design_systems"][0]
    assert ds["id"] == "DS-BRAND"
    assert ds["extends"] == "DS-BASE"
    assert ds["tokens"]["color"]["brand"]["primary"]["$value"] == "#ff0055"

    c4_ctx = pdata["diagrams"]["c4_context"]
    assert "Stripe Gateway" in c4_ctx
    assert "Processes card charges" in c4_ctx
    assert "EXT_GIT" not in c4_ctx
    assert "EXT_IDE" not in c4_ctx

    c4_cnt = pdata["diagrams"]["c4_components"]["CNT-API"]
    assert "peer_CNT_DB" in c4_cnt


def test_portal_zero_node_subprocess_http_endpoints_mermaid_and_build_assets(tmp_path, monkeypatch):
    """Verify zero node/npm subprocess invocations, live HTTP /api/health, /api/validate cache invalidation,
    query string stripping, and path traversal rejection."""  # eidos:fixture
    import subprocess
    import threading
    import urllib.error
    import urllib.request
    from http.server import HTTPServer

    import eidos.core.index.graph_index as gi
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.portal.builder import build_static_portal
    from eidos.portal.generator import get_portal_data
    from eidos.portal.server import EidosPortalHandler

    repo = tmp_path / "portal_ws"
    init_repository(repo, title="Portal Test System")
    eidos_dir = repo / "eidos"
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    cnt_file = create_spec(eidos_dir, "container", "CNT-BACKEND", "Backend Service")
    cnt_doc = parse_spec_file(cnt_file)
    cnt_doc.frontmatter["source_path"] = "src/backend"
    cnt_file.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cmp_file = create_spec(eidos_dir, "component", "CMP-AUTH", "Auth Module")
    cmp_doc = parse_spec_file(cmp_file)
    cmp_doc.frontmatter["source_path"] = "src/backend/auth.py"
    cmp_file.write_text(dump_spec_document(cmp_doc.frontmatter, cmp_doc.body), encoding="utf-8")

    # 1. Intercept subprocess.run to prove zero node/npm/npx/vite calls during get_portal_data & build_static_portal
    orig_run = subprocess.run
    forbidden_bins = {"node", "npm", "npx", "pnpm", "yarn", "vite", "tsc"}

    def _guarded_run(cmd, *args, **kwargs):
        first = str(cmd[0]).lower() if isinstance(cmd, list | tuple) and cmd else str(cmd).lower()
        assert all(b not in first for b in forbidden_bins), f"Forbidden Node toolchain subprocess invoked: {cmd}"
        return orig_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)

    try:
        pdata = get_portal_data(eidos_dir)
        assert "c4_context" in pdata["diagrams"] and pdata["diagrams"]["c4_context"]
        assert "c4_containers" in pdata["diagrams"] and pdata["diagrams"]["c4_containers"]
        assert "CNT-BACKEND" in pdata["diagrams"]["c4_components"]

        out_dir = tmp_path / "static_site"
        res_build = build_static_portal(eidos_dir, out_dir)
        assert res_build == out_dir.resolve()
        assert (out_dir / "index.html").exists()
        assert (out_dir / "favicon.svg").exists()
        built_data = orjson.loads((out_dir / "data.json").read_bytes())
        assert built_data["diagrams"]["c4_containers"]

        # 2. Live HTTP server endpoints: /api/health, /api/data?foo=1, /api/validate (cache hit + invalidation), 404 traversal
        EidosPortalHandler.eidos_dir = eidos_dir
        EidosPortalHandler._cache_key = None
        EidosPortalHandler._cached_portal_data = None
        EidosPortalHandler._cached_validation_key = None
        EidosPortalHandler._cached_validation_res = None

        httpd = HTTPServer(("127.0.0.1", 0), EidosPortalHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health") as resp:
                assert resp.status == 200
                health = orjson.loads(resp.read())
                assert health["status"] == "ok"

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data?cache_bust=123") as resp:
                assert resp.status == 200
                api_data = orjson.loads(resp.read())
                assert api_data["overview"]["total_specs"] >= 2

            # First /api/validate populates cache; second hits cache
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/validate") as resp:
                assert resp.status == 200
                val1 = orjson.loads(resp.read())
                assert "is_clean" in val1
            assert EidosPortalHandler._cached_validation_res is not None
            first_key = EidosPortalHandler._cached_validation_key
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/validate") as resp:
                assert resp.status == 200
            assert EidosPortalHandler._cached_validation_key == first_key

            # Modify spec file to invalidate _cached_validation_key
            cnt_file.write_text(cnt_file.read_text(encoding="utf-8") + "\n// updated\n", encoding="utf-8")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/validate") as resp:
                assert resp.status == 200
            assert EidosPortalHandler._cached_validation_key != first_key

            # Unknown/traversal path returns 404
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/../../etc/passwd")
            assert exc_info.value.code == 404
            exc_info.value.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None


def test_portal_mermaid_diagram_generators_and_asset_fallbacks(tmp_path, monkeypatch):
    """Cover portal/generator.py Mermaid state/sequence/journey helpers, C4 container/component edge cases,
    and portal/assets.py fallback branches."""  # eidos:fixture
    import eidos.portal.assets as portal_assets
    from eidos.portal.generator import (
        _extract_or_generate_flow_mermaid,
        _generate_c4_component_mermaid,
        _generate_c4_containers_mermaid,
        _generate_screen_state_mermaid,
    )

    # 1. _generate_screen_state_mermaid: empty, states-only (no transitions), and full transitions
    empty_state = _generate_screen_state_mermaid("SCR-1", [], [])
    assert "[*] --> SCR_1" in empty_state
    states_only = _generate_screen_state_mermaid(
        "SCR-1",
        [{"name": "Idle", "description": "Waiting"}, "Loaded"],
        [],
    )
    assert "stateDiagram-v2" in states_only and "Idle --> Loaded" in states_only
    with_trans = _generate_screen_state_mermaid(
        "SCR-1",
        ["Idle", "Submitted"],
        [{"from_state": "Idle", "to_state": "Submitted", "action": "click_submit"}],
    )
    assert "Idle --> Submitted: click_submit" in with_trans

    # 2. _extract_or_generate_flow_mermaid: explicit mermaid block, empty steps, cross_client sequenceDiagram, user_journey flowchart
    explicit = _extract_or_generate_flow_mermaid(
        "FLOW-1", "user_journey", [], "[mermaid]\n----\ngraph LR\n  A --> B\n----"
    )
    assert "A --> B" in explicit
    assert "Start --> End" in _extract_or_generate_flow_mermaid("FLOW-1", "user_journey", [], "")

    seq_diag = _extract_or_generate_flow_mermaid(
        "FLOW-SEQ",
        "cross_client",
        [
            {"step": 1, "client": "CLT-WEB", "title": "Login", "action": "POST /auth"},
            {"step": 2, "client": "CNT-API", "title": "Verify", "action": "200 OK"},
        ],
        "",
    )
    assert "sequenceDiagram" in seq_diag and "POST /auth" in seq_diag and "200 OK" in seq_diag

    journey_diag = _extract_or_generate_flow_mermaid(
        "FLOW-JRN",
        "user_journey",
        [
            {"step": 1, "title": "Open Screen", "screen": "screens/home.adoc", "action": "x" * 35},
            {"step": 2, "title": "Done"},
        ],
        "",
    )
    assert "flowchart LR" in journey_diag and "home" in journey_diag and "..." in journey_diag

    # 3. _generate_c4_containers_mermaid & _generate_c4_component_mermaid edge branches
    c4_cnts = _generate_c4_containers_mermaid(
        [
            {
                "id": "CNT-WEB",
                "title": "Web App",
                "container_type": "web_app",
                "runtime": "TypeScript",
                "dependencies": {
                    "internal_containers": ["CNT-API", "CNT-WORKER", "CNT-LIB-RS", "CNT-LIB-GO", "CNT-LIB-TS"]
                },
            },
            {
                "id": "CNT-API",
                "title": "API",
                "container_type": "service",
                "runtime": "Python",
                "dependencies": {"internal_containers": ["CNT-DB"], "external_services": ["S3 Storage"]},
            },
            {
                "id": "CNT-WORKER",
                "title": "Worker",
                "container_type": "worker",
                "runtime": "Python",
                "dependencies": {},
            },
            {
                "id": "CNT-LIB-RS",
                "title": "Rust Core",
                "container_type": "library",
                "runtime": "Rust",
                "dependencies": {},
            },
            {
                "id": "CNT-LIB-GO",
                "title": "Go SDK",
                "container_type": "sdk",
                "runtime": "Go",
                "dependencies": {},
            },
            {
                "id": "CNT-LIB-TS",
                "title": "TS SDK",
                "container_type": "package",
                "runtime": "TypeScript",
                "dependencies": {},
            },
            {
                "id": "CNT-DB",
                "title": "Database",
                "container_type": "database",
                "runtime": "PostgreSQL",
                "dependencies": {},
            },
        ],
        personas=[
            {"id": "PER-PM", "title": "PM", "role": "Product Manager", "archetype": "human_lead"},
            {"id": "PER-OPS", "title": "Ops", "role": "SRE", "archetype": "operator"},
        ],
    )
    assert (
        "In-Process Rust API" in c4_cnts and "In-Process Go API" in c4_cnts and "In-Process TypeScript API" in c4_cnts
    )

    c4_empty_cmp = _generate_c4_component_mermaid(
        {"id": "CNT-EMPTY", "title": "Empty", "internal_components": []},
        [{"id": "CNT-EMPTY", "title": "Empty"}],
    )
    assert "Core Container Logic" in c4_empty_cmp

    # 4. portal/assets.py FileNotFoundError fallback paths
    def _raise_fnf(_subpath=""):
        raise FileNotFoundError("simulated missing asset")

    monkeypatch.setattr(portal_assets, "get_assets_path", _raise_fnf)
    fake_pkg = tmp_path / "fake_portal_pkg"
    (fake_pkg / "frontend" / "public").mkdir(parents=True)
    (fake_pkg / "frontend" / "dist").mkdir(parents=True)
    (fake_pkg / "frontend" / "public" / "favicon.svg").write_text("<svg>fallback</svg>", encoding="utf-8")
    (fake_pkg / "frontend" / "dist" / "index.html").write_text("<html>fallback</html>", encoding="utf-8")
    monkeypatch.setattr(portal_assets, "__file__", str(fake_pkg / "assets.py"))
    assert portal_assets._load_portal_favicon() == "<svg>fallback</svg>"
    assert portal_assets._load_portal_html_template() == "<html>fallback</html>"


def test_dlt_2026_085_portal_graph_derived_affects_and_blast_radius(tmp_path: Path) -> None:
    """DLT-2026-085 (# eidos:fixture): Verify get_portal_data derives delta affects and blast_radius
    (including invariants_to_enforce) dynamically from mutations[].target_* and the living spec graph."""
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.portal.generator import get_portal_data

    repo = tmp_path / "portal_graph_ws"
    init_repository(repo, title="Portal Graph System")
    eidos_dir = repo / "eidos"

    src_core = repo / "src" / "core"
    src_core.mkdir(parents=True, exist_ok=True)
    (src_core / "target.py").write_text("def target_fn():\n    return 1\n", encoding="utf-8")
    (src_core / "caller.py").write_text(
        "from core.target import target_fn\n\ndef caller_fn():\n    return target_fn()\n",
        encoding="utf-8",
    )

    (eidos_dir / "architecture" / "components").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "containers" / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/core\n"
        "invariants:\n"
        "  - id: INV-CNT-001\n"
        "    rule: Container invariant.\n"
        "    test_binding: tests/test_core.py::test_cnt\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "target.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-TARGET\n"
        "title: Target\n"
        "container: CNT-CORE\n"
        "source_path: src/core/target.py\n"
        "invariants:\n"
        "  - id: INV-CMP-001\n"
        "    rule: Target invariant.\n"
        "    test_binding: tests/test_core.py::test_cmp\n"
        "---\n"
        "= Target\n\n== Responsibility\nDirect mutation target.\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "caller.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CALLER\n"
        "title: Caller\n"
        "container: CNT-CORE\n"
        "source_path: src/core/caller.py\n"
        "---\n"
        "= Caller\n\n== Responsibility\nCalls CMP-TARGET.\n",
        encoding="utf-8",
    )

    (eidos_dir / "deltas" / "dlt-2026-099.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-099\n"
        "title: Mutate Target Component\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Test graph-derived affects and blast_radius\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-TARGET\n"
        "---\n"
        "= Mutate Target\n",
        encoding="utf-8",
    )

    data = get_portal_data(eidos_dir)
    dlt = next(d for d in data["deltas"]["proposed"] if d["id"] == "DLT-2026-099")
    assert dlt["affects"]["components"] == ["CMP-TARGET"]
    assert "CNT-CORE" in dlt["blast_radius"]["impacted_containers"]
    assert "CMP-CALLER" in dlt["blast_radius"]["impacted_components"]
    assert "INV-CMP-001" in dlt["blast_radius"]["invariants_to_enforce"]
    assert "INV-CNT-001" in dlt["blast_radius"]["invariants_to_enforce"]


def test_portal_lazy_assets_hatch_mtime_gate_all_personas_and_c4_component_edges(tmp_path: Path, monkeypatch) -> None:
    """Verify B4 & B5:
    - portal/assets.py lazily loads and caches PORTAL_HTML_TEMPLATE and PORTAL_FAVICON_SVG on demand
    - hatch_build.py only invokes build_assets.py when dist/index.html is newer than bundle.html.gz
    - _generate_c4_containers_mermaid renders all personas (>3) without truncating at [:3]
    - _generate_c4_component_mermaid only connects intra-container components with AST-derived dependencies
    """
    import importlib.util
    import os
    import subprocess

    import eidos.portal.assets as portal_assets
    from eidos.portal.generator import _generate_c4_component_mermaid, _generate_c4_containers_mermaid

    # 1. Lazy asset loading in eidos.portal.assets
    monkeypatch.setattr(portal_assets, "_CACHED_HTML_TEMPLATE", None)
    monkeypatch.setattr(portal_assets, "_CACHED_FAVICON_SVG", None)
    html = portal_assets.get_portal_html_template()
    fav = portal_assets.get_portal_favicon_svg()
    assert "<!DOCTYPE html>" in html
    assert "<svg" in fav
    assert portal_assets._CACHED_HTML_TEMPLATE is html
    assert portal_assets._CACHED_FAVICON_SVG is fav

    # 2. Hatch build hook mtime gating
    hatch_script = Path("src/eidos/portal/frontend/scripts/hatch_build.py").resolve()
    spec = importlib.util.spec_from_file_location("eidos_hatch_build_test", hatch_script)
    assert spec is not None and spec.loader is not None
    hatch_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hatch_mod)

    fake_root = tmp_path / "hatch_repo"
    dist_dir = fake_root / "src" / "eidos" / "portal" / "frontend" / "dist"
    scripts_dir = fake_root / "src" / "eidos" / "portal" / "frontend" / "scripts"
    assets_dir = fake_root / "assets" / "portal"
    dist_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    assets_dir.mkdir(parents=True)

    dist_index = dist_dir / "index.html"
    build_script = scripts_dir / "build_assets.py"
    bundle_gz = assets_dir / "bundle.html.gz"
    favicon_svg = assets_dir / "favicon.svg"

    dist_index.write_text("<html>dist</html>", encoding="utf-8")
    build_script.write_text("# build_assets stub\n", encoding="utf-8")
    bundle_gz.write_bytes(b"gz")
    favicon_svg.write_text("<svg/>", encoding="utf-8")

    # Set bundle.html.gz mtime newer than dist/index.html -> subprocess.run must NOT be called
    now = time.time()
    os.utime(dist_index, (now - 100, now - 100))
    os.utime(bundle_gz, (now, now))

    run_calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: run_calls.append(list(cmd)))

    hook = object.__new__(hatch_mod.PortalAssetsBuildHook)
    hook.root = str(fake_root)
    hook.initialize("standard", {})
    assert run_calls == []

    # Set dist/index.html mtime newer than bundle.html.gz -> build_assets.py is invoked
    os.utime(dist_index, (now + 100, now + 100))
    hook.initialize("standard", {})
    assert len(run_calls) == 1 and str(build_script) in run_calls[0]

    # 3. _generate_c4_containers_mermaid renders >3 personas without slicing [:3]
    five_personas = [
        {"id": f"PER-ROLE-{i}", "title": f"Persona {i}", "role": f"Role {i}", "archetype": "developer"}
        for i in range(1, 6)
    ]
    c4_containers = _generate_c4_containers_mermaid(
        [{"id": "CNT-CLI", "title": "CLI", "container_type": "cli", "runtime": "Python", "dependencies": {}}],
        personas=five_personas,
    )
    for i in range(1, 6):
        assert f"PER_ROLE_{i}" in c4_containers

    # 4. _generate_c4_component_mermaid does NOT fabricate sequential mod_0 -.-> mod_1 edges when no dependencies exist,
    #    and DOES emit edges when AST-derived dependencies are present
    cnt_independent = {
        "id": "CNT-IND",
        "title": "Independent Container",
        "internal_components": [
            {"id": "CMP-ALPHA", "title": "Alpha", "responsibility": "First independent module."},
            {"id": "CMP-BETA", "title": "Beta", "responsibility": "Second independent module."},
        ],
    }
    diag_ind = _generate_c4_component_mermaid(cnt_independent, [cnt_independent])
    assert "mod_CNT_IND_0 -.->" not in diag_ind

    cnt_dependent = {
        "id": "CNT-DEP",
        "title": "Dependent Container",
        "internal_components": [
            {
                "id": "CMP-CALLER",
                "title": "Caller",
                "responsibility": "Calls Callee.",
                "dependencies": ["CMP-CALLEE"],
            },
            {"id": "CMP-CALLEE", "title": "Callee", "responsibility": "Handles call."},
        ],
    }
    diag_dep = _generate_c4_component_mermaid(cnt_dependent, [cnt_dependent])
    assert "mod_CNT_DEP_0 -.-> mod_CNT_DEP_1" in diag_dep

    # 5. Promoted tech/component specs preserve domain and AST-derived dependencies in get_portal_data
    from eidos.portal.generator import get_portal_data

    p_root = tmp_path / "portal_promoted"
    p_eidos = p_root / "eidos"
    p_src = p_root / "src"
    (p_eidos / "architecture" / "containers").mkdir(parents=True)
    (p_eidos / "architecture" / "components").mkdir(parents=True)
    p_src.mkdir(parents=True)
    (p_src / "b.py").write_text("def run_b() -> None:\n    pass\n", encoding="utf-8")
    (p_src / "a.py").write_text("from b import run_b\n\ndef run_a() -> None:\n    run_b()\n", encoding="utf-8")
    (p_eidos / "architecture" / "containers" / "app.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-APP\n"
        "title: App\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: src\n"
        "---\n= App\n",
        encoding="utf-8",
    )
    (p_eidos / "architecture" / "components" / "prom-a.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-PROM-A\n"
        "title: Promoted A\n"
        "container: CNT-APP\n"
        "status: active\n"
        "domain: DOM-CORE\n"
        "source_path: src/a.py\n"
        "responsibility: Calls Promoted B.\n"
        "---\n= Promoted A\n",
        encoding="utf-8",
    )
    (p_eidos / "architecture" / "components" / "prom-b.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-PROM-B\n"
        "title: Promoted B\n"
        "container: CNT-APP\n"
        "status: active\n"
        "source_path: src/b.py\n"
        "responsibility: Called by A.\n"
        "---\n= Promoted B\n",
        encoding="utf-8",
    )
    pdata = get_portal_data(p_eidos)
    app_cnt = next(c for c in pdata["components"] if c["id"] == "CNT-APP")
    prom_a = next(ic for ic in app_cnt["internal_components"] if ic["id"] == "CMP-PROM-A")
    assert prom_a["domain"] == "DOM-CORE"
    assert prom_a["dependencies"] == ["CMP-PROM-B"]
    assert "mod_CNT_APP_0 -.-> mod_CNT_APP_1" in pdata["diagrams"]["c4_components"]["CNT-APP"]


def test_group_d4_portal_regressions(tmp_path):  # eidos:fixture
    """Regression suite for Group D4: dependency derivation, non-dict & duplicate invariants,
    Mermaid HTML escaping, EidosPortalHandler static_dir & POST /api/validate guards, and frontend views."""  # eidos:fixture
    import threading
    import urllib.error
    import urllib.request
    from http.server import HTTPServer
    from pathlib import Path

    from eidos.portal.generator import get_portal_data
    from eidos.portal.server import EidosPortalHandler

    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "policies").mkdir(parents=True)
    (eidos_dir / "domains" / "core" / "capabilities").mkdir(parents=True)
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "personas").mkdir(parents=True)

    (eidos_dir / "system.adoc").write_text(
        """---
type: product/system
id: SYS-XSS
title: System <script> & Title
---
= System
""",
        encoding="utf-8",
    )
    (eidos_dir / "personas" / "per-dev.adoc").write_text(
        """---
type: product/persona
id: PER-DEV
title: Dev <Admin> & Lead
role_type: platform_<engineer>_lead
---
= Dev
""",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "pol-dup.adoc").write_text(
        """---
type: product/policy
id: POL-DUP
title: Dup Policy
category: security
severity: blocking
invariants:
  - id: INV-SHARED-001
    rule: Shared invariant rule
    test_binding: tests/test_portal.py::test_group_d4_portal_regressions
---
= Dup Policy
""",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "core" / "capabilities" / "cap-1.adoc").write_text(
        """---
type: product/capability
id: CAP-001
title: Core Cap
domain: DOM-CORE
technical:
  invariants:
    - "INV-RAW-STRING-ENTRY"
    - id: INV-SHARED-001
      rule: Duplicate invariant from capability
    - id: INV-CAP-UNIQUE-001
      rule: Unique capability invariant
---
= Core Cap
""",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "cnt-web.adoc").write_text(
        """---
type: tech/container
id: CNT-WEB
title: Web <App> & Gateway
container_type: web_app
runtime: Node <20> & TS
source_path: src/web
contracts:
  consumes:
    - CTR-CORE
invariants:
  - "INV-RAW-CONTAINER-STRING"
  - id: INV-SHARED-001
    rule: Duplicate invariant on container
components:
  - id: CMP-WEB-AUTH
    title: Auth Filter
    invariants:
      - "INV-RAW-COMP-STRING"
      - id: INV-SHARED-001
        rule: Duplicate invariant on component
---
= Web
""",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "cnt-core.adoc").write_text(
        """---
type: tech/container
id: CNT-CORE
title: Core Engine
container_type: service
runtime: Python 3.12
source_path: src/core
contracts:
  provides:
    - CTR-CORE
---
= Core
""",
        encoding="utf-8",
    )

    pdata = get_portal_data(eidos_dir)

    # 1. Inter-container dependencies derived from contracts for frontend and Mermaid
    web_comp = next(c for c in pdata["components"] if c["id"] == "CNT-WEB")
    assert web_comp["dependencies"]["internal_containers"] == ["CNT-CORE"]
    assert "Consumes [CTR-CORE]" in pdata["diagrams"]["c4_containers"]

    # 2. Non-dict invariant strings ignored without crash and INV-SHARED-001 deduplicated once
    inv_ids = [i["id"] for i in pdata["invariants"]]
    assert inv_ids.count("INV-SHARED-001") == 1
    assert "INV-CAP-UNIQUE-001" in inv_ids

    # 3. Mermaid diagrams HTML-escape user-provided strings
    assert "<script>" not in pdata["diagrams"]["c4_context"]
    assert "&lt;script&gt;" in pdata["diagrams"]["c4_context"]
    assert "&lt;Admin&gt;" in pdata["diagrams"]["c4_context"]
    assert "&lt;Engineer&gt;" in pdata["diagrams"]["c4_containers"]
    assert "&lt;20&gt;" in pdata["diagrams"]["c4_containers"]
    assert "&lt;App&gt;" in pdata["diagrams"]["c4_components"]["CNT-WEB"]

    # 4. EidosPortalHandler static_dir path traversal protection and POST /api/validate resolved path comparison
    static_dir = tmp_path / "static_root"
    static_dir.mkdir()
    (static_dir / "hello.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top-secret", encoding="utf-8")

    EidosPortalHandler.eidos_dir = eidos_dir
    EidosPortalHandler.static_dir = static_dir
    httpd = HTTPServer(("127.0.0.1", 0), EidosPortalHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        # Allowed static file
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/hello.txt", timeout=5) as resp:
            assert resp.status == 200
            assert resp.read().decode("utf-8") == "hello"

        # Traversal outside static_dir -> 403 Forbidden
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/..%2Fsecret.txt", timeout=5)
            raise AssertionError("Expected 403 Forbidden on static_dir traversal")
        except urllib.error.HTTPError as err:
            assert err.code == 403
            err.close()

        # POST /api/validate with empty body or matching eidos dir -> 200 OK
        req_ok = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/validate",
            data=b'{"dir": "eidos"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_ok, timeout=5) as resp:
            assert resp.status == 200

        # POST /api/validate targeting another directory -> 403 Forbidden
        req_bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/validate",
            data=b'{"dir": "../static_root"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req_bad, timeout=5)
            raise AssertionError("Expected 403 Forbidden on mismatched POST /api/validate dir")
        except urllib.error.HTTPError as err:
            assert err.code == 403
            err.close()
    finally:
        EidosPortalHandler.static_dir = None
        httpd.shutdown()
        httpd.server_close()

    # 5. Verify frontend source fixes in ArchitectureView.tsx, DesignView.tsx, and BusinessView.tsx
    repo_root = Path(__file__).resolve().parent.parent
    arch_tsx = (
        repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "views" / "ArchitectureView.tsx"
    ).read_text(encoding="utf-8")
    design_tsx = (
        repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "views" / "DesignView.tsx"
    ).read_text(encoding="utf-8")
    business_tsx = (
        repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "views" / "BusinessView.tsx"
    ).read_text(encoding="utf-8")
    nav_tsx = (
        repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "layout" / "NavigationRail.tsx"
    ).read_text(encoding="utf-8")
    assert "closeC4Inspector();\n                    drillDownToContainer(inspectedComponent.id);" in arch_tsx
    assert "Array.isArray(f.clients) && f.clients.length > 1" in design_tsx
    assert "handleDesignClientSwitch" not in design_tsx
    assert "toggleSidebarCollapse" not in nav_tsx
    assert "toggleSidebarCollapse" not in PORTAL_HTML_TEMPLATE
    assert "handleDesignClientSwitch" not in PORTAL_HTML_TEMPLATE
    assert business_tsx.index('id="businessDomainsSection"') < business_tsx.index('id="businessPoliciesSection"')
    assert PORTAL_HTML_TEMPLATE.index("businessDomainsSection") < PORTAL_HTML_TEMPLATE.index("businessPoliciesSection")


def test_deep_audit_group_d_d1_through_d7(tmp_path: Path):
    """Comprehensive regression test covering Deep Audit Group D (D1-D7)."""
    import http.client
    import threading
    from http.server import HTTPServer

    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.plugin.hooks import handle_stop_hook
    from eidos.plugin.installer import (
        PluginInstallTarget,
        install_plugin,
        update_plugin,
    )
    from eidos.portal.generator import (
        _extract_or_generate_flow_mermaid,
        _generate_c4_component_mermaid,
        _generate_screen_state_mermaid,
        get_portal_data,
    )
    from eidos.portal.server import EidosPortalHandler

    init_repository(tmp_path, title="DeepAuditD")
    eidos_dir = tmp_path / "eidos"

    # D1: get_portal_data populates cnt_dependents and comp_dependents for delta blast_radius
    src_d1 = tmp_path / "src"
    src_d1.mkdir(parents=True, exist_ok=True)
    (src_d1 / "beta.py").write_text("def beta_fn():\n    return 1\n", encoding="utf-8")
    (src_d1 / "alpha.py").write_text(
        "from beta import beta_fn\n\ndef alpha_fn():\n    return beta_fn()\n", encoding="utf-8"
    )
    (eidos_dir / "architecture" / "components").mkdir(parents=True, exist_ok=True)
    cnt_a = eidos_dir / "architecture" / "containers" / "cnt-alpha.adoc"
    cnt_a.write_text(
        "---\n"
        'id: "CNT-ALPHA"\n'
        'title: "Alpha Container"\n'
        'type: "tech/container"\n'
        'status: "active"\n'
        'tier: "tier_core"\n'
        'container_type: "service"\n'
        'runtime: "python3.12"\n'
        "---\n"
        "= Alpha Container\n\nBody.\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "alpha-mod.adoc").write_text(
        "---\n"
        'id: "CMP-ALPHA-MOD"\n'
        'title: "Alpha Mod"\n'
        'type: "tech/component"\n'
        'container: "CNT-ALPHA"\n'
        'status: "active"\n'
        'source_path: "src/alpha.py"\n'
        "---\n"
        "= Alpha Mod\n\n== Responsibility\nAlpha module.\n",
        encoding="utf-8",
    )
    cnt_b = eidos_dir / "architecture" / "containers" / "cnt-beta.adoc"
    cnt_b.write_text(
        "---\n"
        'id: "CNT-BETA"\n'
        'title: "Beta Container"\n'
        'type: "tech/container"\n'
        'status: "active"\n'
        'tier: "tier_core"\n'
        'container_type: "service"\n'
        'runtime: "python3.12"\n'
        "---\n"
        "= Beta Container\n\nBody.\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "beta-mod.adoc").write_text(
        "---\n"
        'id: "CMP-BETA-MOD"\n'
        'title: "Beta Mod"\n'
        'type: "tech/component"\n'
        'container: "CNT-BETA"\n'
        'status: "active"\n'
        'source_path: "src/beta.py"\n'
        "---\n"
        "= Beta Mod\n\n== Responsibility\nBeta module.\n",
        encoding="utf-8",
    )
    dlt_file = eidos_dir / "deltas" / "dlt-2026-900-beta.adoc"
    dlt_file.write_text(
        "---\n"
        'id: "DLT-2026-900"\n'
        'title: "Mutate Beta"\n'
        'type: "delta/mutation"\n'
        'status: "proposed"\n'
        "intent:\n"
        '  kind: "feature"\n'
        '  motivation: "Test blast radius dependents."\n'
        "mutations:\n"
        '  - operation: "modify"\n'
        '    target: "CMP-BETA-MOD"\n'
        '    details: "Update beta mod."\n'
        "---\n"
        "= Mutate Beta\n\nBody.\n",
        encoding="utf-8",
    )
    portal_data = get_portal_data(eidos_dir)
    proposed_dlt = next(d for d in portal_data["deltas"]["proposed"] if d["id"] == "DLT-2026-900")
    assert "CNT-ALPHA" in proposed_dlt["blast_radius"]["impacted_containers"]
    assert "CMP-ALPHA-MOD" in proposed_dlt["blast_radius"]["impacted_components"]

    # D2 & D7: Non-destructive skill protection and update_plugin target lookup
    proj_dir = tmp_path / "plugin_proj"
    proj_dir.mkdir()
    foreign_skill_dir = proj_dir / ".agents" / "skills" / "eidos"
    foreign_skill_dir.mkdir(parents=True)
    foreign_skill = foreign_skill_dir / "SKILL.md"
    foreign_skill.write_text("# Custom user skill\n", encoding="utf-8")
    install_plugin(target=PluginInstallTarget.ANTIGRAVITY, project_dir=proj_dir, force=False)
    assert foreign_skill.read_text(encoding="utf-8") == "# Custom user skill\n"

    claude_proj = tmp_path / "claude_proj"
    claude_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CLAUDE, project_dir=claude_proj, force=True)
    claude_manifest = claude_proj / ".claude" / ".eidos-manifest.json"
    claude_data = orjson.loads(claude_manifest.read_bytes())
    claude_data["version"] = "0.0.8"
    claude_manifest.write_bytes(orjson.dumps(claude_data))
    prev_claude_ver, _, _ = update_plugin(target=PluginInstallTarget.CLAUDE, project_dir=claude_proj)
    assert prev_claude_ver == "0.0.8"

    codex_proj = tmp_path / "codex_proj"
    codex_proj.mkdir()
    install_plugin(target=PluginInstallTarget.CODEX, project_dir=codex_proj, force=True)
    codex_manifest = codex_proj / ".agents" / ".eidos-manifest.json"
    codex_data = orjson.loads(codex_manifest.read_bytes())
    codex_data["version"] = "0.0.9"
    codex_manifest.write_bytes(orjson.dumps(codex_data))
    prev_ver, curr_ver, _ = update_plugin(target=PluginInstallTarget.CODEX, project_dir=codex_proj)
    assert prev_ver == "0.0.9"
    assert curr_ver != "0.0.9"

    # D3: ExplorationsView.tsx uses valid Badge variants ("primary" and "danger", not "info" or "destructive")
    repo_root = Path(__file__).resolve().parent.parent
    exp_tsx = (
        repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "views" / "ExplorationsView.tsx"
    ).read_text(encoding="utf-8")
    assert '"info"' not in exp_tsx
    assert '"destructive"' not in exp_tsx

    # D4: handle_stop_hook runs statelessly without creating stop-gate.json
    stop_res = handle_stop_hook({}, tmp_path)
    assert stop_res["decision"] == "continue"
    assert handle_stop_hook({"executionNum": 2}, tmp_path) == {"decision": "allow"}
    assert not (eidos_dir / ".cache" / "stop-gate.json").exists()

    # D5: EidosPortalHandler MIME types and Content-Length validation
    static_dir = tmp_path / "static_d5"
    static_dir.mkdir()
    (static_dir / "icon.svg").write_text("<svg></svg>", encoding="utf-8")
    EidosPortalHandler.eidos_dir = eidos_dir
    EidosPortalHandler.static_dir = static_dir
    httpd = HTTPServer(("127.0.0.1", 0), EidosPortalHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/icon.svg")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200
        assert "image/svg+xml" in (resp.getheader("Content-Type") or "")
        conn.close()

        conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn2.putrequest("POST", "/api/validate")
        conn2.putheader("Content-Length", "not-an-int")
        conn2.endheaders()
        resp2 = conn2.getresponse()
        resp2.read()
        assert resp2.status == 400
        conn2.close()
    finally:
        EidosPortalHandler.static_dir = None
        httpd.shutdown()
        httpd.server_close()

    # D6: Mermaid generators handle None fields, dict entries in contracts.provides, and spaces/hyphens in state names
    c4_mermaid = _generate_c4_component_mermaid(
        {
            "id": None,
            "name": None,
            "container_type": None,
            "runtime": None,
            "internal_components": [
                {"id": "CMP-1", "name": None, "responsibility": None, "source_path": [None, "a.py"]}
            ],
            "contracts": {"provides": [{"id": "CTR-API-1 (REST)"}]},
        },
        [],
    )
    assert "ctr_CTR_API_1" in c4_mermaid

    state_mermaid = _generate_screen_state_mermaid(
        "SCR-TEST",
        [{"name": "In Progress-State"}, {"name": "Done State"}],
        [{"from_state": "In Progress-State", "to_state": "Done State", "action": "complete"}],
    )
    assert "In_Progress_State --> Done_State: complete" in state_mermaid

    flow_mermaid = _extract_or_generate_flow_mermaid(
        "FLOW-TEST",
        "cross_client",
        [{"step": None, "client": None, "title": None, "action": None}],
        "",
    )
    assert "participant Client as Client" in flow_mermaid

    # D7: eidos portal build --path <repo_root> resolves <repo_root>/eidos automatically
    runner = CliRunner()
    out_dir = tmp_path / "portal_dist"
    res = runner.invoke(app, ["portal", "build", "--path", str(tmp_path), "--out", str(out_dir)])
    assert res.exit_code == 0, res.output
    assert (out_dir / "index.html").is_file()


def test_portal_static_scalability_and_deep_linking(tmp_path: Path):
    """Verify INV-PORTAL-STATIC-SCALABILITY (DLT-2026-108-SCALABLE-STATIC):
    - get_portal_data exports pre-computed directed graph 'edges' and 'validation' diagnostics
    - build_static_portal deduplicates spec bodies in data.json and window.__EIDOS_DATA__ while keeping specs[].body intact
    - EidosPortalHandler responds to GET /api/version and GET /api/drift
    - PORTAL_HTML_TEMPLATE includes hash-routing, hydratePortalData, Blast Radius Simulator, Boundary Interface Contracts,
      Validation & Governance Diagnostics, and Recursive W3C Token Tree Explorer
    """
    import threading
    import urllib.request
    from http.server import HTTPServer

    from eidos.portal.builder import _deduplicate_portal_payload, build_static_portal
    from eidos.portal.generator import get_portal_data
    from eidos.portal.server import EidosPortalHandler

    raw_data = get_portal_data(Path("eidos"))
    assert "edges" in raw_data and isinstance(raw_data["edges"], list) and len(raw_data["edges"]) > 0
    assert all({"source", "target", "type"} <= set(e.keys()) for e in raw_data["edges"])
    assert "validation" in raw_data and isinstance(raw_data["validation"], dict)
    assert "is_clean" in raw_data["validation"]
    assert "schema_errors" in raw_data["validation"]
    assert "broken_references" in raw_data["validation"]

    # Verify _deduplicate_portal_payload reduces serialized payload size while keeping specs[].body
    raw_bytes = orjson.dumps(raw_data)
    dedup_data = _deduplicate_portal_payload(raw_data)
    dedup_bytes = orjson.dumps(dedup_data)
    assert len(dedup_bytes) < len(raw_bytes)
    assert any(s.get("body") for s in dedup_data["specs"])
    assert all(not c.get("body") for c in dedup_data["capabilities"])
    assert all(not d.get("body") for d in dedup_data["domains"])

    out_dir = tmp_path / "scalable_static"
    build_static_portal(Path("eidos"), out_dir)
    built_json = orjson.loads((out_dir / "data.json").read_bytes())
    assert "edges" in built_json and len(built_json["edges"]) > 0
    assert "validation" in built_json and "is_clean" in built_json["validation"]
    assert all(not c.get("body") for c in built_json["capabilities"])
    assert all(not s.get("body") for s in built_json["screens"])

    # Verify default chunked export keeps index.html < 400 KB, emits specs/<id>.json chunks, and escapes <!--
    chunked_index = (out_dir / "index.html").read_text(encoding="utf-8")
    assert len((out_dir / "index.html").read_bytes()) < 400 * 1024
    assert "specs/" in chunked_index
    assert (out_dir / "specs" / "SYS-EIDOS-001.json").is_file()
    sys_chunk = orjson.loads((out_dir / "specs" / "SYS-EIDOS-001.json").read_bytes())
    assert sys_chunk["id"] == "SYS-EIDOS-001"
    assert len(sys_chunk["body"]) > 50

    from eidos.portal.builder import _serialize_script_json

    escaped_json = _serialize_script_json({"note": "<!-- <script>alert(1)</script> -->\u2028\u2029"})
    assert "<!--" not in escaped_json
    assert "\\u003c!--" in escaped_json
    assert "</script>" not in escaped_json
    assert "<\\/script>" in escaped_json
    assert "\u2028" not in escaped_json and "\u2029" not in escaped_json

    # Verify CLI --single-file flag emits standalone single-HTML output
    cli_single_out = tmp_path / "cli_single"
    res_single = runner.invoke(
        app,
        ["portal", "build", "--path", "eidos", "--out", str(cli_single_out), "--single-file"],
    )
    assert res_single.exit_code == 0, res_single.output
    assert "single-file" in res_single.stdout
    assert _external_asset_elements((cli_single_out / "index.html").read_text(encoding="utf-8")) == []

    # Verify live HTTP server /api/version and /api/drift endpoints
    EidosPortalHandler.eidos_dir = Path("eidos")
    EidosPortalHandler._cached_drift_key = None
    EidosPortalHandler._cached_drift_res = None
    httpd = HTTPServer(("127.0.0.1", 0), EidosPortalHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=5) as resp:
            assert resp.status == 200
            ver = orjson.loads(resp.read())
            assert ver["count"] > 0
            assert ver["max_mtime_ns"] > 0
            assert ver["total_size"] > 0

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/drift", timeout=10) as resp:
            assert resp.status == 200
            drift_payload = orjson.loads(resp.read())
            assert "is_clean" in drift_payload
            assert "drift_detected" in drift_payload
            assert "workflow_mode" in drift_payload
    finally:
        httpd.shutdown()
        httpd.server_close()

    # Verify compiled bundle contains DLT-2026-108 UI features & hash routing
    assert "hashchange" in PORTAL_HTML_TEMPLATE
    assert "Interactive Blast Radius Simulator" in PORTAL_HTML_TEMPLATE
    assert "Boundary Interface Contracts" in PORTAL_HTML_TEMPLATE
    assert "Validation & Governance Diagnostics" in PORTAL_HTML_TEMPLATE
    assert "Recursive W3C Token Tree" in PORTAL_HTML_TEMPLATE
    assert "Show More Deltas" in PORTAL_HTML_TEMPLATE
    assert "overscroll-contain" in PORTAL_HTML_TEMPLATE
    assert "deltas/archive/" in PORTAL_HTML_TEMPLATE
    assert "explorations/archive/" in PORTAL_HTML_TEMPLATE
    assert all("is_archived" in e for e in raw_data["explorations"])


def test_portal_xss_sanitization_path_containment_and_host_cors_isolation(tmp_path: Path):
    """Verify INV-PORTAL-XSS-AND-SERVER-ISOLATION:
    - Mermaid strict securityLevel, DOMPurify SANITIZE_DOM, and spec- idprefix in compiled portal bundle
    - ArchitectureView avoids error .innerHTML, suppresses pan-drag clicks, and highlights blast-radius nodes
    - _extract_or_generate_flow_mermaid escapes HTML/quotes and strips unsafe Mermaid directives
    - _extract_design_systems blocks tokens.manifest path traversal / outside symlinks via is_relative_to(repo_root)
    - EidosPortalHandler omits wildcard CORS, sends isolation headers, and blocks Host rebinding + cross-site /api/*
    """
    import http.client

    from eidos.portal.generator import _extract_or_generate_flow_mermaid
    from eidos.portal.server import EidosPortalHandler, PortalThreadingHTTPServer

    # 1. Compiled frontend bundle & source security invariants
    assert (
        "securityLevel:`strict`" in PORTAL_HTML_TEMPLATE
        or 'securityLevel:"strict"' in PORTAL_HTML_TEMPLATE
        or 'securityLevel: "strict"' in PORTAL_HTML_TEMPLATE
    )
    assert "SANITIZE_DOM" in PORTAL_HTML_TEMPLATE
    assert "spec-" in PORTAL_HTML_TEMPLATE

    arch_view_src = Path(
        "src/eidos/portal/frontend/src/components/views/ArchitectureView.tsx"
    ).read_text(encoding="utf-8")
    assert "String(err)" not in arch_view_src or "innerHTML" not in arch_view_src.split("catch")[1]
    assert "if (hasMovedRef.current) return;" in arch_view_src
    assert "blast-radius-1-hop" in arch_view_src and "blast-radius-2-hop" in arch_view_src
    assert "blast-radius-1-hop" in PORTAL_HTML_TEMPLATE

    # 2. Flow Mermaid XSS escaping & directive stripping
    xss_payload = '<img src=x onerror="alert(1)">'
    seq_mermaid = _extract_or_generate_flow_mermaid(
        "FLOW-XSS-SEQ",
        "cross_client",
        [
            {
                "step": 1,
                "client": f"CLT-{xss_payload}",
                "title": f"Step {xss_payload}",
                "action": f"Run {xss_payload}",
                "outcome": f"Done {xss_payload}",
            }
        ],
        "",
    )
    assert "<img" not in seq_mermaid
    assert "&lt;img" in seq_mermaid

    journey_mermaid = _extract_or_generate_flow_mermaid(
        "FLOW-XSS-JOURNEY",
        "screen_journey",
        [
            {
                "step": 1,
                "screen": f"SCR-{xss_payload}",
                "title": f'Click "{xss_payload}"',
                "action": f"Submit {xss_payload}",
            }
        ],
        "",
    )
    assert "<img" not in journey_mermaid
    assert "&lt;img" in journey_mermaid

    raw_mermaid = _extract_or_generate_flow_mermaid(
        "FLOW-RAW-UNSAFE",
        "screen_journey",
        [],
        '=== Diagram\n[mermaid]\n----\n%%{init: {"securityLevel": "loose"}}%%\nflowchart TD\n  A["Ok"] --> B["Next"]\n  click A call evilCallback()\n  click B "javascript:alert(1)"\n----\n',
    )
    assert "securityLevel" not in raw_mermaid
    assert "call evilCallback" not in raw_mermaid
    assert "javascript:" not in raw_mermaid
    assert 'A["Ok"] --> B["Next"]' in raw_mermaid

    # 3. tokens.manifest repository path containment (parent traversal, absolute path, outside symlink)
    outside_secret = tmp_path / "outside_secret.json"
    outside_secret.write_text('{"secret": {"$value": "TOP_SECRET_HOST_TOKEN"}}', encoding="utf-8")

    repo_root = tmp_path / "isolated_repo"
    ds_dir = repo_root / "eidos" / "design" / "systems" / "core"
    ds_dir.mkdir(parents=True)
    (repo_root / "eidos" / "system.adoc").write_text(
        '---\ntype: architecture/system\nid: SYS-ISO-001\ntitle: "Isolated"\nstatus: active\n---\n= Isolated\n',
        encoding="utf-8",
    )
    # Sibling tokens.json must NOT be loaded if explicit tokens.manifest escapes repo_root
    (ds_dir / "tokens.json").write_text(
        '{"fallback": {"$value": "SHOULD_NOT_LOAD_ON_TRAVERSAL"}}', encoding="utf-8"
    )
    escape_symlink = ds_dir / "symlink_tokens.json"
    escape_symlink.symlink_to(outside_secret)

    for idx, manifest_ref in enumerate(
        [
            "../../../../../outside_secret.json",
            str(outside_secret),
            "symlink_tokens.json",
        ]
    ):
        (ds_dir / "system.adoc").write_text(
            f'---\ntype: design/system\nid: DS-ISO-{idx}\ntitle: "DS {idx}"\nstatus: active\n'
            f'tokens:\n  format: w3c-dtcg\n  manifest: "{manifest_ref}"\n---\n= DS\n',
            encoding="utf-8",
        )
        iso_data = get_portal_data(repo_root / "eidos")
        raw_dump = orjson.dumps(iso_data).decode("utf-8")
        assert "TOP_SECRET_HOST_TOKEN" not in raw_dump
        assert "SHOULD_NOT_LOAD_ON_TRAVERSAL" not in raw_dump
        ds_entry = iso_data["design_systems"][0]
        assert ds_entry["tokens"] == {}
        assert "outside repository root" in ds_entry.get("tokens_warning", "")

    # 4. Server CORS removal, security headers, Host validation & Sec-Fetch-Site isolation
    prev_eidos_dir = EidosPortalHandler.eidos_dir
    EidosPortalHandler.eidos_dir = repo_root / "eidos"
    httpd = PortalThreadingHTTPServer(("127.0.0.1", 0), EidosPortalHandler)
    port = httpd.server_address[1]
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/data", headers={"Host": f"127.0.0.1:{port}"})
        res = conn.getresponse()
        res.read()
        assert res.status == 200
        assert res.getheader("Access-Control-Allow-Origin") is None
        assert res.getheader("X-Content-Type-Options") == "nosniff"
        assert res.getheader("Cross-Origin-Resource-Policy") == "same-origin"
        assert res.getheader("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in (res.getheader("Content-Security-Policy") or "")
        conn.close()

        # DNS rebinding Host header -> 403 Forbidden
        conn_rebind = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_rebind.request("GET", "/api/data", headers={"Host": f"evil.attacker.example:{port}"})
        res_rebind = conn_rebind.getresponse()
        res_rebind.read()
        assert res_rebind.status == 403
        conn_rebind.close()

        # Wrong port in Host header -> 403 Forbidden
        conn_port = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_port.request("GET", "/api/data", headers={"Host": "127.0.0.1:65530"})
        res_port = conn_port.getresponse()
        res_port.read()
        assert res_port.status == 403
        conn_port.close()

        # Cross-site browser fetch on /api/* -> 403 Forbidden
        conn_cs = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn_cs.request(
            "POST",
            "/api/validate",
            headers={"Host": f"localhost:{port}", "Sec-Fetch-Site": "cross-site"},
        )
        res_cs = conn_cs.getresponse()
        res_cs.read()
        assert res_cs.status == 403
        conn_cs.close()

        # IPv6 loopback [::1]:port and bare localhost Host headers are accepted; malformed Host headers -> 403
        for good_host in (f"[::1]:{port}", "localhost", "[::1]"):
            c_ok = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c_ok.request("GET", "/health", headers={"Host": good_host})
            r_ok = c_ok.getresponse()
            r_ok.read()
            assert r_ok.status == 200
            c_ok.close()

        for bad_host in ("", "127.0.0.1/evil", "[::1", "[::1]:abc", "127.0.0.1:notaport"):
            c_bad = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c_bad.request("GET", "/health", headers={"Host": bad_host})
            r_bad = c_bad.getresponse()
            r_bad.read()
            assert r_bad.status == 403
            c_bad.close()

        # POST error paths: unknown route -> 404, invalid JSON -> 400, excessive Content-Length -> 400
        c_post404 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c_post404.request("POST", "/api/unknown", headers={"Host": f"127.0.0.1:{port}"})
        r_post404 = c_post404.getresponse()
        r_post404.read()
        assert r_post404.status == 404
        c_post404.close()

        c_post_json = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c_post_json.request(
            "POST",
            "/api/validate",
            body=b"{invalid",
            headers={"Host": f"127.0.0.1:{port}", "Content-Length": "8"},
        )
        r_post_json = c_post_json.getresponse()
        r_post_json.read()
        assert r_post_json.status == 400
        c_post_json.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        EidosPortalHandler.eidos_dir = prev_eidos_dir

    # Unit-check _is_allowed_host on 0.0.0.0 and explicit interface bindings
    dummy_handler = object.__new__(EidosPortalHandler)
    dummy_srv = type("DummySrv", (), {"server_address": ("0.0.0.0", 3000), "portal_bound_host": "0.0.0.0"})()
    dummy_handler.server = dummy_srv
    assert dummy_handler._is_allowed_host("localhost:3000") is True
    assert dummy_handler._is_allowed_host("evil.example:3000") is False
    dummy_srv2 = type("DummySrv2", (), {"server_address": ("10.1.2.3", 3000), "portal_bound_host": "mybox.local"})()
    dummy_handler.server = dummy_srv2
    assert dummy_handler._is_allowed_host("mybox.local:3000") is True
    assert dummy_handler._is_allowed_host("10.1.2.3:3000") is True
    assert dummy_handler._is_allowed_host("other.local:3000") is False


def test_portal_live_drift_sse_c4_context_and_screen_hydration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify INV-PORTAL-LIVE-DRIFT-SSE:
    - _compute_eidos_mtime_snapshot tracks governed src/ and tests/ trees alongside eidos/
    - _get_cached_drift fails closed (is_clean=False, drift_detected=True, error=...) on exceptions
    - GET /api/events streams text/event-stream SSE status frames on initial connect and live src/ edits
    - _generate_c4_context_mermaid formats {name, role, protocol} external_systems dicts cleanly
    - get_portal_data hydrates screen bodies, repo-relative schema_errors, and exploration exit_criteria/recommendations
    """
    import eidos.cli.portal_commands as portal_cmd_mod
    import eidos.vcs.drift as drift_mod
    from eidos.portal.generator import (
        _extract_exploration_fields,
        _generate_c4_context_mermaid,
        _to_repo_rel_posix,
    )
    from eidos.portal.server import (
        EidosPortalHandler,
        PortalThreadingHTTPServer,
        _compute_eidos_mtime_snapshot,
    )

    assert _compute_eidos_mtime_snapshot(tmp_path / "missing_eidos") == (0, 0, 0)
    assert _to_repo_rel_posix("./eidos/system.adoc", tmp_path.resolve()) == "eidos/system.adoc"
    assert _to_repo_rel_posix(Path("/nonexistent_outside_root/x.adoc"), tmp_path.resolve()) == "/nonexistent_outside_root/x.adoc"

    ec_dict, f_dict, rec_dict = _extract_exploration_fields(
        {"exit_criteria": {"max_duration": "2d", "benchmark": "<50ms"}},
        "== Outcome & Learnings\n* *Recommendation*: Adopt chunked static export.\n* Zero regressions observed.\n",
        {},
        "adopted",
        [],
        "",
    )
    assert isinstance(ec_dict, dict) and ec_dict["max_duration"] == "2d"
    assert f_dict == "Zero regressions observed."
    assert rec_dict == "Adopt chunked static export."

    ec_list, _, rec_inline = _extract_exploration_fields(
        {"exit_criteria": ["p95 < 10ms"]},
        "* *Recommendation*: Keep strict mode.\n",
        {},
        "",
        ["DLT-2026-999"],
        "Fast",
    )
    assert ec_list == ["p95 < 10ms"]
    assert rec_inline == "Keep strict mode."

    # 1. Governed src/ and tests/ mtime snapshot invalidation
    ws = tmp_path / "live_ws"
    (ws / "eidos" / "explorations").mkdir(parents=True)
    (ws / "eidos" / "domains" / "core" / "capabilities").mkdir(parents=True)
    (ws / "eidos" / "architecture" / "containers").mkdir(parents=True)
    (ws / "src" / "pkg").mkdir(parents=True)
    (ws / "tests").mkdir(parents=True)
    inv_live_uncov = "INV-" + "PORTAL-LIVE-UNCOV"
    inv_live_orphan = "INV-" + "PORTAL-LIVE-ORPHAN"
    (ws / "eidos" / "system.adoc").write_text(
        '---\ntype: product/system\nid: SYS-LIVE-001\ntitle: "Live WS"\nstatus: active\n---\n= Live\n',
        encoding="utf-8",
    )
    (ws / "eidos" / "domains" / "core" / "domain.adoc").write_text(
        '---\ntype: product/domain\nid: DOM-LIVE\ntitle: "Live Domain"\nstatus: active\n---\n= Domain\n',
        encoding="utf-8",
    )
    (ws / "eidos" / "architecture" / "containers" / "live.adoc").write_text(
        '---\ntype: tech/container\nid: CNT-LIVE\ntitle: "Live Container"\nstatus: active\n'
        'container_type: service\nruntime: "Python 3.12"\ndomain: DOM-LIVE\nsource_path: src/pkg\n---\n= Live\n',
        encoding="utf-8",
    )
    (ws / "eidos" / "domains" / "core" / "capabilities" / "live-cap.adoc").write_text(
        f'---\ntype: product/capability\nid: CAP-LIVE-001\ntitle: "Live Cap"\nstatus: active\ndomain: DOM-LIVE\n'
        f'technical:\n  containers:\n    - CNT-LIVE\n  invariants:\n    - id: {inv_live_uncov}\n'
        f'      rule: "Must track live drift invariants."\n'
        f'      test_binding: "tests/test_mod.py::test_missing_live_binding"\n---\n= Live Cap\n',
        encoding="utf-8",
    )
    (ws / "eidos" / "explorations" / "exp-001.adoc").write_text(
        '---\ntype: product/exploration\nid: EXP-LIVE-001\ntitle: "Spike"\nstatus: adopted\n'
        'hypothesis: "Test SSE"\ndecision:\n  outcome: adopted\n  resulting_deltas:\n    - DLT-2026-001\n'
        '  learnings: "SSE works"\n---\n= Spike\n\n== 1. Scope\n* *Exit Criteria*: Sub-second drift push.\n\n'
        '== 4. Exit Criteria\n* Zero hanging daemon threads.\n',
        encoding="utf-8",
    )
    src_file = ws / "src" / "pkg" / "mod.py"
    test_file = ws / "tests" / "test_mod.py"
    src_file.write_text("VALUE = 1\n", encoding="utf-8")
    test_file.write_text(f"def test_v():\n    # {inv_live_orphan}\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@eidos.local"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Test"], cwd=ws, check=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init live ws"], cwd=ws, check=True)

    snap0 = _compute_eidos_mtime_snapshot(ws / "eidos")
    time.sleep(0.01)
    src_file.write_text("VALUE = 22\n", encoding="utf-8")
    snap1 = _compute_eidos_mtime_snapshot(ws / "eidos")
    assert snap1 != snap0

    time.sleep(0.01)
    test_file.write_text(f"def test_v():\n    # {inv_live_orphan}\n    assert 1 == 1\n", encoding="utf-8")
    snap2 = _compute_eidos_mtime_snapshot(ws / "eidos")
    assert snap2 != snap1
    (ws / "src" / "unmapped_live.py").write_text("EXTRA = 1\n", encoding="utf-8")

    # 2. _get_cached_drift fails closed on error & GET /api/events streams live SSE updates
    prev_eidos_dir = EidosPortalHandler.eidos_dir
    EidosPortalHandler.eidos_dir = ws / "eidos"
    EidosPortalHandler._cached_drift_key = None
    EidosPortalHandler._cached_drift_res = None

    httpd = PortalThreadingHTTPServer(("127.0.0.1", 0), EidosPortalHandler)
    port = httpd.server_address[1]
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()
    try:
        # Initial SSE frame via ?once=1
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/events?once=1", timeout=5) as sse_res:
            assert sse_res.status == 200
            assert "text/event-stream" in (sse_res.headers.get("Content-Type") or "")
            raw_sse = sse_res.read().decode("utf-8")
            assert "event: status" in raw_sse
            data_lines = [ln[6:] for ln in raw_sse.splitlines() if ln.startswith("data: ")]
            assert len(data_lines) == 1
            first_event = orjson.loads(data_lines[0])
            assert "version" in first_event and "drift" in first_event
            first_drift = first_event["drift"]
            assert first_drift["workflow_mode"] == "direct"
            assert "src/pkg/mod.py" in first_drift["changed_files"]
            assert "src/unmapped_live.py" in first_drift["unmapped_files"]
            assert inv_live_uncov in first_drift["uncovered_invariants"]
            assert any(
                o["invariant_id"] == inv_live_orphan and "test_mod.py" in o["test_id"]
                for o in first_drift["orphan_invariants"]
            )

        # Streaming SSE update when governed src/ file mutates (?max_events=2)
        def _mutate_src_soon() -> None:
            time.sleep(0.25)
            src_file.write_text("VALUE = 333\n", encoding="utf-8")

        mut_thread = threading.Thread(target=_mutate_src_soon, daemon=True)
        mut_thread.start()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/events?max_events=2", timeout=5
        ) as sse_stream:
            stream_body = sse_stream.read().decode("utf-8")
            frames = [ln[6:] for ln in stream_body.splitlines() if ln.startswith("data: ")]
            assert len(frames) == 2
            ev0 = orjson.loads(frames[0])
            ev1 = orjson.loads(frames[1])
            assert ev1["version"] != ev0["version"]

        # Fail-closed drift verification when detect_drift raises
        def _boom(*_a, **_kw):
            raise RuntimeError("simulated git failure")

        monkeypatch.setattr(drift_mod, "detect_drift", _boom)
        EidosPortalHandler._cached_drift_key = None
        EidosPortalHandler._cached_drift_res = None
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/drift", timeout=5) as drift_res:
            drift_json = orjson.loads(drift_res.read())
            assert drift_json["is_clean"] is False
            assert drift_json["drift_detected"] is True
            assert "simulated git failure" in drift_json.get("error", "")
    finally:
        httpd.shutdown()
        httpd.server_close()
        EidosPortalHandler.eidos_dir = prev_eidos_dir

    # 3. C4 Level 1 external_systems dict & string formatting without Python reprs
    c4_ctx = _generate_c4_context_mermaid(
        [{"id": "PER-USER", "title": "End User", "role": "Customer"}],
        "SYS-CTX-001",
        "Context Demo",
        [
            None,
            "",
            {
                "name": "Stripe Billing",
                "role": "Payment Gateway",
                "protocol": "HTTPS Webhook",
            },
            "Legacy Mainframe",
        ],
    )
    assert "{'name':" not in c4_ctx
    assert "Stripe Billing" in c4_ctx
    assert "Payment Gateway" in c4_ctx
    assert "HTTPS Webhook" in c4_ctx
    assert "Legacy Mainframe" in c4_ctx

    # 4. Exploration exit_criteria/recommendation extraction & Screen body hydration
    ws_portal = get_portal_data(ws / "eidos")
    exp = ws_portal["explorations"][0]
    assert exp["exit_criteria"]
    assert "Sub-second drift push." in exp["exit_criteria"]
    assert "DLT-2026-001" in exp["recommendation"]

    live_portal = get_portal_data(Path("eidos"))
    assert len(live_portal["screens"]) >= 6
    assert all(isinstance(s.get("body"), str) and len(s["body"]) > 20 for s in live_portal["screens"])
    assert any(e.get("exit_criteria") for e in live_portal["explorations"])
    assert any(e.get("recommendation") for e in live_portal["explorations"])
    for err_key in live_portal["validation"]["schema_errors"]:
        assert not Path(err_key).is_absolute()

    # 5. CLI portal serve/build path validation & serve_cmd delegation
    missing_dir = tmp_path / "does_not_exist_eidos"
    res_bad_serve = runner.invoke(app, ["portal", "serve", "--path", str(missing_dir)])
    assert res_bad_serve.exit_code == 1
    res_bad_build = runner.invoke(app, ["portal", "build", "--path", str(missing_dir)])
    assert res_bad_build.exit_code == 1

    served_args: list[tuple[Path, str, int, bool]] = []
    monkeypatch.setattr(
        portal_cmd_mod,
        "run_portal_server",
        lambda eidos_dir, host, port, open_browser: served_args.append((eidos_dir, host, port, open_browser)),
    )
    res_ok_serve = runner.invoke(
        app,
        ["portal", "serve", "--path", str(ws / "eidos"), "--host", "127.0.0.1", "--port", "3123"],
    )
    assert res_ok_serve.exit_code == 0, res_ok_serve.output
    assert len(served_args) == 1 and served_args[0][2] == 3123


