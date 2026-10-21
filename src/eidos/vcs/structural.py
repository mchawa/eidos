"""Structural AST diff evaluation, symbol maps, and spec coverage analysis (CMP-DRIFT-DETECTOR).

Compares AST symbol additions, pairs moved files by content, inspects substantive
specification diffs, and evaluates 1-hop and design subgraph coverage.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from eidos.core.spec.parser import safe_load_yaml, split_frontmatter_and_body
from eidos.core.workspace.project import match_path_pattern
from eidos.vcs.diff import _get_file_versions

SYSTEM_LEVEL_SPEC_PATHS = (
    "eidos/system.adoc",
    "eidos/architecture/system-architecture.adoc",
)


def get_system_level_spec_paths(eidos_rel_prefix: str = "eidos") -> tuple[str, str]:
    """Return the system-level specification paths for a given eidos directory relative prefix."""
    prefix = eidos_rel_prefix.strip().replace("\\", "/").strip("/") or "eidos"
    return (
        f"{prefix}/system.adoc",
        f"{prefix}/architecture/system-architecture.adoc",
    )


def _is_system_level_spec(
    spec_path: str,
    system_spec_paths: tuple[str, ...] = SYSTEM_LEVEL_SPEC_PATHS,
) -> bool:
    """True when spec_path matches one of the system-level specification paths on a path boundary."""
    s_norm = spec_path.replace("\\", "/").lower().strip("/")
    for sys_p in (*SYSTEM_LEVEL_SPEC_PATHS, *system_spec_paths):
        sp = sys_p.replace("\\", "/").lower().strip("/")
        if s_norm == sp or s_norm.endswith("/" + sp):
            return True
    return False


_ADOC_LINE_COMMENT_RE = re.compile(r"^\s*//(?!/)")
_ADOC_COMMENT_DELIM_RE = re.compile(r"^/{4,}$")
_ADOC_VERBATIM_DELIM_RE = re.compile(r"^(-{4,}|\.{4,}|\+{4,})$")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_OWNERSHIP_KEYS = ("source_path",)
_MIN_ANCHOR_LEN = 3


@dataclass
class _StructuralAddition:
    """Structural additions of one code file: a new file and/or new public symbols."""

    file: str
    reasons: list[str]
    symbol_names: list[str]
    is_new_file: bool
    boundary_symbol_names: list[str] | None = None


class _SymbolMapCache:
    """Parses each (file, side) version at most once per drift run."""

    def __init__(self, versions: dict[str, tuple[str | None, str | None]]) -> None:
        self._versions = versions
        self._maps: dict[tuple[str, int], dict[tuple[str, str, str], str]] = {}
        self._boundary_maps: dict[tuple[str, int], dict[tuple[str, str, str], str]] = {}

    @staticmethod
    def supports(cf: str) -> bool:
        from eidos.core.index.ast_indexer import AST_DIFF_EXTS

        return Path(cf).suffix.lower() in AST_DIFF_EXTS

    def versions_of(self, cf: str) -> tuple[str | None, str | None]:
        return self._versions.get(cf, (None, None))

    def get(self, cf: str, side: int) -> dict[tuple[str, str, str], str]:
        """Public symbol map of ``cf`` at ``side`` (0 = old, 1 = new)."""
        from eidos.core.index.ast_indexer import public_symbol_map

        key = (cf, side)
        if key not in self._maps:
            self._maps[key] = public_symbol_map(self.versions_of(cf)[side], cf)
        return self._maps[key]

    def get_boundary(self, cf: str, side: int) -> dict[tuple[str, str, str], str]:
        """Boundary-exported symbol map of ``cf`` at ``side`` (0 = old, 1 = new)."""
        from eidos.core.index.ast_indexer import boundary_symbol_map

        key = (cf, side)
        if key not in self._boundary_maps:
            self._boundary_maps[key] = boundary_symbol_map(self.versions_of(cf)[side], cf)
        return self._boundary_maps[key]

    def diff(self, cf: str, old_from: str | None = None) -> Any:
        """Public symbol diff of ``cf`` (old side taken from ``old_from`` for moved files)."""
        from eidos.core.index.ast_indexer import diff_symbol_maps

        src_old = old_from or cf
        return diff_symbol_maps(
            self.get(src_old, 0),
            self.get(cf, 1),
            old_boundary_map=self.get_boundary(src_old, 0),
            new_boundary_map=self.get_boundary(cf, 1),
        )


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _pair_moved_files(
    versions: dict[str, tuple[str | None, str | None]],
    smap: _SymbolMapCache,
) -> dict[str, str]:
    """Pair added files with deleted files of the same change set that they were moved/renamed from."""
    added = sorted(cf for cf, (old, new) in versions.items() if old is None and new is not None)
    deleted = sorted(cf for cf, (old, new) in versions.items() if old is not None and new is None)
    if not added or not deleted:
        return {}

    candidates: list[tuple[int, int, str, str]] = []
    for a in added:
        a_text = _normalize_ws(versions[a][1] or "")
        for d in deleted:
            if Path(a).suffix.lower() != Path(d).suffix.lower():
                continue
            if a_text == _normalize_ws(versions[d][0] or ""):
                candidates.append((0, 0, a, d))
                continue
            if not smap.supports(a):
                continue
            d_keys = set(smap.get(d, 0))
            if d_keys and d_keys <= set(smap.get(a, 1)):
                candidates.append((1, -len(d_keys), a, d))

    pairs: dict[str, str] = {}
    used: set[str] = set()
    for _, _, a, d in sorted(candidates):
        if a not in pairs and d not in used:
            pairs[a] = d
            used.add(d)
    return pairs


def _structural_addition_for(
    cf: str,
    synthetic: bool,
    smap: _SymbolMapCache,
    *,
    symbol_diff: Any = None,
    moved_from: str | None = None,
) -> _StructuralAddition | None:
    """Return the structural additions of a structurally changed file, or None when it adds nothing."""
    old_content, new_content = smap.versions_of(cf)
    if synthetic or new_content is None:
        return None
    if moved_from is None and old_content is None:
        diff = smap.diff(cf)
        return _StructuralAddition(
            file=cf,
            reasons=[f"added file '{cf}'"],
            symbol_names=[diff.qualified_name(k) for k in diff.added],
            is_new_file=True,
            boundary_symbol_names=[diff.qualified_name(k) for k in getattr(diff, "boundary_added", diff.added)],
        )
    diff = smap.diff(cf, old_from=moved_from) if moved_from else (symbol_diff or smap.diff(cf))
    if not diff.added:
        return None
    return _StructuralAddition(
        file=cf,
        reasons=[f"added {k[0]} '{diff.qualified_name(k)}'" for k in diff.added],
        symbol_names=[diff.qualified_name(k) for k in diff.added],
        is_new_file=False,
        boundary_symbol_names=[diff.qualified_name(k) for k in getattr(diff, "boundary_added", diff.added)],
    )


def _split_spec(content: str | None) -> tuple[str | None, str]:
    """Split a spec into (raw frontmatter text or None, AsciiDoc body)."""
    return split_frontmatter_and_body(content or "")


def _normalized_body_lines(body: str) -> list[str]:
    """AsciiDoc body lines without comments or blank lines, inner whitespace collapsed."""
    out: list[str] = []
    open_delim: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if open_delim is not None:
            if line == open_delim:
                if not _ADOC_COMMENT_DELIM_RE.match(open_delim):
                    out.append(line)
                open_delim = None
            elif line and not _ADOC_COMMENT_DELIM_RE.match(open_delim):
                out.append(_normalize_ws(line))
            continue
        if _ADOC_COMMENT_DELIM_RE.match(line) or _ADOC_VERBATIM_DELIM_RE.match(line):
            open_delim = line
            if not _ADOC_COMMENT_DELIM_RE.match(line):
                out.append(line)
            continue
        if not line or _ADOC_LINE_COMMENT_RE.match(raw):
            continue
        out.append(_normalize_ws(line))
    return out


def _parse_frontmatter(raw: str | None) -> dict[str, Any] | None:
    """Parsed frontmatter mapping, ``{}`` when absent, ``None`` when it is not valid YAML."""
    if raw is None:
        return {}
    try:
        data = safe_load_yaml(raw)
    except Exception:
        return None
    if data is None:
        return {}
    return data if isinstance(data, dict) else None


def _spec_frontmatter(content: str | None) -> dict[str, Any]:
    return _parse_frontmatter(_split_spec(content)[0]) or {}


def _canonical(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str).decode("utf-8")


def _declared_source_patterns(frontmatter: dict[str, Any]) -> list[str]:
    """Explicit file/glob ownership declared by a spec via ``source_path``."""
    patterns: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            patterns.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                _add(item)

    _add(frontmatter.get("source_path"))
    return patterns


class _SpecDiffInspector:
    """Per-run, cached inspection of how a modified specification changed (DLT-2026-039)."""

    def __init__(
        self,
        root: Path,
        base_ref: str | None,
        head_ref: str,
        uncommitted: bool,
        merge_base: str | None = None,
        versions_fn: Any = None,
        synthetic_diff_mock: bool = False,
    ) -> None:
        self._root = root
        self._base_ref = base_ref
        self._head_ref = head_ref
        self._uncommitted = uncommitted
        self.merge_base = merge_base
        self._versions_fn = versions_fn
        self._synthetic_diff_mock = synthetic_diff_mock
        self._versions: dict[str, tuple[str | None, str | None]] = {}

    def versions(self, spec: str) -> tuple[str | None, str | None]:
        if spec not in self._versions:
            if self._versions_fn is not None:
                self._versions[spec] = self._versions_fn(spec)
            else:
                self._versions[spec] = _get_file_versions(
                    self._root,
                    spec,
                    base_ref=self._base_ref,
                    head_ref=self._head_ref,
                    uncommitted=self._uncommitted,
                    merge_base=self.merge_base,
                )
        return self._versions[spec]

    def is_new_spec(self, spec: str) -> bool:
        old, new = self.versions(spec)
        return old is None and new is not None

    def is_substantive(self, spec: str) -> bool:
        """True when the frontmatter data or the comment/whitespace-normalized body differs."""
        old, new = self.versions(spec)
        if new is None:
            return False
        if old is None:
            return True
        if old == new:
            return self._synthetic_diff_mock

        spec_lower = spec.lower()
        if spec_lower.endswith(".json"):
            try:
                return _canonical(orjson.loads(old)) != _canonical(orjson.loads(new))
            except Exception:
                return _normalize_ws(old) != _normalize_ws(new)

        if spec_lower.endswith((".yaml", ".yml")):
            try:
                return _canonical(safe_load_yaml(old)) != _canonical(safe_load_yaml(new))
            except Exception:
                return _normalize_ws(old) != _normalize_ws(new)

        old_raw, old_body = _split_spec(old)
        new_raw, new_body = _split_spec(new)
        old_fm, new_fm = _parse_frontmatter(old_raw), _parse_frontmatter(new_raw)
        if old_fm is None or new_fm is None:
            fm_changed = _normalize_ws(old_raw or "") != _normalize_ws(new_raw or "")
        else:
            fm_changed = _canonical(old_fm) != _canonical(new_fm)
        if fm_changed:
            return True
        return _normalize_ws(" ".join(_normalized_body_lines(old_body))) != _normalize_ws(
            " ".join(_normalized_body_lines(new_body))
        )

    def declares_at_base(self, spec: str, cf: str) -> bool:
        """True when the spec, at the base revision, already declared ``cf`` via an explicit file or glob pattern."""
        old, _ = self.versions(spec)
        cf_norm = cf.replace("\\", "/").strip("/")
        for p in _declared_source_patterns(_spec_frontmatter(old)):
            p_norm = p.replace("\\", "/").strip("/")
            if (any(ch in p_norm for ch in "*?[") or p_norm == cf_norm) and match_path_pattern(cf, p) > 0:
                return True
        return False

    def is_anchored(
        self,
        spec: str,
        cf: str,
        symbol_names: list[str],
        *,
        boundary_symbol_names: list[str] | None = None,
    ) -> bool:
        """True when the spec edit changes ownership keys or its added content names the file or a new symbol."""
        old, new = self.versions(spec)
        if old is None:
            return True
        if new is None:
            return False
        old_raw, old_body = _split_spec(old)
        new_raw, new_body = _split_spec(new)
        old_fm, new_fm = _parse_frontmatter(old_raw) or {}, _parse_frontmatter(new_raw) or {}
        if any(_canonical(old_fm.get(k)) != _canonical(new_fm.get(k)) for k in _OWNERSHIP_KEYS):
            return True

        added = list((Counter(_normalized_body_lines(new_body)) - Counter(_normalized_body_lines(old_body))).elements())
        added += [_canonical(v) for k, v in new_fm.items() if _canonical(v) != _canonical(old_fm.get(k))]
        needles = {cf, Path(cf).name, *symbol_names, *(boundary_symbol_names or ())}
        patterns = [
            re.compile(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])")
            for n in sorted(needles)
            for _ in (0,)
            if n and len(n) >= _MIN_ANCHOR_LEN
        ]
        return any(p.search(line) for line in added for p in patterns)


def _collect_1hop_spec_targets(
    idx: Any,
    cf: str,
    cid: str,
    spath: str,
    *,
    system_spec_paths: tuple[str, ...] = SYSTEM_LEVEL_SPEC_PATHS,
) -> tuple[set[str], set[str]]:
    """Return (allowed_spec_paths, allowed_entity_ids) within 1 hop of a code file's owners."""
    allowed_paths: set[str] = {spath.replace("\\", "/").lower()}
    allowed_ids: set[str] = {cid.lower()}

    if idx is None:
        return allowed_paths, allowed_ids

    seed_ids: set[str] = {cid}
    resolved_data_by_id: dict[str, dict[str, Any]] = {}
    for m in idx.resolve_symbol_or_path(cf):
        if m.get("type") not in ("tech/container", "tech/component"):
            continue
        eid = m.get("entity_id")
        fp = m.get("file_path")
        if eid:
            seed_ids.add(eid)
            allowed_ids.add(eid.lower())
            if isinstance(m.get("data"), dict):
                resolved_data_by_id.setdefault(eid, m["data"])
        if fp:
            allowed_paths.add(str(fp).replace("\\", "/").lower())

    neighborhoods: dict[str, list[dict[str, Any]]] = {}
    queue = list(seed_ids)
    while queue:
        sid = queue.pop(0)
        if sid not in neighborhoods:
            neighborhoods[sid] = idx.get_k_hop_neighborhood(sid, max_depth=1)
        neighbors = neighborhoods[sid]
        self_node = next((n for n in neighbors if n.get("id") == sid), None)
        self_type = (self_node or {}).get("type", "")
        self_data = (self_node or {}).get("data") or resolved_data_by_id.get(sid) or {}

        if self_type == "tech/component" or (not self_type and sid.startswith("CMP-")):
            parent_cnt = str(self_data.get("container") or "").strip()
            if parent_cnt and parent_cnt not in seed_ids:
                seed_ids.add(parent_cnt)
                allowed_ids.add(parent_cnt.lower())
                queue.append(parent_cnt)
            for nb in neighbors:
                if nb.get("type") != "tech/container":
                    continue
                nb_id = str(nb.get("id") or "").strip()
                if nb_id and nb_id == parent_cnt and nb_id not in seed_ids:
                    seed_ids.add(nb_id)
                    allowed_ids.add(nb_id.lower())
                    queue.append(nb_id)

    for sid in seed_ids:
        for n in neighborhoods.get(sid, ()):
            nid = n.get("id", "")
            ntype = n.get("type", "")
            ndata = n.get("data", {}) or {}
            if ntype not in ("tech/contract", "product/capability", "tech/container", "tech/component"):
                continue
            if ntype in ("tech/container", "tech/component") and nid not in seed_ids:
                continue
            if nid.startswith("POL-") or nid.startswith("PER-"):
                continue
            if nid:
                allowed_ids.add(nid.lower())
            nfp = n.get("file_path")
            if nfp:
                allowed_paths.add(str(nfp).replace("\\", "/").lower())
            for pkey in ("file_path", "path"):
                pval = ndata.get(pkey)
                if pval:
                    allowed_paths.add(str(pval).replace("\\", "/").lower())

    allowed_paths = {p for p in allowed_paths if not _is_system_level_spec(p, system_spec_paths)}
    return allowed_paths, allowed_ids


def _path_in(changed_spec: str, allowed_paths: set[str]) -> bool:
    s_norm = changed_spec.replace("\\", "/").lower().strip("/")
    for ap in allowed_paths:
        if not ap:
            continue
        ap_norm = ap.replace("\\", "/").lower().strip("/")
        if s_norm == ap_norm or s_norm.endswith("/" + ap_norm) or ap_norm.endswith("/" + s_norm):
            return True
    return False


def _relevant_changed_specs(
    changed_specs: list[str],
    allowed_paths: set[str],
    allowed_ids: set[str],
    *,
    system_spec_paths: tuple[str, ...] = SYSTEM_LEVEL_SPEC_PATHS,
) -> list[str]:
    """Modified spec files inside the 1-hop neighborhood of a modified code file (system-level specs excluded)."""
    relevant: list[str] = []
    for s in changed_specs:
        s_norm = s.replace("\\", "/").lower()
        if _is_system_level_spec(s_norm, system_spec_paths):
            continue
        if _path_in(s_norm, allowed_paths):
            relevant.append(s)
    return relevant


def _spec_coverage_verdict(
    changed_specs: list[str],
    allowed_paths: set[str],
    allowed_ids: set[str],
    *,
    addition: _StructuralAddition | None = None,
    owner_spec: str = "",
    inspector: _SpecDiffInspector | None = None,
    system_spec_paths: tuple[str, ...] = SYSTEM_LEVEL_SPEC_PATHS,
) -> tuple[bool, str | None, list[str]]:
    """Decide whether modified specs cover a code file; returns ``(covered, rejection_rule, rejected_specs)``."""
    relevant = _relevant_changed_specs(
        changed_specs,
        allowed_paths,
        allowed_ids,
        system_spec_paths=system_spec_paths,
    )
    if not relevant:
        return False, None, []

    if inspector is not None:
        substantive = [s for s in relevant if inspector.is_substantive(s)]
        if not substantive:
            return False, "trivial_spec_diff", relevant
    else:
        substantive = relevant

    if addition is None or not owner_spec or inspector is None:
        return True, None, []

    owner_norm = owner_spec.replace("\\", "/").lower()
    owner_changed = [s for s in substantive if s.replace("\\", "/").lower() == owner_norm]
    if addition.is_new_file:
        owner_is_new = any(inspector.is_new_spec(s) for s in owner_changed)
        needs_anchor = not owner_is_new and not inspector.declares_at_base(owner_spec, addition.file)
        if needs_anchor and not any(
            inspector.is_anchored(s, addition.file, addition.symbol_names) for s in owner_changed
        ):
            return False, "missing_anchor", relevant
        return True, None, []

    boundary_syms = addition.symbol_names if addition.boundary_symbol_names is None else addition.boundary_symbol_names
    if (
        boundary_syms
        and not owner_changed
        and not any(
            inspector.is_new_spec(s)
            or inspector.is_anchored(
                s,
                addition.file,
                addition.symbol_names,
                boundary_symbol_names=boundary_syms,
            )
            for s in substantive
        )
    ):
        return False, "missing_anchor", relevant
    return True, None, []


def _resolve_bound_design_ids(idx: Any, cf: str, owner_ids: set[str]) -> set[str]:
    """Resolve the design clients (CLT-*) and design systems (DS-*) bound to a frontend file via the graph."""
    bound: set[str] = set()
    if idx is None:
        return bound
    for m in idx.resolve_symbol_or_path(cf):
        if m.get("type") in ("design/client", "design/system") and m.get("entity_id"):
            bound.add(m["entity_id"])
    for oid in owner_ids:
        for n in idx.get_k_hop_neighborhood(oid, max_depth=1):
            if n.get("type") in ("design/client", "design/system") and n.get("id"):
                bound.add(n["id"])
    return bound


def _collect_design_subgraph(
    design_entities: list[dict[str, Any]],
    bound_ids: set[str],
) -> tuple[set[str], set[str]]:
    """Return (spec_paths, entity_ids) of the design subgraph owned by the bound CLT-*/DS-* entities."""
    by_id = {e.get("id"): e for e in design_entities if e.get("id")}
    ids = {b for b in bound_ids if b in by_id}
    changed = True
    while changed:
        changed = False
        for eid in list(ids):
            e = by_id.get(eid, {})
            candidates: list[Any] = [
                e.get("design_system"),
                e.get("extends"),
            ]
            for ref in candidates:
                if isinstance(ref, str) and ref in by_id and ref not in ids:
                    ids.add(ref)
                    changed = True

    paths: set[str] = set()
    sub_ids: set[str] = set(ids)
    client_owned_refs: set[str] = set()
    for eid in ids:
        e = by_id[eid]
        fp = str(e.get("file_path") or "").replace("\\", "/")
        if fp:
            paths.add(fp.lower())
        if e.get("type") == "design/system":
            tokens = e.get("tokens") if isinstance(e.get("tokens"), dict) else {}
            tok = tokens.get("manifest")
            if isinstance(tok, str) and tok and fp:
                tok_path = tok if "/" in tok else str(Path(fp).parent / tok)
                paths.add(tok_path.replace("\\", "/").lower())
        if e.get("type") == "design/client":
            for key in ("screens", "flows"):
                for ref in e.get(key, []) or []:
                    if isinstance(ref, str) and ref.strip():
                        r_clean = ref.strip().replace("\\", "/")
                        client_owned_refs.add(r_clean.lower())
                        if r_clean in by_id:
                            sub_ids.add(r_clean)
                            target_fp = str(by_id[r_clean].get("file_path") or "").replace("\\", "/")
                            if target_fp:
                                paths.add(target_fp.lower())
                        else:
                            paths.add(r_clean.lower())

    systems = {i for i in ids if by_id[i].get("type") == "design/system"}
    clients = {i for i in ids if by_id[i].get("type") == "design/client"}
    for e in design_entities:
        eid = e.get("id")
        if not eid or eid in sub_ids:
            continue
        etype = e.get("type")
        fp = str(e.get("file_path") or "").replace("\\", "/")
        fp_low = fp.lower()
        member = False
        if etype == "design/ui-component":
            member = e.get("design_system") in systems or e.get("implements") in sub_ids
        elif etype in ("design/screen", "design/flow"):
            if eid.lower() in client_owned_refs or (
                fp_low and any(fp_low == r or fp_low.endswith("/" + r) for r in client_owned_refs)
            ):
                member = True
            elif etype == "design/flow" and e.get("flow_type") != "cross_client":
                step_clients = [
                    str(s.get("client")).strip()
                    for s in (e.get("steps") or [])
                    if isinstance(s, dict) and isinstance(s.get("client"), str) and str(s.get("client")).strip()
                ]
                member = bool(step_clients) and all(c in clients for c in step_clients)
        if member:
            sub_ids.add(eid)
            if fp_low:
                paths.add(fp_low)
    return paths, sub_ids
