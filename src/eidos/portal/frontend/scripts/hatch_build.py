"""Hatch custom build hook for Eidos Living Web Portal assets (DLT-2026-050).

Ensures ``assets/portal/bundle.html.gz`` and ``assets/portal/favicon.svg`` are synchronized
from ``portal/frontend/dist/index.html`` whenever a frontend build artifact is present,
and verifies packaged portal assets exist prior to building sdist or wheel distributions.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class PortalAssetsBuildHook(BuildHookInterface):
    """Synchronize or verify pre-compiled Web Portal assets during package build."""

    PLUGIN_NAME = "portal-assets"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        frontend_dir = Path(__file__).resolve().parent.parent
        dist_index = frontend_dir / "dist" / "index.html"
        build_script = frontend_dir / "scripts" / "build_assets.py"
        bundle_gz = root / "assets" / "portal" / "bundle.html.gz"
        favicon_svg = root / "assets" / "portal" / "favicon.svg"

        if dist_index.is_file() and build_script.is_file():
            subprocess.run(
                [sys.executable, str(build_script)],
                cwd=str(root),
                check=True,
            )

        if not bundle_gz.is_file() or not favicon_svg.is_file():
            raise RuntimeError(
                f"Portal bundle assets missing in {root / 'assets' / 'portal'}. "
                f"Run 'pnpm build:assets' in {frontend_dir} first."
            )

