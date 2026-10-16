"""Tests verifying automated SpecDelta-backed changelog generation (INV-DELTA-CHANGELOG-GENERATION, POL-004)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.lifecycle.changelog import ChangelogGenerator

runner = CliRunner()


@pytest.mark.invariant("INV-DELTA-CHANGELOG-GENERATION")
def test_delta_changelog_generation():
    """INV-DELTA-CHANGELOG-GENERATION: eidos delta changelog compiles an auditable changelog partitioned by git release tags."""
    repo_root = Path(__file__).parent.parent
    eidos_dir = repo_root / "eidos"
    gen = ChangelogGenerator(repo_root=repo_root, eidos_dir=eidos_dir)

    # 1. Tags discovery
    tags = gen.get_git_tags()
    if not tags:
        pytest.skip("No git tags found in repository clone (shallow clone)")
    assert len(tags) >= 5
    tag_names = {t["name"] for t in tags}
    assert "v0.7.0" in tag_names
    assert "v0.1.0" in tag_names

    # 2. SpecDeltas discovery
    deltas = gen.get_applied_deltas()
    assert len(deltas) >= 20
    delta_ids = {d["id"] for d in deltas}
    assert "DLT-2026-001" in delta_ids
    assert "DLT-2026-020" in delta_ids
    assert "DLT-2026-021" in delta_ids

    # 3. Full markdown compilation
    md = gen.generate_changelog(include_unreleased=True)
    assert "# Changelog" in md
    assert "## [Unreleased]" in md or "## [0.8.0]" in md
    assert "## [0.7.0]" in md
    assert "## [0.1.0]" in md
    assert "### Added" in md or "### Changed" in md

    # Verify DLT-2026-021 and DLT-2026-020 are referenced as clean code badges without broken .adoc links
    assert "(`DLT-2026-020`)" in md
    assert "(`DLT-2026-021`)" in md
    assert ".adoc" not in md


def test_changelog_cli_stdout():
    """Verify that eidos delta changelog --stdout outputs markdown to console and root shortcut is removed."""
    result = runner.invoke(app, ["delta", "changelog", "--stdout"])
    assert result.exit_code == 0
    assert "# Changelog" in result.output
    assert "## [Unreleased]" in result.output or "## [0.8.0]" in result.output
    assert "## [0.7.0]" in result.output

    res_removed = runner.invoke(app, ["changelog", "--stdout"])
    assert res_removed.exit_code != 0


def test_changelog_cli_file_generation(tmp_path):
    """Verify that eidos delta changelog writes custom target file and respects --no-unreleased."""
    target_out = tmp_path / "TEST_CHANGELOG.md"
    result = runner.invoke(app, ["delta", "changelog", "--output", str(target_out), "--no-unreleased", "--no-docs"])
    assert result.exit_code == 0
    assert target_out.exists()

    content = target_out.read_text(encoding="utf-8")
    assert "# Changelog" in content
    assert "## [Unreleased]" not in content
    assert "## [0.7.0]" in content


def test_changelog_deterministic_delta_classification():
    """DLT-2026-043 / INV-DELTA-CHANGELOG-GENERATION: Verify _add_delta_to_section combines intent.kind and mutations[].action."""
    from eidos.core.lifecycle.changelog import ReleaseSection

    gen = ChangelogGenerator()
    sec = ReleaseSection(tag="Unreleased", version_label="Unreleased", date="")

    gen._add_delta_to_section(
        sec,
        {
            "id": "DLT-2026-101",
            "title": "New Feature",
            "summary": "Add capability.",
            "kind": "feature",
            "mutations": [{"action": "add_capability"}, {"action": "modify_container"}],
        },
    )
    gen._add_delta_to_section(
        sec,
        {
            "id": "DLT-2026-102",
            "title": "Feature Enhancement",
            "summary": "Extend existing capability.",
            "kind": "feature",
            "mutations": [{"action": "extend_capability"}, {"action": "modify_component"}],
        },
    )
    gen._add_delta_to_section(
        sec,
        {
            "id": "DLT-2026-103",
            "title": "Architecture Enabler",
            "summary": "Refactor internal modules.",
            "kind": "enabler",
            "mutations": [{"action": "add_component"}, {"action": "modify_component"}],
        },
    )
    gen._add_delta_to_section(
        sec,
        {
            "id": "DLT-2026-104",
            "title": "Remove Legacy Bridge",
            "summary": "Delete legacy container.",
            "kind": "enabler",
            "mutations": [{"action": "remove_container"}, {"action": "remove_contract"}],
        },
    )
    gen._add_delta_to_section(
        sec,
        {
            "id": "DLT-2026-105",
            "title": "Deprecate Old Policy",
            "summary": "Sunset policy.",
            "kind": "feature",
            "mutations": [{"action": "deprecate_policy"}],
        },
    )
    gen._add_delta_to_section(
        sec,
        {
            "id": "DLT-2026-106",
            "title": "Fix Parser Edge Case",
            "summary": "Fix crash.",
            "kind": "bug",
            "mutations": [{"action": "modify_component"}],
        },
    )

    assert len(sec.added) == 1 and "DLT-2026-101" in sec.added[0]
    assert len(sec.changed) == 2 and "DLT-2026-102" in sec.changed[0] and "DLT-2026-103" in sec.changed[1]
    assert len(sec.removed) == 1 and "DLT-2026-104" in sec.removed[0]
    assert len(sec.deprecated) == 1 and "DLT-2026-105" in sec.deprecated[0]
    assert len(sec.fixed) == 1 and "DLT-2026-106" in sec.fixed[0]


def test_changelog_excludes_unapplied_deltas_and_deduplicates_unreleased(tmp_path):
    """DLT-2026-061: exclude proposed/rejected SpecDeltas and deduplicate Unreleased conventional commits."""
    import subprocess

    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    # Create one applied delta and two unapplied deltas (proposed and rejected)
    (deltas_dir / "dlt-2026-001.adoc").write_text(
        """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-001
title: "Applied Feature Alpha"
status: applied
intent:
  kind: feature
  motivation: "Deliver alpha capability."
mutations: []
---
= Applied Feature Alpha
""",
        encoding="utf-8",
    )
    (deltas_dir / "dlt-2026-002.adoc").write_text(
        """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-002
title: "Proposed Feature Beta"
status: proposed
intent:
  kind: feature
  motivation: "Work in progress."
mutations: []
---
= Proposed Feature Beta
""",
        encoding="utf-8",
    )
    (deltas_dir / "dlt-2026-003.adoc").write_text(
        """---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-003
title: "Rejected Feature Gamma"
status: rejected
intent:
  kind: feature
  motivation: "Rejected proposal."
mutations: []
---
= Rejected Feature Gamma
""",
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "eidos/deltas/dlt-2026-002.adoc"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial tag base"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True, capture_output=True)

    # Unreleased commit referencing DLT-2026-001 + an independent conventional commit
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: Applied Feature Alpha (DLT-2026-001)"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "extra.txt").write_text("extra\n", encoding="utf-8")
    subprocess.run(["git", "add", "extra.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fix: resolve standalone edge case"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    gen = ChangelogGenerator(repo_root=tmp_path, eidos_dir=eidos_dir)
    applied = gen.get_applied_deltas()
    assert [d["id"] for d in applied] == ["DLT-2026-001"]

    md = gen.generate_changelog(include_unreleased=True)
    assert "DLT-2026-001" in md
    assert "DLT-2026-002" not in md
    assert "DLT-2026-003" not in md
    # Unreleased commit referencing DLT-2026-001 must not be duplicated as a second bullet
    assert md.count("DLT-2026-001") == 1
    assert "Resolve standalone edge case" in md


def test_changelog_hermetic_multi_tag_partitioning_draft_exclusion_and_docs_sync(tmp_path):
    """Verify multi-tag release partitioning (v0.1.0, v0.2.0, Unreleased), draft exclusion, chore(release) filtering, and docs sync."""
    import subprocess

    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    docs_dir = tmp_path / "docs"
    deltas_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)

    (tmp_path / "zensical.toml").write_text(
        '[project]\nname = "test"\nnav = [\n  { "Overview" = "index.md" },\n  { "Contributing" = "contributing.md" },\n]\n',
        encoding="utf-8",
    )

    def _write_delta(did: str, title: str, status: str) -> None:
        (deltas_dir / f"{did.lower()}.adoc").write_text(
            f"---\n"
            f"type: delta/mutation\n"
            f"schema: schemas/v1/delta/delta.schema.yaml\n"
            f"id: {did}\n"
            f"title: {title}\n"
            f"status: {status}\n"
            f"intent:\n"
            f"  kind: feature\n"
            f"  motivation: Deliver {title}.\n"
            f"mutations:\n"
            f"  - action: add_capability\n"
            f"---\n"
            f"= {title}\n",
            encoding="utf-8",
        )

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "rel@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Rel Eng"], cwd=tmp_path, check=True, capture_output=True)

    # Tag v0.1.0 with DLT-2026-010
    _write_delta("DLT-2026-010", "Release 0.1 Capability", "applied")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: Release 0.1 Capability (DLT-2026-010)"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True, capture_output=True)

    # Tag v0.2.0 with DLT-2026-020 + chore(release) commit (which should be filtered out)
    _write_delta("DLT-2026-020", "Release 0.2 Capability", "applied")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: Release 0.2 Capability (DLT-2026-020)"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "chore(release): v0.2.0"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "tag", "v0.2.0"], cwd=tmp_path, check=True, capture_output=True)

    # Unreleased DLT-2026-030 (applied) + DLT-2026-031 (draft, must be excluded)
    _write_delta("DLT-2026-030", "Unreleased Capability", "applied")
    _write_delta("DLT-2026-031", "Draft Secret Capability", "draft")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: Unreleased Capability (DLT-2026-030)"], cwd=tmp_path, check=True, capture_output=True)

    gen = ChangelogGenerator(repo_root=tmp_path, eidos_dir=eidos_dir)
    applied_ids = {d["id"] for d in gen.get_applied_deltas()}
    assert applied_ids == {"DLT-2026-010", "DLT-2026-020", "DLT-2026-030"}
    assert "DLT-2026-031" not in applied_ids

    written = gen.update_changelog_files(include_unreleased=True, update_docs=True)
    assert written["output_file"] == str(tmp_path / "CHANGELOG.md")
    assert written["docs_file"] == str(docs_dir / "changelog.md")

    root_md = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    docs_md = (docs_dir / "changelog.md").read_text(encoding="utf-8")
    assert root_md.startswith("# Changelog")
    assert docs_md == root_md
    assert "## [Unreleased]" in root_md
    assert "## [0.2.0]" in root_md
    assert "## [0.1.0]" in root_md
    assert "chore(release)" not in root_md
    assert "DLT-2026-031" not in root_md

    # Verify ordering: Unreleased -> 0.2.0 -> 0.1.0
    idx_unrel = root_md.index("## [Unreleased]")
    idx_020 = root_md.index("## [0.2.0]")
    idx_010 = root_md.index("## [0.1.0]")
    assert idx_unrel < idx_020 < idx_010
    assert idx_unrel < root_md.index("DLT-2026-030") < idx_020
    assert idx_020 < root_md.index("DLT-2026-020") < idx_010
    assert idx_010 < root_md.index("DLT-2026-010")

    # Verify zensical.toml nav was updated with changelog.md
    assert "changelog.md" in (tmp_path / "zensical.toml").read_text(encoding="utf-8")


