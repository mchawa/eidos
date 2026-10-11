"""Standard Model Context Protocol (MCP) stdio server for Eidos."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import eidos
from eidos.core.validator import SpecValidator
from eidos.core.parser import parse_spec_file
from eidos.core.scaffolder import (
    create_spec,
    delete_spec,
    list_specs,
    apply_delta,
)

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
    },
    {
        "name": "eidos_list_specs",
        "description": "List all living specifications and SpecDeltas in the repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to specifications directory (defaults to 'eidos')."
                },
                "type": {
                    "type": "string",
                    "description": "Filter by type (capability, policy, contract, component, domain, persona, delta)."
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status (active, draft, proposed, applied)."
                }
            }
        }
    },
    {
        "name": "eidos_create_spec",
        "description": "Scaffold a new living specification document (capability, policy, contract, component, domain, persona, delta).",
        "inputSchema": {
            "type": "object",
            "required": ["type", "id", "title"],
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Specification type: capability, policy, contract, component, domain, persona, delta, exploration."
                },
                "id": {
                    "type": "string",
                    "description": "Unique identifier (e.g. 'CAP-AUTH-001', 'POL-003', 'DLT-2026-002')."
                },
                "title": {
                    "type": "string",
                    "description": "Human-readable title."
                },
                "domain": {
                    "type": "string",
                    "description": "Parent domain ID for capabilities (e.g. 'DOM-PRODUCT-CORE')."
                },
                "path": {
                    "type": "string",
                    "description": "Path to specifications directory (defaults to 'eidos')."
                },
                "kind": {
                    "type": "string",
                    "description": "Mutation kind for SpecDeltas."
                },
                "motivation": {
                    "type": "string",
                    "description": "Motivation / problem statement."
                },
                "category": {
                    "type": "string",
                    "description": "Category for policies."
                },
                "rule": {
                    "type": "string",
                    "description": "Rule statement for policies."
                }
            }
        }
    },
    {
        "name": "eidos_delete_spec",
        "description": "Safely delete a living specification or SpecDelta with reference safety checks.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Document ID, path, or filename to delete."
                },
                "path": {
                    "type": "string",
                    "description": "Path to specifications directory (defaults to 'eidos')."
                },
                "force": {
                    "type": "boolean",
                    "description": "Force deletion even if references exist (defaults to false)."
                }
            }
        }
    },
    {
        "name": "eidos_apply_delta",
        "description": "Reconcile a SpecDelta in-place with status 'applied' and synchronize gitignored symlink views.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Delta ID, path, or filename of the SpecDelta to apply."
                },
                "path": {
                    "type": "string",
                    "description": "Path to specifications directory (defaults to 'eidos')."
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
        path = Path(arguments.get("path", "eidos"))

        file_path = create_spec(
            eidos_dir=path,
            doc_type_key="delta",
            raw_id=delta_id,
            title=title,
            kind=kind,
            motivation=motivation,
        )
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

    elif name == "eidos_list_specs":
        path = Path(arguments.get("path") or "eidos")
        specs = list_specs(path, type_filter=arguments.get("type"), status_filter=arguments.get("status"))
        serialized = [
            {
                "id": s["id"],
                "type": s["type"],
                "title": s["title"],
                "status": s["status"],
                "path": str(s["path"]),
                "relative_path": str(s["relative_path"])
            }
            for s in specs
        ]
        return [{"type": "text", "text": json.dumps(serialized, indent=2)}]

    elif name == "eidos_create_spec":
        path = Path(arguments.get("path") or "eidos")
        try:
            dest_file = create_spec(
                eidos_dir=path,
                doc_type_key=arguments["type"],
                raw_id=arguments["id"],
                title=arguments["title"],
                domain=arguments.get("domain"),
                kind=arguments.get("kind", "feature_addition"),
                motivation=arguments.get("motivation", "Motivation statement."),
                category=arguments.get("category", "governance"),
                rule=arguments.get("rule", "Rule statement."),
            )
            return [{"type": "text", "text": f"Created and validated specification: {dest_file}"}]
        except Exception as e:
            return [{"type": "text", "text": f"Error creating specification: {str(e)}"}]

    elif name == "eidos_delete_spec":
        path = Path(arguments.get("path") or "eidos")
        target = arguments["target"]
        force = arguments.get("force", False)
        success, msg, refs = delete_spec(path, target, force=force)
        res = {
            "success": success,
            "message": msg,
            "references": [str(r) for r in refs]
        }
        return [{"type": "text", "text": json.dumps(res, indent=2)}]

    elif name == "eidos_apply_delta":
        path = Path(arguments.get("path") or "eidos")
        target = arguments["target"]
        success, msg, applied_file = apply_delta(path, target)
        res = {
            "success": success,
            "message": msg,
            "applied_file": str(applied_file) if applied_file else None
        }
        return [{"type": "text", "text": json.dumps(res, indent=2)}]

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
