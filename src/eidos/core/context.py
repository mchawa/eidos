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

        # 1. Containers & Components
        lines.append(f"## 🧱 C4 Level 2 Containers & Level 3 Components ({len(self.components)})")
        if not self.components:
            lines.append("*No containers or components directly matched.*")
        for comp in self.components:
            cid = comp.get("id", "")
            name = comp.get("name", cid)
            ctype = comp.get("container_type", "module")
            runtime = comp.get("runtime", "N/A")
            pkg = comp.get("package_path", "")
            lines.append(f"### `{cid}` ({name})")
            lines.append(f"- **Container Type:** `{ctype}` | **Runtime:** `{runtime}` | **Package:** `{pkg}`")
            if comp.get("domain_binding"):
                lines.append(f"- **Domain:** `{comp['domain_binding']}`")
            deps = (
                (comp.get("dependencies") or {}).get("internal_containers", [])
                or (comp.get("dependencies") or {}).get("internal_components", [])
            ) if isinstance(comp.get("dependencies"), dict) else []
            if deps:
                lines.append(f"- **Internal Dependencies:** {', '.join(f'`{d}`' for d in deps)}")
            contracts = comp.get("contracts") or {}
            prov = contracts.get("provides", []) if isinstance(contracts, dict) else []
            if prov:
                lines.append(f"- **Provides Contracts:** {', '.join(f'`{p}`' for p in prov)}")
            req = (contracts.get("requires") or contracts.get("consumes") or []) if isinstance(contracts, dict) else []
            if req:
                lines.append(f"- **Requires Contracts:** {', '.join(f'`{r}`' for r in req)}")
            modules = comp.get("components", []) or comp.get("c4_internal_components", [])
            if modules:
                lines.append("- **C4 Level 3 Components:**")
                for m in modules[:6]:
                    mid = f"`{m['id']}` " if m.get("id") else ""
                    mname = m.get("name", "")
                    mresp = m.get("responsibility", "")
                    lines.append(f"  - {mid}**{mname}**: {mresp}")
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
    from eidos.core.graph_index import get_architectural_index

    root = (repo_root or Path.cwd()).resolve()
    target_eidos = (eidos_dir or (root / "eidos")).resolve()

    index = get_architectural_index(eidos_dir=target_eidos, repo_root=root)
    return index.get_tiered_context_slice(
        files=files,
        component=component,
        task=task,
        tier=tier,
    )
