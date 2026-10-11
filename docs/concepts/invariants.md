# Invariant-Test Traceability (`POL-*` & `INV-*`)

In traditional projects, product requirements live in Jira or Notion, architectural constraints live in slide decks or minds, and code tests live in Git. There is zero verifiable connection between them.

Eidos enforces **Bidirectional Invariant-Test Traceability** using two complementary constructs: **Policies (`POL-*`)** and **Invariants (`INV-*`)**.

---

## 1. What are `POL-*` and `INV-*`?

| Construct | Scope | Living Spec Location | Business vs. Technical |
| :--- | :--- | :--- | :--- |
| **`POL-*` (Policies)** | **System-wide** governance & architectural laws | Standalone documents in `eidos/policies/POL-*.adoc` (`type: product/policy`) | **Macro Governance**: Covers business compliance (GDPR, SOC2, ground truth) down to engineering rules (layering, test gates). |
| **`INV-*` (Invariants)** | **Feature-level** falsifiable truth assertions | Inside Capability manifests (`CAP-*`) and SpecDeltas (`DLT-*`) under `technical.invariants` | **Executable Contract**: Translates core business rules and technical constraints into testable code bindings. |

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

## 3. Capability Invariants (`INV-*`)

Invariants are fine-grained, verifiable behavioral conditions that **must always remain true** for a specific capability or feature. In the Eidos schema, every invariant is explicitly mapped to a concrete test via `test_binding`.

### Example Capability Invariants (`eidos/domains/product-core/capabilities/living-spec-engine.adoc`):

```yaml
technical:
  components:
    - "packages/spec-parser"
    - "packages/spec-validator"
  contracts:
    interfaces:
      - "eidos spec validate"
  invariants:
    # 1. Technical Invariant:
    - id: INV-SPEC-VALIDATION
      rule: "Every .adoc file with a YAML frontmatter must validate against its referenced JSON/YAML schema."
      test_binding: "tests/specs/test_schema_validation.py::test_all_specs_conform_to_schema"

    # 2. Structural Invariant:
    - id: INV-SPEC-GRAPH-INTEGRITY
      rule: "All cross-document references (personas, policies, domain IDs) must resolve to existing files."
      test_binding: "tests/specs/test_schema_validation.py::test_graph_references_exist"
```

---

## 4. The Engineering Inspiration Behind Them

The pairing of `POL-*` and `INV-*` was designed to solve three critical challenges created by the rise of **autonomous AI coding agents**:

1. **Design by Contract (DbC) Scaled to Architecture**: Pioneered by Bertrand Meyer in Eiffel (preconditions, postconditions, class invariants), Eidos elevates DbC to the **product and architectural level**. An `INV-*` is an invariant for an entire product capability.
2. **Policy-as-Code (PaC)**: Inspired by Open Policy Agent (OPA) and Kubernetes admission controllers, `POL-*` treats architectural and compliance rules as declarative, machine-evaluable code in Git.
3. **Eliminating AI Failure Modes**:
   - **Silent Regressions**: When an AI agent modifies code to fix a bug, it often unintentionally violates unwritten business assumptions. `INV-*` with test bindings catches these immediately.
   - **Split-Brain Context**: Traditional documentation lives outside Git (Jira, Notion). By co-locating `POL-*` and `INV-*` inside Git, AI agents read the exact constraints directly in their workspace context.
   - **Verification Asymmetry**: Humans cannot review thousands of lines of AI boilerplate with full vigilance. `POL-*` and `INV-*` provide high-signal automated verification gates.

---

## 5. Test Binding Conventions

To satisfy invariant traceability in the test suite:

1. **Reference the Invariant in docstrings**:
   ```python
   def test_all_specs_conform_to_schema():
       """Verify INV-SPEC-VALIDATION and POL-002: All living specs conform to schema."""
       ...
   ```
2. **Reference the Invariant in function names or pytest marks**:
   ```python
   @pytest.mark.invariant("INV-SPEC-VALIDATION")
   def test_inv_spec_validation():
       ...
   ```
3. **Verify with Drift Detection**:
   ```bash
   eidos drift --base origin/main
   ```

