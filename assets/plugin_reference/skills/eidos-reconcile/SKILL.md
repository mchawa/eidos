---
name: eidos-reconcile
description: Detect spec-to-code drift, inspect Git diffs, map code changes to living specifications (Direct Mode), and enforce PR gate integrity.
---

# Eidos Spec-Code Reconciliation & Drift Gate (`eidos-reconcile`)

This skill equips AI agents to detect spec-to-code drift, semantically map Git diffs to living specifications, and maintain 100% synchronization without paperwork friction.

---

## 1. When to Use This Skill

Activate this skill when:
- Operating in **Direct Mode** (routine feature additions, refactorings, or bug fixes where code was modified directly).
- The user prompts: *"Bring specs up to date with my code changes"* or *"Check for drift"*.
- Preparing to open a Pull Request or verifying changes before concluding an agent turn.

---

## 2. The Reconciliation Protocol

When code has been modified in `src/` or `packages/`:

### Step 1: Inspect the Git Diff
Run CLI:
```bash
eidos reconcile
```
Or with prompt synthesis:
```bash
eidos reconcile --prompt
```
Eidos deterministically extracts:
1. All modified and untracked code files.
2. The owning architectural component manifests (`CMP-*`) based on `package_path`.
3. The catalog of current living specifications (Capabilities, Contracts, Policies).

### Step 2: Think Deeply & Map Semantically
Do not rely on naive path matching. **Think deeply** about the semantic behavior introduced in the Git diff. If you are unsure which capability or contract governs a change, run `eidos index search "<keywords>"` to search specification text and invariants using BM25.

| Code Change | Target Living Specification | What to Update |
|---|---|---|
| New route, CLI flag, or API parameter | Interface Contract (`CTR-*`) | Add endpoint, protocol definition, or payload schema. |
| New internal module or package dependency | Component Manifest (`CMP-*`) | Update `c4_internal_components` or `dependencies`. |
| New business rule, validation, or security logic | Policy (`POL-*`) | Add or patch invariant rule and link to `tests/`. |
| New user-facing feature or outcome | Domain Capability (`CAP-*`) | Update JTBD, acceptance criteria, and invariant IDs. |

### Step 3: Edit Living Specifications Directly
Use standard file editing tools (`replace_file_content` / `write_to_file`) to update the target `.adoc` files in `eidos/`.

### Step 4: Verify Zero Drift & Invariant Test Coverage
Run drift detection via CLI:
```bash
# 1. Verify schema compliance and cross-references:
eidos validate eidos

# 2. Verify spec-code synchronization:
eidos drift --uncommitted
```
Ensure all checks pass with exit code `0`.

---

## 3. CLI Commands Reference

```bash
# Detect drift (compares origin/main...HEAD)
eidos drift

# Detect drift on uncommitted working tree changes
eidos drift --uncommitted

# Generate Markdown drift summary file
eidos drift --markdown drift_summary.md

# Inspect Git diff and suggest candidate specs to update
eidos reconcile

# Generate structured AI prompt for copy-pasting
eidos reconcile --prompt
```

