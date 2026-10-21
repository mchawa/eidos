"""Tiered Context Compressor (CMP-TIERED-CONTEXT-COMPRESSOR).

Extracts rank-ordered, token-budgeted architectural slices across anchor (<=300 tokens),
guardrails (<=1000 tokens), and deep (<=2500 tokens) tiers for AI coding agents.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.query import (
    TASK_SLICE_ENTITY_TYPES,
    execute_fts_query,
    resolve_symbol_or_path_in_db,
)

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


def collect_component_decisions(comp: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect embedded architectural decisions from a container/component and its inline components."""
    decisions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for d in comp.get("decisions", []) or []:
        if isinstance(d, dict):
            did = str(d.get("id", "")).strip()
            if not did or did not in seen_ids:
                if did:
                    seen_ids.add(did)
                decisions.append(d)
    for m in comp.get("components", []) or []:
        if isinstance(m, dict):
            for d in m.get("decisions", []) or []:
                if isinstance(d, dict):
                    did = str(d.get("id", "")).strip()
                    if not did or did not in seen_ids:
                        if did:
                            seen_ids.add(did)
                        decisions.append(d)
    return decisions


def _clip_text(text: str, limit: int) -> str:
    return f"{text[: limit - 3].rstrip()}..." if len(text) > limit else text


def format_decisions_markdown(
    decisions: list[dict[str, Any]],
    tier: str = "guardrails",
    *,
    compact: bool = False,
    compact_prose: bool = False,
    max_items: int | None = None,
) -> list[str]:
    """Render embedded architectural decisions (DEC-*) and rejected_alternatives according to the requested tier."""
    if not decisions:
        return []
    limit_n = max_items if max_items is not None else (2 if (tier == "anchor" and compact_prose) else len(decisions))
    lines = ["- **Architectural Decisions (`DEC-*`):**"]
    for dec_idx, dec in enumerate(decisions[:limit_n]):
        if not isinstance(dec, dict):
            continue
        did = dec.get("id", "")
        dtitle = dec.get("title", "")
        dtext = str(dec.get("decision", "") or "")
        alts = [
            str(a.get("option", "")).strip()
            for a in (dec.get("rejected_alternatives") or [])
            if isinstance(a, dict) and a.get("option")
        ]
        if tier == "anchor" or compact:
            short_dtext = _clip_text(dtext, 55 if compact_prose else 120)
            max_anchor_alts = 1 if compact_prose else 2
            short_alts = [_clip_text(a, 45 if compact_prose else 55) for a in alts[:max_anchor_alts]]
            rej_suffix = f" *(Rejected: {', '.join(short_alts)})*" if short_alts else ""
            lines.append(f"  - `{did}` (**{dtitle}**): {short_dtext}{rej_suffix}")
        elif tier == "guardrails":
            grad = (
                f" [Graduated from `{dec['graduated_from']}`]"
                if dec.get("graduated_from") and not (compact_prose and dec_idx > 0)
                else ""
            )
            d_lim = 100 if compact_prose else 180
            r_lim = 100 if compact_prose else 180
            alt_lim = 80 if compact_prose else 140
            max_alts = 1 if compact_prose else 2
            lines.append(f"  - `{did}` (**{dtitle}**){grad}: {_clip_text(dtext, d_lim)}")
            if not (compact_prose and dec_idx > 0):
                if dec.get("rationale"):
                    lines.append(f"    - *Rationale:* {_clip_text(str(dec['rationale']), r_lim)}")
                for alt in (dec.get("rejected_alternatives") or [])[:max_alts]:
                    if isinstance(alt, dict) and alt.get("option"):
                        raw_r = str(alt.get("reason") or alt.get("reason_rejected") or "")
                        reason = _clip_text(raw_r, alt_lim)
                        lines.append(f"    - *Rejected Alternative:* **{alt['option']}** — {reason}")
        else:
            grad = f" [Graduated from `{dec['graduated_from']}`]" if dec.get("graduated_from") else ""
            d_out = _clip_text(dtext, 160) if compact_prose else dtext
            lines.append(f"  - `{did}` (**{dtitle}**){grad}: {d_out}")
            if dec.get("rationale"):
                r_out = _clip_text(str(dec["rationale"]), 140) if compact_prose else str(dec["rationale"])
                lines.append(f"    - *Rationale:* {r_out}")
            for alt in dec.get("rejected_alternatives") or []:
                if isinstance(alt, dict) and alt.get("option"):
                    raw_reason = str(alt.get("reason") or alt.get("reason_rejected") or "")
                    reason = _clip_text(raw_reason, 100) if compact_prose else raw_reason
                    lines.append(f"    - *Rejected Alternative:* **{alt['option']}** — {reason}")
    return lines


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
    tier: str = "guardrails"  # "anchor" | "guardrails" | "deep"
    query_latency_ms: float = 0.0
    token_estimate: int = 0

    def _is_secondary_dependency(self, cid: str) -> bool:
        return self.matched_reasons.get(cid) == ["Internal dependency"]

    def to_dict(self) -> dict[str, Any]:
        """Convert slice to dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serialize slice to JSON."""
        option = orjson.OPT_INDENT_2 if indent else 0
        return orjson.dumps(self.to_dict(), option=option).decode("utf-8")

    def to_markdown(self) -> str:
        """Render a clean, token-efficient Markdown representation formatted for the requested tier."""
        compact_prose = bool(getattr(self, "_compact_prose", False))
        compact_decisions = bool(getattr(self, "_compact_decisions", False))
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
        for idx_comp, comp in enumerate(self.components):
            cid = comp.get("id", "")
            title = comp.get("title", cid)
            ctype = comp.get("container_type", "service")
            runtime = comp.get("runtime", "N/A")
            raw_sp = comp.get("source_path", "")
            pkg = ", ".join(str(x) for x in raw_sp) if isinstance(raw_sp, list) else str(raw_sp or "")
            is_comp = comp.get("type") == "tech/component" or cid.startswith("CMP-")
            is_secondary = self._is_secondary_dependency(cid)

            if is_comp:
                parent_cnt = comp.get("container", "N/A")
                lines.append(f"### `{cid}` ({title}) [C4 Level 3 Component]")
                lines.append(f"- **Owning Container:** `{parent_cnt}` | **Source Path:** `{pkg}`")
                if comp.get("file_path"):
                    lines.append(f"- **Specification:** `{comp['file_path']}`")
                if self.tier == "deep" and comp.get("status"):
                    lines.append(f"- **Lifecycle Status:** `{comp['status']}`")
                if comp.get("responsibility"):
                    resp_txt = str(comp["responsibility"])
                    if compact_prose:
                        resp_txt = _clip_text(resp_txt.splitlines()[0], 120)
                    lines.append(f"- **Responsibility:** {resp_txt}")
                if self.tier in ("guardrails", "deep"):
                    invs = comp.get("invariants", [])
                    if invs:
                        lines.append("- **Component Invariants (`INV-*`):**")
                        for inv in invs:
                            irule = str(inv.get("rule", ""))
                            if compact_prose:
                                irule = _clip_text(irule, 120)
                            lines.append(f"  - `{inv.get('id')}`: {irule}")
            else:
                lines.append(f"### `{cid}` ({title})")
                lines.append(f"- **Container:** `{ctype}` | **Runtime:** `{runtime}` | **Source Path:** `{pkg}`")
                if comp.get("domain"):
                    lines.append(f"- **Domain:** `{comp['domain']}`")

                if self.tier in ("guardrails", "deep") and not (is_secondary and len(self.components) > 3):
                    contracts = comp.get("contracts") or {}
                    prov = contracts.get("provides", []) if isinstance(contracts, dict) else []
                    if prov:
                        lines.append(f"- **Provides Contracts:** {', '.join(f'`{p}`' for p in prov)}")
                    req = contracts.get("consumes", []) if isinstance(contracts, dict) else []
                    if req:
                        lines.append(f"- **Consumes Contracts:** {', '.join(f'`{r}`' for r in req)}")

                    cinvs = comp.get("invariants", [])
                    if cinvs:
                        lines.append("- **Container Invariants (`INV-*`):**")
                        for inv in cinvs:
                            irule = str(inv.get("rule", ""))
                            if compact_prose:
                                irule = _clip_text(irule, 120)
                            lines.append(f"  - `{inv.get('id')}`: {irule}")

                if self.tier == "deep" and not is_secondary:
                    modules = comp.get("components", [])
                    if modules:
                        lines.append("- **C4 Level 3 Components:**")
                        for m in modules[:6]:
                            mid = f"`{m['id']}` " if m.get("id") else ""
                            mtitle = m.get("title", "")
                            mresp = str(m.get("responsibility", ""))
                            if compact_prose and len(mresp) > 100:
                                first_l = mresp.strip().splitlines()[0] if mresp.strip() else ""
                                mresp = (
                                    first_l.split(". ", 1)[0].rstrip(".") + "."
                                    if ". " in first_l and len(first_l.split(". ", 1)[0]) < 100
                                    else _clip_text(first_l, 100)
                                )
                            lines.append(f"  - {mid}**{mtitle}**: {mresp}")

            if (
                not is_secondary
                and (not compact_decisions or (self.tier == "anchor" and idx_comp == 0))
                and not (self.tier != "deep" and len(self.components) > 4)
            ):
                decisions = collect_component_decisions(comp)
                if decisions and not (self.tier == "anchor" and compact_decisions and len(decisions) > 4):
                    compact_decs = (
                        (self.tier == "guardrails" and idx_comp > 0)
                        or (compact_prose and idx_comp > 0)
                        or compact_decisions
                    )
                    max_dec_items = 1 if (self.tier == "anchor" and compact_decisions) else None
                    lines.extend(
                        format_decisions_markdown(
                            decisions,
                            tier=self.tier,
                            compact=compact_decs,
                            compact_prose=compact_prose,
                            max_items=max_dec_items,
                        )
                    )

            lines.append("")

        # 2. Contracts
        lines.append(f"## 📜 Bound Interface Contracts ({len(self.contracts)})")
        if not self.contracts:
            lines.append("*No bound contracts in slice.*")
        for ctr in self.contracts:
            cid = ctr.get("id", "")
            title = ctr.get("title", cid)
            protocol = ctr.get("protocol", "standard")
            owner = ctr.get("container", "N/A")
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
                if invs and not compact_prose:
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
                rule = str(inv.get("rule", ""))
                if compact_prose:
                    rule = _clip_text(rule, 120)
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
                sev = pol.get("severity", "")
                sev_suffix = f" [`{sev}`]" if sev else ""
                lines.append(f"- **`{pid}`** ({title}){sev_suffix}")
            lines.append("")

        lines.append(
            f"> Context Slice generated in {self.query_latency_ms:.2f}ms | Estimated Tokens: ~{self.token_estimate}"
        )
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
    """Extract an architectural subgraph context slice tailored for an AI agent's current task."""
    from eidos.core.index.graph_index import get_architectural_index

    root = (repo_root or Path.cwd()).resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    index = get_architectural_index(eidos_dir=target_eidos, repo_root=root)
    return index.get_tiered_context_slice(
        files=files,
        component=component,
        task=task,
        tier=tier,
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
    _compact_prose: bool = False

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        return f"{text[: limit - 3].rstrip()}..." if len(text) > limit else text

    @classmethod
    def _compact_summary(cls, text: str, limit: int = 120) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        if ". " in first_line:
            first_sentence = first_line.split(". ", 1)[0].rstrip(".") + "."
            if len(first_sentence) <= limit:
                return first_sentence
        return cls._clip(first_line, limit)

    def _project_decisions(
        self,
        decisions: list[dict[str, Any]],
        *,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        if self.tier == "anchor" and self._compact_decisions:
            max_decs = 1
        elif self.tier == "anchor" and self._compact_prose:
            max_decs = 2
        else:
            max_decs = len(decisions)
        for dec_idx, d in enumerate(decisions[:max_decs]):
            if not isinstance(d, dict):
                continue
            dtext = str(d.get("decision", "") or "")
            if self.tier == "anchor" or compact or self._compact_decisions:
                short_dtext = self._compact_summary(dtext, 55 if self._compact_prose else 120)
                opt_limit = 45 if self._compact_prose else 60
                max_anchor_alts = 1 if self._compact_prose else 2
                alts = [
                    {"option": self._clip(str(a.get("option")).strip(), opt_limit)}
                    for a in (d.get("rejected_alternatives") or [])[:max_anchor_alts]
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
                    "decision": self._compact_summary(dtext, 90) if self._compact_prose else self._clip(dtext, 140),
                }
                if not (self._compact_prose and dec_idx > 0):
                    if d.get("graduated_from"):
                        entry["graduated_from"] = d["graduated_from"]
                    if d.get("rationale"):
                        rat = str(d["rationale"])
                        entry["rationale"] = (
                            self._compact_summary(rat, 85) if self._compact_prose else self._clip(rat, 140)
                        )
                    if d.get("rejected_alternatives"):
                        max_alts = 1 if self._compact_prose else 2
                        entry["rejected_alternatives"] = [
                            {
                                "option": self._clip(str(a.get("option", "")), 65 if self._compact_prose else 80),
                                "reason_rejected": (
                                    self._compact_summary(str(a.get("reason_rejected", "")), 65)
                                    if self._compact_prose
                                    else self._clip(str(a.get("reason_rejected", "")), 100)
                                ),
                            }
                            for a in (d.get("rejected_alternatives") or [])[:max_alts]
                            if isinstance(a, dict) and a.get("option")
                        ]
                projected.append(entry)
            else:
                entry = {
                    "id": d.get("id", ""),
                    "title": d.get("title", ""),
                    "decision": self._compact_summary(dtext, 160) if self._compact_prose else dtext,
                }
                if d.get("graduated_from"):
                    entry["graduated_from"] = d["graduated_from"]
                if d.get("rationale"):
                    rat = str(d["rationale"])
                    entry["rationale"] = self._compact_summary(rat, 140) if self._compact_prose else d["rationale"]
                if d.get("rejected_alternatives"):
                    entry["rejected_alternatives"] = [
                        {
                            "option": a.get("option", ""),
                            "reason_rejected": (
                                self._compact_summary(str(a.get("reason_rejected", "")), 100)
                                if self._compact_prose
                                else a.get("reason_rejected", "")
                            ),
                        }
                        for a in (d.get("rejected_alternatives") or [])
                        if isinstance(a, dict) and a.get("option")
                    ]
                projected.append(entry)
        return projected

    def _project_component(self, comp: dict[str, Any], index_in_slice: int = 0) -> dict[str, Any]:
        cid = comp.get("id", "")
        title = comp.get("title", cid)
        out: dict[str, Any] = {
            "id": cid,
            "title": title,
        }
        base_keys = (
            ("type", "container_type", "runtime", "source_path", "domain", "container")
            if self.tier == "anchor"
            else (
                "type",
                "container_type",
                "runtime",
                "source_path",
                "domain",
                "container",
                "file_path",
                "responsibility",
            )
        )
        for base_key in base_keys:
            if comp.get(base_key):
                val = comp[base_key]
                if base_key == "responsibility" and (
                    self._compact_prose or (self._compact_decisions and index_in_slice > 0)
                ):
                    out[base_key] = self._compact_summary(str(val), 120)
                else:
                    out[base_key] = val

        is_secondary = self._is_secondary_dependency(cid)
        compact_comp = self._compact_decisions and index_in_slice > 0
        if self.tier in ("guardrails", "deep") and not compact_comp and not (is_secondary and len(self.components) > 3):
            if comp.get("contracts"):
                out["contracts"] = comp["contracts"]
            if comp.get("invariants"):
                out["invariants"] = [
                    {
                        "id": inv.get("id", ""),
                        "rule": (
                            self._compact_summary(str(inv.get("rule", "")), 120)
                            if self._compact_prose
                            else inv.get("rule", "")
                        ),
                    }
                    for inv in comp.get("invariants", [])
                    if isinstance(inv, dict)
                ]

        if self.tier == "deep" and not is_secondary:
            modules = comp.get("components", [])
            if modules:
                out["components"] = [
                    {
                        k: (
                            self._compact_summary(str(m[k]), 100)
                            if k == "responsibility" and self._compact_prose
                            else m[k]
                        )
                        for k in ("id", "title", "responsibility", "source_path")
                        if isinstance(m, dict) and m.get(k)
                    }
                    for m in modules
                    if isinstance(m, dict)
                ]

        if (
            not is_secondary
            and (not self._compact_decisions or (self.tier == "anchor" and index_in_slice == 0))
            and not (self.tier != "deep" and len(self.components) > 4)
        ):
            decisions = collect_component_decisions(comp)
            if decisions and not (self.tier == "anchor" and self._compact_decisions and len(decisions) > 4):
                compact_decs = (
                    (self.tier == "guardrails" and index_in_slice > 0)
                    or (self._compact_prose and index_in_slice > 0)
                    or self._compact_decisions
                )
                out["decisions"] = self._project_decisions(decisions, compact=compact_decs)

        return out

    def _project_contract(self, ctr: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": ctr.get("id", ""),
            "title": ctr.get("title", ctr.get("id", "")),
            "protocol": ctr.get("protocol", "standard"),
        }
        if ctr.get("container"):
            out["container"] = ctr["container"]
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
        if self.tier in ("guardrails", "deep") and cap.get("invariants") and not self._compact_prose:
            out["invariants"] = [
                {"id": inv.get("id", ""), "rule": inv.get("rule", "")}
                for inv in cap.get("invariants", [])
                if isinstance(inv, dict)
            ]
        return out

    def _project_invariant(self, inv: dict[str, Any]) -> dict[str, Any]:
        raw_rule = str(inv.get("rule", ""))
        out: dict[str, Any] = {
            "id": inv.get("id", ""),
            "rule": self._compact_summary(raw_rule, 110) if self._compact_prose else raw_rule,
            "has_test_coverage": bool(inv.get("has_test_coverage")),
        }
        tb = inv.get("test_binding")
        if tb and tb != "None":
            out["test_binding"] = tb
        if self.tier in ("guardrails", "deep") and inv.get("parent_entity") and not self._compact_prose:
            out["parent_entity"] = inv["parent_entity"]
        return out

    def _project_policy(self, pol: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": pol.get("id", ""),
            "title": pol.get("title", pol.get("id", "")),
        }
        if pol.get("severity"):
            out["severity"] = pol["severity"]
        return out

    def _compress_verbose_fields_in_place(self, primary_cids: set[str]) -> bool:
        """Progressively compress verbose prose fields (description, rationale, details, responsibility)
        to compact 1-line summaries before dropping invariants or policies.
        """
        changed = False
        if not self._compact_prose:
            self._compact_prose = True
            changed = True
        for idx, comp in enumerate(self.components):
            cid = comp.get("id", "")
            is_non_primary = idx > 0 or (primary_cids and cid not in primary_cids)
            for prose_key in ("description", "details", "rationale"):
                if comp.get(prose_key) and len(str(comp[prose_key])) > 120:
                    comp[prose_key] = self._compact_summary(str(comp[prose_key]), 120)
                    changed = True
            if is_non_primary and comp.get("responsibility") and len(str(comp["responsibility"])) > 120:
                comp["responsibility"] = self._compact_summary(str(comp["responsibility"]), 120)
                changed = True
        for cap in self.capabilities:
            for prose_key in ("description", "details"):
                if cap.get(prose_key) and len(str(cap[prose_key])) > 120:
                    cap[prose_key] = self._compact_summary(str(cap[prose_key]), 120)
                    changed = True
        for pol in self.policies:
            for prose_key in ("description", "details", "rationale"):
                if pol.get(prose_key) and len(str(pol[prose_key])) > 120:
                    pol[prose_key] = self._compact_summary(str(pol[prose_key]), 120)
                    changed = True
        if changed:
            self._invalidate_budget_cache()
        return changed

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

    _STRUCTURAL_STRIP_TABLE = str.maketrans("", "", "{}[]:,\"'`|")

    def to_json(self, indent: int = 2) -> str:
        option = orjson.OPT_INDENT_2 if indent else 0
        return orjson.dumps(self.to_dict(), option=option).decode("utf-8")

    @classmethod
    def _estimate_text_tokens(cls, text: str) -> int:
        """Estimate BPE token count weighting structural syntax/punctuation at ~3.0 chars/token and prose at ~4.0 chars/token."""
        if not text:
            return 1
        prose_chars = len(text.translate(cls._STRUCTURAL_STRIP_TABLE))
        structural_chars = len(text) - prose_chars
        return max(1, int(prose_chars / 4.0 + structural_chars / 3.0))

    def _estimate_tokens(self) -> int:
        return self._estimate_text_tokens(self.to_markdown())

    def _prune_matched_reasons(self) -> None:
        retained_ids = {
            e.get("id", "")
            for group in (self.components, self.contracts, self.capabilities, self.invariants, self.policies)
            for e in group
            if e.get("id")
        }
        self.matched_reasons = {
            k: v for k, v in self.matched_reasons.items() if k in retained_ids or k.startswith(("DOM-", "DS-"))
        }

    def _state_signature(self, budget: int) -> tuple[Any, ...]:
        return (
            budget,
            self.tier,
            self._compact_decisions,
            self._compact_prose,
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
        self.__dict__.pop("_md_tok_lb", None)
        self.__dict__.pop("_json_char_lb", None)
        self.__dict__.pop("_md_ok", None)

    def _item_md_and_json_upper_bounds(
        self, lst: list[dict[str, Any]], item: dict[str, Any], idx: int
    ) -> tuple[int, int]:
        cid = str(item.get("id", "") or "")
        id_len = len(cid)
        if lst is self.invariants:
            raw_rule = str(item.get("rule", "") or "")
            rule_len = min(len(raw_rule), 120) if self._compact_prose else len(raw_rule)
            tb = str(item.get("test_binding") or "")
            tb_len = len(tb) if tb and tb != "None" else 0
            pe_len = 0 if self._compact_prose else len(str(item.get("parent_entity") or ""))
            md_ub = id_len + rule_len + (tb_len + 20 if tb_len else 0) + 32
            json_ub = id_len + rule_len + tb_len + pe_len + 135
            return md_ub, json_ub
        if lst is self.capabilities:
            title_len = len(str(item.get("title", "") or cid))
            dom_len = len(str(item.get("domain", "") or ""))
            if self._compact_prose or self.tier == "anchor":
                return id_len + title_len + dom_len + 34, id_len + title_len + dom_len + 115
            invs_len = sum(
                len(str(i.get("id", ""))) + len(str(i.get("rule", ""))) + 40
                for i in (item.get("invariants") or [])
                if isinstance(i, dict)
            )
            return id_len + title_len + dom_len + invs_len + 75, id_len + title_len + dom_len + invs_len + 150
        if lst is self.policies:
            title_len = len(str(item.get("title", "") or cid))
            sev_len = len(str(item.get("severity", "") or ""))
            return id_len + title_len + sev_len + 24, id_len + title_len + sev_len + 95
        if lst is self.contracts:
            title_len = len(str(item.get("title", "") or cid))
            prot_len = len(str(item.get("protocol", "") or "standard"))
            cnt_len = len(str(item.get("container", "") or "N/A"))
            path_len = len(str(item.get("path", "") or ""))
            return id_len + title_len + prot_len + cnt_len + path_len + 60, (
                id_len + title_len + prot_len + cnt_len + path_len + 125
            )
        if self._compact_decisions and (idx > 0 or (idx < 0 and len(lst) >= 1)):
            title_len = len(str(item.get("title", "") or cid))
            sp_len = len(str(item.get("source_path", "") or ""))
            fp_len = len(str(item.get("file_path", "") or ""))
            dom_len = len(str(item.get("domain", "") or ""))
            resp_len = min(len(str(item.get("responsibility", "") or "")), 120)
            base_ub = id_len + title_len + sp_len + fp_len + dom_len + resp_len
            return base_ub + 140, base_ub + 260
        proj = self._project_component(item, idx if idx >= 0 else len(lst))
        comp_ub = len(orjson.dumps(proj, option=orjson.OPT_INDENT_2)) + 64
        return comp_ub, comp_ub

    def _pop_item(self, lst: list[dict[str, Any]], idx: int = -1) -> None:
        item = lst.pop(idx)
        if lst is self.components and (
            (len(self.components) == 4 and not self._compact_decisions)
            or (
                len(self.components) == 3
                and any(self._is_secondary_dependency(c.get("id", "")) for c in self.components)
            )
        ):
            self._invalidate_budget_cache()
            return
        eid = str(item.get("id", "") or "")
        reason_ub = 0
        if eid and not eid.startswith(("DOM-", "DS-")):
            removed_reasons = self.matched_reasons.pop(eid, None)
            if removed_reasons:
                reason_ub = len(eid) + sum(len(str(r)) + 12 for r in removed_reasons) + 24
        md_ub, json_ub = self._item_md_and_json_upper_bounds(lst, item, idx)
        json_ub += reason_ub
        md_lb = self.__dict__.get("_md_char_lb", 0)
        md_tok_lb = self.__dict__.get("_md_tok_lb", 0)
        json_lb = self.__dict__.get("_json_char_lb", 0)
        sig_tail = self._state_signature(0)[1:]
        if md_lb > 0:
            self.__dict__["_md_char_lb"] = max(0, md_lb - md_ub)
            self.__dict__["_md_lb_sig"] = sig_tail
        if md_tok_lb > 0:
            self.__dict__["_md_tok_lb"] = max(0, md_tok_lb - (md_ub // 3 + 1))
            self.__dict__["_md_lb_sig"] = sig_tail
        if json_lb > 0:
            self.__dict__["_json_char_lb"] = max(0, json_lb - json_ub)
            self.__dict__["_md_lb_sig"] = sig_tail
        self.__dict__.pop("_cached_sig", None)

    def _exceeds_budget(self, budget: int) -> bool:
        sig = self._state_signature(budget)
        if self.__dict__.get("_cached_sig") == sig:
            return bool(self.__dict__.get("_cached_exceeds"))
        has_lb_sig = self.__dict__.get("_md_lb_sig") == sig[1:]
        md_lb = self.__dict__.get("_md_char_lb", 0) if has_lb_sig else 0
        md_tok_lb = self.__dict__.get("_md_tok_lb", 0) if has_lb_sig else 0
        json_lb = self.__dict__.get("_json_char_lb", 0) if has_lb_sig else 0
        if md_tok_lb > budget or max(md_lb, json_lb) // 4 > budget:
            self.__dict__["_cached_sig"] = sig
            self.__dict__["_cached_exceeds"] = True
            return True
        if not (has_lb_sig and self.__dict__.get("_md_ok")):
            md_text = self.to_markdown()
            md_len = len(md_text)
            self._prune_matched_reasons()
            self.__dict__["_md_char_lb"] = md_len
            self.__dict__["_md_lb_sig"] = sig[1:]
            if max(1, md_len // 4) > budget:
                self.__dict__["_md_tok_lb"] = md_len // 4
                self.__dict__["_cached_sig"] = sig
                self.__dict__["_cached_exceeds"] = True
                return True
            md_tokens = self._estimate_text_tokens(md_text)
            self.__dict__["_md_tok_lb"] = md_tokens
            if md_tokens > budget:
                self.__dict__["_cached_sig"] = sig
                self.__dict__["_cached_exceeds"] = True
                return True
            self.__dict__["_md_ok"] = True
        else:
            self._prune_matched_reasons()
            self.__dict__["_md_lb_sig"] = sig[1:]
        d = self.to_dict()
        # Include 24-byte headroom for post-enforcement query_latency_ms and token_estimate float/int formatting
        json_len = len(orjson.dumps(d, option=orjson.OPT_INDENT_2)) + 24
        self.__dict__["_json_char_lb"] = json_len
        exceeds = (json_len // 4) > budget
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
            if self._exceeds_budget(budget):
                self._compress_verbose_fields_in_place(primary_cids)
            while len(self.contracts) > 0 and self._exceeds_budget(budget):
                self._pop_item(self.contracts)
            if self._exceeds_budget(budget) and not self._compact_decisions:
                self._compact_decisions = True
                self._invalidate_budget_cache()
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

        # Stage 1a: Progressively compress verbose prose fields (description, rationale, details, responsibility)
        # before evicting invariants, policies, or capabilities.
        if self._exceeds_budget(budget):
            self._compress_verbose_fields_in_place(primary_cids)

        # Stage 1b: Trim 1-hop expanded (non-direct) entities before touching any direct query matches.
        _pop_non_direct(self.invariants, direct_invs, min_len=3)
        _pop_non_direct(self.components, primary_cids, min_len=1 if has_non_comp_direct else 4)
        _pop_non_direct(self.capabilities, direct_caps, min_len=3)

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
            SELECT id, title, type, domain, file_path, data_json
            FROM entities
            WHERE type IN ('tech/container', 'tech/component') AND (id = ? OR id LIKE '%' || ? || '%');
            """,
            (c_clean, c_clean),
        )
        for row in cursor.fetchall():
            cid = row["id"]
            data = orjson.loads(row["data_json"])
            data["id"] = cid
            data["title"] = row["title"]
            data.setdefault("file_path", row["file_path"])
            matched_components[cid] = data
            _add_reason(cid, f"Explicit component parameter '{component}'")
        if not matched_components:
            cursor.execute(
                """
                SELECT e.id, e.title, e.type, e.domain, e.file_path, e.data_json
                FROM symbol_mappings s
                JOIN entities e ON e.id = s.entity_id
                WHERE s.kind = 'component_id' AND UPPER(s.symbol) = ?;
                """,
                (c_clean,),
            )
            for row in cursor.fetchall():
                cid = row["id"]
                data = orjson.loads(row["data_json"])
                data["id"] = cid
                data["title"] = row["title"]
                data.setdefault("file_path", row["file_path"])
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
        matches = resolve_symbol_or_path_in_db(conn, fpath)
        for m in matches:
            eid = m["entity_id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]
            if m.get("file_path"):
                data.setdefault("file_path", m["file_path"])

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
        fts_matches = execute_fts_query(conn, text=task, limit=8, include_types=TASK_SLICE_ENTITY_TYPES)
        for m in fts_matches:
            eid = m["id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]
            if m.get("file_path"):
                data.setdefault("file_path", m["file_path"])

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
                member_reason = (
                    f"Member of FTS-matched domain {eid}"
                    if etype == "product/domain"
                    else f"Styled by FTS-matched design system {eid}"
                )
                member_sql = (
                    """
                    SELECT e.id, e.title, e.type, e.file_path, e.data_json FROM edges ed
                    JOIN entities e ON e.id = ed.source_id
                    WHERE ed.target_id = ? AND ed.rel_type = 'realizes'
                    ORDER BY e.id;
                    """
                    if etype == "product/domain"
                    else """
                    SELECT e.id, e.title, e.type, e.file_path, e.data_json FROM edges ed
                    JOIN entities e ON e.id = ed.target_id
                    WHERE ed.source_id = ? AND ed.rel_type = 'styles_container'
                    ORDER BY e.id;
                    """
                )
                member_ids: list[str] = []
                for row in cursor.execute(member_sql, (eid,)).fetchall():
                    m_data = orjson.loads(row["data_json"])
                    m_data["id"] = row["id"]
                    m_data["title"] = row["title"]
                    m_data.setdefault("file_path", row["file_path"])
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
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_cap,),
            ).fetchone()
            if row:
                c_data = orjson.loads(row["data_json"])
                c_data["id"] = row["id"]
                c_data["title"] = row["title"]
                c_data.setdefault("file_path", row["file_path"])
                matched_capabilities[row["id"]] = c_data
                _add_reason(row["id"], f"Owning capability of matched invariant {iid}")
        parent_pol = inv_data.get("policy_id")
        if parent_pol and parent_pol not in matched_policies:
            row = cursor.execute(
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_pol,),
            ).fetchone()
            if row:
                p_data = orjson.loads(row["data_json"])
                p_data["id"] = row["id"]
                p_data["title"] = row["title"]
                p_data.setdefault("file_path", row["file_path"])
                matched_policies[row["id"]] = p_data
                _add_reason(row["id"], f"Owning policy of matched invariant {iid}")
        for parent_comp_key in ("component_id", "container_id"):
            parent_comp = inv_data.get(parent_comp_key)
            if parent_comp and parent_comp not in matched_components:
                row = cursor.execute(
                    "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                    (parent_comp,),
                ).fetchone()
                if row:
                    comp_data = orjson.loads(row["data_json"])
                    comp_data["id"] = row["id"]
                    comp_data["title"] = row["title"]
                    comp_data.setdefault("file_path", row["file_path"])
                    matched_components[row["id"]] = comp_data
                    _add_reason(row["id"], f"Owning component/container of matched invariant {iid}")

    has_explicit_query = bool(clean_files or (component and component.strip()) or (task and task.strip()))
    any_matched = bool(
        matched_components or matched_capabilities or matched_contracts or matched_invariants or matched_policies
    )

    # 4. Full catalog overview ONLY when no query filters were provided at all.
    if not any_matched and not has_explicit_query:
        cursor.execute(
            "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE type IN ('tech/container', 'tech/component');"
        )
        for row in cursor.fetchall():
            c_data = orjson.loads(row["data_json"])
            c_data["id"] = row["id"]
            c_data["title"] = row["title"]
            c_data.setdefault("file_path", row["file_path"])
            matched_components[row["id"]] = c_data

    # 5. Expand 1-hop internal dependencies via graph edges (depends_on, consumes_api)
    primary_cids = list(matched_components.keys())
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        dep_rows = cursor.execute(
            f"""
            SELECT DISTINCT target_id FROM edges
            WHERE source_id IN ({ph}) AND rel_type IN ('depends_on', 'consumes_api')
            """,
            tuple(primary_cids),
        ).fetchall()
        missing_dep_ids = [r[0] for r in dep_rows if r[0] and r[0] not in matched_components]
        if missing_dep_ids:
            ph_dep = ",".join("?" for _ in missing_dep_ids)
            cursor.execute(
                f"SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id IN ({ph_dep}) ORDER BY id;",
                tuple(missing_dep_ids),
            )
            for row in cursor.fetchall():
                c_data = orjson.loads(row["data_json"])
                c_data["id"] = row["id"]
                c_data["title"] = row["title"]
                c_data.setdefault("file_path", row["file_path"])
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
            SELECT id, title, type, domain, file_path, data_json FROM entities
            WHERE type = 'tech/contract' AND (
                id IN (SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type IN ('provides', 'consumes'))
                OR id IN (SELECT source_id FROM edges WHERE target_id IN ({ph}) AND rel_type = 'provides')
            );
            """,
            tuple(primary_cids + primary_cids),
        )
        for row in cursor.fetchall():
            ctr_data = orjson.loads(row["data_json"])
            ctr_data["id"] = row["id"]
            ctr_data["title"] = row["title"]
            ctr_data.setdefault("file_path", row["file_path"])
            matched_contracts.setdefault(row["id"], ctr_data)

    # Identify any inline C4 Level 3 components whose source_path specifically matches the queried files
    matched_inline_comp_ids: set[str] = set()
    if clean_files and primary_cids:
        from eidos.core.workspace.project import matches_owned_paths

        for cid in primary_cids:
            c_entry = matched_components.get(cid) or {}
            for inline_c in c_entry.get("components") or []:
                if not isinstance(inline_c, dict) or not inline_c.get("id"):
                    continue
                raw_sp = inline_c.get("source_path")
                sp_list = [raw_sp] if isinstance(raw_sp, str) else (raw_sp if isinstance(raw_sp, list) else [])
                sp_clean = [str(s).strip() for s in sp_list if isinstance(s, str) and str(s).strip()]
                if sp_clean and any(matches_owned_paths(f, sp_clean)[0] for f in clean_files):
                    matched_inline_comp_ids.add(str(inline_c["id"]))

    # 7. Include capabilities directly implementing/implemented by primary components (or their inline components),
    # then fall back to domain capabilities when no direct capability edges exist or when domain capabilities match.
    direct_edge_cap_ids: set[str] = set()
    if primary_cids:
        candidate_comp_targets: list[str] = list(primary_cids)
        for cid in primary_cids:
            c_entry = matched_components.get(cid) or {}
            for inline_c in c_entry.get("components") or []:
                if isinstance(inline_c, dict) and inline_c.get("id"):
                    ic_id = str(inline_c["id"])
                    if ic_id not in candidate_comp_targets:
                        candidate_comp_targets.append(ic_id)
        ph = ",".join("?" for _ in candidate_comp_targets)
        cursor.execute(
            f"""
            SELECT id, title, type, domain, file_path, data_json FROM entities
            WHERE type = 'product/capability' AND (
                id IN (SELECT source_id FROM edges WHERE target_id IN ({ph}) AND rel_type IN ('implemented_by', 'implements'))
                OR id IN (SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type IN ('implements', 'implemented_by'))
            );
            """,
            tuple(candidate_comp_targets + candidate_comp_targets),
        )
        for row in cursor.fetchall():
            cap_data = orjson.loads(row["data_json"])
            cap_data["id"] = row["id"]
            cap_data["title"] = row["title"]
            cap_data.setdefault("file_path", row["file_path"])
            matched_capabilities.setdefault(row["id"], cap_data)
            direct_edge_cap_ids.add(row["id"])

    domains = {c.get("domain") for c in [matched_components[cid] for cid in primary_cids]}
    domains.discard("")
    domains.discard(None)
    if domains:
        ph = ",".join("?" for _ in domains)
        cursor.execute(
            f"SELECT id, title, type, domain, file_path, data_json FROM entities WHERE type = 'product/capability' AND domain IN ({ph});",
            tuple(domains),
        )
        all_matched_comp_ids = set(matched_components.keys())
        for c_entry in matched_components.values():
            for inline_c in c_entry.get("components") or []:
                if isinstance(inline_c, dict) and inline_c.get("id"):
                    all_matched_comp_ids.add(str(inline_c["id"]))
        for row in cursor.fetchall():
            cap_data = orjson.loads(row["data_json"])
            cap_data["id"] = row["id"]
            cap_data["title"] = row["title"]
            cap_data.setdefault("file_path", row["file_path"])
            if direct_edge_cap_ids and row["id"] not in direct_edge_cap_ids:
                tech_block = cap_data.get("technical") if isinstance(cap_data.get("technical"), dict) else {}
                bound_targets = {
                    str(t)
                    for t in (tech_block.get("containers") or []) + (tech_block.get("components") or [])
                    if isinstance(t, str) and t.strip()
                }
                if bound_targets and not (bound_targets & all_matched_comp_ids):
                    continue
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
            inv_data = orjson.loads(row["data_json"])
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
            inv_data = orjson.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)

        cursor.execute(
            f"""
            SELECT id, title, file_path, data_json FROM entities
            WHERE type = 'product/policy' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type = 'governed_by'
            );
            """,
            tuple(cap_ids),
        )
        for row in cursor.fetchall():
            pol_data = orjson.loads(row["data_json"])
            pol_data["id"] = row["id"]
            pol_data["title"] = row["title"]
            pol_data.setdefault("file_path", row["file_path"])
            matched_policies.setdefault(row["id"], pol_data)

    # 9. Fallback governing policies if none matched
    if not matched_policies and (any_matched or not has_explicit_query):
        for row in cursor.execute(
            "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE type = 'product/policy' ORDER BY id LIMIT 2;"
        ).fetchall():
            p_data = orjson.loads(row["data_json"])
            p_data["id"] = row["id"]
            p_data["title"] = row["title"]
            p_data.setdefault("file_path", row["file_path"])
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
            inv_data = orjson.loads(row["data_json"])
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

    file_and_inline_tokens: set[str] = set()
    for f in clean_files:
        file_and_inline_tokens.update(_tokenize_text(Path(f).stem.replace("_", " ").replace("-", " ")))
    for ic_id in matched_inline_comp_ids:
        file_and_inline_tokens.update(_tokenize_text(ic_id.removeprefix("CMP-").replace("_", " ").replace("-", " ")))

    def _cap_binds_matched_inline_comp(cap_entry: dict[str, Any]) -> bool:
        if not matched_inline_comp_ids:
            return False
        tech = cap_entry.get("technical") if isinstance(cap_entry.get("technical"), dict) else {}
        comp_refs = {str(t).strip() for t in (tech.get("components") or []) if isinstance(t, str)}
        return bool(comp_refs & matched_inline_comp_ids)

    def _cap_relevance_tuple(cap_entry: dict[str, Any]) -> tuple[int, int, int, str]:
        tech = cap_entry.get("technical") if isinstance(cap_entry.get("technical"), dict) else {}
        cnt_refs = [str(t).strip() for t in (tech.get("containers") or []) if isinstance(t, str) and str(t).strip()]
        primary_cnt_match = bool(cnt_refs and cnt_refs[0] in primary_set)
        binds_inline = _cap_binds_matched_inline_comp(cap_entry)
        in_domain = cap_entry.get("domain", "") in domains
        overlap = 0
        if file_and_inline_tokens:
            cap_tokens = set(_tokenize_text(str(cap_entry.get("title", "")).replace("_", " ").replace("-", " ")))
            overlap = len(file_and_inline_tokens & cap_tokens)
        if (primary_cnt_match and overlap > 0) or (binds_inline and in_domain):
            tier_score = 0
        elif in_domain or binds_inline:
            tier_score = 1
        else:
            tier_score = 2
        return (tier_score, -overlap, 0 if in_domain else 1, str(cap_entry.get("id", "")))

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
            *_cap_relevance_tuple(x),
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
            for comp_item in src.get("components") or []:
                if isinstance(comp_item, dict):
                    raw_invs.extend(comp_item.get("invariants") or [])
            inv_lists.append([inv.get("id") for inv in raw_invs if isinstance(inv, dict) and inv.get("id")])
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
