---
name: eidos-plan
description: Orchestrate technical discovery spikes (EXP-*), the SpecDelta architectural mutation lifecycle (DLT-*), and Staff Systems Architect 5-dimension reviews in Planned Mode. Use when formulating hypotheses, proposing or applying SpecDeltas (create, verify, audit, apply, changelog), or reviewing PRs and architectural changes.
---
# Eidos Planning, SpecDeltas & Architectural Review (`eidos-plan`)

This skill governs **Planned Mode** workflows in Eidos:
1. **Technical Explorations (`EXP-*`)**: Hypothesis-driven spikes when requirements or technical feasibility are uncertain.
2. **SpecDelta Lifecycle (`DLT-*`)**: Formal architectural mutations across product, architecture, and design specifications.
3. **Staff Architectural Review**: Rigorous 5-dimension audit of SpecDeltas, PR diffs, and blast radius.

---

## 1. Technical Spikes & Feasibility Discovery (`EXP-*`)

When an engineering problem has high uncertainty (e.g., evaluating storage engines, benchmarking a protocol, or prototyping an algorithm), start with an **Exploration** (`eidos/explorations/exp-yyyy-nnn.adoc`) before committing to a SpecDelta.

```text
proposed ──► in_progress ──► adopted   (Graduates to a SpecDelta DLT-* via decision.resulting_delta_id)
                         └──► discarded (Preserved in Git so future agents never repeat failed approaches)
```

```bash
eidos spec create exploration EXP-2026-001 --title "Evaluate Outbox Pattern vs Direct Webhook Dispatch"
```

```yaml
---
type: product/exploration
schema: schemas/v1/product/exploration.schema.yaml
id: EXP-2026-001
title: "Evaluate Transactional Outbox vs Direct Webhook Dispatch"
status: in_progress # proposed | in_progress | adopted | discarded

hypothesis: "A PostgreSQL transactional outbox with SKIP LOCKED polling can deliver 5,000 events/sec with p99 <50ms without introducing Kafka."

questions_to_answer:
  - "Does SKIP LOCKED batch polling sustain 5,000 events/sec without lock contention on the ledger table?"
  - "What is the recovery time after a downstream webhook endpoint outage?"

spikes_to_run:
  - name: "pg-outbox-benchmark"
    target: "Benchmark 4 concurrent worker processes polling 100k queued billing events"

exit_criteria:
  max_duration: "2 days"
  benchmark: "Sustained >=5,000 events/sec throughput and p99 <50ms dispatch latency with zero lost events on worker kill."

decision:
  outcome: adopted # adopted | discarded | pivoted
  resulting_delta_id: DLT-2026-015
  learnings: "SKIP LOCKED batch size 250 achieved 6,400 events/sec at p99 18ms with zero duplicate deliveries."
---
= Exploration: Evaluate Transactional Outbox vs Direct Webhook Dispatch
```

* **If `adopted`**: Scaffold a SpecDelta (`eidos delta create --slug --title "..."`), set `decision.outcome: adopted` and `decision.resulting_delta_id: <DLT-ID>`, and record the winning rationale + `rejected_alternatives` in the owning spec's `decisions:` block.
* **If `discarded`**: Set `status: discarded` and `decision.outcome: discarded`, and record empirical failure reasons in `decision.learnings:` so future searches (`eidos index search`) surface the prior result.

---

## 2. SpecDelta Mutation Lifecycle (`DLT-*`)

In **Planned Mode**, any change that adds/removes a container (`CNT-*`) or component (`CMP-*`), alters a contract (`CTR-*`), adds/extends a capability (`CAP-*`), or modifies a policy (`POL-*`) is governed by a **SpecDelta** (`eidos/deltas/dlt-yyyy-nnn.adoc`).

### 2.1 End-to-End SpecDelta Workflow

```bash
# 1. Scaffold a new SpecDelta (ID defaults to collision-free 'auto' across all local/remote Git refs; --slug appends a title slug)
eidos delta create \
  --title "Add Webhook Signature Verification Contract" \
  --slug \
  --kind feature \
  --branch feat/webhook-signatures

# 2. Edit the created eidos/deltas/dlt-*.adoc file to declare mutations and structured expect post-conditions

# 3. Implement code changes, update living specs in eidos/, and bind tests

# 4. Verify all target entities, baseline modification evidence, and expect: post-conditions
eidos delta verify DLT-2026-015 --json

# 5. Reconcile and apply the SpecDelta (verifies mutations and sets status: applied)
eidos delta apply DLT-2026-015

# 6. If removing/deprecating entities, audit for dangling references; optionally compile CHANGELOG.md
eidos delta audit DLT-2026-015 --strict --json
eidos delta changelog
```

### 2.2 SpecDelta Frontmatter & Schema Rules

> **Critical Schema Rules (`delta.schema.yaml`)**:
> 1. **Composable `operation` + `target` (+ optional `parent` for `add`/`remove`) is the Sole Mutation Contract**: Declare each mutation with `operation: add | modify | remove | deprecate` and `target: <ENTITY-ID>`. Omit `parent` on `modify` mutations (the catalog already resolves existing entities by ID); only include `parent: <PARENT-ENTITY-ID>` when adding or removing child entities (`INV-*`, inline `CMP-*`, etc.). Never add `affects:` or `blast_radius:` blocks to SpecDelta YAML frontmatter (`additionalProperties: false` rejects them).
> 2. **Closed `expect:` Schema**: `expect:` only permits `body:` (`contains`, `not_contains`) and `frontmatter:` (`responsibility`, `source_path`, `rule`, `test_binding`, `status`, `severity`, `version`, `category`, `custom`). Flat top-level keys under `expect:` are rejected.

```yaml
---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-015
title: "Add Webhook Signature Verification Contract and Ingestor Component"
status: proposed # proposed | in_review | approved | applied | rejected
vcs:
  branch: feat/webhook-signatures
intent:
  kind: feature # feature | enabler | bug (for bug, pair with bug_classification: spec_violation | spec_gap and severity: low | medium | high | critical)
  motivation: "Prevent spoofed billing webhooks by enforcing HMAC-SHA256 timestamped signature verification at the ingestion boundary."
mutations:
  - operation: add
    target: CMP-WEBHOOK-VERIFIER
    parent: CNT-BILLING-API
    details: "Introduce WebhookSignatureVerifier in src/billing/verifier.py."
    expect:
      frontmatter:
        source_path: "src/billing/verifier.py"
        responsibility: "HMAC-SHA256 signature verification"
  - operation: modify
    target: CTR-BILLING-API
    details: "Require X-Webhook-Signature and X-Webhook-Timestamp headers on POST /v1/usage/webhooks and bump contract version to 1.2.0."
    expect:
      frontmatter:
        version: "1.2.0"
      body:
        contains:
          - "X-Webhook-Signature"
  - operation: modify
    target: CAP-BILL-001
    details: "Extend capability to cover HMAC webhook verification and bump capability version to 1.1.0."
    expect:
      frontmatter:
        version: "1.1.0"
  - operation: add
    target: INV-BILL-WEBHOOK-SIG
    parent: CAP-BILL-001
    details: "Add INV-BILL-WEBHOOK-SIG invariant bound to tests/billing/test_webhooks.py::test_webhook_hmac_signature."
---
= SpecDelta: Add Webhook Signature Verification Contract and Ingestor Component
```

### 2.3 Composable `operation` + `target` Matrix

| Entity Category (`target` Prefix) | Supported `operation` Values | Optional `parent` Scope (`add` / `remove` only; omit on `modify`) |
| :--- | :--- | :--- |
| **Architecture (`ARCH-*`)** | `modify` | — |
| **Containers (`CNT-*`)** | `add`, `modify`, `remove` | — |
| **Components (`CMP-*`)** | `add`, `modify`, `remove` | `CNT-*` (on `add` / `remove` of inline components) |
| **Contracts (`CTR-*`)** | `add`, `modify`, `remove` | `CNT-*` (on `add` / `remove`) |
| **Domains (`DOM-*`)** | `add`, `modify`, `remove` | — |
| **Capabilities (`CAP-*`)** | `add`, `modify`, `deprecate`, `remove` | `DOM-*` (on `add` / `remove`) |
| **Invariants (`INV-*`)** | `add`, `modify`, `remove` | `CAP-*`, `POL-*`, `CNT-*`, or `CMP-*` (required on `add`, optional on `remove`) |
| **Personas (`PER-*`)** | `add`, `modify`, `remove` | — |
| **Policies (`POL-*`)** | `add`, `modify`, `deprecate`, `remove` | — |
| **Design Systems (`DS-*`)** | `add`, `modify`, `remove` | — |
| **UI Components (`UIC-*`)** | `add`, `modify`, `remove` | `DS-*` (on `add` / `remove`) |
| **Clients (`CLT-*`)** | `add`, `modify`, `remove` | `CNT-*` (on `add` / `remove`) |
| **Screens (`SCR-*`)** | `add`, `modify`, `remove` | `CLT-*` (on `add` / `remove`) |
| **Flows (`FLOW-*`)** | `add`, `modify`, `remove` | `CLT-*` (on `add` / `remove`) |

### 2.4 Structured `expect:` Post-Conditions & `tombstone:` Audit Exemptions

For `add` and `modify` mutations, `eidos delta verify` checks both baseline digest modification and `expect:` assertions:
* **`expect.frontmatter`**:
  * `version: "1.2.0"` — SemVer string match.
  * `status: "active"` — lifecycle status match (`draft`, `planned`, `active`, `deprecated`, `retired`).
  * `severity: "blocking"` — policy severity match (`blocking`, `warning`, `advisory`).
  * `source_path: "src/..."` (or list of paths) — exact or list-subset match on `source_path`.
  * `responsibility: "substring"` — substring match on container/component `responsibility`.
  * `rule: "substring"` — substring match on invariant `rule`.
  * `test_binding: "tests/...::test_..."` — invariant `test_binding` match (`file::func`, `N/A`, or `TBD`).
  * `category: "security"` — policy `category` match.
  * `custom: { <key>: <expected_value> }` — equality, substring, or list-subset match for any other frontmatter field.
* **`expect.body`** (evaluated against comment-stripped AsciiDoc body):
  * `contains: "substring"` (or list of strings) — all substrings must appear in the body.
  * `not_contains: "substring"` (or list of strings) — none of the substrings may appear in the body.
* **`tombstone:` (Optional top-level block for `remove` / `deprecate` audits)**:
  When removing entities, declare `tombstone: { search_tokens: ["LegacySymbol"], allowed_reasons: [{ path: "CHANGELOG.md", reason: "Historical release note" }] }` if legitimate historical references need exemption during `eidos delta audit <ID> --strict`.

---

## 3. Staff Systems Architect 5-Dimension Review (`eidos-review`)

When asked to review a SpecDelta (`DLT-*`), branch/PR diff, or architectural proposal, run the deterministic diagnostics first and audit across 5 dimensions:

```bash
eidos validate eidos --json
eidos drift --uncommitted --json
eidos index blast-radius <TARGET_ENTITY_ID> --depth 2 --json
eidos delta verify <DLT-ID> --json
eidos delta audit <DLT-ID> --strict --json
```

1. **Blast Radius & Boundary Integrity**: Do all modified code files map to containers/components authorized in `mutations[].target` / `parent` and its 1-hop ownership neighborhood? Are downstream dependents from `eidos index blast-radius` protected from unintended breakage or circular imports?
2. **Interface Contract & SemVer Compatibility (`CTR-*`)**: Are added/removed CLI flags, endpoints, or RPCs bidirectionally synchronized in `CTR-*` specs with proper SemVer 2.0.0 bumps?
3. **Governance Policies & Invariant Traceability (`POL-*`, `INV-*`)**: Does the change honor all active `POL-*` policies, and does every new/modified capability behavior have a genuine test bound via `test_binding: file::func`?
4. **SpecDelta Verification Rigor**: Does every `modify` mutation include structured `expect: { frontmatter, body }` post-conditions?
5. **Living Specification Purity & Decision Capture (`DEC-*`)**: Zero standalone ADRs; non-obvious trade-offs and `rejected_alternatives` recorded in `decisions:`; promoted `CMP-*` components obey `INV-COMPONENT-CENTRALIZATION-MANDATE` (standalone component files in `eidos/architecture/components/` link bottom-up via `container: CNT-*` without duplicating entries in the parent container's `components[]`).

### Review Report Template

```markdown
## Architectural Review: <Target DLT-ID or Branch>
**Verdict**: ✅ APPROVED | ⚠️ APPROVED WITH REVISIONS | ❌ CHANGES REQUESTED

### Automated Gate Status
- **Spec Validation (`eidos validate`)**: PASS / FAIL
- **Drift Gate (`eidos drift`)**: CLEAN / DRIFT DETECTED
- **Delta Verification (`eidos delta verify`)**: PASS / FAIL

### Findings by Severity
- **[BLOCKER]**: Must be fixed before merging/applying (schema errors, broken invariants, missing `expect:` post-conditions, unauthorized blast radius).
- **[WARNING]**: Architectural smell, missing `DEC-*` rationale, or incomplete SemVer bump.
- **[NIT]**: Minor documentation clarity or naming improvement.
```
