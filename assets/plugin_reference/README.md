# Eidos Agent Plugin

[![Agent Plugins 1.0.0](https://img.shields.io/badge/agent--plugins-1.0.0-blue)](https://agent-plugins.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A portable Agent Plugin conforming strictly to the [Agent Plugins 1.0.0 specification](https://agent-plugins.org/) and the [Agent Skills specification](https://agentskills.io/).

This plugin equips AI agents (Antigravity `agy`, Claude Code, Cursor, GitHub Copilot) with native understanding and control over **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## What It Provides

1. **Model Context Protocol (MCP) Server (`mcp_config.json`)**:
   - `eidos_detect_drift`: Inspects spec-to-code drift between git branches or uncommitted changes, validates schemas, and checks test bindings.
   - `eidos_reconcile`: Analyzes recent code changes against the living spec catalog and proposes candidate specs or tailored AI prompts.
   - `eidos_validate`: Validates all `.adoc` specifications against JSON/YAML schemas and checks bidirectional cross-references.
   - `eidos_info`: Returns Eidos engine version, loaded v1 schemas, and supported platforms.
   - `eidos_list_components`: Lists monorepo containers, internal modules, dependencies, and package paths.
   - `eidos_read_spec`: Parses and extracts structured frontmatter metadata and AsciiDoc sections.
   - `eidos_list_specs`: Lists living specifications matching optional type and lifecycle filters.
   - `eidos_create_spec`: Scaffolds a new living specification document with schema validation.
   - `eidos_delete_spec`: Safely deletes a living specification with cross-reference safety checks.
   - `eidos_create_delta`: Scaffolds a new SpecDelta mutation document (`DLT-*`) for Planned Mode.
   - `eidos_apply_delta`: Reconciles an applied SpecDelta in-place and updates symlink views.
   - `eidos_init_repo`: Bootstraps a repository with standard living specifications and agent plugin.

2. **Agent Skills Suite (`skills/`)**:
   - Conforms strictly to the `agentskills.io` standard.
   - `skills/eidos/SKILL.md`: **Master Orchestrator** covering living specs, dual workflows, and specialized skills catalog.
   - `skills/eidos-init/SKILL.md`: **Repository Bootstrapping** initializing greenfield repositories with Arc42/C4 topology and core policies.
   - `skills/eidos-extract/SKILL.md`: **Brownfield Reverse-Engineering** guiding agents to systematically extract living specs from existing codebases.
   - `skills/eidos-product/SKILL.md`: **Domain & Capability Modeling** covering domains (`DOM-*`), capabilities (`CAP-*`), and personas (`PER-*`).
   - `skills/eidos-architecture/SKILL.md`: **Arc42 & C4 Topology Modeling** covering component manifests (`tech/component`) and interface contracts (`tech/contract`), enforcing **NO ADRs**.
   - `skills/eidos-design/SKILL.md`: **Multi-Design Systems** covering W3C Design Tokens, UI component contracts, and screen state machines.
   - `skills/eidos-explore/SKILL.md`: **Spikes & Feasibility Discovery** formulating technical spikes (`EXP-*`) and graduating them to SpecDeltas.
   - `skills/eidos-delta/SKILL.md`: **SpecDelta Mutation Lifecycle** covering Planned Mode mutations (`DLT-*`), execution, and reconciliation.
   - `skills/eidos-invariants/SKILL.md`: **Policy Governance & Invariant Traceability** covering system policies (`POL-*`), capability invariants (`INV-*`), and test bindings (`POL-002`).
   - `skills/eidos-review/SKILL.md`: **Staff Systems Architect Review** auditing blast radius, SemVer breaking changes, and invariant test coverage.
   - `skills/eidos-drift/SKILL.md`: **Spec-to-Code Drift Detection** verifying synchronization, schema conformance, and PR gates via `eidos drift` and `eidos_detect_drift`.
   - `skills/eidos-reconcile/SKILL.md`: **Direct Mode Spec Reconciliation** mapping code modifications to living specifications and syncing docs.
   - `skills/eidos-portal/SKILL.md`: **Living Web Portal & Documentation** serving (`eidos serve`) and exporting static sites (`eidos build-site`).

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
