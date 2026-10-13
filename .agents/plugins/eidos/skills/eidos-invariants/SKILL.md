---
name: eidos-invariants
description: Define system policies (POL-*), capability invariants (INV-*), and enforce bidirectional invariant-test traceability across the test suite.
---

# Eidos Invariant Governance & Test Traceability (`eidos-invariants`)

This skill equips AI agents to define non-negotiable policies, author capability invariants, and enforce bidirectional traceability between living specifications and automated tests (`POL-*`, `INV-*`).

## The Invariant-Test Golden Rule

> [!IMPORTANT]
> **Invariant-Test Enforcement**: Every policy (`POL-*`) and capability (`CAP-*`) has defined invariants (`INV-*`). Modifying code or specs without corresponding invariant test bindings violates the repository's governance.

---

## 1. CLI Commands for Governance

Use the Eidos CLI to scaffold policies and capabilities:

```bash
# 1. Scaffold a new system policy:
eidos spec create policy POL-ZERO-ORPHAN-SPECS \
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
id: POL-INVARIANT-TRACEABILITY
title: "Invariant-Test Traceability Policy"
category: governance  # governance | architectural | security | compliance | testing
rule: "Every product capability must declare atomic invariants with traceable test bindings."

enforcement:
  mechanism: "Static invariant test bindings & CI quality gates"
  severity: critical
---
= Policy: Invariant-Test Traceability Policy
:toc: left

== Rule Statement
All code mutations must maintain verified invariant test bindings.
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
  containers:
    - "CNT-AUTH-SERVICE"
  components:
    - "CMP-AUTH-HASHER"
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

## 4. Polyglot Test Binding Conventions

To satisfy invariant traceability, every invariant (`INV-*`) must be traceable to automated tests. The Eidos drift engine statically verifies test bindings across polyglot test suites. Place invariant markers using the native conventions of your language and framework:

### A. Python (`pytest`, `unittest`)
1. **In the test docstring (Recommended):**
   ```python
   def test_login_brute_force_lockout():
       """Verify account lock after 5 failed attempts (INV-AUTH-RATE-LIMIT)."""
       ...
   ```
2. **Via Pytest custom markers:**
   ```python
   @pytest.mark.invariant("INV-AUTH-RATE-LIMIT")
   def test_lockout():
       ...
   ```
3. **In the test function name:**
   ```python
   def test_inv_auth_rate_limit():
       ...
   ```

### B. TypeScript & JavaScript (`Jest`, `Vitest`, `Playwright`, `Mocha`)
1. **In test or suite titles (`describe` / `it` / `test`):**
   ```typescript
   describe('Authentication Invariants', () => {
     it('INV-AUTH-RATE-LIMIT: locks account for 15 minutes after 5 failed attempts', async () => {
       const res = await authService.login(badCredentials);
       expect(res.status).toBe(429);
     });
   });
   ```
2. **In source comments inside the test body:**
   ```typescript
   test('password hashing strength', async () => {
     // INV-AUTH-PASSWORD-HASH: Enforce Argon2id with 64MB memory cost
     const hash = await hashPassword('secret');
     expect(hash.algorithm).toBe('argon2id');
   });
   ```

### C. Go (`testing`)
1. **Via `t.Log` or assertion output:**
   ```go
   func TestAccountLockout(t *testing.T) {
       t.Log("INV-AUTH-RATE-LIMIT: verifying account lockout threshold")
       err := authService.AttemptLogin("alice", "wrong-pwd", 6)
       if err != ErrAccountLocked {
           t.Fatalf("expected ErrAccountLocked, got %v", err)
       }
   }
   ```
2. **In test function names or test comments:**
   ```go
   // INV-AUTH-RATE-LIMIT: 5 failed attempts locks for 15 minutes
   func TestInvAuthRateLimit(t *testing.T) {
       // ...
   }
   ```

### D. Rust (`cargo test`)
1. **In test function doc comments (`///`):**
   ```rust
   /// Verifies INV-AUTH-PASSWORD-HASH: Passwords hashed with Argon2id using >= 64MB memory cost.
   #[test]
   fn test_argon2_parameters() {
       let params = argon2::Params::default();
       assert!(params.m_cost() >= 65536);
   }
   ```
2. **In inline test comments:**
   ```rust
   #[test]
   fn test_lockout() {
       // INV-AUTH-RATE-LIMIT
       assert!(account.is_locked());
   }
   ```

### E. Java & Kotlin (`JUnit 5`, `Kotest`)
1. **Using JUnit 5 `@DisplayName` or `@Tag` annotations:**
   ```java
   @Test
   @Tag("INV-AUTH-RATE-LIMIT")
   @DisplayName("INV-AUTH-RATE-LIMIT: Locks account for 15 minutes after 5 failed attempts")
   void testBruteForceLockout() {
       authService.recordFailedAttempt("user@example.com", 5);
       assertTrue(authService.isLocked("user@example.com"));
   }
   ```
2. **In Kotlin test names and doc comments:**
   ```kotlin
   @Test
   @DisplayName("INV-AUTH-PASSWORD-HASH: Argon2id parameter compliance")
   fun `test argon2id password hashing strength`() {
       // INV-AUTH-PASSWORD-HASH
       val hash = hasher.hash("password")
       assertEquals("argon2id", hash.algorithm)
   }
   ```

### Capability Frontmatter `test_binding`
In addition to in-file markers, capabilities record explicit test bindings under `technical.invariants[].test_binding`:
- Relative file path: `test_binding: "tests/auth/test_login.py"`
- Test target with member selector: `test_binding: "tests/auth/test_login.py::test_brute_force_lockout"`
- Polyglot targets: `test_binding: "packages/auth/test/auth.test.ts"`, `test_binding: "internal/auth/auth_test.go"`, `test_binding: "tests/auth_test.rs"`

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
3. Run drift verification to verify all invariants are covered:
   ```bash
   eidos drift --uncommitted
   ```
Ensure 100% of declared invariants have passing automated test coverage.
