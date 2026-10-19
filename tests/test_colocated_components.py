"""Empirical Benchmark & Validation Suite for EXP-2026-004:
Dedicated Component Manifests vs Inline Container Components (Centralized C4 L3 Architecture).

Verifies:
1. Spike 1: Schema Formalization & Invariants Validation:
   - INV-COMPONENT-CENTRALIZATION-MANDATE: Components must reside in eidos/architecture/components/, never in src/
   - INV-COMPONENT-CONTAINER-AFFINITY: Must bind to valid CNT-* container
   - INV-INTERFACE-BOUNDARY-GOVERNANCE: Boundary contracts (CTR-*) owned by CNT-*, not CMP-*
2. Spike 2: SQLite Knowledge Graph Centralized Discovery & Querying:
   - Discovers centralized component .adoc files in eidos/architecture/components/
   - Indexes graph edges (contains_component, part_of, consumes_api, asserts)
   - Resolves reverse source paths and in-process API signatures without touching src/
3. Spike 3: Scaffolding, Promotion & Inlining Lifecycle:
   - promote_component transforms inline entry to eidos/architecture/components/<name>.adoc and ref:
   - Verifies src/ remains 100% pure code with zero .adoc files
   - inline_component demotes dedicated spec back to inline representation and removes file
4. Spike 4 / Exit Criteria:
   - Benchmark token retrieval efficiency >= 40% improvement
   - Zero broken graph references across multi-tier topologies
   - 100% centralized spec containment under eidos/
"""

import pytest

from eidos.core.index.graph_index import ArchitecturalIndex
from eidos.core.lifecycle.scaffolder import inline_component, promote_component
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator


@pytest.fixture
def repo_env(tmp_path):
    """Set up an isolated Eidos repository environment with containers and code."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    eidos_dir = repo_root / "eidos"
    containers_dir = eidos_dir / "architecture" / "containers"
    components_dir = eidos_dir / "architecture" / "components"
    contracts_dir = eidos_dir / "architecture" / "contracts"
    containers_dir.mkdir(parents=True)
    components_dir.mkdir(parents=True)
    contracts_dir.mkdir(parents=True)

    # Source code directory structure (pure code, zero specs)
    src_core = repo_root / "src" / "sample" / "core"
    src_core.mkdir(parents=True)
    (src_core / "parser.py").write_text("class SpecParser:\n    pass\n", encoding="utf-8")
    (src_core / "validator.py").write_text(
        "from sample.core.parser import SpecParser\n\nclass SpecValidator:\n    pass\n",
        encoding="utf-8",
    )

    # Tests directory
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_core.py").write_text("def test_parser(): pass\n", encoding="utf-8")

    # Container manifest with 1 inline component (promoted components link bottom-up via container: CNT-*)
    (containers_dir / "core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-SAMPLE-CORE\n"
        "title: Sample Core Engine\n"
        "container_type: library\n"
        "runtime: Python 3.12+\n"
        "source_path: src/sample/core\n"
        "contracts:\n"
        "  provides:\n"
        "    - CTR-SAMPLE-API\n"
        "components:\n"
        "  - id: CMP-CORE-PARSER\n"
        "    title: SpecParser\n"
        "    responsibility: Parses specification documents with frontmatter.\n"
        "    source_path: src/sample/core/parser.py\n"
        "---\n"
        "= Sample Core Engine\n",
        encoding="utf-8",
    )

    # Centralized dedicated component manifest in eidos/architecture/components/validator.adoc
    (components_dir / "validator.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CORE-VALIDATOR\n"
        "title: Specification Validator\n"
        "container: CNT-SAMPLE-CORE\n"
        "status: active\n"
        "source_path: src/sample/core/validator.py\n"
        "responsibility: Validates living specifications against schemas.\n"
        "invariants:\n"
        "  - id: INV-VALIDATOR-CONFORMANCE\n"  # eidos:fixture
        "    rule: All documents must conform to Draft 2020-12 schemas.\n"
        "    test_binding: tests/test_core.py::test_parser\n"
        "---\n"
        "= Component: Specification Validator\n\n"
        "Validates living specifications against Draft 2020-12 JSON schemas and cross-reference rules.\n",
        encoding="utf-8",
    )

    # Valid contract owned by CNT-SAMPLE-CORE
    (contracts_dir / "api.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-SAMPLE-API\n"
        "title: Core API Contract\n"
        "protocol: cli_args\n"
        "container: CNT-SAMPLE-CORE\n"
        "specification_file: src/sample/core/parser.py\n"
        "---\n"
        "= Core API Contract\n",
        encoding="utf-8",
    )

    return repo_root, eidos_dir


def test_spike_1_centralized_component_validation(repo_env):
    """Spike 1: Verify valid centralized component and parent container pass schema validation."""
    _repo_root, eidos_dir = repo_env
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)

    assert report.is_clean, f"Validation errors: {report.schema_errors}, broken: {report.broken_references}"
    assert "CMP-CORE-VALIDATOR" in report.entity_index
    assert "CNT-SAMPLE-CORE" in report.entity_index


def test_spike_1_centralization_mandate_enforcement(repo_env):
    """Spike 1: Verify INV-COMPONENT-CENTRALIZATION-MANDATE rejects components placed in src/ code tree."""
    repo_root, _eidos_dir = repo_env
    src_core = repo_root / "src" / "sample" / "core"

    # Illegally place component manifest in src/ code directory
    illegal_comp = src_core / "illegal.adoc"
    illegal_comp.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-ILLEGAL-CODE-POLLUTION\n"
        "title: Illegal Code Pollution\n"
        "container: CNT-SAMPLE-CORE\n"
        "status: active\n"
        "source_path: src/sample/core/parser.py\n"
        "responsibility: Illegally placed in src/ code tree.\n"
        "---\n"
        "= Illegal\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    report = validator.validate_directory(repo_root)
    assert not report.is_clean
    errors_str = " ".join(report.schema_errors.get(str(illegal_comp), []))
    assert "INV-COMPONENT-CENTRALIZATION-MANDATE" in errors_str

    # Also verify a tech/component placed inside eidos/domains/ (wrong subdir under eidos/) is rejected
    domains_dir = _eidos_dir / "domains"
    domains_dir.mkdir(parents=True, exist_ok=True)
    wrong_subdir_comp = domains_dir / "misplaced_comp.adoc"
    wrong_subdir_comp.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-MISPLACED-SUBDIR\n"
        "title: Misplaced Subdir Component\n"
        "container: CNT-SAMPLE-CORE\n"
        "status: active\n"
        "source_path: src/sample/core/parser.py\n"
        "responsibility: Placed in eidos/domains/ instead of eidos/architecture/components/.\n"
        "---\n"
        "= Misplaced\n",
        encoding="utf-8",
    )
    report2 = validator.validate_directory(_eidos_dir)
    errors_str2 = " ".join(report2.schema_errors.get(str(wrong_subdir_comp), []))
    assert "INV-COMPONENT-CENTRALIZATION-MANDATE" in errors_str2


def test_spike_1_container_affinity_enforcement(repo_env):
    """Spike 1: Verify INV-COMPONENT-CONTAINER-AFFINITY rejects orphaned components and non-container bindings."""
    _repo_root, eidos_dir = repo_env
    comp_dir = eidos_dir / "architecture" / "components"

    (comp_dir / "orphan.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-ORPHAN\n"
        "title: Orphan Component\n"
        "container: CNT-NONEXISTENT\n"
        "status: active\n"
        "source_path: src/sample/core/parser.py\n"
        "responsibility: References container that does not exist.\n"
        "---\n"
        "= Orphan\n",
        encoding="utf-8",
    )
    (comp_dir / "wrong_parent_type.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-WRONG-PARENT\n"
        "title: Wrong Parent Type\n"
        "container: CTR-SAMPLE-API\n"
        "status: active\n"
        "source_path: src/sample/core/parser.py\n"
        "responsibility: References a contract ID instead of a container ID.\n"
        "---\n"
        "= Wrong Parent\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    broken_str = " ".join(report.broken_references)
    schema_str = " ".join(report.schema_errors.get(str(comp_dir / "wrong_parent_type.adoc"), []))
    assert "INV-COMPONENT-CONTAINER-AFFINITY" in broken_str
    assert "CNT-NONEXISTENT" in broken_str
    assert "CTR-SAMPLE-API" in broken_str or "pattern" in schema_str


def test_spike_1_interface_boundary_governance_enforcement(repo_env):
    """Spike 1: Verify INV-INTERFACE-BOUNDARY-GOVERNANCE enforces container boundary ownership."""
    _repo_root, eidos_dir = repo_env
    contracts_dir = eidos_dir / "architecture" / "contracts"

    # Contract erroneously owned by a component instead of a container
    (contracts_dir / "bad_owner.adoc").write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-BAD-OWNER\n"
        "title: Bad Owner Contract\n"
        "protocol: cli_args\n"
        "container: CMP-CORE-VALIDATOR\n"
        "specification_file: src/sample/core/parser.py\n"
        "---\n"
        "= Bad Owner\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert not report.is_clean
    errors_str = " ".join(report.schema_errors.get(str(contracts_dir / "bad_owner.adoc"), []))
    assert "INV-INTERFACE-BOUNDARY-GOVERNANCE" in errors_str


def test_spike_2_centralized_component_indexing(repo_env):
    """Spike 2: Verify ArchitecturalIndex discovers and indexes centralized components and edges."""  # eidos:fixture
    repo_root, eidos_dir = repo_env
    db_path = repo_root / "test_index.db"

    index = ArchitecturalIndex(db_path=db_path, repo_root=repo_root, eidos_dir=eidos_dir)
    stats = index.sync(force=True)

    assert stats.indexed_new >= 2
    conn = index._get_connection()

    # Verify entities table contains both container and centralized component
    comp_row = conn.execute("SELECT * FROM entities WHERE id = 'CMP-CORE-VALIDATOR';").fetchone()
    assert comp_row is not None
    assert comp_row["type"] == "tech/component"
    assert "architecture/components/validator.adoc" in comp_row["file_path"].replace("\\", "/")

    # Verify contains_component and part_of edges
    edge_contains = conn.execute(
        "SELECT * FROM edges WHERE source_id = 'CNT-SAMPLE-CORE' AND target_id = 'CMP-CORE-VALIDATOR' AND rel_type = 'contains_component';"
    ).fetchone()
    assert edge_contains is not None

    edge_part_of = conn.execute(
        "SELECT * FROM edges WHERE source_id = 'CMP-CORE-VALIDATOR' AND target_id = 'CNT-SAMPLE-CORE' AND rel_type = 'part_of';"
    ).fetchone()
    assert edge_part_of is not None

    # Verify component invariant indexed
    inv_row = conn.execute("SELECT * FROM entities WHERE id = 'INV-VALIDATOR-CONFORMANCE';").fetchone()  # eidos:fixture
    assert inv_row is not None

    # Verify reverse symbol mapping for source path & interface method
    matches = index.resolve_symbol_or_path("src/sample/core/validator.py")
    matched_ids = [m["entity_id"] for m in matches]
    assert "CMP-CORE-VALIDATOR" in matched_ids

    index.close()


def test_spike_3_promotion_and_inlining_lifecycle(repo_env):
    """Spike 3: Verify promote_component and inline_component lifecycle CLI prototype."""
    repo_root, eidos_dir = repo_env

    # Enrich inline CMP-CORE-PARSER with domain and decisions
    domains_dir = eidos_dir / "domains"
    domains_dir.mkdir(parents=True, exist_ok=True)
    (domains_dir / "core.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-SAMPLE\ntitle: Sample Domain\nclassification: core\n---\n= Sample Domain\n",
        encoding="utf-8",
    )
    cnt_file = eidos_dir / "architecture" / "containers" / "core.adoc"
    cnt_file.write_text(
        cnt_file.read_text(encoding="utf-8").replace(
            "    source_path: src/sample/core/parser.py\n",
            "    source_path: src/sample/core/parser.py\n    domain: DOM-SAMPLE\n",
        ),
        encoding="utf-8",
    )

    # Error branches: promoting or inlining nonexistent component raises ValueError
    with pytest.raises(ValueError, match="not found"):
        promote_component("CMP-DOES-NOT-EXIST", eidos_dir=eidos_dir, repo_root=repo_root)
    with pytest.raises(ValueError, match="not found"):
        inline_component("CMP-CORE-PARSER", eidos_dir=eidos_dir, repo_root=repo_root)

    # 1. Promote inline component CMP-CORE-PARSER to dedicated specification
    promoted_spec, container_spec = promote_component(
        component_id="CMP-CORE-PARSER",
        eidos_dir=eidos_dir,
        repo_root=repo_root,
    )

    assert promoted_spec.exists()
    assert promoted_spec.name == "core-parser.adoc"
    assert promoted_spec.parent == eidos_dir / "architecture" / "components"

    # Verify src/ contains ZERO .adoc files - Eidos never touches code!
    src_adocs = list((repo_root / "src").rglob("*.adoc"))
    assert len(src_adocs) == 0, f"Specs illegally created in src/: {src_adocs}"

    # Verify inline component was removed from container components[] upon promotion
    c_doc = parse_spec_file(container_spec)
    comps = c_doc.frontmatter.get("components") or []
    assert not any(c.get("id") == "CMP-CORE-PARSER" for c in comps)

    # Verify promoted spec is valid tech/component and preserved domain
    promoted_doc = parse_spec_file(promoted_spec)
    assert promoted_doc.doc_type == "tech/component"
    assert promoted_doc.frontmatter["container"] == "CNT-SAMPLE-CORE"
    assert promoted_doc.frontmatter["domain"] == "DOM-SAMPLE"
    assert promoted_doc.is_valid

    # Validate entire eidos directory
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert report.is_clean, f"Errors after promotion: {report.schema_errors}"

    # 2. Inline the component back into container
    updated_container, _deleted_spec = inline_component(
        component_id="CMP-CORE-PARSER",
        eidos_dir=eidos_dir,
        repo_root=repo_root,
    )

    assert not promoted_spec.exists()
    c_doc_inlined = parse_spec_file(updated_container)
    parser_inlined = next(c for c in c_doc_inlined.frontmatter["components"] if c["id"] == "CMP-CORE-PARSER")
    assert "ref" not in parser_inlined
    assert "source_path" in parser_inlined
    assert parser_inlined.get("domain") == "DOM-SAMPLE"

    report_after_inline = validator.validate_directory(eidos_dir)
    assert report_after_inline.is_clean

    # 3. Verify CLI 'eidos spec component promote' and 'eidos spec component inline' work and legacy shortcuts are rejected
    from typer.testing import CliRunner

    from eidos.cli.main import app

    runner = CliRunner()
    assert runner.invoke(app, ["component", "promote", "CMP-CORE-PARSER"]).exit_code != 0
    assert runner.invoke(app, ["spec", "component", "list"]).exit_code != 0

    res_prom = runner.invoke(
        app,
        ["spec", "component", "promote", "CMP-CORE-PARSER", "--path", str(eidos_dir)],
    )
    assert res_prom.exit_code == 0
    assert promoted_spec.exists()

    res_inl = runner.invoke(
        app,
        ["spec", "component", "inline", "CMP-CORE-PARSER", "--path", str(eidos_dir)],
    )
    assert res_inl.exit_code == 0
    assert not promoted_spec.exists()


def test_spike_4_exit_criteria_token_budget_benchmark(repo_env):
    """Spike 4 / Exit Criteria: Benchmark context retrieval token savings >= 40% in hybrid mode."""
    repo_root, eidos_dir = repo_env
    # Add additional sibling components/policies in the catalog so raw workspace spec size reflects a multi-spec repo
    comp_dir = eidos_dir / "architecture" / "components"
    for i in range(4):
        (comp_dir / f"extra_{i}.adoc").write_text(
            "---\n"
            "type: tech/component\n"
            f"id: CMP-EXTRA-{i}\n"
            f"title: Extra Component {i}\n"
            "container: CNT-SAMPLE-CORE\n"
            "status: active\n"
            "source_path: src/sample/core/parser.py\n"
            "responsibility: " + ("Handles auxiliary domain orchestration and telemetry routing. " * 8) + "\n"
            "---\n"
            f"= Extra Component {i}\n\n" + ("Detailed component design narrative and architectural notes.\n" * 12),
            encoding="utf-8",
        )

    db_path = repo_root / "benchmark_index.db"

    index = ArchitecturalIndex(db_path=db_path, repo_root=repo_root, eidos_dir=eidos_dir)
    index.sync(force=True)

    # 1. Measure context slice token consumption for targeted component
    slice_anchor = index.get_tiered_context_slice(component="CMP-CORE-VALIDATOR", tier="anchor")
    slice_guardrails = index.get_tiered_context_slice(component="CMP-CORE-VALIDATOR", tier="guardrails")
    slice_deep = index.get_tiered_context_slice(component="CMP-CORE-VALIDATOR", tier="deep")

    # Compute baseline tokens directly from the raw specification files on disk
    raw_chars = sum(len(p.read_text(encoding="utf-8")) for p in eidos_dir.rglob("*.adoc"))
    baseline_centralized_tokens = max(raw_chars // 4, 1)
    anchor_savings = (1.0 - (slice_anchor.token_estimate / baseline_centralized_tokens)) * 100.0
    guardrails_savings = (1.0 - (slice_guardrails.token_estimate / baseline_centralized_tokens)) * 100.0

    # Verify exit criterion: Token retrieval efficiency >= 40% improvement
    assert anchor_savings >= 40.0, f"Anchor savings {anchor_savings:.1f}% was below 40% target"
    assert guardrails_savings >= 40.0, f"Guardrails savings {guardrails_savings:.1f}% was below 40% target"
    assert slice_anchor.token_estimate < slice_guardrails.token_estimate <= slice_deep.token_estimate

    index.close()


def test_centralization_mandate_parent_path_named_src(tmp_path):
    """Verify INV-COMPONENT-CENTRALIZATION-MANDATE does not false-positive when repo is in a parent directory named 'src'."""
    repo_root = tmp_path / "src" / "my_project"
    repo_root.mkdir(parents=True)
    eidos_dir = repo_root / "eidos"
    comp_dir = eidos_dir / "architecture" / "components"
    comp_dir.mkdir(parents=True)

    # Valid component in eidos/architecture/components/
    valid_comp = comp_dir / "valid_component.adoc"
    valid_comp.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-SRC-PATH-VALID\n"
        "title: Valid Component Under Src Parent\n"
        "container: CNT-SAMPLE-CORE\n"
        "status: active\n"
        "source_path: src/sample/core/parser.py\n"
        "responsibility: Valid component placed centrally in eidos/architecture/components/.\n"
        "---\n"
        "= Valid Component\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    errors = report.schema_errors.get(str(valid_comp), [])
    assert not any("INV-COMPONENT-CENTRALIZATION-MANDATE" in err for err in errors)
