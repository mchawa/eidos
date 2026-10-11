"""Standard Model Context Protocol (MCP) stdio server for Eidos."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import eidos
from eidos.core.validator import SpecValidator
from eidos.core.parser import parse_spec_file

MCP_PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "eidos_validate",
        "description": "Validate all Eidos specifications (.adoc files) in a directory against bundled JSON/YAML schemas and verify cross-references.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute directory path containing specifications (defaults to 'eidos')."
                }
            }
        }
    },
    {
        "name": "eidos_info",
        "description": "Get Eidos engine version, loaded v1 schemas, supported platforms, and available agent adapters.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "eidos_list_components",
        "description": "List all monorepo components (C4 Level 2 containers) and interface contracts in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to scan for component manifests (defaults to 'eidos/architecture/components')."
                }
            }
        }
    },
    {
        "name": "eidos_create_delta",
        "description": "Scaffold a new SpecDelta mutation file (type: delta/mutation) for a feature addition, bugfix, or refactor.",
        "inputSchema": {
            "type": "object",
            "required": ["delta_id", "title", "kind", "motivation"],
            "properties": {
                "delta_id": {
                    "type": "string",
                    "description": "Unique delta ID (e.g. 'DLT-2026-001')."
                },
                "title": {
                    "type": "string",
                    "description": "Short title describing the change."
                },
                "kind": {
                    "type": "string",
                    "description": "Nature of change: feature_addition, bugfix, modification, feature_removal, refactor."
                },
                "motivation": {
                    "type": "string",
                    "description": "Why this change is needed."
                }
            }
        }
    },
    {
        "name": "eidos_read_spec",
        "description": "Parse and read an Eidos specification (.adoc file), returning structured frontmatter, metadata, and body.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute path to the .adoc specification file."
                }
            }
        }
    }
]


def handle_tool_call(name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Execute an MCP tool and return content blocks."""
    if name == "eidos_info":
        validator = SpecValidator()
        schemas = sorted(set(s for s in validator._schema_cache.keys() if "/" in s))
        payload = {
            "engine_version": eidos.__version__,
            "bundled_schemas": schemas,
            "supported_vcs": ["GitHub", "GitLab"],
            "supported_agents": ["Antigravity (agy)", "Claude Code", "OpenAI Codex", "Copilot"]
        }
        return [{"type": "text", "text": json.dumps(payload, indent=2)}]

    elif name == "eidos_validate":
        target = Path(arguments.get("path") or "eidos")
        if not target.exists():
            return [{"type": "text", "text": f"Error: directory '{target}' does not exist."}]

        validator = SpecValidator()
        report = validator.validate_directory(target)

        res = {
            "total_documents": report.total_docs,
            "valid_documents": report.valid_docs,
            "is_clean": report.is_clean,
            "documents": [
                {
                    "id": d.doc_id,
                    "type": d.doc_type,
                    "valid": d.is_valid,
                    "path": str(d.file_path),
                    "errors": d.errors
                }
                for d in report.documents
            ],
            "broken_references": report.broken_references
        }
        return [{"type": "text", "text": json.dumps(res, indent=2)}]

    elif name == "eidos_list_components":
        search_dir = Path(arguments.get("path") or "eidos/architecture")
        components = []
        if search_dir.exists():
            for p in search_dir.rglob("*.adoc"):
                doc = parse_spec_file(p)
                if doc.doc_type == "tech/component":
                    components.append({
                        "id": doc.doc_id,
                        "name": doc.frontmatter.get("name"),
                        "container_type": doc.frontmatter.get("container_type"),
                        "runtime": doc.frontmatter.get("runtime"),
                        "package_path": doc.frontmatter.get("package_path"),
                        "dependencies": doc.frontmatter.get("dependencies", {}),
                        "c4_internal_components": doc.frontmatter.get("c4_internal_components", []),
                        "file_path": str(p)
                    })
        return [{"type": "text", "text": json.dumps(components, indent=2)}]

    elif name == "eidos_create_delta":
        delta_id = arguments["delta_id"]
        title = arguments["title"]
        kind = arguments["kind"]
        motivation = arguments["motivation"]

        deltas_dir = Path("eidos/deltas/active")
        deltas_dir.mkdir(parents=True, exist_ok=True)
        file_path = deltas_dir / f"{delta_id.lower()}.adoc"

        content = f"""---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: {delta_id}
title: "{title}"
status: proposed

intent:
  kind: {kind}
  motivation: "{motivation}"

mutations: []
---
= SpecDelta: {title}
:toc: left

== Problem Statement
{motivation}

== Proposed Specification Changes
Describe the exact behavioral diffs and invariant updates here.
"""
        file_path.write_text(content, encoding="utf-8")
        return [{"type": "text", "text": f"Created new SpecDelta at {file_path}"}]

    elif name == "eidos_read_spec":
        file_path = Path(arguments.get("path", ""))
        if not file_path.exists():
            return [{"type": "text", "text": f"Error: specification file '{file_path}' does not exist."}]

        try:
            doc = parse_spec_file(file_path)
            res = {
                "id": doc.doc_id,
                "type": doc.doc_type,
                "schema": doc.schema_ref,
                "title": doc.title,
                "frontmatter": doc.frontmatter,
                "body_length": len(doc.body),
                "body_preview": doc.body[:500] if len(doc.body) > 500 else doc.body,
                "full_body": doc.body
            }
            return [{"type": "text", "text": json.dumps(res, indent=2)}]
        except Exception as e:
            return [{"type": "text", "text": f"Error parsing '{file_path}': {str(e)}"}]

    return [{"type": "text", "text": f"Unknown tool: {name}"}]


def run_mcp_server():
    """Run the stdio JSON-RPC MCP server loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "eidos-mcp-server",
                        "version": eidos.__version__
                    }
                }
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            # Handshake complete notification
            pass

        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS
                }
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            content = handle_tool_call(tool_name, tool_args)
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": content
                }
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

        elif req_id is not None:
            # Unhandled method with ID
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
