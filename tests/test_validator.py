"""Tests verifying the Eidos specification validator and repo dogfooding."""

from pathlib import Path

import pytest

from eidos.core.spec.validator import SpecValidator


@pytest.fixture
def validator():
    return SpecValidator()


def test_bundled_schemas_loaded(validator):
    """Verify that all 16 core v1 schemas are successfully preloaded (including path-qualified keys)."""
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
        "component.schema.yaml",
        "contract.schema.yaml",
        "design/system.schema.yaml",
        "design/ui-component.schema.yaml",
        "design/screen.schema.yaml",
        "design/client.schema.yaml",
        "design/flow.schema.yaml",
    ]
    for schema_name in expected_schemas:
        assert schema_name in validator._schema_cache, f"Missing schema: {schema_name}"


def test_validate_eidos_specifications(validator):
    """Verify INV-SPEC-VALIDATION, INV-SPEC-GRAPH-INTEGRITY, INV-W3C-DTCG-COMPLIANCE, and INV-CLIENT-DESIGN-BINDING: all living specifications pass schema validation and entity ID graph integrity."""
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
    """Verify SpecDelta mutations[].target entity ID references are validated and report broken references."""
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

    # Delta with 1 valid existing component, 1 newly added component, and 3 broken references on modify
    (deltas_dir / "dlt-test.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-TEST\n"
        "title: Test Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Testing\n"
        "mutations:\n"
        "  - operation: add\n"
        "    target: CMP-NEW-ADDED\n"
        "  - operation: modify\n"
        "    target: CMP-TEST-CORE\n"
        "  - operation: modify\n"
        "    target: CMP-NONEXISTENT\n"
        "  - operation: modify\n"
        "    target: CTR-NONEXISTENT\n"
        "  - operation: modify\n"
        "    target: POL-NONEXISTENT\n"
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
    assert "CMP-NEW-ADDED" not in broken_str



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
    from eidos.core.spec.parser import parse_spec_file

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


def test_decisions_schema_and_cross_reference_validation(tmp_path, validator):
    """Verify INV-SPEC-VALIDATION and INV-SPEC-GRAPH-INTEGRITY: embedded decisions validate against schemas, enforce DEC-* uniqueness, and check graduated_from references."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    exps_dir = eidos_dir / "explorations"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)
    exps_dir.mkdir(parents=True)

    (exps_dir / "exp.adoc").write_text(
        "---\n"
        "type: product/exploration\n"
        "schema: schemas/v1/product/exploration.schema.yaml\n"
        "id: EXP-2026-001\n"
        "title: Valid Spike\n"
        "status: adopted\n"
        "hypothesis: Test\n"
        "exit_criteria:\n"
        "  max_duration: 1_day\n"
        "---\n"
        "= Spike\n",
        encoding="utf-8",
    )

    (eidos_dir / "system.adoc").write_text(
        "---\n"
        "type: product/system\n"
        "schema: schemas/v1/product/system.schema.yaml\n"
        "id: SYS-TEST-001\n"
        "title: Test System\n"
        "archetypes:\n"
        "  - cli_tool\n"
        "business_identity:\n"
        "  vision: Vision\n"
        "  target_users:\n"
        "    - Devs\n"
        "  value_proposition: Value\n"
        "technical_profile:\n"
        "  primary_languages:\n"
        "    - Python\n"
        "decisions:\n"
        "  - id: DEC-SHARED-ID\n"
        "    title: System Decision\n"
        "    decision: Use Python\n"
        "    rationale: Standard runtime\n"
        "    rejected_alternatives:\n"
        "      - option: Perl\n"
        "        reason_rejected: Legacy\n"
        "    graduated_from: EXP-2026-001\n"
        "---\n"
        "= System\n",
        encoding="utf-8",
    )

    (cnts_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-TEST-CORE\n"
        "name: Core\n"
        "container_type: library\n"
        "runtime: Python\n"
        "package_path: src/core\n"
        "components:\n"
        "  - id: CMP-INLINE-ONE\n"
        "    name: InlineOne\n"
        "    responsibility: Does inline work\n"
        "    source_path: src/core/one.py\n"
        "    decisions:\n"
        "      - id: DEC-SHARED-ID\n"
        "        title: Duplicate Decision\n"
        "        decision: Collides with system.adoc\n"
        "        rationale: Testing duplicate detection\n"
        "decisions:\n"
        "  - id: DEC-BROKEN-GRAD\n"
        "    title: Broken Graduated Ref\n"
        "    decision: Test broken ref\n"
        "    rationale: Testing x-eidos-ref\n"
        "    graduated_from: EXP-9999-999\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )

    (comps_dir / "dedicated.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-DEDICATED-ONE\n"
        "name: DedicatedOne\n"
        "container: CNT-TEST-CORE\n"
        "status: planned\n"
        "responsibility: Dedicated component\n"
        "decisions:\n"
        "  - id: DEC-DEDICATED-VALID\n"
        "    title: Dedicated Component Decision\n"
        "    decision: Keep stateless\n"
        "    rationale: Simplifies testing\n"
        "---\n"
        "= Dedicated\n",
        encoding="utf-8",
    )

    (eidos_dir / "architecture" / "system-architecture.adoc").write_text(
        "---\n"
        "type: tech/architecture\n"
        "schema: schemas/v1/tech/architecture.schema.yaml\n"
        "id: ARCH-TEST-001\n"
        "title: Test Architecture\n"
        "topology_style: modular_monolith\n"
        "c4_context:\n"
        "  actors: []\n"
        "c4_containers:\n"
        "  - CNT-TEST-CORE\n"
        "solution_strategy:\n"
        "  - Keep simple\n"
        "cross_cutting_concepts: {}\n"
        "deployment_view:\n"
        "  hosting_model: Local\n"
        "  ci_cd_engine: GitHub Actions\n"
        "decisions:\n"
        "  - id: DEC-ARCH-VALID\n"
        "    title: Architecture Decision\n"
        "    decision: Use modular monolith\n"
        "    rationale: Low operational overhead\n"
        "    graduated_from: EXP-2026-001\n"
        "---\n"
        "= Architecture\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    assert report.schema_errors == {}
    broken_text = "\n".join(report.broken_references)
    assert "Duplicate entity ID 'DEC-SHARED-ID'" in broken_text
    assert "EXP-9999-999" in broken_text
    assert "EXP-2026-001" not in broken_text


def test_spec_validation_cache_warm_hit_and_invalidation(tmp_path: Path, monkeypatch) -> None:
    """INV-SPEC-VALIDATION & INV-AGENT-GRAPH-INDEX-FRESHNESS: SpecValidator.validate_directory reuses
    spec_validation_cache on warm runs with zero Draft202012Validator calls, and invalidates on file edit,
    file deletion, or schema_hash mutation."""
    import os
    import sqlite3

    eidos_dir = tmp_path / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    comp_dir = eidos_dir / "architecture" / "components"
    cnt_dir.mkdir(parents=True)
    comp_dir.mkdir(parents=True)
    (tmp_path / "src" / "core").mkdir(parents=True)
    cnt_file = cnt_dir / "core.adoc"
    comp_file = comp_dir / "worker.adoc"

    cnt_file.write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CACHE-CORE\n"
        "name: Cache Core\n"
        "container_type: library\n"
        "runtime: Python\n"
        "package_path: src/core\n"
        "components:\n"
        "  - id: CMP-CACHE-WORKER\n"
        "    ref: eidos/architecture/components/worker.adoc\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    comp_file.write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-CACHE-WORKER\n"
        "name: CacheWorker\n"
        "container: CNT-CACHE-CORE\n"
        "status: planned\n"
        "responsibility: Executes cached validation work\n"
        "---\n"
        "= Worker\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    cold_report = validator.validate_directory(eidos_dir)
    assert cold_report.is_clean
    db_path = eidos_dir / ".cache" / "index.db"
    assert db_path.exists()

    # 1. Warm run: zero validate_document / Draft202012Validator executions
    validated_docs: list[str] = []
    orig_validate_doc = SpecValidator.validate_document

    def _spy_validate_doc(self, doc):
        validated_docs.append(doc.file_path.name)
        return orig_validate_doc(self, doc)

    monkeypatch.setattr(SpecValidator, "validate_document", _spy_validate_doc)
    warm_report = validator.validate_directory(eidos_dir)
    assert warm_report.is_clean
    assert len(warm_report.documents) == 2
    assert validated_docs == []

    # 2. Modify one .adoc file (introduce schema error): only the edited file is re-validated
    bad_comp_text = comp_file.read_text(encoding="utf-8").replace("status: planned\n", "status: invalid_status\n")
    comp_file.write_text(bad_comp_text, encoding="utf-8")
    st = comp_file.stat()
    os.utime(comp_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    edited_report = validator.validate_directory(eidos_dir)
    assert validated_docs == ["worker.adoc"]
    assert not edited_report.is_clean
    assert str(comp_file) in edited_report.schema_errors

    # Warm run on invalid document still preserves cached schema_errors without re-running validator
    validated_docs.clear()
    warm_invalid_report = validator.validate_directory(eidos_dir)
    assert validated_docs == []
    assert not warm_invalid_report.is_clean
    assert warm_invalid_report.schema_errors == edited_report.schema_errors

    # 3. Delete worker.adoc: cache row is pruned and broken component ref is detected in memory
    comp_file.unlink()
    after_delete_report = validator.validate_directory(eidos_dir)
    assert validated_docs == []
    assert any("worker.adoc" in br for br in after_delete_report.broken_references)
    import contextlib

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        cached_paths = {r[0] for r in conn.execute("SELECT file_path FROM spec_validation_cache;")}
    assert not any(p.endswith("worker.adoc") for p in cached_paths)

    # 4. Bundled schema hash change invalidates spec_validation_cache
    validator.schema_hash = "mutated_bundled_schema_hash"
    validator.validate_directory(eidos_dir)
    assert validated_docs == ["core.adoc"]


def test_strict_container_vs_component_target_separation(tmp_path: Path, validator: SpecValidator) -> None:
    """DLT-2026-042 (INV-SPEC-VALIDATION & INV-SPEC-GRAPH-INTEGRITY): Verify strict separation between
    C4 Level 2 Container (CNT-*, tech/container) and Level 3 Component (CMP-*, tech/component) targets
    across capability, delta, container, domain, and client schemas and runtime x-eidos-ref checks."""
    from eidos.core.spec.parser import parse_spec_file

    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    caps_dir = eidos_dir / "domains" / "core" / "capabilities"
    deltas_dir = eidos_dir / "deltas"
    clients_dir = eidos_dir / "design" / "clients" / "web"
    for d in (cnts_dir, comps_dir, caps_dir, deltas_dir, clients_dir):
        d.mkdir(parents=True)

    # Catalog entities: CNT-CORE (tech/container) and CMP-PARSER (tech/component)
    (cnts_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CORE\n"
        "name: Core\n"
        "container_type: library\n"
        "runtime: Python\n"
        "package_path: src/core\n"
        "dependencies:\n"
        "  internal_components:\n"
        "    - CNT-CORE\n"
        "  internal_containers:\n"
        "    - CMP-PARSER\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    (comps_dir / "parser.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-PARSER\n"
        "name: Parser\n"
        "container: CNT-CORE\n"
        "status: planned\n"
        "responsibility: Parse specs\n"
        "---\n"
        "= Parser\n",
        encoding="utf-8",
    )

    # 1. Capability placing existing CNT-CORE inside technical.components and CMP-PARSER inside technical.containers
    cap_file = caps_dir / "cap.adoc"
    cap_file.write_text(
        "---\n"
        "type: product/capability\n"
        "schema: schemas/v1/product/capability.schema.yaml\n"
        "id: CAP-CORE-001\n"
        "title: Core Capability\n"
        "domain: DOM-CORE\n"
        "status: planned\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Test\n"
        "technical:\n"
        "  containers:\n"
        "    - CMP-PARSER\n"
        "  components:\n"
        "    - CNT-CORE\n"
        "---\n"
        "= Cap\n",
        encoding="utf-8",
    )

    # 2. Delta misusing legacy action/target_component/target_container rejected by composable schema
    bad_delta = deltas_dir / "dlt-bad.adoc"
    bad_delta.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-BAD\n"
        "title: Bad Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test\n"
        "mutations:\n"
        "  - action: modify_component\n"
        "    target_component: CNT-CORE\n"
        "  - operation: modify\n"
        "    target: ARCH-CORE-001\n"
        "---\n"
        "= Bad Delta\n",
        encoding="utf-8",
    )
    delta_errs = "\n".join(validator.validate_document(parse_spec_file(bad_delta)))
    assert "target_component" in delta_errs or "action" in delta_errs
    assert "ARCH-CORE-001" not in delta_errs


    # 3. Client pointing container to CMP-PARSER
    (clients_dir / "client.adoc").write_text(
        "---\n"
        "type: design/client\n"
        "schema: schemas/v1/design/client.schema.yaml\n"
        "id: CLT-WEB\n"
        "title: Web\n"
        "status: planned\n"
        "platform: web\n"
        "design_system: DS-CORE\n"
        "container: CMP-PARSER\n"
        "---\n"
        "= Web\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_text = "\n".join(report.broken_references)
    # CNT-CORE exists in catalog_ids, yet placing it in technical.components must fail x-eidos-ref prefix/type check!
    assert "component 'CNT-CORE'" in broken_text and "technical.components" in broken_text
    assert "container 'CMP-PARSER'" in broken_text and "technical.containers" in broken_text
    assert "component 'CNT-CORE'" in broken_text and "dependencies.internal_components" in broken_text
    assert "container 'CMP-PARSER'" in broken_text and "dependencies.internal_containers" in broken_text


def test_delta_schema_enum_taxonomies(tmp_path, validator):
    """DLT-2026-043 / INV-SPEC-VALIDATION: Verify delta.schema.yaml enforces strict enums on intent.kind, bug_classification, severity, and mutations[].operation."""
    from eidos.core.spec.parser import parse_spec_file

    delta_file = tmp_path / "dlt-enum.adoc"

    # 1. Valid enum values pass
    delta_file.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-ENUM\n"
        "title: Valid Enum Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: bug\n"
        "  motivation: Fix edge case\n"
        "  bug_classification: spec_gap\n"
        "  severity: high\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-DELTA-ENGINE\n"
        "---\n"
        "= Enum Delta\n",
        encoding="utf-8",
    )
    assert validator.validate_document(parse_spec_file(delta_file)) == []

    # 2. Invalid enum values on all four categorical fields are rejected
    delta_file.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-ENUM\n"
        "title: Invalid Enum Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: feature_addition\n"
        "  motivation: Legacy kind\n"
        "  bug_classification: unknown_bug_type\n"
        "  severity: blocker\n"
        "mutations:\n"
        "  - operation: custom_unknown_operation\n"
        "    target: CMP-DELTA-ENGINE\n"
        "---\n"
        "= Invalid Enum Delta\n",
        encoding="utf-8",
    )
    errs = "\n".join(validator.validate_document(parse_spec_file(delta_file)))
    assert "feature_addition" in errs
    assert "unknown_bug_type" in errs
    assert "blocker" in errs
    assert "custom_unknown_operation" in errs


def test_typed_pydantic_frontmatter_models(tmp_path):
    """Verify typed Pydantic frontmatter domain models in CMP-DOMAIN-MODELS (DLT-2026-050)."""
    from eidos.core.spec.models import FrontmatterModel, SpecDocument

    raw_fm = {
        "id": "CNT-TEST",
        "type": "tech/container",
        "name": "Test Container",
        "status": "accepted",
        "package_path": "src/test",
        "decisions": [
            {
                "id": "DEC-TEST-001",
                "title": "Use SQLite",
                "decision": "Store in SQLite",
                "rationale": "Embedded and fast",
                "rejected_alternatives": [
                    {"option": "PostgreSQL", "reason_rejected": "Heavy external service"}
                ],
            }
        ],
        "custom_key": "custom_value",
    }
    doc = SpecDocument(
        file_path=tmp_path / "test.adoc",
        frontmatter=raw_fm,
        body="= Test",
        doc_type="tech/container",
        doc_id="CNT-TEST",
    )
    model = doc.as_model()
    assert isinstance(model, FrontmatterModel)
    assert model.id == "CNT-TEST"
    assert model.type == "tech/container"
    assert model.name == "Test Container"
    assert len(model.decisions) == 1
    assert model.decisions[0].id == "DEC-TEST-001"
    assert model.decisions[0].rejected_alternatives[0].option == "PostgreSQL"
    # Dict-like backwards compatibility
    assert model["custom_key"] == "custom_value"
    assert model.get("missing", "default") == "default"


def test_custom_spec_root_under_parent_src_and_subdirectory_validation(tmp_path: Path, validator: SpecValidator) -> None:
    """DLT-2026-049: Verify validate_directory resolves (spec_root, repo_root) for custom '--path specs/'
    and subdirectory 'specs/architecture' even when repo is under a parent 'src/' directory."""
    repo_root = tmp_path / "src" / "my_repo"
    specs_dir = repo_root / "specs"
    cnts_dir = specs_dir / "architecture" / "containers"
    comps_dir = specs_dir / "architecture" / "components"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)
    (repo_root / "src" / "app").mkdir(parents=True)
    (repo_root / "src" / "app" / "worker.py").write_text("class Worker:\n    pass\n", encoding="utf-8")

    (cnts_dir / "app.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CUSTOM-APP\n"
        "name: Custom App\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/app\n"
        "components:\n"
        "  - id: CMP-CUSTOM-WORKER\n"
        "    ref: specs/architecture/components/worker.adoc\n"
        "---\n"
        "= Custom App\n",
        encoding="utf-8",
    )
    (comps_dir / "worker.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-CUSTOM-WORKER\n"
        "name: Custom Worker\n"
        "container: CNT-CUSTOM-APP\n"
        "status: active\n"
        "source_path: src/app/worker.py\n"
        "responsibility: Executes background tasks.\n"
        "---\n"
        "= Custom Worker\n",
        encoding="utf-8",
    )

    # 1. Validate custom spec root 'specs/' directly
    report_root = validator.validate_directory(specs_dir)
    assert report_root.is_clean, (
        f"Custom specs/ root validation failed: {report_root.schema_errors}, {report_root.broken_references}"
    )
    assert "CNT-CUSTOM-APP" in report_root.entity_index
    assert "CMP-CUSTOM-WORKER" in report_root.entity_index

    # 2. Validate subdirectory 'specs/architecture' directly
    report_subdir = validator.validate_directory(specs_dir / "architecture")
    assert report_subdir.is_clean, (
        f"Subdirectory specs/architecture validation failed: {report_subdir.schema_errors}, {report_subdir.broken_references}"
    )


def test_dynamic_schema_entity_id_prefix_extraction(validator: SpecValidator) -> None:
    """DLT-2026-049: Verify SpecValidator and is_entity_id dynamically extract prefixes from v1 schemas and x-eidos-ref."""
    from eidos.core.spec.validator import (
        _DYNAMIC_ENTITY_ID_PREFIXES,
        _extract_prefixes_from_schema,
        is_entity_id,
    )

    for expected in ("CNT-", "CMP-", "CTR-", "CAP-", "DOM-", "POL-", "PER-", "DLT-", "DS-", "UIC-", "SCR-", "CLT-", "FLOW-", "EXP-", "SYS-", "ARCH-", "DEC-"):
        assert expected in validator.entity_id_prefixes
        assert is_entity_id(f"{expected}TEST-001")

    custom_schema = {
        "properties": {
            "id": {"type": "string", "pattern": "^MOD-[A-Z0-9_-]+$"},
            "ref_target": {
                "type": "string",
                "x-eidos-ref": {"prefixes": ["WIDGET-"]},
            },
        }
    }
    extracted = _extract_prefixes_from_schema(custom_schema)
    assert extracted == {"MOD-", "WIDGET-"}

    _DYNAMIC_ENTITY_ID_PREFIXES.update(extracted)
    try:
        assert is_entity_id("MOD-CORE-01")
        assert is_entity_id("WIDGET-BTN")
    finally:
        _DYNAMIC_ENTITY_ID_PREFIXES.difference_update(extracted)


def test_scoped_typer_cli_contract_parity_non_typer_and_positional_add_typer(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-049: Verify _verify_cli_contract_commands ignores non-Typer Click/argparse CLIs and supports positional add_typer."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    cnts_dir.mkdir(parents=True)
    ctrs_dir.mkdir(parents=True)

    src_cli = tmp_path / "src" / "cli"
    src_cli.mkdir(parents=True)

    # (a) Non-Typer Click CLI file (even with 'app' group and @app.command) -> must pass with zero false positives
    (src_cli / "click_cli.py").write_text(
        "import click\n\n"
        "@click.group()\ndef app():\n    pass\n\n"
        "@app.command()\ndef click_only_cmd():\n    pass\n",
        encoding="utf-8",
    )

    # (b) Typer CLI file using custom variable name 'cli = Typer()' and positional add_typer(sub, 'subgrp')
    (src_cli / "typer_cli.py").write_text(
        "from typer import Typer\n\n"
        "cli = Typer()\n"
        "sub = Typer()\n"
        "cli.add_typer(sub, 'subgrp')\n\n"
        "@cli.command()\ndef run_job():\n    pass\n\n"
        "@sub.command()\ndef nested_sub_leaf():\n    pass\n",
        encoding="utf-8",
    )

    (cnts_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CLI\n"
        "name: CLI\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "package_path: src/cli\n"
        "---\n"
        "= CLI\n",
        encoding="utf-8",
    )

    (ctrs_dir / "click_ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-CLICK-CLI\n"
        "title: Click CLI Contract\n"
        "protocol: cli_args\n"
        "owning_container: CNT-CLI\n"
        "specification_file: src/cli/click_cli.py\n"
        "---\n"
        "= Click CLI Contract\n",
        encoding="utf-8",
    )

    typer_ctr = ctrs_dir / "typer_ctr.adoc"
    typer_ctr.write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-TYPER-CLI\n"
        "title: Typer CLI Contract\n"
        "protocol: cli_args\n"
        "owning_container: CNT-CLI\n"
        "specification_file: src/cli/typer_cli.py\n"
        "---\n"
        "= Typer CLI Contract\n\n"
        "=== `run-job`\nRuns job.\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(report.broken_references)
    # Click CLI is ignored (no false positive on click-only-cmd)
    assert "click-only-cmd" not in broken_text
    # Positional add_typer 'subgrp' is detected as undocumented, while nested_sub_leaf is not treated as root command
    assert "subgrp" in broken_text
    assert "nested-sub-leaf" not in broken_text

    # Documenting 'subgrp' makes the report completely clean
    typer_ctr.write_text(
        typer_ctr.read_text(encoding="utf-8") + "\n=== `subgrp`\nSubcommand group.\n",
        encoding="utf-8",
    )
    clean_report = validator.validate_directory(eidos_dir)
    assert clean_report.is_clean, f"Expected clean report, got broken refs: {clean_report.broken_references}"


def test_v1_schema_parity_invariants_decisions_contracts_and_delta_refs(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-073 (# eidos:fixture): Verify container/policy invariants, domain/capability/policy/contract decisions,
    domain ubiquitous_language/owners, contract version/consumers, and delta affects/blast_radius x-eidos-ref."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    pols_dir = eidos_dir / "policies"
    dom_dir = eidos_dir / "domains" / "billing"
    caps_dir = dom_dir / "capabilities"
    deltas_dir = eidos_dir / "deltas"
    for d in (cnts_dir, ctrs_dir, pols_dir, caps_dir, deltas_dir):
        d.mkdir(parents=True)
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "billing" / "api.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")

    # 1. Container with top-level invariants + inline component invariants
    (cnts_dir / "billing.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-BILLING\n"
        "name: Billing Service\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/billing\n"
        "invariants:\n"
        "  - id: INV-CNT-BILLING-001\n"
        "    rule: Container must remain stateless.\n"
        "    test_binding: tests/test_billing.py::test_stateless\n"
        "components:\n"
        "  - id: CMP-BILLING-LEDGER\n"
        "    name: BillingLedger\n"
        "    responsibility: Double-entry ledger.\n"
        "    source_path: src/billing\n"
        "    invariants:\n"
        "      - id: INV-CMP-LEDGER-001\n"
        "        rule: Debits must equal credits.\n"
        "        test_binding: tests/test_billing.py::test_ledger\n"
        "---\n"
        "= Billing\n",
        encoding="utf-8",
    )

    # 2. Policy with invariants + decisions
    (pols_dir / "pol.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "schema: schemas/v1/product/policy.schema.yaml\n"
        "id: POL-001-AUDIT\n"
        "title: Audit Policy\n"
        "category: compliance\n"
        "enforcement:\n"
        "  mechanism: ci_cd_gate\n"
        "  severity: hard_block\n"
        "status: active\n"
        "rule: Every ledger mutation must emit an audit event.\n"
        "invariants:\n"
        "  - id: INV-POL-AUDIT-001\n"
        "    rule: Audit events are append-only.\n"
        "    test_binding: tests/test_billing.py::test_audit\n"
        "decisions:\n"
        "  - id: DEC-POL-001\n"
        "    title: Immutable Audit Log\n"
        "    decision: Store audit log in append-only table.\n"
        "    rationale: Regulatory compliance.\n"
        "---\n"
        "= Policy\n",
        encoding="utf-8",
    )

    # 3. Domain with ubiquitous_language, owners, and decisions
    (dom_dir / "domain.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "schema: schemas/v1/product/domain.schema.yaml\n"
        "id: DOM-BILLING\n"
        "name: Billing Domain\n"
        "classification: core\n"
        "boundaries:\n"
        "  responsibilities:\n"
        "    - Billing bounded context.\n"
        "owners:\n"
        "  - FinOps Team\n"
        "ubiquitous_language:\n"
        "  - term: Invoice\n"
        "    definition: Immutable billing statement.\n"
        "decisions:\n"
        "  - id: DEC-DOM-001\n"
        "    title: Exact Decimal Arithmetic\n"
        "    decision: Use fixed-point decimal representation.\n"
        "    rationale: Prevent floating-point rounding drift.\n"
        "---\n"
        "= Domain\n",
        encoding="utf-8",
    )

    # 4. Capability with decisions
    (caps_dir / "invoicing.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "schema: schemas/v1/product/capability.schema.yaml\n"
        "id: CAP-BILLING-001\n"
        "title: Invoicing\n"
        "domain: DOM-BILLING\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Issue invoices.\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-BILLING\n"
        "  components:\n"
        "    - CMP-BILLING-LEDGER\n"
        "decisions:\n"
        "  - id: DEC-CAP-001\n"
        "    title: Async Invoice Generation\n"
        "    decision: Generate PDFs asynchronously.\n"
        "    rationale: Keep API latency low.\n"
        "---\n"
        "= Capability\n",
        encoding="utf-8",
    )

    # 5. Contract with version, consumers (x-eidos-ref), and decisions
    (ctrs_dir / "api.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-BILLING-API\n"
        "title: Billing REST API\n"
        "version: 1.2.0\n"
        "protocol: rest_openapi\n"
        "owning_container: CNT-BILLING\n"
        "consumers:\n"
        "  - CMP-BILLING-LEDGER\n"
        "specification_file: src/billing/api.yaml\n"
        "decisions:\n"
        "  - id: DEC-CTR-001\n"
        "    title: Cursor Pagination\n"
        "    decision: Use opaque cursor pagination.\n"
        "    rationale: Stable pagination under concurrent inserts.\n"
        "---\n"
        "= Contract\n",
        encoding="utf-8",
    )

    # 6. Proposed SpecDelta with vcs.base_commit and a missing target reference on modify
    (deltas_dir / "dlt-test.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-001\n"
        "title: Billing Update\n"
        "status: proposed\n"
        "vcs:\n"
        "  branch: feat/billing\n"
        "  base_commit: abc1234\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Extend billing\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-BILLING-LEDGER\n"
        "  - operation: modify\n"
        "    target: CMP-MISSING-GHOST\n"
        "---\n"
        "= Delta\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    assert report.schema_errors == {}, f"Unexpected schema errors: {report.schema_errors}"
    broken_text = "\n".join(report.broken_references)
    assert "CMP-MISSING-GHOST" in broken_text



def test_unified_block_scalar_safe_frontmatter_parser_and_extended_models(tmp_path: Path) -> None:
    """DLT-2026-060 (# eidos:fixture): Verify split_frontmatter_and_body ignores indented '---' inside YAML block scalars
    across parser, catalog, and structural helpers, and verify extended FrontmatterModel accessors."""
    from eidos.core.lifecycle.catalog import _set_frontmatter_scalar, _split_raw_frontmatter
    from eidos.core.spec.parser import dump_spec_document, extract_frontmatter, split_frontmatter_and_body
    from eidos.vcs.structural import _spec_frontmatter, _split_spec

    spec_text = (
        "---\n"
        "type: product/policy\n"
        "id: POL-001\n"
        "status: draft\n"
        "rule: |\n"
        "  First paragraph.\n"
        "  ---\n"
        "  Second paragraph after horizontal rule.\n"
        "---\n"
        "= Policy Title\n\n"
        "Body paragraph.\n"
    )
    raw_yaml, body = split_frontmatter_and_body(spec_text)
    assert raw_yaml is not None
    assert "Second paragraph after horizontal rule." in raw_yaml
    assert body.startswith("= Policy Title")

    fm, parsed_body, err = extract_frontmatter(spec_text)
    assert err is None
    assert fm is not None
    assert "---\nSecond paragraph" in fm["rule"]
    assert parsed_body.startswith("= Policy Title")

    # catalog.py and structural.py share the exact same splitter
    assert _split_raw_frontmatter(spec_text) == (raw_yaml, body)
    assert _split_spec(spec_text) == (raw_yaml, body)
    assert _spec_frontmatter(spec_text)["id"] == "POL-001"

    updated = _set_frontmatter_scalar(spec_text, "status", "active")
    assert _spec_frontmatter(updated)["status"] == "active"
    assert "Second paragraph after horizontal rule." in _spec_frontmatter(updated)["rule"]

    # File without opening delimiter at line 0 must NOT match '---' in the body
    body_only = "\n= Document Without Frontmatter\n---\nSection\n---\n"
    assert split_frontmatter_and_body(body_only) == (None, body_only)
    fm_none, _, err_none = extract_frontmatter(body_only)
    assert fm_none is None
    assert err_none is not None

    # Extended FrontmatterModel accessors
    dumped = dump_spec_document(
        {
            "id": "CNT-TEST",
            "type": "tech/container",
            "package_path": ["src/a", "src/b"],
            "invariants": [{"id": "INV-CNT-1", "rule": "Top rule", "test_binding": "tests/t.py::test_1"}],
            "decisions": [{"id": "DEC-CNT-1", "title": "T1", "decision": "D1", "rationale": "R1"}],
            "components": [
                {
                    "id": "CMP-INLINE-1",
                    "name": "Inline1",
                    "source_path": "src/a/mod.py",
                    "invariants": [{"id": "INV-CMP-1", "rule": "Comp rule"}],
                    "decisions": [{"id": "DEC-CMP-1", "title": "T2", "decision": "D2", "rationale": "R2"}],
                }
            ],
        },
        "= Container\n",
    )
    spec_file = tmp_path / "cnt.adoc"
    spec_file.write_text(dumped, encoding="utf-8")
    from eidos.core.spec.parser import parse_spec_file

    doc = parse_spec_file(spec_file)
    model = doc.as_model()
    assert model.package_paths() == ["src/a", "src/b"]
    assert model.components[0].id == "CMP-INLINE-1"
    assert model.components[0]["id"] == "CMP-INLINE-1"
    assert model.components[0].source_paths() == ["src/a/mod.py"]
    assert [i.id for i in model.all_invariants()] == ["INV-CNT-1", "INV-CMP-1"]
    assert [d.id for d in model.all_decisions()] == ["DEC-CNT-1", "DEC-CMP-1"]


def test_scoped_empty_system_adoc_exemption_and_non_system_empty_failure(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-066: Verify 0-byte root system.adoc is permitted as SYS-UNINITIALIZED while any other
    0-byte or whitespace-only .adoc file fails parse_spec_file and validate_directory."""
    from eidos.core.spec.parser import parse_spec_file

    eidos_dir = tmp_path / "eidos"
    caps_dir = eidos_dir / "domains" / "core" / "capabilities"
    caps_dir.mkdir(parents=True)

    sys_file = eidos_dir / "system.adoc"
    sys_file.write_text("", encoding="utf-8")
    sys_doc = parse_spec_file(sys_file)
    assert sys_doc.is_valid
    assert sys_doc.doc_id == "SYS-UNINITIALIZED"
    assert validator.validate_directory(eidos_dir).is_clean

    # Empty non-system .adoc file must fail both parse_spec_file and validate_directory
    empty_cap = caps_dir / "empty_cap.adoc"
    empty_cap.write_text("   \n", encoding="utf-8")
    empty_doc = parse_spec_file(empty_cap)
    assert not empty_doc.is_valid
    assert any("Missing YAML frontmatter" in e for e in empty_doc.errors)

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    assert str(empty_cap) in report.schema_errors


def test_schema_cache_collision_free_and_functional_cli_parity(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-072: Verify product/system.schema.yaml and design/system.schema.yaml do not collide,
    eidos info lists all 16 bundled schemas, and _verify_cli_contract_commands discovers functional
    app.command(...)(fn) and *_commands.register(app) root commands."""
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.spec.models import SpecDocument

    # 1. Both product/system and design/system schemas resolve distinctly even with bare schema_ref
    assert validator._schema_cache["product/system.schema.yaml"]["title"] == "SystemSchema"
    assert validator._schema_cache["design/system.schema.yaml"]["title"] == "DesignSystemSchema"
    prod_doc = SpecDocument(
        file_path=tmp_path / "sys.adoc",
        frontmatter={},
        body="",
        doc_type="product/system",
        doc_id="SYS-1",
        schema_ref="system.schema.yaml",
    )
    ds_doc = SpecDocument(
        file_path=tmp_path / "ds.adoc",
        frontmatter={},
        body="",
        doc_type="design/system",
        doc_id="DS-1",
        schema_ref="system.schema.yaml",
    )
    assert validator.resolve_schema(prod_doc)["title"] == "SystemSchema"
    assert validator.resolve_schema(ds_doc)["title"] == "DesignSystemSchema"

    # 2. eidos info reports 16 loaded schemas
    res = CliRunner().invoke(app, ["info"])
    assert res.exit_code == 0
    assert "16 loaded" in res.stdout
    assert "product/system.schema.yaml" in res.stdout
    assert "design/system.schema.yaml" in res.stdout

    # 3. Functional app.command(...)(fn) and sibling register(app) commands are discovered
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    src_cli = tmp_path / "src" / "cli"
    for d in (cnts_dir, ctrs_dir, src_cli):
        d.mkdir(parents=True)

    (src_cli / "extra_command.py").write_text(
        "import typer\n"
        "def extra_cmd():\n    pass\n"
        "def register(app: typer.Typer) -> None:\n"
        "    app.command('delegated-cmd')(extra_cmd)\n",
        encoding="utf-8",
    )
    (src_cli / "main.py").write_text(
        "import typer\n"
        "from . import extra_command\n"
        "app = typer.Typer()\n"
        "def info():\n    pass\n"
        "app.command()(info)\n"
        "extra_command.register(app)\n",
        encoding="utf-8",
    )

    (cnts_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CLI\n"
        "name: CLI\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "package_path: src/cli\n"
        "---\n"
        "= CLI\n",
        encoding="utf-8",
    )
    ctr_file = ctrs_dir / "cli.adoc"
    ctr_file.write_text(
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
        "=== `info`\n",
        encoding="utf-8",
    )

    rep = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(rep.broken_references)
    assert "info" not in broken_text
    assert "delegated-cmd" in broken_text

    ctr_file.write_text(ctr_file.read_text(encoding="utf-8") + "\n=== `delegated-cmd`\n", encoding="utf-8")
    assert validator.validate_directory(eidos_dir).is_clean


def test_component_source_path_ui_test_bindings_and_invariant_test_binding_validation(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-059 (# eidos:fixture): Verify SpecValidator checks active CMP-* source_path, UIC-* test_bindings,
    and INV-* test_binding (file::func) file and symbol existence on disk."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    uic_dir = eidos_dir / "design" / "systems" / "core" / "components"
    pols_dir = eidos_dir / "policies"
    src_dir = tmp_path / "src" / "app"
    tests_dir = tmp_path / "tests"
    for d in (cnts_dir, uic_dir, pols_dir, src_dir, tests_dir):
        d.mkdir(parents=True)

    (src_dir / "real.py").write_text("def run():\n    return True\n", encoding="utf-8")
    (tests_dir / "test_app.py").write_text("def test_existing_rule():\n    assert True\n", encoding="utf-8")

    # 1. Container with one valid inline component source_path and one missing inline component source_path
    cnt_file = cnts_dir / "app.adoc"
    cnt_file.write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-APP\n"
        "name: App\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/app\n"
        "components:\n"
        "  - id: CMP-VALID\n"
        "    name: ValidComp\n"
        "    responsibility: Valid\n"
        "    source_path: src/app/real.py\n"
        "  - id: CMP-BROKEN-SRC\n"
        "    name: BrokenComp\n"
        "    responsibility: Broken\n"
        "    source_path: src/app/missing.py\n"
        "---\n"
        "= App\n",
        encoding="utf-8",
    )

    # 2. UI Component with broken test_bindings file
    uic_file = uic_dir / "btn.adoc"
    uic_file.write_text(
        "---\n"
        "type: design/ui-component\n"
        "schema: schemas/v1/design/ui-component.schema.yaml\n"
        "id: UIC-BTN\n"
        "title: Button\n"
        "design_system: DS-CORE\n"
        "status: active\n"
        "category: actions\n"
        "variants:\n"
        "  - name: primary\n"
        "    description: Primary button\n"
        "states:\n"
        "  - default\n"
        "accessibility:\n"
        "  wcag_level: AA\n"
        "  aria_role: button\n"
        "  keyboard_nav: Enter activates\n"
        "test_bindings:\n"
        "  unit_test: tests/test_missing_ui.py::test_btn\n"
        "---\n"
        "= Button\n",
        encoding="utf-8",
    )

    # 3. Policy with one valid INV-* test_binding, one missing file, and one missing symbol
    pol_file = pols_dir / "pol.adoc"
    pol_file.write_text(
        "---\n"
        "type: product/policy\n"
        "schema: schemas/v1/product/policy.schema.yaml\n"
        "id: POL-001-TEST\n"
        "title: Test Policy\n"
        "category: architecture\n"
        "enforcement:\n"
        "  mechanism: ci_cd_gate\n"
        "  severity: hard_block\n"
        "status: active\n"
        "rule: Test rule.\n"
        "invariants:\n"
        "  - id: INV-OK\n"
        "    rule: Valid binding\n"
        "    test_binding: tests/test_app.py::test_existing_rule\n"
        "  - id: INV-MISSING-FILE\n"
        "    rule: Missing file\n"
        "    test_binding: tests/no_such_file.py::test_x\n"
        "  - id: INV-MISSING-SYM\n"
        "    rule: Missing symbol\n"
        "    test_binding: tests/test_app.py::test_nonexistent_symbol\n"
        "---\n"
        "= Policy\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(report.broken_references)
    assert "CMP-VALID" not in broken_text
    assert "CMP-BROKEN-SRC" in broken_text and "src/app/missing.py" in broken_text
    assert "UIC-BTN" in broken_text and "tests/test_missing_ui.py" in broken_text
    assert "INV-OK" not in broken_text
    assert "INV-MISSING-FILE" in broken_text and "tests/no_such_file.py" in broken_text
    assert "INV-MISSING-SYM" in broken_text and "test_nonexistent_symbol" in broken_text


def test_design_system_w3c_dtcg_manifest_and_acyclic_inheritance(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-064 (INV-W3C-DTCG-COMPLIANCE): Verify SpecValidator validates W3C DTCG tokens.manifest
    existence and $value/$type structure and detects circular inheritance.extends chains."""
    eidos_dir = tmp_path / "eidos"
    ds_a_dir = eidos_dir / "design" / "systems" / "brand_a"
    ds_b_dir = eidos_dir / "design" / "systems" / "brand_b"
    ds_a_dir.mkdir(parents=True)
    ds_b_dir.mkdir(parents=True)

    # DS-A has malformed token missing $value and extends DS-B
    (ds_a_dir / "tokens.json").write_text(
        '{"color": {"primary": {"hex": "#ffffff"}}}',
        encoding="utf-8",
    )
    (ds_a_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "schema: schemas/v1/design/system.schema.yaml\n"
        "id: DS-BRAND-A\n"
        "title: Brand A\n"
        "status: active\n"
        "inheritance:\n"
        "  extends: DS-BRAND-B\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: tokens.json\n"
        "---\n"
        "= Brand A\n",
        encoding="utf-8",
    )

    # DS-B has missing tokens.json and extends DS-A (cycle!)
    (ds_b_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "schema: schemas/v1/design/system.schema.yaml\n"
        "id: DS-BRAND-B\n"
        "title: Brand B\n"
        "status: active\n"
        "inheritance:\n"
        "  extends: DS-BRAND-A\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: missing_tokens.json\n"
        "---\n"
        "= Brand B\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(report.broken_references)
    assert "missing required '$value'" in broken_text
    assert "missing_tokens.json" in broken_text
    assert "Circular design system inheritance" in broken_text
    assert "DS-BRAND-A -> DS-BRAND-B -> DS-BRAND-A" in broken_text or "DS-BRAND-B -> DS-BRAND-A -> DS-BRAND-B" in broken_text


def test_pluggable_contract_protocol_verifier_registry(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-056: Verify _CONTRACT_PROTOCOL_VERIFIERS checks python_protocol, rest_openapi,
    json_schema, grpc_proto, and graphql contracts against their specification_file."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    src_dir = tmp_path / "src" / "api"
    for d in (cnts_dir, ctrs_dir, src_dir):
        d.mkdir(parents=True)

    (cnts_dir / "api.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-API\n"
        "name: API\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/api\n"
        "---\n"
        "= API\n",
        encoding="utf-8",
    )

    # 1. python_protocol
    (src_dir / "ports.py").write_text(
        "from typing import Protocol\n"
        "class PaymentGateway(Protocol):\n"
        "    def charge_card(self, amount: int) -> bool: ...\n",
        encoding="utf-8",
    )
    (ctrs_dir / "py_proto.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-PY-PROTO\n"
        "title: Payment Gateway Protocol\n"
        "protocol: python_protocol\n"
        "owning_container: CNT-API\n"
        "specification_file: src/api/ports.py\n"
        "---\n"
        "= PaymentGateway\n",
        encoding="utf-8",
    )

    # 2. rest_openapi
    (src_dir / "openapi.yaml").write_text(
        "openapi: 3.0.0\n"
        "paths:\n"
        "  /v1/charges:\n"
        "    post: {}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "openapi.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-OPENAPI\n"
        "title: Charges REST API\n"
        "protocol: rest_openapi\n"
        "owning_container: CNT-API\n"
        "specification_file: src/api/openapi.yaml\n"
        "---\n"
        "= Charges REST API\n",
        encoding="utf-8",
    )

    # 3. json_schema
    (src_dir / "event.schema.json").write_text(
        '{"type": "object", "required": ["invoice_id"], "properties": {"invoice_id": {"type": "string"}}}',
        encoding="utf-8",
    )
    (ctrs_dir / "json_schema.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-JSON-SCHEMA\n"
        "title: Invoice Event Schema\n"
        "protocol: json_schema\n"
        "owning_container: CNT-API\n"
        "specification_file: src/api/event.schema.json\n"
        "---\n"
        "= Invoice Event Schema\n",
        encoding="utf-8",
    )

    # 4. grpc_proto
    (src_dir / "ledger.proto").write_text(
        "syntax = \"proto3\";\n"
        "service LedgerService {\n"
        "  rpc RecordEntry (EntryReq) returns (EntryResp);\n"
        "}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "grpc.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-GRPC\n"
        "title: Ledger gRPC\n"
        "protocol: grpc_proto\n"
        "owning_container: CNT-API\n"
        "specification_file: src/api/ledger.proto\n"
        "---\n"
        "= LedgerService\n",
        encoding="utf-8",
    )

    # 5. graphql
    (src_dir / "schema.graphql").write_text(
        "type Query {\n"
        "  fetchInvoice(id: ID!): Invoice\n"
        "}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "graphql.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-GRAPHQL\n"
        "title: GraphQL Schema\n"
        "protocol: graphql\n"
        "owning_container: CNT-API\n"
        "specification_file: src/api/schema.graphql\n"
        "---\n"
        "= GraphQL Schema\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(report.broken_references)
    assert "charge_card" in broken_text
    assert "/v1/charges" in broken_text
    assert "invoice_id" in broken_text
    assert "RecordEntry" in broken_text
    assert "fetchInvoice" in broken_text


def test_deep_review_subsystem_1_models_workspace_and_validator_edge_cases(tmp_path):
    """Verify deep-review fixes across models, parser, workspace path matching, and validator."""
    import pytest

    from eidos.core.spec.parser import _parse_spec_content
    from eidos.core.workspace.project import _normalize_rel_path, match_path_pattern

    # 1. Non-string frontmatter scalars normalize cleanly and _DictCompatibleModel resolves field aliases
    doc = _parse_spec_content(
        tmp_path / "num.adoc",
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: 42\n"
        "name: Numeric Container\n"
        "package_path: src/num\n"
        "components:\n"
        "  - id: CMP-NUM\n"
        "    name: NumComp\n"
        "    responsibility: Handles numbers.\n"
        "    status: accepted\n"
        "    domain_binding: DOM-CORE\n"
        "---\n"
        "= Numeric Container\n",
    )
    assert doc.doc_id == "42"
    assert doc.model["schema"] == "schemas/v1/tech/container.schema.yaml"
    assert "schema" in doc.model
    with pytest.raises(KeyError):
        _ = doc.model["model_dump"]
    assert doc.model.components[0].status == "accepted"
    assert doc.model.components[0].domain_binding == "DOM-CORE"

    # 2. Path normalization preserves dotfiles and specificity favors deeper subpackage over parent dir/**
    assert _normalize_rel_path(".github/workflows/ci.yml") == ".github/workflows/ci.yml"
    assert _normalize_rel_path("./src/app.py") == "src/app.py"
    spec_sub = match_path_pattern("src/pkg/sub/file.py", "src/pkg/sub")
    spec_glob = match_path_pattern("src/pkg/sub/file.py", "src/pkg/**")
    spec_dir = match_path_pattern("src/pkg/sub/file.py", "src/pkg")
    assert spec_sub > spec_glob == spec_dir > 0

    # 3. Subdirectory validate_directory seeds cross-references from spec_root
    eidos_dir = tmp_path / "eidos"
    dom_dir = eidos_dir / "domains"
    cnt_dir = eidos_dir / "architecture" / "containers"
    dom_dir.mkdir(parents=True)
    cnt_dir.mkdir(parents=True)
    (cnt_dir / "svc.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-BILLING\n"
        "name: Billing Container\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/billing\n"
        "components:\n"
        "  - id: CMP-BILLING-CORE\n"
        "    name: BillingCore\n"
        "    responsibility: Core billing.\n"
        "    source_path: src/billing/core.py\n"
        "    status: accepted\n"
        "    domain_binding: DOM-BILLING\n"
        "---\n"
        "= Billing Container\n",
        encoding="utf-8",
    )
    (dom_dir / "billing.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "schema: schemas/v1/product/domain.schema.yaml\n"
        "id: DOM-BILLING\n"
        "name: Billing Domain\n"
        "classification: core\n"
        "boundaries:\n"
        "  responsibilities:\n"
        "    - Invoicing\n"
        "technical_mapping:\n"
        "  containers:\n"
        "    - CNT-BILLING\n"
        "---\n"
        "= Billing Domain\n",
        encoding="utf-8",
    )
    validator = SpecValidator()
    sub_report = validator.validate_directory(dom_dir)
    assert sub_report.is_clean, f"Unexpected errors on subdirectory validation: {sub_report.broken_references}"
    full_report = validator.validate_directory(eidos_dir)
    assert full_report.is_clean, f"Unexpected schema/ref errors: {full_report.schema_errors} {full_report.broken_references}"


def test_validator_cache_second_tier_hit_and_parser_dtcg_protocol_edge_cases(tmp_path):
    """Verify SQLite spec_validation_cache second-tier SHA-256 hits, DTCG token edge cases, clean contract protocols, parser BOM/rules, and workspace helpers."""  # eidos:fixture
    import os
    import sqlite3

    from eidos.core.spec.parser import extract_frontmatter
    from eidos.core.workspace.assets import (
        get_agent_plugins_schemas_dir,
        get_assets_path,
        get_plugin_reference_dir,
        get_schemas_dir,
        get_v1_schemas_dir,
    )
    from eidos.core.workspace.project import find_repo_root, get_tracked_files, is_generated_or_vendored

    # 1. Parser edge cases: UTF-8 BOM, multiple --- horizontal rules in body, unclosed ---, non-dict YAML
    fm_bom, body_bom, err_bom = extract_frontmatter(
        "\ufeff---\ntype: product/policy\nid: POL-BOM\n---\n= Title\n\n---\nHorizontal rule\n"
    )
    assert err_bom is None and fm_bom["id"] == "POL-BOM"
    assert "---\nHorizontal rule" in body_bom

    _, _, err_unclosed = extract_frontmatter("---\ntype: product/policy\nid: POL-UNCLOSED\n")
    assert err_unclosed is not None and "No closing frontmatter delimiter" in err_unclosed

    _, _, err_list = extract_frontmatter("---\n- item1\n- item2\n---\n= Body\n")
    assert err_list is not None and "dictionary" in err_list

    # 2. Workspace helpers: find_repo_root from domains/ or policies/, get_tracked_files, is_generated_or_vendored, assets
    stand_root = tmp_path / "standalone_ws"
    (stand_root / "eidos" / "policies").mkdir(parents=True)
    (stand_root / "src").mkdir(parents=True)
    (stand_root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (stand_root / "node_modules").mkdir(parents=True)
    (stand_root / "node_modules" / "ignored.js").write_text("x = 1\n", encoding="utf-8")
    assert find_repo_root(stand_root / "eidos" / "policies") == stand_root
    assert get_tracked_files(stand_root) == ["src/main.py"]
    assert is_generated_or_vendored(Path.cwd(), ["src/eidos/core/spec/validator.py"]) == set()
    assert get_schemas_dir().is_dir()
    assert get_v1_schemas_dir().is_dir()
    assert get_agent_plugins_schemas_dir().is_dir()
    assert get_plugin_reference_dir().is_dir()
    with pytest.raises(FileNotFoundError):
        get_assets_path("nonexistent_asset_subpath_999")

    # 3. SQLite spec_validation_cache tier-1 (mtime_ns+size) and tier-2 (mtime bumped, sha256 identical) hits
    eidos_dir = tmp_path / "eidos"
    pol_dir = eidos_dir / "policies"
    ds_dir = eidos_dir / "design" / "systems" / "clean"
    cnt_dir = eidos_dir / "architecture" / "containers"
    ctr_dir = eidos_dir / "architecture" / "contracts"
    src_dir = tmp_path / "src" / "clean"
    for d in (pol_dir, ds_dir, cnt_dir, ctr_dir, src_dir):
        d.mkdir(parents=True, exist_ok=True)

    pol_file = pol_dir / "pol.adoc"
    pol_file.write_text(
        "---\n"
        "type: product/policy\n"
        "schema: schemas/v1/product/policy.schema.yaml\n"
        "id: POL-CACHE-001\n"
        "title: Cache Policy\n"
        "category: quality\n"
        "rule: Validate caching\n"
        "enforcement:\n"
        "  severity: blocking\n"
        "  mechanism: automated_test\n"
        "---\n"
        "= Cache Policy\n",
        encoding="utf-8",
    )
    validator = SpecValidator()
    r1 = validator.validate_directory(eidos_dir)
    assert r1.is_clean
    cache_db = eidos_dir / ".cache" / "index.db"
    assert cache_db.exists()

    # Bump mtime_ns without changing content -> tier-2 sha256 hit updates mtime_ns in cache
    st = pol_file.stat()
    os.utime(pol_file, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    r2 = validator.validate_directory(eidos_dir)
    assert r2.is_clean
    import contextlib

    with contextlib.closing(sqlite3.connect(cache_db)) as conn:
        row = conn.execute("SELECT mtime_ns FROM spec_validation_cache WHERE file_path LIKE '%pol.adoc'").fetchone()
        assert row is not None and row[0] == pol_file.stat().st_mtime_ns

    # 4. Positive & negative W3C DTCG token validation (valid manifest, unsupported $type, empty object)
    (ds_dir / "tokens.json").write_text(
        '{"$schema": "https://tr.designtokens.org/format/", "color": {"$type": "color", "primary": {"$value": "#0055ff"}}}',
        encoding="utf-8",
    )
    (ds_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "schema: schemas/v1/design/system.schema.yaml\n"
        "id: DS-CLEAN\n"
        "title: Clean DS\n"
        "status: active\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: tokens.json\n"
        "---\n"
        "= Clean DS\n",
        encoding="utf-8",
    )
    assert validator.validate_directory(eidos_dir).is_clean

    (ds_dir / "tokens.json").write_text(
        '{"color": {"bad": {"$value": "#fff", "$type": "not_a_dtcg_type"}, "empty_group": {}}}',
        encoding="utf-8",
    )
    bad_dtcg = "\n".join(validator.validate_directory(eidos_dir).broken_references)
    assert "unsupported $type 'not_a_dtcg_type'" in bad_dtcg
    assert "missing required '$value'" in bad_dtcg

    # Restore valid tokens.json and test clean contract protocol verifiers (ABC, Protocol[T], async def)
    (ds_dir / "tokens.json").write_text(
        '{"color": {"$type": "color", "primary": {"$value": "#0055ff"}}}',
        encoding="utf-8",
    )
    (cnt_dir / "clean.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CLEAN\n"
        "name: Clean Service\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/clean\n"
        "---\n"
        "= Clean Service\n",
        encoding="utf-8",
    )
    (src_dir / "ports.py").write_text(
        "from abc import ABC\n"
        "from typing import Generic, Protocol, TypeVar\n"
        "T = TypeVar('T')\n"
        "class AsyncRepo(Protocol[T]):\n"
        "    async def fetch_item(self, item_id: str) -> T: ...\n"
        "class BaseService(ABC):\n"
        "    def health_check(self) -> bool: return True\n",
        encoding="utf-8",
    )
    (ctr_dir / "clean_proto.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-CLEAN-PROTO\n"
        "title: Clean Protocol\n"
        "protocol: python_protocol\n"
        "owning_container: CNT-CLEAN\n"
        "specification_file: src/clean/ports.py\n"
        "---\n"
        "= Clean Protocol\n\n"
        "Defines `AsyncRepo` (`fetch_item`) and `BaseService` (`health_check`).\n",
        encoding="utf-8",
    )
    clean_rep = validator.validate_directory(eidos_dir)
    assert clean_rep.is_clean, f"Expected clean protocol report, got: {clean_rep.broken_references}"

    # 5. Architectural boundary invariants: contract owned by CMP-*, component declaring contracts
    (ctr_dir / "bad_owner.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-BAD-OWNER\n"
        "title: Bad Owner Contract\n"
        "protocol: rest_openapi\n"
        "owning_component: CMP-ILLEGAL\n"
        "specification_file: N/A\n"
        "---\n"
        "= Bad Owner\n",
        encoding="utf-8",
    )
    cmp_dir = eidos_dir / "architecture" / "components"
    cmp_dir.mkdir(parents=True, exist_ok=True)
    (cmp_dir / "cmp_with_ctr.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
        "id: CMP-WITH-CTR\n"
        "name: Component With Contracts\n"
        "owning_container: CNT-CLEAN\n"
        "responsibility: Invalid boundary contracts.\n"
        "source_path: src/clean/ports.py\n"
        "contracts:\n"
        "  provides:\n"
        "    - CTR-CLEAN-PROTO\n"
        "---\n"
        "= Component With Contracts\n",
        encoding="utf-8",
    )
    inv_rep = validator.validate_directory(eidos_dir)
    all_schema_errs = "\n".join(e for errs in inv_rep.schema_errors.values() for e in errs)
    assert "INV-INTERFACE-BOUNDARY-GOVERNANCE" in all_schema_errs
    (ctr_dir / "bad_owner.adoc").unlink()
    (cmp_dir / "cmp_with_ctr.adoc").unlink()

    # 6. _test_symbol_exists with class-qualified Python test symbol and non-Python test file
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    py_cls_test = tests_dir / "test_cls.py"
    py_cls_test.write_text(
        "class TestSuite:\n    def test_method_ok(self):\n        assert True\n",
        encoding="utf-8",
    )
    assert SpecValidator._test_symbol_exists(py_cls_test, ["TestSuite", "test_method_ok"]) is True
    assert SpecValidator._test_symbol_exists(py_cls_test, ["TestSuite", "test_missing"]) is False
    assert SpecValidator._test_symbol_exists(py_cls_test, []) is True
    ts_test_file = tests_dir / "app.test.ts"
    ts_test_file.write_text("function verifyWidgetRender() {}\n", encoding="utf-8")
    assert SpecValidator._test_symbol_exists(ts_test_file, ["verifyWidgetRender"]) is True

    # 7. Design system broken manifest path, malformed JSON, non-dict JSON root, and OpenAPI/JSON Schema parse errors
    (ds_dir / "tokens.json").write_text("[1, 2, 3]", encoding="utf-8")
    rep_arr = validator.validate_directory(eidos_dir)
    assert any("root must be a JSON object" in r for r in rep_arr.broken_references)

    (ds_dir / "tokens.json").write_text("{not valid json", encoding="utf-8")
    rep_bad_json = validator.validate_directory(eidos_dir)
    assert any("is not valid JSON" in r for r in rep_bad_json.broken_references)

    (ds_dir / "tokens.json").unlink()
    rep_missing_manifest = validator.validate_directory(eidos_dir)
    assert any("Broken tokens.manifest" in r for r in rep_missing_manifest.broken_references)

    # OpenAPI & JSON Schema contract non-dict roots
    (src_dir / "bad_openapi.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
    (ctr_dir / "bad_oa.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-BAD-OA\n"
        "title: Bad OpenAPI\n"
        "protocol: rest_openapi\n"
        "owning_container: CNT-CLEAN\n"
        "specification_file: src/clean/bad_openapi.yaml\n"
        "---\n"
        "= Bad OpenAPI\n",
        encoding="utf-8",
    )
    (ctr_dir / "bad_js.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-BAD-JS\n"
        "title: Bad JSON Schema\n"
        "protocol: json_schema\n"
        "owning_container: CNT-CLEAN\n"
        "specification_file: src/clean/bad_openapi.yaml\n"
        "---\n"
        "= Bad JSON Schema\n",
        encoding="utf-8",
    )
    rep_ctrs = validator.validate_directory(eidos_dir)
    joined_broken = "\n".join(rep_ctrs.broken_references)
    assert "Invalid OpenAPI specification_file" in joined_broken
    assert "Invalid JSON Schema specification_file" in joined_broken

    # Malformed test_binding "::" across policy invariants, inline component invariants, and UI component test_bindings,
    # plus design/system inheritance path and planned status
    from eidos.core.spec.validator import is_entity_id
    from eidos.core.workspace.assets import (
        get_agent_plugins_schemas_dir,
        get_plugin_reference_dir,
        get_schemas_dir,
        get_v1_schemas_dir,
    )

    assert is_entity_id(123) is False  # type: ignore[arg-type]
    assert get_schemas_dir().is_dir()
    assert get_v1_schemas_dir().is_dir()
    assert get_agent_plugins_schemas_dir().is_dir()
    assert get_plugin_reference_dir().is_dir()

    uic_dir = eidos_dir / "design" / "systems" / "clean" / "components"
    uic_dir.mkdir(parents=True, exist_ok=True)
    (uic_dir / "btn.adoc").write_text(
        "---\n"
        "type: design/ui-component\n"
        "schema: schemas/v1/design/ui-component.schema.yaml\n"
        "id: UIC-BTN-BAD\n"
        "title: Bad Button\n"
        "design_system: DS-CLEAN\n"
        "status: active\n"
        "test_bindings:\n"
        "  unit: \"::\"\n"
        "  a11y: tests/test_cls.py::TestSuite::missing_fn\n"
        "---\n"
        "= Bad Button\n",
        encoding="utf-8",
    )
    (pol_dir / "pol_bad_bind.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "schema: schemas/v1/product/policy.schema.yaml\n"
        "id: POL-BAD-BIND\n"
        "title: Bad Bind\n"
        "category: security\n"
        "rule: Must bind.\n"
        "invariants:\n"
        "  - id: INV-BAD-BIND-1\n"
        "    rule: R\n"
        "    test_binding: \"::\"\n"
        "---\n"
        "= Bad Bind\n",
        encoding="utf-8",
    )
    ds2_dir = eidos_dir / "design" / "systems" / "sub"
    ds2_dir.mkdir(parents=True, exist_ok=True)
    (ds2_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "schema: schemas/v1/design/system.schema.yaml\n"
        "id: DS-SUB\n"
        "title: Sub Tokens\n"
        "status: planned\n"
        "extends_system: DS-CLEAN\n"
        "inheritance:\n"
        "  extends: ../clean/system.adoc\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: missing_planned_tokens.json\n"
        "---\n"
        "= Sub Tokens\n",
        encoding="utf-8",
    )
    rep_more = validator.validate_directory(eidos_dir)
    joined_more = "\n".join(rep_more.broken_references)
    assert "has invalid test binding '::'" in joined_more
    assert "which does not exist in 'tests/test_cls.py'" in joined_more
    assert "has invalid test_binding '::'" in joined_more

    # Also cover inline component invariants (line 1114), TBD bindings (1082, 1124), and workspace/assets.py CWD fallback (22-26)
    import eidos.core.workspace.assets as ws_assets

    (cnt_dir / "clean.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CLEAN\n"
        "name: Clean Service\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "package_path: src/clean\n"
        "components:\n"
        "  - id: CMP-CLEAN-INL\n"
        "    name: Clean Inline\n"
        "    responsibility: Core\n"
        "    source_path: src/clean/ports.py\n"
        "    invariants:\n"
        "      - id: INV-CMP-INL-1\n"
        "        rule: Must hold\n"
        "        test_binding: \"::\"\n"
        "      - id: INV-CMP-INL-2\n"
        "        rule: TBD binding\n"
        "        test_binding: TBD\n"
        "---\n"
        "= Clean Service\n",
        encoding="utf-8",
    )
    rep_inl = validator.validate_directory(eidos_dir)
    assert any("INV-CMP-INL-1" in r for r in rep_inl.broken_references)

    orig_file = ws_assets.__file__
    orig_cwd = os.getcwd()
    try:
        deep_fake = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "assets.py"
        deep_fake.parent.mkdir(parents=True, exist_ok=True)
        ws_assets.__file__ = str(deep_fake)
        cwd_with_assets = tmp_path / "cwd_ws"
        (cwd_with_assets / "assets").mkdir(parents=True, exist_ok=True)
        os.chdir(cwd_with_assets)
        assert ws_assets.get_assets_root() == (cwd_with_assets / "assets").resolve()
        os.chdir(deep_fake.parent)
        with pytest.raises(FileNotFoundError):
            ws_assets.get_assets_root()
    finally:
        ws_assets.__file__ = orig_file
        os.chdir(orig_cwd)

    # Also cover _resolve_spec_and_repo_roots for custom spec folder inside a .git repo and fallback path (lines 162-177)
    from eidos.core.spec.validator import _resolve_spec_and_repo_roots

    repo_m = tmp_path / "repo_m"
    (repo_m / ".git").mkdir(parents=True)
    custom_specs = repo_m / "custom_specs"
    custom_specs.mkdir()
    sr_m, rr_m = _resolve_spec_and_repo_roots(custom_specs)
    assert sr_m == custom_specs.resolve() and rr_m == repo_m.resolve()

    fb_specs = tmp_path / "no_git_root" / "specs"
    fb_specs.mkdir(parents=True)
    sr_fb, rr_fb = _resolve_spec_and_repo_roots(fb_specs)
    assert sr_fb == fb_specs.resolve() and rr_fb == fb_specs.parent.resolve()


def test_delta_expect_closed_schema_rejects_unknown_keys(tmp_path):
    """Verify DLT-2026-079: delta.schema.yaml enforces additionalProperties: false on mutations[].expect,
    expect.body, and expect.frontmatter, permitting only structured body/frontmatter blocks."""
    from eidos.core.spec.parser import parse_spec_file

    validator = SpecValidator()
    dlt_bad_top = tmp_path / "dlt-bad-top.adoc"
    dlt_bad_top.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-991\n"
        "title: Typo or flat key in expect\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: bug\n"
        "  motivation: Test top-level unknown/flat key rejection.\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-CORE\n"
        "    expect:\n"
        "      responsibility: flat_not_allowed\n"
        "---\n"
        "= Bad Delta\n",
        encoding="utf-8",
    )
    errs_top = validator.validate_document(parse_spec_file(dlt_bad_top))
    assert any("responsibility" in e and "Additional properties are not allowed" in e for e in errs_top)

    dlt_bad_nested = tmp_path / "dlt-bad-nested.adoc"
    dlt_bad_nested.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-992\n"
        "title: Typo in nested expect.body\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: bug\n"
        "  motivation: Test nested typo rejection.\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-CORE\n"
        "    expect:\n"
        "      body:\n"
        "        contain: misspelled\n"
        "---\n"
        "= Bad Nested Delta\n",
        encoding="utf-8",
    )
    errs_nested = validator.validate_document(parse_spec_file(dlt_bad_nested))
    assert any("contain" in e and "Additional properties are not allowed" in e for e in errs_nested)


def test_dlt_2026_082_bidirectional_contract_parity_and_multi_framework_cli(tmp_path):
    """Verify DLT-2026-082: bidirectional contract parity detects phantom/deleted operations in CTR-* specs and extracts Click/argparse/sub-Typer CLI commands."""
    validator = SpecValidator()
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    cnts_dir.mkdir(parents=True)
    ctrs_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "svc"
    src_dir.mkdir(parents=True)

    (cnts_dir / "svc.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-SVC\n"
        "name: Service\n"
        "container_type: backend_service\n"
        "runtime: Python 3.12\n"
        "package_path: src/svc\n"
        "---\n"
        "= Service\n",
        encoding="utf-8",
    )

    # 1. Typer CLI with sub-app and phantom documented command + phantom subcommand
    (src_dir / "cli.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "db_app = typer.Typer()\n"
        "app.add_typer(db_app, name='db')\n\n"
        "@app.command()\n"
        "def serve():\n"
        "    pass\n\n"
        "@db_app.command()\n"
        "def migrate():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (ctrs_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "protocol: cli_args\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/cli.py\n"
        "---\n"
        "= CLI Contract\n\n"
        "=== `mybin serve`\nServes app.\n\n"
        "=== `mybin removed-legacy-cmd`\nDeleted command still in spec.\n\n"
        "=== `mybin db`\n"
        "* **Sub-commands**:\n"
        "  - `migrate`: Runs migrations.\n"
        "  - `phantom-rollback`: Deleted subcommand.\n",
        encoding="utf-8",
    )

    # 2. Click CLI and argparse CLI extraction
    (src_dir / "click_app.py").write_text(
        "import click\n\n"
        "@click.group()\n"
        "def cli():\n"
        "    pass\n\n"
        "@cli.command()\n"
        "def sync_users():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (ctrs_dir / "click_ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-CLICK\n"
        "title: Click Contract\n"
        "protocol: cli_args\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/click_app.py\n"
        "---\n"
        "= Click Contract\n\n"
        "=== `phantom-click-cmd`\n",
        encoding="utf-8",
    )

    (src_dir / "arg_app.py").write_text(
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "sub = parser.add_subparsers()\n"
        "sub.add_parser('export-report')\n",
        encoding="utf-8",
    )
    (ctrs_dir / "arg_ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-ARG\n"
        "title: Argparse Contract\n"
        "protocol: cli_args\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/arg_app.py\n"
        "---\n"
        "= Argparse Contract\n\n"
        "=== `old-arg-cmd`\n",
        encoding="utf-8",
    )

    # 3. Bidirectional OpenAPI, gRPC, GraphQL, and Python Protocol phantom operations
    (src_dir / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n  /v1/users:\n    get: {}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "openapi.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-OA\n"
        "title: OpenAPI Contract\n"
        "protocol: rest_openapi\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/openapi.yaml\n"
        "---\n"
        "= OpenAPI\n\n"
        "Exposes `GET /v1/users` and `DELETE /v1/legacy-orders`.\n",
        encoding="utf-8",
    )

    (src_dir / "svc.proto").write_text(
        'syntax = "proto3";\nservice Billing {\n  rpc Charge (Req) returns (Resp);\n}\n',
        encoding="utf-8",
    )
    (ctrs_dir / "grpc.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-GRPC\n"
        "title: gRPC Contract\n"
        "protocol: grpc_proto\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/svc.proto\n"
        "---\n"
        "= Billing\n\n"
        "Defines `service Billing`, `rpc Charge`, and `rpc RefundDeleted`.\n",
        encoding="utf-8",
    )

    (src_dir / "schema.graphql").write_text(
        "type Query {\n  activeUser(id: ID!): User\n}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "graphql.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-GQL\n"
        "title: GraphQL Contract\n"
        "protocol: graphql\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/schema.graphql\n"
        "---\n"
        "= GraphQL\n\n"
        "Operations: `activeUser(id: ID!)` and `deletedMutation(id: ID!)`.\n",
        encoding="utf-8",
    )

    (src_dir / "ports.py").write_text(
        "from typing import Protocol\n\n"
        "class StoragePort(Protocol):\n"
        "    def save_blob(self, key: str) -> bool: ...\n",
        encoding="utf-8",
    )
    (ctrs_dir / "proto.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-PROTO\n"
        "title: Storage Protocol\n"
        "protocol: python_protocol\n"
        "owning_container: CNT-SVC\n"
        "specification_file: src/svc/ports.py\n"
        "---\n"
        "= StoragePort\n\n"
        "Methods: `save_blob(key: str)` and `purge_deleted_blob(key: str)`.\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    broken = "\n".join(report.broken_references)
    assert "removed-legacy-cmd" in broken
    assert "db phantom-rollback" in broken
    assert "sync-users" in broken
    assert "phantom-click-cmd" in broken
    assert "export-report" in broken
    assert "old-arg-cmd" in broken
    assert "/v1/legacy-orders" in broken
    assert "RefundDeleted" in broken
    assert "deletedMutation" in broken
    assert "purge_deleted_blob" in broken


def test_dlt_2026_085_mutations_target_ref_validation_and_schema_rejection(tmp_path: Path, validator: SpecValidator) -> None:
    """DLT-2026-085 (# eidos:fixture): Verify hand-authored affects/blast_radius are rejected by schema
    and mutations[].target enforces prefix/type and operation-aware catalog existence."""
    from eidos.core.spec.parser import parse_spec_file

    eidos_dir = tmp_path / "eidos"

    cnts_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)

    (cnts_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CORE\n"
        "name: Core\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "package_path: src/core\n"
        "components:\n"
        "  - id: CMP-EXISTING\n"
        "    name: Existing\n"
        "    responsibility: Existing component.\n"
        "    source_path: src/core\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )

    # 1. Legacy affects / blast_radius rejected by JSON Schema
    legacy_delta = deltas_dir / "dlt-legacy.adoc"
    legacy_delta.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-098\n"
        "title: Legacy Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Test legacy fields\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-EXISTING\n"
        "affects:\n"
        "  components:\n"
        "    - CMP-EXISTING\n"
        "blast_radius:\n"
        "  impacted_containers:\n"
        "    - CNT-CORE\n"
        "---\n"
        "= Legacy\n",
        encoding="utf-8",
    )
    legacy_errs = "\n".join(validator.validate_document(parse_spec_file(legacy_delta)))
    assert "affects" in legacy_errs and "blast_radius" in legacy_errs
    legacy_delta.unlink()

    # 2. Operation-aware catalog existence on mutations[].target and parent
    (deltas_dir / "dlt-targets.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-099\n"
        "title: Target Validation Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Test operation-aware target validation\n"
        "mutations:\n"
        "  - operation: add\n"
        "    target: CMP-NEW-ADDED\n"
        "    parent: CNT-CORE\n"
        "  - operation: add\n"
        "    target: CAP-NEW-001\n"
        "  - operation: add\n"
        "    target: CAP-NEW-BAD-DOM\n"
        "    parent: DOM-MISSING-PARENT\n"
        "  - operation: add\n"
        "    target: INV-NEW-001\n"
        "    parent: CAP-NEW-001\n"
        "  - operation: add\n"
        "    target: INV-ORPHAN-001\n"
        "    parent: CAP-MISSING-PARENT\n"
        "  - operation: modify\n"
        "    target: CMP-MISSING-MODIFIED\n"
        "---\n"
        "= Target Validation\n",
        encoding="utf-8",
    )

    report = validator.validate_directory(eidos_dir)
    assert report.schema_errors == {}
    broken = "\n".join(report.broken_references)
    assert "CMP-NEW-ADDED" not in broken
    assert "CAP-NEW-001" not in broken
    assert "CAP-NEW-BAD-DOM" not in broken
    assert "DOM-MISSING-PARENT" in broken
    assert "INV-NEW-001" not in broken
    assert "CAP-MISSING-PARENT" in broken
    assert "CMP-MISSING-MODIFIED" in broken


def test_dlt_2026_086_fault_tolerant_frontmatter_model_and_cache_isolation(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-086 (# eidos:fixture): FrontmatterModel gracefully coerces incomplete/malformed nested fields
    (null technical block, shorthand invariant strings, legacy c4_internal_components, non-dict decisions)
    without raising pydantic.ValidationError, and get_cached_spec_document isolates post-parse errors."""
    from eidos.core.index.storage import get_cached_spec_document
    from eidos.core.spec.models import FrontmatterModel

    # 1. Fault-tolerant FrontmatterModel coercion on malformed/partial dicts
    fm = FrontmatterModel.model_validate(
        {
            "type": "tech/container",
            "id": ["CNT-LIST-ID"],
            "name": 123,
            "package_path": ["src/a", "src/b"],
            "technical": None,
            "invariants": [
                "INV-SHORTHAND-001",
                {"id": "INV-DICT-001", "rule": None, "verification": "tests/test_a.py::test_ok"},
            ],
            "c4_internal_components": [
                {
                    "id": "CMP-LEGACY-INLINE",
                    "source_path": ["src/a/mod.py"],
                    "invariants": [{"id": "INV-CMP-001", "rule": "Rule", "test_binding": "tests/test_a.py::test_cmp"}],
                }
            ],
            "decisions": [
                {"id": "DEC-PARTIAL-001", "rejected_alternatives": ["plain-string-alt", {"option": "OptB"}]},
                "non-dict-decision-ignored",
            ],
        }
    )
    assert fm.id is None
    assert fm.name == "123"
    assert fm.package_paths() == ["src/a", "src/b"]
    assert fm.components[0].source_paths() == ["src/a/mod.py"]
    inv_ids = [inv.id for inv in fm.all_invariants()]
    assert inv_ids == ["INV-SHORTHAND-001", "INV-DICT-001", "INV-CMP-001"]
    assert fm.invariants[1].effective_test_binding() == "tests/test_a.py::test_ok"
    assert len(fm.all_decisions()) == 1
    assert fm.all_decisions()[0].rejected_alternatives[0].option == "plain-string-alt"

    # 2. Cached SpecDocument defensive copy prevents cross-reference errors from mutating cached parse errors
    spec_file = tmp_path / "sample.adoc"
    spec_file.write_text(
        "---\n"
        "type: product/domain\n"
        "schema: schemas/v1/product/domain.schema.yaml\n"
        "id: DOM-ISOLATION\n"
        "title: Isolation Domain\n"
        "---\n"
        "= Isolation Domain\n",
        encoding="utf-8",
    )
    doc1, _, _ = get_cached_spec_document(spec_file)
    assert doc1 is not None
    assert doc1.errors == []
    doc1.errors.append("Cross-ref error appended by caller")

    doc2, _, _ = get_cached_spec_document(spec_file)
    assert doc2 is not None
    assert doc2.errors == []


def test_dlt_2026_087_composable_delta_mutation_validation(tmp_path):
    """Verify DLT-2026-087: SpecValidator validates composable mutations (operation + target + parent)."""  # eidos:fixture
    eidos_dir = tmp_path / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    dlt_dir = eidos_dir / "deltas"
    cnt_dir.mkdir(parents=True)
    dlt_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("def run(): pass\n", encoding="utf-8")

    (cnt_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "schema: schemas/v1/tech/container.schema.yaml\n"
        "id: CNT-CORE\n"
        "name: Core\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "package_path: src/core\n"
        "components:\n"
        "  - id: CMP-ENGINE\n"
        "    name: Engine\n"
        "    responsibility: Runs core engine\n"
        "    source_path: src/core/engine.py\n"
        "invariants:\n"
        "  - id: INV-" + "CORE-001\n"
        "    rule: Core must run\n"
        "    test_binding: N/A\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )

    # Valid composable SpecDelta
    valid_dlt = dlt_dir / "dlt-2026-001.adoc"
    valid_dlt.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-001\n"
        "title: Valid Composable Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test composable schema.\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-ENGINE\n"
        "    parent: CNT-CORE\n"
        "  - operation: modify\n"
        "    target: INV-" + "CORE-001\n"
        "    parent: CNT-CORE\n"
        "  - operation: add\n"
        "    target: CMP-NEW-WORKER\n"
        "    parent: CNT-CORE\n"
        "---\n"
        "= Valid Composable Delta\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    rep_ok = validator.validate_directory(eidos_dir)
    assert rep_ok.is_clean, f"Expected clean report, got schema={rep_ok.schema_errors}, refs={rep_ok.broken_references}"

    # Invalid composable SpecDelta: deprecate on CMP-*, add on ARCH-*, add INV-* without parent, and missing modify target
    bad_dlt = dlt_dir / "dlt-2026-002.adoc"
    bad_dlt.write_text(
        "---\n"
        "type: delta/mutation\n"
        "schema: schemas/v1/delta/delta.schema.yaml\n"
        "id: DLT-2026-002\n"
        "title: Invalid Composable Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test invalid composable rules.\n"
        "mutations:\n"
        "  - operation: deprecate\n"
        "    target: CMP-ENGINE\n"
        "  - operation: add\n"
        "    target: ARCH-SYSTEM-001\n"
        "  - operation: add\n"
        "    target: INV-" + "ORPHAN-001\n"
        "  - operation: modify\n"
        "    target: CMP-NONEXISTENT\n"
        "---\n"
        "= Invalid Composable Delta\n",
        encoding="utf-8",
    )
    rep_bad = validator.validate_directory(eidos_dir)
    assert not rep_bad.is_clean
    schema_errs = "\n".join(e for errs in rep_bad.schema_errors.values() for e in errs)
    assert "operation 'deprecate' is only supported for CAP-* and POL-*" in schema_errs
    assert "ARCH-* targets only support operation 'modify'" in schema_errs
    assert "requires a 'parent' specification ID" in schema_errs
    assert any("CMP-NONEXISTENT" in r for r in rep_bad.broken_references)


def test_ast_backed_polyglot_test_symbol_exists_rejects_comments_and_strings(tmp_path: Path) -> None:
    """Verify DLT-2026-089: SpecValidator._test_symbol_exists uses AST test/symbol extraction instead of raw regex."""
    ts_file = tmp_path / "widget.test.ts"
    ts_file.write_text(
        "// function ghostCommentTest() {}\n"
        "/* it('ghostBlockCommentTest', () => {}) */\n"
        "const note = 'ghostStringLiteralTest should not match';\n"
        "test.each([[1, 2]])('validates matrix %i', (a, b) => {});\n"
        "Deno.test({ name: 'deno runtime check', fn() {} });\n"
        "export function verifyRealHelper() {}\n",
        encoding="utf-8",
    )
    assert SpecValidator._test_symbol_exists(ts_file, ["ghostCommentTest"]) is False
    assert SpecValidator._test_symbol_exists(ts_file, ["ghostBlockCommentTest"]) is False
    assert SpecValidator._test_symbol_exists(ts_file, ["ghostStringLiteralTest"]) is False
    assert SpecValidator._test_symbol_exists(ts_file, ["validates matrix %i"]) is True
    assert SpecValidator._test_symbol_exists(ts_file, ["deno runtime check"]) is True
    assert SpecValidator._test_symbol_exists(ts_file, ["verifyRealHelper"]) is True

    go_file = tmp_path / "store_test.go"
    go_file.write_text(
        "package store\n"
        "import \"testing\"\n"
        "// func TestDeletedInComment(t *testing.T) {}\n"
        "func TestStore(t *testing.T) {\n"
        "    msg := \"TestDeletedInString\"\n"
        "    _ = msg\n"
        "    t.Run(\"subtest_atomic_write\", func(t *testing.T) {})\n"
        "}\n",
        encoding="utf-8",
    )
    assert SpecValidator._test_symbol_exists(go_file, ["TestDeletedInComment"]) is False
    assert SpecValidator._test_symbol_exists(go_file, ["TestDeletedInString"]) is False
    assert SpecValidator._test_symbol_exists(go_file, ["TestStore"]) is True
    assert SpecValidator._test_symbol_exists(go_file, ["TestStore/subtest_atomic_write"]) is True
    assert SpecValidator._test_symbol_exists(go_file, ["TestStore", "subtest_atomic_write"]) is True
    assert SpecValidator._test_symbol_exists(go_file, ["WrongParent", "subtest_atomic_write"]) is False

    rs_file = tmp_path / "engine_test.rs"
    rs_file.write_text(
        "// fn fake_commented_rust_test() {}\n"
        "#[tokio::test]\n"
        "async fn real_tokio_async_test() {\n"
        "    let _s = \"fake_string_rust_test\";\n"
        "}\n",
        encoding="utf-8",
    )
    assert SpecValidator._test_symbol_exists(rs_file, ["fake_commented_rust_test"]) is False
    assert SpecValidator._test_symbol_exists(rs_file, ["fake_string_rust_test"]) is False
    assert SpecValidator._test_symbol_exists(rs_file, ["real_tokio_async_test"]) is True


def test_warm_cache_idempotence_duplicate_ids_and_non_src_repo_validation(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """Verify Bugs 1, 8, 9 & status enums: warm-cache idempotence, duplicate CMP/INV IDs, and non-src Go repo path validation."""  # eidos:fixture
    # 1. Bug 1 & Bug 8: Warm-cache idempotence for architectural invariants, applied deltas, and duplicate CMP-*/INV-* IDs
    ws1 = tmp_path / "ws_cache_and_dups"
    eidos1 = ws1 / "eidos"
    (eidos1 / "architecture" / "containers").mkdir(parents=True)
    (eidos1 / "architecture" / "components").mkdir(parents=True)
    (eidos1 / "architecture" / "contracts").mkdir(parents=True)
    (eidos1 / "domains" / "core" / "capabilities").mkdir(parents=True)
    (eidos1 / "policies").mkdir(parents=True)
    (eidos1 / "deltas").mkdir(parents=True)

    # Valid domain, capability (status: sunset), policy (status: removed), and promoted component (status: accepted)
    (eidos1 / "domains" / "core" / "domain.adoc").write_text(
        "---\ntype: product/domain\nschema: schemas/v1/product/domain.schema.yaml\nid: DOM-CORE\nname: Core\nstatus: active\n---\n= Core\n",
        encoding="utf-8",
    )
    (eidos1 / "domains" / "core" / "capabilities" / "cap.adoc").write_text(
        "---\ntype: product/capability\nschema: schemas/v1/product/capability.schema.yaml\nid: CAP-SUNSET-001\ntitle: Sunset Cap\ndomain: DOM-CORE\nstatus: sunset\nbusiness:\n  personas: []\n  jtbd: When sunsetting, I want valid status so validation passes.\ntechnical:\n  containers:\n    - CNT-A\n  invariants: []\n---\n= Sunset Cap\n",
        encoding="utf-8",
    )
    (eidos1 / "policies" / "pol.adoc").write_text(
        "---\ntype: product/policy\nschema: schemas/v1/product/policy.schema.yaml\nid: POL-REMOVED-001\ntitle: Removed Pol\ncategory: security\nstatus: removed\nrule: Old rule.\nenforcement:\n  mechanism: pytest\n  severity: blocking\n---\n= Removed Pol\n",
        encoding="utf-8",
    )
    (eidos1 / "architecture" / "components" / "accepted-comp.adoc").write_text(
        "---\ntype: tech/component\nschema: schemas/v1/tech/component.schema.yaml\nid: CMP-ACCEPTED-001\nname: Accepted Comp\ncontainer: CNT-A\nstatus: accepted\nsource_path: src/a\nresponsibility: Accepted component.\n---\n= Accepted Comp\n",
        encoding="utf-8",
    )

    # Two containers declaring duplicate inline CMP-DUP-001 and duplicate INV-DUP-001
    (eidos1 / "architecture" / "containers" / "cnt-a.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-A\n"
        "name: Container A\n"
        "container_type: service\n"
        "runtime: Python\n"
        "components:\n"
        "  - id: CMP-DUP-001\n"
        "    name: Dup One\n"
        "    responsibility: First duplicate.\n"
        "invariants:\n"
        "  - id: INV-DUP-001\n"
        "    rule: First duplicate invariant.\n"
        "    test_binding: tests/test_a.py::test_one\n"
        "---\n= Container A\n",
        encoding="utf-8",
    )
    (eidos1 / "architecture" / "containers" / "cnt-b.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-B\n"
        "name: Container B\n"
        "container_type: service\n"
        "runtime: Python\n"
        "components:\n"
        "  - id: CMP-DUP-001\n"
        "    name: Dup Two\n"
        "    responsibility: Second duplicate.\n"
        "invariants:\n"
        "  - id: INV-DUP-001\n"
        "    rule: Second duplicate invariant.\n"
        "    test_binding: tests/test_b.py::test_two\n"
        "---\n= Container B\n",
        encoding="utf-8",
    )

    # Contract with forbidden CMP-* owning_container
    (eidos1 / "architecture" / "contracts" / "bad-ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "schema: schemas/v1/tech/contract.schema.yaml\n"
        "id: CTR-BAD-OWNER\n"
        "title: Bad Owner Contract\n"
        "owning_container: CMP-DUP-001\n"
        "protocol: http\n"
        "specification_file: src/api.py\n"
        "---\n= Bad Owner Contract\n",
        encoding="utf-8",
    )

    # Applied delta with architectural invariant error (deprecate on CNT-A) and broken reference (CMP-MISSING-999)
    (eidos1 / "deltas" / "dlt-2026-901.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-901\n"
        "title: Broken Applied Delta\n"
        "status: applied\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Test warm cache idempotence.\n"
        "mutations:\n"
        "  - operation: deprecate\n"
        "    target: CNT-A\n"
        "  - operation: modify\n"
        "    target: CMP-MISSING-999\n"
        "    parent: CNT-A\n"
        "---\n= Broken Applied Delta\n",
        encoding="utf-8",
    )

    rep_cold = validator.validate_directory(eidos1)
    rep_warm = validator.validate_directory(eidos1)
    assert not rep_cold.is_clean
    assert not rep_warm.is_clean
    assert rep_cold.schema_errors == rep_warm.schema_errors
    assert set(rep_cold.broken_references) == set(rep_warm.broken_references)

    # Verify status enum alignment produced zero schema errors on capability, policy, and component
    for clean_key in ("cap.adoc", "pol.adoc", "accepted-comp.adoc"):
        assert not any(k.endswith(clean_key) for k in rep_cold.schema_errors), rep_cold.schema_errors

    # Verify architectural invariant errors persisted across warm cache
    all_schema_errs = "\n".join(e for errs in rep_warm.schema_errors.values() for e in errs)
    assert "Boundary Protocol Contracts (CTR-*) must be owned by a Container (CNT-*)" in all_schema_errs
    assert "operation 'deprecate' is only supported for CAP-* and POL-*" in all_schema_errs

    # Verify duplicate CMP-* and INV-* IDs and invalid contract owner are flagged on warm cache
    broken_joined = "\n".join(rep_warm.broken_references)
    assert "Duplicate entity ID 'CMP-DUP-001'" in broken_joined
    assert "Duplicate entity ID 'INV-DUP-001'" in broken_joined
    assert "owning_container must start with CNT-" in broken_joined

    # 2. Bug 9: Non-src/ non-tests/ Go repository where declared package_path and test_binding are broken
    go_ws = tmp_path / "go_flat_repo"
    go_eidos = go_ws / "eidos"
    (go_eidos / "architecture" / "containers").mkdir(parents=True)
    (go_ws / "pkg" / "server").mkdir(parents=True)
    (go_ws / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    (go_ws / "pkg" / "server" / "server.go").write_text("package server\n\nfunc Run() {}\n", encoding="utf-8")
    (go_ws / "pkg" / "server" / "server_test.go").write_text(
        'package server\n\nimport "testing"\n\nfunc TestExisting(t *testing.T) {}\n',
        encoding="utf-8",
    )
    (go_eidos / "architecture" / "containers" / "go-svc.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-GO-SVC\n"
        "name: Go Server\n"
        "container_type: service\n"
        "runtime: Go 1.22\n"
        "package_path: pkg/nonexistent_dir\n"
        "invariants:\n"
        "  - id: INV-GO-BROKEN-BINDING\n"
        "    rule: Must bind to an existing Go test.\n"
        "    test_binding: pkg/server/server_test.go::TestDoesNotExist\n"
        "---\n= Go Server\n",
        encoding="utf-8",
    )

    rep_go = validator.validate_directory(go_eidos)
    go_broken = "\n".join(rep_go.broken_references)
    assert "pkg/nonexistent_dir" in go_broken
    assert "TestDoesNotExist" in go_broken


def test_validator_class_qualified_test_binding_and_nested_scope(tmp_path: Path) -> None:
    """Verify B2: SpecValidator._test_symbol_exists scopes class methods to their immediate class body
    while still allowing unqualified test_binding (file.py::test_foo) to match a class method."""
    py_file = tmp_path / "test_scoped.py"
    py_file.write_text(
        "class OuterSuite:\n"
        "    def test_outer_only(self):\n"
        "        pass\n"
        "    class InnerHelper:\n"
        "        def test_inner_only(self):\n"
        "            pass\n",
        encoding="utf-8",
    )

    assert SpecValidator._test_symbol_exists(py_file, ["OuterSuite", "test_outer_only"]) is True
    assert SpecValidator._test_symbol_exists(py_file, ["WrongSuite", "test_outer_only"]) is False
    assert SpecValidator._test_symbol_exists(py_file, ["test_outer_only"]) is True
    # InnerHelper's method must not be attributed directly to OuterSuite
    assert SpecValidator._test_symbol_exists(py_file, ["OuterSuite", "test_inner_only"]) is False
    assert SpecValidator._test_symbol_exists(py_file, ["InnerHelper", "test_inner_only"]) is True



