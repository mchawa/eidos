"""Tests verifying continuous documentation synchronization and invariant coverage (INV-DOCS-SYNCHRONIZATION, POL-003)."""

import glob
import shutil
import subprocess
from pathlib import Path

import pytest

from eidos.core.spec.parser import safe_load_yaml


def test_documentation_and_schemas_in_sync():
    """Verify INV-DOCS-SYNCHRONIZATION, INV-POL-003-CONTINUOUS-DOCUMENTATION-SYNC, and POL-003: All 16 living specification schemas and CLI commands are accurately documented."""
    repo_root = Path(__file__).parent.parent

    # 1. Verify all canonical v1 schemas are represented in docs/concepts/schema-glossary.md
    schema_files = sorted(glob.glob(str(repo_root / "assets/schemas/v1/**/*.schema.yaml"), recursive=True))
    assert len(schema_files) == 16, f"Expected 16 canonical schemas, found {len(schema_files)}"

    glossary_path = repo_root / "docs/concepts/schema-glossary.md"
    assert glossary_path.exists(), "Schema glossary docs/concepts/schema-glossary.md is missing"
    glossary_text = glossary_path.read_text(encoding="utf-8")

    for sf in schema_files:
        with open(sf, encoding="utf-8") as f:
            schema_data = safe_load_yaml(f)

        doc_type = schema_data.get("properties", {}).get("type", {}).get("const")
        assert doc_type, f"Schema {sf} missing properties.type.const"
        assert f"`{doc_type}`" in glossary_text, (
            f"Missing documentation for schema type '{doc_type}' in {glossary_path}"
        )

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
        "info",
        "doctor",
        "init",
        "validate",
        "drift",
        "context",
        "index",
        "spec",
        "delta",
        "portal",
        "portal serve",
        "portal build",
        "plugin",
        "plugin install",
        "plugin uninstall",
        "hook",
    ]
    for cmd in expected_commands:
        assert f"eidos {cmd}" in cli_doc_text, f"Command 'eidos {cmd}' is not documented in {cli_doc_path}"

    # Dynamically introspect live Typer/Click command tree so newly added commands cannot be undocumented
    import typer

    from eidos.cli.main import app

    root_click = typer.main.get_command(app)
    for top_name, top_cmd in getattr(root_click, "commands", {}).items():
        if getattr(top_cmd, "hidden", False):
            continue
        assert f"eidos {top_name}" in cli_doc_text, f"Live CLI command 'eidos {top_name}' missing from {cli_doc_path}"
        for sub_name, sub_cmd in getattr(top_cmd, "commands", {}).items():
            if getattr(sub_cmd, "hidden", False):
                continue
            assert f"eidos {top_name} {sub_name}" in cli_doc_text or f"`{sub_name}`" in cli_doc_text, (
                f"Live CLI subcommand 'eidos {top_name} {sub_name}' missing from {cli_doc_path}"
            )

    # 3. Verify all C4 containers are registered in system-architecture.adoc
    sys_arch_path = repo_root / "eidos/architecture/system-architecture.adoc"
    assert sys_arch_path.exists(), "system-architecture.adoc is missing"
    sys_arch_text = sys_arch_path.read_text(encoding="utf-8")

    container_files = sorted(glob.glob(str(repo_root / "eidos/architecture/containers/*.adoc")))
    assert len(container_files) >= 6, f"Expected at least 6 containers, found {len(container_files)}"

    for cont_file in container_files:
        with open(cont_file, encoding="utf-8") as f:
            lines = f.readlines()
        cont_id = None
        for line in lines:
            if line.startswith("id:"):
                cont_id = line.split(":", 1)[1].strip()
                break
        assert cont_id, f"Container file {cont_file} missing id: frontmatter"
        assert cont_id in sys_arch_text, f"Container '{cont_id}' not registered in {sys_arch_path}"

    # 4. Verify all 4 consolidated skills are documented in docs/guides/agent-plugin.md
    agent_plugin_doc = repo_root / "docs/guides/agent-plugin.md"
    assert agent_plugin_doc.exists(), "agent-plugin.md guide missing"
    agent_plugin_text = agent_plugin_doc.read_text(encoding="utf-8")
    skills_dir = repo_root / "assets/plugin_reference/skills"
    bundled_skills = [d.name for d in skills_dir.iterdir() if d.is_dir()]
    assert len(bundled_skills) == 4, f"Expected 4 skills, found {len(bundled_skills)}"
    for skill_name in bundled_skills:
        assert f"`{skill_name}`" in agent_plugin_text, f"Skill '{skill_name}' missing in {agent_plugin_doc}"

    # 5. Verify all YAML frontmatter templates in bundled skills validate against v1 JSON Schemas
    test_bundled_skill_yaml_templates_conform_to_v1_schemas()


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
    assert (site_dir / "concepts" / "how-eidos-works" / "index.html").exists(), "how-eidos-works page missing"
    assert (site_dir / "concepts" / "living-specs" / "index.html").exists(), "living-specs page missing"
    assert (site_dir / "concepts" / "architectural-index" / "index.html").exists(), "architectural-index page missing"
    assert (site_dir / "concepts" / "explorations" / "index.html").exists(), "explorations page missing"
    assert (site_dir / "concepts" / "design" / "index.html").exists(), "design page missing"
    assert (site_dir / "concepts" / "schema-glossary" / "index.html").exists(), "schema-glossary page missing"


def test_bundled_skill_yaml_templates_conform_to_v1_schemas():
    """Verify all YAML frontmatter templates in bundled skills validate against v1 JSON Schemas."""
    import re

    from eidos.core.spec.models import SpecDocument
    from eidos.core.spec.parser import split_frontmatter_and_body
    from eidos.core.spec.validator import SpecValidator

    repo_root = Path(__file__).parent.parent
    skills_dir = repo_root / "assets/plugin_reference/skills"
    validator = SpecValidator()
    yaml_fence_re = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

    validated_count = 0
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        content = skill_md.read_text(encoding="utf-8")
        assert "eidos/clients/" not in content, f"Stale path 'eidos/clients/' found in {skill_md}"
        assert "type: tech/system" not in content, f"Invalid system type 'tech/system' found in {skill_md}"

        for match in yaml_fence_re.finditer(content):
            block = match.group(1).strip()
            if not block.startswith("---"):
                continue
            fm_raw, body = split_frontmatter_and_body(block + "\n")
            assert fm_raw is not None, f"Failed to split frontmatter in {skill_md}:\n{block}"
            fm = safe_load_yaml(fm_raw)
            assert isinstance(fm, dict), f"Frontmatter in {skill_md} is not a mapping"
            doc = SpecDocument(
                file_path=skill_md,
                doc_type=fm.get("type", ""),
                doc_id=fm.get("id", ""),
                schema_ref=fm.get("schema"),
                frontmatter=fm,
                body=body,
            )
            errors = validator.validate_document(doc)
            assert errors == [], (
                f"Skill template {fm.get('id')} ({fm.get('type')}) in {skill_md.relative_to(repo_root)} "
                f"failed v1 schema validation: {errors}"
            )
            validated_count += 1

    assert validated_count >= 8, f"Expected at least 8 skill YAML frontmatter templates, validated {validated_count}"


def test_schema_glossary_yaml_examples_conform_to_v1_schemas():
    """Verify YAML and AsciiDoc frontmatter examples across all docs/**/*.md validate against v1 JSON Schemas (DLT-2026-077)."""
    import re

    from eidos.core.spec.models import SpecDocument
    from eidos.core.spec.parser import split_frontmatter_and_body
    from eidos.core.spec.validator import SpecValidator

    repo_root = Path(__file__).parent.parent
    validator = SpecValidator()
    fence_re = re.compile(r"```(?:yaml|asciidoc|adoc)\n(.*?)```", re.DOTALL)

    glossary_validated = 0
    total_validated = 0
    for md_file in sorted((repo_root / "docs").rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        for match in fence_re.finditer(content):
            block = match.group(1).strip()
            if not block.startswith("---"):
                continue
            fm_raw, body = split_frontmatter_and_body(block + "\n")
            if fm_raw is None:
                continue
            fm = safe_load_yaml(fm_raw)
            if not isinstance(fm, dict) or "type" not in fm or "id" not in fm:
                continue
            doc = SpecDocument(
                file_path=md_file,
                doc_type=fm.get("type", ""),
                doc_id=fm.get("id", ""),
                schema_ref=fm.get("schema"),
                frontmatter=fm,
                body=body,
            )
            errors = validator.validate_document(doc)
            assert errors == [], (
                f"Documentation example {fm.get('id')} ({fm.get('type')}) in {md_file.relative_to(repo_root)} "
                f"failed v1 schema validation: {errors}"
            )
            total_validated += 1
            if md_file.name == "schema-glossary.md":
                glossary_validated += 1

    assert glossary_validated >= 12, f"Expected at least 12 glossary YAML examples, validated {glossary_validated}"
    assert total_validated >= 25, (
        f"Expected at least 25 validated spec frontmatter examples across docs/, got {total_validated}"
    )


def test_documented_cli_flags_match_live_command_tree():
    """Verify all 'eidos ...' CLI invocations across docs/, README.md, and bundled skills use valid subcommands and flags."""
    import re

    import typer

    from eidos.cli.main import app

    repo_root = Path(__file__).parent.parent
    root_click = typer.main.get_command(app)

    def collect_cmd_info(click_cmd, prefix=()):
        mapping = {}
        opts = set()
        for param in getattr(click_cmd, "params", []):
            for o in getattr(param, "opts", []) + getattr(param, "secondary_opts", []):
                opts.add(o)
        opts.add("--help")
        mapping[prefix] = opts
        for sub_name, sub_cmd in getattr(click_cmd, "commands", {}).items():
            mapping.update(collect_cmd_info(sub_cmd, (*prefix, sub_name)))
        return mapping

    cmd_tree = collect_cmd_info(root_click)

    md_files = [
        *sorted((repo_root / "docs").rglob("*.md")),
        repo_root / "README.md",
        *sorted((repo_root / "assets/plugin_reference/skills").rglob("*.md")),
    ]
    fence_re = re.compile(r"```(?:bash|sh|shell|zsh)\n(.*?)```", re.DOTALL)

    checked_invocations = 0
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        for m in fence_re.finditer(text):
            block = m.group(1)
            lines = []
            buf = ""
            for raw_line in block.splitlines():
                stripped = raw_line.strip()
                if stripped.startswith("#"):
                    continue
                if stripped.endswith("\\"):
                    buf += stripped[:-1] + " "
                else:
                    buf += stripped
                    if buf:
                        lines.append(buf)
                    buf = ""
            if buf:
                lines.append(buf)

            for line in lines:
                tokens = line.split()
                if not tokens or tokens[0] != "eidos":
                    continue
                idx = 1
                matched_prefix = ()
                while idx < len(tokens):
                    tok = tokens[idx]
                    if tok.startswith("["):
                        break
                    cand = (*matched_prefix, tok)
                    if cand in cmd_tree:
                        matched_prefix = cand
                        idx += 1
                    else:
                        break

                assert matched_prefix or (
                    idx < len(tokens) and (tokens[idx].startswith("-") or tokens[idx] == "[OPTIONS]")
                ), f"Unknown command invocation in {md.relative_to(repo_root)}: {line}"

                valid_opts = cmd_tree.get(matched_prefix, set())
                for tok in tokens[idx:]:
                    clean_tok = tok.strip("[]")
                    if clean_tok in ("|", "&&", "||", ";", ">", ">>"):
                        break
                    if clean_tok.startswith("-"):
                        flag_part = clean_tok.split("=")[0]
                        for sub_flag in flag_part.split("|"):
                            if sub_flag.startswith("-"):
                                assert sub_flag in valid_opts, (
                                    f"Invalid flag '{sub_flag}' on 'eidos {' '.join(matched_prefix)}' "
                                    f"in {md.relative_to(repo_root)} (line: {line}). Valid: {sorted(valid_opts)}"
                                )
                checked_invocations += 1

    assert checked_invocations >= 80, f"Expected at least 80 checked CLI invocations, checked {checked_invocations}"
