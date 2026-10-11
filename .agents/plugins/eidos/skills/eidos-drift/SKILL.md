---
name: eidos-drift
description: Detect spec-to-code drift, inspect uncommitted or branched code changes, verify living specification synchronization, and check invariant test coverage using the eidos_detect_drift AI tool.
---

# Eidos Drift Detection (`eidos-drift`)

This skill empowers AI agents to autonomously detect spec-to-code drift, verify living specification synchronization, and ensure invariant test coverage across both **Direct Mode** and **Planned Mode** workflows.

---

## 1. When to Use This Skill

Activate this skill when:
- The user asks: *"Is my code in sync with specs?"*, *"Check for drift"*, or *"Did I break any spec invariants?"*.
- Verifying changes before concluding an agent turn or prior to opening a merge request.
- Checking uncommitted working tree edits (`uncommitted=True`) to determine if living specs or tests need to be updated.

---

## 2. Triggering via AI Tool (`eidos_detect_drift`)

Agents should invoke the MCP tool `eidos_detect_drift` to inspect drift without shell execution:

```json
{
  "name": "eidos_detect_drift",
  "arguments": {
    "uncommitted": true
  }
}
```

Or for branch comparisons against the default branch:
```json
{
  "name": "eidos_detect_drift",
  "arguments": {
    "base": "origin/main"
  }
}
```

### Interpreting the Tool Result

The tool returns a structured payload:

```json
{
  "is_clean": false,
  "drift_detected": true,
  "workflow_mode": "direct",
  "code_files": ["src/eidos/cli/main.py"],
  "spec_files": [],
  "delta_files": [],
  "test_files": ["tests/test_drift.py"],
  "touched_components": ["CMP-EIDOS-CLI"],
  "valid_specs": 31,
  "total_specs": 31,
  "remediation_suggestions": [
    "Drift Detected: 1 code file(s) changed in src/ without living specification updates in eidos/.",
    "• 'src/eidos/cli/main.py' relates to CMP-EIDOS-CLI -> Update 'eidos/architecture/components/cli.adoc'"
  ],
  "summary_markdown": "..."
}
```

---

## 3. Workflow Evaluation Protocol

### Direct Mode (Default)
When no `SpecDelta` (`DLT-*`) files are present:
1. `classification.code_files` must have corresponding updates in `classification.spec_files`.
2. If code was modified but no living specifications were touched -> **Drift Detected!**
3. If drift is detected, proceed to **Remediation**:
   - Identify owning components in `touched_components`.
   - Invoke `eidos_reconcile` (or activate `eidos-reconcile` skill) to generate candidate specification updates.
   - Update the relevant living specifications (`CMP-*`, `CTR-*`, `POL-*`, `CAP-*`) in `eidos/`.

### Planned Mode (SpecDelta Present)
When `delta_files` are present (e.g., `eidos/deltas/dlt-*.adoc`):
1. The changes are recognized as part of an explicit change plan.
2. Code modifications are permitted while the SpecDelta is in progress.
3. The tool verifies that the SpecDelta itself satisfies schema constraints and valid cross-references against the living catalog.

---

## 4. Invariant Test Coverage

Every capability invariant (`INV-*`) and policy (`POL-*`) must be bound to automated test coverage in `tests/`.
- If an invariant is added or touched without corresponding tests, the check reports missing test coverage.
- Add or update pytest test functions referencing the invariant ID in docstrings or test names.

---

## 5. CLI Alternative

Human developers and CI/CD pipelines can run the deterministic CLI command:

```bash
# Detect drift against base branch
eidos drift

# Detect drift on uncommitted working tree changes
eidos drift --uncommitted

# Emit JSON report for scripts
eidos drift --json

# Emit Markdown summary report
eidos drift --markdown drift_summary.md
```

---

## 6. Recommended Agent Loop

1. Run `eidos_detect_drift` with `uncommitted=True` after writing code.
2. If `drift_detected` is `true`:
   - Inspect `touched_components` and `remediation_suggestions`.
   - Update living specifications in `eidos/` directly (Direct Mode).
3. Run `eidos_validate` to confirm zero schema or link errors.
4. Run `eidos_detect_drift` again to verify `is_clean: true`.
