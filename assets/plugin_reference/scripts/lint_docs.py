#!/usr/bin/env python3
"""Eidos Living Documentation Linter & Agent Lifecycle Hook.

Executes within agent environments (Antigravity hooks.json) and standalone CLI
to ensure AsciiDoc specifications strictly adhere to YAML frontmatter syntax,
JSON schema validation (Draft 2020-12), and cross-reference integrity.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

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
    from eidos.core.validator import SpecValidator
except ImportError:
    SpecValidator = None


def find_workspace_root(payload: Optional[Dict[str, Any]] = None) -> Path:
    """Determine the active workspace root directory."""
    if payload:
        ws_paths = payload.get("workspacePaths", [])
        if ws_paths and isinstance(ws_paths, list):
            p = Path(ws_paths[0])
            if p.exists():
                return p.resolve()

    # Fallback to traversing current working directory
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists() or (parent / "eidos").exists():
            return parent
    return cwd


def handle_pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate YAML frontmatter syntax before file modification tools execute."""
    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    args = tool_call.get("args", {})
    target_file_str = args.get("TargetFile", "")

    if not target_file_str:
        return {"decision": "allow"}

    target_path = Path(target_file_str)
    if target_path.suffix.lower() != ".adoc":
        return {"decision": "allow"}

    content_to_check: Optional[str] = None
    if tool_name == "write_to_file":
        content_to_check = args.get("CodeContent", "")
    elif tool_name == "replace_file_content" and target_path.exists():
        try:
            curr_text = target_path.read_text(encoding="utf-8")
            target_str = args.get("TargetContent", "")
            replace_str = args.get("ReplacementContent", "")
            if target_str in curr_text:
                content_to_check = curr_text.replace(target_str, replace_str, 1)
        except Exception:
            pass

    if content_to_check is not None:
        stripped = content_to_check.lstrip()
        if stripped.startswith("---"):
            parts = stripped.split("---", 2)
            if len(parts) < 3:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] Syntax Error: Missing closing '---' delimiter in frontmatter for '{target_path.name}'.",
                }
            yaml_part = parts[1]
            try:
                data = yaml.safe_load(yaml_part)
                if not isinstance(data, dict):
                    return {
                        "decision": "deny",
                        "reason": f"[Eidos Doc Linter] Syntax Error: Frontmatter in '{target_path.name}' must be a YAML mapping dictionary.",
                    }
                missing = [f for f in ["type", "schema", "id"] if f not in data]
                if missing:
                    return {
                        "decision": "deny",
                        "reason": f"[Eidos Doc Linter] Frontmatter in '{target_path.name}' is missing required fields: {', '.join(missing)}.",
                    }
            except yaml.YAMLError as exc:
                return {
                    "decision": "deny",
                    "reason": f"[Eidos Doc Linter] YAML Syntax Error in '{target_path.name}': {exc}",
                }

    return {"decision": "allow"}


def handle_post_invocation(payload: Dict[str, Any], workspace_root: Path) -> Dict[str, Any]:
    """Inject ephemeral diagnostics into agent context if specs are invalid."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists() or SpecValidator is None:
        return {"injectSteps": []}

    try:
        validator = SpecValidator()
        report = validator.validate_directory(eidos_dir)
        if not report.is_clean:
            errors: List[str] = []
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
    except Exception:
        pass

    return {"injectSteps": []}


def handle_stop(payload: Dict[str, Any], workspace_root: Path) -> Dict[str, Any]:
    """Block agent completion if any living specifications are invalid."""
    eidos_dir = workspace_root / "eidos"
    if not eidos_dir.exists() or SpecValidator is None:
        return {"decision": "allow"}

    try:
        validator = SpecValidator()
        report = validator.validate_directory(eidos_dir)
        if not report.is_clean:
            errors: List[str] = []
            for f, errs in report.schema_errors.items():
                fname = Path(f).name
                for e in errs[:3]:
                    errors.append(f"  - {fname}: {e}")
            for r in report.broken_references[:3]:
                errors.append(f"  - Broken reference: {r}")

            err_msg = "\n".join(errors[:10])
            reason = (
                f"🛑 [Eidos Doc Linter] Cannot complete task: One or more specifications in 'eidos/' failed validation:\n"
                f"{err_msg}\n\n"
                f"According to Eidos Rule #5, all specifications in eidos/ must strictly conform to their schema with zero broken references before concluding your work."
            )
            return {"decision": "continue", "reason": reason}
    except Exception as exc:
        return {
            "decision": "continue",
            "reason": f"🛑 [Eidos Doc Linter] Validator execution error: {exc}",
        }

    return {"decision": "allow"}


def run_standalone(target_path: Optional[Path] = None) -> int:
    """Run doc linter in interactive terminal mode."""
    target = (target_path or Path("eidos")).resolve()
    if not target.exists():
        print(f"Error: Directory '{target}' does not exist.", file=sys.stderr)
        return 1

    if SpecValidator is None:
        print("Error: SpecValidator could not be imported.", file=sys.stderr)
        return 1

    validator = SpecValidator()
    report = validator.validate_directory(target)

    print(f"\n--- Eidos Living Documentation Lint Report ---")
    print(f"Target: {target}")
    print(f"Total specs scanned: {report.total_docs}")
    print(f"Valid specs: {report.valid_docs}")

    if report.is_clean:
        print(f"\n✓ SUCCESS: All {report.total_docs} specifications are valid and have correct syntax.")
        return 0

    print(f"\n✗ FAILURES DETECTED ({len(report.schema_errors)} files with errors):", file=sys.stderr)
    for f, errs in report.schema_errors.items():
        print(f"\n  {Path(f).name}:", file=sys.stderr)
        for e in errs:
            print(f"    - {e}", file=sys.stderr)

    if report.broken_references:
        print("\nBroken cross-references:", file=sys.stderr)
        for r in report.broken_references:
            print(f"  - {r}", file=sys.stderr)

    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Eidos Living Documentation Linter & Lifecycle Hook")
    parser.add_argument(
        "--event",
        choices=["pre_tool", "post_tool", "pre_invocation", "post_invocation", "stop"],
        help="Explicit lifecycle event identifier",
    )
    parser.add_argument("--path", type=str, help="Target path for standalone scanning")
    args = parser.parse_args()

    # Check if input is coming from stdin pipe (Agent Hook invocation)
    is_piped = not sys.stdin.isatty()
    stdin_content = ""
    payload: Dict[str, Any] = {}

    if is_piped:
        try:
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                payload = json.loads(stdin_content)
        except Exception:
            payload = {}

    # If no piped JSON payload and no explicit hook event, run standalone
    if not payload and not args.event:
        target = Path(args.path) if args.path else None
        return run_standalone(target)

    # Determine event type from CLI flag or payload structure
    event = args.event
    if not event:
        if "toolCall" in payload:
            event = "pre_tool"
        elif "executionNum" in payload or "terminationReason" in payload:
            event = "stop"
        elif "invocationNum" in payload:
            event = "post_invocation"
        elif "stepIdx" in payload:
            event = "post_tool"
        else:
            event = "stop"

    ws_root = find_workspace_root(payload)

    if event == "pre_tool":
        res = handle_pre_tool_use(payload)
        print(json.dumps(res))
        return 0

    if event == "post_tool":
        print(json.dumps({}))
        return 0

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
