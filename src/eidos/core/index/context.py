"""Tiered Context Compressor (CMP-TIERED-CONTEXT-COMPRESSOR).

Extracts rank-ordered, token-budgeted architectural slices across anchor (<=300 tokens),
guardrails (<=1000 tokens), and deep (<=2500 tokens) tiers for AI coding agents.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eidos.core.index.query import TASK_SLICE_ENTITY_TYPES
from eidos.core.workspace.context import (
    ArchitecturalContextSlice,
    collect_component_decisions,
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

    _compact_decisions: bool = False

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        return f"{text[: limit - 3].rstrip()}..." if len(text) > limit else text

    def _project_decisions(
        self,
        decisions: list[dict[str, Any]],
        *,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for d in decisions:
            if not isinstance(d, dict):
                continue
            dtext = str(d.get("decision", "") or "")
            if self.tier == "anchor" or compact or self._compact_decisions:
                short_dtext = self._clip(dtext, 120)
                alts = [
                    {"option": self._clip(str(a.get("option")).strip(), 60)}
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
            elif self.tier == "guardrails":
                entry = {
                    "id": d.get("id", ""),
                    "title": d.get("title", ""),
                    "decision": self._clip(dtext, 140),
                }
                if d.get("graduated_from"):
                    entry["graduated_from"] = d["graduated_from"]
                if d.get("rationale"):
                    entry["rationale"] = self._clip(str(d["rationale"]), 140)
                if d.get("rejected_alternatives"):
                    entry["rejected_alternatives"] = [
                        {
                            "option": self._clip(str(a.get("option", "")), 80),
                            "reason_rejected": self._clip(str(a.get("reason_rejected", "")), 100),
                        }
                        for a in (d.get("rejected_alternatives") or [])[:2]
                        if isinstance(a, dict) and a.get("option")
                    ]
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

    def _project_component(self, comp: dict[str, Any], index_in_slice: int = 0) -> dict[str, Any]:
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
            else (
                "type",
                "container_type",
                "runtime",
                "package_path",
                "domain_binding",
                "container",
                "file_path",
                "responsibility",
            )
        )
        for base_key in base_keys:
            if comp.get(base_key):
                out[base_key] = comp[base_key]

        is_secondary = self._is_secondary_dependency(cid)
        compact_comp = self._compact_decisions and index_in_slice > 0
        if self.tier in ("guardrails", "deep") and not compact_comp and not (is_secondary and len(self.components) > 3):
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

        if (
            not is_secondary
            and not self._compact_decisions
            and not (self.tier != "deep" and len(self.components) > 4)
        ):
            decisions = collect_component_decisions(comp)
            if decisions:
                compact_decs = self.tier == "guardrails" and index_in_slice > 0
                out["decisions"] = self._project_decisions(decisions, compact=compact_decs)

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
            rule = str(pol["rule"])
            out["rule"] = self._clip(rule, 160) if self.tier == "guardrails" else rule
        return out

    def to_dict(self) -> dict[str, Any]:
        """Project tier-scoped view of matched architectural entities rather than raw frontmatter (DLT-2026-047)."""
        return {
            "query": self.query,
            "tier": self.tier,
            "components": [self._project_component(c, idx) for idx, c in enumerate(self.components)],
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

    def _state_signature(self, budget: int) -> tuple[Any, ...]:
        return (
            budget,
            self.tier,
            self._compact_decisions,
            len(self.components),
            len(self.contracts),
            len(self.capabilities),
            len(self.invariants),
            len(self.policies),
        )

    def _invalidate_budget_cache(self) -> None:
        self.__dict__.pop("_cached_sig", None)
        self.__dict__.pop("_cached_exceeds", None)
        self.__dict__.pop("_md_char_lb", None)

    def _pop_item(self, lst: list[dict[str, Any]], idx: int = -1) -> None:
        item = lst.pop(idx)
        md_lb = self.__dict__.get("_md_char_lb", 0)
        if lst is self.invariants and md_lb > 0:
            ub_removed = (
                len(str(item.get("id", "")))
                + len(str(item.get("rule", "")))
                + len(str(item.get("test_binding", "")))
                + 60
            )
            self.__dict__["_md_char_lb"] = max(0, md_lb - ub_removed)
            self.__dict__["_md_lb_sig"] = self._state_signature(0)[1:]
        else:
            self.__dict__.pop("_md_char_lb", None)
        self.__dict__.pop("_cached_sig", None)

    def _exceeds_budget(self, budget: int) -> bool:
        sig = self._state_signature(budget)
        if self.__dict__.get("_cached_sig") == sig:
            return bool(self.__dict__.get("_cached_exceeds"))
        md_lb = self.__dict__.get("_md_char_lb", 0) if self.__dict__.get("_md_lb_sig") == sig[1:] else 0
        if md_lb // 4 > budget:
            self.__dict__["_cached_sig"] = sig
            self.__dict__["_cached_exceeds"] = True
            return True
        md_len = len(self.to_markdown())
        self.__dict__["_md_char_lb"] = md_len
        self.__dict__["_md_lb_sig"] = sig[1:]
        if max(1, md_len // 4) > budget:
            exceeds = True
        else:
            self._prune_matched_reasons()
            d = self.to_dict()
            # Include 24-byte headroom for post-enforcement query_latency_ms and token_estimate float/int formatting
            exceeds = ((len(json.dumps(d)) + 24) // 4) > budget or ((len(json.dumps(d, indent=2)) + 24) // 4) > budget
        self.__dict__["_cached_sig"] = sig
        self.__dict__["_cached_exceeds"] = exceeds
        return exceeds

    def _enforce_token_budget(
        self,
        primary_cids: set[str],
        direct_cap_ids: set[str] | None = None,
        direct_inv_ids: set[str] | None = None,
        direct_pol_ids: set[str] | None = None,
    ) -> None:
        """Enforce rank-ordered token budget truncation for the requested tier across Markdown and JSON (DLT-2026-047)."""
        budget = TIER_TOKEN_BUDGETS.get(self.tier, 1000)
        self._invalidate_budget_cache()

        if self.tier == "anchor":
            self.capabilities = []
            self.invariants = []
            self.policies = []
            self._invalidate_budget_cache()
            if self._exceeds_budget(budget) and primary_cids:
                self.components = [c for c in self.components if c.get("id") in primary_cids] or self.components[:1]
                self._invalidate_budget_cache()
            while len(self.contracts) > 1 and self._exceeds_budget(budget):
                self._pop_item(self.contracts)
            while len(self.components) > 1 and self._exceeds_budget(budget):
                self._pop_item(self.components)
            self._prune_matched_reasons()
            self._invalidate_budget_cache()
            self.token_estimate = self._estimate_tokens()
            return

        direct_caps = direct_cap_ids or set()
        direct_invs = direct_inv_ids or set()
        direct_pols = direct_pol_ids or set()
        has_non_comp_direct = bool(direct_caps or direct_invs or direct_pols)

        def _pop_non_direct(items: list[dict[str, Any]], keep_ids: set[str], min_len: int) -> None:
            if len(items) <= min_len or not self._exceeds_budget(budget):
                return
            for idx in range(len(items) - 1, -1, -1):
                if len(items) <= min_len:
                    break
                if items[idx].get("id", "") not in keep_ids:
                    self._pop_item(items, idx)
                    if not self._exceeds_budget(budget):
                        break

        # Stage 1: Trim 1-hop expanded (non-direct) entities before touching any direct query matches.
        _pop_non_direct(self.invariants, direct_invs, min_len=3)
        _pop_non_direct(self.components, primary_cids, min_len=1 if has_non_comp_direct else 4)
        _pop_non_direct(self.invariants, direct_invs, min_len=1)
        _pop_non_direct(self.policies, direct_pols, min_len=1)
        _pop_non_direct(self.capabilities, direct_caps, min_len=1)

        # If still over budget and direct invariant/capability/policy matches exist, compact decision prose
        # and trim lower-ranked tail components before evicting direct matches.
        if has_non_comp_direct and self._exceeds_budget(budget):
            if self.tier == "guardrails":
                self._compact_decisions = True
                self._invalidate_budget_cache()
            while len(self.components) > 1 and self._exceeds_budget(budget):
                self._pop_item(self.components)

        # Stage 2: If still over budget, trim lowest-ranked tail items while keeping primary components intact.
        while len(self.invariants) > 3 and self._exceeds_budget(budget):
            self._pop_item(self.invariants)

        while len(self.policies) > 1 and self._exceeds_budget(budget):
            self._pop_item(self.policies)

        while len(self.invariants) > 2 and self._exceeds_budget(budget):
            self._pop_item(self.invariants)

        while len(self.capabilities) > 2 and self._exceeds_budget(budget):
            self._pop_item(self.capabilities)

        while len(self.invariants) > 1 and self._exceeds_budget(budget):
            self._pop_item(self.invariants)

        while len(self.capabilities) > 1 and self._exceeds_budget(budget):
            self._pop_item(self.capabilities)

        # Stage 3: If still over budget, trim remaining non-primary 1-hop components and compact decisions.
        if self._exceeds_budget(budget):
            _pop_non_direct(self.components, primary_cids, min_len=1)
        if self._exceeds_budget(budget) and not self._compact_decisions:
            self._compact_decisions = True
            self._invalidate_budget_cache()
        while len(self.contracts) > 1 and self._exceeds_budget(budget):
            self._pop_item(self.contracts)
        while len(self.components) > 1 and self._exceeds_budget(budget):
            self._pop_item(self.components)

        self._prune_matched_reasons()
        self._invalidate_budget_cache()
        self.token_estimate = self._estimate_tokens()


def build_tiered_context_slice(
    index: Any,
    files: list[str] | str | None = None,
    component: str | None = None,
    task: str | None = None,
    tier: str = "guardrails",
) -> TieredContextSlice:
    """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
    t0 = time.perf_counter()
    index._ensure_synced()

    matched_components: dict[str, dict[str, Any]] = {}
    matched_contracts: dict[str, dict[str, Any]] = {}
    matched_capabilities: dict[str, dict[str, Any]] = {}
    matched_invariants: dict[str, dict[str, Any]] = {}
    matched_policies: dict[str, dict[str, Any]] = {}
    matched_reasons: dict[str, list[str]] = {}
    direct_inv_ids: list[str] = []

    def _add_reason(eid: str, reason: str) -> None:
        bucket = matched_reasons.setdefault(eid, [])
        if reason not in bucket:
            bucket.append(reason)

    conn = index._get_connection()
    cursor = conn.cursor()

    # 1. Resolve by Component / Container parameter
    if component:
        c_clean = component.strip().upper()
        cursor.execute(
            """
            SELECT id, title, type, domain, data_json
            FROM entities
            WHERE type IN ('tech/container', 'tech/component') AND (id = ? OR id LIKE '%' || ? || '%');
            """,
            (c_clean, c_clean),
        )
        for row in cursor.fetchall():
            cid = row["id"]
            data = json.loads(row["data_json"])
            data["id"] = cid
            data["title"] = row["title"]
            matched_components[cid] = data
            _add_reason(cid, f"Explicit component parameter '{component}'")
        if not matched_components:
            cursor.execute(
                """
                SELECT e.id, e.title, e.type, e.domain, e.data_json
                FROM symbol_mappings s
                JOIN entities e ON e.id = s.entity_id
                WHERE s.kind = 'component_id' AND UPPER(s.symbol) = ?;
                """,
                (c_clean,),
            )
            for row in cursor.fetchall():
                cid = row["id"]
                data = json.loads(row["data_json"])
                data["id"] = cid
                data["title"] = row["title"]
                matched_components[cid] = data
                _add_reason(cid, f"Inline component parameter '{component}'")

    # 2. Resolve by File paths
    raw_files = [files] if isinstance(files, str) else (files or [])
    clean_files: list[str] = []
    for f in raw_files:
        if not f:
            continue
        for sub in str(f).split(","):
            sub_clean = sub.strip()
            if not sub_clean:
                continue
            p = Path(sub_clean)
            if p.is_absolute():
                try:
                    rel = str(p.resolve().relative_to(index.root)).replace("\\", "/")
                    clean_files.append(rel)
                except ValueError:
                    clean_files.append(sub_clean.replace("\\", "/"))
            else:
                c = sub_clean.replace("\\", "/")
                while c.startswith("./"):
                    c = c[2:]
                clean_files.append(c)

    for fpath in clean_files:
        matches = index.resolve_symbol_or_path(fpath)
        for m in matches:
            eid = m["entity_id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]

            if etype in ("tech/container", "tech/component"):
                matched_components[eid] = data
                _add_reason(eid, f"Source file match on '{fpath}'")
            elif etype == "invariant":
                matched_invariants[eid] = data
                if eid not in direct_inv_ids:
                    direct_inv_ids.append(eid)
                _add_reason(eid, f"Test binding match on '{fpath}'")
            elif etype == "tech/contract":
                matched_contracts[eid] = data
                _add_reason(eid, f"Contract file match on '{fpath}'")

    # 3. Resolve by Task prompt (FTS5 lexical search restricted to sliceable entity types, so historical
    #    SpecDeltas, explorations, and personas never consume the top-k candidate slots)
    if task and task.strip():
        fts_matches = index.query_fts(task, limit=8, include_types=TASK_SLICE_ENTITY_TYPES)
        for m in fts_matches:
            eid = m["id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]

            reason = f"FTS lexical match on task (BM25 score: {m['rank']:.2f})"
            if etype in ("tech/container", "tech/component"):
                matched_components[eid] = data
                _add_reason(eid, reason)
            elif etype == "tech/contract":
                matched_contracts[eid] = data
                _add_reason(eid, reason)
            elif etype == "product/capability":
                matched_capabilities[eid] = data
                _add_reason(eid, reason)
            elif etype == "product/policy":
                matched_policies[eid] = data
                _add_reason(eid, reason)
            elif etype == "invariant":
                matched_invariants[eid] = data
                if eid not in direct_inv_ids:
                    direct_inv_ids.append(eid)
                _add_reason(eid, reason)
            elif etype in ("product/domain", "design/system"):
                rel_types = ("domain_member",) if etype == "product/domain" else ("styles_container",)
                member_reason = (
                    f"Member of FTS-matched domain {eid}"
                    if etype == "product/domain"
                    else f"Styled by FTS-matched design system {eid}"
                )
                ph = ",".join("?" for _ in rel_types)
                member_ids: list[str] = []
                for row in cursor.execute(
                    f"""
                    SELECT e.id, e.title, e.type, e.data_json FROM edges ed
                    JOIN entities e ON e.id = ed.target_id
                    WHERE ed.source_id = ? AND ed.rel_type IN ({ph})
                    ORDER BY e.id;
                    """,
                    (eid, *rel_types),
                ).fetchall():
                    m_data = json.loads(row["data_json"])
                    m_data["id"] = row["id"]
                    m_data["title"] = row["title"]
                    if row["type"] in ("tech/container", "tech/component"):
                        matched_components.setdefault(row["id"], m_data)
                    elif row["type"] == "product/capability":
                        matched_capabilities.setdefault(row["id"], m_data)
                    else:
                        continue
                    member_ids.append(row["id"])
                    _add_reason(row["id"], member_reason)
                expanded = f"; expanded to {', '.join(member_ids)}" if member_ids else ""
                _add_reason(eid, f"{reason}{expanded}")

    for iid in direct_inv_ids:
        inv_data = matched_invariants[iid]
        parent_cap = inv_data.get("capability_id")
        if parent_cap and parent_cap not in matched_capabilities:
            row = cursor.execute(
                "SELECT id, title, type, domain, data_json FROM entities WHERE id = ?;",
                (parent_cap,),
            ).fetchone()
            if row:
                c_data = json.loads(row["data_json"])
                c_data["id"] = row["id"]
                c_data["title"] = row["title"]
                matched_capabilities[row["id"]] = c_data
                _add_reason(row["id"], f"Owning capability of matched invariant {iid}")
        parent_pol = inv_data.get("policy_id")
        if parent_pol and parent_pol not in matched_policies:
            row = cursor.execute(
                "SELECT id, title, type, domain, data_json FROM entities WHERE id = ?;",
                (parent_pol,),
            ).fetchone()
            if row:
                p_data = json.loads(row["data_json"])
                p_data["id"] = row["id"]
                p_data["title"] = row["title"]
                matched_policies[row["id"]] = p_data
                _add_reason(row["id"], f"Owning policy of matched invariant {iid}")
        for parent_comp_key in ("component_id", "container_id"):
            parent_comp = inv_data.get(parent_comp_key)
            if parent_comp and parent_comp not in matched_components:
                row = cursor.execute(
                    "SELECT id, title, type, domain, data_json FROM entities WHERE id = ?;",
                    (parent_comp,),
                ).fetchone()
                if row:
                    comp_data = json.loads(row["data_json"])
                    comp_data["id"] = row["id"]
                    comp_data["title"] = row["title"]
                    matched_components[row["id"]] = comp_data
                    _add_reason(row["id"], f"Owning component/container of matched invariant {iid}")

    has_explicit_query = bool(clean_files or (component and component.strip()) or (task and task.strip()))
    any_matched = bool(
        matched_components
        or matched_capabilities
        or matched_contracts
        or matched_invariants
        or matched_policies
    )

    # 4. Full catalog overview ONLY when no query filters were provided at all.
    if not any_matched and not has_explicit_query:
        cursor.execute(
            "SELECT id, title, type, domain, data_json FROM entities WHERE type IN ('tech/container', 'tech/component');"
        )
        for row in cursor.fetchall():
            c_data = json.loads(row["data_json"])
            c_data["id"] = row["id"]
            c_data["title"] = row["title"]
            matched_components[row["id"]] = c_data

    # 5. Forward dependency expansion for primary matched components
    primary_cids = list(matched_components.keys())
    missing_dep_ids: list[str] = []
    for cid in primary_cids:
        comp_data = matched_components.get(cid, {})
        deps_dict = comp_data.get("dependencies") or {}
        deps = deps_dict.get("internal_containers", []) or deps_dict.get("internal_components", [])
        for dep_id in deps:
            if dep_id not in matched_components and dep_id not in missing_dep_ids:
                missing_dep_ids.append(dep_id)
    if missing_dep_ids:
        ph = ",".join("?" for _ in missing_dep_ids)
        cursor.execute(
            f"SELECT id, title, type, domain, data_json FROM entities WHERE id IN ({ph});",
            tuple(missing_dep_ids),
        )
        for row in cursor.fetchall():
            c_data = json.loads(row["data_json"])
            c_data["id"] = row["id"]
            c_data["title"] = row["title"]
            matched_components[row["id"]] = c_data
            _add_reason(row["id"], "Internal dependency")

    # 6. Include contracts owned by or provided/consumed by primary components
    direct_ctr_ids = list(matched_contracts.keys())
    direct_cap_ids = list(matched_capabilities.keys())
    direct_pol_ids = list(matched_policies.keys())
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        cursor.execute(
            f"""
            SELECT id, title, type, domain, data_json FROM entities
            WHERE type = 'tech/contract' AND (
                id IN (SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type IN ('provides', 'consumes'))
                OR id IN (SELECT source_id FROM edges WHERE target_id IN ({ph}) AND rel_type = 'provides')
            );
            """,
            tuple(primary_cids + primary_cids),
        )
        for row in cursor.fetchall():
            ctr_data = json.loads(row["data_json"])
            ctr_data["id"] = row["id"]
            ctr_data["title"] = row["title"]
            matched_contracts.setdefault(row["id"], ctr_data)

    # 7. Include domain capabilities for primary components' domains
    domains = {
        c.get("domain_binding") or c.get("domain") for c in [matched_components[cid] for cid in primary_cids]
    }
    domains.discard("")
    if domains:
        ph = ",".join("?" for _ in domains)
        cursor.execute(
            f"SELECT id, title, type, domain, data_json FROM entities WHERE type = 'product/capability' AND domain IN ({ph});",
            tuple(domains),
        )
        for row in cursor.fetchall():
            cap_data = json.loads(row["data_json"])
            cap_data["id"] = row["id"]
            cap_data["title"] = row["title"]
            matched_capabilities.setdefault(row["id"], cap_data)

    # 7b. Include invariants declared directly on primary components
    primary_comp_inv_ids: set[str] = set()
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type = 'asserts'
            );
            """,
            tuple(primary_cids),
        )
        for row in cursor.fetchall():
            inv_data = json.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)
            primary_comp_inv_ids.add(row["id"])

    # 8. Invariants & Policies expansion from capabilities
    if matched_capabilities:
        cap_ids = list(matched_capabilities.keys())
        ph = ",".join("?" for _ in cap_ids)
        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type = 'asserts'
            );
            """,
            tuple(cap_ids),
        )
        for row in cursor.fetchall():
            inv_data = json.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)

        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'product/policy' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type = 'governed_by'
            );
            """,
            tuple(cap_ids),
        )
        for row in cursor.fetchall():
            pol_data = json.loads(row["data_json"])
            pol_data["id"] = row["id"]
            pol_data["title"] = row["title"]
            matched_policies.setdefault(row["id"], pol_data)

    # 9. Fallback governing policies if none matched
    if not matched_policies and (any_matched or not has_explicit_query):
        for row in cursor.execute(
            "SELECT id, title, type, domain, data_json FROM entities WHERE type = 'product/policy' ORDER BY id LIMIT 2;"
        ).fetchall():
            p_data = json.loads(row["data_json"])
            p_data["id"] = row["id"]
            p_data["title"] = row["title"]
            matched_policies[row["id"]] = p_data

    # 10. Invariants from governing policies
    for pid in list(matched_policies.keys()):
        cursor.execute(
            """
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'asserts'
            );
            """,
            (pid,),
        )
        for row in cursor.fetchall():
            inv_data = json.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)

    # Ensure embedded decisions (including inline component decisions) are surfaced on each matched component
    for comp_entry in matched_components.values():
        collected_decs = collect_component_decisions(comp_entry)
        if collected_decs:
            comp_entry["decisions"] = collected_decs

    # Assemble and rank-order lists so token-budget truncation retains highest-priority entities first
    primary_set = set(primary_cids)
    primary_rank = {cid: idx for idx, cid in enumerate(primary_cids)}
    direct_ctr_rank = {cid: idx for idx, cid in enumerate(direct_ctr_ids)}
    direct_cap_rank = {cid: idx for idx, cid in enumerate(direct_cap_ids)}
    direct_pol_rank = {pid: idx for idx, pid in enumerate(direct_pol_ids)}

    sorted_comps = sorted(
        matched_components.values(),
        key=lambda x: (
            0 if x.get("id", "") in primary_set else 1,
            primary_rank.get(x.get("id", ""), 999),
            x.get("id", ""),
        ),
    )
    sorted_ctrs = sorted(
        matched_contracts.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_ctr_rank else 1,
            direct_ctr_rank.get(x.get("id", ""), 999),
            x.get("id", ""),
        ),
    )
    sorted_caps = sorted(
        matched_capabilities.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_cap_rank else 1,
            direct_cap_rank.get(x.get("id", ""), 999),
            x.get("id", ""),
        ),
    )
    sorted_pols = sorted(
        matched_policies.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_pol_rank else 1,
            direct_pol_rank.get(x.get("id", ""), 999),
            x.get("id", ""),
        ),
    )

    ordered_invs: list[dict[str, Any]] = []
    seen_inv_ids: set[str] = set()
    for iid in direct_inv_ids:
        if iid in matched_invariants and iid not in seen_inv_ids:
            ordered_invs.append(matched_invariants[iid])
            seen_inv_ids.add(iid)
    for iid in sorted(primary_comp_inv_ids):
        if iid in matched_invariants and iid not in seen_inv_ids:
            ordered_invs.append(matched_invariants[iid])
            seen_inv_ids.add(iid)

    for source_list in (sorted_comps, sorted_caps, sorted_pols):
        inv_lists: list[list[str]] = []
        for src in source_list:
            raw_invs = list((src.get("technical") or {}).get("invariants") or src.get("invariants") or [])
            for comp_item in src.get("components") or src.get("c4_internal_components") or []:
                if isinstance(comp_item, dict):
                    raw_invs.extend(comp_item.get("invariants") or [])
            inv_lists.append(
                [inv.get("id") for inv in raw_invs if isinstance(inv, dict) and inv.get("id")]
            )
        max_len = max((len(lst) for lst in inv_lists), default=0)
        for idx in range(max_len):
            for lst in inv_lists:
                if idx < len(lst):
                    iid = lst[idx]
                    if iid in matched_invariants and iid not in seen_inv_ids:
                        ordered_invs.append(matched_invariants[iid])
                        seen_inv_ids.add(iid)

    for iid in sorted(matched_invariants.keys()):
        if iid not in seen_inv_ids:
            ordered_invs.append(matched_invariants[iid])
            seen_inv_ids.add(iid)

    slice_obj = TieredContextSlice(
        tier=tier,
        query={"files": clean_files, "component": component, "task": task},
        components=sorted_comps,
        contracts=sorted_ctrs,
        capabilities=sorted_caps,
        invariants=ordered_invs,
        policies=sorted_pols,
        matched_reasons=matched_reasons,
        query_latency_ms=0.0,
        token_estimate=0,
    )
    slice_obj._enforce_token_budget(
        primary_set,
        direct_cap_ids=set(direct_cap_ids),
        direct_inv_ids=set(direct_inv_ids),
        direct_pol_ids=set(direct_pol_ids),
    )
    slice_obj.query_latency_ms = (time.perf_counter() - t0) * 1000
    return slice_obj
