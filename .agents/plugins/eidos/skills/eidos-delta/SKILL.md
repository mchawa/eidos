---
name: eidos-delta
description: Orchestrate the Eidos SpecDelta mutation lifecycle (DLT-*). Propose, author, verify, and reconcile semantic mutations against living specifications in Planned Mode.
---

# Eidos SpecDelta Lifecycle Orchestration (`eidos-delta`)

This skill equips AI agents to author, verify, and reconcile **SpecDeltas** (`type: delta/mutation`) when operating in **Planned Mode**.

---

## 1. When to Use SpecDelta (Planned Mode)

In Eidos, SpecDeltas are **optional architectural change plans**. You do **not** need a SpecDelta for everyday routine tasks or small fixes (which use Direct Mode with Git diffs).

### Ideal Scenarios for SpecDelta:
1. **Complex or Breaking Changes:** Multi-service migrations, new domains (`DOM-*`), breaking API contract changes (`CTR-*`), or system-wide security policies (`POL-*`).
2. **The Architect / Builder (Tiered Model) Pattern:**
   - A **strong reasoning model** activates deep thinking to analyze architectural impact and author the SpecDelta.
   - A human/lead reviews and approves the compact 40-line document.
   - A **faster/cheaper model** implements the code and tests against the deterministic delta checklist.
3. **Human Alignment:** When team leads or clients require pre-implementation design sign-off.

---

## 2. Authoring Workflow: Think Deeply Before Coding

When tasked with a complex feature or assigned a GitHub/GitLab issue:

1. **Think & Scaffold (`DLT-*.adoc`)**: Deliberate on architectural blast radius and scaffold with VCS linkage.
2. **Review & Alignment**: Lead or human architect reviews and signs off on the intent diff.
3. **Implement & Verify**: Update living specs, implement code, run test suite (`pytest`), and verify zero drift.
4. **Audit Deletions (`eidos delta audit`)**: If deleting entities, verify zero unacknowledged leaks.
5. **Reconcile (`eidos delta apply`)**: Reconcile in-place to update the baseline living specifications.

### Step 1: Think Deeply & Scaffold
Activate internal reasoning to deliberate on:
- *Which existing components (`CMP-*`) are impacted?*
- *What new capabilities (`CAP-*`) or interface contracts (`CTR-*`) are introduced?*
- *What policy invariants (`INV-*`) must be tested?*
- *What is the blast radius across personas and downstream modules?*

**Leverage Architectural Graph Tools:**
- Run `eidos index search "..."` to locate related specs and existing invariants.
- Run `eidos index blast-radius <component>` to compute the impacted components and contracts for `blast_radius.impacted_components`.

Scaffold the delta with VCS linkage:
```bash
eidos delta create DLT-2026-042 --title "Tenant Rate Limiting and Quota Enforcement" --kind feature_addition --motivation "Prevent noisy neighbor issues on multitenant clusters."
```

### Step 2: Author the SpecDelta Frontmatter
Edit `eidos/deltas/DLT-2026-042.adoc`:
```yaml
---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-042
title: "Tenant Rate Limiting and Quota Enforcement"
status: proposed  # proposed | in_review | approved | applied | rejected

vcs:
  issue: "#42"
  branch: "feat/tenant-quotas"

intent:
  kind: feature_addition
  motivation: "Prevent noisy neighbor problems across multitenant clusters."

mutations:
  - action: add_capability
    target_domain: DOM-TENANT-MGMT
    new_capability_id: CAP-TENANT-005
    details: "Introduce automated quota evaluation."
  - action: add_policy
    target_policy: POL-004-RATE-LIMIT-CEILING
    details: "Enforce 1000 req/min default per tier."

blast_radius:
  impacted_components:
    - CMP-GATEWAY-SERVICE
  invariants_to_enforce:
    - INV-QUOTA-BURST-001
---
= SpecDelta: Tenant Rate Limiting and Quota Enforcement
```

### Step 3: Implement & Verify
1. Update target living specs in `eidos/architecture/`, `eidos/policies/`, etc.
2. Implement code in `src/` and tests covering `INV-*` in `tests/`.
3. Verify with `pytest tests/` and `eidos validate eidos`.

### Step 3.5: Run Deletion / Deprecation Audit (If Deleting Entities)
If the SpecDelta contains any deletion or deprecation mutations (`remove_*`, `delete_*`, `deprecate_*`), run the context-aware blast-radius audit before applying:
```bash
eidos delta audit DLT-2026-042 --strict
# or inspect machine-readable JSON:
eidos delta audit DLT-2026-042 --json
```
Verify that active specifications, diagrams, and code contain zero unacknowledged leaks. If references were kept on purpose (e.g. uninstaller cleanup routines or negative tests), declare them in `tombstone.allowed_reasons` or annotate them with `# eidos:allow-retired`.

### Step 4: Reconcile Delta
Reconcile the delta in-place:
```bash
eidos delta apply DLT-2026-042
```
This sets `status: applied` in the frontmatter.

---

## 3. CLI Delta Commands

```bash
# 1. Create a new delta
eidos delta create DLT-2026-042 --title "..." --kind feature_addition

# 2. View lifecycle status dashboard
eidos delta status

# 3. List deltas
eidos delta list
eidos delta list --status active      # in-flight proposals
eidos delta list --status applied     # reconciled baselines

# 4. Audit deleted/deprecated entities for dangling references and leaks
eidos delta audit DLT-2026-042 --strict

# 5. Apply a completed delta
eidos delta apply DLT-2026-042

# 6. Delete an abandoned proposal
eidos delta delete DLT-2026-042
```
