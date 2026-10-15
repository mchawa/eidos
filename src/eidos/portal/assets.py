"""Bundled Web Portal UI assets loader (CMP-PORTAL-ASSETS).

Loads the single-file air-gapped HTML bundle (``PORTAL_HTML_TEMPLATE``) and SVG brand mark
(``PORTAL_FAVICON_SVG``) from ``assets/portal/bundle.html.gz`` and ``assets/portal/favicon.svg``
(with fallback to local ``frontend/dist/index.html`` during frontend development builds)
so ``eidos portal serve`` and ``eidos portal build`` require zero Node.js runtime (INV-PORTAL-ZERO-NODE-DEP)
without storing a 4.7 MB single-line Python string literal in ``portal/assets.py`` (DLT-2026-050).
"""

from __future__ import annotations

import gzip
from pathlib import Path

from eidos.core.workspace.assets import get_assets_path


def _load_portal_favicon() -> str:
    try:
        return get_assets_path("portal/favicon.svg").read_text(encoding="utf-8")
    except FileNotFoundError:
        fallback = Path(__file__).resolve().parent / "frontend" / "public" / "favicon.svg"
        if fallback.is_file():
            return fallback.read_text(encoding="utf-8")
        raise


def _load_portal_html_template() -> str:
    try:
        bundle_gz = get_assets_path("portal/bundle.html.gz")
        return gzip.decompress(bundle_gz.read_bytes()).decode("utf-8")
    except FileNotFoundError:
        fallback = Path(__file__).resolve().parent / "frontend" / "dist" / "index.html"
        if fallback.is_file():
            return fallback.read_text(encoding="utf-8")
        raise


PORTAL_FAVICON_SVG: str = _load_portal_favicon()
PORTAL_HTML_TEMPLATE: str = _load_portal_html_template()

__all__ = ["PORTAL_FAVICON_SVG", "PORTAL_HTML_TEMPLATE"]
