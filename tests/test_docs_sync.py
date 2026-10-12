"""Tests verifying continuous documentation synchronization and invariant coverage (INV-DOCS-SYNCHRONIZATION, POL-003)."""

import glob
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def test_documentation_and_schemas_in_sync():
    """Verify INV-DOCS-SYNCHRONIZATION and POL-003: All 13 living specification schemas and CLI commands are accurately documented."""
    repo_root = Path(__file__).parent.parent

    # 1. Verify all canonical v1 schemas are represented in docs/concepts/schema-glossary.md
    schema_files = sorted(glob.glob(str(repo_root / "assets/schemas/v1/**/*.yaml"), recursive=True))
    assert len(schema_files) == 13, f"Expected 13 canonical schemas, found {len(schema_files)}"

    glossary_path = repo_root / "docs/concepts/schema-glossary.md"
    assert glossary_path.exists(), "Schema glossary docs/concepts/schema-glossary.md is missing"
    glossary_text = glossary_path.read_text(encoding="utf-8")

    for sf in schema_files:
        with open(sf, encoding="utf-8") as f:
            schema_data = yaml.safe_load(f)

        doc_type = schema_data.get("properties", {}).get("type", {}).get("const")
        assert doc_type, f"Schema {sf} missing properties.type.const"
        assert f"`{doc_type}`" in glossary_text, f"Missing documentation for schema type '{doc_type}' in {glossary_path}"

        id_pattern = schema_data.get("properties", {}).get("id", {}).get("pattern", "")
        # Extract prefix from pattern (e.g. ^CAP-[A-Z0-9_-]+$ -> CAP-)
        if id_pattern.startswith("^") and "-" in id_pattern:
            prefix = id_pattern[1:].split("-")[0] + "-*"
            assert prefix in glossary_text, f"Missing ID prefix '{prefix}' for schema '{doc_type}' in glossary"

    # 2. Verify all primary CLI commands are documented in docs/guides/cli.md
    cli_doc_path = repo_root / "docs/guides/cli.md"
    assert cli_doc_path.exists(), "CLI guide docs/guides/cli.md is missing"
    cli_doc_text = cli_doc_path.read_text(encoding="utf-8")

    expected_commands = [
        "drift",
        "reconcile",
        "validate",
        "serve",
        "build-site",
        "plugin",
        "spec",
        "delta",
        "init",
        "components",
        "info",
        "mcp",
    ]
    for cmd in expected_commands:
        assert f"eidos {cmd}" in cli_doc_text, f"Command 'eidos {cmd}' is not documented in {cli_doc_path}"

    # 3. Verify all C4 components are registered in system-architecture.adoc
    sys_arch_path = repo_root / "eidos/architecture/system-architecture.adoc"
    assert sys_arch_path.exists(), "system-architecture.adoc is missing"
    sys_arch_text = sys_arch_path.read_text(encoding="utf-8")

    component_files = sorted(glob.glob(str(repo_root / "eidos/architecture/components/*.adoc")))
    assert len(component_files) >= 6, f"Expected at least 6 components, found {len(component_files)}"

    for comp_file in component_files:
        with open(comp_file, encoding="utf-8") as f:
            lines = f.readlines()
        comp_id = None
        for line in lines:
            if line.startswith("id:"):
                comp_id = line.split(":", 1)[1].strip()
                break
        assert comp_id, f"Component file {comp_file} missing id: frontmatter"
        assert comp_id in sys_arch_text, f"Component '{comp_id}' not registered in {sys_arch_path}"


def test_zensical_strict_build():
    """Verify INV-DOCS-SYNCHRONIZATION and POL-003: Zensical strict documentation build succeeds with zero errors."""
    repo_root = Path(__file__).parent.parent

    # Locate zensical executable
    zensical_bin = shutil.which("zensical")
    if not zensical_bin:
        venv_bin = repo_root / ".venv" / "bin" / "zensical"
        if venv_bin.exists():
            zensical_bin = str(venv_bin)
        else:
            pytest.skip("zensical executable not found in PATH or .venv")

    res = subprocess.run(
        [zensical_bin, "build", "--strict"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Zensical build failed with return code {res.returncode}:\n{res.stderr}\n{res.stdout}"

    # Verify key generated documentation pages exist
    site_dir = repo_root / "site"
    assert (site_dir / "index.html").exists(), "site/index.html missing after build"
    assert (site_dir / "search.json").exists(), "site/search.json missing after build"
    assert (site_dir / "sitemap.xml").exists(), "site/sitemap.xml missing after build"
    assert (site_dir / "concepts" / "living-specs" / "index.html").exists(), "living-specs page missing"
    assert (site_dir / "concepts" / "explorations" / "index.html").exists(), "explorations page missing"
    assert (site_dir / "concepts" / "design" / "index.html").exists(), "design page missing"
    assert (site_dir / "concepts" / "schema-glossary" / "index.html").exists(), "schema-glossary page missing"
