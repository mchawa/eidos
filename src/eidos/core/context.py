"""Architectural Subgraph Context Slicing for AI Agents and Developers.

Extracts a compact, token-efficient architectural slice (C4 components, bound
contracts, domain capabilities, active invariants, and governing policies) given
target file paths, a component ID, or a task description.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.parser import parse_spec_file

STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "with",
}


@dataclass
class ArchitecturalContextSlice:
    """Compact architectural subgraph slice containing relevant entities and invariants."""

    query: dict[str, Any] = field(default_factory=dict)
    components: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)
    matched_reasons: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert slice to dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialize slice to JSON."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Render a clean, token-efficient Markdown representation for LLMs."""
        lines = [
            "# 🧭 Eidos Architectural Context Slice",
            "",
        ]

        query_parts = []
        if self.query.get("files"):
            query_parts.append(f"**Files:** `{', '.join(self.query['files'])}`")
        if self.query.get("component"):
            query_parts.append(f"**Component:** `{self.query['component']}`")
        if self.query.get("task"):
            query_parts.append(f"**Task:** *\"{self.query['task']}\"*")

        if query_parts:
            lines.append(" | ".join(query_parts))
            lines.append("")

        # 1. Components
        lines.append(f"## 🧱 C4 Level 2/3 Components ({len(self.components)})")
        if not self.components:
            lines.append("*No components directly matched.*")
        for comp in self.components:
            cid = comp.get("id", "")
            name = comp.get("name", cid)
            ctype = comp.get("container_type", "module")
            runtime = comp.get("runtime", "N/A")
            pkg = comp.get("package_path", "")
            lines.append(f"### `{cid}` ({name})")
            lines.append(f"- **Container:** `{ctype}` | **Runtime:** `{runtime}` | **Package:** `{pkg}`")
            if comp.get("domain_binding"):
                lines.append(f"- **Domain:** `{comp['domain_binding']}`")
            deps = (comp.get("dependencies") or {}).get("internal_components", []) if isinstance(comp.get("dependencies"), dict) else []
            if deps:
                lines.append(f"- **Internal Dependencies:** {', '.join(f'`{d}`' for d in deps)}")
            contracts = comp.get("contracts") or {}
            prov = contracts.get("provides", []) if isinstance(contracts, dict) else []
            if prov:
                lines.append(f"- **Provides Contracts:** {', '.join(f'`{p}`' for p in prov)}")
            req = (contracts.get("requires") or contracts.get("consumes") or []) if isinstance(contracts, dict) else []
            if req:
                lines.append(f"- **Requires Contracts:** {', '.join(f'`{r}`' for r in req)}")
            modules = comp.get("c4_internal_components", [])
            if modules:
                lines.append("- **Internal Modules:**")
                for m in modules[:4]:
                    mname = m.get("name", "")
                    mresp = m.get("responsibility", "")
                    lines.append(f"  - `{mname}`: {mresp}")
            lines.append("")

        # 2. Contracts
        lines.append(f"## 📜 Bound Interface Contracts ({len(self.contracts)})")
        if not self.contracts:
            lines.append("*No bound contracts in slice.*")
        for ctr in self.contracts:
            cid = ctr.get("id", "")
            title = ctr.get("title", cid)
            protocol = ctr.get("protocol", "standard")
            owner = ctr.get("owning_component", "N/A")
            path = ctr.get("path", "")
            lines.append(f"### `{cid}`: {title}")
            lines.append(f"- **Protocol:** `{protocol}` | **Owner:** `{owner}` | **Path:** `{path}`")
            lines.append("")

        # 3. Domain Capabilities
        lines.append(f"## 🎯 Domain Capabilities ({len(self.capabilities)})")
        if not self.capabilities:
            lines.append("*No capabilities directly matched.*")
        for cap in self.capabilities:
            cid = cap.get("id", "")
            title = cap.get("title", cid)
            domain = cap.get("domain", "")
            jtbd = cap.get("jtbd", "")
            lines.append(f"### `{cid}`: {title}")
            lines.append(f"- **Domain:** `{domain}` | **Status:** `{cap.get('status', 'active')}`")
            if jtbd:
                lines.append(f"- **JTBD:** *\"{jtbd}\"*")
            lines.append("")

        # 4. Invariants
        lines.append(f"## 🔒 Active Invariants & Test Bindings ({len(self.invariants)})")
        if not self.invariants:
            lines.append("*No active invariants linked to this slice.*")
        for inv in self.invariants:
            iid = inv.get("id", "")
            rule = inv.get("rule", "")
            tb = inv.get("test_binding", "None")
            cov = "✓ Covered" if inv.get("has_test_coverage") else "⚠️ Uncovered"
            lines.append(f"- **`{iid}`** [{cov}]: {rule}")
            if tb and tb != "None":
                lines.append(f"  - *Binding:* `{tb}`")
            elif inv.get("matching_tests"):
                tests_str = ", ".join(f"`{t}`" for t in inv["matching_tests"][:2])
                lines.append(f"  - *Discovered in:* {tests_str}")
        lines.append("")

        # 5. Policies
        lines.append(f"## 📋 Governing Policies ({len(self.policies)})")
        if not self.policies:
            lines.append("*No specific governing policies in slice.*")
        for pol in self.policies:
            pid = pol.get("id", "")
            title = pol.get("title", pid)
            rule = pol.get("rule", "")
            lines.append(f"- **`{pid}`** ({title})")
            if rule:
                lines.append(f"  - *Rule:* {rule}")
        lines.append("")

        return "\n".join(lines)


def _tokenize_text(text: str) -> set[str]:
    """Extract lowercased semantic keywords from a text string."""
    words = re.findall(r"[A-Za-z0-9_-]{3,}", text.lower())
    return {w for w in words if w not in STOP_WORDS}


def get_context_slice(
    files: list[str] | str | None = None,
    component: str | None = None,
    task: str | None = None,
    eidos_dir: Path | None = None,
    repo_root: Path | None = None,
    tier: str = "guardrails",
) -> ArchitecturalContextSlice:
    """Extract an architectural subgraph context slice tailored for an AI agent's current task.

    Args:
        files: Optional list or string of source file paths touched or being worked on.
        component: Optional component ID (e.g. 'CMP-EIDOS-CLI') or name.
        task: Optional free-text task or feature prompt.
        eidos_dir: Path to the eidos/ living specification catalog.
        repo_root: Path to the repository root.
        tier: Context slice verbosity tier ('anchor', 'guardrails', 'deep'). Defaults to 'guardrails'.

    Returns:
        ArchitecturalContextSlice with matched components, contracts,
        capabilities, invariants, and policies.
    """
    from eidos.vcs.drift import extract_invariants_and_verify

    root = (repo_root or Path.cwd()).resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    # Collect all specifications
    components_by_id: dict[str, dict[str, Any]] = {}
    contracts_by_id: dict[str, dict[str, Any]] = {}
    capabilities_by_id: dict[str, dict[str, Any]] = {}
    policies_by_id: dict[str, dict[str, Any]] = {}

    if target_eidos.exists():
        for p in target_eidos.rglob("*.adoc"):
            if "by-status" in p.parts or p.name.startswith("_") or p.name.lower() == "readme.adoc":
                continue
            try:
                doc = parse_spec_file(p)
                fm = doc.frontmatter or {}
                doc_id = doc.doc_id or p.stem
                rel_path = str(p.relative_to(root)) if p.is_relative_to(root) else str(p.relative_to(target_eidos))

                deps = fm.get("dependencies") if isinstance(fm.get("dependencies"), dict) else {}
                contracts = fm.get("contracts") if isinstance(fm.get("contracts"), dict) else {}
                c4_internals = fm.get("c4_internal_components") if isinstance(fm.get("c4_internal_components"), list) else []
                biz = fm.get("business") if isinstance(fm.get("business"), dict) else {}
                tech = fm.get("technical") if isinstance(fm.get("technical"), dict) else {}

                if doc.doc_type == "tech/component":
                    components_by_id[doc_id] = {
                        "id": doc_id,
                        "name": fm.get("name", p.stem),
                        "container_type": fm.get("container_type", "service"),
                        "runtime": fm.get("runtime", ""),
                        "package_path": fm.get("package_path", ""),
                        "domain_binding": fm.get("domain_binding", ""),
                        "dependencies": deps,
                        "contracts": contracts,
                        "c4_internal_components": c4_internals,
                        "path": rel_path,
                    }
                elif doc.doc_type == "tech/contract":
                    contracts_by_id[doc_id] = {
                        "id": doc_id,
                        "title": doc.title or fm.get("title", doc_id),
                        "protocol": fm.get("protocol", "standard"),
                        "owning_component": fm.get("owning_component", ""),
                        "path": rel_path,
                    }
                elif doc.doc_type == "product/capability":
                    capabilities_by_id[doc_id] = {
                        "id": doc_id,
                        "title": doc.title or fm.get("title", doc_id),
                        "domain": fm.get("domain", ""),
                        "status": fm.get("status", "active"),
                        "jtbd": biz.get("jtbd", ""),
                        "policies": biz.get("policies", []) or [],
                        "components": tech.get("components", []) or [],
                        "invariants": tech.get("invariants", []) or [],
                        "path": rel_path,
                    }
                elif doc.doc_type == "product/policy":
                    policies_by_id[doc_id] = {
                        "id": doc_id,
                        "title": doc.title or fm.get("title", doc_id),
                        "category": fm.get("category", "governance"),
                        "rule": fm.get("rule", ""),
                        "invariants": fm.get("invariants", []) or tech.get("invariants", []) or [],
                        "path": rel_path,
                    }
            except Exception:
                continue

    # Resolution structures
    selected_comp_ids: set[str] = set()
    matched_reasons: dict[str, list[str]] = {}

    normalized_files: list[str] = []
    raw_files = [files] if isinstance(files, str) else (files or [])

    for f in raw_files:
        if not f:
            continue
        for sub_f in str(f).split(","):
            sub_clean = sub_f.strip()
            if not sub_clean:
                continue
            p = Path(sub_clean)
            if p.is_absolute():
                try:
                    rel_p = p.resolve().relative_to(root)
                    normalized_files.append(str(rel_p).replace("\\", "/"))
                except ValueError:
                    normalized_files.append(sub_clean.replace("\\", "/"))
            else:
                clean_rel = sub_clean.replace("\\", "/")
                while clean_rel.startswith("./"):
                    clean_rel = clean_rel[2:]
                normalized_files.append(clean_rel)

    # 1. Match by component ID / name
    if component:
        comp_query = component.strip().upper()
        # Direct or suffix match
        for cid, comp_data in components_by_id.items():
            if (
                cid == comp_query
                or cid.endswith(comp_query)
                or comp_query in cid
                or comp_query.lower() in comp_data.get("name", "").lower()
            ):
                selected_comp_ids.add(cid)
                matched_reasons.setdefault(cid, []).append(f"Explicit component parameter '{component}'")

    # 2. Match by file paths
    for f_path in normalized_files:
        f_norm = f_path.replace("\\", "/").lower()
        parts = set(re.split(r"[/._-]", f_norm))
        # If f_path is still absolute (outside root), only consider local parts
        if Path(f_path).is_absolute():
            parts = set(re.split(r"[/._-]", Path(f_path).name.lower()))
            for p_dir in Path(f_path).parents[:2]:
                parts.update(re.split(r"[/._-]", p_dir.name.lower()))

        for cid, comp_data in components_by_id.items():
            pkg = comp_data.get("package_path", "").replace("\\", "/").strip("./").lower()
            if (
                pkg
                and (f_norm == pkg or f_norm.startswith(pkg + "/") or f"/{pkg}/" in f"/{f_norm}/")
                and (not Path(f_path).is_absolute() or pkg in parts)
            ):
                selected_comp_ids.add(cid)
                matched_reasons.setdefault(cid, []).append(f"File '{f_path}' inside package '{pkg}'")
            # Check internal module source paths
            for mod in comp_data.get("c4_internal_components", []):
                src = mod.get("source_path", "").replace("\\", "/").lower()
                while src.startswith("./"):
                    src = src[2:]
                if src and (src == f_norm or f_norm.endswith("/" + src) or src.endswith("/" + f_norm)):
                    selected_comp_ids.add(cid)
                    matched_reasons.setdefault(cid, []).append(f"File '{f_path}' matches module '{mod.get('name')}'")
            # Slug heuristic with word boundary tokens to prevent false positives (e.g. 'cli' in 'client')
            slug = cid.lower().replace("cmp-eidos-", "").replace("cmp-", "")
            if slug and slug in parts:
                selected_comp_ids.add(cid)
                matched_reasons.setdefault(cid, []).append(f"File '{f_path}' matches slug '{slug}'")

    # 3. Match by task description
    task_keywords = _tokenize_text(task) if task else set()
    selected_cap_ids: set[str] = set()

    if task_keywords:
        for cid, comp_data in components_by_id.items():
            comp_text = f"{cid} {comp_data.get('name', '')} {comp_data.get('package_path', '')} " + " ".join(
                f"{m.get('name', '')} {m.get('responsibility', '')}"
                for m in comp_data.get("c4_internal_components", [])
            )
            comp_kw = _tokenize_text(comp_text)
            overlap = task_keywords & comp_kw
            if len(overlap) >= 1:
                selected_comp_ids.add(cid)
                matched_reasons.setdefault(cid, []).append(f"Task keywords matched: {', '.join(sorted(overlap))}")

        for cap_id, cap_data in capabilities_by_id.items():
            cap_text = f"{cap_id} {cap_data.get('title', '')} {cap_data.get('jtbd', '')} {cap_data.get('domain', '')}"
            cap_kw = _tokenize_text(cap_text)
            overlap = task_keywords & cap_kw
            if len(overlap) >= 1:
                selected_cap_ids.add(cap_id)
                matched_reasons.setdefault(cap_id, []).append(f"Task keywords matched: {', '.join(sorted(overlap))}")

    # Fallback if no specific filter provided or matched: select core components
    if not selected_comp_ids and not selected_cap_ids and not normalized_files and not component and not task:
        # Default full baseline summary
        selected_comp_ids.update(components_by_id.keys())
    elif not selected_comp_ids and not selected_cap_ids:
        # User specified filters but nothing matched: include baseline anchors
        for fallback_id in ["CMP-EIDOS-CLI", "CMP-EIDOS-CORE"]:
            if fallback_id in components_by_id:
                selected_comp_ids.add(fallback_id)
                matched_reasons.setdefault(fallback_id, []).append("Fallback baseline component anchor")
        if not selected_comp_ids and components_by_id:
            for fallback_id in list(components_by_id.keys())[:2]:
                selected_comp_ids.add(fallback_id)
                matched_reasons.setdefault(fallback_id, []).append("Fallback baseline component anchor")

    # 4. Dependency graph expansion (1-hop internal dependencies)
    expanded_comps = set(selected_comp_ids)
    for cid in selected_comp_ids:
        comp_data = components_by_id.get(cid)
        if comp_data:
            internal_deps = (comp_data.get("dependencies") or {}).get("internal_components", [])
            for dep_id in internal_deps:
                if dep_id in components_by_id and dep_id not in expanded_comps:
                    expanded_comps.add(dep_id)
                    matched_reasons.setdefault(dep_id, []).append(f"Dependency of '{cid}'")
    selected_comp_ids = expanded_comps

    # 5. Bound contracts expansion
    selected_contracts: list[dict[str, Any]] = []
    contract_ids_seen: set[str] = set()

    for cid in selected_comp_ids:
        comp_data = components_by_id.get(cid, {})
        c_spec = comp_data.get("contracts") or {}
        prov = c_spec.get("provides", []) or []
        req = (c_spec.get("requires") or []) + (c_spec.get("consumes") or [])

        for ref in prov + req:
            # ref can be an ID (CTR-*) or a file path
            for ctr_id, ctr_data in contracts_by_id.items():
                if (ctr_id == ref or ref in ctr_data.get("path", "")) and ctr_id not in contract_ids_seen:
                    contract_ids_seen.add(ctr_id)
                    selected_contracts.append(ctr_data)

    # Also include contracts owned by selected components
    for ctr_id, ctr_data in contracts_by_id.items():
        if ctr_data.get("owning_component") in selected_comp_ids and ctr_id not in contract_ids_seen:
            contract_ids_seen.add(ctr_id)
            selected_contracts.append(ctr_data)

    # 6. Domain Capabilities expansion
    bound_domains = {components_by_id[cid].get("domain_binding") for cid in selected_comp_ids if cid in components_by_id}
    bound_domains.discard("")

    for cap_id, cap_data in capabilities_by_id.items():
        cap_domain = cap_data.get("domain")
        # Match by domain binding
        if cap_domain and cap_domain in bound_domains:
            selected_cap_ids.add(cap_id)
        # Match by technical component package
        cap_tech_comps = cap_data.get("components", []) or []
        for tc in cap_tech_comps:
            if not tc or not isinstance(tc, str):
                continue
            for sc in selected_comp_ids:
                sc_pkg = components_by_id.get(sc, {}).get("package_path", "")
                if tc == sc or (sc_pkg and (tc == sc_pkg or sc_pkg.startswith(tc) or tc.startswith(sc_pkg))):
                    selected_cap_ids.add(cap_id)

    # 7. Extract Invariants with test verification
    all_invariants = extract_invariants_and_verify(target_eidos, root)
    inv_traces_by_id = {inv.invariant_id: inv for inv in all_invariants}

    sliced_invariants: list[dict[str, Any]] = []
    invariants_seen: set[str] = set()

    for cap_id in selected_cap_ids:
        cap_data = capabilities_by_id.get(cap_id, {})
        for inv_spec in cap_data.get("invariants", []) or []:
            if not isinstance(inv_spec, dict):
                continue
            iid = inv_spec.get("id")
            if not iid or iid in invariants_seen:
                continue
            invariants_seen.add(iid)
            trace = inv_traces_by_id.get(iid)
            sliced_invariants.append(
                {
                    "id": iid,
                    "rule": inv_spec.get("rule", ""),
                    "test_binding": inv_spec.get("test_binding"),
                    "source_id": cap_id,
                    "capability_id": cap_id,
                    "has_test_coverage": trace.has_test_coverage if trace else False,
                    "matching_tests": trace.matching_tests if trace else [],
                }
            )

    # 8. Governing Policies expansion
    selected_policies: list[dict[str, Any]] = []
    policy_ids_seen: set[str] = set()

    for cap_id in selected_cap_ids:
        cap_data = capabilities_by_id.get(cap_id, {})
        for pol_ref in cap_data.get("policies", []) or []:
            for pid, pdata in policies_by_id.items():
                if (pid == pol_ref or pol_ref in pdata.get("path", "")) and pid not in policy_ids_seen:
                    policy_ids_seen.add(pid)
                    selected_policies.append(pdata)

    # If no policies matched via capabilities, add core system policies (POL-001, POL-002)
    if not selected_policies:
        for pid in ["POL-001-GIT-SOURCE-OF-TRUTH", "POL-002-INVARIANT-TEST-ENFORCEMENT"]:
            if pid in policies_by_id and pid not in policy_ids_seen:
                policy_ids_seen.add(pid)
                selected_policies.append(policies_by_id[pid])
        if not selected_policies and policies_by_id:
            for pid in list(policies_by_id.keys())[:2]:
                if pid not in policy_ids_seen:
                    policy_ids_seen.add(pid)
                    selected_policies.append(policies_by_id[pid])

    # Also include invariants from selected governing policies
    for pol in selected_policies:
        pid = pol.get("id")
        for inv_spec in pol.get("invariants", []) or []:
            if not isinstance(inv_spec, dict):
                continue
            iid = inv_spec.get("id")
            if not iid or iid in invariants_seen:
                continue
            invariants_seen.add(iid)
            trace = inv_traces_by_id.get(iid)
            sliced_invariants.append(
                {
                    "id": iid,
                    "rule": inv_spec.get("rule", ""),
                    "test_binding": inv_spec.get("test_binding"),
                    "source_id": pid,
                    "capability_id": None,
                    "policy_id": pid,
                    "has_test_coverage": trace.has_test_coverage if trace else False,
                    "matching_tests": trace.matching_tests if trace else [],
                }
            )

    # Assemble and return slice
    sorted_comps = [components_by_id[cid] for cid in sorted(selected_comp_ids) if cid in components_by_id]
    sorted_caps = [capabilities_by_id[cid] for cid in sorted(selected_cap_ids) if cid in capabilities_by_id]

    return ArchitecturalContextSlice(
        query={
            "files": normalized_files,
            "component": component,
            "task": task,
        },
        components=sorted_comps,
        contracts=selected_contracts,
        capabilities=sorted_caps,
        invariants=sliced_invariants,
        policies=selected_policies,
        matched_reasons=matched_reasons,
    )
