"""Unified data generator synthesizing living specifications into the Web Portal data model."""

import json
import re
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
        rel_path = (
            str(doc.file_path.relative_to(eidos_path.parent))
            if doc.file_path.is_relative_to(eidos_path.parent)
            else str(doc.file_path)
        )
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
        components.append(
            {
                "id": c["id"],
                "name": fm.get("name", c["id"]),
                "container_type": fm.get("container_type", "library"),
                "runtime": fm.get("runtime", "N/A"),
                "package_path": fm.get("package_path", "N/A"),
                "internal_components": fm.get("c4_internal_components", []),
                "dependencies": fm.get("dependencies", {}),
                "contracts": fm.get("contracts", {}),
                "description": c["body"],
            }
        )

    # Contracts
    raw_contracts = specs_by_type.get("tech/contract", [])
    contracts: list[dict[str, Any]] = []
    for ct in raw_contracts:
        fm = ct["frontmatter"]
        contracts.append(
            {
                "id": ct["id"],
                "title": ct["title"],
                "protocol": fm.get("protocol", "cli_args"),
                "owning_component": fm.get("owning_component", "N/A"),
                "specification_file": fm.get("specification_file", "N/A"),
                "description": ct["body"],
            }
        )

    # 2. Product Domains, Capabilities, Personas, Policies
    domains = [s["frontmatter"] for s in specs_by_type.get("product/domain", [])]
    capabilities = [s["frontmatter"] for s in specs_by_type.get("product/capability", [])]
    personas = [s["frontmatter"] for s in specs_by_type.get("product/persona", [])]
    policies = [s["frontmatter"] for s in specs_by_type.get("product/policy", [])]

    # Invariants list
    invariants: list[dict[str, Any]] = []
    for pol in policies:
        invariants.append(
            {
                "id": pol.get("id"),
                "title": pol.get("title"),
                "rule": pol.get("rule", "System policy invariant rule."),
                "category": pol.get("category", "governance"),
                "source_type": "policy",
                "test_binding": "tests/test_validator.py",
            }
        )

    for cap in capabilities:
        cap_id = cap.get("id", "CAP")
        for inv in cap.get("technical", {}).get("invariants", []):
            invariants.append(
                {
                    "id": inv.get("id"),
                    "title": inv.get("id"),
                    "rule": inv.get("rule"),
                    "category": "capability",
                    "source_type": f"capability ({cap_id})",
                    "test_binding": inv.get("test_binding", "N/A"),
                }
            )

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

    # 4. Multi-Design Systems, Tokens, UI Components, Clients, Flows, and Screens
    design_data = _extract_design_systems(eidos_path, specs_by_type)
    design_systems = design_data.get("systems", [])
    screens = design_data.get("screens", [])
    ui_components = design_data.get("ui_components", [])
    clients = design_data.get("clients", [])
    flows = design_data.get("flows", [])

    # 5. Product Explorations & Spikes
    raw_explorations = specs_by_type.get("product/exploration", [])
    explorations: list[dict[str, Any]] = []
    for exp in raw_explorations:
        fm = exp["frontmatter"]
        explorations.append(
            {
                "id": exp["id"],
                "title": exp["title"],
                "status": fm.get("status", "proposed"),
                "hypothesis": fm.get("hypothesis", ""),
                "questions_to_answer": fm.get("questions_to_answer", []),
                "spikes_to_run": fm.get("spikes_to_run", []),
                "exit_criteria": fm.get("exit_criteria", {}),
                "decision": fm.get("decision", {}),
                "body": exp["body"],
            }
        )

    # 6. Dynamic Mermaid Diagrams
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
        "ui_components_count": len(ui_components),
        "screens_count": len(screens),
        "clients_count": len(clients),
        "flows_count": len(flows),
        "explorations_count": len(explorations),
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
        "ui_components": ui_components,
        "screens": screens,
        "clients": clients,
        "flows": flows,
        "explorations": explorations,
        "diagrams": {
            "c4_context": c4_context_mermaid,
            "c4_containers": c4_containers_mermaid,
        },
    }


def _extract_design_systems(eidos_path: Path, specs_by_type: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Scan and parse multi-design systems, W3C tokens.json, UI components, clients, flows, and screens."""
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

        systems.append(
            {
                "id": sys_id,
                "title": ds["title"],
                "client": fm.get("client_id", fm.get("client", "core")),
                "brand_name": fm.get("brand_name", ds["title"]),
                "extends": fm.get("inheritance", {}).get("extends") if isinstance(fm.get("inheritance"), dict) else fm.get("extends"),
                "tokens": tokens_data,
            }
        )

    # Clients
    clients: list[dict[str, Any]] = []
    for clt in specs_by_type.get("design/client", []):
        fm = clt["frontmatter"]
        clients.append(
            {
                "id": clt["id"],
                "title": clt["title"],
                "platform": fm.get("platform", "web"),
                "design_system": fm.get("design_system", "DS-CORE"),
                "container": fm.get("container", "CMP-EIDOS-PORTAL"),
                "capabilities": fm.get("capabilities", []),
                "screens": fm.get("screens", []),
                "flows": fm.get("flows", []),
                "body": clt["body"],
            }
        )

    # Flows
    flows: list[dict[str, Any]] = []
    for fl in specs_by_type.get("design/flow", []):
        fm = fl["frontmatter"]
        flow_type = fm.get("flow_type", "client_internal")
        steps = fm.get("steps", [])
        body = fl.get("body", "")
        mermaid = _extract_or_generate_flow_mermaid(fl["id"], flow_type, steps, body)
        flows.append(
            {
                "id": fl["id"],
                "title": fl["title"],
                "status": fm.get("status", "active"),
                "flow_type": flow_type,
                "client": fm.get("client"),
                "clients": fm.get("clients", ([fm["client"]] if fm.get("client") else [])),
                "capabilities": fm.get("capabilities", []),
                "steps": steps,
                "mermaid": mermaid,
                "body": body,
            }
        )

    # UI Components
    ui_components: list[dict[str, Any]] = []
    for uic in specs_by_type.get("design/ui-component", []):
        fm = uic["frontmatter"]
        ui_components.append(
            {
                "id": uic["id"],
                "title": uic["title"],
                "design_system": fm.get("design_system", "DS-CORE"),
                "category": fm.get("category", "layouts"),
                "status": fm.get("status", "active"),
                "variants": fm.get("variants", []),
                "states": fm.get("states", []),
                "accessibility": fm.get("accessibility", {}),
                "test_bindings": fm.get("test_bindings", {}),
                "body": uic["body"],
            }
        )

    # Screens and state machines
    screens: list[dict[str, Any]] = []
    for scr in specs_by_type.get("design/screen", []):
        fm = scr["frontmatter"]
        states = fm.get("states", [])
        transitions = fm.get("transitions", [])
        mermaid_state = _generate_screen_state_mermaid(scr["id"], states, transitions)
        screens.append(
            {
                "id": scr["id"],
                "title": scr["title"],
                "route": fm.get("route", "/"),
                "client": fm.get("client", "CLT-WEB-PORTAL"),
                "system_ref": fm.get("supported_design_systems", ["DS-CORE"])[0] if fm.get("supported_design_systems") else "DS-CORE",
                "states": states,
                "slots": fm.get("slots", {}),
                "capabilities": fm.get("capabilities", []),
                "mermaid": mermaid_state,
            }
        )

    return {
        "systems": systems,
        "ui_components": ui_components,
        "screens": screens,
        "clients": clients,
        "flows": flows,
    }


def _generate_c4_context_mermaid(personas: list[dict[str, Any]], system_id: str, system_title: str) -> str:
    """Generate Mermaid syntax for C4 Level 1 System Context."""
    clean_title = system_title.replace('"', "'")
    lines = [
        "graph TD",
        "  classDef actor fill:#0284c7,stroke:#0369a1,color:#fff,stroke-width:2px,rx:6px;",
        "  classDef system fill:#059669,stroke:#047857,color:#fff,stroke-width:3px,rx:8px;",
        "  classDef external fill:#334155,stroke:#475569,color:#fff,stroke-width:2px,rx:6px;",
        "",
        f'  SYS["<div style=\'padding:10px 14px;font-size:15px;\'><b>{system_id}</b><br/>{clean_title}<br/><i>[Living Specification &amp; Governance Ecosystem]</i></div>"]:::system',
        '  EXT_GIT["<div style=\'padding:8px 12px;\'><b>GitHub / GitLab</b><br/>VCS Platform &amp; CI/CD Pipeline<br/><i>[External Platform]</i></div>"]:::external',
        '  EXT_IDE["<div style=\'padding:8px 12px;\'><b>AI IDEs &amp; MCP Clients</b><br/>Antigravity, Cursor, Claude Code<br/><i>[Development Environments]</i></div>"]:::external',
    ]

    for p in personas:
        pid = p.get("id", "PER")
        pname = p.get("name", pid)
        node_id = pid.replace("-", "_")
        lines.append(f'  {node_id}["<div style=\'padding:8px 12px;\'><b>{pname}</b><br/>Persona Actor<br/><i>[Human / Autonomous Lead]</i></div>"]:::actor')

    lines.append("")
    for p in personas:
        pid = p.get("id", "PER")
        node_id = pid.replace("-", "_")
        lines.append(f'  {node_id} ==>|Interacts & Collaborates| SYS')

    lines.append('  SYS ==>|Syncs Specs, Issues, PRs| EXT_GIT')
    lines.append('  SYS ==>|JSON-RPC Stdio MCP Protocol| EXT_IDE')

    return "\n".join(lines)


def _generate_c4_containers_mermaid(components: list[dict[str, Any]]) -> str:
    """Generate Mermaid syntax for C4 Level 2 Containers."""
    lines = [
        "graph TD",
        "  classDef core fill:#4338ca,stroke:#3730a3,color:#fff,stroke-width:3px,rx:8px;",
        "  classDef container fill:#0f766e,stroke:#115e59,color:#fff,stroke-width:2px,rx:6px;",
    ]

    for c in components:
        cid = c["id"]
        node_id = cid.replace("-", "_")
        cname = c["name"].replace('"', "'")
        ctype = c["container_type"]
        runtime = c["runtime"]
        style_class = "core" if "CORE" in cid else "container"
        lines.append(
            f'  {node_id}["<div style=\'padding:8px 12px;\'><b>{cid}</b><br/>{cname}<br/><i>[{ctype} • {runtime}]</i></div>"]:::{style_class}'
        )

    # Standard container interaction relationships
    if any(c["id"] == "CMP-EIDOS-CORE" for c in components):
        for c in components:
            if c["id"] != "CMP-EIDOS-CORE":
                node_id = c["id"].replace("-", "_")
                lines.append(f"  {node_id} -->|In-Process Python API| CMP_EIDOS_CORE")

    return "\n".join(lines)


def _generate_screen_state_mermaid(screen_id: str, states: list[Any], transitions: list[dict[str, Any]]) -> str:
    """Generate Mermaid stateDiagram-v2 syntax for screen state transitions."""
    clean_id = screen_id.replace("-", "_")
    lines = ["stateDiagram-v2", "  direction LR"]
    if not states:
        lines.append(f"  [*] --> {clean_id}")
        return "\n".join(lines)

    state_names = [s.get("name", str(s)) if isinstance(s, dict) else str(s) for s in states]
    lines.append(f"  [*] --> {state_names[0]}")
    for t in transitions:
        src = t.get("from_state", state_names[0])
        dst = t.get("to_state", state_names[-1])
        trigger = t.get("action", t.get("event", "action"))
        lines.append(f"  {src} --> {dst}: {trigger}")

    if not transitions and len(state_names) > 1:
        for i in range(len(state_names) - 1):
            lines.append(f"  {state_names[i]} --> {state_names[i+1]}")

    return "\n".join(lines)


def _extract_or_generate_flow_mermaid(
    flow_id: str, flow_type: str, steps: list[dict[str, Any]], body: str
) -> str:
    """Extract authored Mermaid block from spec body or generate from steps."""
    match = re.search(r"\[mermaid\]\s*\n----\s*\n(.*?)\n----", body, re.DOTALL)
    if match:
        return match.group(1).strip()

    if not steps:
        return "flowchart LR\n  Start --> End"

    if flow_type == "cross_client":
        lines = ["sequenceDiagram", "  autonumber"]
        participants: list[str] = []
        for s in steps:
            clt = s.get("client", "Client")
            if clt not in participants:
                participants.append(clt)
        for p in participants:
            p_clean = p.replace("-", "_")
            lines.append(f"  participant {p_clean} as {p}")
        for s in steps:
            clt = s.get("client", participants[0]).replace("-", "_")
            title = s.get("title", f"Step {s.get('step', '')}").replace('"', "'")
            action = s.get("action", "").replace('"', "'")
            lines.append(f"  Note over {clt}: {title}")
            lines.append(f"  {clt}->>{clt}: {action}")
        return "\n".join(lines)
    else:
        lines = ["flowchart LR"]
        for i, s in enumerate(steps):
            step_num = s.get("step", i + 1)
            title = s.get("title", f"Step {step_num}").replace('"', "'")
            screen = s.get("screen", "")
            scr_label = f"<br/><small>{Path(screen).stem}</small>" if screen else ""
            lines.append(f'  step{step_num}["<b>Step {step_num}: {title}</b>{scr_label}"]')
        for i in range(len(steps) - 1):
            s_curr = steps[i]
            trigger = s_curr.get("action", "next").replace('"', "'")
            if len(trigger) > 28:
                trigger = trigger[:25] + "..."
            lines.append(f'  step{steps[i].get("step", i+1)} -->|"{trigger}"| step{steps[i+1].get("step", i+2)}')
        return "\n".join(lines)
