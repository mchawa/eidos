"""Isolated Git-tree snapshot materialization and onboarding governance validation for drift checks."""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from eidos.core.spec.models import ValidationReport
from eidos.core.spec.parser import extract_frontmatter


def _safe_extract_member(tf: tarfile.TarFile, member: tarfile.TarInfo, snap_root: Path, snap_res: Path) -> None:
    """Extract one tar member safely, replacing outside-pointing git symlinks with safe dangling placeholders."""
    try:
        tf.extract(member, path=snap_root, filter="data")
    except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
        if not member.issym():
            raise
        norm_name = member.name.replace("\\", "/")
        raw_parts = [seg for seg in norm_name.split("/") if seg and seg != "."]
        if not raw_parts or norm_name.startswith("/") or any(seg == ".." for seg in raw_parts):
            raise
        cur = snap_res
        for parent_seg in raw_parts[:-1]:
            cur = cur / parent_seg
            if cur.is_symlink():
                raise
            cur.mkdir(exist_ok=True)
        dest = cur / raw_parts[-1]
        os.symlink("__eidos_outside_symlink__", dest)


def _materialize_head_snapshot(
    git_root: Path,
    repo_root: Path,
    target_eidos: Path,
    eidos_rel_prefix: str,
    head_ref: str,
) -> tempfile.TemporaryDirectory[str] | None:
    """Materialize `--head` Git commit tree into an isolated TemporaryDirectory for clean commit-range drift checks."""
    if not repo_root.is_relative_to(git_root) or not target_eidos.is_relative_to(repo_root):
        return None
    tree_spec = head_ref
    if repo_root != git_root:
        sub_rel = str(repo_root.relative_to(git_root)).replace("\\", "/").strip("/")
        if sub_rel:
            tree_spec = f"{head_ref}:{sub_rel}"

    tmp_dir = tempfile.TemporaryDirectory(prefix="eidos_head_")
    snap_root = Path(tmp_dir.name)
    snap_res = snap_root.resolve()
    try:
        proc = subprocess.Popen(
            ["git", "archive", "--format=tar", tree_spec],
            cwd=git_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            for member in tf:
                _safe_extract_member(tf, member, snap_root, snap_res)
        proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        if proc.wait() != 0:
            tmp_dir.cleanup()
            return None
    except (OSError, tarfile.TarError):
        tmp_dir.cleanup()
        return None

    src_cache_db = target_eidos / ".cache" / "index.db"
    snap_eidos = snap_root / eidos_rel_prefix
    if src_cache_db.is_file() and snap_eidos.is_dir():
        with contextlib.suppress(OSError):
            dst_cache_dir = snap_eidos / ".cache"
            dst_cache_dir.mkdir(parents=True, exist_ok=True)
            dst_db = dst_cache_dir / "index.db"
            shutil.copy2(src_cache_db, dst_db)
            with contextlib.suppress(sqlite3.Error), sqlite3.connect(dst_db) as snap_conn:
                snap_conn.execute("UPDATE indexed_files SET mtime = -1.0;")
    return tmp_dir


def _head_tree_has_symlink(git_root: Path, head_ref: str, sub_prefix: str, rel_target: str) -> bool:
    """Return True if any path segment of ``rel_target`` is a Git symlink (mode 120000) in ``head_ref``."""
    parts = [seg for seg in rel_target.replace("\\", "/").split("/") if seg and seg != "."]
    if not parts:
        return False
    check_paths: list[str] = []
    acc: list[str] = [sub_prefix] if sub_prefix else []
    for seg in parts:
        acc.append(seg)
        check_paths.append("/".join(acc))
    try:
        res = subprocess.run(
            ["git", "ls-tree", head_ref, "--", *check_paths],
            cwd=git_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if res.returncode != 0:
        return False
    return any(line.startswith("120000 ") for line in res.stdout.splitlines())


def _load_head_commit_config(
    git_root: Path,
    repo_root: Path,
    eidos_rel_prefix: str,
    head_ref: str,
) -> Any:
    """Load optional <eidos_dir>/eidos.yaml strictly from head_ref via git show (zero working-tree leakage)."""
    from eidos.core.workspace.config import CONFIG_FILENAME, parse_config_yaml_text

    sub_prefix = ""
    if repo_root != git_root and repo_root.is_relative_to(git_root):
        sub_prefix = str(repo_root.relative_to(git_root)).replace("\\", "/").strip("/")
    clean_eidos_rel = eidos_rel_prefix.replace("\\", "/").strip("/")
    parts = [p for p in (sub_prefix, clean_eidos_rel, CONFIG_FILENAME) if p]
    rel_config = "/".join(parts)
    obj_spec = f"{head_ref}:{rel_config}"
    raw_text = ""
    is_regular_blob = True
    try:
        ls_res = subprocess.run(
            ["git", "ls-tree", head_ref, "--", rel_config],
            cwd=git_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if ls_res.returncode == 0:
            ls_line = ls_res.stdout.strip().splitlines()[0] if ls_res.stdout.strip() else ""
            if ls_line and not ls_line.startswith(("100644 ", "100755 ")):
                is_regular_blob = False
            elif ls_line:
                res = subprocess.run(
                    ["git", "show", obj_spec],
                    cwd=git_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                raw_text = res.stdout if res.returncode == 0 else ""
    except OSError:
        raw_text = ""

    if not is_regular_blob:
        cfg_env_only, _errs, _sources = parse_config_yaml_text("", repo_root=None)
        return cfg_env_only

    cfg, errs, sources = parse_config_yaml_text(raw_text, repo_root=None)
    if errs:
        return cfg
    if (
        raw_text
        and sources.get("portal.build.out_dir") == "file"
        and _head_tree_has_symlink(git_root, head_ref, sub_prefix, cfg.portal.build.out_dir)
    ):
        cfg_env_only, _e, _s = parse_config_yaml_text("", repo_root=None)
        return cfg_env_only
    return cfg


def _remap_snapshot_str(text: str, snap_root: Path | None) -> str:
    """Replace any temporary snapshot directory prefix with clean repo-relative paths."""
    if snap_root is None or not text:
        return text
    for cand in (snap_root.resolve(), snap_root):
        prefix_posix = cand.as_posix().rstrip("/")
        if prefix_posix:
            text = text.replace(f"{prefix_posix}/", "")
            text = text.replace(prefix_posix, ".")
        prefix_os = str(cand).rstrip("/\\")
        if prefix_os and prefix_os != prefix_posix:
            text = text.replace(f"{prefix_os}\\", "").replace(f"{prefix_os}/", "")
            text = text.replace(prefix_os, ".")
    return text


def _remap_validation_report(report: ValidationReport, snap_root: Path | None) -> None:
    """Normalize schema_errors, broken_references, and warnings in-place so snapshot paths report as repo-relative."""
    if snap_root is None:
        return
    remapped_errors: dict[str, list[str]] = {}
    for err_key, msgs in report.schema_errors.items():
        clean_key = _remap_snapshot_str(str(err_key), snap_root)
        clean_msgs = [_remap_snapshot_str(m, snap_root) for m in msgs]
        remapped_errors.setdefault(clean_key, []).extend(clean_msgs)
    report.schema_errors = remapped_errors
    report.broken_references = [_remap_snapshot_str(ref, snap_root) for ref in report.broken_references]
    report.warnings = [_remap_snapshot_str(warn, snap_root) for warn in report.warnings]


def _has_eidos_onboarding(eval_root: Path) -> bool:
    """Return True if Eidos Agent Skills or installation manifests exist in eval_root."""
    skills_dir = eval_root / ".agents" / "skills"
    if not skills_dir.is_symlink() and skills_dir.is_dir():
        return True
    for marker_dir in (".agents", ".claude", ".cursor", ".github", ".windsurf", ".aider", ".clinerules"):
        if (eval_root / marker_dir / ".eidos-manifest.json").is_file():
            return True
    return False


def _validate_onboarding_governance(
    *,
    onboarding_files: list[str],
    repo_root: Path,
    eval_root: Path,
    effective_uncommitted: bool,
    fetch_versions_fn: Any,
    validation_report: ValidationReport,
) -> bool:
    """Validate modified onboarding governance artifacts (AGENTS.md, CLAUDE.md, .aider.conf.yml, SKILL.md)."""
    if not onboarding_files:
        return False

    from eidos.plugin.managed_block import find_managed_block, read_managed_block

    onboarding_drift = False
    repo_has_onboarding = _has_eidos_onboarding(eval_root)

    for ob_raw in onboarding_files:
        ob_p = Path(ob_raw)
        if ob_p.is_absolute():
            try:
                ob_rel = ob_p.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                ob_rel = ob_p.as_posix()
        else:
            ob_rel = ob_p.as_posix()

        old_ob, new_ob = fetch_versions_fn(ob_raw)
        disk_ob = eval_root / ob_rel
        disk_text: str | None = None
        if disk_ob.is_file():
            with contextlib.suppress(OSError):
                disk_text = disk_ob.read_text(encoding="utf-8", errors="replace")
        if new_ob is None and effective_uncommitted and disk_text is not None:
            new_ob = disk_text

        low_rel = ob_rel.lower()
        if low_rel == "agents.md":
            had_managed_block = bool(old_ob and "<!-- eidos:begin agents-md" in old_ob)
            if not (repo_has_onboarding or had_managed_block):
                continue
            eff_text = new_ob if new_ob is not None else disk_text
            if eff_text and "<!-- eidos:agents-md off -->" in eff_text:
                continue
            try:
                if disk_ob.is_file() and (new_ob is None or disk_text == new_ob):
                    block = read_managed_block(disk_ob, "agents-md")
                elif eff_text is not None:
                    block = find_managed_block(eff_text, "agents-md")
                else:
                    block = None
            except ValueError as exc:
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    f"Root AGENTS.md managed block is malformed ({exc}). Run 'eidos plugin install <target>' or add '<!-- eidos:agents-md off -->'."
                )
                onboarding_drift = True
                continue
            if block is None or not block.inner.strip():
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    "Root AGENTS.md is missing a valid non-empty '<!-- eidos:begin agents-md ... -->' managed block. Restore via 'eidos plugin install <target>' or add '<!-- eidos:agents-md off -->'."
                )
                onboarding_drift = True

        elif low_rel.endswith("/skill.md"):
            if not repo_has_onboarding:
                continue
            eff_skill = new_ob if new_ob is not None else (disk_text if effective_uncommitted else None)
            if eff_skill is not None:
                fm_sk, _, fm_err = extract_frontmatter(eff_skill)
                if (
                    fm_err
                    or not isinstance(fm_sk, dict)
                    or not str(fm_sk.get("name", "")).strip()
                    or not str(fm_sk.get("description", "")).strip()
                ):
                    validation_report.schema_errors.setdefault(ob_rel, []).append(
                        "Modified Agent Skill file is missing valid YAML frontmatter ('name' and 'description')."
                    )
                    onboarding_drift = True
            else:
                top_dir = Path(ob_rel).parts[0] if Path(ob_rel).parts else ""
                if top_dir and (eval_root / top_dir / ".eidos-manifest.json").is_file():
                    validation_report.schema_errors.setdefault(ob_rel, []).append(
                        "Installed Agent Skill file was deleted while '.eidos-manifest.json' remains active."
                    )
                    onboarding_drift = True

        elif low_rel == "claude.md":
            had_claude = bool(old_ob and "<!-- eidos:begin claude-import" in old_ob)
            claude_active = (eval_root / ".claude" / "skills").exists() or (
                eval_root / ".claude" / ".eidos-manifest.json"
            ).is_file()
            if not (had_claude or claude_active):
                continue
            eff_claude = new_ob if new_ob is not None else disk_text
            if eff_claude and (
                "<!-- eidos:agents-md off -->" in eff_claude or "<!-- eidos:claude-import off -->" in eff_claude
            ):
                continue
            try:
                c_block = (
                    read_managed_block(disk_ob, "claude-import")
                    if disk_ob.is_file() and (new_ob is None or disk_text == new_ob)
                    else (find_managed_block(eff_claude, "claude-import") if eff_claude is not None else None)
                )
            except ValueError as exc:
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    f"Root CLAUDE.md managed block is malformed ({exc})."
                )
                onboarding_drift = True
                continue
            if had_claude and (c_block is None or not c_block.inner.strip()) and "@AGENTS.md" not in (eff_claude or ""):
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    "Root CLAUDE.md wiped its 'claude-import' (@AGENTS.md) managed block while Claude skills are active."
                )
                onboarding_drift = True

        elif low_rel == ".aider.conf.yml":
            had_aider = bool(old_ob and "AGENTS.md" in old_ob)
            aider_active = (eval_root / ".aider" / "skills").exists() or (
                eval_root / ".aider" / ".eidos-manifest.json"
            ).is_file()
            eff_aider = new_ob if new_ob is not None else disk_text
            if had_aider and aider_active and (eff_aider is None or "AGENTS.md" not in eff_aider):
                validation_report.schema_errors.setdefault(ob_rel, []).append(
                    "Modified .aider.conf.yml removed 'AGENTS.md' read configuration while Aider skills are active."
                )
                onboarding_drift = True

    return onboarding_drift
