"""Tiered Context Compressor (CMP-TIERED-CONTEXT-COMPRESSOR).

Extracts rank-ordered, token-budgeted architectural slices across anchor (<=300 tokens),
guardrails (<=1000 tokens), and deep (<=2500 tokens) tiers for AI coding agents.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.query import (
    PRIMARY_TASK_SLICE_ENTITY_TYPES,
    SECONDARY_TASK_SLICE_ENTITY_TYPES,
    TASK_SLICE_ENTITY_TYPES,
    execute_fts_query,
    resolve_symbol_or_path_in_db,
)

_GLOB_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".cache",
    }
)


def split_brace_aware_csv(raw: str) -> list[str]:
    """Split comma-separated file patterns only on commas outside balanced ``{a,b}`` brace groups."""
    if not raw:
        return []
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for idx, ch in enumerate(raw):
        if ch == "{":
            if "}" in raw[idx + 1 :]:
                depth += 1
            buf.append(ch)
        elif ch == "}" and depth > 0:
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                parts.append(item)
            buf.clear()
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def expand_brace_globs(pattern: str) -> list[str]:
    """Deterministically expand balanced ``{a,b}`` brace groups containing commas."""
    if "{" not in pattern or "}" not in pattern:
        return [pattern]
    depth = 0
    start_idx = -1
    options: list[str] = []
    opt_start = 0
    for idx, ch in enumerate(pattern):
        if ch == "{":
            if depth == 0:
                start_idx = idx
                opt_start = idx + 1
                options = []
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start_idx != -1:
                options.append(pattern[opt_start:idx])
                if len(options) >= 2:
                    prefix = pattern[:start_idx]
                    suffix = pattern[idx + 1 :]
                    expanded: list[str] = []
                    for opt in options:
                        for sub in expand_brace_globs(f"{prefix}{opt}{suffix}"):
                            if sub not in expanded:
                                expanded.append(sub)
                    return expanded
                start_idx = -1
        elif ch == "," and depth == 1:
            options.append(pattern[opt_start:idx])
            opt_start = idx + 1
    return [pattern]


def _has_glob_magic(path_str: str) -> bool:
    return any(ch in path_str for ch in "*?[") or len(expand_brace_globs(path_str)) > 1


def _concretize_glob_segment(seg: str) -> str:
    if seg == "**":
        return "_planned_dir"
    cleaned = re.sub(r"\[[^\]]*\]", "_p", seg)
    cleaned = re.sub(r"[*?]+", "_planned", cleaned)
    return cleaned or "_planned"


def _expand_context_file_pattern(root: Path, conn: Any, pattern: str) -> list[str]:
    """Deterministically expand a repo-relative glob pattern across existing files and planned governed paths."""
    brace_variants = expand_brace_globs(pattern)
    if len(brace_variants) > 1:
        resolved_root = root.resolve()
        existing_union: list[str] = []
        for variant in brace_variants:
            for m in _expand_single_context_file_pattern(resolved_root, conn, variant, existing_only=True):
                if m not in existing_union:
                    existing_union.append(m)
        if existing_union:
            return sorted(existing_union)
        planned_union: list[str] = []
        for variant in brace_variants:
            for m in _expand_single_context_file_pattern(resolved_root, conn, variant, existing_only=False):
                if m not in planned_union:
                    planned_union.append(m)
        return sorted(planned_union)
    return _expand_single_context_file_pattern(root.resolve(), conn, pattern, existing_only=False)


def _expand_single_context_file_pattern(
    resolved_root: Path,
    conn: Any,
    pattern: str,
    *,
    existing_only: bool = False,
) -> list[str]:
    """Expand a single brace-free glob pattern against existing files or planned governed paths."""
    import posixpath

    root_posix = resolved_root.as_posix().rstrip("/")
    root_prefix = f"{root_posix}/"
    raw_pat = pattern.strip().replace("\\", "/")
    if raw_pat.startswith("/"):
        norm_abs = posixpath.normpath(raw_pat)
        if norm_abs.startswith(root_prefix):
            pat = norm_abs[len(root_prefix) :]
        else:
            return []
    else:
        pat = raw_pat
        while pat.startswith("./"):
            pat = pat[2:]
    norm_segs: list[str] = []
    for seg in pat.split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            if not norm_segs or _has_glob_magic(norm_segs[-1]):
                return []
            norm_segs.pop()
        else:
            norm_segs.append(seg)
    if not norm_segs:
        return []
    pat = "/".join(norm_segs)

    existing_matches: list[str] = []
    try:
        for candidate in resolved_root.glob(pat):
            if not candidate.is_file():
                continue
            try:
                rel_parts = candidate.resolve().relative_to(resolved_root).parts
            except ValueError:
                continue
            if any(part in _GLOB_EXCLUDED_DIRS for part in rel_parts):
                continue
            existing_matches.append("/".join(rel_parts))
    except (OSError, ValueError, NotImplementedError):
        existing_matches = []

    if existing_matches or existing_only:
        return sorted(dict.fromkeys(existing_matches))

    # Fallback for planned not-yet-created files in proposed SpecDeltas:
    # 1) Walk existing parent directory segments on disk when at least one directory segment matches.
    synthesized: list[str] = []
    segs = norm_segs
    if len(segs) >= 2:
        dir_segs = segs[:-1]
        leaf_concrete = _concretize_glob_segment(segs[-1])

        def _walk_dirs(cur_dir: Path, idx: int, matched_depth: int, rel_prefix: tuple[str, ...]) -> None:
            if idx >= len(dir_segs):
                if matched_depth > 0:
                    synthesized.append("/".join((*rel_prefix, leaf_concrete)))
                return
            seg = dir_segs[idx]
            if seg == "**":
                _walk_dirs(cur_dir, idx + 1, matched_depth, rel_prefix)
                try:
                    children = sorted(cur_dir.iterdir(), key=lambda p: p.name)
                except OSError:
                    children = []
                for child in children:
                    if child.is_dir() and child.name not in _GLOB_EXCLUDED_DIRS:
                        _walk_dirs(child, idx, matched_depth + 1, (*rel_prefix, child.name))
                return

            try:
                children = sorted(cur_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                children = []
            matched_dirs = [
                c for c in children if c.is_dir() and c.name not in _GLOB_EXCLUDED_DIRS and fnmatch.fnmatchcase(c.name, seg)
            ]
            if matched_dirs:
                for child in matched_dirs:
                    _walk_dirs(child, idx + 1, matched_depth + 1, (*rel_prefix, child.name))
            elif matched_depth > 0 and not _has_glob_magic(seg):
                rest = tuple(_concretize_glob_segment(s) for s in dir_segs[idx:])
                synthesized.append("/".join((*rel_prefix, *rest, leaf_concrete)))

        _walk_dirs(resolved_root, 0, 0, ())

    # 2) Match indexed symbol_mappings paths and governed source_path / owned_path / package_path prefixes.
    for row in conn.execute(
        "SELECT DISTINCT symbol, kind FROM symbol_mappings WHERE symbol IS NOT NULL AND symbol != '';"
    ).fetchall():
        sym = str(row["symbol"]).strip().replace("\\", "/").strip("/")
        kind = str(row["kind"] or "")
        if not sym:
            continue
        if not _has_glob_magic(sym) and fnmatch.fnmatchcase(sym, pat):
            synthesized.append(sym)
            continue
        if kind in ("package_path", "owned_path", "inferred_path", "source_path"):
            sym_parts = [s for s in sym.split("/") if s and s != "."]
            if (
                sym_parts
                and len(segs) >= len(sym_parts)
                and "**" not in segs[: len(sym_parts)]
                and "**" not in sym_parts
                and all(
                    fnmatch.fnmatchcase(sp, pp) or fnmatch.fnmatchcase(pp, sp)
                    for sp, pp in zip(sym_parts, segs[: len(sym_parts)], strict=False)
                )
            ):
                head = [
                    sp if not _has_glob_magic(sp) else (pp if not _has_glob_magic(pp) else _concretize_glob_segment(pp))
                    for sp, pp in zip(sym_parts, segs[: len(sym_parts)], strict=False)
                ]
                tail = [_concretize_glob_segment(s) for s in segs[len(sym_parts) :]]
                synthesized.append("/".join((*head, *tail)))

    return sorted(dict.fromkeys(synthesized))

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
            max_anchor_alts = 0 if (tier == "guardrails" and compact_prose and dec_idx > 0) else (1 if compact_prose else 2)
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
        compact_primary_decisions = bool(getattr(self, "_compact_primary_decisions", False))
        compact_metadata = bool(getattr(self, "_compact_metadata", False))
        drop_anchor_decisions = bool(getattr(self, "_drop_anchor_decisions", False))
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
                        self.tier == "guardrails"
                        and compact_primary_decisions
                        and idx_comp == 0
                        and len(decisions) > 3
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
                    elif (self.tier == "guardrails" and compact_decisions and idx_comp == 0) or heavy_primary_guardrails:
                        max_dec_items = 2
                    elif self.tier == "guardrails" and compact_primary_decisions and idx_comp == 0:
                        max_dec_items = 3
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
    _compact_primary_decisions: bool = False
    _compact_prose: bool = False
    _compact_metadata: bool = False
    _drop_anchor_decisions: bool = False

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
        else:
            max_decs = len(decisions)
        for dec_idx, d in enumerate(decisions[:max_decs]):
            if not isinstance(d, dict):
                continue
            dtext = str(d.get("decision", "") or "")
            use_compact_entry = (
                self.tier == "anchor"
                or compact
                or self._compact_decisions
                or (compact_tail and dec_idx > 0)
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
        elif is_secondary and (self._compact_prose or len(self.components) > 4):
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
        out: dict[str, Any] = {
            "id": inv.get("id", ""),
            "rule": self._compact_summary(raw_rule, 110) if self._compact_prose else raw_rule,
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

    def to_dict(self) -> dict[str, Any]:
        """Project tier-scoped view of matched architectural entities rather than raw frontmatter (DLT-2026-047)."""
        return {
            "query": self.query,
            "tier": self.tier,
            "components": [self._project_component(c, idx) for idx, c in enumerate(self.components)],
            "contracts": [self._project_contract(c) for c in self.contracts],
            "capabilities": [self._project_capability(c, idx) for idx, c in enumerate(self.capabilities)],
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

    def _state_signature(self, budget: int) -> tuple[Any, ...]:
        return (
            budget,
            self.tier,
            self._compact_decisions,
            self._compact_primary_decisions,
            self._compact_prose,
            self._compact_metadata,
            self._drop_anchor_decisions,
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
        if eid and not eid.startswith(self._EXPANDED_REASON_PREFIXES):
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


def build_tiered_context_slice(
    index: Any,
    files: list[str] | str | None = None,
    component: str | None = None,
    task: str | None = None,
    tier: str = "guardrails",
    use_semantic: bool = True,
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
    direct_cnt_ids: list[str] = []
    expanded_cnt_ids: list[str] = []
    direct_cap_ids: list[str] = []
    expanded_cap_ids: list[str] = []

    def _add_reason(eid: str, reason: str) -> None:
        bucket = matched_reasons.setdefault(eid, [])
        if reason not in bucket and len(bucket) < 3:
            bucket.append(reason)

    conn = index._get_connection()
    cursor = conn.cursor()

    def _expand_cap_to_components(cap_ids: list[str], member_reason: str, member_ids: list[str] | None = None) -> None:
        if not cap_ids:
            return
        ph_sc = ",".join("?" for _ in cap_ids)
        cap_comp_rows = cursor.execute(
            f"""
            SELECT DISTINCT e.id, e.title, e.type, e.file_path, e.data_json
            FROM edges ed
            JOIN entities e ON e.id = ed.target_id
            WHERE ed.source_id IN ({ph_sc}) AND ed.rel_type = 'implements'
              AND e.type IN ('tech/container', 'tech/component')
            ORDER BY CASE WHEN e.type = 'tech/container' THEN 0 ELSE 1 END, e.id
            LIMIT 4;
            """,
            tuple(cap_ids),
        ).fetchall()
        for row in cap_comp_rows:
            m_data = orjson.loads(row["data_json"])
            m_data["id"] = row["id"]
            m_data["title"] = row["title"]
            m_data.setdefault("file_path", row["file_path"])
            matched_components.setdefault(row["id"], m_data)
            if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                expanded_cnt_ids.append(row["id"])
            if member_ids is not None and row["id"] not in member_ids:
                member_ids.append(row["id"])
            _add_reason(row["id"], member_reason)

    def _ingest_entity(
        eid: str,
        etype: str,
        data: dict[str, Any],
        reason: str,
        hit_ranks: dict[str, int] | None = None,
        *,
        expand_cap_components: bool = False,
    ) -> None:
        ranks = hit_ranks or {}
        if etype in ("tech/container", "tech/component"):
            matched_components[eid] = data
            if eid not in direct_cnt_ids:
                direct_cnt_ids.append(eid)
            _add_reason(eid, reason)
        elif etype == "tech/contract":
            matched_contracts[eid] = data
            _add_reason(eid, reason)
        elif etype == "product/capability":
            matched_capabilities[eid] = data
            if eid not in direct_cap_ids:
                direct_cap_ids.append(eid)
            _add_reason(eid, reason)
            if expand_cap_components:
                _expand_cap_to_components([eid], f"Implemented by matched capability {eid}")
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
            member_rows = sorted(
                cursor.execute(member_sql, (eid,)).fetchall(),
                key=lambda r: (0 if r["id"] in ranks else 1, ranks.get(r["id"], 999), r["id"]),
            )
            member_ids: list[str] = []
            for row in member_rows:
                m_data = orjson.loads(row["data_json"])
                m_data["id"] = row["id"]
                m_data["title"] = row["title"]
                m_data.setdefault("file_path", row["file_path"])
                if row["type"] in ("tech/container", "tech/component"):
                    matched_components.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                        expanded_cnt_ids.append(row["id"])
                elif row["type"] == "product/capability":
                    matched_capabilities.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cap_ids and row["id"] not in expanded_cap_ids:
                        expanded_cap_ids.append(row["id"])
                else:
                    continue
                member_ids.append(row["id"])
                _add_reason(row["id"], member_reason)
            expanded = f"; expanded to {', '.join(member_ids)}" if member_ids else ""
            _add_reason(eid, f"{reason}{expanded}")
        elif etype in SECONDARY_TASK_SLICE_ENTITY_TYPES:
            member_reason = (
                f"Serves FTS-matched persona {eid}"
                if etype == "product/persona"
                else f"Delivers FTS-matched design entity {eid}"
            )
            sec_rows = sorted(
                cursor.execute(
                    """
                    SELECT DISTINCT e.id, e.title, e.type, e.file_path, e.data_json
                    FROM entities e
                    WHERE e.type IN ('product/capability', 'tech/container', 'tech/component', 'invariant')
                      AND (
                          e.id IN (
                              SELECT source_id FROM edges
                              WHERE target_id = ? AND rel_type IN ('serves_persona', 'delivered_by', 'styled_by')
                          )
                          OR e.id IN (
                              SELECT target_id FROM edges
                              WHERE source_id = ? AND rel_type IN ('implemented_by', 'styles_container', 'asserts')
                          )
                          OR e.id IN (
                              SELECT ed2.target_id FROM edges ed1
                              JOIN edges ed2 ON ed2.source_id = ed1.source_id
                              WHERE ed1.target_id = ? AND ed1.rel_type IN ('has_screen', 'has_flow')
                                AND ed2.rel_type = 'implemented_by'
                          )
                          OR e.id IN (
                              SELECT ed2.source_id FROM edges ed1
                              JOIN edges ed2 ON ed2.target_id = ed1.source_id
                              WHERE ed1.target_id = ? AND ed1.rel_type IN ('has_screen', 'has_flow')
                                AND ed2.rel_type = 'delivered_by'
                          )
                          OR e.id IN (
                              SELECT ed2.source_id FROM edges ed1
                              JOIN edges ed2 ON ed2.target_id = ed1.target_id
                              WHERE ed1.source_id = ? AND ed1.rel_type IN ('uses_screen', 'styled_by')
                                AND ed2.rel_type = 'delivered_by'
                          )
                          OR e.id IN (
                              SELECT ed2.target_id FROM edges ed1
                              JOIN edges ed2 ON ed2.source_id = ed1.target_id
                              WHERE ed1.source_id = ? AND ed1.rel_type = 'styled_by'
                                AND ed2.rel_type = 'styles_container'
                          )
                      )
                    ORDER BY e.id;
                    """,
                    (eid, eid, eid, eid, eid, eid),
                ).fetchall(),
                key=lambda r: (0 if r["id"] in ranks else 1, ranks.get(r["id"], 999), r["id"]),
            )
            sec_cap_ids: list[str] = []
            member_ids = []
            for row in sec_rows:
                m_data = orjson.loads(row["data_json"])
                m_data["id"] = row["id"]
                m_data["title"] = row["title"]
                if row["file_path"]:
                    m_data.setdefault("file_path", row["file_path"])
                if row["type"] in ("tech/container", "tech/component"):
                    matched_components.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                        expanded_cnt_ids.append(row["id"])
                elif row["type"] == "product/capability":
                    matched_capabilities.setdefault(row["id"], m_data)
                    if row["id"] not in direct_cap_ids and row["id"] not in expanded_cap_ids:
                        expanded_cap_ids.append(row["id"])
                    sec_cap_ids.append(row["id"])
                elif row["type"] == "invariant":
                    matched_invariants.setdefault(row["id"], m_data)
                else:
                    continue
                member_ids.append(row["id"])
                _add_reason(row["id"], member_reason)

            if sec_cap_ids and not any(r["type"] in ("tech/container", "tech/component") for r in sec_rows):
                _expand_cap_to_components(sec_cap_ids, member_reason, member_ids)
            expanded = f"; expanded to {', '.join(member_ids)}" if member_ids else ""
            _add_reason(eid, f"{reason}{expanded}")

    # 1. Resolve by Component / Container / Entity parameter
    if component:
        c_clean = component.strip().upper()
        comp_rows = cursor.execute(
            """
            SELECT id, title, type, domain, file_path, data_json
            FROM entities
            WHERE type IN ('tech/container', 'tech/component') AND (id = ? OR id LIKE '%' || ? || '%');
            """,
            (c_clean, c_clean),
        ).fetchall()
        if not comp_rows:
            ph_types = ",".join("?" for _ in TASK_SLICE_ENTITY_TYPES)
            comp_rows = cursor.execute(
                f"""
                SELECT id, title, type, domain, file_path, data_json
                FROM entities
                WHERE type IN ({ph_types}) AND (id = ? OR id LIKE '%' || ? || '%');
                """,
                (*TASK_SLICE_ENTITY_TYPES, c_clean, c_clean),
            ).fetchall()
        for row in comp_rows:
            cid = row["id"]
            data = orjson.loads(row["data_json"])
            data["id"] = cid
            data["title"] = row["title"]
            data.setdefault("file_path", row["file_path"])
            _ingest_entity(
                cid,
                row["type"],
                data,
                f"Explicit component parameter '{component}'",
                expand_cap_components=True,
            )

    # 2. Resolve by File paths
    raw_files = [files] if isinstance(files, str) else (files or [])
    requested_files: list[str] = []
    clean_files: list[str] = []
    seen_clean_files: set[str] = set()
    for f in raw_files:
        if not f:
            continue
        for sub in split_brace_aware_csv(str(f)):
            sub_clean = sub.strip()
            if not sub_clean:
                continue
            if _has_glob_magic(sub_clean):
                norm_pat = sub_clean.replace("\\", "/")
                while norm_pat.startswith("./"):
                    norm_pat = norm_pat[2:]
                if norm_pat not in requested_files:
                    requested_files.append(norm_pat)
                for expanded in _expand_context_file_pattern(index.root, conn, norm_pat):
                    if expanded not in seen_clean_files:
                        seen_clean_files.add(expanded)
                        clean_files.append(expanded)
                continue
            p = Path(sub_clean)
            escaped_root = False
            if p.is_absolute():
                try:
                    norm_f = str(p.resolve().relative_to(index.root.resolve())).replace("\\", "/")
                except ValueError:
                    norm_f = sub_clean.replace("\\", "/")
                    escaped_root = True
            else:
                norm_f = sub_clean.replace("\\", "/")
                while norm_f.startswith("./"):
                    norm_f = norm_f[2:]
                if ".." in norm_f.split("/"):
                    try:
                        norm_f = str((index.root / norm_f).resolve().relative_to(index.root.resolve())).replace("\\", "/")
                    except ValueError:
                        escaped_root = True
            if norm_f not in requested_files:
                requested_files.append(norm_f)
            if not escaped_root and norm_f not in seen_clean_files:
                seen_clean_files.add(norm_f)
                clean_files.append(norm_f)

    for fpath in clean_files:
        matches = resolve_symbol_or_path_in_db(conn, fpath)
        primary_file_matches = [
            m for m in matches if m["type"] in ("tech/container", "tech/component", "invariant", "tech/contract")
        ]
        if primary_file_matches:
            for m in primary_file_matches:
                eid = m["entity_id"]
                etype = m["type"]
                data = m["data"]
                data["id"] = eid
                data["title"] = m["title"]
                if m.get("file_path"):
                    data.setdefault("file_path", m["file_path"])
                if etype in ("tech/container", "tech/component"):
                    file_reason = f"Source file match on '{fpath}'"
                elif etype == "invariant":
                    file_reason = f"Test binding match on '{fpath}'"
                else:
                    file_reason = f"Contract file match on '{fpath}'"
                _ingest_entity(eid, etype, data, file_reason)
            continue

        if not matches:
            spec_rows = list(
                cursor.execute(
                    "SELECT id, title, type, file_path, data_json FROM entities WHERE file_path = ? AND type != 'invariant';",
                    (fpath,),
                ).fetchall()
            )
            if not spec_rows and fpath.endswith(".json"):
                for ds_row in cursor.execute(
                    "SELECT id, title, type, file_path, data_json FROM entities WHERE type = 'design/system';"
                ).fetchall():
                    ds_data = orjson.loads(ds_row["data_json"])
                    tokens = ds_data.get("tokens") if isinstance(ds_data.get("tokens"), dict) else {}
                    tok = tokens.get("manifest")
                    if isinstance(tok, str) and tok.strip():
                        ds_fp = str(ds_row["file_path"] or "").replace("\\", "/")
                        tok_path = tok.strip() if "/" in tok.strip() else f"{ds_fp.rsplit('/', 1)[0]}/{tok.strip()}"
                        if tok_path.replace("\\", "/") == fpath:
                            spec_rows.append(ds_row)
            matches = [
                {
                    "entity_id": r["id"],
                    "title": r["title"],
                    "type": r["type"],
                    "file_path": r["file_path"],
                    "data": orjson.loads(r["data_json"]),
                }
                for r in spec_rows
            ]
        for m in matches:
            eid = m["entity_id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]
            if m.get("file_path"):
                data.setdefault("file_path", m["file_path"])
            _ingest_entity(eid, etype, data, f"Specification file match on '{fpath}'", expand_cap_components=True)

    # 3. Resolve by Task prompt (FTS5 lexical + optional Harrier Hybrid RRF search restricted to sliceable entity types,
    #    retaining up to 8 primary structural matches and up to 4 secondary persona/design matches so secondary entities
    #    never displace primary C4/capability/invariant candidates)
    if task and task.strip():
        raw_matches = execute_fts_query(
            conn,
            text=task,
            limit=12,
            include_types=TASK_SLICE_ENTITY_TYPES,
            use_semantic=use_semantic,
            eidos_dir=getattr(index, "eidos_dir", None),
        )
        primary_hits = [m for m in raw_matches if m["type"] in PRIMARY_TASK_SLICE_ENTITY_TYPES][:8]
        if len(primary_hits) < 8 and len(raw_matches) >= 12:
            seen_primary = {m["id"] for m in primary_hits}
            for m in execute_fts_query(
                conn,
                text=task,
                limit=8,
                include_types=PRIMARY_TASK_SLICE_ENTITY_TYPES,
                use_semantic=use_semantic,
                eidos_dir=getattr(index, "eidos_dir", None),
            ):
                if m["id"] not in seen_primary:
                    primary_hits.append(m)
                    seen_primary.add(m["id"])
                    if len(primary_hits) >= 8:
                        break
        secondary_hits = [
            m
            for idx, m in enumerate(raw_matches)
            if (idx < 8 or len(primary_hits) < 8) and m["type"] in SECONDARY_TASK_SLICE_ENTITY_TYPES
        ][:4]
        fts_matches = primary_hits + secondary_hits
        fts_hit_ranks = {m["id"]: idx for idx, m in enumerate(fts_matches)}
        for m in fts_matches:
            eid = m["id"]
            etype = m["type"]
            data = m["data"]
            data["id"] = eid
            data["title"] = m["title"]
            if m.get("file_path"):
                data.setdefault("file_path", m["file_path"])

            match_source = m.get("match_source")
            if match_source == "hybrid":
                lex_rank = int(m.get("bm25_rank") or m.get("lexical_rank") or 0)
                reason = (
                    f"Hybrid task match (RRF: {float(m.get('rrf_score') or 0.0):.3f}, "
                    f"BM25 #{lex_rank}, cos: {float(m.get('semantic_score') or 0.0):.2f})"
                )
            elif match_source == "semantic":
                reason = (
                    f"Semantic task match (RRF: {float(m.get('rrf_score') or 0.0):.3f}, "
                    f"cos: {float(m.get('semantic_score') or 0.0):.2f})"
                )
            else:
                reason = f"FTS lexical match on task (BM25 score: {m['rank']:.2f})"

            _ingest_entity(eid, etype, data, reason, fts_hit_ranks)

    for iid in direct_inv_ids:
        inv_data = matched_invariants[iid]
        parent_cap = inv_data.get("capability_id")
        if parent_cap:
            if parent_cap not in matched_capabilities:
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
            if parent_cap in matched_capabilities:
                if parent_cap not in direct_cap_ids:
                    direct_cap_ids.append(parent_cap)
                _add_reason(parent_cap, f"Owning capability of matched invariant {iid}")
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
                    if row["id"] not in direct_cnt_ids:
                        direct_cnt_ids.append(row["id"])
                    _add_reason(row["id"], f"Owning component/container of matched invariant {iid}")
        parent_uic = inv_data.get("design_component_id")
        if parent_uic:
            row = cursor.execute(
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_uic,),
            ).fetchone()
            if row:
                u_data = orjson.loads(row["data_json"])
                u_data["id"] = row["id"]
                u_data["title"] = row["title"]
                u_data.setdefault("file_path", row["file_path"])
                _ingest_entity(row["id"], row["type"], u_data, f"Owning UI component of matched invariant {iid}")

    has_explicit_query = bool(
        requested_files or clean_files or (component and component.strip()) or (task and task.strip())
    )
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

    # 4b. Ensure every matched standalone tech/component includes its owning parent tech/container,
    #     interleaving the owning container of any direct component hit immediately after that component
    #     in direct_cnt_ids so token-budget tail eviction never drops the #1 component's parent container.
    prioritized_direct_cnt_ids: list[str] = []
    for cid in list(direct_cnt_ids):
        if cid not in prioritized_direct_cnt_ids:
            prioritized_direct_cnt_ids.append(cid)
        c_data = matched_components.get(cid) or {}
        parent_cnt = str(c_data.get("container") or "").strip()
        if parent_cnt:
            if parent_cnt not in matched_components:
                row = cursor.execute(
                    "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                    (parent_cnt,),
                ).fetchone()
                if row:
                    cnt_data = orjson.loads(row["data_json"])
                    cnt_data["id"] = row["id"]
                    cnt_data["title"] = row["title"]
                    cnt_data.setdefault("file_path", row["file_path"])
                    matched_components[row["id"]] = cnt_data
            if parent_cnt in matched_components:
                _add_reason(parent_cnt, f"Owning container of {cid}")
                if parent_cnt not in prioritized_direct_cnt_ids:
                    prioritized_direct_cnt_ids.append(parent_cnt)
    direct_cnt_ids = prioritized_direct_cnt_ids

    for cid, c_data in list(matched_components.items()):
        parent_cnt = str(c_data.get("container") or "").strip()
        if parent_cnt and parent_cnt not in matched_components:
            row = cursor.execute(
                "SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id = ?;",
                (parent_cnt,),
            ).fetchone()
            if row:
                cnt_data = orjson.loads(row["data_json"])
                cnt_data["id"] = row["id"]
                cnt_data["title"] = row["title"]
                cnt_data.setdefault("file_path", row["file_path"])
                matched_components[row["id"]] = cnt_data
                if row["id"] not in direct_cnt_ids and row["id"] not in expanded_cnt_ids:
                    expanded_cnt_ids.append(row["id"])
                _add_reason(row["id"], f"Owning container of {cid}")

    # 5. Expand 1-hop internal dependencies via graph edges (depends_on, consumes_api)
    primary_cids = list(
        dict.fromkeys([*direct_cnt_ids, *[cid for cid in matched_components if cid not in direct_cnt_ids]])
    )
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
                f"SELECT id, title, type, domain, file_path, data_json FROM entities WHERE id IN ({ph_dep}) "
                "ORDER BY CASE WHEN type = 'tech/container' THEN 0 ELSE 1 END, id;",
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

    # 7. Include capabilities directly implementing/implemented by primary components,
    # then fall back to domain capabilities when no direct capability edges exist or when domain capabilities match.
    direct_edge_cap_ids: set[str] = set()
    if primary_cids:
        ph = ",".join("?" for _ in primary_cids)
        cursor.execute(
            f"""
            SELECT id, title, type, domain, file_path, data_json FROM entities
            WHERE type = 'product/capability' AND (
                id IN (SELECT source_id FROM edges WHERE target_id IN ({ph}) AND rel_type IN ('implemented_by', 'implements'))
                OR id IN (SELECT target_id FROM edges WHERE source_id IN ({ph}) AND rel_type IN ('implements', 'implemented_by'))
            );
            """,
            tuple(primary_cids + primary_cids),
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
    if matched_policies and tier != "anchor":
        pol_ids = list(matched_policies.keys())
        ph_pol = ",".join("?" for _ in pol_ids)
        cursor.execute(
            f"""
            SELECT id, title, data_json FROM entities
            WHERE type = 'invariant' AND id IN (
                SELECT target_id FROM edges WHERE source_id IN ({ph_pol}) AND rel_type = 'asserts'
            );
            """,
            tuple(pol_ids),
        )
        for row in cursor.fetchall():
            inv_data = orjson.loads(row["data_json"])
            inv_data["id"] = row["id"]
            matched_invariants.setdefault(row["id"], inv_data)

    # Ensure embedded decisions, domain_path, and bound design pointers are surfaced on each matched component
    comp_domain_ids: set[str] = set()
    for comp_entry in matched_components.values():
        collected_decs = collect_component_decisions(comp_entry)
        if collected_decs:
            comp_entry["decisions"] = collected_decs
        dom_id = str(comp_entry.get("domain") or "").strip()
        if not dom_id:
            parent_cnt = str(comp_entry.get("container") or "").strip()
            if parent_cnt and parent_cnt in matched_components:
                dom_id = str(matched_components[parent_cnt].get("domain") or "").strip()
                if dom_id:
                    comp_entry["domain"] = dom_id
        if dom_id:
            comp_domain_ids.add(dom_id)

    if tier != "anchor" and matched_components:
        domain_path_by_id: dict[str, str] = {}
        if comp_domain_ids:
            ph_dom = ",".join("?" for _ in comp_domain_ids)
            for row in cursor.execute(
                f"SELECT id, file_path FROM entities WHERE type = 'product/domain' AND id IN ({ph_dom});",
                tuple(comp_domain_ids),
            ).fetchall():
                if row["file_path"]:
                    domain_path_by_id[row["id"]] = row["file_path"]

        all_scope_ids = sorted(
            {
                sid
                for cid, comp_entry in matched_components.items()
                for sid in (cid, str(comp_entry.get("container") or "").strip())
                if sid
            }
        )
        design_by_scope: dict[str, dict[str, dict[str, str]]] = {}
        if all_scope_ids:
            ph_sc = ",".join("?" for _ in all_scope_ids)
            for r in cursor.execute(
                f"""
                SELECT ed.target_id AS scope_id, e.id, e.type, e.file_path
                FROM edges ed
                JOIN entities e ON e.id = ed.source_id
                WHERE ed.target_id IN ({ph_sc})
                  AND ed.rel_type IN ('implemented_by', 'styled_by', 'styles_container', 'delivered_by')
                  AND e.type IN ('design/client', 'design/system')
                UNION
                SELECT ed.source_id AS scope_id, e.id, e.type, e.file_path
                FROM edges ed
                JOIN entities e ON e.id = ed.target_id
                WHERE ed.source_id IN ({ph_sc})
                  AND ed.rel_type IN ('implemented_by', 'styled_by', 'styles_container', 'delivered_by')
                  AND e.type IN ('design/client', 'design/system')
                UNION
                SELECT ed1.target_id AS scope_id, e.id, e.type, e.file_path
                FROM edges ed1
                JOIN edges ed2 ON ed2.source_id = ed1.source_id
                JOIN entities e ON e.id = ed2.target_id
                WHERE ed1.target_id IN ({ph_sc})
                  AND ed1.rel_type = 'implemented_by'
                  AND ed2.rel_type = 'styled_by'
                  AND e.type IN ('design/client', 'design/system')
                ORDER BY e.id;
                """,
                all_scope_ids * 3,
            ).fetchall():
                if r["id"]:
                    design_by_scope.setdefault(r["scope_id"], {})[r["id"]] = {
                        "id": r["id"],
                        "type": r["type"],
                        "file_path": r["file_path"] or "",
                    }

        for cid, comp_entry in matched_components.items():
            dom_id = str(comp_entry.get("domain") or "").strip()
            if dom_id and dom_id in domain_path_by_id:
                comp_entry.setdefault("domain_path", domain_path_by_id[dom_id])
            parent_cnt = str(comp_entry.get("container") or "").strip()
            merged_design: dict[str, dict[str, str]] = {}
            for sid in (cid, parent_cnt):
                if sid and sid in design_by_scope:
                    merged_design.update(design_by_scope[sid])
            if merged_design:
                comp_entry.setdefault("design", [merged_design[k] for k in sorted(merged_design)])

    # Assemble and rank-order lists so token-budget truncation retains highest-priority entities first
    primary_set = set(primary_cids)
    direct_cnt_set = set(direct_cnt_ids)
    direct_cnt_rank = {cid: idx for idx, cid in enumerate(direct_cnt_ids)}
    expanded_cnt_rank = {cid: idx for idx, cid in enumerate(expanded_cnt_ids)}
    primary_rank = {cid: idx for idx, cid in enumerate(primary_cids)}
    direct_ctr_rank = {cid: idx for idx, cid in enumerate(direct_ctr_ids)}
    direct_cap_rank = {cid: idx for idx, cid in enumerate(direct_cap_ids)}
    expanded_cap_rank = {cid: idx for idx, cid in enumerate(expanded_cap_ids)}
    direct_pol_rank = {pid: idx for idx, pid in enumerate(direct_pol_ids)}

    file_tokens: set[str] = set()
    for f in clean_files:
        file_tokens.update(_tokenize_text(Path(f).stem.replace("_", " ").replace("-", " ")))
    for cid in primary_cids:
        if cid.startswith("CMP-"):
            file_tokens.update(_tokenize_text(cid.removeprefix("CMP-").replace("_", " ").replace("-", " ")))

    def _cap_binds_matched_comp(cap_entry: dict[str, Any]) -> bool:
        if not primary_set:
            return False
        tech = cap_entry.get("technical") if isinstance(cap_entry.get("technical"), dict) else {}
        comp_refs = {str(t).strip() for t in (tech.get("components") or []) if isinstance(t, str)}
        return bool(comp_refs & primary_set)

    def _cap_relevance_tuple(cap_entry: dict[str, Any]) -> tuple[int, int, int, str]:
        tech = cap_entry.get("technical") if isinstance(cap_entry.get("technical"), dict) else {}
        cnt_refs = [str(t).strip() for t in (tech.get("containers") or []) if isinstance(t, str) and str(t).strip()]
        primary_cnt_match = bool(cnt_refs and cnt_refs[0] in primary_set)
        binds_comp = _cap_binds_matched_comp(cap_entry)
        in_domain = cap_entry.get("domain", "") in domains
        overlap = 0
        if file_tokens:
            cap_tokens = set(_tokenize_text(str(cap_entry.get("title", "")).replace("_", " ").replace("-", " ")))
            overlap = len(file_tokens & cap_tokens)
        if (primary_cnt_match and overlap > 0) or (binds_comp and in_domain):
            tier_score = 0
        elif in_domain or binds_comp:
            tier_score = 1
        else:
            tier_score = 2
        return (tier_score, -overlap, 0 if in_domain else 1, str(cap_entry.get("id", "")))

    sorted_comps = sorted(
        matched_components.values(),
        key=lambda x: (
            0 if x.get("id", "") in direct_cnt_set else (1 if x.get("id", "") in primary_set else 2),
            direct_cnt_rank.get(
                x.get("id", ""),
                100 + expanded_cnt_rank.get(x.get("id", ""), primary_rank.get(x.get("id", ""), 999)),
            ),
            -len(matched_reasons.get(x.get("id", ""), ())),
            0 if x.get("type") == "tech/container" else 1,
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
            0 if x.get("id", "") in direct_cap_rank else (1 if x.get("id", "") in expanded_cap_rank else 2),
            direct_cap_rank.get(x.get("id", ""), 100 + expanded_cap_rank.get(x.get("id", ""), 999)),
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
    if tier != "anchor":
        max_invs = max(len(direct_inv_ids) + 12, 16) if tier == "guardrails" else 1000
        seen_inv_ids: set[str] = set()
        for iid in direct_inv_ids:
            if iid in matched_invariants and iid not in seen_inv_ids:
                ordered_invs.append(matched_invariants[iid])
                seen_inv_ids.add(iid)
        for iid in sorted(primary_comp_inv_ids):
            if len(ordered_invs) >= max_invs:
                break
            if iid in matched_invariants and iid not in seen_inv_ids:
                ordered_invs.append(matched_invariants[iid])
                seen_inv_ids.add(iid)

        inv_focus_tokens: set[str] = set()
        inv_leaf_tokens: set[str] = set()
        if clean_files or component:
            for f in clean_files:
                inv_focus_tokens.update(
                    _tokenize_text(f.replace("/", " ").replace("_", " ").replace("-", " ").replace(".", " "))
                )
                leaf_stem = f.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                inv_leaf_tokens.update(_tokenize_text(leaf_stem.replace("_", " ").replace("-", " ")))
            for cid in primary_cids:
                inv_focus_tokens.update(
                    _tokenize_text(cid.removeprefix("CNT-").removeprefix("CMP-").replace("_", " ").replace("-", " "))
                )
            _focus_noise = {"eidos", "src", "py", "md", "adoc", "json", "ts", "js", "go", "rs", "main", "init", "index"}
            inv_focus_tokens -= _focus_noise
            inv_leaf_tokens -= _focus_noise

        def _cap_inv_rank_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            inv_toks = set(
                _tokenize_text(
                    f"{item[1].get('id', '')} {item[1].get('test_binding', '')}"
                    .replace("/", " ")
                    .replace("_", " ")
                    .replace("-", " ")
                    .replace(":", " ")
                    .replace(".", " ")
                )
            )
            return (-len(inv_leaf_tokens & inv_toks), -len(inv_focus_tokens & inv_toks), item[0])

        for source_list in (sorted_comps, sorted_caps, sorted_pols):
            if len(ordered_invs) >= max_invs:
                break
            inv_lists: list[list[str]] = []
            is_cap_group = source_list is sorted_caps
            for src in source_list:
                raw_invs = list((src.get("technical") or {}).get("invariants") or src.get("invariants") or [])
                valid_invs = [inv for inv in raw_invs if isinstance(inv, dict) and inv.get("id")]
                if is_cap_group and inv_focus_tokens:
                    valid_invs = [inv_d for _, inv_d in sorted(enumerate(valid_invs), key=_cap_inv_rank_key)]
                inv_lists.append([inv["id"] for inv in valid_invs])
            max_len = max((len(lst) for lst in inv_lists), default=0)
            for idx in range(max_len):
                if len(ordered_invs) >= max_invs:
                    break
                for lst in inv_lists:
                    if idx < len(lst):
                        iid = lst[idx]
                        if iid in matched_invariants and iid not in seen_inv_ids:
                            ordered_invs.append(matched_invariants[iid])
                            seen_inv_ids.add(iid)
                            if len(ordered_invs) >= max_invs:
                                break

        if len(ordered_invs) < max_invs:
            for iid in sorted(matched_invariants.keys()):
                if iid not in seen_inv_ids:
                    ordered_invs.append(matched_invariants[iid])
                    seen_inv_ids.add(iid)
                    if len(ordered_invs) >= max_invs:
                        break

    slice_obj = TieredContextSlice(
        tier=tier,
        query={"files": requested_files, "component": component, "task": task},
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
        direct_cnt_set if direct_cnt_set else primary_set,
        direct_cap_ids=set(direct_cap_ids),
        direct_inv_ids=set(direct_inv_ids),
        direct_pol_ids=set(direct_pol_ids),
    )
    slice_obj.query_latency_ms = (time.perf_counter() - t0) * 1000
    return slice_obj
