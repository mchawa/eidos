---
name: eidos-drift
description: Detect spec-to-code drift, inspect uncommitted or branched code changes, verify living specification synchronization, and check invariant test coverage using the eidos drift CLI command.
---

# Eidos Drift Detection (`eidos-drift`)

This skill empowers AI agents to autonomously detect spec-to-code drift, verify living specification synchronization, and ensure invariant test coverage across both **Direct Mode** and **Planned Mode** workflows.

---

## 1. When to Use This Skill

Activate this skill when:
- The user asks: *"Is my code in sync with specs?"*, *"Check for drift"*, or *"Did I break any spec invariants?"*.
- Verifying changes before concluding an agent turn or prior to opening a merge request.
- Checking uncommitted working tree edits (`--uncommitted`) to determine if living specs or tests need to be updated.

---

## 2. Triggering via CLI (`eidos drift`)

Agents run `eidos drift --json` to inspect drift with machine-readable JSON output:

```bash
# Detect drift on uncommitted working tree changes
eidos drift --uncommitted --json
```

Or for branch comparisons against the default branch:
```bash
# Detect drift against base branch
eidos drift --base origin/main --json
```

### Interpreting the Command Result

The command returns a structured JSON payload:

```json
{
  "is_clean": false,
  "drift_detected": true,
  "workflow_mode": "direct",
  "code_files": ["src/auth/service.py"],
  "spec_files": [],
  "delta_files": [],
  "test_files": ["tests/test_auth.py"],
  "touched_components": ["CNT-AUTH-SERVICE"],
  "valid_specs": 47,
  "total_specs": 47,
  "remediation_suggestions": [
    "Drift Detected: 1 code file(s) had structural changes without living specification updates in eidos/.",
    "• 'src/auth/service.py' relates to CNT-AUTH-SERVICE -> Update 'eidos/architecture/containers/auth.adoc'"
  ],
  "summary_markdown": "..."
}
```

---

## 3. Workflow Evaluation Protocol

### Direct Mode (Default)
When no `SpecDelta` (`DLT-*`) files are present:
1. Code files with **structural changes** (added/deleted files, or added/removed/renamed/signature-changed public classes, functions, or methods) must have corresponding updates in their owning spec or a 1-hop linked spec in `eidos/`. Body-only edits inside existing functions do not trigger drift.
2. If structural code changes occur without any owning or 1-hop linked specification update -> **Drift Detected!**
3. If drift is detected, proceed to **Remediation**:
   - Identify owning components in `touched_components`.
   - Activate `eidos-reconcile` skill or run `eidos index search` to locate candidate living specifications.
   - Update the relevant living specifications (`CNT-*`, `CMP-*`, `CTR-*`, `POL-*`, `CAP-*`) in `eidos/`.

### Planned Mode (SpecDelta Present)
When `delta_files` are present (e.g., `eidos/deltas/dlt-*.adoc`):
1. The changes are recognized as part of an explicit change plan.
2. Code modifications are permitted while the SpecDelta is in progress.
3. The tool verifies that the SpecDelta itself satisfies schema constraints and valid cross-references against the living catalog.

---

## 4. Invariant Test Coverage & Polyglot Scanning

Every capability invariant (`INV-*`) and policy (`POL-*`) must be bound to automated test coverage in the repository test suite.

### Polyglot Test File Discovery
`eidos drift` features an integrated **Polyglot Invariant Test Scanner** that automatically discovers and inspects test files across multiple programming languages and ecosystems:

| Language / Framework | Recognized Test File Patterns |
| :--- | :--- |
| **Python** (`pytest`, `unittest`) | `test_*.py`, `*_test.py`, and `*.py` within `tests/`, `test/`, `__tests__/` |
| **TypeScript & JavaScript** (`Jest`, `Vitest`, `Mocha`, `Playwright`) | `*.test.ts`, `*.spec.ts`, `*.test.tsx`, `*.spec.tsx`, `*.test.js`, `*.spec.js`, `*.test.jsx`, `*.spec.jsx` |
| **Go** (`go test`) | `*_test.go` |
| **Rust** (`cargo test`) | `tests/*.rs`, `*_test.rs`, and `*.rs` inside test directories |
| **Java & Kotlin** (`JUnit`, `Kotest`) | `*Test.java`, `*Test.kt`, `*Spec.kt` |

### Invariant Marker Placement
The scanner reads test file contents and validates invariant coverage whenever an invariant identifier (`INV-*`) is discovered in:
1. **Source Comments:**
   - C-style single-line or multi-line comments (`// INV-AUTH-001`, `/* INV-RATE-LIMIT */`).
   - Python / shell comments (`# INV-AUTH-001`).
2. **Docstrings & Test Descriptions:**
   - Python docstrings (`"""Verify lockout policy (INV-AUTH-RATE-LIMIT)."""`).
   - Jest / Vitest / Playwright test titles (`it("INV-AUTH-001: locks after 5 attempts", ...)`).
   - Go log statements (`t.Log("INV-AUTH-001: checking lockout")`).
   - Rust test doc comments (`/// INV-AUTH-PASSWORD-HASH`).
   - JUnit `@DisplayName("INV-AUTH-001: Lockout")` or `@Tag("INV-AUTH-001")`.
3. **Explicit Test Bindings:**
   - Declared in capability frontmatter: `test_binding: "tests/auth/test_login.py::test_lockout"`.
   - Pointing to any valid polyglot test path (e.g. `tests/auth_test.go`, `tests/auth.test.ts`, `tests/auth_test.rs`).

When any declared invariant lacks test mentions and has an invalid or missing `test_binding`, drift detection flags missing invariant test coverage, preventing unverified mutations from slipping through.

---

## 5. CLI Command Reference

Developers and CI/CD pipelines can run the deterministic CLI command:

```bash
# Detect drift against base branch
eidos drift

# Detect drift on uncommitted working tree changes
eidos drift --uncommitted

# Emit JSON report for scripts or agent reasoning
eidos drift --uncommitted --json

# Emit Markdown summary report
eidos drift --markdown drift_summary.md
```

---

## 6. Recommended Agent Loop

1. Run `eidos drift --uncommitted --json` after writing code.
2. If `drift_detected` is `true`:
   - Inspect `touched_components` and `remediation_suggestions`.
   - Update living specifications in `eidos/` directly (Direct Mode).
3. Run `eidos validate --json` to confirm zero schema or link errors.
4. Run `eidos drift --uncommitted --json` again to verify `is_clean: true`.
