# Eidos

**Git-Native, Agent-Agnostic Development AI Ecosystem & Lifecycle Orchestrator**

[![CI](https://github.com/mchawa/eidos/actions/workflows/ci.yml/badge.svg)](https://github.com/mchawa/eidos/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/eidos-ai.svg)](https://pypi.org/project/eidos-ai/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue.svg)](https://pypi.org/project/eidos-ai/)
[![Documentation](https://img.shields.io/badge/docs-zensical-blue.svg)](https://mchawa.github.io/eidos/)
[![Living Portal](https://img.shields.io/badge/portal-c4--explorer-green.svg)](https://mchawa.github.io/eidos/portal/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

**Eidos** bridges the gap between high-level architectural intent and autonomous coding AI agents. It replaces static documentation graveyards with a single, continuously verified source of truth stored directly in Git.

This repository dogfoods the **Eidos Specification Standard** to define Eidos itself.

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
       │  (CLI & Portal)   │           │ (Antigravity/MCP) │
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

Modern software engineering with AI coding assistants suffers from two fundamental problems:

1. **Write-Only Documentation (The ADR Graveyard)**: Traditional Architecture Decision Records (ADRs) and wiki pages become obsolete the moment code changes.
2. **Context-Blind AI Agents**: AI assistants hallucinate interfaces, violate unwritten architectural policies, and break invisible system invariants because they lack structured, verifiable context.

**Eidos solves both problems** by organizing your project into executable, continuously validated **Living Specifications**:

* **Living Arc42 & C4 Architecture**: Containers, components, and interface contracts codified as AsciiDoc with YAML frontmatter.
* **Strictly No ADRs**: Decisions mutate the living architecture directly through auditable **SpecDeltas** (`DLT-*`).
* **Bidirectional Invariant-Test Traceability**: Every capability and policy enforces invariants (`INV-*`, `POL-*`) that trace directly to automated tests in `tests/`.
* **Agent-Agnostic Plugin & MCP Server**: Equips AI agents (Google Antigravity, Claude Code, Cursor, Windsurf, Cline) with specialized tools to query, mutate, and validate architecture before editing code.
* **Interactive Living Portal**: Generates a self-contained visual C4 architecture explorer and graph directly from your specifications.

---

## Key Distinctions from Existing Tools

| Dimension | Traditional / Existing Tools | Eidos Approach |
| :--- | :--- | :--- |
| **Scope** | Coding loop only (Aider, Claude Code) or monolithic bot (Devin) | Full SDLC: Exploration → Product Baseline → SpecDelta → Code → Invariant Verification |
| **Specifications** | Ephemeral Jira tickets, stale Confluence/Notion pages | Versioned YAML frontmatter + AsciiDoc living as Git files in `eidos/` |
| **Decisions (No ADRs)** | Narrative ADR essays that rot in archive folders | Living architecture updated via atomic, traceable `SpecDelta` (`DLT-*`) lifecycle |
| **Agent Support** | Vendor-locked to a single proprietary LLM | Agent-agnostic via **agent-plugins.org v1.0.0** and **Model Context Protocol (MCP)** |
| **Verification** | Manual code review or disconnected linters | Automated schema validation (`eidos validate`) and invariant-test mapping |

---

## Installation

Install Eidos using `uv` or `pip`:

```bash
# With uv (recommended)
uv add --dev eidos-ai

# Or with pip
pip install eidos-ai

# Or run instantly via uvx without installation
uvx eidos-ai info
```

---

## Quickstart

### 1. Initialize Eidos in your Git Repository
```bash
eidos init
```
This scaffolds the baseline living architecture topology, system descriptors, and policies inside `eidos/`.

### 2. Validate Living Specifications
```bash
eidos validate eidos
```
Ensures all specification documents strictly satisfy their bundled JSON Schemas and that all cross-references resolve.

### 3. Launch the Visual Living Architecture Portal
```bash
eidos serve --path eidos --port 8080
```
Launches a local interactive explorer for C4 diagrams, container maps, component dependencies, and policy matrices.

### 4. Propose Architectural Changes via SpecDeltas
```bash
# Scaffold a new SpecDelta before modifying code
eidos delta create "Add Webhook Notification Support"

# Once implemented and tests pass, apply and reconcile in-place
eidos delta apply dlt-2026-005
```

---

## AI Agent Integration

Eidos includes a standardized plugin conforming to **[agent-plugins.org v1.0.0](https://agent-plugins.org)** and a built-in **Model Context Protocol (MCP)** server.

Configure your project for all supported AI coding assistants in one command:

```bash
# Configure Google Antigravity, Claude Code, and Cursor at once
eidos plugin install --target all
```

Or configure specific assistants:
* **Google Antigravity**: `eidos plugin install --target antigravity` (deploys `.agents/plugins/eidos/` with rules and 7 bundled skills)
* **Claude Code**: `eidos plugin install --target claude` (configures `.claude/mcp.json`)
* **Cursor**: `eidos plugin install --target cursor` (configures `.cursor/mcp.json`)
* **Generic MCP (Windsurf, Roo Code, Cline)**: `uv run eidos mcp` or `uvx eidos-ai mcp`

For complete instructions, configuration examples, and rule definitions, see the **[AI Agent Integration Guide](https://mchawa.github.io/eidos/guides/agent-plugin/)**.

---

## Repository Architecture

This repository dogfoods Eidos to define Eidos:

```text
eidos/
├── pyproject.toml                 # Python package definition (eidos-ai on PyPI)
├── zensical.toml                  # Zensical documentation configuration
│
├── src/eidos/                     # [THE ENGINE / IMPLEMENTATION]
│   ├── cli/                       # Typer CLI commands (validate, delta, plugin, serve)
│   ├── core/                      # AsciiDoc + YAML frontmatter parser & schema validator
│   ├── mcp/                       # Model Context Protocol stdio server
│   ├── plugin/                    # Agent plugin packager, validator, and installer
│   ├── portal/                    # Interactive Living Architecture static portal generator
│   └── schemas/v1/                # Bundled JSON Schemas (product, tech, delta)
│
├── eidos/                         # [THE LIVING SPECIFICATIONS]
│   ├── system.adoc                # Root system descriptor
│   ├── personas/                  # Human & AI actor roles
│   ├── policies/                  # Non-negotiable architectural invariants (POL-*)
│   ├── domains/                   # Bounded contexts & capabilities (CAP-*)
│   ├── architecture/              # Arc42 / C4 containers, components, contracts
│   └── deltas/                    # In-flight and applied SpecDeltas (DLT-*)
│
├── docs/                          # Comprehensive user documentation (Zensical)
└── tests/                         # Engine & invariant test suite (pytest)
```

---

## Navigating the Living Specifications

* **Root System**: [`eidos/system.adoc`](eidos/system.adoc)
* **Product Domains**:
  * [`eidos/domains/product-core/domain.adoc`](eidos/domains/product-core/domain.adoc)
  * [`eidos/domains/agent-harness/domain.adoc`](eidos/domains/agent-harness/domain.adoc)
  * [`eidos/domains/vcs-sync/domain.adoc`](eidos/domains/vcs-sync/domain.adoc)
* **Core Policies**:
  * [`eidos/policies/POL-001-git-as-single-source-of-truth.adoc`](eidos/policies/POL-001-git-as-single-source-of-truth.adoc)
  * [`eidos/policies/POL-002-invariant-test-enforcement.adoc`](eidos/policies/POL-002-invariant-test-enforcement.adoc)
* **Technical Architecture**:
  * [`eidos/architecture/system-architecture.adoc`](eidos/architecture/system-architecture.adoc)
  * [`eidos/architecture/components/`](eidos/architecture/components/)
  * [`eidos/architecture/contracts/`](eidos/architecture/contracts/)
* **SpecDeltas**:
  * [`eidos/deltas/`](eidos/deltas/)

---

## Documentation & Links

* **User Documentation**: [https://mchawa.github.io/eidos/](https://mchawa.github.io/eidos/)
* **Living Architecture Portal**: [https://mchawa.github.io/eidos/portal/](https://mchawa.github.io/eidos/portal/)
* **PyPI Package**: [https://pypi.org/project/eidos-ai/](https://pypi.org/project/eidos-ai/)
* **Plugin Standard**: [https://agent-plugins.org](https://agent-plugins.org)

---

## License

Licensed under the [MIT License](LICENSE).
