# Eidos

**Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator**

---

[![CI](https://github.com/mchawa/eidos/actions/workflows/ci.yml/badge.svg)](https://github.com/mchawa/eidos/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/eidos-ai.svg)](https://pypi.org/project/eidos-ai/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue.svg)](https://pypi.org/project/eidos-ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Eidos** bridges the gap between high-level architectural intent and autonomous coding AI agents. It replaces static documentation graveyards with a single, verifiable source of truth stored directly in Git.

```
                    ┌─────────────────────────┐
                    │  Living Specifications  │
                    │      (eidos/*.adoc)     │
                    └────────────┬────────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
       ┌───────────────────┐           ┌───────────────────┐
       │   Human Engineers │           │  AI Agent Harness │
       │  (CLI & Portal)   │           │ (Claude/Cursor/AG)│
       └─────────┬─────────┘           └─────────┬─────────┘
                 │                               │
                 └───────────────┬───────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  Codebase & Test Suite  │
                    │   (Invariant Verified)  │
                    └─────────────────────────┘
```

---

## Why Eidos?

Modern software development often suffers from two major problems:

1. **Write-Only Documentation (The ADR Graveyard)**: Traditional Architecture Decision Records (ADRs) and design docs become outdated the moment code changes.
2. **Context-Blind AI Agents**: AI coding agents hallucinate interfaces, violate unwritten architectural policies, and break invisible system invariants because they lack structured, verifiable context.

**Eidos solves both problems** by organizing your project into executable, continuously validated **Living Specifications**:

* **Living Arc42 & C4 Architecture**: System context (`SYS-*`, `ARCH-*`), containers (`CNT-*`), hybrid inline/promoted components (`CMP-*`), and interface contracts (`CTR-*`) are codified as AsciiDoc with YAML frontmatter.
* **Strictly No ADRs (`POL-001`)**: Durable design trade-offs and rejected alternatives are recorded in-place via embedded `decisions:` (`DEC-*`) blocks, while architectural transitions are governed by auditable **SpecDeltas** (`DLT-*`).
* **Embedded SQLite + FTS5 + AST Knowledge Graph**: Auto-synchronizes in `<5ms` (`eidos/.cache/index.db`) to serve token-budgeted 3-tier context slices (`eidos context`), structural repo maps (`eidos index repo-map`), and recursive blast-radius analysis.
* **Bidirectional Polyglot Invariant-Test Traceability (`POL-002`)**: Every capability and policy enforces invariants (`INV-*`, `POL-*`) statically verified against automated tests across Python, TypeScript/JS, Go, Rust, Java, and Kotlin via `eidos drift`.
* **Multi-Design Systems & W3C Tokens**: White-label and multi-brand design systems (`DS-*`), official W3C DTCG tokens, UI component contracts (`UIC-*`), client manifests (`CLT-*`), screen state machines (`SCR-*`), and interaction flows (`FLOW-*`).
* **Agent-Agnostic Plugin, 4 Cohesive Skills & Lifecycle Hooks**: Equips AI agents (Google Antigravity, Claude Code, Cursor, GitHub Copilot, Aider, Cline, OpenCode, Codex) with 4 workflow skills and Block-Once drift gates (`eidos hook`).
* **Interactive Air-Gapped Living Portal**: Generates a self-contained static visual C4 architecture explorer and SpecDelta Kanban dashboard directly from your specs with zero Node.js runtime dependencies.

---

## How Eidos Works in 4 Steps

1. **Slice Context (`eidos context`)**: Before touching code, engineers or AI agents query the embedded SQLite knowledge graph for a token-budgeted slice (`anchor`, `guardrails`, or `deep`) containing the owning `CNT-*`/`CMP-*`, contracts (`CTR-*`), active invariants (`INV-*`), and historical decisions (`DEC-*`).
2. **Choose Workflow Mode (Direct vs. Planned)**:
   * **Direct Mode**: Co-evolve code, tests, and `.adoc` specifications in-place for localized fixes and single-container enhancements.
   * **Planned Mode**: Scaffold a declarative **SpecDelta (`DLT-*`)** (or run a hypothesis-driven **Exploration (`EXP-*`)** spike) for cross-container features or breaking contract changes.
3. **Implement & Bind Invariants**: Write implementation code in `src/` and bind every new or modified invariant (`INV-*`) to an automated test via `test_binding: path/to/test_file::test_function`.
4. **Verify Zero Drift (`eidos validate && eidos drift`)**: Eidos validates all 16 JSON Schemas, checks `x-eidos-ref` graph integrity, verifies substantive spec updates across 1-hop component neighborhoods, and confirms polyglot `INV-*` test coverage.

---

## Quick Installation

Install Eidos globally as a standalone CLI tool (so it works across any Python, TypeScript, Go, Rust, Java, or Kotlin repository without polluting project dependencies):

=== "uv tool (Recommended)"

    ```bash
    uv tool install eidos-ai
    ```

=== "pipx"

    ```bash
    pipx install eidos-ai
    ```

=== "Ephemeral (uvx)"

    ```bash
    uvx --from eidos-ai eidos --help
    ```

Verify the installation:

```bash
eidos doctor
```

---

## Next Steps

* **[How Eidos Works](concepts/how-eidos-works.md)**: Deep dive into the Eidos execution loop, SQLite knowledge graph, AST drift engine, and lifecycle hooks.
* **[Installation Guide](getting-started/installation.md)**: System requirements, optional extras, and workspace diagnostics.
* **[5-Minute Quickstart](getting-started/quickstart.md)**: Bootstrap your repository (`eidos init` or `eidos init --lite`) and inspect your first living specifications.
* **[Core Concepts](concepts/living-specs.md)**: Learn about Living Specs, Embedded Decisions (`DEC-*`), SpecDeltas, Invariants, and Dual Workflows.
* **[Explorations & Spikes](concepts/explorations.md)**: Hypothesis-driven technical discovery and prototype spikes (`EXP-*`).
* **[Multi-Design Systems](concepts/design.md)**: Multi-brand design systems, W3C tokens, and UI component contracts.
* **[Schema Glossary](concepts/schema-glossary.md)**: Comprehensive reference for all 16 living specification schemas.
* **[CLI Reference](guides/cli.md)**: Explore the complete command-line interface.

