"""Team operational memory freshness evaluation and secondary budgeted context attachment (CMP-TIERED-CONTEXT-COMPRESSOR)."""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import orjson

from eidos.core.index.context_globs import _GLOB_EXCLUDED_DIRS, _has_glob_magic, expand_brace_globs
from eidos.core.index.fts_query import execute_fts_query
from eidos.core.index.storage import (
    _resolve_conn_db_path,
    load_memory_freshness_cache,
    save_memory_freshness_cache,
)
from eidos.core.workspace.config import EidosConfig, load_repo_config_with_errors
from eidos.core.workspace.project import match_path_pattern

DEFAULT_MEMORY_COMMIT_DRIFT_THRESHOLD = 5

# Per-tier (max_anchored, max_global) caps calibrated in EXP-2026-012 Spike 2.
MEMORY_TIER_CAPS: dict[str, tuple[int, int]] = {
    "anchor": (2, 0),
    "guardrails": (3, 2),
    "deep": (5, 3),
}

_FRESHNESS_ORDER: dict[str, int] = {
    "fresh": 0,
    "unverifiable": 1,
    "stale": 2,
    "expired": 3,
}

_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_H2_HEADING_RE = re.compile(r"^==[ \t]+(.+?)[ \t]*$")
_MD_FENCE_LINE_RE = re.compile(r"^(`{3,}|~{3,})")
_ADOC_BLOCK_DELIM_RE = re.compile(r"^(?:-{4,}|={4,}|\.{4,}|\+{4,}|/{4,}|\*{4,}|_{4,})$")
_ADOC_ATTR_LINE_RE = re.compile(r"^:[A-Za-z0-9_.-]+:.*$")


def _scan_memory_h2_sections(raw_body: str) -> tuple[str, list[tuple[str, str]]]:
    """Scan top-level AsciiDoc ``== Heading`` sections while ignoring headings inside fenced or delimited blocks.

    Returns ``(preamble, sections)`` where ``preamble`` preserves any prose before the first H2
    (excluding the leading ``= Title`` and ``:attr:`` document header lines) and ``sections`` is an
    ordered list of ``(heading, raw_content)`` pairs.
    """
    lines = (raw_body or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    total = len(lines)
    in_fence_char: str | None = None
    in_fence_len = 0
    in_adoc_delim: str | None = None

    preamble_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    in_doc_header = True

    for idx, line in enumerate(lines):
        stripped = line.strip()

        if in_fence_char is not None:
            if re.fullmatch(rf"{re.escape(in_fence_char)}{{{in_fence_len},}}", stripped):
                in_fence_char = None
            (sections[-1][1] if sections else preamble_lines).append(line)
            continue

        if in_adoc_delim is not None:
            if stripped == in_adoc_delim:
                in_adoc_delim = None
            (sections[-1][1] if sections else preamble_lines).append(line)
            continue

        if not sections and in_doc_header:
            if not stripped:
                continue
            if stripped.startswith("= ") and not stripped.startswith("== "):
                continue
            if _ADOC_ATTR_LINE_RE.match(stripped):
                continue
            in_doc_header = False

        m_fence = _MD_FENCE_LINE_RE.match(stripped)
        if m_fence:
            in_fence_char = m_fence.group(1)[0]
            in_fence_len = len(m_fence.group(1))
            (sections[-1][1] if sections else preamble_lines).append(line)
            continue

        if _ADOC_BLOCK_DELIM_RE.match(stripped):
            has_close = any(lines[scan].strip() == stripped for scan in range(idx + 1, total))
            if has_close:
                in_adoc_delim = stripped
            (sections[-1][1] if sections else preamble_lines).append(line)
            continue

        m_h2 = _H2_HEADING_RE.match(line)
        if m_h2:
            sections.append((m_h2.group(1).strip(), []))
            continue

        (sections[-1][1] if sections else preamble_lines).append(line)

    preamble = "\n".join(preamble_lines).strip()
    rendered_sections = [(heading, "\n".join(buf).strip()) for heading, buf in sections]
    return preamble, rendered_sections


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "")


def _resolve_git_head_sha(repo_root: Path, *, head_ref: str = "HEAD") -> str:
    """Resolve repository Git commit SHA (default ``HEAD`` with a sub-millisecond .git/HEAD fast path)."""
    if head_ref == "HEAD":
        git_dir = repo_root / ".git"
        if git_dir.is_dir():
            head_file = git_dir / "HEAD"
            with contextlib.suppress(OSError):
                head_raw = head_file.read_text(encoding="utf-8", errors="replace").strip()
                if _HEX40_RE.match(head_raw):
                    return head_raw.lower()
                if head_raw.startswith("ref:"):
                    ref_name = head_raw[4:].strip()
                    ref_file = git_dir / ref_name
                    if ref_file.is_file():
                        sha = ref_file.read_text(encoding="utf-8", errors="replace").strip()
                        if _HEX40_RE.match(sha):
                            return sha.lower()
                    packed_refs = git_dir / "packed-refs"
                    if packed_refs.is_file():
                        for line in packed_refs.read_text(encoding="utf-8", errors="replace").splitlines():
                            if not line or line.startswith(("#", "^")):
                                continue
                            parts = line.split(" ", 1)
                            if len(parts) == 2 and parts[1].strip() == ref_name and _HEX40_RE.match(parts[0].strip()):
                                return parts[0].strip().lower()
    res = _run_git(repo_root, "rev-parse", "--verify", head_ref)
    return res.stdout.strip().lower() if res.returncode == 0 else ""


def _compute_memory_digest(
    frontmatter: dict[str, Any],
    *,
    commit_drift_threshold: int = DEFAULT_MEMORY_COMMIT_DRIFT_THRESHOLD,
) -> str:
    """Compute a stable SHA-256 digest over freshness-relevant memory frontmatter fields and threshold."""
    payload = {
        "targets": frontmatter.get("targets") or {},
        "verified_at": frontmatter.get("verified_at"),
        "verified_commit": frontmatter.get("verified_commit"),
        "expires_at": frontmatter.get("expires_at"),
        "status": frontmatter.get("status"),
        "promoted_to": frontmatter.get("promoted_to"),
        "commit_drift_threshold": int(commit_drift_threshold),
    }
    raw = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(raw).hexdigest()[:16]


def extract_memory_sections(raw_text: str) -> dict[str, str]:
    """Extract compact ``guidance`` and ``why`` sections from an ``agent/memory`` AsciiDoc document."""
    if not raw_text:
        return {}
    body = _FRONTMATTER_RE.sub("", raw_text, count=1)
    _preamble, scanned = _scan_memory_h2_sections(body)
    if not scanned:
        return {}
    sections: dict[str, str] = {}
    for heading_orig, raw_content in scanned:
        heading = heading_orig.lower()
        content = re.sub(r"\s+", " ", raw_content).strip()
        if not content:
            continue
        if heading == "guidance" and "guidance" not in sections:
            sections["guidance"] = content
        elif heading in ("why", "rationale", "context") and "why" not in sections:
            sections["why"] = content
    return sections


def _normalize_target_path(raw: str) -> str:
    norm = raw.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


def _is_unsafe_rel_path(norm: str) -> bool:
    return not norm or norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":") or ".." in norm.split("/")


def _extract_target_lists(frontmatter: dict[str, Any]) -> tuple[list[str], list[str]]:
    targets = frontmatter.get("targets") if isinstance(frontmatter.get("targets"), dict) else {}
    raw_entities = targets.get("entities")
    raw_paths = targets.get("paths")
    entities = (
        [str(e).strip() for e in raw_entities if isinstance(e, str) and e.strip()]
        if isinstance(raw_entities, list)
        else []
    )
    paths = (
        [str(p).strip() for p in raw_paths if isinstance(p, str) and p.strip()] if isinstance(raw_paths, list) else []
    )
    return entities, paths


def _check_target_paths(repo_root: Path, raw_paths: list[str]) -> tuple[list[str], list[str]]:
    """Verify target paths/globs on disk and return ``(missing_paths, git_pathspecs)``."""
    resolved_root = repo_root.resolve()
    missing_paths: list[str] = []
    git_pathspecs: list[str] = []
    seen_specs: set[str] = set()

    for raw_pat in raw_paths:
        if not isinstance(raw_pat, str) or not raw_pat.strip():
            continue
        norm_pat = _normalize_target_path(raw_pat)
        if _is_unsafe_rel_path(norm_pat):
            missing_paths.append(raw_pat.strip())
            continue
        variants = expand_brace_globs(norm_pat)
        pat_matched = False
        for var in variants:
            if _is_unsafe_rel_path(var):
                continue
            if _has_glob_magic(var):
                matched_any = False
                with contextlib.suppress(OSError, ValueError, NotImplementedError):
                    for candidate in resolved_root.glob(var):
                        try:
                            rel_parts = candidate.resolve().relative_to(resolved_root).parts
                        except ValueError:
                            continue
                        if not rel_parts or any(part in _GLOB_EXCLUDED_DIRS for part in rel_parts):
                            continue
                        matched_any = True
                        break
                if matched_any:
                    pat_matched = True
                    sp = f":(glob){var}"
                    if sp not in seen_specs:
                        seen_specs.add(sp)
                        git_pathspecs.append(sp)
            else:
                target_abs = resolved_root / var
                if target_abs.exists():
                    pat_matched = True
                    if var not in seen_specs:
                        seen_specs.add(var)
                        git_pathspecs.append(var)
        if not pat_matched:
            missing_paths.append(raw_pat.strip())

    return missing_paths, git_pathspecs


def _check_instant_freshness(
    repo_root: Path,
    frontmatter: dict[str, Any],
    catalog_ids: set[str],
    effective_today: date,
) -> tuple[str, str] | None:
    """Evaluate instant in-memory / stat freshness checks before consulting cache or Git."""
    expires_at = frontmatter.get("expires_at")
    if isinstance(expires_at, str) and expires_at.strip():
        exp_str = expires_at.strip()
        with contextlib.suppress(ValueError):
            if date.fromisoformat(exp_str) < effective_today:
                return "expired", f"expired on {exp_str} (today is {effective_today.isoformat()})"

    entities, raw_paths = _extract_target_lists(frontmatter)
    missing_entities = [e for e in entities if e not in catalog_ids]
    if missing_entities:
        return "stale", f"missing target entity ID(s): {', '.join(missing_entities)}"

    if not raw_paths:
        return "fresh", "no path anchors; entity anchors and TTL valid"

    missing_paths, _ = _check_target_paths(repo_root, raw_paths)
    if missing_paths:
        return "stale", f"missing target path(s): {', '.join(missing_paths)}"

    return None


def _resolve_memory_config(
    repo_root: Path,
    eidos_dir: Path | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> EidosConfig:
    """Resolve optional repository configuration for memory freshness thresholds and context caps."""
    effective_eidos = eidos_dir
    if effective_eidos is None:
        resolved_db = db_path
        if resolved_db is None and conn is not None:
            resolved_db = _resolve_conn_db_path(conn, None)
        if resolved_db is not None and resolved_db.parent.name == ".cache":
            effective_eidos = resolved_db.parent.parent
    cfg, _ = load_repo_config_with_errors(eidos_dir=effective_eidos, repo_root=repo_root)
    return cfg


def evaluate_memory_freshness(
    repo_root: Path,
    frontmatter: dict[str, Any],
    catalog_ids: set[str],
    *,
    today: date | None = None,
    head_sha: str | None = None,
    commit_drift_threshold: int | None = None,
    eidos_dir: Path | None = None,
    git_repo_root: Path | None = None,
    head_ref: str = "HEAD",
) -> tuple[str, str]:
    """Deterministically evaluate freshness for a single ``agent/memory`` entry.

    Returns ``(freshness, reason)`` where ``freshness`` is one of
    ``'fresh'``, ``'stale'``, ``'expired'``, or ``'unverifiable'``.
    """
    effective_threshold = (
        commit_drift_threshold
        if commit_drift_threshold is not None
        else _resolve_memory_config(repo_root, eidos_dir).memory.commit_drift_threshold
    )
    effective_today = today or date.today()
    instant = _check_instant_freshness(repo_root, frontmatter, catalog_ids, effective_today)
    if instant is not None:
        return instant

    _, raw_paths = _extract_target_lists(frontmatter)
    missing_paths, git_pathspecs = _check_target_paths(repo_root, raw_paths)
    if missing_paths:
        return "stale", f"missing target path(s): {', '.join(missing_paths)}"

    v_commit_raw = frontmatter.get("verified_commit")
    v_commit = str(v_commit_raw).strip() if isinstance(v_commit_raw, str) and v_commit_raw.strip() else ""
    v_at = str(frontmatter.get("verified_at") or "").strip()

    git_cwd = git_repo_root if git_repo_root is not None else repo_root
    effective_head = _resolve_git_head_sha(git_cwd, head_ref=head_ref) if head_sha is None else head_sha
    if not effective_head:
        return "unverifiable", f"no Git HEAD commit available (last verified {v_at})"

    if not v_commit:
        return "unverifiable", f"verified_commit not recorded (last verified {v_at})"

    anc_res = _run_git(git_cwd, "merge-base", "--is-ancestor", v_commit, head_ref)
    if anc_res.returncode != 0:
        return (
            "unverifiable",
            f"verified_commit {v_commit[:7]} unreachable from {head_ref} (shallow clone, squash-merge, or rebase); last verified {v_at}",
        )

    cnt_res = _run_git(git_cwd, "rev-list", "--count", f"{v_commit}..{head_ref}", "--", *git_pathspecs)
    if cnt_res.returncode == 0 and cnt_res.stdout.strip().isdigit():
        n_commits = int(cnt_res.stdout.strip())
        if n_commits >= effective_threshold:
            return (
                "stale",
                f"{n_commits} commits modified target path(s) since {v_commit[:7]} (threshold {effective_threshold})",
            )
        return "fresh", f"{n_commits} commit(s) on target path(s) since {v_commit[:7]}"

    return "unverifiable", f"could not compute commit distance from {v_commit[:7]}"


def resolve_memory_freshness_batch(
    repo_root: Path,
    memories: list[tuple[str, dict[str, Any]]],
    catalog_ids: set[str],
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
    today: date | None = None,
    commit_drift_threshold: int | None = None,
    eidos_dir: Path | None = None,
    git_repo_root: Path | None = None,
    head_ref: str = "HEAD",
) -> dict[str, tuple[str, str]]:
    """Resolve freshness verdicts for a batch of memories backed by ``memory_freshness_cache``."""
    if not memories:
        return {}

    effective_threshold = (
        commit_drift_threshold
        if commit_drift_threshold is not None
        else _resolve_memory_config(repo_root, eidos_dir, conn=conn, db_path=db_path).memory.commit_drift_threshold
    )
    effective_today = today or date.today()
    today_iso = effective_today.isoformat()
    results: dict[str, tuple[str, str]] = {}
    need_git_check: list[tuple[str, dict[str, Any]]] = []

    for mem_id, fm in memories:
        instant = _check_instant_freshness(repo_root, fm, catalog_ids, effective_today)
        if instant is not None:
            results[mem_id] = instant
        else:
            need_git_check.append((mem_id, fm))

    if not need_git_check:
        return results

    git_cwd = git_repo_root if git_repo_root is not None else repo_root
    head_sha = _resolve_git_head_sha(git_cwd, head_ref=head_ref)
    cached_rows: dict[str, tuple[str, str, str, str, str]] = {}
    if conn is not None:
        mem_ids = [mid for mid, _ in need_git_check]
        with contextlib.suppress(sqlite3.Error):
            for start in range(0, len(mem_ids), 400):
                batch = mem_ids[start : start + 400]
                ph = ",".join("?" for _ in batch)
                for row in conn.execute(
                    f"SELECT memory_id, head_sha, mem_sha256, freshness, reason, checked_at "
                    f"FROM memory_freshness_cache WHERE memory_id IN ({ph});",
                    tuple(batch),
                ):
                    cached_rows[str(row["memory_id"])] = (
                        str(row["head_sha"]),
                        str(row["mem_sha256"]),
                        str(row["freshness"]),
                        str(row["reason"]),
                        str(row["checked_at"]),
                    )
    elif db_path is not None:
        cached_rows = load_memory_freshness_cache(db_path)

    to_upsert: list[tuple[str, str, str, str, str, str]] = []
    for mem_id, fm in need_git_check:
        mem_sha = _compute_memory_digest(fm, commit_drift_threshold=effective_threshold)
        cached = cached_rows.get(mem_id)
        if (
            cached is not None
            and cached[0] == head_sha
            and cached[1] == mem_sha
            and cached[4] == today_iso
            and cached[2] != "unverifiable"
            and not cached[3].startswith("missing target")
        ):
            results[mem_id] = (cached[2], cached[3])
            continue
        freshness, reason = evaluate_memory_freshness(
            repo_root,
            fm,
            catalog_ids,
            today=effective_today,
            head_sha=head_sha,
            commit_drift_threshold=effective_threshold,
            eidos_dir=eidos_dir,
            git_repo_root=git_repo_root,
            head_ref=head_ref,
        )
        results[mem_id] = (freshness, reason)
        if freshness != "unverifiable":
            to_upsert.append((mem_id, head_sha, mem_sha, freshness, reason, today_iso))

    if to_upsert and db_path is not None:
        save_memory_freshness_cache(db_path, to_upsert)

    return results


def _best_path_anchor_score(target_paths: list[str], candidate_files: list[str]) -> int:
    best = 0
    for raw_pat in target_paths:
        if not isinstance(raw_pat, str) or not raw_pat.strip():
            continue
        for var in expand_brace_globs(_normalize_target_path(raw_pat)):
            for fpath in candidate_files:
                score = match_path_pattern(fpath, var)
                if score > best:
                    best = score
    return best


def attach_context_memories(
    conn: sqlite3.Connection,
    repo_root: Path,
    *,
    tier: str = "guardrails",
    clean_files: list[str] | None = None,
    requested_files: list[str] | None = None,
    direct_entity_ids: set[str] | None = None,
    expanded_entity_ids: set[str] | None = None,
    task: str | None = None,
    has_explicit_query: bool = False,
    has_architectural_matches: bool = False,
    use_semantic: bool = True,
    eidos_dir: Path | None = None,
    db_path: Path | None = None,
    today: date | None = None,
    tier_caps: dict[str, tuple[int, int]] | None = None,
    commit_drift_threshold: int | None = None,
) -> list[dict[str, Any]]:
    """Select, rank, and freshness-annotate active team operational memories for a context slice."""
    rows = conn.execute(
        "SELECT id, title, file_path, data_json FROM entities WHERE type = 'agent/memory' AND status = 'active' ORDER BY id;"
    ).fetchall()
    if not rows:
        return []

    active_by_id: dict[str, tuple[str, str, dict[str, Any]]] = {}
    global_ids: list[str] = []
    for row in rows:
        rel_fp = str(row["file_path"] or "").replace("\\", "/")
        if "/memory/archive/" in f"/{rel_fp.lstrip('/')}":
            continue
        try:
            fm = orjson.loads(row["data_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(fm, dict):
            continue
        mem_id = str(row["id"])
        title = str(row["title"] or mem_id)
        active_by_id[mem_id] = (title, rel_fp, fm)
        t_entities, t_paths = _extract_target_lists(fm)
        if not t_entities and not t_paths:
            global_ids.append(mem_id)

    if not active_by_id:
        return []

    if tier_caps is None or commit_drift_threshold is None:
        mem_cfg = _resolve_memory_config(repo_root, eidos_dir, conn=conn, db_path=db_path).memory
        effective_caps = tier_caps if tier_caps is not None else mem_cfg.tier_caps.as_dict()
        effective_threshold = (
            commit_drift_threshold if commit_drift_threshold is not None else mem_cfg.commit_drift_threshold
        )
    else:
        effective_caps = tier_caps
        effective_threshold = commit_drift_threshold

    max_anchored, max_global = effective_caps.get(tier, MEMORY_TIER_CAPS.get(tier, MEMORY_TIER_CAPS["guardrails"]))
    candidate_files = list(
        dict.fromkeys(
            f
            for f in (clean_files or ())
            if isinstance(f, str) and not _has_glob_magic(f) and not _is_unsafe_rel_path(_normalize_target_path(f))
        )
    )
    direct_ids = direct_entity_ids or set()
    expanded_ids = expanded_entity_ids or set()

    # anchored_hits maps mem_id -> (match_group, neg_specificity, scope, matched_by)
    anchored_hits: dict[str, tuple[int, int, str, str]] = {}
    task_fts_ranks: dict[str, int] = {}

    for mem_id, (_title, _rel_fp, fm) in active_by_id.items():
        t_entities, t_paths = _extract_target_lists(fm)
        if not t_entities and not t_paths:
            continue

        best_rank: tuple[int, int, str, str] | None = None
        if candidate_files and t_paths:
            path_score = _best_path_anchor_score(t_paths, candidate_files)
            if path_score > 0:
                best_rank = (0, -path_score, "anchored", "target path match")

        direct_overlap = [e for e in t_entities if e in direct_ids]
        if direct_overlap:
            cand_rank = (0, -1500, "anchored", f"anchored to {', '.join(direct_overlap[:2])}")
            if best_rank is None or cand_rank[:2] < best_rank[:2]:
                best_rank = cand_rank
        else:
            exp_overlap = [e for e in t_entities if e in expanded_ids]
            if exp_overlap:
                cand_rank = (1, -500, "anchored", f"anchored to {', '.join(exp_overlap[:2])}")
                if best_rank is None or cand_rank[:2] < best_rank[:2]:
                    best_rank = cand_rank

        if best_rank is not None:
            anchored_hits[mem_id] = best_rank

    if task and task.strip():
        fts_hits = execute_fts_query(
            conn,
            text=task,
            limit=max_anchored + 2,
            include_types=("agent/memory",),
            use_semantic=use_semantic,
            eidos_dir=eidos_dir,
        )
        for rank_idx, hit in enumerate(fts_hits):
            mid = str(hit.get("id") or "")
            if mid in active_by_id:
                task_fts_ranks.setdefault(mid, rank_idx)
                if mid not in anchored_hits:
                    anchored_hits[mid] = (2, rank_idx, "task_fts", "task FTS match")

    # Honest Empty Slices: never inject untargeted globals when an explicit query matched nothing.
    if has_explicit_query and not has_architectural_matches and not anchored_hits:
        return []

    def _verified_sort_key(fm: dict[str, Any]) -> str:
        return str(fm.get("verified_at") or "")

    def _neg_iso_date(iso_str: str) -> tuple[int, int, int]:
        try:
            d = date.fromisoformat(iso_str)
            return (-d.year, -d.month, -d.day)
        except ValueError:
            return (0, 0, 0)

    anchored_pool = sorted(
        anchored_hits.items(),
        key=lambda kv: (kv[1][0], kv[1][1], _neg_iso_date(_verified_sort_key(active_by_id[kv[0]][2])), kv[0]),
    )
    anchored_pool_ids = {mid for mid, _ in anchored_pool}
    all_global_ids = list(global_ids) if max_global > 0 else []

    batch_ids = list(dict.fromkeys([*anchored_pool_ids, *all_global_ids]))
    batch_inputs = [(mid, active_by_id[mid][2]) for mid in batch_ids]
    if not batch_inputs:
        return []

    catalog_ids = {str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall()}
    freshness_map = resolve_memory_freshness_batch(
        repo_root,
        batch_inputs,
        catalog_ids,
        conn=conn,
        db_path=db_path,
        today=today,
        commit_drift_threshold=effective_threshold,
        eidos_dir=eidos_dir,
    )

    ranked_anchored = sorted(
        anchored_pool,
        key=lambda kv: (
            kv[1][0],
            kv[1][1],
            _FRESHNESS_ORDER.get(freshness_map.get(kv[0], ("fresh", ""))[0], 9),
            _neg_iso_date(_verified_sort_key(active_by_id[kv[0]][2])),
            kv[0],
        ),
    )[:max_anchored]

    selected_anchored_ids = {mid for mid, _ in ranked_anchored}
    remaining_global_ids = [mid for mid in all_global_ids if mid not in selected_anchored_ids]

    ranked_globals = sorted(
        remaining_global_ids,
        key=lambda mid: (
            0 if mid in task_fts_ranks else 1,
            task_fts_ranks.get(mid, 9999),
            _FRESHNESS_ORDER.get(freshness_map.get(mid, ("fresh", ""))[0], 9),
            _neg_iso_date(_verified_sort_key(active_by_id[mid][2])),
            mid,
        ),
    )[:max_global]

    selected_specs: list[tuple[str, str, str]] = [(mid, meta[2], meta[3]) for mid, meta in ranked_anchored] + [
        (mid, "global", "global repository memory") for mid in ranked_globals
    ]

    output: list[dict[str, Any]] = []
    for mid, scope, matched_by in selected_specs:
        title, rel_fp, fm = active_by_id[mid]
        freshness, reason = freshness_map.get(mid, ("fresh", ""))
        entry: dict[str, Any] = {
            "id": mid,
            "title": title,
            "category": str(fm.get("category") or "feedback"),
            "summary": str(fm.get("summary") or title).strip(),
            "freshness": freshness,
            "freshness_reason": reason,
            "file_path": rel_fp,
            "verified_at": str(fm.get("verified_at") or ""),
            "match_scope": scope,
            "matched_by": matched_by,
        }
        if tier in ("guardrails", "deep") and rel_fp:
            with contextlib.suppress(OSError):
                sections = extract_memory_sections((repo_root / rel_fp).read_text(encoding="utf-8", errors="replace"))
                if sections.get("guidance"):
                    entry["guidance"] = sections["guidance"]
                if tier == "deep" and sections.get("why"):
                    entry["why"] = sections["why"]
        output.append(entry)

    return output
