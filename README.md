# Eidos: Git-Native AI Software Development Ecosystem

[![CI](https://github.com/mchawa/eidos/actions/workflows/ci.yml/badge.svg)](https://github.com/mchawa/eidos/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/eidos-ai.svg)](https://pypi.org/project/eidos-ai/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue.svg)](https://pypi.org/project/eidos-ai/)
[![Documentation](https://img.shields.io/badge/docs-zensical-blue.svg)](https://mchawa.github.io/eidos/)
[![Living Portal](https://img.shields.io/badge/portal-c4--explorer-green.svg)](https://mchawa.github.io/eidos/portal/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Eidos is an open, Git-native, agent-agnostic development framework designed to unify product definition, architectural invariants, and autonomous coding agents under executable living specifications.

This repository dogfoods the **Eidos Specification Standard** to define Eidos itself.

---

## Quickstart

```bash
# Install globally as a standalone CLI tool with uv (recommended)
uv tool install eidos-ai

# Or install with pipx
pipx install eidos-ai

# Or run ephemerally without installing
uvx --from eidos-ai eidos --help

# Initialize Eidos in your repository
eidos init

# Synchronize knowledge graph index
eidos index sync

# Validate living specifications
eidos validate eidos

# Verify zero drift between code and living specs
eidos drift

# Launch interactive C4 architecture portal
eidos portal serve --path eidos --port 8080

# Install AI agent plugin (4 skills for Antigravity, Claude, Cursor, Copilot)
eidos plugin install --target all
```

---

## Repository Architecture

```text
eidos/
├── pyproject.toml                 # Modern Python package definition (eidos-ai on PyPI)
├── zensical.toml                  # Zensical documentation site configuration
│
├── src/eidos/                     # [THE ENGINE / SOLUTION]
│   ├── cli/                       # CLI commands (`eidos validate`, `eidos drift`, `eidos delta`, etc.)
│   ├── core/                      # AsciiDoc + YAML frontmatter parser & schema validator
│   ├── plugin/                    # Agent plugin packager, validator, and installer (4 skills)
│   ├── portal/                    # Interactive Living Architecture static portal generator
│   ├── vcs/                       # Git spec-to-code drift detection & reconciliation engine
│   └── schemas/v1/                # Portable JSON/YAML Schemas bundled as package data
│
├── eidos/                         # [THE LIVING SPECIFICATION / REAL USAGE]
│   ├── system.adoc                # Root system descriptor
│   ├── personas/                  # Human & AI actor roles
│   ├── policies/                  # Non-negotiable architectural invariants (POL-*)
│   ├── domains/                   # Bounded contexts & capabilities (CAP-*)
│   ├── architecture/              # System architecture, components, & contracts
│   └── deltas/                    # In-flight and applied mutations (DLT-*)
│
├── docs/                          # User documentation site (Zensical)
└── tests/                         # Engine & specification test suites
```

---

## Key Distinctions from Existing Tools

| Dimension | Traditional / Existing Tools | Eidos Approach |
| :--- | :--- | :--- |
| **Scope** | Coding loop only (Aider, Claude Code) or monolithic bot (Devin) | Full SDLC: Exploration → Product Baseline → SpecDelta → Code → CI |
| **Specifications** | Ephemeral Jira tickets, stale Confluence/Notion pages | Versioned YAML frontmatter + AsciiDoc living in Git |
| **Change Management** | Narrative RFC essays that rot in archive folders | Declarative `SpecDelta` mutations applied automatically upon merge |
| **Agent Support** | Vendor-locked to a single proprietary LLM | Agent-agnostic via agent-plugins.org v1.0.0 & CLI-first Agent Skills |
| **VCS Support** | Tied to a single SaaS platform or closed web UI | 100% Git-native local repository engine |

---

## Navigating the Specifications

* **Root System**: [`eidos/system.adoc`](eidos/system.adoc)
* **Product Domains**:
  * [`Spec Engine Domain`](eidos/domains/spec-engine/domain.adoc)
  * [`Agent Intelligence Domain`](eidos/domains/agent-intelligence/domain.adoc)
  * [`VCS Lifecycle Domain`](eidos/domains/vcs-lifecycle/domain.adoc)
  * [`Experience Portal Domain`](eidos/domains/experience-portal/domain.adoc)
* **Policies**:
  * [`POL-001: Git Source of Truth`](eidos/policies/POL-001-git-as-single-source-of-truth.adoc)
  * [`POL-002: Invariant Test Enforcement`](eidos/policies/POL-002-invariant-test-enforcement.adoc)
* **Technical Architecture**:
  * [`Global System Architecture`](eidos/architecture/system-architecture.adoc)
  * [`Containers Directory`](eidos/architecture/containers/)
  * [`Contracts Directory`](eidos/architecture/contracts/)
* **SpecDeltas**:
  * [`SpecDeltas Directory`](eidos/deltas/)

---

## Documentation & Living Portal

* **User Documentation**: [https://mchawa.github.io/eidos/](https://mchawa.github.io/eidos/)
* **Interactive Living Architecture Portal**: [https://mchawa.github.io/eidos/portal/](https://mchawa.github.io/eidos/portal/)
* **PyPI Package**: [https://pypi.org/project/eidos-ai/](https://pypi.org/project/eidos-ai/)
* **AI Agent Plugin Guide**: [https://mchawa.github.io/eidos/guides/agent-plugin/](https://mchawa.github.io/eidos/guides/agent-plugin/)

---

## License

Licensed under the [MIT License](LICENSE).
