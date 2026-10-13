"""Tests verifying specification scaffolding, reference checking, and delta lifecycle management (CTR-EIDOS-CLI, POL-002)."""

from pathlib import Path

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.parser import parse_spec_file
from eidos.core.scaffolder import (
    apply_delta,
    create_spec,
    delete_spec,
    find_spec_by_id_or_path,
    init_repository,
    list_specs,
)
from eidos.core.validator import SpecValidator

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
        kind="feature_addition",
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
            "feature_addition",
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
    """Verify top-level 'eidos create', 'eidos list', and 'eidos delete' shortcuts (CTR-EIDOS-CLI)."""
    # Create shortcut
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
    assert res_create.exit_code == 0
    assert "POL-009-RULE" in res_create.stdout

    # List shortcut
    res_list = runner.invoke(app, ["list", "--path", str(tmp_path)])
    assert res_list.exit_code == 0
    assert "POL-009-RULE" in res_list.stdout

    # Delete shortcut
    res_delete = runner.invoke(app, ["delete", "POL-009-RULE", "--path", str(tmp_path)])
    assert res_delete.exit_code == 0
    assert "Successfully deleted" in res_delete.stdout


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
            "feature_addition",
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
    """Verify that the plugin contains all 14 specialized skills (CAP-AGENT-001)."""
    from eidos.plugin.installer import locate_plugin_bundle
    from eidos.plugin.validator import validate_plugin_package

    bundle_dir = locate_plugin_bundle()
    report = validate_plugin_package(bundle_dir)

    assert report.is_valid
    expected_skills = {
        "eidos",
        "eidos-architecture",
        "eidos-context",
        "eidos-delta",
        "eidos-design",
        "eidos-drift",
        "eidos-explore",
        "eidos-extract",
        "eidos-init",
        "eidos-invariants",
        "eidos-portal",
        "eidos-product",
        "eidos-reconcile",
        "eidos-review",
    }
    assert expected_skills == set(report.skills)
    assert len(report.skills) == 14


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
    from eidos.core.scaffolder import init_repository
    from eidos.core.validator import SpecValidator

    success, _msg, _files = init_repository(
        project_dir=tmp_path,
        name="test-project",
        title="Test Project System",
    )
    assert success
    eidos_dir = tmp_path / "eidos"
    assert (eidos_dir / ".gitignore").exists()
    assert not (tmp_path / ".agents").exists()

    # Verify system.adoc exists but is empty
    assert (eidos_dir / "system.adoc").exists()
    assert (eidos_dir / "system.adoc").read_text(encoding="utf-8") == ""

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

    # Re-init with force succeeds
    success3, _, _ = init_repository(project_dir=tmp_path, force=True)
    assert success3

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
    assert (target / "eidos" / "system.adoc").read_text(encoding="utf-8") == ""
    assert (target / "eidos" / "policies" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "containers" / ".gitkeep").exists()
    assert (target / "eidos" / "architecture" / "components" / ".gitkeep").exists()
    assert not any(p for p in (target / "eidos").rglob("*.adoc") if p.name != "system.adoc")
    assert not (target / ".agents").exists()


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
    assert (target / "eidos" / "system.adoc").read_text(encoding="utf-8") == ""
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
    """Verify 'eidos index update' acts as an alias to sync (CTR-EIDOS-CLI)."""
    from eidos.core.scaffolder import init_repository

    init_repository(project_dir=tmp_path)
    res = runner.invoke(app, ["index", "update", "--path", str(tmp_path / "eidos")])
    assert res.exit_code == 0
    assert "index" in res.output


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
    assert "component" in res.stdout
    assert "index" in res.stdout
    assert "portal" in res.stdout
    assert "plugin" in res.stdout

    # Verify eidos spec containers and components
    res_containers = runner.invoke(app, ["spec", "containers"])
    assert res_containers.exit_code == 0
    assert "Containers" in res_containers.stdout

    res_components = runner.invoke(app, ["spec", "components"])
    assert res_components.exit_code == 0


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
