"""Static site documentation generator exporting the Living Web Portal to a standalone folder."""

import hashlib
import re
from pathlib import Path
from typing import Any

import orjson

from eidos.portal.assets import get_portal_favicon_svg, get_portal_html_template
from eidos.portal.generator import get_portal_data

_SLIM_FRONTMATTER_DROP_KEYS: frozenset[str] = frozenset(
    {"mutations", "decisions", "invariants", "technical", "c4_context"}
)


def _deduplicate_portal_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate multi-kilobyte AsciiDoc bodies that are already stored in data['specs'].

    Single-source spec body hydration (DEC-PORTAL-PAYLOAD-DEDUP-AND-CLIENT-BFS,
    INV-PORTAL-STATIC-SCALABILITY): each specification's AsciiDoc prose lives once in
    `data["specs"][]` and is hydrated by ID on the client, cutting `window.__EIDOS_DATA__`
    and `data.json` payload size by ~40-50%.
    """
    out = dict(data)
    spec_bodies: dict[str, str] = {
        str(s.get("id")): str(s.get("body") or "")
        for s in (data.get("specs") or [])
        if isinstance(s, dict) and s.get("id")
    }

    def _strip_field(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                result.append(item)
                continue
            item_copy = dict(item)
            iid = str(item_copy.get("id") or "")
            if iid in spec_bodies and item_copy.get(field) == spec_bodies[iid]:
                item_copy.pop(field, None)
            if field == "description" and isinstance(item_copy.get("internal_components"), list):
                new_ic: list[dict[str, Any]] = []
                for ic in item_copy["internal_components"]:
                    if isinstance(ic, dict):
                        ic_copy = dict(ic)
                        ic_id = str(ic_copy.get("id") or "")
                        if ic_id in spec_bodies and ic_copy.get("description") == spec_bodies[ic_id]:
                            ic_copy.pop("description", None)
                        new_ic.append(ic_copy)
                    else:
                        new_ic.append(ic)
                item_copy["internal_components"] = new_ic
            result.append(item_copy)
        return result

    if isinstance(out.get("system_architecture"), dict):
        sa = dict(out["system_architecture"])
        sa_id = str(sa.get("id") or "")
        if sa_id in spec_bodies and sa.get("body") == spec_bodies[sa_id]:
            sa.pop("body", None)
        out["system_architecture"] = sa

    out["components"] = _strip_field(out.get("components") or [], "description")
    out["contracts"] = _strip_field(out.get("contracts") or [], "description")
    for coll in (
        "domains",
        "capabilities",
        "personas",
        "policies",
        "explorations",
        "clients",
        "flows",
        "ui_components",
        "screens",
    ):
        if isinstance(out.get(coll), list):
            out[coll] = _strip_field(out[coll], "body")

    if isinstance(out.get("deltas"), dict):
        dedup_deltas: dict[str, list[dict[str, Any]]] = {}
        for status_key, d_list in out["deltas"].items():
            dedup_deltas[status_key] = _strip_field(d_list if isinstance(d_list, list) else [], "body")
        out["deltas"] = dedup_deltas

    return out


def _serialize_script_json(payload: dict[str, Any]) -> str:
    """Serialize JSON safely for embedding inside an HTML <script> element."""
    return (
        orjson.dumps(payload)
        .decode("utf-8")
        .replace("</", "<\\/")
        .replace("<!--", "\\u003c!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _spec_chunk_filename(spec_id: str, used_lower: set[str]) -> str:
    """Build a deterministic URL-safe JSON chunk filename for a specification ID."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", spec_id.strip()).strip("._") or "spec"
    candidate = f"{base}.json"
    if candidate.lower() in used_lower:
        digest = hashlib.sha256(spec_id.encode("utf-8")).hexdigest()[:8]
        candidate = f"{base}-{digest}.json"
    used_lower.add(candidate.lower())
    return candidate


def _extract_bundle_assets_to_local_files(template_html: str, assets_dir: Path) -> str:
    """Extract inline module JS and CSS from PORTAL_HTML_TEMPLATE into local hashed ./assets/* files."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    shell_html = template_html

    # 1. Extract <script type="module" ...>...</script> FIRST (before <style> regex, because JS contains '<style>' strings)
    def _replace_module_script(match: re.Match[str]) -> str:
        js_body = match.group(1)
        js_bytes = js_body.encode("utf-8")
        js_hash = hashlib.sha256(js_bytes).hexdigest()[:12]
        js_name = f"portal-{js_hash}.js"
        (assets_dir / js_name).write_bytes(js_bytes)
        return f'<script type="module" src="./assets/{js_name}"></script>'

    shell_html = re.sub(
        r'<script\b[^>]*\btype=["\']module["\'][^>]*>(.*?)</script>',
        _replace_module_script,
        shell_html,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 2. Extract inline <style ...>...</style> blocks into hashed local CSS files
    css_chunks: list[str] = []

    def _collect_style(match: re.Match[str]) -> str:
        css_chunks.append(match.group(1))
        return "<!--__EIDOS_EXTRACTED_STYLE__-->"

    shell_html = re.sub(
        r"<style\b[^>]*>(.*?)</style>",
        _collect_style,
        shell_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if css_chunks:
        combined_css = "\n".join(css_chunks).encode("utf-8")
        css_hash = hashlib.sha256(combined_css).hexdigest()[:12]
        css_name = f"portal-{css_hash}.css"
        (assets_dir / css_name).write_bytes(combined_css)
        link_tag = f'<link rel="stylesheet" href="./assets/{css_name}">'
        shell_html = shell_html.replace("<!--__EIDOS_EXTRACTED_STYLE__-->", link_tag, 1)
        shell_html = shell_html.replace("<!--__EIDOS_EXTRACTED_STYLE__-->", "")

    shell_html = shell_html.replace('href="/favicon.ico"', 'href="./favicon.svg"')
    return shell_html


def _build_chunked_bootstrap_payload(data: dict[str, Any], specs_dir: Path) -> dict[str, Any]:
    """Write per-spec JSON body chunks under specs/ and return a compact bootstrap payload for index.html."""
    specs_dir.mkdir(parents=True, exist_ok=True)
    bootstrap = dict(data)
    used_names: set[str] = set()
    slim_specs: list[dict[str, Any]] = []

    for idx, raw_spec in enumerate(data.get("specs") or []):
        if not isinstance(raw_spec, dict):
            continue
        spec_id = str(raw_spec.get("id") or f"spec-{idx + 1}")
        chunk_name = _spec_chunk_filename(spec_id, used_names)
        chunk_rel = f"specs/{chunk_name}"
        full_fm = raw_spec.get("frontmatter") if isinstance(raw_spec.get("frontmatter"), dict) else {}
        chunk_payload = {
            "id": spec_id,
            "body": str(raw_spec.get("body") or ""),
            "frontmatter": full_fm,
        }
        (specs_dir / chunk_name).write_bytes(orjson.dumps(chunk_payload))

        slim_fm = {k: v for k, v in full_fm.items() if k not in _SLIM_FRONTMATTER_DROP_KEYS}
        if isinstance(slim_fm.get("steps"), list):
            slim_fm["steps"] = [
                {"client": st.get("client")}
                for st in slim_fm["steps"]
                if isinstance(st, dict) and st.get("client")
            ]

        spec_copy = dict(raw_spec)
        spec_copy["body"] = ""
        spec_copy["body_chunk"] = chunk_rel
        spec_copy["frontmatter"] = slim_fm
        slim_specs.append(spec_copy)

    bootstrap["specs"] = slim_specs

    # Compact historical applied/rejected/archived delta mutation details in inline bootstrap
    if isinstance(data.get("deltas"), dict):
        slim_deltas: dict[str, list[dict[str, Any]]] = {}
        for status_key, d_list in data["deltas"].items():
            if not isinstance(d_list, list):
                slim_deltas[status_key] = []
                continue
            new_d_list: list[dict[str, Any]] = []
            for d_item in d_list:
                if not isinstance(d_item, dict):
                    continue
                d_copy = dict(d_item)
                is_historical = bool(d_copy.get("is_archived")) or status_key in ("applied", "rejected")
                if is_historical and isinstance(d_copy.get("mutations"), list):
                    d_copy["mutations"] = [
                        {
                            "operation": m.get("operation", "modify"),
                            "target": m.get("target", ""),
                            **({"title": m["title"]} if m.get("title") else {}),
                        }
                        for m in d_copy["mutations"]
                        if isinstance(m, dict)
                    ]
                new_d_list.append(d_copy)
            slim_deltas[status_key] = new_d_list
        bootstrap["deltas"] = slim_deltas

    return bootstrap


def build_static_portal(eidos_dir: Path, output_dir: Path, *, single_file: bool = False) -> Path:
    """Export the Eidos Living Web Portal as a local static site (chunked by default, or --single-file)."""
    eidos_path = eidos_dir.resolve()
    out_path = output_dir.resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Generate full portal data payload and deduplicate spec bodies
    raw_data = get_portal_data(eidos_path)
    data = _deduplicate_portal_payload(raw_data)

    # 2. Write complete standalone data.json for programmatic consumers
    data_file = out_path / "data.json"
    data_file.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

    # 3. Emit either single-file archive or chunked local static site (zero remote URLs)
    template_html = get_portal_html_template()
    if single_file:
        safe_json = _serialize_script_json(data)
        injected_data_script = f"<script>window.__EIDOS_DATA__ = {safe_json};</script>\n</head>"
        static_html = template_html.replace("</head>", injected_data_script, 1)
    else:
        assets_dir = out_path / "assets"
        shell_html = _extract_bundle_assets_to_local_files(template_html, assets_dir)
        bootstrap_data = _build_chunked_bootstrap_payload(data, out_path / "specs")
        data_js = f"window.__EIDOS_DATA__ = {_serialize_script_json(bootstrap_data)};\n".encode()
        data_hash = hashlib.sha256(data_js).hexdigest()[:12]
        data_js_name = f"portal-data-{data_hash}.js"
        (assets_dir / data_js_name).write_bytes(data_js)

        inline_index: dict[str, Any] = {
            "overview": bootstrap_data.get("overview", {}),
            "validation": bootstrap_data.get("validation", {}),
            "policies": bootstrap_data.get("policies", []),
            "specs": [
                {
                    "id": s.get("id", ""),
                    "title": s.get("title", ""),
                    "type": s.get("type", ""),
                    "path": s.get("path", ""),
                    "is_valid": s.get("is_valid", True),
                    "body": "",
                    "body_chunk": s.get("body_chunk", ""),
                    **({"errors": s["errors"]} if s.get("errors") else {}),
                }
                for s in (bootstrap_data.get("specs") or [])
                if isinstance(s, dict)
            ],
        }
        inline_json = _serialize_script_json(inline_index)
        injected_data_script = (
            f'<script src="./assets/{data_js_name}"></script>\n'
            f"<script>window.__EIDOS_DATA__ = Object.assign({inline_json}, window.__EIDOS_DATA__ || {{}});</script>\n"
            f"</head>"
        )
        static_html = shell_html.replace("</head>", injected_data_script, 1)

    index_file = out_path / "index.html"
    index_file.write_text(static_html, encoding="utf-8")

    # 4. Write favicon.svg for offline static site asset resolution
    favicon_file = out_path / "favicon.svg"
    favicon_file.write_text(get_portal_favicon_svg(), encoding="utf-8")

    return out_path
