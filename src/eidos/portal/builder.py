"""Static site documentation generator exporting the Living Web Portal to a standalone folder."""

from pathlib import Path
from typing import Any

import orjson

from eidos.portal.assets import get_portal_favicon_svg, get_portal_html_template
from eidos.portal.generator import get_portal_data


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
    ):
        if isinstance(out.get(coll), list):
            out[coll] = _strip_field(out[coll], "body")

    if isinstance(out.get("deltas"), dict):
        dedup_deltas: dict[str, list[dict[str, Any]]] = {}
        for status_key, d_list in out["deltas"].items():
            dedup_deltas[status_key] = _strip_field(d_list if isinstance(d_list, list) else [], "body")
        out["deltas"] = dedup_deltas

    return out


def build_static_portal(eidos_dir: Path, output_dir: Path) -> Path:
    """Export the Eidos Living Web Portal as a self-contained static site for GitHub/GitLab Pages."""
    eidos_path = eidos_dir.resolve()
    out_path = output_dir.resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Generate full portal data payload and deduplicate spec bodies
    raw_data = get_portal_data(eidos_path)
    data = _deduplicate_portal_payload(raw_data)

    # 2. Write standalone data.json
    data_file = out_path / "data.json"
    data_file.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))

    # 3. Inject data into index.html for zero-fetch offline loading (escape closing </script> and line separators)
    safe_json = (
        orjson.dumps(data)
        .decode("utf-8")
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    injected_data_script = f"<script>window.__EIDOS_DATA__ = {safe_json};</script>\n</head>"
    static_html = get_portal_html_template().replace("</head>", injected_data_script, 1)

    index_file = out_path / "index.html"
    index_file.write_text(static_html, encoding="utf-8")

    # 4. Write favicon.svg for offline static site asset resolution
    favicon_file = out_path / "favicon.svg"
    favicon_file.write_text(get_portal_favicon_svg(), encoding="utf-8")

    return out_path
