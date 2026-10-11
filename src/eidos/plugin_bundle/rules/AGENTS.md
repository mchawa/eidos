# Eidos AI Agent Architectural Guidelines

You are operating within a codebase managed by **Eidos**, a Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

Whenever a repository contains an `eidos/` directory, adhere strictly to the following rules:

## 1. Single Source of Truth
* All product requirements, architectural topologies, component interfaces, and policies are defined inside `eidos/` as living AsciiDoc (`.adoc`) files with YAML frontmatter.
* The living specs are not historical docs; they are continuously synchronized with the codebase.

## 2. Absolutely No ADRs (Architecture Decision Records)
* **Never create ADRs.** ADRs become write-only documentation graveyards.
* All decisions and architectural evolutions are recorded directly in the living specifications:
  - System Context & Container Topologies: `eidos/architecture/system-architecture.adoc` (Arc42 + C4 Model).
  - Component Manifests: `eidos/architecture/components/<name>.adoc` (or monorepo package `packages/<name>/component.adoc`).
  - Interface Contracts: `eidos/architecture/contracts/<contract>.adoc`.
  - Invariants & Policies: `eidos/policies/<policy>.adoc`.
  - Proposed Changes: `eidos/deltas/active/<delta-id>.adoc`.

## 3. SpecDelta Mutation Lifecycle
* Before writing code for non-trivial features, refactors, or fixes, verify if a SpecDelta is needed.
* Scaffold deltas using MCP `eidos_create_delta` or in `eidos/deltas/active/<delta_id>.adoc` with `type: delta/mutation`.
* Once implemented and verified, update the living specs and reconcile the delta status to `applied`.

## 4. Invariant-Test Traceability
* Every policy (`POL-*`) and capability (`CAP-*`) has defined invariants.
* All automated tests in `tests/` must trace back to these IDs in test markers, docstrings, or test function names.
* Modifying code without maintaining invariant test coverage violates the repository's governance.

## 5. Specification Integrity & Validation
* Before concluding your work, always validate all specifications:
  - Run CLI: `eidos validate eidos`
  - Or MCP tool: `eidos_validate`
* All documents must strictly conform to their schema and have zero broken cross-references.

