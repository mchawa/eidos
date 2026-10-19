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
    arch_doc = next((s for s in specs if s["type"] in ("tech/architecture", "arch/system")), None)
    sys_doc = next((s for s in specs if s["type"] in ("product/system", "arch/system")), None)

    system_title = (
        (sys_doc["frontmatter"].get("title") if sys_doc else None)
        or (arch_doc["frontmatter"].get("title") if arch_doc else None)
        or getattr(project, "system_title", None)
        or getattr(project, "system_name", None)
        or "Living Architecture System"
    )
    system_id = (sys_doc["id"] if sys_doc else None) or getattr(project, "system_id", "SYS-001")

    path_to_id: dict[str, str] = {}
    for s in specs:
        sid = s.get("id")
        spath = str(s.get("path", "")).replace("\\", "/").strip().lstrip("./")
        if sid and spath:
            path_to_id[spath.lower()] = sid
            if spath.lower().startswith("eidos/"):
                path_to_id[spath[6:].lower()] = sid

    def _resolve_ref_id(raw_val: Any) -> str:
        val = str(raw_val or "").strip()
        if not val:
            return ""
        norm = val.replace("\\", "/").lstrip("./").lower()
        return path_to_id.get(norm, val)

    def _add_unique(lst: list[str], item: str) -> None:
        if item and item not in lst:
            lst.append(item)

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

    # Query AST-derived component/container dependencies from the architectural knowledge graph
    ast_consumes_by_comp: dict[str, list[str]] = {}
    ast_depends_by_cnt: dict[str, list[str]] = {}
    try:
        from eidos.core.index.graph_index import get_architectural_index

        arch_idx = get_architectural_index(eidos_dir=eidos_path, repo_root=eidos_path.parent)
        arch_idx._ensure_synced()
        conn = arch_idx._get_connection()
        for r in conn.execute(
            "SELECT source_id, target_id, rel_type FROM edges WHERE rel_type IN ('consumes_api', 'depends_on');"
        ).fetchall():
            if r["rel_type"] == "consumes_api":
                _add_unique(ast_consumes_by_comp.setdefault(str(r["source_id"]), []), str(r["target_id"]))
            elif r["rel_type"] == "depends_on":
                _add_unique(ast_depends_by_cnt.setdefault(str(r["source_id"]), []), str(r["target_id"]))
    except Exception:
        pass

    # Components & Containers (C4 Level 2 Containers and Level 3 Promoted Components)
    raw_containers = specs_by_type.get("tech/container", [])
    raw_promoted_components = specs_by_type.get("tech/component", [])

    # Index promoted components by their parent container ID
    promoted_by_container: dict[str, list[dict[str, Any]]] = {}
    for pc in raw_promoted_components:
        p_fm = pc.get("frontmatter", {})
        target_cnt = _resolve_ref_id(p_fm.get("container", ""))
        promoted_dict = {
            "id": pc["id"],
            "title": p_fm.get("title", pc["id"]),
            "responsibility": p_fm.get("responsibility", ""),
            "source_path": p_fm.get("source_path", ""),
            "file_path": pc.get("path", ""),
            "spec_path": pc.get("path", ""),
            "domain": p_fm.get("domain", ""),
            "dependencies": list(ast_consumes_by_comp.get(pc["id"], [])),
            "consumes_components": list(ast_consumes_by_comp.get(pc["id"], [])),
            "invariants": p_fm.get("invariants", []),
            "decisions": p_fm.get("decisions", []),
            "status": p_fm.get("status", "active"),
            "is_promoted": True,
            "description": pc.get("body", ""),
        }
        if target_cnt:
            promoted_by_container.setdefault(target_cnt, []).append(promoted_dict)

    components: list[dict[str, Any]] = []
    for c in raw_containers:
        fm = c["frontmatter"]
        raw_src = fm.get("source_path", "N/A")
        src_path_str = ", ".join(str(p) for p in raw_src) if isinstance(raw_src, list) else str(raw_src)

        # Extract container responsibility / summary from frontmatter or Architectural Boundary
        resp = fm.get("responsibility") or fm.get("summary")
        if not resp:
            m = re.search(r"== Architectural Boundary\s*\n+([^\n=]+(?:\n[^\n=]+)*)", c.get("body", ""))
            resp = " ".join(m.group(1).split()) if m else f"Core architectural container for {c['id']}."

        # Internal components (inline + promoted)
        doc_id = c["id"]
        cnt_promoted = promoted_by_container.get(doc_id, [])
        raw_internal = list(fm.get("components", []) or [])
        internal_components: list[dict[str, Any]] = []
        existing_ids: set[str] = set()

        for ic in raw_internal:
            if not isinstance(ic, dict):
                continue
            ic_copy = dict(ic)
            ic_id = ic_copy.get("id")
            if ic_id:
                ic_copy["title"] = ic_copy.get("title", ic_id)
                ic_copy["dependencies"] = list(ast_consumes_by_comp.get(ic_id, []))
                ic_copy["consumes_components"] = list(ast_consumes_by_comp.get(ic_id, []))
                internal_components.append(ic_copy)
                existing_ids.add(ic_id)

        for pc_dict in cnt_promoted:
            if pc_dict["id"] not in existing_ids:
                internal_components.append(dict(pc_dict))
                existing_ids.add(pc_dict["id"])

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
            "dependencies": {"internal_containers": [], "external_services": []},
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

    # Derive inter-container dependencies from contracts (consumes -> provides/container) and AST import edges
    contract_to_provider: dict[str, str] = {}
    for ct in contracts:
        if ct.get("id") and ct.get("container") and str(ct["container"]).startswith("CNT-"):
            contract_to_provider[ct["id"]] = str(ct["container"])
    comp_to_container: dict[str, str] = {}
    for cnt in components:
        cnt_id = cnt["id"]
        cnt_ctrs = cnt.get("contracts", {})
        if isinstance(cnt_ctrs, dict):
            for p_ctr in cnt_ctrs.get("provides", []) or []:
                p_id = _resolve_ref_id(p_ctr)
                if p_id:
                    contract_to_provider[p_id] = cnt_id
        for ic in cnt.get("internal_components", []):
            ic_id = ic.get("id")
            if ic_id:
                comp_to_container[ic_id] = cnt_id

    for cnt in components:
        cnt_id = cnt["id"]
        derived_int_cnts: list[str] = []
        cnt_ctrs = cnt.get("contracts", {})
        if isinstance(cnt_ctrs, dict):
            for c_ctr in cnt_ctrs.get("consumes", []) or []:
                c_id = _resolve_ref_id(c_ctr)
                prov_cnt = contract_to_provider.get(c_id)
                if prov_cnt and prov_cnt != cnt_id:
                    _add_unique(derived_int_cnts, prov_cnt)
        for dep_cnt_id in ast_depends_by_cnt.get(cnt_id, []):
            if dep_cnt_id.startswith("CNT-") and dep_cnt_id != cnt_id:
                _add_unique(derived_int_cnts, dep_cnt_id)
        for ic in cnt.get("internal_components", []):
            for t_id in ic.get("consumes_components", []) or []:
                if t_id.startswith("CNT-") and t_id != cnt_id:
                    _add_unique(derived_int_cnts, t_id)
                elif t_id in comp_to_container and comp_to_container[t_id] != cnt_id:
                    _add_unique(derived_int_cnts, comp_to_container[t_id])
        cnt["dependencies"] = {
            "internal_containers": derived_int_cnts,
            "external_services": [],
        }

    # 2. Product Domains, Capabilities, Personas, Policies
    domains: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/domain", []):
        d = dict(s["frontmatter"])
        d["title"] = d.get("title") or s.get("title") or d.get("id", "")
        d["body"] = s.get("body", "")
        d["path"] = s.get("path", "")
        if not d.get("mission"):
            m = re.search(r"== Domain Vision\s*\n+([^=\n][^\n]+)", s.get("body", ""))
            if m:
                d["mission"] = m.group(1).strip()
            else:
                d["mission"] = f"Bounded strategic context {d.get('title', d.get('id', ''))}."
        domains.append(d)

    capabilities: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/capability", []):
        c = dict(s["frontmatter"])
        c["body"] = s.get("body", "")
        c["path"] = s.get("path", "")
        c["title"] = c.get("title") or s.get("title") or c.get("id", "Capability")
        if not c.get("summary"):
            m_jtbd = re.search(
                r"^==\s+Job To Be Done[^\n]*\n+([^=\n][^\n]+)",
                s.get("body", ""),
                re.MULTILINE,
            )
            c["summary"] = m_jtbd.group(1).strip() if m_jtbd else ""
        c["invariants"] = c.get("technical", {}).get("invariants", [])
        capabilities.append(c)

    # Link capability IDs to domains if not explicitly set
    for dom in domains:
        dom_id = dom.get("id")
        bound_cap_ids = [c.get("id") for c in capabilities if _resolve_ref_id(c.get("domain")) == dom_id]
        if not dom.get("capabilities"):
            dom["capabilities"] = bound_cap_ids

    personas: list[dict[str, Any]] = []
    for s in specs_by_type.get("product/persona", []):
        p = dict(s["frontmatter"])
        p["title"] = p.get("title") or s.get("title") or p.get("id", "Persona")
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
    seen_inv_ids: set[str] = set()
    for pol in policies:
        pid = pol.get("id", "")
        for inv in pol.get("invariants", []) or []:
            if not isinstance(inv, dict):
                continue
            iid = inv.get("id")
            if iid and iid not in seen_inv_ids:
                seen_inv_ids.add(iid)
                invariants.append(
                    {
                        "id": iid,
                        "title": iid,
                        "rule": inv.get("rule", "Policy invariant rule."),
                        "category": pol.get("category", "governance"),
                        "severity": pol.get("severity", "blocking"),
                        "source_type": f"policy ({pid})",
                        "test_binding": inv.get("test_binding", "N/A"),
                    }
                )

    for cap in capabilities:
        cap_id = cap.get("id", "CAP")
        for inv in cap.get("technical", {}).get("invariants", []) or []:
            if not isinstance(inv, dict):
                continue
            iid = inv.get("id")
            if iid and iid in seen_inv_ids:
                continue
            if iid:
                seen_inv_ids.add(iid)
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": inv.get("rule"),
                    "category": "capability",
                    "source_type": f"capability ({cap_id})",
                    "test_binding": inv.get("test_binding", "N/A"),
                }
            )

    # Invariants from Containers and Components (DLT-2026-031)
    for comp in components:
        for inv in comp.get("invariants", []) or []:
            if not isinstance(inv, dict):
                continue
            iid = inv.get("id")
            if iid and iid in seen_inv_ids:
                continue
            if iid:
                seen_inv_ids.add(iid)
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": inv.get("rule", "Container operational invariant."),
                    "category": "architecture",
                    "source_type": f"container ({comp['id']})",
                    "test_binding": inv.get("test_binding", "N/A"),
                }
            )
        for ic in comp.get("internal_components", []):
            for inv in ic.get("invariants", []) or []:
                if not isinstance(inv, dict):
                    continue
                iid = inv.get("id")
                if iid and iid in seen_inv_ids:
                    continue
                if iid:
                    seen_inv_ids.add(iid)
                invariants.append(
                    {
                        "id": iid,
                        "title": iid,
                        "rule": inv.get("rule", "Component technical invariant."),
                        "category": "architecture",
                        "source_type": f"component ({ic.get('id', comp['id'])})",
                        "test_binding": inv.get("test_binding", "N/A"),
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
            cap.get("decisions", []) or cap.get("technical", {}).get("decisions", []),
            cap.get("id", "CAP"),
            "capability",
        )
    for pol in policies:
        _append_decisions(pol.get("decisions", []), pol.get("id", "POL"), "policy")

    # 3. Multi-Design Systems, Tokens, UI Components, Clients, Flows, and Screens
    design_data = _extract_design_systems(eidos_path, specs_by_type, capabilities, _resolve_ref_id)
    design_systems = design_data.get("systems", [])
    screens = design_data.get("screens", [])
    ui_components = design_data.get("ui_components", [])
    clients = design_data.get("clients", [])
    flows = design_data.get("flows", [])

    for uic in ui_components:
        uic_id = uic.get("id", "UIC")
        for inv in uic.get("invariants", []) or []:
            if not isinstance(inv, dict):
                continue
            iid = inv.get("id")
            if iid and iid in seen_inv_ids:
                continue
            if iid:
                seen_inv_ids.add(iid)
            invariants.append(
                {
                    "id": iid,
                    "title": iid,
                    "rule": inv.get("rule", "UI component invariant."),
                    "category": "design",
                    "source_type": f"ui_component ({uic_id})",
                    "test_binding": inv.get("test_binding", "N/A"),
                }
            )

    # 4. SpecDeltas & Kanban status columns (dynamically computes delta affects and blast_radius from mutations[].target_* and graph)
    deltas_by_status: dict[str, list[dict[str, Any]]] = {
        "proposed": [],
        "in_review": [],
        "approved": [],
        "applied": [],
        "rejected": [],
    }

    comp_dependents: dict[str, list[str]] = {}
    cnt_dependents: dict[str, list[str]] = {}
    entity_invariants: dict[str, list[str]] = {}
    for uic in ui_components:
        uic_id = uic.get("id")
        if uic_id:
            uic_invs = [str(inv["id"]) for inv in uic.get("invariants", []) if isinstance(inv, dict) and inv.get("id")]
            if uic_invs:
                entity_invariants[uic_id] = uic_invs
    for cnt in components:
        cnt_id = cnt["id"]
        cnt_invs = [str(inv["id"]) for inv in cnt.get("invariants", []) if isinstance(inv, dict) and inv.get("id")]
        if cnt_invs:
            entity_invariants[cnt_id] = cnt_invs
        cnt_deps = cnt.get("dependencies", {})
        cnt_int_deps = cnt_deps.get("internal_containers", []) if isinstance(cnt_deps, dict) else []
        for dep_ref in cnt_int_deps or []:
            dep_id = _resolve_ref_id(dep_ref)
            if dep_id:
                _add_unique(cnt_dependents.setdefault(dep_id, []), cnt_id)
        for ic in cnt.get("internal_components", []):
            ic_id = ic.get("id")
            if ic_id:
                ic_invs = [
                    str(inv["id"]) for inv in ic.get("invariants", []) if isinstance(inv, dict) and inv.get("id")
                ]
                if ic_invs:
                    entity_invariants[ic_id] = ic_invs
                for dep_ref in ic.get("consumes_components", []) or []:
                    dep_id = _resolve_ref_id(dep_ref)
                    if dep_id:
                        _add_unique(comp_dependents.setdefault(dep_id, []), ic_id)

    contract_to_container: dict[str, str] = {
        ct["id"]: ct["container"]
        for ct in contracts
        if ct.get("id") and ct.get("container") and str(ct["container"]).startswith("CNT-")
    }
    cap_by_id: dict[str, dict[str, Any]] = {}
    entity_to_caps: dict[str, list[str]] = {}
    for cap in capabilities:
        cid = cap.get("id")
        if cid:
            cap_by_id[cid] = cap
            tech = cap.get("technical", {}) or {}
            c_invs = [str(inv["id"]) for inv in tech.get("invariants", []) if isinstance(inv, dict) and inv.get("id")]
            if c_invs:
                entity_invariants[cid] = c_invs
            raw_ctrs = tech.get("contracts", [])
            ctr_refs = raw_ctrs if isinstance(raw_ctrs, list) else []
            for ref in (tech.get("containers", []) or []) + (tech.get("components", []) or []) + ctr_refs:
                ref_id = _resolve_ref_id(ref)
                if ref_id:
                    _add_unique(entity_to_caps.setdefault(ref_id, []), cid)

    for pol in policies:
        pid = pol.get("id")
        if pid:
            p_invs = [str(inv["id"]) for inv in pol.get("invariants", []) if isinstance(inv, dict) and inv.get("id")]
            entity_invariants[pid] = p_invs or [pid]

    inv_to_owners: dict[str, list[str]] = {}
    for owner_id, inv_ids in entity_invariants.items():
        for inv_id in inv_ids:
            if inv_id != owner_id:
                _add_unique(inv_to_owners.setdefault(inv_id, []), owner_id)

    client_by_id: dict[str, dict[str, Any]] = {cl["id"]: cl for cl in clients if cl.get("id")}
    container_to_clients: dict[str, list[dict[str, Any]]] = {}
    for cl in clients:
        cnt_ref = cl.get("container")
        if isinstance(cnt_ref, str) and cnt_ref:
            container_to_clients.setdefault(cnt_ref, []).append(cl)

    from eidos.core.lifecycle.delta_engine import normalize_delta_mutation, resolve_entity_prefix_meta

    raw_deltas = specs_by_type.get("delta/mutation", [])
    for d in raw_deltas:
        fm = d["frontmatter"]
        status = str(fm.get("status", "proposed")).lower()
        raw_muts = fm.get("mutations", []) or []
        muts = [normalize_delta_mutation(m) if isinstance(m, dict) else m for m in raw_muts]

        aff_lists: dict[str, list[str]] = {
            "containers": [],
            "components": [],
            "contracts": [],
            "capabilities": [],
            "domains": [],
            "policies": [],
            "personas": [],
            "design_systems": [],
            "screens": [],
            "ui_components": [],
            "clients": [],
            "flows": [],
        }
        br_lists: dict[str, list[str]] = {
            "impacted_containers": [],
            "impacted_components": [],
            "impacted_design_systems": [],
            "impacted_screens": [],
            "impacted_personas": [],
            "invariants_to_enforce": [],
        }

        kind_to_cat = {
            "container": "containers",
            "component": "components",
            "contract": "contracts",
            "capability": "capabilities",
            "domain": "domains",
            "policy": "policies",
            "persona": "personas",
            "design_system": "design_systems",
            "screen": "screens",
            "ui_component": "ui_components",
            "client": "clients",
            "flow": "flows",
        }
        seed_entities: list[str] = []
        for m in muts:
            if not isinstance(m, dict):
                continue
            for ref_field in ("target", "parent"):
                raw_t = m.get(ref_field)
                if not raw_t or not isinstance(raw_t, str) or not raw_t.strip():
                    continue
                resolved_id = _resolve_ref_id(raw_t.strip())
                p_meta = resolve_entity_prefix_meta(resolved_id)
                if p_meta is None:
                    continue
                entity_kind = p_meta[1]
                if entity_kind == "invariant":
                    if ref_field == "target":
                        _add_unique(br_lists["invariants_to_enforce"], resolved_id)
                        for owner_id in inv_to_owners.get(resolved_id, []):
                            o_meta = resolve_entity_prefix_meta(owner_id)
                            o_cat = kind_to_cat.get(o_meta[1]) if o_meta else None
                            if o_cat:
                                _add_unique(aff_lists[o_cat], owner_id)
                                _add_unique(seed_entities, owner_id)
                                if o_cat == "components" and owner_id in comp_to_container:
                                    _add_unique(aff_lists["containers"], comp_to_container[owner_id])
                                elif o_cat == "capabilities" and owner_id in cap_by_id:
                                    dom_id = cap_by_id[owner_id].get("domain")
                                    if isinstance(dom_id, str) and dom_id:
                                        _add_unique(aff_lists["domains"], _resolve_ref_id(dom_id))
                    continue
                cat = kind_to_cat.get(entity_kind)
                if not cat:
                    continue
                _add_unique(aff_lists[cat], resolved_id)
                _add_unique(seed_entities, resolved_id)
                if cat == "components" and resolved_id in comp_to_container:
                    _add_unique(aff_lists["containers"], comp_to_container[resolved_id])
                elif cat == "contracts" and resolved_id in contract_to_container:
                    _add_unique(aff_lists["containers"], contract_to_container[resolved_id])
                elif cat == "capabilities" and resolved_id in cap_by_id:
                    dom_id = cap_by_id[resolved_id].get("domain")
                    if isinstance(dom_id, str) and dom_id:
                        _add_unique(aff_lists["domains"], _resolve_ref_id(dom_id))

        # Preserve any explicit affects/blast_radius fields on legacy or synthetic fixtures
        raw_aff = fm.get("affects")
        if isinstance(raw_aff, dict):
            for k, vals in raw_aff.items():
                if isinstance(vals, list):
                    for v in vals:
                        _add_unique(aff_lists.setdefault(k, []), _resolve_ref_id(v))

        for cnt_id in aff_lists["containers"]:
            _add_unique(br_lists["impacted_containers"], cnt_id)
            for dep_cnt in cnt_dependents.get(cnt_id, []):
                _add_unique(br_lists["impacted_containers"], dep_cnt)
            for cl in container_to_clients.get(cnt_id, []):
                if cl.get("design_system"):
                    _add_unique(br_lists["impacted_design_systems"], str(cl["design_system"]))
                for scr_ref in cl.get("screens", []) or []:
                    _add_unique(br_lists["impacted_screens"], _resolve_ref_id(scr_ref))

        for cmp_id in aff_lists["components"]:
            _add_unique(br_lists["impacted_components"], cmp_id)
            for dep_cmp in comp_dependents.get(cmp_id, []):
                _add_unique(br_lists["impacted_components"], dep_cmp)
                if dep_cmp in comp_to_container:
                    _add_unique(br_lists["impacted_containers"], comp_to_container[dep_cmp])
            if cmp_id in comp_to_container:
                _add_unique(br_lists["impacted_containers"], comp_to_container[cmp_id])

        for ds_id in aff_lists["design_systems"]:
            _add_unique(br_lists["impacted_design_systems"], ds_id)
        for scr_id in aff_lists["screens"]:
            _add_unique(br_lists["impacted_screens"], scr_id)
        for per_id in aff_lists["personas"]:
            _add_unique(br_lists["impacted_personas"], per_id)
        for cl_id in aff_lists["clients"]:
            cl_obj = client_by_id.get(cl_id)
            if cl_obj:
                if cl_obj.get("container"):
                    _add_unique(br_lists["impacted_containers"], str(cl_obj["container"]))
                if cl_obj.get("design_system"):
                    _add_unique(br_lists["impacted_design_systems"], str(cl_obj["design_system"]))
                for scr_ref in cl_obj.get("screens", []) or []:
                    _add_unique(br_lists["impacted_screens"], _resolve_ref_id(scr_ref))

        related_caps = list(aff_lists["capabilities"])
        for ent_id in seed_entities + aff_lists["containers"] + aff_lists["components"]:
            for cid in entity_to_caps.get(ent_id, []):
                _add_unique(related_caps, cid)

        for cap_id in related_caps:
            cap_obj = cap_by_id.get(cap_id)
            if not cap_obj:
                continue
            tech = cap_obj.get("technical", {}) or {}
            if cap_id in aff_lists["capabilities"]:
                for cnt_ref in tech.get("containers", []) or []:
                    _add_unique(br_lists["impacted_containers"], _resolve_ref_id(cnt_ref))
                for cmp_ref in tech.get("components", []) or []:
                    _add_unique(br_lists["impacted_components"], _resolve_ref_id(cmp_ref))
            bus = cap_obj.get("business", {}) or {}
            for per_ref in bus.get("personas", []) or []:
                _add_unique(br_lists["impacted_personas"], _resolve_ref_id(per_ref))
            exp = cap_obj.get("experience", {}) or {}
            for scr_ref in exp.get("screens", []) or []:
                _add_unique(br_lists["impacted_screens"], _resolve_ref_id(scr_ref))

        for seed_id in seed_entities + aff_lists["containers"] + br_lists["impacted_components"]:
            for inv_id in entity_invariants.get(seed_id, []):
                _add_unique(br_lists["invariants_to_enforce"], inv_id)

        raw_br = fm.get("blast_radius")
        if isinstance(raw_br, dict):
            for k, vals in raw_br.items():
                if isinstance(vals, list):
                    for v in vals:
                        _add_unique(br_lists.setdefault(k, []), _resolve_ref_id(v))

        derived_affects = {k: v for k, v in aff_lists.items() if v}
        derived_blast_radius = {k: v for k, v in br_lists.items() if v}

        delta_item = {
            "id": d["id"],
            "title": d["title"],
            "status": status,
            "kind": fm.get("intent", {}).get("kind", "feature_addition"),
            "motivation": fm.get("intent", {}).get("motivation", ""),
            "mutations": muts,
            "affects": derived_affects,
            "blast_radius": derived_blast_radius,
            "vcs": fm.get("vcs"),
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

        hypothesis = fm.get("hypothesis", "")
        if not hypothesis:
            m_hyp = re.search(
                r"(?:\*Hypothesis:\*|^==+\s+[^\n]*Hypothesis[^\n]*)\s*\n+([^=\n][^\n]+)",
                exp.get("body", ""),
                re.MULTILINE,
            )
            if m_hyp:
                hypothesis = " ".join(m_hyp.group(1).split())

        findings = fm.get("findings") or decision_dict.get("learnings") or ""
        if not findings:
            m_find = re.search(
                r"(?:\*Findings:\*|^==+\s+[^\n]*Findings[^\n]*)\s*\n+\*?\s*([^\n]+)",
                exp.get("body", ""),
                re.MULTILINE,
            )
            if m_find:
                findings = " ".join(m_find.group(1).split())

        recommendation = fm.get("recommendation") or decision_dict.get("recommendation") or ""
        outcome = (
            decision_dict.get("outcome")
            or fm.get("outcome")
            or (fm.get("status") if fm.get("status") in ("adopted", "discarded", "pivoted") else "")
            or recommendation
        )

        raw_res_deltas = fm.get("resulting_deltas") or decision_dict.get("resulting_deltas") or []
        if isinstance(raw_res_deltas, str):
            resulting_deltas = [raw_res_deltas]
        elif isinstance(raw_res_deltas, list):
            resulting_deltas = [str(d) for d in raw_res_deltas if d]
        else:
            resulting_deltas = []

        if outcome:
            decision_dict["outcome"] = outcome
        if findings:
            decision_dict.setdefault("learnings", findings)
            decision_dict["findings"] = findings
        if recommendation:
            decision_dict["recommendation"] = recommendation
        if resulting_deltas:
            decision_dict["resulting_deltas"] = resulting_deltas

        explorations.append(
            {
                "id": exp["id"],
                "title": exp["title"],
                "status": fm.get("status", "proposed"),
                "hypothesis": hypothesis,
                "exit_criteria": fm.get("exit_criteria", {}),
                "findings": findings,
                "recommendation": recommendation,
                "resulting_deltas": resulting_deltas,
                "decision": decision_dict,
                "body": exp["body"],
                "path": exp.get("path", ""),
            }
        )

    # 6. Dynamic Mermaid Diagrams
    ext_systems = (
        (arch_doc.get("frontmatter", {}).get("c4_context", {}).get("external_systems", []) if arch_doc else [])
        or (sys_doc.get("frontmatter", {}).get("c4_context", {}).get("external_systems", []) if sys_doc else [])
        or []
    )
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
    capabilities: list[dict[str, Any]] | None = None,
    resolve_ref: Any = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan and parse multi-design systems, W3C tokens.json, UI components, clients, flows, and screens."""

    def _res(val: Any) -> str:
        if resolve_ref is not None:
            return str(resolve_ref(val))
        return str(val or "").strip()

    def _add_uniq(lst: list[str], item: str) -> None:
        if item and item not in lst:
            lst.append(item)

    # Reverse-map capabilities -> clients, screens, flows
    clt_to_caps: dict[str, list[str]] = {}
    scr_to_caps: dict[str, list[str]] = {}
    flw_to_caps: dict[str, list[str]] = {}
    for cap in capabilities or []:
        cid = str(cap.get("id") or "").strip()
        if not cid:
            continue
        exp = cap.get("experience") if isinstance(cap.get("experience"), dict) else {}
        for ref in exp.get("clients", []) or []:
            _add_uniq(clt_to_caps.setdefault(_res(ref), []), cid)
        for ref in exp.get("screens", []) or []:
            _add_uniq(scr_to_caps.setdefault(_res(ref), []), cid)
        for ref in exp.get("flows", []) or []:
            _add_uniq(flw_to_caps.setdefault(_res(ref), []), cid)

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
                tokens_data = json.loads(tokens_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                tokens_data = {}

        systems.append(
            {
                "id": sys_id,
                "title": ds["title"],
                "client": "core",
                "brand_name": fm.get("brand_name", ds["title"]),
                "extends": fm.get("extends"),
                "tokens": tokens_data,
            }
        )

    default_ds_id = systems[0]["id"] if systems else "DS-001"

    # Clients (and reverse-map client -> screens, flows, design_system)
    scr_to_client: dict[str, str] = {}
    scr_to_ds: dict[str, str] = {}
    flw_to_clients: dict[str, list[str]] = {}
    ds_to_client: dict[str, str] = {}

    clients: list[dict[str, Any]] = []
    for clt in specs_by_type.get("design/client", []):
        fm = clt["frontmatter"]
        clt_id = clt["id"]
        ds_ref = _res(fm.get("design_system", default_ds_id)) or default_ds_id
        ds_to_client.setdefault(ds_ref, clt_id)
        raw_scrs = fm.get("screens", []) or []
        raw_flws = fm.get("flows", []) or []
        for s_ref in raw_scrs:
            s_id = _res(s_ref)
            if s_id:
                scr_to_client.setdefault(s_id, clt_id)
                scr_to_ds.setdefault(s_id, ds_ref)
        for f_ref in raw_flws:
            f_id = _res(f_ref)
            if f_id:
                _add_uniq(flw_to_clients.setdefault(f_id, []), clt_id)

        clients.append(
            {
                "id": clt_id,
                "title": clt["title"],
                "platform": fm.get("platform", "web"),
                "design_system": ds_ref,
                "container": _res(fm.get("container", "N/A")) or "N/A",
                "capabilities": clt_to_caps.get(clt_id, []),
                "screens": raw_scrs,
                "flows": raw_flws,
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
        for step in steps or []:
            if isinstance(step, dict) and step.get("client"):
                _add_uniq(derived_clients, _res(step["client"]))
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
                "design_system": fm.get("design_system", default_ds_id),
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
        screens.append(
            {
                "id": scr_id,
                "title": scr["title"],
                "route": fm.get("route", "/"),
                "client": scr_to_client.get(scr_id, "N/A"),
                "system_ref": scr_to_ds.get(scr_id, default_ds_id),
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
        if isinstance(ext, dict):
            raw_ext_id = str(ext.get("id") or "").strip()
            ext_node_id = re.sub(r"[^A-Za-z0-9_]", "_", raw_ext_id) if raw_ext_id else f"EXT_{idx + 1}"
            ext_name = html.escape(
                str(ext.get("title") or ext.get("name") or ext.get("id") or f"External System {idx + 1}")
            ).replace('"', "'")
            ext_role = html.escape(
                str(ext.get("description") or ext.get("role") or ext.get("relationship") or "External Integration")
            ).replace('"', "'")
            ext_proto = html.escape(str(ext.get("protocol") or ext.get("type") or "External System")).replace('"', "'")
        else:
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
        ext_list = (
            (deps.get("external_service_links") or deps.get("external_services", [])) if isinstance(deps, dict) else []
        )

        for ext in ext_list:
            if isinstance(ext, dict):
                raw_name = str(ext.get("title") or ext.get("name") or ext.get("service") or "").strip()
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
        int_deps = (
            (deps.get("internal_container_links") or deps.get("internal_containers", []))
            if isinstance(deps, dict)
            else []
        )
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
    int_deps = (
        (deps.get("internal_containers") or deps.get("internal_components") or []) if isinstance(deps, dict) else []
    )
    for raw_dep in int_deps:
        dep_id = (
            (
                str(raw_dep.get("id") or raw_dep.get("container") or raw_dep.get("target") or "")
                if isinstance(raw_dep, dict)
                else str(raw_dep)
            )
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
            (
                str(raw_ctr.get("id") or raw_ctr.get("contract") or raw_ctr.get("target") or "")
                if isinstance(raw_ctr, dict)
                else str(raw_ctr)
            )
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
        trigger = str(t.get("action") or t.get("event") or "action").replace("\n", " ")
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
