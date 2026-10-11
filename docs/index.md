# Eidos

**Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator**

---

[![CI](https://github.com/mchawa/eidos/actions/workflows/ci.yml/badge.svg)](https://github.com/mchawa/eidos/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/eidos-ai.svg)](https://pypi.org/project/eidos-ai/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://pypi.org/project/eidos-ai/)
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

* **Living Arc42 & C4 Architecture**: Containers, components, and interface contracts are codified as AsciiDoc with YAML frontmatter.
* **Strictly No ADRs**: Decisions mutate the living architecture directly through auditable **SpecDeltas** (`DLT-*`).
* **Bidirectional Invariant-Test Traceability**: Every capability and policy enforces invariants (`INV-*`, `POL-*`) that map directly to automated tests in `tests/`.
* **Agent-Agnostic Plugin & MCP Server**: Equips AI agents (Google Antigravity, Claude Code, Cursor, Codex) with specialized tools and skills to query, mutate, and validate architecture before editing code.
* **Interactive Living Portal**: Generates a self-contained static visual architecture explorer and graph directly from your specs.

---

## Quick Installation

Install Eidos using `pip` or `uv`:

=== "uv"

    ```bash
    uv add eidos-ai
    ```

=== "pip"

    ```bash
    pip install eidos-ai
    ```

=== "Standalone (pipx)"

    ```bash
    pipx install eidos-ai
    ```

Verify the installation:

```bash
eidos --help
```

---

## Next Steps

* **[Installation Guide](getting-started/installation.md)**: System requirements, optional extras, and devcontainer setup.
* **[5-Minute Quickstart](getting-started/quickstart.md)**: Bootstrap your repository and inspect your first living specifications.
* **[Core Concepts](concepts/living-specs.md)**: Learn about Living Specs, SpecDeltas, and Invariant Traceability.
* **[CLI Reference](guides/cli.md)**: Explore the complete command-line interface.

