"""Eidos Living Web Portal & Static Site Generator."""

from eidos.portal.builder import build_static_portal
from eidos.portal.generator import get_portal_data
from eidos.portal.server import run_portal_server

__all__ = ["build_static_portal", "get_portal_data", "run_portal_server"]
