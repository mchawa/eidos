"""Tests verifying the Eidos specification validator and repo dogfooding."""

from pathlib import Path

import pytest

from eidos.core.spec.validator import SpecValidator


@pytest.fixture
def validator():
    return SpecValidator()


def test_bundled_schemas_loaded(validator):
    """Verify that all 16 core v1 schemas are preloaded under path-qualified keys."""
    expected_schemas = [
        "product/system.schema.yaml",
        "product/domain.schema.yaml",
        "product/capability.schema.yaml",
        "product/policy.schema.yaml",
        "product/persona.schema.yaml",
        "product/exploration.schema.yaml",
        "delta/delta.schema.yaml",
        "tech/architecture.schema.yaml",
        "tech/container.schema.yaml",
        "tech/component.schema.yaml",
        "tech/contract.schema.yaml",
        "design/system.schema.yaml",
        "design/ui-component.schema.yaml",
        "design/screen.schema.yaml",
        "design/client.schema.yaml",
        "design/flow.schema.yaml",
    ]
    for schema_name in expected_schemas:
        assert schema_name in validator._schema_cache, f"Missing schema: {schema_name}"


def test_validate_eidos_specifications(validator):
    """Verify INV-SPEC-VALIDATION, INV-SPEC-GRAPH-INTEGRITY, INV-W3C-DTCG-COMPLIANCE, INV-CLIENT-DESIGN-BINDING, INV-POL-001-GIT-SOURCE-OF-TRUTH, and INV-POL-002-INVARIANT-TEST-ENFORCEMENT: all living specifications pass schema validation and entity ID graph integrity."""
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
        "title: Core\n"
        "container: CNT-CORE\n"
        "source_path: src/core\n"
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
    assert "target 'CMP-TEST-CORE'" not in broken_str
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
        "title: Core\n"
        "container: CNT-CORE\n"
        "source_path: src/core\n"
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
        "  contracts:\n"
        "    - CTR-GHOST-1\n"
        "    - CTR-GHOST-2\n"
        "---\n"
        "= Cap\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "DOM-NONEXISTENT" in broken_str
    assert "CNT-GHOST" in broken_str
    assert "CMP-GHOST" in broken_str
    assert "CTR-GHOST-1" in broken_str
    assert "CTR-GHOST-2" in broken_str
    assert "component 'CMP-TEST-CORE'" not in broken_str


def test_component_semantic_references_broken(tmp_path, validator):
    """Verify Initiative A: Container and Component interfaces and contracts are checked via x-eidos-ref."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)

    (cnts_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-TEST-CLI\n"
        "title: CLI\n"
        "container_type: cli_app\n"
        "runtime: python\n"
        "source_path: src/cli\n"
        "domain: DOM-NONEXISTENT\n"
        "contracts:\n"
        "  provides:\n"
        "    - CTR-MISSING-PROV\n"
        "  consumes:\n"
        "    - CTR-MISSING-REQ\n"
        "---\n"
        "= CLI\n"
    )

    (comps_dir / "comp.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-TEST-PARSER\n"
        "title: Parser\n"
        "container: CNT-MISSING-CONTAINER\n"
        "status: planned\n"
        "domain: DOM-MISSING-PEER\n"
        "---\n"
        "= Parser\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "DOM-NONEXISTENT" in broken_str
    assert "CTR-MISSING-PROV" in broken_str
    assert "CTR-MISSING-REQ" in broken_str
    assert "CNT-MISSING-CONTAINER" in broken_str
    assert "DOM-MISSING-PEER" in broken_str


def test_contract_and_domain_semantic_references(tmp_path, validator):
    """Verify Initiative A: Contract container and Domain decisions.graduated_from are validated."""
    eidos_dir = tmp_path / "eidos"
    contracts_dir = eidos_dir / "architecture" / "contracts"
    domains_dir = eidos_dir / "domains" / "sub"
    explorations_dir = eidos_dir / "explorations"
    contracts_dir.mkdir(parents=True)
    domains_dir.mkdir(parents=True)
    explorations_dir.mkdir(parents=True)

    # 1. Contract with non-existent owning container
    (contracts_dir / "broken_contract.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-TEST-API\n"
        "title: Test API\n"
        "protocol: openapi\n"
        "container: CNT-NONEXISTENT-OWNER\n"
        "specification_file: contracts/api.yaml\n"
        "---\n"
        "= Contract\n"
    )

    # 2. Domain with non-existent exploration in decisions.graduated_from
    (domains_dir / "domain.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "id: DOM-SUB-001\n"
        "title: Sub Domain\n"
        "classification: core\n"
        "decisions:\n"
        "  - id: DEC-DOM-001\n"
        "    title: Domain Decision\n"
        "    decision: Split domain\n"
        "    rationale: Clear boundary\n"
        "    graduated_from: EXP-GHOST-SPIKE\n"
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
        "decision:\n"
        "  outcome: adopted\n"
        "  resulting_deltas:\n"
        "    - DLT-NONEXISTENT-DELTA\n"
        "---\n"
        "= Spike\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "CNT-NONEXISTENT-OWNER" in broken_str
    assert "EXP-GHOST-SPIKE" in broken_str
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
        "title: First\n"
        "container: CNT-A\n"
        "source_path: src/first\n"
        "---\n"
        "= First\n"
    )

    (comps_dir / "comp2.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-DUPLICATE-ID\n"
        "title: Second\n"
        "container: CNT-A\n"
        "source_path: src/second\n"
        "---\n"
        "= Second\n"
    )

    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = "\n".join(report.broken_references)
    assert "Duplicate entity ID 'CMP-DUPLICATE-ID'" in broken_str


def test_client_and_flow_semantic_validation(tmp_path, validator):
    """Verify Client (CLT-*) and Flow (FLOW-*) references to design systems, containers, and step clients."""
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

    # Broken flow referencing non-existent client in steps
    (flows_dir / "flow.adoc").write_text(
        "---\n"
        "type: design/flow\n"
        "id: FLOW-TEST-EXP\n"
        "title: Test Flow\n"
        "status: active\n"
        "flow_type: client_internal\n"
        "steps:\n"
        "  - step: 1\n"
        "    title: Step 1\n"
        "    client: CLT-GHOST-CLIENT\n"
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
    """Verify A5: Strict schemas enforce closed properties, status enums, and ID regex patterns."""
    # 1. Verify all 16 bundled schemas enforce closed root properties (additionalProperties: false or unevaluatedProperties: false)
    for schema_key, schema_data in validator._schema_cache.items():
        if schema_key.endswith(".schema.yaml"):
            is_closed = (
                schema_data.get("additionalProperties") is False or schema_data.get("unevaluatedProperties") is False
            )
            assert is_closed, f"Schema {schema_key} missing root additionalProperties/unevaluatedProperties: false"
            props = schema_data.get("properties", {})
            if "status" in props:
                status_prop = props["status"]
                if "$ref" in status_prop:
                    ref_name = status_prop["$ref"].split("/")[-1]
                    status_enum = schema_data.get("$defs", {}).get(ref_name, {}).get("enum", [])
                else:
                    status_enum = status_prop.get("enum", [])
                assert len(status_enum) >= 4, f"Schema {schema_key} status enum missing values: {status_enum}"

    # 2. Verify 'status: planned' is accepted on capability, component, design, exploration, and delta
    cap_file = tmp_path / "cap_planned.adoc"
    cap_file.write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-TEST-PLANNED\n"
        "title: Planned Capability\n"
        "domain: DOM-CORE\n"
        "status: planned\n"
        "business:\n"
        "  personas:\n"
        "    - PER-DEV\n"
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
        "id: CAP-TEST-BAD\n"
        "title: Bad Capability\n"
        "domain: DOM-CORE\n"
        "status: bogus_status\n"
        "unrecognized_root_field: true\n"
        "business:\n"
        "  personas: []\n"
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
        count_refs(schema) for key, schema in validator._schema_cache.items() if key.endswith(".schema.yaml")
    )
    assert total_annotations >= 35, (
        f"Expected >= 35 x-eidos-ref annotations across v1 schemas, found {total_annotations}"
    )


def test_validator_checks_package_path_specification_file_and_cli_contract_parity(tmp_path, validator):
    """Verify SpecValidator detects missing source_path, missing specification_file, and undocumented CLI commands."""
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
        "id: CNT-CLI\n"
        "title: CLI Container\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "source_path:\n"
        "  - src/cli\n"
        "  - src/missing_pkg\n"
        "---\n"
        "= CLI Container\n",
        encoding="utf-8",
    )

    (ctrs_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "protocol: cli_args\n"
        "container: CNT-CLI\n"
        "specification_file: src/cli/main.py\n"
        "---\n"
        "= CLI Contract\n\n"
        "=== `documented-cmd`\nDocumented command.\n",
        encoding="utf-8",
    )

    (ctrs_dir / "missing.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-MISSING\n"
        "title: Missing Contract File\n"
        "protocol: rest_openapi\n"
        "container: CNT-CLI\n"
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
        "---\ntype: product/exploration\nid: EXP-2026-001\ntitle: Valid Spike\nstatus: adopted\n---\n= Spike\n",
        encoding="utf-8",
    )

    (eidos_dir / "system.adoc").write_text(
        "---\n"
        "type: product/system\n"
        "id: SYS-TEST-001\n"
        "title: Test System\n"
        "status: active\n"
        "archetypes:\n"
        "  - cli_tool\n"
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
        "id: CNT-TEST-CORE\n"
        "title: Core\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python\n"
        "source_path: src/core\n"
        "decisions:\n"
        "  - id: DEC-SHARED-ID\n"
        "    title: Duplicate Decision\n"
        "    decision: Collides with system.adoc\n"
        "    rationale: Testing duplicate detection\n"
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
        "id: CMP-DEDICATED-ONE\n"
        "title: DedicatedOne\n"
        "container: CNT-TEST-CORE\n"
        "status: planned\n"
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
        "id: ARCH-TEST-001\n"
        "title: Test Architecture\n"
        "status: active\n"
        "c4_context:\n"
        "  actors: []\n"
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
        "id: CNT-CACHE-CORE\n"
        "title: Cache Core\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python\n"
        "source_path: src/core\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    comp_file.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CACHE-WORKER\n"
        "title: CacheWorker\n"
        "container: eidos/architecture/containers/core.adoc\n"
        "status: planned\n"
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

    def _spy_validate_doc(self, doc, *args, **kwargs):
        validated_docs.append(doc.file_path.name)
        return orig_validate_doc(self, doc, *args, **kwargs)

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
    assert "eidos/architecture/components/worker.adoc" in edited_report.schema_errors

    # Warm run on invalid document still preserves cached schema_errors without re-running validator
    validated_docs.clear()
    warm_invalid_report = validator.validate_directory(eidos_dir)
    assert validated_docs == []
    assert not warm_invalid_report.is_clean
    assert warm_invalid_report.schema_errors == edited_report.schema_errors

    # 3. Delete core.adoc: cache row is pruned and broken container ref is detected in memory
    cnt_file.unlink()
    after_delete_report = validator.validate_directory(eidos_dir)
    assert validated_docs == []
    assert any("core.adoc" in br for br in after_delete_report.broken_references)
    import contextlib

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        cached_paths = {r[0] for r in conn.execute("SELECT file_path FROM spec_validation_cache;")}
    assert not any(p.endswith("core.adoc") for p in cached_paths)

    # 4. Bundled schema hash change invalidates spec_validation_cache
    validator.schema_hash = "mutated_bundled_schema_hash"
    validator.validate_directory(eidos_dir)
    assert validated_docs == ["worker.adoc"]


def test_strict_container_vs_component_target_separation(tmp_path: Path, validator: SpecValidator) -> None:
    """DLT-2026-042 (INV-SPEC-VALIDATION & INV-SPEC-GRAPH-INTEGRITY): Verify strict separation between
    C4 Level 2 Container (CNT-*, tech/container) and Level 3 Component (CMP-*, tech/component) targets
    across capability, delta, container, and client schemas and runtime x-eidos-ref checks."""
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
        "id: CNT-CORE\n"
        "title: Core\n"
        "container_type: library\n"
        "runtime: Python\n"
        "source_path: src/core\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    (comps_dir / "parser.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-PARSER\n"
        "title: Parser\n"
        "container: CNT-CORE\n"
        "status: planned\n"
        "---\n"
        "= Parser\n",
        encoding="utf-8",
    )

    # 1. Capability placing existing CNT-CORE inside technical.components and CMP-PARSER inside technical.containers
    cap_file = caps_dir / "cap.adoc"
    cap_file.write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-CORE-001\n"
        "title: Core Capability\n"
        "domain: DOM-CORE\n"
        "status: planned\n"
        "business:\n"
        "  personas: []\n"
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
    assert "container 'CMP-PARSER'" in broken_text and "container" in broken_text


def test_delta_schema_enum_taxonomies(tmp_path, validator):
    """DLT-2026-043 / INV-SPEC-VALIDATION: Verify delta.schema.yaml enforces strict enums on intent.kind, bug_classification, severity, and mutations[].operation."""
    from eidos.core.spec.parser import parse_spec_file

    delta_file = tmp_path / "dlt-enum.adoc"

    # 1. Valid enum values pass
    delta_file.write_text(
        "---\n"
        "type: delta/mutation\n"
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
        "title": "Test Container",
        "status": "active",
        "source_path": "src/test",
        "decisions": [
            {
                "id": "DEC-TEST-001",
                "title": "Use SQLite",
                "decision": "Store in SQLite",
                "rationale": "Embedded and fast",
                "rejected_alternatives": [{"option": "PostgreSQL", "reason_rejected": "Heavy external service"}],
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
    assert model.title == "Test Container"
    assert len(model.decisions) == 1
    assert model.decisions[0].id == "DEC-TEST-001"
    assert model.decisions[0].rejected_alternatives[0].option == "PostgreSQL"
    assert model.custom_key == "custom_value"


def test_custom_spec_root_under_parent_src_and_subdirectory_validation(
    tmp_path: Path, validator: SpecValidator
) -> None:
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
        "id: CNT-CUSTOM-APP\n"
        "title: Custom App\n"
        "status: active\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/app\n"
        "---\n"
        "= Custom App\n",
        encoding="utf-8",
    )
    (comps_dir / "worker.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CUSTOM-WORKER\n"
        "title: Custom Worker\n"
        "container: CNT-CUSTOM-APP\n"
        "status: active\n"
        "source_path: src/app/worker.py\n"
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

    for expected in (
        "CNT-",
        "CMP-",
        "CTR-",
        "CAP-",
        "DOM-",
        "POL-",
        "PER-",
        "DLT-",
        "DS-",
        "UIC-",
        "SCR-",
        "CLT-",
        "FLOW-",
        "EXP-",
        "SYS-",
        "ARCH-",
        "DEC-",
    ):
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
        "import click\n\n@click.group()\ndef app():\n    pass\n\n@app.command()\ndef click_only_cmd():\n    pass\n",
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
        "id: CNT-CLI\n"
        "title: CLI\n"
        "status: active\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "source_path: src/cli\n"
        "---\n"
        "= CLI\n",
        encoding="utf-8",
    )

    (ctrs_dir / "click_ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLICK-CLI\n"
        "title: Click CLI Contract\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CLI\n"
        "specification_file: src/cli/click_cli.py\n"
        "---\n"
        "= Click CLI Contract\n",
        encoding="utf-8",
    )

    typer_ctr = ctrs_dir / "typer_ctr.adoc"
    typer_ctr.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-TYPER-CLI\n"
        "title: Typer CLI Contract\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CLI\n"
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
    domain owners, contract version, and delta mutations x-eidos-ref."""
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

    # 1. Container with top-level invariants + standalone component invariants
    comps_dir = eidos_dir / "architecture" / "components"
    comps_dir.mkdir(parents=True, exist_ok=True)
    (cnts_dir / "billing.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-BILLING\n"
        "title: Billing Service\n"
        "status: active\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/billing\n"
        "invariants:\n"
        "  - id: INV-CNT-BILLING-001\n"
        "    rule: Container must remain stateless.\n"
        "    test_binding: tests/test_billing.py::test_stateless\n"
        "---\n"
        "= Billing\n",
        encoding="utf-8",
    )
    (comps_dir / "billing-ledger.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-BILLING-LEDGER\n"
        "title: BillingLedger\n"
        "container: CNT-BILLING\n"
        "status: active\n"
        "source_path: src/billing\n"
        "invariants:\n"
        "  - id: INV-CMP-LEDGER-001\n"
        "    rule: Debits must equal credits.\n"
        "    test_binding: tests/test_billing.py::test_ledger\n"
        "---\n"
        "= BillingLedger\n",
        encoding="utf-8",
    )

    # 2. Policy with invariants + decisions
    (pols_dir / "pol.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-001-AUDIT\n"
        "title: Audit Policy\n"
        "category: compliance\n"
        "severity: blocking\n"
        "status: active\n"
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
        "id: DOM-BILLING\n"
        "title: Billing Domain\n"
        "status: active\n"
        "classification: core\n"
        "owners:\n"
        "  - FinOps Team\n"
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
        "id: CAP-BILLING-001\n"
        "title: Invoicing\n"
        "domain: DOM-BILLING\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
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

    # 5. Contract with version and decisions
    (ctrs_dir / "api.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-BILLING-API\n"
        "title: Billing REST API\n"
        "status: active\n"
        "version: 1.2.0\n"
        "protocol: rest_openapi\n"
        "container: CNT-BILLING\n"
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

    # 7. Verify strict #/$defs/semver validation rejects non-SemVer strings while accepting pre-release/build metadata
    from eidos.core.spec.parser import _parse_spec_content

    valid_semver_doc = _parse_spec_content(
        ctrs_dir / "semver_ok.adoc",
        "---\n"
        "type: product/domain\n"
        "id: DOM-SEMVER\n"
        "title: SemVer Domain\n"
        "status: active\n"
        "version: 2.1.0-alpha.1+build.42\n"
        "classification: core\n"
        "---\n"
        "= SemVer Domain\n",
    )
    assert validator.validate_document(valid_semver_doc) == []

    invalid_semver_doc = _parse_spec_content(
        ctrs_dir / "semver_bad.adoc",
        "---\n"
        "type: product/domain\n"
        "id: DOM-SEMVER-BAD\n"
        "title: Bad SemVer Domain\n"
        "status: active\n"
        "version: v1.2\n"
        "classification: core\n"
        "---\n"
        "= Bad SemVer Domain\n",
    )
    semver_errs = "\n".join(validator.validate_document(invalid_semver_doc))
    assert "v1.2" in semver_errs and "does not match" in semver_errs


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
            "source_path": ["src/a", "src/b"],
            "invariants": [{"id": "INV-CNT-1", "rule": "Top rule", "test_binding": "tests/t.py::test_1"}],
            "decisions": [{"id": "DEC-CNT-1", "title": "T1", "decision": "D1", "rationale": "R1"}],
        },
        "= Container\n",
    )
    spec_file = tmp_path / "cnt.adoc"
    spec_file.write_text(dumped, encoding="utf-8")
    from eidos.core.spec.parser import parse_spec_file

    doc = parse_spec_file(spec_file)
    model = doc.as_model()
    assert model.source_paths() == ["src/a", "src/b"]
    assert [i.id for i in model.all_invariants()] == ["INV-CNT-1"]
    assert [d.id for d in model.all_decisions()] == ["DEC-CNT-1"]


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
    assert "eidos/domains/core/capabilities/empty_cap.adoc" in report.schema_errors


def test_schema_cache_collision_free_and_functional_cli_parity(tmp_path: Path, validator: SpecValidator) -> None:
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
        "id: CNT-CLI\n"
        "title: CLI\n"
        "status: active\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "source_path: src/cli\n"
        "---\n"
        "= CLI\n",
        encoding="utf-8",
    )
    ctr_file = ctrs_dir / "cli.adoc"
    ctr_file.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CLI\n"
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

    # 1. Container with one valid standalone component source_path and one missing standalone component source_path
    comps_dir = eidos_dir / "architecture" / "components"
    comps_dir.mkdir(parents=True, exist_ok=True)
    cnt_file = cnts_dir / "app.adoc"
    cnt_file.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-APP\n"
        "title: App\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/app\n"
        "---\n"
        "= App\n",
        encoding="utf-8",
    )
    (comps_dir / "valid.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-VALID\n"
        "title: ValidComp\n"
        "container: CNT-APP\n"
        "status: active\n"
        "source_path: src/app/real.py\n"
        "---\n"
        "= ValidComp\n",
        encoding="utf-8",
    )
    (comps_dir / "broken.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-BROKEN-SRC\n"
        "title: BrokenComp\n"
        "container: CNT-APP\n"
        "status: active\n"
        "source_path: src/app/missing.py\n"
        "---\n"
        "= BrokenComp\n",
        encoding="utf-8",
    )

    # 2. UI Component with broken invariant test_binding file
    uic_file = uic_dir / "btn.adoc"
    uic_file.write_text(
        "---\n"
        "type: design/ui-component\n"
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
        "invariants:\n"
        "  - id: INV-UIC-BTN-001\n"
        "    rule: Button must pass unit test.\n"
        "    test_binding: tests/test_missing_ui.py::test_btn\n"
        "---\n"
        "= Button\n",
        encoding="utf-8",
    )

    # 3. Policy with one valid INV-* test_binding, one missing file, and one missing symbol
    pol_file = pols_dir / "pol.adoc"
    pol_file.write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-001-TEST\n"
        "title: Test Policy\n"
        "category: architecture\n"
        "severity: blocking\n"
        "status: active\n"
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
    assert "INV-UIC-BTN-001" in broken_text and "tests/test_missing_ui.py" in broken_text
    assert "INV-OK" not in broken_text
    assert "INV-MISSING-FILE" in broken_text and "tests/no_such_file.py" in broken_text
    assert "INV-MISSING-SYM" in broken_text and "test_nonexistent_symbol" in broken_text


def test_design_system_w3c_dtcg_manifest_and_acyclic_inheritance(tmp_path: Path, validator: SpecValidator) -> None:
    """DLT-2026-064 (INV-W3C-DTCG-COMPLIANCE): Verify SpecValidator validates W3C DTCG tokens.manifest
    existence and $value/$type structure and detects circular extends chains."""
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
        "id: DS-BRAND-A\n"
        "title: Brand A\n"
        "status: active\n"
        "extends: DS-BRAND-B\n"
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
        "id: DS-BRAND-B\n"
        "title: Brand B\n"
        "status: active\n"
        "extends: DS-BRAND-A\n"
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
    assert (
        "DS-BRAND-A -> DS-BRAND-B -> DS-BRAND-A" in broken_text
        or "DS-BRAND-B -> DS-BRAND-A -> DS-BRAND-B" in broken_text
    )


def test_pluggable_contract_protocol_verifier_registry(tmp_path: Path, validator: SpecValidator) -> None:
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
        "id: CNT-API\n"
        "title: API\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/api\n"
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
        "id: CTR-PY-PROTO\n"
        "title: Payment Gateway Protocol\n"
        "protocol: python_protocol\n"
        "container: CNT-API\n"
        "specification_file: src/api/ports.py\n"
        "---\n"
        "= PaymentGateway\n",
        encoding="utf-8",
    )

    # 2. rest_openapi
    (src_dir / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n  /v1/charges:\n    post: {}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "openapi.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-OPENAPI\n"
        "title: Charges REST API\n"
        "protocol: rest_openapi\n"
        "container: CNT-API\n"
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
        "id: CTR-JSON-SCHEMA\n"
        "title: Invoice Event Schema\n"
        "protocol: json_schema\n"
        "container: CNT-API\n"
        "specification_file: src/api/event.schema.json\n"
        "---\n"
        "= Invoice Event Schema\n",
        encoding="utf-8",
    )

    # 4. grpc_proto
    (src_dir / "ledger.proto").write_text(
        'syntax = "proto3";\nservice LedgerService {\n  rpc RecordEntry (EntryReq) returns (EntryResp);\n}\n',
        encoding="utf-8",
    )
    (ctrs_dir / "grpc.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-GRPC\n"
        "title: Ledger gRPC\n"
        "protocol: grpc_proto\n"
        "container: CNT-API\n"
        "specification_file: src/api/ledger.proto\n"
        "---\n"
        "= LedgerService\n",
        encoding="utf-8",
    )

    # 5. graphql
    (src_dir / "schema.graphql").write_text(
        "type Query {\n  fetchInvoice(id: ID!): Invoice\n}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "graphql.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-GRAPHQL\n"
        "title: GraphQL Schema\n"
        "protocol: graphql\n"
        "container: CNT-API\n"
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

    from eidos.core.spec.parser import _parse_spec_content
    from eidos.core.workspace.project import _normalize_rel_path, match_path_pattern

    # 1. Non-string frontmatter scalars normalize cleanly and _DictCompatibleModel resolves field aliases
    doc = _parse_spec_content(
        tmp_path / "num.adoc",
        "---\n"
        "type: tech/component\n"
        "id: 42\n"
        "title: Numeric Component\n"
        "container: CNT-NUM\n"
        "source_path: src/num\n"
        "status: active\n"
        "domain: DOM-CORE\n"
        "---\n"
        "= Numeric Component\n",
    )
    assert doc.doc_id == "42"
    assert doc.model.type == "tech/component"
    assert doc.model.status == "active"
    assert doc.model.domain == "DOM-CORE"

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
    cmp_dir = eidos_dir / "architecture" / "components"
    dom_dir.mkdir(parents=True)
    cnt_dir.mkdir(parents=True)
    cmp_dir.mkdir(parents=True)
    (cnt_dir / "svc.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-BILLING\n"
        "title: Billing Container\n"
        "status: active\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/billing\n"
        "domain: DOM-BILLING\n"
        "---\n"
        "= Billing Container\n",
        encoding="utf-8",
    )
    (cmp_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-BILLING-CORE\n"
        "title: BillingCore\n"
        "container: CNT-BILLING\n"
        "source_path: src/billing/core.py\n"
        "status: planned\n"
        "domain: DOM-BILLING\n"
        "---\n"
        "= BillingCore\n",
        encoding="utf-8",
    )
    (dom_dir / "billing.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "id: DOM-BILLING\n"
        "title: Billing Domain\n"
        "status: active\n"
        "classification: core\n"
        "---\n"
        "= Billing Domain\n",
        encoding="utf-8",
    )
    validator = SpecValidator()
    sub_report = validator.validate_directory(cnt_dir)
    assert sub_report.is_clean, f"Unexpected errors on subdirectory validation: {sub_report.broken_references}"
    full_report = validator.validate_directory(eidos_dir)
    assert full_report.is_clean, (
        f"Unexpected schema/ref errors: {full_report.schema_errors} {full_report.broken_references}"
    )


def test_validator_cache_second_tier_hit_and_parser_dtcg_protocol_edge_cases(tmp_path):
    """Verify SQLite spec_validation_cache second-tier SHA-256 hits, DTCG token edge cases, clean contract protocols, parser BOM/rules, and workspace helpers."""  # eidos:fixture
    import os
    import sqlite3

    from eidos.core.spec.parser import extract_frontmatter
    from eidos.core.workspace.assets import (
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
        "id: POL-CACHE-001\n"
        "title: Cache Policy\n"
        "status: draft\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-" + "POL-CACHE-1\n"
        "    rule: Validate caching\n"
        "    test_binding: TBD\n"
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
        "id: CNT-CLEAN\n"
        "title: Clean Service\n"
        "status: active\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/clean\n"
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
        "id: CTR-CLEAN-PROTO\n"
        "title: Clean Protocol\n"
        "status: active\n"
        "protocol: python_protocol\n"
        "container: CNT-CLEAN\n"
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
        "id: CTR-BAD-OWNER\n"
        "title: Bad Owner Contract\n"
        "protocol: rest_openapi\n"
        "container: CMP-ILLEGAL\n"
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
        "id: CMP-WITH-CTR\n"
        "title: Component With Contracts\n"
        "container: CNT-CLEAN\n"
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
        "id: CTR-BAD-OA\n"
        "title: Bad OpenAPI\n"
        "protocol: rest_openapi\n"
        "container: CNT-CLEAN\n"
        "specification_file: src/clean/bad_openapi.yaml\n"
        "---\n"
        "= Bad OpenAPI\n",
        encoding="utf-8",
    )
    (ctr_dir / "bad_js.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-BAD-JS\n"
        "title: Bad JSON Schema\n"
        "protocol: json_schema\n"
        "container: CNT-CLEAN\n"
        "specification_file: src/clean/bad_openapi.yaml\n"
        "---\n"
        "= Bad JSON Schema\n",
        encoding="utf-8",
    )
    rep_ctrs = validator.validate_directory(eidos_dir)
    joined_broken = "\n".join(rep_ctrs.broken_references)
    assert "Invalid OpenAPI specification_file" in joined_broken
    assert "Invalid JSON Schema specification_file" in joined_broken

    # Malformed test_binding "::" across policy invariants, inline component invariants, and UI component invariants,
    # plus design/system inheritance path and planned status
    from eidos.core.spec.validator import is_entity_id
    from eidos.core.workspace.assets import (
        get_plugin_reference_dir,
        get_schemas_dir,
        get_v1_schemas_dir,
    )

    assert is_entity_id(123) is False  # type: ignore[arg-type]
    assert get_schemas_dir().is_dir()
    assert get_v1_schemas_dir().is_dir()
    assert get_plugin_reference_dir().is_dir()

    uic_dir = eidos_dir / "design" / "systems" / "clean" / "components"
    uic_dir.mkdir(parents=True, exist_ok=True)
    (uic_dir / "btn.adoc").write_text(
        "---\n"
        "type: design/ui-component\n"
        "id: UIC-BTN-BAD\n"
        "title: Bad Button\n"
        "design_system: DS-CLEAN\n"
        "status: active\n"
        "invariants:\n"
        "  - id: INV-UIC-BAD-UNIT\n"
        "    rule: Unit test must exist\n"
        '    test_binding: "::"\n'
        "  - id: INV-UIC-BAD-A11Y\n"
        "    rule: Accessibility test must exist\n"
        "    test_binding: tests/test_cls.py::TestSuite::missing_fn\n"
        "---\n"
        "= Bad Button\n",
        encoding="utf-8",
    )
    (pol_dir / "pol_bad_bind.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-BAD-BIND\n"
        "title: Bad Bind\n"
        "category: security\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-BAD-BIND-1\n"
        "    rule: R\n"
        '    test_binding: "::"\n'
        "---\n"
        "= Bad Bind\n",
        encoding="utf-8",
    )
    ds2_dir = eidos_dir / "design" / "systems" / "sub"
    ds2_dir.mkdir(parents=True, exist_ok=True)
    (ds2_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "id: DS-SUB\n"
        "title: Sub Tokens\n"
        "status: planned\n"
        "extends: ../clean/system.adoc\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: missing_planned_tokens.json\n"
        "---\n"
        "= Sub Tokens\n",
        encoding="utf-8",
    )
    rep_more = validator.validate_directory(eidos_dir)
    joined_more = "\n".join(rep_more.broken_references)
    assert "which does not exist in 'tests/test_cls.py'" in joined_more
    assert "has invalid test_binding '::'" in joined_more

    # Also cover component invariants, TBD bindings, and workspace/assets.py CWD fallback (22-26)
    import eidos.core.workspace.assets as ws_assets

    (cmp_dir / "clean-inl.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CLEAN-INL\n"
        "title: Clean Component\n"
        "container: CNT-CLEAN\n"
        "status: active\n"
        "source_path: src/clean/ports.py\n"
        "invariants:\n"
        "  - id: INV-CMP-INL-1\n"
        "    rule: Must hold\n"
        '    test_binding: "::"\n'
        "  - id: INV-CMP-INL-2\n"
        "    rule: TBD binding\n"
        "    test_binding: TBD\n"
        "---\n"
        "= Clean Component\n",
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

    # Also cover resolve_spec_and_repo_roots for custom spec folder inside a .git repo and fallback path (lines 162-177)
    from eidos.core.workspace.project import resolve_spec_and_repo_roots

    repo_m = tmp_path / "repo_m"
    (repo_m / ".git").mkdir(parents=True)
    custom_specs = repo_m / "custom_specs"
    custom_specs.mkdir()
    sr_m, rr_m = resolve_spec_and_repo_roots(custom_specs)
    assert sr_m == custom_specs.resolve() and rr_m == repo_m.resolve()

    fb_specs = tmp_path / "no_git_root" / "specs"
    fb_specs.mkdir(parents=True)
    sr_fb, rr_fb = resolve_spec_and_repo_roots(fb_specs)
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
        "id: CNT-SVC\n"
        "title: Service\n"
        "container_type: backend_service\n"
        "runtime: Python 3.12\n"
        "source_path: src/svc\n"
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
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "protocol: cli_args\n"
        "container: CNT-SVC\n"
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
        "import click\n\n@click.group()\ndef cli():\n    pass\n\n@cli.command()\ndef sync_users():\n    pass\n",
        encoding="utf-8",
    )
    (ctrs_dir / "click_ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLICK\n"
        "title: Click Contract\n"
        "protocol: cli_args\n"
        "container: CNT-SVC\n"
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
        "id: CTR-ARG\n"
        "title: Argparse Contract\n"
        "protocol: cli_args\n"
        "container: CNT-SVC\n"
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
        "id: CTR-OA\n"
        "title: OpenAPI Contract\n"
        "protocol: rest_openapi\n"
        "container: CNT-SVC\n"
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
        "id: CTR-GRPC\n"
        "title: gRPC Contract\n"
        "protocol: grpc_proto\n"
        "container: CNT-SVC\n"
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
        "id: CTR-GQL\n"
        "title: GraphQL Contract\n"
        "protocol: graphql\n"
        "container: CNT-SVC\n"
        "specification_file: src/svc/schema.graphql\n"
        "---\n"
        "= GraphQL\n\n"
        "Operations: `activeUser(id: ID!)` and `deletedMutation(id: ID!)`.\n",
        encoding="utf-8",
    )

    (src_dir / "ports.py").write_text(
        "from typing import Protocol\n\nclass StoragePort(Protocol):\n    def save_blob(self, key: str) -> bool: ...\n",
        encoding="utf-8",
    )
    (ctrs_dir / "proto.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-PROTO\n"
        "title: Storage Protocol\n"
        "protocol: python_protocol\n"
        "container: CNT-SVC\n"
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


def test_dlt_2026_085_mutations_target_ref_validation_and_schema_rejection(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-085 (# eidos:fixture): Verify hand-authored affects/blast_radius are rejected by schema
    and mutations[].target enforces prefix/type and operation-aware catalog existence."""
    from eidos.core.spec.parser import parse_spec_file

    eidos_dir = tmp_path / "eidos"

    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)
    (tmp_path / "src" / "core").mkdir(parents=True)

    (cnts_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/core\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    (comps_dir / "existing.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-EXISTING\n"
        "title: Existing\n"
        "container: CNT-CORE\n"
        "status: active\n"
        "source_path: src/core\n"
        "---\n"
        "= Existing\n",
        encoding="utf-8",
    )

    # 1. Legacy affects / blast_radius rejected by JSON Schema
    legacy_delta = deltas_dir / "dlt-legacy.adoc"
    legacy_delta.write_text(
        "---\n"
        "type: delta/mutation\n"
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

    # 2. Operation-aware catalog existence on mutations[].target
    (deltas_dir / "dlt-targets.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-099\n"
        "title: Target Validation Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Test operation-aware target validation\n"
        "mutations:\n"
        "  - operation: add\n"
        "    target: CMP-NEW-ADDED\n"
        "  - operation: add\n"
        "    target: CAP-NEW-001\n"
        "  - operation: add\n"
        "    target: INV-NEW-001\n"
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
    assert "INV-NEW-001" not in broken
    assert "CMP-MISSING-MODIFIED" in broken


def test_dlt_2026_086_fault_tolerant_frontmatter_model_and_cache_isolation(
    tmp_path: Path, validator: SpecValidator
) -> None:
    """DLT-2026-086 (# eidos:fixture): FrontmatterModel gracefully coerces incomplete/malformed nested fields
    (null technical block, shorthand invariant strings, inline components, non-dict decisions)
    without raising pydantic.ValidationError, and get_cached_spec_document isolates post-parse errors."""
    from eidos.core.index.storage import get_cached_spec_document
    from eidos.core.spec.models import FrontmatterModel

    # 1. Fault-tolerant FrontmatterModel coercion on malformed/partial dicts
    fm = FrontmatterModel.model_validate(
        {
            "type": "tech/container",
            "id": ["CNT-LIST-ID"],
            "title": 123,
            "source_path": ["src/a", "src/b"],
            "technical": None,
            "invariants": [
                "INV-SHORTHAND-001",
                {"id": "INV-DICT-001", "rule": None, "test_binding": "tests/test_a.py::test_ok"},
            ],
            "decisions": [
                {"id": "DEC-PARTIAL-001", "rejected_alternatives": ["plain-string-alt", {"option": "OptB"}]},
                "non-dict-decision-ignored",
            ],
        }
    )
    assert fm.id is None
    assert fm.title == "123"
    assert fm.source_paths() == ["src/a", "src/b"]
    inv_ids = [inv.id for inv in fm.all_invariants()]
    assert inv_ids == ["INV-SHORTHAND-001", "INV-DICT-001"]
    assert fm.invariants[1].test_binding == "tests/test_a.py::test_ok"
    assert len(fm.all_decisions()) == 1
    assert fm.all_decisions()[0].rejected_alternatives[0].option == "plain-string-alt"

    # 2. Cached SpecDocument defensive copy prevents cross-reference errors from mutating cached parse errors
    spec_file = tmp_path / "sample.adoc"
    spec_file.write_text(
        "---\ntype: product/domain\nid: DOM-ISOLATION\ntitle: Isolation Domain\n---\n= Isolation Domain\n",
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
    """Verify DLT-2026-087: SpecValidator validates composable mutations (operation + target)."""  # eidos:fixture
    eidos_dir = tmp_path / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cmp_dir = eidos_dir / "architecture" / "components"
    dlt_dir = eidos_dir / "deltas"
    cnt_dir.mkdir(parents=True)
    cmp_dir.mkdir(parents=True)
    dlt_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("def run(): pass\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_core.py").write_text(
        "def test_core_runs():\n    # INV-" + "CORE-001\n    assert True\n", encoding="utf-8"
    )

    (cnt_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/core\n"
        "invariants:\n"
        "  - id: INV-" + "CORE-001\n"
        "    rule: Core must run\n"
        "    test_binding: tests/test_core.py::test_core_runs\n"
        "---\n"
        "= Core\n",
        encoding="utf-8",
    )
    (cmp_dir / "engine.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-ENGINE\n"
        "title: Engine\n"
        "container: CNT-CORE\n"
        "status: active\n"
        "source_path: src/core/engine.py\n"
        "---\n"
        "= Engine\n",
        encoding="utf-8",
    )

    # Valid composable SpecDelta
    valid_dlt = dlt_dir / "dlt-2026-001.adoc"
    valid_dlt.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-001\n"
        "title: Valid Composable Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test composable schema.\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CMP-ENGINE\n"
        "  - operation: modify\n"
        "    target: INV-" + "CORE-001\n"
        "  - operation: add\n"
        "    target: CMP-NEW-WORKER\n"
        "---\n"
        "= Valid Composable Delta\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    rep_ok = validator.validate_directory(eidos_dir)
    assert rep_ok.is_clean, f"Expected clean report, got schema={rep_ok.schema_errors}, refs={rep_ok.broken_references}"

    # Invalid composable SpecDelta: deprecate on INV-* and missing modify target
    bad_dlt = dlt_dir / "dlt-2026-002.adoc"
    bad_dlt.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-002\n"
        "title: Invalid Composable Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test invalid composable rules.\n"
        "mutations:\n"
        "  - operation: deprecate\n"
        "    target: INV-" + "CORE-001\n"
        "  - operation: modify\n"
        "    target: CMP-NONEXISTENT\n"
        "---\n"
        "= Invalid Composable Delta\n",
        encoding="utf-8",
    )
    rep_bad = validator.validate_directory(eidos_dir)
    assert not rep_bad.is_clean
    schema_errs = "\n".join(e for errs in rep_bad.schema_errors.values() for e in errs)
    assert "operation 'deprecate' is not supported for INV-* targets" in schema_errs
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
        'import "testing"\n'
        "// func TestDeletedInComment(t *testing.T) {}\n"
        "func TestStore(t *testing.T) {\n"
        '    msg := "TestDeletedInString"\n'
        "    _ = msg\n"
        '    t.Run("subtest_atomic_write", func(t *testing.T) {})\n'
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
        '    let _s = "fake_string_rust_test";\n'
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

    # Valid domain, capability (status: deprecated), policy (status: retired), and promoted component (status: active)
    (eidos1 / "domains" / "core" / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-CORE\ntitle: Core\nstatus: active\n---\n= Core\n",
        encoding="utf-8",
    )
    (eidos1 / "domains" / "core" / "capabilities" / "cap.adoc").write_text(
        "---\ntype: product/capability\nid: CAP-SUNSET-001\ntitle: Sunset Cap\ndomain: DOM-CORE\nstatus: deprecated\nbusiness:\n  personas: []\ntechnical:\n  containers:\n    - CNT-A\n  invariants: []\n---\n= Sunset Cap\n",
        encoding="utf-8",
    )
    (eidos1 / "policies" / "pol.adoc").write_text(
        "---\ntype: product/policy\nid: POL-REMOVED-001\ntitle: Removed Pol\ncategory: security\nstatus: retired\nseverity: blocking\ninvariants:\n  - id: INV-"
        + "POL-RET-1\n    rule: Old rule.\n    test_binding: N/A\n---\n= Removed Pol\n",
        encoding="utf-8",
    )
    (eidos1 / "architecture" / "components" / "accepted-comp.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-ACCEPTED-001\ntitle: Accepted Comp\ncontainer: CNT-A\nstatus: active\nsource_path: src/a\n---\n= Accepted Comp\n",
        encoding="utf-8",
    )
    (eidos1 / "architecture" / "components" / "dup-1.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-DUP-001\ntitle: Dup One\ncontainer: CNT-A\n---\n= Dup One\n",
        encoding="utf-8",
    )
    (eidos1 / "architecture" / "components" / "dup-2.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-DUP-001\ntitle: Dup Two\ncontainer: CNT-B\n---\n= Dup Two\n",
        encoding="utf-8",
    )

    # Two containers declaring duplicate INV-DUP-001
    (eidos1 / "architecture" / "containers" / "cnt-a.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-A\n"
        "title: Container A\n"
        "container_type: service\n"
        "runtime: Python\n"
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
        "title: Container B\n"
        "container_type: service\n"
        "runtime: Python\n"
        "invariants:\n"
        "  - id: INV-DUP-001\n"
        "    rule: Second duplicate invariant.\n"
        "    test_binding: tests/test_b.py::test_two\n"
        "---\n= Container B\n",
        encoding="utf-8",
    )

    # Contract with forbidden CMP-* container
    (eidos1 / "architecture" / "contracts" / "bad-ctr.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-BAD-OWNER\n"
        "title: Bad Owner Contract\n"
        "container: CMP-DUP-001\n"
        "protocol: http\n"
        "specification_file: src/api.py\n"
        "---\n= Bad Owner Contract\n",
        encoding="utf-8",
    )

    # Applied delta with architectural invariant error (deprecate on INV-DUP-001) and broken reference (CMP-MISSING-999)
    (eidos1 / "deltas" / "dlt-2026-901.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-901\n"
        "title: Broken Applied Delta\n"
        "status: applied\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test warm cache idempotence.\n"
        "mutations:\n"
        "  - operation: deprecate\n"
        "    target: INV-DUP-001\n"
        "  - operation: modify\n"
        "    target: CMP-MISSING-999\n"
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
    assert "operation 'deprecate' is not supported for INV-* targets" in all_schema_errs

    # Verify duplicate CMP-* and INV-* IDs and invalid contract owner are flagged on warm cache
    broken_joined = "\n".join(rep_warm.broken_references)
    assert "Duplicate entity ID 'CMP-DUP-001'" in broken_joined
    assert "Duplicate entity ID 'INV-DUP-001'" in broken_joined
    assert "in container must start with CNT-" in broken_joined

    # 2. Bug 9: Non-src/ non-tests/ Go repository where declared source_path and test_binding are broken
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
        "title: Go Server\n"
        "container_type: service\n"
        "runtime: Go 1.22\n"
        "source_path: pkg/nonexistent_dir\n"
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


def test_adoc_path_reference_target_types_validation(tmp_path: Path, validator: SpecValidator) -> None:
    """Verify Group 1 (#5): .adoc path references in x-eidos-ref fields enforce target_types."""
    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "domains" / "core" / "capabilities").mkdir(parents=True)
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "personas").mkdir(parents=True)
    (tmp_path / "src").mkdir(parents=True)

    # Create a product/domain spec and reference its .adoc path inside business.personas (which requires product/persona)
    dom_path = eidos_dir / "domains" / "core" / "domain.adoc"
    dom_path.write_text(
        "---\n"
        "type: product/domain\n"
        "id: DOM-CORE\n"
        "title: Core\n"
        "classification: core\n"
        "boundaries:\n"
        "  responsibilities:\n"
        "    - Core logic\n"
        "---\n= Core\n",
        encoding="utf-8",
    )
    cnt_path = eidos_dir / "architecture" / "containers" / "core.adoc"
    cnt_path.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core\n"
        "container_type: library\n"
        "runtime: Python\n"
        "source_path: src\n"
        "---\n= Core\n",
        encoding="utf-8",
    )
    cap_path = eidos_dir / "domains" / "core" / "capabilities" / "cap.adoc"
    cap_path.write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-CORE-001\n"
        "title: Cap\n"
        "domain: DOM-CORE\n"
        "status: active\n"
        "version: '1.0.0'\n"
        "business:\n"
        "  personas:\n"
        "    - eidos/domains/core/domain.adoc\n"
        "  jtbd: Test wrong .adoc target type\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-CORE\n"
        "---\n= Cap\n",
        encoding="utf-8",
    )

    rep = validator.validate_directory(eidos_dir)
    broken_str = "\n".join(rep.broken_references)
    assert "Invalid entity reference type in cap.adoc" in broken_str
    assert "eidos/domains/core/domain.adoc" in broken_str
    assert "product/persona" in broken_str


# eidos:fixture
def test_group_b_regressions_b1_through_b8(tmp_path: Path, validator: SpecValidator) -> None:
    """Regression suite for Group B bugs (B1 through B8)."""  # eidos:fixture
    import subprocess

    from eidos.core.index.storage import clone_spec_document, get_cached_spec_document
    from eidos.core.spec.parser import _parse_spec_content, parse_spec_file
    from eidos.core.workspace.project import (
        extract_entity_paths,
        get_tracked_files,
        match_path_pattern,
    )

    # --- B1: clone_spec_document _model isolation ---
    doc_omitted = _parse_spec_content(
        tmp_path / "omitted.adoc",
        "---\ntype: product/domain\nid: DOM-B1\ntitle: Domain B1\n---\n= Domain B1\n",
    )
    assert doc_omitted.model.status is None

    cloned_doc = clone_spec_document(doc_omitted)
    assert cloned_doc._model is None
    cloned_doc.frontmatter["status"] = "deprecated"
    assert cloned_doc.model.status == "deprecated"
    assert doc_omitted.model.status is None

    # --- B4: is_root_system parent directory check ---
    # 1) Repo cloned under a path containing 'design' -> eidos/system.adoc is still root system
    design_clone_sys = tmp_path / "design" / "my_repo" / "eidos" / "system.adoc"
    design_clone_sys.parent.mkdir(parents=True, exist_ok=True)
    design_clone_sys.write_text("", encoding="utf-8")
    parsed_root_sys = parse_spec_file(design_clone_sys)
    assert parsed_root_sys.doc_id == "SYS-UNINITIALIZED"
    assert parsed_root_sys.errors == []
    cached_root_sys, _, _ = get_cached_spec_document(design_clone_sys)
    assert cached_root_sys is not None and cached_root_sys.doc_id == "SYS-UNINITIALIZED"

    # 2) Empty system.adoc inside eidos/domains/ is NOT root system
    dom_sys = tmp_path / "design" / "my_repo" / "eidos" / "domains" / "system.adoc"
    dom_sys.parent.mkdir(parents=True, exist_ok=True)
    dom_sys.write_text("", encoding="utf-8")
    parsed_dom_sys = parse_spec_file(dom_sys)
    assert parsed_dom_sys.doc_type == "unknown"
    assert parsed_dom_sys.errors != []
    cached_dom_sys, _, _ = get_cached_spec_document(dom_sys)
    assert cached_dom_sys is not None and cached_dom_sys.doc_type == "unknown" and cached_dom_sys.errors != []

    # --- B6: resolve_schema disambiguates schemas/v1/system.schema.yaml for design/system & deduplicates validate_document ---
    ds_ambig = _parse_spec_content(
        tmp_path / "ds_ambig.adoc",
        "---\n"
        "type: design/system\n"
        "id: DS-AMBIG\n"
        "title: Ambiguous Schema Design System\n"
        "status: active\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: tokens.json\n"
        "---\n= DS\n",
    )
    resolved_ds_schema = validator.resolve_schema(ds_ambig)
    assert resolved_ds_schema is not None
    assert resolved_ds_schema.get("properties", {}).get("type", {}).get("const") == "design/system"
    assert validator.validate_document(ds_ambig) == []

    # Calling validate_document twice on an invalid doc does not duplicate errors
    bad_doc = _parse_spec_content(
        tmp_path / "bad_dup.adoc",
        "---\ntype: product/domain\nid: INVALID-ID\n---\n= Bad\n",
    )
    errs_first = list(validator.validate_document(bad_doc))
    errs_second = list(validator.validate_document(bad_doc))
    assert errs_first == errs_second

    # --- B2, B3, B5, B7: End-to-end directory validation ---
    ws = tmp_path / "ws_group_b"
    eidos_dir = ws / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    pols_dir = eidos_dir / "policies"
    deltas_dir = eidos_dir / "deltas"
    uic_dir = eidos_dir / "design" / "systems" / "main" / "components"
    src_dir = ws / "src" / "app"
    tests_dir = ws / "tests"
    for d in (cnts_dir, ctrs_dir, pols_dir, deltas_dir, uic_dir, src_dir, tests_dir):
        d.mkdir(parents=True, exist_ok=True)

    (tests_dir / "test_app.py").write_text("def test_valid():\n    assert True\n", encoding="utf-8")
    (src_dir / "__init__.py").write_text("", encoding="utf-8")

    (cnts_dir / "app.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-APP\n"
        "title: App\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/app\n"
        "---\n= App\n",
        encoding="utf-8",
    )

    # B2: JSON Schema with required: true (boolean instead of list)
    (src_dir / "bool_req.schema.json").write_text(
        '{"type": "object", "required": true, "properties": {"order_id": {"type": "string"}}}',
        encoding="utf-8",
    )
    (ctrs_dir / "js_bool.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-JS-BOOL\n"
        "title: JSON Schema Bool Required\n"
        "protocol: json_schema\n"
        "container: CNT-APP\n"
        "specification_file: src/app/bool_req.schema.json\n"
        "---\n= JSON Schema\n\nDocuments `order_id`.\n",
        encoding="utf-8",
    )

    # B3: test_binding with missing symbol + trailing '::' with empty symbol
    (pols_dir / "pol_b3.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-B3\n"
        "title: Policy B3\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-B3-TRAILING\n"  # eidos:fixture
        "    rule: Trailing colons.\n"
        '    test_binding: "tests/test_app.py::"\n'
        "  - id: INV-B3-VERIF\n"  # eidos:fixture
        "    rule: Missing symbol in test_binding.\n"
        '    test_binding: "tests/test_app.py::test_nonexistent_verif"\n'
        "---\n= Policy B3\n",
        encoding="utf-8",
    )

    # B5: Delta with non-existent .adoc paths in tombstone, expect, and remove/add mutations
    (deltas_dir / "dlt_b5.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-888\n"
        "title: Delta B5\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Test B5 dict reference exemptions.\n"
        "tombstone:\n"
        "  removed_files:\n"
        "    - eidos/domains/deleted_legacy.adoc\n"
        "mutations:\n"
        "  - operation: remove\n"
        "    target: CMP-OLD\n"
        "    details: Removes eidos/architecture/components/removed_comp.adoc\n"
        "    expect:\n"
        "      frontmatter:\n"
        "        ref: eidos/architecture/components/removed_comp.adoc\n"
        "---\n= Delta B5\n",
        encoding="utf-8",
    )

    # B7: Python Protocol with duplicate method across classes, prose backticks `O(1)` / `len(x)`, and // comments
    (src_dir / "proto_b7.py").write_text(
        "from typing import Protocol\n\n"
        "class ReaderA(Protocol):\n"
        "    def read_chunk(self, size: int) -> bytes: ...\n"
        "    def missing_once(self) -> None: ...\n\n"
        "class ReaderB(Protocol):\n"
        "    def read_chunk(self, size: int) -> bytes: ...\n"
        "    def missing_once(self) -> None: ...\n",
        encoding="utf-8",
    )
    (ctrs_dir / "proto_b7.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-PROTO-B7\n"
        "title: Protocol B7\n"
        "protocol: python_protocol\n"
        "container: CNT-APP\n"
        "specification_file: src/app/proto_b7.py\n"
        "---\n= Protocol B7\n\n"
        "Provides `ReaderA` and `ReaderB` with `read_chunk(size: int)` in `O(1)` time for `len(x)` bytes.\n"
        "// `def commented_out_phantom(self)`\n"
        "Also documents `def undocumented_proto_fn(self)`.\n",
        encoding="utf-8",
    )

    # B7: GraphQL with prose backticks `O(1)` and `helper(x)` vs real operation
    (src_dir / "schema_b7.graphql").write_text(
        "type Query {\n  getItem(id: ID!): Item\n}\ntype Mutation {\n  getItem(id: ID!): Item\n}\n",
        encoding="utf-8",
    )
    (ctrs_dir / "gql_b7.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-GQL-B7\n"
        "title: GraphQL B7\n"
        "protocol: graphql\n"
        "container: CNT-APP\n"
        "specification_file: src/app/schema_b7.graphql\n"
        "---\n= GraphQL B7\n\n"
        "Runs `getItem(id: ID!)` in `O(1)` complexity using `helper(x)`.\n",
        encoding="utf-8",
    )

    # B7: Typer CLI with constructor name=..., unnamed mounted sub-app, and async def command
    (src_dir / "typer_b7.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "users_app = typer.Typer(name='users')\n"
        "flat_app = typer.Typer()\n"
        "app.add_typer(users_app)\n"
        "app.add_typer(flat_app)\n\n"
        "@users_app.command()\n"
        "async def list_users():\n"
        "    pass\n\n"
        "@flat_app.command()\n"
        "async def ping():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (ctrs_dir / "typer_b7.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-TYPER-B7\n"
        "title: Typer B7\n"
        "protocol: cli_args\n"
        "container: CNT-APP\n"
        "specification_file: src/app/typer_b7.py\n"
        "---\n= Typer B7\n\n"
        "=== `ping`\nPings server.\n\n"
        "=== `users`\n"
        "* **Sub-commands**:\n"
        "  - `list-users`: Lists users.\n",
        encoding="utf-8",
    )

    # B7: Click CLI with @<group>.group() and <group>.add_command(...)
    (src_dir / "click_b7.py").write_text(
        "import click\n\n"
        "@click.group()\n"
        "def cli():\n"
        "    pass\n\n"
        "@cli.group()\n"
        "def cache():\n"
        "    pass\n\n"
        "@cache.command()\n"
        "def clear_cache():\n"
        "    pass\n\n"
        "@click.command()\n"
        "def doctor_check():\n"
        "    pass\n\n"
        "cli.add_command(doctor_check, name='doctor')\n",
        encoding="utf-8",
    )
    (ctrs_dir / "click_b7.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLICK-B7\n"
        "title: Click B7\n"
        "protocol: cli_args\n"
        "container: CNT-APP\n"
        "specification_file: src/app/click_b7.py\n"
        "---\n= Click B7\n\n"
        "=== `cache`\n"
        "* **Sub-commands**:\n"
        "  - `clear-cache`: Clears cache.\n\n"
        "=== `doctor`\nRuns doctor.\n",
        encoding="utf-8",
    )

    rep_b = validator.validate_directory(eidos_dir)
    broken_b = rep_b.broken_references
    broken_b_text = "\n".join(broken_b)

    # B2: no crash, and order_id is documented
    assert "bool_req.schema.json" not in broken_b_text
    # B3: trailing '::' and missing symbol caught
    assert "Malformed test_binding 'tests/test_app.py::' (empty symbol after '::')" in broken_b_text
    assert "test_nonexistent_verif" in broken_b_text
    # B5: deleted_legacy.adoc and removed_comp.adoc are NOT flagged
    assert "deleted_legacy.adoc" not in broken_b_text
    assert "removed_comp.adoc" not in broken_b_text
    # B7: Python protocol deduplicated missing_once (exactly 1 error), caught undocumented_proto_fn, ignored O(1)/len(x)/commented_out_phantom
    assert sum(1 for r in broken_b if "missing_once" in r) == 1
    assert "undocumented_proto_fn" in broken_b_text
    assert "commented_out_phantom" not in broken_b_text
    assert "'O'" not in broken_b_text and "'len'" not in broken_b_text and "'helper'" not in broken_b_text
    # B7: GraphQL, Typer, and Click contracts are completely clean
    assert "CTR-GQL-B7" not in broken_b_text
    assert "CTR-TYPER-B7" not in broken_b_text
    assert "CTR-CLICK-B7" not in broken_b_text

    # --- B8: match_path_pattern, get_tracked_files, and extract_entity_paths ---
    target_file = "src/eidos/core/spec/validator.py"
    score_specific_dir = match_path_pattern(target_file, "src/eidos/core/spec")
    score_broad_glob_all = match_path_pattern(target_file, "src/**/*")
    score_broad_glob_py = match_path_pattern(target_file, "src/**/*.py")
    assert score_specific_dir > score_broad_glob_py > score_broad_glob_all > 0
    assert 0 < match_path_pattern("setup.py", "*.py") < 500
    assert 0 < match_path_pattern("app.py", "a*.py") < 500

    extracted_paths = extract_entity_paths(
        {
            "source_path": ["src/core", "N/A", "src/owned", "NONE", "TBD"],
            "specification_file": "N/A",
        }
    )
    assert extracted_paths == [
        "src/core",
        "src/owned",
    ]

    git_b8 = tmp_path / "git_b8"
    (git_b8 / "src").mkdir(parents=True)
    (git_b8 / "eidos" / ".cache").mkdir(parents=True)
    (git_b8 / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (git_b8 / "eidos" / ".cache" / "index.db").write_text("sqlite", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=git_b8, check=True)
    tracked_b8 = get_tracked_files(git_b8, include_untracked=True)
    assert tracked_b8 == ["src/main.py"]


def test_deep_audit_group_b_b1_through_b10(tmp_path):
    """Verify deep audit Group B fixes (B1 through B10)."""  # eidos:fixture
    import orjson

    from eidos.core.spec.models import FrontmatterModel
    from eidos.core.spec.parser import parse_spec_file
    from eidos.core.spec.validator import SpecValidator
    from eidos.core.workspace.project import resolve_spec_and_repo_roots

    validator = SpecValidator()

    # --- B10: FrontmatterModel attribute defaults ---
    fm_min = FrontmatterModel.model_validate({"id": "POL-MIN", "type": "product/policy", "schema": None})
    assert fm_min.model_extra["schema"] is None
    assert fm_min.id == "POL-MIN"
    assert fm_min.title is None
    assert fm_min.invariants == []
    assert fm_min.mutations == []
    assert fm_min.decisions == []

    # --- B1: Nested design system.adoc is not classified as root product/system ---
    repo = tmp_path / "repo_b"
    eidos_dir = repo / "eidos"
    ds_brand_dir = eidos_dir / "design" / "systems" / "brand"
    ds_brand_dir.mkdir(parents=True)
    (eidos_dir / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-B\ntitle: System B\narchetypes: [service]\n---\n= System B\n",
        encoding="utf-8",
    )
    empty_ds_sys = ds_brand_dir / "system.adoc"
    empty_ds_sys.write_text("", encoding="utf-8")
    parsed_empty_ds = parse_spec_file(empty_ds_sys)
    assert parsed_empty_ds.doc_type == "unknown"
    spec_root, _ = resolve_spec_and_repo_roots(ds_brand_dir, empty_ds_sys)
    assert spec_root == eidos_dir.resolve()
    empty_ds_sys.unlink()

    # --- B5: _strip_asciidoc_comments strips block comments with trailing whitespace and empty block comments ---
    commented_body = "////   \n=== `ghost-cmd`\n////  \n////\n////\n=== `real-cmd`\n"
    stripped_body = SpecValidator._strip_asciidoc_comments(commented_body)
    assert "ghost-cmd" not in stripped_body
    assert "real-cmd" in stripped_body

    # --- B2, B3, B4: Cyclic Typer add_typer, Click sub-group add_command, and CLI section bounds / multi-token subcommands ---
    src_dir = repo / "src" / "app"
    tests_dir = repo / "tests"
    cnt_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    dom_dir = eidos_dir / "domains" / "core"
    cap_dir = dom_dir / "capabilities"
    pol_dir = eidos_dir / "policies"
    deltas_dir = eidos_dir / "deltas"
    for d in (src_dir, tests_dir, cnt_dir, ctrs_dir, dom_dir, cap_dir, pol_dir, deltas_dir):
        d.mkdir(parents=True, exist_ok=True)

    (tests_dir / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    cmp_dir = eidos_dir / "architecture" / "components"
    cmp_dir.mkdir(parents=True, exist_ok=True)
    (cnt_dir / "app.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-APP\n"
        "title: App Container\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: src/app\n"
        "---\n= App Container\n",
        encoding="utf-8",
    )
    (cmp_dir / "planned.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-PLANNED\n"
        "title: Planned Comp\n"
        "container: CNT-APP\n"
        "status: planned\n"
        "invariants:\n"
        "  - id: INV-PLANNED-SKIP\n"
        "    rule: Planned component invariant\n"
        "    test_binding: tests/test_app.py::test_not_written_yet\n"
        "---\n= Planned Comp\n",
        encoding="utf-8",
    )

    # B2 & B4: Cyclic Typer app with multi-token subcommands (`portal serve`) and subsequent `== Notes` heading
    (src_dir / "typer_cycle.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "portal_app = typer.Typer(name='portal')\n"
        "app.add_typer(portal_app, name='portal')\n"
        "portal_app.add_typer(app)\n\n"
        "@portal_app.command()\n"
        "def serve():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (ctrs_dir / "typer_cycle.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-TYPER-CYCLE\n"
        "title: Typer Cycle\n"
        "protocol: cli_args\n"
        "container: CNT-APP\n"
        "specification_file: src/app/typer_cycle.py\n"
        "---\n= Typer Cycle\n\n"
        "=== `portal`\n"
        "* **Sub-commands**:\n"
        "  - `portal serve`: Starts portal.\n"
        "  - `portal ghost-sub`: Nonexistent subcommand.\n\n"
        "== Implementation Notes\n"
        "- `unrelated-note-token`: Not a subcommand.\n",
        encoding="utf-8",
    )

    # B3: Click sub-group mounted via cli.add_command(sub_grp) with subcommand
    (src_dir / "click_sub.py").write_text(
        "import click\n\n"
        "@click.group()\n"
        "def cli():\n"
        "    pass\n\n"
        "@click.group(name='db')\n"
        "def db_grp():\n"
        "    pass\n\n"
        "cli.add_command(db_grp)\n\n"
        "@db_grp.command()\n"
        "def migrate():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (ctrs_dir / "click_sub.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLICK-SUB\n"
        "title: Click Sub\n"
        "protocol: cli_args\n"
        "container: CNT-APP\n"
        "specification_file: src/app/click_sub.py\n"
        "---\n= Click Sub\n\n"
        "=== `db`\n"
        "* **Sub-commands**:\n"
        "  - `migrate`: Runs migrations.\n",
        encoding="utf-8",
    )

    # B9: Capability with domain is schema-valid, and contract with component path owner is rejected
    (dom_dir / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-CORE\ntitle: Core\n---\n= Core\n",
        encoding="utf-8",
    )
    cap_pdom = cap_dir / "cap_pdom.adoc"
    cap_pdom.write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-PDOM\n"
        "title: Parent Domain Capability\n"
        "domain: DOM-CORE\n"
        "status: active\n"
        "business:\n"
        "  personas: [PER-DEV]\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-APP\n"
        "---\n= Parent Domain Capability\n",
        encoding="utf-8",
    )
    cap_doc = parse_spec_file(cap_pdom)
    assert cap_doc.model.domain == "DOM-CORE"
    assert validator.validate_document(cap_doc, root_dir=eidos_dir) == []

    bad_ctr_cmp = ctrs_dir / "bad_cmp_owner.adoc"
    bad_ctr_cmp.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-BAD-OWNER\n"
        "title: Bad Owner\n"
        "protocol: rest_openapi\n"
        "container: eidos/architecture/components/cmp-auth.adoc\n"
        "---\n= Bad Owner\n",
        encoding="utf-8",
    )

    # B6: Delta with .adoc targets for deprecate (INV) and modify on missing .adoc
    (deltas_dir / "dlt_b6.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-960\n"
        "title: Delta B6\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: enabler\n"
        "  motivation: Test B6 path invariants\n"
        "mutations:\n"
        "  - operation: deprecate\n"
        "    target: INV-DEPRECATED.adoc\n"
        "  - operation: modify\n"
        "    target: eidos/architecture/containers/missing_parent.adoc\n"
        "---\n= Delta B6\n",
        encoding="utf-8",
    )

    # B7: Double colons in test_binding
    (pol_dir / "pol_b7.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-B7\n"
        "title: Policy B7\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-B7-DBL\n"
        "    rule: Double colons\n"
        "    test_binding: tests/test_app.py::::test_ok\n"
        "---\n= Policy B7\n",
        encoding="utf-8",
    )

    # B8: Root-level scalar in W3C DTCG tokens.json and cyclic DS-* inheritance deduplication
    ds_a_dir = eidos_dir / "design" / "systems" / "dsa"
    ds_b_dir = eidos_dir / "design" / "systems" / "dsb"
    ds_a_dir.mkdir(parents=True, exist_ok=True)
    ds_b_dir.mkdir(parents=True, exist_ok=True)
    (ds_a_dir / "tokens.json").write_bytes(
        orjson.dumps(
            {
                "root_scalar_bad": "#123456",
                "color": {"primary": {"$value": "#000000", "$type": "color"}},
            }
        )
    )
    (ds_a_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "id: DS-A\n"
        "title: Design A\n"
        "status: active\n"
        "extends: DS-B\n"
        "tokens:\n"
        "  manifest: tokens.json\n"
        "  spec_format: w3c_design_tokens\n"
        "---\n= Design A\n",
        encoding="utf-8",
    )
    (ds_b_dir / "system.adoc").write_text(
        "---\ntype: design/system\nid: DS-B\ntitle: Design B\nstatus: draft\nextends: DS-A\n---\n= Design B\n",
        encoding="utf-8",
    )

    rep = validator.validate_directory(eidos_dir)
    broken_text = "\n".join(rep.broken_references)
    schema_err_text = "\n".join(msg for msgs in rep.schema_errors.values() for msg in msgs)

    # B2 & B4: portal serve accepted, portal ghost-sub flagged, unrelated-note-token after == Heading ignored
    assert "portal serve" not in broken_text
    assert "portal ghost-sub" in broken_text
    assert "unrelated-note-token" not in broken_text
    # B3: Click subcommand migrate under db accepted cleanly
    assert "CTR-CLICK-SUB" not in broken_text
    # B6: .adoc path invariants and broken target reference caught
    assert "operation 'deprecate' is not supported for INV-* targets" in schema_err_text
    assert "cannot locate 'eidos/architecture/containers/missing_parent.adoc'" in broken_text
    # B7: Double colons caught, and planned inline component invariant skipped
    assert "Malformed test_binding 'tests/test_app.py::::test_ok'" in broken_text
    assert "INV-PLANNED-SKIP" not in broken_text
    # B8: Root scalar token caught, and circular DS-A <-> DS-B reported once
    assert "token 'root_scalar_bad' is missing required '$value' property" in broken_text
    assert sum(1 for r in rep.broken_references if "Circular design system inheritance" in r) == 1
    # B9: Contract with component path owner flagged by INV-INTERFACE-BOUNDARY-GOVERNANCE
    assert "Contract 'CTR-BAD-OWNER' specifies in-process component" in schema_err_text


def test_dlt_2026_113_component_public_surface_symbol_validation(tmp_path: Path, validator: SpecValidator) -> None:
    """Verify DLT-2026-113-HARDEN-SPEC-B4EE: SpecValidator._validate_source_and_contract_paths
    flags nonexistent backticked symbols under '== Public Surface' in active CMP-*/CNT-* specs."""
    repo_root = tmp_path / "pub_surface_repo"
    eidos_dir = repo_root / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir = eidos_dir / "architecture" / "components"
    cnts_dir.mkdir(parents=True)
    comps_dir.mkdir(parents=True)
    (repo_root / "src" / "pkg").mkdir(parents=True)
    (repo_root / "src" / "pkg" / "engine.py").write_text(
        "DEFAULT_LIMIT = 10\n\n"
        "class BaseEngine:\n"
        "    def shared_run(self) -> bool:\n"
        "        return True\n\n"
        "class SubEngine(BaseEngine):\n"
        "    def execute(self) -> int:\n"
        "        return DEFAULT_LIMIT\n",
        encoding="utf-8",
    )

    (cnts_dir / "pkg.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-PKG\n"
        "title: Package Container\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/pkg\n"
        "---\n"
        "= Container: Package\n",
        encoding="utf-8",
    )
    cmp_path = comps_dir / "engine.adoc"
    cmp_path.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-ENGINE\n"
        "title: Engine Component\n"
        "container: CNT-PKG\n"
        "status: active\n"
        "source_path: src/pkg/engine.py\n"
        "---\n"
        "= Component: Engine\n\n"
        "== Public Surface\n"
        "* `DEFAULT_LIMIT`\n"
        "* `SubEngine.execute() -> int`\n"
        "* `SubEngine.shared_run() -> bool`\n"
        "* `ghost_removed_func(x: int) -> None`\n",
        encoding="utf-8",
    )

    rep_bad = validator.validate_directory(eidos_dir)
    assert not rep_bad.is_clean
    broken_str = "\n".join(rep_bad.broken_references)
    assert "ghost_removed_func" in broken_str
    assert "SubEngine.shared_run" not in broken_str
    assert "DEFAULT_LIMIT" not in broken_str

    # Removing the nonexistent symbol makes the report clean
    cmp_path.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-ENGINE\n"
        "title: Engine Component\n"
        "container: CNT-PKG\n"
        "status: active\n"
        "source_path: src/pkg/engine.py\n"
        "---\n"
        "= Component: Engine\n\n"
        "== Public Surface\n"
        "* `DEFAULT_LIMIT`\n"
        "* `SubEngine.execute() -> int`\n"
        "* `SubEngine.shared_run() -> bool`\n",
        encoding="utf-8",
    )
    rep_clean = validator.validate_directory(eidos_dir)
    assert rep_clean.is_clean, f"Unexpected errors: {rep_clean.broken_references}"


def test_dlt_2026_114_cli_contract_positional_placeholder_alignment(tmp_path: Path, validator: SpecValidator) -> None:
    """Verify DLT-2026-114-RESOLVE-SYMBOLS-0327: SpecValidator._verify_cli_contract_commands
    flags positional argument count mismatch and placeholder-to-parameter/metavar misalignment."""
    repo_root = tmp_path / "cli_pos_repo"
    eidos_dir = repo_root / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    ctrs_dir = eidos_dir / "architecture" / "contracts"
    cnts_dir.mkdir(parents=True)
    ctrs_dir.mkdir(parents=True)
    src_dir = repo_root / "src" / "cli"
    src_dir.mkdir(parents=True)

    (src_dir / "main.py").write_text(
        "import typer\n\n"
        "app = typer.Typer()\n"
        "index_app = typer.Typer(name='index')\n"
        "app.add_typer(index_app, name='index')\n\n"
        "@index_app.command('blast-radius')\n"
        "def blast_radius_cmd(entity_id: str = typer.Argument(...)):\n"
        "    pass\n",
        encoding="utf-8",
    )
    (cnts_dir / "cli.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CLI\n"
        "title: CLI Container\n"
        "status: active\n"
        "container_type: cli\n"
        "runtime: Python 3.12\n"
        "source_path: src/cli\n"
        "---\n= CLI\n",
        encoding="utf-8",
    )
    ctr_path = ctrs_dir / "cli.adoc"
    ctr_path.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CLI\n"
        "specification_file: src/cli/main.py\n"
        "---\n= CLI Contract\n\n"
        "=== `eidos index`\n"
        "* **Sub-commands**:\n"
        "  - `blast-radius <symbol_or_path>`: Computes blast radius.\n",
        encoding="utf-8",
    )

    rep_mismatch = validator.validate_directory(eidos_dir)
    assert not rep_mismatch.is_clean
    assert any("symbol_or_path" in r and "entity_id" in r for r in rep_mismatch.broken_references)

    # Aligning the parameter name in main.py (plus Annotated[..., typer.Argument(...)] and 3-level nested subcommands) resolves drift
    (src_dir / "main.py").write_text(
        "from typing import Annotated\n"
        "import typer\n\n"
        "app = typer.Typer()\n"
        "index_app = typer.Typer(name='index')\n"
        "semantic_app = typer.Typer(name='semantic')\n"
        "index_app.add_typer(semantic_app, name='semantic')\n"
        "app.add_typer(index_app, name='index')\n\n"
        "@index_app.command('blast-radius')\n"
        "def blast_radius_cmd(symbol_or_path: str = typer.Argument(..., metavar='SYMBOL_OR_PATH')):\n"
        "    pass\n\n"
        "@index_app.command('sync')\n"
        "def index_sync_cmd(target: Annotated[str, typer.Argument()] = 'all'):\n"
        "    pass\n\n"
        "@semantic_app.command('sync')\n"
        "def semantic_sync_cmd(force: bool = typer.Option(False, '--force')):\n"
        "    pass\n",
        encoding="utf-8",
    )
    ctr_path.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLI\n"
        "title: CLI Contract\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CLI\n"
        "specification_file: src/cli/main.py\n"
        "---\n= CLI Contract\n\n"
        "=== `eidos index`\n"
        "* **Sub-commands**:\n"
        "  - `eidos index blast-radius <symbol_or_path>`: Computes blast radius.\n"
        "  - `eidos index sync [target]`: Syncs index.\n"
        "  - `eidos index semantic sync [--force]`: Syncs semantic embeddings.\n",
        encoding="utf-8",
    )
    rep_ok = validator.validate_directory(eidos_dir)
    assert rep_ok.is_clean, f"Unexpected errors: {rep_ok.broken_references}"


def test_dlt_132_134_136_tokens_containment_active_bindings_and_strict_schemas(tmp_path, validator):
    """Verify DLT-2026-132, DLT-2026-134, and DLT-2026-136: tokens.manifest containment, repo-relative schema_errors, strict active test_binding (including UIC-*), non-empty schema strings/arrays, and cli client platform (INV-W3C-DTCG-COMPLIANCE, INV-POL-002-INVARIANT-TEST-ENFORCEMENT, INV-SPEC-VALIDATION, INV-CLIENT-DESIGN-BINDING)."""
    from eidos.core.spec.parser import parse_spec_file

    repo = tmp_path / "repo"
    eidos_dir = repo / "eidos"
    ds_dir = eidos_dir / "design" / "systems" / "brand"
    uic_dir = ds_dir / "components"
    clt_dir = eidos_dir / "design" / "clients" / "cli-app"
    pol_dir = eidos_dir / "policies"
    dlt_dir = eidos_dir / "deltas"
    tests_dir = repo / "tests"
    for d in (ds_dir, uic_dir, clt_dir, pol_dir, dlt_dir, tests_dir):
        d.mkdir(parents=True, exist_ok=True)

    outside_tokens = tmp_path / "outside_tokens.json"
    outside_tokens.write_text('{"color": {"$value": "#000000", "$type": "color"}}\n', encoding="utf-8")
    (ds_dir / "tokens.json").write_text('{"color": {"$value": "#112233", "$type": "color"}}\n', encoding="utf-8")

    # 1. Symlinked tokens.manifest escaping repo_root must fail containment check
    escape_link = ds_dir / "escape_tokens.json"
    escape_link.symlink_to(outside_tokens)
    ds_spec = ds_dir / "system.adoc"
    ds_spec.write_text(
        "---\n"
        "type: design/system\n"
        "id: DS-BRAND\n"
        "title: Brand Design System\n"
        "status: active\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: escape_tokens.json\n"
        "---\n= Brand\n",
        encoding="utf-8",
    )
    rep_escape = validator.validate_directory(eidos_dir)
    assert any("outside repository root" in r for r in rep_escape.broken_references)

    # Schema also rejects ../ traversal and absolute paths in tokens.manifest
    ds_traversal = ds_dir / "traversal.adoc"
    ds_traversal.write_text(
        "---\n"
        "type: design/system\n"
        "id: DS-TRAVERSAL\n"
        "title: Traversal DS\n"
        "status: draft\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: ../../outside_tokens.json\n"
        "---\n= Traversal\n",
        encoding="utf-8",
    )
    trav_errs = validator.validate_document(parse_spec_file(ds_traversal))
    assert any("manifest" in e for e in trav_errs)
    ds_traversal.unlink()

    # Fix DS-BRAND to valid repo-contained tokens.json and add CLI client (platform: cli)
    ds_spec.write_text(
        ds_spec.read_text(encoding="utf-8").replace("escape_tokens.json", "tokens.json"),
        encoding="utf-8",
    )
    (clt_dir / "client.adoc").write_text(
        "---\n"
        "type: design/client\n"
        "id: CLT-CLI-APP\n"
        "title: CLI Client Surface\n"
        "platform: cli\n"
        "design_system: DS-BRAND\n"
        "status: active\n"
        "---\n= CLI Client\n",
        encoding="utf-8",
    )

    # 2. Active UIC-* and POL-* invariants reject N/A, TBD, NONE, and bare file paths lacking ::
    uic_spec = uic_dir / "button.adoc"
    uic_spec.write_text(
        "---\n"
        "type: design/ui-component\n"
        "id: UIC-BRAND-BTN\n"
        "title: Brand Button\n"
        "design_system: DS-BRAND\n"
        "status: active\n"
        "invariants:\n"
        "  - id: INV-" + "UIC-BTN-1\n"
        "    rule: Button must expose accessible label.\n"
        "    test_binding: TBD\n"
        "---\n= Button\n",
        encoding="utf-8",
    )
    pol_spec = pol_dir / "pol-strict.adoc"
    pol_spec.write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-STRICT-001\n"
        "title: Strict Policy\n"
        "status: active\n"
        "category: quality\n"
        "invariants:\n"
        "  - id: INV-" + "POL-NA\n"
        "    rule: N/A binding rejected.\n"
        "    test_binding: N/A\n"
        "  - id: INV-" + "POL-BARE\n"
        "    rule: Bare path rejected.\n"
        "    test_binding: tests/test_ui.py\n"
        "---\n= Policy\n",
        encoding="utf-8",
    )
    (tests_dir / "test_ui.py").write_text(
        "def test_btn():\n    # INV-" + "UIC-BTN-1\n    # INV-" + "POL-NA\n    # INV-" + "POL-BARE\n    assert True\n",
        encoding="utf-8",
    )
    rep_bindings = validator.validate_directory(eidos_dir)
    assert any(
        ("INV-" + "UIC-BTN-1") in r and "invalid test_binding 'TBD'" in r for r in rep_bindings.broken_references
    )
    assert any(("INV-" + "POL-NA") in r and "invalid test_binding 'N/A'" in r for r in rep_bindings.broken_references)
    assert any(("INV-" + "POL-BARE") in r and "missing '::symbol'" in r for r in rep_bindings.broken_references)

    # Draft spec with TBD passes, and active specs with file::symbol pass
    uic_spec.write_text(
        uic_spec.read_text(encoding="utf-8").replace("status: active", "status: draft"), encoding="utf-8"
    )
    pol_spec.write_text(
        pol_spec.read_text(encoding="utf-8")
        .replace("test_binding: N/A\n", "test_binding: tests/test_ui.py::test_btn\n")
        .replace("test_binding: tests/test_ui.py\n", "test_binding: tests/test_ui.py::test_btn\n"),
        encoding="utf-8",
    )
    rep_clean = validator.validate_directory(eidos_dir)
    assert rep_clean.is_clean, f"Unexpected errors: {rep_clean.schema_errors} / {rep_clean.broken_references}"

    # 3. Non-empty strings, minItems: 1 code_paths, delta expect test_binding N/A removal, and tombstone pattern
    bad_blank = pol_dir / "blank.adoc"
    bad_blank.write_text(
        "---\ntype: product/policy\nid: POL-BLANK\ntitle: '   '\nstatus: draft\ncategory: quality\n---\n= Blank\n",
        encoding="utf-8",
    )
    rep_blank = validator.validate_directory(eidos_dir)
    assert "eidos/policies/blank.adoc" in rep_blank.schema_errors
    assert rep_blank.schema_errors.get(str(bad_blank))
    bad_blank.unlink()

    bad_cnt = eidos_dir / "architecture" / "containers" / "empty-paths.adoc"
    bad_cnt.parent.mkdir(parents=True, exist_ok=True)
    bad_cnt.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-EMPTY-PATHS\n"
        "title: Empty Paths\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: []\n"
        "---\n= Empty\n",
        encoding="utf-8",
    )
    assert validator.validate_document(parse_spec_file(bad_cnt))
    bad_cnt.unlink()

    dlt_na = dlt_dir / "dlt-na.adoc"
    dlt_na.write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-001\n"
        "title: Reject NA Expect\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: bug\n"
        "  motivation: Forbid N/A in expect.frontmatter.test_binding.\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: INV-" + "POL-NA\n"
        "    expect:\n"
        "      frontmatter:\n"
        "        test_binding: N/A\n"
        "---\n= Delta\n",
        encoding="utf-8",
    )
    assert any("N/A" in e or "test_binding" in e for e in validator.validate_document(parse_spec_file(dlt_na)))
    dlt_na.unlink()

    # Verify $defs.non_empty_string is reused everywhere (zero inline minLength / \S redefinitions or bare string types)
    from eidos.core.spec.parser import safe_load_yaml
    from eidos.core.workspace.assets import get_v1_schemas_dir

    inline_redefs: list[tuple[str, str]] = []
    bare_strings: list[tuple[str, str]] = []

    def _audit_schema_strings(node: object, fname: str, path: str = "") -> None:
        if isinstance(node, dict):
            if node.get("type") == "string" and path != "$defs.non_empty_string":
                if "minLength" in node or node.get("pattern") == "\\S":
                    inline_redefs.append((fname, path))
                if not any(k in node for k in ("const", "enum", "pattern")):
                    bare_strings.append((fname, path))
            for k, v in node.items():
                _audit_schema_strings(v, fname, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _audit_schema_strings(item, fname, f"{path}[{idx}]")

    for yaml_path in sorted(get_v1_schemas_dir().rglob("*.yaml")):
        _audit_schema_strings(safe_load_yaml(yaml_path.read_text(encoding="utf-8")), yaml_path.name)

    assert inline_redefs == [], f"Inline non_empty_string redefinitions found: {inline_redefs}"
    assert bare_strings == [], f"Bare unconstrained string fields found: {bare_strings}"

    # Verify $ref: "#/$defs/non_empty_string" + sibling x-eidos-ref both enforce on flow.schema.yaml steps[].screen
    flow_dir = eidos_dir / "design" / "flows"
    flow_dir.mkdir(parents=True, exist_ok=True)
    flow_ws = flow_dir / "flow-blank-screen.adoc"
    flow_ws.write_text(
        "---\ntype: design/flow\nid: FLOW-CHECK\ntitle: Flow Check\nstatus: draft\nsteps:\n  - step: 1\n    title: Open\n    screen: '   '\n---\n= Flow\n",
        encoding="utf-8",
    )
    assert validator.validate_document(parse_spec_file(flow_ws))
    flow_ws.write_text(
        "---\ntype: design/flow\nid: FLOW-CHECK\ntitle: Flow Check\nstatus: draft\nsteps:\n  - step: 1\n    title: Open\n    screen: SCR-MISSING\n---\n= Flow\n",
        encoding="utf-8",
    )
    rep_flow = validator.validate_directory(eidos_dir)
    assert any("SCR-MISSING" in b for b in rep_flow.broken_references)
    flow_ws.unlink()


def test_dlt_135_contract_parity_public_surface_parser_models_and_brace_globs(tmp_path, validator):
    """Verify DLT-2026-135: trailing '---' YAML, graceful UnicodeDecodeError, parent-first path inference, {a,b} brace globs, backtick gRPC & protobuf message/enum parity, OpenAPI route boundaries, '::'/generics/per-class Public Surface, and imported Typer/Click CLI parity (INV-SPEC-VALIDATION, INV-SPEC-GRAPH-INTEGRITY)."""
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.spec.models import infer_entity_type_from_path
    from eidos.core.spec.parser import YamlError, parse_spec_file, safe_load_yaml
    from eidos.core.workspace.project import expand_brace_globs, match_path_pattern

    # 1. Parser: trailing --- / ... allowed; genuine multi-doc rejected; invalid UTF-8 captured in parse_errors
    assert safe_load_yaml("id: POL-001\ntitle: Valid\n---\n") == {"id": "POL-001", "title": "Valid"}
    assert safe_load_yaml("id: POL-001\n...\n") == {"id": "POL-001"}
    with pytest.raises(YamlError, match="multiple documents"):
        safe_load_yaml("a: 1\n---\nb: 2\n")

    bad_utf8 = tmp_path / "corrupt.adoc"
    bad_utf8.write_bytes(b"\xff\xfe---\nid: POL-BAD\n---\n")
    corrupt_doc = parse_spec_file(bad_utf8)
    assert not corrupt_doc.is_valid
    assert any("Invalid UTF-8 encoding" in err for err in corrupt_doc.parse_errors)

    # 2. Models: canonical parent directories take precedence over stem prefixes (e.g. flow-engine.adoc)
    assert infer_entity_type_from_path(Path("eidos/architecture/components/flow-engine.adoc")) == (
        "CMP-",
        "tech/component",
        "CMP-FLOW-ENGINE",
    )
    assert infer_entity_type_from_path(Path("eidos/architecture/containers/scr-worker.adoc")) == (
        "CNT-",
        "tech/container",
        "CNT-SCR-WORKER",
    )
    assert infer_entity_type_from_path(Path("eidos/architecture/contracts/cap-gateway.adoc")) == (
        "CTR-",
        "tech/contract",
        "CTR-CAP-GATEWAY",
    )
    assert infer_entity_type_from_path(Path("eidos/design/systems/core/components/dom-card.adoc")) == (
        "UIC-",
        "design/ui-component",
        "UIC-DOM-CARD",
    )

    # 3. Brace globs in project.py and validator._resolve_source_path
    assert expand_brace_globs("src/{core,cli}/*.{py,ts}") == [
        "src/core/*.py",
        "src/core/*.ts",
        "src/cli/*.py",
        "src/cli/*.ts",
    ]
    assert expand_brace_globs("src/{unclosed/*.py") == ["src/{unclosed/*.py"]
    assert match_path_pattern("src/cli/run.py", "src/{core,cli}/*.py")
    assert not match_path_pattern("src/other/run.py", "src/{core,cli}/*.py")

    # 4. Build workspace testing gRPC, Protobuf, OpenAPI, Public Surface, and CLI sub-apps
    ws = tmp_path / "ws"
    eidos_dir = ws / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cmp_dir = eidos_dir / "architecture" / "components"
    ctr_dir = eidos_dir / "architecture" / "contracts"
    for d in (
        cnt_dir,
        cmp_dir,
        ctr_dir,
        ws / "src" / "alpha",
        ws / "src" / "beta",
        ws / "proto",
        ws / "api",
        ws / "cli",
    ):
        d.mkdir(parents=True, exist_ok=True)

    (ws / "src" / "alpha" / "mod.py").write_text(
        "class BaseSvc:\n"
        "    def inherited_op(self):\n"
        "        return 1\n\n"
        "class ClassA(BaseSvc):\n"
        "    def method_a(self):\n"
        "        return 2\n\n"
        "class ClassB:\n"
        "    def method_b(self):\n"
        "        return 3\n\n"
        "def first(items):\n"
        "    return items[0]\n",
        encoding="utf-8",
    )
    (ws / "src" / "beta" / "lib.rs").write_text(
        "pub struct RenderGraph {}\n"
        "impl RenderGraph {\n"
        "    pub fn execute(&self) {}\n"
        "}\n"
        "pub async fn parse<T>(input: T) -> T { input }\n",
        encoding="utf-8",
    )
    (ws / "src" / "beta" / "server.go").write_text(
        "package beta\ntype Server struct{}\nfunc (s *Server) Start() {}\n",
        encoding="utf-8",
    )

    (cnt_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core\n"
        "status: active\n"
        "container_type: service\n"
        "runtime: Polyglot\n"
        "source_path: 'src/{alpha,beta}/*'\n"
        "---\n= Core\n",
        encoding="utf-8",
    )

    # Public Surface: valid ::, generics, Go receiver, pub async fn, and inherited method pass; ClassA.method_b and RenderGraph::Missing fail
    cmp_surface = cmp_dir / "surface.adoc"
    cmp_surface.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-SURFACE\n"
        "title: Surface Component\n"
        "container: CNT-CORE\n"
        "status: active\n"
        "source_path: 'src/{alpha,beta}/*'\n"
        "---\n= Surface\n\n"
        "== Public Surface\n"
        "* `crate::beta::parse<T>(input: T)`\n"
        "* `RenderGraph::execute()`\n"
        "* `RenderGraph::DoesNotExist()`\n"
        "* `func (s *Server) Start()`\n"
        "* `def first[T](items: list[T])`\n"
        "* `ClassA.inherited_op()`\n"
        "* `ClassA.method_b()`\n",
        encoding="utf-8",
    )

    # Protobuf pure message/enum contract + gRPC contract with prose 'service layer' and 'rpc retries'
    (ws / "proto" / "events.proto").write_text(
        'syntax = "proto3";\nmessage UserEvent { string id = 1; }\nenum EventKind { UNKNOWN = 0; }\n',
        encoding="utf-8",
    )
    (ws / "proto" / "svc.proto").write_text(
        'syntax = "proto3";\nservice OrderService {\n  rpc CreateOrder (OrderReq) returns (OrderResp);\n}\n',
        encoding="utf-8",
    )
    ctr_proto = ctr_dir / "events.adoc"
    ctr_proto.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-EVENTS-PROTO\n"
        "title: Events Protobuf\n"
        "status: active\n"
        "protocol: protobuf\n"
        "container: CNT-CORE\n"
        "specification_file: proto/events.proto\n"
        "---\n= Events\n\n"
        "Defines `message UserEvent`, `enum EventKind`, and `message GhostEvent`.\n",
        encoding="utf-8",
    )
    ctr_grpc = ctr_dir / "grpc.adoc"
    ctr_grpc.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-ORDER-GRPC\n"
        "title: Order gRPC\n"
        "status: active\n"
        "protocol: grpc\n"
        "container: CNT-CORE\n"
        "specification_file: proto/svc.proto\n"
        "---\n= Order gRPC\n\n"
        "The service layer handles rpc retries for `service OrderService` and `rpc CreateOrder`.\n",
        encoding="utf-8",
    )

    # OpenAPI route boundary: /v2/users must NOT satisfy /users, and `/etc/eidos/config.yaml` must NOT false-fail
    (ws / "api" / "openapi.yaml").write_text(
        "openapi: 3.1.0\npaths:\n  /users:\n    get:\n      summary: Users\n  /v2/users:\n    get:\n      summary: V2 Users\n",
        encoding="utf-8",
    )
    ctr_rest = ctr_dir / "rest.adoc"
    ctr_rest.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-REST\n"
        "title: REST API\n"
        "status: active\n"
        "protocol: openapi\n"
        "container: CNT-CORE\n"
        "specification_file: api/openapi.yaml\n"
        "---\n= REST\n\n"
        "Reads `/etc/eidos/config.yaml` and exposes `GET /v2/users` and `POST /v1/ghost`.\n",
        encoding="utf-8",
    )

    # CLI contract: Click with helper import from typer + Typer with imported sibling sub_app
    (ws / "cli" / "click_cli.py").write_text(
        "import click\nfrom typer import BadParameter\n\n"
        "@click.group()\ndef cli(): pass\n\n"
        "@cli.command('serve')\ndef serve_cmd(): pass\n",
        encoding="utf-8",
    )
    (ws / "cli" / "sub_commands.py").write_text(
        "import typer\nsub_app = typer.Typer(name='sub')\n\n@sub_app.command('ping')\ndef ping_cmd(): pass\n",
        encoding="utf-8",
    )
    (ws / "cli" / "extra_sub.py").write_text(
        "import typer\nextra_app = typer.Typer(name='extra')\n\n@extra_app.command('pong')\ndef pong_cmd(): pass\n",
        encoding="utf-8",
    )
    (ws / "cli" / "sibling_cmds.py").write_text(
        "def register(app):\n    @app.command('health')\n    def health_cmd(): pass\n",
        encoding="utf-8",
    )
    (ws / "cli" / "typer_main.py").write_text(
        "import typer\nimport sub_commands\nimport sibling_cmds\nfrom extra_sub import extra_app\n"
        "app = typer.Typer()\napp.add_typer(sub_commands.sub_app, name='sub')\n"
        "app.add_typer(extra_app, name='extra')\nsibling_cmds.register(app)\n",
        encoding="utf-8",
    )
    ctr_click = ctr_dir / "click.adoc"
    ctr_click.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-CLICK\n"
        "title: Click CLI\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CORE\n"
        "specification_file: cli/click_cli.py\n"
        "---\n= Click CLI\nDocuments `cli` entrypoint.\n",
        encoding="utf-8",
    )
    ctr_typer = ctr_dir / "typer.adoc"
    ctr_typer.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-TYPER\n"
        "title: Typer CLI\n"
        "status: active\n"
        "protocol: cli_args\n"
        "container: CNT-CORE\n"
        "specification_file: cli/typer_main.py\n"
        "---\n= Typer CLI\n\n"
        "=== `mycli health`\nHealth check.\n\n"
        "=== `mycli extra`\n* **Sub-commands**:\n  - `mycli extra pong`: Pong.\n\n"
        "=== `mycli sub`\n* **Sub-commands**:\n  - `mycli sub bogus`: Bogus.\n",
        encoding="utf-8",
    )

    rep = validator.validate_directory(eidos_dir)
    refs_joined = "\n".join(rep.broken_references)
    # Surface checks
    assert "DoesNotExist" in refs_joined
    assert "method_b" in refs_joined
    assert "inherited_op" not in refs_joined
    assert "execute" not in refs_joined
    assert "parse" not in refs_joined
    assert "Start" not in refs_joined
    assert "first" not in refs_joined
    # Protobuf & gRPC checks
    assert "GhostEvent" in refs_joined
    assert "layer" not in refs_joined
    assert "retries" not in refs_joined
    # OpenAPI checks
    assert "OpenAPI path '/users' defined in 'openapi.yaml' is not documented" in refs_joined
    assert "/v1/ghost" in refs_joined
    assert "/etc/eidos/config.yaml" not in refs_joined
    # CLI checks
    assert "CLI command 'serve' defined in 'click_cli.py'" in refs_joined
    assert "CLI subcommand 'sub ping' is not documented" in refs_joined
    assert "documented CLI subcommand 'sub bogus'" in refs_joined

    # Fix all intentional drift items and confirm clean validation
    cmp_surface.write_text(
        cmp_surface.read_text(encoding="utf-8")
        .replace("* `RenderGraph::DoesNotExist()`\n", "")
        .replace("* `ClassA.method_b()`\n", "* `ClassA.method_a()`\n"),
        encoding="utf-8",
    )
    ctr_proto.write_text(
        ctr_proto.read_text(encoding="utf-8").replace(", and `message GhostEvent`", ""),
        encoding="utf-8",
    )
    ctr_rest.write_text(
        ctr_rest.read_text(encoding="utf-8").replace(
            "exposes `GET /v2/users` and `POST /v1/ghost`.",
            "exposes `GET /v2/users`.\n\n|===\n| Method | Path\n| GET | `/users`\n|===\n",
        ),
        encoding="utf-8",
    )
    ctr_click.write_text(
        ctr_click.read_text(encoding="utf-8") + "\n=== `cli serve`\nServes requests.\n",
        encoding="utf-8",
    )
    ctr_typer.write_text(
        ctr_typer.read_text(encoding="utf-8").replace("mycli sub bogus", "mycli sub ping"),
        encoding="utf-8",
    )

    rep_fixed = validator.validate_directory(eidos_dir)
    assert rep_fixed.is_clean, f"Expected clean workspace, got: {rep_fixed.broken_references}"
    # Exercise SQLite validation cache hit (mtime match + sha256 match after mtime bump)
    rep_cached = validator.validate_directory(eidos_dir)
    assert rep_cached.is_clean
    import os as _os

    st_now = cmp_surface.stat()
    _os.utime(cmp_surface, ns=(st_now.st_atime_ns, st_now.st_mtime_ns + 1_000_000))
    assert validator.validate_directory(eidos_dir).is_clean

    # Verify `eidos spec show DLT-*` directs callers to `eidos delta show`
    dlt_sample = eidos_dir / "deltas" / "dlt-2026-001-sample.adoc"
    dlt_sample.parent.mkdir(parents=True, exist_ok=True)
    dlt_sample.write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-001\ntitle: Sample\nstatus: proposed\n"
        "intent:\n  kind: bug\n  motivation: Test redirect.\nmutations:\n  - operation: modify\n    target: CNT-CORE\n---\n= Sample\n",
        encoding="utf-8",
    )
    cli_runner = CliRunner()
    res_spec_dlt = cli_runner.invoke(app, ["spec", "show", "DLT-2026-001", "--path", str(eidos_dir)])
    assert res_spec_dlt.exit_code == 1
    assert "eidos delta show DLT-2026-001" in " ".join(res_spec_dlt.output.split())
    res_spec_path = cli_runner.invoke(app, ["spec", "show", str(dlt_sample), "--path", str(eidos_dir)])
    assert res_spec_path.exit_code == 1
    assert "eidos delta show DLT-2026-001" in " ".join(res_spec_path.output.split())


def test_group_e_parser_models_project_and_spec_cli_branches(tmp_path, validator):
    """Exercise edge-case branches across parser.py, models.py, project.py, validator.py, and spec_commands.py."""
    import io
    import subprocess

    import orjson
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.spec.models import (
        CapabilityTechnicalModel,
        DecisionModel,
        DeltaIntentModel,
        DeltaMutationModel,
        FrontmatterModel,
        InvariantModel,
        RejectedAlternativeModel,
        _SchemaErrorsMap,
        infer_entity_type_from_path,
    )
    from eidos.core.spec.parser import (
        YamlError,
        _parse_spec_content,
        parse_spec_file,
        safe_dump_yaml,
        safe_load_yaml,
        split_frontmatter_and_body,
    )
    from eidos.core.workspace.project import (
        _glob_to_anchored_regex,
        _is_inside_plugin_or_assets,
        discover_nested_subproject_roots,
        find_repo_root,
        get_tracked_files,
        is_generated_or_vendored,
        is_polyglot_test_file,
        load_project,
        match_path_pattern,
        matches_owned_paths,
        resolve_spec_and_repo_roots,
    )

    # 1. Parser edge cases
    assert safe_load_yaml(None) is None
    assert safe_load_yaml("   \n  ") is None
    assert safe_load_yaml("---\n...\n") is None
    assert safe_load_yaml(b"key: 42\n") == {"key": 42}
    with pytest.raises(YamlError):
        safe_load_yaml(12345)
    buf = io.StringIO()
    dumped = safe_dump_yaml({"alpha": "beta"}, stream=buf)
    assert "alpha: beta" in dumped and buf.getvalue() == dumped
    assert split_frontmatter_and_body("") == (None, "")
    raw_fm, body_txt = split_frontmatter_and_body("---\nnote: 'a\n---\nb'\n---\n= Title\n")
    assert raw_fm is not None and "note:" in raw_fm and "= Title" in body_txt
    missing_doc = parse_spec_file(tmp_path / "does_not_exist.adoc")
    assert not missing_doc.is_valid and any("Failed to read" in e for e in missing_doc.parse_errors)
    empty_sys = _parse_spec_content(tmp_path / "eidos" / "system.adoc", "")
    assert empty_sys.doc_id == "SYS-UNINITIALIZED"
    no_fm_doc = _parse_spec_content(tmp_path / "plain.adoc", "---\n---\n= Plain\n")
    assert not no_fm_doc.is_valid

    # 2. Models & path inference edge cases
    assert infer_entity_type_from_path("") is None
    assert infer_entity_type_from_path("eidos/design/systems/brand/system.adoc") == ("DS-", "design/system", "DS-BRAND")
    assert infer_entity_type_from_path("eidos/screens/login.adoc") == ("SCR-", "design/screen", "SCR-LOGIN")
    assert infer_entity_type_from_path("eidos/flows/signup.adoc") == ("FLOW-", "design/flow", "FLOW-SIGNUP")
    assert infer_entity_type_from_path("eidos/design/clients/mobile/client.adoc") == (
        "CLT-",
        "design/client",
        "CLT-MOBILE",
    )
    assert infer_entity_type_from_path("eidos/architecture/system-architecture.adoc") == (
        "ARCH-",
        "tech/architecture",
        "ARCH-SYSTEM-ARCHITECTURE",
    )
    assert infer_entity_type_from_path("eidos/capabilities/billing.adoc") == (
        "CAP-",
        "product/capability",
        "CAP-BILLING",
    )
    assert infer_entity_type_from_path("eidos/policies/pol-sec.adoc") == ("POL-", "product/policy", "POL-SEC")
    assert infer_entity_type_from_path("eidos/personas/admin.adoc") == ("PER-", "product/persona", "PER-ADMIN")
    assert infer_entity_type_from_path("eidos/explorations/exp-01.adoc") == ("EXP-", "product/exploration", "EXP-01")
    assert infer_entity_type_from_path("eidos/deltas/dlt-01.adoc") == ("DLT-", "delta/mutation", "DLT-01")
    assert infer_entity_type_from_path("eidos/domains/payments/domain.adoc") == (
        "DOM-",
        "product/domain",
        "DOM-PAYMENTS",
    )
    assert infer_entity_type_from_path("eidos/system.adoc") == ("SYS-", "product/system", "SYS-SYSTEM")
    assert infer_entity_type_from_path("cap-solo.adoc") == ("CAP-", "product/capability", "CAP-SOLO")
    assert infer_entity_type_from_path("dlt-solo.adoc") == ("DLT-", "delta/mutation", "DLT-SOLO")
    assert infer_entity_type_from_path("unrecognized.adoc") is None

    assert RejectedAlternativeModel.model_validate("Manual SQL").option == "Manual SQL"
    assert RejectedAlternativeModel.model_validate(99).option == ""
    assert DecisionModel.model_validate(99).id == ""
    assert InvariantModel.model_validate("INV-" + "RAW").id == "INV-" + "RAW"
    assert InvariantModel.model_validate(99).id == ""
    assert CapabilityTechnicalModel.model_validate(99).containers == []
    assert DeltaIntentModel.model_validate(99).kind is None
    assert DeltaMutationModel.model_validate(99).operation == ""
    assert FrontmatterModel.model_validate(99).source_paths() == []
    assert FrontmatterModel.model_validate({"source_path": None}).source_paths() == []
    err_map = _SchemaErrorsMap({"eidos/policies/a.adoc": ["bad"]})
    assert err_map.get("/workspace/repo/eidos/policies/a.adoc") == ["bad"]
    assert err_map.get("/workspace/repo/other.adoc") is None

    # 3. Workspace project.py branches: nested subprojects, gitattributes, globs, polyglot tests, root resolution
    g_repo = tmp_path / "git_ws"
    (g_repo / "eidos").mkdir(parents=True)
    (g_repo / "eidos" / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-GIT-WS\ntitle: Git WS\nstatus: active\narchetypes:\n  - cli_tool\n---\n= Git WS\n",
        encoding="utf-8",
    )
    (g_repo / "src").mkdir(parents=True)
    (g_repo / "src" / "main.py").write_text(
        "class Widget:\n"
        "    count: int = 0\n"
        "    plain = 1\n"
        "    def __init__(self):\n"
        "        self.ready = True\n"
        "__all__ = ['Widget']\n"
        "type WidgetId = str\n",
        encoding="utf-8",
    )
    (g_repo / "src" / "gen.py").write_text("# generated\nx = 1\n", encoding="utf-8")
    (g_repo / ".gitattributes").write_text("src/gen.py linguist-generated=true\n", encoding="utf-8")
    (g_repo / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    (g_repo / "ignored_dir" / "eidos").mkdir(parents=True)
    (g_repo / "ignored_dir" / "eidos" / "system.adoc").write_text("---\nid: SYS-IGN\n---\n", encoding="utf-8")
    sub_proj = g_repo / "packages" / "sub"
    (sub_proj / "eidos").mkdir(parents=True)
    (sub_proj / "eidos" / "system.adoc").write_text("---\nid: SYS-SUB\n---\n", encoding="utf-8")
    (sub_proj / "sub_code.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=g_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=g_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=g_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "add", ".gitattributes", ".gitignore", "eidos", "src"], cwd=g_repo, check=True, capture_output=True
    )
    subprocess.run(["git", "commit", "-m", "init"], cwd=g_repo, check=True, capture_output=True)
    (g_repo / "src" / "untracked.py").write_text("z = 3\n", encoding="utf-8")

    nested = discover_nested_subproject_roots(g_repo)
    assert nested == [sub_proj.resolve()]
    assert discover_nested_subproject_roots(g_repo) == [sub_proj.resolve()]
    assert discover_nested_subproject_roots(tmp_path / "no_such_dir") == []
    assert "src/gen.py" in is_generated_or_vendored(g_repo, ["src/gen.py", "src/main.py"])
    tracked = get_tracked_files(g_repo, include_untracked=True)
    assert "src/main.py" in tracked and "src/untracked.py" in tracked
    assert "src/gen.py" not in tracked
    assert not any(f.startswith("packages/sub/") for f in tracked)
    assert get_tracked_files(tmp_path / "missing_repo") == []

    # Non-git fallback walk in get_tracked_files
    nongit = tmp_path / "nongit_ws"
    (nongit / "src").mkdir(parents=True)
    (nongit / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (nongit / "nested" / "eidos").mkdir(parents=True)
    (nongit / "nested" / "eidos" / "system.adoc").write_text("---\nid: SYS-N\n---\n", encoding="utf-8")
    (nongit / "nested" / "skip.py").write_text("b = 2\n", encoding="utf-8")
    assert get_tracked_files(nongit) == ["src/a.py"]

    assert _is_inside_plugin_or_assets(g_repo / ".agents" / "skills" / "eidos", repo_root=g_repo)
    assert _is_inside_plugin_or_assets(g_repo / ".claude" / "plugins" / "x", repo_root=g_repo)
    assert _is_inside_plugin_or_assets(g_repo / "assets" / "schemas" / "v1", repo_root=g_repo)
    assert _is_inside_plugin_or_assets(Path("/nonexistent_root/.agents/skills/eidos"))
    assert _is_inside_plugin_or_assets(Path("/nonexistent_root/.claude/plugins/p"))
    assert _is_inside_plugin_or_assets(Path("/nonexistent_root/assets/schemas/s"))
    assert not _is_inside_plugin_or_assets(tmp_path / "outside", repo_root=g_repo)
    assert resolve_spec_and_repo_roots(None, None)[1].exists()
    assert find_repo_root(g_repo / "src" / "main.py") == g_repo.resolve()
    sr, rr = resolve_spec_and_repo_roots(g_repo / "src")
    assert sr == (g_repo / "eidos").resolve() and rr == g_repo.resolve()
    cfg1 = load_project(repo_root=g_repo)
    cfg2 = load_project(eidos_dir=g_repo / "eidos")
    assert cfg1.system_id == "SYS-GIT-WS" and cfg2.system_title == "Git WS"

    assert is_polyglot_test_file(
        "packages/web/src/__tests__/btn.ts", repo_root=g_repo, package_paths=["packages/web/src"]
    )
    assert is_polyglot_test_file("ui/card.test.tsx")
    assert is_polyglot_test_file("pkg/svc_test.go")
    assert is_polyglot_test_file("crates/core_test.rs")
    assert is_polyglot_test_file("src/test/OrderTest.java")
    assert is_polyglot_test_file(Path("/tmp/tests/test_ext.py"))

    rx = _glob_to_anchored_regex("**/src/**/mod_?/[!0-9]*[]a][abc/**")
    assert rx.startswith("^") and rx.endswith("$")
    assert _glob_to_anchored_regex("**").startswith("^")
    assert match_path_pattern("", "src/*.py") == 0
    assert match_path_pattern("src/a/b.py", "src/**") > 500
    assert match_path_pattern("other/a.py", "src/**") == 0
    assert match_path_pattern("src/mod_1/x.py", "src/**/mod_?/[!0-9]*.py") > 500
    matched_ok, best_sc = matches_owned_paths("src/main.py", ["src/**", "src/main.py"])
    assert matched_ok and best_sc > 2000

    # Public surface class attributes / __all__ / TypeAlias + JSON Schema contract in validator
    (g_repo / "eidos" / "architecture" / "containers").mkdir(parents=True)
    (g_repo / "eidos" / "architecture" / "components").mkdir(parents=True)
    (g_repo / "eidos" / "architecture" / "contracts").mkdir(parents=True)
    (g_repo / "schema.json").write_text(
        '{"type": "object", "required": ["order_id"], "properties": {"order_id": {"type": "string"}}}\n',
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "containers" / "app.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-APP\ntitle: App\nstatus: active\ncontainer_type: service\nruntime: Python\nsource_path: src/main.py\n---\n= App\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "components" / "widget.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-WIDGET\ntitle: Widget\ncontainer: CNT-APP\nstatus: active\nsource_path: src/main.py\n---\n"
        "= Widget\n\n== Public Surface\n* `Widget.count`\n* `Widget.plain`\n* `Widget.ready`\n* `WidgetId`\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "schema.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-SCHEMA\ntitle: Schema\nstatus: active\nprotocol: json_schema\ncontainer: CNT-APP\nspecification_file: schema.json\n---\n= Schema\nRequires `order_id`.\n",
        encoding="utf-8",
    )

    # 4. Exercise spec_commands.py CLI (validate, spec list, spec show, spec create, spec delete, spec schema)
    runner = CliRunner()
    assert runner.invoke(app, ["validate", str(tmp_path / "missing_eidos")]).exit_code == 1
    assert runner.invoke(app, ["validate", str(tmp_path / "missing_eidos"), "--json"]).exit_code == 1
    res_val_ok = runner.invoke(app, ["validate", str(g_repo / "eidos")])
    assert res_val_ok.exit_code == 0, res_val_ok.output
    res_val_json = runner.invoke(app, ["validate", str(g_repo / "eidos"), "--json"])
    assert res_val_json.exit_code == 0 and orjson.loads(res_val_json.stdout)["is_clean"] is True

    # Introduce temporary broken spec to cover failure paths in `eidos validate` (Rich + --json)
    bad_spec = g_repo / "eidos" / "policies" / "bad.adoc"
    bad_spec.parent.mkdir(parents=True, exist_ok=True)
    bad_spec.write_text("---\ntype: product/policy\nid: bad\ntitle: Bad\n---\n= Bad\n", encoding="utf-8")
    assert runner.invoke(app, ["validate", str(g_repo / "eidos")]).exit_code == 1
    assert runner.invoke(app, ["validate", str(g_repo / "eidos"), "--json"]).exit_code == 1
    bad_spec.unlink()

    assert runner.invoke(app, ["spec", "list", "--path", str(g_repo / "eidos")]).exit_code == 0
    res_list_json = runner.invoke(app, ["spec", "list", "--path", str(g_repo / "eidos"), "--json"])
    assert res_list_json.exit_code == 0 and orjson.loads(res_list_json.stdout)["count"] >= 3
    assert runner.invoke(app, ["spec", "list", "--path", str(tmp_path / "empty_specs")]).exit_code == 0
    assert runner.invoke(app, ["spec", "list", "--type", "delta", "--path", str(g_repo / "eidos")]).exit_code == 1
    assert (
        runner.invoke(app, ["spec", "list", "--type", "delta", "--json", "--path", str(g_repo / "eidos")]).exit_code
        == 1
    )
    assert runner.invoke(app, ["spec", "show", "CNT-APP", "--path", str(g_repo / "eidos")]).exit_code == 0
    assert runner.invoke(app, ["spec", "show", "NO-SUCH-SPEC", "--path", str(g_repo / "eidos")]).exit_code == 1
    assert (
        runner.invoke(
            app, ["spec", "create", "policy", "POL-TEMP-01", "-t", "Temp Pol", "--path", str(g_repo / "eidos")]
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["spec", "delete", "POL-TEMP-01", "--path", str(g_repo / "eidos")]).exit_code == 0
    assert runner.invoke(app, ["spec", "schema"]).exit_code == 0
    assert runner.invoke(app, ["spec", "schema", "bogus-type"]).exit_code == 1

    # 5. Additional validator protocol & reference edge cases (GraphQL, Python Protocol, malformed OpenAPI/JSON Schema)
    (g_repo / "api.graphql").write_text("type Query {\n  user(id: ID!): String\n}\n", encoding="utf-8")
    (g_repo / "proto_iface.py").write_text(
        "from typing import Protocol\nclass StoragePort(Protocol):\n    def load(self, key: str) -> str: ...\n",
        encoding="utf-8",
    )
    (g_repo / "bad_openapi.yaml").write_text("paths: [unclosed\n", encoding="utf-8")
    (g_repo / "bad_schema.json").write_text("{unclosed\n", encoding="utf-8")
    (g_repo / "eidos" / "architecture" / "contracts" / "gql.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-GQL\ntitle: GraphQL\nstatus: active\nprotocol: graphql\ncontainer: CNT-APP\nspecification_file: api.graphql\n---\n= GQL\nExposes `query ghostOp`.\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "pyproto.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-PYPROTO\ntitle: PyProto\nstatus: active\nprotocol: python_protocol\ncontainer: CNT-APP\nspecification_file: proto_iface.py\n---\n= PyProto\nExposes `def missing_fn(self)`.\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "badoas.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-BADOAS\ntitle: Bad OAS\nstatus: active\nprotocol: openapi\ncontainer: CNT-APP\nspecification_file: bad_openapi.yaml\n---\n= Bad OAS\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "badjs.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-BADJS\ntitle: Bad JS\nstatus: active\nprotocol: json_schema\ncontainer: CNT-APP\nspecification_file: bad_schema.json\n---\n= Bad JS\n",
        encoding="utf-8",
    )
    rep_more = validator.validate_directory(g_repo / "eidos")
    more_refs = "\n".join(rep_more.broken_references)
    assert "GraphQL operation 'user'" in more_refs and "ghostOp" in more_refs
    assert "StoragePort" in more_refs and "missing_fn" in more_refs
    assert "failed to parse" in more_refs

    # Circular DS extends + argparse + Click nested group/add_command + Typer positional arg parity + nested generics + entity ref type mismatch
    ds_a = g_repo / "eidos" / "design" / "systems" / "dsa" / "system.adoc"
    ds_b = g_repo / "eidos" / "design" / "systems" / "dsb" / "system.adoc"
    ds_a.parent.mkdir(parents=True, exist_ok=True)
    ds_b.parent.mkdir(parents=True, exist_ok=True)
    ds_a.write_text(
        "---\ntype: design/system\nid: DS-A\ntitle: A\nstatus: active\nextends: DS-B\n---\n= A\n", encoding="utf-8"
    )
    ds_b.write_text(
        "---\ntype: design/system\nid: DS-B\ntitle: B\nstatus: active\nextends: DS-A\n"
        "tokens:\n  manifest: /abs/escape/tokens.json\n---\n= B\n",
        encoding="utf-8",
    )
    (g_repo / "arg_cli.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\ns = p.add_subparsers()\ns.add_parser('deploy')\n",
        encoding="utf-8",
    )
    (g_repo / "click_adv.py").write_text(
        "import click\n@click.group()\ndef root(): pass\n@root.group('db')\ndef db_grp(): pass\n"
        "@db_grp.command('migrate')\ndef mig(): pass\n"
        "@click.group()\ndef sub_g(): pass\n@sub_g.command('leaf')\ndef leaf_c(): pass\n"
        "@click.command()\ndef ping(): pass\n@click.command()\ndef solo(): pass\n"
        "root.add_command(sub_g, 'subg')\nsub_g.add_command(ping, 'subping')\n"
        "root.add_command(lambda: None, 'anon')\nsub_g.add_command(lambda: None, 'subanon')\n",
        encoding="utf-8",
    )
    (g_repo / "typer_sub_reg.py").write_text(
        "import typer\nchild_app = typer.Typer(name='child')\nauto_app = typer.Typer(name='autoname')\n"
        "@child_app.command('run')\ndef run_c(item: str = typer.Argument(...)): pass\n"
        "@auto_app.command('go')\ndef go_c(): pass\n"
        "def register(app):\n    sub_app.add_typer(child_app, name='child')\n",
        encoding="utf-8",
    )
    (g_repo / "typer_auto2.py").write_text(
        "import typer\nimp_auto = typer.Typer(name='impname')\n@imp_auto.command('hi')\ndef hi_c(): pass\n",
        encoding="utf-8",
    )
    (g_repo / "typer_pos.py").write_text(
        "import typer\nimport typer_sub_reg\nfrom typer_auto2 import imp_auto\n"
        "app = typer.Typer()\nsub_app = typer.Typer(name='sub')\napp.add_typer(sub_app, name='sub')\n"
        "app.add_typer(typer_sub_reg.auto_app)\napp.add_typer(imp_auto)\n"
        "typer_sub_reg.register(app)\n"
        "@app.command('build')\n"
        "def build_cmd(project_id: str = typer.Argument(...), tag: str = typer.Argument('latest')):\n    pass\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "components" / "widget.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-WIDGET\ntitle: Widget\ncontainer: CNT-APP\nstatus: active\nsource_path: src/main.py\n---\n"
        "= Widget\n\n== Public Surface\n* `Widget<Map<K, V>>.count[T]`\n* `Widget.plain`\n* `Widget.ready`\n* `WidgetId`\n",
        encoding="utf-8",
    )
    # Wrong entity type for .adoc path ref + policies dir recreated after spec delete
    (g_repo / "eidos" / "policies").mkdir(parents=True, exist_ok=True)
    (g_repo / "eidos" / "policies" / "wrong.adoc").write_text(
        "---\ntype: product/policy\nid: POL-WRONG-01\ntitle: Wrong\nstatus: draft\ncategory: quality\n---\n= Wrong\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "deltas").mkdir(parents=True, exist_ok=True)
    (g_repo / "eidos" / "deltas" / "dlt-2026-099-ref.adoc").write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-099\ntitle: Bad Grad\nstatus: proposed\n"
        "graduated_from: eidos/policies/wrong.adoc\n"
        "intent:\n  kind: bug\n  motivation: Test .adoc ref type check.\n"
        "mutations:\n  - operation: modify\n    target: CNT-APP\n---\n= Bad Grad\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "components" / "badref.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-BADREF\ntitle: BadRef\ncontainer: POL-WRONG-01\nstatus: draft\nsource_path: src/main.py\n---\n= BadRef\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "arg.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-ARG\ntitle: Arg\nstatus: active\nprotocol: cli_args\ncontainer: CNT-APP\nspecification_file: arg_cli.py\n---\n= Arg\n=== `myarg other`\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "clickadv.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-CLICKADV\ntitle: ClickAdv\nstatus: active\nprotocol: cli_args\ncontainer: CNT-APP\nspecification_file: click_adv.py\n---\n"
        "= ClickAdv\n=== `root db`\n* **Sub-commands**:\n  - `root db wrong`: Wrong.\n"
        "=== `root subg`\n* **Sub-commands**:\n  - `root subg leaf`: Leaf.\n",
        encoding="utf-8",
    )
    (g_repo / "eidos" / "architecture" / "contracts" / "typerpos.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-TYPERPOS\ntitle: TyperPos\nstatus: active\nprotocol: cli_args\ncontainer: CNT-APP\nspecification_file: typer_pos.py\n---\n"
        "= TyperPos\n=== `tool build --flag <VAL> --verbose <wrong_name> [extra_opt]`\n=== `tool build <only_one>`\n",
        encoding="utf-8",
    )
    rep_adv = validator.validate_directory(g_repo / "eidos")
    adv_refs = "\n".join(rep_adv.broken_references)
    assert "Circular design system inheritance" in adv_refs
    assert "outside repository root" in adv_refs
    assert "has type 'product/policy', expected product/exploration" in adv_refs
    assert "deploy" in adv_refs
    assert "solo" in adv_refs and "db migrate" in adv_refs
    assert "autoname" in adv_refs and "impname" in adv_refs
    assert "positional" in adv_refs


def _verify_pol_005_coverage_json(coverage_json_path: Path, src_dir: Path, repo_root: Path) -> None:
    """Enforce POL-005 strictly >90.0% statement coverage on every individual Python module under src/eidos/."""
    import orjson

    cov_data = orjson.loads(coverage_json_path.read_bytes())
    files = cov_data.get("files", {})
    offenders: list[tuple[str, float]] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        rel = py_file.relative_to(repo_root).as_posix()
        if rel == "src/eidos/_version.py" or rel.startswith("src/eidos/portal/frontend/"):
            continue
        entry = files.get(rel) or files.get(rel.replace("/", "\\"))
        pct = float(entry.get("summary", {}).get("percent_covered", 0.0)) if isinstance(entry, dict) else 0.0
        if pct <= 90.0:
            offenders.append((rel, pct))
    if offenders:
        rows = "\n".join(f"  | {rel} | {pct:.2f}% |" for rel, pct in offenders)
        raise AssertionError(
            "POL-005 per-file statement coverage violation (every module must be strictly > 90.0%):\n"
            "  | Module | Coverage |\n"
            f"{rows}"
        )


def test_pol_005_statement_test_coverage(tmp_path, monkeypatch, validator):
    """Verify POL-005-STATEMENT-TEST-COVERAGE and INV-POL-005-STATEMENT-TEST-COVERAGE: policy spec, pyproject.toml coverage settings, CI per-file coverage.json enforcement, and edge-case branches across core/cli/vcs/plugin modules."""
    import importlib
    import importlib.metadata
    import subprocess
    import sys
    import tomllib

    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.index.ast_indexer import get_ast_indexer
    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.index.sync import index_test_bindings
    from eidos.core.lifecycle.audit import AuditEngine
    from eidos.core.lifecycle.catalog import (
        _format_yaml_scalar_value,
        _set_frontmatter_scalar,
        _split_yaml_inline_comment,
        find_references_to_spec,
        find_spec_by_id_or_path,
    )
    from eidos.core.lifecycle.delta_engine import _resolve_git_baseline_sha, verify_delta_mutations
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import (
        _resolve_schema_node,
        _synthesize_example_value,
        create_spec,
        list_specs,
    )
    from eidos.core.spec.parser import (
        YamlError,
        _parse_spec_content,
        parse_spec_file,
        safe_dump_yaml,
        safe_load_yaml,
        split_frontmatter_and_body,
    )
    from eidos.core.spec.validator import ValidationReport
    from eidos.plugin.installer import (
        MANIFEST_FILENAME,
        _copy_skills_flat,
        _uninstall_from_manifest,
        locate_plugin_bundle,
    )
    from eidos.vcs.diff import (
        GitResolutionError,
        _resolve_base_and_merge_base,
        _scope_git_paths_to_subproject,
    )
    from eidos.vcs.drift import (
        ChangedFilesClassification,
        DriftCheckResult,
        detect_drift,
    )
    from eidos.vcs.invariants import InvariantTrace

    repo_root = Path(__file__).parent.parent

    # 1. Verify POL-005-STATEMENT-TEST-COVERAGE specification and pyproject.toml [tool.coverage]
    policy_path = repo_root / "eidos/policies/pol-005-statement-test-coverage.adoc"
    assert policy_path.is_file()
    pol_doc = parse_spec_file(policy_path)
    assert pol_doc.doc_id == "POL-005-STATEMENT-TEST-COVERAGE"
    assert pol_doc.doc_type == "product/policy"
    assert pol_doc.frontmatter.get("status") == "active"
    assert pol_doc.frontmatter.get("category") == "testing"
    assert pol_doc.frontmatter.get("severity") == "blocking"
    assert not validator.validate_document(pol_doc)

    with open(repo_root / "pyproject.toml", "rb") as f:
        pyproj = tomllib.load(f)
    cov_cfg = pyproj.get("tool", {}).get("coverage", {})
    assert cov_cfg.get("run", {}).get("source") == ["src/eidos"]
    assert "src/eidos/_version.py" in cov_cfg.get("run", {}).get("omit", [])
    assert cov_cfg.get("report", {}).get("fail_under", 0) >= 90

    ci_workflow = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--cov-report=json:coverage.json" in ci_workflow
    assert "pct <= 90.0" in ci_workflow

    import orjson

    src_eidos_dir = repo_root / "src/eidos"
    real_modules = [
        p.relative_to(repo_root).as_posix()
        for p in sorted(src_eidos_dir.rglob("*.py"))
        if p.relative_to(repo_root).as_posix() != "src/eidos/_version.py"
        and not p.relative_to(repo_root).as_posix().startswith("src/eidos/portal/frontend/")
    ]
    assert len(real_modules) >= 30

    cov_pass_json = tmp_path / "cov_pass.json"
    cov_pass_files = {rel: {"summary": {"percent_covered": 90.1}} for rel in real_modules}
    cov_pass_files["src/eidos/_version.py"] = {"summary": {"percent_covered": 0.0}}
    cov_pass_json.write_bytes(orjson.dumps({"files": cov_pass_files}))
    _verify_pol_005_coverage_json(cov_pass_json, src_eidos_dir, repo_root)

    # Strict > 90.0% check: exact 90.0% boundary or subsidized 85.0% module or missing entry must fail!
    cov_boundary_files = dict(cov_pass_files)
    cov_boundary_files[real_modules[0]] = {"summary": {"percent_covered": 90.0}}
    cov_boundary_json = tmp_path / "cov_boundary.json"
    cov_boundary_json.write_bytes(orjson.dumps({"files": cov_boundary_files}))
    with pytest.raises(AssertionError, match=r"strictly > 90\.0%"):
        _verify_pol_005_coverage_json(cov_boundary_json, src_eidos_dir, repo_root)

    cov_missing_files = {rel: {"summary": {"percent_covered": 99.5}} for rel in real_modules[1:]}
    cov_missing_json = tmp_path / "cov_missing.json"
    cov_missing_json.write_bytes(orjson.dumps({"files": cov_missing_files}))
    with pytest.raises(AssertionError, match=real_modules[0]):
        _verify_pol_005_coverage_json(cov_missing_json, src_eidos_dir, repo_root)

    live_cov_json = repo_root / "coverage.json"
    if live_cov_json.is_file():
        _verify_pol_005_coverage_json(live_cov_json, src_eidos_dir, repo_root)

    # 2. src/eidos/__init__.py version fallback branches
    import eidos

    assert eidos.resolve_runtime_version(use_cache=False)
    orig_ver_mod = sys.modules.get("eidos._version")
    try:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("git unavailable")),
        )
        monkeypatch.setitem(sys.modules, "eidos._version", None)
        monkeypatch.setattr(importlib.metadata, "version", lambda _pkg: "9.9.9")
        importlib.reload(eidos)
        assert eidos.__version__ == "9.9.9"

        def _raise_pkg_not_found(_pkg: str) -> str:
            raise importlib.metadata.PackageNotFoundError(_pkg)

        monkeypatch.setattr(importlib.metadata, "version", _raise_pkg_not_found)
        importlib.reload(eidos)
        assert eidos.__version__ == "0.1.0"
    finally:
        monkeypatch.undo()
        if orig_ver_mod is not None:
            sys.modules["eidos._version"] = orig_ver_mod
        importlib.reload(eidos)

    # 3. src/eidos/core/spec/parser.py edge cases
    assert safe_load_yaml(None) is None
    assert safe_load_yaml(b"key: 42\n") == {"key": 42}
    assert safe_load_yaml("   \n  ") is None
    assert safe_load_yaml("\n---") is None
    with pytest.raises(YamlError):
        safe_load_yaml(12345)
    with pytest.raises(YamlError, match="multiple documents"):
        safe_load_yaml("a: 1\n---\nb: 2\n")

    import fast_yaml

    monkeypatch.setattr(fast_yaml, "safe_dump", lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("emit err")))
    with pytest.raises(YamlError, match="emit err"):
        safe_dump_yaml({"a": 1})
    monkeypatch.undo()

    assert split_frontmatter_and_body("") == (None, "")
    fm_multi, _body_multi = split_frontmatter_and_body("---\n: invalid: [\n---\nvalid: true\n---\n= Title\n")
    assert fm_multi == ": invalid: ["

    empty_fm_doc = _parse_spec_content(tmp_path / "empty_fm.adoc", "---\n{}\n---\n= Heading\n")
    assert any("Missing YAML frontmatter" in e for e in empty_fm_doc.errors)

    # 4. src/eidos/core/lifecycle/initializer.py edge cases
    init_ws = tmp_path / "init_edge_ws"
    extra_leaf = init_ws / "eidos" / "architecture" / "custom_leaf"
    extra_leaf.mkdir(parents=True)
    ok_init, _, created_init = init_repository(init_ws, title="Edge Init", force=True)
    assert ok_init
    assert (extra_leaf / ".gitkeep") in created_init

    bad_adoc = init_ws / "eidos" / "domains" / "bad.adoc"
    bad_adoc.write_text(
        "---\ntype: product/capability\nid: CAP-BAD-1\ntitle: Bad\nstatus: active\ndomain: DOM-MISSING\nversion: '1.0.0'\n---\n= Bad\n",
        encoding="utf-8",
    )
    ok_bad, msg_bad, _ = init_repository(init_ws, title="Edge Init", force=True)
    assert not ok_bad
    assert "failed validation" in msg_bad

    # 5. src/eidos/core/lifecycle/catalog.py edge cases
    assert _format_yaml_scalar_value("\"unclosed'")
    assert _format_yaml_scalar_value("12345") == '"12345"'
    assert _split_yaml_inline_comment("'quoted # hash' # comment") == ("'quoted # hash'", " # comment")
    updated_raw = _set_frontmatter_scalar(
        "---\ntype: product/domain\ntitle: Old\n\n  nested_cont: 1\nstatus: active\n---\n= Body\n",
        "title",
        "New",
    )
    assert "title: New" in updated_raw
    assert find_spec_by_id_or_path(init_ws / "eidos", "   ") is None
    assert (
        find_spec_by_id_or_path(init_ws / "eidos", "eidos/system.adoc") == (init_ws / "eidos" / "system.adoc").resolve()
    )

    dlt_dir = init_ws / "eidos" / "deltas"
    arch_dir = dlt_dir / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    dlt_active = dlt_dir / "dlt-2026-777-active-1111.adoc"
    dlt_archived = arch_dir / "dlt-2026-777-old-2222.adoc"
    dlt_active.write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-777-ACTIVE-1111\ntitle: A\nstatus: proposed\nintent:\n  kind: feature\n  motivation: m\nmutations: []\n---\n= A\n",
        encoding="utf-8",
    )
    dlt_archived.write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-777-OLD-2222\ntitle: O\nstatus: applied\nintent:\n  kind: feature\n  motivation: m\nmutations: []\n---\n= O\n",
        encoding="utf-8",
    )
    assert find_spec_by_id_or_path(init_ws / "eidos", "DLT-2026-777") == dlt_active.resolve()

    malformed_applied_dlt = dlt_dir / "dlt-2026-778-malformed.adoc"
    malformed_applied_dlt.write_text(
        "---\nstatus: applied\n: bad_yaml: [\n---\nReferences SYS-EDGE-INIT\n", encoding="utf-8"
    )
    refs = find_references_to_spec(init_ws / "eidos", "SYS-EDGE-INIT", init_ws / "eidos" / "system.adoc")
    assert malformed_applied_dlt.resolve() not in refs

    # 6. src/eidos/core/lifecycle/scaffolder.py edge cases
    scaf_dir = tmp_path / "scaf_edge" / "eidos"
    init_repository(scaf_dir.parent, title="Scaf Edge")
    create_spec(scaf_dir, "exploration", "EXP-DUP-001", "First Spike")
    with pytest.raises(FileExistsError):
        create_spec(scaf_dir, "exploration", "EXP-DUP-001", "Duplicate Spike Having Different Slug")

    assert list_specs(tmp_path / "nonexistent_eidos_dir") == []
    assert list_specs(scaf_dir, type_filter="exp")

    assert _resolve_schema_node("not-a-dict", {}) == {}  # type: ignore[arg-type]
    assert _resolve_schema_node(
        {"$ref": "#/$defs/semver", "description": "override"}, {"semver": {"type": "string"}}
    ) == {
        "type": "string",
        "description": "override",
    }
    assert _resolve_schema_node(
        {"$ref": "#/$defs/a"},
        {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a", "type": "string"}},
    )["type"] == "string"
    assert _synthesize_example_value("x", {"default": "dval"}, {}) == "dval"
    assert _synthesize_example_value("test_binding", {"type": "string"}, {}) == "tests/test_example.py::test_invariant"
    assert _synthesize_example_value("version", {"type": "string"}, {}) == "1.0.0"
    assert _synthesize_example_value("base_commit", {"type": "string"}, {}) == "a1b2c3d"
    assert _synthesize_example_value("flag", {"type": "boolean"}, {}) is True
    assert _synthesize_example_value("arr", {"type": "array"}, {}) == []
    assert _synthesize_example_value("obj", {"type": "object"}, {}) == {"example": "https://example.com"}
    assert _synthesize_example_value("unk", {}, {}) == "example_unk"

    # 7. src/eidos/cli/drift_commands.py & src/eidos/cli/index_commands.py CLI branches
    cli_runner = CliRunner()
    fake_drift = DriftCheckResult(
        is_clean=False,
        drift_detected=True,
        workflow_mode="direct",
        classification=ChangedFilesClassification(
            spec_files=["eidos/system.adoc"],
        ),
        validation_report=validator.validate_directory(init_ws / "eidos"),
        touched_components=["CMP-TEST"],
        unmapped_code_files=[],
        invariants=[
            InvariantTrace(
                invariant_id="INV-" + "X",
                rule="rule",
                spec_path="eidos/system.adoc",
                spec_id="SYS-TEST",
                test_binding="tests/test_x.py::test_x",
                has_test_coverage=False,
            )
        ],
        orphan_invariants=[("INV-" + "ORPHAN", "tests/test_x.py")],
        remediation_suggestions=["Fix drift"],
    )
    monkeypatch.setattr("eidos.cli.drift_commands.detect_drift", lambda **_kw: fake_drift)
    res_drift_rich = cli_runner.invoke(app, ["drift", "--path", str(tmp_path / "missing_eidos_dir"), "--warn-only"])
    assert res_drift_rich.exit_code == 0
    assert "NON-CODE SPECIFICATION DRIFT DETECTED" in res_drift_rich.stdout
    assert "Broken Cross-References" in res_drift_rich.stdout

    fake_inv_only = DriftCheckResult(
        is_clean=False,
        drift_detected=False,
        workflow_mode="direct",
        classification=ChangedFilesClassification(),
        validation_report=validator.validate_directory(scaf_dir),
        touched_components=[],
        unmapped_code_files=[],
        invariants=fake_drift.invariants,
        orphan_invariants=[],
        remediation_suggestions=["Fix invariant"],
    )
    monkeypatch.setattr("eidos.cli.drift_commands.detect_drift", lambda **_kw: fake_inv_only)
    res_inv_rich = cli_runner.invoke(app, ["drift", "--path", str(scaf_dir.parent), "--warn-only"])
    assert res_inv_rich.exit_code == 0
    assert "INVARIANT TRACEABILITY DRIFT DETECTED" in res_inv_rich.stdout
    monkeypatch.undo()

    # CLI index commands branches (search/find-symbol empty, semantic setup/sync/unknown)
    res_no_search = cli_runner.invoke(
        app, ["index", "search", "zzzz_nonexistent_query_token_999", "--no-semantic", "--path", str(scaf_dir)]
    )
    assert res_no_search.exit_code == 0
    assert "No architectural specifications matched" in res_no_search.stdout

    res_no_sym = cli_runner.invoke(app, ["index", "find-symbol", "ZzzzNonexistentSymbol999", "--path", str(scaf_dir)])
    assert res_no_sym.exit_code == 0
    assert "No symbols found" in res_no_sym.stdout

    res_bad_sem = cli_runner.invoke(app, ["index", "semantic", "invalid_action", "--path", str(scaf_dir)])
    assert res_bad_sem.exit_code == 1
    assert "Unknown semantic action" in res_bad_sem.stdout

    scaf_idx = get_architectural_index(eidos_dir=scaf_dir)
    monkeypatch.setattr(
        scaf_idx,
        "setup_semantic",
        lambda variant="quantized", force=False: {
            "variant": variant,
            "model_dir": str(scaf_dir),
            "downloaded": ["a"],
            "skipped": [],
        },
    )
    monkeypatch.setattr(
        scaf_idx,
        "sync_semantic",
        lambda force=False: {"duration_ms": 1.2, "encoded_entities": 2, "reused_entities": 1, "pruned_orphans": 0},
    )
    monkeypatch.setattr("eidos.cli.index_commands.get_architectural_index", lambda eidos_dir=None: scaf_idx)
    assert cli_runner.invoke(app, ["index", "semantic", "setup"]).exit_code == 0
    assert cli_runner.invoke(app, ["index", "semantic", "setup", "--json"]).exit_code == 0
    assert cli_runner.invoke(app, ["index", "semantic", "sync"]).exit_code == 0
    assert cli_runner.invoke(app, ["index", "semantic", "sync", "--json"]).exit_code == 0

    def _fail_sem(**_kw):
        raise RuntimeError("sem failure")

    monkeypatch.setattr(scaf_idx, "setup_semantic", _fail_sem)
    monkeypatch.setattr(scaf_idx, "sync_semantic", _fail_sem)
    assert cli_runner.invoke(app, ["index", "semantic", "setup"]).exit_code == 1
    assert cli_runner.invoke(app, ["index", "semantic", "setup", "--json"]).exit_code == 1
    assert cli_runner.invoke(app, ["index", "semantic", "sync"]).exit_code == 1
    assert cli_runner.invoke(app, ["index", "semantic", "sync", "--json"]).exit_code == 1
    scaf_idx.close()
    monkeypatch.undo()

    # 8. src/eidos/core/index/sync.py & src/eidos/core/lifecycle/audit.py & delta_engine.py & vcs/diff.py & vcs/drift.py & plugin/installer.py
    git_ws = tmp_path / "cov_git_ws"
    git_ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=git_ws, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=git_ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=git_ws, check=True)
    init_repository(git_ws, title="Cov Git")
    pkg_dir = git_ws / "src" / "pkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("from .helper import helper_fn\n", encoding="utf-8")
    (pkg_dir / "helper.py").write_text("def helper_fn() -> int:\n    return 1\n", encoding="utf-8")
    tests_dir = git_ws / "tests"
    tests_dir.mkdir()
    t_file = tests_dir / "test_pkg.py"
    t_file.write_text('def test_pkg():\n    """Verifies ' + "INV-" + 'COV-1."""\n    assert True\n', encoding="utf-8")

    cnt_f = create_spec(git_ws / "eidos", "container", "CNT-PKG", "Pkg Container")
    cnt_f.write_text(
        "---\ntype: tech/container\nid: CNT-PKG\ntitle: Pkg\nstatus: active\ncontainer_type: library\nruntime: Python\nsource_path: src/pkg\n---\n= Pkg\n\n== Public Surface\n* `helper_fn() -> int`\n",
        encoding="utf-8",
    )
    cmp_a = create_spec(git_ws / "eidos", "component", "CMP-PKG-INIT", "Pkg Init")
    cmp_a.write_text(
        "---\ntype: tech/component\nid: CMP-PKG-INIT\ntitle: PkgInit\nstatus: active\ncontainer: CNT-PKG\nsource_path: src/pkg/__init__.py\n---\n= PkgInit\n",
        encoding="utf-8",
    )
    cmp_b = create_spec(git_ws / "eidos", "component", "CMP-PKG-HELPER", "Pkg Helper")
    cmp_b.write_text(
        "---\ntype: tech/component\nid: CMP-PKG-HELPER\ntitle: PkgHelper\nstatus: active\ncontainer: CNT-PKG\nsource_path: src/pkg/*.py\n---\n= PkgHelper\n\n== Public Surface\n* `helper_fn() -> int`\n",
        encoding="utf-8",
    )
    assert validator.validate_directory(git_ws / "eidos").is_clean

    g_idx = get_architectural_index(repo_root=git_ws, eidos_dir=git_ws / "eidos")
    g_idx.sync(force=True)
    index_test_bindings(g_idx._get_connection(), get_ast_indexer(), t_file, "tests/test_pkg.py")
    g_idx.close()

    subprocess.run(["git", "add", "-A"], cwd=git_ws, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=git_ws, check=True)

    # Modify CMP-PKG-HELPER and commit a delta without base_commit so _resolve_git_baseline_sha uses git log fallback
    dlt_f = git_ws / "eidos" / "deltas" / "dlt-2026-901-cov-0001.adoc"
    dlt_f.write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-901-COV-0001\ntitle: Cov Delta\nstatus: proposed\nintent:\n  kind: feature\n  motivation: m\nmutations:\n  - operation: modify\n    target: eidos/architecture/components/pkg-helper.adoc\n    details: Replaced by CMP-PKG-INIT while updating CMP-PKG-HELPER.\n  - operation: modify\n    target: eidos/system.adoc\n    details: Touch system spec.\n  - operation: modify\n    target: SYS-DIFFERENT-99\n    details: Touch system by alias.\n  - operation: modify\n    target: ARCH-DIFFERENT-99\n    details: Touch architecture by alias.\n  - operation: modify\n    target: nonexistent/spec_target.adoc\n    details: Missing path target.\n  - operation: remove\n    target: eidos/architecture/components/pkg-init.adoc\n    details: Remove CMP-PKG-INIT replaced by CMP-PKG-HELPER.\ntombstone:\n  search_tokens:\n    - xy\n  allowed_reasons:\n    - path: src/pkg/helper.py\n      reason: allowed\n---\n= Delta\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=git_ws, check=True)
    subprocess.run(["git", "commit", "-qm", "add delta"], cwd=git_ws, check=True)

    digest = _resolve_git_baseline_sha(
        git_ws / "eidos",
        dlt_f,
        "CMP-PKG-HELPER",
        cmp_b,
        rev_cache={},
        blob_cache={},
    )
    assert digest is not None

    # Fallback rev scan in _resolve_git_baseline_sha when rev_cache was pre-populated without fallback
    _resolve_git_baseline_sha(
        git_ws / "eidos",
        dlt_f,
        "CMP-NONEXISTENT-99",
        cmp_b,
        rev_cache={dlt_f: ["HEAD"]},
        blob_cache={},
    )
    verify_delta_mutations(git_ws / "eidos", "DLT-2026-901-COV-0001")

    auditor = AuditEngine(repo_root=git_ws, eidos_dir=git_ws / "eidos")
    audit_rep = auditor.audit_delta("DLT-2026-901-COV-0001")
    assert audit_rep.delta_id == "DLT-2026-901-COV-0001"
    assert auditor._scan_and_classify_references(set(), set(), dlt_f, {}) == ([], [])
    auditor._check_graph_integrity({"CMP-PKG-INIT"}, "DLT-2026-901-COV-0001", index=None)
    assert auditor._is_delta_applied(Path("eidos/deltas/dlt-2026-901-cov-0001.adoc")) is False
    assert auditor._is_delta_applied(Path("eidos/deltas/nonexistent.adoc")) is False

    # Branch drift evaluation with path target in delta (non-uncommitted)
    drift_branch_res = detect_drift(
        git_ws, base_ref="HEAD~1", head_ref="HEAD", eidos_dir=git_ws / "eidos", uncommitted=False
    )
    assert drift_branch_res.workflow_mode == "planned"

    # Fallback candidate scan and shallow clone error in _resolve_base_and_merge_base
    monkeypatch.setattr("eidos.vcs.diff._resolve_base_ref", lambda repo, base_ref=None: "origin/main")
    monkeypatch.setattr(
        "eidos.vcs.diff._resolve_merge_base",
        lambda repo, base, head="HEAD", fail_loudly=False: "deadbeef" if base == "HEAD~1" else None,
    )
    assert _resolve_base_and_merge_base(git_ws, None, "HEAD") == ("HEAD~1", "deadbeef", False)
    monkeypatch.setattr("eidos.vcs.diff._resolve_merge_base", lambda repo, base, head="HEAD", fail_loudly=False: None)
    assert _resolve_base_and_merge_base(git_ws, None, "HEAD") == (None, None, True)
    (git_ws / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")
    with pytest.raises(GitResolutionError, match="shallow"):
        _resolve_base_and_merge_base(git_ws, None, "HEAD")
    (git_ws / ".git" / "shallow").unlink()
    monkeypatch.undo()

    # SpecValidator schema reference & contract verification edge cases
    rep_v = ValidationReport()
    validator._validate_schema_references(
        {
            "x-eidos-ref": {"target_types": ["product/domain"], "entity_label": "domain"},
            "oneOf": [{"properties": {"sub": {"x-eidos-ref": {"target_types": ["product/domain"]}}}}],
        },
        ["CMP-PKG-INIT"],
        "domains",
        pol_doc,
        {"CMP-PKG-INIT"},
        rep_v,
        {"CMP-PKG-INIT": "tech/component"},
    )
    validator._validate_schema_references(
        {
            "oneOf": [{"properties": {"sub": {"x-eidos-ref": {"target_types": ["product/domain"]}}}}],
        },
        {"sub": "CMP-PKG-INIT"},
        "domains",
        pol_doc,
        {"CMP-PKG-INIT"},
        rep_v,
        {"CMP-PKG-INIT": "tech/component"},
    )
    validator._check_eidos_ref(
        {"entity_label": "target"},
        "CMP-PKG-INIT",
        "mutations.target",
        pol_doc,
        {"CMP-PKG-INIT"},
        rep_v,
        {"CMP-PKG-INIT": "product/domain"},
    )
    bad_yaml_spec = tmp_path / "bad_contract_spec.yaml"
    bad_yaml_spec.write_text(": invalid_yaml: [\n", encoding="utf-8")
    validator._verify_rest_openapi_contract(pol_doc, bad_yaml_spec, rep_v)
    validator._verify_json_schema_contract(pol_doc, bad_yaml_spec, rep_v)
    non_dict_spec = tmp_path / "list_contract_spec.yaml"
    non_dict_spec.write_text("- item1\n- item2\n", encoding="utf-8")
    validator._verify_rest_openapi_contract(pol_doc, non_dict_spec, rep_v)
    validator._verify_json_schema_contract(pol_doc, non_dict_spec, rep_v)
    validator._verify_grpc_proto_contract(pol_doc, tmp_path / "missing_proto.proto", rep_v)
    assert len(rep_v.broken_references) >= 6

    scoped = _scope_git_paths_to_subproject(
        ["", "packages/api/src/a.py", "packages/api/nested/b.py", "other/c.py"],
        git_ws / "packages" / "api",
        git_ws,
        ("nested/",),
    )
    assert scoped == ["src/a.py"]

    # Plugin installer helper edge cases (_copy_skills_flat, _uninstall_from_manifest)
    bundle_dir = locate_plugin_bundle()
    plug_dest = tmp_path / "plug_edge_dest"
    _copy_skills_flat(bundle_dir, plug_dest, force=False)
    (plug_dest / "eidos" / "SKILL.md").write_text("# modified eidos skill\n", encoding="utf-8")
    copied_again = _copy_skills_flat(bundle_dir, plug_dest, force=True)
    assert (plug_dest / "eidos" / "SKILL.md") in copied_again

    uninst_dir = tmp_path / "uninst_edge_dir"
    (uninst_dir / "sub_empty").mkdir(parents=True)
    (uninst_dir / "file_a.txt").write_text("a", encoding="utf-8")
    (uninst_dir / MANIFEST_FILENAME).write_bytes(b'{"files": ["file_a.txt", "sub_empty", "AGENTS.md"]}')
    assert _uninstall_from_manifest(uninst_dir) is True
