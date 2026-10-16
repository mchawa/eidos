---
name: eidos-plan
description: Orchestrate technical discovery spikes (EXP-*), the SpecDelta architectural mutation lifecycle (DLT-*), and Staff Systems Architect 5-dimension reviews in Planned Mode. Use when formulating hypotheses, proposing or applying SpecDeltas (create, verify, audit, apply, changelog), or reviewing PRs and architectural changes.
---
# Eidos Planning, SpecDeltas & Architectural Review (`eidos-plan`)

This skill governs **Planned Mode** workflows in Eidos:
1. **Technical Explorations (`EXP-*`)**: Hypothesis-driven spikes when requirements or technical feasibility are uncertain.
2. **SpecDelta Lifecycle (`DLT-*`)**: Formal architectural mutations across containers, components, contracts, capabilities, and policies.
3. **Staff Architectural Review**: Rigorous 5-dimension audit of SpecDeltas, PR diffs, and blast radius.

---

## 1. Technical Spikes & Feasibility Discovery (`EXP-*`)

When an engineering problem has high uncertainty (e.g., evaluating two storage engines, benchmarking a protocol, or prototyping an algorithm), start with an **Exploration** (`eidos/explorations/exp-yyyy-nnn.adoc`) before committing to a SpecDelta.

### 1.1 Exploration Lifecycle States

```text
proposed ──► in_progress ──► adopted   (Graduates to a SpecDelta DLT-* via decision.resulting_delta_id)
                         └──► discarded (Preserved in Git so future agents never repeat failed approaches)
```

### 1.2 Scaffolding & Authoring an Exploration

```bash
eidos spec create exploration EXP-2026-001 --title "Evaluate Embedded DuckDB vs SQLite FTS5"
```

```yaml
---
type: product/exploration
schema: schemas/v1/product/exploration.schema.yaml
id: EXP-2026-001
title: "Evaluate Embedded DuckDB vs SQLite FTS5 for Local Search"
status: in_progress # draft | proposed | planned | in_progress | adopted | discarded

hypothesis: "We believe Python's stdlib sqlite3 with FTS5 can execute hybrid lexical + graph queries in <5ms for 500 specs without external binary wheels."

questions_to_answer:
  - "Can stdlib sqlite3 FTS5 execute hybrid BM25 + graph queries in <5ms for 500 specs?"
  - "Does incremental dirty checking avoid full rebuilds on clean working trees?"

spikes_to_run:
  - name: "sqlite-fts5-prototype"
    target: "Benchmark cold index build and incremental dirty-check latency on 500 synthetic specs"

exit_criteria:
  max_duration: "2 days"
  benchmark: "Cold index build <150ms for 500 documents; incremental dirty check <5ms with zero C-extension wheels."

decision:
  outcome: adopted # adopted | discarded | pivoted
  resulting_delta_id: DLT-2026-011
  learnings: "SQLite FTS5 achieved 1.8ms incremental dirty checks and 0.9ms BM25 queries using stdlib sqlite3."
---
= Exploration: Evaluate Embedded DuckDB vs SQLite FTS5 for Local Search
```

### 1.3 Graduating or Archiving a Spike
* **If `adopted`**: Scaffold a SpecDelta (`eidos delta create DLT-YYYY-NNN`), set `decision.outcome: adopted` and `decision.resulting_delta_id: DLT-YYYY-NNN` in the `EXP-*` frontmatter, and record the winning rationale + `rejected_alternatives` in the target container/component's `decisions:` block.
* **If `discarded`**: Set `status: discarded` and `decision.outcome: discarded`, and document the empirical failure reasons in `decision.learnings:` so the institutional memory remains queryable via `eidos index search`.

---

## 2. SpecDelta Mutation Lifecycle (`DLT-*`)

In **Planned Mode**, any change that introduces/removes a container (`CNT-*`) or component (`CMP-*`), alters a contract (`CTR-*`), adds/extends a capability (`CAP-*`), or modifies a policy (`POL-*`) is governed by a **SpecDelta** (`eidos/deltas/dlt-yyyy-nnn.adoc`).

### 2.1 End-to-End SpecDelta Workflow

```bash
# 1. Inspect existing deltas to pick the next sequential ID
eidos delta list

# 2. Scaffold a new SpecDelta (status: proposed)
eidos delta create DLT-2026-015 \
  --title "Add Webhook Signature Verification Contract" \
  --kind feature \
  --branch feat/webhook-signatures

# 3. Edit eidos/deltas/dlt-2026-015.adoc to declare mutations, expect post-conditions, affects, and blast_radius

# 4. Transition status during active review/implementation (optional)
eidos delta status DLT-2026-015 in_review

# 5. Implement the code changes in src/, update living specs in eidos/, and bind tests in tests/

# 6. Verify all target entities, modification evidence, and declarative expect: post-conditions
eidos delta verify DLT-2026-015 --json

# 7. Reconcile and apply the SpecDelta (verifies post-conditions, sets status: applied, refreshes eidos/deltas/by-status/)
eidos delta apply DLT-2026-015

# 8. If removing/deprecating entities, audit for dangling references, and optionally regenerate CHANGELOG.md
eidos delta audit DLT-2026-015 --strict
eidos delta changelog
```

### 2.2 SpecDelta Frontmatter & Declarative `expect:` Post-Conditions

```yaml
---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-015
title: "Add Webhook Signature Verification Contract and Ingestor Component"
status: proposed # draft | proposed | planned | in_review | approved | applied | rejected
vcs:
  branch: feat/webhook-signatures
intent:
  kind: feature # feature | enabler | bug (for bug, pair with bug_classification: spec_violation | spec_gap and severity: low | medium | high | critical)
  motivation: "Prevent spoofed billing webhooks by enforcing HMAC-SHA256 timestamped signature verification at the ingestion boundary."
mutations:
  - action: add_component
    target_component: CMP-WEBHOOK-VERIFIER
    details: "Introduce WebhookSignatureVerifier in src/billing/verifier.py."
    expect:
      source_path: "src/billing/verifier.py"
      responsibility: "HMAC-SHA256 signature verification"
  - action: modify_contract
    target_contract: CTR-BILLING-API
    details: "Require X-Webhook-Signature and X-Webhook-Timestamp headers on POST /v1/usage/webhooks and bump contract version to 1.2.0."
    expect:
      version: "1.2.0"
  - action: extend_capability
    target_capability: CAP-BILL-001
    details: "Extend capability to cover HMAC webhook verification and bump capability version to 1.1.0."
    expect:
      version: "1.1.0"
  - action: add_invariant
    target_capability: CAP-BILL-001
    target_invariant_id: INV-BILL-WEBHOOK-SIG
    details: "Add INV-BILL-WEBHOOK-SIG invariant bound to tests/billing/test_webhooks.py::test_webhook_hmac_signature."
affects:
  containers:
    - CNT-BILLING-WORKER
  contracts:
    - CTR-BILLING-API
  capabilities:
    - CAP-BILL-001
blast_radius:
  impacted_containers:
    - CNT-BILLING-WORKER
  impacted_components:
    - CMP-WEBHOOK-VERIFIER
    - CMP-BILLING-INGESTOR
  invariants_to_enforce:
    - INV-BILL-IDEMPOTENCY
    - INV-BILL-WEBHOOK-SIG
---
= SpecDelta: Add Webhook Signature Verification Contract and Ingestor Component
```

### 2.3 Supported `mutations[].action` & Target Keys

| `action` | Required Target Key(s) | Verification Semantics (`eidos delta verify`) |
| :--- | :--- | :--- |
| `add_container` | `target_container: CNT-*` | Verifies the container exists in `eidos/architecture/containers/` and matches any `expect:` assertions. |
| `modify_container` | `target_container: CNT-*` | Verifies the container exists and satisfies `expect:` or shows digest modification evidence. |
| `remove_container` | `target_container: CNT-*` | Verifies the container has been removed (`ABSENT_AS_EXPECTED`). |
| `add_component` | `target_component: CMP-*` (optional `target_container: CNT-*`) | Verifies the component exists in `eidos/architecture/` and matches any `expect:` assertions. |
| `modify_component` | `target_component: CMP-*` | Verifies the component exists and satisfies `expect:` or shows digest modification evidence. |
| `remove_component` | `target_component: CMP-*` | Verifies the component has been removed (`ABSENT_AS_EXPECTED`). |
| `modify_architecture` | `target_architecture: ARCH-*` | Verifies the system architecture overview exists and satisfies `expect:` or shows digest modification evidence. |
| `add_contract` | `target_contract: CTR-*` | Verifies the contract specification exists in `eidos/architecture/contracts/`. |
| `modify_contract` | `target_contract: CTR-*` | Verifies the contract exists and satisfies `expect:` (e.g. `version`) or digest change. |
| `remove_contract` | `target_contract: CTR-*` | Verifies the contract specification has been removed. |
| `add_capability` | `target_capability: CAP-*` | Verifies the capability specification exists in `eidos/domains/<domain>/capabilities/`. |
| `extend_capability` | `target_capability: CAP-*` | Verifies the capability exists and satisfies `expect:` (`version`, `contains`) or digest change. |
| `deprecate_capability` | `target_capability: CAP-*` | Verifies the capability's `status` is `deprecated` or removed. |
| `add_invariant` | `target_invariant_id: INV-*` (+ `target_capability: CAP-*`) | Verifies the invariant ID exists in the target capability's `invariants`. |
| `patch_invariant` | `target_invariant_id: INV-*` (+ `target_capability: CAP-*`) | Verifies the invariant exists on the capability and satisfies `expect:` (`rule`, `test_binding`) or digest change. |
| `remove_invariant` | `target_invariant_id: INV-*` | Verifies the invariant has been removed from all specifications. |
| `add_policy` / `patch_policy` | `target_policy: POL-*` | Verifies the policy exists in `eidos/policies/` and satisfies `expect:` (`rule`, `severity`) or digest change. |

### 2.4 Declarative `expect:` Post-Condition Keys
Adding `expect:` makes `eidos delta verify` branch-independent and self-verifying:
* `version: "1.2.0"` — exact or substring match on target spec `version`.
* `status: "active"` — exact match on target spec `status`.
* `source_path: "src/..."` — exact or list membership match on component `source_path` / container `package_path`.
* `responsibility: "substring"` — substring match against inline or standalone component `responsibility`.
* `rule: "substring"` — substring match against policy or invariant `rule`.
* `test_binding: "tests/...::test_..."` — exact or substring match on an invariant's `test_binding`.
* `contains: "substring"` — verifies that the raw specification file text contains the given substring.
* Any frontmatter key (`<key>: <expected_value>`) — checks equality, substring containment (for string values), or list membership (when the target field is a YAML list).

---

## 3. Staff Systems Architect 5-Dimension Review (`eidos-review`)

When asked to review a SpecDelta (`DLT-*`), a branch/PR diff, or an architectural proposal, conduct a structured **5-Dimension Architectural Audit**:

### Pre-Review Automated Diagnostics
Always run these deterministic checks first:
```bash
eidos validate eidos --json
eidos drift --uncommitted --json
eidos index blast-radius <TARGET_ENTITY_ID> --depth 2 --json
# If reviewing one or more SpecDeltas:
eidos delta verify <DLT-ID> --json
eidos delta audit <DLT-ID> --strict
```

### The 5 Audit Dimensions

1. **Dimension 1: Blast Radius & Boundary Integrity**
   * Do all touched source files map cleanly to declared containers (`CNT-*`) and components (`CMP-*`)?
   * Are there any undeclared cross-container imports, circular dependencies, or leaky abstractions?
   * Does the SpecDelta's `blast_radius.impacted_containers` and `impacted_components` match the actual transitive neighborhood from `eidos index blast-radius`?
2. **Dimension 2: Interface Contract & SemVer Compatibility (`CTR-*`)**
   * If a CLI command, REST endpoint, event payload, or schema changed, was the corresponding `CTR-*` specification updated?
   * Does the contract `version:` follow SemVer 2.0.0 (`MAJOR` bump for breaking changes, `MINOR` for backward-compatible additions, `PATCH` for clarifications)?
3. **Dimension 3: Governance Policies & Invariant Traceability (`POL-*`, `INV-*`)**
   * Does the change violate any active system policy in `eidos/policies/` (`POL-001` through `POL-004`, etc.)?
   * Does every new or modified capability behavior have a corresponding `INV-*` invariant with a valid `test_binding: file::func`?
   * Does the bound test actually assert the invariant rule rather than a trivial no-op?
4. **Dimension 4: Workflow Mode & SpecDelta Completeness**
   * Do every `modify_*`, `extend_*`, and `patch_*` mutation in `DLT-*` include verifiable `expect:` post-conditions so `eidos delta verify` can prove application deterministically?
   * Are sequencing dependencies between multiple proposed SpecDeltas documented?
5. **Dimension 5: Living Specification Conformance & Decision Capture**
   * Are there any prohibited standalone ADR files?
   * Are non-obvious design trade-offs and rejected alternatives captured in a `decisions:` (`DEC-*`) block on the owning specification?
   * Do inline vs. promoted components obey `INV-COMPONENT-CENTRALIZATION-MANDATE` (no duplicated metadata alongside `ref:`)?

### Structured Review Report Format

Present your review findings using this concise template:

```markdown
## Architectural Review: <Target DLT-ID or Branch>
**Verdict**: ✅ APPROVED | ⚠️ APPROVED WITH REVISIONS | ❌ CHANGES REQUESTED

### Automated Gate Status
- **Spec Validation (`eidos validate`)**: PASS / FAIL
- **Drift Gate (`eidos drift`)**: CLEAN / DRIFT DETECTED
- **Delta Verification (`eidos delta verify`)**: PASS / FAIL

### Findings by Severity
- **[BLOCKER]**: Must be fixed before merging or applying (schema errors, broken invariants, missing `expect:` post-conditions, undeclared blast radius).
- **[WARNING]**: Architectural smell, missing `DEC-*` rationale, or incomplete SemVer bump.
- **[NIT]**: Minor documentation clarity or naming improvement.
```
