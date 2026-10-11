"""Static site documentation generator exporting the Living Web Portal to a standalone folder."""

import json
from pathlib import Path

from eidos.portal.assets import PORTAL_HTML_TEMPLATE
from eidos.portal.generator import get_portal_data


def build_static_portal(eidos_dir: Path, output_dir: Path) -> Path:
    """Export the Eidos Living Web Portal as a self-contained static site for GitHub/GitLab Pages."""
    eidos_path = eidos_dir.resolve()
    out_path = output_dir.resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Generate full portal data payload
    data = get_portal_data(eidos_path)

    # 2. Write standalone data.json
    data_file = out_path / "data.json"
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # 3. Inject data into index.html for zero-fetch offline loading
    injected_data_script = f"<script>window.__EIDOS_DATA__ = {json.dumps(data)};</script>\n</head>"
    static_html = PORTAL_HTML_TEMPLATE.replace("</head>", injected_data_script, 1)

    index_file = out_path / "index.html"
    index_file.write_text(static_html, encoding="utf-8")

    return out_path
