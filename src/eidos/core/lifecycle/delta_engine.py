"""SpecDelta engine: listing, status summaries, baseline snapshots, verification,
and application of composable semantic mutations (CMP-DELTA-ENGINE)."""

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

from eidos.core.index.storage import get_cached_spec_document, open_cache_connection
from eidos.core.lifecycle.catalog import (
    _build_spec_catalog_with_invariants,
    _set_frontmatter_scalar,
    find_spec_by_id_or_path,
)
from eidos.core.spec.models import SpecDocument
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator

SUPPORTED_MUTATION_OPERATIONS: frozenset[str] = frozenset({"add", "modify", "remove", "deprecate"})

PREFIX_TO_ENTITY_META: dict[str, tuple[str, str]] = {
    "DOM-": ("domain", "product/domain"),
    "CAP-": ("capability", "product/capability"),
    "PER-": ("persona", "product/persona"),
    "POL-": ("policy", "product/policy"),
    "INV-": ("invariant", "invariant"),
    "ARCH-": ("architecture", "tech/architecture"),
    "CNT-": ("container", "tech/container"),
    "CMP-": ("component", "tech/component"),
    "CTR-": ("contract", "tech/contract"),
    "DS-": ("design_system", "design/system"),
    "UIC-": ("ui_component", "design/ui-component"),
    "SCR-": ("screen", "design/screen"),
    "CLT-": ("client", "design/client"),
    "FLOW-": ("flow", "design/flow"),
}


def resolve_entity_prefix_meta(entity_id_or_path: str) -> tuple[str, str, str] | None:
    """Resolve an entity ID (e.g. 'CMP-DELTA-ENGINE') by prefix to (prefix, entity_kind, spec_doc_type)."""
    cleaned = str(entity_id_or_path or "").strip().upper()
    if "-" not in cleaned:
        return None
    prefix = f"{cleaned.split('-', 1)[0]}-"
    meta = PREFIX_TO_ENTITY_META.get(prefix)
    if meta is None:
        return None
    return prefix, meta[0], meta[1]


@dataclass
class MutationVerificationItem:
    """Status of an individual mutation verified against the living specification catalog."""

    action: str
    target: str
    verified: bool
    status: str  # "FOUND", "MISSING", "ABSENT_AS_EXPECTED", "STILL_PRESENT", "DEPRECATED", "ACTIVE_NOT_DEPRECATED"
    details: str = ""
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "verified": self.verified,
            "status": self.status,
            "details": self.details,
            "failure_reason": self.failure_reason,
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
    """Ensure eidos/.gitignore is present and ignores local SQLite cache so host projects remain unpolluted."""
    gitignore_path = eidos_dir / ".gitignore"
    default_content = (
        "# Eidos local index cache\n"
        ".cache/\n\n"
        "# OS artifacts\n"
        ".DS_Store\n"
        "Thumbs.db\n"
    )
    if not gitignore_path.exists():
        with contextlib.suppress(OSError):
            gitignore_path.write_text(default_content, encoding="utf-8")
    else:
        with contextlib.suppress(OSError):
            content = gitignore_path.read_text(encoding="utf-8")
            if ".cache" not in content:
                gitignore_path.write_text(
                    f"{content.rstrip()}\n\n# Eidos local index cache\n.cache/\n",
                    encoding="utf-8",
                )


ACTIVE_DELTA_STATUSES: tuple[str, ...] = (
    "draft",
    "proposed",
    "planned",
    "in_review",
    "approved",
    "in_progress",
)


def get_delta_status_summary(eidos_dir: Path) -> dict[str, Any]:
    """Get count and listing breakdown of deltas grouped by lifecycle status."""
    deltas = list_deltas(eidos_dir)
    by_status: dict[str, list[dict]] = {
        "draft": [],
        "proposed": [],
        "planned": [],
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

    active_count = sum(len(by_status.get(s, [])) for s in ACTIVE_DELTA_STATUSES)

    return {
        "total": len(deltas),
        "active_count": active_count,
        "by_status": by_status,
    }


def list_deltas(eidos_dir: Path, status_filter: str | None = None) -> list[dict[str, Any]]:
    """List all SpecDeltas from eidos/deltas/ with optional status filtering."""
    deltas_dir = eidos_dir / "deltas"
    results = []
    if not deltas_dir.exists():
        return results

    status_filter_clean = status_filter.lower().strip() if status_filter else None

    for p in sorted(deltas_dir.rglob("*.adoc")):
        doc, _, _ = get_cached_spec_document(p)
        if doc is None or doc.doc_type != "delta/mutation":
            continue

        status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
        is_active = status in ACTIVE_DELTA_STATUSES

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

    return results


def _extract_mutation_target(m: dict[str, Any]) -> str:
    """Extract primary target identifier or path from a composable mutation dictionary."""
    direct_target = m.get("target")
    if direct_target and isinstance(direct_target, str) and direct_target.strip():
        return direct_target.strip()

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


def normalize_delta_mutation(m: dict[str, Any]) -> dict[str, Any]:
    """Normalize a composable SpecDelta mutation dictionary (operation + target + parent)."""
    if not isinstance(m, dict):
        return {}
    norm: dict[str, Any] = dict(m)
    raw_op = str(norm.get("operation") or "").strip().lower()
    raw_target = str(norm.get("target") or "").strip()
    raw_parent = str(norm.get("parent") or "").strip()

    if raw_op:
        norm["operation"] = raw_op
    if not raw_target:
        extracted = _extract_mutation_target(norm)
        if extracted and extracted != "unspecified_target":
            norm["target"] = extracted
            raw_target = extracted
    else:
        norm["target"] = raw_target
    if raw_parent:
        norm["parent"] = raw_parent

    if raw_target:
        t_meta = resolve_entity_prefix_meta(raw_target)
        if t_meta is not None:
            norm["entity_type"] = t_meta[1]

    return norm


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


def _snapshot_delta_baselines(eidos_dir: Path, delta_id: str, overwrite: bool = True) -> None:
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

        insert_sql = (
            "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)"
            if overwrite
            else "INSERT OR IGNORE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)"
        )
        with contextlib.closing(open_cache_connection(db_path)) as conn, conn:
            conn.executemany(insert_sql, rows)
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
            _record_candidate(p.stem)

    repo_root = _resolve_git_repo_root(eidos_dir)
    if repo_root is not None:
        with contextlib.suppress(ValueError, OSError, subprocess.SubprocessError):
            rel_deltas = str(deltas_dir.resolve().relative_to(repo_root)).replace("\\", "/")
            seen_commits: set[str] = set()
            refs_to_scan: list[str] = ["HEAD"]
            ref_res = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "for-each-ref",
                    "--format=%(refname:short) %(objectname)",
                    "refs/heads",
                    "refs/remotes",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if ref_res.returncode == 0:
                for ref_ln in ref_res.stdout.splitlines():
                    parts = ref_ln.strip().split()
                    if not parts:
                        continue
                    r_name = parts[0]
                    commit_sha = parts[1] if len(parts) > 1 else r_name
                    if r_name.endswith("/HEAD"):
                        continue
                    if commit_sha not in seen_commits:
                        seen_commits.add(commit_sha)
                        refs_to_scan.append(r_name)
            else:
                for fallback_ref in ("main", "origin/main", "master", "origin/master"):
                    if fallback_ref not in refs_to_scan:
                        refs_to_scan.append(fallback_ref)

            for ref in refs_to_scan[:32]:
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
            if len(refs_to_scan) > 32:
                log_res = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo_root),
                        "log",
                        "--all",
                        "--diff-filter=A",
                        "--name-only",
                        "--pretty=format:",
                        "--",
                        rel_deltas,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if log_res.returncode == 0:
                    for ln in log_res.stdout.splitlines():
                        name = Path(ln.strip()).stem
                        if name:
                            _record_candidate(name)

    cleaned_raw = (raw_id or "").strip()
    is_auto = cleaned_raw.lower() in ("", "auto", "auto-slug")
    if cleaned_raw.lower() == "auto-slug":
        slug_suffix = True

    year = datetime.datetime.now(datetime.UTC).year
    if is_auto:
        next_seq = max_seq_by_year.get(year, 0) + 1
        base_id = f"DLT-{year}-{next_seq:03d}"
    else:
        base_id = normalize_id("delta", cleaned_raw)

    if slug_suffix or base_id in existing_ids:
        slug_tokens = [tok for tok in re.split(r"[^A-Za-z0-9]+", title.upper()) if len(tok) >= 2][:2]
        suffix = "-".join(slug_tokens) if slug_tokens else hashlib.sha256(
            f"{base_id}:{title}:{os.getpid()}".encode()
        ).hexdigest()[:6].upper()
        if base_id.endswith(f"-{suffix}") and base_id not in existing_ids:
            candidate_id = base_id
        else:
            candidate_id = f"{base_id}-{suffix}"
        if candidate_id in existing_ids:
            short_hash = hashlib.sha256(f"{candidate_id}:{title}".encode()).hexdigest()[:4].upper()
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
    branch: str | None = None,
) -> Path:
    """Create a SpecDelta with automatic collision-free ID resolution and vcs.base_commit baseline capture."""
    from eidos.core.lifecycle.scaffolder import create_spec

    return create_spec(
        eidos_dir=eidos_dir,
        doc_type_key="delta",
        raw_id=raw_id,
        title=title,
        kind=kind,
        motivation=motivation,
        branch=branch,
        slug_suffix=slug_suffix,
    )


VALID_DELTA_STATUSES: tuple[str, ...] = (
    "draft",
    "proposed",
    "planned",
    "in_review",
    "approved",
    "applied",
    "rejected",
)


def set_delta_status(
    eidos_dir: Path,
    delta_id_or_path: str,
    new_status: str,
) -> tuple[bool, str, Path | None, str | None]:
    """Transition a SpecDelta's frontmatter status."""
    norm_status = new_status.strip().lower()
    if norm_status not in VALID_DELTA_STATUSES:
        allowed = ", ".join(VALID_DELTA_STATUSES)
        return False, f"Invalid SpecDelta status '{new_status}'. Allowed values: {allowed}.", None, None

    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None, None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None, None

    old_status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
    if norm_status == "applied" and old_status != "applied":
        ver_ok, ver_msg, report = verify_delta_mutations(eidos_dir, delta_id_or_path)
        if not ver_ok:
            unfulfilled = [f"{m.action}:{m.target}" for m in (report.mutations if report else []) if not m.verified]
            return (
                False,
                f"Cannot transition SpecDelta '{doc.doc_id}' to 'applied' with unverified mutations: {ver_msg} Unfulfilled: {', '.join(unfulfilled)}",
                file_path,
                old_status,
            )

    content = file_path.read_text(encoding="utf-8")
    new_content = _set_frontmatter_scalar(content, "status", norm_status)

    doc_updated = _parse_spec_content(file_path, new_content)
    validator = SpecValidator()
    errors = validator.validate_document(doc_updated)
    if errors:
        return (
            False,
            f"Delta status update failed schema validation: {'; '.join(errors)}",
            file_path,
            old_status,
        )

    file_path.write_text(new_content, encoding="utf-8")
    return (
        True,
        f"Updated SpecDelta '{doc.doc_id}' status from '{old_status}' to '{norm_status}'.",
        file_path,
        old_status,
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
    *,
    allow_applied_fallback: bool = False,
) -> bool:
    """Compare current_sha against SQLite delta_baselines (with Git history fallback on cache miss)."""
    try:
        db_path = eidos_dir / ".cache" / "index.db"
        with contextlib.closing(open_cache_connection(db_path)) as conn:
            row = conn.execute(
                "SELECT baseline_sha256 FROM delta_baselines WHERE delta_id = ? AND target_id = ?",
                (delta_id, target_key),
            ).fetchone()
            if row is not None:
                return current_sha != row[0]

            git_sha = _resolve_git_baseline_sha(eidos_dir, delta_file, target_key, target_file)
            if git_sha is not None:
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                        (delta_id, target_key, git_sha),
                    )
                return current_sha != git_sha

            if allow_applied_fallback:
                return True

            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO delta_baselines (delta_id, target_id, baseline_sha256) VALUES (?, ?, ?)",
                    (delta_id, target_key, current_sha),
                )
            return False
    except (OSError, sqlite3.Error):
        return allow_applied_fallback


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


def _check_structural_subtarget_precondition(
    m: dict[str, Any],
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]],
    container_components: dict[str, str],
) -> bool:
    """Verify structural parent/target membership when both parent and target are specified.

    Returns False if a declared parent/target relationship is missing, or True if satisfied (or not applicable).
    """
    target = str(m.get("target") or "").strip()
    parent = str(m.get("parent") or "").strip()
    if not parent or not target or parent == target:
        return True

    if target.startswith("INV-"):
        owner_spec_id = container_components.get(parent, parent)
        if owner_spec_id not in specs_by_id:
            return False
        _, _, owner_fm = specs_by_id[owner_spec_id]
        invs = list(
            (owner_fm or {}).get("invariants", [])
            or ((owner_fm or {}).get("technical", {}) or {}).get("invariants", [])
            or []
        )
        for comp in (owner_fm or {}).get("components", []) or []:
            if isinstance(comp, dict) and comp.get("invariants") and (
                parent == owner_spec_id or comp.get("id") == parent
            ):
                invs.extend(comp.get("invariants") or [])
        inv_ids = {i.get("id") if isinstance(i, dict) else str(i) for i in invs}
        return target in inv_ids

    if target.startswith("CMP-") and parent.startswith("CNT-"):
        if parent not in specs_by_id:
            return False
        return bool(
            container_components.get(target) == parent
            or (target in specs_by_id and (specs_by_id[target][2] or {}).get("container") == parent)
        )

    if target.startswith("CTR-") and parent.startswith(("CNT-", "CMP-")):
        if parent not in specs_by_id:
            return False
        _, _, cnt_fm = specs_by_id[parent]
        contracts = (cnt_fm or {}).get("contracts", {}) or {}
        all_ctrs = (
            (contracts.get("provides") or []) + (contracts.get("consumes") or []) + (contracts.get("requires") or [])
        )
        if target in all_ctrs:
            return True
        if target in specs_by_id:
            ctr_fm = specs_by_id[target][2] or {}
            return (ctr_fm.get("owning_container") or ctr_fm.get("owning_component")) == parent
        return False

    return True


def _resolve_target_path_and_id(
    target: str,
    eidos_dir: Path,
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]],
) -> tuple[str, Path | None, bool]:
    """Resolve a mutation target (entity ID or relative/eidos-prefixed .adoc path) to (catalog_id, resolved_path, exists)."""
    if target in specs_by_id:
        p = specs_by_id[target][0]
        return target, p.resolve() if p.exists() else p, p.exists()
    if "/" in target or target.endswith(".adoc"):
        clean_p = target.strip("'\"").replace("\\", "/")
        clean_rel = clean_p.removeprefix("eidos/")
        for cand in (eidos_dir / clean_p, eidos_dir / clean_rel, Path(clean_p)):
            if cand.exists() and cand.is_file():
                resolved_cand = cand.resolve()
                for sid, (spath, _, _) in specs_by_id.items():
                    if spath.resolve() == resolved_cand:
                        return sid, resolved_cand, True
                return target, resolved_cand, True
    return target, None, False


_PROSE_EXPECT_FIELDS: frozenset[str] = frozenset(
    {
        "responsibility",
        "rule",
        "test_binding",
        "description",
        "motivation",
        "summary",
        "title",
        "name",
        "details",
        "jtbd",
    }
)


def _strip_spec_comments(raw_text: str) -> str:
    """Strip YAML frontmatter '#' comment lines and AsciiDoc '//' comment lines/blocks."""
    lines = raw_text.splitlines()
    out: list[str] = []
    in_fm = False
    fm_delims = 0
    in_block_comment = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "---" and fm_delims < 2 and (idx == 0 or in_fm):
            fm_delims += 1
            in_fm = fm_delims == 1
            out.append(line)
            continue
        if in_fm:
            if stripped.startswith("#"):
                continue
            out.append(line)
            continue
        if stripped.startswith("////"):
            in_block_comment = not in_block_comment
            continue
        if in_block_comment or stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _lookup_existing_baseline_sha(
    eidos_dir: Path,
    delta_id: str,
    delta_file: Path,
    target_key: str,
    target_file: Path | None,
) -> str | None:
    """Return recorded pre-delta baseline SHA from Git history (vcs.base_commit) or SQLite if available, without recording current_sha."""
    git_sha = _resolve_git_baseline_sha(eidos_dir, delta_file, target_key, target_file)
    if git_sha is not None:
        return git_sha
    try:
        db_path = eidos_dir / ".cache" / "index.db"
        if db_path.exists():
            with contextlib.closing(open_cache_connection(db_path)) as conn:
                row = conn.execute(
                    "SELECT baseline_sha256 FROM delta_baselines WHERE delta_id = ? AND target_id = ?",
                    (delta_id, target_key),
                ).fetchone()
                if row is not None:
                    return str(row[0])
    except (OSError, sqlite3.Error):
        pass
    return None


def _normalize_expect_spec(
    expect: dict[str, Any] | None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Normalize structured (body/frontmatter) expect assertions into (contains, not_contains, frontmatter)."""
    contains_items: list[str] = []
    not_contains_items: list[str] = []
    fm_expectations: dict[str, Any] = {}
    if not isinstance(expect, dict) or not expect:
        return contains_items, not_contains_items, fm_expectations

    def _extend_str_list(dest: list[str], raw_val: Any) -> None:
        if isinstance(raw_val, list):
            for item in raw_val:
                if item is not None:
                    dest.append(str(item))
        elif raw_val is not None:
            dest.append(str(raw_val))

    body_block = expect.get("body")
    if isinstance(body_block, dict):
        if "contains" in body_block:
            _extend_str_list(contains_items, body_block["contains"])
        if "not_contains" in body_block:
            _extend_str_list(not_contains_items, body_block["not_contains"])

    fm_block = expect.get("frontmatter")
    if isinstance(fm_block, dict):
        for fk, fv in fm_block.items():
            if fk == "custom" and isinstance(fv, dict):
                fm_expectations.update(fv)
            else:
                fm_expectations[fk] = fv

    return contains_items, not_contains_items, fm_expectations


def _evaluate_declarative_postcondition(
    m: dict[str, Any],
    target: str,
    specs_by_id: dict[str, tuple[Path, str, dict[str, Any]]],
    container_components: dict[str, str],
    invariant_map: dict[str, tuple[Path, dict[str, Any]]],
    target_file: Path | None = None,
    diagnostics: list[str] | None = None,
) -> bool | None:
    """Evaluate declarative expect block if present on a mutation.

    Returns True if satisfied, False if unsatisfied, or None if no declarative post-condition is specified.
    """
    expect = m.get("expect")
    if isinstance(expect, dict) and expect:
        unsupported_top = [k for k in expect if k not in ("body", "frontmatter")]
        if unsupported_top:
            if diagnostics is not None:
                diagnostics.append(
                    f"expect contains unsupported top-level keys {unsupported_top} (expected 'body' and/or 'frontmatter')"
                )
            return False

        body_block = expect.get("body")
        if body_block is not None:
            if not isinstance(body_block, dict):
                if diagnostics is not None:
                    diagnostics.append("expect.body must be a mapping")
                return False
            unsupported_body = [k for k in body_block if k not in ("contains", "not_contains")]
            if unsupported_body:
                if diagnostics is not None:
                    diagnostics.append(
                        f"expect.body contains unsupported keys {unsupported_body} (expected 'contains' and/or 'not_contains')"
                    )
                return False

        fm_block = expect.get("frontmatter")
        if fm_block is not None and not isinstance(fm_block, dict):
            if diagnostics is not None:
                diagnostics.append("expect.frontmatter must be a mapping")
            return False

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

        parsed_body = ""
        if raw_text:
            _, extracted_body, _ = extract_frontmatter(raw_text)
            parsed_body = extracted_body if extracted_body else raw_text
        uncommented_body = _strip_spec_comments(parsed_body) if parsed_body else ""
        contains_items, not_contains_items, fm_expectations = _normalize_expect_spec(expect)

        for needle in contains_items:
            if needle not in uncommented_body:
                if diagnostics is not None:
                    diagnostics.append(f"body.contains missing '{needle}'")
                return False

        for forbidden in not_contains_items:
            if forbidden and forbidden in uncommented_body:
                if diagnostics is not None:
                    diagnostics.append(f"body.not_contains matched forbidden '{forbidden}'")
                return False

        for k, exp_v in fm_expectations.items():
            actual = target_dict.get(k)
            if isinstance(exp_v, str) and isinstance(actual, str):
                if k in _PROSE_EXPECT_FIELDS:
                    if exp_v not in actual and actual != exp_v:
                        if diagnostics is not None:
                            diagnostics.append(f"frontmatter.{k} expected '{exp_v}', got '{actual}'")
                        return False
                elif actual.strip() != exp_v.strip():
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} expected '{exp_v}', got '{actual}'")
                    return False
            elif isinstance(actual, list) and isinstance(exp_v, list):
                missing_items = [item for item in exp_v if item not in actual]
                if missing_items:
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} missing expected list items {missing_items} in {actual}")
                    return False
            elif isinstance(actual, list) and not isinstance(exp_v, list):
                if exp_v not in actual:
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} missing '{exp_v}' in {actual}")
                    return False
            elif actual != exp_v:
                if diagnostics is not None:
                    diagnostics.append(f"frontmatter.{k} expected {exp_v!r}, got {actual!r}")
                return False
        return True

    return None


def verify_delta_mutations(eidos_dir: Path, delta_id_or_path: str) -> tuple[bool, str, DeltaVerificationReport | None]:
    """Verify that all composable semantic mutations declared in a SpecDelta are fulfilled in living specifications."""
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
    for raw_m in raw_mutations:
        m = normalize_delta_mutation(raw_m) if isinstance(raw_m, dict) else {}
        op = str(m.get("operation", "")).strip().lower()
        target = _extract_mutation_target(m)
        details = str(m.get("details", "")).strip()

        verified = False
        status = "UNKNOWN"
        failure_reason: str | None = None
        diag: list[str] = []

        lookup_target, target_path_resolved, target_path_exists = _resolve_target_path_and_id(
            target, eidos_dir, specs_by_id
        )
        t_meta = resolve_entity_prefix_meta(lookup_target) or resolve_entity_prefix_meta(target)
        entity_kind = t_meta[1] if t_meta else None

        if op not in SUPPORTED_MUTATION_OPERATIONS:
            verified = False
            status = "UNSUPPORTED_ACTION"
            failure_reason = f"Unsupported mutation operation '{op}'."

        elif op == "deprecate" and entity_kind not in ("capability", "policy"):
            verified = False
            status = "UNSUPPORTED_ACTION"
            failure_reason = f"Operation 'deprecate' is only supported for capability and policy targets (got '{target}')."

        elif entity_kind == "architecture" and op != "modify":
            verified = False
            status = "UNSUPPORTED_ACTION"
            failure_reason = f"Architecture target '{target}' only supports operation 'modify' (got '{op}')."

        # 1. Removal / Deprecation Operations
        elif op == "remove" and entity_kind == "invariant":
            if lookup_target not in invariants:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"
                failure_reason = f"Invariant '{lookup_target}' is still present."

        elif op == "deprecate" or (op == "remove" and entity_kind in ("capability", "policy")):
            if lookup_target not in specs_by_id and not target_path_exists:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                doc_fm = specs_by_id[lookup_target][2] if lookup_target in specs_by_id else {}
                if not doc_fm and target_path_resolved and target_path_resolved.exists():
                    doc_fm = parse_spec_file(target_path_resolved).frontmatter or {}
                if str(doc_fm.get("status", "")).lower() in ("deprecated", "sunset", "removed", "retired"):
                    verified = True
                    status = "DEPRECATED"
                else:
                    verified = False
                    status = "ACTIVE_NOT_DEPRECATED"
                    failure_reason = (
                        f"Target '{lookup_target}' has status '{doc_fm.get('status')}', expected deprecated/sunset/retired."
                    )

        elif op == "remove":
            still_present = lookup_target in specs_by_id or lookup_target in container_components or target_path_exists
            if not still_present:
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"
                failure_reason = f"Target '{lookup_target}' is still present in living specifications."

        # 2. Addition Operations
        elif op == "add" and entity_kind == "invariant":
            if lookup_target in invariants and _check_structural_subtarget_precondition(
                m, specs_by_id, container_components
            ):
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, container_components, invariant_map, diagnostics=diag
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                    failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                else:
                    verified = True
                    status = "FOUND"
            else:
                verified = False
                status = "MISSING"
                failure_reason = f"Invariant '{lookup_target}' not found on parent specification."

        elif op == "add":
            found = (
                lookup_target in specs_by_id or lookup_target in container_components or target_path_exists
            ) and _check_structural_subtarget_precondition(m, specs_by_id, container_components)
            if found:
                decl_ok = _evaluate_declarative_postcondition(
                    m,
                    lookup_target,
                    specs_by_id,
                    container_components,
                    invariant_map,
                    target_file=target_path_resolved,
                    diagnostics=diag,
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                    failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                else:
                    verified = True
                    status = "FOUND"
            else:
                verified = False
                status = "MISSING"
                failure_reason = f"Target '{lookup_target}' not found."

        # 3. Modification Operations
        elif op == "modify" and entity_kind == "invariant":
            if lookup_target not in invariant_map or not _check_structural_subtarget_precondition(
                m, specs_by_id, container_components
            ):
                verified = False
                status = "MISSING"
                failure_reason = f"Invariant '{lookup_target}' not found on parent specification."
            else:
                inv_file, inv_dict = invariant_map[lookup_target]
                inv_sha = _digest_dict(inv_dict)
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, container_components, invariant_map, diagnostics=diag
                )
                if decl_ok is not None:
                    if not decl_ok:
                        verified = False
                        status = "UNMODIFIED"
                        failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                    elif delta_status == "applied":
                        verified = True
                        status = "FOUND"
                    else:
                        base_sha = _lookup_existing_baseline_sha(
                            eidos_dir, doc.doc_id, file_path, lookup_target, inv_file
                        )
                        if base_sha is not None and inv_sha == base_sha:
                            verified = False
                            status = "UNMODIFIED"
                            failure_reason = (
                                f"Invariant '{lookup_target}' digest matches pre-delta baseline (no modification detected)."
                            )
                        else:
                            verified = True
                            status = "MODIFIED"
                else:
                    mod_ok = _check_or_record_baseline_sha(
                        eidos_dir,
                        doc.doc_id,
                        file_path,
                        lookup_target,
                        inv_file,
                        inv_sha,
                        allow_applied_fallback=(delta_status == "applied"),
                    )
                    verified = mod_ok
                    status = ("FOUND" if delta_status == "applied" else "MODIFIED") if mod_ok else "UNMODIFIED"
                    if not mod_ok:
                        failure_reason = (
                            f"Invariant '{lookup_target}' digest matches pre-delta baseline (no modification detected)."
                        )

        elif op == "modify":
            target_file: Path | None = None
            if lookup_target in specs_by_id:
                target_file = specs_by_id[lookup_target][0]
            elif lookup_target in container_components and container_components[lookup_target] in specs_by_id:
                target_file = specs_by_id[container_components[lookup_target]][0]
            elif target_path_resolved is not None:
                target_file = target_path_resolved
            elif entity_kind == "architecture":
                for arch_id, (p, d_type, _) in specs_by_id.items():
                    if d_type == "tech/architecture":
                        target_file = p
                        lookup_target = arch_id
                        break

            if (
                target_file is None
                or not target_file.exists()
                or not _check_structural_subtarget_precondition(m, specs_by_id, container_components)
            ):
                verified = False
                status = "MISSING"
                failure_reason = f"Target specification '{lookup_target}' not found."
            else:
                cur_sha: str | None = None
                if lookup_target not in specs_by_id and lookup_target in container_components:
                    cnt_id = container_components[lookup_target]
                    if cnt_id in specs_by_id:
                        comp_dict = _find_inline_component_dict(lookup_target, specs_by_id[cnt_id][2])
                        if comp_dict is not None:
                            cur_sha = _digest_dict(comp_dict)
                if cur_sha is None:
                    cur_sha = hashlib.sha256(target_file.read_bytes()).hexdigest()

                decl_ok = _evaluate_declarative_postcondition(
                    m,
                    lookup_target,
                    specs_by_id,
                    container_components,
                    invariant_map,
                    target_file=target_file,
                    diagnostics=diag,
                )
                if decl_ok is not None:
                    if not decl_ok:
                        verified = False
                        status = "UNMODIFIED"
                        failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                    elif delta_status == "applied":
                        verified = True
                        status = "FOUND"
                    else:
                        base_sha = _lookup_existing_baseline_sha(
                            eidos_dir, doc.doc_id, file_path, lookup_target, target_file
                        )
                        if base_sha is not None and cur_sha == base_sha:
                            verified = False
                            status = "UNMODIFIED"
                            failure_reason = (
                                f"Target '{lookup_target}' digest matches pre-delta baseline (no modification detected)."
                            )
                        else:
                            verified = True
                            status = "MODIFIED"
                else:
                    mod_ok = _check_or_record_baseline_sha(
                        eidos_dir,
                        doc.doc_id,
                        file_path,
                        lookup_target,
                        target_file,
                        cur_sha,
                        allow_applied_fallback=(delta_status == "applied"),
                    )
                    verified = mod_ok
                    status = ("FOUND" if delta_status == "applied" else "MODIFIED") if mod_ok else "UNMODIFIED"
                    if not mod_ok:
                        failure_reason = (
                            f"Target '{lookup_target}' digest matches pre-delta baseline (no modification detected)."
                        )

        items.append(
            MutationVerificationItem(
                action=op,
                target=target,
                verified=verified,
                status=status,
                details=details,
                failure_reason=failure_reason,
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


def apply_delta(
    eidos_dir: Path,
    delta_id_or_path: str,
    verify: bool = True,
) -> tuple[bool, str, Path | None]:
    """Transition a SpecDelta to applied status in-place with pre-commit schema validation.

    Args:
        eidos_dir: Path to the eidos specifications root.
        delta_id_or_path: ID, filename, or Path of the delta to apply.
        verify: If True (default), verify that all mutations are fulfilled in living specs before applying.
    """
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", delta_id_or_path)
    if not file_path:
        file_path = find_spec_by_id_or_path(eidos_dir, delta_id_or_path)

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None

    if verify:
        ver_ok, ver_msg, report = verify_delta_mutations(eidos_dir, delta_id_or_path)
        if not ver_ok:
            unfulfilled = [f"{m.action}:{m.target}" for m in (report.mutations if report else []) if not m.verified]
            return (
                False,
                f"Verification failed before apply: {ver_msg} Unfulfilled: {', '.join(unfulfilled)}",
                file_path,
            )

    current_status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
    if current_status == "applied":
        return True, f"SpecDelta '{doc.doc_id}' is already marked as applied.", file_path

    content = file_path.read_text(encoding="utf-8")
    fm, _body, err = extract_frontmatter(content)
    if err or not fm:
        return False, f"Failed to extract frontmatter: {err}", None

    new_content = _set_frontmatter_scalar(content, "status", "applied")

    # Pre-commit validation: validate the updated SpecDelta in memory BEFORE writing status: applied to disk
    doc_applied = _parse_spec_content(file_path, new_content)
    validator = SpecValidator()
    errors = validator.validate_document(doc_applied)
    if errors:
        return (
            False,
            f"Delta updated to applied but failed validation: {'; '.join(errors)}",
            file_path,
        )

    file_path.write_text(new_content, encoding="utf-8")

    return (
        True,
        f"Successfully applied SpecDelta '{doc.doc_id}' in-place -> {file_path.name}.",
        file_path,
    )
