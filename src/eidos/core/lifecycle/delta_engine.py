"""SpecDelta engine: listing, status summaries, CRUD, apply_delta, and facade exports (CMP-DELTA-ENGINE)."""

import contextlib
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from eidos.core.index.storage import get_cached_spec_document
from eidos.core.lifecycle.archiver import (
    _extract_exploration_section as _extract_exploration_section,
)
from eidos.core.lifecycle.archiver import (
    _sequence_sort_key as _sequence_sort_key,
)
from eidos.core.lifecycle.archiver import (
    archive_deltas as archive_deltas,
)
from eidos.core.lifecycle.archiver import (
    archive_explorations as archive_explorations,
)
from eidos.core.lifecycle.catalog import (
    _render_spec,
    _set_frontmatter_scalar,
    _single_line,
    find_references_to_spec,
)
from eidos.core.lifecycle.delta_verifier import (
    SUPPORTED_MUTATION_OPERATIONS as SUPPORTED_MUTATION_OPERATIONS,
)
from eidos.core.lifecycle.delta_verifier import (
    DeltaVerificationReport as DeltaVerificationReport,
)
from eidos.core.lifecycle.delta_verifier import (
    MutationVerificationItem as MutationVerificationItem,
)
from eidos.core.lifecycle.delta_verifier import (
    _digest_dict as _digest_dict,
)
from eidos.core.lifecycle.delta_verifier import (
    _digest_normalized_bytes as _digest_normalized_bytes,
)
from eidos.core.lifecycle.delta_verifier import (
    _evaluate_declarative_postcondition as _evaluate_declarative_postcondition,
)
from eidos.core.lifecycle.delta_verifier import (
    _extract_mutation_target as _extract_mutation_target,
)
from eidos.core.lifecycle.delta_verifier import (
    _git_rel_path as _git_rel_path,
)
from eidos.core.lifecycle.delta_verifier import (
    _is_shallow_committed_delta as _is_shallow_committed_delta,
)
from eidos.core.lifecycle.delta_verifier import (
    _is_shallow_git_repo as _is_shallow_git_repo,
)
from eidos.core.lifecycle.delta_verifier import (
    _normalize_expect_spec as _normalize_expect_spec,
)
from eidos.core.lifecycle.delta_verifier import (
    _resolve_delta_lookup,
    _resolve_git_head_sha,
    _resolve_git_repo_root,
)
from eidos.core.lifecycle.delta_verifier import (
    _resolve_frontmatter_path as _resolve_frontmatter_path,
)
from eidos.core.lifecycle.delta_verifier import (
    _resolve_git_baseline_sha as _resolve_git_baseline_sha,
)
from eidos.core.lifecycle.delta_verifier import (
    _resolve_target_path_and_id as _resolve_target_path_and_id,
)
from eidos.core.lifecycle.delta_verifier import (
    _strip_spec_comments as _strip_spec_comments,
)
from eidos.core.lifecycle.delta_verifier import (
    detect_delta_conflicts as detect_delta_conflicts,
)
from eidos.core.lifecycle.delta_verifier import (
    normalize_delta_mutation as normalize_delta_mutation,
)
from eidos.core.lifecycle.delta_verifier import (
    verify_delta_mutations as verify_delta_mutations,
)
from eidos.core.spec.parser import _parse_spec_content, extract_frontmatter, parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import render_spec_template

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


def _collect_other_git_branch_delta_stems(eidos_dir: Path) -> list[str]:
    """Collect SpecDelta stems from non-HEAD local/remote Git branches without spawning Git on single-branch repos."""
    resolved_eidos = eidos_dir.resolve()
    repo_root: Path | None = None
    git_dir: Path | None = None
    for cand in (resolved_eidos, *resolved_eidos.parents):
        dot_git = cand / ".git"
        if dot_git.is_dir():
            repo_root = cand
            git_dir = dot_git
            break
        if dot_git.is_file():
            with contextlib.suppress(OSError):
                raw_ptr = dot_git.read_text(encoding="utf-8").strip()
                if raw_ptr.startswith("gitdir:"):
                    ptr_path = Path(raw_ptr.partition(":")[2].strip())
                    resolved_git = (cand / ptr_path).resolve() if not ptr_path.is_absolute() else ptr_path.resolve()
                    commondir_file = resolved_git / "commondir"
                    if commondir_file.is_file():
                        common_rel = commondir_file.read_text(encoding="utf-8").strip()
                        common_dir = (
                            (resolved_git / common_rel).resolve()
                            if not Path(common_rel).is_absolute()
                            else Path(common_rel).resolve()
                        )
                    else:
                        common_dir = resolved_git
                    repo_root = cand
                    git_dir = common_dir
                    break
    if repo_root is None or git_dir is None:
        return []

    head_ref = ""
    head_file = (repo_root / ".git" / "HEAD") if (repo_root / ".git").is_dir() else None
    if head_file is None:
        with contextlib.suppress(OSError):
            ptr_txt = (repo_root / ".git").read_text(encoding="utf-8").strip()
            if ptr_txt.startswith("gitdir:"):
                p_dir = Path(ptr_txt.partition(":")[2].strip())
                wt_git = (repo_root / p_dir).resolve() if not p_dir.is_absolute() else p_dir.resolve()
                if (wt_git / "HEAD").is_file():
                    head_file = wt_git / "HEAD"
    if head_file is not None and head_file.is_file():
        with contextlib.suppress(OSError):
            head_txt = head_file.read_text(encoding="utf-8").strip()
            if head_txt.startswith("ref:"):
                head_ref = head_txt.partition(":")[2].strip()

    discovered_refs: set[str] = set()
    for sub in ("refs/heads", "refs/remotes"):
        ref_base = git_dir / sub
        if ref_base.is_dir():
            for rf in ref_base.rglob("*"):
                if rf.is_file() and rf.name != "HEAD":
                    with contextlib.suppress(ValueError):
                        rel_ref = f"{sub}/{rf.relative_to(ref_base).as_posix()}"
                        discovered_refs.add(rel_ref)
    packed_refs = git_dir / "packed-refs"
    if packed_refs.is_file():
        with contextlib.suppress(OSError):
            for ln in packed_refs.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = ln.strip()
                if not stripped or stripped.startswith(("#", "^")):
                    continue
                parts = stripped.split()
                if (
                    len(parts) >= 2
                    and parts[1].startswith(("refs/heads/", "refs/remotes/"))
                    and not parts[1].endswith("/HEAD")
                ):
                    discovered_refs.add(parts[1])

    other_refs = sorted(r for r in discovered_refs if r != head_ref)
    if not other_refs:
        return []

    try:
        rel_deltas = (eidos_dir / "deltas").resolve().relative_to(repo_root).as_posix()
    except ValueError:
        rel_deltas = "eidos/deltas"

    stems: list[str] = []
    for ref in other_refs:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            res = subprocess.run(
                ["git", "-C", str(repo_root), "ls-tree", "-r", "--name-only", ref, "--", rel_deltas],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                for ln in res.stdout.splitlines():
                    p_str = ln.strip()
                    if p_str.lower().endswith(".adoc"):
                        stems.append(Path(p_str).stem)
    return stems


def generate_collision_free_delta_id(
    eidos_dir: Path,
    raw_id: str | None = None,
    title: str = "",
    slug_suffix: bool = False,
) -> str:
    """Generate a collision-free SpecDelta ID (DLT-YYYY-NNN-<SLUG>-<4HEX>) across local deltas and visible Git branches."""
    import datetime

    from eidos.core.lifecycle.catalog import normalize_id

    deltas_dir = eidos_dir / "deltas"
    existing_ids: set[str] = set()
    existing_seq_prefixes: set[str] = set()
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
            existing_seq_prefixes.add(f"DLT-{yr}-{seq:03d}")
            if seq > max_seq_by_year.get(yr, 0):
                max_seq_by_year[yr] = seq

    if deltas_dir.exists():
        for p in deltas_dir.rglob("*.adoc"):
            _record_candidate(p.stem)
            with contextlib.suppress(OSError):
                fm_cand, _, _ = extract_frontmatter(p.read_text(encoding="utf-8"))
                if isinstance(fm_cand, dict) and isinstance(fm_cand.get("id"), str):
                    _record_candidate(fm_cand["id"])

    for branch_stem in _collect_other_git_branch_delta_stems(eidos_dir):
        _record_candidate(branch_stem)

    cleaned_raw = (raw_id or "").strip()
    use_extended_slug = bool(slug_suffix or cleaned_raw.lower() == "auto-slug")
    is_auto = cleaned_raw.lower() in ("", "auto", "auto-slug")

    year = datetime.datetime.now(datetime.UTC).year
    if is_auto:
        next_seq = max_seq_by_year.get(year, 0) + 1
        while f"DLT-{year}-{next_seq:03d}" in existing_seq_prefixes:
            next_seq += 1
        base_id = f"DLT-{year}-{next_seq:03d}"
    else:
        base_id = normalize_id("delta", cleaned_raw).replace("_", "-")
        if re.match(r"^DLT-(?:\d{4}-\d+|\d+)-[A-Z0-9-]+-[0-9A-F]{4}$", base_id):
            base_id = base_id[:-5]

    max_tokens = 4 if use_extended_slug else 2
    slug_tokens = [tok for tok in re.split(r"[^A-Za-z0-9]+", title.upper()) if len(tok) >= 2][:max_tokens]
    title_slug = "-".join(slug_tokens) if slug_tokens else "DELTA"
    has_alpha_slug = bool(re.match(r"^DLT-(?:\d{4}-\d+|\d+)-[A-Z][A-Z0-9-]*$", base_id))
    if has_alpha_slug and (not use_extended_slug or base_id.endswith(f"-{title_slug}")):
        base_with_slug = base_id
    else:
        base_with_slug = f"{base_id}-{title_slug}"

    attempt = 0
    while True:
        hash_input = f"{base_with_slug}:{title}" if attempt == 0 else f"{base_with_slug}:{title}:{attempt}"
        short_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:4].upper()
        candidate_id = f"{base_with_slug}-{short_hash}"
        if candidate_id not in existing_ids:
            return candidate_id
        attempt += 1


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
    file_path, amb_err = _resolve_delta_lookup(eidos_dir, target)
    if amb_err:
        return False, amb_err, []
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

    file_path, amb_err = _resolve_delta_lookup(eidos_dir, delta_id_or_path)
    if amb_err:
        return False, amb_err, None, None

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
    file_path, amb_err = _resolve_delta_lookup(eidos_dir, delta_id_or_path)
    if amb_err:
        return False, amb_err, None

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None

    raw_mutations = doc.frontmatter.get("mutations", [])
    if not isinstance(raw_mutations, list) or not raw_mutations:
        return (
            False,
            f"Cannot apply SpecDelta '{doc.doc_id}': SpecDelta declares zero mutations (mutations: []).",
            file_path,
        )

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
