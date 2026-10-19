"""Agent lifecycle hook handlers for Eidos living specification linting and drift gating."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from eidos.core.spec.parser import extract_frontmatter
from eidos.core.spec.validator import SpecValidator
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


def is_eidos_doc(target_file_str: str, workspace_root: Path | None = None) -> bool:
    """Check if the given file path refers to an Eidos specification document under eidos/."""
    if not target_file_str:
        return False
    target_path = Path(target_file_str)
    if target_path.suffix.lower() not in (".adoc", ".json"):
        return False

    ws_root = (workspace_root or find_workspace_root()).resolve()
    try:
        resolved = target_path.resolve() if target_path.is_absolute() else (ws_root / target_path).resolve()
        rel = resolved.relative_to(ws_root)
    except (OSError, ValueError, RuntimeError):
        return False
    return bool(rel.parts and rel.parts[0] == "eidos")


def _extract_tool_edit_request(
    payload: dict[str, Any], workspace_root: Path | None = None
) -> tuple[str, str | None]:
    """Normalize tool target file and proposed content across Antigravity, Claude Code, Cursor, Cline, and OpenCode."""
    import os

    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        args = tool_call.get("args") or tool_call.get("input") or tool_call.get("arguments") or {}
    else:
        args = (
            payload.get("tool_input")
            or payload.get("args")
            or payload.get("input")
            or payload.get("parameters")
            or payload.get("arguments")
            or {}
        )
    if not isinstance(args, dict):
        args = {}

    target_file_str = ""
    for key in ("TargetFile", "file_path", "path", "target_file", "filepath", "file"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            target_file_str = val.strip()
            break

    if not target_file_str:
        for key in ("TargetFile", "file_path", "path", "target_file"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                target_file_str = val.strip()
                break

    if not target_file_str:
        target_file_str = os.environ.get("EIDOS_TARGET_FILE", "").strip()

    if not target_file_str:
        return "", None

    ws_root = (workspace_root or find_workspace_root(payload)).resolve()
    raw_target = Path(target_file_str)
    resolved_target = raw_target if raw_target.is_absolute() else (ws_root / raw_target)
    if not resolved_target.exists() and raw_target.exists():
        resolved_target = raw_target

    content_to_check: str | None = None
    for content_key in ("CodeContent", "content", "file_text", "new_content"):
        val = args.get(content_key)
        if isinstance(val, str):
            content_to_check = val
            break

    chunks = args.get("ReplacementChunks") or args.get("edits") or args.get("chunks")
    old_s = args.get("TargetContent") if "TargetContent" in args else args.get("old_string", args.get("old_str"))
    new_s = (
        args.get("ReplacementContent")
        if "ReplacementContent" in args
        else args.get("new_string", args.get("new_str"))
    )

    if content_to_check is None and resolved_target.exists():
        with contextlib.suppress(OSError, UnicodeDecodeError):
            curr_text = resolved_target.read_text(encoding="utf-8")
            if isinstance(chunks, list) and chunks:
                mutated = curr_text
                applied_any = False
                for chunk in chunks:
                    if isinstance(chunk, dict):
                        c_old = (
                            chunk.get("TargetContent")
                            if "TargetContent" in chunk
                            else chunk.get("old_string", chunk.get("old_str"))
                        )
                        c_new = (
                            chunk.get("ReplacementContent")
                            if "ReplacementContent" in chunk
                            else chunk.get("new_string", chunk.get("new_str"))
                        )
                        if isinstance(c_old, str) and isinstance(c_new, str) and c_old in mutated:
                            mutated = mutated.replace(c_old, c_new, 1)
                            applied_any = True
                if applied_any:
                    content_to_check = mutated
            elif isinstance(old_s, str) and isinstance(new_s, str) and old_s in curr_text:
                content_to_check = curr_text.replace(old_s, new_s, 1)

    if content_to_check is None and not resolved_target.exists():
        if isinstance(new_s, str) and new_s.lstrip("\ufeff").lstrip().startswith("---"):
            content_to_check = new_s
        elif isinstance(chunks, list) and chunks and isinstance(chunks[0], dict):
            first_new = (
                chunks[0].get("ReplacementContent")
                if "ReplacementContent" in chunks[0]
                else chunks[0].get("new_string", chunks[0].get("new_str"))
            )
            if isinstance(first_new, str) and first_new.lstrip("\ufeff").lstrip().startswith("---"):
                content_to_check = first_new

    return target_file_str, content_to_check


def _validate_doc_content(target_path: Path, content_to_check: str) -> dict[str, Any]:
    """Validate JSON syntax or AsciiDoc YAML frontmatter and schema reference."""
    if target_path.suffix.lower() == ".json":
        try:
            json.loads(content_to_check)
        except json.JSONDecodeError as exc:
            return {
                "decision": "deny",
                "reason": f"[Eidos Doc Linter] JSON Syntax Error in '{target_path.name}': {exc}",
            }

    stripped = content_to_check.lstrip("\ufeff").lstrip()
    if target_path.suffix.lower() == ".adoc":
        fname_lower = target_path.name.lower()
        is_exempt_adoc = (
            fname_lower == "readme.adoc"
            or fname_lower.startswith("_")
            or (fname_lower == "system.adoc" and not stripped)
        )
        if not stripped.startswith("---"):
            if not is_exempt_adoc:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Missing YAML frontmatter block (delimited by '---') in '{target_path.name}'.",
                }
            return {"decision": "allow"}
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
        schema_rel = data.get("schema")
        if isinstance(schema_rel, str) and schema_rel.strip():
            from eidos.core.spec.models import SpecDocument
            from eidos.core.workspace.assets import get_v1_schemas_dir

            rel_clean = schema_rel.strip().removeprefix("schemas/v1/").removeprefix("schemas/").lstrip("/")
            schema_file = get_v1_schemas_dir() / rel_clean
            if not schema_file.exists():
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Broken schema reference '{schema_rel}' in '{target_path.name}': schema file not found.",
                }
            doc = SpecDocument(
                file_path=target_path,
                doc_id=str(data.get("id", "")),
                doc_type=str(data.get("type", "")),
                schema_ref=schema_rel.strip(),
                title=str(data.get("title") or data.get("name") or ""),
                frontmatter=data,
                body="",
            )
            schema_errs = SpecValidator().validate_document(doc)
            if schema_errs:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Schema validation failed in '{target_path.name}': {'; '.join(schema_errs[:3])}",
                }

    return {"decision": "allow"}


def handle_lint_pre_tool_use(payload: dict[str, Any], workspace_root: Path | None = None) -> dict[str, Any]:
    """Validate YAML frontmatter or JSON syntax before file modification tools execute on eidos/."""
    ws_root = (workspace_root or find_workspace_root(payload)).resolve()
    target_file_str, content_to_check = _extract_tool_edit_request(payload, ws_root)

    if not target_file_str or not is_eidos_doc(target_file_str, ws_root):
        return {"decision": "allow"}

    target_path = Path(target_file_str)
    if content_to_check is not None:
        return _validate_doc_content(target_path, content_to_check)

    return {"decision": "allow"}


def handle_lint_post_tool_use(payload: dict[str, Any], workspace_root: Path | None = None) -> dict[str, Any]:
    """Validate modified Eidos document after tool use across agent harnesses (with stdin fallback)."""
    is_agy_proto = "stepIdx" in payload or "toolCall" in payload
    ws_root = (workspace_root or find_workspace_root(payload)).resolve()
    target_file_str, content_to_check = _extract_tool_edit_request(payload, ws_root)

    if not target_file_str:
        return {} if is_agy_proto else {"decision": "allow"}

    if not is_eidos_doc(target_file_str, ws_root):
        return {} if is_agy_proto else {"decision": "allow"}

    raw_target = Path(target_file_str)
    resolved_target = raw_target if raw_target.is_absolute() else (ws_root / raw_target)
    if content_to_check is None and resolved_target.exists():
        with contextlib.suppress(OSError, UnicodeDecodeError):
            content_to_check = resolved_target.read_text(encoding="utf-8")

    if content_to_check is not None:
        res = _validate_doc_content(raw_target, content_to_check)
        if is_agy_proto:
            if res.get("decision") == "deny":
                print(res.get("reason", "[Eidos Doc Linter] Validation failed."), file=sys.stderr)
            return {}
        return res

    return {} if is_agy_proto else {"decision": "allow"}


_STOP_GATE_RELEASE_WARNING = (
    "⚠️ [Eidos Stop Gate] Allowing termination on repeated failure fingerprint, "
    "but unresolved drift/validation issues remain in 'eidos/'."
)


def _extract_session_id(payload: dict[str, Any] | None = None) -> str:
    """Extract optional session or conversation ID from hook payload or environment."""
    import os

    if isinstance(payload, dict):
        for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return os.environ.get("EIDOS_SESSION_ID", "").strip()


def _check_and_record_stop_gate(
    eidos_dir: Path,
    gate_key: str,
    fingerprint: str,
    session_id: str = "",
    ttl_seconds: float | None = None,
    items: list[str] | None = None,
) -> bool:
    """Return True if this fingerprint should block (first occurrence or expired/new session), or False if already blocked once."""
    import os
    import time

    now = time.time()
    if ttl_seconds is None:
        try:
            eff_ttl = float(os.environ.get("EIDOS_STOP_GATE_TTL_SECONDS", "600"))
        except ValueError:
            eff_ttl = 600.0
    else:
        eff_ttl = float(ttl_seconds)

    cache_dir = eidos_dir / ".cache"
    gate_file = cache_dir / "stop-gate.json"
    state: dict[str, Any] = {}
    if gate_file.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
            loaded = json.loads(gate_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded

    prev_entry = state.get(gate_key)
    prev_fp: str | None = None
    prev_items: set[str] = set()
    expired = False
    session_mismatch = False

    if isinstance(prev_entry, dict):
        prev_fp = str(prev_entry.get("hash", "")) if prev_entry.get("hash") else None
        raw_items = prev_entry.get("items")
        if isinstance(raw_items, list):
            prev_items = {str(x) for x in raw_items if x}
        prev_ts = prev_entry.get("timestamp")
        if isinstance(prev_ts, (int, float)) and (now - float(prev_ts)) > eff_ttl:
            expired = True
        prev_sid = str(prev_entry.get("session_id") or "").strip()
        if session_id and prev_sid and session_id != prev_sid:
            session_mismatch = True
    elif isinstance(prev_entry, str):
        prev_fp = prev_entry

    cur_items = {str(x) for x in (items or []) if x}
    is_already_blocked = prev_fp == fingerprint or bool(
        cur_items and prev_items and cur_items.issubset(prev_items)
    )

    if is_already_blocked and not expired and not session_mismatch:
        # Already blocked once for this exact fingerprint (or strict subset of errors) within active TTL/session
        state.pop(gate_key, None)
        with contextlib.suppress(OSError):
            if state:
                cache_dir.mkdir(parents=True, exist_ok=True)
                gate_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            else:
                gate_file.unlink(missing_ok=True)
        return False

    entry_data: dict[str, Any] = {
        "hash": fingerprint,
        "timestamp": now,
        "session_id": session_id,
    }
    if cur_items:
        entry_data["items"] = sorted(cur_items)
    state[gate_key] = entry_data
    with contextlib.suppress(OSError):
        cache_dir.mkdir(parents=True, exist_ok=True)
        gate_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return True


def _clear_stop_gate(eidos_dir: Path, gate_key: str) -> None:
    """Clear a recorded stop-gate key when validation/drift passes cleanly."""
    gate_file = eidos_dir / ".cache" / "stop-gate.json"
    if not gate_file.is_file():
        return
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
        loaded = json.loads(gate_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and gate_key in loaded:
            loaded.pop(gate_key, None)
            if loaded:
                gate_file.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
            else:
                gate_file.unlink(missing_ok=True)


def _compute_adoc_snapshot(eidos_dir: Path) -> tuple[int, int, int]:
    """Compute a fast (file_count, max_mtime_ns, total_size) snapshot of .adoc files in eidos_dir."""
    count = 0
    max_mtime = 0
    total_size = 0
    for p in eidos_dir.rglob("*.adoc"):
        if ".cache" in p.parts or p.is_symlink():
            continue
        try:
            st = p.stat()
            count += 1
            if st.st_mtime_ns > max_mtime:
                max_mtime = st.st_mtime_ns
            total_size += st.st_size
        except OSError:
            continue
    return (count, max_mtime, total_size)


def _save_stop_validation_snapshot(eidos_dir: Path, report: Any) -> None:
    """Persist the Stop validation report in SQLite metadata table so handle_drift_stop skips duplicate validation."""
    import sqlite3
    import time

    db_path = eidos_dir / ".cache" / "index.db"
    if not db_path.is_file():
        return
    snap = _compute_adoc_snapshot(eidos_dir)
    docs_summary = [
        {
            "file_path": str(d.file_path),
            "doc_type": d.doc_type,
            "doc_id": d.doc_id,
            "errors": list(d.errors),
        }
        for d in getattr(report, "documents", [])
    ]
    payload = json.dumps(
        {
            "timestamp": time.time(),
            "snapshot": list(snap),
            "documents": docs_summary,
            "schema_errors": {str(k): list(v) for k, v in report.schema_errors.items()},
            "broken_references": list(report.broken_references),
        }
    )
    with (
        contextlib.suppress(sqlite3.Error, OSError),
        contextlib.closing(sqlite3.connect(str(db_path))) as conn,
        conn,
    ):
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_stop_validation', ?)",
            (payload,),
        )


def _load_stop_validation_snapshot(eidos_dir: Path, max_age_seconds: float = 5.0) -> Any | None:
    """Load a recent Stop validation report if the .adoc file snapshot is unchanged."""
    import sqlite3
    import time

    from eidos.core.spec.models import SpecDocument, ValidationReport

    db_path = eidos_dir / ".cache" / "index.db"
    if not db_path.is_file():
        return None
    with (
        contextlib.suppress(sqlite3.Error, OSError, json.JSONDecodeError, ValueError, KeyError, TypeError),
        contextlib.closing(sqlite3.connect(str(db_path))) as conn,
    ):
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'last_stop_validation'"
        ).fetchone()
        if not row or not row[0]:
            return None
        data = json.loads(row[0])
        ts = float(data["timestamp"])
        if (time.time() - ts) > max_age_seconds:
            return None
        if tuple(data["snapshot"]) != _compute_adoc_snapshot(eidos_dir):
            return None
        docs = [
            SpecDocument(
                file_path=Path(str(d.get("file_path") or "")),
                frontmatter={},
                body="",
                doc_type=str(d.get("doc_type") or "unknown"),
                doc_id=str(d.get("doc_id") or ""),
                errors=list(d.get("errors") or []),
            )
            for d in (data.get("documents") or [])
            if isinstance(d, dict)
        ]
        return ValidationReport(
            documents=docs,
            schema_errors={str(k): list(v) for k, v in (data.get("schema_errors") or {}).items()},
            broken_references=list(data.get("broken_references") or []),
        )
    return None


def _hash_files(workspace_root: Path, rel_paths: list[str]) -> dict[str, str]:
    """Compute SHA-256 content hashes for relative paths so edits to already-drifted files alter the fingerprint."""
    digests: dict[str, str] = {}
    for rel in sorted(set(rel_paths)):
        p = (workspace_root / rel) if not Path(rel).is_absolute() else Path(rel)
        try:
            digests[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "MISSING"
        except OSError:
            digests[rel] = "UNREADABLE"
    return digests


def handle_lint_stop(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Block agent completion once if any living specifications in eidos/ fail validation."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"decision": "allow"}

    session_id = _extract_session_id(payload)
    try:
        validator = SpecValidator()
        report = validator.validate_directory(eidos_dir)
        _save_stop_validation_snapshot(eidos_dir, report)
        if not report.is_clean:
            errors: list[str] = []
            all_errors: list[str] = []
            tracked_specs = [str(f) for f in report.schema_errors]
            if report.broken_references:
                tracked_specs.extend(
                    str(p) for p in sorted(eidos_dir.rglob("*.adoc")) if ".cache" not in p.parts
                )
            file_hashes = _hash_files(workspace_root, tracked_specs)
            error_items: list[str] = []
            for f, errs in sorted(report.schema_errors.items(), key=lambda x: str(x[0])):
                fname = Path(f).name
                fhash = file_hashes.get(str(f), "")
                for idx_e, e in enumerate(errs):
                    line = f"  - {fname}: {e}"
                    all_errors.append(line)
                    error_items.append(f"schema:{f}:{fhash}:{e}")
                    if idx_e < 3:
                        errors.append(line)
            for idx_r, r in enumerate(report.broken_references):
                line = f"  - Broken reference: {r}"
                all_errors.append(line)
                error_items.append(f"ref:{r}")
                if idx_r < 3:
                    errors.append(line)

            err_msg = "\n".join(errors[:10])
            fp_payload = json.dumps(
                {
                    "errors": all_errors,
                    "file_hashes": file_hashes,
                },
                sort_keys=True,
            )
            fp = hashlib.sha256(fp_payload.encode("utf-8")).hexdigest()
            if not _check_and_record_stop_gate(
                eidos_dir, "lint_fingerprint", fp, session_id=session_id, items=error_items
            ):
                return {"decision": "allow", "systemMessage": _STOP_GATE_RELEASE_WARNING}

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
        if _check_and_record_stop_gate(eidos_dir, "lint_fingerprint", fp, session_id=session_id):
            return {
                "decision": "continue",
                "reason": f"🛑 [Eidos Doc Linter] Internal validation error: {exc}",
            }
        return {"decision": "allow", "systemMessage": _STOP_GATE_RELEASE_WARNING}

    return {"decision": "allow"}


def handle_drift_stop(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Block agent completion once per drift fingerprint if spec-to-code, invariant, or validation drift is detected."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"decision": "allow"}

    session_id = _extract_session_id(payload)
    try:
        cached_val = _load_stop_validation_snapshot(eidos_dir)
        drift_kwargs: dict[str, Any] = {
            "repo_root": workspace_root,
            "eidos_dir": eidos_dir,
            "uncommitted": True,
        }
        if cached_val is not None:
            drift_kwargs["validation_report"] = cached_val
        try:
            drift_res = detect_drift(**drift_kwargs)
        except TypeError:
            drift_kwargs.pop("validation_report", None)
            drift_res = detect_drift(**drift_kwargs)
        has_non_code_failure = (
            not drift_res.validation_passed
            or bool(drift_res.uncovered_invariants)
            or bool(drift_res.orphan_invariants)
        )
        if drift_res.drift_detected or has_non_code_failure:
            all_tracked = (
                list(drift_res.classification.code_files)
                + list(drift_res.classification.spec_files)
                + list(drift_res.classification.delta_files)
                + list(drift_res.classification.test_files)
            )
            file_hashes = _hash_files(workspace_root, all_tracked)
            drift_items: list[str] = [
                *(f"code:{cf}:{file_hashes.get(cf, '')}" for cf in drift_res.classification.code_files),
                *(f"val:{ve}" for ve in drift_res.validation_errors),
                *(f"uncov:{inv.invariant_id}" for inv in drift_res.uncovered_invariants),
                *(f"orphan:{oid}:{loc}" for oid, loc in drift_res.orphan_invariants),
            ]
            fp_payload = json.dumps(
                {
                    "code_files": sorted(drift_res.classification.code_files),
                    "spec_files": sorted(drift_res.classification.spec_files),
                    "delta_files": sorted(drift_res.classification.delta_files),
                    "file_hashes": file_hashes,
                    "remediation": drift_res.remediation_suggestions,
                    "validation_errors": drift_res.validation_errors,
                    "uncovered_invariants": [inv.invariant_id for inv in drift_res.uncovered_invariants],
                    "orphan_invariants": [f"{oid}:{loc}" for oid, loc in drift_res.orphan_invariants],
                },
                sort_keys=True,
            )
            fp = hashlib.sha256(fp_payload.encode("utf-8")).hexdigest()
            if not _check_and_record_stop_gate(
                eidos_dir, "fingerprint", fp, session_id=session_id, items=drift_items
            ):
                return {"decision": "allow", "systemMessage": _STOP_GATE_RELEASE_WARNING}

            remediation_lines = "\n".join(f"  {rem}" for rem in drift_res.remediation_suggestions)
            if drift_res.classification.code_files:
                code_files_str = "\n".join(f"  - {cf}" for cf in drift_res.classification.code_files[:8])
                if len(drift_res.classification.code_files) > 8:
                    code_files_str += f"\n  ... and {len(drift_res.classification.code_files) - 8} more files"
                cause_block = (
                    f"{len(drift_res.classification.code_files)} code file(s) were modified without corresponding living specification updates in 'eidos/':\n"
                    f"{code_files_str}"
                )
            else:
                cause_parts: list[str] = []
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
                    cause_parts.append(
                        "Specification or SpecDelta synchronization issue detected in 'eidos/'."
                    )
                cause_block = "\n".join(cause_parts)

            reason = (
                f"🛑 [Eidos Drift Gate] Cannot complete task: Spec-to-code drift detected!\n"
                f"{cause_block}\n\n"
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
        if _check_and_record_stop_gate(eidos_dir, "fingerprint", fp, session_id=session_id):
            return {
                "decision": "continue",
                "reason": f"🛑 [Eidos Drift Gate] Internal drift check error: {exc}",
            }
        return {"decision": "allow", "systemMessage": _STOP_GATE_RELEASE_WARNING}

    return {"decision": "allow"}


def _infer_event(event: str | None, payload: dict[str, Any], default_event: str = "stop") -> str:
    candidate = event or payload.get("hook_event_name") or payload.get("event")
    if isinstance(candidate, str) and candidate.strip():
        norm = candidate.strip().lower().replace("-", "_")
        if norm in ("pretooluse", "pre_tool_use", "pre_tool"):
            return "pre_tool"
        if norm in ("postinvocation", "post_invocation"):
            return "post_invocation"
        if norm in ("posttooluse", "post_tool_use", "post_tool"):
            return "post_tool"
        if norm == "stop":
            return "stop"
        return norm
    if "stepIdx" in payload or "tool_response" in payload:
        return "post_tool"
    if "toolCall" in payload or "tool_input" in payload or "tool_name" in payload:
        return "pre_tool"
    if "executionNum" in payload or "terminationReason" in payload:
        return "stop"
    if "invocationNum" in payload:
        return "post_invocation"
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
        with contextlib.suppress(OSError):
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
    if ws_root.name == "eidos" and ((ws_root / "system.adoc").exists() or not (ws_root / "eidos").exists()):
        ws_root = ws_root.parent

    if resolved_event == "pre_tool":
        res = handle_lint_pre_tool_use(payload, ws_root)
    elif resolved_event == "post_tool":
        res = handle_lint_post_tool_use(payload, ws_root)
    elif resolved_event == "stop":
        res = handle_lint_stop(payload, ws_root)
    else:
        res = {"decision": "allow"}

    print(json.dumps(res))
    return 0


run_lint_hook = execute_lint_hook


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
        with contextlib.suppress(OSError):
            stdin_data = sys.stdin.read().strip()
    if stdin_data:
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(stdin_data)
            if isinstance(parsed, dict):
                payload = parsed

    if not payload and not event:
        ws_root = (target_path or Path.cwd()).resolve()
        if ws_root.name == "eidos" and ((ws_root / "system.adoc").exists() or not (ws_root / "eidos").exists()):
            ws_root = ws_root.parent
        eidos_dir = ws_root / "eidos"
        if not eidos_dir.exists():
            print(f"Error: Living specifications directory '{eidos_dir}' does not exist.", file=sys.stderr)
            return 1
        drift_res = detect_drift(repo_root=ws_root, eidos_dir=eidos_dir, uncommitted=True)
        if not drift_res.is_clean or drift_res.drift_detected or not drift_res.validation_passed:
            if drift_res.classification.code_files and drift_res.drift_detected:
                print(f"Drift detected in {len(drift_res.classification.code_files)} code file(s):", file=sys.stderr)
                for cf in drift_res.classification.code_files:
                    print(f"  - {cf}", file=sys.stderr)
            else:
                print("Drift or specification governance failure detected:", file=sys.stderr)
            for rem in drift_res.remediation_suggestions:
                print(f"  - {rem}", file=sys.stderr)
            for ve in drift_res.validation_errors:
                print(f"  - Validation error: {ve}", file=sys.stderr)
            return 1
        print("✓ SUCCESS: No spec-to-code drift detected.")
        return 0

    resolved_event = _infer_event(event, payload, default_event="stop")
    ws_root = target_path.resolve() if target_path else find_workspace_root(payload)
    if ws_root.name == "eidos" and ((ws_root / "system.adoc").exists() or not (ws_root / "eidos").exists()):
        ws_root = ws_root.parent

    res = handle_drift_stop(payload, ws_root) if resolved_event == "stop" else {"decision": "allow"}

    print(json.dumps(res))
    return 0

