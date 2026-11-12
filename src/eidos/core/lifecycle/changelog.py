"""Zero-dependency automated changelog generator powered by applied SpecDeltas and Git tags (INV-DELTA-CHANGELOG-GENERATION, POL-004)."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.spec.parser import extract_frontmatter, parse_spec_file


def _frontmatter_status_and_id(raw_text: str) -> tuple[str, str]:
    """Extract normalized (status_lower, id_upper) strictly from YAML frontmatter."""
    fm, _, err = extract_frontmatter(raw_text)
    if err or not isinstance(fm, dict):
        return "", ""
    status = str(fm.get("status") or "").strip().lower()
    doc_id = str(fm.get("id") or "").strip().upper()
    return status, doc_id


def _git_batch_read_blobs(repo_root: Path, specs: list[str]) -> dict[str, str | None]:
    """Read multiple '<rev>:<path>' git blobs in a single `git cat-file --batch` call."""
    if not specs:
        return {}
    unique_specs = list(dict.fromkeys(specs))
    results: dict[str, str | None] = {}
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            input=("\n".join(unique_specs) + "\n").encode("utf-8"),
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0 and not proc.stdout:
            return {}
        buf = proc.stdout
        pos = 0
        n = len(buf)
        for spec in unique_specs:
            nl = buf.find(b"\n", pos)
            if nl == -1:
                break
            header = buf[pos:nl].decode("utf-8", errors="replace")
            pos = nl + 1
            if header.endswith(" missing"):
                results[spec] = None
                continue
            parts = header.rsplit(" ", 2)
            if len(parts) != 3:
                break
            try:
                size = int(parts[2])
            except ValueError:
                break
            blob_bytes = buf[pos : pos + size]
            pos += size
            if pos < n and buf[pos : pos + 1] == b"\n":
                pos += 1
            results[spec] = blob_bytes.decode("utf-8", errors="replace")
    return results


@dataclass
class ReleaseSection:
    """A semantic category within a release (Keep a Changelog standard)."""

    tag: str
    version_label: str
    date: str
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [
                self.added,
                self.changed,
                self.deprecated,
                self.removed,
                self.fixed,
                self.security,
            ]
        )

    def to_markdown(self, repo_url: str = "") -> str:
        """Render section as standard Keep a Changelog markdown."""
        lines: list[str] = []
        if self.tag.lower() == "unreleased":
            lines.append("## [Unreleased]")
        else:
            date_str = f" - {self.date}" if self.date else ""
            lines.append(f"## [{self.version_label}]{date_str}")

        lines.append("")

        groups = [
            ("Added", self.added),
            ("Changed", self.changed),
            ("Deprecated", self.deprecated),
            ("Removed", self.removed),
            ("Fixed", self.fixed),
            ("Security", self.security),
        ]

        for heading, items in groups:
            if items:
                lines.append(f"### {heading}")
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _resolve_default_repo_url(root: Path) -> str:
    """Resolve HTTPS repository URL from git remote origin if available."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        raw = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if raw.startswith("git@") and ":" in raw:
            host_part, path_part = raw.split(":", 1)
            host = host_part.split("@", 1)[-1]
            raw = f"https://{host}/{path_part}"
        if raw.endswith(".git"):
            raw = raw[:-4]
        return raw.rstrip("/")
    return ""


class ChangelogGenerator:
    """Compiles living SpecDeltas and Git release history into a verified CHANGELOG."""

    def __init__(
        self,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
        repo_url: str | None = None,
    ) -> None:
        if repo_root is not None:
            self.root = Path(repo_root).resolve()
        elif eidos_dir is not None:
            edir_res = Path(eidos_dir).resolve()
            self.root = (
                edir_res.parent
                if (edir_res.name == "eidos" or (edir_res / "deltas").exists() or (edir_res / "system.adoc").exists())
                else Path.cwd().resolve()
            )
        else:
            self.root = Path.cwd().resolve()
        self.eidos_dir = Path(eidos_dir).resolve() if eidos_dir else (self.root / "eidos").resolve()
        resolved_url = repo_url if repo_url is not None else _resolve_default_repo_url(self.root)
        self.repo_url = resolved_url.rstrip("/")
        self._tag_ancestors_cache: dict[str, set[str]] = {}

    def get_git_tags(self) -> list[dict[str, str]]:
        """Return list of git tags in chronological order: [{name, date, commit}]."""
        try:
            cmd = [
                "git",
                "for-each-ref",
                "--sort=creatordate",
                "--format=%(refname:short)|%(creatordate:short)|%(objectname)",
                "refs/tags",
            ]
            output = subprocess.check_output(cmd, cwd=self.root, text=True, stderr=subprocess.DEVNULL)
            tags: list[dict[str, str]] = []
            for line in output.strip().splitlines():
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    tags.append({"name": parts[0], "date": parts[1], "commit": parts[2]})
            return tags
        except (OSError, subprocess.SubprocessError):
            return []

    def _resolve_project_name(self) -> str:
        """Resolve human-readable project name from SYS-* in system.adoc, falling back to repo directory name."""
        candidates: list[Path] = []
        sys_file = self.eidos_dir / "system.adoc"
        if sys_file.is_file() and not sys_file.is_symlink():
            candidates.append(sys_file)
        if self.eidos_dir.is_dir():
            for p in sorted(self.eidos_dir.glob("*.adoc")):
                if p not in candidates and p.is_file() and not p.is_symlink():
                    candidates.append(p)
        for cand in candidates:
            with contextlib.suppress(Exception):
                doc = parse_spec_file(cand)
                if doc.doc_type == "product/system":
                    title = str(doc.frontmatter.get("title") or doc.title or "").strip()
                    if title:
                        return title
        return self.root.name or "this project"

    def _batch_resolve_delta_commits(self, deltas_dir: Path) -> tuple[dict[Path, str], dict[str, str]]:
        """Resolve earliest Git commit SHA where YAML frontmatter status: applied was set for delta files."""
        commits_by_path: dict[Path, str] = {}
        commits_by_id: dict[str, str] = {}
        with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
            rel_deltas = str(deltas_dir.resolve().relative_to(self.root)).replace("\\", "/")
            output = subprocess.check_output(
                [
                    "git",
                    "log",
                    "--reverse",
                    "--name-only",
                    "--format=COMMIT:%H",
                    "--",
                    rel_deltas,
                ],
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            events: list[tuple[str, str]] = []
            current_sha = ""
            for raw_line in output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("COMMIT:"):
                    current_sha = line[7:].strip()
                elif current_sha and line.lower().endswith(".adoc"):
                    events.append((current_sha, line))

            blob_specs = [f"{sha}:{rel_line}" for sha, rel_line in events]
            blobs = _git_batch_read_blobs(self.root, blob_specs)

            applied_by_fname: dict[str, str] = {}
            paths_by_fname: dict[str, set[Path]] = {}
            ids_by_fname: dict[str, set[str]] = {}

            for sha, rel_line in events:
                blob_text = blobs.get(f"{sha}:{rel_line}")
                if blob_text is None:
                    continue
                fname = Path(rel_line).name
                fname_key = fname.lower()
                full_p = (self.root / rel_line).resolve()
                hot_p = (deltas_dir / fname).resolve()
                archive_p = (deltas_dir / "archive" / fname).resolve()
                paths_by_fname.setdefault(fname_key, set()).update((full_p, hot_p, archive_p))

                fm_status, fm_id = _frontmatter_status_and_id(blob_text)
                if fm_id:
                    ids_by_fname.setdefault(fname_key, set()).add(fm_id)
                if fm_status == "applied":
                    applied_by_fname.setdefault(fname_key, sha)
                    if fm_id:
                        commits_by_id.setdefault(fm_id, applied_by_fname[fname_key])
                elif fm_status:
                    applied_by_fname.pop(fname_key, None)
                    if fm_id:
                        commits_by_id.pop(fm_id, None)

            for fname_key, sha in applied_by_fname.items():
                for p in paths_by_fname.get(fname_key, set()):
                    commits_by_path[p] = sha
                for fid in ids_by_fname.get(fname_key, set()):
                    commits_by_id.setdefault(fid, sha)

            # Detect deltas that transitioned to frontmatter status: applied only in the uncommitted working tree
            diff_names_out = subprocess.check_output(
                ["git", "diff", "HEAD", "--name-only", "--", rel_deltas],
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            dirty_rel_files = [ln.strip() for ln in diff_names_out.splitlines() if ln.strip().lower().endswith(".adoc")]
            if dirty_rel_files:
                head_specs: list[str] = []
                dirty_candidates: list[tuple[str, list[str]]] = []
                for rel_f in dirty_rel_files:
                    fname = Path(rel_f).name
                    cands = list(
                        dict.fromkeys(
                            [
                                rel_f.replace("\\", "/"),
                                f"{rel_deltas}/{fname}",
                                f"{rel_deltas}/archive/{fname}",
                            ]
                        )
                    )
                    row_specs = [f"HEAD:{c}" for c in cands]
                    head_specs.extend(row_specs)
                    dirty_candidates.append((rel_f, row_specs))
                head_blobs = _git_batch_read_blobs(self.root, head_specs)
                for rel_f, row_specs in dirty_candidates:
                    was_applied_at_head = False
                    head_ids: set[str] = set()
                    for sp in row_specs:
                        htext = head_blobs.get(sp)
                        if htext is not None:
                            hst, hid = _frontmatter_status_and_id(htext)
                            if hid:
                                head_ids.add(hid)
                            if hst == "applied":
                                was_applied_at_head = True
                    if not was_applied_at_head:
                        fname = Path(rel_f).name
                        fname_key = fname.lower()
                        for p in (
                            (self.root / rel_f).resolve(),
                            (deltas_dir / fname).resolve(),
                            (deltas_dir / "archive" / fname).resolve(),
                            *paths_by_fname.get(fname_key, set()),
                        ):
                            commits_by_path.pop(p, None)
                        disk_p = (self.root / rel_f).resolve()
                        if disk_p.is_file():
                            with contextlib.suppress(OSError):
                                _, disk_id = _frontmatter_status_and_id(disk_p.read_text(encoding="utf-8"))
                                if disk_id:
                                    head_ids.add(disk_id)
                        for hid in head_ids | ids_by_fname.get(fname_key, set()):
                            commits_by_id.pop(hid, None)
        return commits_by_path, commits_by_id

    def _get_tag_ancestors(self, tag_name: str) -> set[str]:
        """Return cached set of all commit SHAs reachable from tag_name via a single git rev-list query."""
        if tag_name not in self._tag_ancestors_cache:
            ancestors: set[str] = set()
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                output = subprocess.check_output(
                    ["git", "rev-list", tag_name],
                    cwd=self.root,
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                ancestors = {ln.strip() for ln in output.splitlines() if ln.strip()}
            self._tag_ancestors_cache[tag_name] = ancestors
        return self._tag_ancestors_cache[tag_name]

    def get_applied_deltas(self) -> list[dict[str, Any]]:
        """Scan eidos/deltas and return only applied SpecDeltas (status == 'applied') with parsed frontmatter and git commits."""
        deltas_dir = self.eidos_dir / "deltas"
        if not deltas_dir.exists():
            return []

        delta_files = sorted(
            [p for p in deltas_dir.rglob("*.adoc") if p.is_file() and not p.is_symlink()],
            key=lambda p: (
                0 if "archive" not in p.relative_to(deltas_dir).parts else 1,
                p.name.lower(),
                str(p.relative_to(deltas_dir)).lower(),
            ),
        )
        batched_commits, commits_by_id = self._batch_resolve_delta_commits(deltas_dir)

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for df in delta_files:
            try:
                doc = parse_spec_file(df)
                if doc.doc_type != "delta/mutation" and not (
                    doc.doc_type == "unknown" and df.name.lower().startswith("dlt-")
                ):
                    continue
                fm = doc.frontmatter
                status = str(fm.get("status", "proposed")).strip().lower()
                if status != "applied":
                    continue
                delta_id = str(fm.get("id") or df.stem.upper()).strip()
                canonical_id = delta_id.upper()
                if canonical_id in seen_ids:
                    continue
                seen_ids.add(canonical_id)

                title = str(fm.get("title") or "")
                intent = fm.get("intent") or {}
                kind = str(intent.get("kind") or "feature") if isinstance(intent, dict) else "feature"
                motivation = (intent.get("motivation") or "") if isinstance(intent, dict) else ""
                if isinstance(motivation, str):
                    # Extract first sentence or summary (avoiding splits on 'vs.' or SemVer '2.0.0')
                    clean_mot = re.sub(r"\s+", " ", motivation).strip()
                    sentences = re.split(r"(?<!\bvs)(?<!\d\.\d)(?<=[.!?])\s+(?=[A-Z`\"'(])", clean_mot)
                    summary = sentences[0] if sentences else clean_mot
                else:
                    summary = ""

                commit = batched_commits.get(df.resolve()) or commits_by_id.get(canonical_id, "")

                results.append(
                    {
                        "id": delta_id,
                        "title": title,
                        "status": status,
                        "kind": kind,
                        "mutations": fm.get("mutations") or [],
                        "summary": summary,
                        "motivation": motivation,
                        "file_path": df,
                        "relative_path": df.relative_to(self.root) if df.is_relative_to(self.root) else df,
                        "commit": commit,
                    }
                )
            except Exception:
                continue

        results.sort(key=lambda item: str(item["id"]).lower())
        return results

    def _resolve_delta_release(self, commit: str, tags: list[dict[str, str]]) -> str:
        """Find the earliest release tag containing the given commit using cached batched tag ancestry sets."""
        if not commit or not tags:
            return "Unreleased"

        for tag in tags:
            if commit in self._get_tag_ancestors(tag["name"]):
                return tag["name"]

        return "Unreleased"

    def _get_commits_between(self, prev_ref: str | None, target_ref: str) -> list[str]:
        """Fetch notable conventional commit subjects between two git revisions."""
        try:
            rev_range = f"{prev_ref}..{target_ref}" if prev_ref else target_ref
            cmd = ["git", "log", rev_range, "--no-merges", "--format=%s"]
            raw = subprocess.check_output(cmd, cwd=self.root, text=True, stderr=subprocess.DEVNULL)
            commits = [c.strip() for c in raw.splitlines() if c.strip()]
            return commits
        except (OSError, subprocess.SubprocessError):
            return []

    def generate_changelog(self, include_unreleased: bool = True) -> str:
        """Compile a full Keep a Changelog document from living SpecDeltas and git tags."""
        tags = self.get_git_tags()
        deltas = self.get_applied_deltas()

        # Group deltas by matched tag name (e.g. "v0.7.0", "Unreleased")
        deltas_by_tag: dict[str, list[dict[str, Any]]] = {"Unreleased": []}
        for t in tags:
            deltas_by_tag[t["name"]] = []

        for d in deltas:
            rel = self._resolve_delta_release(d["commit"], tags)
            deltas_by_tag.setdefault(rel, []).append(d)

        # Build ReleaseSection instances
        sections: list[ReleaseSection] = []

        # 1. Unreleased section
        unreleased_deltas = deltas_by_tag.get("Unreleased", [])
        latest_tag = tags[-1]["name"] if tags else None
        unreleased_commits = self._get_commits_between(latest_tag, "HEAD")

        sec_unreleased = ReleaseSection(tag="Unreleased", version_label="Unreleased", date="")
        for d in unreleased_deltas:
            self._add_delta_to_section(sec_unreleased, d)

        # Also add unreleased conventional commits that don't duplicate deltas
        for msg in unreleased_commits:
            self._add_commit_to_section(sec_unreleased, msg, existing_deltas=unreleased_deltas)

        if include_unreleased and not sec_unreleased.is_empty():
            sections.append(sec_unreleased)

        # 2. Tagged releases (from newest to oldest)
        for i, t in enumerate(reversed(tags)):
            t_name = t["name"]
            t_date = t["date"]
            v_label = t_name.lstrip("v")
            sec = ReleaseSection(tag=t_name, version_label=v_label, date=t_date)

            tag_deltas = deltas_by_tag.get(t_name, [])
            for d in tag_deltas:
                self._add_delta_to_section(sec, d)

            # If tag has few or no deltas, backfill from conventional commits
            tag_idx = len(tags) - 1 - i
            prev_tag = tags[tag_idx - 1]["name"] if tag_idx > 0 else None
            tag_commits = self._get_commits_between(prev_tag, t_name)

            for msg in tag_commits:
                self._add_commit_to_section(sec, msg, existing_deltas=tag_deltas)

            if not sec.is_empty():
                sections.append(sec)

        # Assemble markdown document
        project_name = self._resolve_project_name()
        header = (
            "# Changelog\n\n"
            f"All notable changes to {project_name} are documented in this file.\n\n"
            "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
            "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n\n"
            "Every major feature and architectural mutation is directly backed by an immutable\n"
            "**SpecDelta (`DLT-*`)** stored in the living specifications catalog.\n\n"
            "---\n\n"
        )

        body = "\n".join([s.to_markdown(self.repo_url) for s in sections])
        return header + body

    def _add_delta_to_section(self, section: ReleaseSection, delta: dict[str, Any]) -> None:
        """Classify a SpecDelta into Added, Changed, Fixed, Deprecated, or Removed using intent.kind and mutations[].operation."""
        from eidos.core.lifecycle.delta_engine import normalize_delta_mutation

        delta_id = str(delta.get("id") or "")
        title = str(delta.get("title") or "")
        summary = str(delta.get("summary") or "")
        kind = str(delta.get("kind") or "feature").lower()
        raw_mutations = delta.get("mutations") or []
        norm_mutations = [normalize_delta_mutation(m) for m in raw_mutations if isinstance(m, dict)]
        operations = [str(m.get("operation", "")).strip().lower() for m in norm_mutations if m.get("operation")]

        desc = f": {summary}" if summary and summary.lower() != title.lower() else ""
        item_text = f"**{title}** (`{delta_id}`){desc}"

        if kind == "bug":
            section.fixed.append(item_text)
        elif operations and all(op == "remove" for op in operations):
            section.removed.append(item_text)
        elif operations and all(op == "deprecate" for op in operations):
            section.deprecated.append(item_text)
        elif kind == "feature":
            if operations and all(op == "modify" for op in operations):
                section.changed.append(item_text)
            else:
                section.added.append(item_text)
        else:
            section.changed.append(item_text)

    def _add_commit_to_section(
        self,
        section: ReleaseSection,
        msg: str,
        existing_deltas: list[dict[str, Any]] | None = None,
    ) -> None:
        """Add a conventional commit message if not already represented by a delta."""
        clean = msg.strip()
        # Skip release chore commits (e.g. chore(release): prepare v0.7.0 release)
        if clean.startswith("chore(release):") or clean.startswith("chore: bump version"):
            return

        # Check if already mentioned by a delta ID in the section
        if existing_deltas:
            short_counts: dict[str, int] = {}
            for d in existing_deltas:
                sid = "-".join(str(d.get("id") or "").split("-")[:3]).upper()
                if sid:
                    short_counts[sid] = short_counts.get(sid, 0) + 1
            for d in existing_deltas:
                full_id = str(d.get("id") or "").strip()
                short_id = "-".join(full_id.split("-")[:3])
                full_match = bool(
                    full_id
                    and re.search(rf"(?<![A-Za-z0-9_-]){re.escape(full_id)}(?![A-Za-z0-9_-])", clean, re.IGNORECASE)
                )
                short_match = bool(
                    short_id
                    and short_counts.get(short_id.upper(), 0) == 1
                    and re.search(rf"(?<![A-Za-z0-9_-]){re.escape(short_id)}(?![A-Za-z0-9_-])", clean, re.IGNORECASE)
                )
                title_val = str(d.get("title") or "").strip()
                title_match = bool(title_val and title_val.lower() in clean.lower())
                if full_match or short_match or title_match:
                    return

        # Format and categorize by conventional commit prefix
        m = re.match(r"^(feat|fix|refactor)(?:\([^)]+\))?!?:\s*(.+)$", clean)
        if not m:
            return
        prefix, raw_desc = m.group(1), m.group(2).strip()
        if not raw_desc:
            return
        desc = raw_desc[0].upper() + raw_desc[1:]
        if prefix == "feat":
            if not any(desc.lower() in a.lower() for a in section.added):
                section.added.append(desc)
        elif prefix == "fix":
            if not any(desc.lower() in f.lower() for f in section.fixed):
                section.fixed.append(desc)
        elif prefix == "refactor" and not any(desc.lower() in c.lower() for c in section.changed):
            section.changed.append(desc)

    def update_changelog_files(
        self,
        output_file: Path | None = None,
        update_docs: bool = True,
        include_unreleased: bool = True,
    ) -> dict[str, Any]:
        """Generate and write CHANGELOG.md, update docs/changelog.md, and register in zensical.toml."""
        content = self.generate_changelog(include_unreleased=include_unreleased)

        default_target = (self.root / "CHANGELOG.md").resolve()
        target_file = (output_file or default_target).resolve()
        target_file.write_text(content, encoding="utf-8")

        docs_changelog = self.root / "docs" / "changelog.md"
        symlink_created = False

        if update_docs:
            docs_changelog.parent.mkdir(parents=True, exist_ok=True)
            if docs_changelog.is_symlink() or docs_changelog.exists():
                with contextlib.suppress(OSError):
                    docs_changelog.unlink()
            if target_file == default_target:
                try:
                    os.symlink("../CHANGELOG.md", docs_changelog)
                    symlink_created = True
                except OSError:
                    # Fallback to direct content copy
                    docs_changelog.write_text(content, encoding="utf-8")
            else:
                docs_changelog.write_text(content, encoding="utf-8")

            # Ensure zensical.toml includes Changelog in nav
            self._ensure_zensical_nav()

        return {
            "output_file": str(target_file),
            "docs_file": str(docs_changelog) if update_docs else None,
            "symlink_created": symlink_created,
            "bytes_written": len(content.encode("utf-8")),
        }

    def _ensure_zensical_nav(self) -> bool:
        """Ensure { 'Changelog' = 'changelog.md' } is declared in zensical.toml nav."""
        cfg_file = self.root / "zensical.toml"
        if not cfg_file.exists():
            return False

        text = cfg_file.read_text(encoding="utf-8")
        if "changelog.md" in text:
            return False

        # Insert right after Contributing or before Eidos Portal ↗
        pattern = r'(\{\s*"Contributing"\s*=\s*"contributing\.md"\s*\},)'
        replacement = r'\1\n  { "Changelog" = "changelog.md" },'
        if re.search(pattern, text):
            new_text = re.sub(pattern, replacement, text, count=1)
            cfg_file.write_text(new_text, encoding="utf-8")
            return True
        return False
