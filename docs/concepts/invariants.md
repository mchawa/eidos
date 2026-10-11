# Invariant-Test Traceability

In traditional projects, product requirements live in Jira or Notion, architectural constraints live in slide decks or minds, and code tests live in Git. There is zero verifiable connection between them.

Eidos enforces **Bidirectional Invariant-Test Traceability**.

---

## What is an Invariant?

An **Invariant** is a formal, non-negotiable rule that the system must satisfy at all times. Invariants are declared in:

1. **System Policies** (`POL-*`): Cross-cutting architectural rules (e.g., *“No Node.js build dependencies in Python core”*, *“All external network calls must be authenticated”*).
2. **Capability Specifications** (`CAP-*`): Behavioral guarantees of product features (e.g., *“Spec validation must reject duplicate document IDs”*).

---

## Tracing Invariants to Tests

Every policy and capability explicitly lists its invariants. Every automated test in `tests/` must trace back to these IDs in docstrings, test markers, or test names.

### Example Policy (`eidos/policies/POL-002-INVARIANT-TEST-TRACEABILITY.adoc`):

```asciidoc
---
id: POL-002-INVARIANT-TEST-TRACEABILITY
title: Invariant-Test Traceability Policy
category: governance
rule: Every capability invariant and system policy must bind directly to executable tests.
tests:
  - tests/test_validator.py::test_policy_traceability
---
```

### Corresponding Test (`tests/test_validator.py`):

```python
def test_policy_traceability():
    """Verify POL-002: Invariant-Test Traceability across all living specs."""
    validator = SpecValidator()
    report = validator.validate_directory("eidos")
    assert report.is_valid
```

---

## Why Traceability Matters for AI Agents

When an AI agent modifies code or adds a feature:

* The agent can query which invariants (`POL-*`, `INV-*`) govern that component.
* The agent cannot conclude its task until the corresponding invariant tests pass.
* The agent cannot delete tests or bypass architectural invariants without failing CI spec validation.
