#!/usr/bin/env python3
"""Compatibility shim forwarding to native `eidos hook drift` CLI command."""

import argparse
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Eidos Drift Gate Hook Shim")
    parser.add_argument("--event", default="Stop")
    args, _ = parser.parse_known_args()

    try:
        from eidos.plugin.hooks import execute_drift_hook

        return execute_drift_hook(event=args.event)
    except ImportError:
        eidos_bin = shutil.which("eidos") or str(Path.home() / ".local" / "bin" / "eidos")
        if Path(eidos_bin).is_file():
            os.execv(eidos_bin, [eidos_bin, "hook", "drift", "--event", args.event])
        sys.stderr.write("eidos CLI not found on PATH. Install with: uv tool install eidos-ai\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

