---
name: eidos-delta
description: Orchestrate the Eidos SpecDelta mutation lifecycle (DLT-*). Propose, author, verify, and reconcile semantic mutations against living specifications.
---

# Eidos SpecDelta Lifecycle Orchestration (`eidos-delta`)

This skill equips AI agents to safely propose, author, execute, and reconcile semantic mutations against an Eidos living specification repository.

## The SpecDelta Lifecycle

In Eidos, code and specifications evolve together through **SpecDeltas** (`type: delta/mutation`).
SpecDeltas capture the *intent*, *mutations*, and *impact* of changes before implementation.

```
       ┌───────────┐      ┌────────────┐      ┌───────────┐      ┌───────────┐
       │  Propose  │ ───> │ Authoring  │ ───> │ Implement │ ───> │ Reconcile │
       │ (Scaffold)│      │  (Intent)  │      │ & Verify  │      │  (Apply)  │
       └───────────┘      └────────────┘      └───────────┘      └───────────┘
         DLT-*.adoc         Mutations            Pytest /            eidos
        in active/         & Invariants          Validate         delta apply
```

---

## 1. When to Create a SpecDelta

Always create a SpecDelta when:
- Adding a new product capability (`CAP-*`) or domain context (`DOM-*`).
- Introducing or altering an architectural component (`CMP-*`) or interface contract (`CTR-*`).
- Adding or modifying a system policy (`POL-*`) or invariant (`INV-*`).
- Performing non-trivial refactorings that shift module boundaries.

> [!NOTE]
> Minor typo corrections, non-functional documentation tweaks, and test fixture updates do not require a SpecDelta.

---

## 2. CLI Delta Commands

Use the Eidos CLI to manage deltas:

```bash
# 1. Scaffold a new active SpecDelta:
eidos delta create DLT-2026-002 --title "Payment Gateway Integration" --kind feature_addition --motivation "Enable Stripe payments"

# 2. List all active and applied deltas:
eidos delta list
eidos delta list --status proposed

# 3. Reconcile and apply a completed delta:
eidos delta apply DLT-2026-002

# 4. Safely delete a rejected or abandoned delta:
eidos delta delete DLT-2026-002
```

---

## 3. SpecDelta Schema Anatomy (`delta.schema.yaml`)

Every SpecDelta lives in `eidos/deltas/active/<delta-id>.adoc` and strictly satisfies `schemas/v1/delta/delta.schema.yaml`:

```yaml
---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-042
title: "Tenant Rate Limiting and Quota Enforcement"
status: proposed  # proposed | in_review | approved | applied | rejected

intent:
  kind: feature_addition  # feature_addition | bugfix | refactor | modification | feature_removal
  motivation: "Prevent noisy neighbor problems across multitenant clusters."

mutations:
  - action: add_capability
    target_domain: DOM-TENANT-MGMT
    new_capability_id: CAP-TENANT-005
    details: "Introduce automated quota evaluation."
  - action: add_policy
    target_policy: POL-004-RATE-LIMIT-CEILING
    details: "Enforce 1000 req/min default per tier."
  - action: patch_invariant
    target_capability: CAP-TENANT-001
    target_invariant_id: INV-QUOTA-BURST-001
    details: "Allow 20% burst over 10 seconds."

blast_radius:
  impacted_personas:
    - eidos/personas/workspace-admin.adoc
  impacted_components:
    - CMP-GATEWAY-SERVICE
  invariants_to_enforce:
    - INV-QUOTA-BURST-001
---
= SpecDelta: Tenant Rate Limiting and Quota Enforcement
:toc: left

== Problem Statement
Contextual explanation of why the change is necessary.

== Proposed Specification Changes
Detailed textual diffs, API endpoint updates, and policy amendments.
```

---

## 4. Reconciliation Workflow (Rule 3)

Once code is written and verified with passing invariant tests:
1. Update all target living specifications (`eidos/architecture/`, `eidos/domains/`, `eidos/policies/`) to incorporate the delta changes.
2. Run `eidos delta apply <delta-id>`:
   - Sets frontmatter `status: applied`.
   - Moves the file to `eidos/deltas/applied/<delta-id>.adoc`.
3. Validate repository integrity:
   ```bash
   eidos validate eidos
   ```
4. Commit both the living specs and the applied delta in the same git commit.
