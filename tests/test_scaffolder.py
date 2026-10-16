"""Tests verifying specification scaffolding, reference checking, and delta lifecycle management (CTR-EIDOS-CLI, POL-002)."""

from pathlib import Path

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.lifecycle.scaffolder import (
    apply_delta,
    create_spec,
    delete_spec,
    find_spec_by_id_or_path,
    init_repository,
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
    """Verify that create_spec scaffolds syntactically valid documents for every schema type (CTR-EIDOS-CLI, POL-002, INV-SPIKE-ISOLATION)."""
    validator = SpecValidator()

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
        domain="DOM-TEST-AUTH",
    )
    assert cap_file.exists()
    assert_valid_spec(validator, cap_file)

    # 3. Policy
    pol_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="policy",
        raw_id="POL-003-AUTH-LOCK",
        title="Account Lockout Policy",
        category="security",
        rule="Lock account after 5 failed attempts.",
    )
    assert pol_file.exists()
    assert_valid_spec(validator, pol_file)

    # 4. Contract
    ctr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="contract",
        raw_id="CTR-AUTH-REST",
        title="Auth REST API",
        protocol="openapi",
        owning_container="CNT-AUTH-SERVICE",
        specification_file="src/auth.yaml",
    )
    assert ctr_file.exists()
    assert_valid_spec(validator, ctr_file)

    # 5. Container
    cnt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="container",
        raw_id="CNT-AUTH-SERVICE",
        title="Auth Microservice",
        container_type="service",
        runtime="Rust / Axum",
        package_path="packages/auth",
    )
    assert cnt_file.exists()
    assert_valid_spec(validator, cnt_file)

    # 6. Persona
    per_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="persona",
        raw_id="PER-SECURITY-AUDITOR",
        title="Security Auditor",
        role_type="auditor",
    )
    assert per_file.exists()
    assert_valid_spec(validator, per_file)

    # 7. Delta
    dlt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="delta",
        raw_id="DLT-2026-999",
        title="MFA Integration",
        kind="feature",
        motivation="Require TOTP MFA.",
    )
    assert dlt_file.exists()
    assert_valid_spec(validator, dlt_file)

    # 8. Exploration
    exp_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="exploration",
        raw_id="EXP-PASSKEY-SPIKE",
        title="WebAuthn Passkey Spike",
    )
    assert exp_file.exists()
    assert_valid_spec(validator, exp_file)


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
    # Create container and contract that references it
    create_spec(tmp_path, "container", "CNT-CORE", "Core Component")
    create_spec(tmp_path, "contract", "CTR-API", "API Contract", owning_container="CNT-CORE")

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
    """Verify SpecDelta transition in flat storage with in-place status update and symlink views (CTR-EIDOS-CLI, CAP-AGENT-001)."""
    dlt_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="delta",
        raw_id="DLT-2026-100",
        title="Rate Limiter",
        motivation="DDoS mitigation.",
    )
    assert dlt_file.exists()
    assert dlt_file.name == "dlt-2026-100.adoc"
    assert dlt_file.parent == tmp_path / "deltas"

    # Verify initial symlink in active/ and proposed/
    assert (tmp_path / "deltas" / "by-status" / "active" / "dlt-2026-100.adoc").exists()
    assert (tmp_path / "deltas" / "by-status" / "proposed" / "dlt-2026-100.adoc").exists()

    # Apply delta
    success, _msg, applied_path, _mutated = apply_delta(tmp_path, "DLT-2026-100")
    assert success
    assert applied_path is not None
    assert applied_path.resolve() == dlt_file.resolve()

    # Verify content status is updated in-place
    doc = parse_spec_file(applied_path)
    assert doc.frontmatter.get("status") == "applied"

    # Verify symlink moved to applied/ and removed from active/
    assert (tmp_path / "deltas" / "by-status" / "applied" / "dlt-2026-100.adoc").exists()
    assert not (tmp_path / "deltas" / "by-status" / "active" / "dlt-2026-100.adoc").exists()

    # Verify content status is updated and valid
    validator = SpecValidator()
    assert_valid_spec(validator, applied_path)


def test_cli_spec_commands(tmp_path):
    """Verify 'eidos spec' CLI command suite (CTR-EIDOS-CLI)."""
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

    # 5. Create delta via 'spec create' without --kind defaults to 'feature' (root 'create' shortcut is removed)
    res_dlt_spec = runner.invoke(
        app,
        ["spec", "create", "delta", "DLT-2026-501", "--title", "Spec Create Delta", "--path", str(tmp_path)],
    )
    assert res_dlt_spec.exit_code == 0
    doc_501 = parse_spec_file(tmp_path / "deltas" / "dlt-2026-501.adoc")
    assert doc_501.frontmatter["intent"]["kind"] == "feature"

    res_dlt_short = runner.invoke(
        app,
        ["create", "delta", "DLT-2026-502", "--title", "Shortcut Create Delta", "--path", str(tmp_path)],
    )
    assert res_dlt_short.exit_code != 0


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
            "--motivation",
            "Add delta management.",
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

    # 4. Sync views
    res_sync = runner.invoke(app, ["delta", "sync-views", "--path", str(tmp_path)])
    assert res_sync.exit_code == 0
    assert "Synchronized" in res_sync.stdout

    # 5. Apply delta
    res_apply = runner.invoke(app, ["delta", "apply", "DLT-2026-555", "--path", str(tmp_path)])
    assert res_apply.exit_code == 0
    assert "Successfully applied" in res_apply.stdout

    # 6. Delete applied delta
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
            "--rule",
            "CLI tools must be tested.",
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
            "--motivation",
            "Test CLI delta.",
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
    """Verify eidos/.gitignore is automatically created and isolates status views from project root."""
    create_spec(
        eidos_dir=tmp_path,
        doc_type_key="delta",
        raw_id="DLT-2026-999",
        title="Gitignore Isolation",
        motivation="Self-contained eidos/.gitignore",
    )
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert "deltas/by-status/" in content


def test_init_repository_programmatic(tmp_path):
    """Verify init_repository scaffolds an empty living specification skeleton tracked by .gitkeep without installing plugins (CTR-EIDOS-CLI)."""
    from eidos.core.lifecycle.scaffolder import init_repository
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

    # Re-init with force (without name/title) resets system.adoc to 0 bytes
    success3, _, _ = init_repository(project_dir=tmp_path, force=True)
    assert success3
    assert (eidos_dir / "system.adoc").read_text(encoding="utf-8") == ""

    # Validate directory (empty skeleton should be clean)
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert report.is_clean
    assert report.total_docs == 0


def test_init_repository_cli(tmp_path):
    """Verify eidos init CLI command scaffolds without installing plugins (CTR-EIDOS-CLI)."""
    target = tmp_path / "cli_init_target"
    result = runner.invoke(app, ["init", str(target), "--name", "my-app", "--title", "My Application"])
    assert result.exit_code == 0
    assert "Successfully initialized" in result.stdout
    assert (target / "eidos" / ".gitignore").exists()
    assert (target / "eidos" / "system.adoc").exists()
    sys_text = (target / "eidos" / "system.adoc").read_text(encoding="utf-8")
    assert "SYS-MY-APP" in sys_text
    assert "My Application" in sys_text
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
    """Verify eidos --version, -V, and version commands (CTR-EIDOS-CLI, POL-002)."""
    import eidos

    # 1. --version
    res_flag = runner.invoke(app, ["--version"])
    assert res_flag.exit_code == 0
    assert f"eidos {eidos.__version__}" in res_flag.output

    # 2. -V
    res_short = runner.invoke(app, ["-V"])
    assert res_short.exit_code == 0
    assert f"eidos {eidos.__version__}" in res_short.output

    # 3. version command
    res_cmd = runner.invoke(app, ["version"])
    assert res_cmd.exit_code == 0
    assert f"eidos {eidos.__version__}" in res_cmd.output


def test_cli_index_update_alias(tmp_path):
    """Verify 'eidos index sync' works and 'eidos index update' alias is removed (CTR-EIDOS-CLI)."""
    from eidos.core.lifecycle.scaffolder import init_repository

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

    # 2. Client Acme Design System extending Core
    acme_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="design-system",
        raw_id="DS-ACME",
        title="Acme Brand System",
        client_id="acme",
        extends_system="DS-CORE",
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
        design_system="DS-CORE",
    )
    assert btn_file.exists()
    assert_valid_spec(validator, btn_file)

    # Scaffold client override component
    hdr_file = create_spec(
        eidos_dir=tmp_path,
        doc_type_key="ui-component",
        raw_id="UIC-ACME-HEADER",
        title="Acme Header",
        design_system="DS-ACME",
        implements_component="UIC-CORE-HEADER",
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
        route="/checkout",
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
        domain="DOM-CHECKOUT",
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
            "--route",
            "/settings",
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
            "--client",
            "test-client",
            "--extends",
            "DS-CORE",
            "--path",
            str(tmp_path),
        ],
    )
    assert cli_res.exit_code == 0
    assert "Created and validated" in cli_res.stdout
    assert (tmp_path / "design" / "systems" / "test" / "system.adoc").exists()


def test_create_component_client_flow_and_safe_yaml(tmp_path):
    """Verify DLT-2026-032: component/client/flow scaffolding routes correctly and YAML-special input stays valid (INV-SPEC-VALIDATION)."""
    import yaml


    create_spec(tmp_path, "container", "CNT-APP", "App Container")
    tricky = 'Quote: "Test" #hash\nsecond line'

    cmp_file = create_spec(
        tmp_path, "component", "CMP-TEST-001", tricky, owning_container="CNT-APP", package_path="src/app/test.py"
    )
    assert cmp_file == tmp_path / "architecture" / "components" / "test-001.adoc"
    doc = parse_spec_file(cmp_file)
    assert doc.doc_type == "tech/component"
    assert doc.frontmatter["container"] == "CNT-APP"
    assert doc.frontmatter["name"] == tricky
    assert doc.frontmatter["source_path"] == "src/app/test.py"
    assert_valid_spec(SpecValidator(), cmp_file)

    # Bare IDs receive the correct prefix (no CNT-/CMP- cross-over)
    bare = create_spec(tmp_path, "tech/component", "BARE", "Bare", owning_container="CNT-APP")
    assert parse_spec_file(bare).doc_id == "CMP-BARE"

    clt_file = create_spec(tmp_path, "client", "CLT-WEB", "Web App", owning_container="CNT-APP")
    assert clt_file == tmp_path / "design" / "clients" / "web" / "client.adoc"
    assert parse_spec_file(clt_file).frontmatter["container"] == "CNT-APP"
    assert_valid_spec(SpecValidator(), clt_file)

    flow_file = create_spec(tmp_path, "flow", "LOGIN", "Login Flow", client_id="CLT-WEB")
    assert flow_file == tmp_path / "design" / "clients" / "web" / "flows" / "login.adoc"
    flow_doc = parse_spec_file(flow_file)
    assert flow_doc.doc_id == "FLOW-LOGIN"
    assert flow_doc.frontmatter["client"] == "CLT-WEB"
    assert_valid_spec(SpecValidator(), flow_file)

    cross_flow = create_spec(tmp_path, "design/flow", "FLOW-ONBOARD", "Onboarding")
    assert cross_flow == tmp_path / "design" / "flows" / "onboard.adoc"
    assert_valid_spec(SpecValidator(), cross_flow)

    # SpecDelta with quotes/colons/newlines in title and motivation remains valid YAML
    dlt_file = create_spec(
        tmp_path, "delta", "DLT-2026-900", 'Fix "quoted": title', motivation='Because: "reasons"\nand more'
    )
    fm_text = dlt_file.read_text(encoding="utf-8").split("---")[1]
    fm = yaml.safe_load(fm_text)
    assert fm["title"] == 'Fix "quoted": title'
    assert fm["intent"]["motivation"] == 'Because: "reasons"\nand more'
    assert_valid_spec(SpecValidator(), dlt_file)


def test_cli_spec_create_component_with_container(tmp_path):
    """Verify DLT-2026-032: 'eidos spec create component --container' scaffolds a tech/component."""
    create_spec(tmp_path, "container", "CNT-CORE", "Core")
    res = runner.invoke(
        app,
        [
            "spec", "create", "component", "CMP-CLI-TEST", "--title", "CLI Test",
            "--container", "CNT-CORE", "--source-path", "src/core/cli_test.py", "--path", str(tmp_path),
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
    for portal_path in (
        Path("src/eidos/portal/generator.py"),
        Path("src/eidos/portal/frontend/src/components/views/CatalogView.tsx"),
        Path("src/eidos/portal/frontend/src/components/views/DesignView.tsx"),
        Path("src/eidos/portal/frontend/src/components/views/ArchitectureView.tsx"),
    ):
        text = portal_path.read_text(encoding="utf-8", errors="ignore")
        for m in portal_forbidden.finditer(text):
            violations.append(f"{portal_path}: matched '{m.group(0)}'")

    assert violations == [], f"Forbidden self-references found in engine/skills: {violations}"


def test_foreign_repo_end_to_end_workflow(tmp_path):
    """Verify INV-FOREIGN-REPO-E2E: a non-Eidos polyglot repository initialized with eidos init runs the full lifecycle cleanly."""
    import subprocess

    repo = tmp_path / "src" / "acme_payments"
    ok, _, _ = init_repository(project_dir=repo, name="acme-payments", title="Acme Payments Platform")
    assert ok

    eidos_dir = repo / "eidos"
    # Populate a valid non-Eidos system.adoc, domain, capability, container, component, and test
    (eidos_dir / "system.adoc").write_text(
        """---
type: product/system
schema: schemas/v1/product/system.schema.yaml
id: SYS-ACME-001
title: "Acme Payments Platform"
archetypes:
  - microservice
business_identity:
  vision: "Process global payments reliably."
  target_users:
    - "Merchants"
  value_proposition: "Sub-second settlement."
technical_profile:
  primary_languages:
    - "TypeScript"
---
= Acme Payments Platform
""",
        encoding="utf-8",
    )
    create_spec(eidos_dir, "domain", "DOM-PAYMENTS", "Payments Domain")
    cnt_file = create_spec(
        eidos_dir, "container", "CNT-CHECKOUT", "Checkout Service", package_path="services/checkout"
    )
    assert cnt_file.exists()
    cmp_file = create_spec(
        eidos_dir,
        "component",
        "CMP-LEDGER",
        "Ledger Engine",
        owning_container="CNT-CHECKOUT",
        package_path="services/checkout/ledger.ts",
    )
    assert cmp_file.exists()

    # Add source and test file in foreign repo
    svc_dir = repo / "services" / "checkout"
    svc_dir.mkdir(parents=True)
    (svc_dir / "ledger.ts").write_text("export function recordEntry(amount: number): boolean {\n  return amount > 0;\n}\n", encoding="utf-8")

    # Initialize git and commit baseline
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "dev@acme.io"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Acme Dev"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, capture_output=True, check=True)

    # 1. Validate specs (both eidos_dir and eidos_dir/architecture under parent src/ path)
    val_report = SpecValidator().validate_directory(eidos_dir)
    assert val_report.is_clean, f"Validation errors: {val_report.schema_errors}, broken refs: {val_report.broken_references}"
    sub_report = SpecValidator().validate_directory(eidos_dir / "architecture")
    assert sub_report.is_clean, f"Subdir validation errors: {sub_report.schema_errors}, broken refs: {sub_report.broken_references}"

    # 2. Index sync & Context slice
    res_sync = runner.invoke(app, ["index", "sync", "--path", str(eidos_dir)])
    assert res_sync.exit_code == 0

    res_ctx = runner.invoke(
        app,
        ["context", "--component", "CNT-CHECKOUT", "--path", str(eidos_dir), "--format", "json"],
    )
    assert res_ctx.exit_code == 0
    assert "CNT-CHECKOUT" in res_ctx.stdout

    # 3. Drift check on clean foreign repo
    from eidos.vcs.drift import detect_drift

    drift_clean = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
    assert drift_clean.drift_detected is False
    assert drift_clean.is_clean is True


def test_frontmatter_mutations_preserve_yaml_comments_and_formatting(tmp_path):
    """Verify apply_delta, promote_component, inline_component, and execute_delta_mutations preserve YAML comments."""
    from eidos.core.lifecycle.scaffolder import execute_delta_mutations, inline_component, promote_component

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
schema: schemas/v1/tech/container.schema.yaml
id: CNT-CORE # inline id comment
name: "Core Container"
container_type: library
runtime: Python 3.12
package_path: src/core

# Internal C4 components list
components:
  # Parser component comment
  - id: CMP-PARSER
    name: "Parser"
    responsibility: "Parses specs."
    source_path: "src/core/parser.py"
---
= Core Container
""",
        encoding="utf-8",
    )

    # 1. Promote CMP-PARSER and verify comments remain intact
    promoted_path, _ = promote_component("CMP-PARSER", eidos_dir=eidos_dir, repo_root=tmp_path)
    assert promoted_path.exists()
    cnt_after_promote = cnt_file.read_text(encoding="utf-8")
    assert "# Top-level container comment" in cnt_after_promote
    assert "# inline id comment" in cnt_after_promote
    assert "# Internal C4 components list" in cnt_after_promote
    assert "# Parser component comment" in cnt_after_promote

    # 2. Inline CMP-PARSER back and verify comments still remain intact
    inline_component("CMP-PARSER", eidos_dir=eidos_dir, repo_root=tmp_path)
    cnt_after_inline = cnt_file.read_text(encoding="utf-8")
    assert "# Top-level container comment" in cnt_after_inline
    assert "# inline id comment" in cnt_after_inline
    assert "# Internal C4 components list" in cnt_after_inline
    assert "# Parser component comment" in cnt_after_inline

    # 3. Create a delta with comments and execute + apply it
    dlt_file = deltas_dir / "dlt-2026-777.adoc"
    dlt_file.write_text(
        """---
# Delta header comment
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-777
title: "Comment Preservation Delta"
status: proposed # status inline comment
intent:
  kind: feature
  motivation: "Keep comments intact."
mutations:
  - action: add_component
    target_component: CMP-ADDED
    details: "Added component."
---
= Delta
""",
        encoding="utf-8",
    )

    ok_exec, _, _ = execute_delta_mutations(eidos_dir, "DLT-2026-777")
    assert ok_exec
    cnt_after_add = cnt_file.read_text(encoding="utf-8")
    assert "# Top-level container comment" in cnt_after_add
    assert "# Parser component comment" in cnt_after_add
    assert "CMP-ADDED" in cnt_after_add

    ok_apply, _, _, _ = apply_delta(eidos_dir, "DLT-2026-777")
    assert ok_apply
    dlt_after_apply = dlt_file.read_text(encoding="utf-8")
    assert "# Delta header comment" in dlt_after_apply
    assert "status: applied # status inline comment" in dlt_after_apply




def test_scaffolder_backward_compatible_reexports():
    """DLT-2026-038: every previously public name in eidos.core.lifecycle.scaffolder / eidos.cli.main resolves to its new home."""
    import importlib
    import json

    snapshot = json.loads((Path(__file__).parent / "fixtures" / "public_api_names.json").read_text(encoding="utf-8"))
    assert set(snapshot) == {"eidos.core.lifecycle.scaffolder", "eidos.cli.main"}

    for module_name, names in snapshot.items():
        legacy = importlib.import_module(module_name)
        package_prefix = module_name.rsplit(".", 1)[0] + "."
        missing = [n for n in names if not hasattr(legacy, n)]
        assert not missing, f"{module_name} lost public names: {missing}"
        for name in names:
            obj = getattr(legacy, name)
            owner = getattr(obj, "__module__", None)
            if isinstance(owner, str) and owner.startswith(package_prefix):
                home = importlib.import_module(owner)
                assert getattr(home, name) is obj, f"{module_name}.{name} is not the object defined in {owner}"

    from eidos.core.lifecycle import catalog, delta_engine, initializer, scaffolder

    assert scaffolder.find_spec_by_id_or_path is catalog.find_spec_by_id_or_path
    assert scaffolder.VALID_SPEC_TYPES is catalog.VALID_SPEC_TYPES
    assert scaffolder.verify_delta_mutations is delta_engine.verify_delta_mutations
    assert scaffolder.init_repository is initializer.init_repository


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
    for order in ([[n] for n in names] + [list(reversed(names))]):
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
schema: schemas/v1/tech/container.schema.yaml
id: CNT-CORE
name: "Core Container"
container_type: library
runtime: Python 3.12
package_path: src

components:
  - id: CMP-MULTI-SRC
    name: 'Parser: "Core" Engine'
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
    assert promoted_doc.frontmatter["name"] == 'Parser: "Core" Engine'
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
    other_pol = create_spec(eidos_dir, "policy", "POL-REF-CHECK", title="Ref Policy", rule="Applies only to CNT-CORE-API.")
    _ = (api_cnt, other_pol)
    refs_to_core = find_references_to_spec(eidos_dir, "CNT-CORE", cnt_file)
    assert other_pol.resolve() not in refs_to_core

    # Also verify symlinked deltas/by-status/ do not produce duplicate references
    dlt_ref = create_spec(eidos_dir, "delta", "DLT-2026-888", title="Ref Delta", motivation="Touches CNT-CORE.")
    refs_with_delta = find_references_to_spec(eidos_dir, "CNT-CORE", cnt_file)
    assert refs_with_delta.count(dlt_ref.resolve()) == 1
    _ = delete_spec

    # 4. _set_frontmatter_scalar safely quotes values containing colons, hashes, and quotes
    from eidos.core.spec.parser import extract_frontmatter

    updated_raw = _set_frontmatter_scalar(
        cnt_file.read_text(encoding="utf-8"),
        "name",
        'Core: "Engine" #1',
    )
    reparsed = extract_frontmatter(updated_raw)
    assert reparsed[2] is None
    assert reparsed[0]["name"] == 'Core: "Engine" #1'

