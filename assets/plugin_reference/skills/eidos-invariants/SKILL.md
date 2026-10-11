---
name: eidos-invariants
description: Define system policies (POL-*), capability invariants (INV-*), and enforce bidirectional invariant-test traceability across the test suite.
---

# Eidos Invariant Governance & Test Traceability (`eidos-invariants`)

This skill equips AI agents to define non-negotiable policies, author capability invariants, and enforce bidirectional traceability between living specifications and automated tests (`POL-002`).

## The Invariant-Test Golden Rule

> [!IMPORTANT]
> **POL-002 Invariant-Test Enforcement**: Every policy (`POL-*`) and capability (`CAP-*`) has defined invariants. Modifying code or specs without corresponding invariant test coverage violates the repository's governance.

---

## 1. CLI Commands for Governance

Use the Eidos CLI to scaffold policies and capabilities:

```bash
# 1. Scaffold a new system policy:
eidos spec create policy POL-003-ZERO-ORPHAN-SPECS \
  --title "Zero Orphan Specifications Policy" \
  --category governance \
  --rule "All capability documents must link to an active owning domain."

# 2. Scaffold a new capability with invariants:
eidos spec create capability CAP-AUTH-002 \
  --title "Session Token Invalidation" \
  --domain DOM-AUTH

# 3. Validate entire living specification graph:
eidos validate eidos
```

---

## 2. Policy Schema Anatomy (`policy.schema.yaml`)

Policies live in `eidos/policies/POL-*.adoc`:

```yaml
---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: POL-002-INVARIANT-TEST-ENFORCEMENT
title: "Invariant-Test Traceability Policy"
category: governance  # governance | architectural | security | compliance | testing
rule: "Every product capability must declare atomic invariants with traceable test bindings."

enforcement:
  mechanism: "Pytest invariant bindings & CI quality gates"
  severity: critical
---
= Policy: Invariant-Test Traceability Policy
:toc: left

== Rule Statement
All code mutations must maintain verified invariant test coverage.
```

---

## 3. Capability Invariant Definition (`capability.schema.yaml`)

Invariants are declared under `technical.invariants` in capability specifications (`eidos/domains/<domain>/capabilities/*.adoc`):

```yaml
---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: CAP-AUTH-001
title: "User Password Authentication"
domain: DOM-AUTH
status: active
version: "1.0.0"

business:
  personas:
    - eidos/personas/end-user.adoc
  jtbd: "When signing in, I want secure authentication, so that my account is protected."

technical:
  components:
    - "CMP-AUTH-SERVICE"
  contracts:
    interfaces:
      - "CTR-AUTH-API"
  invariants:
    - id: INV-AUTH-RATE-LIMIT
      rule: "Exceeding 5 failed login attempts locks account for 15 minutes."
      test_binding: "tests/auth/test_login.py::test_brute_force_lockout"
    - id: INV-AUTH-PASSWORD-HASH
      rule: "Passwords must be hashed with Argon2id using minimum 64MB memory cost."
      test_binding: "tests/auth/test_hasher.py::test_argon2_parameters"
---
```

---

## 4. Test Binding Conventions

To satisfy invariant traceability in the test suite:
1. **Reference the Invariant ID in the test function name**:
   ```python
   def test_inv_auth_rate_limit():
       ...
   ```
2. **Reference the Policy or Invariant in the docstring**:
   ```python
   def test_login_brute_force_lockout():
       """Verify account lock after 5 failed attempts (INV-AUTH-RATE-LIMIT, POL-002)."""
       ...
   ```
3. **Use Pytest markers**:
   ```python
   @pytest.mark.invariant("INV-AUTH-RATE-LIMIT")
   def test_lockout():
       ...
   ```

---

## 5. Verification Protocol

Before completing any task:
1. Run specification validation:
   ```bash
   eidos validate eidos
   ```
2. Run the test suite:
   ```bash
   pytest -v
   ```
Ensure 100% of declared invariants have passing automated test coverage.
