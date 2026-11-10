"""Stateless agent lifecycle Stop hook handler for Eidos specification validation and drift checking."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any

import orjson

from eidos.core.workspace.project import find_repo_root
from eidos.vcs.drift import detect_drift


def _detect_hook_client(payload: dict[str, Any]) -> str:
    """Identify the calling agent harness ('antigravity', 'cursor', 'cline', or 'standard') from the stdin payload."""
    if "loop_count" in payload or "workspace_roots" in payload or payload.get("hook_event_name") == "stop":
        return "cursor"
    if "clineVersion" in payload or "hookName" in payload or "workspaceRoots" in payload:
        return "cline"
    if "stop_hook_active" in payload or "cwd" in payload or "sessionId" in payload:
        return "standard"
    return "antigravity"


def _allow_response(client: str) -> dict[str, Any]:
    """Return the clean allow response for the detected client."""
    if client == "antigravity":
        return {"decision": "allow"}
    if client == "cline":
        return {"cancel": False}
    return {}


def _is_retry_or_non_normal_stop(payload: dict[str, Any], client: str) -> bool:
    """Return True when the in-memory 1-turn retry guard has already fired or the stop was not a normal completion."""
    if client == "antigravity":
        exec_num = payload.get("executionNum", 1)
        term_reason = str(payload.get("terminationReason", "") or "")
        return (isinstance(exec_num, int) and exec_num > 1) or term_reason not in ("", "NO_TOOL_CALL")
    if client == "cursor":
        loop_count = payload.get("loop_count", 0)
        status = str(payload.get("status", "completed") or "completed")
        return (isinstance(loop_count, int) and loop_count > 0) or status != "completed"
    if client == "cline":
        hook_name = str(payload.get("hookName", "TaskComplete") or "TaskComplete")
        return bool(payload.get("stop_hook_active")) or hook_name != "TaskComplete"
    return bool(payload.get("stop_hook_active"))


def _continuation_response(client: str, message: str) -> dict[str, Any]:
    """Format the 1-turn continuation response for the detected client."""
    if client == "antigravity":
        return {"decision": "continue", "reason": message}
    if client == "cursor":
        return {"followup_message": message}
    if client == "cline":
        return {"cancel": True, "contextModification": message, "errorMessage": message}
    return {"decision": "block", "reason": message}


def find_workspace_root(payload: dict[str, Any] | None = None) -> Path:
    """Determine the active project or sub-project workspace root directory from hook payload or current working directory."""
    if payload:
        for list_key in ("workspacePaths", "workspace_roots", "workspaceRoots"):
            ws_paths = payload.get(list_key, [])
            if ws_paths and isinstance(ws_paths, list) and isinstance(ws_paths[0], str) and ws_paths[0].strip():
                p = Path(ws_paths[0].strip())
                if p.exists():
                    return find_repo_root(p.resolve())
        for key in ("cwd", "workspaceRoot", "workspace_root"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                p = Path(val.strip())
                if p.exists():
                    return find_repo_root(p.resolve())

    return find_repo_root()


def handle_stop_hook(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Run stateless specification validation, invariant verification, and drift check on agent Stop."""
    client = _detect_hook_client(payload)
    if _is_retry_or_non_normal_stop(payload, client):
        return _allow_response(client)

    resolved_root = workspace_root.resolve()
    eidos_dir = resolved_root / "eidos"
    if not eidos_dir.exists() and resolved_root.exists():
        cand_root = find_repo_root(resolved_root)
        if (cand_root / "eidos").exists():
            resolved_root = cand_root
            eidos_dir = cand_root / "eidos"
    if not eidos_dir.exists():
        return _allow_response(client)

    try:
        drift_res = detect_drift(repo_root=resolved_root, eidos_dir=eidos_dir, uncommitted=True)
        has_non_code_failure = (
            not drift_res.validation_passed or bool(drift_res.uncovered_invariants) or bool(drift_res.orphan_invariants)
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
            return _continuation_response(client, msg)
    except Exception as exc:
        print(f"[Eidos Stop Hook Error] {exc}", file=sys.stderr)
        return _allow_response(client)

    return _allow_response(client)


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
        with contextlib.suppress(orjson.JSONDecodeError, ValueError):
            parsed = orjson.loads(stdin_data)
            if isinstance(parsed, dict):
                payload = parsed

    ws_root = target_path.resolve() if target_path else find_workspace_root(payload)
    if ws_root.name == "eidos" and ((ws_root / "system.adoc").exists() or not (ws_root / "eidos").exists()):
        ws_root = ws_root.parent
    elif not (ws_root / "eidos").is_dir() and ws_root.exists():
        ws_root = find_repo_root(ws_root)

    res = handle_stop_hook(payload, ws_root)
    print(orjson.dumps(res).decode("utf-8"))
    return 0
