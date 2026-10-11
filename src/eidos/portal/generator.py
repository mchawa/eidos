"""Unified data generator synthesizing living specifications into the Web Portal data model."""

import json
from pathlib import Path
from typing import Any

from eidos.core.validator import SpecValidator


def get_portal_data(eidos_dir: Path) -> dict[str, Any]:
    """Inspect and extract all living specifications into a structured portal payload."""
    eidos_path = eidos_dir.resolve()
    validator = SpecValidator()
    report = validator.validate_directory(eidos_path)

    specs: list[dict[str, Any]] = []
    specs_by_type: dict[str, list[dict[str, Any]]] = {}

    for doc in report.documents:
        rel_path = str(doc.file_path.relative_to(eidos_path.parent)) if doc.file_path.is_relative_to(eidos_path.parent) else str(doc.file_path)
        item = {
            "id": doc.doc_id,
            "title": doc.title or doc.doc_id,
            "type": doc.doc_type,
            "schema": doc.schema_ref,
            "path": rel_path,
            "filename": doc.file_path.name,
            "frontmatter": doc.frontmatter,
            "body": doc.body,
            "is_valid": doc.is_valid,
            "errors": doc.errors,
        }
        specs.append(item)
        specs_by_type.setdefault(doc.doc_type, []).append(item)

    # 1. System Overview & Architecture
    arch_doc = next((s for s in specs if s["type"] == "tech/architecture"), None)
    sys_doc = next((s for s in specs if s["type"] == "product/system"), None)

    system_title = (sys_doc["frontmatter"].get("title") if sys_doc else None) or "Eidos Living Architecture"
    system_id = (sys_doc["id"] if sys_doc else None) or "SYS-EIDOS-001"

    # Components & Containers
    raw_components = specs_by_type.get("tech/component", [])
    components: list[dict[str, Any]] = []
    for c in raw_components:
        fm = c["frontmatter"]
        components.append({
            "id": c["id"],
            "name": fm.get("name", c["id"]),
            "container_type": fm.get("container_type", "library"),
            "runtime": fm.get("runtime", "N/A"),
            "package_path": fm.get("package_path", "N/A"),
            "internal_components": fm.get("c4_internal_components", []),
            "dependencies": fm.get("dependencies", {}),
            "contracts": fm.get("contracts", {}),
            "description": c["body"],
        })

    # Contracts
    raw_contracts = specs_by_type.get("tech/contract", [])
    contracts: list[dict[str, Any]] = []
    for ct in raw_contracts:
        fm = ct["frontmatter"]
        contracts.append({
            "id": ct["id"],
            "title": ct["title"],
            "protocol": fm.get("protocol", "cli_args"),
            "owning_component": fm.get("owning_component", "N/A"),
            "specification_file": fm.get("specification_file", "N/A"),
            "description": ct["body"],
        })

    # 2. Product Domains, Capabilities, Personas, Policies
    domains = [s["frontmatter"] for s in specs_by_type.get("product/domain", [])]
    capabilities = [s["frontmatter"] for s in specs_by_type.get("product/capability", [])]
    personas = [s["frontmatter"] for s in specs_by_type.get("product/persona", [])]
    policies = [s["frontmatter"] for s in specs_by_type.get("product/policy", [])]

    # Invariants list
    invariants: list[dict[str, Any]] = []
    for pol in policies:
        invariants.append({
            "id": pol.get("id"),
            "title": pol.get("title"),
            "rule": pol.get("rule", "System policy invariant rule."),
            "category": pol.get("category", "governance"),
            "source_type": "policy",
            "test_binding": "tests/test_validator.py",
        })

    for cap in capabilities:
        cap_id = cap.get("id", "CAP")
        for inv in cap.get("technical", {}).get("invariants", []):
            invariants.append({
                "id": inv.get("id"),
                "title": inv.get("id"),
                "rule": inv.get("rule"),
                "category": "capability",
                "source_type": f"capability ({cap_id})",
                "test_binding": inv.get("test_binding", "N/A"),
            })

    # 3. SpecDeltas & Kanban status columns
    deltas_by_status: dict[str, list[dict[str, Any]]] = {
        "proposed": [],
        "in_review": [],
        "approved": [],
        "applied": [],
        "rejected": [],
    }

    raw_deltas = specs_by_type.get("delta/mutation", [])
    for d in raw_deltas:
        fm = d["frontmatter"]
        status = str(fm.get("status", "proposed")).lower()
        delta_item = {
            "id": d["id"],
            "title": d["title"],
            "status": status,
            "kind": fm.get("intent", {}).get("kind", "feature_addition"),
            "motivation": fm.get("intent", {}).get("motivation", ""),
            "mutations": fm.get("mutations", []),
            "blast_radius": fm.get("blast_radius", {}),
            "vcs": fm.get("vcs"),
            "body": d["body"],
        }
        if status in deltas_by_status:
            deltas_by_status[status].append(delta_item)
        else:
            deltas_by_status.setdefault(status, []).append(delta_item)

    # 4. Multi-Design Systems & W3C Tokens
    design_systems = _extract_design_systems(eidos_path, specs_by_type)

    # 5. Dynamic Mermaid Diagrams
    c4_context_mermaid = _generate_c4_context_mermaid(personas, system_id, system_title)
    c4_containers_mermaid = _generate_c4_containers_mermaid(components)

    overview = {
        "system_id": system_id,
        "system_title": system_title,
        "total_specs": report.total_docs,
        "valid_specs": report.valid_docs,
        "is_clean": report.is_clean,
        "domains_count": len(domains),
        "capabilities_count": len(capabilities),
        "components_count": len(components),
        "contracts_count": len(contracts),
        "deltas_count": len(raw_deltas),
        "invariants_count": len(invariants),
        "design_systems_count": len(design_systems),
    }

    return {
        "overview": overview,
        "system_architecture": arch_doc,
        "specs": specs,
        "components": components,
        "contracts": contracts,
        "domains": domains,
        "capabilities": capabilities,
        "personas": personas,
        "policies": policies,
        "invariants": invariants,
        "deltas": deltas_by_status,
        "design_systems": design_systems,
        "diagrams": {
            "c4_context": c4_context_mermaid,
            "c4_containers": c4_containers_mermaid,
        },
    }


def _extract_design_systems(eidos_path: Path, specs_by_type: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Scan and parse multi-design systems, W3C tokens.json, UI components, and screens."""
    systems: list[dict[str, Any]] = []
    design_specs = specs_by_type.get("design/system", [])

    for ds in design_specs:
        fm = ds["frontmatter"]
        sys_id = ds["id"]
        sys_dir = eidos_path / "design" / "systems" / sys_id.lower().replace("ds-", "")
        tokens_file = sys_dir / "tokens.json"
        tokens_data: dict[str, Any] = {}
        if tokens_file.exists():
            try:
                tokens_data = json.loads(tokens_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                tokens_data = {}

        systems.append({
            "id": sys_id,
            "title": ds["title"],
            "client": fm.get("client", "core"),
            "extends": fm.get("extends"),
            "tokens": tokens_data,
        })

    # Screens and state machines
    screens: list[dict[str, Any]] = []
    for scr in specs_by_type.get("design/screen", []):
        fm = scr["frontmatter"]
        states = fm.get("states", [])
        transitions = fm.get("transitions", [])
        mermaid_state = _generate_screen_state_mermaid(scr["id"], states, transitions)
        screens.append({
            "id": scr["id"],
            "title": scr["title"],
            "route": fm.get("route", "/"),
            "system_ref": fm.get("system_ref", "DS-CORE"),
            "states": states,
            "transitions": transitions,
            "mermaid": mermaid_state,
        })

    if not systems and not screens:
        return []

    return [
        {
            "systems": systems,
            "screens": screens,
        }
    ]


def _generate_c4_context_mermaid(personas: list[dict[str, Any]], system_id: str, system_title: str) -> str:
    """Generate Mermaid syntax for C4 Level 1 System Context."""
    lines = [
        "graph TD",
        "  classDef actor fill:#0284c7,stroke:#0369a1,color:#fff,stroke-width:2px,rx:5px;",
        "  classDef system fill:#059669,stroke:#047857,color:#fff,stroke-width:2px,rx:5px;",
        "  classDef external fill:#475569,stroke:#334155,color:#fff,stroke-width:2px,rx:5px;",
    ]

    for p in personas:
        pid = p.get("id", "PER")
        pname = p.get("name", pid)
        node_id = pid.replace("-", "_")
        lines.append(f'  {node_id}["{pname}<br/>[Persona]"]:::actor')
        lines.append(f'  {node_id} -->|Interacts & Collaborates| SYS["{system_id}<br/><b>{system_title}</b>"]:::system')

    lines.append('  SYS -->|Syncs Issues, PRs, Webhooks| EXT_GIT["GitHub / GitLab<br/>[VCS Platform]"]:::external')
    lines.append('  SYS -->|JSON-RPC Stdio Protocol| EXT_IDE["AI IDEs & MCP Clients<br/>(Antigravity, Cursor, Claude)"]:::external')

    return "\n".join(lines)


def _generate_c4_containers_mermaid(components: list[dict[str, Any]]) -> str:
    """Generate Mermaid syntax for C4 Level 2 Containers."""
    lines = [
        "graph TD",
        "  classDef core fill:#4338ca,stroke:#3730a3,color:#fff,stroke-width:2px,rx:5px;",
        "  classDef container fill:#0f766e,stroke:#115e59,color:#fff,stroke-width:2px,rx:5px;",
    ]

    for c in components:
        cid = c["id"]
        node_id = cid.replace("-", "_")
        cname = c["name"]
        ctype = c["container_type"]
        runtime = c["runtime"]
        style_class = "core" if "CORE" in cid else "container"
        lines.append(f'  {node_id}["<b>{cid}</b><br/>{cname}<br/><i>[{ctype} / {runtime}]</i>"]:::{style_class}')

    # Standard container interaction relationships
    if any(c["id"] == "CMP-EIDOS-CORE" for c in components):
        for c in components:
            if c["id"] != "CMP-EIDOS-CORE":
                node_id = c["id"].replace("-", "_")
                lines.append(f"  {node_id} -->|In-Process Python API| CMP_EIDOS_CORE")

    return "\n".join(lines)


def _generate_screen_state_mermaid(screen_id: str, states: list[str], transitions: list[dict[str, Any]]) -> str:
    """Generate Mermaid stateDiagram-v2 syntax for screen state transitions."""
    lines = ["stateDiagram-v2"]
    if not states:
        lines.append(f"  [*] --> {screen_id}")
        return "\n".join(lines)

    lines.append(f"  [*] --> {states[0]}")
    for t in transitions:
        src = t.get("from_state", states[0])
        dst = t.get("to_state", states[-1])
        trigger = t.get("action", t.get("event", "action"))
        lines.append(f"  {src} --> {dst}: {trigger}")

    return "\n".join(lines)
