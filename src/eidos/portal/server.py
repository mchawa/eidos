"""Local HTTP server hosting the interactive Eidos Living Web Portal."""

import json
import logging
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from eidos.core.validator import SpecValidator
from eidos.portal.assets import PORTAL_HTML_TEMPLATE
from eidos.portal.generator import get_portal_data

logger = logging.getLogger("eidos.portal")


class EidosPortalHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching Portal UI and JSON API endpoints."""

    eidos_dir: Path = Path("eidos")

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            self._send_html(PORTAL_HTML_TEMPLATE)
        elif path == "/api/data":
            data = get_portal_data(self.eidos_dir)
            self._send_json(data)
        elif path == "/api/overview":
            data = get_portal_data(self.eidos_dir)
            self._send_json(data.get("overview", {}))
        elif path == "/api/validate":
            validator = SpecValidator()
            report = validator.validate_directory(self.eidos_dir)
            res = {
                "total_docs": report.total_docs,
                "valid_docs": report.valid_docs,
                "is_clean": report.is_clean,
                "schema_errors": report.schema_errors,
                "broken_references": report.broken_references,
            }
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
