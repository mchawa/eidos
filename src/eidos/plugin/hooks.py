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
    with contextlib.suppress(ValueError, RuntimeError):
        resolved = target_path.resolve() if target_path.is_absolute() else (ws_root / target_path).resolve()
        rel = resolved.relative_to(ws_root)
        if rel.parts and rel.parts[0] == "eidos":
            return True

    parts = target_path.parts
    if parts and parts[0] == ".":
        parts = parts[1:]
    return bool(parts and parts[0] == "eidos")


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
            from eidos.core.workspace.assets import get_v1_schemas_dir

            rel_clean = schema_rel.strip().removeprefix("schemas/v1/").removeprefix("schemas/").lstrip("/")
            schema_file = get_v1_schemas_dir() / rel_clean
            if not schema_file.exists():
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Broken schema reference '{schema_rel}' in '{target_path.name}': schema file not found.",
                }

    return {"decision": "allow"}


def handle_lint_pre_tool_use(payload: dict[str, Any], workspace_root: Path | None = None) -> dict[str, Any]:
    """Validate YAML frontmatter or JSON syntax before file modification tools execute on eidos/."""
    target_file_str, content_to_check = _extract_tool_edit_request(payload, workspace_root)

    if not target_file_str or not is_eidos_doc(target_file_str, workspace_root):
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
        if is_agy_proto:
            return {}
        return handle_lint_post_invocation(payload, ws_root)

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


def _compute_working_tree_digest(workspace_root: Path, eidos_dir: Path) -> str:
    """Compute a fast working-tree digest from HEAD SHA, git status --porcelain, and spec/dirty file mtimes."""
    import subprocess

    h = hashlib.sha256()
    h.update(str(workspace_root.resolve()).encode("utf-8"))
    git_dir = workspace_root / ".git"
    dirty_rels: list[str] = []
    if git_dir.exists():
        try:
            rev = subprocess.run(
                ["git", "-c", "core.quotePath=false", "rev-parse", "HEAD"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            h.update(rev.stdout.strip().encode("utf-8"))
            st = subprocess.run(
                ["git", "-c", "core.quotePath=false", "status", "--porcelain", "-z"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            h.update(st.stdout.encode("utf-8"))
            for entry in st.stdout.split("\0"):
                if len(entry) > 3:
                    dirty_rels.append(entry[3:])
        except (OSError, subprocess.SubprocessError):
            pass

    for rel in sorted(set(dirty_rels)):
        if "/.cache/" in f"/{rel}/" or rel.startswith(".cache/"):
            continue
        p = workspace_root / rel
        try:
            stat = p.stat()
            h.update(f"D:{rel}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
        except OSError:
            h.update(f"D:{rel}:MISSING\n".encode())

    if eidos_dir.is_dir():
        for p in sorted(eidos_dir.rglob("*")):
            if ".cache" in p.parts or not p.is_file():
                continue
            try:
                stat = p.stat()
                rel = str(p.relative_to(eidos_dir))
                h.update(f"E:{rel}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
            except OSError:
                continue

    return h.hexdigest()


def _check_hook_state_short_circuit(eidos_dir: Path, key: str, digest: str) -> bool:
    """Return True if the last clean PostInvocation hook run recorded the same working-tree digest."""
    state_file = eidos_dir / ".cache" / "hook-state.json"
    if not state_file.is_file():
        return False
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
        loaded = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and loaded.get(key) == digest:
            return True
    return False


def _record_hook_state_clean(eidos_dir: Path, key: str, digest: str) -> None:
    """Persist the clean working-tree digest in eidos/.cache/hook-state.json."""
    cache_dir = eidos_dir / ".cache"
    state_file = cache_dir / "hook-state.json"
    state: dict[str, Any] = {}
    if state_file.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
    state[key] = digest
    with contextlib.suppress(OSError):
        cache_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_hook_state(eidos_dir: Path, key: str) -> None:
    """Remove a recorded clean digest when validation or drift fails."""
    state_file = eidos_dir / ".cache" / "hook-state.json"
    if not state_file.is_file():
        return
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
        loaded = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and key in loaded:
            loaded.pop(key, None)
            if loaded:
                state_file.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
            else:
                state_file.unlink(missing_ok=True)


def handle_lint_post_invocation(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Inject ephemeral diagnostics into agent context if living specifications in eidos/ are invalid."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists():
        return {"injectSteps": []}

    digest = _compute_working_tree_digest(workspace_root, eidos_dir)
    if _check_hook_state_short_circuit(eidos_dir, "lint_clean_digest", digest):
        return {"injectSteps": []}

    try:
        validator = SpecValidator()
        report = validator.validate_directory(eidos_dir)
        if not report.is_clean:
            _clear_hook_state(eidos_dir, "lint_clean_digest")
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
        _record_hook_state_clean(eidos_dir, "lint_clean_digest", digest)
    except Exception as exc:
        err_msg = f"⚠️ [Eidos Doc Linter Error] Internal validation error: {exc}"
        print(err_msg, file=sys.stderr)
        return {"injectSteps": [{"ephemeralMessage": err_msg}]}

    return {"injectSteps": []}


def _check_and_record_stop_gate(eidos_dir: Path, gate_key: str, fingerprint: str) -> bool:
    """Return True if this fingerprint should block (first occurrence), or False if already blocked once."""
    cache_dir = eidos_dir / ".cache"
    gate_file = cache_dir / "stop-gate.json"
    state: dict[str, Any] = {}
    if gate_file.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
            loaded = json.loads(gate_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded

    prev_fp = state.get(gate_key)
    if prev_fp == fingerprint:
        # Already blocked once for this exact fingerprint; clear key and allow termination
        state.pop(gate_key, None)
        with contextlib.suppress(OSError):
            if state:
                cache_dir.mkdir(parents=True, exist_ok=True)
                gate_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
            else:
                gate_file.unlink(missing_ok=True)
        return False

    state[gate_key] = fingerprint
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

    digest = _compute_working_tree_digest(workspace_root, eidos_dir)
    if _check_hook_state_short_circuit(eidos_dir, "drift_clean_digest", digest):
        return {"injectSteps": []}

    try:
        drift_res = detect_drift(repo_root=workspace_root, eidos_dir=eidos_dir, uncommitted=True)
        if drift_res.drift_detected:
            _clear_hook_state(eidos_dir, "drift_clean_digest")
            code_count = len(drift_res.classification.code_files)
            top_files = ", ".join(Path(f).name for f in drift_res.classification.code_files[:3])
            more = f" (+{code_count - 3} more)" if code_count > 3 else ""
            remediation_hint = ""
            if drift_res.remediation_suggestions:
                remediation_hint = f"\nAction needed: {drift_res.remediation_suggestions[0]}"
            msg = (
                f"[Eidos Sync Notice] Uncommitted code changes detected in {top_files}{more} without living spec updates in 'eidos/'.{remediation_hint}\n"
                f"Remember to synchronize living specifications in 'eidos/' (and verify with 'eidos drift --uncommitted') before concluding."
            )
            return {"injectSteps": [{"ephemeralMessage": msg}]}
    except Exception as exc:
        err_msg = f"⚠️ [Eidos Drift Gate Error] Internal drift check error: {exc}"
        print(err_msg, file=sys.stderr)
        return {"injectSteps": [{"ephemeralMessage": err_msg}]}

    # Lazily ensure knowledge graph index is synchronized
    try:
        from eidos.core.index.graph_index import ArchitecturalIndex

        cache_db = eidos_dir / ".cache" / "index.db"
        if cache_db.exists():
            idx = ArchitecturalIndex(db_path=cache_db, repo_root=workspace_root, eidos_dir=eidos_dir)
            dirty, _, _ = idx.is_dirty()
            if dirty:
                idx.sync()
            idx.close()
    except Exception as exc:
        print(f"[Eidos Drift Gate Warning] Knowledge graph cache sync skipped: {exc}", file=sys.stderr)

    _record_hook_state_clean(eidos_dir, "drift_clean_digest", digest)
    return {"injectSteps": []}


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


def handle_drift_stop(payload: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    """Block agent completion once per drift fingerprint if spec-to-code, invariant, or validation drift is detected."""
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
            all_tracked = (
                list(drift_res.classification.code_files)
                + list(drift_res.classification.spec_files)
                + list(drift_res.classification.delta_files)
                + list(drift_res.classification.test_files)
            )
            fp_payload = json.dumps(
                {
                    "code_files": sorted(drift_res.classification.code_files),
                    "spec_files": sorted(drift_res.classification.spec_files),
                    "delta_files": sorted(drift_res.classification.delta_files),
                    "file_hashes": _hash_files(workspace_root, all_tracked),
                    "remediation": drift_res.remediation_suggestions,
                    "validation_errors": drift_res.validation_errors,
                    "uncovered_invariants": [inv.invariant_id for inv in drift_res.uncovered_invariants],
                    "orphan_invariants": [f"{oid}:{loc}" for oid, loc in drift_res.orphan_invariants],
                },
                sort_keys=True,
            )
            fp = hashlib.sha256(fp_payload.encode("utf-8")).hexdigest()
            if not _check_and_record_stop_gate(eidos_dir, "fingerprint", fp):
                return {"decision": "allow"}

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
        if _check_and_record_stop_gate(eidos_dir, "fingerprint", fp):
            return {
                "decision": "continue",
                "reason": f"🛑 [Eidos Drift Gate] Internal drift check error: {exc}",
            }

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
    if ws_root.name == "eidos" and (ws_root / "system.adoc").exists():
        ws_root = ws_root.parent

    if resolved_event == "pre_tool":
        res = handle_lint_pre_tool_use(payload, ws_root)
    elif resolved_event == "post_tool":
        res = handle_lint_post_tool_use(payload, ws_root)
    elif resolved_event == "post_invocation":
        res = handle_lint_post_invocation(payload, ws_root)
    elif resolved_event == "stop":
        res = handle_lint_stop(payload, ws_root)
    else:
        res = {}

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
        if ws_root.name == "eidos" and (ws_root / "system.adoc").exists():
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

