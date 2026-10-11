---
name: eidos-explore
description: Formulate hypotheses, scope technical spikes, conduct feasibility discovery (EXP-*), and graduate proven findings to SpecDeltas or archive learnings.
---

# Eidos Product Discovery & Technical Spikes (`eidos-explore`)

This skill equips AI agents to conduct **hypothesis-driven research, feasibility spikes, and exploratory discovery** within an Eidos repository before making permanent architectural commitments.

---

## 1. Why Explorations Precede Specifications

```
                       ┌─────────────────────────┐
                       │   Uncertainty / Risk    │
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │  Exploration (EXP-*)    │
                       │  Hypothesis + Spikes    │
                       └────────────┬────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼ Adopted                       ▼ Discarded
        ┌───────────────────────┐       ┌───────────────────────┐
        │  SpecDelta (DLT-*)    │       │  Archived Learnings   │
        │  Formal Mutation Plan │       │  Preserves Knowledge  │
        └───────────────────────┘       └───────────────────────┘
```

1. **Prevent Premature Architecture:** Do not commit to unverified database engines, protocols, or frameworks in living specifications without empirical proof.
2. **Auditable Spikes:** Prototype in disposable branches, but record the hypothesis, questions, and exit criteria in a validated specification (`eidos/explorations/exp-<year>-<num>.adoc`).
3. **No ADR Graveyards:** Explorations that succeed graduate into [`SpecDeltas`](file:///home/MTS/marwan.chawa/WS/eidos/.agents/plugins/eidos/skills/eidos-delta/SKILL.md) (`DLT-*`); explorations that fail are marked `discarded` with post-mortem learnings so mistakes are never repeated.

---

## 2. Exploration Lifecycle States

Explorations follow a 4-state lifecycle defined in `exploration.schema.yaml`:

* **`proposed`**: Queued technical spike or research topic.
* **`in_progress`**: Actively executing experiments on a prototype branch (`spike/exp-*`).
* **`adopted`**: Hypothesis empirically proven; ready to graduate to a SpecDelta (`DLT-*`).
* **`discarded`**: Hypothesis disproven, infeasible, or abandoned; findings archived with lessons learned.

---

## 3. Authoring an Exploration Manifest (`EXP-*`)

Scaffold a new exploration via CLI or MCP tool:
```bash
eidos spec create exploration EXP-2026-001 \
  --title "Evaluate SQLite in WAL Mode for Edge Node Caching" \
  --motivation "Assess whether SQLite WAL mode can sustain 10k reads/sec with under 5ms p99 latency."
```

Or author `eidos/explorations/exp-2026-001.adoc`:

```yaml
---
type: product/exploration
schema: schemas/v1/product/exploration.schema.yaml
id: EXP-2026-001
title: "Evaluate SQLite in WAL Mode for Edge Node Caching"
status: proposed  # proposed | in_progress | adopted | discarded

hypothesis: "Adopting SQLite with WAL mode and memory-mapped I/O will reduce cache read latency by 60% compared to local Redis, while eliminating external daemon dependencies."

questions_to_answer:
  - "Does SQLite in WAL mode handle 50 concurrent readers without database locking errors?"
  - "What is the memory footprint under a 500MB working set size?"
  - "How clean is cross-platform compilation across Alpine Linux and macOS ARM64?"

spikes_to_run:
  - name: "spike-concurrency-benchmark"
    target: "Simulate 50 concurrent reader threads executing 100k read operations against a 500MB database."
  - name: "spike-container-build"
    target: "Verify automated static linking inside minimal Docker scratch image."

exit_criteria: "P99 read latency < 5ms under 50 concurrent readers with zero locking exceptions."
---
= Exploration: Evaluate SQLite in WAL Mode for Edge Node Caching
:toc: left

== Problem Statement
Our current edge daemon requires Redis running as a sidecar, increasing memory overhead on constrained edge gateways. We need a reliable embedded solution.

== Investigation Log & Findings
*(Record benchmark numbers, profiling traces, and findings here as the spike progresses).*

== Final Decision & Rationale
*(Record outcome when concluding the exploration).*
```

---

## 4. The 5-Step Exploration Protocol

### Step 1: Scaffold & Commit Manifest
Scaffold `eidos/explorations/exp-<year>-<num>.adoc` with a clear hypothesis and quantifiable `exit_criteria`. Validate:
```bash
eidos validate eidos
```

### Step 2: Branch & Spike
Create an experimental Git branch:
```bash
git checkout -b spike/exp-2026-001
```
Implement the prototype or benchmark script in a disposable scratch directory (e.g. `scratch/` or `spikes/`).

### Step 3: Record Empirical Data
Document concrete results directly in the exploration body:
- Benchmark throughput (ops/sec)
- Latency percentiles (p50, p95, p99)
- Memory and CPU profiles

### Step 4: Resolve the Exploration

#### Case A: Hypothesis Proven -> Graduate to SpecDelta
1. Update status to `adopted` in the exploration document:
   ```yaml
   status: adopted
   ```
2. Scaffold a formal SpecDelta to plan the production integration:
   ```bash
   eidos delta create DLT-2026-015 \
     --title "Integrate SQLite WAL Cache Engine" \
     --kind feature_addition \
     --motivation "Graduated from validated spike EXP-2026-001."
   ```
3. Link the exploration ID in the SpecDelta motivation.

#### Case B: Hypothesis Disproven -> Archive Learnings
1. Update status to `discarded`:
   ```yaml
   status: discarded
   ```
2. In the `Final Decision & Rationale` section, explain precisely why it failed (e.g., *"SQLite write lock contention degraded throughput by 80% under write-heavy workloads"*).
3. Commit to Git. The institutional knowledge is preserved forever.

---

## 5. Verification

Always verify schema validity before concluding:
```bash
eidos validate eidos
```
Ensures status, exit criteria, and hypotheses conform strictly to `exploration.schema.yaml`.
