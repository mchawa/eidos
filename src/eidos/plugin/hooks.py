"""Agent lifecycle hook handlers for Eidos living specification linting and drift gating."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from eidos.core.parser import extract_frontmatter
from eidos.core.project import find_repo_root
from eidos.core.validator import SpecValidator
from eidos.vcs.drift import detect_drift


def find_workspace_root(payload: dict[str, Any] | None = None) -> Path:
    """Determine the active workspace root directory from hook payload or current working directory."""
    if payload:
        ws_paths = payload.get("workspacePaths", [])
        if ws_paths and isinstance(ws_paths, list):
            p = Path(ws_paths[0])
            if p.exists():
                return p.resolve()

    return find_repo_root()


def is_eidos_doc(target_file_str: str, workspace_root: Path | None = None) -> bool:
    """Check if the given file path refers to an Eidos specification document under eidos/."""
    if not target_file_str:
        return False
    target_path = Path(target_file_str)
    if target_path.suffix.lower() not in (".adoc", ".json"):
        return False

    ws_root = (workspace_root or find_workspace_root()).resolve()
    with contextlib.suppress(ValueError, RuntimeError):
        resolved = target_path.resolve() if target_path.is_absolute() else (ws_root / target_path).resolve()
        rel = resolved.relative_to(ws_root)
        if rel.parts and rel.parts[0] == "eidos":
            return True

    parts = target_path.parts
    if parts and parts[0] == ".":
        parts = parts[1:]
    return bool(parts and parts[0] == "eidos")


def handle_lint_pre_tool_use(payload: dict[str, Any], workspace_root: Path | None = None) -> dict[str, Any]:
    """Validate YAML frontmatter or JSON syntax before file modification tools execute on eidos/."""
    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args", {})
    target_file_str = args.get("TargetFile", "")

    if not target_file_str or not is_eidos_doc(target_file_str, workspace_root):
        return {"decision": "allow"}

    target_path = Path(target_file_str)
    content_to_check: str | None = None
    if tool_name == "write_to_file":
        content_to_check = args.get("CodeContent", "")
    elif tool_name == "replace_file_content" and target_path.exists():
        with contextlib.suppress(OSError, UnicodeDecodeError):
            curr_text = target_path.read_text(encoding="utf-8")
            target_str = args.get("TargetContent", "")
            replace_str = args.get("ReplacementContent", "")
            if target_str in curr_text:
                content_to_check = curr_text.replace(target_str, replace_str, 1)

    if content_to_check is not None:
        if target_path.suffix.lower() == ".json":
            try:
                json.loads(content_to_check)
            except json.JSONDecodeError as exc:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] JSON Syntax Error in '{target_path.name}': {exc}",
                }

        stripped = content_to_check.lstrip("\ufeff").lstrip()
        if stripped.startswith("---") and target_path.suffix.lower() == ".adoc":
            data, _, parse_err = extract_frontmatter(content_to_check)
            if parse_err:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] YAML Syntax Error in '{target_path.name}': {parse_err}",
                }
            if not isinstance(data, dict):
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Syntax Error: Frontmatter in '{target_path.name}' must be a YAML mapping dictionary.",
                }
            missing = [f for f in ("type", "schema", "id") if f not in data]
            if missing:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Frontmatter in '{target_path.name}' is missing required fields: {', '.join(missing)}.",
                }

    return {"decision": "allow"}


def handle_lint_post_invocation(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Inject ephemeral diagnostics into agent context if living specifications in eidos/ are invalid."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"injectSteps": []}

    try:
        validator = SpecValidator()
        report = validator.validate_directory(eidos_dir)
        if not report.is_clean:
            errors: list[str] = []
            for f, errs in report.schema_errors.items():
                fname = Path(f).name
                for e in errs[:2]:
                    errors.append(f"• {fname}: {e}")
            for r in report.broken_references[:3]:
                errors.append(f"• Broken reference: {r}")

            summary = "\n".join(errors[:6])
            msg = (
                f"⚠️ [Eidos Doc Linter] Living specification syntax or schema issues detected in eidos/:\n"
                f"{summary}\n\n"
                f"Please correct these errors before proceeding."
            )
            return {"injectSteps": [{"ephemeralMessage": msg}]}
    except Exception as exc:
        print(f"[Eidos Doc Linter Error] Validator error: {exc}", file=sys.stderr)

    return {"injectSteps": []}


def _check_and_record_stop_gate(eidos_dir: Path, gate_key: str, fingerprint: str) -> bool:
    """Return True if this fingerprint should block (first occurrence), or False if already blocked once."""
    cache_dir = eidos_dir / ".cache"
    gate_file = cache_dir / "stop-gate.json"
    state: dict[str, Any] = {}
    if gate_file.is_file():
        with contextlib.suppress(Exception):
            loaded = json.loads(gate_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded

    prev_fp = state.get(gate_key)
    if prev_fp == fingerprint:
        # Already blocked once for this exact fingerprint; clear key and allow termination
        state.pop(gate_key, None)
        with contextlib.suppress(Exception):
            if state:
                cache_dir.mkdir(parents=True, exist_ok=True)
                gate_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            else:
                gate_file.unlink(missing_ok=True)
        return False

    state[gate_key] = fingerprint
    with contextlib.suppress(Exception):
        cache_dir.mkdir(parents=True, exist_ok=True)
        gate_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return True


def _clear_stop_gate(eidos_dir: Path, gate_key: str) -> None:
    """Clear a recorded stop-gate key when validation/drift passes cleanly."""
    gate_file = eidos_dir / ".cache" / "stop-gate.json"
    if not gate_file.is_file():
        return
    with contextlib.suppress(Exception):
        loaded = json.loads(gate_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and gate_key in loaded:
            loaded.pop(gate_key, None)
            if loaded:
                gate_file.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
            else:
                gate_file.unlink(missing_ok=True)


def handle_lint_stop(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Block agent completion once if any living specifications in eidos/ fail validation."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"decision": "allow"}

    try:
        validator = SpecValidator()
        report = validator.validate_directory(eidos_dir)
        if not report.is_clean:
            errors: list[str] = []
            for f, errs in sorted(report.schema_errors.items(), key=lambda x: str(x[0])):
                fname = Path(f).name
                for e in errs[:3]:
                    errors.append(f"  - {fname}: {e}")
            for r in report.broken_references[:3]:
                errors.append(f"  - Broken reference: {r}")

            err_msg = "\n".join(errors[:10])
            fp = hashlib.sha256(err_msg.encode("utf-8")).hexdigest()
            if not _check_and_record_stop_gate(eidos_dir, "lint_fingerprint", fp):
                return {"decision": "allow"}

            reason = (
                f"🛑 [Eidos Doc Linter] Cannot complete task: One or more specifications in 'eidos/' failed validation:\n"
                f"{err_msg}\n\n"
                f"All specifications in eidos/ must strictly conform to their schema with zero broken references before concluding your work."
            )
            return {"decision": "continue", "reason": reason}

        _clear_stop_gate(eidos_dir, "lint_fingerprint")
    except Exception as exc:
        err_text = f"Validator execution error: {exc}"
        print(f"[Eidos Doc Linter Error] {err_text}", file=sys.stderr)
        fp = hashlib.sha256(err_text.encode("utf-8")).hexdigest()
        if _check_and_record_stop_gate(eidos_dir, "lint_fingerprint", fp):
            return {
                "decision": "continue",
                "reason": f"🛑 [Eidos Doc Linter] Internal validation error: {exc}",
            }

    return {"decision": "allow"}


def handle_drift_post_invocation(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Emit an ephemeral drift notice into agent context if uncommitted structural code changes exist without spec updates."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
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
                f"[Eidos Sync Notice] Uncommitted code changes detected in {top_files}{more} without living spec updates in 'eidos/'.{remediation_hint}\n"
                f"Remember to synchronize living specifications in 'eidos/' (or run 'eidos reconcile') before concluding."
            )
            return {"injectSteps": [{"ephemeralMessage": msg}]}
    except Exception as exc:
        print(f"[Eidos Drift Gate Error] Drift detection error: {exc}", file=sys.stderr)

    # Lazily ensure knowledge graph index is synchronized
    with contextlib.suppress(Exception):
        from eidos.core.graph_index import ArchitecturalIndex

        cache_db = eidos_dir / ".cache" / "index.db"
        if cache_db.exists():
            idx = ArchitecturalIndex(db_path=cache_db, repo_root=workspace_root, eidos_dir=eidos_dir)
            dirty, _, _ = idx.is_dirty()
            if dirty:
                idx.sync()
            idx.close()

    return {"injectSteps": []}


def handle_drift_stop(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Block agent completion once per drift fingerprint if spec-to-code drift is detected."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"decision": "allow"}

    try:
        drift_res = detect_drift(repo_root=workspace_root, eidos_dir=eidos_dir, uncommitted=True)
        if drift_res.drift_detected:
            fp_payload = json.dumps(
                {
                    "code_files": sorted(drift_res.classification.code_files),
                    "spec_files": sorted(drift_res.classification.spec_files),
                    "delta_files": sorted(drift_res.classification.delta_files),
                    "remediation": drift_res.remediation_suggestions,
                },
                sort_keys=True,
            )
            fp = hashlib.sha256(fp_payload.encode("utf-8")).hexdigest()
            if not _check_and_record_stop_gate(eidos_dir, "fingerprint", fp):
                return {"decision": "allow"}

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
                f"Structural code changes must co-evolve with living specs in 'eidos/'. "
                f"Please update the relevant living specifications (or scaffold a SpecDelta) before concluding your work."
            )
            return {"decision": "continue", "reason": reason}

        _clear_stop_gate(eidos_dir, "fingerprint")
    except Exception as exc:
        err_text = f"Drift detection error: {exc}"
        print(f"[Eidos Drift Gate Error] {err_text}", file=sys.stderr)
        fp = hashlib.sha256(err_text.encode("utf-8")).hexdigest()
        if _check_and_record_stop_gate(eidos_dir, "fingerprint", fp):
            return {
                "decision": "continue",
                "reason": f"🛑 [Eidos Drift Gate] Internal drift check error: {exc}",
            }

    return {"decision": "allow"}


def _infer_event(event: str | None, payload: dict[str, Any], default_event: str = "stop") -> str:
    if event:
        norm = event.strip().lower()
        if norm in ("pretooluse", "pre_tool_use", "pre_tool"):
            return "pre_tool"
        if norm in ("postinvocation", "post_invocation"):
            return "post_invocation"
        if norm in ("posttooluse", "post_tool_use", "post_tool"):
            return "post_tool"
        if norm == "stop":
            return "stop"
        return norm
    if "toolCall" in payload:
        return "pre_tool"
    if "executionNum" in payload or "terminationReason" in payload:
        return "stop"
    if "invocationNum" in payload:
        return "post_invocation"
    if "stepIdx" in payload:
        return "post_tool"
    return default_event


def execute_lint_hook(
    event: str | None = None,
    path: Path | None = None,
    repo_root: Path | None = None,
    stdin_data: str | None = None,
) -> int:
    """Execute the lint hook from CLI (`eidos hook lint`)."""
    target_path = path or repo_root
    payload: dict[str, Any] = {}
    if stdin_data is None and not sys.stdin.isatty():
        with contextlib.suppress(Exception):
            stdin_data = sys.stdin.read().strip()
    if stdin_data:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(stdin_data)
            if isinstance(parsed, dict):
                payload = parsed

    if not payload and not event:
        target = (target_path or Path("eidos")).resolve()
        if target.name != "eidos" and (target / "eidos").exists():
            target = target / "eidos"
        if not target.exists():
            print(f"Error: Directory '{target}' does not exist.", file=sys.stderr)
            return 1
        validator = SpecValidator()
        report = validator.validate_directory(target)
        if report.is_clean:
            print(f"✓ SUCCESS: All {report.total_docs} specifications are valid.")
            return 0
        print(f"✗ FAILURES DETECTED ({len(report.schema_errors)} files with errors):", file=sys.stderr)
        for f, errs in report.schema_errors.items():
            for e in errs:
                print(f"  - {Path(f).name}: {e}", file=sys.stderr)
        for r in report.broken_references:
            print(f"  - Broken reference: {r}", file=sys.stderr)
        return 1

    resolved_event = _infer_event(event, payload, default_event="stop")
    ws_root = target_path.resolve() if target_path else find_workspace_root(payload)
    if ws_root.name == "eidos" and (ws_root / "system.adoc").exists():
        ws_root = ws_root.parent

    if resolved_event == "pre_tool":
        res = handle_lint_pre_tool_use(payload, ws_root)
    elif resolved_event == "post_invocation":
        res = handle_lint_post_invocation(payload, ws_root)
    elif resolved_event == "stop":
        res = handle_lint_stop(payload, ws_root)
    else:
        res = {}

    print(json.dumps(res))
    return 0


def execute_drift_hook(
    event: str | None = None,
    path: Path | None = None,
    repo_root: Path | None = None,
    stdin_data: str | None = None,
) -> int:
    """Execute the drift hook from CLI (`eidos hook drift`)."""
    target_path = path or repo_root
    payload: dict[str, Any] = {}
    if stdin_data is None and not sys.stdin.isatty():
        with contextlib.suppress(Exception):
            stdin_data = sys.stdin.read().strip()
    if stdin_data:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(stdin_data)
            if isinstance(parsed, dict):
                payload = parsed

    if not payload and not event:
        ws_root = (target_path or Path.cwd()).resolve()
        if ws_root.name == "eidos" and (ws_root / "system.adoc").exists():
            ws_root = ws_root.parent
        eidos_dir = ws_root / "eidos"
        if not eidos_dir.exists():
            print(f"Error: Living specifications directory '{eidos_dir}' does not exist.", file=sys.stderr)
            return 1
        drift_res = detect_drift(repo_root=ws_root, eidos_dir=eidos_dir, uncommitted=True)
        if drift_res.drift_detected:
            print(f"Drift detected in {len(drift_res.classification.code_files)} code file(s):", file=sys.stderr)
            for cf in drift_res.classification.code_files:
                print(f"  - {cf}", file=sys.stderr)
            return 1
        print("✓ SUCCESS: No spec-to-code drift detected.")
        return 0

    resolved_event = _infer_event(event, payload, default_event="stop")
    ws_root = target_path.resolve() if target_path else find_workspace_root(payload)
    if ws_root.name == "eidos" and (ws_root / "system.adoc").exists():
        ws_root = ws_root.parent

    if resolved_event == "post_invocation":
        res = handle_drift_post_invocation(payload, ws_root)
    elif resolved_event == "stop":
        res = handle_drift_stop(payload, ws_root)
    else:
        res = {}

    print(json.dumps(res))
    return 0
