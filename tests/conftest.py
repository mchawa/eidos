"""Pytest session hooks ensuring deterministic parallel (pytest-xdist) worker isolation."""

from __future__ import annotations

import contextlib
from pathlib import Path


def pytest_sessionstart(session) -> None:
    """Pre-sync the repo's SQLite architectural index once on the xdist controller before workers fan out."""
    if hasattr(session.config, "workerinput") or getattr(session.config.option, "collectonly", False):
        return
    numprocesses = getattr(session.config.option, "numprocesses", None)
    if not numprocesses or numprocesses == 0:
        return
    eidos_dir = Path.cwd() / "eidos"
    if not eidos_dir.is_dir():
        return
    with contextlib.suppress(Exception):
        from eidos.core.index.graph_index import get_architectural_index

        idx = get_architectural_index(eidos_dir=eidos_dir)
        idx.sync(auto_semantic=False)
