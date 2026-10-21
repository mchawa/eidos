"""Zero-dependency automated changelog generator powered by applied SpecDeltas and Git tags (INV-DELTA-CHANGELOG-GENERATION, POL-004)."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.spec.parser import parse_spec_file


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

    def _batch_resolve_delta_commits(self, deltas_dir: Path) -> dict[Path, str]:
        """Resolve earliest Git commit SHA where status: applied was set for delta files."""
        commits_by_path: dict[Path, str] = {}
        with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
            rel_deltas = str(deltas_dir.resolve().relative_to(self.root)).replace("\\", "/")
            output = subprocess.check_output(
                [
                    "git",
                    "log",
                    "-G^status[ \t]*:[ \t]*['\"]?applied",
                    "--name-only",
                    "--format=COMMIT:%H",
                    "--",
                    rel_deltas,
                ],
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            current_sha = ""
            for raw_line in output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("COMMIT:"):
                    current_sha = line[7:].strip()
                elif current_sha:
                    full_p = (self.root / line).resolve()
                    fname = Path(line).name
                    hot_p = (deltas_dir / fname).resolve()
                    archive_p = (deltas_dir / "archive" / fname).resolve()
                    commits_by_path[full_p] = current_sha
                    commits_by_path[hot_p] = current_sha
                    commits_by_path[archive_p] = current_sha

            diff_out = subprocess.check_output(
                ["git", "diff", "HEAD", "--unified=0", "--", rel_deltas],
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            diff_file: Path | None = None
            status_applied_add_re = re.compile(r"^\+status[ \t]*:[ \t]*['\"]?applied\b", re.IGNORECASE)
            status_applied_del_re = re.compile(r"^-status[ \t]*:[ \t]*['\"]?applied\b", re.IGNORECASE)
            added_applied_files: list[Path] = []
            removed_applied_names: set[str] = set()
            src_file_name = ""
            for raw_line in diff_out.splitlines():
                if raw_line.startswith("--- a/"):
                    src_file_name = Path(raw_line[6:].strip()).name.lower()
                elif raw_line.startswith("+++ b/"):
                    rel_f = raw_line[6:].strip()
                    diff_file = (self.root / rel_f).resolve()
                elif status_applied_del_re.search(raw_line) and src_file_name:
                    removed_applied_names.add(src_file_name)
                elif diff_file is not None and status_applied_add_re.search(raw_line):
                    added_applied_files.append(diff_file)
            for af in added_applied_files:
                if af.name.lower() not in removed_applied_names:
                    commits_by_path.pop(af, None)
        return commits_by_path

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
            key=lambda p: (p.name.lower(), str(p.relative_to(deltas_dir)).lower()),
        )
        batched_commits = self._batch_resolve_delta_commits(deltas_dir)

        results: list[dict[str, Any]] = []
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
                delta_id = str(fm.get("id") or df.stem.upper())
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

                commit = batched_commits.get(df.resolve(), "")

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
        header = (
            "# Changelog\n\n"
            "All notable changes to Eidos are documented in this file.\n\n"
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
            for d in existing_deltas:
                short_id = "-".join(d["id"].split("-")[:3])
                if (
                    d["id"] in clean
                    or (short_id and short_id in clean)
                    or (d["title"] and d["title"].lower() in clean.lower())
                ):
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
