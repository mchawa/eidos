"""SpecDelta engine: listing, status summaries, by-status view sync, baseline snapshots, verification, execution,
and application of semantic mutations (CMP-DELTA-ENGINE)."""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.lifecycle.catalog import (
    _build_spec_catalog,
    _build_spec_catalog_with_invariants,
    _mutate_frontmatter_components,
    _set_frontmatter_scalar,
    find_spec_by_id_or_path,
)
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator


@dataclass
class MutationVerificationItem:
    """Status of an individual mutation verified against the living specification catalog."""

    action: str
    target: str
    verified: bool
    status: str  # "FOUND", "MISSING", "ABSENT_AS_EXPECTED", "STILL_PRESENT", "DEPRECATED", "ACTIVE_NOT_DEPRECATED"
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "verified": self.verified,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class DeltaVerificationReport:
    """Consolidated verification report for all mutations declared in a SpecDelta."""

    delta_id: str
    delta_file: Path
    total_mutations: int
    verified_count: int
    is_verified: bool
    mutations: list[MutationVerificationItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "delta_file": str(self.delta_file),
            "total_mutations": self.total_mutations,
            "verified_count": self.verified_count,
            "is_verified": self.is_verified,
            "mutations": [m.to_dict() for m in self.mutations],
        }


def _ensure_eidos_gitignore(eidos_dir: Path) -> None:
    """Ensure eidos/.gitignore is present and ignores generated symlink views and cache so host projects remain unpolluted."""
    gitignore_path = eidos_dir / ".gitignore"
    default_content = (
        "# Eidos living specification lifecycle views (generated symlinks) and local index cache\n"
        "deltas/by-status/\n"
        ".cache/\n"
    )
    if not gitignore_path.exists():
        with contextlib.suppress(OSError):
            gitignore_path.write_text(default_content, encoding="utf-8")
    else:
        with contextlib.suppress(OSError):
            content = gitignore_path.read_text(encoding="utf-8")
            missing: list[str] = []
            if "deltas/by-status" not in content and "by-status" not in content:
                missing.append("deltas/by-status/")
            if ".cache" not in content:
                missing.append(".cache/")
            if missing:
                extra = "\n".join(missing)
                gitignore_path.write_text(
                    f"{content.rstrip()}\n\n# Eidos generated views and cache\n{extra}\n",
                    encoding="utf-8",
                )


def sync_delta_views(eidos_dir: Path) -> dict[str, list[Path]]:
    """Synchronize gitignored symlink views in eidos/deltas/by-status/ based on frontmatter status.

    Categories: proposed, in_review, approved, applied, rejected, active (in-flight).
    """
    deltas_dir = eidos_dir / "deltas"
    if not deltas_dir.exists():
        return {}

    # Ensure self-contained eidos/.gitignore
    _ensure_eidos_gitignore(eidos_dir)

    views_dir = deltas_dir / "by-status"
    categories = ["proposed", "in_review", "approved", "applied", "rejected", "active"]

    # Ensure target category directories exist
    for cat in categories:
        (views_dir / cat).mkdir(parents=True, exist_ok=True)

    # Clean existing symlinks in by-status
    for cat in categories:
        cat_dir = views_dir / cat
        for item in cat_dir.iterdir():
            if item.is_symlink() or item.is_file():
                with contextlib.suppress(OSError):
                    item.unlink()

    synced: dict[str, list[Path]] = {cat: [] for cat in categories}

    for p in sorted(deltas_dir.rglob("*.adoc")):
        if "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            if doc.doc_type != "delta/mutation":
                continue

            status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
            cat_name = status if status in categories else "proposed"
            link_path = views_dir / cat_name / p.name
            rel_target = os.path.relpath(p, views_dir / cat_name)
            try:
                link_path.symlink_to(rel_target)
                synced[cat_name].append(p)
            except OSError:
                pass

            if status in ("proposed", "in_review", "approved", "in_progress", "draft"):
                active_link = views_dir / "active" / p.name
                rel_active = os.path.relpath(p, views_dir / "active")
                try:
                    active_link.symlink_to(rel_active)
                    synced["active"].append(p)
                except OSError:
                    pass
        except OSError:
            continue

    return synced


def get_delta_status_summary(eidos_dir: Path) -> dict[str, Any]:
    """Get count and listing breakdown of deltas grouped by lifecycle status."""
    deltas = list_deltas(eidos_dir)
    by_status: dict[str, list[dict]] = {
        "proposed": [],
        "in_review": [],
        "approved": [],
        "applied": [],
        "rejected": [],
    }
    for d in deltas:
        st = str(d.get("status", "proposed")).lower().strip()
        if st not in by_status:
            by_status[st] = []
        by_status[st].append(d)

    active_count = sum(len(by_status.get(s, [])) for s in ("proposed", "in_review", "approved"))

    return {
        "total": len(deltas),
        "active_count": active_count,
        "by_status": by_status,
        "views_dir": eidos_dir / "deltas" / "by-status",
    }


def list_deltas(eidos_dir: Path, status_filter: str | None = None) -> list[dict[str, Any]]:
    """List all SpecDeltas from eidos/deltas/ with optional status filtering."""
    deltas_dir = eidos_dir / "deltas"
    results = []
    if not deltas_dir.exists():
        return results

    status_filter_clean = status_filter.lower().strip() if status_filter else None

    for p in sorted(deltas_dir.rglob("*.adoc")):
        if "by-status" in p.parts:
            continue
        try:
            doc = parse_spec_file(p)
            if doc.doc_type != "delta/mutation":
                continue

            status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
            is_active = status in ("proposed", "in_review", "approved", "in_progress", "draft")

            if status_filter_clean and (
                (status_filter_clean == "active" and not is_active)
                or (status_filter_clean != "active" and status != status_filter_clean)
            ):
                continue

            intent = doc.frontmatter.get("intent", {})
            kind = intent.get("kind", "feature") if isinstance(intent, dict) else "feature"

            results.append(
                {
                    "id": doc.doc_id,
                    "title": doc.title or "Untitled",
                    "status": status,
                    "kind": kind,
                    "is_active": is_active,
                    "path": p.resolve(),
                    "relative_path": p.relative_to(eidos_dir) if p.is_relative_to(eidos_dir) else p,
                }
            )
        except OSError:
            continue

    return results


_ACTION_PRIMARY_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("_invariant", ("target_invariant_id",)),
    ("_capability", ("new_capability_id", "target_capability")),
    ("_component", ("target_component",)),
    ("_container", ("target_container",)),
    ("_architecture", ("target_architecture",)),
    ("_contract", ("target_contract",)),
    ("_policy", ("target_policy",)),
    ("_domain", ("target_domain",)),
    ("_persona", ("target_persona",)),
    ("_screen", ("target_screen",)),
    ("_ui_component", ("target_ui_component",)),
    ("_design_system", ("target_design_system",)),
    ("_client", ("target_client",)),
    ("_flow", ("target_flow",)),
)


def _extract_mutation_target(m: dict[str, Any]) -> str:
    """Extract primary target identifier or path from a mutation dictionary."""
    action = str(m.get("action", "")).strip().lower()
    for suffix, primary_keys in _ACTION_PRIMARY_KEYS:
        if action.endswith(suffix):
            for key in primary_keys:
                val = m.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()

    for key in (
        "new_capability_id",
        "target_capability",
        "target_component",
        "target_container",
        "target_architecture",
        "target_policy",
        "target_contract",
        "target_domain",
        "target_persona",
        "target_screen",
        "target_ui_component",
        "target_invariant_id",
        "target_design_system",
        "target_client",
        "target_flow",
    ):
        val = m.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # Fallback to regex in details
    details = m.get("details", "")
    if details:
        match = re.search(
            r"\b(CNT|CMP|CAP|CTR|POL|DOM|SCR|UIC|DS|PER|INV|CLT|FLOW|ARCH|SYS)-[A-Z0-9_-]+\b",
            details,
        )
        if match:
            return match.group(0)
        path_match = re.search(r"['\"]?([a-zA-Z0-9_\-\./]+\.adoc)['\"]?", details)
        if path_match:
            return path_match.group(1)

    return "unspecified_target"


def _find_inline_component_dict(
    comp_id: str,
    cnt_fm: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the inline component dict for comp_id from a container frontmatter, if present."""
    if not isinstance(cnt_fm, dict):
        return None
    comps = cnt_fm.get("components", []) or cnt_fm.get("c4_internal_components", [])
    if isinstance(comps, list):
        for c in comps:
            if isinstance(c, dict) and c.get("id") == comp_id:
                return c
    return None


def _digest_dict(data: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hex digest for a frontmatter sub-dictionary."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _snapshot_delta_baselines(eidos_dir: Path, delta_id: str) -> None:
    """Record current SHA-256 digests of all living specs into eidos/.cache/index.db for delta verification."""
    try:
        cache_dir = eidos_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = cache_dir / "index.db"
        specs_by_id, container_components, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
        rows: list[tuple[str, str, str]] = []
        for spec_id, (spec_path, _, _) in specs_by_id.items():
            if spec_path.exists():
                sha = hashlib.sha256(spec_path.read_bytes()).hexdigest()
                rows.append((delta_id, spec_id, sha))
                rows.append((delta_id, str(spec_path.resolve()), sha))
        for comp_id, cnt_id in container_components.items():
            if cnt_id in specs_by_id:
                cnt_path, _, cnt_fm = specs_by_id[cnt_id]
                comp_dict = _find_inline_component_dict(comp_id, cnt_fm)
                if comp_dict is not None:
                    rows.append((delta_id, comp_id, _digest_dict(comp_dict)))
                elif cnt_path.exists():
                    sha = hashlib.sha256(cnt_path.read_bytes()).hexdigest()
                    rows.append((delta_id, comp_id, sha))
        for inv_id, (inv_path, inv_dict) in invariant_map.items():
            if inv_path.exists():
                rows.append((delta_id, inv_id, _digest_dict(inv_dict)))

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS delta_baselines ("
                "delta_id TEXT, target_id TEXT, baseline_sha256 TEXT, "
                "PRIMARY KEY (delta_id, target_id))"
            )
            conn.executemany(
                "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
    except (OSError, sqlite3.Error):
        return


def _check_or_record_baseline_sha(
    eidos_dir: Path,
    delta_id: str,
    delta_file: Path,
    target_key: str,
    target_file: Path | None,
    current_sha: str,
) -> bool:
    """Compare current_sha against SQLite delta_baselines, recording a baseline and returning False when absent."""
    _ = (delta_file, target_file)
    try:
        cache_dir = eidos_dir / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = cache_dir / "index.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS delta_baselines ("
                "delta_id TEXT, target_id TEXT, baseline_sha256 TEXT, "
                "PRIMARY KEY (delta_id, target_id))"
            )
            row = conn.execute(
                "SELECT baseline_sha256 FROM delta_baselines WHERE delta_id = ? AND target_id = ?",
                (delta_id, target_key),
            ).fetchone()
            if row is not None:
                return current_sha != row[0]

            conn.execute(
                "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                (delta_id, target_key, current_sha),
            )
            conn.commit()
            return False
    except (OSError, sqlite3.Error):
        return False


def _check_structural_subtarget_precondition(
    m: dict[str, Any],
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]],
    container_components: dict[str, str],
) -> bool:
    """Verify structural parent/sub-target membership when both parent and child target keys are specified.

    Returns False if a declared parent/sub-target relationship is missing, or True if satisfied (or not applicable).
    """
    t_cap = m.get("target_capability")
    t_inv = m.get("target_invariant_id")
    if t_cap and t_inv:
        if t_cap not in specs_by_id:
            return False
        _, _, cap_fm = specs_by_id[t_cap]
        invs = (cap_fm or {}).get("invariants", []) or ((cap_fm or {}).get("technical", {}) or {}).get("invariants", [])
        inv_ids = {i.get("id") if isinstance(i, dict) else str(i) for i in (invs or [])}
        if t_inv not in inv_ids:
            return False

    t_cnt = m.get("target_container")
    t_cmp = m.get("target_component")
    if t_cnt and t_cmp and t_cnt != t_cmp:
        if t_cnt not in specs_by_id:
            return False
        in_cnt = container_components.get(t_cmp) == t_cnt or (
            t_cmp in specs_by_id and (specs_by_id[t_cmp][2] or {}).get("container") == t_cnt
        )
        if not in_cnt:
            return False

    owner_cnt = t_cnt or t_cmp
    t_ctr = m.get("target_contract")
    if owner_cnt and t_ctr and owner_cnt != t_ctr:
        if owner_cnt not in specs_by_id:
            return False
        _, _, cnt_fm = specs_by_id[owner_cnt]
        contracts = (cnt_fm or {}).get("contracts", {}) or {}
        all_ctrs = (
            (contracts.get("provides") or [])
            + (contracts.get("consumes") or [])
            + (contracts.get("requires") or [])
        )
        if t_ctr not in all_ctrs:
            if t_ctr in specs_by_id:
                ctr_fm = specs_by_id[t_ctr][2] or {}
                if (ctr_fm.get("owning_container") or ctr_fm.get("owning_component")) != owner_cnt:
                    return False
            else:
                return False

    return True


def _evaluate_declarative_postcondition(
    m: dict[str, Any],
    target: str,
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]],
    container_components: dict[str, str],
    invariant_map: dict[str, tuple[Path, dict[str, Any]]],
    target_file: Path | None = None,
) -> bool | None:
    """Evaluate declarative expect block if present on a mutation.

    Returns True if satisfied, False if unsatisfied, or None if no declarative post-condition is specified.
    """
    expect = m.get("expect")
    if isinstance(expect, dict) and expect:
        target_dict: dict[str, Any] = {}
        raw_text = ""
        if target in invariant_map:
            inv_path, inv_data = invariant_map[target]
            target_dict = inv_data
            if inv_path.exists():
                raw_text = inv_path.read_text(encoding="utf-8")
        elif target in specs_by_id:
            spec_path, _, spec_fm = specs_by_id[target]
            target_dict = spec_fm or {}
            if spec_path.exists():
                raw_text = spec_path.read_text(encoding="utf-8")
        elif target in container_components:
            cnt_id = container_components[target]
            if cnt_id in specs_by_id:
                cnt_path, _, cnt_fm = specs_by_id[cnt_id]
                if cnt_path.exists():
                    raw_text = cnt_path.read_text(encoding="utf-8")
                comp_dict = _find_inline_component_dict(target, cnt_fm)
                if comp_dict is not None:
                    target_dict = comp_dict
        elif target_file is not None and target_file.exists():
            raw_text = target_file.read_text(encoding="utf-8")
            parsed_fm, _, _ = extract_frontmatter(raw_text)
            if isinstance(parsed_fm, dict):
                target_dict = parsed_fm

        for k, exp_v in expect.items():
            if k == "contains":
                if str(exp_v) not in raw_text:
                    return False
                continue
            actual = target_dict.get(k)
            if isinstance(exp_v, str) and isinstance(actual, str):
                if exp_v not in actual and actual != exp_v:
                    return False
            elif isinstance(actual, list) and not isinstance(exp_v, list):
                if exp_v not in actual:
                    return False
            elif actual != exp_v:
                return False
        return True

    return None


def verify_delta_mutations(
    eidos_dir: Path, delta_id_or_path: str
) -> tuple[bool, str, DeltaVerificationReport | None]:
    """Verify that all semantic mutations declared in a SpecDelta are fulfilled in living specifications."""
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None

    raw_mutations = doc.frontmatter.get("mutations", [])
    if not isinstance(raw_mutations, list):
        raw_mutations = []

    delta_status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
    specs_by_id, container_components, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
    invariants = set(invariant_map.keys())

    items: list[MutationVerificationItem] = []
    for m in raw_mutations:
        action = str(m.get("action", "")).strip().lower()
        target = _extract_mutation_target(m)
        details = str(m.get("details", "")).strip()

        verified = False
        status = "UNKNOWN"

        target_path_resolved: Path | None = None
        target_path_exists = False
        if "/" in target or target.endswith(".adoc"):
            clean_p = target.strip("'\"")
            cand_rel = eidos_dir / clean_p
            cand_abs = Path(clean_p)
            if cand_rel.exists():
                target_path_exists = True
                target_path_resolved = cand_rel.resolve()
            elif cand_abs.is_absolute() and cand_abs.exists():
                target_path_exists = True
                target_path_resolved = cand_abs.resolve()

        # 1. Removal / Deprecation Actions
        if action in (
            "remove_component",
            "remove_container",
            "remove_contract",
            "remove_domain",
            "remove_persona",
            "remove_design_system",
            "remove_screen",
            "remove_ui_component",
            "remove_client",
            "remove_flow",
        ):
            still_present = (
                target in specs_by_id
                or target in container_components
                or target_path_exists
            )
            if not still_present:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"

        elif action in ("deprecate_capability", "remove_capability", "deprecate_policy", "remove_policy"):
            if target not in specs_by_id and not target_path_exists:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                doc_fm = specs_by_id[target][2] if target in specs_by_id else {}
                if str(doc_fm.get("status", "")).lower() in ("deprecated", "sunset", "removed"):
                    verified = True
                    status = "DEPRECATED"
                else:
                    verified = False
                    status = "ACTIVE_NOT_DEPRECATED"

        # 2. Invariant Actions
        elif action == "add_invariant":
            if target in invariants and _check_structural_subtarget_precondition(
                m, specs_by_id, container_components
            ):
                decl_ok = _evaluate_declarative_postcondition(
                    m, target, specs_by_id, container_components, invariant_map
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                else:
                    verified = True
                    status = "FOUND"
            else:
                verified = False
                status = "MISSING"

        elif action == "patch_invariant":
            if target not in invariant_map or not _check_structural_subtarget_precondition(
                m, specs_by_id, container_components
            ):
                verified = False
                status = "MISSING"
            else:
                decl_ok = _evaluate_declarative_postcondition(
                    m, target, specs_by_id, container_components, invariant_map
                )
                if decl_ok is not None:
                    verified = decl_ok
                    status = ("FOUND" if delta_status == "applied" else "MODIFIED") if decl_ok else "UNMODIFIED"
                elif delta_status == "applied":
                    verified = True
                    status = "FOUND"
                else:
                    inv_file, inv_dict = invariant_map[target]
                    inv_sha = _digest_dict(inv_dict)
                    mod_ok = _check_or_record_baseline_sha(
                        eidos_dir, doc.doc_id, file_path, target, inv_file, inv_sha
                    )
                    verified = mod_ok
                    status = "MODIFIED" if mod_ok else "UNMODIFIED"

        elif action == "remove_invariant":
            if target not in invariants:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"

        # 3. Modification / Extension / Patch Actions
        elif action == "modify_architecture" or action.startswith(("modify_", "extend_", "patch_")):
            target_file: Path | None = None
            if target in specs_by_id:
                target_file = specs_by_id[target][0]
            elif target in container_components and container_components[target] in specs_by_id:
                target_file = specs_by_id[container_components[target]][0]
            elif target_path_resolved is not None:
                target_file = target_path_resolved
            elif action == "modify_architecture":
                for _, (p, d_type, _) in specs_by_id.items():
                    if d_type == "tech/architecture":
                        target_file = p
                        break

            if (
                target_file is None
                or not target_file.exists()
                or not _check_structural_subtarget_precondition(m, specs_by_id, container_components)
            ):
                verified = False
                status = "MISSING"
            else:
                decl_ok = _evaluate_declarative_postcondition(
                    m, target, specs_by_id, container_components, invariant_map, target_file=target_file
                )
                if decl_ok is not None:
                    verified = decl_ok
                    status = ("FOUND" if delta_status == "applied" else "MODIFIED") if decl_ok else "UNMODIFIED"
                elif delta_status == "applied":
                    verified = True
                    status = "FOUND"
                else:
                    cur_sha: str | None = None
                    if target not in specs_by_id and target in container_components:
                        cnt_id = container_components[target]
                        if cnt_id in specs_by_id:
                            comp_dict = _find_inline_component_dict(target, specs_by_id[cnt_id][2])
                            if comp_dict is not None:
                                cur_sha = _digest_dict(comp_dict)
                    if cur_sha is None:
                        cur_sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
                    mod_ok = _check_or_record_baseline_sha(
                        eidos_dir, doc.doc_id, file_path, target, target_file, cur_sha
                    )
                    verified = mod_ok
                    status = "MODIFIED" if mod_ok else "UNMODIFIED"

        # 4. Standard Addition Actions
        else:
            found = (
                target in specs_by_id
                or target in container_components
                or target_path_exists
            ) and _check_structural_subtarget_precondition(m, specs_by_id, container_components)
            if found:
                decl_ok = _evaluate_declarative_postcondition(
                    m,
                    target,
                    specs_by_id,
                    container_components,
                    invariant_map,
                    target_file=target_path_resolved,
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                else:
                    verified = True
                    status = "FOUND"
            else:
                verified = False
                status = "MISSING"

        items.append(
            MutationVerificationItem(
                action=action,
                target=target,
                verified=verified,
                status=status,
                details=details,
            )
        )

    verified_count = sum(1 for it in items if it.verified)
    total_mutations = len(items)
    is_verified = (verified_count == total_mutations)

    report = DeltaVerificationReport(
        delta_id=doc.doc_id,
        delta_file=file_path,
        total_mutations=total_mutations,
        verified_count=verified_count,
        is_verified=is_verified,
        mutations=items,
    )

    msg = (
        f"All {total_mutations} mutation(s) verified for SpecDelta '{doc.doc_id}'."
        if is_verified
        else f"{verified_count}/{total_mutations} mutation(s) verified for SpecDelta '{doc.doc_id}' ({total_mutations - verified_count} unfulfilled)."
    )
    return is_verified, msg, report


def execute_delta_mutations(
    eidos_dir: Path, delta_id_or_path: str
) -> tuple[bool, str, list[Path]]:
    """Deterministically scaffold and execute missing specifications declared in a SpecDelta."""
    # Function-local import: create_spec (scaffolder) itself depends on this module's view/baseline helpers,
    # so a module-level import would create an import cycle (DLT-2026-038).
    from eidos.core.lifecycle.scaffolder import create_spec

    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", []

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", []

    raw_mutations = doc.frontmatter.get("mutations", [])
    if not isinstance(raw_mutations, list):
        raw_mutations = []

    specs_by_id, container_components, _ = _build_spec_catalog(eidos_dir)
    mutated_files: list[Path] = []

    for m in raw_mutations:
        action = str(m.get("action", "")).strip().lower()
        target = _extract_mutation_target(m)
        details = str(m.get("details", "")).strip()

        # 1. Add Capability
        if action == "add_capability":
            target_id = m.get("new_capability_id") or m.get("target_capability") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                domain = m.get("target_domain")
                if not domain:
                    domains = [did for did, (_, dt, _) in specs_by_id.items() if dt == "product/domain"]
                    domain = domains[0] if domains else "DOM-CORE"
                title = m.get("title") or target_id.replace("CAP-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="capability",
                    raw_id=target_id,
                    title=title,
                    domain=domain,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "product/capability", {})

        # 2. Add Policy
        elif action == "add_policy":
            target_id = m.get("target_policy") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("POL-", "").replace("-", " ").title()
                rule = details or f"Rule statement for {target_id}."
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="policy",
                    raw_id=target_id,
                    title=title,
                    rule=rule,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "product/policy", {})

        # 3. Add Component / Container
        elif action in ("add_component", "add_container"):
            target_id = (
                (m.get("target_container") or target)
                if action == "add_container"
                else (m.get("target_component") or m.get("target_container") or target)
            )
            if target_id.startswith("CNT-") and target_id not in specs_by_id:
                title = m.get("title") or target_id.replace("CNT-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="container",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "tech/container", {})
            elif target_id.startswith("CMP-") and target_id not in container_components:
                target_cnt = m.get("target_container")
                cnt_file = None
                if target_cnt and target_cnt in specs_by_id:
                    cnt_file = specs_by_id[target_cnt][0]
                else:
                    for cid, (p, dt, _) in specs_by_id.items():
                        if dt == "tech/container":
                            cnt_file = p
                            target_cnt = cid
                            break
                if cnt_file and cnt_file.exists():
                    cnt_content = cnt_file.read_text(encoding="utf-8")
                    cnt_fm, _cnt_body, _ = extract_frontmatter(cnt_content)
                    if cnt_fm is not None:
                        comps = cnt_fm.setdefault("components", [])
                        new_comp = {
                            "id": target_id,
                            "name": target_id.replace("CMP-", "").replace("-", " ").title(),
                            "responsibility": details or f"Component responsible for {target_id}.",
                        }
                        if not any(c.get("id") == target_id for c in comps):
                            updated_content = _mutate_frontmatter_components(
                                cnt_content, "append", target_id, new_comp
                            )
                            cnt_file.write_text(updated_content, encoding="utf-8")
                            container_components[target_id] = target_cnt or "CNT-CORE"
                            if cnt_file not in mutated_files:
                                mutated_files.append(cnt_file)

        # 4. Remove Component / Container
        elif action in ("remove_component", "remove_container"):
            target_id = (
                (m.get("target_container") or target)
                if action == "remove_container"
                else (m.get("target_component") or m.get("target_container") or target)
            )
            if target_id in container_components:
                cnt_id = container_components[target_id]
                if cnt_id in specs_by_id:
                    cnt_file = specs_by_id[cnt_id][0]
                    if cnt_file.exists():
                        cnt_content = cnt_file.read_text(encoding="utf-8")
                        cnt_fm, _cnt_body, _ = extract_frontmatter(cnt_content)
                        if cnt_fm and "components" in cnt_fm:
                            updated_content = _mutate_frontmatter_components(
                                cnt_content, "remove", target_id
                            )
                            cnt_file.write_text(updated_content, encoding="utf-8")
                            del container_components[target_id]
                            if cnt_file not in mutated_files:
                                mutated_files.append(cnt_file)
            elif target_id in specs_by_id:
                cnt_file = specs_by_id[target_id][0]
                if cnt_file.exists():
                    cnt_file.unlink()
                    del specs_by_id[target_id]
                    if cnt_file not in mutated_files:
                        mutated_files.append(cnt_file)

        # 5. Add Contract
        elif action == "add_contract":
            target_id = m.get("target_contract") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("CTR-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="contract",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "tech/contract", {})

        # 6. Remove Standalone Specification (Contract, Domain, Persona, Design System, Screen, UI Component, Client, Flow)
        elif action in (
            "remove_contract",
            "remove_domain",
            "remove_persona",
            "remove_design_system",
            "remove_screen",
            "remove_ui_component",
            "remove_client",
            "remove_flow",
        ):
            target_id = m.get("target_contract") or target
            if target_id in specs_by_id:
                ctr_file = specs_by_id[target_id][0]
                if ctr_file.exists():
                    ctr_file.unlink()
                    del specs_by_id[target_id]
                    if ctr_file not in mutated_files:
                        mutated_files.append(ctr_file)

        # 7. Add Screen
        elif action == "add_screen":
            target_id = m.get("target_screen") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("SCR-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="screen",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "design/screen", {})

        # 8. Add UI Component
        elif action == "add_ui_component":
            target_id = m.get("target_ui_component") or target
            if target_id not in specs_by_id and not target_id.startswith("unspecified"):
                title = m.get("title") or target_id.replace("UIC-", "").replace("-", " ").title()
                created = create_spec(
                    eidos_dir=eidos_dir,
                    doc_type_key="ui-component",
                    raw_id=target_id,
                    title=title,
                )
                if created and created not in mutated_files:
                    mutated_files.append(created)
                specs_by_id[target_id] = (created, "design/ui-component", {})

    return True, f"Successfully executed mutations for '{doc.doc_id}' ({len(mutated_files)} files created/modified).", mutated_files


def apply_delta(
    eidos_dir: Path,
    delta_id_or_path: str,
    execute: bool = False,
    verify: bool = False,
) -> tuple[bool, str, Path | None, list[Path]]:
    """Transition a SpecDelta to applied status in-place and sync gitignored symlink views.

    Args:
        eidos_dir: Path to the eidos specifications root.
        delta_id_or_path: ID, filename, or Path of the delta to apply.
        execute: If True, deterministically scaffold missing specs and apply structural mutations.
        verify: If True, verify that all mutations are fulfilled in living specs before applying.
    """
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None, []

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None, []

    mutated_files: list[Path] = []
    if execute:
        exec_ok, exec_msg, mutated_files = execute_delta_mutations(eidos_dir, delta_id_or_path)
        if not exec_ok:
            return False, f"Failed executing delta mutations: {exec_msg}", file_path, mutated_files

    if verify:
        ver_ok, ver_msg, report = verify_delta_mutations(eidos_dir, delta_id_or_path)
        if not ver_ok:
            unfulfilled = [
                f"{m.action}:{m.target}" for m in (report.mutations if report else []) if not m.verified
            ]
            return (
                False,
                f"Verification failed before apply: {ver_msg} Unfulfilled: {', '.join(unfulfilled)}",
                file_path,
                mutated_files,
            )

    current_status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
    if current_status == "applied":
        sync_delta_views(eidos_dir)
        return True, f"SpecDelta '{doc.doc_id}' is already marked as applied.", file_path, mutated_files

    # Read and update frontmatter status to applied in-place while preserving YAML comments
    content = file_path.read_text(encoding="utf-8")
    fm, _body, err = extract_frontmatter(content)
    if err or not fm:
        return False, f"Failed to extract frontmatter: {err}", None, mutated_files

    new_content = _set_frontmatter_scalar(content, "status", "applied")

    file_path.write_text(new_content, encoding="utf-8")

    # Validate applied document in-place
    doc_applied = parse_spec_file(file_path)
    validator = SpecValidator()
    errors = validator.validate_document(doc_applied)
    if errors:
        return False, f"Delta updated to applied but failed validation: {'; '.join(errors)}", file_path, mutated_files

    # Synchronize gitignored symlink views
    sync_delta_views(eidos_dir)

    return True, f"Successfully applied SpecDelta '{doc.doc_id}' in-place -> {file_path.name}.", file_path, mutated_files
