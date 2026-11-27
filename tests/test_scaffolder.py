"""Tests verifying specification scaffolding, reference checking, and delta lifecycle management (CTR-EIDOS-CLI, POL-INVARIANT-TEST-ENFORCEMENT)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.lifecycle.catalog import find_spec_by_id_or_path
from eidos.core.lifecycle.delta_engine import apply_delta, create_delta
from eidos.core.lifecycle.initializer import init_repository
from eidos.core.lifecycle.scaffolder import (
    create_spec,
    delete_spec,
    list_specs,
)
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator

runner = CliRunner()


def assert_valid_spec(validator: SpecValidator, path: Path):
    doc = parse_spec_file(path)
    errors = validator.validate_document(doc)
    assert len(errors) == 0, f"Validation errors in {path.name}: {errors}"


def test_scaffold_all_spec_types(tmp_path):
    """Verify that create_spec scaffolds syntactically valid documents for all 15 living spec types and rejects delta (CTR-EIDOS-CLI, POL-INVARIANT-TEST-ENFORCEMENT, INV-SPIKE-ISOLATION)."""
    from eidos.core.lifecycle.scaffolder import get_spec_schema_info

    validator = SpecValidator()

    # 0a. System
    sys_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="system",
        raw_id="SYS-AUTH-PLATFORM",
        title="Auth Platform System",
    )
    assert sys_file == tmp_path / "system.adoc"
    assert_valid_spec(validator, sys_file)

    # 0b. Architecture
    arch_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="architecture",
        raw_id="ARCH-AUTH-PLATFORM",
        title="Auth Technical Architecture",
    )
    assert arch_file == tmp_path / "architecture" / "system-architecture.adoc"
    assert_valid_spec(validator, arch_file)

    # 1. Domain
    dom_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="domain",
        raw_id="DOM-TEST-AUTH",
        title="Authentication Domain",
    )
    assert dom_file.exists()
    assert_valid_spec(validator, dom_file)

    # 2. Capability (prefix-stripped semantic filename)
    cap_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="capability",
        raw_id="CAP-PASSWORD-LOGIN",
        title="Password Login",
    )
    assert cap_file.name == "password-login.adoc"
    assert cap_file.exists()
    assert_valid_spec(validator, cap_file)

    # 3. Policy (prefix-stripped semantic filename + legacy numeric fallback)
    pol_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="policy",
        raw_id="POL-AUTH-LOCK",
        title="Account Lockout Policy",
    )
    assert pol_file.name == "auth-lock.adoc"
    assert pol_file.exists()
    assert_valid_spec(validator, pol_file)

    # 4. Container (prefix-stripped semantic filename)
    cnt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="container",
        raw_id="CNT-AUTH-SERVICE",
        title="Auth Microservice",
    )
    assert cnt_file.name == "auth-service.adoc"
    assert cnt_file.exists()
    assert_valid_spec(validator, cnt_file)

    # 5. Contract (prefix-stripped semantic filename)
    ctr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="contract",
        raw_id="CTR-AUTH-REST",
        title="Auth REST API",
    )
    assert ctr_file.name == "auth-rest.adoc"
    assert ctr_file.exists()
    assert_valid_spec(validator, ctr_file)

    # 6. Persona (prefix-stripped semantic filename)
    per_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="persona",
        raw_id="PER-SECURITY-AUDITOR",
        title="Security Auditor",
    )
    assert per_file.name == "security-auditor.adoc"
    assert per_file.exists()
    assert_valid_spec(validator, per_file)

    # Verify schema example synthesis uses non-numbered semantic IDs for living specs,
    # chronological IDs for explorations/deltas, and hash-suffixed IDs for memories
    cap_schema = get_spec_schema_info("capability")
    assert "id: CAP-EXAMPLE" in cap_schema["example_frontmatter"]
    assert "EXAMPLE-001" not in cap_schema["example_frontmatter"]
    exp_schema = get_spec_schema_info("exploration")
    assert "id: EXP-2026-001" in exp_schema["example_frontmatter"]
    dlt_schema = get_spec_schema_info("delta")
    assert "id: DLT-2026-001" in dlt_schema["example_frontmatter"]
    mem_schema = get_spec_schema_info("memory")
    assert "id: MEM-EXAMPLE-A1B2" in mem_schema["example_frontmatter"]

    # 7. Delta is rejected by create_spec (disjoint boundary) and handled by create_delta
    with pytest.raises(ValueError, match="SpecDeltas"):
        create_spec(
            eidos_dir=tmp_path,
            doc_type_key="delta",
            raw_id="DLT-2026-999",
            title="MFA Integration",
        )
    dlt_file = create_delta(
        eidos_dir=tmp_path,
        raw_id="DLT-2026-999",
        title="MFA Integration",
        kind="feature",
    )
    assert dlt_file.exists()
    dlt_doc = parse_spec_file(dlt_file)
    assert "== Problem Statement" in dlt_doc.body
    assert "== Proposed Specification Changes" in dlt_doc.body
    assert "== Implementation Plan" in dlt_doc.body
    assert_valid_spec(validator, dlt_file)

    # 8. Exploration (INV-SPIKE-ISOLATION: isolated under explorations/ with hypothesis & graduation_target)
    exp_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="exploration",
        raw_id="EXP-PASSKEY-SPIKE",
        title="WebAuthn Passkey Spike",
    )
    assert exp_file.exists()
    assert exp_file.parent == tmp_path / "explorations"
    exp_doc = parse_spec_file(exp_file)
    assert exp_doc.doc_type == "product/exploration"
    assert "Hypothesis" in exp_doc.body
    assert_valid_spec(validator, exp_file)

    # 9. Component, 10. Design System, 11. UI Component, 12. Screen, 13. Client, 14. Flow
    cmp_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="component",
        raw_id="CMP-AUTH-HASH",
        title="Password Hasher",
    )
    assert_valid_spec(validator, cmp_file)

    ds_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="design-system",
        raw_id="DS-AUTH",
        title="Auth Design System",
    )
    assert_valid_spec(validator, ds_file)

    uic_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="ui-component",
        raw_id="UIC-PASSKEY-BTN",
        title="Passkey Button",
    )
    assert_valid_spec(validator, uic_file)

    scr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="screen",
        raw_id="SCR-LOGIN",
        title="Login Screen",
        client="CLT-AUTH-WEB",
    )
    assert_valid_spec(validator, scr_file)

    clt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="client",
        raw_id="CLT-AUTH-WEB",
        title="Auth Web Client",
    )
    assert_valid_spec(validator, clt_file)

    flow_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="flow",
        raw_id="FLOW-PASSKEY-LOGIN",
        title="Passkey Login Flow",
    )
    assert_valid_spec(validator, flow_file)


def test_list_specs_and_filtering(tmp_path):
    """Verify list_specs returns all documents with accurate metadata and supports filtering (CTR-EIDOS-CLI)."""
    pol_num = create_spec(tmp_path, "policy", "POL-001", "Policy 1")
    assert pol_num.name == "pol-001.adoc"
    create_spec(tmp_path, "policy", "POL-DATA-RETENTION", "Policy 2")
    create_spec(tmp_path, "contract", "CTR-CLI", "Contract 1")

    all_specs = list_specs(tmp_path)
    assert len(all_specs) == 3

    policy_specs = list_specs(tmp_path, type_filter="policy")
    assert len(policy_specs) == 2
    assert all("policy" in s["type"] for s in policy_specs)

    contract_specs = list_specs(tmp_path, type_filter="contract")
    assert len(contract_specs) == 1
    assert contract_specs[0]["id"] == "CTR-CLI"


def test_spec_deletion_safety_with_references(tmp_path):
    """Verify delete_spec blocks deletion if referenced by other documents, unless forced (CTR-EIDOS-CLI, POL-001)."""
    # Create container and contract that auto-references it
    create_spec(tmp_path, "container", "CNT-CORE", "Core Component")
    create_spec(tmp_path, "contract", "CTR-API", "API Contract")

    # Attempt delete CNT-CORE without force
    success, msg, refs = delete_spec(tmp_path, "CNT-CORE", force=False)
    assert not success
    assert "referenced by" in msg
    assert len(refs) == 1

    # Attempt delete with force
    success_force, _msg_force, _ = delete_spec(tmp_path, "CNT-CORE", force=True)
    assert success_force
    assert not find_spec_by_id_or_path(tmp_path, "CNT-CORE")


def test_delta_lifecycle_apply(tmp_path):
    """Verify SpecDelta transition in flat storage with in-place status update (CTR-EIDOS-CLI, CAP-AGENT-ADAPTER)."""
    create_spec(tmp_path, "policy", "POL-RATE-001", "Rate Limiter Policy")
    dlt_file = create_delta(
        eidos_dir=tmp_path,
        raw_id="DLT-2026-100",
        title="Rate Limiter",
    )
    dlt_file.write_text(
        dlt_file.read_text(encoding="utf-8").replace(
            "mutations: []",
            "mutations:\n  - operation: add\n    target: POL-RATE-001\n",
        ),
        encoding="utf-8",
    )
    assert dlt_file.exists()
    assert dlt_file.name.startswith("dlt-2026-100-rate-limiter-")
    assert dlt_file.name.endswith(".adoc")
    assert dlt_file.parent == tmp_path / "deltas"

    # Apply delta
    success, _msg, applied_path = apply_delta(tmp_path, "DLT-2026-100")
    assert success
    assert applied_path is not None
    assert applied_path.resolve() == dlt_file.resolve()

    # Verify content status is updated in-place
    doc = parse_spec_file(applied_path)
    assert doc.frontmatter.get("status") == "applied"

    # Verify content status is updated and valid
    validator = SpecValidator()
    assert_valid_spec(validator, applied_path)


def test_cli_spec_commands(tmp_path):
    """Verify 'eidos spec' CLI command suite and disjoint boundary with 'eidos delta' (CTR-EIDOS-CLI)."""
    # 1. Create capability
    res_create = runner.invoke(
        app,
        [
            "spec",
            "create",
            "capability",
            "CAP-TEST-001",
            "--title",
            "Test Capability",
            "--path",
            str(tmp_path),
        ],
    )
    assert res_create.exit_code == 0
    assert "Created and validated" in res_create.stdout

    # 2. List
    res_list = runner.invoke(app, ["spec", "list", "--path", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "CAP-TEST-001" in res_list.stdout

    # 3. Show
    res_show = runner.invoke(app, ["spec", "show", "CAP-TEST-001", "--path", str(tmp_path)])
    assert res_show.exit_code == 0
    assert "CAP-TEST-001" in res_show.stdout

    # 4. Delete
    res_delete = runner.invoke(app, ["spec", "delete", "CAP-TEST-001", "--path", str(tmp_path)])
    assert res_delete.exit_code == 0
    assert "Successfully deleted" in res_delete.stdout

    # 5. Disjoint boundary: 'spec create delta', 'spec list --type delta', and 'spec delete DLT-*' are rejected
    res_dlt_spec = runner.invoke(
        app,
        ["spec", "create", "delta", "DLT-2026-501", "--title", "Spec Create Delta", "--path", str(tmp_path)],
    )
    assert res_dlt_spec.exit_code == 1
    assert "eidos delta create" in res_dlt_spec.stdout

    res_dlt_list = runner.invoke(
        app,
        ["spec", "list", "--type", "delta", "--path", str(tmp_path)],
    )
    assert res_dlt_list.exit_code == 1
    assert "eidos delta list" in res_dlt_list.stdout

    create_delta(tmp_path, "DLT-2026-501", title="Delta For Boundary Test")
    res_dlt_del_via_spec = runner.invoke(
        app,
        ["spec", "delete", "DLT-2026-501", "--path", str(tmp_path)],
    )
    assert res_dlt_del_via_spec.exit_code == 1
    assert "eidos delta delete" in res_dlt_del_via_spec.stdout

    create_spec(tmp_path, "container", "CNT-BOUNDARY", "Boundary Container")
    res_spec_del_via_delta = runner.invoke(
        app,
        ["delta", "delete", "CNT-BOUNDARY", "--path", str(tmp_path)],
    )
    assert res_spec_del_via_delta.exit_code == 1
    assert "eidos spec delete" in res_spec_del_via_delta.stdout


def test_cli_delta_commands(tmp_path):
    """Verify 'eidos delta' CLI command suite (CTR-EIDOS-CLI)."""
    # 1. Create delta
    res_create = runner.invoke(
        app,
        [
            "delta",
            "create",
            "DLT-2026-555",
            "--title",
            "CLI Expansion",
            "--kind",
            "feature",
            "--path",
            str(tmp_path),
        ],
    )
    assert res_create.exit_code == 0
    assert "Created SpecDelta" in res_create.stdout

    # 2. List deltas
    res_list = runner.invoke(app, ["delta", "list", "--path", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "DLT-2026-555" in res_list.stdout
    assert "ACTIVE" in res_list.stdout

    # 3. Status dashboard
    res_status = runner.invoke(app, ["delta", "status", "--path", str(tmp_path)])
    assert res_status.exit_code == 0
    assert "SpecDelta Lifecycle Status Dashboard" in res_status.stdout
    assert "DLT-2026-555" in res_status.stdout

    # 4. Apply delta (after declaring and fulfilling at least one mutation)
    create_spec(tmp_path, "policy", "POL-555-CLI", "CLI Expansion Policy")
    dlt_555 = find_spec_by_id_or_path(tmp_path / "deltas", "DLT-2026-555")
    assert dlt_555 is not None
    dlt_555.write_text(
        dlt_555.read_text(encoding="utf-8").replace(
            "mutations: []",
            "mutations:\n  - operation: add\n    target: POL-555-CLI\n",
        ),
        encoding="utf-8",
    )
    res_apply = runner.invoke(app, ["delta", "apply", "DLT-2026-555", "--path", str(tmp_path)])
    assert res_apply.exit_code == 0
    assert "Successfully applied" in res_apply.stdout

    # 5. Delete applied delta
    res_delete = runner.invoke(app, ["delta", "delete", "DLT-2026-555", "--path", str(tmp_path)])
    assert res_delete.exit_code == 0
    assert "Successfully deleted" in res_delete.stdout


def test_cli_top_level_shortcuts(tmp_path):
    """Verify top-level 'eidos create', 'eidos list', and 'eidos delete' shortcuts are removed (CTR-EIDOS-CLI)."""
    res_create = runner.invoke(
        app,
        [
            "create",
            "policy",
            "POL-009-RULE",
            "--title",
            "Shortcut Policy",
            "--path",
            str(tmp_path),
        ],
    )
    assert res_create.exit_code != 0

    res_list = runner.invoke(app, ["list", "--path", str(tmp_path)])
    assert res_list.exit_code != 0

    res_delete = runner.invoke(app, ["delete", "POL-009-RULE", "--path", str(tmp_path)])
    assert res_delete.exit_code != 0


def test_cli_spec_lifecycle_tools(tmp_path):
    """Verify CLI commands for spec creation, listing, applying delta, and deletion (CTR-EIDOS-CLI)."""
    # 1. eidos spec create
    create_res = runner.invoke(
        app,
        [
            "spec",
            "create",
            "policy",
            "POL-100-CLI",
            "--title",
            "CLI Governed Policy",
            "--path",
            str(tmp_path),
        ],
    )
    assert create_res.exit_code == 0
    assert "Created and validated" in create_res.stdout

    # 2. eidos spec list
    list_res = runner.invoke(app, ["spec", "list", "--path", str(tmp_path)])
    assert list_res.exit_code == 0
    assert "POL-100-CLI" in list_res.stdout

    # 3. eidos delta create & eidos delta apply
    runner.invoke(
        app,
        [
            "delta",
            "create",
            "DLT-2026-888",
            "--title",
            "CLI Delta",
            "--kind",
            "feature",
            "--path",
            str(tmp_path),
        ],
    )
    dlt_888 = find_spec_by_id_or_path(tmp_path / "deltas", "DLT-2026-888")
    assert dlt_888 is not None
    dlt_888.write_text(
        dlt_888.read_text(encoding="utf-8").replace(
            "mutations: []",
            "mutations:\n  - operation: add\n    target: POL-100-CLI\n",
        ),
        encoding="utf-8",
    )
    apply_res = runner.invoke(app, ["delta", "apply", "DLT-2026-888", "--path", str(tmp_path)])
    assert apply_res.exit_code == 0

    # 4. eidos spec delete
    del_res = runner.invoke(app, ["spec", "delete", "POL-100-CLI", "--path", str(tmp_path), "--force"])
    assert del_res.exit_code == 0


def test_all_skills_in_bundle():
    """Verify that the plugin contains the 5 cohesive workflow skills (CAP-AGENT-ADAPTER, DLT-2026-171)."""
    from eidos.plugin.installer import locate_plugin_bundle
    from eidos.plugin.validator import validate_plugin_package

    bundle_dir = locate_plugin_bundle()
    report = validate_plugin_package(bundle_dir)

    assert report.is_valid
    expected_skills = {
        "eidos",
        "eidos-spec",
        "eidos-plan",
        "eidos-ops",
        "eidos-memory",
    }
    assert expected_skills == set(report.skills)
    assert len(report.skills) == 5


def test_self_contained_eidos_gitignore(tmp_path):
    """Verify eidos/.gitignore is automatically created and isolates build/cache artifacts from project root."""
    init_repository(project_dir=tmp_path)
    gitignore = tmp_path / "eidos" / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert ".DS_Store" in content


def test_init_repository_programmatic(tmp_path):
    """Verify init_repository scaffolds an empty living specification skeleton tracked by .gitkeep without installing plugins (CTR-EIDOS-CLI)."""
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.spec.validator import SpecValidator

    success, _msg, _files = init_repository(
        project_dir=tmp_path,
        title="Test Project System",
    )
    assert success
    eidos_dir = tmp_path / "eidos"
    assert (eidos_dir / ".gitignore").exists()
    assert not (tmp_path / ".agents").exists()

    # Verify system.adoc is scaffolded when title is provided
    assert (eidos_dir / "system.adoc").exists()
    sys_content = (eidos_dir / "system.adoc").read_text(encoding="utf-8")
    assert "SYS-TEST-PROJECT-SYSTEM" in sys_content
    assert "Test Project System" in sys_content

    # Verify no other example .adoc specifications exist
    other_adocs = [p for p in eidos_dir.rglob("*.adoc") if p.name != "system.adoc"]
    assert not other_adocs

    # Verify all empty directories contain .gitkeep files
    expected_gitkeeps = [
        eidos_dir / "architecture" / "containers" / ".gitkeep",
        eidos_dir / "architecture" / "components" / ".gitkeep",
        eidos_dir / "architecture" / "contracts" / ".gitkeep",
        eidos_dir / "domains" / ".gitkeep",
        eidos_dir / "policies" / ".gitkeep",
        eidos_dir / "personas" / ".gitkeep",
        eidos_dir / "deltas" / ".gitkeep",
        eidos_dir / "explorations" / ".gitkeep",
        eidos_dir / "memory" / ".gitkeep",
        eidos_dir / "design" / "clients" / ".gitkeep",
        eidos_dir / "design" / "flows" / ".gitkeep",
        eidos_dir / "design" / "systems" / "core" / "components" / ".gitkeep",
    ]
    for gk in expected_gitkeeps:
        assert gk.exists(), f"Expected .gitkeep at {gk}"
    assert not (eidos_dir / "design" / "screens").exists()
    assert set(eidos_dir.rglob(".gitkeep")) == set(expected_gitkeeps)

    # Re-init without force must fail
    success2, msg2, _ = init_repository(project_dir=tmp_path, force=False)
    assert not success2
    assert "already exist" in msg2

    # Re-init with force (without title) preserves existing authored system.adoc
    success3, _, _ = init_repository(project_dir=tmp_path, force=True)
    assert success3
    assert "SYS-TEST-PROJECT-SYSTEM" in (eidos_dir / "system.adoc").read_text(encoding="utf-8")

    # Validate directory
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert report.is_clean
    assert report.total_docs == 1


def test_init_repository_cli(tmp_path):
    """Verify eidos init CLI command scaffolds without installing plugins (CTR-EIDOS-CLI)."""
    target = tmp_path / "cli_init_target"
    result = runner.invoke(app, ["init", str(target), "--title", "my-app"])
    assert result.exit_code == 0
    assert "Successfully initialized" in result.stdout
    assert (target / "eidos" / ".gitignore").exists()
    assert (target / "eidos" / "system.adoc").exists()
    sys_text = (target / "eidos" / "system.adoc").read_text(encoding="utf-8")
    assert "SYS-MY-APP" in sys_text
    assert (target / "eidos" / "policies" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "containers" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "components" / ".gitkeep").exists()
    assert not (target / "eidos" / "design" / "screens").exists()
    assert not any(p for p in (target / "eidos").rglob("*.adoc") if p.name != "system.adoc")
    assert not (target / ".agents").exists()

    # Passing <repo>/eidos directly to `eidos init` initializes <repo>/eidos without nesting eidos/eidos
    target_explicit_eidos = tmp_path / "cli_init_explicit_eidos" / "eidos"
    res_explicit = runner.invoke(app, ["init", str(target_explicit_eidos), "--title", "explicit-eidos-app"])
    assert res_explicit.exit_code == 0
    assert (target_explicit_eidos / "system.adoc").is_file()
    assert not (target_explicit_eidos / "eidos").exists()

    # A repository root whose own directory name is `eidos` (with .git) initializes <repo>/eidos/system.adoc
    repo_named_eidos = tmp_path / "repo_named_eidos_parent" / "eidos"
    (repo_named_eidos / ".git").mkdir(parents=True)
    res_repo_named = runner.invoke(app, ["init", str(repo_named_eidos), "--title", "self-named-repo"])
    assert res_repo_named.exit_code == 0
    assert (repo_named_eidos / "eidos" / "system.adoc").is_file()


def test_init_repository_custom(tmp_path):
    """Verify programmatic init_repository call."""
    target = tmp_path / "custom_init_target"
    ok, _msg, _created = init_repository(
        project_dir=target,
        title="Custom Service",
    )
    assert ok
    assert (target / "eidos" / ".gitignore").exists()
    assert (target / "eidos" / "system.adoc").exists()
    sys_text = (target / "eidos" / "system.adoc").read_text(encoding="utf-8")
    assert "SYS-CUSTOM-SERVICE" in sys_text
    assert "Custom Service" in sys_text
    assert (target / "eidos" / "architecture" / "containers" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "components" / ".gitkeep").exists()
    assert not (target / "eidos" / "design" / "screens").exists()
    assert not any(p for p in (target / "eidos").rglob("*.adoc") if p.name != "system.adoc")
    assert not (target / ".agents").exists()

    # Passing <repo>/eidos directly to `init_repository` initializes <repo>/eidos without nesting eidos/eidos
    target_explicit_eidos = tmp_path / "custom_init_explicit_eidos" / "eidos"
    ok_exp, _msg_exp, _ = init_repository(project_dir=target_explicit_eidos, title="Explicit Service")
    assert ok_exp
    assert (target_explicit_eidos / "system.adoc").is_file()
    assert not (target_explicit_eidos / "eidos").exists()


def test_cli_version_command_and_options():
    """Verify eidos --version and -V flags work and removed 'eidos version' subcommand fails (CTR-EIDOS-CLI, POL-002)."""
    import eidos

    # 1. --version
    res_flag = runner.invoke(app, ["--version"])
    assert res_flag.exit_code == 0
    assert f"eidos {eidos.__version__}" in res_flag.output

    # 2. -V
    res_short = runner.invoke(app, ["-V"])
    assert res_short.exit_code == 0
    assert f"eidos {eidos.__version__}" in res_short.output

    # 3. version subcommand is removed; info command reports engine version & loaded v1 schemas
    res_cmd = runner.invoke(app, ["version"])
    assert res_cmd.exit_code != 0
    res_info = runner.invoke(app, ["info"])
    assert res_info.exit_code == 0 and "Eidos Engine Version" in res_info.output


def test_cli_index_update_alias(tmp_path):
    """Verify 'eidos index sync' works and 'eidos index update' alias is removed (CTR-EIDOS-CLI)."""
    from eidos.core.lifecycle.initializer import init_repository

    init_repository(project_dir=tmp_path)
    res_sync = runner.invoke(app, ["index", "sync", "--path", str(tmp_path / "eidos")])
    assert res_sync.exit_code == 0
    assert "index" in res_sync.output

    res_removed = runner.invoke(app, ["index", "update", "--path", str(tmp_path / "eidos")])
    assert res_removed.exit_code != 0


def test_cli_command_groups_help_and_subapps():
    """Verify semantic command groups in eidos --help and sub-apps (CTR-EIDOS-CLI, POL-002)."""
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "Setup & Workspace" in res.stdout
    assert "Governance & Verification" in res.stdout
    assert "Living Web Portal" in res.stdout
    assert "Living Catalog & Mutations" in res.stdout
    assert "Knowledge Graph & AI Context" in res.stdout
    assert "Agent Ecosystem & Plugins" in res.stdout

    # Verify sub-apps in help
    assert "spec" in res.stdout
    assert "delta" in res.stdout
    assert "index" in res.stdout
    assert "portal" in res.stdout
    assert "plugin" in res.stdout

    # Verify component subgroup is unmounted from eidos spec
    res_spec_help = runner.invoke(app, ["spec", "--help"])
    assert res_spec_help.exit_code == 0
    assert "create" in res_spec_help.stdout

    # Verify eidos spec list --type container and --type component replace spec containers / spec components
    res_containers = runner.invoke(app, ["spec", "list", "--type", "container"])
    assert res_containers.exit_code == 0
    assert "CNT-EIDOS-CLI" in res_containers.stdout

    res_components = runner.invoke(app, ["spec", "list", "--type", "component"])
    assert res_components.exit_code == 0
    assert "CMP-" in res_components.stdout

    assert runner.invoke(app, ["spec", "containers"]).exit_code != 0
    assert runner.invoke(app, ["spec", "components"]).exit_code != 0


def test_scaffold_design_system_and_tokens(tmp_path):
    """Verify scaffolding of base and client design systems with W3C tokens (CAP-AGENT-ADAPTER)."""
    validator = SpecValidator()

    # 1. Base Core Design System
    core_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="design-system",
        raw_id="DS-CORE",
        title="Core Design System",
    )
    assert core_file.exists()
    assert (core_file.parent / "tokens.json").exists()
    assert_valid_spec(validator, core_file)

    # 2. Client Acme Design System
    acme_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="design-system",
        raw_id="DS-ACME",
        title="Acme Brand System",
    )
    assert acme_file.exists()
    assert_valid_spec(validator, acme_file)


def test_scaffold_ui_component(tmp_path):
    """Verify scaffolding of UI components under a design system (CAP-AGENT-ADAPTER)."""
    validator = SpecValidator()

    # Scaffold core component
    btn_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="ui-component",
        raw_id="UIC-CORE-BUTTON",
        title="Action Button",
    )
    assert btn_file.exists()
    assert_valid_spec(validator, btn_file)

    # Scaffold second component
    hdr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="ui-component",
        raw_id="UIC-ACME-HEADER",
        title="Acme Header",
    )
    assert hdr_file.exists()
    assert_valid_spec(validator, hdr_file)


def test_scaffold_screen_and_capability_experience(tmp_path):
    """Verify screen specification and capability experience linkage (CAP-AGENT-ADAPTER)."""
    validator = SpecValidator()

    # Create screen (requires explicit --client)
    screen_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="screen",
        raw_id="SCR-CHECKOUT",
        title="Checkout View",
        client="CLT-STOREFRONT",
    )
    assert screen_file.exists()
    assert screen_file == tmp_path / "design" / "clients" / "storefront" / "screens" / "checkout.adoc"
    assert_valid_spec(validator, screen_file)

    # Create capability linking to screen
    create_spec(
        eidos_dir=tmp_path,
        doc_type_key="domain",
        raw_id="DOM-CHECKOUT",
        title="Checkout Domain",
    )
    cap_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="capability",
        raw_id="CAP-CHK-001",
        title="Express Checkout",
    )
    assert cap_file.exists()

    # Modify capability with experience linkage
    content = cap_file.read_text(encoding="utf-8")
    updated = content.replace(
        "technical:\n",
        f'experience:\n  screens:\n    - "{screen_file.name}"\n  design_systems:\n    - "DS-CORE"\ntechnical:\n',
    )
    cap_file.write_text(updated, encoding="utf-8")
    assert_valid_spec(validator, cap_file)


def test_cli_design_spec_scaffolding(tmp_path):
    """Verify CLI tools for design specifications."""
    # CLI screen creation without --client is rejected (no silent default)
    missing_clt_res = runner.invoke(
        app,
        [
            "spec",
            "create",
            "screen",
            "SCR-SETTINGS",
            "--title",
            "Settings Screen",
            "--path",
            str(tmp_path),
        ],
    )
    assert missing_clt_res.exit_code == 1
    assert "--client" in missing_clt_res.stdout

    # CLI screen creation with explicit --client places screen under design/clients/<slug>/screens/
    res = runner.invoke(
        app,
        [
            "spec",
            "create",
            "screen",
            "SCR-SETTINGS",
            "--title",
            "Settings Screen",
            "--client",
            "CLT-ADMIN-APP",
            "--path",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0
    assert "Created and validated" in res.stdout
    assert (tmp_path / "design" / "clients" / "admin-app" / "screens" / "settings.adoc").exists()

    # CLI design system creation
    cli_res = runner.invoke(
        app,
        [
            "spec",
            "create",
            "design-system",
            "DS-TEST",
            "--title",
            "Test System",
            "--path",
            str(tmp_path),
        ],
    )
    assert cli_res.exit_code == 0
    assert "Created and validated" in cli_res.stdout
    assert (tmp_path / "design" / "systems" / "test" / "system.adoc").exists()


def test_create_component_client_flow_and_safe_yaml(tmp_path):
    """Verify DLT-2026-032 & DLT-2026-092: component/client/flow scaffolding routes correctly and YAML-special input stays valid (INV-SPEC-VALIDATION)."""
    from eidos.core.spec.parser import safe_load_yaml

    create_spec(tmp_path, "container", "CNT-APP", "App Container")
    tricky = 'Quote: "Test" #hash\nsecond line'

    cmp_file = create_spec(tmp_path, "component", "CMP-TEST-001", tricky)
    assert cmp_file == tmp_path / "architecture" / "components" / "test-001.adoc"
    doc = parse_spec_file(cmp_file)
    assert doc.doc_type == "tech/component"
    assert doc.frontmatter["container"] == "CNT-APP"
    assert doc.frontmatter["title"] == tricky
    assert_valid_spec(SpecValidator(), cmp_file)

    # Bare IDs receive the correct prefix (no CNT-/CMP- cross-over)
    bare = create_spec(tmp_path, "tech/component", "BARE", "Bare")
    assert parse_spec_file(bare).doc_id == "CMP-BARE"

    clt_file = create_spec(tmp_path, "client", "CLT-WEB", "Web App")
    assert clt_file == tmp_path / "design" / "clients" / "web" / "client.adoc"
    assert parse_spec_file(clt_file).frontmatter["design_system"] == "DS-APP"
    assert_valid_spec(SpecValidator(), clt_file)

    flow_file = create_spec(tmp_path, "flow", "LOGIN", "Login Flow")
    assert flow_file == tmp_path / "design" / "flows" / "login.adoc"
    flow_doc = parse_spec_file(flow_file)
    assert flow_doc.doc_id == "FLOW-LOGIN"
    assert_valid_spec(SpecValidator(), flow_file)

    cross_flow = create_spec(tmp_path, "design/flow", "FLOW-ONBOARD", "Onboarding")
    assert cross_flow == tmp_path / "design" / "flows" / "onboard.adoc"
    assert_valid_spec(SpecValidator(), cross_flow)

    # SpecDelta with quotes/colons/newlines in title remains valid YAML
    dlt_file = create_delta(tmp_path, "DLT-2026-900", 'Fix "quoted": title')
    fm_text = dlt_file.read_text(encoding="utf-8").split("---")[1]
    fm = safe_load_yaml(fm_text)
    assert fm["title"] == 'Fix "quoted": title'
    assert_valid_spec(SpecValidator(), dlt_file)


def test_cli_spec_create_component_with_container(tmp_path):
    """Verify 'eidos spec create component' scaffolds a tech/component linked to existing container."""
    create_spec(tmp_path, "container", "CNT-CORE", "Core")
    res = runner.invoke(
        app,
        [
            "spec",
            "create",
            "component",
            "CMP-CLI-TEST",
            "--title",
            "CLI Test",
            "--path",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.stdout
    created = tmp_path / "architecture" / "components" / "cli-test.adoc"
    assert created.exists()
    assert "type: tech/component" in created.read_text(encoding="utf-8")


def test_engine_genericity_zero_self_references():
    """Verify INV-ENGINE-GENERICITY: runtime (src/eidos/), portal frontend views, bundled schemas (assets/schemas/), and skills (assets/plugin_reference/skills/) contain zero Eidos-self IDs or paths."""
    import re

    forbidden = re.compile(
        r"\b("
        r"SYS-EIDOS(?:-\d+)?|ARCH-EIDOS(?:-\d+)?|"
        r"CNT-EIDOS-[A-Z0-9_-]+|CTR-EIDOS-[A-Z0-9_-]+|"
        r"DOM-SPEC-ENGINE|DOM-AGENT-INTELLIGENCE|DOM-EXPERIENCE-PORTAL|DOM-VCS-LIFECYCLE|DOM-DEVEX-VCS|"
        r"CAP-PROD-\d+|CAP-AGENT-\d+|CAP-EXP-\d+|CAP-VCS-\d+|"
        r"CAP-LIVING-SPEC-ENGINE|CAP-SPEC-DELTA-MUTATIONS|CAP-EXPLORATION-DISCOVERY|"
        r"CAP-AGENT-ADAPTER|CAP-AST-CODE-GRAPH|CAP-LIVING-WEB-PORTAL|CAP-MULTI-DESIGN-SYSTEMS|CAP-DRIFT-DETECTION|"
        r"POL-GIT-SOURCE-OF-TRUTH|POL-INVARIANT-TEST-ENFORCEMENT|POL-CONTINUOUS-DOCUMENTATION-SYNC|"
        r"POL-ZERO-AI-CALL-DEPENDENCY|POL-STATEMENT-TEST-COVERAGE"
        r")\b|src/eidos"
    )
    skills_and_schemas_forbidden = re.compile(
        r"\b(POL-00\d+|INV-COMPONENT-CENTRALIZATION-MANDATE|INV-COMPONENT-CONTAINER-AFFINITY|DEC-VCS-STATIC-INVARIANT-TRACEABILITY)\b"
    )
    scan_roots = [Path("src/eidos"), Path("assets/plugin_reference/skills"), Path("assets/schemas")]
    violations: list[str] = []

    for root in scan_roots:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if any(part in ("__pycache__", "node_modules", "dist") for part in p.parts):
                continue
            if p.suffix.lower() not in (".py", ".ts", ".tsx", ".md", ".json", ".yaml", ".adoc"):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in forbidden.finditer(text):
                violations.append(f"{p}: matched '{m.group(0)}'")
            if root in (Path("assets/plugin_reference/skills"), Path("assets/schemas")):
                for m in skills_and_schemas_forbidden.finditer(text):
                    violations.append(f"{p}: matched '{m.group(0)}'")

    portal_forbidden = re.compile(
        r'\b(CLT-WEB-PORTAL|SCR-PORTAL-HOME|EXT_GIT|EXT_IDE|DS-CORE)\b|systems/core|"web-portal"|text\.includes\("Eidos"\)'
    )
    for portal_path in sorted(Path("src/eidos/portal").rglob("*")):
        if not portal_path.is_file() or any(
            part in ("__pycache__", "node_modules", "dist", "static", "theme") for part in portal_path.parts
        ):
            continue
        if portal_path.suffix.lower() not in (".py", ".ts", ".tsx"):
            continue
        text = portal_path.read_text(encoding="utf-8", errors="ignore")
        for m in portal_forbidden.finditer(text):
            violations.append(f"{portal_path}: matched '{m.group(0)}'")

    assert violations == [], f"Forbidden self-references found in engine/skills: {violations}"

    # Verify scaffolder.py and changelog.py contain zero hardcoded Eidos/placeholder IDs
    scaffolder_src = Path("src/eidos/core/lifecycle/scaffolder.py").read_text(encoding="utf-8")
    for placeholder in ("PER-USER", "DOM-CORE", "CNT-CORE", "DS-CORE", "tech/contract_cnt"):
        assert placeholder not in scaffolder_src, f"Hardcoded placeholder '{placeholder}' found in scaffolder.py"
    changelog_src = Path("src/eidos/core/lifecycle/changelog.py").read_text(encoding="utf-8")
    assert "All notable changes to Eidos are documented" not in changelog_src

    import tempfile

    from eidos.core.lifecycle.changelog import ChangelogGenerator

    with tempfile.TemporaryDirectory() as td:
        ext_repo = Path(td) / "billing-gateway"
        ext_eidos = ext_repo / "eidos"
        create_spec(ext_eidos, "system", "SYS-BILLING-001", "Nimbus Billing Gateway")
        arch_p = create_spec(ext_eidos, "architecture", "ARCH-BILLING-001", "Billing Architecture")
        assert parse_spec_file(arch_p).frontmatter["c4_context"]["actors"] == []
        create_spec(ext_eidos, "domain", "DOM-LEDGER", "Ledger Domain")
        cnt_p = create_spec(ext_eidos, "container", "CNT-LEDGER-API", "Ledger API")
        (ext_repo / "src").mkdir(parents=True, exist_ok=True)
        (ext_repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        cap_p = create_spec(ext_eidos, "capability", "CAP-LEDGER-001", "Post Journal")
        cap_fm = parse_spec_file(cap_p).frontmatter
        assert cap_fm["domain"] == "DOM-LEDGER"
        assert "business" not in cap_fm
        assert cap_fm["technical"]["containers"] == ["CNT-LEDGER-API"]
        ctr_p = create_spec(ext_eidos, "contract", "CTR-LEDGER-CLI", "Ledger CLI")
        assert parse_spec_file(ctr_p).frontmatter["container"] == "CNT-LEDGER-API"
        _ = cnt_p
        assert SpecValidator().validate_directory(ext_eidos).is_clean
        ext_md = ChangelogGenerator(repo_root=ext_repo, eidos_dir=ext_eidos).generate_changelog()
        assert "All notable changes to Nimbus Billing Gateway are documented in this file." in ext_md


def test_foreign_repo_end_to_end_workflow(tmp_path):
    """Verify INV-FOREIGN-REPO-E2E: a non-Eidos polyglot repository initialized with eidos init runs the full lifecycle cleanly."""
    import subprocess

    import eidos.core.index.graph_index as gi
    from eidos.core.spec.parser import dump_spec_document
    from eidos.portal.builder import build_static_portal

    repo = tmp_path / "src" / "acme_payments"
    ok, _, _ = init_repository(project_dir=repo, title="Acme Payments Platform")
    assert ok

    eidos_dir = repo / "eidos"
    # Verify init_repository's generated system.adoc and baseline specs validate cleanly BEFORE any edits
    initial_report = SpecValidator().validate_directory(eidos_dir)
    assert initial_report.is_clean, (
        f"Initial eidos init specs failed validation: {initial_report.schema_errors} {initial_report.broken_references}"
    )

    create_spec(eidos_dir, "domain", "DOM-PAYMENTS", "Payments Domain")
    create_spec(eidos_dir, "persona", "PER-USER", "Merchant User")
    cnt_file = create_spec(eidos_dir, "container", "CNT-CHECKOUT", "Checkout Service")
    cnt_file.write_text(
        cnt_file.read_text(encoding="utf-8").replace("source_path: src", "source_path: services/checkout"),
        encoding="utf-8",
    )
    assert cnt_file.exists()
    cap_file = create_spec(
        eidos_dir,
        "capability",
        "CAP-PAY-001",
        "Record Ledger Entry",
    )
    cmp_file = create_spec(
        eidos_dir,
        "component",
        "CMP-LEDGER",
        "Ledger Engine",
    )
    cmp_file.write_text(
        cmp_file.read_text(encoding="utf-8").replace("source_path: src", "source_path: services/checkout/ledger.ts"),
        encoding="utf-8",
    )
    assert cmp_file.exists()

    # Add source and test file in foreign repo and bind CAP-PAY-001 invariant to it
    svc_dir = repo / "services" / "checkout"
    svc_dir.mkdir(parents=True)
    (svc_dir / "ledger.ts").write_text(
        "export function recordEntry(amount: number): boolean {\n  return amount > 0;\n}\n", encoding="utf-8"
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_ledger.py").write_text(
        "def test_positive_ledger_entry():\n    # INV-ACME-POSITIVE-AMOUNT\n    assert True\n",  # eidos:fixture
        encoding="utf-8",
    )
    cap_doc = parse_spec_file(cap_file)
    cap_doc.frontmatter["technical"]["invariants"] = [
        {
            "id": "INV-ACME-POSITIVE-AMOUNT",  # eidos:fixture
            "rule": "Ledger amount must be positive.",
            "test_binding": "tests/test_ledger.py::test_positive_ledger_entry",
        }
    ]
    cap_file.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")

    # Initialize git and commit baseline
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "dev@acme.io"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Acme Dev"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, capture_output=True, check=True)

    try:
        # 1. Validate specs (both eidos_dir and eidos_dir/architecture under parent src/ path)
        val_report = SpecValidator().validate_directory(eidos_dir)
        assert val_report.is_clean, (
            f"Validation errors: {val_report.schema_errors}, broken refs: {val_report.broken_references}"
        )
        sub_report = SpecValidator().validate_directory(eidos_dir / "architecture")
        assert sub_report.is_clean, (
            f"Subdir validation errors: {sub_report.schema_errors}, broken refs: {sub_report.broken_references}"
        )

        # 2. Index sync & Context slice
        res_sync = runner.invoke(app, ["index", "sync", "--path", str(eidos_dir)])
        assert res_sync.exit_code == 0

        res_ctx = runner.invoke(
            app,
            ["context", "--component", "CNT-CHECKOUT", "--path", str(eidos_dir), "--json"],
        )
        assert res_ctx.exit_code == 0
        assert "CNT-CHECKOUT" in res_ctx.stdout

        # 3. Drift check on clean foreign repo
        from eidos.vcs.drift import detect_drift

        drift_clean = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
        assert drift_clean.drift_detected is False
        assert drift_clean.is_clean is True

        # 4. Build static portal on foreign repo
        portal_out = tmp_path / "acme_portal_dist"
        build_static_portal(eidos_dir=eidos_dir, output_dir=portal_out)
        assert (portal_out / "index.html").exists()
        assert (portal_out / "data.json").exists()

        # 5. Foreign checkout nested under ~/.agents/... parent path + Git-tracked files in target/site/vendor + cached .gitignore subproject scan
        from eidos.core.lifecycle.catalog import _set_frontmatter_scalar
        from eidos.core.workspace.project import (
            clear_subproject_roots_cache,
            discover_nested_subproject_roots,
            find_repo_root,
            get_tracked_files,
        )

        agents_checkout = tmp_path / ".agents" / "workspaces" / "rust_web_repo"
        init_repository(project_dir=agents_checkout, title="Rust Web Repo")
        (agents_checkout / "src" / "compiler" / "target").mkdir(parents=True)
        (agents_checkout / "src" / "compiler" / "target" / "x86.rs").write_text("pub fn emit() {}\n", encoding="utf-8")
        (agents_checkout / "packages" / "site" / "src").mkdir(parents=True)
        (agents_checkout / "packages" / "site" / "src" / "App.tsx").write_text(
            "export const App = () => null;\n", encoding="utf-8"
        )
        (agents_checkout / ".gitignore").write_text("ignored_subproj/\n", encoding="utf-8")
        (agents_checkout / "ignored_subproj" / "eidos").mkdir(parents=True)
        (agents_checkout / "ignored_subproj" / "eidos" / "system.adoc").write_text(
            "---\ntype: product/system\nid: SYS-IGNORED\ntitle: Ignored\nstatus: draft\narchetypes: [service]\n---\n= Ignored\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init"], cwd=agents_checkout, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "dev@rust.io"], cwd=agents_checkout, capture_output=True, check=True
        )
        subprocess.run(["git", "config", "user.name", "Rust Dev"], cwd=agents_checkout, capture_output=True, check=True)
        subprocess.run(
            ["git", "add", "eidos", "src", "packages", ".gitignore"],
            cwd=agents_checkout,
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "commit", "-m", "init"], cwd=agents_checkout, capture_output=True, check=True)

        assert find_repo_root(agents_checkout / "eidos") == agents_checkout.resolve()
        tracked = set(get_tracked_files(agents_checkout))
        assert "src/compiler/target/x86.rs" in tracked
        assert "packages/site/src/App.tsx" in tracked
        clear_subproject_roots_cache()
        assert discover_nested_subproject_roots(agents_checkout) == []
        assert discover_nested_subproject_roots(agents_checkout) == []

        # 6. _set_frontmatter_scalar preserves multiline quoted strings containing 'status:' at column 0, and find_spec_by_id_or_path guards ambiguity & bare DLT-YYYY
        tricky_yaml = (
            "---\n"
            "type: delta/mutation\n"
            "id: DLT-2026-801\n"
            'title: "Explain\nstatus: proposed\nin multiline string"\n'
            "status: proposed\n"
            "---\n= Tricky\n"
        )
        updated_tricky = _set_frontmatter_scalar(tricky_yaml, "status", "applied")
        assert 'title: "Explain\nstatus: proposed\nin multiline string"' in updated_tricky
        assert "\nstatus: applied\n---" in updated_tricky

        create_delta(agents_checkout / "eidos", "DLT-2026-119-ALPHA", title="Alpha Branch")
        create_delta(agents_checkout / "eidos", "DLT-2026-119-BETA", title="Beta Branch")
        assert find_spec_by_id_or_path(agents_checkout / "eidos" / "deltas", "DLT-2026") is None
        with pytest.raises(ValueError, match="Ambiguous specification prefix"):
            find_spec_by_id_or_path(agents_checkout / "eidos" / "deltas", "DLT-2026-119")
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None


def test_frontmatter_mutations_preserve_yaml_comments_and_formatting(tmp_path):
    """Verify apply_delta preserves YAML comments."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)

    cnt_file = cnts_dir / "core.adoc"
    cnt_file.write_text(
        """---
# Top-level container comment
type: tech/container
id: CNT-CORE # inline id comment
title: "Core Container"
status: active
container_type: library
runtime: Python 3.12
source_path: src/core
---
= Core Container
""",
        encoding="utf-8",
    )

    cmp_file = comps_dir / "parser.adoc"
    cmp_file.write_text(
        """---
type: tech/component
id: CMP-PARSER
title: "Parser"
container: CNT-CORE
status: active
source_path: "src/core/parser.py"
---
= Component: Parser

== Responsibility
Parses specs.
""",
        encoding="utf-8",
    )

    # Create a delta with comments and apply it
    dlt_file = deltas_dir / "dlt-2026-777.adoc"
    dlt_file.write_text(
        """---
# Delta header comment
type: delta/mutation
id: DLT-2026-777
title: "Comment Preservation Delta"
status: proposed # status inline comment
intent:
  kind: feature
  motivation: "Keep comments intact."
mutations:
  - operation: add
    target: CMP-PARSER
    details: "Parses specs."
---
= Delta
""",
        encoding="utf-8",
    )

    ok_apply, _, _ = apply_delta(eidos_dir, "DLT-2026-777")
    assert ok_apply
    dlt_after_apply = dlt_file.read_text(encoding="utf-8")
    assert "# Delta header comment" in dlt_after_apply
    assert "status: applied # status inline comment" in dlt_after_apply


def test_core_and_cli_modules_import_without_cycles():
    """DLT-2026-038: modules import cleanly in a fresh interpreter, in any order, with an acyclic module-level graph."""
    import ast
    import subprocess
    import sys

    src_root = Path(__file__).resolve().parent.parent / "src"
    modules: dict[str, Path] = {}
    for pkg in ("core", "cli"):
        for path in sorted((src_root / "eidos" / pkg).rglob("*.py")):
            if path.name != "__init__.py":
                rel = path.relative_to(src_root).with_suffix("")
                modules[".".join(rel.parts)] = path

    # Dedicated module exists for every C4 component split by DLT-2026-038.
    for expected in (
        "eidos.core.lifecycle.catalog",
        "eidos.core.lifecycle.delta_engine",
        "eidos.core.lifecycle.design_engine",
        "eidos.core.lifecycle.initializer",
        "eidos.cli._shared",
        "eidos.cli.spec_commands",
        "eidos.cli.delta_commands",
        "eidos.cli.plugin_commands",
        "eidos.cli.drift_commands",
        "eidos.cli.init_command",
        "eidos.cli.context_command",
        "eidos.cli.index_commands",
        "eidos.cli.portal_commands",
        "eidos.cli.doctor_hook_commands",
    ):
        assert expected in modules, f"missing module {expected}"

    # 1. Fresh-interpreter import of every module on its own, and all modules in reverse order.
    env_code = "import importlib, sys\nfor n in sys.argv[1:]:\n    importlib.import_module(n)\n"
    pythonpath = str(src_root)
    names = sorted(modules)
    for order in [[n] for n in names] + [list(reversed(names))]:
        proc = subprocess.run(
            [sys.executable, "-c", env_code, *order],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": pythonpath},
        )
        assert proc.returncode == 0, f"import of {order[:2]}... failed:\n{proc.stderr}"

    # 2. Module-level (not function-local) imports among these modules form a DAG.
    graph: dict[str, set[str]] = {}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        deps: set[str] = set()
        for node in tree.body:  # top-level statements only
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in modules:
                    deps.add(node.module)
                deps.update(f"{node.module}.{a.name}" for a in node.names if f"{node.module}.{a.name}" in modules)
            elif isinstance(node, ast.Import):
                deps.update(a.name for a in node.names if a.name in modules)
        graph[name] = deps - {name}

    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        if state.get(node) == 2:
            return
        assert state.get(node) != 1, f"import cycle: {' -> '.join([*stack, node])}"
        state[node] = 1
        for dep in sorted(graph[node]):
            visit(dep, [*stack, node])
        state[node] = 2

    for n in graph:
        visit(n, [])

    # 3. Command modules never import the root dispatcher.
    for name, deps in graph.items():
        if name.startswith("eidos.cli.") and name != "eidos.cli.main":
            assert "eidos.cli.main" not in deps, f"{name} must not import eidos.cli.main"


def test_lossless_component_promote_inline_and_word_bounded_references(tmp_path):
    """DLT-2026-065: standalone component parsing and word-bounded find_references_to_spec # eidos:fixture."""
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar, find_references_to_spec
    from eidos.core.lifecycle.scaffolder import create_spec, delete_spec
    from eidos.core.spec.parser import parse_spec_file

    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)
    (tmp_path / "src" / "a.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")

    cnt_file = cnts_dir / "core.adoc"
    cnt_file.write_text(
        """---
type: tech/container
id: CNT-CORE
title: "Core Container"
status: active
container_type: library
runtime: Python 3.12
source_path: src
---
= Core Container
""",
        encoding="utf-8",
    )

    promoted_path = comps_dir / "multi-src.adoc"
    promoted_path.write_text(
        """---
type: tech/component
id: CMP-MULTI-SRC
title: 'Parser: "Core" Engine'
container: CNT-CORE
status: active
source_path:
  - "src/a.py"
  - "src/b.py"
decisions:
  - id: DEC-COMP-001
    title: "Use safe YAML dump"
    decision: "Serialize via yaml.safe_dump."
    rationale: "Raw f-strings corrupt list source_path."
invariants:
  - id: INV-COMP-ROUNDTRIP
    rule: "Preserve component invariants."
    test_binding: "tests/test_scaffolder.py::test_lossless_component_promote_inline_and_word_bounded_references"
---
= Component: Parser

== Responsibility
Handles parsing: YAML & AsciiDoc.
""",
        encoding="utf-8",
    )

    promoted_doc = parse_spec_file(promoted_path)
    assert not promoted_doc.errors
    assert promoted_doc.frontmatter["source_path"] == ["src/a.py", "src/b.py"]
    assert promoted_doc.frontmatter["title"] == 'Parser: "Core" Engine'
    assert len(promoted_doc.frontmatter.get("decisions", [])) == 1
    assert promoted_doc.frontmatter["decisions"][0]["id"] == "DEC-COMP-001"
    assert len(promoted_doc.frontmatter.get("invariants", [])) == 1
    assert promoted_doc.frontmatter["invariants"][0]["id"] == "INV-COMP-ROUNDTRIP"

    # 3. Word-bounded find_references_to_spec: CNT-CORE-API must NOT count as a reference to CNT-CORE
    api_cnt = create_spec(eidos_dir, "container", "CNT-CORE-API", title="Core API Container")
    other_pol = create_spec(eidos_dir, "policy", "POL-REF-CHECK", title="Ref Policy")
    other_pol.write_text(
        other_pol.read_text(encoding="utf-8").replace(
            "Normative rule for Ref Policy.", "Applies only to CNT-CORE-API."
        ),
        encoding="utf-8",
    )
    _ = (api_cnt, other_pol)
    refs_to_core = find_references_to_spec(eidos_dir, "CNT-CORE", cnt_file)
    assert other_pol.resolve() not in refs_to_core

    # Also verify symlinked deltas/by-status/ do not produce duplicate references
    dlt_ref = create_delta(eidos_dir, "DLT-2026-888", title="Ref Delta")
    dlt_ref.write_text(
        dlt_ref.read_text(encoding="utf-8").replace("Proposed feature mutation for Ref Delta.", "Touches CNT-CORE."),
        encoding="utf-8",
    )
    refs_with_delta = find_references_to_spec(eidos_dir, "CNT-CORE", cnt_file)
    assert refs_with_delta.count(dlt_ref.resolve()) == 1
    _ = delete_spec

    # 4. _set_frontmatter_scalar safely quotes values containing colons, hashes, and quotes
    from eidos.core.spec.parser import extract_frontmatter

    updated_raw = _set_frontmatter_scalar(
        cnt_file.read_text(encoding="utf-8"),
        "title",
        'Core: "Engine" #1',
    )
    reparsed = extract_frontmatter(updated_raw)
    assert reparsed[2] is None
    assert reparsed[0]["title"] == 'Core: "Engine" #1'


def test_design_scaffolder_hygiene_and_dynamic_system_fallback(tmp_path):
    """DLT-2026-077 & DLT-2026-092: ui-component scaffolds minimal status=draft blueprint; design-system protects existing tokens.json."""
    import pytest

    eidos_dir = tmp_path / "eidos"

    # 1. Create a non-DS-CORE design system first
    ds_path = create_spec(eidos_dir, "design-system", "DS-BRAND", title="Brand System")
    tokens_path = ds_path.parent / "tokens.json"
    assert tokens_path.exists()
    custom_tokens = '{"custom": true}\n'
    tokens_path.write_text(custom_tokens, encoding="utf-8")

    # 2. Attempting to re-create DS-BRAND must raise FileExistsError without overwriting custom tokens.json
    with pytest.raises(FileExistsError):
        create_spec(eidos_dir, "design-system", "DS-BRAND", title="Duplicate Brand System")
    assert tokens_path.read_text(encoding="utf-8") == custom_tokens

    # 3. Create ui-component without explicit design_system -> dynamically discovers DS-BRAND, sets status: draft minimal blueprint
    uic_path = create_spec(eidos_dir, "ui-component", "UIC-BRAND-CARD", title="Brand Card")
    uic_doc = parse_spec_file(uic_path)
    assert uic_doc.frontmatter["design_system"] == "DS-BRAND"
    assert uic_doc.frontmatter["status"] == "draft"
    assert "test_bindings" not in uic_doc.frontmatter
    assert "packages/ui/src" not in uic_path.read_text(encoding="utf-8")


def test_multiline_scalar_update_and_delete_spec_applied_delta_and_promote_accepted(tmp_path):
    """Verify Bug 11 & status alignment: _set_frontmatter_scalar replaces multiline block scalars cleanly,
    delete_spec succeeds when entity is only referenced by an applied SpecDelta, and standalone component validates status: planned."""
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar, find_references_to_spec
    from eidos.core.spec.parser import dump_spec_document, extract_frontmatter
    from eidos.core.spec.validator import SpecValidator

    eidos_dir = tmp_path / "eidos"
    (tmp_path / "src" / "acc").mkdir(parents=True)
    (tmp_path / "src" / "acc" / "worker.py").write_text("def run(): pass\n", encoding="utf-8")

    # 1. Bug 11a: _set_frontmatter_scalar on YAML multiline block scalars (| and >)
    raw_multiline = (
        "---\n"
        "type: product/policy\n"
        "id: POL-MULTI-001\n"
        "title: |\n"
        "  Multi-line Title Line One\n"
        "  Multi-line Title Line Two\n"
        "status: active\n"
        "rule: >\n"
        "  Folded rule first line\n"
        "  and second line.\n"
        "---\n"
        "= Multi-line Policy\n"
    )
    updated_title = _set_frontmatter_scalar(raw_multiline, "title", "Single Line Replaced Title")
    fm1, _, err1 = extract_frontmatter(updated_title)
    assert err1 is None
    assert fm1["title"] == "Single Line Replaced Title"
    assert fm1["status"] == "active"

    updated_rule = _set_frontmatter_scalar(updated_title, "rule", "Single line replaced rule.")
    fm2, _, err2 = extract_frontmatter(updated_rule)
    assert err2 is None
    assert fm2["rule"] == "Single line replaced rule."

    # 2. Bug 11b: find_references_to_spec ignores applied/rejected SpecDeltas so delete_spec succeeds
    pol_path = create_spec(eidos_dir, "policy", "POL-OLD-TEMP", title="Temp Policy")
    dlt_path = create_delta(
        eidos_dir,
        "DLT-2026-950",
        title="Historical Delta",
    )
    dlt_path.write_text(
        dlt_path.read_text(encoding="utf-8").replace(
            "Proposed feature mutation for Historical Delta.",
            "Previously touched POL-OLD-TEMP.",
        ),
        encoding="utf-8",
    )
    # While delta is 'proposed', delete_spec without force=True is blocked
    ok_blocked, msg_blocked, refs_blocked = delete_spec(eidos_dir, "POL-OLD-TEMP", force=False)
    assert ok_blocked is False
    assert "referenced by" in msg_blocked
    assert len(refs_blocked) == 1
    assert pol_path.exists()

    # Once delta is closed ('applied'), delete_spec(force=False) succeeds cleanly
    dlt_raw = dlt_path.read_text(encoding="utf-8")
    dlt_path.write_text(_set_frontmatter_scalar(dlt_raw, "status", "applied"), encoding="utf-8")
    assert find_references_to_spec(eidos_dir, "POL-OLD-TEMP", pol_path) == []
    ok_del, _msg_del, _refs_del = delete_spec(eidos_dir, "POL-OLD-TEMP", force=False)
    assert ok_del is True
    assert not pol_path.exists()

    # 3. Create a standalone component with status: planned and verify schema validation passes
    cnt_path = create_spec(eidos_dir, "container", "CNT-ACC", title="Accepted Container")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["source_path"] = "src/acc"
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    promoted_path = create_spec(eidos_dir, "component", "CMP-ACC-WORKER", title="Accepted Worker")
    promoted_doc = parse_spec_file(promoted_path)
    promoted_doc.frontmatter["status"] = "planned"
    promoted_doc.frontmatter["source_path"] = "src/acc/worker.py"
    promoted_path.write_text(dump_spec_document(promoted_doc.frontmatter, promoted_doc.body), encoding="utf-8")

    val = SpecValidator()
    promoted_doc = parse_spec_file(promoted_path)
    val.validate_document(promoted_doc)
    assert not promoted_doc.errors, promoted_doc.errors
    assert promoted_doc.frontmatter["status"] == "planned"


def test_a5_a6_frontmatter_scalar_and_components_surgical_edge_cases() -> None:
    """A5: _set_frontmatter_scalar block-scalar/list-item/special-string handling."""
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar
    from eidos.core.spec.parser import extract_frontmatter

    # A5a: Block scalar containing indented bullet lines `- item` must consume all lines of the block scalar
    raw_block_bullets = (
        "---\n"
        "type: product/policy\n"
        "id: POL-BULLET\n"
        "rule: |\n"
        "  Summary of rules:\n"
        "  - First bullet line\n"
        "  - Second bullet line\n"
        "status: active\n"
        "---\n"
        "= Policy\n"
    )
    replaced_bullets = _set_frontmatter_scalar(raw_block_bullets, "rule", "New concise rule.")
    fm_b, _, err_b = extract_frontmatter(replaced_bullets)
    assert err_b is None, err_b
    assert fm_b["rule"] == "New concise rule."
    assert fm_b["status"] == "active"

    # A5b: Empty scalar `title:` followed by a mapping block must NOT eat the mapping block
    raw_empty_scalar = (
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-099\n"
        "title:\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Keep nested mapping intact.\n"
        "status: proposed\n"
        "---\n"
    )
    replaced_empty = _set_frontmatter_scalar(raw_empty_scalar, "title", "true")
    fm_e, _, err_e = extract_frontmatter(replaced_empty)
    assert err_e is None, err_e
    assert fm_e["title"] == "true"
    assert isinstance(fm_e["title"], str)
    assert fm_e["intent"]["motivation"] == "Keep nested mapping intact."

    # A5c: Multiline string scalar is JSON-escaped on a single line so YAML remains valid
    replaced_nl = _set_frontmatter_scalar(replaced_empty, "title", "Line 1\nLine 2")
    fm_nl, _, err_nl = extract_frontmatter(replaced_nl)
    assert err_nl is None, err_nl
    assert fm_nl["title"] == "Line 1\nLine 2"


def test_a7_a8_a11_catalog_scaffolder_and_design_edge_cases(tmp_path: Path) -> None:
    """A7, A8, A11: path boundary & reference detection, list_specs filters/types, and design_system path / resolution."""
    from eidos.core.lifecycle.catalog import find_references_to_spec, find_spec_by_id_or_path
    from eidos.core.lifecycle.design_engine import _resolve_design_entity_ref
    from eidos.core.lifecycle.scaffolder import list_specs
    from eidos.core.spec.parser import dump_spec_document

    eidos_dir = tmp_path / "eidos"
    init_repository(tmp_path, title="TestSys")

    # A7a: find_spec_by_id_or_path rejects non-.adoc files (like tokens.json) and paths escaping eidos_dir
    ds_file = create_spec(eidos_dir, "design-system", "DS-CORE", "Core Design")
    tokens_file = ds_file.parent / "tokens.json"
    assert tokens_file.exists()
    assert find_spec_by_id_or_path(eidos_dir, str(tokens_file)) is None
    assert find_spec_by_id_or_path(eidos_dir, "design/systems/core/tokens.json") is None

    outside_adoc = tmp_path / "outside.adoc"
    outside_adoc.write_text("---\ntype: product/policy\nid: POL-OUT\n---\n", encoding="utf-8")
    assert find_spec_by_id_or_path(eidos_dir, "../outside.adoc") is None
    assert find_spec_by_id_or_path(eidos_dir, str(outside_adoc)) is None

    # A7b: find_references_to_spec detects `eidos/<rel_path>` references and proposed deltas with custom filenames
    pol_target = create_spec(eidos_dir, "policy", "POL-TARGET", "Target Policy")
    pol_referrer = create_spec(
        eidos_dir,
        "policy",
        "POL-REFERRER",
        "Referrer Policy",
    )
    pol_referrer.write_text(
        pol_referrer.read_text(encoding="utf-8").replace(
            "Normative rule for Referrer Policy.",
            "See eidos/policies/target.adoc for details.",
        ),
        encoding="utf-8",
    )
    custom_delta = eidos_dir / "deltas" / "custom-audit-name.adoc"
    custom_delta.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-500\n"
        "title: Custom Named Proposed Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: modification\n"
        "  motivation: Modify POL-TARGET.\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: POL-TARGET\n"
        "    details: Update POL-TARGET.\n"
        "---\n"
        "= Custom Delta\n",
        encoding="utf-8",
    )
    refs = find_references_to_spec(eidos_dir, "POL-TARGET", pol_target)
    assert pol_referrer.resolve() in refs
    assert custom_delta.resolve() in refs

    # Once custom-named delta is marked applied, it is no longer an active reference
    custom_delta.write_text(
        custom_delta.read_text(encoding="utf-8").replace("status: proposed", "status: applied"),
        encoding="utf-8",
    )
    refs_after = find_references_to_spec(eidos_dir, "POL-TARGET", pol_target)
    assert custom_delta.resolve() not in refs_after

    # A8: list_specs supports --type system, --status active (including default-status containers and standalone components)
    cnt_path = create_spec(eidos_dir, "container", "CNT-API", "API Container")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["status"] = "draft"
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cmp_act_path = create_spec(eidos_dir, "component", "CMP-INLINE-ACT", "Active Component")
    cmp_act_doc = parse_spec_file(cmp_act_path)
    cmp_act_doc.frontmatter["status"] = "active"
    cmp_act_path.write_text(dump_spec_document(cmp_act_doc.frontmatter, cmp_act_doc.body), encoding="utf-8")

    cnt_default_path = create_spec(eidos_dir, "container", "CNT-DEFAULT-ACT", "Default Active Container")
    cnt_default_path.write_text(
        cnt_default_path.read_text(encoding="utf-8").replace("status: draft\n", ""),
        encoding="utf-8",
    )

    sys_specs = list_specs(eidos_dir, type_filter="system")
    assert any(s["id"] == "SYS-TESTSYS" for s in sys_specs)

    active_specs = list_specs(eidos_dir, status_filter="active")
    active_ids = {s["id"] for s in active_specs}
    assert "CNT-DEFAULT-ACT" in active_ids
    assert "CMP-INLINE-ACT" in active_ids
    for s in active_specs:
        assert not str(s["relative_path"]).startswith("eidos/")

    # A11a: create_spec resolves the design_system when DS-CORE exists
    uic_from_path = create_spec(
        eidos_dir,
        "ui-component",
        "UIC-PATH-BTN",
        "Path Button",
    )
    assert uic_from_path == eidos_dir / "design" / "systems" / "core" / "components" / "path-btn.adoc"
    assert parse_spec_file(uic_from_path).frontmatter["design_system"] == "DS-CORE"

    # A11b: _resolve_design_entity_ref resolves path-based design system references cleanly
    assert _resolve_design_entity_ref("design/systems/core/system.adoc", "DS-", "system.adoc", eidos_dir) == (
        "DS-CORE",
        "core",
    )


def test_deep_audit_group_a_a1_through_a8(tmp_path):
    """Verify deep audit Group A fixes (A1 through A8)."""  # eidos:fixture

    from eidos.core.lifecycle.audit import AuditEngine
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar, find_spec_by_id_or_path
    from eidos.core.lifecycle.changelog import ChangelogGenerator
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import parse_spec_file

    # --- A8: init_repository strips whitespace-only title ---
    ws_a8 = tmp_path / "my-clean-service"
    ws_a8.mkdir()
    init_repository(ws_a8, title="  My Clean Service  ")
    sys_doc_a8 = parse_spec_file(ws_a8 / "eidos" / "system.adoc")
    assert sys_doc_a8.frontmatter["id"] == "SYS-MY-CLEAN-SERVICE"
    assert sys_doc_a8.frontmatter["title"] == "My Clean Service"

    # --- A5: _set_frontmatter_scalar handles multi-line plain, next-line, and quoted scalars ---
    raw_multiline = (
        "---\n"
        "id: POL-001\n"
        "title:\n"
        "  Multi-line title line 1\n"
        "  line 2\n"
        "# Preserved column-0 comment\n\n"
        'summary: "First line\n'
        '  second line"\n'
        "status: draft\n"
        "---\n= Doc\n"
    )
    updated_fm = _set_frontmatter_scalar(raw_multiline, "title", "Single Line Title")
    updated_fm = _set_frontmatter_scalar(updated_fm, "summary", "Clean Summary")
    assert "Multi-line title" not in updated_fm
    assert "second line" not in updated_fm
    assert "# Preserved column-0 comment\n\nsummary: Clean Summary" in updated_fm

    # --- A2 & A3: bare .adoc lookup, capability domain auto-discovery, design entity refs, contract container ---
    ws = tmp_path / "ws_a"
    ws.mkdir()
    init_repository(ws, title="WS A")
    eidos_dir = ws / "eidos"
    cnt_path = create_spec(eidos_dir, "container", "CNT-API-GATEWAY", "API Gateway")
    assert find_spec_by_id_or_path(eidos_dir, "api-gateway.adoc") == cnt_path.resolve()

    dom_path = create_spec(eidos_dir, "domain", "DOM-PAYMENTS", "Payments Domain")
    cap_path = create_spec(
        eidos_dir,
        "capability",
        "CAP-CHECKOUT",
        "Checkout",
    )
    assert cap_path.parent == dom_path.parent / "capabilities"
    assert parse_spec_file(cap_path).frontmatter["domain"] == "DOM-PAYMENTS"

    # Design specs auto-discover design_system
    ds_path = create_spec(
        eidos_dir,
        "design-system",
        "DS-BRAND",
        "Brand System",
    )
    assert ds_path == eidos_dir / "design" / "systems" / "brand" / "system.adoc"

    clt_path = create_spec(
        eidos_dir,
        "client",
        "CLT-WEB-SPA",
        "Web SPA",
    )
    assert parse_spec_file(clt_path).frontmatter["design_system"] == "DS-BRAND"

    scr_path = create_spec(
        eidos_dir,
        "screen",
        "SCR-HOME",
        "Home Screen",
        client="CLT-WEB-SPA",
    )
    assert scr_path.parent == eidos_dir / "design" / "clients" / "web-spa" / "screens"

    flow_path = create_spec(
        eidos_dir,
        "flow",
        "FLOW-CHECKOUT",
        "Checkout Flow",
    )
    assert flow_path.parent == eidos_dir / "design" / "flows"

    cmp_promoted = create_spec(
        eidos_dir,
        "component",
        "CMP-PROMOTED",
        "Promoted Auth",
    )
    assert cmp_promoted.exists()

    # --- A6: ChangelogGenerator with eidos_dir only and null frontmatter values ---
    null_dlt = eidos_dir / "deltas" / "dlt-2026-901-nulls.adoc"
    null_dlt.parent.mkdir(parents=True, exist_ok=True)
    null_dlt.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-901\n"
        "title: null\n"
        "status: applied\n"
        "intent:\n"
        "  kind: null\n"
        "  motivation: null\n"
        "mutations: null\n"
        "---\n= Null Delta\n",
        encoding="utf-8",
    )
    cgen = ChangelogGenerator(eidos_dir=eidos_dir)
    assert cgen.root == ws.resolve()
    md = cgen.generate_changelog()
    assert "DLT-2026-901" in md
    assert "None" not in md

    # --- A7: AuditEngine.audit_delta rejects non-delta/broken files and resolves deleted .adoc targets for DOM-* and EXP-* ---
    auditor = AuditEngine(repo_root=ws, eidos_dir=eidos_dir)
    non_delta_audit = auditor.audit_delta("CNT-API-GATEWAY")
    assert non_delta_audit.status == "ERROR"
    assert non_delta_audit.is_clean is False

    exp_ref_pol = create_spec(
        eidos_dir,
        "policy",
        "POL-EXP-REF",
        "Policy Referencing Deleted Domain and Exploration",
    )
    exp_ref_pol.write_text(
        exp_ref_pol.read_text(encoding="utf-8").replace(
            "Normative rule for Policy Referencing Deleted Domain and Exploration.",
            "Governs DOM-LEGACY and EXP-OLD.",
        ),
        encoding="utf-8",
    )
    assert exp_ref_pol.exists()
    dlt_del_paths = eidos_dir / "deltas" / "dlt-2026-902-del.adoc"
    dlt_del_paths.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-902\n"
        "title: Remove Legacy Domain and Exploration\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: deprecation\n"
        "  motivation: Remove legacy specs\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: eidos/domains/legacy/domain.adoc\n"
        "  - operation: remove\n"
        "    target: eidos/explorations/exp-old.adoc\n"
        "---\n= Remove Legacy\n",
        encoding="utf-8",
    )
    audit_res = auditor.audit_delta("DLT-2026-902")
    assert audit_res.status == "LEAKS_DETECTED"
    matched_tokens = {f.matched_term for f in audit_res.unacknowledged_findings}
    assert "DOM-LEGACY" in matched_tokens
    assert "EXP-OLD" in matched_tokens


def test_spec_schema_introspection_loads_from_assets_and_synthesizes_valid_examples(tmp_path):
    """Verify get_spec_schema_info and 'eidos spec schema' dynamically load all 17 v1 schemas from assets/schemas/v1/."""
    import orjson

    from eidos.core.lifecycle.scaffolder import get_spec_schema_info

    all_schemas = get_spec_schema_info()
    assert isinstance(all_schemas, list)
    assert len(all_schemas) == 17

    validator = SpecValidator()
    seen_types = set()
    for schema_info in all_schemas:
        ctype = schema_info["type"]
        seen_types.add(ctype)
        assert schema_info["schema_path"].startswith("assets/schemas/v1/")
        assert schema_info["additional_properties"] is False
        assert "type" in schema_info["required"]
        assert "id" in schema_info["required"]
        assert "title" in schema_info["required"]
        assert schema_info["id_prefix"].endswith("-")

        # Synthesized example_frontmatter must parse and validate cleanly against the v1 JSON Schema
        if ctype == "tech/component":
            synthetic_path = tmp_path / "eidos" / "architecture" / "components" / "example.adoc"
        elif ctype == "design/screen":
            synthetic_path = tmp_path / "eidos" / "design" / "clients" / "web" / "screens" / "example.adoc"
        else:
            synthetic_path = tmp_path / "example.adoc"
        synthetic_path.parent.mkdir(parents=True, exist_ok=True)
        synthetic_path.write_text(f"{schema_info['example_frontmatter']}\n= Example\n", encoding="utf-8")
        doc = parse_spec_file(synthetic_path)
        validator.validate_document(doc)
        assert doc.is_valid, f"Synthesized example for {ctype} failed schema validation: {doc.errors}"

        # Lookup by short_type and canonical type must resolve to the same schema
        by_short = get_spec_schema_info(schema_info["short_type"])
        by_canon = get_spec_schema_info(ctype)
        assert isinstance(by_short, dict) and by_short["type"] == ctype
        assert isinstance(by_canon, dict) and by_canon["type"] == ctype

    # Verify CLI 'eidos spec schema' summary and single-type inspection (both Rich and --json)
    res_all_json = runner.invoke(app, ["spec", "schema", "--json"])
    assert res_all_json.exit_code == 0
    payload_all = orjson.loads(res_all_json.stdout)
    assert payload_all["count"] == 17

    res_cap_json = runner.invoke(app, ["spec", "schema", "capability", "--json"])
    assert res_cap_json.exit_code == 0
    payload_cap = orjson.loads(res_cap_json.stdout)
    assert payload_cap["type"] == "product/capability"
    assert "invariants" in payload_cap["properties"]["technical"]["allowed_keys"]

    res_rich = runner.invoke(app, ["spec", "schema", "component"])
    assert res_rich.exit_code == 0
    assert "tech/component" in res_rich.stdout
    assert "container" in res_rich.stdout

    res_invalid = runner.invoke(app, ["spec", "schema", "nonexistent-type", "--json"])
    assert res_invalid.exit_code == 1
    assert "Unknown specification schema type" in orjson.loads(res_invalid.stdout)["error"]


def test_catalog_yaml_scalar_state_machine_and_archive_symlink_filtering(tmp_path):
    """Verify catalog.py YAML scalar quoting/multiline state machine, archive exclusion, and symlink safety."""
    from eidos.core.lifecycle.catalog import (
        _format_yaml_scalar_value,
        _set_frontmatter_scalar,
        find_references_to_spec,
        find_spec_by_id_or_path,
    )

    # 1. Malformed outer-quoted scalar falls back to safe JSON-escaped string
    escaped_bad = _format_yaml_scalar_value('"broken " inner"')
    assert escaped_bad.startswith('"') and '\\"' in escaped_bad

    # 2. _set_frontmatter_scalar preserves multiline block scalars, escaped single quotes, and escaped double quotes
    complex_raw = (
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-888\n"
        "summary: |\n"
        "  status: should_not_touch\n"
        "  second line\n"
        "single_note: 'it''s multiline\n"
        "  status: ignored'\n"
        'double_note: "escaped \\"quote\\"\n'
        '  status: ignored"\n'
        "status: proposed # inline status comment\n"
        "---\n"
        "= Delta Body\n"
    )
    updated_raw = _set_frontmatter_scalar(complex_raw, "status", "applied")
    parsed_doc = SpecValidator()  # ensure valid YAML parse via parse_spec_file
    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "deltas" / "archive").mkdir(parents=True)
    active_dlt = eidos_dir / "deltas" / "dlt-2026-888.adoc"
    active_dlt.write_text(updated_raw, encoding="utf-8")
    doc = parse_spec_file(active_dlt)
    assert parsed_doc is not None
    assert doc.frontmatter["status"] == "applied"
    assert "status: should_not_touch" in doc.frontmatter["summary"]
    assert "it's multiline" in doc.frontmatter["single_note"]
    assert 'escaped "quote"' in doc.frontmatter["double_note"]

    # 3. include_archived=False excludes deltas/archive/* and skips symlinks safely
    arch_dlt = eidos_dir / "deltas" / "archive" / "dlt-2026-777.adoc"
    arch_dlt.write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-777\ntitle: Archived\nstatus: applied\n---\n= Archived\n",
        encoding="utf-8",
    )
    sym_spec = eidos_dir / "deltas" / "symlink-spec.adoc"
    sym_spec.symlink_to(arch_dlt)

    assert find_spec_by_id_or_path(eidos_dir, "DLT-2026-777", include_archived=False) is None
    assert find_spec_by_id_or_path(eidos_dir, str(arch_dlt), include_archived=False) is None
    assert find_spec_by_id_or_path(eidos_dir, "DLT-2026-777", include_archived=True) == arch_dlt.resolve()
    assert find_references_to_spec(eidos_dir, "DLT-2026-888", active_dlt) == []


def test_spec_discover_polyglot_brownfield_blueprint(tmp_path):
    """Verify INV-BROWNFIELD-DETERMINISTIC-DISCOVERY: deterministic zero-AI polyglot discovery via API and CLI."""
    import orjson

    from eidos.core.lifecycle.scaffolder import discover_repository_blueprint

    repo = tmp_path / "polyglot-acme"
    init_repository(project_dir=repo, title="Polyglot Acme")
    eidos_dir = repo / "eidos"

    # 1. .NET Web API project + test project + bin/obj build outputs (must be pruned)
    dotnet_api = repo / "services" / "orders-api"
    (dotnet_api / "Controllers").mkdir(parents=True)
    (dotnet_api / "Orders.Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>\n',
        encoding="utf-8",
    )
    (dotnet_api / "bin" / "Debug").mkdir(parents=True)
    (dotnet_api / "bin" / "Debug" / "openapi.json").write_text("{}", encoding="utf-8")
    (dotnet_api / "obj").mkdir(parents=True)
    dotnet_tests = repo / "services" / "Orders.Api.Tests"
    dotnet_tests.mkdir(parents=True)
    (dotnet_tests / "Orders.Api.Tests.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"></Project>\n', encoding="utf-8"
    )

    # 2. Python FastAPI service with [project.scripts] launcher MUST still infer container_type == 'service'
    py_svc = repo / "services" / "billing"
    (py_svc / "src").mkdir(parents=True)
    (py_svc / "pyproject.toml").write_text(
        '[project]\nname = "billing-svc"\ndependencies = ["fastapi", "uvicorn"]\n'
        '[project.scripts]\nbilling-svc = "billing.main:run"\n',
        encoding="utf-8",
    )
    # Python monorepo sub-service with no local web dep in pyproject.toml promoted to 'service' via discovered routes
    py_monorepo_svc = repo / "services" / "classifier"
    (py_monorepo_svc / "src").mkdir(parents=True)
    (py_monorepo_svc / "pyproject.toml").write_text(
        '[project]\nname = "classifier-svc"\n[project.scripts]\nclassifier = "classifier.main:run"\n',
        encoding="utf-8",
    )
    (py_monorepo_svc / "src" / "routes.py").write_text(
        'from fastapi import APIRouter\nrouter = APIRouter()\n@router.post("/classify")\ndef classify(): return {}\n',
        encoding="utf-8",
    )

    # 3. TypeScript React webapp + root monorepo workspace wrapper (skipped) + Node CLI/service/lib + invalid package.json
    (repo / "package.json").write_bytes(orjson.dumps({"name": "monorepo", "workspaces": ["apps/*"]}))
    web_app = repo / "apps" / "portal"
    (web_app / "src").mkdir(parents=True)
    (web_app / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    (web_app / "package.json").write_bytes(
        orjson.dumps({"name": "@acme/portal-web", "dependencies": {"react": "^19.0.0"}})
    )
    node_cli = repo / "apps" / "cli-tool"
    node_cli.mkdir(parents=True)
    (node_cli / "package.json").write_bytes(orjson.dumps({"name": "acme-cli", "bin": "./cli.js"}))
    node_svc = repo / "apps" / "bff"
    node_svc.mkdir(parents=True)
    (node_svc / "package.json").write_bytes(
        orjson.dumps({"name": "portal-web", "bin": "./server.js", "dependencies": {"express": "^4.19.0"}})
    )
    node_lib = repo / "packages" / "shared"
    node_lib.mkdir(parents=True)
    (node_lib / "package.json").write_bytes(orjson.dumps({"name": "shared-lib"}))
    bad_pkg = repo / "packages" / "broken"
    bad_pkg.mkdir(parents=True)
    (bad_pkg / "package.json").write_text("{invalid json", encoding="utf-8")

    # 4. Go service, Rust CLI + workspace-only Cargo.toml, Python CLI + lib, Java Spring service, and unowned loose code root
    go_svc = repo / "services" / "gateway"
    (go_svc / "cmd").mkdir(parents=True)
    (go_svc / "go.mod").write_text("module github.com/acme/gateway\n", encoding="utf-8")

    (repo / "Cargo.toml").write_text('[workspace]\nmembers = ["tools/indexer"]\n', encoding="utf-8")
    rs_cli = repo / "tools" / "indexer"
    (rs_cli / "src").mkdir(parents=True)
    (rs_cli / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (rs_cli / "Cargo.toml").write_text('[package]\nname = "acme-indexer"\n', encoding="utf-8")

    py_cli = repo / "tools" / "runner"
    py_cli.mkdir(parents=True)
    (py_cli / "pyproject.toml").write_text(
        '[project]\nname = "runner"\ndescription = "CLI tool for inspecting FastAPI services"\n'
        '[project.scripts]\nrun = "runner:main"\n',
        encoding="utf-8",
    )
    py_lib = repo / "packages" / "core-lib"
    py_lib.mkdir(parents=True)
    (py_lib / "pyproject.toml").write_text('[project]\nname = "core-lib"\n', encoding="utf-8")

    jvm_svc = repo / "services" / "ledger"
    (jvm_svc / "src" / "main").mkdir(parents=True)
    (jvm_svc / "pom.xml").write_text(
        "<project><artifactId>spring-boot-starter-web</artifactId></project>\n", encoding="utf-8"
    )

    (repo / "workers").mkdir(parents=True)
    (repo / "workers" / "job.py").write_text("def run(): pass\n", encoding="utf-8")
    (repo / "tests").mkdir(parents=True)

    # 5. Interface contracts (OpenAPI, Protobuf, GraphQL, AsyncAPI, OpenRPC, WSDL, Avro, Prisma, canonical SQL)
    schemas_dir = repo / "contracts"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "orders-openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    (schemas_dir / "payments.proto").write_text('syntax = "proto3";\n', encoding="utf-8")
    (schemas_dir / "catalog.graphql").write_text("type Query { ok: Boolean }\n", encoding="utf-8")
    (schemas_dir / "events-asyncapi.yaml").write_text("asyncapi: 2.6.0\n", encoding="utf-8")
    (schemas_dir / "mcp-openrpc.json").write_text('{"openrpc": "1.2.6"}\n', encoding="utf-8")
    (schemas_dir / "erp.wsdl").write_text("<definitions/>\n", encoding="utf-8")
    (schemas_dir / "order-event.avsc").write_text('{"type":"record","name":"Order","fields":[]}\n', encoding="utf-8")
    (schemas_dir / "ledger.avdl").write_text("protocol Ledger {}\n", encoding="utf-8")
    db_dir = repo / "db"
    db_dir.mkdir(parents=True)
    (db_dir / "schema.prisma").write_text("model User { id Int @id }\n", encoding="utf-8")
    (db_dir / "schema.sql").write_text("CREATE TABLE users (id INT);\n", encoding="utf-8")
    (db_dir / "ddl.sql").write_text("CREATE VIEW active_users AS SELECT * FROM users;\n", encoding="utf-8")
    # Numbered migration, seed script, and example SQL must be excluded
    (db_dir / "001_init.sql").write_text("CREATE TABLE migrations (id INT);\n", encoding="utf-8")
    (db_dir / "seed.sql").write_text("INSERT INTO users VALUES (1);\n", encoding="utf-8")
    (repo / "examples").mkdir(parents=True)
    (repo / "examples" / "schema.sql").write_text("CREATE TABLE demo (id INT);\n", encoding="utf-8")

    # Map one container and one contract in eidos/ to verify already_mapped detection
    cnt_p = create_spec(eidos_dir, "container", "CNT-BILLING-SVC", "Billing Service")
    cnt_p.write_text(
        cnt_p.read_text(encoding="utf-8").replace("source_path: src", "source_path: services/billing/src"),
        encoding="utf-8",
    )
    ctr_p = create_spec(eidos_dir, "contract", "CTR-ORDERS-API", "Orders OpenAPI")
    ctr_doc = parse_spec_file(ctr_p)
    ctr_doc.frontmatter["protocol"] = "openapi"
    ctr_doc.frontmatter["specification_file"] = "contracts/orders-openapi.yaml"
    ctr_doc.frontmatter["status"] = "active"
    from eidos.core.spec.parser import dump_spec_document

    ctr_p.write_text(dump_spec_document(ctr_doc.frontmatter, ctr_doc.body), encoding="utf-8")

    bp = discover_repository_blueprint(repo_root=repo, eidos_dir=eidos_dir)
    assert bp["eidos_initialized"] is True

    cnt_by_manifest = {c["manifest"]: c for c in bp["containers"]}
    assert cnt_by_manifest["services/orders-api/Orders.Api.csproj"]["runtime"] == ".NET / C#"
    assert cnt_by_manifest["services/orders-api/Orders.Api.csproj"]["container_type"] == "service"
    assert cnt_by_manifest["services/orders-api/Orders.Api.csproj"]["already_mapped"] is False
    assert cnt_by_manifest["services/billing/pyproject.toml"]["already_mapped"] is True
    assert cnt_by_manifest["services/billing/pyproject.toml"]["container_type"] == "service"
    assert cnt_by_manifest["services/classifier/pyproject.toml"]["container_type"] == "service"
    assert cnt_by_manifest["tools/runner/pyproject.toml"]["container_type"] == "cli"
    assert cnt_by_manifest["apps/bff/package.json"]["container_type"] == "service"
    assert cnt_by_manifest["apps/cli-tool/package.json"]["container_type"] == "cli"
    assert cnt_by_manifest["apps/portal/package.json"]["container_type"] == "webapp"
    assert cnt_by_manifest["apps/portal/package.json"]["runtime"] == "TypeScript / Node.js"
    assert cnt_by_manifest["services/gateway/go.mod"]["runtime"] == "Go"
    assert cnt_by_manifest["tools/indexer/Cargo.toml"]["runtime"] == "Rust"
    assert cnt_by_manifest["tools/indexer/Cargo.toml"]["container_type"] == "cli"
    assert cnt_by_manifest["services/ledger/pom.xml"]["runtime"] == "Java / JVM"
    assert "services/Orders.Api.Tests/Orders.Api.Tests.csproj" not in cnt_by_manifest

    ctr_by_file = {c["specification_file"]: c for c in bp["contracts"]}
    assert ctr_by_file["contracts/orders-openapi.yaml"]["protocol"] == "openapi"
    assert ctr_by_file["contracts/orders-openapi.yaml"]["already_mapped"] is True
    assert ctr_by_file["contracts/payments.proto"]["protocol"] == "protobuf"
    assert ctr_by_file["contracts/catalog.graphql"]["protocol"] == "graphql"
    assert ctr_by_file["contracts/events-asyncapi.yaml"]["protocol"] == "asyncapi"
    assert ctr_by_file["contracts/mcp-openrpc.json"]["protocol"] == "json_rpc_stdio"
    assert ctr_by_file["contracts/erp.wsdl"]["protocol"] == "wsdl"
    assert ctr_by_file["contracts/order-event.avsc"]["protocol"] == "avro"
    assert ctr_by_file["contracts/ledger.avdl"]["protocol"] == "avro"
    assert ctr_by_file["db/schema.prisma"]["protocol"] == "sql_ddl"
    assert ctr_by_file["db/schema.sql"]["protocol"] == "sql_ddl"
    assert ctr_by_file["db/ddl.sql"]["protocol"] == "sql_ddl"
    assert ctr_by_file["db/ddl.sql"]["suggested_id"] == "CTR-DB-DDL"
    assert "db/001_init.sql" not in ctr_by_file
    assert "db/seed.sql" not in ctr_by_file
    assert "examples/schema.sql" not in ctr_by_file
    # Pruned .NET bin/ output must not appear
    assert "services/orders-api/bin/Debug/openapi.json" not in ctr_by_file

    assert "tests" in bp["test_roots"]
    assert "services/Orders.Api.Tests" in bp["test_roots"]
    assert "workers" in bp["unmapped_code_roots"]
    assert "services/billing/src" not in bp["unmapped_code_roots"]

    # CLI --json (passing repo root and passing eidos dir), Rich rendering, and missing path errors
    res_json = runner.invoke(app, ["spec", "discover", "--path", str(repo), "--json"])
    assert res_json.exit_code == 0, res_json.stdout
    parsed = orjson.loads(res_json.stdout)
    assert parsed["summary"]["containers_found"] == 12
    assert parsed["summary"]["contracts_found"] == 12
    assert any(c["suggested_id"].endswith("-2") for c in parsed["containers"])

    res_eidos_arg = runner.invoke(app, ["spec", "discover", "--path", str(eidos_dir), "--json"])
    assert res_eidos_arg.exit_code == 0
    assert orjson.loads(res_eidos_arg.stdout)["summary"]["containers_found"] == 12

    res_rich = runner.invoke(app, ["spec", "discover", "--path", str(repo)])
    assert res_rich.exit_code == 0
    assert "Eidos Brownfield Repository Discovery" in res_rich.stdout
    assert "Candidate Containers" in res_rich.stdout
    assert "Candidate Contracts" in res_rich.stdout

    res_missing = runner.invoke(app, ["spec", "discover", "--path", str(tmp_path / "does-not-exist"), "--json"])
    assert res_missing.exit_code == 1
    assert "not found" in orjson.loads(res_missing.stdout)["error"]

    res_missing_rich = runner.invoke(app, ["spec", "discover", "--path", str(tmp_path / "does-not-exist")])
    assert res_missing_rich.exit_code == 1
    assert "Error" in res_missing_rich.stdout

    res_default = runner.invoke(app, ["spec", "discover", "--json"])
    assert res_default.exit_code == 0
    assert orjson.loads(res_default.stdout)["summary"]["containers_found"] >= 1


def test_init_repository_ci_workflow_templates(tmp_path):
    """Verify INV-INIT-CI-WORKFLOW-TEMPLATES: valid YAML GitHub/GitLab templates, non-destructive preservation, and provider validation."""
    from eidos.core.spec.parser import safe_load_yaml

    # 1. GitHub Actions via CLI
    gh_repo = tmp_path / "gh-repo"
    res_gh = runner.invoke(app, ["init", str(gh_repo), "--title", "Acme GH", "--ci", "github"])
    assert res_gh.exit_code == 0, res_gh.stdout
    gh_wf = gh_repo / ".github" / "workflows" / "eidos-drift.yml"
    assert gh_wf.is_file()
    gh_parsed = safe_load_yaml(gh_wf.read_text(encoding="utf-8"))
    assert isinstance(gh_parsed, dict)
    assert "jobs" in gh_parsed and "eidos-drift" in gh_parsed["jobs"]
    steps = gh_parsed["jobs"]["eidos-drift"]["steps"]
    assert any((s.get("with") or {}).get("fetch-depth") == 0 for s in steps)
    assert any("eidos drift" in str(s.get("run", "")) and "--markdown" not in str(s.get("run", "")) for s in steps)
    assert "GITHUB_STEP_SUMMARY" not in gh_wf.read_text(encoding="utf-8")

    # 2. Non-destructive preservation when workflow already exists without --force, overwrite with --force
    existing_repo = tmp_path / "existing-wf-repo"
    (existing_repo / "eidos").mkdir(parents=True)
    (existing_repo / "eidos" / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    custom_wf = existing_repo / ".github" / "workflows" / "eidos-drift.yml"
    custom_wf.parent.mkdir(parents=True)
    custom_wf.write_text("# custom user workflow\nname: Custom\n", encoding="utf-8")
    ok, _, created = init_repository(existing_repo, title="Preserve Test", ci="github", force=False)
    assert ok
    assert custom_wf not in created
    assert "# custom user workflow" in custom_wf.read_text(encoding="utf-8")
    assert ".cache/" in (existing_repo / "eidos" / ".gitignore").read_text(encoding="utf-8")

    (existing_repo / "eidos" / "custom_empty_leaf").mkdir(parents=True)
    ok_force, _, created_force = init_repository(existing_repo, title="Preserve Test", ci="github", force=True)
    assert ok_force
    assert custom_wf in created_force
    assert (existing_repo / "eidos" / "custom_empty_leaf" / ".gitkeep").exists()
    assert "Eidos Living Spec Drift Gate" in custom_wf.read_text(encoding="utf-8")

    # 3. GitLab CI via CLI
    gl_repo = tmp_path / "gl-repo"
    res_gl = runner.invoke(app, ["init", str(gl_repo), "--title", "Acme GL", "--ci", "gitlab"])
    assert res_gl.exit_code == 0, res_gl.stdout
    gl_wf = gl_repo / ".gitlab-ci.eidos.yml"
    assert gl_wf.is_file()
    gl_parsed = safe_load_yaml(gl_wf.read_text(encoding="utf-8"))
    assert isinstance(gl_parsed, dict)
    assert "eidos-governance" in gl_parsed
    assert gl_parsed["eidos-governance"]["variables"]["GIT_DEPTH"] == "0"

    # 4. Unsupported --ci provider raises ValueError in Python API and exits 1 in CLI
    with pytest.raises(ValueError, match="Unsupported --ci provider"):
        init_repository(tmp_path / "bad-ci", title="Bad CI", ci="jenkins")
    res_bad = runner.invoke(app, ["init", str(tmp_path / "bad-ci-cli"), "--ci", "circleci"])
    assert res_bad.exit_code == 1
    assert "Unsupported --ci provider" in res_bad.stdout


def test_spec_discover_code_first_rest_http_controllers(tmp_path):
    """Verify INV-BROWNFIELD-REST-CONTROLLER-DISCOVERY: discover_repository_blueprint detects code-first modern REST
    controller/router roots across ASP.NET Core, FastAPI, NestJS, Spring Boot, and Go, reports route_count and
    already_mapped status, skips tests, and ignores legacy WCF/SOAP contracts (DEC-CORE-MODERN-REST-PARITY-ONLY)."""
    import orjson

    from eidos.core.lifecycle.scaffolder import discover_repository_blueprint
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    repo = tmp_path / "code-first-rest-repo"
    init_repository(repo, title="Code-First REST Repo")
    eidos_dir = repo / "eidos"

    # 1. ASP.NET Core Controllers directory (2 controllers grouped under directory) + legacy WCF service (ignored)
    aspnet_ctrl = repo / "services" / "orders-api" / "Controllers"
    aspnet_ctrl.mkdir(parents=True)
    (aspnet_ctrl / "OrdersController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        '[ApiController]\n[Route("api/orders")]\n'
        "public class OrdersController : ControllerBase {\n"
        '    [HttpGet("{id}")]\n    public IActionResult GetOrder(int id) => Ok();\n'
        "    [HttpPost]\n    public IActionResult CreateOrder() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    (aspnet_ctrl / "ItemsController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        '[ApiController]\n[Route("api/items")]\n'
        "public class ItemsController : ControllerBase {\n"
        "    [HttpGet]\n    public IActionResult ListItems() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    wcf_dir = repo / "services" / "legacy-wcf"
    wcf_dir.mkdir(parents=True)
    (wcf_dir / "LegacyService.cs").write_text(
        "[ServiceContract]\npublic interface ILegacyService {\n    [OperationContract]\n    string Ping();\n}\n",
        encoding="utf-8",
    )

    # 2. FastAPI standalone router file
    fastapi_dir = repo / "services" / "billing" / "src"
    fastapi_dir.mkdir(parents=True)
    (fastapi_dir / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.get("/v1/invoices")\ndef list_invoices():\n    return []\n'
        '@app.post("/v1/invoices")\ndef create_invoice():\n    return {}\n',
        encoding="utf-8",
    )

    # 3. NestJS controller inside controllers/
    nest_dir = repo / "services" / "catalog" / "src" / "controllers"
    nest_dir.mkdir(parents=True)
    (nest_dir / "products.controller.ts").write_text(
        '@Controller("products")\nexport class ProductsController {\n  @Get(":id")\n  findOne() { return {}; }\n}\n',
        encoding="utf-8",
    )

    # 4. Spring Boot RestController
    spring_dir = repo / "services" / "ledger" / "src" / "main" / "java" / "com" / "acme"
    spring_dir.mkdir(parents=True)
    (spring_dir / "LedgerController.java").write_text(
        '@RestController\n@RequestMapping("/api/ledger")\n'
        "public class LedgerController {\n"
        '    @GetMapping("/entries")\n    public String list() { return "[]"; }\n'
        "}\n",
        encoding="utf-8",
    )

    # 5. Go HTTP router
    go_dir = repo / "services" / "gateway"
    go_dir.mkdir(parents=True)
    (go_dir / "router.go").write_text(
        'package main\nimport "net/http"\nfunc setup(mux *http.ServeMux) {\n'
        '    mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {})\n'
        "}\n",
        encoding="utf-8",
    )

    # 6. Test files with routes must be ignored, while legitimate files ending in lowercase 'test' (e.g. latest.py) MUST be scanned
    (repo / "services" / "billing" / "src" / "test_app.py").write_text(
        '@app.get("/fake-test-route")\ndef fake(): pass\n',
        encoding="utf-8",
    )
    (repo / "services" / "billing" / "src" / "BillingTests.cs").write_text(
        '[ApiController]\npublic class BillingTests { [HttpGet("/fake")] public void X() {} }\n',
        encoding="utf-8",
    )

    # 7. FastAPI advanced route styles: module-qualified/typed APIRouter, @*.api_route, dotted @self.router.get, keyword path=, f-string/concat, add_api_route, latest.py
    cls_dir = repo / "services" / "hs-classifier" / "src" / "routers"
    cls_dir.mkdir(parents=True)
    (cls_dir / "latest.py").write_text(
        "import fastapi\n"
        'API_PREFIX = "/v1"\n'
        'router: fastapi.APIRouter = fastapi.APIRouter(prefix="/v1/classify")\n'
        "app = fastapi.FastAPI()\n\n"
        '@router.api_route("/batch", methods=("GET", "POST"))\n'
        "def batch_classify(): return {}\n\n"
        '@router.post(path="/single")\n'
        "def single_classify(): return {}\n\n"
        '@router.get(f"{API_PREFIX}/status")\n'
        "def status(): return {}\n\n"
        "class ClassifierRoutes:\n"
        '    self.router = fastapi.APIRouter(prefix="/v1/classify")\n'
        '    @self.router.delete("/cache/{item_id}")\n'
        "    def evict(): return {}\n\n"
        'app.add_api_route("/health", status, methods=["GET"])\n'
        'app.add_route(path=API_PREFIX + "/ready", route=status, methods={"GET"})\n',
        encoding="utf-8",
    )

    # 8. OpenAPI-first services MUST deduplicate sibling rest_http proposals:
    #    (a) active CNT-* providing active protocol: openapi CTR-* (with external contracts/ path)
    #    (b) co-located openapi.yaml inside a non-root container directory
    oapi_mapped_dir = repo / "services" / "payments" / "src"
    oapi_mapped_dir.mkdir(parents=True)
    (repo / "services" / "payments" / "pyproject.toml").write_text(
        '[project]\nname = "payments"\ndependencies = ["fastapi"]\n', encoding="utf-8"
    )
    (oapi_mapped_dir / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.post("/v1/pay")\ndef pay(): return {}\n',
        encoding="utf-8",
    )
    (repo / "contracts").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "payments-openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    cnt_pay = create_spec(eidos_dir, "container", "CNT-PAYMENTS", "Payments Service")
    d_pay = parse_spec_file(cnt_pay)
    d_pay.frontmatter["status"] = "active"
    d_pay.frontmatter["source_path"] = "services/payments/src"
    d_pay.frontmatter["contracts"] = {"provides": ["CTR-PAYMENTS-OPENAPI"], "consumes": []}
    cnt_pay.write_text(dump_spec_document(d_pay.frontmatter, d_pay.body), encoding="utf-8")
    ctr_pay = create_spec(eidos_dir, "contract", "CTR-PAYMENTS-OPENAPI", "Payments OpenAPI")
    d_ctr_pay = parse_spec_file(ctr_pay)
    d_ctr_pay.frontmatter["status"] = "active"
    d_ctr_pay.frontmatter["protocol"] = "openapi"
    d_ctr_pay.frontmatter["specification_file"] = "contracts/payments-openapi.yaml"
    ctr_pay.write_text(dump_spec_document(d_ctr_pay.frontmatter, d_ctr_pay.body), encoding="utf-8")

    oapi_coloc = repo / "services" / "invoices"
    (oapi_coloc / "src").mkdir(parents=True)
    (oapi_coloc / "pyproject.toml").write_text(
        '[project]\nname = "invoices"\ndependencies = ["fastapi"]\n', encoding="utf-8"
    )
    (oapi_coloc / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    (oapi_coloc / "src" / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/v1/inv")\ndef inv(): return []\n',
        encoding="utf-8",
    )

    # Map ASP.NET Core Controllers directory in an active rest_http contract to verify already_mapped
    ctr_p = create_spec(eidos_dir, "contract", "CTR-ORDERS-HTTP", "Orders REST HTTP Contract")
    ctr_doc = parse_spec_file(ctr_p)
    ctr_doc.frontmatter["protocol"] = "rest_http"
    ctr_doc.frontmatter["specification_file"] = "services/orders-api/Controllers"
    ctr_doc.frontmatter["status"] = "active"
    ctr_p.write_text(dump_spec_document(ctr_doc.frontmatter, ctr_doc.body), encoding="utf-8")

    bp = discover_repository_blueprint(repo_root=repo, eidos_dir=eidos_dir)
    rest_contracts = {c["specification_file"]: c for c in bp["contracts"] if c["protocol"] == "rest_http"}

    assert "services/orders-api/Controllers" in rest_contracts
    assert rest_contracts["services/orders-api/Controllers"]["route_count"] == 3
    assert rest_contracts["services/orders-api/Controllers"]["already_mapped"] is True
    assert rest_contracts["services/orders-api/Controllers"]["suggested_id"].endswith("-HTTP")

    assert "services/billing/src/app.py" in rest_contracts
    assert rest_contracts["services/billing/src/app.py"]["route_count"] == 2
    assert rest_contracts["services/billing/src/app.py"]["already_mapped"] is False

    assert "services/hs-classifier/src/routers" in rest_contracts
    assert rest_contracts["services/hs-classifier/src/routers"]["route_count"] == 7
    assert rest_contracts["services/hs-classifier/src/routers"]["already_mapped"] is False

    assert "services/catalog/src/controllers" in rest_contracts
    assert rest_contracts["services/catalog/src/controllers"]["route_count"] == 1
    assert rest_contracts["services/catalog/src/controllers"]["already_mapped"] is False

    assert "services/ledger/src/main/java/com/acme/LedgerController.java" in rest_contracts
    assert rest_contracts["services/ledger/src/main/java/com/acme/LedgerController.java"]["route_count"] == 1

    assert "services/gateway/router.go" in rest_contracts
    assert rest_contracts["services/gateway/router.go"]["route_count"] == 1

    # OpenAPI-covered services must NOT emit redundant sibling rest_http proposals
    assert not any("services/payments" in k for k in rest_contracts)
    assert not any("services/invoices" in k for k in rest_contracts)

    # Legacy WCF and test files must never be proposed
    assert not any("legacy-wcf" in k for k in rest_contracts)
    assert not any("test_app.py" in k for k in rest_contracts)
    assert not any("BillingTests.cs" in k for k in rest_contracts)

    # CLI JSON parity
    cli_res = runner.invoke(app, ["spec", "discover", "--path", str(repo), "--json"])
    assert cli_res.exit_code == 0, cli_res.stdout
    cli_payload = orjson.loads(cli_res.stdout)
    cli_rest = [c for c in cli_payload["contracts"] if c["protocol"] == "rest_http"]
    assert len(cli_rest) == 6
