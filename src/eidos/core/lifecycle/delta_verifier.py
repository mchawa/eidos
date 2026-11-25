"""Stateless Git-baseline and declarative expect: post-condition verifier for SpecDeltas (CMP-DELTA-ENGINE)."""

import contextlib
import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from eidos.core.lifecycle.catalog import (
    _build_spec_catalog_with_invariants,
    find_spec_by_id_or_path,
)
from eidos.core.spec.models import resolve_entity_prefix_meta
from eidos.core.spec.parser import extract_frontmatter, parse_spec_file

SUPPORTED_MUTATION_OPERATIONS: frozenset[str] = frozenset({"add", "modify", "remove", "deprecate"})


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


def _extract_mutation_target(m: dict[str, Any]) -> str:
    """Extract primary target identifier or path from a composable mutation dictionary."""
    direct_target = m.get("target")
    if direct_target and isinstance(direct_target, str) and direct_target.strip():
        return direct_target.strip()
    return "unspecified_target"


def normalize_delta_mutation(m: dict[str, Any]) -> dict[str, Any]:
    """Normalize a composable SpecDelta mutation dictionary (operation + target)."""
    if not isinstance(m, dict):
        return {}
    norm: dict[str, Any] = dict(m)
    raw_op = str(norm.get("operation") or "").strip().lower()
    raw_target = str(norm.get("target") or "").strip()

    if raw_op:
        norm["operation"] = raw_op
    if raw_target:
        norm["target"] = raw_target

    if raw_target:
        t_meta = resolve_entity_prefix_meta(raw_target)
        if t_meta is not None:
            norm["entity_type"] = t_meta[1]

    return norm


def _digest_dict(data: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hex digest for a frontmatter sub-dictionary."""
    return hashlib.sha256(
        orjson.dumps(data, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)
    ).hexdigest()


def _resolve_git_repo_root(eidos_dir: Path) -> Path | None:
    """Return the enclosing Git repository root for eidos_dir across ancestor directories (including monorepo sub-projects)."""
    from eidos.core.workspace.project import find_git_root

    resolved = eidos_dir.resolve()
    start = resolved if resolved.exists() else resolved.parent
    return find_git_root(start)


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


def _resolve_delta_lookup(eidos_dir: Path, target: str) -> tuple[Path | None, str | None]:
    """Resolve a SpecDelta target across deltas/ and eidos_dir, capturing ambiguity errors cleanly."""
    try:
        fp = find_spec_by_id_or_path(eidos_dir / "deltas", target)
        if not fp:
            fp = find_spec_by_id_or_path(eidos_dir, target)
        return fp, None
    except ValueError as exc:
        return None, str(exc)


def _digest_normalized_bytes(raw_bytes: bytes) -> str:
    """Compute SHA-256 digest after normalizing CRLF line endings to LF."""
    return hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest()


def _is_shallow_git_repo(repo_root: Path) -> bool:
    """Return True if repo_root is a shallow Git repository (--depth / fetch-depth)."""
    from eidos.vcs.diff import _is_shallow_repository

    return _is_shallow_repository(repo_root)


def _git_rel_path(
    path: Path,
    repo_root: Path,
    snapshot_root: Path | None = None,
    git_sub_prefix: str = "",
) -> str | None:
    """Resolve `path` relative to `repo_root` (remapping `snapshot_root` prefixes for commit-range drift snapshots)."""
    resolved = path.resolve()
    if snapshot_root is not None:
        try:
            sub_rel = resolved.relative_to(snapshot_root.resolve()).as_posix()
            return f"{git_sub_prefix}/{sub_rel}".strip("/") if git_sub_prefix else sub_rel
        except ValueError:
            pass
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return None


def _resolve_git_baseline_sha(
    eidos_dir: Path,
    delta_file: Path,
    target_key: str,
    target_file: Path | None,
    *,
    rev_cache: dict[Path, list[str]] | None = None,
    blob_cache: dict[tuple[str, str], tuple[bytes | None, dict[str, Any] | None]] | None = None,
    git_repo_root: Path | None = None,
    snapshot_root: Path | None = None,
    git_sub_prefix: str = "",
) -> str | None:
    """Recover the pre-delta baseline SHA-256 of target_key from vcs.base_commit or Git history."""
    if target_file is None or not target_file.exists():
        return None

    repo_root = git_repo_root.resolve() if git_repo_root is not None else _resolve_git_repo_root(eidos_dir)
    if repo_root is None:
        return None

    rel_target = _git_rel_path(target_file, repo_root, snapshot_root=snapshot_root, git_sub_prefix=git_sub_prefix)
    if rel_target is None:
        return None

    def _compute_fallback_revs() -> list[str]:
        fb_revs: list[str] = []
        is_shallow = _is_shallow_git_repo(repo_root)
        delta_in_head = False

        def _add_fb(cand: str | None) -> None:
            if cand and cand not in fb_revs:
                fb_revs.append(cand)

        with contextlib.suppress(ValueError, OSError, subprocess.SubprocessError):
            rel_delta = _git_rel_path(delta_file, repo_root, snapshot_root=snapshot_root, git_sub_prefix=git_sub_prefix)
            if rel_delta is None:
                raise ValueError("delta outside repo_root")
            if is_shallow:
                cat_res = subprocess.run(
                    ["git", "-C", str(repo_root), "cat-file", "-e", f"HEAD:{rel_delta}"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                delta_in_head = cat_res.returncode == 0
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
                elif not (is_shallow and delta_in_head):
                    _add_fb(first_commit)

        if not fb_revs and not (is_shallow and delta_in_head):
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

        if target_key.startswith("INV-"):
            if hist_fm is None:
                parsed_fm, _, _ = extract_frontmatter(raw_bytes.decode("utf-8", errors="replace"))
                hist_fm = parsed_fm if isinstance(parsed_fm, dict) else {}
                if blob_cache is not None:
                    blob_cache[cache_key] = (raw_bytes, hist_fm)
            if hist_fm:
                invs = list(hist_fm.get("invariants", []) or [])
                tech = hist_fm.get("technical")
                if isinstance(tech, dict) and isinstance(tech.get("invariants"), list):
                    invs.extend(tech["invariants"])
                for inv in invs:
                    if isinstance(inv, dict) and inv.get("id") == target_key:
                        return True, _digest_dict(inv)
            return True, None
        return True, _digest_normalized_bytes(raw_bytes)

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
        "rule",
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
            if "." in k:
                segs = k.split(".")
                if len(segs) < 2 or any(not seg for seg in segs):
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} has malformed dotted key path")
                    return False
            found_key, actual = _resolve_frontmatter_path(target_dict, k)
            leaf_key = k.rsplit(".", 1)[-1]
            is_prose = leaf_key in _PROSE_EXPECT_FIELDS
            if exp_v is None:
                if found_key and actual is not None:
                    if diagnostics is not None:
                        diagnostics.append(f"frontmatter.{k} expected None (absent/deleted), got {actual!r}")
                    return False
                continue
            if not found_key:
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


def _is_shallow_committed_delta(
    eidos_dir: Path,
    delta_file: Path,
    *,
    git_repo_root: Path | None = None,
    snapshot_root: Path | None = None,
    git_sub_prefix: str = "",
) -> bool:
    """Return True if delta_file is committed in a shallow Git clone whose pre-delta parent commit is grafted away."""
    repo_root = git_repo_root.resolve() if git_repo_root is not None else _resolve_git_repo_root(eidos_dir)
    if repo_root is None or not _is_shallow_git_repo(repo_root):
        return False
    with contextlib.suppress(ValueError, OSError, subprocess.SubprocessError):
        rel_delta = _git_rel_path(delta_file, repo_root, snapshot_root=snapshot_root, git_sub_prefix=git_sub_prefix)
        if rel_delta is None:
            return False
        cat_res = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-e", f"HEAD:{rel_delta}"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if cat_res.returncode != 0:
            return False
        log_res = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--diff-filter=A", "--format=%H", "--", rel_delta],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        commits = [ln.strip() for ln in log_res.stdout.splitlines() if ln.strip()]
        earliest = commits[-1] if commits else "HEAD"
        parent_res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", f"{earliest}~1"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return parent_res.returncode != 0
    return False


def _extract_decision_ids(raw_decisions: Any) -> set[str]:
    """Extract non-empty DEC-* identifiers from a frontmatter decisions list."""
    ids: set[str] = set()
    if isinstance(raw_decisions, list):
        for entry in raw_decisions:
            if isinstance(entry, dict):
                dec_id = str(entry.get("id") or "").strip()
                if dec_id:
                    ids.add(dec_id)
    return ids


def _extract_expected_decision_ids(mutation: dict[str, Any]) -> set[str]:
    """Extract expected DEC-* IDs declared under mutation.expect.frontmatter.decisions.id."""
    expect = mutation.get("expect")
    if not isinstance(expect, dict):
        return set()
    exp_fm = expect.get("frontmatter")
    if not isinstance(exp_fm, dict):
        return set()
    ids: set[str] = set()
    raw_dec_ids = exp_fm.get("decisions.id")
    if isinstance(raw_dec_ids, str) and raw_dec_ids.strip():
        ids.add(raw_dec_ids.strip())
    elif isinstance(raw_dec_ids, list):
        for item in raw_dec_ids:
            if isinstance(item, str) and item.strip():
                ids.add(item.strip())
    raw_decs = exp_fm.get("decisions")
    ids.update(_extract_decision_ids(raw_decs))
    return ids


def verify_delta_mutations(
    eidos_dir: Path,
    delta_id_or_path: str,
    *,
    git_repo_root: Path | None = None,
    snapshot_root: Path | None = None,
    git_sub_prefix: str = "",
) -> tuple[bool, str, DeltaVerificationReport | None]:
    """Verify that all composable semantic mutations declared in a SpecDelta are fulfilled in living specifications."""
    file_path, amb_err = _resolve_delta_lookup(eidos_dir, delta_id_or_path)
    if amb_err:
        return False, amb_err, None

    if not file_path or not file_path.exists():
        return False, f"SpecDelta '{delta_id_or_path}' not found.", None

    doc = parse_spec_file(file_path)
    if doc.doc_type != "delta/mutation":
        return False, f"Document at '{file_path}' is not a SpecDelta (type: {doc.doc_type}).", None

    raw_mutations = doc.frontmatter.get("mutations", [])
    if not isinstance(raw_mutations, list):
        raw_mutations = []
    if not raw_mutations:
        empty_report = DeltaVerificationReport(
            delta_id=doc.doc_id,
            delta_file=file_path,
            total_mutations=0,
            verified_count=0,
            is_verified=False,
            mutations=[],
        )
        return False, f"SpecDelta '{doc.doc_id}' declares zero mutations (mutations: []).", empty_report

    delta_status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
    shallow_committed = _is_shallow_committed_delta(
        eidos_dir,
        file_path,
        git_repo_root=git_repo_root,
        snapshot_root=snapshot_root,
        git_sub_prefix=git_sub_prefix,
    )
    specs_by_id, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
    invariants = set(invariant_map.keys())
    rev_cache: dict[Path, list[str]] = {}
    blob_cache: dict[tuple[str, str], tuple[bytes | None, dict[str, Any] | None]] = {}

    delta_decision_ids = _extract_decision_ids(doc.frontmatter.get("decisions"))

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

        overlapping_decisions = sorted(delta_decision_ids & _extract_expected_decision_ids(m))
        if overlapping_decisions:
            verified = False
            status = "DUPLICATE_DECISION"
            failure_reason = (
                f"Duplicate decision ID(s) {', '.join(overlapping_decisions)} declared in both this SpecDelta's "
                f"'decisions' frontmatter and mutation '{target}' expect.frontmatter.decisions.id. "
                "Record planned DEC-* decisions solely on the owning CNT-*/CMP-* specification."
            )

        elif op not in SUPPORTED_MUTATION_OPERATIONS:
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
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, invariant_map, diagnostics=diag
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                    failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                else:
                    verified = True
                    status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"
                failure_reason = f"Invariant '{lookup_target}' is still present."

        elif op == "deprecate" or (op == "remove" and entity_kind in ("capability", "policy")):
            if lookup_target not in specs_by_id and not target_path_exists:
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, invariant_map, target_file=target_path_resolved, diagnostics=diag
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                    failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                else:
                    verified = True
                    status = "ABSENT_AS_EXPECTED"
            else:
                doc_fm = specs_by_id[lookup_target][2] if lookup_target in specs_by_id else {}
                if not doc_fm and target_path_resolved and target_path_resolved.exists():
                    doc_fm = parse_spec_file(target_path_resolved).frontmatter or {}
                if str(doc_fm.get("status", "")).lower() in ("deprecated", "retired"):
                    decl_ok = _evaluate_declarative_postcondition(
                        m,
                        lookup_target,
                        specs_by_id,
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
                        status = "DEPRECATED"
                else:
                    verified = False
                    status = "ACTIVE_NOT_DEPRECATED"
                    failure_reason = (
                        f"Target '{lookup_target}' has status '{doc_fm.get('status')}', expected deprecated/retired."
                    )

        elif op == "remove":
            still_present = lookup_target in specs_by_id or target_path_exists
            if not still_present:
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, invariant_map, target_file=target_path_resolved, diagnostics=diag
                )
                if decl_ok is False:
                    verified = False
                    status = "UNMODIFIED"
                    failure_reason = "; ".join(diag) if diag else "Declarative expect post-condition failed."
                else:
                    verified = True
                    status = "ABSENT_AS_EXPECTED"
            else:
                verified = False
                status = "STILL_PRESENT"
                failure_reason = f"Target '{lookup_target}' is still present in living specifications."

        # 2. Addition Operations
        elif op == "add" and entity_kind == "invariant":
            if lookup_target in invariants:
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, invariant_map, diagnostics=diag
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
            found = lookup_target in specs_by_id or target_path_exists
            if found:
                decl_ok = _evaluate_declarative_postcondition(
                    m,
                    lookup_target,
                    specs_by_id,
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
            if lookup_target not in invariant_map:
                verified = False
                status = "MISSING"
                failure_reason = f"Invariant '{lookup_target}' not found on parent specification."
            else:
                inv_file, inv_dict = invariant_map[lookup_target]
                inv_sha = _digest_dict(inv_dict)
                decl_ok = _evaluate_declarative_postcondition(
                    m, lookup_target, specs_by_id, invariant_map, diagnostics=diag
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
                            git_repo_root=git_repo_root,
                            snapshot_root=snapshot_root,
                            git_sub_prefix=git_sub_prefix,
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
                        git_repo_root=git_repo_root,
                        snapshot_root=snapshot_root,
                        git_sub_prefix=git_sub_prefix,
                    )
                    mod_ok = (
                        (inv_sha != base_sha)
                        if base_sha is not None
                        else (delta_status == "applied" or shallow_committed)
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

            if target_file is None or not target_file.exists():
                verified = False
                status = "MISSING"
                failure_reason = f"Target specification '{lookup_target}' not found."
            else:
                cur_sha = _digest_normalized_bytes(target_file.read_bytes())

                decl_ok = _evaluate_declarative_postcondition(
                    m,
                    lookup_target,
                    specs_by_id,
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
                            git_repo_root=git_repo_root,
                            snapshot_root=snapshot_root,
                            git_sub_prefix=git_sub_prefix,
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
                        git_repo_root=git_repo_root,
                        snapshot_root=snapshot_root,
                        git_sub_prefix=git_sub_prefix,
                    )
                    mod_ok = (
                        (cur_sha != base_sha)
                        if base_sha is not None
                        else (delta_status == "applied" or shallow_committed)
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


def detect_delta_conflicts(
    eidos_dir: Path,
    *,
    include_blast_radius: bool = True,
) -> dict[str, Any]:
    """Detect direct target collisions and 1-hop blast-radius overlaps across open SpecDeltas (DLT-2026-156)."""
    from eidos.core.lifecycle.delta_engine import normalize_delta_mutation

    deltas_dir = eidos_dir / "deltas"
    active_statuses = {"proposed", "in_review", "approved"}
    specs_by_id, _ = _build_spec_catalog_with_invariants(eidos_dir)

    active_records: list[dict[str, Any]] = []
    if deltas_dir.is_dir():
        for fp in sorted(deltas_dir.glob("*.adoc")):
            if not fp.is_file() or fp.is_symlink() or fp.name.lower() == "readme.adoc":
                continue
            doc = parse_spec_file(fp)
            if doc.doc_type != "delta/mutation":
                continue
            status = str(doc.frontmatter.get("status", "proposed")).strip().lower()
            if status not in active_statuses:
                continue
            raw_muts = doc.frontmatter.get("mutations") or []
            if not isinstance(raw_muts, list):
                raw_muts = []
            targets_ops: dict[str, set[str]] = {}
            for raw_m in raw_muts:
                if not isinstance(raw_m, dict):
                    continue
                m = normalize_delta_mutation(raw_m)
                op = str(m.get("operation", "")).strip().lower()
                raw_t = _extract_mutation_target(m)
                if not raw_t:
                    continue
                canon_t, _, _ = _resolve_target_path_and_id(raw_t, eidos_dir, specs_by_id)
                targets_ops.setdefault(canon_t, set()).add(op or "modify")
            active_records.append(
                {
                    "id": doc.doc_id,
                    "title": doc.title,
                    "status": status,
                    "file": fp.name,
                    "targets": targets_ops,
                    "neighbors": set(),
                }
            )

    active_records.sort(key=lambda r: r["id"])

    if include_blast_radius and len(active_records) >= 2:
        from eidos.core.index.graph_index import get_architectural_index
        from eidos.core.workspace.project import resolve_spec_and_repo_roots

        spec_root, repo_root = resolve_spec_and_repo_roots(eidos_dir)
        idx = get_architectural_index(repo_root=repo_root, eidos_dir=spec_root)
        excluded_neighbor_types = {
            "delta/mutation",
            "product/exploration",
            "product/policy",
            "product/persona",
            "agent/memory",
        }
        for rec in active_records:
            t_ids = sorted(rec["targets"].keys())
            if not t_ids:
                continue
            with contextlib.suppress(Exception):
                neighbors = idx.get_k_hop_neighborhood(t_ids, max_depth=1)
                rec["neighbors"] = {
                    str(n["id"])
                    for n in neighbors
                    if n.get("id")
                    and str(n["id"]) not in rec["targets"]
                    and str(n.get("type") or "") not in excluded_neighbor_types
                }

    destructive_ops = {"remove", "deprecate"}
    direct_conflicts: list[dict[str, Any]] = []
    blast_overlaps: list[dict[str, Any]] = []

    for i in range(len(active_records)):
        rec_a = active_records[i]
        targets_a = set(rec_a["targets"].keys())
        for j in range(i + 1, len(active_records)):
            rec_b = active_records[j]
            targets_b = set(rec_b["targets"].keys())
            shared_targets = sorted(targets_a & targets_b)
            for tgt in shared_targets:
                ops_a = sorted(rec_a["targets"][tgt])
                ops_b = sorted(rec_b["targets"][tgt])
                is_high = bool((set(ops_a) & destructive_ops) or (set(ops_b) & destructive_ops))
                severity = "high" if is_high else "medium"
                reason = (
                    f"Incompatible operations ({'/'.join(ops_a)} vs {'/'.join(ops_b)}) on '{tgt}'"
                    if is_high
                    else f"Concurrent {'/'.join(ops_a)} and {'/'.join(ops_b)} on '{tgt}'"
                )
                direct_conflicts.append(
                    {
                        "delta_a": rec_a["id"],
                        "delta_b": rec_b["id"],
                        "target": tgt,
                        "operations_a": ops_a,
                        "operations_b": ops_b,
                        "severity": severity,
                        "reason": reason,
                    }
                )

            if include_blast_radius:
                shared_neighbors = sorted((rec_a["neighbors"] & rec_b["neighbors"]) - set(shared_targets))
                if shared_neighbors:
                    blast_overlaps.append(
                        {
                            "delta_a": rec_a["id"],
                            "delta_b": rec_b["id"],
                            "shared_entities": shared_neighbors,
                            "severity": "info",
                        }
                    )

    return {
        "active_deltas_scanned": len(active_records),
        "include_blast_radius": include_blast_radius,
        "has_direct_conflicts": bool(direct_conflicts),
        "direct_target_conflicts": direct_conflicts,
        "blast_radius_overlaps": blast_overlaps,
        "summary": {
            "high": sum(1 for c in direct_conflicts if c["severity"] == "high"),
            "medium": sum(1 for c in direct_conflicts if c["severity"] == "medium"),
            "blast_radius_overlaps": len(blast_overlaps),
        },
    }
