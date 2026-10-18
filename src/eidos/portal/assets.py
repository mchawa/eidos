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
from typing import TYPE_CHECKING

from eidos.core.workspace.assets import get_assets_path

if TYPE_CHECKING:
    PORTAL_FAVICON_SVG: str
    PORTAL_HTML_TEMPLATE: str


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


_CACHED_FAVICON_SVG: str | None = None
_CACHED_HTML_TEMPLATE: str | None = None


def get_portal_favicon_svg() -> str:
    """Lazily load and cache the SVG brand mark."""
    global _CACHED_FAVICON_SVG
    if _CACHED_FAVICON_SVG is None:
        _CACHED_FAVICON_SVG = _load_portal_favicon()
    return _CACHED_FAVICON_SVG


def get_portal_html_template() -> str:
    """Lazily decompress and cache the single-file air-gapped HTML bundle."""
    global _CACHED_HTML_TEMPLATE
    if _CACHED_HTML_TEMPLATE is None:
        _CACHED_HTML_TEMPLATE = _load_portal_html_template()
    return _CACHED_HTML_TEMPLATE


def __getattr__(name: str) -> str:
    if name == "PORTAL_FAVICON_SVG":
        return get_portal_favicon_svg()
    if name == "PORTAL_HTML_TEMPLATE":
        return get_portal_html_template()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PORTAL_FAVICON_SVG",
    "PORTAL_HTML_TEMPLATE",
    "get_portal_favicon_svg",
    "get_portal_html_template",
]
