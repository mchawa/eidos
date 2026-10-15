"""Embedded SQLite Architectural Knowledge Graph & Hybrid Context Retrieval Index.

Implements EXP-2026-002:
- Standard library `sqlite3` backed graph store (eidos/.cache/index.db).
- FTS5 virtual table with BM25 ranking for fuzzy and lexical matching.
- Recursive CTE queries for sub-millisecond k-hop blast radius and dependency traversal.
- SHA-256 and mtime dirty-checking for incremental <5ms zero-drift refreshes.
- Tiered context output (Anchor, Guardrails, Deep) for optimal token compression.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.spec.parser import parse_spec_file
from eidos.core.workspace.context import (
    ArchitecturalContextSlice,
    collect_component_decisions,
    format_decisions_markdown,
)
from eidos.core.workspace.project import extract_entity_paths, find_repo_root, match_path_pattern

# Canonical schema version shared across ArchitecturalIndex, SpecValidator, and DriftDetector (DLT-2026-048).
SCHEMA_VERSION = "2.3.0"

# Enforced maximum token budgets per context slice tier (DLT-2026-047).
TIER_TOKEN_BUDGETS: dict[str, int] = {
    "anchor": 300,
    "guardrails": 1000,
    "deep": 2500,
}

# FTS5 ``bm25()`` weights are positional over *every* declared column of ``specs_fts``, including the UNINDEXED
# ``entity_id`` column, so the first weight must be a placeholder. Order: entity_id, title, jtbd_or_rule, body_text,
# keywords, symbols. Short, high-signal fields outrank long prose bodies (DEC-INDEX-FTS-RETRIEVAL-HYGIENE).
FTS_COLUMN_WEIGHTS: tuple[float, ...] = (0.0, 4.0, 2.0, 1.0, 2.0, 2.0)

# Entity types that get_tiered_context_slice can surface from a task prompt. Restricting the BM25 candidate pool to
# these types keeps historical SpecDeltas, explorations, personas, and screens from consuming the top-k slots.
TASK_SLICE_ENTITY_TYPES: tuple[str, ...] = (
    "tech/container",
    "tech/component",
    "tech/contract",
    "product/capability",
    "product/policy",
    "invariant",
    "product/domain",
    "design/system",
)

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def split_identifier(token: str) -> list[str]:
    """Split a snake_case / camelCase / PascalCase identifier into lowercase sub-tokens.

    ``get_tiered_context_slice`` -> ``[get, tiered, context, slice]``; ``ArchitecturalIndex`` ->
    ``[architectural, index]``; ``HTTPServer`` -> ``[http, server]``. Plain words return ``[word]``.
    """
    parts: list[str] = []
    for chunk in token.split("_"):
        if chunk:
            parts.extend(p.lower() for p in _CAMEL_BOUNDARY_RE.split(chunk) if p)
    return parts


def compound_identifier_subtokens(text: str) -> str:
    """Return the de-compounded sub-tokens of every camelCase / snake_case identifier found in ``text``.

    Only compound identifiers contribute (plain words are already tokenized by ``unicode61``), so the output is a
    compact, order-preserving, de-duplicated supplement for the FTS ``symbols`` column.
    """
    seen: dict[str, None] = {}
    for ident in _IDENTIFIER_RE.findall(text or ""):
        parts = split_identifier(ident)
        if len(parts) > 1:
            for part in parts:
                seen.setdefault(part, None)
    return " ".join(seen)


def build_fts_match_query(text: str) -> str:
    """Translate a free-text prompt into a deterministic FTS5 ``OR`` prefix query with de-compounded terms.

    Each ``\\w+`` token contributes its identifier sub-tokens plus, for non-snake_case tokens, the whole lowercased
    token (so ``ArchitecturalIndex`` still prefix-matches the atomic ``architecturalindex`` index token). snake_case
    tokens are *not* passed through whole: FTS5 would re-tokenize them into an order-sensitive phrase query.
    """
    terms: dict[str, None] = {}
    for tok in re.findall(r"\w+", text or ""):
        for part in split_identifier(tok):
            terms.setdefault(part, None)
        if "_" not in tok:
            terms.setdefault(tok.lower(), None)
    return " OR ".join(f'"{term}"*' for term in terms)


@dataclass
class IndexSyncStats:
    """Statistics from an incremental index sync pass."""

    total_specs_scanned: int = 0
    indexed_new: int = 0
    updated_modified: int = 0
    pruned_deleted: int = 0
    duration_ms: float = 0.0
    was_clean: bool = True
    duplicate_id_collisions: list[str] = field(default_factory=list)


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
        if self.tier == "deep":
            jtbd = cap.get("jtbd") or (cap.get("business") or {}).get("jtbd")
            if jtbd:
                out["jtbd"] = jtbd
        return out

    def _project_invariant(self, inv: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": inv.get("id", ""),
            "rule": inv.get("rule", ""),
            "test_binding": inv.get("test_binding", ""),
            "has_test_coverage": bool(inv.get("has_test_coverage")),
        }
        for k in ("policy_id", "capability_id", "component_id"):
            if inv.get(k):
                out[k] = inv[k]
        if self.tier == "deep" and inv.get("matching_tests"):
            out["matching_tests"] = inv["matching_tests"]
        return out

    def _project_policy(self, pol: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": pol.get("id", ""),
            "title": pol.get("title", pol.get("id", "")),
        }
        if pol.get("category"):
            out["category"] = pol["category"]
        if self.tier == "deep" and pol.get("rule"):
            out["rule"] = pol["rule"]
        return out

    def to_dict(self) -> dict[str, Any]:
        """Project tier-scoped fields per entity so JSON output honors the requested tier."""
        return {
            "query": self.query,
            "tier": self.tier,
            "components": [self._project_component(c) for c in self.components],
            "contracts": [self._project_contract(c) for c in self.contracts],
            "capabilities": (
                [] if self.tier == "anchor" else [self._project_capability(c) for c in self.capabilities]
            ),
            "invariants": (
                [] if self.tier == "anchor" else [self._project_invariant(i) for i in self.invariants]
            ),
            "policies": (
                [] if self.tier == "anchor" else [self._project_policy(p) for p in self.policies]
            ),
            "matched_reasons": self.matched_reasons,
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
                        if isinstance(comp.get("dependencies"), dict)
                        else []
                    )
                    if deps:
                        lines.append(f"- **Internal Dependencies:** {', '.join(f'`{d}`' for d in deps)}")
                    contracts = comp.get("contracts") or {}
                    prov = contracts.get("provides", []) if isinstance(contracts, dict) else []
                    if prov:
                        lines.append(f"- **Provides Contracts:** {', '.join(f'`{p}`' for p in prov)}")
                    req = (
                        (contracts.get("requires") or contracts.get("consumes") or [])
                        if isinstance(contracts, dict)
                        else []
                    )
                    if req:
                        lines.append(f"- **Requires Contracts:** {', '.join(f'`{r}`' for r in req)}")

                if self.tier == "deep" and not is_secondary:
                    modules = comp.get("components", []) or comp.get("c4_internal_components", [])
                    if modules:
                        lines.append("- **C4 Level 3 Components:**")
                        for m in modules:
                            mid = f"`{m['id']}` " if m.get("id") else ""
                            if m.get("ref"):
                                lines.append(
                                    f"  - {mid}**{m.get('name', m.get('id', ''))}** (dedicated): `ref: {m['ref']}`"
                                )
                            else:
                                lines.append(f"  - {mid}**{m.get('name', '')}**: {m.get('responsibility', '')}")
            if not is_secondary and not (self.tier != "deep" and len(self.components) > 4):
                decisions = collect_component_decisions(comp)
                if decisions:
                    lines.extend(format_decisions_markdown(decisions, tier=self.tier))
            lines.append("")

        # 2. Bound Contracts
        lines.append(f"## 📜 Bound Interface Contracts ({len(self.contracts)})")
        if not self.contracts:
            lines.append("*No bound contracts in slice.*")
        for ctr in self.contracts:
            cid = ctr.get("id", "")
            title = ctr.get("title", cid)
            protocol = ctr.get("protocol", "standard")
            owner = ctr.get("owning_container") or ctr.get("owning_component", "N/A")
            lines.append(f"### `{cid}`: {title}")
            lines.append(f"- **Protocol:** `{protocol}` | **Owner:** `{owner}`")
            lines.append("")

        # 3. Domain Capabilities (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🎯 Domain Capabilities ({len(self.capabilities)})")
            if not self.capabilities:
                lines.append("*No capabilities directly matched.*")
            for cap in self.capabilities:
                cid = cap.get("id", "")
                title = cap.get("title", cid)
                dom = cap.get("domain", "")
                lines.append(f"### `{cid}`: {title}")
                lines.append(f"- **Domain:** `{dom}` | **Status:** `{cap.get('status', 'active')}`")
                if self.tier == "deep" and cap.get("jtbd"):
                    lines.append(f'- **JTBD:** *"{cap["jtbd"]}"*')
                lines.append("")

        # 4. Invariants (Guardrails & Deep)
        if self.tier in ("guardrails", "deep"):
            lines.append(f"## 🔒 Active Invariants & Verification Guardrails ({len(self.invariants)})")
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


_CODE_EXTS = frozenset({".py", ".ts", ".tsx", ".go", ".rs", ".js", ".jsx", ".java", ".kt", ".kts"})

_EXCLUDED_CODE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".eidos",
        "htmlcov",
        "site",
    }
)

# Every table owned by the derived index cache (dropped and rebuilt on schema version mismatch).
_MANAGED_TABLES = (
    "code_symbols",
    "symbol_mappings",
    "edges",
    "entities",
    "indexed_files",
    "spec_validation_cache",
    "test_binding_cache",
    "specs_fts",
    "metadata",
)

_CACHE_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS indexed_files (
    rel_path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    sha256 TEXT NOT NULL,
    doc_id TEXT,
    doc_type TEXT,
    last_indexed REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_validation_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    doc_json TEXT NOT NULL,
    schema_errors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_binding_cache (
    file_path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    has_fixture INTEGER NOT NULL DEFAULT 0,
    functions_json TEXT NOT NULL,
    bindings_json TEXT NOT NULL,
    file_tokens_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT,
    domain TEXT,
    package_path TEXT,
    runtime TEXT,
    container_type TEXT,
    file_path TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities(domain);

CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    metadata_json TEXT,
    PRIMARY KEY (source_id, target_id, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel_type);

CREATE TABLE IF NOT EXISTS symbol_mappings (
    symbol TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (symbol, entity_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_symbols_symbol ON symbol_mappings(symbol);
CREATE INDEX IF NOT EXISTS idx_symbols_entity ON symbol_mappings(entity_id);

-- AST symbol coordinates (DLT-2026-034), one row set per owning entity of the file ('is_primary' marks the
-- resolved primary owner; files declared by several entities keep rows for each). 'id' preserves AST order.
CREATE TABLE IF NOT EXISTS code_symbols (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_symbol TEXT NOT NULL DEFAULT '',
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL,
    component_id TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 1,
    UNIQUE (file_path, entity_id, kind, parent_symbol, symbol, line_start)
);
CREATE INDEX IF NOT EXISTS idx_code_symbols_symbol ON code_symbols(symbol);
CREATE INDEX IF NOT EXISTS idx_code_symbols_file ON code_symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_code_symbols_entity ON code_symbols(entity_id, kind, symbol);

-- FTS5 full-text search table for BM25 ranking
CREATE VIRTUAL TABLE IF NOT EXISTS specs_fts USING fts5(
    entity_id UNINDEXED,
    title,
    jtbd_or_rule,
    body_text,
    keywords,
    symbols,
    tokenize='porter unicode61'
);
"""


def _unlink_cache_files(db_path: Path) -> None:
    """Remove a corrupted SQLite cache database and its WAL/SHM sidecar files."""
    for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        with contextlib.suppress(OSError):
            if candidate.exists():
                candidate.unlink()


def ensure_cache_schema(conn: sqlite3.Connection) -> None:
    """Ensure ``conn`` has the canonical ``SCHEMA_VERSION`` tables, dropping stale managed tables on version mismatch."""
    existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")}
    if existing_tables & set(_MANAGED_TABLES):
        stored_version = None
        if "metadata" in existing_tables:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()
            stored_version = row[0] if row else None
        if stored_version != SCHEMA_VERSION:
            with conn:
                for table in _MANAGED_TABLES:
                    conn.execute(f"DROP TABLE IF EXISTS {table};")
    with conn:
        conn.executescript(_CACHE_SCHEMA_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?);",
            (SCHEMA_VERSION,),
        )


def open_cache_connection(db_path: Path) -> sqlite3.Connection:
    """Open a WAL-configured SQLite cache connection with unified schema versioning and corruption recovery."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA mmap_size = 268435456;")
            ensure_cache_schema(conn)
            return conn
        except sqlite3.DatabaseError:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            if attempt == 0:
                _unlink_cache_files(db_path)
                continue
            raise
    raise sqlite3.DatabaseError(f"Failed to initialize SQLite cache at {db_path}")


# symbol_mappings kinds that are *not* AST symbols (everything else resolves to code_symbols coordinates).
_NON_AST_SYMBOL_KINDS = frozenset(
    {
        "specification_file",
        "contract_path",
        "interface_method",
        "interface_signature",
        "component_ref",
        "test_binding",
        "package_path",
        "owned_path",
        "module_name",
        "source_path",
        "component_id",
        "interface_command",
        "inferred_path",
    }
)


def _normalize_rel_path(path: str) -> str:
    norm = path.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


class _CodeOwnershipResolver:
    """Deterministic, spec-order-independent attribution of source files to owning containers/components.

    Built once per sync from the persisted specification state, so every file resolves identically no matter
    which spec was processed first. Precedence (DLT-2026-034):

    1. exact component ``source_path`` declared by any container (lowest container id wins; every other declaring
       entity is kept as a secondary owner, see ``owners()``);
    2. highest-scoring ``owned_path`` pattern (``match_path_pattern``);
    3. longest ``package_path`` prefix (path-boundary aware) across all entries of all containers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._component_sources: dict[str, tuple[str, str]] = {}
        self._declared_by: dict[str, set[str]] = {}
        self._declared_components: dict[tuple[str, str], str] = {}
        for row in conn.execute("SELECT id, data_json FROM entities WHERE type = 'tech/container' ORDER BY id;"):
            try:
                fm = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            for comp in fm.get("components", []) or fm.get("c4_internal_components", []) or []:
                if not isinstance(comp, dict):
                    continue
                raw = comp.get("source_path")
                paths = raw if isinstance(raw, list) else ([raw] if raw else [])
                for sp in paths:
                    norm = _normalize_rel_path(str(sp))
                    if not norm or any(ch in norm for ch in "*?["):
                        continue
                    self._component_sources.setdefault(norm, (row["id"], str(comp.get("id") or "")))
                    self._declared_by.setdefault(norm, set()).add(row["id"])
                    self._declared_components.setdefault((norm, row["id"]), str(comp.get("id") or ""))

        self._owned: list[tuple[str, str]] = [
            (r["symbol"], r["entity_id"])
            for r in conn.execute(
                "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'owned_path' ORDER BY entity_id, symbol;"
            )
        ]
        self._packages: list[tuple[str, str]] = []
        for r in conn.execute(
            "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'package_path' ORDER BY entity_id, symbol;"
        ):
            norm = _normalize_rel_path(r["symbol"])
            if not norm or any(ch in norm for ch in "*?["):
                continue
            self._packages.append((norm, r["entity_id"]))
            if "." in norm.rsplit("/", 1)[-1]:  # literal file declared as package_path
                self._declared_by.setdefault(norm, set()).add(r["entity_id"])

    def package_dirs(self) -> set[str]:
        """All literal ``package_path`` entries (callers filter for existing directories)."""
        return {pp for pp, _ in self._packages}

    def declared_owners(self, rel_path: str) -> set[str]:
        """Entities that explicitly declare ``rel_path`` as a source/package file."""
        return self._declared_by.get(_normalize_rel_path(rel_path), set())

    def declared_files(self) -> set[str]:
        """Every literal source file explicitly declared by any entity."""
        return set(self._declared_by)

    def owners(self, rel_path: str) -> list[tuple[str, str]]:
        """``(entity_id, component_id)`` owners of a file: the resolved primary owner first, then every other entity
        that explicitly declares the file (sorted by id). Empty when the file is unowned."""
        primary, component_id = self.resolve(rel_path)
        if not primary:
            return []
        rel = _normalize_rel_path(rel_path)
        result = [(primary, component_id)]
        for entity_id in sorted(self._declared_by.get(rel, set()) - {primary}):
            result.append((entity_id, self._declared_components.get((rel, entity_id), "")))
        return result

    def resolve(self, rel_path: str) -> tuple[str | None, str]:
        """Return ``(owning entity id or None, component id or '')`` for a repo-relative source path."""
        rel = _normalize_rel_path(rel_path)
        hit = self._component_sources.get(rel)
        if hit:
            return hit

        best_score = 0
        owner: str | None = None
        for pattern, entity_id in self._owned:
            score = match_path_pattern(rel, pattern)
            if score > best_score:
                best_score, owner = score, entity_id
        if owner:
            return owner, ""

        best_len = -1
        for pkg, entity_id in self._packages:
            if (rel == pkg or rel.startswith(pkg + "/")) and len(pkg) > best_len:
                best_len, owner = len(pkg), entity_id
        return owner, ""


class ArchitecturalIndex:
    """Embedded SQLite Architectural Knowledge Graph & Hybrid Retrieval Index."""

    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(
        self,
        db_path: Path | None = None,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
    ) -> None:
        if repo_root is not None:
            self.root = Path(repo_root).resolve()
        elif (eidos_dir is not None and (Path(eidos_dir) / "system.adoc").exists()) or (
            eidos_dir is not None
            and Path(eidos_dir).name == "eidos"
            and not any(p in Path(eidos_dir).parts for p in (".agents", "plugins", "assets"))
        ):
            self.root = Path(eidos_dir).resolve().parent
        else:
            self.root = find_repo_root(Path(eidos_dir).resolve() if eidos_dir else None)

        self.eidos_dir = (eidos_dir or (self.root / "eidos")).resolve()

        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            cache_dir = self.eidos_dir / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_dir / "index.db"

        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = open_cache_connection(self.db_path)
        return self._conn

    def _init_db(self) -> None:
        """Create tables, indexes, and FTS5 virtual tables with corruption recovery."""
        self._get_connection()

    # ------------------------------------------------------------------
    # Code symbol persistence helpers (DLT-2026-034)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_test_code_path(rel_path: str) -> bool:
        """Return True for test files (by name or by a tests/test/__tests__ directory segment)."""
        parts = rel_path.replace("\\", "/").split("/")
        name = parts[-1]
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or any(part in ("tests", "test", "__tests__") for part in parts[:-1])
        )

    def _store_code_symbols(
        self,
        conn: sqlite3.Connection,
        rel_path: str,
        owners: list[tuple[str, str]],
        symbols: list[Any],
    ) -> None:
        """Replace all persisted symbols of one source file (delete-then-insert) and keep lookups consistent.

        ``owners`` lists ``(entity_id, component_id)`` pairs, primary owner first; every owner gets the file's AST
        coordinates in ``code_symbols`` and its lookup rows in ``symbol_mappings``. Mapping rows that were only
        provided by the file's previous symbols/owners are pruned, so edits, deletions and ownership changes leave
        nothing stale behind.
        """
        previous = conn.execute(
            "SELECT DISTINCT entity_id, kind, symbol, parent_symbol FROM code_symbols WHERE file_path = ?;",
            (rel_path,),
        ).fetchall()
        conn.execute("DELETE FROM code_symbols WHERE file_path = ?;", (rel_path,))

        for position, (entity_id, component_id) in enumerate(owners):
            for sym in symbols:
                parent = sym.parent_symbol or ""
                conn.execute(
                    """
                    INSERT OR IGNORE INTO code_symbols
                    (file_path, symbol, kind, parent_symbol, line_start, line_end, signature, entity_id,
                     component_id, is_primary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        rel_path,
                        sym.name,
                        sym.kind,
                        parent,
                        sym.line_start,
                        sym.line_end,
                        sym.signature or "",
                        entity_id,
                        component_id,
                        1 if position == 0 else 0,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, ?);",
                    (sym.name, entity_id, sym.kind),
                )
                if parent:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, 'method');",
                        (f"{parent}.{sym.name}", entity_id),
                    )

        for old in previous:
            old_entity, old_kind, old_symbol, old_parent = (
                old["entity_id"],
                old["kind"],
                old["symbol"],
                old["parent_symbol"],
            )
            if not conn.execute(
                "SELECT 1 FROM code_symbols WHERE entity_id = ? AND kind = ? AND symbol = ? LIMIT 1;",
                (old_entity, old_kind, old_symbol),
            ).fetchone():
                conn.execute(
                    "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = ?;",
                    (old_symbol, old_entity, old_kind),
                )
            if (
                old_parent
                and not conn.execute(
                    "SELECT 1 FROM code_symbols WHERE entity_id = ? AND parent_symbol = ? AND symbol = ? LIMIT 1;",
                    (old_entity, old_parent, old_symbol),
                ).fetchone()
            ):
                conn.execute(
                    "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = 'method';",
                    (f"{old_parent}.{old_symbol}", old_entity),
                )

    def _restore_code_mappings(self, conn: sqlite3.Connection, entity_id: str) -> None:
        """Re-create ``symbol_mappings`` rows for an entity from persisted code state (no source re-parsing)."""
        conn.execute(
            """
            INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
            SELECT DISTINCT file_path, entity_id, 'source_path' FROM code_symbols WHERE entity_id = ?
            UNION
            SELECT rel_path, doc_id, 'source_path' FROM indexed_files
            WHERE doc_type = 'code/source' AND doc_id = ?;
            """,
            (entity_id, entity_id),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
            SELECT symbol, entity_id, kind FROM code_symbols WHERE entity_id = ?;
            """,
            (entity_id,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind)
            SELECT parent_symbol || '.' || symbol, entity_id, 'method' FROM code_symbols
            WHERE entity_id = ? AND parent_symbol != '';
            """,
            (entity_id,),
        )

    def _index_source_file(
        self,
        conn: sqlite3.Connection,
        ast_idx: Any,
        path: Path,
        rel_path: str,
        mtime: float,
        sha: str,
        resolver: _CodeOwnershipResolver,
    ) -> None:
        """Index one non-test source file for all its resolved owners (one AST parse)."""
        owners = resolver.owners(rel_path)
        owner_ids = {entity_id for entity_id, _ in owners}
        # Drop source_path rows of entities that no longer own or declare the file.
        keep = resolver.declared_owners(rel_path) | owner_ids
        for stale in conn.execute(
            "SELECT entity_id FROM symbol_mappings WHERE symbol = ? AND kind = 'source_path';", (rel_path,)
        ).fetchall():
            if stale["entity_id"] not in keep:
                conn.execute(
                    "DELETE FROM symbol_mappings WHERE symbol = ? AND entity_id = ? AND kind = 'source_path';",
                    (rel_path, stale["entity_id"]),
                )
        for entity_id in sorted(owner_ids):
            conn.execute(
                "INSERT OR IGNORE INTO symbol_mappings (symbol, entity_id, kind) VALUES (?, ?, 'source_path');",
                (rel_path, entity_id),
            )
        self._store_code_symbols(conn, rel_path, owners, ast_idx.extract_symbols(path) if owners else [])
        conn.execute(
            """
            INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
            VALUES (?, ?, ?, ?, 'code/source', ?);
            """,
            (rel_path, mtime, sha, owners[0][0] if owners else "source", time.time()),
        )

    def _reconcile_code_ownership(
        self,
        conn: sqlite3.Connection,
        ast_idx: Any,
        resolver: _CodeOwnershipResolver,
        processed: set[str],
    ) -> None:
        """Recursively index every container package directory and re-attribute files whose owners changed.

        Runs after all specifications are loaded, so ownership is independent of spec processing order.
        Candidates are all non-test code files under any declared ``package_path`` (excluded directories pruned),
        every explicitly declared source file, and every already-indexed source file. A file is (re)indexed when it
        is unknown, its primary owner differs from the manifest, or its persisted owner set differs from the
        resolved one.
        """
        candidates: dict[str, Path] = {}
        for pkg in sorted(resolver.package_dirs()):
            abs_pkg = (self.root / pkg).resolve()
            if not abs_pkg.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(str(abs_pkg)):
                dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_CODE_DIRS and not d.startswith("."))
                for fn in sorted(filenames):
                    p_file = Path(dirpath) / fn
                    if p_file.suffix not in _CODE_EXTS:
                        continue
                    try:
                        rel_code = str(p_file.relative_to(self.root)).replace("\\", "/")
                    except ValueError:
                        continue
                    candidates.setdefault(rel_code, p_file)

        manifest = {
            r["rel_path"]: r["doc_id"]
            for r in conn.execute("SELECT rel_path, doc_id FROM indexed_files WHERE doc_type = 'code/source';")
        }
        for rel_code in sorted(set(manifest) | resolver.declared_files()):
            if rel_code not in candidates:
                abs_known = (self.root / rel_code).resolve()
                if abs_known.is_file() and abs_known.suffix in _CODE_EXTS:
                    candidates[rel_code] = abs_known

        for rel_code, p_file in sorted(candidates.items()):
            if rel_code in processed or self._is_test_code_path(rel_code):
                continue
            owners = resolver.owners(rel_code)
            if rel_code in manifest and manifest[rel_code] == (owners[0][0] if owners else "source"):
                stored = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT entity_id FROM code_symbols WHERE file_path = ?;", (rel_code,)
                    )
                }
                if not stored or stored == {entity_id for entity_id, _ in owners}:
                    continue
            stat = p_file.stat()
            self._index_source_file(
                conn,
                ast_idx,
                p_file,
                rel_code,
                stat.st_mtime,
                hashlib.sha256(p_file.read_bytes()).hexdigest(),
                resolver,
            )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def is_dirty(self) -> tuple[bool, list[Path], list[Path]]:
        """Perform sub-5ms dirty checking comparing filesystem mtime with indexed state.

        Checks both living specifications in eidos/ and tracked source code and test files.
        When a file's mtime changed (e.g. git checkout or touch) but its SHA-256 digest matches
        cached_sha, refreshes indexed_files.mtime in place so subsequent checks do not re-hash.

        Returns:
            (is_dirty, modified_or_new_files, deleted_rel_paths)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT rel_path, mtime, sha256 FROM indexed_files;")
        db_files = {row["rel_path"]: (row["mtime"], row["sha256"]) for row in cursor.fetchall()}

        disk_files: set[str] = set()
        to_update: list[Path] = []
        mtime_refreshes: list[tuple[float, float, str]] = []
        now_ts = time.time()

        if not self.eidos_dir.exists():
            deleted = [Path(rel) for rel in db_files]
            return len(deleted) > 0, [], deleted

        root_str = str(self.root)

        def _check_file(file_path: Path, rel_key: str, st_mtime: float) -> None:
            cached = db_files.get(rel_key)
            if not cached:
                to_update.append(file_path)
            elif abs(st_mtime - cached[0]) > 0.001:
                try:
                    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                except OSError:
                    return
                if digest != cached[1]:
                    to_update.append(file_path)
                else:
                    mtime_refreshes.append((st_mtime, now_ts, rel_key))
                    db_files[rel_key] = (st_mtime, cached[1])

        # 1. Living specifications in eidos/ (.adoc)
        for dirpath, dirnames, filenames in os.walk(str(self.eidos_dir)):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "by-status"]
            for fn in filenames:
                if not fn.endswith(".adoc") or fn.startswith("_") or fn.lower() == "readme.adoc":
                    continue
                full_path = os.path.join(dirpath, fn)
                try:
                    rel_p = os.path.relpath(full_path, root_str).replace("\\", "/")
                except ValueError:
                    rel_p = full_path.replace("\\", "/")
                disk_files.add(rel_p)
                stat = os.stat(full_path)
                _check_file(Path(full_path), rel_p, stat.st_mtime)

        # 2. Tracked code and test files
        code_exts = _CODE_EXTS
        tracked_code_dirs: set[Path] = set()

        # entities.package_path only holds the first entry of a multi-path container; every declared entry is also
        # persisted as a kind='package_path' mapping (DLT-2026-034).
        package_paths: set[str] = set()
        cursor.execute("SELECT DISTINCT package_path FROM entities WHERE package_path != '';")
        package_paths.update(row[0].strip() for row in cursor.fetchall())
        cursor.execute("SELECT DISTINCT symbol FROM symbol_mappings WHERE kind = 'package_path';")
        package_paths.update(row[0].strip() for row in cursor.fetchall())
        for pp in sorted(package_paths):
            if pp:
                p_abs = (self.root / pp).resolve()
                if p_abs.is_dir():
                    tracked_code_dirs.add(p_abs)
                elif p_abs.is_file() and p_abs.suffix in code_exts:
                    try:
                        rel_code = str(p_abs.relative_to(self.root)).replace("\\", "/")
                    except ValueError:
                        rel_code = str(p_abs).replace("\\", "/")
                    disk_files.add(rel_code)
                    stat = p_abs.stat()
                    _check_file(p_abs, rel_code, stat.st_mtime)

        for std_dir in ("src", "tests", "test"):
            cand = (self.root / std_dir).resolve()
            if cand.is_dir():
                tracked_code_dirs.add(cand)

        for rel_tracked in list(db_files.keys()):
            if not rel_tracked.endswith(".adoc") and rel_tracked not in disk_files:
                abs_tracked = (self.root / rel_tracked).resolve()
                if abs_tracked.is_file():
                    disk_files.add(rel_tracked)
                    stat = abs_tracked.stat()
                    _check_file(abs_tracked, rel_tracked, stat.st_mtime)

        excluded_dirs = _EXCLUDED_CODE_DIRS
        for code_dir in tracked_code_dirs:
            if not code_dir.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(str(code_dir)):
                dirnames[:] = [d for d in dirnames if d not in excluded_dirs and not d.startswith(".")]
                for fn in filenames:
                    p_file = Path(dirpath) / fn
                    if p_file.suffix not in code_exts:
                        continue
                    try:
                        rel_code = str(p_file.relative_to(self.root)).replace("\\", "/")
                    except ValueError:
                        rel_code = str(p_file).replace("\\", "/")
                    if rel_code in disk_files:
                        continue
                    disk_files.add(rel_code)

                    stat = p_file.stat()
                    _check_file(p_file, rel_code, stat.st_mtime)

        if mtime_refreshes:
            with conn:
                conn.executemany(
                    "UPDATE indexed_files SET mtime = ?, last_indexed = ? WHERE rel_path = ?;",
                    mtime_refreshes,
                )

        deleted = [Path(rel) for rel in db_files.keys() - disk_files]
        is_dirty = len(to_update) > 0 or len(deleted) > 0
        return is_dirty, to_update, deleted

    def _ensure_synced(self) -> None:
        """Lazily ensure the index is synchronized (<2ms check when clean)."""
        now = time.monotonic()
        last_check = getattr(self, "_last_synced_check", 0.0)
        if now - last_check < 0.2:
            return
        dirty_state = self.is_dirty()
        if dirty_state[0]:
            self.sync(dirty_state=dirty_state)
        self._last_synced_check = time.monotonic()

    def sync(
        self,
        force: bool = False,
        dirty_state: tuple[bool, list[Path], list[Path]] | None = None,
        strict: bool = False,
    ) -> IndexSyncStats:
        """Incrementally synchronize the index with specifications and code on disk."""
        t0 = time.perf_counter()
        stats = IndexSyncStats()

        if force:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM indexed_files;")
                conn.execute("DELETE FROM entities;")
                conn.execute("DELETE FROM edges;")
                conn.execute("DELETE FROM symbol_mappings;")
                conn.execute("DELETE FROM code_symbols;")
                conn.execute("DELETE FROM specs_fts;")

        if force or dirty_state is None:
            dirty, to_update, deleted = self.is_dirty()
        else:
            dirty, to_update, deleted = dirty_state
        if not dirty and not force:
            stats.duration_ms = (time.perf_counter() - t0) * 1000
            stats.was_clean = True
            self._last_synced_check = time.monotonic()
            return stats

        stats.was_clean = False
        conn = self._get_connection()

        with conn:
            # Handle deletions
            specs_deleted = False
            for d in deleted:
                d_rel = str(d).replace("\\", "/")
                row = conn.execute("SELECT doc_id, doc_type FROM indexed_files WHERE rel_path = ?", (d_rel,)).fetchone()
                if row and row["doc_type"] in ("code/source", "code/test"):
                    self._store_code_symbols(conn, d_rel, [], [])  # prune coordinates + orphaned AST mappings
                    conn.execute("DELETE FROM symbol_mappings WHERE symbol = ?;", (d_rel,))
                    conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
                elif row and row["doc_id"]:
                    doc_id = row["doc_id"]
                    specs_deleted = True
                    self._delete_spec_fts_rows(conn, doc_id, d_rel)
                    conn.execute(
                        "DELETE FROM entities WHERE id = ? OR (file_path = ? AND type = 'invariant')",
                        (doc_id, d_rel),
                    )
                    conn.execute("DELETE FROM symbol_mappings WHERE entity_id = ?", (doc_id,))
                    conn.execute("DELETE FROM code_symbols WHERE entity_id = ?", (doc_id,))
                    conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", (doc_id, doc_id))
                    conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
                else:
                    conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (d_rel,))
                stats.pruned_deleted += 1

            # Partition updates into specifications (.adoc) and code/test files
            spec_updates = [p for p in to_update if p.suffix == ".adoc"]
            code_updates = [p for p in to_update if p.suffix != ".adoc"]

            stats.total_specs_scanned = len(spec_updates)
            parsed_specs: list[tuple[Path, str, float, str, Any]] = []
            for p in spec_updates:
                try:
                    rel_p = str(p.relative_to(self.root)).replace("\\", "/")
                except ValueError:
                    rel_p = str(p).replace("\\", "/")

                try:
                    mtime = p.stat().st_mtime
                    content = p.read_bytes()
                    doc = parse_spec_file(p)
                except OSError:
                    continue
                sha = hashlib.sha256(content).hexdigest()
                parsed_specs.append((p, rel_p, mtime, sha, doc))

            # Pre-pass: evict previous doc_id rows for every updated spec path before inserting new/renamed entities
            # so path-in-place ID renames leave zero ghost entities, FTS rows, symbol mappings, or edges (DLT-2026-048).
            updated_rel_paths: set[str] = set()
            for _p, rel_p, _mtime, _sha, doc in parsed_specs:
                new_doc_id = doc.doc_id or _p.stem
                existing_row = conn.execute(
                    "SELECT doc_id FROM indexed_files WHERE rel_path = ?;",
                    (rel_p,),
                ).fetchone()
                if existing_row is not None:
                    updated_rel_paths.add(rel_p)
                    old_doc_id = existing_row["doc_id"]
                    if old_doc_id:
                        self._delete_spec_fts_rows(conn, old_doc_id, rel_p)
                        conn.execute(
                            "DELETE FROM entities WHERE (id = ? AND file_path = ?) OR (file_path = ? AND type = 'invariant');",
                            (old_doc_id, rel_p, rel_p),
                        )
                        conn.execute("DELETE FROM symbol_mappings WHERE entity_id = ?;", (old_doc_id,))
                        if old_doc_id != new_doc_id:
                            conn.execute("DELETE FROM code_symbols WHERE entity_id = ?;", (old_doc_id,))
                            conn.execute(
                                "DELETE FROM edges WHERE source_id = ? OR target_id = ?;",
                                (old_doc_id, old_doc_id),
                            )
                    if new_doc_id != old_doc_id:
                        self._delete_spec_fts_rows(conn, new_doc_id, rel_p)
                        conn.execute(
                            "DELETE FROM entities WHERE (id = ? AND file_path = ?) OR (file_path = ? AND type = 'invariant');",
                            (new_doc_id, rel_p, rel_p),
                        )
                        conn.execute("DELETE FROM symbol_mappings WHERE entity_id = ?;", (new_doc_id,))

            for p, rel_p, mtime, sha, doc in parsed_specs:
                doc_id = doc.doc_id or p.stem
                fm = doc.frontmatter or {}
                doc_type = doc.doc_type or "unknown"

                if rel_p in updated_rel_paths:
                    stats.updated_modified += 1
                else:
                    stats.indexed_new += 1

                # Detect and reject duplicate entity ID collisions across distinct specification files (DLT-2026-048)
                collision = conn.execute(
                    "SELECT file_path FROM entities WHERE id = ? AND file_path != ?;",
                    (doc_id, rel_p),
                ).fetchone()
                if collision is not None:
                    msg = (
                        f"Duplicate entity ID '{doc_id}' in '{rel_p}' "
                        f"(already indexed from '{collision['file_path']}')"
                    )
                    stats.duplicate_id_collisions.append(msg)
                    if strict:
                        raise ValueError(msg)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                        VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (rel_p, mtime, sha, doc_id, doc_type, time.time()),
                    )
                    continue

                # 1. Insert entity
                title = doc.title or fm.get("title", doc_id)
                status = fm.get("status", "active")
                domain = fm.get("domain", fm.get("domain_binding", ""))
                raw_pkg = fm.get("package_path", fm.get("source_path", ""))
                pkg_list = (
                    [str(x).strip() for x in raw_pkg if str(x).strip()]
                    if isinstance(raw_pkg, list)
                    else ([str(raw_pkg).strip()] if str(raw_pkg).strip() else [])
                )
                package_path = pkg_list[0] if pkg_list else ""
                runtime = fm.get("runtime", "")
                container_type = fm.get("container_type", "")

                conn.execute(
                    """
                    INSERT INTO entities
                    (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        doc_id,
                        doc_type,
                        title,
                        status,
                        domain,
                        package_path,
                        runtime,
                        container_type,
                        rel_p,
                        json.dumps(fm),
                    ),
                )

                # 2. Extract spec symbol mappings & child invariants (spec edges are rebuilt across all entities below)
                if doc_type == "tech/contract":
                    spec_file = fm.get("specification_file")
                    if spec_file:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'specification_file');",
                            (spec_file, doc_id),
                        )
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'contract_path');", (rel_p, doc_id)
                    )

                biz = fm.get("business", {})
                tech = fm.get("technical", {})
                invariants = []
                if isinstance(tech, dict):
                    invariants = tech.get("invariants", []) or []

                # Policy invariants
                if doc_type == "product/policy":
                    invariants = fm.get("invariants", []) or []

                # Dedicated Component bindings
                if doc_type == "tech/component":
                    ifaces = fm.get("interfaces", {})
                    if isinstance(ifaces, dict):
                        for prov in ifaces.get("provides", []):
                            if isinstance(prov, dict) and prov.get("signature"):
                                sig = prov["signature"]
                                meth = sig.split("(")[0].strip()
                                conn.execute(
                                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_method');",
                                    (meth, doc_id),
                                )
                                conn.execute(
                                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_signature');",
                                    (sig, doc_id),
                                )

                    # Component invariants
                    invariants = fm.get("invariants", []) or []

                # Container references to dedicated components
                if doc_type == "tech/container":
                    for comp_entry in fm.get("components", []):
                        if isinstance(comp_entry, dict) and comp_entry.get("ref"):
                            ref_path = comp_entry["ref"]
                            cid = comp_entry.get("id")
                            if cid:
                                conn.execute(
                                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'component_ref');",
                                    (ref_path, cid),
                                )

                # Insert invariants as child entities
                for inv in invariants:
                    iid = inv.get("id")
                    if iid:
                        inv_collision = conn.execute(
                            "SELECT file_path FROM entities WHERE id = ? AND file_path != ?;",
                            (iid, rel_p),
                        ).fetchone()
                        if inv_collision is not None:
                            inv_msg = (
                                f"Duplicate invariant ID '{iid}' in '{rel_p}' "
                                f"(already indexed from '{inv_collision['file_path']}')"
                            )
                            stats.duplicate_id_collisions.append(inv_msg)
                            if strict:
                                raise ValueError(inv_msg)
                            continue
                        rule = inv.get("rule", "")
                        test = inv.get("test_binding", "")
                        test_file = test.split("::")[0] if test else ""
                        test_exists = bool(test_file and (self.root / test_file).exists())
                        matching = [test] if test else []
                        for (ast_match,) in conn.execute(
                            "SELECT source_id FROM edges WHERE rel_type = 'asserts' AND target_id = ? AND instr(source_id, '::') > 0;",
                            (iid,),
                        ):
                            if ast_match not in matching:
                                matching.append(ast_match)
                        inv_data = {
                            "id": iid,
                            "rule": rule,
                            "test_binding": test,
                            "source_id": doc_id,
                            "capability_id": doc_id if doc_type == "product/capability" else None,
                            "policy_id": doc_id if doc_type == "product/policy" else None,
                            "component_id": doc_id if doc_type == "tech/component" else None,
                            "has_test_coverage": test_exists or bool(matching and not test),
                            "matching_tests": matching,
                        }
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO entities
                            (id, type, title, status, domain, package_path, runtime, container_type, file_path, data_json)
                            VALUES (?, 'invariant', ?, 'active', ?, '', '', '', ?, ?);
                            """,
                            (iid, rule, domain, rel_p, json.dumps(inv_data)),
                        )

                        # Child invariants get their own FTS row so task prompts can match INV-* rules directly.
                        test_str = str(test or "")
                        rule_str = str(rule or "")
                        test_func = test_str.split("::", 1)[1] if "::" in test_str else ""
                        conn.execute("DELETE FROM specs_fts WHERE entity_id = ?", (iid,))
                        conn.execute(
                            """
                            INSERT INTO specs_fts (entity_id, title, jtbd_or_rule, body_text, keywords, symbols)
                            VALUES (?, '', ?, ?, ?, ?);
                            """,
                            (
                                iid,
                                rule_str,
                                str(title or ""),
                                " ".join(str(x) for x in (iid, "invariant", domain, doc_id) if x),
                                " ".join(
                                    x for x in (test_str, compound_identifier_subtokens(f"{test_func} {rule_str}")) if x
                                ),
                            ),
                        )

                        if test:
                            conn.execute(
                                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                                (test_file, iid),
                            )
                            abs_test = (self.root / test_file).resolve()
                            if abs_test.is_file():
                                try:
                                    rel_tf = str(abs_test.relative_to(self.root)).replace("\\", "/")
                                except ValueError:
                                    rel_tf = str(abs_test).replace("\\", "/")
                                conn.execute(
                                    """
                                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                                    VALUES (?, ?, ?, ?, 'code/test', ?);
                                    """,
                                    (
                                        rel_tf,
                                        abs_test.stat().st_mtime,
                                        hashlib.sha256(abs_test.read_bytes()).hexdigest(),
                                        iid,
                                        time.time(),
                                    ),
                                )

                # 3. Symbol mappings
                for pkg_entry in pkg_list:
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'package_path');", (pkg_entry, doc_id)
                    )
                for owned_pat in extract_entity_paths(fm):
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'owned_path');", (owned_pat, doc_id)
                    )

                # Internal C4 modules & components: record declarations only. Source files are indexed exactly
                # once per sync by _reconcile_code_ownership() after every spec is loaded, so ownership (including
                # files declared by several entities) never depends on spec processing order (DLT-2026-034).
                all_modules = list(fm.get("components", []) or fm.get("c4_internal_components", []))
                for module in all_modules:
                    mname = module.get("name")
                    raw_mpath = module.get("source_path")
                    mpath_list = (
                        [str(x).strip() for x in raw_mpath if str(x).strip()]
                        if isinstance(raw_mpath, list)
                        else ([str(raw_mpath).strip()] if raw_mpath and str(raw_mpath).strip() else [])
                    )
                    mid = module.get("id")
                    if mname:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'module_name');", (mname, doc_id)
                        )
                    for mpath in mpath_list:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'source_path');", (mpath, doc_id)
                        )
                    if mid:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'component_id');", (mid, doc_id)
                        )

                for pkg_item in pkg_list:
                    if any(ch in pkg_item for ch in "*?[") or not (self.root / pkg_item).is_file():
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'source_path');", (pkg_item, doc_id)
                    )

                # Re-attach previously indexed source files and AST symbols that the per-entity DELETE above removed.
                self._restore_code_mappings(conn, doc_id)

                # Interface commands
                if isinstance(fm.get("contracts"), dict):
                    for iface in fm["contracts"].get("interfaces", []):
                        cmd = iface.split()[0]
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'interface_command');", (cmd, doc_id)
                        )

                # 4. Insert into FTS5
                jtbd_or_rule = biz.get("jtbd", fm.get("rule", "")) if isinstance(biz, dict) else fm.get("rule", "")
                body_clean = re.sub(r"[{}\[\]=:#*`]", " ", doc.body or "")
                embedded_decisions = collect_component_decisions(fm)
                decision_ids: list[str] = []
                decision_texts: list[str] = []
                for dec in embedded_decisions:
                    did = str(dec.get("id", "")).strip()
                    if did:
                        decision_ids.append(did)
                    grad_ref = str(dec.get("graduated_from", "")).strip()
                    if grad_ref:
                        decision_ids.append(grad_ref)
                    for field_key in ("title", "decision", "rationale"):
                        fval = str(dec.get(field_key, "")).strip()
                        if fval:
                            decision_texts.append(fval)
                    for alt in dec.get("rejected_alternatives") or []:
                        if isinstance(alt, dict):
                            for alt_key in ("option", "reason_rejected"):
                                aval = str(alt.get(alt_key, "")).strip()
                                if aval:
                                    decision_texts.append(aval)
                if decision_texts:
                    body_clean = f"{body_clean} " + re.sub(r"[{}\[\]=:#*`]", " ", " ".join(decision_texts))
                keywords = " ".join([doc_id, doc_type, domain, container_type, package_path, *decision_ids])
                symbols = " ".join(
                    [m.get("name", "") for m in (fm.get("components", []) or fm.get("c4_internal_components", []))]
                )
                # unicode61 keeps camelCase identifiers atomic; index their de-compounded sub-tokens alongside.
                decompounded = compound_identifier_subtokens(
                    f"{title or ''} {jtbd_or_rule or ''} {body_clean} {symbols}"
                )
                if decompounded:
                    symbols = f"{symbols} {decompounded}".strip()

                conn.execute(
                    """
                    INSERT INTO specs_fts (entity_id, title, jtbd_or_rule, body_text, keywords, symbols)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (doc_id, title, jtbd_or_rule, body_clean, keywords, symbols),
                )

                # Update indexed_files manifest
                conn.execute(
                    """
                    INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (rel_p, mtime, sha, doc_id, doc_type, time.time()),
                )

            if spec_updates or specs_deleted:
                self._rebuild_spec_edges(conn)
                conn.execute("DELETE FROM symbol_mappings WHERE kind = 'inferred_path';")
                # Propagate container owned_path/source_path to bound design/client (CLT-*) and design/system (DS-*)
                client_cnt_rows = conn.execute(
                    "SELECT source_id, target_id FROM edges WHERE rel_type = 'implemented_by' AND source_id LIKE 'CLT-%';"
                ).fetchall()
                for clt_id, cnt_id in client_cnt_rows:
                    cnt_paths = [
                        r[0]
                        for r in conn.execute(
                            """
                            SELECT DISTINCT symbol FROM symbol_mappings
                            WHERE kind IN ('owned_path', 'package_path', 'source_path')
                              AND (entity_id = ? OR entity_id IN (
                                  SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'contains_component'
                              ));
                            """,
                            (cnt_id, cnt_id),
                        ).fetchall()
                    ]
                    if not cnt_paths:
                        continue
                    for pat in cnt_paths:
                        conn.execute(
                            "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'inferred_path');",
                            (pat, clt_id),
                        )
                    # Propagate to any DS-* styling this client (and parent/child DS-* via extends)
                    ds_rows = conn.execute(
                        """
                        WITH RECURSIVE ds_tree(ds_id) AS (
                            SELECT target_id FROM edges WHERE source_id = ? AND rel_type = 'styled_by'
                            UNION
                            SELECT e.target_id FROM edges e JOIN ds_tree d ON e.source_id = d.ds_id
                            WHERE e.rel_type IN ('extends', 'extended_by')
                        )
                        SELECT DISTINCT ds_id FROM ds_tree;
                        """,
                        (clt_id,),
                    ).fetchall()
                    for (ds_id,) in ds_rows:
                        conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);", (cnt_id, ds_id))
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_container', NULL);", (ds_id, cnt_id)
                        )
                        for pat in cnt_paths:
                            conn.execute(
                                "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'inferred_path');",
                                (pat, ds_id),
                            )

            # Handle code and test file updates
            ast_idx = get_ast_indexer()
            resolver = _CodeOwnershipResolver(conn)
            processed_code: set[str] = set()
            for p in code_updates:
                if not p.is_file():
                    continue
                try:
                    rel_p = str(p.relative_to(self.root)).replace("\\", "/")
                except ValueError:
                    rel_p = str(p).replace("\\", "/")

                mtime = p.stat().st_mtime
                sha = hashlib.sha256(p.read_bytes()).hexdigest()

                is_test = self._is_test_code_path(rel_p)

                existing = conn.execute("SELECT 1 FROM indexed_files WHERE rel_path = ?", (rel_p,)).fetchone()
                if existing:
                    stats.updated_modified += 1
                else:
                    stats.indexed_new += 1

                if is_test:
                    self._index_test_bindings(conn, ast_idx, p, rel_p)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                        VALUES (?, ?, ?, 'tests', 'code/test', ?);
                        """,
                        (rel_p, mtime, sha, time.time()),
                    )
                else:
                    self._index_source_file(conn, ast_idx, p, rel_p, mtime, sha, resolver)
                    processed_code.add(rel_p)

            # Ownership can only change when specifications change: only then walk every package directory and
            # re-attribute files (code-only syncs index just the dirty files above) (DLT-2026-034).
            if force or spec_updates or specs_deleted:
                self._reconcile_code_ownership(conn, ast_idx, resolver, processed_code)

            # Ensure test bindings across tests/ are indexed
            tests_dir = self.root / "tests"
            if tests_dir.is_dir():
                for tp in tests_dir.rglob("*.py"):
                    if tp.is_file() and (tp.name.startswith("test_") or tp.name.endswith("_test.py")):
                        try:
                            rel_t = str(tp.relative_to(self.root)).replace("\\", "/")
                        except ValueError:
                            rel_t = str(tp).replace("\\", "/")
                        cached_entry = conn.execute(
                            "SELECT mtime, sha256 FROM indexed_files WHERE rel_path = ? AND doc_type = 'code/test';",
                            (rel_t,),
                        ).fetchone()
                        stat = tp.stat()
                        if not cached_entry or abs(stat.st_mtime - cached_entry["mtime"]) > 0.001 or force:
                            t_sha = hashlib.sha256(tp.read_bytes()).hexdigest()
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type, last_indexed)
                                VALUES (?, ?, ?, 'tests', 'code/test', ?);
                                """,
                                (rel_t, stat.st_mtime, t_sha, time.time()),
                            )
                            self._index_test_bindings(conn, ast_idx, tp, rel_t)

            # Resolve any cross-reference edges where target_id or source_id is a file path.
            # Test-binding edges ('asserts': test -> INV, 'bound_to': INV -> test) intentionally
            # reference 'file::func' test identifiers that are not catalog entities (DLT-2026-029).
            conn.execute(
                """
                UPDATE OR IGNORE edges
                SET target_id = (
                    SELECT doc_id FROM indexed_files
                    WHERE rel_path = edges.target_id
                       OR 'eidos/' || rel_path = edges.target_id
                       OR rel_path = 'eidos/' || edges.target_id
                       OR rel_path LIKE '%' || edges.target_id
                    LIMIT 1
                )
                WHERE target_id NOT IN (SELECT id FROM entities)
                  AND rel_type != 'bound_to';
                """
            )
            conn.execute(
                """
                DELETE FROM edges
                WHERE target_id NOT IN (SELECT id FROM entities)
                  AND rel_type != 'bound_to';
                """
            )
            conn.execute(
                """
                UPDATE OR IGNORE edges
                SET source_id = (
                    SELECT doc_id FROM indexed_files
                    WHERE rel_path = edges.source_id
                       OR 'eidos/' || rel_path = edges.source_id
                       OR rel_path = 'eidos/' || edges.source_id
                       OR rel_path LIKE '%' || edges.source_id
                    LIMIT 1
                )
                WHERE source_id NOT IN (SELECT id FROM entities)
                  AND rel_type != 'asserts';
                """
            )
            conn.execute(
                """
                DELETE FROM edges
                WHERE source_id NOT IN (SELECT id FROM entities)
                  AND rel_type != 'asserts';
                """
            )
            self._prune_stale_test_binding_edges(conn)

        stats.duration_ms = (time.perf_counter() - t0) * 1000
        self._last_synced_check = time.monotonic()
        return stats

    @staticmethod
    def _rebuild_spec_edges(conn: sqlite3.Connection) -> None:
        """Rebuild all specification-declared relational edges from persisted entity frontmatter.

        Preserves 'file::func' AST test-binding edges while guaranteeing that incremental updates or deletions of a
        subset of specs never drop incoming/bidirectional edges declared by unchanged specs.
        """
        conn.execute(
            """
            DELETE FROM edges
            WHERE NOT (
                (rel_type = 'asserts' AND instr(source_id, '::') > 0)
                OR (rel_type = 'bound_to' AND instr(target_id, '::') > 0)
            );
            """
        )
        rows = conn.execute(
            "SELECT id, type, data_json FROM entities WHERE type != 'invariant' ORDER BY id;"
        ).fetchall()
        for row in rows:
            doc_id = row["id"]
            doc_type = row["type"]
            try:
                fm = json.loads(row["data_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(fm, dict):
                continue

            domain = fm.get("domain", fm.get("domain_binding", ""))
            if domain:
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'realizes', NULL);", (doc_id, domain))
                conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'domain_member', NULL);", (domain, doc_id))

            deps = fm.get("dependencies", {})
            if isinstance(deps, dict):
                dep_list = deps.get("internal_containers", []) or deps.get("internal_components", [])
                for dep in dep_list:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'depends_on', NULL);", (doc_id, dep))

            contracts = fm.get("contracts", {})
            if isinstance(contracts, dict):
                for prov in contracts.get("provides", []):
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'provides', NULL);", (doc_id, prov))
                for req in contracts.get("requires") or contracts.get("consumes") or []:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'consumes', NULL);", (doc_id, req))

            if doc_type == "tech/contract":
                owning = fm.get("owning_container") or fm.get("owning_component")
                if owning:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'provides', NULL);", (owning, doc_id))
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'owned_by', NULL);", (doc_id, owning))

            if doc_type == "design/client":
                cnt_ref = fm.get("container")
                ds_ref = fm.get("design_system")
                if cnt_ref and isinstance(cnt_ref, str):
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implemented_by', NULL);",
                        (doc_id, cnt_ref.strip()),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implements_client', NULL);",
                        (cnt_ref.strip(), doc_id),
                    )
                if ds_ref and isinstance(ds_ref, str):
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);", (doc_id, ds_ref.strip())
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_client', NULL);",
                        (ds_ref.strip(), doc_id),
                    )
                if cnt_ref and isinstance(cnt_ref, str) and ds_ref and isinstance(ds_ref, str):
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);",
                        (cnt_ref.strip(), ds_ref.strip()),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_container', NULL);",
                        (ds_ref.strip(), cnt_ref.strip()),
                    )

            if doc_type == "design/system":
                for clt_ref in fm.get("clients", []) or []:
                    if isinstance(clt_ref, str) and clt_ref.strip():
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styled_by', NULL);",
                            (clt_ref.strip(), doc_id),
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?, ?, 'styles_client', NULL);",
                            (doc_id, clt_ref.strip()),
                        )
                inh = fm.get("inheritance")
                if isinstance(inh, dict) and inh.get("extends"):
                    parent_ds = str(inh["extends"]).strip()
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'extends', NULL);", (doc_id, parent_ds))
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'extended_by', NULL);", (parent_ds, doc_id))

            biz = fm.get("business", {})
            if isinstance(biz, dict):
                for pol in biz.get("policies", []):
                    pol_ref = str(pol).strip()
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'governed_by', NULL);", (doc_id, pol_ref))
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'governs', NULL);", (pol_ref, doc_id))

            tech = fm.get("technical", {})
            invariants = []
            if isinstance(tech, dict):
                tech_targets = list(tech.get("containers", []) or []) + list(tech.get("components", []) or [])
                for comp_ref in tech_targets:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'implements', NULL);", (doc_id, comp_ref))
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'implemented_by', NULL);", (comp_ref, doc_id)
                    )
                invariants = tech.get("invariants", []) or []

            if doc_type == "product/policy":
                invariants = fm.get("invariants", []) or []

            if doc_type == "tech/component":
                cnt_id = fm.get("container")
                if cnt_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO edges VALUES (?, ?, 'contains_component', NULL);", (cnt_id, doc_id)
                    )
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'part_of', NULL);", (doc_id, cnt_id))
                ifaces = fm.get("interfaces", {})
                if isinstance(ifaces, dict):
                    for consumed in ifaces.get("consumes", []):
                        if isinstance(consumed, str):
                            conn.execute(
                                "INSERT OR IGNORE INTO edges VALUES (?, ?, 'consumes_api', NULL);",
                                (doc_id, consumed.strip()),
                            )
                invariants = fm.get("invariants", []) or []

            if doc_type == "tech/container":
                for comp_entry in fm.get("components", []):
                    if isinstance(comp_entry, dict) and comp_entry.get("ref"):
                        cid = comp_entry.get("id")
                        if cid:
                            conn.execute(
                                "INSERT OR IGNORE INTO edges VALUES (?, ?, 'contains_component', NULL);",
                                (doc_id, cid),
                            )
                            conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'part_of', NULL);", (cid, doc_id))

            for inv in invariants:
                iid = inv.get("id") if isinstance(inv, dict) else None
                if iid:
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);", (doc_id, iid))
                    conn.execute("INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);", (iid, doc_id))

    @staticmethod
    def _index_test_bindings(conn: sqlite3.Connection, ast_idx: Any, test_path: Path, rel_test: str) -> None:
        """Persist 'file::func' -> INV-* test-binding edges for a single test file (DLT-2026-029).

        Stale edges previously recorded for this test file are cleared first so renamed or
        untagged test functions do not leave phantom assertions behind.
        """
        prefix_like = rel_test.replace("%", "\\%").replace("_", "\\_") + "::%"
        conn.execute(
            "DELETE FROM edges WHERE rel_type = 'asserts' AND source_id LIKE ? ESCAPE '\\';",
            (prefix_like,),
        )
        conn.execute(
            "DELETE FROM edges WHERE rel_type = 'bound_to' AND target_id LIKE ? ESCAPE '\\';",
            (prefix_like,),
        )
        conn.execute(
            "DELETE FROM symbol_mappings WHERE symbol = ? AND kind = 'test_binding';",
            (rel_test,),
        )
        for binding in ast_idx.extract_test_bindings(test_path):
            identifier = f"{rel_test}::{binding.test_name}"
            for inv_id in binding.invariants:
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'asserts', NULL);",
                    (identifier, inv_id),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO edges VALUES (?, ?, 'bound_to', NULL);",
                    (inv_id, identifier),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_mappings VALUES (?, ?, 'test_binding');",
                    (rel_test, inv_id),
                )
                inv_row = conn.execute("SELECT data_json FROM entities WHERE id = ?", (inv_id,)).fetchone()
                if inv_row:
                    idata = json.loads(inv_row["data_json"])
                    idata["has_test_coverage"] = True
                    if identifier not in idata.get("matching_tests", []):
                        idata.setdefault("matching_tests", []).append(identifier)
                    conn.execute(
                        "UPDATE entities SET data_json = ? WHERE id = ?",
                        (json.dumps(idata), inv_id),
                    )

    @staticmethod
    def _prune_stale_test_binding_edges(conn: sqlite3.Connection) -> None:
        """Drop test-binding edges whose invariant entity or test file no longer exists.

        Only edges whose test-side endpoint is a non-entity 'file::func' identifier are considered;
        spec-declared assertions (e.g. CAP-* -> INV-*) are left untouched.
        """
        conn.execute(
            """
            DELETE FROM edges
            WHERE rel_type = 'asserts'
              AND source_id NOT IN (SELECT id FROM entities)
              AND (
                target_id NOT IN (SELECT id FROM entities)
                OR instr(source_id, '::') = 0
                OR substr(source_id, 1, instr(source_id, '::') - 1) NOT IN (
                    SELECT rel_path FROM indexed_files WHERE doc_type = 'code/test'
                )
              );
            """
        )
        conn.execute(
            """
            DELETE FROM edges
            WHERE rel_type = 'bound_to'
              AND target_id NOT IN (SELECT id FROM entities)
              AND (
                source_id NOT IN (SELECT id FROM entities)
                OR instr(target_id, '::') = 0
                OR substr(target_id, 1, instr(target_id, '::') - 1) NOT IN (
                    SELECT rel_path FROM indexed_files WHERE doc_type = 'code/test'
                )
              );
            """
        )

    @staticmethod
    def _delete_spec_fts_rows(conn: sqlite3.Connection, doc_id: str, rel_path: str) -> None:
        """Delete the FTS rows of a specification and of every child entity (e.g. ``INV-*``) declared in its file.

        Must run *before* the ``entities`` rows are deleted, because child FTS rows are keyed by the child ID and
        located through ``entities.file_path``.
        """
        conn.execute(
            "DELETE FROM specs_fts WHERE entity_id = ? OR entity_id IN (SELECT id FROM entities WHERE file_path = ?);",
            (doc_id, rel_path),
        )

    def query_fts(
        self,
        text: str,
        limit: int = 10,
        include_types: tuple[str, ...] | list[str] | None = None,
        exclude_types: tuple[str, ...] | list[str] = (),
    ) -> list[dict[str, Any]]:
        """Execute a column-weighted BM25 ranked full-text query using FTS5.

        Args:
            text: Free-text prompt. camelCase / snake_case identifiers are de-compounded into sub-terms.
            limit: Maximum number of results.
            include_types: When given, only entities of these ``entities.type`` values are ranked.
            exclude_types: Entity types never ranked (filtered inside SQL, before ``LIMIT``).
        """
        if not text or not text.strip():
            return []

        self._ensure_synced()

        fts_query = build_fts_match_query(text)
        if not fts_query:
            return []

        type_clauses: list[str] = []
        type_params: list[str] = []
        if include_types is not None:
            include = list(include_types)
            if not include:
                return []
            type_clauses.append(f"e.type IN ({','.join('?' for _ in include)})")
            type_params.extend(include)
        exclude = list(exclude_types or ())
        if exclude:
            type_clauses.append(f"e.type NOT IN ({','.join('?' for _ in exclude)})")
            type_params.extend(exclude)
        type_sql = "".join(f" AND {clause}" for clause in type_clauses)
        weights_sql = ", ".join(repr(float(w)) for w in FTS_COLUMN_WEIGHTS)

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                f"""
                SELECT
                    e.id, e.type, e.title, e.domain, e.package_path, e.data_json,
                    bm25(specs_fts, {weights_sql}) as rank
                FROM specs_fts f
                JOIN entities e ON e.id = f.entity_id
                WHERE specs_fts MATCH ?{type_sql}
                ORDER BY rank, e.id
                LIMIT ?;
                """,
                (fts_query, *type_params, limit),
            )
            results = []
            for row in cursor.fetchall():
                results.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "domain": row["domain"],
                        "package_path": row["package_path"],
                        "rank": row["rank"],
                        "data": json.loads(row["data_json"]),
                    }
                )
            return results
        except sqlite3.OperationalError:
            return []

    def get_k_hop_neighborhood(
        self, entity_id: str, max_depth: int = 2, max_hops: int | None = None
    ) -> list[dict[str, Any]]:
        """Traverse graph up to k hops in both directions using recursive CTE."""
        if max_hops is not None:
            max_depth = max_hops
        self._ensure_synced()
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            WITH RECURSIVE graph_walk(node_id, depth, path) AS (
                SELECT ?, 0, ?
                UNION
                SELECT
                    CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END,
                    gw.depth + 1,
                    gw.path || '->' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END
                FROM edges e
                JOIN graph_walk gw ON (e.source_id = gw.node_id OR e.target_id = gw.node_id)
                WHERE gw.depth < ?
                  AND gw.path NOT LIKE '%' || CASE WHEN e.source_id = gw.node_id THEN e.target_id ELSE e.source_id END || '%'
            )
            SELECT DISTINCT gw.node_id, gw.depth, ent.type, ent.title, ent.domain, ent.file_path, ent.data_json
            FROM graph_walk gw
            JOIN entities ent ON ent.id = gw.node_id
            ORDER BY gw.depth ASC;
            """,
            (entity_id, entity_id, max_depth),
        )

        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "id": row["node_id"],
                    "depth": row["depth"],
                    "type": row["type"],
                    "title": row["title"],
                    "domain": row["domain"],
                    "file_path": row["file_path"],
                    "data": json.loads(row["data_json"]),
                }
            )
        return results

    def resolve_symbol_or_path(self, query: str) -> list[dict[str, Any]]:
        """Find entity IDs matching source path or symbol."""
        self._ensure_synced()
        conn = self._get_connection()
        cursor = conn.cursor()

        clean = query.strip().replace("\\", "/")
        while clean.startswith("./"):
            clean = clean[2:]

        cursor.execute(
            """
            SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.symbol = ?
               OR (? LIKE s.symbol || '/%' AND s.kind IN ('package_path', 'inferred_path'))
               OR (? LIKE '%' || s.symbol AND s.kind IN ('source_path', 'inferred_path', 'specification_file', 'component_ref', 'interface_method', 'class', 'function', 'method', 'interface', 'struct', 'enum'))
               OR (? LIKE s.symbol || '%' AND s.kind = 'test_binding')
            LIMIT 25;
            """,
            (clean, clean, clean, clean),
        )

        results = []
        seen_entities: set[str] = set()
        for row in cursor.fetchall():
            seen_entities.add(row["entity_id"])
            results.append(
                {
                    "symbol": row["symbol"],
                    "entity_id": row["entity_id"],
                    "kind": row["kind"],
                    "type": row["type"],
                    "title": row["title"],
                    "file_path": row["file_path"],
                    "data": json.loads(row["data_json"]),
                }
            )

        # Also check owned_path and inferred_path glob patterns if query looks like a file path
        if "/" in clean or "." in clean:
            cursor.execute(
                """
                SELECT s.symbol, s.entity_id, s.kind, e.type, e.title, e.file_path, e.data_json
                FROM symbol_mappings s
                JOIN entities e ON e.id = s.entity_id
                WHERE s.kind IN ('owned_path', 'inferred_path');
                """
            )
            scored_matches: list[tuple[int, dict[str, Any]]] = []
            for row in cursor.fetchall():
                if row["entity_id"] in seen_entities:
                    continue
                score = match_path_pattern(clean, row["symbol"])
                if score > 0:
                    scored_matches.append(
                        (
                            score,
                            {
                                "symbol": row["symbol"],
                                "entity_id": row["entity_id"],
                                "kind": row["kind"],
                                "type": row["type"],
                                "title": row["title"],
                                "file_path": row["file_path"],
                                "data": json.loads(row["data_json"]),
                            },
                        )
                    )
            scored_matches.sort(key=lambda item: (-item[0], item[1]["entity_id"]))
            for _, item in scored_matches:
                if item["entity_id"] not in seen_entities:
                    seen_entities.add(item["entity_id"])
                    results.append(item)

        return results

    def find_symbol(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Locate symbol matches and their associated living component specs across the Code Graph."""
        self._ensure_synced()
        conn = self._get_connection()
        cursor = conn.cursor()
        q_norm = query.strip()
        q_alt = q_norm.replace("-", "_") if "-" in q_norm else q_norm.replace("_", "-")
        cursor.execute(
            """
            SELECT s.symbol, s.entity_id, s.kind, e.title, e.type, e.file_path
            FROM symbol_mappings s
            JOIN entities e ON e.id = s.entity_id
            WHERE s.symbol = ?
               OR s.symbol = ?
               OR s.symbol LIKE ?
               OR s.symbol LIKE ?
            ORDER BY CASE WHEN s.symbol IN (?, ?) THEN 0 ELSE 1 END, s.symbol ASC
            LIMIT ?;
            """,
            (q_norm, q_alt, f"%{q_norm}%", f"%{q_alt}%", q_norm, q_alt, limit),
        )
        results: list[dict[str, Any]] = []
        for r in cursor.fetchall():
            base = {
                "symbol": r["symbol"],
                "entity_id": r["entity_id"],
                "kind": r["kind"],
                "title": r["title"],
                "type": r["type"],
                # Spec path of the owning entity (unchanged meaning); source coordinates live in 'location'.
                "file_path": r["file_path"],
            }
            locations = (
                []
                if r["kind"] in _NON_AST_SYMBOL_KINDS
                else self._symbol_locations(r["entity_id"], r["kind"], r["symbol"])
            )
            if locations:
                results.extend({**base, "location": loc} for loc in locations)
            else:
                results.append({**base, "location": None})
        return results[:limit]

    def _symbol_locations(self, entity_id: str, kind: str, symbol: str) -> list[dict[str, Any]]:
        """Resolve persisted source coordinates (one entry per definition) for an AST symbol mapping."""
        cursor = self._get_connection().cursor()
        if kind == "method" and "." in symbol:
            parent, name = symbol.rsplit(".", 1)
            cursor.execute(
                """
                SELECT file_path, line_start, line_end, signature, parent_symbol FROM code_symbols
                WHERE entity_id = ? AND kind = 'method' AND parent_symbol = ? AND symbol = ?
                ORDER BY file_path, line_start;
                """,
                (entity_id, parent, name),
            )
        else:
            cursor.execute(
                """
                SELECT file_path, line_start, line_end, signature, parent_symbol FROM code_symbols
                WHERE entity_id = ? AND kind = ? AND symbol = ?
                ORDER BY file_path, line_start;
                """,
                (entity_id, kind, symbol),
            )
        return [
            {
                "file": row["file_path"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "signature": row["signature"],
                "parent_symbol": row["parent_symbol"] or None,
            }
            for row in cursor.fetchall()
        ]

    def get_repo_map(self, max_tokens: int = 1500) -> str:
        """Generate a token-budgeted structural repo map outline across all indexed components for AI coding agents.

        Reconstructed entirely from the persisted ``code_symbols`` table; source files are never re-parsed.
        """
        self._ensure_synced()
        from eidos.core.index.ast_indexer import CodeSymbol

        indexer = get_ast_indexer()
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT file_path, symbol, kind, parent_symbol, line_start, line_end, signature
            FROM code_symbols
            WHERE is_primary = 1
            ORDER BY file_path, id;
            """
        ).fetchall()

        symbols_by_file: dict[str, list[Any]] = {}
        for r in rows:
            symbols_by_file.setdefault(r["file_path"], []).append(
                CodeSymbol(
                    name=r["symbol"],
                    kind=r["kind"],
                    file_path=r["file_path"],
                    line_start=r["line_start"],
                    line_end=r["line_end"],
                    parent_symbol=r["parent_symbol"] or None,
                    signature=r["signature"] or None,
                )
            )

        return indexer.generate_repo_map(symbols_by_file, max_tokens=max_tokens)

    def get_index_status(self) -> dict[str, Any]:
        """Return comprehensive health and statistics of the architectural index."""
        t0 = time.perf_counter()
        dirty, to_update, to_delete = self.is_dirty()
        dirty_ms = (time.perf_counter() - t0) * 1000

        conn = self._get_connection()
        cursor = conn.cursor()

        total_entities = cursor.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
        total_edges = cursor.execute("SELECT COUNT(*) FROM edges;").fetchone()[0]
        total_symbols = cursor.execute("SELECT COUNT(*) FROM symbol_mappings;").fetchone()[0]
        total_files = cursor.execute("SELECT COUNT(*) FROM indexed_files;").fetchone()[0]

        type_counts = {}
        for row in cursor.execute("SELECT type, COUNT(*) FROM entities GROUP BY type;").fetchall():
            type_counts[row[0]] = row[1]

        symbol_counts = {}
        for row in cursor.execute("SELECT kind, COUNT(*) FROM symbol_mappings GROUP BY kind;").fetchall():
            symbol_counts[row[0]] = row[1]

        # Only count assertions originating from AST test functions ('file::func'), not
        # spec-declared CAP-*/POL-*/CMP-* -> INV-* ownership edges (DLT-2026-029).
        test_assert_filter = (
            "rel_type = 'asserts' AND instr(source_id, '::') > 0 "
            "AND source_id NOT IN (SELECT id FROM entities)"
        )
        test_invariant_assertions = cursor.execute(
            f"SELECT COUNT(*) FROM edges WHERE {test_assert_filter};"
        ).fetchone()[0]
        tested_invariants = cursor.execute(
            f"SELECT COUNT(DISTINCT target_id) FROM edges WHERE {test_assert_filter};"
        ).fetchone()[0]

        from eidos.core.index.ast_indexer import get_ast_indexer, is_ast_grep_available

        ast_avail = is_ast_grep_available()
        ast_idx = get_ast_indexer()

        ast_telemetry = {
            "engine": "ast-grep (Native AST)" if ast_avail else "python-ast (Standard Library Fallback)",
            "engine_class": ast_idx.__class__.__name__,
            "ast_grep_available": ast_avail,
            "total_symbols": total_symbols,
            "classes": symbol_counts.get("class", 0),
            "functions": symbol_counts.get("function", 0),
            "methods": symbol_counts.get("method", 0),
            "test_bindings": symbol_counts.get("test_binding", 0),
            "symbols_by_kind": symbol_counts,
            "test_invariant_assertions": test_invariant_assertions,
            "tested_invariants": tested_invariants,
        }

        db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "db_path": str(self.db_path),
            "schema_version": self.SCHEMA_VERSION,
            "total_entities": total_entities,
            "total_edges": total_edges,
            "total_symbols": total_symbols,
            "total_indexed_files": total_files,
            "db_size_bytes": db_size_bytes,
            "is_dirty": dirty,
            "pending_updates": len(to_update),
            "pending_deletions": len(to_delete),
            "dirty_check_latency_ms": round(dirty_ms, 2),
            "entity_counts_by_type": type_counts,
            "ast": ast_telemetry,
        }

    def get_tiered_context_slice(
        self,
        files: list[str] | str | None = None,
        component: str | None = None,
        task: str | None = None,
        tier: str = "guardrails",
    ) -> TieredContextSlice:
        """Extract a token-budgeted architectural context slice using hybrid indexed retrieval."""
        t0 = time.perf_counter()
        self._ensure_synced()

        matched_components: dict[str, dict[str, Any]] = {}
        matched_contracts: dict[str, dict[str, Any]] = {}
        matched_capabilities: dict[str, dict[str, Any]] = {}
        matched_invariants: dict[str, dict[str, Any]] = {}
        matched_policies: dict[str, dict[str, Any]] = {}
        matched_reasons: dict[str, list[str]] = {}
        direct_inv_ids: set[str] = set()

        def _add_reason(eid: str, reason: str) -> None:
            bucket = matched_reasons.setdefault(eid, [])
            if reason not in bucket:
                bucket.append(reason)

        conn = self._get_connection()
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
                        rel = str(p.resolve().relative_to(self.root)).replace("\\", "/")
                        clean_files.append(rel)
                    except ValueError:
                        clean_files.append(sub_clean.replace("\\", "/"))
                else:
                    c = sub_clean.replace("\\", "/")
                    while c.startswith("./"):
                        c = c[2:]
                    clean_files.append(c)

        for fpath in clean_files:
            matches = self.resolve_symbol_or_path(fpath)
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
                    direct_inv_ids.add(eid)
                    _add_reason(eid, f"Test binding match on '{fpath}'")
                elif etype == "tech/contract":
                    matched_contracts[eid] = data
                    _add_reason(eid, f"Contract file match on '{fpath}'")

        # 3. Resolve by Task prompt (FTS5 lexical search restricted to sliceable entity types, so historical
        #    SpecDeltas, explorations, and personas never consume the top-k candidate slots)
        if task and task.strip():
            fts_matches = self.query_fts(task, limit=8, include_types=TASK_SLICE_ENTITY_TYPES)
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
                    direct_inv_ids.add(eid)
                    _add_reason(eid, reason)
                elif etype in ("product/domain", "design/system"):
                    # Domains and design systems have no dedicated slice list: surface their member containers and
                    # capabilities, and keep the matched DOM-*/DS-* ID visible in matched_reasons.
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

        has_explicit_query = bool(clean_files or (component and component.strip()) or (task and task.strip()))
        any_matched = bool(
            matched_components
            or matched_capabilities
            or matched_contracts
            or matched_invariants
            or matched_policies
        )

        # 4. Full catalog overview ONLY when no query filters were provided at all.
        #    When explicit filters (files, component, or task) match zero entities, return an honest empty slice
        #    rather than injecting unrelated fallback containers or policies (DLT-2026-047).
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
            # Invariants
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

            # Policies
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

        # 9. Fallback governing policies if none matched (only when a query matched something or on full catalog overview)
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
        sorted_ctrs = sorted(matched_contracts.values(), key=lambda x: x.get("id", ""))
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

        # Order invariants by rank:
        # 1) Directly matched invariants (file test binding or FTS match)
        # 2) Primary component invariants
        # 3) Round-robin across matched capabilities and policies in their YAML declaration order
        # 4) Remaining invariants sorted by ID
        ordered_invs: list[dict[str, Any]] = []
        seen_inv_ids: set[str] = set()
        for iid in sorted(direct_inv_ids):
            if iid in matched_invariants and iid not in seen_inv_ids:
                ordered_invs.append(matched_invariants[iid])
                seen_inv_ids.add(iid)
        for iid in sorted(primary_comp_inv_ids):
            if iid in matched_invariants and iid not in seen_inv_ids:
                ordered_invs.append(matched_invariants[iid])
                seen_inv_ids.add(iid)

        for source_list in (sorted_caps, sorted_pols):
            inv_lists = [
                [
                    inv.get("id")
                    for inv in ((src.get("technical") or {}).get("invariants") or src.get("invariants") or [])
                    if isinstance(inv, dict) and inv.get("id")
                ]
                for src in source_list
            ]
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
        slice_obj._enforce_token_budget(primary_set)
        slice_obj.query_latency_ms = (time.perf_counter() - t0) * 1000
        return slice_obj

    def get_components(self) -> list[dict[str, Any]]:
        """Retrieve all C4 containers and components from the knowledge graph index."""
        self._ensure_synced()
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, title, file_path, data_json FROM entities WHERE type IN ('tech/container', 'tech/component') ORDER BY id;"
            ).fetchall()
            components = []
            for r in rows:
                data = json.loads(r["data_json"])
                data["id"] = r["id"]
                data["file_path"] = r["file_path"]
                components.append(data)
            return components

    def get_containers(self) -> list[dict[str, Any]]:
        """Retrieve all C4 containers (Level 2) from the knowledge graph index."""
        return self.get_entities(entity_type="tech/container")

    def get_entities(
        self,
        entity_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve entities from the knowledge graph with optional type and status filtering."""
        self._ensure_synced()
        query = "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE 1=1"
        params: list[Any] = []
        if entity_type:
            query += " AND type = ?"
            params.append(entity_type)
        query += " ORDER BY type, id;"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            entities = []
            for r in rows:
                data = json.loads(r["data_json"])
                data["id"] = r["id"]
                data["title"] = r["title"]
                data["type"] = r["type"]
                data["domain"] = r["domain"]
                data["file_path"] = r["file_path"]
                if status and data.get("status") != status:
                    continue
                entities.append(data)
            return entities

    def get_incoming_references(self, target_id_or_path: str) -> list[dict[str, Any]]:
        """Find all incoming graph edges pointing to an entity ID or file path."""
        self._ensure_synced()
        target = target_id_or_path
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT doc_id FROM indexed_files WHERE rel_path = ? OR 'eidos/' || rel_path = ?;",
                (target, target),
            ).fetchone()
            if row:
                target = row["doc_id"]
            rows = conn.execute(
                """
                SELECT e.source_id, e.rel_type, ent.type, ent.title, ent.file_path
                FROM edges e
                JOIN entities ent ON ent.id = e.source_id
                WHERE e.target_id = ?
                ORDER BY ent.type, e.source_id;
                """,
                (target,),
            ).fetchall()
            return [
                {
                    "source_id": r["source_id"],
                    "rel_type": r["rel_type"],
                    "type": r["type"],
                    "title": r["title"],
                    "file_path": r["file_path"],
                }
                for r in rows
            ]


# Global singleton instance for high-speed module reuse
_INDEX_INSTANCE: ArchitecturalIndex | None = None


def get_architectural_index(
    repo_root: Path | None = None,
    eidos_dir: Path | None = None,
    db_path: Path | None = None,
    force_new: bool = False,
) -> ArchitecturalIndex:
    """Get or create singleton ArchitecturalIndex instance."""
    global _INDEX_INSTANCE
    if repo_root is not None:
        target_root = Path(repo_root).resolve()
    elif (eidos_dir is not None and (Path(eidos_dir) / "system.adoc").exists()) or (
        eidos_dir is not None
        and Path(eidos_dir).name == "eidos"
        and not any(p in Path(eidos_dir).parts for p in (".agents", "plugins", "assets"))
    ):
        target_root = Path(eidos_dir).resolve().parent
    else:
        target_root = find_repo_root(Path(eidos_dir).resolve() if eidos_dir else None)

    target_eidos = (eidos_dir or (target_root / "eidos")).resolve()
    if (
        force_new
        or _INDEX_INSTANCE is None
        or _INDEX_INSTANCE.root != target_root
        or _INDEX_INSTANCE.eidos_dir != target_eidos
    ):
        _INDEX_INSTANCE = ArchitecturalIndex(db_path=db_path, repo_root=target_root, eidos_dir=target_eidos)
    return _INDEX_INSTANCE
