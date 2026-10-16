"""SpecDelta engine: listing, status summaries, by-status view sync, baseline snapshots, verification, execution,
and application of semantic mutations (CMP-DELTA-ENGINE)."""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.lifecycle.catalog import (
    _build_spec_catalog_with_invariants,
    _mutate_frontmatter_components,
    _render_spec,
    _set_frontmatter_scalar,
    find_spec_by_id_or_path,
)
from eidos.core.spec.models import SpecDocument
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator

ADD_STANDALONE_ACTIONS: dict[str, tuple[str, str, str, str]] = {
    "add_domain": ("domain", "target_domain", "DOM-", "product/domain"),
    "add_capability": ("capability", "new_capability_id", "CAP-", "product/capability"),
    "add_persona": ("persona", "target_persona", "PER-", "product/persona"),
    "add_policy": ("policy", "target_policy", "POL-", "product/policy"),
    "add_container": ("container", "target_container", "CNT-", "tech/container"),
    "add_contract": ("contract", "target_contract", "CTR-", "tech/contract"),
    "add_design_system": ("design-system", "target_design_system", "DS-", "design/system"),
    "add_ui_component": ("ui-component", "target_ui_component", "UIC-", "design/ui-component"),
    "add_screen": ("screen", "target_screen", "SCR-", "design/screen"),
    "add_client": ("client", "target_client", "CLT-", "design/client"),
    "add_flow": ("flow", "target_flow", "FLOW-", "design/flow"),
}

ADD_ACTIONS: frozenset[str] = frozenset((*ADD_STANDALONE_ACTIONS.keys(), "add_component", "add_invariant"))

REMOVE_ACTION_EXPECTED_TYPES: dict[str, str] = {
    "remove_domain": "product/domain",
    "remove_capability": "product/capability",
    "remove_persona": "product/persona",
    "remove_policy": "product/policy",
    "remove_container": "tech/container",
    "remove_component": "tech/component",
    "remove_contract": "tech/contract",
    "remove_design_system": "design/system",
    "remove_ui_component": "design/ui-component",
    "remove_screen": "design/screen",
    "remove_client": "design/client",
    "remove_flow": "design/flow",
}

DEPRECATE_ACTION_EXPECTED_TYPES: dict[str, str] = {
    "deprecate_capability": "product/capability",
    "deprecate_policy": "product/policy",
}

MODIFY_ACTION_EXPECTED_TYPES: dict[str, str] = {
    "modify_domain": "product/domain",
    "modify_capability": "product/capability",
    "extend_capability": "product/capability",
    "modify_persona": "product/persona",
    "modify_policy": "product/policy",
    "patch_policy": "product/policy",
    "modify_architecture": "tech/architecture",
    "modify_container": "tech/container",
    "modify_component": "tech/component",
    "modify_contract": "tech/contract",
    "modify_design_system": "design/system",
    "modify_ui_component": "design/ui-component",
    "modify_screen": "design/screen",
    "modify_client": "design/client",
    "modify_flow": "design/flow",
}

SUPPORTED_MUTATION_ACTIONS: frozenset[str] = (
    ADD_ACTIONS
    | frozenset(REMOVE_ACTION_EXPECTED_TYPES.keys())
    | frozenset(("remove_invariant",))
    | frozenset(DEPRECATE_ACTION_EXPECTED_TYPES.keys())
    | frozenset(MODIFY_ACTION_EXPECTED_TYPES.keys())
    | frozenset(("patch_invariant",))
)


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
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


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


def _resolve_git_repo_root(eidos_dir: Path) -> Path | None:
    """Return the enclosing Git repository root for eidos_dir if one exists directly on or above eidos_dir."""
    for candidate in (eidos_dir.resolve(), eidos_dir.resolve().parent):
        if (candidate / ".git").exists():
            return candidate
    return None


def _resolve_git_head_sha(eidos_dir: Path) -> str | None:
    """Resolve the current Git HEAD commit SHA for eidos_dir, or None if not in a Git repository with commits."""
    repo_root = _resolve_git_repo_root(eidos_dir)
    if repo_root is None:
        return None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return None


def generate_collision_free_delta_id(
    eidos_dir: Path,
    raw_id: str | None = None,
    title: str = "",
    slug_suffix: bool = False,
) -> str:
    """Generate or normalize a SpecDelta ID, scanning local and Git ref deltas and optionally appending a collision-free slug/hash."""
    import datetime

    from eidos.core.lifecycle.catalog import normalize_id

    deltas_dir = eidos_dir / "deltas"
    existing_ids: set[str] = set()
    max_seq_by_year: dict[int, int] = {}
    seq_re = re.compile(r"^DLT-(\d{4})-(\d{3,})(?:-[A-Z0-9_-]+)?$", re.IGNORECASE)

    def _record_candidate(stem_or_id: str) -> None:
        norm = stem_or_id.strip().upper().replace("_", "-")
        if not norm.startswith("DLT-"):
            return
        existing_ids.add(norm)
        m = seq_re.match(norm)
        if m:
            yr, seq = int(m.group(1)), int(m.group(2))
            if seq > max_seq_by_year.get(yr, 0):
                max_seq_by_year[yr] = seq

    if deltas_dir.exists():
        for p in deltas_dir.rglob("*.adoc"):
            if "by-status" in p.parts:
                continue
            _record_candidate(p.stem)

    repo_root = _resolve_git_repo_root(eidos_dir)
    if repo_root is not None:
        with contextlib.suppress(ValueError, OSError, subprocess.SubprocessError):
            rel_deltas = str(deltas_dir.resolve().relative_to(repo_root)).replace("\\", "/")
            for ref in ("HEAD", "main", "origin/main", "master", "origin/master"):
                ls_res = subprocess.run(
                    ["git", "-C", str(repo_root), "ls-tree", "--name-only", ref, f"{rel_deltas}/"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if ls_res.returncode == 0:
                    for ln in ls_res.stdout.splitlines():
                        name = Path(ln.strip()).stem
                        if name:
                            _record_candidate(name)

    cleaned_raw = (raw_id or "").strip()
    is_auto = cleaned_raw.lower() in ("", "auto", "auto-slug")
    if cleaned_raw.lower() == "auto-slug":
        slug_suffix = True

    year = datetime.datetime.now(datetime.timezone.utc).year
    if is_auto:
        next_seq = max_seq_by_year.get(year, 0) + 1
        base_id = f"DLT-{year}-{next_seq:03d}"
    else:
        base_id = normalize_id("delta", cleaned_raw)

    if slug_suffix or (is_auto and base_id in existing_ids):
        slug_tokens = [tok for tok in re.split(r"[^A-Za-z0-9]+", title.upper()) if len(tok) >= 2][:2]
        suffix = "-".join(slug_tokens) if slug_tokens else hashlib.sha256(
            f"{base_id}:{title}:{os.getpid()}".encode("utf-8")
        ).hexdigest()[:6].upper()
        candidate_id = f"{base_id}-{suffix}"
        if candidate_id in existing_ids:
            short_hash = hashlib.sha256(f"{candidate_id}:{title}".encode("utf-8")).hexdigest()[:4].upper()
            candidate_id = f"{candidate_id}-{short_hash}"
        return candidate_id

    return base_id


def create_delta(
    eidos_dir: Path,
    raw_id: str = "auto",
    title: str = "Untitled SpecDelta",
    kind: str = "feature",
    motivation: str = "Proposed change motivation.",
    slug_suffix: bool = False,
) -> Path:
    """Create a SpecDelta with automatic collision-free ID resolution and vcs.base_commit baseline capture."""
    from eidos.core.lifecycle.scaffolder import create_spec

    resolved_id = generate_collision_free_delta_id(
        eidos_dir,
        raw_id=raw_id,
        title=title,
        slug_suffix=slug_suffix,
    )
    return create_spec(
        eidos_dir=eidos_dir,
        doc_type_key="delta",
        raw_id=resolved_id,
        title=title,
        kind=kind,
        motivation=motivation,
    )


def _resolve_git_baseline_sha(
    eidos_dir: Path,
    delta_file: Path,
    target_key: str,
    target_file: Path | None,
) -> str | None:
    """Recover the pre-delta baseline SHA-256 of target_key from vcs.base_commit or Git history when SQLite cache is absent."""
    if target_file is None or not target_file.exists():
        return None

    repo_root = _resolve_git_repo_root(eidos_dir)
    if repo_root is None:
        return None

    try:
        rel_target = str(target_file.resolve().relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return None

    revs: list[str] = []

    def _add_rev(rev_candidate: str | None) -> None:
        if rev_candidate and rev_candidate not in revs:
            revs.append(rev_candidate)

    # 1. Check vcs.base_commit recorded in the SpecDelta frontmatter (portable across machines/CI and uncommitted deltas)
    if delta_file.exists():
        with contextlib.suppress(OSError):
            dlt_fm, _, _ = extract_frontmatter(delta_file.read_text(encoding="utf-8"))
            if isinstance(dlt_fm, dict):
                vcs_block = dlt_fm.get("vcs")
                if isinstance(vcs_block, dict):
                    base_commit = vcs_block.get("base_commit")
                    if isinstance(base_commit, str) and base_commit.strip():
                        _add_rev(base_commit.strip())

    # 2. Check Git commit history where the SpecDelta file was added
    with contextlib.suppress(ValueError, OSError, subprocess.SubprocessError):
        rel_delta = str(delta_file.resolve().relative_to(repo_root)).replace("\\", "/")
        log_res = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--diff-filter=A", "--format=%H", "--", rel_delta],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        commits = [ln.strip() for ln in log_res.stdout.splitlines() if ln.strip()]
        if commits:
            first_commit = commits[-1]
            parent_check = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", f"{first_commit}~1"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if parent_check.returncode == 0:
                _add_rev(f"{first_commit}~1")
            _add_rev(first_commit)

    # 3. For uncommitted delta files without vcs.base_commit, try merge-base against main/origin/main then HEAD
    if not revs:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            for base_branch in ("origin/main", "main", "origin/master", "master"):
                mb_res = subprocess.run(
                    ["git", "-C", str(repo_root), "merge-base", "HEAD", base_branch],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if mb_res.returncode == 0 and mb_res.stdout.strip():
                    _add_rev(mb_res.stdout.strip())
        _add_rev("HEAD")

    for rev in revs:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            show_res = subprocess.run(
                ["git", "-C", str(repo_root), "show", f"{rev}:{rel_target}"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if show_res.returncode != 0 or not show_res.stdout:
                continue
            raw_bytes = show_res.stdout
            if target_key.startswith("CMP-") and target_file.stem.upper() != target_key:
                hist_fm, _, _ = extract_frontmatter(raw_bytes.decode("utf-8", errors="replace"))
                comp_dict = _find_inline_component_dict(target_key, hist_fm)
                if comp_dict is not None:
                    return _digest_dict(comp_dict)
                return None
            if target_key.startswith("INV-"):
                hist_fm, _, _ = extract_frontmatter(raw_bytes.decode("utf-8", errors="replace"))
                if isinstance(hist_fm, dict):
                    invs = hist_fm.get("invariants", []) or ((hist_fm.get("technical", {}) or {}).get("invariants", []))
                    for inv in invs or []:
                        if isinstance(inv, dict) and inv.get("id") == target_key:
                            return _digest_dict(inv)
                return None
            return hashlib.sha256(raw_bytes).hexdigest()

    return None


def _check_or_record_baseline_sha(
    eidos_dir: Path,
    delta_id: str,
    delta_file: Path,
    target_key: str,
    target_file: Path | None,
    current_sha: str,
) -> bool:
    """Compare current_sha against SQLite delta_baselines (with Git history fallback on cache miss)."""
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

            git_sha = _resolve_git_baseline_sha(eidos_dir, delta_file, target_key, target_file)
            baseline_sha = git_sha if git_sha is not None else current_sha
            conn.execute(
                "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                (delta_id, target_key, baseline_sha),
            )
            conn.commit()
            return current_sha != baseline_sha
    except (OSError, sqlite3.Error):
        return False


def _parse_spec_content(path: Path, content: str) -> SpecDocument:
    """Parse raw AsciiDoc content into a SpecDocument in memory without writing to disk."""
    if not content.strip():
        doc_type = "product/system" if path.name == "system.adoc" else "unknown"
        return SpecDocument(
            file_path=path,
            frontmatter={},
            body="",
            doc_type=doc_type,
            doc_id=path.stem,
        )

    frontmatter_dict, body, parse_error = extract_frontmatter(content)
    doc_type = frontmatter_dict.get("type", "unknown") if frontmatter_dict else "unknown"
    doc_id = frontmatter_dict.get("id", path.stem) if frontmatter_dict else path.stem
    schema_ref = frontmatter_dict.get("schema") if frontmatter_dict else None
    title = (frontmatter_dict.get("title") or frontmatter_dict.get("name")) if frontmatter_dict else None

    doc = SpecDocument(
        file_path=path,
        frontmatter=frontmatter_dict or {},
        body=body,
        doc_type=doc_type,
        doc_id=doc_id,
        schema_ref=schema_ref,
        title=title,
    )
    if parse_error:
        doc.errors.append(f"Frontmatter parse error: {parse_error}")
    elif not frontmatter_dict:
        doc.errors.append("Missing YAML frontmatter block (delimited by '---')")
    return doc


class _MutationTransaction:
    """Copy-on-write transactional filesystem snapshot and rollback manager for SpecDelta execution and application."""

    def __init__(self, eidos_dir: Path) -> None:
        self.eidos_dir = eidos_dir.resolve()
        self.original_files: dict[Path, bytes] = {}
        self.original_dirs: set[Path] = set()
        self._initial_files: set[Path] = set()
        self.created_files: set[Path] = set()

    def snapshot(self, target_files: list[Path] | None = None) -> None:
        """Record initial directory/file inventory without reading all of eidos/ into RAM; optionally pre-snapshot target_files."""
        self.original_files.clear()
        self.original_dirs.clear()
        self._initial_files.clear()
        self.created_files.clear()
        if not self.eidos_dir.exists():
            return
        self.original_dirs.add(self.eidos_dir)
        for root, dirs, files in os.walk(self.eidos_dir):
            dirs[:] = [d for d in dirs if d not in (".cache", "by-status")]
            root_path = Path(root).resolve()
            self.original_dirs.add(root_path)
            for d in dirs:
                self.original_dirs.add((root_path / d).resolve())
            for fn in files:
                fp = (root_path / fn).resolve()
                if not fp.is_symlink():
                    self._initial_files.add(fp)
        for tf in target_files or ():
            self.record_before_write(tf)

    def record_before_write(self, path: Path) -> None:
        """Copy-on-write snapshot of a single file immediately prior to mutation or deletion."""
        fp = path.resolve()
        if fp in self.original_files or fp in self.created_files:
            return
        if fp.exists() and not fp.is_symlink():
            with contextlib.suppress(OSError):
                self.original_files[fp] = fp.read_bytes()
                self._initial_files.add(fp)
        else:
            self.created_files.add(fp)

    def rollback(self) -> None:
        if not self.eidos_dir.exists():
            return
        current_files: set[Path] = set()
        current_dirs: list[Path] = []
        for root, dirs, files in os.walk(self.eidos_dir):
            dirs[:] = [d for d in dirs if d not in (".cache", "by-status")]
            root_path = Path(root).resolve()
            current_dirs.append(root_path)
            for fn in files:
                fp = (root_path / fn).resolve()
                if not fp.is_symlink():
                    current_files.add(fp)

        for fp in (current_files - self._initial_files) | self.created_files:
            with contextlib.suppress(OSError):
                if fp.exists():
                    fp.unlink()

        for fp, raw_bytes in self.original_files.items():
            with contextlib.suppress(OSError):
                fp.parent.mkdir(parents=True, exist_ok=True)
                if not fp.exists() or fp.read_bytes() != raw_bytes:
                    fp.write_bytes(raw_bytes)

        for dp in sorted(current_dirs, key=lambda p: len(p.parts), reverse=True):
            if dp not in self.original_dirs and dp.exists():
                with contextlib.suppress(OSError):
                    if not any(dp.iterdir()):
                        dp.rmdir()

        sync_delta_views(self.eidos_dir)


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
            (contracts.get("provides") or []) + (contracts.get("consumes") or []) + (contracts.get("requires") or [])
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


def verify_delta_mutations(eidos_dir: Path, delta_id_or_path: str) -> tuple[bool, str, DeltaVerificationReport | None]:
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

        if action not in SUPPORTED_MUTATION_ACTIONS:
            verified = False
            status = "UNSUPPORTED_ACTION"

        # 1. Removal / Deprecation Actions
        elif action in (
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
            still_present = target in specs_by_id or target in container_components or target_path_exists
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
            if target in invariants and _check_structural_subtarget_precondition(m, specs_by_id, container_components):
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
                    mod_ok = _check_or_record_baseline_sha(eidos_dir, doc.doc_id, file_path, target, inv_file, inv_sha)
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
        elif action in MODIFY_ACTION_EXPECTED_TYPES:
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
        elif action in ADD_ACTIONS:
            found = (
                target in specs_by_id or target in container_components or target_path_exists
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
    is_verified = verified_count == total_mutations

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


def _apply_expect_or_details_to_spec(
    spec_file: Path,
    expect: dict[str, Any] | None,
    details: str,
    tx: _MutationTransaction | None = None,
) -> bool:
    """Apply declarative expect fields or substantive details to a specification file on disk (never comment-only)."""
    content = spec_file.read_text(encoding="utf-8")
    updated = content
    if isinstance(expect, dict) and expect:
        fm, body, err = extract_frontmatter(updated)
        if err or not isinstance(fm, dict):
            return False
        fm_changed = False
        for k, v in expect.items():
            if k == "contains":
                if str(v) not in updated:
                    body = f"{body.rstrip()}\n\n{v}\n"
                    updated = _render_spec(fm, body) if fm_changed else f"{updated.rstrip()}\n\n{v}\n"
            elif isinstance(v, (str, int, float, bool)) and k in fm and not isinstance(fm.get(k), (dict, list)):
                val_str = json.dumps(v) if isinstance(v, str) else str(v).lower() if isinstance(v, bool) else str(v)
                updated = _set_frontmatter_scalar(updated, k, val_str)
                fm[k] = v
            else:
                fm[k] = v
                fm_changed = True
        if fm_changed:
            updated = _render_spec(fm, body)
    elif details and not details.lstrip().startswith("//"):
        fm, _body, err = extract_frontmatter(updated)
        if not err and isinstance(fm, dict) and "rule" in fm and isinstance(fm.get("rule"), str):
            updated = _set_frontmatter_scalar(updated, "rule", json.dumps(details))
        elif not err and isinstance(fm, dict) and "responsibility" in fm and isinstance(fm.get("responsibility"), str):
            updated = _set_frontmatter_scalar(updated, "responsibility", json.dumps(details))
        elif details not in updated:
            updated = f"{updated.rstrip()}\n\n{details}\n"
    else:
        return False

    if updated != content:
        if tx is not None:
            tx.record_before_write(spec_file)
        spec_file.write_text(updated, encoding="utf-8")
    return True


def execute_delta_mutations(
    eidos_dir: Path,
    delta_id_or_path: str,
    _tx: _MutationTransaction | None = None,
) -> tuple[bool, str, list[Path]]:
    """Deterministically scaffold and execute mutations declared in a SpecDelta with copy-on-write transactional rollback."""
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

    tx = _tx if _tx is not None else _MutationTransaction(eidos_dir)
    if _tx is None:
        tx.snapshot(target_files=[file_path])

    specs_by_id, container_components, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
    mutated_files: list[Path] = []
    deleted_files: set[Path] = set()

    def _abort(reason: str) -> tuple[bool, str, list[Path]]:
        tx.rollback()
        return False, reason, []

    try:
        for m in raw_mutations:
            if not isinstance(m, dict):
                return _abort(f"Invalid mutation entry in '{doc.doc_id}': expected mapping.")
            action = str(m.get("action", "")).strip().lower()
            target = _extract_mutation_target(m)
            details = str(m.get("details", "")).strip()
            expect = m.get("expect") if isinstance(m.get("expect"), dict) else None

            if action not in SUPPORTED_MUTATION_ACTIONS:
                return _abort(f"Unsupported mutation action '{action}' in SpecDelta '{doc.doc_id}'.")

            # 1. Standalone Addition Actions (11 specification types)
            if action in ADD_STANDALONE_ACTIONS:
                doc_type_key, primary_field, prefix, catalog_type = ADD_STANDALONE_ACTIONS[action]
                target_id = (
                    (m.get("new_capability_id") or m.get("target_capability") or target)
                    if action == "add_capability"
                    else (m.get(primary_field) or target)
                )
                if not target_id or target_id.startswith("unspecified"):
                    return _abort(f"Cannot execute '{action}': missing target identifier.")
                if target_id not in specs_by_id:
                    title = m.get("title") or target_id.replace(prefix, "").replace("-", " ").title()
                    kwargs: dict[str, Any] = {
                        "eidos_dir": eidos_dir,
                        "doc_type_key": doc_type_key,
                        "raw_id": target_id,
                        "title": title,
                    }
                    if action == "add_capability":
                        domain = m.get("target_domain")
                        if not domain:
                            domains = [did for did, (_, dt, _) in specs_by_id.items() if dt == "product/domain"]
                            domain = domains[0] if domains else "DOM-CORE"
                        kwargs["domain"] = domain
                    elif action == "add_policy":
                        kwargs["rule"] = (expect or {}).get("rule") or details or f"Rule statement for {target_id}."
                    elif action in ("add_contract", "add_screen", "add_client", "add_flow"):
                        if m.get("target_container"):
                            kwargs["owning_container"] = m["target_container"]
                    created = create_spec(**kwargs)
                    tx.created_files.add(created.resolve())
                    if expect:
                        _apply_expect_or_details_to_spec(created, expect, "")
                    if created and created not in mutated_files:
                        mutated_files.append(created)
                    created_doc = parse_spec_file(created)
                    specs_by_id[created_doc.doc_id] = (created, catalog_type, created_doc.frontmatter)

            # 2. Add Component (inline on container or standalone)
            elif action == "add_component":
                target_id = m.get("target_component") or target
                if not target_id or target_id.startswith("unspecified"):
                    return _abort("Cannot execute 'add_component': missing component target.")
                if target_id not in container_components and target_id not in specs_by_id:
                    target_cnt = m.get("target_container")
                    cnt_file: Path | None = None
                    if target_cnt:
                        if target_cnt not in specs_by_id or specs_by_id[target_cnt][1] != "tech/container":
                            return _abort(
                                f"Cannot execute 'add_component' for '{target_id}': target container '{target_cnt}' not found."
                            )
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
                            pkg_raw = cnt_fm.get("package_path", "src")
                            pkg_base = str(pkg_raw[0] if isinstance(pkg_raw, list) and pkg_raw else pkg_raw).rstrip("/")
                            comp_slug = target_id.replace("CMP-", "").lower().replace("-", "_")
                            new_comp: dict[str, Any] = {
                                "id": target_id,
                                "name": m.get("title") or target_id.replace("CMP-", "").replace("-", " ").title(),
                                "responsibility": details or f"Component responsible for {target_id}.",
                                "source_path": f"{pkg_base}/{comp_slug}.py",
                            }
                            if expect:
                                for ek, ev in expect.items():
                                    if ek != "contains":
                                        new_comp[ek] = ev
                            if not any(isinstance(c, dict) and c.get("id") == target_id for c in comps):
                                updated_content = _mutate_frontmatter_components(
                                    cnt_content, "append", target_id, new_comp
                                )
                                tx.record_before_write(cnt_file)
                                cnt_file.write_text(updated_content, encoding="utf-8")
                                container_components[target_id] = target_cnt or "CNT-CORE"
                                updated_doc = parse_spec_file(cnt_file)
                                if target_cnt:
                                    specs_by_id[target_cnt] = (cnt_file, "tech/container", updated_doc.frontmatter)
                                if cnt_file not in mutated_files:
                                    mutated_files.append(cnt_file)
                    else:
                        title = m.get("title") or target_id.replace("CMP-", "").replace("-", " ").title()
                        created = create_spec(
                            eidos_dir=eidos_dir,
                            doc_type_key="component",
                            raw_id=target_id,
                            title=title,
                        )
                        tx.created_files.add(created.resolve())
                        if created and created not in mutated_files:
                            mutated_files.append(created)
                        created_doc = parse_spec_file(created)
                        specs_by_id[created_doc.doc_id] = (created, "tech/component", created_doc.frontmatter)

            # 3. Add Invariant
            elif action == "add_invariant":
                inv_id = m.get("target_invariant_id") or target
                if not inv_id or not inv_id.startswith("INV-"):
                    return _abort(f"Cannot execute 'add_invariant': invalid invariant ID '{inv_id}'.")
                if inv_id not in invariant_map:
                    parent_id = (
                        m.get("target_capability")
                        or m.get("target_policy")
                        or m.get("target_component")
                        or m.get("target_container")
                    )
                    if not parent_id or parent_id not in specs_by_id:
                        return _abort(
                            f"Cannot execute 'add_invariant' for '{inv_id}': parent specification '{parent_id or 'unspecified'}' not found."
                        )
                    parent_file, parent_type, _ = specs_by_id[parent_id]
                    p_content = parent_file.read_text(encoding="utf-8")
                    p_fm, p_body, p_err = extract_frontmatter(p_content)
                    if p_err or not isinstance(p_fm, dict):
                        return _abort(f"Cannot execute 'add_invariant': failed to parse '{parent_file.name}'.")
                    new_inv = {
                        "id": inv_id,
                        "rule": (expect or {}).get("rule") or details or f"Invariant rule for {inv_id}.",
                        "test_binding": (expect or {}).get("test_binding")
                        or "tests/test_placeholder.py::test_placeholder",
                    }
                    if parent_type == "product/capability":
                        tech = p_fm.setdefault("technical", {})
                        inv_list = tech.setdefault("invariants", [])
                    else:
                        inv_list = p_fm.setdefault("invariants", [])
                    if not any(isinstance(i, dict) and i.get("id") == inv_id for i in inv_list):
                        inv_list.append(new_inv)
                        tx.record_before_write(parent_file)
                        parent_file.write_text(_render_spec(p_fm, p_body), encoding="utf-8")
                        invariant_map[inv_id] = (parent_file, new_inv)
                        specs_by_id[parent_id] = (parent_file, parent_type, p_fm)
                        if parent_file not in mutated_files:
                            mutated_files.append(parent_file)

            # 4. Type-Safe Removals
            elif action == "remove_component":
                target_id = m.get("target_component") or target
                if target_id in container_components and target_id not in specs_by_id:
                    cnt_id = container_components[target_id]
                    if m.get("target_container") and m.get("target_container") != cnt_id:
                        return _abort(
                            f"Cannot execute 'remove_component': '{target_id}' belongs to '{cnt_id}', not '{m.get('target_container')}'."
                        )
                    if cnt_id in specs_by_id:
                        cnt_file, cnt_type, _ = specs_by_id[cnt_id]
                        if cnt_type != "tech/container":
                            return _abort(
                                f"Type mismatch for 'remove_component': parent '{cnt_id}' has type '{cnt_type}', expected 'tech/container'."
                            )
                        if cnt_file.exists():
                            cnt_content = cnt_file.read_text(encoding="utf-8")
                            cnt_fm, _cnt_body, _ = extract_frontmatter(cnt_content)
                            if cnt_fm and "components" in cnt_fm:
                                updated_content = _mutate_frontmatter_components(cnt_content, "remove", target_id)
                                tx.record_before_write(cnt_file)
                                cnt_file.write_text(updated_content, encoding="utf-8")
                                del container_components[target_id]
                                updated_doc = parse_spec_file(cnt_file)
                                specs_by_id[cnt_id] = (cnt_file, cnt_type, updated_doc.frontmatter)
                                if cnt_file not in mutated_files:
                                    mutated_files.append(cnt_file)
                elif target_id in specs_by_id:
                    spec_file, actual_type, _ = specs_by_id[target_id]
                    if actual_type != "tech/component":
                        return _abort(
                            f"Type mismatch for 'remove_component': target '{target_id}' has type '{actual_type}', expected 'tech/component'."
                        )
                    if spec_file.exists():
                        tx.record_before_write(spec_file)
                        spec_file.unlink()
                        deleted_files.add(spec_file)
                        del specs_by_id[target_id]
                        if spec_file not in mutated_files:
                            mutated_files.append(spec_file)

            elif action in REMOVE_ACTION_EXPECTED_TYPES:
                expected_type = REMOVE_ACTION_EXPECTED_TYPES[action]
                target_id = target
                if target_id in container_components and target_id not in specs_by_id:
                    return _abort(
                        f"Type mismatch for '{action}': target '{target_id}' is an inline 'tech/component', expected '{expected_type}'."
                    )
                if target_id in specs_by_id:
                    spec_file, actual_type, _ = specs_by_id[target_id]
                    if actual_type != expected_type:
                        return _abort(
                            f"Type mismatch for '{action}': target '{target_id}' has type '{actual_type}', expected '{expected_type}'."
                        )
                    if spec_file.exists():
                        tx.record_before_write(spec_file)
                        spec_file.unlink()
                        deleted_files.add(spec_file)
                        del specs_by_id[target_id]
                        if spec_file not in mutated_files:
                            mutated_files.append(spec_file)

            elif action == "remove_invariant":
                inv_id = m.get("target_invariant_id") or target
                if inv_id in specs_by_id:
                    _, actual_type, _ = specs_by_id[inv_id]
                    return _abort(
                        f"Type mismatch for 'remove_invariant': target '{inv_id}' has type '{actual_type}', expected 'invariant'."
                    )
                if inv_id in invariant_map:
                    inv_file, _ = invariant_map[inv_id]
                    if inv_file.exists():
                        p_content = inv_file.read_text(encoding="utf-8")
                        p_fm, p_body, p_err = extract_frontmatter(p_content)
                        if p_err or not isinstance(p_fm, dict):
                            return _abort(f"Cannot execute 'remove_invariant': failed to parse '{inv_file.name}'.")
                        if isinstance(p_fm.get("invariants"), list):
                            p_fm["invariants"] = [
                                i for i in p_fm["invariants"] if not (isinstance(i, dict) and i.get("id") == inv_id)
                            ]
                        if isinstance((p_fm.get("technical") or {}).get("invariants"), list):
                            p_fm["technical"]["invariants"] = [
                                i
                                for i in p_fm["technical"]["invariants"]
                                if not (isinstance(i, dict) and i.get("id") == inv_id)
                            ]
                        tx.record_before_write(inv_file)
                        inv_file.write_text(_render_spec(p_fm, p_body), encoding="utf-8")
                        del invariant_map[inv_id]
                        if inv_file not in mutated_files:
                            mutated_files.append(inv_file)

            # 5. Deprecation Actions
            elif action in DEPRECATE_ACTION_EXPECTED_TYPES:
                expected_type = DEPRECATE_ACTION_EXPECTED_TYPES[action]
                if target not in specs_by_id:
                    return _abort(f"Cannot execute '{action}': target '{target}' not found.")
                spec_file, actual_type, _ = specs_by_id[target]
                if actual_type != expected_type:
                    return _abort(
                        f"Type mismatch for '{action}': target '{target}' has type '{actual_type}', expected '{expected_type}'."
                    )
                content = spec_file.read_text(encoding="utf-8")
                updated_content = _set_frontmatter_scalar(content, "status", "deprecated")
                tx.record_before_write(spec_file)
                spec_file.write_text(updated_content, encoding="utf-8")
                updated_doc = parse_spec_file(spec_file)
                specs_by_id[target] = (spec_file, actual_type, updated_doc.frontmatter)
                if spec_file not in mutated_files:
                    mutated_files.append(spec_file)

            # 6. Patch Invariant
            elif action == "patch_invariant":
                inv_id = m.get("target_invariant_id") or target
                if inv_id not in invariant_map or not _check_structural_subtarget_precondition(
                    m, specs_by_id, container_components
                ):
                    return _abort(f"Cannot execute 'patch_invariant': invariant '{inv_id}' not found.")
                inv_file, inv_dict = invariant_map[inv_id]
                decl_ok = _evaluate_declarative_postcondition(
                    m, inv_id, specs_by_id, container_components, invariant_map
                )
                if decl_ok is not True:
                    if not expect and not details:
                        return _abort(
                            f"Cannot execute 'patch_invariant' on '{inv_id}': no 'expect' or 'details' provided."
                        )
                    p_content = inv_file.read_text(encoding="utf-8")
                    p_fm, p_body, p_err = extract_frontmatter(p_content)
                    if p_err or not isinstance(p_fm, dict):
                        return _abort(f"Cannot execute 'patch_invariant': failed to parse '{inv_file.name}'.")
                    inv_lists = [
                        p_fm.get("invariants"),
                        (p_fm.get("technical") or {}).get("invariants"),
                    ]
                    for ilist in inv_lists:
                        if isinstance(ilist, list):
                            for item in ilist:
                                if isinstance(item, dict) and item.get("id") == inv_id:
                                    if expect:
                                        for ek, ev in expect.items():
                                            if ek == "contains":
                                                if str(ev) not in p_body:
                                                    p_body = f"{p_body.rstrip()}\n\n{ev}\n"
                                            else:
                                                item[ek] = ev
                                    elif details:
                                        item["rule"] = details
                                    inv_dict = item
                    tx.record_before_write(inv_file)
                    inv_file.write_text(_render_spec(p_fm, p_body), encoding="utf-8")
                    invariant_map[inv_id] = (inv_file, inv_dict)
                    if inv_file not in mutated_files:
                        mutated_files.append(inv_file)

            # 7. Modify / Extend / Patch Specification Actions
            elif action in MODIFY_ACTION_EXPECTED_TYPES:
                expected_type = MODIFY_ACTION_EXPECTED_TYPES[action]
                if not _check_structural_subtarget_precondition(m, specs_by_id, container_components):
                    return _abort(
                        f"Cannot execute '{action}': structural sub-target precondition failed for '{target}'."
                    )
                if action == "modify_component" and target in container_components and target not in specs_by_id:
                    cnt_id = container_components[target]
                    cnt_file, cnt_type, cnt_fm = specs_by_id[cnt_id]
                    comp_dict = _find_inline_component_dict(target, cnt_fm)
                    if comp_dict is None:
                        return _abort(f"Cannot execute 'modify_component': inline component '{target}' not found.")
                    decl_ok = _evaluate_declarative_postcondition(
                        m, target, specs_by_id, container_components, invariant_map, target_file=cnt_file
                    )
                    if decl_ok is not True:
                        if not expect and not details:
                            return _abort(
                                f"Cannot execute 'modify_component' on '{target}': no 'expect' or 'details' provided."
                            )
                        updated_comp = dict(comp_dict)
                        if expect:
                            for ek, ev in expect.items():
                                if ek != "contains":
                                    updated_comp[ek] = ev
                        elif details:
                            updated_comp["responsibility"] = details
                        cnt_content = cnt_file.read_text(encoding="utf-8")
                        updated_content = _mutate_frontmatter_components(cnt_content, "replace", target, updated_comp)
                        if expect and "contains" in expect and str(expect["contains"]) not in updated_content:
                            updated_content = f"{updated_content.rstrip()}\n\n{expect['contains']}\n"
                        tx.record_before_write(cnt_file)
                        cnt_file.write_text(updated_content, encoding="utf-8")
                        updated_doc = parse_spec_file(cnt_file)
                        specs_by_id[cnt_id] = (cnt_file, cnt_type, updated_doc.frontmatter)
                        if cnt_file not in mutated_files:
                            mutated_files.append(cnt_file)
                else:
                    target_file: Path | None = None
                    actual_type: str | None = None
                    if target in specs_by_id:
                        target_file, actual_type, _ = specs_by_id[target]
                    elif action == "modify_architecture":
                        for _, (p, d_type, _) in specs_by_id.items():
                            if d_type == "tech/architecture":
                                target_file = p
                                actual_type = d_type
                                break
                    if target_file is None or not target_file.exists():
                        return _abort(f"Cannot execute '{action}': target '{target}' not found.")
                    if actual_type and actual_type != expected_type:
                        return _abort(
                            f"Type mismatch for '{action}': target '{target}' has type '{actual_type}', expected '{expected_type}'."
                        )
                    decl_ok = _evaluate_declarative_postcondition(
                        m, target, specs_by_id, container_components, invariant_map, target_file=target_file
                    )
                    if decl_ok is not True:
                        if not _apply_expect_or_details_to_spec(target_file, expect, details, tx=tx):
                            return _abort(
                                f"Cannot execute '{action}' on '{target}': no 'expect' or 'details' provided."
                            )
                        updated_doc = parse_spec_file(target_file)
                        if updated_doc.doc_id:
                            specs_by_id[updated_doc.doc_id] = (
                                target_file,
                                updated_doc.doc_type,
                                updated_doc.frontmatter,
                            )
                        if target_file not in mutated_files:
                            mutated_files.append(target_file)

        # Pre-commit validation: validate all created or modified baseline specifications
        validator = SpecValidator()
        for mf in mutated_files:
            if mf in deleted_files or not mf.exists():
                continue
            mut_doc = parse_spec_file(mf)
            errors = validator.validate_document(mut_doc)
            if errors:
                return _abort(
                    f"Transactional rollback: mutated specification '{mf.name}' failed schema validation: {'; '.join(errors)}"
                )

    except Exception as exc:
        return _abort(f"Transactional rollback due to error during mutation execution: {exc}")

    return (
        True,
        f"Successfully executed mutations for '{doc.doc_id}' ({len(mutated_files)} files created/modified).",
        mutated_files,
    )


def apply_delta(
    eidos_dir: Path,
    delta_id_or_path: str,
    execute: bool = False,
    verify: bool = False,
) -> tuple[bool, str, Path | None, list[Path]]:
    """Transition a SpecDelta to applied status in-place with pre-commit schema validation and transactional rollback.

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

    tx = _MutationTransaction(eidos_dir)
    tx.snapshot(target_files=[file_path])

    mutated_files: list[Path] = []
    if execute:
        exec_ok, exec_msg, mutated_files = execute_delta_mutations(eidos_dir, delta_id_or_path, _tx=tx)
        if not exec_ok:
            tx.rollback()
            return False, f"Failed executing delta mutations: {exec_msg}", file_path, []

    if verify:
        ver_ok, ver_msg, report = verify_delta_mutations(eidos_dir, delta_id_or_path)
        if not ver_ok:
            tx.rollback()
            unfulfilled = [f"{m.action}:{m.target}" for m in (report.mutations if report else []) if not m.verified]
            return (
                False,
                f"Verification failed before apply: {ver_msg} Unfulfilled: {', '.join(unfulfilled)}",
                file_path,
                [],
            )

    current_status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
    if current_status == "applied":
        sync_delta_views(eidos_dir)
        return True, f"SpecDelta '{doc.doc_id}' is already marked as applied.", file_path, mutated_files

    content = file_path.read_text(encoding="utf-8")
    fm, _body, err = extract_frontmatter(content)
    if err or not fm:
        tx.rollback()
        return False, f"Failed to extract frontmatter: {err}", None, []

    new_content = _set_frontmatter_scalar(content, "status", "applied")

    # Pre-commit validation: validate the updated SpecDelta in memory BEFORE writing status: applied to disk
    doc_applied = _parse_spec_content(file_path, new_content)
    validator = SpecValidator()
    errors = validator.validate_document(doc_applied)
    if errors:
        tx.rollback()
        return (
            False,
            f"Delta updated to applied but failed validation: {'; '.join(errors)}",
            file_path,
            [],
        )

    tx.record_before_write(file_path)
    file_path.write_text(new_content, encoding="utf-8")
    sync_delta_views(eidos_dir)

    return (
        True,
        f"Successfully applied SpecDelta '{doc.doc_id}' in-place -> {file_path.name}.",
        file_path,
        mutated_files,
    )
