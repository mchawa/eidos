# Invariant-Test Traceability (`POL-*` & `INV-*`)

In traditional projects, product requirements live in Jira or Notion, architectural constraints live in slide decks or minds, and code tests live in Git. There is zero verifiable connection between them.

Eidos enforces **Bidirectional Invariant-Test Traceability** using two complementary constructs: **Policies (`POL-*`)** and **Invariants (`INV-*`)**.

---

## 1. What are `POL-*` and `INV-*`?

| Construct | Scope | Living Spec Location | Business vs. Technical |
| :--- | :--- | :--- | :--- |
| **`POL-*` (Policies)** | **System-wide** governance & architectural laws | Standalone documents in `eidos/policies/POL-*.adoc` (`type: product/policy`) | **Macro Governance**: Covers business compliance (GDPR, SOC2, ground truth) down to engineering rules (layering, test gates, zero-AI determinism). |
| **`INV-*` (Invariants)** | **Feature & Component-level** falsifiable truth assertions | Inside Capability manifests (`CAP-*` under `technical.invariants`), Component manifests (`CMP-*` under `invariants`), and SpecDeltas (`DLT-*` under `blast_radius.invariants_to_enforce`) | **Executable Contract**: Translates core business rules and technical constraints into AST-verified code test bindings (`file::func` or `file::Class::method`). |

---

## 2. System Policies (`POL-*`)

Policies define non-negotiable system invariants, compliance rules, structural guardrails, and quality gates across the entire repository.

### Example Policy: [`POL-002-INVARIANT-TEST-ENFORCEMENT.adoc`](../../eidos/policies/POL-002-invariant-test-enforcement.adoc)

```asciidoc
---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: POL-002-INVARIANT-TEST-ENFORCEMENT
title: "Mandatory Invariant Test Binding"
category: testing
rule: "Every technical invariant declared in a Capability or SpecDelta must specify a concrete test_binding path. A PR cannot merge if any bound test fails or is missing."
enforcement:
  mechanism: "Eidos CI Runner executes test runner targeting all test_binding paths"
  severity: blocking
---
= Policy: Mandatory Invariant Test Binding

== Rationale
Specifications without verifiable test bindings are merely aspirations. By binding every system invariant to an automated test, the system ensures that AI agents cannot claim completion without providing green regression tests.
```

---

## 3. Capability & Component Invariants (`INV-*`)

Invariants are fine-grained, verifiable behavioral conditions that **must always remain true** for a specific capability (`CAP-*`) or promoted component (`CMP-*`). Every capability invariant is explicitly mapped to a concrete test via `test_binding`.

### Example Capability Invariants (`eidos/domains/spec-engine/capabilities/living-spec-engine.adoc`):

```yaml
technical:
  containers:
    - "CNT-EIDOS-CORE"
    - "CNT-EIDOS-INDEX"
  components:
    - "CMP-SPEC-PARSER"
    - "CMP-SPEC-VALIDATOR"
  contracts:
    interfaces:
      - "eidos validate"
  invariants:
    # 1. Technical Invariant:
    - id: INV-SPEC-VALIDATION
      rule: "Every .adoc file with a YAML frontmatter across all 16 living specification types must serialize safely via yaml.safe_dump and validate against its referenced JSON/YAML schema."
      test_binding: "tests/test_validator.py::test_validate_eidos_specifications"

    # 2. Structural Invariant:
    - id: INV-SPEC-GRAPH-INTEGRITY
      rule: "All cross-document references and entity IDs must resolve to valid catalog entity IDs with zero collisions and strictly match the target entity prefix and type declared by x-eidos-ref."
      test_binding: "tests/test_validator.py::test_validate_eidos_specifications"

    # 3. Documentation Sync Invariant (POL-003):
    - id: INV-DOCS-SYNCHRONIZATION
      rule: "All 16 living specification schemas, CLI command interfaces, and C4 components must be accurately documented in docs/ and pass strict static site compilation."
      test_binding: "tests/test_docs_sync.py::test_documentation_and_schemas_in_sync"
```

---

## 4. The Engineering Inspiration Behind Them

The pairing of `POL-*` and `INV-*` was designed to solve three critical challenges created by the rise of **autonomous AI coding agents**:

1. **Design by Contract (DbC) Scaled to Architecture**: Pioneered by Bertrand Meyer in Eiffel (preconditions, postconditions, class invariants), Eidos elevates DbC to the **product and architectural level**. An `INV-*` is an invariant for an entire product capability or C4 component.
2. **Policy-as-Code (PaC)**: Inspired by Open Policy Agent (OPA) and Kubernetes admission controllers, `POL-*` treats architectural and compliance rules as declarative, machine-evaluable code in Git.
3. **Eliminating AI Failure Modes**:
   - **Silent Regressions**: When an AI agent modifies code to fix a bug, it often unintentionally violates unwritten business assumptions. `INV-*` with test bindings catches these immediately.
   - **Split-Brain Context**: Traditional documentation lives outside Git (Jira, Notion). By co-locating `POL-*` and `INV-*` inside Git, AI agents read the exact constraints directly in their workspace context (`eidos context`).
   - **Verification Asymmetry**: Humans cannot review thousands of lines of AI boilerplate with full vigilance. `POL-*` and `INV-*` provide high-signal automated verification gates.

---

## 5. How Polyglot Invariant Verification Works Under the Hood

When `eidos drift` runs, `CMP-DRIFT-DETECTOR` (`src/eidos/vcs/invariants.py`) and `CMP-AST-SYMBOL-INDEXER` (`src/eidos/core/index/ast_indexer.py`) statically verify every declared `INV-*` binding across **6 language families** without executing arbitrary code:

1. **Supported Languages**: Python (`.py`), TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`), Go (`_test.go`), Rust (`.rs`), Java (`.java`), and Kotlin (`.kt`, `.kts`).
2. **Supported Binding Formats**:
   * **Standalone Function**: `path/to/test_file.py::test_function_name`
   * **Class-Qualified Method**: `path/to/test_file.py::TestSuiteClass::test_method_name` (automatically falls back to bare `test_method_name` lookup when needed).
3. **Two-Way Verification (`test_binding_cache`)**:
   * **Forward Check**: Confirms that the test file exists on disk, the bound test function/method exists in the parsed AST, and the test body, docstring, comment, or decorator explicitly contains the `INV-*` identifier.
   * **Orphan Check**: Scans all test suites for `INV-*` references that do not correspond to any declared invariant in `eidos/`, flagging dangling test references.

### Test Binding Examples

=== "Python (pytest)"

    ```python
    @pytest.mark.invariant("INV-SPEC-VALIDATION")
    def test_validate_eidos_specifications():
        """Verify INV-SPEC-VALIDATION and POL-002: All living specs conform to schema."""
        ...
    ```

=== "TypeScript / JavaScript (Vitest / Jest)"

    ```typescript
    // Verifies INV-PORTAL-INTERACTIVE-C4
    describe("C4 Architecture Explorer", () => {
      it("test_c4_level_drilldown", () => {
        expect(renderC4Explorer()).toBeDefined();
      });
    });
    ```

=== "Go / Rust / Java / Kotlin"

    ```go
    // Verify INV-AUTH-TOKEN-EXPIRY
    func TestTokenExpirationInvariant(t *testing.T) {
        // ...
    }
    ```

Verify invariant coverage at any time with:

```bash
eidos drift --uncommitted
```

