"""Local HTTP server hosting the interactive Eidos Living Web Portal."""

import logging
import os
import re
import select
import socket
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import orjson

from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import find_repo_root
from eidos.portal.assets import get_portal_favicon_svg, get_portal_html_template
from eidos.portal.generator import _to_repo_rel_posix, get_portal_data

logger = logging.getLogger("eidos.portal")

_SNAPSHOT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".cache",
        "dist",
        "site",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        "htmlcov",
    }
)
_SNAPSHOT_SKIP_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo", ".pyd", ".swp", ".sqlite", ".sqlite3", ".db"})
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _discover_governed_roots(eidos_path: Path, repo_root: Path) -> list[Path]:
    """Return deduplicated governed directories (eidos/, src/, tests/, and declared container source_path roots)."""
    candidates: list[Path] = [eidos_path]
    for std_dir in ("src", "tests"):
        cand = repo_root / std_dir
        if cand.is_dir():
            candidates.append(cand)

    containers_dir = eidos_path / "architecture" / "containers"
    if containers_dir.is_dir():
        for adoc_file in containers_dir.glob("*.adoc"):
            try:
                head = adoc_file.read_text(encoding="utf-8", errors="ignore")[:4096]
            except OSError:
                continue
            for m in re.finditer(r"source_path\s*:\s*(.+)", head):
                raw_val = m.group(1).strip()
                tokens = re.findall(r'["\']([^"\']+)["\']', raw_val) or [raw_val.strip("[] ")]
                for tok in tokens:
                    cleaned = tok.strip()
                    if not cleaned or cleaned == "N/A" or cleaned.startswith("-"):
                        continue
                    cand_path = (repo_root / cleaned).resolve()
                    if cand_path.is_relative_to(repo_root) and cand_path.exists():
                        candidates.append(cand_path if cand_path.is_dir() else cand_path.parent)

    resolved_unique: list[Path] = []
    for c in sorted({p.resolve() for p in candidates if p.exists()}, key=lambda p: len(p.parts)):
        if not any(c != parent and c.is_relative_to(parent) for parent in resolved_unique):
            resolved_unique.append(c)
    return resolved_unique


def _resolve_portal_repo_root(eidos_resolved: Path) -> Path:
    """Resolve the governing repository root for a resolved eidos/ directory."""
    if eidos_resolved.name == "eidos":
        return eidos_resolved.parent.resolve()
    return find_repo_root(eidos_resolved)


def _compute_eidos_mtime_snapshot(eidos_dir: Path) -> tuple[int, int, int]:
    """Compute a lightweight (file_count, max_mtime_ns, total_size) snapshot across eidos/, src/, and tests/."""
    if not eidos_dir.is_dir():
        return (0, 0, 0)
    eidos_resolved = eidos_dir.resolve()
    repo_root = _resolve_portal_repo_root(eidos_resolved)
    roots = _discover_governed_roots(eidos_resolved, repo_root)

    count = 0
    max_mtime = 0
    total_size = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SNAPSHOT_SKIP_DIRS]
            for fname in filenames:
                if fname == ".coverage" or fname.endswith("~"):
                    continue
                lower_ext = os.path.splitext(fname)[1].lower()
                if lower_ext in _SNAPSHOT_SKIP_SUFFIXES:
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    st = os.stat(fpath)
                except OSError:
                    continue
                count += 1
                if st.st_mtime_ns > max_mtime:
                    max_mtime = st.st_mtime_ns
                total_size += st.st_size
    return (count, max_mtime, total_size)


class PortalThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server using daemon worker threads so SSE streams never block shutdown."""

    daemon_threads = True
    allow_reuse_address = True
    portal_bound_host: str = "127.0.0.1"
    _portal_shutting_down: bool = False

    def server_close(self) -> None:
        self._portal_shutting_down = True
        super().server_close()


class EidosPortalHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler dispatching Portal UI, SSE stream, and JSON API endpoints."""

    eidos_dir: Path = Path("eidos")
    static_dir: Path | None = None
    _sse_poll_interval: float = 0.5
    _cache_lock: threading.Lock = threading.Lock()
    _cache_key: tuple[str, int, int, int] | None = None
    _cached_portal_data: dict[str, Any] | None = None
    _cached_validation_key: tuple[str, int, int, int] | None = None
    _cached_validation_res: dict[str, Any] | None = None
    _cached_drift_key: tuple[str, int, int, int] | None = None
    _cached_drift_res: dict[str, Any] | None = None
    _security_headers_sent: bool = False

    def send_response(self, code: int, message: str | None = None) -> None:
        self._security_headers_sent = False
        super().send_response(code, message)

    def end_headers(self) -> None:
        if not getattr(self, "_security_headers_sent", False):
            self._security_headers_sent = True
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        super().end_headers()

    def _is_allowed_host(self, raw_host_header: str | None) -> bool:
        """Validate HTTP Host header against bound host:port to prevent DNS rebinding."""
        if not raw_host_header or not raw_host_header.strip():
            return False
        raw = raw_host_header.strip().lower()
        if any(ch in raw for ch in ("/", "\\", "@", " ", "\t")):
            return False

        req_host: str
        req_port: int | None
        if raw.startswith("["):
            end_bracket = raw.find("]")
            if end_bracket == -1:
                return False
            req_host = raw[: end_bracket + 1]
            rest = raw[end_bracket + 1 :]
            if rest == "":
                req_port = None
            elif rest.startswith(":") and rest[1:].isdigit():
                req_port = int(rest[1:])
            else:
                return False
        elif ":" in raw:
            host_part, port_part = raw.rsplit(":", 1)
            if not host_part or not port_part.isdigit():
                return False
            req_host = host_part
            req_port = int(port_part)
        else:
            req_host = raw
            req_port = None

        srv_addr = getattr(self.server, "server_address", ("127.0.0.1", 0))
        bound_host = str(srv_addr[0]).strip().lower() if srv_addr else "127.0.0.1"
        bound_port = int(srv_addr[1]) if srv_addr and len(srv_addr) > 1 else 0
        explicit_host = str(getattr(self.server, "portal_bound_host", "") or "").strip().lower()

        if bound_host in _LOOPBACK_HOSTS or explicit_host in _LOOPBACK_HOSTS:
            allowed_hosts = {"127.0.0.1", "localhost", "[::1]"}
        elif bound_host in ("0.0.0.0", "::", ""):
            allowed_hosts = {"127.0.0.1", "localhost", "[::1]", bound_host}
            if explicit_host and explicit_host not in ("0.0.0.0", "::"):
                allowed_hosts.add(explicit_host)
        else:
            allowed_hosts = {bound_host, f"[{bound_host}]"}
            if explicit_host:
                allowed_hosts.add(explicit_host)

        if req_host not in allowed_hosts:
            return False
        return not (req_port is not None and bool(bound_port) and req_port != bound_port)

    def _check_request_isolation(self, path: str) -> bool:
        """Enforce Host header validation on all routes and Sec-Fetch-Site isolation on /api/*."""
        if not self._is_allowed_host(self.headers.get("Host")):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden Host header.")
            return False
        if path == "/api" or path.startswith("/api/"):
            sec_fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if sec_fetch_site == "cross-site":
                self.send_error(HTTPStatus.FORBIDDEN, "Cross-site API requests are forbidden.")
                return False
        return True

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
        eidos_resolved = self.eidos_dir.resolve()
        repo_root_resolved = _resolve_portal_repo_root(eidos_resolved)
        snap = (str(eidos_resolved), *_compute_eidos_mtime_snapshot(self.eidos_dir))
        with EidosPortalHandler._cache_lock:
            if (
                EidosPortalHandler._cached_validation_key == snap
                and EidosPortalHandler._cached_validation_res is not None
            ):
                return EidosPortalHandler._cached_validation_res
            validator = SpecValidator()
            report = validator.validate_directory(self.eidos_dir)
            normalized_errors: dict[str, list[str]] = {}
            for raw_k, err_list in (report.schema_errors or {}).items():
                norm_k = _to_repo_rel_posix(raw_k, repo_root_resolved)
                normalized_errors.setdefault(norm_k, []).extend(err_list)
            res = {
                "total_docs": report.total_docs,
                "valid_docs": report.valid_docs,
                "is_clean": report.is_clean,
                "schema_errors": normalized_errors,
                "broken_references": report.broken_references,
            }
            EidosPortalHandler._cached_validation_key = snap
            EidosPortalHandler._cached_validation_res = res
            return res

    def _get_cached_drift(self) -> dict[str, Any]:
        from eidos.vcs.drift import detect_drift

        eidos_resolved = self.eidos_dir.resolve()
        snap = (str(eidos_resolved), *_compute_eidos_mtime_snapshot(self.eidos_dir))
        with EidosPortalHandler._cache_lock:
            if EidosPortalHandler._cached_drift_key == snap and EidosPortalHandler._cached_drift_res is not None:
                return EidosPortalHandler._cached_drift_res
            try:
                res_obj = detect_drift(
                    _resolve_portal_repo_root(eidos_resolved),
                    eidos_dir=eidos_resolved,
                    uncommitted=True,
                )
                cls = res_obj.classification
                changed_files = list(
                    dict.fromkeys(
                        cls.code_files
                        + cls.spec_files
                        + cls.delta_files
                        + cls.test_files
                        + cls.doc_files
                        + cls.other_files
                    )
                )
                res = {
                    "is_clean": res_obj.is_clean,
                    "drift_detected": res_obj.drift_detected,
                    "workflow_mode": res_obj.workflow_mode,
                    "touched_components": res_obj.touched_components,
                    "changed_files": changed_files,
                    "unmapped_files": list(res_obj.unmapped_code_files),
                    "validation_passed": res_obj.validation_passed,
                    "validation_errors": res_obj.validation_errors,
                    "uncovered_invariants": [inv.invariant_id for inv in res_obj.uncovered_invariants],
                    "orphan_invariants": [
                        {"test_id": loc, "invariant_id": inv_id}
                        for inv_id, loc in res_obj.orphan_invariants
                    ],
                    "remediation_suggestions": res_obj.remediation_suggestions,
                    "summary_markdown": res_obj.summary_markdown,
                }
            except Exception as exc:
                return {
                    "is_clean": False,
                    "drift_detected": True,
                    "workflow_mode": "unknown",
                    "touched_components": [],
                    "changed_files": [],
                    "unmapped_files": [],
                    "validation_passed": False,
                    "validation_errors": [str(exc)],
                    "uncovered_invariants": [],
                    "orphan_invariants": [],
                    "remediation_suggestions": [
                        "Inspect repository and Git workspace state; drift check failed closed."
                    ],
                    "summary_markdown": f"Drift check failed: {exc}",
                    "error": str(exc),
                }
            EidosPortalHandler._cached_drift_key = snap
            EidosPortalHandler._cached_drift_res = res
            return res

    def _build_sse_status_payload(self, snap_tuple: tuple[int, int, int]) -> dict[str, Any]:
        count, max_mtime, total_size = snap_tuple
        return {
            "version": {"count": count, "max_mtime_ns": max_mtime, "total_size": total_size},
            "drift": self._get_cached_drift(),
        }

    def _handle_sse_events(self, query: str) -> None:
        qs = urllib.parse.parse_qs(query)
        once = qs.get("once", ["0"])[0].strip().lower() in ("1", "true", "yes")
        max_events_raw = qs.get("max_events", [None])[0]
        max_events = int(max_events_raw) if (max_events_raw and str(max_events_raw).isdigit()) else (1 if once else 0)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close" if max_events == 1 else "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            last_snap = _compute_eidos_mtime_snapshot(self.eidos_dir)
            initial_json = orjson.dumps(self._build_sse_status_payload(last_snap)).decode("utf-8")
            self.wfile.write(f"event: status\ndata: {initial_json}\n\n".encode())
            self.wfile.flush()
            emitted = 1
            if max_events and emitted >= max_events:
                self.close_connection = True
                return

            poll_interval = max(0.02, float(self._sse_poll_interval))
            while not getattr(self.server, "_portal_shutting_down", False):
                try:
                    readable, _, _ = select.select([self.connection], [], [], poll_interval)
                    if readable:
                        peek = self.connection.recv(1, socket.MSG_PEEK)
                        if not peek:
                            break
                except BlockingIOError:
                    pass
                except (OSError, ValueError, TypeError):
                    time.sleep(poll_interval)

                if getattr(self.server, "_portal_shutting_down", False):
                    break

                curr_snap = _compute_eidos_mtime_snapshot(self.eidos_dir)
                if curr_snap != last_snap:
                    last_snap = curr_snap
                    update_json = orjson.dumps(self._build_sse_status_payload(curr_snap)).decode("utf-8")
                    self.wfile.write(f"event: status\ndata: {update_json}\n\n".encode())
                    self.wfile.flush()
                    emitted += 1
                    if max_events and emitted >= max_events:
                        break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.close_connection = True

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed_url.path)
        if not self._check_request_isolation(path):
            return

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
        elif path == "/api/version":
            count, max_mtime, total_size = _compute_eidos_mtime_snapshot(self.eidos_dir)
            self._send_json({"count": count, "max_mtime_ns": max_mtime, "total_size": total_size})
        elif path == "/api/validate":
            res = self._get_cached_validation()
            self._send_json(res)
        elif path == "/api/drift":
            res = self._get_cached_drift()
            self._send_json(res)
        elif path == "/api/events":
            self._handle_sse_events(parsed_url.query)
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
        if not self._check_request_isolation(path):
            return

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
    httpd = PortalThreadingHTTPServer(server_address, EidosPortalHandler)
    httpd.portal_bound_host = host

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
