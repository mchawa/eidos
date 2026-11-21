"""Fast in-process QuickJS Mermaid diagram syntax validator (CMP-MERMAID-VALIDATOR).

Extracts `[mermaid]` and `[source,mermaid]` blocks from AsciiDoc specification bodies and validates
them via `mermaid.parse()` (`securityLevel: 'strict'`) inside a dedicated, lock-serialized
`quickjs-ng` worker thread backed by pinned `assets/mermaid/mermaid.min.js.gz` and a stateless
SHA-256 SQLite cache (`mermaid_validation_cache`). Degrades cleanly to a conservative pure-Python
structural validator (`structural-v1`) when `quickjs-ng` is unavailable.
"""

from __future__ import annotations

import atexit
import contextlib
import gzip
import hashlib
import json
import os
import queue
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eidos.core.workspace.assets import get_mermaid_bundle_path

MERMAID_BUNDLE_VERSION = "12.1.0"
MERMAID_BUNDLE_SHA256 = "21007c7d6934db06f60540ba97e84bb20a9f8d55243a7234dfc394985e7a86bc"
STRUCTURAL_FALLBACK_ENGINE_VERSION = "structural-v1"

_MERMAID_MEMORY_LIMIT_BYTES = 128 * 1024 * 1024
_MERMAID_STACK_LIMIT_BYTES = 4 * 1024 * 1024
_MERMAID_WORKER_STACK_BYTES = 8 * 1024 * 1024
_MERMAID_TIME_LIMIT_SECONDS = 5

_MERMAID_ATTR_RE = re.compile(
    r"^\[\s*(?:(mermaid)|(source)\s*,\s*mermaid)(?:\s*,[^\]]*)?\s*\]$",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^(-{4,}|\.{4,})$")
_COMMENT_FENCE_RE = re.compile(r"^(/{4,})$")
_PASSTHROUGH_FENCE_RE = re.compile(r"^(\+{4,})$")
_BLOCK_META_LINE_RE = re.compile(r"^(?:\.[^.\s].*|\[[^\]]+\]|:[A-Za-z0-9_-]+:.*)$")

_RECOGNIZED_MERMAID_HEADERS: tuple[str, ...] = (
    "flowchart",
    "graph",
    "sequencediagram",
    "classdiagram",
    "classdiagram-v2",
    "statediagram",
    "statediagram-v2",
    "erdiagram",
    "gantt",
    "pie",
    "journey",
    "gitgraph",
    "mindmap",
    "timeline",
    "quadrantchart",
    "requirementdiagram",
    "c4context",
    "c4container",
    "c4component",
    "c4dynamic",
    "c4deployment",
    "sankey-beta",
    "xychart-beta",
    "block-beta",
    "packet-beta",
    "kanban",
    "architecture-beta",
    "zenuml",
    "radar-beta",
    "treemap-beta",
    "info",
)

_QUICKJS_DOM_SHIM = """
globalThis.window = globalThis;
globalThis.self = globalThis;
globalThis.addEventListener = function() {};
globalThis.removeEventListener = function() {};
globalThis.navigator = { userAgent: "eidos-quickjs" };
globalThis.location = {
  href: "http://localhost/",
  protocol: "http:",
  host: "localhost",
  hostname: "localhost",
  pathname: "/",
  search: "",
  hash: "",
};
function _mkEl(tag) {
  const attrs = {};
  const style = {};
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    nodeName: String(tag || "div").toUpperCase(),
    nodeType: 1,
    attributes: attrs,
    style,
    children: [],
    childNodes: [],
    innerHTML: "",
    textContent: "",
    className: "",
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k); },
    appendChild(c) { el.children.push(c); el.childNodes.push(c); return c; },
    removeChild(c) { return c; },
    insertBefore(c) { el.children.push(c); el.childNodes.push(c); return c; },
    cloneNode() { return _mkEl(tag); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getElementsByTagName() { return []; },
    getElementsByClassName() { return []; },
    getBoundingClientRect() { return { x: 0, y: 0, width: 100, height: 20, top: 0, left: 0, right: 100, bottom: 20 }; },
    getBBox() { return { x: 0, y: 0, width: 100, height: 20 }; },
    getComputedTextLength() { return 50; },
    addEventListener() {},
    removeEventListener() {},
    remove() {},
  };
  return el;
}
globalThis.document = {
  nodeType: 9,
  documentElement: _mkEl("html"),
  head: _mkEl("head"),
  body: _mkEl("body"),
  implementation: { createHTMLDocument() { return globalThis.document; } },
  createElement: _mkEl,
  createElementNS(_ns, tag) { return _mkEl(tag); },
  createTextNode(t) { return { nodeType: 3, textContent: String(t || "") }; },
  createDocumentFragment() { return _mkEl("#fragment"); },
  getElementById() { return null; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  getElementsByTagName() { return []; },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.DOMParser = function() {
  return { parseFromString() { return globalThis.document; } };
};
globalThis.Element = function() {};
globalThis.HTMLElement = function() {};
globalThis.SVGElement = function() {};
globalThis.Node = { ELEMENT_NODE: 1, TEXT_NODE: 3, DOCUMENT_NODE: 9 };
globalThis.performance = { now() { return Date.now(); } };
globalThis.requestAnimationFrame = function() { return 0; };
globalThis.cancelAnimationFrame = function() {};
globalThis.setTimeout = function() { return 0; };
globalThis.clearTimeout = function() {};
globalThis.console = { log() {}, warn() {}, error() {}, debug() {}, info() {} };
globalThis.structuredClone = function(v) { return JSON.parse(JSON.stringify(v)); };
"""

_QUICKJS_PARSE_HARNESS = """
mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
globalThis.__eidos_parse = function(code) {
  globalThis.__eidos_res = null;
  try {
    const p = mermaid.parse(code);
    if (p && typeof p.then === "function") {
      p.then(
        () => { globalThis.__eidos_res = JSON.stringify({ ok: true, err: "" }); },
        (e) => { globalThis.__eidos_res = JSON.stringify({ ok: false, err: String(e && e.message ? e.message : e) }); }
      );
    } else {
      globalThis.__eidos_res = JSON.stringify({ ok: Boolean(p), err: p ? "" : "mermaid.parse() returned false" });
    }
  } catch (e) {
    globalThis.__eidos_res = JSON.stringify({ ok: false, err: String(e && e.message ? e.message : e) });
  }
};
"""


@dataclass(frozen=True)
class MermaidBlock:
    """An extracted `[mermaid]` or `[source,mermaid]` AsciiDoc block."""

    code: str
    body_line: int
    header_kind: str = "mermaid"
    sha256: str = ""
    unclosed: bool = False

    def __post_init__(self) -> None:
        if not self.sha256:
            object.__setattr__(self, "sha256", hashlib.sha256(self.code.encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class MermaidValidationResult:
    """Validation verdict for a single extracted Mermaid diagram block."""

    block: MermaidBlock
    is_valid: bool
    error_message: str = ""
    from_cache: bool = False


def extract_mermaid_blocks(body: str) -> list[MermaidBlock]:
    """Extract `[mermaid]` and `[source,mermaid]` AsciiDoc blocks from a spec body.

    Handles optional block attributes, variable-length matched `----` / `....` fences,
    skips `////` comment blocks, single-line `//` comments, and non-mermaid delimited listing
    or passthrough blocks, preserves empty blocks for explicit validation errors, and records
    1-based body line numbers.
    """
    if not body or "mermaid" not in body.lower():
        return []

    lines = body.splitlines()
    blocks: list[MermaidBlock] = []
    idx = 0
    total = len(lines)

    while idx < total:
        stripped = lines[idx].strip()

        # Skip AsciiDoc block comments (//// ... ////)
        m_comment = _COMMENT_FENCE_RE.match(stripped)
        if m_comment:
            fence = m_comment.group(1)
            idx += 1
            while idx < total and lines[idx].strip() != fence:
                idx += 1
            idx += 1
            continue

        # Skip single-line AsciiDoc comments
        if stripped.startswith("//"):
            idx += 1
            continue

        # Skip non-mermaid delimited listing/literal/passthrough blocks so embedded examples aren't parsed
        m_other_fence = _FENCE_RE.match(stripped) or _PASSTHROUGH_FENCE_RE.match(stripped)
        if m_other_fence:
            fence = m_other_fence.group(1)
            idx += 1
            while idx < total and lines[idx].strip() != fence:
                idx += 1
            idx += 1
            continue

        m_attr = _MERMAID_ATTR_RE.match(stripped)
        if not m_attr:
            idx += 1
            continue

        header_kind = "source,mermaid" if m_attr.group(2) else "mermaid"
        attr_line_1based = idx + 1
        scan = idx + 1
        # Allow optional AsciiDoc block title (`.Title`) or extra attribute lines before opening fence
        while scan < total and (lines[scan].strip().startswith("//") or _BLOCK_META_LINE_RE.match(lines[scan].strip())):
            scan += 1
        if scan >= total:
            idx += 1
            continue

        m_open = _FENCE_RE.match(lines[scan].strip())
        if not m_open:
            idx += 1
            continue

        fence = m_open.group(1)
        content_start = scan + 1
        content_end = content_start
        closed = False
        while content_end < total:
            if lines[content_end].strip() == fence:
                closed = True
                break
            content_end += 1

        raw_code = "\n".join(lines[content_start:content_end])
        blocks.append(
            MermaidBlock(
                code=raw_code,
                body_line=content_start + 1 if closed or content_start < total else attr_line_1based,
                header_kind=header_kind,
                unclosed=not closed,
            )
        )
        idx = content_end + 1 if closed else total

    return blocks


def resolve_adoc_line_offset(file_path: Path | None, body_line: int) -> int:
    """Lazily resolve a 1-based body line number to its 1-based line number in ``file_path``.

    Called only when a Mermaid validation failure occurs so valid documents incur zero extra disk I/O.
    """
    if file_path is None:
        return body_line
    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return body_line

    raw_lines = raw_text.splitlines()
    if not raw_lines or raw_lines[0].strip() != "---":
        return body_line

    # Walk YAML frontmatter identically to parser.py until closing `---` or `...`
    for i in range(1, len(raw_lines)):
        if raw_lines[i].strip() in ("---", "..."):
            # `parse_spec_file` strips leading blank lines after frontmatter? Let's check exact offset.
            body_start_idx = i + 1
            while body_start_idx < len(raw_lines) and raw_lines[body_start_idx] == "":
                # Only advance if parser strips leading newlines; parser uses `raw[end:].lstrip('\r\n')`
                body_start_idx += 1
            return body_start_idx + body_line
    return body_line


def _is_quickjs_available() -> bool:
    try:
        import quickjs  # noqa: F401

        return True
    except ImportError:
        return False


def _bundle_digest_hex() -> str:
    try:
        bundle_path = get_mermaid_bundle_path()
        return hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    except OSError:
        return MERMAID_BUNDLE_SHA256


def get_mermaid_engine_version() -> str:
    """Return the canonical cache key version for the active Mermaid validation backend."""
    if not _is_quickjs_available():
        return STRUCTURAL_FALLBACK_ENGINE_VERSION
    return f"quickjs-mermaid-{MERMAID_BUNDLE_VERSION}-{_bundle_digest_hex()[:12]}"


def _validate_mermaid_structural(code: str) -> tuple[bool, str]:
    """Conservative pure-Python structural Mermaid fallback when `quickjs-ng` is unavailable."""
    stripped = code.strip()
    if not stripped:
        return False, "Mermaid diagram block is empty."

    # Validate any %%{...}%% JSON init directives
    for m_dir in re.finditer(r"%%(\{[\s\S]*?\})%%", stripped):
        payload = re.sub(r"^\{\s*init\s*:", '{"init":', m_dir.group(1).strip())
        try:
            json.loads(payload)
        except json.JSONDecodeError as exc:
            return False, f"Invalid Mermaid directive JSON: {exc.msg}"

    # Find first non-comment, non-directive header token
    cleaned_no_dir = re.sub(r"%%\{[\s\S]*?\}%%", "", stripped)
    header = ""
    effective_lines: list[str] = []
    for raw_line in cleaned_no_dir.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue
        effective_lines.append(line)
        if not header:
            header = line.split()[0].lower()

    if not header:
        return False, "Mermaid diagram block contains no diagram declaration."
    if header not in _RECOGNIZED_MERMAID_HEADERS:
        return False, f"Unrecognized Mermaid diagram header '{header}'."

    # Check balanced double quotes outside comments
    for line_no, line in enumerate(effective_lines, start=1):
        in_quote = False
        escaped = False
        for ch in line:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_quote = not in_quote
        if in_quote:
            return False, f"Unclosed double quote on diagram line {line_no}."

    # Check block nesting balance (`subgraph` / sequence `alt`/`opt`/`loop`/`par`/`critical`/`break`/`rect`/`box` vs `end`)
    open_blocks = 0
    for line in effective_lines[1:]:
        first_tok = line.split()[0].lower()
        if (header in ("flowchart", "graph") and first_tok == "subgraph") or (
            header == "sequencediagram"
            and first_tok in ("alt", "opt", "loop", "par", "critical", "break", "rect", "box")
        ):
            open_blocks += 1
        elif first_tok == "end":
            if open_blocks == 0:
                return False, "Unexpected 'end' without matching opening block."
            open_blocks -= 1
    if open_blocks > 0:
        return False, f"Unclosed Mermaid block: missing {open_blocks} 'end' terminator(s)."

    return True, ""


class _QuickJSMermaidWorker:
    """Dedicated single-thread QuickJS worker ensuring thread-safe serialized `mermaid.parse()` execution."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._req_q: queue.Queue[tuple[str | None, queue.Queue[tuple[bool, str]]]] = queue.Queue()
        self._call_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._init_error: BaseException | None = None
        self._closed = False
        self._thread = self._spawn_thread()
        self._ready_event.wait()
        if self._init_error is not None:
            raise RuntimeError(f"Failed to initialize QuickJS Mermaid worker: {self._init_error}") from self._init_error

    def _spawn_thread(self) -> threading.Thread:
        prev_stack: int | None = None
        with contextlib.suppress(RuntimeError, ValueError):
            prev_stack = threading.stack_size(_MERMAID_WORKER_STACK_BYTES)
        try:
            t = threading.Thread(target=self._run, name="eidos-quickjs-mermaid", daemon=True)
            t.start()
            return t
        finally:
            if prev_stack is not None:
                with contextlib.suppress(RuntimeError, ValueError):
                    threading.stack_size(prev_stack)

    def _load_or_compile_mermaid(self, ctx: Any) -> None:
        bundle_path = get_mermaid_bundle_path()
        gz_bytes = bundle_path.read_bytes()
        digest12 = hashlib.sha256(gz_bytes).hexdigest()[:12]
        bc_path = (self._cache_dir / f"mermaid_parser.{digest12}.qjsbc") if self._cache_dir else None

        if bc_path is not None and bc_path.is_file():
            try:
                ctx.eval_bytecode(bc_path.read_bytes())
                return
            except Exception:
                with contextlib.suppress(OSError):
                    bc_path.unlink(missing_ok=True)

        js_source = gzip.decompress(gz_bytes).decode("utf-8")
        bytecode = ctx.compile(js_source)
        ctx.eval_bytecode(bytecode)

        if bc_path is not None:
            tmp_bc = bc_path.with_suffix(f".qjsbc.tmp.{os.getpid()}")
            try:
                bc_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_bc.write_bytes(bytecode)
                os.replace(tmp_bc, bc_path)
            except OSError:
                with contextlib.suppress(OSError):
                    tmp_bc.unlink(missing_ok=True)

    def _run(self) -> None:
        try:
            import quickjs

            ctx = quickjs.Context()
            ctx.set_memory_limit(_MERMAID_MEMORY_LIMIT_BYTES)
            ctx.set_max_stack_size(_MERMAID_STACK_LIMIT_BYTES)
            ctx.set_time_limit(_MERMAID_TIME_LIMIT_SECONDS)
            ctx.eval(_QUICKJS_DOM_SHIM)
            self._load_or_compile_mermaid(ctx)
            ctx.eval(_QUICKJS_PARSE_HARNESS)
        except BaseException as exc:  # pragma: no cover - defensive boot failure
            self._init_error = exc
            self._ready_event.set()
            return

        self._ready_event.set()

        while True:
            code, reply_q = self._req_q.get()
            if code is None:
                reply_q.put((True, ""))
                break
            try:
                ctx.set_time_limit(_MERMAID_TIME_LIMIT_SECONDS)
                ctx.set("__eidos_code", code)
                ctx.eval("__eidos_parse(__eidos_code)")
                while ctx.execute_pending_job():
                    pass
                raw_res = ctx.get("__eidos_res")
                if not raw_res:
                    reply_q.put((False, "Mermaid parser returned no result."))
                else:
                    parsed = json.loads(str(raw_res))
                    reply_q.put((bool(parsed.get("ok")), str(parsed.get("err") or "")))
            except Exception as exc:
                reply_q.put((False, f"Mermaid QuickJS evaluation error: {exc}"))

    def parse(self, code: str) -> tuple[bool, str]:
        with self._call_lock:
            if self._closed:
                raise RuntimeError("QuickJS Mermaid worker is closed.")
            reply_q: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)
            self._req_q.put((code, reply_q))
            return reply_q.get()

    def close(self) -> None:
        with self._call_lock:
            if self._closed:
                return
            self._closed = True
            reply_q: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)
            self._req_q.put((None, reply_q))
            with contextlib.suppress(Exception):
                reply_q.get(timeout=2.0)


_WORKER_LOCK = threading.Lock()
_WORKER_INSTANCE: _QuickJSMermaidWorker | None = None


def _get_quickjs_worker(cache_dir: Path | None = None) -> _QuickJSMermaidWorker:
    global _WORKER_INSTANCE
    with _WORKER_LOCK:
        if _WORKER_INSTANCE is None or _WORKER_INSTANCE._closed:
            _WORKER_INSTANCE = _QuickJSMermaidWorker(cache_dir=cache_dir)
        return _WORKER_INSTANCE


def _shutdown_quickjs_worker() -> None:
    global _WORKER_INSTANCE
    with _WORKER_LOCK:
        if _WORKER_INSTANCE is not None:
            _WORKER_INSTANCE.close()
            _WORKER_INSTANCE = None


atexit.register(_shutdown_quickjs_worker)


def validate_mermaid_diagram(code: str, *, cache_dir: Path | None = None) -> tuple[bool, str]:
    """Validate a single Mermaid diagram string using QuickJS `mermaid.parse()` or the structural fallback."""
    if not code or not code.strip():
        return False, "Mermaid diagram block is empty."
    if not _is_quickjs_available():
        return _validate_mermaid_structural(code)
    worker = _get_quickjs_worker(cache_dir=cache_dir)
    return worker.parse(code)


def validate_mermaid_blocks(
    blocks: Sequence[MermaidBlock],
    *,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
    preloaded_cache: dict[str, tuple[bool, str]] | None = None,
) -> tuple[list[MermaidValidationResult], list[tuple[str, str, int, str]]]:
    """Validate a batch of extracted Mermaid blocks with SHA-256 SQLite cache hydration and deduplication.

    Never imports `quickjs` or boots the QuickJS VM when all blocks are empty, unclosed, or hit
    `mermaid_validation_cache`. Returns `(results, new_cache_rows)` where `new_cache_rows` contains
    `(diagram_sha256, engine_version, is_valid_int, error_message)` tuples for newly evaluated blocks.
    """
    if not blocks:
        return [], []

    engine_version = get_mermaid_engine_version()
    cache_map: dict[str, tuple[bool, str]]
    managed_db = db_path is not None and preloaded_cache is None
    if preloaded_cache is not None:
        cache_map = dict(preloaded_cache)
    elif db_path is not None:
        from eidos.core.index.storage import load_mermaid_validation_cache

        cache_map = load_mermaid_validation_cache(db_path, engine_version)
    else:
        cache_map = {}

    effective_cache_dir = cache_dir if cache_dir is not None else (db_path.parent if db_path is not None else None)
    new_rows: list[tuple[str, str, int, str]] = []
    evaluated_now: dict[str, tuple[bool, str]] = {}
    results: list[MermaidValidationResult] = []

    for blk in blocks:
        if blk.unclosed:
            results.append(
                MermaidValidationResult(
                    block=blk,
                    is_valid=False,
                    error_message="Unclosed AsciiDoc Mermaid block fence.",
                    from_cache=False,
                )
            )
            continue
        if not blk.code.strip():
            results.append(
                MermaidValidationResult(
                    block=blk,
                    is_valid=False,
                    error_message="Mermaid diagram block is empty.",
                    from_cache=False,
                )
            )
            continue

        cached = cache_map.get(blk.sha256)
        if cached is not None:
            results.append(
                MermaidValidationResult(
                    block=blk,
                    is_valid=cached[0],
                    error_message=cached[1],
                    from_cache=True,
                )
            )
            continue

        already = evaluated_now.get(blk.sha256)
        if already is not None:
            results.append(
                MermaidValidationResult(
                    block=blk,
                    is_valid=already[0],
                    error_message=already[1],
                    from_cache=False,
                )
            )
            continue

        ok, err_msg = validate_mermaid_diagram(blk.code, cache_dir=effective_cache_dir)
        evaluated_now[blk.sha256] = (ok, err_msg)
        cache_map[blk.sha256] = (ok, err_msg)
        new_rows.append((blk.sha256, engine_version, 1 if ok else 0, err_msg))
        results.append(
            MermaidValidationResult(
                block=blk,
                is_valid=ok,
                error_message=err_msg,
                from_cache=False,
            )
        )

    if managed_db and db_path is not None and new_rows:
        from eidos.core.index.storage import save_mermaid_validation_cache

        save_mermaid_validation_cache(db_path, new_rows)

    return results, new_rows
