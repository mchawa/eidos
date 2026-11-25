"""ArchitecturalContextSlice and TieredContextSlice models, decision formatting, and calibrated token-budget pruning (CMP-TIERED-CONTEXT-COMPRESSOR)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import orjson

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
    """Collect embedded architectural decisions from a container or component."""
    decisions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for d in comp.get("decisions", []) or []:
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
    compact_tail: bool = False,
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
        if tier == "anchor" or compact or (compact_tail and dec_idx > 0):
            short_dtext = _clip_text(dtext, 55 if compact_prose else 120)
            max_anchor_alts = (
                0 if (tier == "guardrails" and compact_prose and dec_idx > 0) else (1 if compact_prose else 2)
            )
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
                        raw_r = str(alt.get("reason_rejected") or "")
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
                    raw_reason = str(alt.get("reason_rejected") or "")
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
    memories: list[dict[str, Any]] = field(default_factory=list)
    matched_reasons: dict[str, list[str]] = field(default_factory=dict)
    provenance_tags: dict[str, dict[str, str]] = field(default_factory=dict)
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
        compact_primary_decisions = bool(getattr(self, "_compact_primary_decisions", False))
        compact_metadata = bool(getattr(self, "_compact_metadata", False))
        drop_anchor_decisions = bool(getattr(self, "_drop_anchor_decisions", False))
        compact_memories = bool(getattr(self, "_compact_memories", False))
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

            def _fmt_design_item(d: Any) -> str:
                if isinstance(d, dict):
                    did = d.get("id", "")
                    dfp = d.get("file_path", "")
                    return f"`{did}` (`{dfp}`)" if dfp else f"`{did}`"
                return f"`{d}`"

            if is_comp:
                parent_cnt = comp.get("container", "N/A")
                lines.append(f"### `{cid}` ({title}) [C4 Level 3 Component]")
                lines.append(f"- **Owning Container:** `{parent_cnt}` | **Source Path:** `{pkg}`")
                if comp.get("file_path"):
                    lines.append(f"- **Specification:** `{comp['file_path']}`")
                if (
                    self.tier in ("guardrails", "deep")
                    and not compact_metadata
                    and (not is_secondary or not compact_prose)
                ):
                    ptag = self.provenance_tags.get(cid)
                    if ptag:
                        lines.append(f"- **Provenance:** `{ptag.get('provenance')}` (`{ptag.get('confidence')}`)")
                if self.tier in ("guardrails", "deep") and not compact_metadata and not is_secondary:
                    if comp.get("domain_path"):
                        lines.append(f"- **Domain Spec:** `{comp['domain_path']}`")
                    if comp.get("design"):
                        lines.append(f"- **Bound Design:** {', '.join(_fmt_design_item(d) for d in comp['design'])}")
                if self.tier == "deep" and comp.get("status"):
                    lines.append(f"- **Lifecycle Status:** `{comp['status']}`")
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
                if (
                    self.tier in ("guardrails", "deep")
                    and not compact_metadata
                    and (not is_secondary or not compact_prose)
                ):
                    ptag = self.provenance_tags.get(cid)
                    if ptag:
                        lines.append(f"- **Provenance:** `{ptag.get('provenance')}` (`{ptag.get('confidence')}`)")
                if comp.get("domain"):
                    dom_path_str = (
                        f" (`{comp['domain_path']}`)"
                        if (self.tier in ("guardrails", "deep") and not compact_metadata and comp.get("domain_path"))
                        else ""
                    )
                    lines.append(f"- **Domain:** `{comp['domain']}`{dom_path_str}")
                if (
                    self.tier in ("guardrails", "deep")
                    and not compact_metadata
                    and not is_secondary
                    and comp.get("design")
                ):
                    lines.append(f"- **Bound Design:** {', '.join(_fmt_design_item(d) for d in comp['design'])}")

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

            if (
                not is_secondary
                and (not compact_decisions or idx_comp == 0)
                and not (self.tier != "deep" and len(self.components) > 4)
            ):
                decisions = collect_component_decisions(comp)
                if decisions and not (self.tier == "anchor" and drop_anchor_decisions):
                    heavy_primary_guardrails = (
                        self.tier == "guardrails" and compact_primary_decisions and idx_comp == 0 and len(decisions) > 3
                    )
                    compact_decs = (
                        (self.tier == "guardrails" and idx_comp > 0)
                        or (compact_prose and idx_comp > 0)
                        or compact_decisions
                    )
                    compact_tail = (
                        compact_primary_decisions
                        and idx_comp == 0
                        and not compact_decs
                        and (self.tier != "deep" or len(decisions) > 3)
                    )
                    if self.tier == "anchor" and compact_decisions:
                        max_dec_items: int | None = 1
                    elif self.tier == "guardrails" and compact_prose and idx_comp > 0:
                        max_dec_items = 1
                    elif (
                        self.tier == "guardrails" and compact_decisions and idx_comp == 0
                    ) or heavy_primary_guardrails:
                        max_dec_items = 2
                    elif self.tier == "guardrails" and compact_primary_decisions and idx_comp == 0:
                        max_dec_items = 3
                    elif self.tier == "deep" and compact_primary_decisions and idx_comp == 0 and len(decisions) > 7:
                        max_dec_items = 7
                    else:
                        max_dec_items = None
                    lines.extend(
                        format_decisions_markdown(
                            decisions,
                            tier=self.tier,
                            compact=compact_decs,
                            compact_tail=compact_tail,
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
            path = ctr.get("file_path") or ctr.get("specification_file") or ""
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
                if self.tier == "deep" and not compact_metadata:
                    if cap.get("file_path"):
                        lines.append(f"- **Specification:** `{cap['file_path']}`")
                    biz = cap.get("business") if isinstance(cap.get("business"), dict) else {}
                    personas = [str(p) for p in (biz.get("personas") or []) if p]
                    if personas:
                        lines.append(f"- **Personas:** {', '.join(f'`{p}`' for p in personas)}")
                    exp = cap.get("experience") if isinstance(cap.get("experience"), dict) else {}
                    exp_items = [
                        str(x)
                        for k in ("clients", "design_systems", "screens", "flows", "ui_components")
                        for x in (exp.get(k) or [])
                        if x
                    ]
                    if exp_items:
                        lines.append(f"- **Experience (`design/*`):** {', '.join(f'`{x}`' for x in exp_items)}")
                invs = (cap.get("technical") or {}).get("invariants") or []
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

        # 6. Team Operational Memories (secondary budgeted attachment, omitted when empty)
        if self.memories:
            lines.append(f"## 🧠 Team Operational Memories ({len(self.memories)})")
            for mem in self.memories:
                mid = mem.get("id", "")
                mcat = mem.get("category", "feedback")
                mfresh = mem.get("freshness", "fresh")
                msum = str(mem.get("summary", "") or "")
                if compact_memories:
                    msum = _clip_text(msum, 120)
                lines.append(f"- **`{mid}`** ({mcat}, `{mfresh}`): {msum}")
                if self.tier in ("guardrails", "deep") and not compact_memories:
                    mreason = str(mem.get("freshness_reason") or "").strip()
                    if mfresh != "fresh" and mreason:
                        lines.append(f"  - *Freshness:* {mreason}")
                    if self.tier == "deep":
                        mguid = str(mem.get("guidance") or "").strip()
                        if mguid:
                            lines.append(f"  - *Guidance:* {_clip_text(mguid, 300) if compact_prose else mguid}")
                        mwhy = str(mem.get("why") or "").strip()
                        if mwhy:
                            lines.append(f"  - *Why:* {_clip_text(mwhy, 200) if compact_prose else mwhy}")
            lines.append("")

        lines.append(
            f"> Context Slice generated in {self.query_latency_ms:.2f}ms | Estimated Tokens: ~{self.token_estimate}"
        )
        return "\n".join(lines)


def _tokenize_text(text: str) -> set[str]:
    """Extract lowercased semantic keywords from a text string."""
    words = re.findall(r"[A-Za-z0-9_-]{3,}", text.lower())
    return {w for w in words if w not in STOP_WORDS}


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
    _compact_primary_decisions: bool = False
    _compact_prose: bool = False
    _compact_metadata: bool = False
    _drop_anchor_decisions: bool = False
    _compact_memories: bool = False

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
        compact_tail: bool = False,
        is_primary: bool = True,
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        heavy_primary_guardrails = (
            self.tier == "guardrails" and self._compact_primary_decisions and is_primary and len(decisions) > 3
        )
        if self.tier == "anchor" and self._compact_decisions:
            max_decs = 1
        elif self.tier == "anchor" and self._compact_prose:
            max_decs = 2
        elif self.tier == "guardrails" and self._compact_prose and compact and not is_primary:
            max_decs = 1
        elif (self.tier == "guardrails" and self._compact_decisions and is_primary) or heavy_primary_guardrails:
            max_decs = 2
        elif self.tier == "guardrails" and self._compact_primary_decisions and is_primary:
            max_decs = 3
        elif self.tier == "deep" and self._compact_primary_decisions and is_primary and len(decisions) > 7:
            max_decs = 7
        else:
            max_decs = len(decisions)
        for dec_idx, d in enumerate(decisions[:max_decs]):
            if not isinstance(d, dict):
                continue
            dtext = str(d.get("decision", "") or "")
            use_compact_entry = (
                self.tier == "anchor" or compact or self._compact_decisions or (compact_tail and dec_idx > 0)
            )
            if use_compact_entry:
                short_dtext = self._compact_summary(dtext, 55 if self._compact_prose else 120)
                opt_limit = 45 if self._compact_prose else 60
                max_anchor_alts = (
                    0
                    if (
                        (self.tier == "anchor" and self._compact_decisions)
                        or ((compact or compact_tail) and self.tier == "guardrails" and self._compact_prose)
                    )
                    else (1 if self._compact_prose else 2)
                )
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
        is_secondary = self._is_secondary_dependency(cid)
        if self.tier == "anchor":
            base_keys: tuple[str, ...] = ("type", "container_type", "runtime", "source_path", "domain", "container")
        elif is_secondary and self._compact_decisions:
            base_keys = ("type", "container")
        elif is_secondary and self._compact_prose:
            base_keys = ("type", "container", "file_path")
        elif is_secondary and len(self.components) > 4:
            base_keys = ("type", "source_path", "domain", "container", "file_path")
        else:
            base_keys = (
                "type",
                "container_type",
                "runtime",
                "source_path",
                "domain",
                "container",
                "file_path",
            )
        for base_key in base_keys:
            if comp.get(base_key):
                out[base_key] = comp[base_key]

        compact_comp = self._compact_decisions and index_in_slice > 0
        if self.tier in ("guardrails", "deep") and not compact_comp and not (is_secondary and len(self.components) > 3):
            if not self._compact_metadata and not is_secondary:
                if comp.get("domain_path") and not (self._compact_prose and index_in_slice > 0):
                    out["domain_path"] = comp["domain_path"]
                if comp.get("design") and not (self._compact_prose and index_in_slice > 0):
                    out["design"] = comp["design"]
            raw_contracts = comp.get("contracts")
            if isinstance(raw_contracts, dict):
                clean_contracts = {k: v for k, v in raw_contracts.items() if v}
                if clean_contracts:
                    out["contracts"] = clean_contracts
            elif raw_contracts:
                out["contracts"] = raw_contracts
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

        if (
            not is_secondary
            and (not self._compact_decisions or index_in_slice == 0)
            and not (self.tier != "deep" and len(self.components) > 4)
        ):
            decisions = collect_component_decisions(comp)
            if decisions and not (self.tier == "anchor" and self._drop_anchor_decisions):
                compact_decs = (
                    (self.tier == "guardrails" and index_in_slice > 0)
                    or (self._compact_prose and index_in_slice > 0)
                    or self._compact_decisions
                )
                compact_tail = (
                    self._compact_primary_decisions
                    and index_in_slice == 0
                    and not compact_decs
                    and (self.tier != "deep" or len(decisions) > 3)
                )
                out["decisions"] = self._project_decisions(
                    decisions,
                    compact=compact_decs,
                    compact_tail=compact_tail,
                    is_primary=(index_in_slice == 0),
                )

        return out

    def _project_contract(self, ctr: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": ctr.get("id", ""),
            "title": ctr.get("title", ctr.get("id", "")),
            "protocol": ctr.get("protocol", "standard"),
        }
        if ctr.get("container"):
            out["container"] = ctr["container"]
        ctr_path = ctr.get("file_path") or ctr.get("specification_file")
        if self.tier in ("guardrails", "deep") and ctr_path:
            out["path"] = ctr_path
        return out

    def _project_capability(self, cap: dict[str, Any], index_in_slice: int = 0) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": cap.get("id", ""),
            "title": cap.get("title", cap.get("id", "")),
            "domain": cap.get("domain", ""),
            "status": cap.get("status", "active"),
        }
        if self.tier in ("guardrails", "deep"):
            if cap.get("file_path") and not (self._compact_metadata and index_in_slice > 0):
                out["file_path"] = cap["file_path"]
            if not self._compact_metadata and (self.tier == "deep" or not self._compact_prose or index_in_slice == 0):
                biz = cap.get("business") if isinstance(cap.get("business"), dict) else {}
                max_refs = 1 if (self.tier == "guardrails" and self._compact_prose) else 10
                personas = [str(p) for p in (biz.get("personas") or []) if p][:max_refs]
                if personas:
                    out["personas"] = personas
                if self.tier == "deep" or not self._compact_prose:
                    policies = [str(p) for p in (biz.get("policies") or []) if p][:max_refs]
                    if policies:
                        out["policies"] = policies
                has_design_comp = any(c.get("design") for c in self.components[:2])
                if self.tier == "deep" or has_design_comp or not self._compact_prose:
                    exp = cap.get("experience") if isinstance(cap.get("experience"), dict) else {}
                    skip_exp_keys = (
                        {"clients", "design_systems"}
                        if (self.tier == "guardrails" and self._compact_prose and has_design_comp)
                        else set()
                    )
                    exp_clean = {
                        k: [str(x) for x in v[:max_refs] if x]
                        for k, v in exp.items()
                        if k not in skip_exp_keys and isinstance(v, list) and any(x for x in v)
                    }
                    if exp_clean:
                        out["experience"] = exp_clean
        cap_invs = (cap.get("technical") or {}).get("invariants") or []
        if self.tier in ("guardrails", "deep") and cap_invs and not self._compact_prose:
            out["invariants"] = [
                {"id": inv.get("id", ""), "rule": inv.get("rule", "")} for inv in cap_invs if isinstance(inv, dict)
            ]
        return out

    def _project_invariant(self, inv: dict[str, Any]) -> dict[str, Any]:
        raw_rule = str(inv.get("rule", ""))
        rule_limit = 95 if (self._compact_prose and self._compact_decisions) else 110
        out: dict[str, Any] = {
            "id": inv.get("id", ""),
            "rule": self._compact_summary(raw_rule, rule_limit) if self._compact_prose else raw_rule,
            "has_test_coverage": bool(inv.get("has_test_coverage")),
        }
        tb = inv.get("test_binding")
        if tb and tb != "None":
            out["test_binding"] = tb
        parent_ent = inv.get("source_id")
        if self.tier in ("guardrails", "deep") and parent_ent and not self._compact_prose:
            out["parent_entity"] = parent_ent
        return out

    def _project_policy(self, pol: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": pol.get("id", ""),
            "title": pol.get("title", pol.get("id", "")),
        }
        if pol.get("category"):
            out["category"] = pol["category"]
        if pol.get("severity"):
            out["severity"] = pol["severity"]
        if self.tier in ("guardrails", "deep") and pol.get("file_path"):
            out["file_path"] = pol["file_path"]
        return out

    def _project_memory(self, mem: dict[str, Any]) -> dict[str, Any]:
        summary = str(mem.get("summary") or "")
        freshness = str(mem.get("freshness") or "fresh")
        out: dict[str, Any] = {
            "id": mem.get("id", ""),
            "category": mem.get("category", "feedback"),
            "summary": self._clip(summary, 120) if self._compact_memories else summary,
            "freshness": freshness,
        }
        if self.tier in ("guardrails", "deep") and not self._compact_memories:
            reason = str(mem.get("freshness_reason") or "").strip()
            if freshness != "fresh" and reason:
                out["freshness_reason"] = reason
            if mem.get("file_path") and not self._compact_metadata:
                out["file_path"] = mem["file_path"]
            if self.tier == "deep":
                if mem.get("verified_at"):
                    out["verified_at"] = mem["verified_at"]
                if mem.get("guidance"):
                    raw_g = str(mem["guidance"])
                    out["guidance"] = self._clip(raw_g, 300) if self._compact_prose else raw_g
                if mem.get("why"):
                    raw_w = str(mem["why"])
                    out["why"] = self._clip(raw_w, 200) if self._compact_prose else raw_w
        return out

    def _compress_verbose_fields_in_place(self, primary_cids: set[str]) -> bool:
        """Progressively compress verbose prose fields (invariant rules and primary tail decision narratives)
        and, on later calls, secondary governance/design metadata before dropping invariants or policies.
        """
        _ = primary_cids
        if not self._compact_prose:
            self._compact_prose = True
            self._invalidate_budget_cache()
            return True
        if self.tier != "anchor" and not self._compact_primary_decisions:
            self._compact_primary_decisions = True
            self._invalidate_budget_cache()
            return True
        if not self._compact_metadata:
            self._compact_metadata = True
            self._invalidate_budget_cache()
            return True
        return False

    def _project_provenance_tags(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        max_guard_tags = 2 if (self.tier == "guardrails" and self._compact_prose and len(self.components) > 3) else None
        for idx_c, c in enumerate(self.components):
            cid = str(c.get("id") or "")
            if not cid or cid not in self.provenance_tags:
                continue
            if self._compact_metadata and idx_c > 0:
                continue
            if max_guard_tags is not None and len(out) >= max_guard_tags and self._is_secondary_dependency(cid):
                continue
            raw_tag = self.provenance_tags[cid]
            entry: dict[str, str] = {
                "provenance": str(raw_tag.get("provenance") or "declared_spec"),
                "confidence": str(raw_tag.get("confidence") or "observed"),
            }
            ev = str(raw_tag.get("evidence") or "")
            if ev and not self._compact_metadata and not self._compact_prose:
                entry["evidence"] = self._clip(ev, 80) if self.tier == "guardrails" else ev
            out[cid] = entry
        return out

    def to_dict(self) -> dict[str, Any]:
        """Project tier-scoped view of matched architectural entities rather than raw frontmatter (DLT-2026-047)."""
        payload: dict[str, Any] = {
            "query": self.query,
            "tier": self.tier,
            "components": [self._project_component(c, idx) for idx, c in enumerate(self.components)],
            "contracts": [self._project_contract(c) for c in self.contracts],
            "capabilities": [self._project_capability(c, idx) for idx, c in enumerate(self.capabilities)],
            "invariants": [self._project_invariant(i) for i in self.invariants],
            "policies": [self._project_policy(p) for p in self.policies],
            "matched_reasons": (
                {
                    k: v[:1]
                    for k, v in self.matched_reasons.items()
                    if not (self.tier == "guardrails" and self._is_secondary_dependency(k))
                }
                if self._compact_decisions
                else dict(self.matched_reasons)
            ),
        }
        if self.tier in ("guardrails", "deep"):
            payload["provenance_tags"] = self._project_provenance_tags()
        if self.memories:
            payload["memories"] = [self._project_memory(m) for m in self.memories]
        payload["query_latency_ms"] = self.query_latency_ms
        payload["token_estimate"] = self.token_estimate
        return payload

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

    _EXPANDED_REASON_PREFIXES = ("DOM-", "DS-", "PER-", "CLT-", "UIC-", "SCR-", "FLOW-")

    def _prune_matched_reasons(self) -> None:
        retained_ids = {
            e.get("id", "")
            for group in (self.components, self.contracts, self.capabilities, self.invariants, self.policies)
            for e in group
            if e.get("id")
        }
        max_r = 1 if (self.tier == "anchor" and self._compact_decisions) else None
        self.matched_reasons = {
            k: (v[:max_r] if max_r is not None else v)
            for k, v in self.matched_reasons.items()
            if k in retained_ids or k.startswith(self._EXPANDED_REASON_PREFIXES)
        }
        self.provenance_tags = {k: v for k, v in self.provenance_tags.items() if k in retained_ids}

    def _state_signature(self, budget: int) -> tuple[Any, ...]:
        return (
            budget,
            self.tier,
            self._compact_decisions,
            self._compact_primary_decisions,
            self._compact_prose,
            self._compact_metadata,
            self._drop_anchor_decisions,
            self._compact_memories,
            len(self.components),
            len(self.contracts),
            len(self.capabilities),
            len(self.invariants),
            len(self.policies),
            len(self.memories),
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
            pe_len = 0 if self._compact_prose else len(str(item.get("source_id") or ""))
            md_ub = id_len + rule_len + (tb_len + 20 if tb_len else 0) + 32
            json_ub = id_len + rule_len + tb_len + pe_len + 135
            return md_ub, json_ub
        if lst is self.capabilities:
            proj_cap = self._project_capability(item, idx if idx >= 0 else len(lst))
            cap_ub = len(orjson.dumps(proj_cap, option=orjson.OPT_INDENT_2)) + 48
            return cap_ub + 32, cap_ub
        if lst is self.policies:
            proj_pol = self._project_policy(item)
            pol_ub = len(orjson.dumps(proj_pol, option=orjson.OPT_INDENT_2)) + 32
            return pol_ub, pol_ub
        if lst is self.memories:
            proj_mem = self._project_memory(item)
            mem_ub = len(orjson.dumps(proj_mem, option=orjson.OPT_INDENT_2)) + 48
            return mem_ub, mem_ub
        if lst is self.contracts:
            title_len = len(str(item.get("title", "") or cid))
            prot_len = len(str(item.get("protocol", "") or "standard"))
            cnt_len = len(str(item.get("container", "") or "N/A"))
            path_len = len(str(item.get("file_path") or item.get("specification_file") or ""))
            return id_len + title_len + prot_len + cnt_len + path_len + 60, (
                id_len + title_len + prot_len + cnt_len + path_len + 125
            )
        if self._compact_decisions and (idx > 0 or (idx < 0 and len(lst) >= 1)):
            title_len = len(str(item.get("title", "") or cid))
            sp_len = len(str(item.get("source_path", "") or ""))
            fp_len = len(str(item.get("file_path", "") or ""))
            dom_len = len(str(item.get("domain", "") or ""))
            dom_path_len = len(str(item.get("domain_path", "") or ""))
            design_len = len(str(item.get("design", "") or ""))
            base_ub = id_len + title_len + sp_len + fp_len + dom_len + dom_path_len + design_len
            return base_ub + 160, base_ub + 280
        proj = self._project_component(item, idx if idx >= 0 else len(lst))
        comp_ub = len(orjson.dumps(proj, option=orjson.OPT_INDENT_2)) + 64
        return comp_ub, comp_ub

    def _pop_item(self, lst: list[dict[str, Any]], idx: int = -1) -> None:
        item = lst.pop(idx)
        if lst is self.memories and not self.memories:
            self._invalidate_budget_cache()
            return
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
        prov_md_ub = 0
        if eid and not eid.startswith(self._EXPANDED_REASON_PREFIXES):
            removed_reasons = self.matched_reasons.pop(eid, None)
            if removed_reasons:
                reason_ub = len(eid) + sum(len(str(r)) + 12 for r in removed_reasons) + 24
        removed_ptag = self.provenance_tags.pop(eid, None) if eid else None
        if removed_ptag:
            ev_len = len(str(removed_ptag.get("evidence") or ""))
            reason_ub += len(eid) + ev_len + 96
            prov_md_ub = 56
        md_ub, json_ub = self._item_md_and_json_upper_bounds(lst, item, idx)
        md_ub += prov_md_ub
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
            self.__dict__["_md_char_lb"] = md_len
            self.__dict__["_md_lb_sig"] = sig[1:]
            if max(1, md_len // 4) > budget:
                self.__dict__["_md_tok_lb"] = md_len // 4
                self.__dict__["_cached_sig"] = sig
                self.__dict__["_cached_exceeds"] = True
                return True
            md_tokens = self._estimate_text_tokens(md_text)
            self.__dict__["_md_tok_lb"] = md_tokens
            # Reserve 2 tokens headroom for post-enforcement query_latency_ms / token_estimate footer digits
            if md_tokens + 2 > budget:
                self.__dict__["_cached_sig"] = sig
                self.__dict__["_cached_exceeds"] = True
                return True
            self.__dict__["_md_ok"] = True
        else:
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
        self._prune_matched_reasons()
        self._invalidate_budget_cache()

        def _prune_memories_first() -> None:
            # Stage 0: Secondary team memories always yield before any architectural entity or invariant.
            if not self.memories or not self._exceeds_budget(budget):
                return
            for idx in range(len(self.memories) - 1, -1, -1):
                if self.memories[idx].get("match_scope") == "global":
                    self._pop_item(self.memories, idx)
                    if not self._exceeds_budget(budget):
                        return
            if self.memories and not self._compact_memories and self._exceeds_budget(budget):
                self._compact_memories = True
                self._invalidate_budget_cache()
                if not self._exceeds_budget(budget):
                    return
            while self.memories and self._exceeds_budget(budget):
                self._pop_item(self.memories)

        if self.tier == "anchor":
            self.capabilities = []
            self.invariants = []
            self.policies = []
            self._invalidate_budget_cache()
            _prune_memories_first()
            if self._exceeds_budget(budget) and primary_cids:
                self.components = [c for c in self.components if c.get("id") in primary_cids] or self.components[:1]
                self._invalidate_budget_cache()
            while len(self.contracts) > 1 and self._exceeds_budget(budget):
                self._pop_item(self.contracts)
            while len(self.components) > 1 and self._exceeds_budget(budget):
                self._pop_item(self.components)
            if self._exceeds_budget(budget):
                self._compress_verbose_fields_in_place(primary_cids)
            if self._exceeds_budget(budget):
                self._compress_verbose_fields_in_place(primary_cids)
            while len(self.contracts) > 0 and self._exceeds_budget(budget):
                self._pop_item(self.contracts)
            if self._exceeds_budget(budget) and not self._compact_decisions:
                self._compact_decisions = True
                self._invalidate_budget_cache()
            if self._exceeds_budget(budget) and not self._drop_anchor_decisions:
                self._drop_anchor_decisions = True
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

        # Stage 0: Prune secondary team memories (globals -> compact -> anchored tail) before touching architectural entities.
        _prune_memories_first()

        # Stage 1a: Progressively compress verbose prose fields (invariant rules and primary tail DEC-* narratives)
        # before evicting invariants, policies, or capabilities (deferring _compact_metadata to Stage 3).
        if self._exceeds_budget(budget):
            self._compress_verbose_fields_in_place(primary_cids)
        if self._exceeds_budget(budget) and not self._compact_primary_decisions:
            self._compress_verbose_fields_in_place(primary_cids)

        # Stage 1b: Trim 1-hop expanded (non-direct) entities before touching any direct query matches.
        _pop_non_direct(self.invariants, direct_invs, min_len=3)
        _pop_non_direct(
            self.components, primary_cids, min_len=1 if has_non_comp_direct else max(4, len(primary_cids) + 3)
        )
        _pop_non_direct(self.capabilities, direct_caps, min_len=3)

        if not has_non_comp_direct and any(
            len(collect_component_decisions(c)) > 3 for c in self.components if c.get("id") in primary_cids
        ):
            _pop_non_direct(self.components, primary_cids, min_len=max(1, len(primary_cids)))
            _pop_non_direct(self.policies, direct_pols, min_len=1)
            _pop_non_direct(self.capabilities, direct_caps, min_len=1)

        _pop_non_direct(self.capabilities, direct_caps, min_len=2)
        _pop_non_direct(self.policies, direct_pols, min_len=1)
        if self.tier == "guardrails" and self._exceeds_budget(budget):
            _pop_non_direct(self.capabilities, direct_caps, min_len=1)
            if self._exceeds_budget(budget) and not self._compact_decisions:
                self._compact_decisions = True
                self._invalidate_budget_cache()
        _pop_non_direct(self.invariants, direct_invs, min_len=2)
        _pop_non_direct(self.invariants, direct_invs, min_len=1)
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

        # Stage 3: If still over budget, trim remaining non-primary 1-hop components, compact secondary metadata, and compact decisions.
        if self._exceeds_budget(budget):
            _pop_non_direct(self.components, primary_cids, min_len=1)
        if self._exceeds_budget(budget):
            self._compress_verbose_fields_in_place(primary_cids)
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
