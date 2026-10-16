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
    for m in comp.get("components", []) or comp.get("c4_internal_components", []) or []:
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
) -> list[str]:
    """Render embedded architectural decisions (DEC-*) and rejected_alternatives according to the requested tier."""
    if not decisions:
        return []
    lines = ["- **Architectural Decisions (`DEC-*`):**"]
    for dec in decisions:
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
            short_dtext = _clip_text(dtext, 120)
            short_alts = [_clip_text(a, 55) for a in alts[:2]]
            rej_suffix = f" *(Rejected: {', '.join(short_alts)})*" if short_alts else ""
            lines.append(f"  - `{did}` (**{dtitle}**): {short_dtext}{rej_suffix}")
        elif tier == "guardrails":
            grad = f" [Graduated from `{dec['graduated_from']}`]" if dec.get("graduated_from") else ""
            lines.append(f"  - `{did}` (**{dtitle}**){grad}: {_clip_text(dtext, 180)}")
            if dec.get("rationale"):
                lines.append(f"    - *Rationale:* {_clip_text(str(dec['rationale']), 180)}")
            for alt in (dec.get("rejected_alternatives") or [])[:2]:
                if isinstance(alt, dict) and alt.get("option"):
                    reason = _clip_text(str(alt.get("reason_rejected", "")), 140)
                    lines.append(f"    - *Rejected Alternative:* **{alt['option']}** — {reason}")
        else:
            grad = f" [Graduated from `{dec['graduated_from']}`]" if dec.get("graduated_from") else ""
            lines.append(f"  - `{did}` (**{dtitle}**){grad}: {dtext}")
            if dec.get("rationale"):
                lines.append(f"    - *Rationale:* {dec['rationale']}")
            for alt in dec.get("rejected_alternatives") or []:
                if isinstance(alt, dict) and alt.get("option"):
                    reason = alt.get("reason_rejected", "")
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
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        """Render a clean, token-efficient Markdown representation formatted for the requested tier."""
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

            if (
                not is_secondary
                and not getattr(self, "_compact_decisions", False)
                and not (self.tier != "deep" and len(self.components) > 4)
            ):
                decisions = collect_component_decisions(comp)
                if decisions:
                    compact_decs = self.tier == "guardrails" and idx_comp > 0
                    lines.extend(format_decisions_markdown(decisions, tier=self.tier, compact=compact_decs))

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
