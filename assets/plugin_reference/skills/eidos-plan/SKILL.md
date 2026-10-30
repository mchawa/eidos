---
name: eidos-plan
description: Orchestrate technical discovery spikes (EXP-*), the SpecDelta architectural mutation lifecycle (DLT-*), and Staff Systems Architect 5-dimension reviews in Planned Mode. Use when formulating hypotheses, running pre-mutation impact analysis (eidos context, eidos index search, eidos index blast-radius, eidos index find-symbol), proposing or applying SpecDeltas (create, verify, audit, apply, changelog), or reviewing PRs and architectural changes.
---
# Eidos Planning, SpecDeltas & Architectural Review (`eidos-plan`)

This skill governs **Planned Mode** workflows in Eidos:
1. **Technical Explorations (`EXP-*`)**: Hypothesis-driven spikes when requirements or technical feasibility are uncertain.
2. **SpecDelta Lifecycle (`DLT-*`)**: Formal architectural mutations across product, architecture, and design specifications.
3. **Staff Architectural Review**: Rigorous 5-dimension audit of SpecDeltas, PR diffs, and blast radius.

---

## 1. Technical Spikes & Feasibility Discovery (`EXP-*`)

When an engineering problem has high uncertainty (e.g., evaluating storage engines, benchmarking a protocol, or prototyping an algorithm), search for prior spikes first and start with an **Exploration** (`eidos/explorations/exp-yyyy-nnn.adoc`) before committing to a SpecDelta.

```text
proposed ──► in_progress ──► adopted   (Graduates to SpecDelta(s) DLT-* via decision.resulting_deltas)
                         └──► discarded (Preserved in Git so future agents never repeat failed approaches)
```

```bash
# 1. Search prior explorations, decisions, and specs before starting a new spike
eidos index search "outbox webhook" --json
eidos context -t "webhook dispatch and outbox throughput" --tier deep --json

# 2. Scaffold a new Exploration spec
eidos spec create exploration EXP-2026-001 --title "Evaluate Outbox Pattern vs Direct Webhook Dispatch"
```

```yaml
---
type: product/exploration
id: EXP-2026-001
title: "Evaluate Transactional Outbox vs Direct Webhook Dispatch"
status: in_progress # proposed | in_progress | adopted | discarded

decision:
  outcome: adopted # adopted | discarded | pivoted
  resulting_deltas:
    - DLT-2026-015
---
= Exploration: Evaluate Transactional Outbox vs Direct Webhook Dispatch

== 1. Hypothesis & Exit Criteria
* *Hypothesis*: A PostgreSQL transactional outbox with `SKIP LOCKED` polling can deliver 5,000 events/sec with p99 <50ms without introducing Kafka.
* *Exit Criteria*: Max duration 2 days; sustained >=5,000 events/sec throughput and p99 <50ms dispatch latency with zero lost events on worker kill.

== 2. Questions to Answer
* Does `SKIP LOCKED` batch polling sustain 5,000 events/sec without lock contention on the ledger table?
* What is the recovery time after a downstream webhook endpoint outage?

== 3. Spikes & Prototypes
* *pg-outbox-benchmark*: Benchmark 4 concurrent worker processes polling 100k queued billing events.

== 4. Outcome & Learnings
* `SKIP LOCKED` batch size 250 achieved 6,400 events/sec at p99 18ms with zero duplicate deliveries.
```

* **If `adopted`**: Scaffold a SpecDelta (`eidos delta create --slug --title "..."`), set `decision.outcome: adopted` and `decision.resulting_deltas: [<DLT-ID>]`, and record the winning rationale + `rejected_alternatives` in the owning spec's `decisions:` block.
* **If `discarded`**: Set `status: discarded` and `decision.outcome: discarded`, and record empirical failure reasons in the AsciiDoc body so future searches (`eidos index search`) surface the prior result.

---

## 2. SpecDelta Mutation Lifecycle (`DLT-*`)

In **Planned Mode**, any change that adds/removes a container (`CNT-*`) or component (`CMP-*`), alters a contract (`CTR-*`), adds/extends a capability (`CAP-*`), or modifies a policy (`POL-*`) is governed by a **SpecDelta** (`eidos/deltas/dlt-*.adoc`).

### 2.1 End-to-End SpecDelta Workflow

```bash
# 1. Discover affected entities, code symbols, and transitive blast radius BEFORE authoring mutations
eidos context -t "webhook signature verification" --tier deep --json
eidos index search "webhook signature" --json
eidos index find-symbol "WebhookSignatureVerifier" --json
eidos index blast-radius CNT-BILLING-API --depth 2 --json

# 2. Scaffold a new SpecDelta (automatically generates the collision-free ID, title slug, 4-hex suffix,
#    and vcs.branch / vcs.base_commit; never hand-craft the ID—all delta commands accept
#    unambiguous short prefixes like DLT-2026-015)
eidos delta create \
  --title "Add Webhook Signature Verification Contract" \
  --kind feature

# 3. Inspect active SpecDeltas or transition lifecycle status (proposed | in_review | approved | applied | rejected)
eidos delta list --status active --json
eidos delta status DLT-2026-015 in_review

# 4. Edit the created eidos/deltas/dlt-*.adoc file to declare mutations, structured expect post-conditions,
#    and the step-by-step Implementation Plan in the AsciiDoc prose body
#    (Run `eidos spec schema delta` to inspect exact SpecDelta frontmatter keys and closed sub-schemas)

# 5. Implement code changes, update living specs in eidos/, and bind tests

# 6. Verify all target entities, baseline modification evidence, and expect: post-conditions
eidos delta verify DLT-2026-015 --json

# 7. Reconcile and apply the SpecDelta (verifies mutations and sets status: applied)
eidos delta apply DLT-2026-015

# 8. If removing/deprecating entities, audit for dangling references; optionally compile CHANGELOG.md or archive older deltas
eidos delta audit DLT-2026-015 --strict --json
eidos delta changelog
eidos delta archive --keep-recent 15 --dry-run --json
```

### 2.2 SpecDelta Frontmatter & Schema Rules

> **Critical Schema Rules (`delta.schema.yaml`)**:
> 1. **Composable `operation` + `target` is the Sole Mutation Contract**: Declare each mutation with `operation: add | modify | remove | deprecate` and `target: <ENTITY-ID>`. Never add `parent:`, `affects:`, or `blast_radius:` blocks to SpecDelta YAML frontmatter (`additionalProperties: false` rejects them).
> 2. **Closed `expect:` Schema**: `expect:` only permits `body:` (`contains`, `not_contains`) and `frontmatter:` (`source_path`, `rule`, `test_binding`, `status`, `severity`, `version`, `category`, plus nested dot-path keys). Flat top-level keys under `expect:` are rejected.

```yaml
---
type: delta/mutation
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
    details: "Introduce WebhookSignatureVerifier in src/billing/verifier.py."
    expect:
      frontmatter:
        source_path: "src/billing/verifier.py"
      body:
        contains:
          - "HMAC-SHA256 signature verification"
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
    details: "Add INV-BILL-WEBHOOK-SIG invariant bound to tests/billing/test_webhooks.py::test_webhook_hmac_signature."
---
= SpecDelta: Add Webhook Signature Verification Contract and Ingestor Component
:toc: left

== Problem Statement
Prevent spoofed billing webhooks by enforcing HMAC-SHA256 timestamped signature verification at the ingestion boundary.

== Proposed Specification Changes
* Introduce `CMP-WEBHOOK-VERIFIER` (`WebhookSignatureVerifier` in `src/billing/verifier.py`) under `CNT-BILLING-API`.
* Require `X-Webhook-Signature` and `X-Webhook-Timestamp` headers on `POST /v1/usage/webhooks` in `CTR-BILLING-API` (`1.2.0`).
* Extend `CAP-BILL-001` (`1.1.0`) with `INV-BILL-WEBHOOK-SIG`.

== Implementation Plan
1. **Core Verifier Component**: Implement `WebhookSignatureVerifier` in `src/billing/verifier.py` with constant-time HMAC-SHA256 comparison and 5-minute replay window enforcement.
2. **Route & Contract Integration**: Wire `WebhookSignatureVerifier` into `POST /v1/usage/webhooks` and update `CTR-BILLING-API` and `CNT-BILLING-API` specifications.
3. **Invariant & Test Verification**: Add `tests/billing/test_webhooks.py::test_webhook_hmac_signature` referencing `INV-BILL-WEBHOOK-SIG`, then run `eidos delta verify` and `eidos delta apply`.
```

### 2.3 Composable `operation` + `target` Matrix

| Entity Category (`target` Prefix) | Supported `operation` Values |
| :--- | :--- |
| **System (`SYS-*`) & Architecture (`ARCH-*`)** | `add`, `modify` |
| **Containers (`CNT-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Components (`CMP-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Contracts (`CTR-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Domains (`DOM-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Capabilities (`CAP-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Invariants (`INV-*`)** | `add`, `modify`, `remove` |
| **Personas (`PER-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Policies (`POL-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Design Systems (`DS-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **UI Components (`UIC-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Clients (`CLT-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Screens (`SCR-*`)** | `add`, `modify`, `deprecate`, `remove` |
| **Flows (`FLOW-*`)** | `add`, `modify`, `deprecate`, `remove` |

### 2.4 Structured `expect:` Post-Conditions & `tombstone:` Audit Exemptions

For `add` and `modify` mutations, `eidos delta verify` checks both baseline digest modification and `expect:` assertions:
* **`expect.frontmatter`**:
  * `version: "1.2.0"` — SemVer string match.
  * `status: "active"` — lifecycle status match (`draft`, `planned`, `active`, `deprecated`, `retired`).
  * `severity: "blocking"` — policy severity match (`blocking`, `warning`, `advisory`).
  * `source_path: "src/..."` (or list of paths) — exact or list-subset match on `source_path`.
  * `rule: "substring"` — substring match on invariant `rule`.
  * `test_binding: "tests/...::test_..."` — invariant `test_binding` match (`file::func`, `N/A`, or `TBD`).
  * `category: "security"` — policy `category` match.
  * **Any frontmatter key or dot-separated nested path** (e.g., `technical.components: ["CMP-WEBHOOK-VERIFIER"]`, `decisions.id: ["DEC-BILL-002"]`, `invariants.id: ["INV-BILL-WEBHOOK-SIG"]`, or `custom: { <key>: <expected_value> }`) — equality, substring, or list-subset match across top-level keys, nested dicts, and arrays of dicts.
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
eidos context -c <TARGET_ENTITY_ID> --tier deep --json
eidos index blast-radius <TARGET_ENTITY_ID> --depth 2 --json
eidos index find-symbol <MODIFIED_SYMBOL> --json
eidos delta verify <DLT-ID> --json
eidos delta audit <DLT-ID> --strict --json
```

1. **Blast Radius & Boundary Integrity**: Do all modified code files map to containers/components authorized in `mutations[].target` and its 1-hop ownership neighborhood? Are downstream dependents from `eidos index blast-radius` protected from unintended breakage or circular imports?
2. **Interface Contract & Public Surface Parity (`CTR-*`, `CMP-*`, `CNT-*`)**: Are added/removed CLI flags, endpoints, or RPCs bidirectionally synchronized in `CTR-*` specs with proper SemVer 2.0.0 bumps? Do `== Public Surface` sections in `CMP-*`/`CNT-*` specs match exact AST symbols, with zero stale references to removed symbols (`stale_removed_symbol`)?
3. **Governance Policies & Invariant Traceability (`POL-*`, `INV-*`)**: Does the change honor all active `POL-*` policies, and does every new/modified capability behavior have a genuine test bound via `test_binding: file::func`?
4. **SpecDelta Verification Rigor**: Does every `modify` mutation include structured `expect: { frontmatter, body }` post-conditions?
5. **Living Specification Purity & Decision Capture (`DEC-*`)**: Zero standalone ADRs; non-obvious trade-offs and `rejected_alternatives` recorded in `decisions:`; standalone `CMP-*` component files reside in `eidos/architecture/components/` and link bottom-up via `container: CNT-*`.

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
