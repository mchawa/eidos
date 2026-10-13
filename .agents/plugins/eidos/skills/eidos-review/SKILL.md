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

1. **Blast Radius & Boundary Integrity**: Component boundaries, cross-package imports, and transitive dependencies.
2. **Interface Contract SemVer Compatibility**: Protocol stability across CLI, REST, gRPC, and messaging contracts.
3. **Policies & Invariant Guardrails**: Enforcement of repository policies (`POL-*`) and test bindings (`INV-*`).
4. **Workflow Mode Compliance**: Proper execution of Planned Mode (SpecDeltas) or Direct Mode reconciliation.
5. **Spec Conformance & Link Integrity**: Zero broken references and valid schemas (`eidos validate`).

### Dimension 1: Blast Radius & Boundary Integrity
* **Recommended Step 1:** Run `eidos context -f <file> --format json` on the changed files. This immediately extracts the exact C4 Level 2/3 component slice, bound interface contracts (`CTR-*`), domain capabilities (`CAP-*`), and technical invariants (`INV-*`).
* **Blast-Radius Graph Traversal:** Run `eidos index blast-radius <comp_id>` to compute recursive transitive dependencies across containers, interface contracts, and domain capabilities. Verify all impacted downstream entities are accounted for in the change plan.
* Inspect touched code files and verify they belong strictly to declared container packages (`CNT-*` in `eidos/architecture/containers/`).
* **Red Flag:** A pull request touches files across 4 different components when the SpecDelta only declared mutations for 1 component.
* **Red Flag:** Introduction of circular module dependencies or unauthorized cross-package imports violating architectural boundaries.

### Dimension 2: Interface Contract & Protocol Compatibility (SemVer)
* Inspect interface contract modifications (`CTR-*` in `eidos/architecture/contracts/`):
  - CLI commands (`cli_args`): Were required flags added? Were existing arguments renamed or removed?
  - REST APIs (`openapi`): Were required request body fields added? Were response status codes changed?
  - gRPC / Protobuf (`protobuf`): Were field tag numbers altered or types changed?
* **Verdict:** Flag any breaking interface change that lacks a corresponding major version bump or backward-compatibility deprecation plan.

### Dimension 3: Policy & Invariant Guardrails
* Verify compliance against repository policies (`POL-*` in `eidos/policies/`):
  - Does the PR violate any declared repository policy (`POL-*`)?
  - Do all capability invariants (`INV-*`) have verified static test bindings (`file::func`)?
* **Red Flag:** New business validation rules added in code without an explicit `INV-*` ID in the capability and without an automated test binding in `tests/`.

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
- **Invariant Test Coverage:** 100% Verified / ⚠️ Missing Bindings

---

### 🔍 Architectural Findings

#### 1. Blast Radius & Components [PASS / WARN / BLOCK]
- **Touched Components:** `CNT-AUTH-SERVICE`, `CMP-AUTH-VALIDATOR`
- **Assessment:** Clean separation. All touched files belong strictly to declared components.

#### 2. Contract & SemVer Impact [PASS / WARN / BLOCK]
- **Touched Contracts:** `CTR-AUTH-API`
- **Assessment:** Added optional parameter `scope`. Fully backward-compatible (Minor evolution).

#### 3. Invariants & Policy Compliance [PASS / WARN / BLOCK]
- **Policies Checked:** `POL-INVARIANT-TRACEABILITY`
- **New Invariants:** `INV-AUTH-TOKEN-EXPIRY`
- **Test Binding:** `tests/test_auth.py::test_expired_token_rejected` (Verified).

---

### 💡 Required Actions (Blockers)
1. None. Ready to merge.

### 📝 Suggestions (Non-blocking)
- Consider adding a short usage example to `eidos/architecture/contracts/auth-api.adoc`.
```

---

## 4. Automation & Tools

During review, the agent should invoke:
1. `eidos context -f <files> --format json`: Immediately slice the architectural subgraph of touched files to inspect impacted C4 components, bound contracts, domain capabilities, and invariants.
2. `eidos drift --uncommitted --json` (or `--base origin/main --json`): Inspect git diff and verify invariant test bindings across polyglot test suites.
3. `eidos validate --json`: Check schema compliance and broken cross-references.
4. `eidos index search` / `eidos index blast-radius`: Verify architectural dependencies and blast radius.
