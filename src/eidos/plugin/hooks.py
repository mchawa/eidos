"""Stateless agent lifecycle Stop hook handler for Eidos specification validation and drift checking."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from eidos.core.workspace.project import find_repo_root
from eidos.vcs.drift import detect_drift


def find_workspace_root(payload: dict[str, Any] | None = None) -> Path:
    """Determine the active workspace root directory from hook payload or current working directory."""
    if payload:
        ws_paths = payload.get("workspacePaths", [])
        if ws_paths and isinstance(ws_paths, list):
            p = Path(ws_paths[0])
            if p.exists():
                return p.resolve()
        for key in ("cwd", "workspaceRoot", "workspace_root"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                p = Path(val.strip())
                if p.exists():
                    return p.resolve()

    return find_repo_root()


def handle_stop_hook(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Run stateless specification validation, invariant verification, and drift check on agent Stop."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"decision": "allow"}

    try:
        drift_res = detect_drift(repo_root=workspace_root, eidos_dir=eidos_dir, uncommitted=True)
        has_non_code_failure = (
            not drift_res.validation_passed
            or bool(drift_res.uncovered_invariants)
            or bool(drift_res.orphan_invariants)
        )
        if drift_res.drift_detected or has_non_code_failure:
            cause_parts: list[str] = []
            if drift_res.classification.code_files and drift_res.drift_detected:
                code_files_str = "\n".join(f"  - {cf}" for cf in drift_res.classification.code_files[:8])
                if len(drift_res.classification.code_files) > 8:
                    code_files_str += f"\n  ... and {len(drift_res.classification.code_files) - 8} more files"
                cause_parts.append(
                    f"{len(drift_res.classification.code_files)} code file(s) were modified without corresponding living specification updates in 'eidos/':\n"
                    f"{code_files_str}"
                )
            if not drift_res.validation_passed:
                err_lines = "\n".join(f"  - {ve}" for ve in drift_res.validation_errors[:5])
                cause_parts.append(
                    f"Specification validation failed ({len(drift_res.validation_errors)} error(s)):\n{err_lines}"
                )
            if drift_res.uncovered_invariants or drift_res.orphan_invariants:
                cause_parts.append(
                    f"Invariant test binding verification failed ({len(drift_res.uncovered_invariants)} uncovered invariant(s), "
                    f"{len(drift_res.orphan_invariants)} orphan test reference(s))."
                )
            if not cause_parts:
                cause_parts.append("Specification or SpecDelta synchronization issue detected in 'eidos/'.")

            cause_block = "\n".join(cause_parts)
            remediation_lines = "\n".join(f"  {rem}" for rem in drift_res.remediation_suggestions)
            msg = (
                f"⚠️ [Eidos Stop Hook] Spec-to-code drift or validation issues detected:\n"
                f"{cause_block}\n\n"
                f"Recommended Actions:\n"
                f"{remediation_lines}"
            )
            return {"decision": "allow", "systemMessage": msg}
    except Exception as exc:
        print(f"[Eidos Stop Hook Error] {exc}", file=sys.stderr)
        return {
            "decision": "allow",
            "systemMessage": f"⚠️ [Eidos Stop Hook] Internal drift check error: {exc}",
        }

    return {"decision": "allow"}


def execute_hook(
    path: Path | None = None,
    repo_root: Path | None = None,
    stdin_data: str | None = None,
) -> int:
    """Execute the stateless Stop hook from CLI (`eidos hook`)."""
    target_path = path or repo_root
    payload: dict[str, Any] = {}
    if stdin_data is None and not sys.stdin.isatty():
        with contextlib.suppress(OSError):
            stdin_data = sys.stdin.read().strip()
    if stdin_data:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(stdin_data)
            if isinstance(parsed, dict):
                payload = parsed

    ws_root = target_path.resolve() if target_path else find_workspace_root(payload)
    if ws_root.name == "eidos" and ((ws_root / "system.adoc").exists() or not (ws_root / "eidos").exists()):
        ws_root = ws_root.parent

    res = handle_stop_hook(payload, ws_root)
    print(json.dumps(res))
    return 0
