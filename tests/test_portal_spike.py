"""Empirical Benchmark & Validation Suite for EXP-2026-003:
Web Living Portal Modernization: shadcn/ui, Tailwind CSS & Material Design 3 (MD3) Integration.

Verifies:
1. Spike A: Vite + React + Tailwind CSS v4 + vite-plugin-singlefile bundle generation (<=2 MiB compressed after DLT-2026-041).
2. Spike B: W3C DTCG tokens to MD3 tonal surfaces & color roles bridge.
3. Spike C: React C4 Mermaid architecture viewer & SpecDelta Kanban board viewports.
4. Spike D: Zero Node.js runtime invariant (INV-PORTAL-ZERO-NODE-DEP) for eidos serve & build-site.
5. Exit Criteria: Cold-boot delivery < 50ms, single-file bundle <= 5 MiB (revised by DLT-2026-041), zero runtime node dependency.
"""

import gzip
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture
def repo_root():
    return Path.cwd().resolve()


def test_spike_a_vite_singlefile_bundler(repo_root):
    """Spike A: Verify single-file HTML bundle generation with vite-plugin-singlefile."""
    frontend_dir = repo_root / "src" / "eidos" / "portal" / "frontend"
    dist_html = frontend_dir / "dist" / "index.html"

    if dist_html.exists():
        raw_content = dist_html.read_bytes()
    else:
        from eidos.portal.assets import PORTAL_HTML_TEMPLATE
        assert PORTAL_HTML_TEMPLATE, "PORTAL_HTML_TEMPLATE must exist in eidos.portal.assets"
        raw_content = PORTAL_HTML_TEMPLATE.encode("utf-8")
    raw_size_kb = len(raw_content) / 1024
    gzip_size_kb = len(gzip.compress(raw_content)) / 1024

    # Exit Criteria (revised by DLT-2026-041, which embeds Mermaid.js, Asciidoctor.js and Geist fonts so the portal
    # is air-gapped): uncompressed <= 5 MiB (5120KB, see test_portal.py::test_portal_bundle_size_budget),
    # compressed <= 2 MiB (2048KB). The original EXP-2026-003 limits were 1500KB / 1200KB.
    assert raw_size_kb <= 5120.0, f"Raw bundle size {raw_size_kb:.2f}KB exceeds 5120KB limit"
    assert gzip_size_kb <= 2048.0, f"Compressed bundle size {gzip_size_kb:.2f}KB exceeds 2048KB limit"

    # Verify self-contained single-file properties
    html_text = raw_content.decode("utf-8")
    assert "<!DOCTYPE html>" in html_text
    assert "<script" in html_text
    assert "<style" in html_text
    # No external CSS or JS links: structural check over real elements (inline JS string literals, e.g. Asciidoctor's
    # standalone-page templates, are script data and never become elements).
    from html.parser import HTMLParser

    class _AssetLinks(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: list[str] = []

        def handle_starttag(self, tag, attrs):
            attr_map = dict(attrs)
            if tag == "script" and attr_map.get("src"):
                self.found.append(f"<script src={attr_map['src']}>")
            if tag == "link" and "stylesheet" in (attr_map.get("rel") or "").lower():
                self.found.append(f"<link rel=stylesheet href={attr_map.get('href')}>")

    links = _AssetLinks()
    links.feed(html_text)
    assert links.found == []


def test_spike_b_md3_tokens_and_shadcn_bridge(repo_root):
    """Spike B: Verify W3C DTCG tokens (tokens.json) map to MD3 color roles and elevation levels."""
    tokens_file = repo_root / "eidos" / "design" / "systems" / "core" / "tokens.json"
    assert tokens_file.exists(), "W3C DTCG tokens.json must exist"

    with open(tokens_file, encoding="utf-8") as f:
        tokens_data = json.load(f)

    # Verify core token structures
    assert "color" in tokens_data
    assert "brand" in tokens_data["color"]
    assert "primary" in tokens_data["color"]["brand"]
    primary_hex = tokens_data["color"]["brand"]["primary"]["$value"]
    assert primary_hex == "#3b82f6"

    # Verify MD3 theme bridge definitions exist
    bridge_file = repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "theme" / "md3-tokens.ts"
    assert bridge_file.exists()
    bridge_code = bridge_file.read_text(encoding="utf-8")

    assert "md3DarkColors" in bridge_code
    assert "md3LightColors" in bridge_code
    assert "md3Elevation" in bridge_code
    assert "level1" in bridge_code
    assert "level5" in bridge_code

    # Verify Tailwind v4 theme CSS defines MD3 variables & classes
    css_file = repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "theme" / "theme.css"
    assert css_file.exists()
    css_text = css_file.read_text(encoding="utf-8")

    assert "--md-primary:" in css_text
    assert "--md-surface:" in css_text
    assert "--md-surface-container:" in css_text
    assert ".md-elevation-1" in css_text
    assert ".md-elevation-4" in css_text
    assert ".md-state-layer" in css_text


def test_spike_c_c4_mermaid_kanban_react(repo_root):
    """Spike C: Verify React components for C4 architecture viewer and SpecDelta Kanban board."""
    views_dir = repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "views"

    arch_file = views_dir / "ArchitectureView.tsx"
    deltas_file = views_dir / "DeltasView.tsx"
    catalog_file = views_dir / "CatalogView.tsx"
    home_file = views_dir / "HomeView.tsx"
    design_file = views_dir / "DesignView.tsx"
    explorations_file = views_dir / "ExplorationsView.tsx"

    assert arch_file.exists(), "ArchitectureView.tsx must exist"
    assert deltas_file.exists(), "DeltasView.tsx must exist"
    assert catalog_file.exists(), "CatalogView.tsx must exist"
    assert home_file.exists(), "HomeView.tsx must exist"
    assert design_file.exists(), "DesignView.tsx must exist"
    assert explorations_file.exists(), "ExplorationsView.tsx must exist"

    arch_code = arch_file.read_text(encoding="utf-8")
    assert "mermaid" in arch_code
    assert "c4LevelSwitcher" in arch_code
    assert "c4InspectorDrawer" in arch_code

    deltas_code = deltas_file.read_text(encoding="utf-8")
    assert "proposed" in deltas_code
    assert "in_review" in deltas_code
    assert "approved" in deltas_code
    assert "applied" in deltas_code

    # Verify Command Palette spotlight search component
    cmd_file = repo_root / "src" / "eidos" / "portal" / "frontend" / "src" / "components" / "ui" / "command.tsx"
    assert cmd_file.exists()
    cmd_code = cmd_file.read_text(encoding="utf-8")
    assert "CommandDialog" in cmd_code
    assert "handleKeyDown" in cmd_code


def test_spike_d_runtime_zero_node_invariant(repo_root):
    """Spike D: Verify that eidos serve and build-site run with ZERO Node.js runtime dependencies."""
    from eidos.portal.builder import build_static_portal
    from eidos.portal.server import run_portal_server

    # 1. Test build_static_portal runs pure Python without Node.js
    tmp_out = repo_root / ".eidos" / "test_portal_out"
    build_static_portal(repo_root / "eidos", tmp_out)
    assert (tmp_out / "index.html").exists()
    assert (tmp_out / "data.json").exists()

    # 2. Test cold-boot delivery latency < 50ms (Exit Criteria)
    test_port = 8996
    thread = threading.Thread(
        target=run_portal_server,
        args=(repo_root / "eidos", "127.0.0.1", test_port, False),
        daemon=True,
    )
    thread.start()
    time.sleep(0.2)

    t0 = time.perf_counter()
    with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/") as res:
        assert res.status == 200
        html = res.read()
    delivery_latency_ms = (time.perf_counter() - t0) * 1000

    # Exit Criteria: Cold boot live preview response < 50ms (headroom when instrumented with coverage)
    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    max_delivery_ms = 150.0 if is_instrumented else 50.0
    assert delivery_latency_ms < max_delivery_ms, f"Delivery latency {delivery_latency_ms:.2f}ms exceeds {max_delivery_ms}ms"
    assert len(html) > 1000
