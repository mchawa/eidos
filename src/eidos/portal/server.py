"""Local HTTP server hosting the interactive Eidos Living Web Portal."""

import json
import logging
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from eidos.core.spec.validator import SpecValidator
from eidos.portal.assets import PORTAL_FAVICON_SVG, PORTAL_HTML_TEMPLATE
from eidos.portal.generator import get_portal_data

logger = logging.getLogger("eidos.portal")


def _compute_eidos_mtime_snapshot(eidos_dir: Path) -> tuple[int, int, int]:
    """Compute a lightweight (file_count, max_mtime_ns, total_size) snapshot of eidos_dir."""
    if not eidos_dir.is_dir():
        return (0, 0, 0)
    count = 0
    max_mtime = 0
    total_size = 0
    for p in eidos_dir.rglob("*"):
        if ".cache" in p.parts or not p.is_file():
            continue
        if p.suffix.lower() not in (".adoc", ".json", ".yaml", ".yml"):
            continue
        try:
            st = p.stat()
            count += 1
            if st.st_mtime_ns > max_mtime:
                max_mtime = st.st_mtime_ns
            total_size += st.st_size
        except OSError:
            continue
    return (count, max_mtime, total_size)


class EidosPortalHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching Portal UI and JSON API endpoints."""

    eidos_dir: Path = Path("eidos")
    _cache_lock: threading.Lock = threading.Lock()
    _cache_key: tuple[str, int, int, int] | None = None
    _cached_portal_data: dict[str, Any] | None = None
    _cached_validation_key: tuple[str, int, int, int] | None = None
    _cached_validation_res: dict[str, Any] | None = None

    def _get_cached_portal_data(self) -> dict[str, Any]:
        snap = (str(self.eidos_dir.resolve()), *_compute_eidos_mtime_snapshot(self.eidos_dir))
        with EidosPortalHandler._cache_lock:
            if EidosPortalHandler._cache_key == snap and EidosPortalHandler._cached_portal_data is not None:
                return EidosPortalHandler._cached_portal_data
            data = get_portal_data(self.eidos_dir)
            EidosPortalHandler._cache_key = snap
            EidosPortalHandler._cached_portal_data = data
            return data

    def _get_cached_validation(self) -> dict[str, Any]:
        snap = (str(self.eidos_dir.resolve()), *_compute_eidos_mtime_snapshot(self.eidos_dir))
        with EidosPortalHandler._cache_lock:
            if (
                EidosPortalHandler._cached_validation_key == snap
                and EidosPortalHandler._cached_validation_res is not None
            ):
                return EidosPortalHandler._cached_validation_res
            validator = SpecValidator()
            report = validator.validate_directory(self.eidos_dir)
            res = {
                "total_docs": report.total_docs,
                "valid_docs": report.valid_docs,
                "is_clean": report.is_clean,
                "schema_errors": report.schema_errors,
                "broken_references": report.broken_references,
            }
            EidosPortalHandler._cached_validation_key = snap
            EidosPortalHandler._cached_validation_res = res
            return res

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            self._send_html(PORTAL_HTML_TEMPLATE)
        elif path in ("/favicon.svg", "/favicon.ico"):
            self._send_svg(PORTAL_FAVICON_SVG)
        elif path == "/api/data":
            data = self._get_cached_portal_data()
            self._send_json(data)
        elif path == "/api/overview":
            data = self._get_cached_portal_data()
            self._send_json(data.get("overview", {}))
        elif path == "/api/validate":
            res = self._get_cached_validation()
            self._send_json(res)
        elif path in ("/health", "/api/health"):
            self._send_json({"status": "ok", "system": "eidos-portal"})
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"Path '{path}' not found.")

    def _send_html(self, html_content: str) -> None:
        payload = html_content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def _send_svg(self, svg_content: str) -> None:
        payload = svg_content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard request logging in production/CLI
        pass


def run_portal_server(
    eidos_dir: Path,
    host: str = "127.0.0.1",
    port: int = 3000,
    open_browser: bool = False,
) -> None:
    """Launch the Eidos Living Web Portal HTTP server."""
    target_dir = eidos_dir.resolve()
    EidosPortalHandler.eidos_dir = target_dir

    server_address = (host, port)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(server_address, EidosPortalHandler)

    url = f"http://{host}:{port}"
    print(f"\n⚡ Eidos Living Web Portal serving at: {url}")
    print(f"   Root directory: {target_dir}")
    print("   Press Ctrl+C to terminate.")

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Eidos Web Portal server...")
    finally:
        httpd.server_close()
