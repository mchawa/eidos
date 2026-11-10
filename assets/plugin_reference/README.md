# Eidos Agent Skills Bundle (`agentskills.io`)

This directory contains the official **Eidos Agent Skills** reference bundle, structured in compliance with the [agentskills.io](https://agentskills.io) open specification.

## Contents

- `hooks.json` — Reference stateless `Stop` lifecycle hook (`eidos-drift-gate` invoking `eidos hook`) verifying specification validity, invariant test bindings, and spec-to-code synchronization.
- `skills/` — 4 cohesive workflow-aligned Agent Skills conforming to [agentskills.io](https://agentskills.io):
  - `eidos/SKILL.md` — **Master Orchestrator, Context & Drift**: Workflow routing (Direct vs. Planned Mode), tiered context slicing (`eidos context`), and continuous drift detection (`eidos drift`).
  - `eidos-spec/SKILL.md` — **Living Specification & Invariant Authoring**: Product domains (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), Arc42/C4 architecture (`CNT-*`, standalone `CMP-*`, `CTR-*`, embedded `decisions:`), Multi-Design Systems (`DS-*`, W3C tokens, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`), and policies/invariants (`POL-*`, `INV-*` with polyglot `file::func` test bindings).
  - `eidos-plan/SKILL.md` — **Explorations, SpecDeltas & Staff Architectural Review**: Technical discovery spikes (`EXP-*`), SpecDelta mutation lifecycle (`DLT-*`, `create`, `verify`, `audit`, `apply`, `changelog`), and 5-dimension Staff Systems Architect reviews.
  - `eidos-ops/SKILL.md` — **Bootstrap, Brownfield Extraction & Web Portal**: Greenfield initialization (`eidos init`), systematic brownfield codebase extraction, and Living Web Portal operations (`eidos portal serve`, `eidos portal build`).

## CLI-First Architecture

Eidos operates via **direct CLI execution** (`eidos validate --json`, `eidos drift --uncommitted --json`, `eidos context --json`). This eliminates background daemon flakiness in cloud, container, and remote sandbox environments.

## Usage with Eidos CLI

```bash
# Validate Agent Skills bundle conformance
eidos plugin validate

# View Agent Skills metadata
eidos plugin info

# Install Agent Skills into project workspace (.agents/skills, .claude/skills, .aider/skills) and IDE configs
eidos plugin install all

# Upgrade installed skills to the latest bundled version
eidos plugin update all
```
