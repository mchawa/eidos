"""Local HTTP server hosting the interactive Eidos Living Web Portal."""

import logging
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import orjson

from eidos.core.spec.validator import SpecValidator
from eidos.portal.assets import get_portal_favicon_svg, get_portal_html_template
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
    static_dir: Path | None = None
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
        path = urllib.parse.unquote(parsed_url.path)

        if path in ("/", "/index.html"):
            self._send_html(get_portal_html_template())
        elif path in ("/favicon.svg", "/favicon.ico"):
            self._send_svg(get_portal_favicon_svg())
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
        elif self.static_dir is not None:
            import mimetypes

            static_root = self.static_dir.resolve()
            rel_path = path.lstrip("/")
            full_path = (static_root / rel_path).resolve()
            if not full_path.resolve().is_relative_to(static_root):
                self.send_error(HTTPStatus.FORBIDDEN, "Access denied.")
                return
            if full_path.is_file():
                payload = full_path.read_bytes()
                mime_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND, f"Path '{path}' not found.")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"Path '{path}' not found.")

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed_url.path)

        if path != "/api/validate":
            self.send_error(HTTPStatus.NOT_FOUND, f"Path '{path}' not found.")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length header.")
            return
        if content_length < 0 or content_length > 10 * 1024 * 1024:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid or excessive Content-Length header.")
            return
        payload: dict[str, Any] = {}
        if content_length > 0:
            raw_body = self.rfile.read(content_length)
            try:
                parsed = orjson.loads(raw_body)
                if isinstance(parsed, dict):
                    payload = parsed
            except (UnicodeDecodeError, orjson.JSONDecodeError, ValueError):
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON payload.")
                return

        resolved_eidos = self.eidos_dir.resolve()
        target_dir_str = str(payload.get("dir") or resolved_eidos.name)
        raw_target = Path(target_dir_str)
        target_dir = (
            raw_target.resolve() if raw_target.is_absolute() else (resolved_eidos.parent / raw_target).resolve()
        )
        if target_dir != resolved_eidos:
            self.send_error(HTTPStatus.FORBIDDEN, "Validation is restricted to the active eidos directory.")
            return

        res = self._get_cached_validation()
        self._send_json(res)

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
        payload = orjson.dumps(data, option=orjson.OPT_INDENT_2)
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
