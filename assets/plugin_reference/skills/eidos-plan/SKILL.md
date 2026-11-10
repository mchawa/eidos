---
name: eidos-plan
description: "Planned-mode workflows: technical explorations (EXP-*), the SpecDelta lifecycle (DLT-*: create, verify, audit, apply), expect: post-conditions, and architectural reviews. Load for new or removed containers/components, breaking contract changes, cross-domain features, spikes, or PR and delta reviews."
---
# Eidos Planning, SpecDeltas & Architectural Review (`eidos-plan`)

This skill governs **Planned Mode** workflows in Eidos:
1. **Technical Explorations (`EXP-*`)**: Hypothesis-driven spikes when requirements or technical feasibility are uncertain.
2. **SpecDelta Lifecycle (`DLT-*`)**: Formal architectural mutations across product, architecture, and design specifications.
3. **Staff Architectural Review**: Rigorous 5-dimension audit of SpecDeltas, PR diffs, and blast radius.

---

## 1. Technical Spikes & Feasibility Discovery (`EXP-*`)

When feasibility is uncertain (e.g., storage engines, protocols, or algorithms), search prior spikes first and start an **Exploration** (`eidos/explorations/exp-yyyy-nnn.adoc`) before authoring a SpecDelta.

```text
proposed ──► in_progress ──► adopted   (Graduates to DLT-* via decision.resulting_deltas)
                         └──► discarded (Kept in Git so agents never repeat failed spikes)
```

```bash
eidos index search "outbox webhook" --json
eidos context -t "webhook dispatch and outbox throughput" --tier deep --json
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
* *Hypothesis*: PostgreSQL `SKIP LOCKED` outbox polling sustains >=5,000 events/sec at p99 <50ms without Kafka.
* *Exit Criteria*: Max 2 days; >=5,000 events/sec, p99 <50ms, zero lost events on worker kill.

== 2. Questions to Answer
* Does `SKIP LOCKED` batch polling avoid ledger lock contention and recover after endpoint outages?

== 3. Spikes & Prototypes
* *pg-outbox-benchmark*: 4 workers polling 100k queued billing events.

== 4. Outcome & Learnings
* Batch size 250 sustained 6,400 events/sec at p99 18ms with zero duplicates.
```

* **If `adopted`**: Scaffold a SpecDelta (`eidos delta create --slug --title "..."`), set `decision.outcome: adopted` + `decision.resulting_deltas: [<DLT-ID>]`, and record rationale + `rejected_alternatives` in the owning spec's `decisions:`.
* **If `discarded`**: Set `status: discarded` + `decision.outcome: discarded`, and record empirical failure reasons in the body for future `eidos index search` queries.

---

## 2. SpecDelta Mutation Lifecycle (`DLT-*`)

In **Planned Mode**, adding/removing `CNT-*`/`CMP-*`, altering `CTR-*`, adding/extending `CAP-*`, or modifying `POL-*` is governed by a **SpecDelta** (`eidos/deltas/dlt-*.adoc`).

### 2.1 End-to-End SpecDelta Workflow

```bash
# 1. Discover affected entities, symbols, and blast radius BEFORE authoring mutations
eidos context -t "webhook signature verification" --tier deep --json
eidos index search "webhook signature" --json
eidos index find-symbol "WebhookSignatureVerifier" --json
eidos index blast-radius CNT-BILLING-API --depth 2 --json

# 2. Scaffold SpecDelta (auto-generates ID, slug, 4-hex suffix, vcs.branch/base_commit; commands accept short prefixes)
eidos delta create --title "Add Webhook Signature Verification Contract" --kind feature

# 3. Inspect active SpecDeltas or transition status (proposed | in_review | approved | applied | rejected)
eidos delta list --status active --json
eidos delta status DLT-2026-015 in_review

# 4. Populate mutations + Implementation Plan, then slice EVERY planned file or QUOTED glob bottom-up
#    (Run `eidos spec schema delta` for exact keys; run §3 5-Dimension self-review BEFORE presenting)
eidos context -f "apps/*/Dockerfile" --tier guardrails --json
eidos context -f src/billing/verifier.py,src/billing/routes.py --tier guardrails --json

# 5. Implement code, update living specs in eidos/, bind tests, then verify, apply, and audit
eidos delta verify DLT-2026-015 --json
eidos delta apply DLT-2026-015
eidos delta audit DLT-2026-015 --strict --json
eidos delta changelog
eidos delta archive --keep-recent 15 --dry-run --json
```

### 2.2 SpecDelta Frontmatter, Plan Reconciliation & Schema Rules

> **Critical Authoring & Schema Rules (`delta.schema.yaml`)**:
> 1. **Composable `operation` + `target` Only**: Declare `operation: add | modify | remove | deprecate` and `target: <ENTITY-ID>` (`CNT-*`, `CMP-*`, `CTR-*`, `CAP-*`, `INV-*`, `DOM-*`, `PER-*`, `POL-*`, `DS-*`/`UIC-*`/`CLT-*`/`SCR-*`/`FLOW-*`). Never add `parent:`, `affects:`, or `blast_radius:` (`additionalProperties: false`).
> 2. **Closed `expect:` Schema**: Only `body:` and `frontmatter:` sub-blocks are allowed (§2.3); flat keys under `expect:` fail validation.
> 3. **Bottom-Up `== Implementation Plan` Reconciliation**: Slice every file or **quoted** glob in `== Implementation Plan` (`eidos context -f "apps/*/Dockerfile" --tier guardrails --json` or `-f a,b`; never unquoted shell globs). Declare 1-hop owners (`CMP-*`, parent `CNT-*`, or bound `CTR-*`/`CAP-*`) for structural code or boundary/runtime changes, plus `DS-*`/`CLT-*`/`UIC-*`/`SCR-*`/`FLOW-*` on design-token or `ui_contract` edits. `SYS-*` and `ARCH-*` cover only *unmapped* repository files—never `CNT-*`/`CMP-*` `source_path` files.
> 4. **Avoid `UNMODIFIED` Catch-22s**: Never list a `CNT-*`/`CMP-*` in `mutations:` unless its `.adoc` file changes on disk (`eidos delta verify` rejects unchanged specs as `UNMODIFIED`). Annotate unchanged parent-covered or non-structural files inline in `== Implementation Plan` as `(covered via parent CNT-*)` or `(body-only; no spec mutation)` (note: `eidos drift --strict` requires test updates on body-only code edits).
> 5. **`INV-*` Test Pairing, Concrete Targets & Pre-Presentation Self-Review**: Pair every planned/modified test (`tests/...::test_...`) or capability/policy rule with an `INV-*` (`add`/`modify`) mutation (`expect.frontmatter: { rule, test_binding }` + parent `invariants.id`/`technical.invariants.id`), cite concrete `INV-*`/`POL-*` limits (word ceilings, `POL-*` per-file coverage thresholds, CLI snapshots), verify planned CLI flags, workspace paths/symlinks, and in-file skill templates against the live repo, and run §3's 5-Dimension Review autonomously before presenting any SpecDelta.

```yaml
---
type: delta/mutation
id: DLT-2026-015
title: "Add Webhook Signature Verification Contract and Ingestor Component"
status: proposed # proposed | in_review | approved | applied | rejected
vcs:
  branch: feat/webhook-signatures
intent:
  kind: feature # feature | enabler | bug (bug requires bug_classification + severity)
  motivation: "Prevent spoofed billing webhooks via HMAC-SHA256 timestamped signature verification."
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
    details: "Require X-Webhook-Signature and X-Webhook-Timestamp headers on POST /v1/usage/webhooks (1.2.0)."
    expect:
      frontmatter:
        version: "1.2.0"
      body:
        contains:
          - "X-Webhook-Signature"
  - operation: modify
    target: CAP-BILL-001
    details: "Extend CAP-BILL-001 (1.1.0) with INV-BILL-WEBHOOK-SIG."
    expect:
      frontmatter:
        version: "1.1.0"
        technical.invariants.id:
          - "INV-BILL-WEBHOOK-SIG"
      body:
        contains:
          - "INV-BILL-WEBHOOK-SIG"
  - operation: add
    target: INV-BILL-WEBHOOK-SIG
    details: "Bind INV-BILL-WEBHOOK-SIG to tests/billing/test_webhooks.py::test_webhook_hmac_signature."
    expect:
      frontmatter:
        rule: "HMAC-SHA256"
        test_binding: "tests/billing/test_webhooks.py::test_webhook_hmac_signature"
---
= SpecDelta: Add Webhook Signature Verification Contract and Ingestor Component
:toc: left

== Problem Statement
Prevent spoofed billing webhooks via HMAC-SHA256 timestamped signature verification at ingestion.

== Proposed Specification Changes
* Introduce `CMP-WEBHOOK-VERIFIER` (`WebhookSignatureVerifier` in `src/billing/verifier.py`) under `CNT-BILLING-API`.
* Require `X-Webhook-Signature` and `X-Webhook-Timestamp` on `POST /v1/usage/webhooks` in `CTR-BILLING-API` (`1.2.0`).
* Extend `CAP-BILL-001` (`1.1.0`) with `INV-BILL-WEBHOOK-SIG`.

== Implementation Plan
1. **Core Verifier Component**: Implement `WebhookSignatureVerifier` in `src/billing/verifier.py` with constant-time HMAC-SHA256 and 5-minute replay window checks.
2. **Route & Contract Integration**: Wire `WebhookSignatureVerifier` into `POST /v1/usage/webhooks` and update `CTR-BILLING-API` (`CNT-BILLING-API` covered via `CMP-WEBHOOK-VERIFIER`).
3. **Invariant & Test Verification**: Add `tests/billing/test_webhooks.py::test_webhook_hmac_signature` referencing `INV-BILL-WEBHOOK-SIG`, then run `eidos delta verify` and `eidos delta apply`.
```

### 2.3 Composable `operation` + `target` & `expect:` Post-Conditions

Every entity supports `operation: add | modify | deprecate | remove`, except `SYS-*`/`ARCH-*` (`add`, `modify` only) and `INV-*` (`add`, `modify`, `remove`). For `add` and `modify`, `eidos delta verify` checks baseline digest modification and `expect:` assertions:
* **`expect.frontmatter`**: Match `version` (SemVer), `status` (`draft | planned | active | deprecated | retired`), `severity` (`blocking | warning | advisory`), `category`, `source_path` (path or list subset), `rule` (substring), `test_binding` (`file::func`, `N/A`, `TBD`), or **any frontmatter key / dot-separated nested path** (e.g., `technical.components: ["CMP-WEBHOOK-VERIFIER"]`, `decisions.id: ["DEC-BILL-002"]`, `invariants.id: ["INV-BILL-WEBHOOK-SIG"]`, `custom: { k: v }`).
* **`expect.body`** (comment-stripped AsciiDoc): `contains` and `not_contains` (string or list of substrings).
* **`tombstone:` (Optional top-level block on `remove`/`deprecate`)**: `tombstone: { search_tokens: ["LegacySymbol"], allowed_reasons: [{ path: "CHANGELOG.md", reason: "Historical note" }] }` exempts historical references in `eidos delta audit <ID> --strict`.

---

## 3. Staff Systems Architect 5-Dimension Review (`eidos-review`)

Run this 5-dimension audit **autonomously before presenting any authored or updated SpecDelta** (fixing every `[BLOCKER]`, `[WARNING]`, and `[NIT]` first), and whenever asked to review a SpecDelta (`DLT-*`), branch/PR diff, or architectural proposal:

```bash
eidos drift --uncommitted --json
eidos context -f "<PLANNED_FILE_OR_QUOTED_GLOB>" --tier guardrails --json
eidos context -c <TARGET_ENTITY_ID> --tier deep --json
eidos index blast-radius <TARGET_ENTITY_ID> --depth 2 --json
eidos index find-symbol <MODIFIED_SYMBOL> --json
eidos delta verify <DLT-ID> --json
eidos delta audit <DLT-ID> --strict --json
```

1. **Blast Radius & 1-Hop Boundary Integrity**: Does every structural file or glob in `== Implementation Plan` and `git diff` resolve via `eidos context -f` to `mutations[].target` or its 1-hop neighborhood (`CMP-*`, parent `CNT-*`, or bound `CTR-*`/`CAP-*`, plus `DS-*`/`CLT-*`/`UIC-*`/`SCR-*`/`FLOW-*` on design-token/`ui_contract` edits; `SYS-*`/`ARCH-*` cover only unmapped files)? Are unchanged parent-covered or non-structural edits annotated inline (`(covered via parent CNT-*)` / `(body-only; no spec mutation)`) instead of causing `UNMODIFIED` failures?
2. **Interface Contract & Public Surface Parity (`CTR-*`, `CMP-*`, `CNT-*`)**: Are added/removed CLI flags, endpoints, or RPCs bidirectionally synchronized in `CTR-*` specs with proper SemVer 2.0.0 bumps? Do `== Public Surface` sections in `CMP-*`/`CNT-*` specs match exact AST symbols, with zero stale references to removed symbols (`stale_removed_symbol`)?
3. **Governance Policies & Invariant Pairing (`POL-*`, `INV-*`)**: Are active `POL-*`/`INV-*` targets cited concretely (word ceilings, `>90%` per-file coverage), and is every new/modified test (`tests/...::test_...`) or capability/policy rule paired with an `INV-*` mutation (`expect.frontmatter: { rule, test_binding }`)?
4. **SpecDelta Verification Rigor & Live Plan Grounding**: Do `add`/`modify` targets declare structured `expect: { frontmatter, body }` post-conditions, are planned CLI flags/snapshots and workspace paths/symlinks verified against the live repo, and are few-shot templates inside edited skills kept compliant?
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
