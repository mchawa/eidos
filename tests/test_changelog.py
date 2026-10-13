"""Tests verifying automated SpecDelta-backed changelog generation (INV-DELTA-CHANGELOG-GENERATION, POL-004)."""

import json
from pathlib import Path
from typer.testing import CliRunner
import pytest

from eidos.cli.main import app
from eidos.core.changelog import ChangelogGenerator

runner = CliRunner()


@pytest.mark.invariant("INV-DELTA-CHANGELOG-GENERATION")
def test_delta_changelog_generation():
    """INV-DELTA-CHANGELOG-GENERATION: eidos delta changelog compiles an auditable changelog partitioned by git release tags."""
    repo_root = Path(__file__).parent.parent
    eidos_dir = repo_root / "eidos"
    gen = ChangelogGenerator(repo_root=repo_root, eidos_dir=eidos_dir)

    # 1. Tags discovery
    tags = gen.get_git_tags()
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
    assert "## [Unreleased]" in md
    assert "## [0.7.0]" in md
    assert "## [0.1.0]" in md
    assert "### Added" in md or "### Changed" in md

    # Verify DLT-2026-021 and DLT-2026-020 are referenced
    assert "DLT-2026-020" in md
    assert "DLT-2026-021" in md


def test_changelog_cli_stdout():
    """Verify that eidos changelog --stdout outputs markdown to console."""
    result = runner.invoke(app, ["changelog", "--stdout"])
    assert result.exit_code == 0
    assert "# Changelog" in result.output
    assert "## [Unreleased]" in result.output
    assert "## [0.7.0]" in result.output


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

