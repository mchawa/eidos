"""Tests verifying the Eidos specification validator and repo dogfooding."""

from pathlib import Path

import pytest

from eidos.core.validator import SpecValidator


@pytest.fixture
def validator():
    return SpecValidator()


def test_bundled_schemas_loaded(validator):
    """Verify that all core v1 schemas are successfully preloaded."""
    expected_schemas = [
        "system.schema.yaml",
        "domain.schema.yaml",
        "capability.schema.yaml",
        "policy.schema.yaml",
        "persona.schema.yaml",
        "exploration.schema.yaml",
        "delta.schema.yaml",
        "architecture.schema.yaml",
        "container.schema.yaml",
        "contract.schema.yaml",
    ]
    for schema_name in expected_schemas:
        assert schema_name in validator._schema_cache, f"Missing schema: {schema_name}"


def test_validate_eidos_specifications(validator):
    """Verify INV-SPEC-VALIDATION, INV-SPEC-GRAPH-INTEGRITY, and INV-CLIENT-DESIGN-BINDING: all living specifications pass schema validation and entity ID graph integrity."""
    eidos_dir = Path("eidos")
    assert eidos_dir.exists(), "eidos/ specification directory does not exist"

    report = validator.validate_directory(eidos_dir)
    assert report.total_docs > 0, "No specification documents found"
    assert report.is_clean, (
        f"Validation failed with {report.invalid_docs} invalid docs.\n"
        f"Errors: {report.schema_errors}\n"
        f"Broken references: {report.broken_references}"
    )
    assert len(report.entity_index) == report.total_docs
    assert "CNT-EIDOS-CLI" in report.entity_index
    assert "DOM-SPEC-ENGINE" in report.entity_index
    assert "DOM-EXPERIENCE-PORTAL" in report.entity_index
    assert "DOM-AGENT-INTELLIGENCE" in report.entity_index
    assert "DOM-VCS-LIFECYCLE" in report.entity_index
    assert "PER-SOFTWARE-ARCHITECT" in report.entity_index
    assert "PER-SOFTWARE-ENGINEER" in report.entity_index
    assert "PER-DESIGN-ENGINEER" in report.entity_index
    assert "POL-001-GIT-SOURCE-OF-TRUTH" in report.entity_index


def test_delta_affects_validation_broken_references(tmp_path, validator):
    """Verify Initiative A: SpecDelta affects.* entity ID references are validated and report broken references."""
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    comps_dir = eidos_dir / "architecture" / "components"
    deltas_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)

    # Valid component
    (comps_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-TEST-CORE\n"
        "name: Core\n"
        "container_type: library\n"
        "runtime: python\n"
        "package_path: src/core\n"
        "---\n"
        "= Core\n"
    )

    # Delta with 1 valid component and 3 broken references
    (deltas_dir / "dlt-test.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-TEST\n"
        "title: Test Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: feature_addition\n"
        "  motivation: Testing\n"
        "mutations:\n"
        "  - action: add_component\n"
        "affects:\n"
        "  components:\n"
        "    - CMP-TEST-CORE\n"
        "    - CMP-NONEXISTENT\n"
        "  contracts:\n"
        "    - CTR-NONEXISTENT\n"
        "  policies:\n"
        "    - POL-NONEXISTENT\n"
        "---\n"
        "= Delta\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "CMP-NONEXISTENT" in broken_str
    assert "CTR-NONEXISTENT" in broken_str
    assert "POL-NONEXISTENT" in broken_str
    assert "CMP-TEST-CORE" not in broken_str


def test_capability_semantic_references_broken(tmp_path, validator):
    """Verify Initiative A: Capability domain and technical references are checked."""
    eidos_dir = tmp_path / "eidos"
    caps_dir = eidos_dir / "domains" / "sub"
    comps_dir = eidos_dir / "architecture" / "components"
    caps_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)

    # Valid component
    (comps_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-TEST-CORE\n"
        "name: Core\n"
        "container_type: library\n"
        "runtime: python\n"
        "package_path: src/core\n"
        "---\n"
        "= Core\n"
    )

    (caps_dir / "cap-test.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-TEST-001\n"
        "title: Cap Test\n"
        "domain: DOM-NONEXISTENT\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Test\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-GHOST\n"
        "  components:\n"
        "    - CMP-TEST-CORE\n"
        "    - CMP-GHOST\n"
        "    - packages/local-pkg\n"
        "  contracts:\n"
        "    interfaces:\n"
        "      - CTR-GHOST-IFACE\n"
        "    provides:\n"
        "      - CTR-GHOST-PROV\n"
        "    requires:\n"
        "      - CTR-GHOST-REQ\n"
        "---\n"
        "= Cap\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "DOM-NONEXISTENT" in broken_str
    assert "CNT-GHOST" in broken_str
    assert "CMP-GHOST" in broken_str
    assert "CTR-GHOST-IFACE" in broken_str
    assert "CTR-GHOST-PROV" in broken_str
    assert "CTR-GHOST-REQ" in broken_str
    assert "CMP-TEST-CORE" not in broken_str
    assert "packages/local-pkg" not in broken_str


def test_component_semantic_references_broken(tmp_path, validator):
    """Verify Initiative A: Container and Component dependencies, interfaces, and contracts are checked via x-eidos-ref."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)

    (cnts_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-TEST-CLI\n"
        "name: CLI\n"
        "container_type: cli_app\n"
        "runtime: python\n"
        "package_path: src/cli\n"
        "domain_binding: DOM-NONEXISTENT\n"
        "dependencies:\n"
        "  internal_components:\n"
        "    - CMP-MISSING-DEP\n"
        "contracts:\n"
        "  provides:\n"
        "    - CTR-MISSING-PROV\n"
        "  requires:\n"
        "    - CTR-MISSING-REQ\n"
        "---\n"
        "= CLI\n"
    )

    (comps_dir / "comp.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-TEST-PARSER\n"
        "name: Parser\n"
        "container: CNT-MISSING-CONTAINER\n"
        "status: planned\n"
        "responsibility: Parse specs\n"
        "interfaces:\n"
        "  consumes:\n"
        "    - CMP-MISSING-PEER\n"
        "---\n"
        "= Parser\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "DOM-NONEXISTENT" in broken_str
    assert "CMP-MISSING-DEP" in broken_str
    assert "CTR-MISSING-PROV" in broken_str
    assert "CTR-MISSING-REQ" in broken_str
    assert "CNT-MISSING-CONTAINER" in broken_str
    assert "CMP-MISSING-PEER" in broken_str


def test_contract_and_domain_semantic_references(tmp_path, validator):
    """Verify Initiative A: Contract owning_component and Domain technical_mapping are validated."""
    eidos_dir = tmp_path / "eidos"
    contracts_dir = eidos_dir / "architecture" / "contracts"
    domains_dir = eidos_dir / "domains" / "sub"
    explorations_dir = eidos_dir / "explorations"
    contracts_dir.mkdir(parents=True)
    domains_dir.mkdir(parents=True)
    explorations_dir.mkdir(parents=True)

    # 1. Contract with non-existent owning component
    (contracts_dir / "broken_contract.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-TEST-API\n"
        "title: Test API\n"
        "protocol: openapi\n"
        "owning_component: CMP-NONEXISTENT-OWNER\n"
        "specification_file: contracts/api.yaml\n"
        "---\n"
        "= Contract\n"
    )

    # 2. Domain with non-existent dependencies and components
    (domains_dir / "domain.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "id: DOM-SUB-001\n"
        "name: Sub Domain\n"
        "classification: core\n"
        "boundaries:\n"
        "  responsibilities: ['testing']\n"
        "technical_mapping:\n"
        "  dependencies:\n"
        "    - DOM-GHOST-DOMAIN\n"
        "  components:\n"
        "    - CMP-GHOST-COMP\n"
        "---\n"
        "= Domain\n"
    )

    # 3. Exploration with non-existent resulting delta
    (explorations_dir / "exp.adoc").write_text(
        "---\n"
        "type: product/exploration\n"
        "id: EXP-2026-TEST\n"
        "title: Spike\n"
        "status: adopted\n"
        "hypothesis: Test\n"
        "exit_criteria:\n"
        "  max_duration: 1_week\n"
        "decision:\n"
        "  outcome: adopted\n"
        "  resulting_delta_id: DLT-NONEXISTENT-DELTA\n"
        "---\n"
        "= Spike\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "CMP-NONEXISTENT-OWNER" in broken_str
    assert "DOM-GHOST-DOMAIN" in broken_str
    assert "CMP-GHOST-COMP" in broken_str
    assert "DLT-NONEXISTENT-DELTA" in broken_str


def test_duplicate_entity_id_detection(tmp_path, validator):
    """Verify Initiative A: Duplicate entity IDs across catalog documents are detected and reported."""
    eidos_dir = tmp_path / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    comps_dir.mkdir(parents=True)

    (comps_dir / "comp1.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-DUPLICATE-ID\n"
        "name: First\n"
        "container_type: service\n"
        "runtime: python\n"
        "package_path: src/first\n"
        "---\n"
        "= First\n"
    )

    (comps_dir / "comp2.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-DUPLICATE-ID\n"
        "name: Second\n"
        "container_type: service\n"
        "runtime: python\n"
        "package_path: src/second\n"
        "---\n"
        "= Second\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "Duplicate entity ID 'CMP-DUPLICATE-ID'" in broken_str


def test_client_and_flow_semantic_validation(tmp_path, validator):
    """Verify Client (CLT-*) and Flow (FLOW-*) references to design systems, containers, and screens."""
    eidos_dir = tmp_path / "eidos"
    clients_dir = eidos_dir / "design" / "clients" / "test-web"
    flows_dir = clients_dir / "flows"
    screens_dir = clients_dir / "screens"
    flows_dir.mkdir(parents=True)
    screens_dir.mkdir(parents=True)

    # Broken client referencing non-existent design system and container
    (clients_dir / "client.adoc").write_text(
        "---\n"
        "type: design/client\n"
        "id: CLT-TEST-WEB\n"
        "title: Web Client\n"
        "status: active\n"
        "platform: web\n"
        "design_system: DS-NONEXISTENT\n"
        "container: CMP-NONEXISTENT\n"
        "---\n"
        "= Client\n"
    )

    # Broken flow referencing non-existent client
    (flows_dir / "flow.adoc").write_text(
        "---\n"
        "type: design/flow\n"
        "id: FLOW-TEST-EXP\n"
        "title: Test Flow\n"
        "status: active\n"
        "flow_type: client_internal\n"
        "client: CLT-GHOST-CLIENT\n"
        "steps:\n"
        "  - step: 1\n"
        "    title: Step 1\n"
        "    action: Action\n"
        "---\n"
        "= Flow\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "DS-NONEXISTENT" in broken_str
    assert "CMP-NONEXISTENT" in broken_str
    assert "CLT-GHOST-CLIENT" in broken_str


def test_strict_schemas_reject_unknown_fields_and_enforce_enums_and_patterns(tmp_path, validator):
    """Verify A5: Strict schemas enforce additionalProperties: false, status enums (with planned), and ID regex patterns."""
    # 1. Verify all 16 bundled schemas enforce additionalProperties: false on root
    for schema_key, schema_data in validator._schema_cache.items():
        if schema_key.endswith(".schema.yaml"):
            assert schema_data.get("additionalProperties") is False, (
                f"Schema {schema_key} missing root additionalProperties: false"
            )
            props = schema_data.get("properties", {})
            if "status" in props:
                status_enum = props["status"].get("enum", [])
                assert "planned" in status_enum, f"Schema {schema_key} status enum missing 'planned': {status_enum}"

    # 2. Verify 'status: planned' is accepted on capability, component, design, exploration, and delta
    cap_file = tmp_path / "cap_planned.adoc"
    cap_file.write_text(
        "---\n"
        "type: product/capability\n"
        "schema: schemas/v1/product/capability.schema.yaml\n"
        "id: CAP-TEST-PLANNED\n"
        "title: Planned Capability\n"
        "domain: DOM-CORE\n"
        "status: planned\n"
        "business:\n"
        "  personas:\n"
        "    - PER-DEV\n"
        "  jtbd: When planning, I want status planned, so that roadmap items validate.\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-CORE\n"
        "  invariants:\n"
        "    - id: INV-SPEC-VALIDATION\n"
        "      rule: Must validate\n"
        "      test_binding: tests/test_validator.py::test_strict_schemas\n"
        "---\n"
        "= Planned Capability\n",
        encoding="utf-8",
    )
    from eidos.core.parser import parse_spec_file

    doc_valid = parse_spec_file(cap_file)
    assert validator.validate_document(doc_valid) == []

    # 3. Verify unknown property, invalid status, and invalid invariant ID pattern are rejected
    bad_file = tmp_path / "cap_bad.adoc"
    bad_file.write_text(
        "---\n"
        "type: product/capability\n"
        "schema: schemas/v1/product/capability.schema.yaml\n"
        "id: CAP-TEST-BAD\n"
        "title: Bad Capability\n"
        "domain: DOM-CORE\n"
        "status: bogus_status\n"
        "unrecognized_root_field: true\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Test\n"
        "  unrecognized_nested_field: 123\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-CORE\n"
        "  invariants:\n"
        "    - id: BAD_RULE_001\n"
        "      rule: Must fail pattern\n"
        "      test_binding: tests/test_validator.py::test_strict\n"
        "---\n"
        "= Bad Capability\n",
        encoding="utf-8",
    )
    doc_bad = parse_spec_file(bad_file)
    errs = validator.validate_document(doc_bad)
    err_text = "\n".join(errs)
    assert "unrecognized_root_field" in err_text
    assert "unrecognized_nested_field" in err_text
    assert "bogus_status" in err_text
    assert "BAD_RULE_001" in err_text


def test_schema_driven_x_eidos_ref_annotations_present(validator):
    """Verify A4: Reference fields across v1 schemas declare x-eidos-ref metadata."""

    def count_refs(node) -> int:
        if isinstance(node, dict):
            c = 1 if "x-eidos-ref" in node else 0
            return c + sum(count_refs(v) for v in node.values())
        if isinstance(node, list):
            return sum(count_refs(i) for i in node)
        return 0

    total_annotations = sum(
        count_refs(schema)
        for key, schema in validator._schema_cache.items()
        if key.endswith(".schema.yaml") and "/" not in key
    )
    assert total_annotations >= 35, f"Expected >= 35 x-eidos-ref annotations across v1 schemas, found {total_annotations}"


def test_validator_checks_package_path_specification_file_and_cli_contract_parity(tmp_path, validator):
    """Verify SpecValidator detects missing package_path, missing specification_file, and undocumented CLI commands."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    cnts_dir.mkdir(parents=True)
    ctrs_dir.mkdir(parents=True)

    src_cli = tmp_path / "src" / "cli"
    src_cli.mkdir(parents=True)
    (src_cli / "main.py").write_text(
        "import typer\napp = typer.Typer()\n\n"
        "@app.command()\ndef documented_cmd():\n    pass\n\n"
        "@app.command()\ndef undocumented_secret_cmd():\n    pass\n",
        encoding="utf-8",
    )

    (cnts_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CLI\n"
        "name: CLI Container\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "package_path:\n"
        "  - src/cli\n"
        "  - src/missing_pkg\n"
        "components: []\n"
        "---\n"
        "= CLI Container\n",
        encoding="utf-8",
    )

    (ctrs_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "protocol: cli_args\n"
        "owning_container: CNT-CLI\n"
        "specification_file: src/cli/main.py\n"
        "---\n"
        "= CLI Contract\n\n"
        "=== `documented-cmd`\nDocumented command.\n",
        encoding="utf-8",
    )

    (ctrs_dir / "missing.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-MISSING\n"
        "title: Missing Contract File\n"
        "protocol: rest_openapi\n"
        "owning_container: CNT-CLI\n"
        "specification_file: src/cli/nonexistent_openapi.yaml\n"
        "---\n"
        "= Missing Contract\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(report.broken_references)
    assert "src/missing_pkg" in broken_text
    assert "src/cli/nonexistent_openapi.yaml" in broken_text
    assert "undocumented-secret-cmd" in broken_text
    assert "documented-cmd" not in broken_text





