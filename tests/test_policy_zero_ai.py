"""Tests verifying POL-004: Zero AI-Call Dependency (Deterministic Governance Substrate)."""

import ast
import re
import tomllib
from pathlib import Path

from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator

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

FORBIDDEN_IMPORT_ROOTS = {pkg.replace("-", "_") for pkg in FORBIDDEN_AI_PACKAGES} | {
    "vertexai",
    "langchain_core",
    "llama_index",
}

FORBIDDEN_LLM_ENDPOINTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
)


def _extract_pep508_pkg_name(dep: str) -> str:
    return re.split(r"[<>=!~;\[\s@]", dep, maxsplit=1)[0].strip().lower()


def test_pyproject_contains_zero_ai_dependencies():
    """Verify POL-004 and INV-AGENT-ZERO-AI-DEPENDENCY: pyproject.toml does not require any AI/LLM SDK dependencies."""
    repo_root = Path(__file__).parent.parent
    pyproject_path = repo_root / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml not found"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    runtime_deps = data.get("project", {}).get("dependencies", [])
    for dep in runtime_deps:
        pkg_name = _extract_pep508_pkg_name(dep)
        assert (
            pkg_name not in FORBIDDEN_AI_PACKAGES
        ), f"Violation of POL-004: runtime dependency '{dep}' is an AI/LLM SDK."

    optional_deps = data.get("project", {}).get("optional-dependencies", {})
    for group, deps in optional_deps.items():
        for dep in deps:
            pkg_name = _extract_pep508_pkg_name(dep)
            assert (
                pkg_name not in FORBIDDEN_AI_PACKAGES
            ), f"Violation of POL-004: optional dependency '{dep}' in group '{group}' is an AI/LLM SDK."


def test_src_contains_zero_agent_runner_stubs():
    """Verify POL-004: src/eidos/agent/base.py is decommissioned and no agent runner stubs exist."""
    repo_root = Path(__file__).parent.parent
    agent_base = repo_root / "src/eidos/agent/base.py"
    assert not agent_base.exists(), "src/eidos/agent/base.py must not exist under POL-004"


def test_src_ast_contains_zero_ai_sdk_imports_or_endpoints():
    """Verify INV-AGENT-ZERO-AI-DEPENDENCY: no Python module in src/eidos/ imports any AI SDK or hardcodes LLM API endpoints."""
    repo_root = Path(__file__).parent.parent
    src_dir = repo_root / "src" / "eidos"
    py_files = sorted(src_dir.rglob("*.py"))
    assert len(py_files) >= 20

    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0].lower()
                    assert root_mod not in FORBIDDEN_IMPORT_ROOTS, (
                        f"Forbidden AI SDK import '{alias.name}' in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0].lower()
                assert root_mod not in FORBIDDEN_IMPORT_ROOTS, (
                    f"Forbidden AI SDK import from '{node.module}' in {py_file}"
                )
        for endpoint in FORBIDDEN_LLM_ENDPOINTS:
            assert endpoint not in source, f"Forbidden LLM endpoint '{endpoint}' found in {py_file}"


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
