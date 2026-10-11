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

