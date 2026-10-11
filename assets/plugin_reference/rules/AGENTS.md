# Eidos AI Agent Architectural Guidelines

You are operating within a codebase managed by **Eidos**, a Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

Whenever a repository contains an `eidos/` directory, adhere strictly to the following guidelines:

## 1. Single Source of Truth
* All product requirements, architectural topologies, component interfaces, and policies are defined inside `eidos/` as living AsciiDoc (`.adoc`) files with YAML frontmatter.
* The living specs are not historical docs; they are continuously synchronized with the codebase.

## 2. Absolutely No ADRs (Architecture Decision Records)
* **Never create ADRs or historical changelogs.** ADRs become write-only documentation graveyards. Git preserves history.
* All decisions and architectural evolutions are recorded directly in the living specifications:
  - System Context & Container Topologies: `eidos/architecture/system-architecture.adoc` (Arc42 + C4 Model).
  - Component Manifests: `eidos/architecture/components/<name>.adoc` (or monorepo package `packages/<name>/component.adoc`).
  - Interface Contracts: `eidos/architecture/contracts/<contract>.adoc`.
  - Invariants & Policies: `eidos/policies/<policy>.adoc`.
  - Design Systems & Tokens: `eidos/design/` (W3C DTCG tokens & screen state machines).

## 3. Dual Workflows (Direct Mode vs. Planned Mode)
* **Direct Mode (Default for fast, routine, or flow-state tasks):**
  - When modifying code in `src/`, always update the corresponding living specifications in `eidos/` in the same turn/commit.
  - **Git diffs** serve as the change delta. Zero extra paperwork required.
* **Planned Mode (Optional for complex, breaking, or multi-component changes):**
  - **Think deeply** about the architectural implications, blast radius, contract boundaries, and policy invariants before touching code.
  - Scaffold a SpecDelta (`DLT-*`) as a structured, machine-validated change plan via MCP `eidos_create_delta` or CLI `eidos delta create`.
  - Review it with the human/team if needed, then execute the implementation against the delta checklist and reconcile it (`eidos delta apply <delta-id>`).

## 4. Invariant-Test Traceability
* Every policy (`POL-*`) and capability (`CAP-*`) has defined invariants.
* All automated tests in `tests/` must trace back to these IDs in test markers, docstrings, or test function names.
* Modifying code without maintaining invariant test coverage violates the repository's governance.

## 5. Reconciliation & Specification Integrity
* If code was modified before specs, run `eidos reconcile` (or use the `eidos-reconcile` skill) to inspect the Git diff and synchronize the living specifications.
* Before concluding your work, always validate all specifications and check for drift:
  - Run CLI: `eidos validate eidos` and `eidos drift`
  - Or MCP tools: `eidos_validate` and `eidos_detect_drift`
* All documents must strictly conform to their schema and have zero broken cross-references.
