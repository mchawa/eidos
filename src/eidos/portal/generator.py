"""Unified data generator synthesizing living specifications into the Web Portal data model."""

import html
import re
from pathlib import Path
from typing import Any

import orjson

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
        doc_body = doc.body
        if doc.doc_type == "delta/mutation":
            doc_status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
            if doc_status in ("applied", "rejected"):
                mot = str((doc.frontmatter.get("intent") or {}).get("motivation") or "").strip()
                doc_body = (
                    f"= SpecDelta: {doc.title or doc.doc_id}\n\n{mot}\n"
                    if mot
                    else f"= SpecDelta: {doc.title or doc.doc_id}\n"
                )
        item = {
            "id": doc.doc_id,
            "title": doc.title or doc.doc_id,
            "type": doc.doc_type,
            "schema": doc.schema_ref,
            "path": rel_path,
            "filename": doc.file_path.name,
            "frontmatter": doc.frontmatter,
            "body": doc_body,
            "is_valid": doc.is_valid,
            "errors": doc.errors,
        }
        specs.append(item)
        specs_by_type.setdefault(doc.doc_type, []).append(item)

    # 1. System Overview & Architecture
    arch_doc = next((s for s in specs if s["type"] == "tech/architecture"), None)
    sys_doc = next((s for s in specs if s["type"] == "product/system"), None)

    system_title = (
        (sys_doc["frontmatter"].get("title") if sys_doc else None)
        or (arch_doc["frontmatter"].get("title") if arch_doc else None)
        or getattr(project, "system_title", None)
        or "Living Architecture System"
    )
    system_id = (sys_doc["id"] if sys_doc else None) or getattr(project, "system_id", "SYS-001")

    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.index.sync import build_spec_path_resolver

    arch_idx = get_architectural_index(eidos_dir=eidos_path, repo_root=eidos_path.parent)
    arch_idx._ensure_synced()
    conn = arch_idx._get_connection()
    _resolve_ref_id = build_spec_path_resolver(conn)

    def _add_unique(lst: list[str], item: str) -> None:
        if item and item not in lst:
            lst.append(item)

    ast_consumes_by_comp: dict[str, list[str]] = {}
    ast_depends_by_cnt: dict[str, list[str]] = {}
    comp_to_cnt: dict[str, str] = {}
    dom_to_caps: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT source_id, target_id, rel_type FROM edges WHERE rel_type IN ('consumes_api', 'depends_on', 'part_of', 'realizes');"
    ).fetchall():
        src = str(r["source_id"] or "")
        dst = str(r["target_id"] or "")
        rel = r["rel_type"]
        if rel == "consumes_api":
            _add_unique(ast_consumes_by_comp.setdefault(src, []), dst)
        elif rel == "depends_on" and src.startswith("CNT-") and dst.startswith("CNT-") and src != dst:
            _add_unique(ast_depends_by_cnt.setdefault(src, []), dst)
        elif rel == "part_of":
            comp_to_cnt[src] = dst
        elif rel == "realizes" and src.startswith("CAP-"):
            _add_unique(dom_to_caps.setdefault(dst, []), src)

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
                "container": _resolve_ref_id(fm.get("container", "N/A")) or "N/A",
                "specification_file": fm.get("specification_file", "N/A"),
                "description": ct["body"],
            }
        )

    # Components & Containers (C4 Level 2 Containers and Level 3 Components)
    raw_containers = specs_by_type.get("tech/container", [])
    raw_components = specs_by_type.get("tech/component", [])

    # Index standalone components by their parent container ID
    components_by_container: dict[str, list[dict[str, Any]]] = {}
    for pc in raw_components:
        p_fm = pc.get("frontmatter", {})
        target_cnt = comp_to_cnt.get(pc["id"]) or _resolve_ref_id(p_fm.get("container", ""))
        m_resp = re.search(r"== Responsibility\s*\n+([^\n=]+(?:\n[^\n=]+)*)", pc.get("body", ""))
        comp_resp = " ".join(m_resp.group(1).split()) if m_resp else ""
        comp_item_dict = {
            "id": pc["id"],
            "title": p_fm.get("title", pc["id"]),
            "responsibility": comp_resp,
            "source_path": p_fm.get("source_path", ""),
            "file_path": pc.get("path", ""),
            "spec_path": pc.get("path", ""),
            "domain": p_fm.get("domain", ""),
            "dependencies": list(ast_consumes_by_comp.get(pc["id"], [])),
            "consumes_components": list(ast_consumes_by_comp.get(pc["id"], [])),
            "invariants": p_fm.get("invariants", []),
            "decisions": p_fm.get("decisions", []),
            "status": p_fm.get("status", "active"),
            "description": pc.get("body", ""),
        }
        if target_cnt:
            components_by_container.setdefault(target_cnt, []).append(comp_item_dict)

    components: list[dict[str, Any]] = []
    for c in raw_containers:
        fm = c["frontmatter"]
        raw_src = fm.get("source_path", "N/A")
        src_path_str = ", ".join(str(p) for p in raw_src) if isinstance(raw_src, list) else str(raw_src)

        # Extract container responsibility / summary from Architectural Boundary
        m = re.search(r"== Architectural Boundary\s*\n+([^\n=]+(?:\n[^\n=]+)*)", c.get("body", ""))
        resp = " ".join(m.group(1).split()) if m else f"Core architectural container for {c['id']}."

        # Internal components (standalone C4 Level 3 components mapped to container)
        doc_id = c["id"]
        internal_components = [dict(pc_dict) for pc_dict in components_by_container.get(doc_id, [])]

        comp_dict = {
            "id": c["id"],
            "title": fm.get("title", c["id"]),
            "container_type": fm.get("container_type", "library"),
            "runtime": fm.get("runtime", "N/A"),
            "source_path": src_path_str,
            "domain": fm.get("domain", "N/A"),
            "responsibility": resp,
            "summary": resp,
            "internal_components": internal_components,
            "dependencies": {
                "internal_containers": list(ast_depends_by_cnt.get(doc_id, [])),
                "external_services": [],
            },
            "contracts": fm.get("contracts", {}),
            "invariants": fm.get("invariants", []),
            "decisions": fm.get("decisions", []),
            "tier": fm.get("tier"),
            "description": c["body"],
        }
        tier_id, tier_name = _classify_container_tier(comp_dict)
        comp_dict["tier"] = tier_id
        comp_dict["tier_name"] = tier_name
        components.append(comp_dict)

    # 2. Product Domains, Capabilities, Personas, Policies
    domains: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/domain", []):
        d = dict(s["frontmatter"])
        d["title"] = d.get("title") or s.get("title") or d.get("id", "")
        d["body"] = s.get("body", "")
        d["path"] = s.get("path", "")
        m = re.search(r"== Domain Vision\s*\n+([^=\n][^\n]+)", s.get("body", ""))
        if m:
            d["mission"] = m.group(1).strip()
        else:
            d["mission"] = f"Bounded strategic context {d.get('title', d.get('id', ''))}."
        d["capabilities"] = list(dom_to_caps.get(str(d.get("id") or ""), []))
        domains.append(d)

    capabilities: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/capability", []):
        c = dict(s["frontmatter"])
        c["body"] = s.get("body", "")
        c["path"] = s.get("path", "")
        c["title"] = c.get("title") or s.get("title") or c.get("id", "Capability")
        m_jtbd = re.search(
            r"^==\s+Job To Be Done[^\n]*\n+([^=\n][^\n]+)",
            s.get("body", ""),
            re.MULTILINE,
        )
        c["summary"] = m_jtbd.group(1).strip() if m_jtbd else ""
        c["invariants"] = c.get("technical", {}).get("invariants", [])
        capabilities.append(c)

    personas: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/persona", []):
        p = dict(s["frontmatter"])
        p["title"] = p.get("title") or s.get("title") or p.get("id", "Persona")
        p["body"] = s.get("body", "")
        p["path"] = s.get("path", "")
        p["role"] = (
            p.get("role_type", "").replace("_", " ").title() if p.get("role_type") else "Actor Persona"
        )
        p["archetype"] = p.get("role_type", "persona")
        personas.append(p)

    policies: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/policy", []):
        pol = dict(s["frontmatter"])
        pol["body"] = s.get("body", "")
        pol["path"] = s.get("path", "")
        policies.append(pol)

    # Invariants list (hydrated from indexed invariant entities in ArchitecturalIndex)
    invariants: list[dict[str, Any]] = []
    for inv_ent in arch_idx.get_entities(entity_type="invariant"):
        iid = str(inv_ent.get("id") or "").strip()
        if not iid:
            continue
        src_doc_type = str(inv_ent.get("source_doc_type") or "")
        src_id = str(inv_ent.get("source_id") or "")
        rule = inv_ent.get("rule")
        tb = inv_ent.get("test_binding") or "N/A"
        if src_doc_type == "product/policy":
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": rule or "Policy invariant rule.",
                    "category": inv_ent.get("category") or "governance",
                    "severity": inv_ent.get("severity") or "blocking",
                    "source_type": f"policy ({src_id})",
                    "test_binding": tb,
                }
            )
        elif src_doc_type == "product/capability":
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": rule,
                    "category": "capability",
                    "source_type": f"capability ({src_id})",
                    "test_binding": tb,
                }
            )
        elif src_doc_type == "design/ui-component":
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": rule or "UI component invariant.",
                    "category": "design",
                    "source_type": f"ui_component ({src_id})",
                    "test_binding": tb,
                }
            )
        elif src_doc_type == "tech/component":
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": rule or "Component technical invariant.",
                    "category": "architecture",
                    "source_type": f"component ({src_id})",
                    "test_binding": tb,
                }
            )
        else:
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": rule or "Container operational invariant.",
                    "category": "architecture",
                    "source_type": f"container ({src_id})",
                    "test_binding": tb,
                }
            )

    # Embedded architectural decisions (DEC-*) across system, architecture, containers, domains, capabilities, and policies
    all_decisions: list[dict[str, Any]] = []
    seen_dec_ids: set[str] = set()

    def _append_decisions(dec_list: list[Any], source_entity: str, source_type: str) -> None:
        for dec in dec_list or []:
            if not isinstance(dec, dict):
                continue
            did = dec.get("id")
            if did and did in seen_dec_ids:
                continue
            if did:
                seen_dec_ids.add(did)
            d_entry = dict(dec)
            d_entry["source_entity"] = source_entity
            d_entry["source_id"] = source_entity
            d_entry["source_type"] = source_type
            all_decisions.append(d_entry)

    if sys_doc:
        _append_decisions(sys_doc.get("frontmatter", {}).get("decisions", []), sys_doc["id"], "system")
    if arch_doc:
        _append_decisions(arch_doc.get("frontmatter", {}).get("decisions", []), arch_doc["id"], "architecture")
    for comp in components:
        _append_decisions(comp.get("decisions", []), comp["id"], "container")
        for ic in comp.get("internal_components", []):
            _append_decisions(ic.get("decisions", []), ic.get("id", comp["id"]), "component")
    for dom in domains:
        _append_decisions(dom.get("decisions", []), dom.get("id", "DOM"), "domain")
    for cap in capabilities:
        _append_decisions(
            cap.get("decisions", []),
            cap.get("id", "CAP"),
            "capability",
        )
    for pol in policies:
        _append_decisions(pol.get("decisions", []), pol.get("id", "POL"), "policy")

    # 3. Multi-Design Systems, Tokens, UI Components, Clients, Flows, and Screens
    design_data = _extract_design_systems(eidos_path, specs_by_type, _resolve_ref_id, conn=conn)
    design_systems = design_data.get("systems", [])
    screens = design_data.get("screens", [])
    ui_components = design_data.get("ui_components", [])
    clients = design_data.get("clients", [])
    flows = design_data.get("flows", [])

    # 4. SpecDeltas & Kanban status columns (computes delta affects and blast_radius via ArchitecturalIndex)
    deltas_by_status: dict[str, list[dict[str, Any]]] = {
        "proposed": [],
        "in_review": [],
        "approved": [],
        "applied": [],
        "rejected": [],
    }

    from eidos.core.lifecycle.delta_engine import normalize_delta_mutation

    raw_deltas = specs_by_type.get("delta/mutation", [])
    for d in raw_deltas:
        fm = d["frontmatter"]
        status = str(fm.get("status", "proposed")).lower()
        raw_muts = fm.get("mutations", []) or []
        muts = [normalize_delta_mutation(m) if isinstance(m, dict) else m for m in raw_muts]
        d_path_norm = str(d.get("path", "")).replace("\\", "/")
        is_archived_delta = "deltas/archive/" in d_path_norm
        derived_affects, derived_blast_radius = arch_idx.compute_delta_impact(
            mutations=muts,
            sync=False,
        )

        delta_item = {
            "id": d["id"],
            "title": d["title"],
            "status": status,
            "kind": fm.get("intent", {}).get("kind", "feature"),
            "motivation": fm.get("intent", {}).get("motivation", ""),
            "mutations": muts,
            "affects": derived_affects,
            "blast_radius": derived_blast_radius,
            "vcs": fm.get("vcs"),
            "is_archived": is_archived_delta,
            "body": d["body"],
            "path": d.get("path", ""),
        }
        if status in deltas_by_status:
            deltas_by_status[status].append(delta_item)
        else:
            deltas_by_status.setdefault(status, []).append(delta_item)

    # 5. Product Explorations & Spikes
    raw_explorations = specs_by_type.get("product/exploration", [])
    explorations: list[dict[str, Any]] = []
    for exp in raw_explorations:
        fm = exp["frontmatter"]
        raw_dec = fm.get("decision")
        decision_dict: dict[str, Any] = dict(raw_dec) if isinstance(raw_dec, dict) else {}

        hypothesis = ""
        m_hyp = re.search(
            r"(?:\*Hypothesis:\*|^==+\s+[^\n]*Hypothesis[^\n]*)\s*\n+([^=\n][^\n]+)",
            exp.get("body", ""),
            re.MULTILINE,
        )
        if m_hyp:
            hypothesis = " ".join(m_hyp.group(1).split())

        findings = ""
        m_find = re.search(
            r"(?:\*Findings:\*|^==+\s+[^\n]*Findings[^\n]*)\s*\n+\*?\s*([^\n]+)",
            exp.get("body", ""),
            re.MULTILINE,
        )
        if m_find:
            findings = " ".join(m_find.group(1).split())

        outcome = decision_dict.get("outcome") or (
            fm.get("status") if fm.get("status") in ("adopted", "discarded", "pivoted") else ""
        )

        raw_res_deltas = decision_dict.get("resulting_deltas") or []
        if isinstance(raw_res_deltas, str):
            resulting_deltas = [raw_res_deltas]
        elif isinstance(raw_res_deltas, list):
            resulting_deltas = [str(d) for d in raw_res_deltas if d]
        else:
            resulting_deltas = []

        if outcome:
            decision_dict["outcome"] = outcome
        if findings:
            decision_dict["learnings"] = findings
            decision_dict["findings"] = findings
        if resulting_deltas:
            decision_dict["resulting_deltas"] = resulting_deltas

        exp_path_norm = str(exp.get("path", "")).replace("\\", "/")
        is_archived_exp = "explorations/archive/" in exp_path_norm

        explorations.append(
            {
                "id": exp["id"],
                "title": exp["title"],
                "status": fm.get("status", "proposed"),
                "is_archived": is_archived_exp,
                "hypothesis": hypothesis,
                "exit_criteria": {},
                "findings": findings,
                "recommendation": "",
                "resulting_deltas": resulting_deltas,
                "decision": decision_dict,
                "body": exp["body"],
                "path": exp.get("path", ""),
            }
        )

    # 6. Dynamic Mermaid Diagrams
    ext_systems = (
        arch_doc.get("frontmatter", {}).get("c4_context", {}).get("external_systems", []) if arch_doc else []
    ) or []
    c4_context_mermaid = _generate_c4_context_mermaid(personas, system_id, system_title, external_systems=ext_systems)
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
        "decisions_count": len(all_decisions),
    }

    edges_list = [
        {
            "source": str(r["source_id"] or ""),
            "target": str(r["target_id"] or ""),
            "type": str(r["rel_type"] or ""),
        }
        for r in conn.execute(
            "SELECT source_id, target_id, rel_type FROM edges ORDER BY source_id, target_id, rel_type;"
        ).fetchall()
    ]
    validation_summary = {
        "is_clean": report.is_clean,
        "total_docs": report.total_docs,
        "valid_docs": report.valid_docs,
        "schema_errors": report.schema_errors,
        "broken_references": report.broken_references,
    }

    return {
        "overview": overview,
        "validation": validation_summary,
        "edges": edges_list,
        "system_architecture": arch_doc,
        "specs": specs,
        "components": components,
        "contracts": contracts,
        "domains": domains,
        "capabilities": capabilities,
        "personas": personas,
        "policies": policies,
        "invariants": invariants,
        "decisions": all_decisions,
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


def _extract_design_systems(
    eidos_path: Path,
    specs_by_type: dict[str, list[dict[str, Any]]],
    resolve_ref: Any = None,
    conn: Any = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan and parse multi-design systems, W3C tokens.json, UI components, clients, flows, and screens."""

    def _res(val: Any) -> str:
        if resolve_ref is not None:
            return str(resolve_ref(val))
        return str(val or "").strip()

    def _add_uniq(lst: list[str], item: str) -> None:
        if item and item not in lst:
            lst.append(item)

    # Reverse-map capabilities and clients from persisted knowledge graph edges
    clt_to_caps: dict[str, list[str]] = {}
    scr_to_caps: dict[str, list[str]] = {}
    flw_to_caps: dict[str, list[str]] = {}
    scr_to_client: dict[str, str] = {}
    flw_to_clients: dict[str, list[str]] = {}
    if conn is not None:
        for src_id, dst_id, rel_type in conn.execute(
            "SELECT source_id, target_id, rel_type FROM edges WHERE rel_type IN ('delivered_by', 'has_screen', 'has_flow');"
        ).fetchall():
            src = str(src_id or "").strip()
            target = str(dst_id or "").strip()
            if rel_type == "delivered_by":
                if target.startswith("CLT-"):
                    _add_uniq(clt_to_caps.setdefault(target, []), src)
                elif target.startswith("SCR-"):
                    _add_uniq(scr_to_caps.setdefault(target, []), src)
                elif target.startswith("FLOW-"):
                    _add_uniq(flw_to_caps.setdefault(target, []), src)
            elif rel_type == "has_screen" and src.startswith("CLT-"):
                scr_to_client.setdefault(target, src)
            elif rel_type == "has_flow" and src.startswith("CLT-"):
                _add_uniq(flw_to_clients.setdefault(target, []), src)

    systems: list[dict[str, Any]] = []
    design_specs = specs_by_type.get("design/system", [])

    for ds in design_specs:
        fm = ds["frontmatter"]
        sys_id = ds["id"]
        ds_rel_path = ds.get("path", "")
        ds_file_path = (eidos_path.parent / ds_rel_path) if ds_rel_path else Path()
        sys_dir = (
            ds_file_path.parent
            if ds_file_path.exists()
            else (eidos_path / "design" / "systems" / sys_id.lower().replace("ds-", ""))
        )
        tok_cfg = fm.get("tokens") if isinstance(fm.get("tokens"), dict) else {}
        manifest_ref = tok_cfg.get("manifest")
        tokens_file = sys_dir / "tokens.json"
        if isinstance(manifest_ref, str) and manifest_ref.strip():
            cand_rel = manifest_ref.strip()
            for candidate in (
                sys_dir / cand_rel,
                eidos_path.parent / cand_rel,
                eidos_path / cand_rel,
            ):
                if candidate.is_file():
                    tokens_file = candidate
                    break
        tokens_data: dict[str, Any] = {}
        if tokens_file.exists():
            try:
                tokens_data = orjson.loads(tokens_file.read_text(encoding="utf-8"))
            except (orjson.JSONDecodeError, ValueError, OSError):
                tokens_data = {}

        systems.append(
            {
                "id": sys_id,
                "title": ds["title"],
                "client": "core",
                "brand_name": ds["title"],
                "extends": fm.get("extends"),
                "tokens": tokens_data,
            }
        )

    default_ds_id = systems[0]["id"] if systems else "DS-001"

    # Clients (and map client -> design_system)
    clt_to_ds: dict[str, str] = {}
    ds_to_client: dict[str, str] = {}

    clients: list[dict[str, Any]] = []
    for clt in specs_by_type.get("design/client", []):
        fm = clt["frontmatter"]
        clt_id = clt["id"]
        ds_ref = _res(fm.get("design_system", default_ds_id)) or default_ds_id
        clt_to_ds[clt_id] = ds_ref
        ds_to_client.setdefault(ds_ref, clt_id)

        clients.append(
            {
                "id": clt_id,
                "title": clt["title"],
                "platform": fm.get("platform", "web"),
                "design_system": ds_ref,
                "container": _res(fm.get("container", "N/A")) or "N/A",
                "capabilities": clt_to_caps.get(clt_id, []),
                "screens": fm.get("screens", []) or [],
                "flows": fm.get("flows", []) or [],
                "body": clt["body"],
            }
        )

    for sys_item in systems:
        if sys_item["id"] in ds_to_client:
            sys_item["client"] = ds_to_client[sys_item["id"]]

    # Flows
    flows: list[dict[str, Any]] = []
    for fl in specs_by_type.get("design/flow", []):
        fm = fl["frontmatter"]
        fl_id = fl["id"]
        flow_type = fm.get("flow_type", "client_internal")
        steps = fm.get("steps", [])
        body = fl.get("body", "")
        mermaid = _extract_or_generate_flow_mermaid(fl_id, flow_type, steps, body)
        derived_clients = list(flw_to_clients.get(fl_id, []))
        flows.append(
            {
                "id": fl_id,
                "title": fl["title"],
                "status": fm.get("status", "active"),
                "flow_type": flow_type,
                "client": derived_clients[0] if derived_clients else None,
                "clients": derived_clients,
                "capabilities": flw_to_caps.get(fl_id, []),
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
                "design_system": _res(fm.get("design_system", default_ds_id)) or default_ds_id,
                "category": fm.get("category", "layouts"),
                "status": fm.get("status", "active"),
                "variants": fm.get("variants", []),
                "states": fm.get("states", []),
                "invariants": fm.get("invariants", []),
                "body": uic["body"],
            }
        )

    # Screens and state machines
    screens: list[dict[str, Any]] = []
    for scr in specs_by_type.get("design/screen", []):
        fm = scr["frontmatter"]
        scr_id = scr["id"]
        states = fm.get("states", [])
        mermaid_state = _generate_screen_state_mermaid(scr_id, states, [])
        owning_client = scr_to_client.get(scr_id, "N/A")
        screens.append(
            {
                "id": scr_id,
                "title": scr["title"],
                "route": fm.get("route", "/"),
                "client": owning_client,
                "system_ref": clt_to_ds.get(owning_client, default_ds_id),
                "states": states,
                "slots": fm.get("slots", {}),
                "capabilities": scr_to_caps.get(scr_id, []),
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


def _generate_c4_context_mermaid(
    personas: list[dict[str, Any]],
    system_id: str,
    system_title: str,
    external_systems: list[Any] | None = None,
) -> str:
    """Generate Mermaid syntax for C4 Level 1 System Context from declared personas and external_systems."""
    clean_title = html.escape(str(system_title or "")).replace('"', "'")
    lines = [
        "graph TD",
        "  classDef actor fill:#0284c7,stroke:#0369a1,color:#fff,stroke-width:2px,rx:6px;",
        "  classDef system fill:#059669,stroke:#047857,color:#fff,stroke-width:3px,rx:8px;",
        "  classDef external fill:#334155,stroke:#475569,color:#fff,stroke-width:2px,rx:6px;",
        "",
        f"  SYS[\"<div style='padding:10px 14px;font-size:15px;'><b>{html.escape(str(system_id or 'SYS-001'))}</b><br/>{clean_title}<br/><i>[Software System]</i></div>\"]:::system",
    ]

    ext_edges: list[tuple[str, str]] = []
    for idx, ext in enumerate(external_systems or []):
        ext_node_id = f"EXT_{idx + 1}"
        ext_name = html.escape(str(ext)).replace('"', "'")
        ext_role = "External Integration"
        ext_proto = "External System"
        lines.append(
            f"  {ext_node_id}[\"<div style='padding:8px 12px;'><b>{ext_name}</b><br/>{ext_role}<br/><i>[{ext_proto}]</i></div>\"]:::external"
        )
        ext_edges.append((ext_node_id, ext_proto))

    for p in personas:
        pid = p.get("id", "PER")
        ptitle = html.escape(str(p.get("title", pid))).replace('"', "'")
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
        lines.append(f"  SYS ==>|{ext_proto}| {ext_node_id}")

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
        ctr_id = (
            (str(raw_ctr.get("id") or "") if isinstance(raw_ctr, dict) else str(raw_ctr))
            .split("(", 1)[0]
            .strip()
        )
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
        trigger = str(t.get("action") or "action").replace("\n", " ")
        lines.append(f"  {src} --> {dst}: {trigger}")

    if not transitions and len(state_names) > 1:
        for i in range(len(state_names) - 1):
            lines.append(f"  {state_names[i]} --> {state_names[i + 1]}")

    return "\n".join(lines)


def _extract_or_generate_flow_mermaid(flow_id: str, flow_type: str, steps: list[dict[str, Any]], body: str) -> str:
    """Extract authored Mermaid block from spec body or generate from steps."""
    match = re.search(r"\[mermaid\]\s*\n----\s*\n(.*?)\n----", body or "", re.DOTALL)
    if match:
        return match.group(1).strip()

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
        for p in participants:
            p_clean = re.sub(r"[^a-zA-Z0-9_]", "_", p)
            lines.append(f"  participant {p_clean} as {p}")
        for s in steps:
            if not isinstance(s, dict):
                continue
            clt = re.sub(r"[^a-zA-Z0-9_]", "_", str(s.get("client") or participants[0]))
            title = str(s.get("title") or f"Step {s.get('step') or ''}").replace('"', "'")
            action = str(s.get("action") or "").replace('"', "'")
            lines.append(f"  Note over {clt}: {title}")
            lines.append(f"  {clt}->>{clt}: {action}")
        return "\n".join(lines)
    else:
        lines = ["flowchart LR"]
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                continue
            step_num = s.get("step") if s.get("step") is not None else (i + 1)
            title = str(s.get("title") or f"Step {step_num}").replace('"', "'")
            screen = str(s.get("screen") or "")
            scr_label = f"<br/><small>{Path(screen).stem}</small>" if screen else ""
            lines.append(f'  step{step_num}["<b>Step {step_num}: {title}</b>{scr_label}"]')
        for i in range(len(steps) - 1):
            s_curr = steps[i] if isinstance(steps[i], dict) else {}
            s_next = steps[i + 1] if isinstance(steps[i + 1], dict) else {}
            trigger = str(s_curr.get("action") or "next").replace('"', "'")
            if len(trigger) > 28:
                trigger = trigger[:25] + "..."
            curr_num = s_curr.get("step") if s_curr.get("step") is not None else (i + 1)
            next_num = s_next.get("step") if s_next.get("step") is not None else (i + 2)
            lines.append(f'  step{curr_num} -->|"{trigger}"| step{next_num}')
        return "\n".join(lines)
