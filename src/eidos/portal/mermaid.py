"""C4 Level 1-3 and UX Screen/Flow Mermaid diagram synthesis and directive sanitization for the Living Web Portal."""

import html
import re
from pathlib import Path
from typing import Any


def _generate_c4_context_mermaid(
    personas: list[dict[str, Any]],
    system_id: str,
    system_title: str,
    external_systems: list[Any] | None = None,
) -> str:
    """Generate Mermaid syntax for C4 Level 1 System Context from declared personas and external_systems."""
    clean_title = html.escape(str(system_title or ""), quote=True)
    lines = [
        "graph TD",
        "  classDef actor fill:#0284c7,stroke:#0369a1,color:#fff,stroke-width:2px,rx:6px;",
        "  classDef system fill:#059669,stroke:#047857,color:#fff,stroke-width:3px,rx:8px;",
        "  classDef external fill:#334155,stroke:#475569,color:#fff,stroke-width:2px,rx:6px;",
        "",
        f"  SYS[\"<div style='padding:10px 14px;font-size:15px;'><b>{html.escape(str(system_id or 'SYS-001'), quote=True)}</b><br/>{clean_title}<br/><i>[Software System]</i></div>\"]:::system",
    ]

    ext_edges: list[tuple[str, str]] = []
    for idx, ext in enumerate(external_systems or []):
        if not ext:
            continue
        ext_node_id = f"EXT_{idx + 1}"
        if isinstance(ext, dict):
            raw_name = str(ext.get("name") or ext.get("title") or ext.get("id") or f"External System {idx + 1}").strip()
            raw_role = str(ext.get("role") or ext.get("description") or "External Integration").strip()
            raw_proto = str(ext.get("protocol") or ext.get("type") or "External System").strip()
        else:
            raw_name = str(ext).strip()
            raw_role = "External Integration"
            raw_proto = "External System"
        if not raw_name:
            continue
        ext_name = html.escape(raw_name, quote=True)
        ext_role = html.escape(raw_role, quote=True)
        ext_proto = html.escape(raw_proto, quote=True)
        lines.append(
            f"  {ext_node_id}[\"<div style='padding:8px 12px;'><b>{ext_name}</b><br/>{ext_role}<br/><i>[{ext_proto}]</i></div>\"]:::external"
        )
        ext_edges.append((ext_node_id, ext_proto.replace("|", "/")))

    for p in personas:
        pid = p.get("id", "PER")
        ptitle = html.escape(str(p.get("title", pid)), quote=True)
        node_id = pid.replace("-", "_")
        lines.append(
            f"  {node_id}[\"<div style='padding:8px 12px;'><b>{ptitle}</b><br/>Persona Actor<br/><i>[Human / Autonomous Lead]</i></div>\"]:::actor"
        )

    lines.append("")
    for p in personas:
        pid = p.get("id", "PER")
        node_id = pid.replace("-", "_")
        lines.append(f"  {node_id} ==>|Interacts & Collaborates| SYS")

    for ext_node_id, ext_proto in ext_edges:
        lines.append(f'  SYS ==>|"{ext_proto}"| {ext_node_id}')

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
        "types": {
            "service",
            "microservice",
            "worker",
            "background_worker",
            "agent",
            "daemon",
            "job",
            "ml_pipeline",
            "pipeline",
        },
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
    ctitle = str(comp.get("title", "")).upper()
    for t_id, meta in sorted(GENERIC_TIERS.items(), key=lambda x: x[1]["order"]):
        if any(k in cid or k in ctitle for k in meta["keywords"]):
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
    clean_sys_title = html.escape(str(system_title or "System Architecture")).replace('"', "'")
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
        selected_personas = personas_list
        for p in selected_personas:
            pid = p.get("id", "PER")
            node_id = pid.replace("-", "_")
            ptitle = html.escape(str(p.get("title", pid))).replace('"', "'")
            prole = html.escape(str(p.get("role", "Actor"))).replace('"', "'")
            ptype = html.escape(
                str(p.get("archetype", p.get("role_type", "Human Lead"))).replace("_", " ").title()
            ).replace('"', "'")

            actor_lines.append(
                f"  {node_id}[\"<div style='padding:6px 10px;'><b>{ptitle}</b><br/>{prole}<br/><i>[{ptype}]</i></div>\"]:::actor"
            )
            actor_nodes_rendered.append(node_id)

            p_text = f"{pid} {ptitle} {prole} {p.get('role_type', '')}".lower()
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
            "  ACTOR_USERS[\"<div style='padding:6px 10px;'><b>System Users &amp; Clients</b><br/>Primary Actors<br/><i>[Users &amp; Clients]</i></div>\"]:::actor"
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
    lines.append(
        f'  subgraph SYS_BOUNDARY ["📦 System Boundary: {clean_sys_title} ({html.escape(str(system_id or "SYS-001"))})"]'
    )

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
            ctitle = html.escape(str(c.get("title", cid))).replace('"', "'")
            ctype = html.escape(str(c.get("container_type", "container"))).replace('"', "'")
            runtime = html.escape(str(c.get("runtime", "N/A"))).replace('"', "'")
            lines.append(
                f"      {node_id}[\"<div style='padding:8px 12px;'><b>{html.escape(cid)}</b><br/>{ctitle}<br/><i>[{ctype} • {runtime}]</i></div>\"]:::{style_class}"
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
            raw_name = str(ext).strip()
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
                f"  {ext['id']}[\"<div style='padding:6px 10px;'><b>{ext['title']}</b><br/><i>[{ext['sub']}]</i></div>\"]:::external"
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
            tgt_id = str(dep_item).strip()
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
    cid = str(component.get("id") or "CNT")
    clean_title = html.escape(str(component.get("title") or cid)).replace('"', "'")
    str(component.get("container_type") or "library")
    str(component.get("runtime") or "N/A").replace('"', "'")
    internals = component.get("internal_components") or []
    box_id = f"subgraph_{cid.replace('-', '_')}"

    lines = [
        "graph TD",
        "  classDef module fill:#1e1b4b,stroke:#6366f1,color:#fff,stroke-width:2px,rx:8px;",
        "  classDef peer fill:#1e293b,stroke:#475569,color:#94a3b8,stroke-width:1.5px,rx:6px,stroke-dasharray: 4 4;",
        "  classDef contract fill:#064e3b,stroke:#10b981,color:#fff,stroke-width:2px,rx:6px;",
        "",
        f'  subgraph {box_id} ["📦 Level 2 Container: {html.escape(cid)} ({clean_title})"]',
    ]

    if not internals:
        fallback_id = f"mod_{cid.replace('-', '_')}_main"
        lines.append(
            f"    {fallback_id}[\"<div style='padding:10px 14px;min-width:300px;max-width:420px;white-space:normal;word-break:break-word;'><b>{clean_title}</b><br/><i>Core Container Logic</i></div>\"]:::module"
        )
    else:
        mod_node_by_ref: dict[str, str] = {}
        for idx, mod in enumerate(internals):
            mid = mod.get("id")
            mtitle = str(mod.get("title") or f"Module {idx + 1}").replace('"', "'")
            title_text = (
                f"<b>{html.escape(str(mid))}: {html.escape(mtitle)}</b>" if mid else f"<b>{html.escape(mtitle)}</b>"
            )
            resp = str(mod.get("responsibility") or "").replace('"', "'")
            raw_spath = mod.get("source_path") or ""
            spath = (
                ", ".join(str(p) for p in raw_spath if p is not None) if isinstance(raw_spath, list) else str(raw_spath)
            ).replace('"', "'")
            mod_id = f"mod_{cid.replace('-', '_')}_{idx}"
            if isinstance(mid, str) and mid.strip():
                mod_node_by_ref[mid.strip()] = mod_id
            if isinstance(mtitle, str) and mtitle.strip():
                mod_node_by_ref[mtitle.strip()] = mod_id
            lines.append(
                f"    {mod_id}[\"<div style='padding:10px 14px;text-align:left;min-width:300px;max-width:420px;white-space:normal;word-break:break-word;line-height:1.45;'>"
                f"{title_text}<br/><small style='color:#93c5fd;font-family:monospace;font-size:10px;'>{html.escape(spath)}</small><br/>"
                f"<span style='font-size:11px;color:#cbd5e1;'>{html.escape(resp)}</span></div>\"]:::module"
            )

        # Render intra-container component dependencies derived via AST imports
        drawn_mod_edges: set[tuple[str, str]] = set()
        for idx, mod in enumerate(internals):
            src_mod_id = f"mod_{cid.replace('-', '_')}_{idx}"
            dep_targets = [
                str(item) for item in (mod.get("consumes_components") or mod.get("dependencies") or []) if item
            ]
            for tgt_ref in dep_targets:
                tgt_mod_id = mod_node_by_ref.get(tgt_ref.strip())
                if tgt_mod_id and tgt_mod_id != src_mod_id and (src_mod_id, tgt_mod_id) not in drawn_mod_edges:
                    drawn_mod_edges.add((src_mod_id, tgt_mod_id))
                    lines.append(f"    {src_mod_id} -.-> {tgt_mod_id}")

    lines.append("  end")

    # Inbound / Outbound peer container dependencies
    deps = component.get("dependencies") or {}
    int_deps = (deps.get("internal_containers") or []) if isinstance(deps, dict) else []
    for raw_dep in int_deps:
        dep_id = (
            (str(raw_dep.get("id") or raw_dep.get("container") or "") if isinstance(raw_dep, dict) else str(raw_dep))
            .split("(", 1)[0]
            .strip()
        )
        if dep_id and dep_id != cid:
            peer_node = f"peer_{re.sub(r'[^a-zA-Z0-9_]', '_', dep_id)}"
            lines.append(
                f"  {peer_node}[\"<div style='padding:8px 12px;'><b>{html.escape(dep_id)}</b><br/><i>[Peer Container]</i></div>\"]:::peer"
            )
            first_mod = f"mod_{cid.replace('-', '_')}_0" if internals else f"mod_{cid.replace('-', '_')}_main"
            lines.append(f'  {first_mod} ==>|"Consumes / Depends On"| {peer_node}')

    # Provided contracts
    contracts = component.get("contracts") or {}
    provides = (contracts.get("provides") or []) if isinstance(contracts, dict) else []
    for raw_ctr in provides:
        ctr_id = (str(raw_ctr.get("id") or "") if isinstance(raw_ctr, dict) else str(raw_ctr)).split("(", 1)[0].strip()
        if not ctr_id:
            continue
        ctr_node = f"ctr_{re.sub(r'[^a-zA-Z0-9_]', '_', ctr_id)}"
        lines.append(
            f"  {ctr_node}[\"<div style='padding:8px 12px;'><b>{html.escape(ctr_id)}</b><br/><i>[Interface Contract]</i></div>\"]:::contract"
        )
        last_mod = (
            f"mod_{cid.replace('-', '_')}_{len(internals) - 1}" if internals else f"mod_{cid.replace('-', '_')}_main"
        )
        lines.append(f'  {last_mod} -->|"Provides Interface"| {ctr_node}')

    return "\n".join(lines)


def _generate_screen_state_mermaid(screen_id: str, states: list[Any], transitions: list[dict[str, Any]]) -> str:
    """Generate Mermaid stateDiagram-v2 syntax for screen state transitions."""
    clean_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(screen_id or "screen"))
    lines = ["stateDiagram-v2", "  direction LR"]
    if not states:
        lines.append(f"  [*] --> {clean_id}")
        return "\n".join(lines)

    def _sanitize_state_id(raw_val: Any, fallback_idx: int = 0) -> str:
        raw_str = str(raw_val or "").strip()
        cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw_str).strip("_")
        return cleaned or f"state_{fallback_idx}"

    state_names: list[str] = []
    for idx, s in enumerate(states):
        raw_name = (s.get("name") or s.get("id") or f"state_{idx}") if isinstance(s, dict) else (s or f"state_{idx}")
        state_names.append(_sanitize_state_id(raw_name, idx))

    lines.append(f"  [*] --> {state_names[0]}")
    for t in transitions or []:
        if not isinstance(t, dict):
            continue
        src = _sanitize_state_id(t.get("from_state") or state_names[0], 0)
        dst = _sanitize_state_id(t.get("to_state") or state_names[-1], len(state_names) - 1)
        trigger = html.escape(str(t.get("action") or "action").replace("\n", " "), quote=True)
        lines.append(f"  {src} --> {dst}: {trigger}")

    if not transitions and len(state_names) > 1:
        for i in range(len(state_names) - 1):
            lines.append(f"  {state_names[i]} --> {state_names[i + 1]}")

    return "\n".join(lines)


def _sanitize_mermaid_block(raw_code: str) -> str:
    """Strip unsafe Mermaid directives (`%%{init:...}%%`) and inline JS/URI click callbacks."""
    cleaned = re.sub(r"%%\{.*?\}%%", "", raw_code or "", flags=re.DOTALL)
    safe_lines: list[str] = []
    for line in cleaned.splitlines():
        if re.match(
            r"^\s*click\s+.*(?:\bcall\b|(?:javascript|data|vbscript)\s*:)",
            line,
            flags=re.IGNORECASE,
        ):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines).strip()


def _extract_or_generate_flow_mermaid(flow_id: str, flow_type: str, steps: list[dict[str, Any]], body: str) -> str:
    """Extract authored Mermaid block from spec body or generate from steps."""
    match = re.search(r"\[mermaid\]\s*\n----\s*\n(.*?)\n----", body or "", re.DOTALL)
    if match:
        sanitized = _sanitize_mermaid_block(match.group(1))
        return sanitized or "flowchart LR\n  Start --> End"

    if not steps:
        return "flowchart LR\n  Start --> End"

    if flow_type == "cross_client":
        lines = ["sequenceDiagram", "  autonumber"]
        participants: list[str] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            clt = str(s.get("client") or "Client")
            if clt not in participants:
                participants.append(clt)
        if not participants:
            participants.append("Client")
        for idx, p in enumerate(participants):
            p_clean = re.sub(r"[^a-zA-Z0-9_]", "_", p).strip("_") or f"Client_{idx + 1}"
            p_escaped = html.escape(p.replace("\n", " "), quote=True)
            lines.append(f"  participant {p_clean} as {p_escaped}")
        for idx, s in enumerate(steps):
            if not isinstance(s, dict):
                continue
            raw_clt = str(s.get("client") or participants[0])
            clt = re.sub(r"[^a-zA-Z0-9_]", "_", raw_clt).strip("_") or "Client_1"
            raw_title = str(s.get("title") or f"Step {s.get('step') or (idx + 1)}").replace("\n", " ")
            raw_action = str(s.get("action") or "").replace("\n", " ")
            raw_outcome = str(s.get("outcome") or "").replace("\n", " ")
            raw_screen = str(s.get("screen") or "").replace("\n", " ")
            title = html.escape(raw_title, quote=True)
            action = html.escape(raw_action, quote=True)
            outcome = html.escape(raw_outcome, quote=True)
            screen_stem = html.escape(Path(raw_screen).stem, quote=True) if raw_screen else ""
            note_suffix = f" [{screen_stem}]" if screen_stem else ""
            lines.append(f"  Note over {clt}: {title}{note_suffix}")
            msg = f"{action} ({outcome})" if outcome else action
            lines.append(f"  {clt}->>{clt}: {msg}")
        return "\n".join(lines)
    else:
        lines = ["flowchart LR"]
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                continue
            raw_step_num = s.get("step") if s.get("step") is not None else (i + 1)
            step_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw_step_num)).strip("_") or str(i + 1)
            step_label_num = html.escape(str(raw_step_num), quote=True)
            title = html.escape(str(s.get("title") or f"Step {raw_step_num}").replace("\n", " "), quote=True)
            raw_screen = str(s.get("screen") or "").replace("\n", " ")
            scr_stem = html.escape(Path(raw_screen).stem, quote=True) if raw_screen else ""
            scr_label = f"<br/><small>{scr_stem}</small>" if scr_stem else ""
            raw_outcome = str(s.get("outcome") or "").replace("\n", " ")
            outcome_label = f"<br/><i>{html.escape(raw_outcome, quote=True)}</i>" if raw_outcome else ""
            lines.append(f'  step{step_id}["<b>Step {step_label_num}: {title}</b>{scr_label}{outcome_label}"]')
        for i in range(len(steps) - 1):
            s_curr = steps[i] if isinstance(steps[i], dict) else {}
            s_next = steps[i + 1] if isinstance(steps[i + 1], dict) else {}
            raw_trigger = str(s_curr.get("action") or "next").replace("\n", " ").replace("|", "/")
            if len(raw_trigger) > 28:
                raw_trigger = raw_trigger[:25] + "..."
            trigger = html.escape(raw_trigger, quote=True)
            curr_raw = s_curr.get("step") if s_curr.get("step") is not None else (i + 1)
            next_raw = s_next.get("step") if s_next.get("step") is not None else (i + 2)
            curr_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(curr_raw)).strip("_") or str(i + 1)
            next_id = re.sub(r"[^a-zA-Z0-9_]", "_", str(next_raw)).strip("_") or str(i + 2)
            lines.append(f'  step{curr_id} -->|"{trigger}"| step{next_id}')
        return "\n".join(lines)
