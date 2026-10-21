"""Tests verifying specification scaffolding, reference checking, and delta lifecycle management (CTR-EIDOS-CLI, POL-002)."""

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
    """Verify that create_spec scaffolds syntactically valid documents for all 15 living spec types and rejects delta (CTR-EIDOS-CLI, POL-002, INV-SPIKE-ISOLATION)."""
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
        raw_id="ARCH-AUTH-001",
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

    # 2. Capability
    cap_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="capability",
        raw_id="CAP-AUTH-001",
        title="Password Login",
    )
    assert cap_file.exists()
    assert_valid_spec(validator, cap_file)

    # 3. Policy
    pol_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="policy",
        raw_id="POL-003-AUTH-LOCK",
        title="Account Lockout Policy",
    )
    assert pol_file.exists()
    assert_valid_spec(validator, pol_file)

    # 4. Container
    cnt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="container",
        raw_id="CNT-AUTH-SERVICE",
        title="Auth Microservice",
    )
    assert cnt_file.exists()
    assert_valid_spec(validator, cnt_file)

    # 5. Contract
    ctr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="contract",
        raw_id="CTR-AUTH-REST",
        title="Auth REST API",
    )
    assert ctr_file.exists()
    assert_valid_spec(validator, ctr_file)

    # 6. Persona
    per_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="persona",
        raw_id="PER-SECURITY-AUDITOR",
        title="Security Auditor",
    )
    assert per_file.exists()
    assert_valid_spec(validator, per_file)

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
    create_spec(tmp_path, "policy", "POL-001", "Policy 1")
    create_spec(tmp_path, "policy", "POL-002", "Policy 2")
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
    """Verify SpecDelta transition in flat storage with in-place status update (CTR-EIDOS-CLI, CAP-AGENT-001)."""
    dlt_file = create_delta(
        eidos_dir=tmp_path,
        raw_id="DLT-2026-100",
        title="Rate Limiter",
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

    # 4. Apply delta
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
    apply_res = runner.invoke(app, ["delta", "apply", "DLT-2026-888", "--path", str(tmp_path)])
    assert apply_res.exit_code == 0

    # 4. eidos spec delete
    del_res = runner.invoke(app, ["spec", "delete", "POL-100-CLI", "--path", str(tmp_path), "--force"])
    assert del_res.exit_code == 0


def test_all_skills_in_bundle():
    """Verify that the plugin contains the 4 cohesive workflow skills (CAP-AGENT-001, DLT-2026-037)."""
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
    }
    assert expected_skills == set(report.skills)
    assert len(report.skills) == 4


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
        name="test-project",
        title="Test Project System",
    )
    assert success
    eidos_dir = tmp_path / "eidos"
    assert (eidos_dir / ".gitignore").exists()
    assert not (tmp_path / ".agents").exists()

    # Verify system.adoc is scaffolded when name and title are provided
    assert (eidos_dir / "system.adoc").exists()
    sys_content = (eidos_dir / "system.adoc").read_text(encoding="utf-8")
    assert "SYS-TEST-PROJECT" in sys_content
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
        eidos_dir / "design" / "clients" / ".gitkeep",
        eidos_dir / "design" / "flows" / ".gitkeep",
        eidos_dir / "design" / "screens" / ".gitkeep",
        eidos_dir / "design" / "systems" / "core" / "components" / ".gitkeep",
    ]
    for gk in expected_gitkeeps:
        assert gk.exists(), f"Expected .gitkeep at {gk}"

    # Re-init without force must fail
    success2, msg2, _ = init_repository(project_dir=tmp_path, force=False)
    assert not success2
    assert "already exist" in msg2

    # Re-init with force (without name/title) preserves existing authored system.adoc
    success3, _, _ = init_repository(project_dir=tmp_path, force=True)
    assert success3
    assert "SYS-TEST-PROJECT" in (eidos_dir / "system.adoc").read_text(encoding="utf-8")

    # Validate directory
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert report.is_clean
    assert report.total_docs == 1


def test_init_repository_cli(tmp_path):
    """Verify eidos init CLI command scaffolds without installing plugins (CTR-EIDOS-CLI)."""
    target = tmp_path / "cli_init_target"
    result = runner.invoke(app, ["init", str(target), "--name", "my-app"])
    assert result.exit_code == 0
    assert "Successfully initialized" in result.stdout
    assert (target / "eidos" / ".gitignore").exists()
    assert (target / "eidos" / "system.adoc").exists()
    sys_text = (target / "eidos" / "system.adoc").read_text(encoding="utf-8")
    assert "SYS-MY-APP" in sys_text
    assert (target / "eidos" / "policies" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "containers" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "components" / ".gitkeep").exists()
    assert not any(p for p in (target / "eidos").rglob("*.adoc") if p.name != "system.adoc")
    assert not (target / ".agents").exists()


def test_init_repository_lite_cli(tmp_path):
    """Verify eidos init --lite scaffolds progressive minimal baseline skeleton (DLT-2026-050)."""
    target = tmp_path / "cli_init_lite_target"
    result = runner.invoke(app, ["init", str(target), "--lite", "--name", "my-lite-app"])
    assert result.exit_code == 0
    assert "Successfully initialized" in result.stdout
    eidos_dir = target / "eidos"
    assert (eidos_dir / ".gitignore").exists()
    assert (eidos_dir / "system.adoc").exists()
    assert (eidos_dir / "architecture" / "containers" / ".gitkeep").exists()
    assert (eidos_dir / "domains" / "core" / "capabilities" / ".gitkeep").exists()
    assert (eidos_dir / "deltas" / ".gitkeep").exists()
    # Lite profile skips heavy optional taxonomies
    assert not (eidos_dir / "architecture" / "contracts").exists()
    assert not (eidos_dir / "policies").exists()
    assert not (eidos_dir / "personas").exists()
    assert not (eidos_dir / "design").exists()
    assert not (eidos_dir / "explorations").exists()


def test_init_repository_custom(tmp_path):
    """Verify programmatic init_repository call."""
    target = tmp_path / "custom_init_target"
    ok, _msg, _created = init_repository(
        project_dir=target,
        name="custom-service",
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
    assert not any(p for p in (target / "eidos").rglob("*.adoc") if p.name != "system.adoc")
    assert not (target / ".agents").exists()


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

    # 3. version subcommand is removed
    res_cmd = runner.invoke(app, ["version"])
    assert res_cmd.exit_code != 0


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

    # Verify component subgroup is nested under eidos spec
    res_spec_help = runner.invoke(app, ["spec", "--help"])
    assert res_spec_help.exit_code == 0
    assert "component" in res_spec_help.stdout

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
    """Verify scaffolding of base and client design systems with W3C tokens (CAP-AGENT-001)."""
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
    """Verify scaffolding of UI components under a design system (CAP-AGENT-001)."""
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
    """Verify screen specification and capability experience linkage (CAP-AGENT-001)."""
    validator = SpecValidator()

    # Create screen
    screen_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="screen",
        raw_id="SCR-CHECKOUT",
        title="Checkout View",
    )
    assert screen_file.exists()
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
    # CLI screen creation
    res = runner.invoke(
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
    assert res.exit_code == 0
    assert "Created and validated" in res.stdout
    assert (tmp_path / "design" / "screens" / "settings.adoc").exists()

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
    assert parse_spec_file(clt_file).frontmatter["design_system"] == "DS-CORE"
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
    """Verify INV-ENGINE-GENERICITY: runtime (src/eidos/), portal frontend views, and skills (assets/plugin_reference/skills/) contain zero Eidos-self IDs or paths."""
    import re

    forbidden = re.compile(
        r"\b(SYS-EIDOS-\d+|CNT-EIDOS-[A-Z0-9_-]+|CTR-EIDOS-[A-Z0-9_-]+|DOM-SPEC-ENGINE|DOM-AGENT-INTELLIGENCE|DOM-DEVEX-VCS|CAP-PROD-\d+|CAP-AGENT-\d+|CAP-VCS-\d+)\b|src/eidos"
    )
    scan_roots = [Path("src/eidos"), Path("assets/plugin_reference/skills")]
    violations: list[str] = []

    for root in scan_roots:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if any(part in ("__pycache__", "node_modules", "dist") for part in p.parts):
                continue
            if p.suffix.lower() not in (".py", ".ts", ".tsx", ".md", ".json", ".yaml"):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in forbidden.finditer(text):
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


def test_foreign_repo_end_to_end_workflow(tmp_path):
    """Verify INV-FOREIGN-REPO-E2E: a non-Eidos polyglot repository initialized with eidos init runs the full lifecycle cleanly."""
    import subprocess

    import eidos.core.index.graph_index as gi
    from eidos.core.spec.parser import dump_spec_document
    from eidos.portal.builder import build_static_portal

    repo = tmp_path / "src" / "acme_payments"
    ok, _, _ = init_repository(project_dir=repo, name="acme-payments", title="Acme Payments Platform")
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
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None


def test_frontmatter_mutations_preserve_yaml_comments_and_formatting(tmp_path):
    """Verify apply_delta, promote_component, and inline_component preserve YAML comments."""
    from eidos.core.lifecycle.scaffolder import inline_component, promote_component

    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)

    cnt_file = cnts_dir / "core.adoc"
    cnt_file.write_text(
        """---
# Top-level container comment
type: tech/container
id: CNT-CORE # inline id comment
title: "Core Container"
container_type: library
runtime: Python 3.12
source_path: src/core

# Internal C4 components list
components:
  # Parser component comment
  - id: CMP-PARSER
    title: "Parser"
    responsibility: "Parses specs."
    source_path: "src/core/parser.py"
---
= Core Container
""",
        encoding="utf-8",
    )

    # 1. Promote CMP-PARSER and verify container-level comments remain intact
    promoted_path, _ = promote_component("CMP-PARSER", eidos_dir=eidos_dir, repo_root=tmp_path)
    assert promoted_path.exists()
    cnt_after_promote = cnt_file.read_text(encoding="utf-8")
    assert "# Top-level container comment" in cnt_after_promote
    assert "# inline id comment" in cnt_after_promote
    assert "# Internal C4 components list" in cnt_after_promote

    # 2. Inline CMP-PARSER back and verify comments still remain intact
    inline_component("CMP-PARSER", eidos_dir=eidos_dir, repo_root=tmp_path)
    cnt_after_inline = cnt_file.read_text(encoding="utf-8")
    assert "# Top-level container comment" in cnt_after_inline
    assert "# inline id comment" in cnt_after_inline
    assert "# Internal C4 components list" in cnt_after_inline
    assert "CMP-PARSER" in cnt_after_inline

    # 3. Create a delta with comments and apply it
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
    parent: CNT-CORE
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
        "eidos.cli.component_commands",
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
    """DLT-2026-065: lossless promote_component / inline_component and word-bounded find_references_to_spec # eidos:fixture."""
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar, find_references_to_spec
    from eidos.core.lifecycle.scaffolder import create_spec, delete_spec, inline_component, promote_component
    from eidos.core.spec.parser import parse_spec_file

    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    cnts_dir.mkdir(parents=True)
    (tmp_path / "src" / "a.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")

    cnt_file = cnts_dir / "core.adoc"
    cnt_file.write_text(
        """---
type: tech/container
id: CNT-CORE
title: "Core Container"
container_type: library
runtime: Python 3.12
source_path: src

components:
  - id: CMP-MULTI-SRC
    title: 'Parser: "Core" Engine'
    responsibility: "Handles parsing: YAML & AsciiDoc."
    source_path:
      - "src/a.py"
      - "src/b.py"
    decisions:
      - id: DEC-COMP-001
        title: "Use safe YAML dump"
        status: accepted
        context: "Raw f-strings corrupt list source_path."
        decision: "Serialize via yaml.safe_dump."
    invariants:
      - id: INV-COMP-ROUNDTRIP
        rule: "Preserve component invariants across promote/inline."
        test_binding: "tests/test_scaffolder.py::test_lossless_component_promote_inline_and_word_bounded_references"
---
= Core Container
""",
        encoding="utf-8",
    )

    # 1. Promote CMP-MULTI-SRC and verify list[str] source_path, decisions, and invariants are preserved
    promoted_path, _ = promote_component("CMP-MULTI-SRC", eidos_dir=eidos_dir, repo_root=tmp_path)
    promoted_doc = parse_spec_file(promoted_path)
    assert not promoted_doc.errors
    assert promoted_doc.frontmatter["source_path"] == ["src/a.py", "src/b.py"]
    assert promoted_doc.frontmatter["title"] == 'Parser: "Core" Engine'
    assert len(promoted_doc.frontmatter.get("decisions", [])) == 1
    assert promoted_doc.frontmatter["decisions"][0]["id"] == "DEC-COMP-001"
    assert len(promoted_doc.frontmatter.get("invariants", [])) == 1
    assert promoted_doc.frontmatter["invariants"][0]["id"] == "INV-COMP-ROUNDTRIP"

    # 2. Inline CMP-MULTI-SRC back into CNT-CORE and verify round-trip preservation
    inline_component("CMP-MULTI-SRC", eidos_dir=eidos_dir, repo_root=tmp_path)
    cnt_doc = parse_spec_file(cnt_file)
    assert not cnt_doc.errors
    inlined_comp = cnt_doc.frontmatter["components"][0]
    assert inlined_comp["source_path"] == ["src/a.py", "src/b.py"]
    assert inlined_comp["decisions"][0]["id"] == "DEC-COMP-001"
    assert inlined_comp["invariants"][0]["id"] == "INV-COMP-ROUNDTRIP"

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
    delete_spec succeeds when entity is only referenced by an applied SpecDelta, and promote_component preserves status: accepted."""
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar, find_references_to_spec
    from eidos.core.lifecycle.scaffolder import inline_component, promote_component
    from eidos.core.spec.parser import extract_frontmatter
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

    # 3. Promote an inline component with status: planned and verify schema validation passes
    cnt_path = create_spec(eidos_dir, "container", "CNT-ACC", title="Accepted Container")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["source_path"] = "src/acc"
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-ACC-WORKER",
            "title": "Accepted Worker",
            "status": "planned",
            "source_path": "src/acc/worker.py",
            "responsibility": "Executes accepted background jobs.",
        }
    ]
    from eidos.core.spec.parser import dump_spec_document

    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")
    promoted_path, _ = promote_component("CMP-ACC-WORKER", eidos_dir=eidos_dir, repo_root=tmp_path)
    val = SpecValidator()
    promoted_doc = parse_spec_file(promoted_path)
    val.validate_document(promoted_doc)
    assert not promoted_doc.errors, promoted_doc.errors
    assert promoted_doc.frontmatter["status"] == "planned"

    # 4. Inline CMP-ACC-WORKER back into CNT-ACC and verify status: planned is preserved
    inline_component("CMP-ACC-WORKER", eidos_dir=eidos_dir, repo_root=tmp_path)
    cnt_inlined_doc = parse_spec_file(cnt_path)
    val.validate_document(cnt_inlined_doc)
    assert not cnt_inlined_doc.errors, cnt_inlined_doc.errors
    assert cnt_inlined_doc.frontmatter["components"][0]["status"] == "planned"


def test_a5_a6_frontmatter_scalar_and_components_surgical_edge_cases() -> None:
    """A5 & A6: _set_frontmatter_scalar block-scalar/list-item/special-string handling and _mutate_frontmatter_components flow/comment handling."""
    from eidos.core.lifecycle.catalog import _mutate_frontmatter_components, _set_frontmatter_scalar
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

    # A6a: Removing the last block component with interior comments produces valid `components: []`
    raw_cnt_comments = (
        "---\n"
        "type: tech/container\n"
        "id: CNT-TEST\n"
        "components: # inline header comment\n"
        "  # interior comment before item\n"
        "  - id: CMP-ONLY\n"
        "    title: Only Component\n"
        "status: active\n"
        "---\n"
        "= Container\n"
    )
    after_remove = _mutate_frontmatter_components(raw_cnt_comments, "remove", "CMP-ONLY")
    assert after_remove is not None
    fm_rem, _, err_rem = extract_frontmatter(after_remove)
    assert err_rem is None, f"Invalid YAML after removing last component: {err_rem}\n{after_remove}"
    assert fm_rem["components"] == []
    assert "# inline header comment" in after_remove

    # A6b: Flow-style non-empty components list `components: [{id: CMP-FLOW, title: Flow}]`
    raw_flow = (
        "---\n"
        "type: tech/container\n"
        "id: CNT-FLOW\n"
        "components: [{id: CMP-FLOW, title: Flow}]\n"
        "status: active\n"
        "---\n"
        "= Flow Container\n"
    )
    after_flow_replace = _mutate_frontmatter_components(
        raw_flow,
        "replace",
        "CMP-FLOW",
        {"id": "CMP-NEW", "title": "New Comp"},
    )
    assert after_flow_replace is not None
    fm_flow, _, err_flow = extract_frontmatter(after_flow_replace)
    assert err_flow is None, err_flow
    assert [c["id"] for c in fm_flow["components"]] == ["CMP-NEW"]


def test_a7_a8_a11_catalog_scaffolder_and_design_edge_cases(tmp_path: Path) -> None:
    """A7, A8, A11: path boundary & reference detection, list_specs filters/types, and design_system path / resolution."""
    from eidos.core.lifecycle.catalog import find_references_to_spec, find_spec_by_id_or_path
    from eidos.core.lifecycle.design_engine import _resolve_design_entity_ref
    from eidos.core.lifecycle.scaffolder import list_specs
    from eidos.core.spec.parser import dump_spec_document

    eidos_dir = tmp_path / "eidos"
    init_repository(tmp_path, name="TestSys")

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
            "See eidos/policies/pol-target.adoc for details.",
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

    # A8: list_specs supports --type system, --status active (including default-status containers and inline components on draft containers)
    cnt_path = create_spec(eidos_dir, "container", "CNT-API", "API Container")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["status"] = "draft"
    cnt_doc.frontmatter["components"] = [
        {
            "id": "CMP-INLINE-ACT",
            "title": "Active Inline",
            "status": "active",
            "responsibility": "Inline work",
        }
    ]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")
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
    from eidos.core.lifecycle.delta_engine import (
        _check_structural_subtarget_precondition,
        _find_inline_component_dict,
    )
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec, inline_component, promote_component
    from eidos.core.spec.parser import parse_spec_file

    # --- A8: init_repository strips whitespace-only name and title ---
    ws_a8 = tmp_path / "my-clean-service"
    ws_a8.mkdir()
    init_repository(ws_a8, name="   ", title="  My Clean Service  ")
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
    init_repository(ws, name="ws-a", title="WS A")
    eidos_dir = ws / "eidos"
    cnt_path = create_spec(eidos_dir, "container", "CNT-API-GATEWAY", "API Gateway")
    assert find_spec_by_id_or_path(eidos_dir, "cnt-api-gateway.adoc") == cnt_path.resolve()

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
    )
    assert scr_path.parent == eidos_dir / "design" / "screens"

    flow_path = create_spec(
        eidos_dir,
        "flow",
        "FLOW-CHECKOUT",
        "Checkout Flow",
    )
    assert flow_path.parent == eidos_dir / "design" / "flows"

    # --- A1: _find_inline_component_dict and _check_structural_subtarget_precondition ---
    cmp_promoted = create_spec(
        eidos_dir,
        "component",
        "CMP-PROMOTED",
        "Promoted Auth",
    )
    cnt_path.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-API-GATEWAY\n"
        "title: API Gateway\n"
        "container_type: service\n"
        "runtime: Python\n"
        "invariants:\n"
        "  - id: INV-TOP-001\n"
        "    rule: Top rule\n"
        "technical:\n"
        "  invariants:\n"
        "    - id: INV-TECH-001\n"
        "      rule: Tech rule\n"
        "components:\n"
        "  - id: CMP-INLINE-C4\n"
        "    title: Inline C4\n"
        "    responsibility: Handle C4\n"
        "    invariants:\n"
        "      - id: INV-INLINE-001\n"
        "        rule: Inline rule\n"
        "    decisions:\n"
        "      - id: DEC-INLINE-001\n"
        "        title: Inline Dec\n"
        "    contracts:\n"
        "      provides:\n"
        "        - CTR-INLINE-001\n"
        "---\n= API Gateway\n",
        encoding="utf-8",
    )
    cnt_doc = parse_spec_file(cnt_path)
    cmp_doc = parse_spec_file(cmp_promoted)
    assert _find_inline_component_dict("CMP-PROMOTED", cnt_doc.frontmatter) is None
    assert _find_inline_component_dict("CMP-INLINE-C4", cnt_doc.frontmatter) is not None

    specs_by_id = {
        "CNT-API-GATEWAY": (cnt_path, cnt_doc.doc_type, cnt_doc.frontmatter),
        "CMP-PROMOTED": (cmp_promoted, cmp_doc.doc_type, cmp_doc.frontmatter),
    }
    container_components = {"CMP-PROMOTED": "CNT-API-GATEWAY", "CMP-INLINE-C4": "CNT-API-GATEWAY"}

    # Subtarget precondition checks for INV-*, DEC-*, CTR-* on container and inline CMP-*
    assert (
        _check_structural_subtarget_precondition(
            {"target": "INV-TOP-001", "parent": "CNT-API-GATEWAY"}, specs_by_id, container_components
        )
        is True
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target": "INV-TECH-001", "parent": "CNT-API-GATEWAY"}, specs_by_id, container_components
        )
        is True
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target": "INV-INLINE-001", "parent": "CMP-INLINE-C4"}, specs_by_id, container_components
        )
        is True
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target": "INV-TOP-001", "parent": "CMP-INLINE-C4"}, specs_by_id, container_components
        )
        is False
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target": "DEC-INLINE-001", "parent": "CMP-INLINE-C4"}, specs_by_id, container_components
        )
        is True
    )
    assert (
        _check_structural_subtarget_precondition(
            {"target": "CTR-INLINE-001", "parent": "CMP-INLINE-C4"}, specs_by_id, container_components
        )
        is True
    )

    # --- A4: promote_component and inline_component when eidos_dir.name != 'eidos' ---
    custom_specs = tmp_path / "custom_specs"
    custom_cnt = custom_specs / "architecture" / "containers" / "core.adoc"
    custom_cnt.parent.mkdir(parents=True)
    custom_cnt.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core\n"
        "components:\n"
        "  - id: CMP-SUB\n"
        "    title: null\n"
        "    responsibility: null\n"
        "---\n= Core\n",
        encoding="utf-8",
    )
    promoted_sub, _ = promote_component("CMP-SUB", eidos_dir=custom_specs)
    assert promoted_sub.exists()
    inlined_cnt, _ = inline_component("CMP-SUB", eidos_dir=custom_specs)
    assert inlined_cnt == custom_cnt
    assert not promoted_sub.exists()
    inlined_doc = parse_spec_file(custom_cnt)
    all_inlined = list(inlined_doc.frontmatter.get("components") or [])
    assert any(isinstance(c, dict) and c.get("id") == "CMP-SUB" for c in all_inlined)

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
    """Verify get_spec_schema_info and 'eidos spec schema' dynamically load all 16 v1 schemas from assets/schemas/v1/."""
    import orjson

    from eidos.core.lifecycle.scaffolder import get_spec_schema_info

    all_schemas = get_spec_schema_info()
    assert isinstance(all_schemas, list)
    assert len(all_schemas) == 16

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
        synthetic_path = (
            tmp_path / "eidos" / "architecture" / "components" / "example.adoc"
            if ctype == "tech/component"
            else tmp_path / "example.adoc"
        )
        synthetic_path.parent.mkdir(parents=True, exist_ok=True)
        synthetic_path.write_text(f"{schema_info['example_frontmatter']}\n= Example\n", encoding="utf-8")
        doc = parse_spec_file(synthetic_path)
        validator.validate_document(doc)
        assert doc.is_valid, f"Synthesized example for {ctype} failed schema validation: {doc.validation_errors}"

        # Lookup by short_type and canonical type must resolve to the same schema
        by_short = get_spec_schema_info(schema_info["short_type"])
        by_canon = get_spec_schema_info(ctype)
        assert isinstance(by_short, dict) and by_short["type"] == ctype
        assert isinstance(by_canon, dict) and by_canon["type"] == ctype

    # Verify CLI 'eidos spec schema' summary and single-type inspection (both Rich and --json)
    res_all_json = runner.invoke(app, ["spec", "schema", "--json"])
    assert res_all_json.exit_code == 0
    payload_all = orjson.loads(res_all_json.stdout)
    assert payload_all["count"] == 16

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
