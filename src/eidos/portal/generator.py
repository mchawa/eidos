"""Unified data generator synthesizing living specifications into the Web Portal data model."""

import html
import json
import re
from pathlib import Path
from typing import Any

from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import load_project


def get_portal_data(eidos_dir: Path) -> dict[str, Any]:
    """Inspect and extract all living specifications into a structured portal payload."""
    eidos_path = eidos_dir.resolve()
    project = load_project(eidos_path.parent)
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

    system_title = (
        (sys_doc["frontmatter"].get("title") or sys_doc["frontmatter"].get("name") if sys_doc else None)
        or (arch_doc["frontmatter"].get("title") if arch_doc else None)
        or project.system_name
    )
    system_id = (sys_doc["id"] if sys_doc else None) or project.system_id

    # Components & Containers (C4 Level 2)
    raw_components = specs_by_type.get("tech/container", []) + specs_by_type.get("tech/component", [])
    components: list[dict[str, Any]] = []
    for c in raw_components:
        fm = c["frontmatter"]
        raw_pkg = fm.get("package_path", "N/A")
        pkg_path_str = ", ".join(str(p) for p in raw_pkg) if isinstance(raw_pkg, list) else str(raw_pkg)
        comp_dict = {
            "id": c["id"],
            "name": fm.get("name", c["id"]),
            "container_type": fm.get("container_type", "library"),
            "runtime": fm.get("runtime", "N/A"),
            "package_path": pkg_path_str,
            "domain_binding": fm.get("domain_binding", "N/A"),
            "internal_components": fm.get("components", []) or fm.get("c4_internal_components", []),
            "dependencies": fm.get("dependencies", {}),
            "contracts": fm.get("contracts", {}),
            "quality_attributes": fm.get("quality_attributes", {}),
            "tier": fm.get("tier"),
            "description": c["body"],
        }
        tier_id, tier_name = _classify_container_tier(comp_dict)
        comp_dict["tier"] = tier_id
        comp_dict["tier_name"] = tier_name
        components.append(comp_dict)

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
    domains: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/domain", []):
        d = dict(s["frontmatter"])
        d["body"] = s.get("body", "")
        d["path"] = s.get("path", "")
        if not d.get("mission"):
            m = re.search(r"== Domain Vision\s*\n+([^=\n][^\n]+)", s.get("body", ""))
            if m:
                d["mission"] = m.group(1).strip()
            elif d.get("boundaries", {}).get("responsibilities"):
                d["mission"] = d["boundaries"]["responsibilities"][0]
            else:
                d["mission"] = f"Bounded strategic context {d.get('name', d.get('id', ''))}."
        domains.append(d)

    capabilities: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/capability", []):
        c = dict(s["frontmatter"])
        c["body"] = s.get("body", "")
        c["path"] = s.get("path", "")
        c["name"] = c.get("name") or c.get("title") or c.get("id", "Capability")
        c["summary"] = c.get("summary") or c.get("business", {}).get("jtbd") or ""
        c["invariants"] = c.get("technical", {}).get("invariants", [])
        capabilities.append(c)

    # Link capability IDs to domains if not explicitly set
    for dom in domains:
        dom_id = dom.get("id")
        bound_cap_ids = [c.get("id") for c in capabilities if c.get("domain") == dom_id]
        if not dom.get("capabilities"):
            dom["capabilities"] = bound_cap_ids

    personas: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/persona", []):
        p = dict(s["frontmatter"])
        p["body"] = s.get("body", "")
        p["path"] = s.get("path", "")
        p["role"] = p.get("role") or (
            p.get("role_type", "").replace("_", " ").title() if p.get("role_type") else "Actor Persona"
        )
        p["archetype"] = p.get("archetype") or p.get("role_type", "persona")
        personas.append(p)

    policies: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/policy", []):
        pol = dict(s["frontmatter"])
        pol["body"] = s.get("body", "")
        pol["path"] = s.get("path", "")
        policies.append(pol)

    # Invariants list
    invariants: list[dict[str, Any]] = []
    for pol in policies:
        pid = pol.get("id", "")
        tb = pol.get("test_binding") or pol.get("enforcement_command") or "N/A"
        invariants.append(
            {
                "id": pid,
                "title": pol.get("title"),
                "rule": pol.get("rule", "System policy invariant rule."),
                "category": pol.get("category", "governance"),
                "source_type": "policy",
                "test_binding": tb,
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
            "path": d.get("path", ""),
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
                "path": exp.get("path", ""),
            }
        )

    # 6. Dynamic Mermaid Diagrams
    c4_context_mermaid = _generate_c4_context_mermaid(personas, system_id, system_title)
    c4_containers_mermaid = _generate_c4_containers_mermaid(
        components,
        system_id=system_id,
        system_title=system_title,
        contracts=contracts,
        personas=personas,
    )
    c4_components_mermaid: dict[str, str] = {}
    for comp in components:
        c4_components_mermaid[comp["id"]] = _generate_c4_component_mermaid(comp, components)

    overview = {
        "system_id": system_id,
        "system_title": system_title,
        "total_specs": report.total_docs,
        "valid_specs": report.valid_docs,
        "is_clean": report.is_clean,
        "domains_count": len(domains),
        "capabilities_count": len(capabilities),
        "policies_count": len(policies),
        "personas_count": len(personas),
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
            "c4_components": c4_components_mermaid,
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
                "container": fm.get("container", "N/A"),
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
                "client": fm.get("client", "N/A"),
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
        f'  SYS["<div style=\'padding:10px 14px;font-size:15px;\'><b>{system_id}</b><br/>{clean_title}<br/><i>[Software System]</i></div>"]:::system',
        '  EXT_GIT["<div style=\'padding:8px 12px;\'><b>Git Repository &amp; CI/CD</b><br/>Version Control &amp; Verification Pipeline<br/><i>[External Platform]</i></div>"]:::external',
        '  EXT_IDE["<div style=\'padding:8px 12px;\'><b>AI IDEs &amp; Coding Agents</b><br/>Antigravity, Cursor, Claude Code<br/><i>[Development Environments]</i></div>"]:::external',
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

    lines.append('  SYS ==>|Tracks Specs, Commits &amp; Drift| EXT_GIT')
    lines.append('  SYS ==>|CLI Commands &amp; Agent Skills| EXT_IDE')

    return "\n".join(lines)


GENERIC_TIERS: dict[str, dict[str, Any]] = {
    "tier_presentation": {
        "name": "Presentation & Interface Layer",
        "icon": "🖥️",
        "style_class": "uiContainer",
        "types": {"cli_app", "web_app", "frontend_spa", "mobile_app", "desktop_app", "gateway", "api_gateway", "ui"},
        "keywords": ("CLI", "PORTAL", "DOCS", "WEB", "UI", "FRONTEND", "APP", "GATEWAY"),
        "order": 1,
    },
    "tier_service": {
        "name": "Service & Processing Layer",
        "icon": "⚡",
        "style_class": "serviceContainer",
        "types": {"service", "microservice", "worker", "background_worker", "agent", "daemon", "job", "ml_pipeline", "pipeline"},
        "keywords": ("SERVICE", "WORKER", "DAEMON", "JOB", "PIPELINE"),
        "order": 2,
    },
    "tier_extensibility": {
        "name": "Extensibility & Agent Integration Layer",
        "icon": "🔌",
        "style_class": "extContainer",
        "types": {"plugin", "extension", "integration", "adapter", "hook"},
        "keywords": ("PLUGIN", "EXTENSION", "INTEGRATION", "ADAPTER", "HOOK"),
        "order": 3,
    },
    "tier_core": {
        "name": "Core Domain & Specification Engine",
        "icon": "⚙️",
        "style_class": "coreContainer",
        "types": {"library", "engine", "package", "sdk", "driver", "core"},
        "keywords": ("CORE", "ENGINE", "DOMAIN", "SDK", "LIB"),
        "order": 4,
    },
    "tier_persistence": {
        "name": "Knowledge Graph & Persistence Layer",
        "icon": "🗄️",
        "style_class": "dataContainer",
        "types": {"database", "storage", "cache", "data_store", "search_engine", "event_store"},
        "keywords": ("INDEX", "DB", "DATABASE", "STORAGE", "CACHE", "GRAPH", "SQLITE", "POSTGRES", "REDIS"),
        "order": 5,
    },
}


def _classify_container_tier(comp: dict[str, Any]) -> tuple[str, str]:
    """Classify a container into an architectural tier (tier_id, tier_name) generically."""
    raw_tier = comp.get("tier")
    if raw_tier:
        raw_slug = str(raw_tier).strip().lower()
        tier_key = f"tier_{raw_slug}" if not raw_slug.startswith("tier_") else raw_slug
        if tier_key in GENERIC_TIERS:
            return (tier_key, GENERIC_TIERS[tier_key]["name"])
        clean_slug = tier_key.replace("tier_", "")
        custom_name = clean_slug.replace("_", " ").replace("-", " ").title() + " Layer"
        return (tier_key, custom_name)

    ctype = str(comp.get("container_type", "")).strip().lower()
    for t_id, meta in sorted(GENERIC_TIERS.items(), key=lambda x: x[1]["order"]):
        if ctype in meta["types"]:
            return (t_id, meta["name"])

    cid = str(comp.get("id", "")).upper()
    cname = str(comp.get("name", "")).upper()
    for t_id, meta in sorted(GENERIC_TIERS.items(), key=lambda x: x[1]["order"]):
        if any(k in cid or k in cname for k in meta["keywords"]):
            return (t_id, meta["name"])

    return ("tier_core", GENERIC_TIERS["tier_core"]["name"])


def _generate_c4_containers_mermaid(
    components: list[dict[str, Any]],
    system_id: str = "SYS-001",
    system_title: str = "System Architecture",
    contracts: list[dict[str, Any]] | None = None,
    personas: list[dict[str, Any]] | None = None,
) -> str:
    """Generate Mermaid syntax for C4 Level 2 Containers with tiered hierarchy and explicit connections.

    Completely generic: dynamically derives active tiers, inter-container dependencies,
    contract bindings, upstream personas, and external services from living specifications.
    """
    clean_sys_title = system_title.replace('"', "'")
    lines = [
        "graph TD",
        "  classDef uiContainer fill:#0284c7,stroke:#0369a1,color:#fff,stroke-width:2px,rx:8px;",
        "  classDef serviceContainer fill:#d97706,stroke:#b45309,color:#fff,stroke-width:2px,rx:8px;",
        "  classDef extContainer fill:#7c3aed,stroke:#6d28d9,color:#fff,stroke-width:2px,rx:8px;",
        "  classDef coreContainer fill:#4338ca,stroke:#3730a3,color:#fff,stroke-width:3px,rx:8px;",
        "  classDef dataContainer fill:#0f766e,stroke:#115e59,color:#fff,stroke-width:2px,rx:6px;",
        "  classDef customContainer fill:#475569,stroke:#334155,color:#fff,stroke-width:2px,rx:6px;",
        "  classDef actor fill:#0d9488,stroke:#0f766e,color:#fff,stroke-width:2px,rx:6px;",
        "  classDef external fill:#334155,stroke:#475569,color:#cbd5e1,stroke-width:1.5px,rx:6px,stroke-dasharray: 4 4;",
        "",
    ]

    # 1. Identify Entrypoint / Presentation Containers
    entrypoints = [
        c
        for c in components
        if c.get("tier") == "tier_presentation"
        or c.get("container_type")
        in (
            "cli_app",
            "web_app",
            "frontend_spa",
            "mobile_app",
            "desktop_app",
            "gateway",
            "api_gateway",
        )
    ]
    if not entrypoints and components:
        entrypoints = [components[0]]

    # 2. Upstream Personas & Actor Nodes (Generically harvested)
    actor_lines: list[str] = []
    actor_edge_lines: list[str] = []
    actor_nodes_rendered: list[str] = []

    personas_list = personas or []
    if personas_list:
        selected_personas = personas_list[:3]
        for p in selected_personas:
            pid = p.get("id", "PER")
            node_id = pid.replace("-", "_")
            pname = p.get("name", pid).replace('"', "'")
            prole = p.get("role", "Actor").replace('"', "'")
            ptype = str(p.get("archetype", p.get("role_type", "Human Lead"))).replace("_", " ").title().replace('"', "'")

            actor_lines.append(
                f'  {node_id}["<div style=\'padding:6px 10px;\'><b>{pname}</b><br/>{prole}<br/><i>[{ptype}]</i></div>"]:::actor'
            )
            actor_nodes_rendered.append(node_id)

            p_text = f"{pid} {pname} {prole} {p.get('role_type', '')}".lower()
            connected = False

            for ep in entrypoints:
                ep_node = ep["id"].replace("-", "_")
                ep_type = ep.get("container_type", "")

                is_match = False
                if ep_type == "cli_app" and any(k in p_text for k in ("developer", "engineer", "agent", "dev", "tech")):
                    is_match = True
                    label = "Runs CLI Commands"
                elif ep_type in ("web_app", "frontend_spa") and any(
                    k in p_text for k in ("product", "design", "architect", "lead", "user", "manager")
                ):
                    is_match = True
                    label = "Browses Web Application"
                elif ep_type == "cli_app":
                    label = "Executes Commands"
                elif ep_type in ("web_app", "frontend_spa"):
                    label = "Accesses Web Interface"
                else:
                    label = "Interacts With"

                if is_match:
                    clean_l = label.replace('"', "'")
                    actor_edge_lines.append(f'  {node_id} ==>|"{clean_l}"| {ep_node}')
                    connected = True

            if not connected and entrypoints:
                first_ep = entrypoints[0]
                ep_node = first_ep["id"].replace("-", "_")
                ep_type = first_ep.get("container_type", "")
                label = "Runs Commands" if ep_type == "cli_app" else "Interacts With"
                clean_l = label.replace('"', "'")
                actor_edge_lines.append(f'  {node_id} ==>|"{clean_l}"| {ep_node}')
    else:
        actor_lines.append(
            '  ACTOR_USERS["<div style=\'padding:6px 10px;\'><b>System Users &amp; Clients</b><br/>Primary Actors<br/><i>[Users &amp; Clients]</i></div>"]:::actor'
        )
        actor_nodes_rendered.append("ACTOR_USERS")
        for ep in entrypoints:
            ep_node = ep["id"].replace("-", "_")
            ep_type = ep.get("container_type", "")
            label = "Runs Commands" if ep_type == "cli_app" else "Accesses Interface"
            clean_l = label.replace('"', "'")
            actor_edge_lines.append(f'  ACTOR_USERS ==>|"{clean_l}"| {ep_node}')

    # Ensure every entrypoint has at least one incoming actor connection
    connected_ep_nodes = {line.split()[-1] for line in actor_edge_lines}
    for ep in entrypoints:
        ep_node = ep["id"].replace("-", "_")
        if ep_node not in connected_ep_nodes and actor_nodes_rendered:
            first_actor = actor_nodes_rendered[0]
            ep_type = ep.get("container_type", "")
            label = "Browses Application" if ep_type in ("web_app", "frontend_spa") else "Interacts With"
            clean_l = label.replace('"', "'")
            actor_edge_lines.append(f'  {first_actor} ==>|"{clean_l}"| {ep_node}')

    if actor_lines:
        lines.extend(actor_lines)
        lines.append("")

    # 3. System Boundary & Tiered Subgraphs (Generically grouped and ordered)
    lines.append(f'  subgraph SYS_BOUNDARY ["📦 System Boundary: {clean_sys_title} ({system_id})"]')

    containers_by_tier: dict[str, list[dict[str, Any]]] = {}
    for c in components:
        t_id = c.get("tier") or _classify_container_tier(c)[0]
        containers_by_tier.setdefault(t_id, []).append(c)

    active_tier_ids = sorted(
        [t for t in containers_by_tier if containers_by_tier[t]],
        key=lambda t: GENERIC_TIERS.get(t, {}).get("order", 99),
    )

    for idx, t_id in enumerate(active_tier_ids, start=1):
        tier_containers = containers_by_tier[t_id]
        if t_id in GENERIC_TIERS:
            meta = GENERIC_TIERS[t_id]
            tier_title = f"{meta['icon']} Tier {idx}: {html.escape(meta['name'])}"
            style_class = meta["style_class"]
        else:
            clean_slug = t_id.replace("tier_", "").replace("_", " ").title()
            tier_title = f"📦 Tier {idx}: {clean_slug} Layer"
            style_class = "customContainer"

        subgraph_key = f"SUB_{t_id.upper()}"
        lines.append(f'    subgraph {subgraph_key} ["{tier_title}"]')
        for c in tier_containers:
            cid = c["id"]
            node_id = cid.replace("-", "_")
            cname = c["name"].replace('"', "'")
            ctype = c.get("container_type", "container")
            runtime = str(c.get("runtime", "N/A")).replace('"', "'")
            lines.append(
                f'      {node_id}["<div style=\'padding:8px 12px;\'><b>{cid}</b><br/>{cname}<br/><i>[{ctype} • {runtime}]</i></div>"]:::{style_class}'
            )
        lines.append("    end")

    lines.append("  end")
    lines.append("")

    # 4. Harvest External Backing Services Dynamically
    ext_service_nodes: dict[str, dict[str, Any]] = {}
    ext_edge_lines: list[str] = []

    for c in components:
        c_node = c["id"].replace("-", "_")
        deps = c.get("dependencies", {})
        ext_list = deps.get("external_services", []) if isinstance(deps, dict) else []

        for ext in ext_list:
            if isinstance(ext, dict):
                raw_name = str(ext.get("name") or ext.get("service") or "").strip()
                custom_rel = ext.get("relationship") or ext.get("description")
            else:
                raw_name = str(ext).strip()
                custom_rel = None

            if not raw_name:
                continue

            m = re.match(r"^([^(]+)(?:\(([^)]+)\))?", raw_name)
            if m:
                main_title = m.group(1).strip()
                sub_title = (m.group(2) or "External System").strip()
            else:
                main_title = raw_name
                sub_title = "External System"

            slug = re.sub(r"[^A-Za-z0-9]+", "_", main_title).strip("_").upper()
            if not slug:
                slug = "EXTERNAL"
            ext_node_id = f"EXT_{slug}"

            if ext_node_id not in ext_service_nodes:
                ext_service_nodes[ext_node_id] = {
                    "id": ext_node_id,
                    "title": html.escape(main_title),
                    "sub": html.escape(sub_title[:80] + ("..." if len(sub_title) > 80 else "")),
                }

            if custom_rel:
                edge_label = html.escape(custom_rel)
            else:
                name_lower = raw_name.lower()
                if any(k in name_lower for k in ("git", "vcs", "repo", "filesystem")):
                    edge_label = "Tracks &amp; Syncs Files"
                elif any(k in name_lower for k in ("sqlite", "postgres", "mysql", "redis", "database", "db")):
                    edge_label = "Persists Relational Data"
                elif any(k in name_lower for k in ("browser", "chrome", "firefox", "safari")):
                    edge_label = "Serves Web Application"
                elif any(k in name_lower for k in ("ide", "cursor", "claude", "agent", "editor", "vscode")):
                    edge_label = "Syncs Tooling &amp; Rules"
                elif any(k in name_lower for k in ("pages", "host", "s3", "cloud", "deploy")):
                    edge_label = "Deploys Static Site"
                elif any(k in name_lower for k in ("terminal", "shell", "stdout")):
                    edge_label = "Streams Command Output"
                else:
                    edge_label = "Integrates With"

            clean_edge_l = edge_label.replace('"', "'")
            ext_edge_lines.append(f'  {c_node} -.->|"{clean_edge_l}"| {ext_node_id}')

    if ext_service_nodes:
        for ext in ext_service_nodes.values():
            lines.append(
                f'  {ext["id"]}["<div style=\'padding:6px 10px;\'><b>{ext["title"]}</b><br/><i>[{ext["sub"]}]</i></div>"]:::external'
            )
        lines.append("")

    # 5. Upstream Actor -> Entrypoint Connections
    if actor_edge_lines:
        lines.extend(actor_edge_lines)
        lines.append("")

    # 6. Inter-Container Dependency Connections (Dynamically resolved)
    comps_by_id = {c["id"]: c for c in components}
    contracts_by_id = {ct["id"]: ct for ct in (contracts or [])}
    drawn_connections: set[tuple[str, str]] = set()
    container_edge_lines: list[str] = []

    for c in components:
        src_id = c["id"]
        src_node = src_id.replace("-", "_")
        deps = c.get("dependencies", {})
        int_deps = deps.get("internal_containers", []) if isinstance(deps, dict) else []
        src_contracts = c.get("contracts", {})
        consumes = src_contracts.get("consumes", []) if isinstance(src_contracts, dict) else []

        for dep_item in int_deps:
            if isinstance(dep_item, dict):
                tgt_id = dep_item.get("container") or dep_item.get("id") or dep_item.get("target")
                custom_rel = dep_item.get("relationship") or dep_item.get("description") or dep_item.get("label")
            else:
                tgt_id = str(dep_item).strip()
                custom_rel = None

            if not tgt_id or tgt_id not in comps_by_id or tgt_id == src_id:
                continue

            edge_key = (src_id, tgt_id)
            if edge_key in drawn_connections:
                continue
            drawn_connections.add(edge_key)

            tgt_c = comps_by_id[tgt_id]
            tgt_node = tgt_id.replace("-", "_")
            tgt_contracts = tgt_c.get("contracts", {})
            tgt_provides = tgt_contracts.get("provides", []) if isinstance(tgt_contracts, dict) else []

            if custom_rel:
                label = html.escape(custom_rel)
            else:
                # 1. Matching consumed contract
                matching_contracts = [ctr for ctr in consumes if ctr in tgt_provides]
                if matching_contracts:
                    ctr_id = matching_contracts[0]
                    ctr_meta = contracts_by_id.get(ctr_id, {})
                    proto = ctr_meta.get("protocol")
                    if proto:
                        proto_str = proto.replace("_", " ").upper()
                        label = f"{proto_str} • Consumes [{', '.join(matching_contracts)}]"
                    else:
                        label = f"Consumes [{', '.join(matching_contracts)}]"
                # 2. Target provides a contract
                elif tgt_provides:
                    ctr_id = tgt_provides[0]
                    ctr_meta = contracts_by_id.get(ctr_id, {})
                    proto = ctr_meta.get("protocol")
                    if proto:
                        proto_str = proto.replace("_", " ").upper()
                        label = f"{proto_str} • [{', '.join(tgt_provides)}]"
                    else:
                        label = f"Consumes [{', '.join(tgt_provides)}]"
                # 3. Database / Storage target
                elif tgt_c.get("container_type") in ("database", "storage") or any(
                    k in tgt_id for k in ("INDEX", "DB", "STORAGE", "SQLITE", "SQL")
                ):
                    label = "Queries &amp; Stores Data"
                # 4. Service / Worker target
                elif tgt_c.get("container_type") in ("worker", "background_worker", "agent", "job", "ml_pipeline"):
                    label = "Dispatches Tasks &amp; Events"
                elif tgt_c.get("container_type") in ("service", "microservice", "daemon"):
                    label = "Service API Calls"
                # 5. Library / Engine target
                elif tgt_c.get("container_type") in ("library", "engine", "package", "sdk", "driver", "core"):
                    runtime_lower = str(tgt_c.get("runtime", "")).lower()
                    if "python" in runtime_lower or "py" in runtime_lower:
                        label = "In-Process Python API"
                    elif "rust" in runtime_lower:
                        label = "In-Process Rust API"
                    elif "go" in runtime_lower:
                        label = "In-Process Go API"
                    elif any(k in runtime_lower for k in ("typescript", "ts", "node", "javascript")):
                        label = "In-Process TypeScript API"
                    else:
                        label = "In-Process Library API"
                else:
                    label = "Depends On"

            clean_l = label.replace('"', "'")
            container_edge_lines.append(f'  {src_node} -->|"{clean_l}"| {tgt_node}')

    # Fallback if no container dependencies were declared in frontmatter
    if not drawn_connections:
        core_c = next((c for c in components if c.get("tier") == "tier_core" or "CORE" in c["id"]), None)
        if core_c:
            core_node = core_c["id"].replace("-", "_")
            for c in components:
                if c["id"] != core_c["id"]:
                    node_id = c["id"].replace("-", "_")
                    container_edge_lines.append(f'  {node_id} -->|"Depends On"| {core_node}')

    if container_edge_lines:
        lines.extend(container_edge_lines)

    # 7. Downstream Container -> External Service Integrations
    if ext_edge_lines:
        lines.append("")
        lines.extend(ext_edge_lines)

    return "\n".join(lines)


def _generate_c4_component_mermaid(component: dict[str, Any], all_components: list[dict[str, Any]]) -> str:
    """Generate Mermaid syntax for C4 Level 3 Component Whitebox View of a single container."""
    cid = component["id"]
    clean_name = component.get("name", cid).replace('"', "'")
    component.get("container_type", "library")
    component.get("runtime", "N/A").replace('"', "'")
    internals = component.get("internal_components", [])
    box_id = f"subgraph_{cid.replace('-', '_')}"

    lines = [
        "graph TD",
        "  classDef module fill:#1e1b4b,stroke:#6366f1,color:#fff,stroke-width:2px,rx:8px;",
        "  classDef peer fill:#1e293b,stroke:#475569,color:#94a3b8,stroke-width:1.5px,rx:6px,stroke-dasharray: 4 4;",
        "  classDef contract fill:#064e3b,stroke:#10b981,color:#fff,stroke-width:2px,rx:6px;",
        "",
        f'  subgraph {box_id} ["📦 Level 2 Container: {cid} ({clean_name})"]',
    ]

    if not internals:
        fallback_id = f"mod_{cid.replace('-', '_')}_main"
        lines.append(f'    {fallback_id}["<div style=\'padding:10px 14px;min-width:300px;max-width:420px;white-space:normal;word-break:break-word;\'><b>{html.escape(clean_name)}</b><br/><i>Core Container Logic</i></div>"]:::module')
    else:
        for idx, mod in enumerate(internals):
            mid = mod.get("id")
            mname = mod.get("name", f"Module {idx+1}").replace('"', "'")
            title_text = f"<b>{html.escape(mid)}: {html.escape(mname)}</b>" if mid else f"<b>{html.escape(mname)}</b>"
            resp = mod.get("responsibility", "").replace('"', "'")
            raw_spath = mod.get("source_path", "")
            spath = (
                ", ".join(str(p) for p in raw_spath)
                if isinstance(raw_spath, list)
                else str(raw_spath)
            ).replace('"', "'")
            mod_id = f"mod_{cid.replace('-', '_')}_{idx}"
            lines.append(
                f'    {mod_id}["<div style=\'padding:10px 14px;text-align:left;min-width:300px;max-width:420px;white-space:normal;word-break:break-word;line-height:1.45;\'>'
                f'{title_text}<br/><small style=\'color:#93c5fd;font-family:monospace;font-size:10px;\'>{html.escape(spath)}</small><br/>'
                f'<span style=\'font-size:11px;color:#cbd5e1;\'>{html.escape(resp)}</span></div>"]:::module'
            )

        # Sequential flow representation among modules
        for idx in range(len(internals) - 1):
            curr_id = f"mod_{cid.replace('-', '_')}_{idx}"
            next_id = f"mod_{cid.replace('-', '_')}_{idx+1}"
            lines.append(f"    {curr_id} -.-> {next_id}")

    lines.append("  end")

    # Inbound / Outbound peer container dependencies
    deps = component.get("dependencies", {})
    int_deps = (deps.get("internal_containers", []) or deps.get("internal_components", [])) if isinstance(deps, dict) else []
    for dep_id in int_deps:
        if dep_id != cid:
            peer_node = f"peer_{dep_id.replace('-', '_')}"
            lines.append(f'  {peer_node}["<div style=\'padding:8px 12px;\'><b>{dep_id}</b><br/><i>[Peer Container]</i></div>"]:::peer')
            first_mod = f"mod_{cid.replace('-', '_')}_0" if internals else f"mod_{cid.replace('-', '_')}_main"
            lines.append(f'  {first_mod} ==>|"Consumes / Depends On"| {peer_node}')

    # Provided contracts
    contracts = component.get("contracts", {})
    provides = contracts.get("provides", []) if isinstance(contracts, dict) else []
    for ctr_id in provides:
        ctr_node = f"ctr_{ctr_id.replace('-', '_')}"
        lines.append(f'  {ctr_node}["<div style=\'padding:8px 12px;\'><b>{ctr_id}</b><br/><i>[Interface Contract]</i></div>"]:::contract')
        last_mod = f"mod_{cid.replace('-', '_')}_{len(internals)-1}" if internals else f"mod_{cid.replace('-', '_')}_main"
        lines.append(f'  {last_mod} -->|"Provides Interface"| {ctr_node}')

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
