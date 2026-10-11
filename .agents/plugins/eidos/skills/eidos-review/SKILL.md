---
name: eidos-review
description: Perform rigorous architectural reviews of SpecDeltas, PR diffs, contract changes, and invariant test coverage like a Staff Systems Architect.
---

# Eidos Architectural & Spec Reviewer (`eidos-review`)

This skill empowers AI agents to operate as an autonomous **Staff Systems Architect and Technical Reviewer**, critiquing pull requests, SpecDeltas, and codebase mutations against living specifications and architectural policies.

---

## 1. When to Use This Skill

Activate this skill when:
- The user prompts: *"Review this PR"*, *"Audit our SpecDelta"*, *"Check this change for architectural violations"*, or *"Review my code changes against specs"*.
- Acting as an automated PR gate reviewer or pre-merge sanity check.
- Validating whether a proposed mutation introduces breaking contract changes or untracked blast radius.

---

## 2. The 5-Dimension Architectural Audit

When reviewing a branch, PR, or working tree, audit across these 5 dimensions:

```
                      ┌─────────────────────────────────┐
                      │    5-Dimension Review Matrix    │
                      └───────────────┬─────────────────┘
                                      │
        ┌─────────────┬───────────────┼───────────────┬─────────────┐
        ▼             ▼               ▼               ▼             ▼
   Blast Radius   Contract SemVer  Policies &   Workflow Mode  Spec & Link
   & Boundaries   Compatibility   Invariants     Compliance     Integrity
```

### Dimension 1: Blast Radius & Boundary Integrity
* Inspect touched code files and map them to their owning component manifests ([`CMP-*`](file:///home/MTS/marwan.chawa/WS/eidos/eidos/architecture/components)).
* **Red Flag:** A pull request touches files across 4 different components when the SpecDelta only declared mutations for 1 component.
* **Red Flag:** Introduction of circular module dependencies or unauthorized cross-package imports violating architectural boundaries.

### Dimension 2: Interface Contract & Protocol Compatibility (SemVer)
* Inspect interface contract modifications ([`CTR-*`](file:///home/MTS/marwan.chawa/WS/eidos/eidos/architecture/contracts)):
  - CLI commands (`cli_args`): Were required flags added? Were existing arguments renamed or removed?
  - REST APIs (`openapi`): Were required request body fields added? Were response status codes changed?
  - gRPC / Protobuf (`protobuf`): Were field tag numbers altered or types changed?
* **Verdict:** Flag any breaking interface change that lacks a corresponding major version bump or backward-compatibility deprecation plan.

### Dimension 3: Policy & Invariant Guardrails
* Verify compliance against repository policies ([`POL-*`](file:///home/MTS/marwan.chawa/WS/eidos/eidos/policies)):
  - Does the PR violate [`POL-001`](file:///home/MTS/marwan.chawa/WS/eidos/eidos/policies/POL-001-git-as-single-source-of-truth.adoc) (Git as single source of truth)?
  - Does the PR violate [`POL-002`](file:///home/MTS/marwan.chawa/WS/eidos/eidos/policies/POL-002-invariant-test-enforcement.adoc) (All invariants must have test bindings)?
* **Red Flag:** New business validation rules added in code without an explicit `INV-*` ID in the capability and without a passing automated test in `tests/`.

### Dimension 4: Workflow Mode Compliance
* **Planned Mode:** If a SpecDelta is present (`eidos/deltas/dlt-*.adoc`), does the code implementation fulfill the declared mutations and checklist items?
* **Direct Mode:** If no delta is present, did the author update living specifications (`eidos/`) in the same turn/commit?

### Dimension 5: Spec Conformance & Link Integrity
* Run automated validation:
  ```bash
  eidos validate eidos
  eidos drift --uncommitted
  ```
* **Verdict:** Zero broken cross-references, zero schema errors, zero un-synchronized drift.

---

## 3. Emitting the Architectural Review Report

Deliver structured, actionable review feedback formatted like a Senior/Staff Architect review:

```markdown
## 🏛️ Eidos Architectural Review Report

### 🎯 Summary Decision: [APPROVE | REQUEST_CHANGES | COMMENT]
- **Workflow Mode:** Planned Mode (`DLT-2026-012`) / Direct Mode
- **Drift Status:** Synced (Zero Drift) / Drift Detected
- **Invariant Test Coverage:** 100% Green / ⚠️ Missing Bindings

---

### 🔍 Architectural Findings

#### 1. Blast Radius & Components [PASS / WARN / BLOCK]
- **Touched Components:** `CMP-EIDOS-CLI`, `CMP-EIDOS-CORE`
- **Assessment:** Clean separation. All touched files belong strictly to declared components.

#### 2. Contract & SemVer Impact [PASS / WARN / BLOCK]
- **Touched Contracts:** `CTR-EIDOS-CLI`
- **Assessment:** Added optional flag `--prompt`. Fully backward-compatible (Minor evolution).

#### 3. Invariants & Policy Compliance [PASS / WARN / BLOCK]
- **Policies Checked:** `POL-001`, `POL-002`
- **New Invariants:** `INV-RECONCILE-PROMPT`
- **Test Binding:** `tests/test_drift.py::test_reconcile_specs_catalog_and_prompt` (Verified passing).

---

### 💡 Required Actions (Blockers)
1. None. Ready to merge.

### 📝 Suggestions (Non-blocking)
- Consider adding a short usage example to `eidos/architecture/contracts/cli.adoc`.
```

---

## 4. Automation & Tools

During review, the agent should invoke:
1. `eidos_detect_drift`: Inspect git diff and verify invariant test bindings.
2. `eidos_validate`: Check schema compliance and broken cross-references.
3. `eidos_list_components`: Verify architectural dependencies.

