"""Zero-dependency automated changelog generator powered by applied SpecDeltas and Git tags (INV-DELTA-CHANGELOG-GENERATION, POL-004)."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eidos.core.parser import parse_spec_file


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

    def to_markdown(self, repo_url: str = "https://github.com/mchawa/eidos") -> str:
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


class ChangelogGenerator:
    """Compiles living SpecDeltas and Git release history into a verified CHANGELOG."""

    def __init__(
        self,
        repo_root: Path | None = None,
        eidos_dir: Path | None = None,
        repo_url: str = "https://github.com/mchawa/eidos",
    ) -> None:
        self.root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self.eidos_dir = Path(eidos_dir).resolve() if eidos_dir else (self.root / "eidos").resolve()
        self.repo_url = repo_url.rstrip("/")

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
        except Exception:
            return []

    def get_applied_deltas(self) -> list[dict[str, Any]]:
        """Scan eidos/deltas and return all SpecDeltas with parsed frontmatter and git commits."""
        deltas_dir = self.eidos_dir / "deltas"
        if not deltas_dir.exists():
            return []

        delta_files = sorted(
            [
                p
                for p in deltas_dir.glob("dlt-*.adoc")
                if p.is_file() and not p.is_symlink()
            ],
            key=lambda p: p.name,
        )

        results: list[dict[str, Any]] = []
        for df in delta_files:
            try:
                doc = parse_spec_file(df)
                fm = doc.frontmatter
                delta_id = fm.get("id", df.stem.upper())
                title = fm.get("title", "")
                status = fm.get("status", "proposed")
                intent = fm.get("intent", {})
                kind = intent.get("kind", "feature")
                motivation = intent.get("motivation", "")
                if isinstance(motivation, str):
                    # Extract first sentence or summary
                    clean_mot = re.sub(r"\s+", " ", motivation).strip()
                    sentences = re.split(r"(?<=[.!?])\s+", clean_mot)
                    summary = sentences[0] if sentences else clean_mot
                else:
                    summary = ""

                # Determine git commit that introduced or last modified this delta
                commit = ""
                try:
                    commit = subprocess.check_output(
                        ["git", "log", "-1", "--format=%H", "--", str(df)],
                        cwd=self.root,
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                except Exception:
                    pass

                results.append(
                    {
                        "id": delta_id,
                        "title": title,
                        "status": status,
                        "kind": kind,
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
        """Find the earliest release tag containing the given commit."""
        if not commit or not tags:
            return "Unreleased"

        for tag in tags:
            res = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, tag["name"]],
                cwd=self.root,
                capture_output=True,
            )
            if res.returncode == 0:
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
        except Exception:
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
        unreleased_commits = self._get_commits_between(latest_tag, "HEAD") if latest_tag else []

        sec_unreleased = ReleaseSection(tag="Unreleased", version_label="Unreleased", date="")
        for d in unreleased_deltas:
            self._add_delta_to_section(sec_unreleased, d)

        # Also add unreleased conventional commits that don't duplicate deltas
        for msg in unreleased_commits:
            self._add_commit_to_section(sec_unreleased, msg)

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
        """Classify a SpecDelta into Added, Changed, Fixed, Deprecated, or Removed."""
        delta_id = delta["id"]
        title = delta["title"]
        summary = delta["summary"]
        rel_path = delta["relative_path"]
        kind = str(delta.get("kind", "feature")).lower()

        link = f"[{delta_id}]({rel_path})"
        desc = f": {summary}" if summary and summary.lower() != title.lower() else ""
        item_text = f"**{title}** ({link}){desc}"

        if kind in ("creation", "feature", "architecture"):
            section.added.append(item_text)
        elif kind in ("modification", "refactor"):
            section.changed.append(item_text)
        elif kind in ("bugfix", "fix"):
            section.fixed.append(item_text)
        elif kind in ("deprecation",):
            section.deprecated.append(item_text)
        elif kind in ("removal",):
            section.removed.append(item_text)
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
                if d["id"] in clean or (d["title"] and d["title"].lower() in clean.lower()):
                    return

        # Format and categorize by conventional commit prefix
        if clean.startswith("feat"):
            desc = re.sub(r"^feat(\([^)]+\))?:\s*", "", clean).capitalize()
            # Avoid duplicate bullet points
            if not any(desc.lower() in a.lower() for a in section.added):
                section.added.append(desc)
        elif clean.startswith("fix"):
            desc = re.sub(r"^fix(\([^)]+\))?:\s*", "", clean).capitalize()
            if not any(desc.lower() in f.lower() for f in section.fixed):
                section.fixed.append(desc)
        elif clean.startswith("refactor"):
            desc = re.sub(r"^refactor(\([^)]+\))?:\s*", "", clean).capitalize()
            if not any(desc.lower() in c.lower() for c in section.changed):
                section.changed.append(desc)

    def update_changelog_files(
        self,
        output_file: Path | None = None,
        update_docs: bool = True,
        include_unreleased: bool = True,
    ) -> dict[str, Any]:
        """Generate and write CHANGELOG.md, update docs/changelog.md, and register in zensical.toml."""
        content = self.generate_changelog(include_unreleased=include_unreleased)

        target_file = (output_file or (self.root / "CHANGELOG.md")).resolve()
        target_file.write_text(content, encoding="utf-8")

        docs_changelog = self.root / "docs" / "changelog.md"
        symlink_created = False

        if update_docs:
            docs_changelog.parent.mkdir(parents=True, exist_ok=True)
            # Create relative symlink if supported
            if docs_changelog.is_symlink() or docs_changelog.exists():
                try:
                    docs_changelog.unlink()
                except Exception:
                    pass
            try:
                os.symlink("../CHANGELOG.md", docs_changelog)
                symlink_created = True
            except OSError:
                # Fallback to direct content copy
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

