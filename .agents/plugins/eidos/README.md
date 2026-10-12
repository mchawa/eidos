# Eidos Agent Plugin

[![Agent Plugins 1.0.0](https://img.shields.io/badge/agent--plugins-1.0.0-blue)](https://agent-plugins.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A portable Agent Plugin conforming strictly to the [Agent Plugins 1.0.0 specification](https://agent-plugins.org/) and the [Agent Skills specification](https://agentskills.io/).

This plugin equips AI agents (Antigravity `agy`, Claude Code, Cursor, GitHub Copilot) with native understanding and control over **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## What It Provides

1. **Agent Skills Suite (`skills/`)**:
   - Conforms strictly to the `agentskills.io` standard with 14 specialized skills enabling AI agents to drive Eidos via direct CLI execution.
   - `skills/eidos/SKILL.md`: **Master Orchestrator** covering living specs, dual workflows, and specialized skills catalog.
   - `skills/eidos-context/SKILL.md`: **Context Slicing** extracting targeted C4 Level 2/3 topology, contracts, and active invariants.
   - `skills/eidos-init/SKILL.md`: **Repository Bootstrapping** initializing greenfield repositories with Arc42/C4 topology and core policies.
   - `skills/eidos-extract/SKILL.md`: **Brownfield Reverse-Engineering** guiding agents to systematically extract living specs from existing codebases.
   - `skills/eidos-product/SKILL.md`: **Domain & Capability Modeling** covering domains (`DOM-*`), capabilities (`CAP-*`), and personas (`PER-*`).
   - `skills/eidos-architecture/SKILL.md`: **Arc42 & C4 Topology Modeling** covering component manifests (`tech/component`) and interface contracts (`tech/contract`), enforcing **NO ADRs**.
   - `skills/eidos-design/SKILL.md`: **Multi-Design Systems** covering W3C Design Tokens, UI component contracts, and screen state machines.
   - `skills/eidos-explore/SKILL.md`: **Spikes & Feasibility Discovery** formulating technical spikes (`EXP-*`) and graduating them to SpecDeltas.
   - `skills/eidos-delta/SKILL.md`: **SpecDelta Mutation Lifecycle** covering Planned Mode mutations (`DLT-*`), execution, and reconciliation.
   - `skills/eidos-invariants/SKILL.md`: **Policy Governance & Invariant Traceability** covering system policies (`POL-*`), capability invariants (`INV-*`), and test bindings (`POL-002`).
   - `skills/eidos-review/SKILL.md`: **Staff Systems Architect Review** auditing blast radius, SemVer breaking changes, and invariant test coverage.
   - `skills/eidos-drift/SKILL.md`: **Spec-to-Code Drift Detection** verifying synchronization, schema conformance, and PR gates via `eidos drift`.
   - `skills/eidos-reconcile/SKILL.md`: **Direct Mode Spec Reconciliation** mapping code modifications to living specifications and syncing docs.
   - `skills/eidos-portal/SKILL.md`: **Living Web Portal & Documentation** serving (`eidos portal serve` / `eidos serve`) and exporting static sites (`eidos portal build` / `eidos build-site`).

2. **Direct CLI Tooling & JSON Output**:
   - Agents interact with Eidos through deterministic CLI commands with `--json` output support (`eidos validate --json`, `eidos drift --uncommitted --json`, `eidos context --format json`).

---

## Installation via CLI (Project Scope)

You can install this plugin into your local repository/workspace using the `eidos` CLI:

```bash
# Install to project scope (installs .agents/plugins/eidos and workspace configurations):
eidos install
# or:
eidos plugin install

# Or target specific project configurations:
eidos install project       # .agents/plugins/eidos (strict agent-plugins.org v1.0.0)
eidos install antigravity   # .agents/plugins/eidos (Antigravity workspace plugin)
eidos install claude        # .claude/skills/eidos (Claude Code plugin)
eidos install cursor        # .cursor/skills/eidos (Cursor project skills)

# Remove / uninstall from project scope:
eidos uninstall
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
