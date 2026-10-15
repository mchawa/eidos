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
