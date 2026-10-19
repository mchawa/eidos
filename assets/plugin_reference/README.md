# Eidos Agent Plugin (`agent-plugins.org v1.0.0`)

This directory contains the official **Eidos Agent Plugin**, structured in strict compliance with the [agent-plugins.org v1.0.0](https://agent-plugins.org) open specification.

## Contents

- `plugin.json` — Standard plugin manifest declaring identity, SemVer version, metadata, and client extensions (`com.google.antigravity`).
- `hooks.json` — Stateless `Stop` lifecycle hook (`eidos-drift-gate` invoking `eidos hook`) verifying specification validity, invariant test bindings, and spec-to-code synchronization.
- `skills/` — 4 cohesive workflow-aligned Agent Skills conforming to [agentskills.io](https://agentskills.io):
  - `eidos/SKILL.md` — **Master Orchestrator, Context & Drift**: Workflow routing (Direct vs. Planned Mode), tiered context slicing (`eidos context`), and continuous drift detection (`eidos drift`).
  - `eidos-spec/SKILL.md` — **Living Specification & Invariant Authoring**: Product domains (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), Arc42/C4 architecture (`CNT-*`, inline/promoted `CMP-*`, `CTR-*`, embedded `decisions:`), Multi-Design Systems (`DS-*`, W3C tokens, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`), and policies/invariants (`POL-*`, `INV-*` with polyglot `file::func` test bindings).
  - `eidos-plan/SKILL.md` — **Explorations, SpecDeltas & Staff Architectural Review**: Technical discovery spikes (`EXP-*`), SpecDelta mutation lifecycle (`DLT-*`, `create`, `verify`, `audit`, `apply`, `changelog`), and 5-dimension Staff Systems Architect reviews.
  - `eidos-ops/SKILL.md` — **Bootstrap, Brownfield Extraction & Web Portal**: Greenfield initialization (`eidos init`), systematic brownfield codebase extraction, and Living Web Portal operations (`eidos portal serve`, `eidos portal build`).

## CLI-First Architecture

Eidos operates via **direct CLI execution** (`eidos validate --json`, `eidos drift --uncommitted --json`, `eidos context --json`). This eliminates background daemon flakiness in cloud, container, and remote sandbox environments.

## Usage with Eidos CLI

```bash
# Validate plugin conformance
eidos plugin validate

# View plugin metadata
eidos plugin info

# Install plugin into project workspace (.agents/plugins/eidos) and IDE configs
eidos plugin install

# Upgrade installed plugin and prune legacy skill directories
eidos plugin update all
```
