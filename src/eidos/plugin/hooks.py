"""Agent lifecycle SessionStart baseline and Stop hook handler for Eidos specification validation and drift checking."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import orjson

from eidos.core.workspace.project import find_repo_root
from eidos.vcs.diff import classify_changed_files
from eidos.vcs.drift import detect_drift

_BASELINE_TTL_SECONDS = 86400.0
_CLINE_RETRY_TTL_SECONDS = 300.0
_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_BASELINE_EVENTS = frozenset(
    {
        "sessionstart",
        "userpromptsubmit",
        "userpromptsubmitted",
        "beforesubmitprompt",
        "taskstart",
    }
)


def _is_stop_hook_active(payload: dict[str, Any]) -> bool:
    """Return True when either snake_case `stop_hook_active` or camelCase `stopHookActive` is truthy."""
    return bool(payload.get("stop_hook_active") or payload.get("stopHookActive"))


def _detect_hook_client(payload: dict[str, Any]) -> str:
    """Identify the calling agent harness ('antigravity', 'cursor', 'cline', or 'standard') from the stdin payload."""
    if (
        "loop_count" in payload
        or "workspace_roots" in payload
        or payload.get("hook_event_name") in ("stop", "sessionStart", "beforeSubmitPrompt")
    ):
        return "cursor"
    if (
        "clineVersion" in payload
        or "hookName" in payload
        or "workspaceRoots" in payload
        or isinstance(payload.get("preToolUse"), dict)
    ):
        return "cline"
    if (
        "stop_hook_active" in payload
        or "stopHookActive" in payload
        or "cwd" in payload
        or "sessionId" in payload
        or "session_id" in payload
        or "hook_event_name" in payload
    ):
        return "standard"
    return "antigravity"


def _allow_response(client: str) -> dict[str, Any]:
    """Return the clean allow response for the detected client."""
    if client == "antigravity":
        return {"decision": "allow"}
    if client == "cline":
        return {"cancel": False}
    return {}


def _is_baseline_event(payload: dict[str, Any], event: str | None = None) -> bool:
    """Return True if the hook invocation is a SessionStart / UserPromptSubmit baseline snapshot event."""
    raw_ev = (
        event
        or payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("hookName")
        or payload.get("event")
        or ""
    )
    norm_ev = re.sub(r"[\s_-]+", "", str(raw_ev).strip().lower())
    if norm_ev in _BASELINE_EVENTS:
        return True
    return (
        not _is_stop_hook_active(payload)
        and "stopHookActive" not in payload
        and "stop_hook_active" not in payload
        and payload.get("source") in ("new", "resume", "startup")
    )


def _extract_cline_tool_name(payload: dict[str, Any]) -> str:
    """Extract the target tool name from a Cline PreToolUse hook payload."""
    pre_tool = payload.get("preToolUse")
    pre_tool_dict = pre_tool if isinstance(pre_tool, dict) else {}
    raw = (
        pre_tool_dict.get("toolName")
        or pre_tool_dict.get("tool")
        or payload.get("tool_name")
        or payload.get("tool")
        or ""
    )
    return str(raw).strip()


def _is_retry_or_non_normal_stop(payload: dict[str, Any], client: str) -> bool:
    """Return True when the 1-turn retry guard has already fired or the event is not a cancellable completion stop."""
    if _is_stop_hook_active(payload):
        return True
    if client == "antigravity":
        exec_num = payload.get("executionNum", 1)
        term_reason = str(payload.get("terminationReason", "") or "")
        return (isinstance(exec_num, int) and exec_num > 1) or term_reason not in ("", "NO_TOOL_CALL")
    if client == "cursor":
        loop_count = payload.get("loop_count", 0)
        status = str(payload.get("status", "completed") or "completed")
        return (isinstance(loop_count, int) and loop_count > 0) or status != "completed"
    if client == "cline":
        hook_name = str(payload.get("hookName", "") or "")
        if hook_name and hook_name != "PreToolUse":
            return True
        return _extract_cline_tool_name(payload) != "attempt_completion"
    return False


def _continuation_response(client: str, message: str) -> dict[str, Any]:
    """Format the 1-turn continuation response for the detected client."""
    if client == "antigravity":
        return {"decision": "continue", "reason": message}
    if client == "cursor":
        return {"followup_message": message}
    if client == "cline":
        return {"cancel": True, "contextModification": message, "errorMessage": message}
    return {"decision": "block", "reason": message}


def _resolve_session_key(payload: dict[str, Any]) -> str:
    """Resolve a stable session identifier from the hook payload, falling back to 'default'."""
    for key in ("session_id", "sessionId", "conversation_id", "conversationId", "taskId", "task_id", "transcript_path"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "default"


def _session_baseline_filename(payload: dict[str, Any]) -> str:
    """Build a safe filename inside `eidos/.cache/hook_baselines/` for a session key."""
    raw_key = _resolve_session_key(payload)
    if raw_key == "default":
        return "session_default.json"
    safe_prefix = _SAFE_KEY_RE.sub("_", raw_key).strip("._")[:32] or "sess"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    return f"session_{safe_prefix}_{digest}.json"


def _cline_retry_filename(payload: dict[str, Any], resolved_root: Path) -> str:
    """Build a safe retry-marker filename inside `eidos/.cache/hook_baselines/` for Cline `attempt_completion`."""
    raw_key = _resolve_session_key(payload)
    seed = f"{resolved_root}:{raw_key}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"cline_retry_{digest}.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _prune_stale_baselines(baseline_dir: Path, max_age_seconds: float = _BASELINE_TTL_SECONDS) -> None:
    """Remove stale baseline and retry marker files older than `max_age_seconds`."""
    if not baseline_dir.is_dir():
        return
    now = time.time()
    for entry in baseline_dir.glob("*.json"):
        with contextlib.suppress(OSError):
            if now - entry.stat().st_mtime > max_age_seconds:
                entry.unlink()


def _snapshot_dirty_worktree(repo_root: Path) -> dict[str, str] | None:
    """Snapshot uncommitted/untracked working-tree files via `git status --porcelain=v1 -z` and SHA-256 digests."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    tokens = proc.stdout.split(b"\x00")
    snapshot: dict[str, str] = {}
    idx = 0
    while idx < len(tokens):
        entry = tokens[idx]
        idx += 1
        if len(entry) < 4:
            continue
        xy = entry[:2].decode("utf-8", errors="replace")
        rel_path = entry[3:].decode("utf-8", errors="replace")
        paths_to_record = [rel_path]
        if ("R" in xy or "C" in xy) and idx < len(tokens) and tokens[idx]:
            paths_to_record.append(tokens[idx].decode("utf-8", errors="replace"))
            idx += 1

        for p_str in paths_to_record:
            norm = p_str.replace("\\", "/")
            if norm.startswith(("eidos/.cache/", ".cache/", ".git/")) or "/.cache/" in norm or "/.git/" in norm:
                continue
            full = repo_root / norm
            try:
                if full.is_symlink():
                    snapshot[norm] = f"{xy}:symlink:{os.readlink(full)}"
                elif full.is_file():
                    snapshot[norm] = f"{xy}:sha256:{_sha256_file(full)}"
                else:
                    snapshot[norm] = f"{xy}:deleted"
            except OSError:
                snapshot[norm] = f"{xy}:unreadable"
    return snapshot


def _compute_state_digest(resolved_root: Path, eidos_dir: Path, dirty_map: dict[str, str] | None) -> str:
    """Compute a deterministic digest of the current workspace state for Cline 1-turn retry protection."""
    if dirty_map is not None:
        return hashlib.sha256(orjson.dumps(dirty_map, option=orjson.OPT_SORT_KEYS)).hexdigest()
    entries: list[str] = []
    for f in sorted(eidos_dir.rglob("*")):
        if ".cache" in f.parts:
            continue
        with contextlib.suppress(OSError):
            if f.is_file():
                st = f.stat()
                entries.append(f"{f.relative_to(resolved_root)}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _resolve_eidos_roots(workspace_root: Path) -> tuple[Path, Path] | None:
    """Resolve `(repo_root, eidos_dir)` if `workspace_root` belongs to an Eidos workspace, else None."""
    resolved_root = workspace_root.resolve()
    eidos_dir = resolved_root / "eidos"
    if not eidos_dir.exists() and resolved_root.exists():
        cand_root = find_repo_root(resolved_root)
        if (cand_root / "eidos").exists():
            resolved_root = cand_root
            eidos_dir = cand_root / "eidos"
    if not eidos_dir.is_dir():
        return None
    return resolved_root, eidos_dir


def capture_session_baseline(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Record a session working-tree dirty-file baseline in `eidos/.cache/hook_baselines/`."""
    client = _detect_hook_client(payload)
    roots = _resolve_eidos_roots(workspace_root)
    if roots is None:
        return _allow_response(client)
    resolved_root, eidos_dir = roots

    dirty_map = _snapshot_dirty_worktree(resolved_root)
    if dirty_map is None:
        return _allow_response(client)

    baseline_dir = eidos_dir / ".cache" / "hook_baselines"
    with contextlib.suppress(OSError):
        baseline_dir.mkdir(parents=True, exist_ok=True)
        _prune_stale_baselines(baseline_dir)
        baseline_file = baseline_dir / _session_baseline_filename(payload)
        data = {
            "timestamp": time.time(),
            "session_key": _resolve_session_key(payload),
            "files": dirty_map,
        }
        baseline_file.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    return _allow_response(client)


def _zero_governed_changes_since_baseline(
    payload: dict[str, Any],
    resolved_root: Path,
    eidos_dir: Path,
    current_dirty: dict[str, str] | None,
) -> bool:
    """Return True if explicit payload metadata or an active session baseline proves zero governed files changed."""
    for explicit_key in ("modified_files", "editedFiles"):
        explicit_val = payload.get(explicit_key)
        if isinstance(explicit_val, list):
            if not explicit_val:
                return True
            str_paths = [str(p) for p in explicit_val if isinstance(p, str) and p.strip()]
            if not str_paths:
                return True
            cls_exp = classify_changed_files(str_paths, repo_root=resolved_root, eidos_dir=eidos_dir)
            return not (
                cls_exp.code_files
                or cls_exp.spec_files
                or cls_exp.delta_files
                or cls_exp.test_files
                or cls_exp.onboarding_files
            )

    baseline_dir = eidos_dir / ".cache" / "hook_baselines"
    if not baseline_dir.is_dir() or current_dirty is None:
        return False

    _prune_stale_baselines(baseline_dir)
    baseline_file = baseline_dir / _session_baseline_filename(payload)
    if not baseline_file.is_file():
        return False

    try:
        raw = orjson.loads(baseline_file.read_bytes())
    except (OSError, orjson.JSONDecodeError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    baseline_files = raw.get("files")
    if not isinstance(baseline_files, dict):
        return False

    changed_paths = sorted(
        p for p in (set(baseline_files) | set(current_dirty)) if baseline_files.get(p) != current_dirty.get(p)
    )
    if not changed_paths:
        return True
    cls = classify_changed_files(changed_paths, repo_root=resolved_root, eidos_dir=eidos_dir)
    return not (
        cls.code_files or cls.spec_files or cls.delta_files or cls.test_files or cls.onboarding_files
    )


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


def handle_stop_hook(payload: dict[str, Any], workspace_root: Path, event: str | None = None) -> dict[str, Any]:
    """Run session-scoped specification validation, invariant verification, and drift check on agent Stop."""
    if _is_baseline_event(payload, event=event):
        return capture_session_baseline(payload, workspace_root)

    client = _detect_hook_client(payload)
    if _is_retry_or_non_normal_stop(payload, client):
        return _allow_response(client)

    roots = _resolve_eidos_roots(workspace_root)
    if roots is None:
        return _allow_response(client)
    resolved_root, eidos_dir = roots

    current_dirty = _snapshot_dirty_worktree(resolved_root)
    if _zero_governed_changes_since_baseline(payload, resolved_root, eidos_dir, current_dirty):
        return _allow_response(client)

    cline_marker_path: Path | None = None
    cline_state_digest: str | None = None
    if client == "cline":
        baseline_dir = eidos_dir / ".cache" / "hook_baselines"
        cline_marker_path = baseline_dir / _cline_retry_filename(payload, resolved_root)
        cline_state_digest = _compute_state_digest(resolved_root, eidos_dir, current_dirty)
        if cline_marker_path.is_file():
            try:
                marker = orjson.loads(cline_marker_path.read_bytes())
                ts = float(marker.get("timestamp", 0.0)) if isinstance(marker, dict) else 0.0
                prev_digest = marker.get("state_digest") if isinstance(marker, dict) else None
                with contextlib.suppress(OSError):
                    cline_marker_path.unlink()
                if time.time() - ts <= _CLINE_RETRY_TTL_SECONDS and prev_digest == cline_state_digest:
                    return _allow_response(client)
            except (OSError, orjson.JSONDecodeError, ValueError, TypeError):
                with contextlib.suppress(OSError):
                    cline_marker_path.unlink()

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
            if cline_marker_path is not None and cline_state_digest is not None:
                with contextlib.suppress(OSError):
                    cline_marker_path.parent.mkdir(parents=True, exist_ok=True)
                    cline_marker_path.write_bytes(
                        orjson.dumps({"timestamp": time.time(), "state_digest": cline_state_digest})
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
    event: str | None = None,
) -> int:
    """Execute the Eidos lifecycle hook from CLI (`eidos hook`)."""
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

    res = handle_stop_hook(payload, ws_root, event=event)
    print(orjson.dumps(res).decode("utf-8"))
    return 0
