#!/usr/bin/env python3
"""Eidos Spec-to-Code Drift Lifecycle Hook.

Executes within agent environments (Antigravity hooks.json) to ensure code
modifications in src/ co-evolve with living specifications in eidos/ (Direct Mode
governance) and block completion if spec-to-code drift is detected.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

# Resolve and mount eidos core packages if available in workspace
current_dir = Path(__file__).resolve().parent
search_roots = [
    current_dir.parent,  # plugin root
    current_dir.parent.parent.parent.parent,  # repo root from .agents/plugins/eidos/scripts
    current_dir.parent.parent,  # repo root from assets/plugin_reference/scripts
    Path.cwd(),
]
for root in search_roots:
    src_dir = root / "src"
    if (src_dir / "eidos").exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
        break

try:
    from eidos.vcs.drift import detect_drift
except ImportError:
    detect_drift = None


def find_workspace_root(payload: dict[str, Any] | None = None) -> Path:
    """Determine the active workspace root directory."""
    if payload:
        ws_paths = payload.get("workspacePaths", [])
        if ws_paths and isinstance(ws_paths, list):
            p = Path(ws_paths[0])
            if p.exists():
                return p.resolve()

    # Fallback to traversing current working directory
    cwd = Path.cwd().resolve()
    for parent in [cwd, *list(cwd.parents)]:
        if (parent / ".git").exists() or (parent / "eidos").exists():
            return parent
    return cwd


def handle_post_invocation(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Emit an ephemeral drift notice into agent context if uncommitted code changes exist without spec updates."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists() or detect_drift is None:
        return {"injectSteps": []}

    try:
        drift_res = detect_drift(repo_root=workspace_root, eidos_dir=eidos_dir, uncommitted=True)
        if drift_res.drift_detected:
            code_count = len(drift_res.classification.code_files)
            top_files = ", ".join(Path(f).name for f in drift_res.classification.code_files[:3])
            more = f" (+{code_count - 3} more)" if code_count > 3 else ""
            remediation_hint = ""
            if drift_res.remediation_suggestions:
                remediation_hint = f"\nAction needed: {drift_res.remediation_suggestions[0]}"
            msg = (
                f"ℹ️ [Eidos Sync Notice] Uncommitted code changes detected in {top_files}{more} without living spec updates in 'eidos/'.{remediation_hint}\n"
                f"Remember to synchronize living specifications in 'eidos/' (or run 'eidos reconcile') before concluding."
            )
            return {"injectSteps": [{"ephemeralMessage": msg}]}
    except Exception as exc:
        print(f"[Eidos Drift Gate Error] Drift detection error: {exc}", file=sys.stderr)

    # Lazily ensure knowledge graph index is synchronized
    with contextlib.suppress(Exception):
        from eidos.core.graph_index import ArchitecturalIndex

        cache_db = workspace_root / ".eidos" / "cache" / "index.db"
        if cache_db.exists():
            idx = ArchitecturalIndex(db_path=cache_db, repo_root=workspace_root, eidos_dir=eidos_dir)
            dirty, _, _ = idx.is_dirty()
            if dirty:
                idx.sync()
            idx.close()

    return {"injectSteps": []}


def handle_stop(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Block agent completion if spec-to-code drift is detected."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists() or detect_drift is None:
        return {"decision": "allow"}

    try:
        drift_res = detect_drift(repo_root=workspace_root, eidos_dir=eidos_dir, uncommitted=True)
        if drift_res.drift_detected:
            remediation_lines = "\n".join(f"  {rem}" for rem in drift_res.remediation_suggestions)
            code_files_str = "\n".join(f"  - {cf}" for cf in drift_res.classification.code_files[:8])
            if len(drift_res.classification.code_files) > 8:
                code_files_str += f"\n  ... and {len(drift_res.classification.code_files) - 8} more files"

            reason = (
                f"🛑 [Eidos Drift Gate] Cannot complete task: Spec-to-code drift detected!\n"
                f"{len(drift_res.classification.code_files)} code file(s) were modified without corresponding living specification updates in 'eidos/':\n"
                f"{code_files_str}\n\n"
                f"Recommended Actions:\n"
                f"{remediation_lines}\n\n"
                f"According to Eidos Guidelines (Rule #3 & #5), all code modifications in src/ must co-evolve with living specs in 'eidos/'. "
                f"Please update the relevant living specifications (or scaffold a SpecDelta) before concluding your work."
            )
            return {"decision": "continue", "reason": reason}
    except Exception as exc:
        print(f"[Eidos Drift Gate Error] Drift detection error: {exc}", file=sys.stderr)

    return {"decision": "allow"}


def run_standalone(workspace_root: Path | None = None) -> int:
    """Run drift check in interactive terminal mode."""
    ws_root = (workspace_root or Path.cwd()).resolve()
    eidos_dir = ws_root / "eidos"
    if not eidos_dir.exists():
        print(f"Error: Living specifications directory '{eidos_dir}' does not exist.", file=sys.stderr)
        return 1

    if detect_drift is None:
        print("Error: detect_drift could not be imported.", file=sys.stderr)
        return 1

    drift_res = detect_drift(repo_root=ws_root, eidos_dir=eidos_dir, uncommitted=True)
    if drift_res.drift_detected:
        print(f"Drift detected in {len(drift_res.classification.code_files)} code file(s):", file=sys.stderr)
        for cf in drift_res.classification.code_files:
            print(f"  - {cf}", file=sys.stderr)
        return 1

    print("✓ SUCCESS: No spec-to-code drift detected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Eidos Spec-to-Code Drift Lifecycle Hook")
    parser.add_argument(
        "--event",
        choices=["post_invocation", "stop"],
        help="Explicit lifecycle event identifier",
    )
    parser.add_argument("--path", type=str, help="Workspace root path for standalone scanning")
    args = parser.parse_args()

    # Check if input is coming from stdin pipe (Agent Hook invocation)
    is_piped = not sys.stdin.isatty()
    stdin_content = ""
    payload: dict[str, Any] = {}

    if is_piped:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                payload = json.loads(stdin_content)

    # If no piped JSON payload and no explicit hook event, run standalone
    if not payload and not args.event:
        target = Path(args.path) if args.path else None
        return run_standalone(target)

    event = args.event
    if not event:
        if "executionNum" in payload or "terminationReason" in payload:
            event = "stop"
        elif "invocationNum" in payload:
            event = "post_invocation"
        else:
            event = "stop"

    ws_root = find_workspace_root(payload)

    if event == "post_invocation":
        res = handle_post_invocation(payload, ws_root)
        print(json.dumps(res))
        return 0

    if event == "stop":
        res = handle_stop(payload, ws_root)
        print(json.dumps(res))
        return 0

    # Default fallback
    print(json.dumps({}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

