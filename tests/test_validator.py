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
        "component.schema.yaml",
        "contract.schema.yaml",
    ]
    for schema_name in expected_schemas:
        assert schema_name in validator._schema_cache, f"Missing schema: {schema_name}"


def test_validate_eidos_specifications(validator):
    """Verify that all living specifications under eidos/ pass schema validation."""
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
    assert "CMP-EIDOS-CLI" in report.entity_index
    assert "DOM-PRODUCT-CORE" in report.entity_index
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
    assert "CMP-GHOST" in broken_str
    assert "CTR-GHOST-IFACE" in broken_str
    assert "CTR-GHOST-PROV" in broken_str
    assert "CTR-GHOST-REQ" in broken_str
    assert "CMP-TEST-CORE" not in broken_str
    assert "packages/local-pkg" not in broken_str


def test_component_semantic_references_broken(tmp_path, validator):
    """Verify Initiative A: Component dependencies and contracts are checked."""
    eidos_dir = tmp_path / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    comps_dir.mkdir(parents=True)

    (comps_dir / "comp.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-TEST-CLI\n"
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

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "DOM-NONEXISTENT" in broken_str
    assert "CMP-MISSING-DEP" in broken_str
    assert "CTR-MISSING-PROV" in broken_str
    assert "CTR-MISSING-REQ" in broken_str


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



