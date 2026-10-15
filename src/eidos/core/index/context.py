"""Tiered Context Compressor (CMP-TIERED-CONTEXT-COMPRESSOR).

Extracts rank-ordered, token-budgeted architectural slices across anchor (<=300 tokens),
guardrails (<=1000 tokens), and deep (<=2500 tokens) tiers for AI coding agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from eidos.core.workspace.context import (
    ArchitecturalContextSlice,
    collect_component_decisions,
    format_decisions_markdown,
)

# Enforced maximum token budgets per context slice tier (DLT-2026-047).
TIER_TOKEN_BUDGETS: dict[str, int] = {
    "anchor": 300,
    "guardrails": 1000,
    "deep": 2500,
}


@dataclass
class TieredContextSlice(ArchitecturalContextSlice):
    """Token-budgeted architectural slice matching a query, extending ArchitecturalContextSlice."""

    tier: str = "guardrails"  # "anchor" | "guardrails" | "deep"
    query_latency_ms: float = 0.0
    token_estimate: int = 0

    def _is_secondary_dependency(self, cid: str) -> bool:
        return self.matched_reasons.get(cid) == ["Internal dependency"]

    def _project_decisions(self, decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for d in decisions:
            if not isinstance(d, dict):
                continue
            dtext = str(d.get("decision", "") or "")
            if self.tier == "anchor":
                short_dtext = f"{dtext[:117].rstrip()}..." if len(dtext) > 120 else dtext
                alts = [
                    {"option": str(a.get("option")).strip()}
                    for a in (d.get("rejected_alternatives") or [])[:2]
                    if isinstance(a, dict) and a.get("option")
                ]
                entry: dict[str, Any] = {
                    "id": d.get("id", ""),
                    "title": d.get("title", ""),
                    "decision": short_dtext,
                }
                if alts:
                    entry["rejected_alternatives"] = alts
                projected.append(entry)
            else:
                entry = {
                    "id": d.get("id", ""),
                    "title": d.get("title", ""),
                    "decision": dtext,
                }
                if d.get("graduated_from"):
                    entry["graduated_from"] = d["graduated_from"]
                if d.get("rationale"):
                    entry["rationale"] = d["rationale"]
                if d.get("rejected_alternatives"):
                    entry["rejected_alternatives"] = [
                        {
                            "option": a.get("option", ""),
                            "reason_rejected": a.get("reason_rejected", ""),
                        }
                        for a in (d.get("rejected_alternatives") or [])
                        if isinstance(a, dict) and a.get("option")
                    ]
                projected.append(entry)
        return projected

    def _project_component(self, comp: dict[str, Any]) -> dict[str, Any]:
        cid = comp.get("id", "")
        name = comp.get("name", comp.get("title", cid))
        title = comp.get("title", name)
        out: dict[str, Any] = {
            "id": cid,
            "name": name,
        }
        if title and title != name:
            out["title"] = title
        base_keys = (
            ("type", "container_type", "runtime", "package_path", "domain_binding", "container")
            if self.tier == "anchor"
            else ("type", "container_type", "runtime", "package_path", "domain_binding", "container", "file_path", "responsibility")
        )
        for base_key in base_keys:
            if comp.get(base_key):
                out[base_key] = comp[base_key]

        is_secondary = self._is_secondary_dependency(cid)
        if self.tier in ("guardrails", "deep") and not (is_secondary and len(self.components) > 3):
            if comp.get("dependencies"):
                out["dependencies"] = comp["dependencies"]
            if comp.get("contracts"):
                out["contracts"] = comp["contracts"]
            if comp.get("interfaces"):
                out["interfaces"] = comp["interfaces"]
            if comp.get("invariants"):
                out["invariants"] = [
                    {"id": inv.get("id", ""), "rule": inv.get("rule", "")}
                    for inv in comp.get("invariants", [])
                    if isinstance(inv, dict)
                ]

        if self.tier == "deep" and not is_secondary:
            if comp.get("quality_attributes"):
                out["quality_attributes"] = comp["quality_attributes"]
            modules = comp.get("components", []) or comp.get("c4_internal_components", [])
            if modules:
                out["components"] = [
                    {
                        k: m[k]
                        for k in ("id", "name", "responsibility", "ref", "source_path")
                        if isinstance(m, dict) and m.get(k)
                    }
                    for m in modules
                    if isinstance(m, dict)
                ]

        if not is_secondary and not (self.tier != "deep" and len(self.components) > 4):
            decisions = collect_component_decisions(comp)
            if decisions:
                out["decisions"] = self._project_decisions(decisions)

        return out

    def _project_contract(self, ctr: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": ctr.get("id", ""),
            "title": ctr.get("title", ctr.get("id", "")),
            "protocol": ctr.get("protocol", "standard"),
        }
        for k in ("owning_container", "owning_component"):
            if ctr.get(k):
                out[k] = ctr[k]
        if self.tier in ("guardrails", "deep") and ctr.get("path"):
            out["path"] = ctr["path"]
        return out

    def _project_capability(self, cap: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": cap.get("id", ""),
            "title": cap.get("title", cap.get("id", "")),
            "domain": cap.get("domain", ""),
            "status": cap.get("status", "active"),
        }
        if self.tier in ("guardrails", "deep") and cap.get("invariants"):
            out["invariants"] = [
                {"id": inv.get("id", ""), "rule": inv.get("rule", "")}
                for inv in cap.get("invariants", [])
                if isinstance(inv, dict)
            ]
        return out

    def _project_invariant(self, inv: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": inv.get("id", ""),
            "rule": inv.get("rule", ""),
            "has_test_coverage": bool(inv.get("has_test_coverage")),
        }
        tb = inv.get("test_binding")
        if tb and tb != "None":
            out["test_binding"] = tb
        if self.tier in ("guardrails", "deep") and inv.get("parent_entity"):
            out["parent_entity"] = inv["parent_entity"]
        return out

    def _project_policy(self, pol: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": pol.get("id", ""),
            "title": pol.get("title", pol.get("id", "")),
        }
        if self.tier in ("guardrails", "deep") and pol.get("rule"):
            out["rule"] = pol["rule"]
        return out

    def to_dict(self) -> dict[str, Any]:
        """Project tier-scoped view of matched architectural entities rather than raw frontmatter (DLT-2026-047)."""
        return {
            "query": self.query,
            "tier": self.tier,
            "components": [self._project_component(c) for c in self.components],
            "contracts": [self._project_contract(c) for c in self.contracts],
            "capabilities": [self._project_capability(c) for c in self.capabilities],
            "invariants": [self._project_invariant(i) for i in self.invariants],
            "policies": [self._project_policy(p) for p in self.policies],
            "matched_reasons": dict(self.matched_reasons),
            "query_latency_ms": self.query_latency_ms,
            "token_estimate": self.token_estimate,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Render markdown formatted for the requested tier."""
        lines = [
            f"# 🧭 Eidos Architectural Context Slice ({self.tier.upper()} Tier)",
            "",
        ]

        query_parts = []
        if self.query.get("files"):
            query_parts.append(f"**Files:** `{', '.join(self.query['files'])}`")
        if self.query.get("component"):
            query_parts.append(f"**Component:** `{self.query['component']}`")
        if self.query.get("task"):
            query_parts.append(f'**Task:** *"{self.query["task"]}"*')
        if query_parts:
            lines.append(" | ".join(query_parts))
            lines.append("")

        # 1. Containers & Components
        lines.append(f"## 🧱 C4 Level 2 Containers & Level 3 Components ({len(self.components)})")
        if not self.components:
            lines.append("*No components or containers directly matched.*")
        for comp in self.components:
            cid = comp.get("id", "")
            name = comp.get("name", cid)
            ctype = comp.get("container_type", "service")
            runtime = comp.get("runtime", "N/A")
            pkg = comp.get("package_path", "")
            is_comp = comp.get("type") == "tech/component" or cid.startswith("CMP-")
            is_secondary = self._is_secondary_dependency(cid)

            if is_comp:
                parent_cnt = comp.get("container", "N/A")
                lines.append(f"### `{cid}` ({name}) [C4 Level 3 Component]")
                lines.append(f"- **Owning Container:** `{parent_cnt}` | **Package:** `{pkg}`")
                if comp.get("file_path"):
                    lines.append(f"- **Specification:** `{comp['file_path']}`")
                if comp.get("responsibility"):
                    lines.append(f"- **Responsibility:** {comp['responsibility']}")
                if self.tier in ("guardrails", "deep"):
                    ifaces = comp.get("interfaces", {})
                    if isinstance(ifaces, dict) and ifaces.get("provides"):
                        lines.append("- **In-Process APIs (`interfaces.provides`):**")
                        for p in ifaces["provides"]:
                            sig = p.get("signature", "")
                            desc = f": {p.get('description', '')}" if p.get("description") else ""
                            lines.append(f"  - `{sig}`{desc}")
                    invs = comp.get("invariants", [])
                    if invs:
                        lines.append("- **Component Invariants (`INV-*`):**")
                        for inv in invs:
                            lines.append(f"  - `{inv.get('id')}`: {inv.get('rule')}")
                if self.tier == "deep":
                    qas = comp.get("quality_attributes", {})
                    if qas:
                        qa_str = ", ".join(f"{k}: {v}" for k, v in qas.items())
                        lines.append(f"- **Quality Attributes:** {qa_str}")
            else:
                lines.append(f"### `{cid}` ({name})")
                lines.append(f"- **Container:** `{ctype}` | **Runtime:** `{runtime}` | **Package:** `{pkg}`")
                if comp.get("domain_binding"):
                    lines.append(f"- **Domain:** `{comp['domain_binding']}`")

                if self.tier in ("guardrails", "deep") and not (is_secondary and len(self.components) > 3):
                    deps = (
                        (comp.get("dependencies") or {}).get("internal_containers", [])
                        or (comp.get("dependencies") or {}).get("internal_components", [])
                    ) if isinstance(comp.get("dependencies"), dict) else []
                    if deps:
                        lines.append(f"- **Dependencies:** {', '.join(f'`{d}`' for d in deps)}")

                    contracts = comp.get("contracts") or {}
                    prov = contracts.get("provides", []) if isinstance(contracts, dict) else []
                    if prov:
                        lines.append(f"- **Provides Contracts:** {', '.join(f'`{p}`' for p in prov)}")
                    req = (contracts.get("requires") or contracts.get("consumes") or []) if isinstance(contracts, dict) else []
                    if req:
                        lines.append(f"- **Requires Contracts:** {', '.join(f'`{r}`' for r in req)}")

                    cinvs = comp.get("invariants", [])
                    if cinvs:
                        lines.append("- **Container Invariants (`INV-*`):**")
                        for inv in cinvs:
                            lines.append(f"  - `{inv.get('id')}`: {inv.get('rule')}")

                if self.tier == "deep" and not is_secondary:
                    modules = comp.get("components", []) or comp.get("c4_internal_components", [])
                    if modules:
                        lines.append("- **C4 Level 3 Components:**")
                        for m in modules[:6]:
                            mid = f"`{m['id']}` " if m.get("id") else ""
                            mname = m.get("name", "")
                            mresp = m.get("responsibility", "")
                            lines.append(f"  - {mid}**{mname}**: {mresp}")

            if not is_secondary and not (self.tier != "deep" and len(self.components) > 4):
                decisions = collect_component_decisions(comp)
                if decisions:
                    lines.extend(format_decisions_markdown(decisions, tier=self.tier))

            lines.append("")

        # 2. Contracts
        lines.append(f"## 📜 Bound Interface Contracts ({len(self.contracts)})")
        if not self.contracts:
            lines.append("*No bound contracts in slice.*")
        for ctr in self.contracts:
            cid = ctr.get("id", "")
            title = ctr.get("title", cid)
            protocol = ctr.get("protocol", "standard")
            owner = ctr.get("owning_container") or ctr.get("owning_component", "N/A")
            path = ctr.get("path", "")
            lines.append(f"### `{cid}`: {title}")
            lines.append(f"- **Protocol:** `{protocol}` | **Owner:** `{owner}` | **Path:** `{path}`")
            lines.append("")

        # 3. Domain Capabilities (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🎯 Domain Capabilities ({len(self.capabilities)})")
            if not self.capabilities:
                lines.append("*No capabilities directly matched.*")
            for cap in self.capabilities:
                cid = cap.get("id", "")
                title = cap.get("title", cid)
                domain = cap.get("domain", "")
                lines.append(f"### `{cid}`: {title}")
                lines.append(f"- **Domain:** `{domain}`")
                invs = cap.get("invariants", [])
                if invs:
                    lines.append("- **Capability Invariants (`INV-*`):**")
                    for inv in invs:
                        lines.append(f"  - `{inv.get('id')}`: {inv.get('rule')}")
                lines.append("")

        # 4. Invariants (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🛡️ Active Invariants ({len(self.invariants)})")
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
            lines.append("")

        # 5. Policies (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 📋 Governing Policies ({len(self.policies)})")
            if not self.policies:
                lines.append("*No specific governing policies in slice.*")
            for pol in self.policies:
                pid = pol.get("id", "")
                title = pol.get("title", pid)
                rule = pol.get("rule", "")
                lines.append(f"- **`{pid}`** ({title})")
                if rule and self.tier == "deep":
                    lines.append(f"  - *Rule:* {rule}")
            lines.append("")

        lines.append(
            f"> Context Slice generated in {self.query_latency_ms:.2f}ms | Estimated Tokens: ~{self.token_estimate}"
        )
        return "\n".join(lines)

    def _estimate_tokens(self) -> int:
        return max(1, len(self.to_markdown()) // 4)

    def _prune_matched_reasons(self) -> None:
        retained_ids = {
            e.get("id", "")
            for group in (self.components, self.contracts, self.capabilities, self.invariants, self.policies)
            for e in group
            if e.get("id")
        }
        self.matched_reasons = {
            k: v
            for k, v in self.matched_reasons.items()
            if k in retained_ids or k.startswith(("DOM-", "DS-"))
        }

    def _exceeds_budget(self, budget: int) -> bool:
        self._prune_matched_reasons()
        return self._estimate_tokens() > budget or (len(self.to_json()) // 4) > budget

    def _enforce_token_budget(self, primary_cids: set[str]) -> None:
        """Enforce rank-ordered token budget truncation for the requested tier across Markdown and JSON (DLT-2026-047)."""
        budget = TIER_TOKEN_BUDGETS.get(self.tier, 1000)

        if self.tier == "anchor":
            self.capabilities = []
            self.invariants = []
            self.policies = []
            if self._exceeds_budget(budget) and primary_cids:
                self.components = [c for c in self.components if c.get("id") in primary_cids] or self.components[:1]
            while self._exceeds_budget(budget) and len(self.contracts) > 1:
                self.contracts.pop()
            while self._exceeds_budget(budget) and len(self.components) > 1:
                self.components.pop()
            self._prune_matched_reasons()
            self.token_estimate = self._estimate_tokens()
            return

        # For guardrails and deep tiers, trim lowest-priority invariants first while keeping core guardrails.
        while self._exceeds_budget(budget) and len(self.invariants) > 3:
            self.invariants.pop()

        if self._exceeds_budget(budget) and len(self.components) > 4:
            # Trim trailing secondary 1-hop dependency components while retaining primary + top dependencies
            for idx in range(len(self.components) - 1, -1, -1):
                if not self._exceeds_budget(budget) or len(self.components) <= 4:
                    break
                cid = self.components[idx].get("id", "")
                if cid not in primary_cids:
                    self.components.pop(idx)

        while self._exceeds_budget(budget) and len(self.invariants) > 1:
            self.invariants.pop()

        while self._exceeds_budget(budget) and len(self.policies) > 1:
            self.policies.pop()

        while self._exceeds_budget(budget) and len(self.capabilities) > 1:
            self.capabilities.pop()

        self._prune_matched_reasons()
        self.token_estimate = self._estimate_tokens()
