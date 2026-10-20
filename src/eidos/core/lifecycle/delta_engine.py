"""SpecDelta engine: listing, status summaries, Git-native baseline verification,
and application of composable semantic mutations (CMP-DELTA-ENGINE)."""

import contextlib
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.storage import get_cached_spec_document
from eidos.core.lifecycle.catalog import (
    _build_spec_catalog_with_invariants,
    _render_spec,
    _set_frontmatter_scalar,
    _single_line,
    find_references_to_spec,
    find_spec_by_id_or_path,
)
from eidos.core.spec.parser import _parse_spec_content, extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import render_spec_template

SUPPORTED_MUTATION_OPERATIONS: frozenset[str] = frozenset({"add", "modify", "remove", "deprecate"})

PREFIX_TO_ENTITY_META: dict[str, tuple[str, str]] = {
    "SYS-": ("system", "product/system"),
    "DOM-": ("domain", "product/domain"),
    "CAP-": ("capability", "product/capability"),
    "PER-": ("persona", "product/persona"),
    "POL-": ("policy", "product/policy"),
    "EXP-": ("exploration", "product/exploration"),
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


ACTIVE_DELTA_STATUSES: tuple[str, ...] = (
    "proposed",
    "in_review",
    "approved",
)


def get_delta_status_summary(eidos_dir: Path, include_archived: bool = False) -> dict[str, Any]:
    """Get count and listing breakdown of deltas grouped by lifecycle status."""
    deltas = list_deltas(eidos_dir, include_archived=include_archived)
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

    active_count = sum(len(by_status.get(s, [])) for s in ACTIVE_DELTA_STATUSES)
    archive_dir = eidos_dir / "deltas" / "archive"
    archived_count = (
        sum(1 for d in deltas if d.get("is_archived"))
        if include_archived
        else (len(list(archive_dir.glob("*.adoc"))) if archive_dir.is_dir() else 0)
    )

    return {
        "total": len(deltas),
        "active_count": active_count,
        "archived_count": archived_count,
        "by_status": by_status,
    }


def list_deltas(
    eidos_dir: Path,
    status_filter: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """List SpecDeltas from eidos/deltas/ (excluding eidos/deltas/archive/ unless include_archived=True)."""
    deltas_dir = eidos_dir / "deltas"
    results = []
    if not deltas_dir.exists():
        return results

    status_filter_clean = status_filter.lower().strip() if status_filter else None
    candidate_paths = sorted(deltas_dir.rglob("*.adoc") if include_archived else deltas_dir.glob("*.adoc"))

    for p in candidate_paths:
        if not p.is_file() or p.is_symlink():
            continue
        doc, _, _ = get_cached_spec_document(p)
        if doc is None or doc.doc_type != "delta/mutation":
            continue

        status = str(doc.frontmatter.get("status", "proposed")).lower().strip()
        is_active = status in ACTIVE_DELTA_STATUSES
        rel_to_deltas = p.relative_to(deltas_dir) if p.is_relative_to(deltas_dir) else Path(p.name)
        is_archived = "archive" in rel_to_deltas.parts

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
                "is_archived": is_archived,
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
    if raw_target:
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
    comps = list(cnt_fm.get("components") or [])
    for c in comps:
        if isinstance(c, dict) and c.get("id") == comp_id and not c.get("ref"):
            return c
    return None


def _digest_dict(data: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hex digest for a frontmatter sub-dictionary."""
    return hashlib.sha256(
        orjson.dumps(data, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)
    ).hexdigest()


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


def _resolve_git_branch(eidos_dir: Path) -> str | None:
    """Resolve the current Git branch name for eidos_dir, or None if detached HEAD or not in a Git repository."""
    repo_root = _resolve_git_repo_root(eidos_dir)
    if repo_root is None:
        return None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            branch_name = res.stdout.strip()
            if branch_name and branch_name != "HEAD":
                return branch_name
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
            with contextlib.suppress(OSError):
                fm_cand, _, _ = extract_frontmatter(p.read_text(encoding="utf-8"))
                if isinstance(fm_cand, dict) and isinstance(fm_cand.get("id"), str):
                    _record_candidate(fm_cand["id"])

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
                    ["git", "-C", str(repo_root), "ls-tree", "-r", "--name-only", ref, f"{rel_deltas}/"],
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
        suffix = (
            "-".join(slug_tokens)
            if slug_tokens
            else hashlib.sha256(f"{base_id}:{title}:{os.getpid()}".encode()).hexdigest()[:6].upper()
        )
        if base_id.endswith(f"-{suffix}") and base_id not in existing_ids:
            candidate_id = base_id
        else:
            candidate_id = f"{base_id}-{suffix}"
        base_candidate = candidate_id
        attempt = 0
        while candidate_id in existing_ids:
            hash_input = f"{base_candidate}:{title}" if attempt == 0 else f"{base_candidate}:{title}:{attempt}"
            short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:4].upper()
            candidate_id = f"{base_candidate}-{short_hash}"
            attempt += 1
        return candidate_id

    return base_id


def create_delta(
    eidos_dir: Path,
    raw_id: str = "auto",
    title: str = "Untitled SpecDelta",
    kind: str = "feature",
    slug_suffix: bool = False,
) -> Path:
    """Create a SpecDelta blueprint with automatic collision-free ID resolution and Git branch/base_commit capture."""
    doc_id = generate_collision_free_delta_id(
        eidos_dir,
        raw_id=raw_id,
        title=title,
        slug_suffix=slug_suffix,
    )
    slug = doc_id.lower().replace("_", "-")
    heading = _single_line(title)
    dest_dir = eidos_dir / "deltas"
    dest_file = dest_dir / f"{slug}.adoc"
    motivation = f"Proposed {kind} mutation for {heading}."

    fm: dict[str, Any] = {
        "type": "delta/mutation",
        "id": doc_id,
        "title": title,
        "status": "proposed",
        "intent": {"kind": kind, "motivation": motivation},
        "mutations": [],
    }
    branch_name = _resolve_git_branch(eidos_dir)
    head_sha = _resolve_git_head_sha(eidos_dir)
    vcs_dict: dict[str, Any] = {}
    if branch_name:
        vcs_dict["branch"] = branch_name
    if head_sha:
        vcs_dict["base_commit"] = head_sha
    if vcs_dict:
        fm["vcs"] = vcs_dict

    body = render_spec_template("delta/mutation", heading=heading, motivation=motivation)
    content = _render_spec(fm, body)
    archived_file = dest_dir / "archive" / f"{slug}.adoc"
    if dest_file.exists() or archived_file.exists():
        existing_target = archived_file if archived_file.exists() else dest_file
        raise FileExistsError(f"SpecDelta document already exists at: {existing_target}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file.write_text(content, encoding="utf-8")

    doc = parse_spec_file(dest_file)
    validator = SpecValidator()
    errors = validator.validate_document(doc)
    if errors:
        dest_file.unlink()
        err_msg = "; ".join(errors)
        raise ValueError(f"Generated SpecDelta failed validation: {err_msg}")

    return dest_file


def delete_delta(eidos_dir: Path, target: str, force: bool = False) -> tuple[bool, str, list[Path]]:
    """Safely delete a SpecDelta (DLT-*) document, rejecting non-delta living specifications."""
    file_path = find_spec_by_id_or_path(eidos_dir / "deltas", target) or find_spec_by_id_or_path(eidos_dir, target)
    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{target}' not found under '{eidos_dir}'.", []

    doc = parse_spec_file(file_path)
    doc_id = doc.doc_id or file_path.stem
    if doc.doc_type != "delta/mutation":
        return (
            False,
            f"Document '{doc_id}' is a living specification ({doc.doc_type}), not a SpecDelta. Use 'eidos spec delete {target}' instead.",
            [],
        )

    references = find_references_to_spec(eidos_dir, doc_id, file_path)
    if references and not force:
        refs_str = ", ".join(p.name for p in references[:5])
        return (
            False,
            f"Cannot delete '{doc_id}': referenced by {len(references)} specification(s) ({refs_str}). Use --force to delete anyway.",
            references,
        )

    file_path.unlink()
    parent = file_path.parent
    if parent != eidos_dir and parent.exists() and not any(parent.iterdir()):
        with contextlib.suppress(OSError):
            parent.rmdir()

    return True, f"Successfully deleted SpecDelta '{doc_id}' ({file_path.name}).", references


VALID_DELTA_STATUSES: tuple[str, ...] = (
    "proposed",
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
    *,
    rev_cache: dict[Path, list[str]] | None = None,
    blob_cache: dict[tuple[str, str], tuple[bytes | None, dict[str, Any] | None]] | None = None,
) -> str | None:
    """Recover the pre-delta baseline SHA-256 of target_key from vcs.base_commit or Git history."""
    if target_file is None or not target_file.exists():
        return None

    repo_root = _resolve_git_repo_root(eidos_dir)
    if repo_root is None:
        return None

    try:
        rel_target = str(target_file.resolve().relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return None

    def _compute_fallback_revs() -> list[str]:
        fb_revs: list[str] = []

        def _add_fb(cand: str | None) -> None:
            if cand and cand not in fb_revs:
                fb_revs.append(cand)

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
                    _add_fb(f"{first_commit}~1")
                _add_fb(first_commit)

        if not fb_revs:
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
                        _add_fb(mb_res.stdout.strip())
            _add_fb("HEAD")
        return fb_revs

    if rev_cache is not None and delta_file in rev_cache:
        revs = list(rev_cache[delta_file])
    else:
        revs = []
        if delta_file.exists():
            with contextlib.suppress(OSError):
                dlt_fm, _, _ = extract_frontmatter(delta_file.read_text(encoding="utf-8"))
                if isinstance(dlt_fm, dict):
                    vcs_block = dlt_fm.get("vcs")
                    if isinstance(vcs_block, dict):
                        base_commit = vcs_block.get("base_commit")
                        if isinstance(base_commit, str) and base_commit.strip():
                            revs.append(base_commit.strip())
        if not revs:
            revs = _compute_fallback_revs()
        if rev_cache is not None:
            rev_cache[delta_file] = list(revs)

    tried_fallback = len(revs) > 1 or (revs and revs[0] == "HEAD")

    def _digest_from_rev(rev: str) -> tuple[bool, str | None]:
        """Return (blob_found, digest_or_none) for (rev, rel_target)."""
        cache_key = (rev, rel_target)
        raw_bytes: bytes | None = None
        hist_fm: dict[str, Any] | None = None
        if blob_cache is not None and cache_key in blob_cache:
            raw_bytes, hist_fm = blob_cache[cache_key]
        else:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                show_res = subprocess.run(
                    ["git", "-C", str(repo_root), "show", f"{rev}:{rel_target}"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if show_res.returncode == 0 and show_res.stdout:
                    raw_bytes = show_res.stdout
            if blob_cache is not None:
                blob_cache[cache_key] = (raw_bytes, None)

        if raw_bytes is None:
            return False, None

        if target_key.startswith(("CMP-", "INV-")):
            if hist_fm is None:
                parsed_fm, _, _ = extract_frontmatter(raw_bytes.decode("utf-8", errors="replace"))
                hist_fm = parsed_fm if isinstance(parsed_fm, dict) else {}
                if blob_cache is not None:
                    blob_cache[cache_key] = (raw_bytes, hist_fm)
            if target_key.startswith("CMP-"):
                if hist_fm:
                    if hist_fm.get("type") == "tech/component" or str(hist_fm.get("id", "")).upper() == target_key:
                        return True, hashlib.sha256(raw_bytes).hexdigest()
                    comp_dict = _find_inline_component_dict(target_key, hist_fm)
                    if comp_dict is not None:
                        return True, _digest_dict(comp_dict)
                    if hist_fm.get("type") == "tech/container":
                        return True, None
                if target_file.stem.upper() != target_key and f"CMP-{target_file.stem.upper()}" != target_key:
                    return True, None
            if target_key.startswith("INV-"):
                if hist_fm:
                    invs = list(hist_fm.get("invariants", []) or [])
                    tech = hist_fm.get("technical")
                    if isinstance(tech, dict) and isinstance(tech.get("invariants"), list):
                        invs.extend(tech["invariants"])
                    for comp in hist_fm.get("components", []) or []:
                        if isinstance(comp, dict) and isinstance(comp.get("invariants"), list):
                            invs.extend(comp["invariants"])
                    for inv in invs:
                        if isinstance(inv, dict) and inv.get("id") == target_key:
                            return True, _digest_dict(inv)
                return True, None
        return True, hashlib.sha256(raw_bytes).hexdigest()

    for rev in revs:
        found, digest = _digest_from_rev(rev)
        if found:
            return digest

    if not tried_fallback:
        extra_revs = [r for r in _compute_fallback_revs() if r not in revs]
        if extra_revs:
            revs.extend(extra_revs)
            if rev_cache is not None:
                rev_cache[delta_file] = list(revs)
            for rev in extra_revs:
                found, digest = _digest_from_rev(rev)
                if found:
                    return digest

    return None


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
        owner_spec_id = parent if parent in specs_by_id else container_components.get(parent, parent)
        if owner_spec_id not in specs_by_id:
            return False
        _, _, owner_fm = specs_by_id[owner_spec_id]
        invs: list[Any] = []
        if parent == owner_spec_id:
            invs.extend((owner_fm or {}).get("invariants") or [])
            invs.extend(((owner_fm or {}).get("technical") or {}).get("invariants") or [])
        for comp in list((owner_fm or {}).get("components") or []):
            if (
                isinstance(comp, dict)
                and comp.get("invariants")
                and (parent == owner_spec_id or comp.get("id") == parent)
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
        if parent in specs_by_id:
            _, _, cnt_fm = specs_by_id[parent]
            contracts = (cnt_fm or {}).get("contracts", {}) or {}
            all_ctrs = (contracts.get("provides") or []) + (contracts.get("consumes") or [])
            if target in all_ctrs:
                return True
        elif parent in container_components and container_components[parent] in specs_by_id:
            inline_comp = _find_inline_component_dict(parent, specs_by_id[container_components[parent]][2])
            if inline_comp is not None:
                ifaces = inline_comp.get("contracts") or {}
                if isinstance(ifaces, dict):
                    all_ifaces = (ifaces.get("provides") or []) + (ifaces.get("consumes") or [])
                    if target in all_ifaces:
                        return True
        else:
            return False
        if target in specs_by_id:
            ctr_fm = specs_by_id[target][2] or {}
            return parent == ctr_fm.get("container")
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
        "details",
        "decision",
        "rationale",
    }
)


def _resolve_frontmatter_path(target_dict: dict[str, Any], key_path: str) -> tuple[bool, Any]:
    """Resolve an exact or dot-separated key path across nested dicts and lists of dicts."""
    if not isinstance(target_dict, dict):
        return False, None
    if key_path in target_dict:
        return True, target_dict[key_path]
    if "." not in key_path:
        return False, None
    segments = key_path.split(".")
    if len(segments) < 2 or any(not seg for seg in segments):
        return False, None
    current: Any = target_dict
    for seg in segments:
        if isinstance(current, dict):
            if seg not in current:
                return False, None
            current = current[seg]
        elif isinstance(current, list):
            projected: list[Any] = []
            found_any = False
            for elem in current:
                if isinstance(elem, dict) and seg in elem:
                    found_any = True
                    val = elem[seg]
                    if isinstance(val, list):
                        projected.extend(val)
                    else:
                        projected.append(val)
            if not found_any:
                return False, None
            current = projected
        else:
            return False, None
    return True, current


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
            fm_dict, extracted_body, fm_err = extract_frontmatter(raw_text)
            parsed_body = extracted_body if (fm_dict is not None and fm_err is None) else raw_text
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
            found_key, actual = _resolve_frontmatter_path(target_dict, k)
            leaf_key = k.rsplit(".", 1)[-1]
            is_prose = leaf_key in _PROSE_EXPECT_FIELDS
            if not found_key and (exp_v is not None or "." in k):
                if diagnostics is not None:
                    diagnostics.append(f"frontmatter.{k} expected {exp_v!r}, got None")
                return False
            if isinstance(exp_v, str) and isinstance(actual, str):
                if is_prose:
                    if exp_v not in actual and actual != exp_v:
                        if diagnostics is not None:
                            diagnostics.append(f"frontmatter.{k} expected '{exp_v}', got '{actual}'")
                        return False
                elif actual.strip() != exp_v.strip():
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} expected '{exp_v}', got '{actual}'")
                    return False
            elif isinstance(actual, list) and isinstance(exp_v, list):
                missing_items = [
                    item
                    for item in exp_v
                    if item not in actual
                    and not (
                        is_prose and isinstance(item, str) and any(isinstance(a, str) and item in a for a in actual)
                    )
                ]
                if missing_items:
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} missing expected list items {missing_items} in {actual}")
                    return False
            elif isinstance(actual, list) and not isinstance(exp_v, list):
                matched_in_list = exp_v in actual or (
                    is_prose and isinstance(exp_v, str) and any(isinstance(a, str) and exp_v in a for a in actual)
                )
                if not matched_in_list:
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
    rev_cache: dict[Path, list[str]] = {}
    blob_cache: dict[tuple[str, str], tuple[bytes | None, dict[str, Any] | None]] = {}

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

        elif op == "deprecate" and entity_kind == "invariant":
            verified = False
            status = "UNSUPPORTED_ACTION"
            failure_reason = f"Operation 'deprecate' is not supported for invariant targets (got '{target}')."

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
            if (
                lookup_target not in specs_by_id
                and lookup_target not in container_components
                and not target_path_exists
            ):
                verified = True
                status = "ABSENT_AS_EXPECTED"
            else:
                doc_fm = specs_by_id[lookup_target][2] if lookup_target in specs_by_id else {}
                if (
                    not doc_fm
                    and lookup_target in container_components
                    and container_components[lookup_target] in specs_by_id
                ):
                    comp_dict = _find_inline_component_dict(
                        lookup_target, specs_by_id[container_components[lookup_target]][2]
                    )
                    if comp_dict is not None:
                        doc_fm = comp_dict
                if not doc_fm and target_path_resolved and target_path_resolved.exists():
                    doc_fm = parse_spec_file(target_path_resolved).frontmatter or {}
                if str(doc_fm.get("status", "")).lower() in ("deprecated", "retired"):
                    verified = True
                    status = "DEPRECATED"
                else:
                    verified = False
                    status = "ACTIVE_NOT_DEPRECATED"
                    failure_reason = (
                        f"Target '{lookup_target}' has status '{doc_fm.get('status')}', expected deprecated/retired."
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
                        base_sha = _resolve_git_baseline_sha(
                            eidos_dir,
                            file_path,
                            lookup_target,
                            inv_file,
                            rev_cache=rev_cache,
                            blob_cache=blob_cache,
                        )
                        if base_sha is not None and inv_sha == base_sha:
                            verified = False
                            status = "UNMODIFIED"
                            failure_reason = f"Invariant '{lookup_target}' digest matches pre-delta baseline (no modification detected)."
                        else:
                            verified = True
                            status = "MODIFIED"
                else:
                    base_sha = _resolve_git_baseline_sha(
                        eidos_dir,
                        file_path,
                        lookup_target,
                        inv_file,
                        rev_cache=rev_cache,
                        blob_cache=blob_cache,
                    )
                    mod_ok = (inv_sha != base_sha) if base_sha is not None else (delta_status == "applied")
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
            elif entity_kind == "system":
                for sys_id, (p, d_type, _) in specs_by_id.items():
                    if d_type == "product/system":
                        target_file = p
                        lookup_target = sys_id
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
                        base_sha = _resolve_git_baseline_sha(
                            eidos_dir,
                            file_path,
                            lookup_target,
                            target_file,
                            rev_cache=rev_cache,
                            blob_cache=blob_cache,
                        )
                        if base_sha is not None and cur_sha == base_sha:
                            verified = False
                            status = "UNMODIFIED"
                            failure_reason = f"Target '{lookup_target}' digest matches pre-delta baseline (no modification detected)."
                        else:
                            verified = True
                            status = "MODIFIED"
                else:
                    base_sha = _resolve_git_baseline_sha(
                        eidos_dir,
                        file_path,
                        lookup_target,
                        target_file,
                        rev_cache=rev_cache,
                        blob_cache=blob_cache,
                    )
                    mod_ok = (cur_sha != base_sha) if base_sha is not None else (delta_status == "applied")
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


def _sequence_sort_key(doc_id: str, prefix: str) -> tuple[int, int, str]:
    """Return a deterministic (year, seq, doc_id) sort key for DLT-YYYY-NNN or EXP-YYYY-NNN IDs."""
    cleaned = str(doc_id or "").strip().upper()
    m = re.match(rf"^{re.escape(prefix.upper())}-(\d{{4}})-(\d+)(?:-[A-Z0-9_-]+)?$", cleaned)
    if m:
        return int(m.group(1)), int(m.group(2)), cleaned
    return 0, 0, cleaned


def archive_explorations(
    eidos_dir: Path,
    keep_recent: int = 15,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move and slim concluded Explorations (adopted/discarded) beyond the last keep_recent items into eidos/explorations/archive/."""
    if keep_recent < 0:
        raise ValueError("keep_recent must be >= 0")

    exp_dir = eidos_dir / "explorations"
    archive_dir = exp_dir / "archive"
    if not exp_dir.exists():
        return {
            "archived_count": 0,
            "retained_terminal_count": 0,
            "active_retained_count": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "bytes_saved": 0,
            "archived_ids": [],
            "retained_ids": [],
        }

    terminal_items: list[tuple[tuple[int, int, str], Path, Any]] = []
    active_retained_count = 0

    for p in sorted(exp_dir.glob("*.adoc")):
        if not p.is_file() or p.is_symlink():
            continue
        doc, _, _ = get_cached_spec_document(p)
        if doc is None or doc.doc_type != "product/exploration":
            continue
        status = str((doc.frontmatter or {}).get("status", "proposed")).strip().lower()
        doc_id = str(doc.doc_id or p.stem.upper())
        if status in ("adopted", "discarded"):
            terminal_items.append((_sequence_sort_key(doc_id, "EXP"), p, doc))
        else:
            active_retained_count += 1

    terminal_items.sort(key=lambda item: item[0])
    if keep_recent == 0:
        to_archive = terminal_items
        retained = []
    elif len(terminal_items) > keep_recent:
        to_archive = terminal_items[:-keep_recent]
        retained = terminal_items[-keep_recent:]
    else:
        to_archive = []
        retained = terminal_items

    bytes_before = 0
    bytes_after = 0
    archived_ids: list[str] = []
    validator = SpecValidator()

    for _, p, doc in to_archive:
        fm = dict(doc.frontmatter or {})
        doc_id = str(doc.doc_id or p.stem.upper())
        title = str(fm.get("title") or doc.title or doc_id)
        orig_size = p.stat().st_size

        hypothesis = str(fm.get("hypothesis") or "").strip()
        if not hypothesis:
            m_hyp = re.search(
                r"(?:\*Hypothesis:\*|\* \*Hypothesis\*:|^==+\s+[^\n]*Hypothesis[^\n]*)\s*\n+(?:\*\s*\*Hypothesis\*:\s*)?([^=\n][^\n]+)",
                doc.body or "",
                re.MULTILINE,
            )
            if m_hyp:
                hypothesis = " ".join(m_hyp.group(1).split())

        findings = str(fm.get("findings") or "").strip()
        if not findings:
            m_find = re.search(
                r"(?:\*Findings:\*|\* \*Findings\*:|^==+\s+[^\n]*(?:Findings|Outcome)[^\n]*)\s*\n+\*?\s*([^=\n][^\n]+)",
                doc.body or "",
                re.MULTILINE,
            )
            if m_find:
                findings = " ".join(m_find.group(1).split())

        slim_fm: dict[str, Any] = {
            "type": "product/exploration",
            "id": doc_id,
            "title": title,
            "status": str(fm.get("status", "adopted")).strip().lower(),
        }
        if isinstance(fm.get("decision"), dict) and fm["decision"]:
            dec_clean = {k: v for k, v in fm["decision"].items() if k in ("outcome", "resulting_deltas")}
            if dec_clean:
                slim_fm["decision"] = dec_clean

        body_lines = [f"= Exploration: {_single_line(title)}", ""]
        if hypothesis:
            body_lines.extend(["== Core Hypothesis", hypothesis, ""])
        if findings:
            body_lines.extend(["== Findings & Learnings", findings, ""])
        slim_body = "\n".join(body_lines).rstrip() + "\n"
        slim_content = _render_spec(slim_fm, slim_body)
        slim_size = len(slim_content.encode("utf-8"))

        dest_p = archive_dir / p.name
        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)
            slim_doc = _parse_spec_content(dest_p, slim_content)
            errors = validator.validate_document(slim_doc)
            if errors:
                raise ValueError(f"Archived exploration '{doc_id}' failed schema validation: {'; '.join(errors)}")
            dest_p.write_text(slim_content, encoding="utf-8")
            p.unlink()

        bytes_before += orig_size
        bytes_after += slim_size
        archived_ids.append(doc_id)

    return {
        "archived_count": len(archived_ids),
        "retained_terminal_count": len(retained),
        "active_retained_count": active_retained_count,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "bytes_saved": max(0, bytes_before - bytes_after),
        "archived_ids": archived_ids,
        "retained_ids": [str(it[2].doc_id or it[1].stem.upper()) for it in retained],
    }


def archive_deltas(
    eidos_dir: Path,
    keep_recent: int = 15,
    include_explorations: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move and slim reconciled SpecDeltas (applied/rejected) beyond the last keep_recent items into eidos/deltas/archive/."""
    if keep_recent < 0:
        raise ValueError("keep_recent must be >= 0")

    deltas_dir = eidos_dir / "deltas"
    archive_dir = deltas_dir / "archive"
    terminal_items: list[tuple[tuple[int, int, str], Path, Any]] = []
    active_retained_count = 0

    if deltas_dir.exists():
        for p in sorted(deltas_dir.glob("*.adoc")):
            if not p.is_file() or p.is_symlink():
                continue
            doc, _, _ = get_cached_spec_document(p)
            if doc is None or doc.doc_type != "delta/mutation":
                continue
            status = str((doc.frontmatter or {}).get("status", "proposed")).strip().lower()
            doc_id = str(doc.doc_id or p.stem.upper())
            if status in ("applied", "rejected"):
                terminal_items.append((_sequence_sort_key(doc_id, "DLT"), p, doc))
            else:
                active_retained_count += 1

    terminal_items.sort(key=lambda item: item[0])
    if keep_recent == 0:
        to_archive = terminal_items
        retained = []
    elif len(terminal_items) > keep_recent:
        to_archive = terminal_items[:-keep_recent]
        retained = terminal_items[-keep_recent:]
    else:
        to_archive = []
        retained = terminal_items

    bytes_before = 0
    bytes_after = 0
    archived_ids: list[str] = []

    if to_archive:
        validator = SpecValidator()

        for _, p, doc in to_archive:
            fm = dict(doc.frontmatter or {})
            doc_id = str(doc.doc_id or p.stem.upper())
            title = str(fm.get("title") or doc.title or doc_id)
            orig_size = p.stat().st_size

            slim_muts: list[dict[str, Any]] = []
            for m in fm.get("mutations") or []:
                if not isinstance(m, dict):
                    continue
                m_copy = {k: v for k, v in m.items() if k not in ("expect", "details")}
                slim_muts.append(m_copy)

            slim_fm: dict[str, Any] = {
                "type": "delta/mutation",
                "id": doc_id,
                "title": title,
                "status": str(fm.get("status", "applied")).strip().lower(),
                "intent": fm.get("intent") or {"kind": "feature", "motivation": title},
            }
            if fm.get("graduated_from"):
                slim_fm["graduated_from"] = fm["graduated_from"]
            if fm.get("tombstone"):
                slim_fm["tombstone"] = fm["tombstone"]
            if fm.get("decisions"):
                slim_fm["decisions"] = fm["decisions"]
            if isinstance(fm.get("vcs"), dict) and fm["vcs"]:
                slim_fm["vcs"] = dict(fm["vcs"])

            slim_fm["mutations"] = slim_muts
            slim_body = f"= SpecDelta: {_single_line(title)}\n"
            slim_content = _render_spec(slim_fm, slim_body)
            slim_size = len(slim_content.encode("utf-8"))

            dest_p = archive_dir / p.name
            if not dry_run:
                archive_dir.mkdir(parents=True, exist_ok=True)
                slim_doc = _parse_spec_content(dest_p, slim_content)
                errors = validator.validate_document(slim_doc)
                if errors:
                    raise ValueError(f"Archived SpecDelta '{doc_id}' failed schema validation: {'; '.join(errors)}")
                dest_p.write_text(slim_content, encoding="utf-8")
                p.unlink()

            bytes_before += orig_size
            bytes_after += slim_size
            archived_ids.append(doc_id)

    exp_report = (
        archive_explorations(eidos_dir, keep_recent=keep_recent, dry_run=dry_run)
        if include_explorations
        else {
            "archived_count": 0,
            "retained_terminal_count": 0,
            "active_retained_count": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "bytes_saved": 0,
            "archived_ids": [],
            "retained_ids": [],
        }
    )

    total_before = bytes_before + exp_report["bytes_before"]
    total_after = bytes_after + exp_report["bytes_after"]
    return {
        "keep_recent": keep_recent,
        "dry_run": dry_run,
        "deltas": {
            "archived_count": len(archived_ids),
            "retained_terminal_count": len(retained),
            "active_retained_count": active_retained_count,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved": max(0, bytes_before - bytes_after),
            "archived_ids": archived_ids,
            "retained_ids": [str(it[2].doc_id or it[1].stem.upper()) for it in retained],
        },
        "explorations": exp_report,
        "total_bytes_saved": max(0, total_before - total_after),
    }
