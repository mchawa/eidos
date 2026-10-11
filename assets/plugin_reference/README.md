# Eidos Agent Plugin

[![Agent Plugins 1.0.0](https://img.shields.io/badge/agent--plugins-1.0.0-blue)](https://agent-plugins.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A portable Agent Plugin conforming strictly to the [Agent Plugins 1.0.0 specification](https://agent-plugins.org/) and the [Agent Skills specification](https://agentskills.io/).

This plugin equips AI agents (Antigravity `agy`, Claude Code, Cursor, GitHub Copilot) with native understanding and control over **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## What It Provides

1. **MCP Server (`mcp_config.json`)**:
   - `eidos_validate`: Validates all `.adoc` specifications against JSON/YAML schemas and checks cross-references.
   - `eidos_info`: Inspects Eidos engine version and loaded v1 schemas.
   - `eidos_list_components`: Lists monorepo containers, C4 components, internal modules, and package paths.
   - `eidos_read_spec`: Parses and extracts structured frontmatter and AsciiDoc sections.
   - `eidos_create_delta`: Scaffolds a new SpecDelta change file in `eidos/deltas/active/`.

2. **Agent Skills Suite (`skills/`)**:
   - Conforms strictly to the `agentskills.io` standard.
   - `skills/eidos/SKILL.md`: **Master Orchestrator** covering the 6-step lifecycle (Orient -> Policies -> SpecDelta -> Code & Invariants -> Validate -> Reconcile).
   - `skills/eidos-delta/SKILL.md`: **SpecDelta Lifecycle** covering atomic mutation authoring (`add_capability`, `patch_invariant`, `modify_component`), validation, and reconciliation.
   - `skills/eidos-architecture/SKILL.md`: **Arc42 & C4 Topology Modeling** covering component manifests (`tech/component`), whitebox modules, and interface contracts (`tech/contract`), enforcing **NO ADRs**.
   - `skills/eidos-invariants/SKILL.md`: **Policy Governance & Invariant Traceability** covering system policies (`product/policy`), capability invariants (`INV-*`), and test bindings (`POL-002`).
   - `skills/eidos-extract/SKILL.md`: **Codebase Reverse-Engineering & Extraction** guiding agents through an 8-phase pipeline to extract complete Eidos living specs from brownfield/legacy codebases.

3. **Continuous Agent Awareness Rules (`rules/AGENTS.md`)**:
   - Automatically injected into agents working in an Eidos repository.

---

## Installation via CLI (Project Scope)

You can install this plugin into your local repository/workspace using the `eidos` CLI:

```bash
# Install to project scope (installs .agents/plugins/eidos and workspace configurations):
eidos plugin install

# Or target specific project configurations:
eidos plugin install --target workspace     # .agents/plugins/eidos (agent-plugins.org v1.0.0)
eidos plugin install --target antigravity   # .agents/plugins/eidos (Antigravity workspace plugin)
eidos plugin install --target claude        # .claude/mcp.json (Claude Code project MCP)
eidos plugin install --target cursor        # .cursor/mcp.json (Cursor project MCP)

# Remove / uninstall from project scope:
eidos plugin remove
# Or:
eidos plugin uninstall --target cursor      # removes only cursor MCP entry
```

## Versioning and Updates

Inspect the installed vs bundled plugin version and SemVer 2.0.0 synchronization status:

```bash
# Check version status in current workspace:
eidos plugin version

# Update installed plugin to match latest bundled engine version:
eidos plugin update
# Or using the upgrade alias:
eidos plugin upgrade
```

## Conformance Verification

You can validate this plugin package against the official `agent-plugins.org` v1.0.0 JSON schemas and SemVer 2.0.0 rules at any time:

```bash
eidos plugin validate
```
