"""Tests verifying POL-004: Zero AI-Call Dependency (Deterministic Governance Substrate)."""

import tomllib
from pathlib import Path

from eidos.core.parser import parse_spec_file
from eidos.core.validator import SpecValidator

FORBIDDEN_AI_PACKAGES = {
    "openai",
    "anthropic",
    "google-generativeai",
    "google-genai",
    "langchain",
    "litellm",
    "cohere",
    "huggingface",
    "transformers",
    "instructor",
    "guidance",
    "dspy",
}


def test_pyproject_contains_zero_ai_dependencies():
    """Verify POL-004 and INV-AGENT-ZERO-AI-DEPENDENCY: pyproject.toml does not require any AI/LLM SDK dependencies."""
    repo_root = Path(__file__).parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml not found"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    runtime_deps = data.get("project", {}).get("dependencies", [])
    for dep in runtime_deps:
        pkg_name = dep.split(">=")[0].split("==")[0].split("<")[0].split("~=")[0].strip().lower()
        assert (
            pkg_name not in FORBIDDEN_AI_PACKAGES
        ), f"Violation of POL-004: runtime dependency '{dep}' is an AI/LLM SDK."

    optional_deps = data.get("project", {}).get("optional-dependencies", {})
    for group, deps in optional_deps.items():
        for dep in deps:
            pkg_name = dep.split(">=")[0].split("==")[0].split("<")[0].split("~=")[0].strip().lower()
            assert (
                pkg_name not in FORBIDDEN_AI_PACKAGES
            ), f"Violation of POL-004: optional dependency '{dep}' in group '{group}' is an AI/LLM SDK."


def test_src_contains_zero_agent_runner_stubs():
    """Verify POL-004: src/eidos/agent/base.py is decommissioned and no agent runner stubs exist."""
    repo_root = Path(__file__).parent.parent
    agent_base = repo_root / "src/eidos/agent/base.py"
    assert not agent_base.exists(), "src/eidos/agent/base.py must not exist under POL-004"


def test_pol_004_spec_is_valid():
    """Verify POL-004 specification document parses and passes schema validation."""
    repo_root = Path(__file__).parent.parent
    policy_path = repo_root / "eidos/policies/POL-004-zero-ai-call-dependency.adoc"
    assert policy_path.exists(), "POL-004 policy document must exist"

    doc = parse_spec_file(policy_path)
    assert doc.doc_id == "POL-004-ZERO-AI-CALL-DEPENDENCY"
    assert doc.doc_type == "product/policy"
    assert doc.frontmatter.get("category") == "architectural"
    assert doc.frontmatter.get("enforcement", {}).get("severity") == "blocking"

    validator = SpecValidator()
    errors = validator.validate_document(doc)
    assert not errors, f"POL-004 document failed schema validation: {errors}"

