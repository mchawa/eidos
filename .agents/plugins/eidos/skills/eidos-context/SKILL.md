---
name: eidos-context
description: Extract token-efficient architectural context slices (C4 Level 2/3 topology, contracts, capabilities, invariants, and policies) for targeted tasks.
---

# Eidos Architectural Context Slicing (`eidos-context`)

This skill teaches AI agents how to extract a compact, token-efficient **architectural context slice** before beginning any feature, bugfix, or refactoring task.

---

## 1. When to Use This Skill

Activate this skill as **Step 1** of any engineering task:
- **At the start of an agent turn:** Before writing code, inspect the architectural boundaries, contracts, and invariants relevant to the requested task.
- **Token-Efficient Reasoning:** Avoid loading the full codebase or the entire `eidos/` directory into the agent context window. Slicing reduces context overhead while keeping 100% of the critical architectural guardrails.
- **Blast Radius & Impact Analysis:** Instantly discover which C4 containers, internal modules, provided/consumed interface contracts (`CTR-*`), domain capabilities (`CAP-*`), and technical invariants (`INV-*`) will be affected by a change.
- **Workflow Mode Selection:** Determine whether the upcoming change qualifies for **Direct Mode** (routine/localized changes) or requires **Planned Mode** (cross-component mutations or breaking contract changes via SpecDelta `DLT-*`).

---

## 2. The 3-Tier Architectural Context Model

To balance token budgets with reasoning depth, context slices are structured into 3 hierarchical tiers:

| Tier | Token Footprint | Contents | Recommended Usage |
| :--- | :--- | :--- | :--- |
| **`anchor`** | **~140 tokens** | Matched C4 components, container types, runtimes, package paths, and bound interface contracts. | Fast triage, quick bugfixes, read-only inspections. |
| **`guardrails`** *(Default)* | **~260 - 890 tokens** | Everything in Anchor + domain capabilities, active `INV-*` invariants, test coverage status, and governing policies. | Standard agent turns, feature implementations, and PR prep. |
| **`deep`** | **~500 - 1,500 tokens** | Everything in Guardrails + C4 Level 3 internal modules, JTBD outcomes, full invariant rules, and detailed policy statements. | Complex refactors, SpecDeltas, and cross-component migrations. |

---

## 3. Triggering Context Slicing via CLI

Agents extract architectural context slices directly using the `eidos context` and `eidos index` CLI commands:

```bash
# Slice context with default guardrails tier (~260-890 tokens)
eidos context -f src/eidos/cli/main.py

# Slice minimal anchor tier (~140 tokens)
eidos context -f src/eidos/cli/main.py --tier anchor

# Slice deep comprehensive tier with C4 internal modules (~1,500 tokens)
eidos context -f src/eidos/cli/main.py --tier deep

# Machine-readable JSON output for agent automation
eidos context -f src/eidos/cli/main.py --format json

# Generate token-budgeted structural repo map outline (~98% compression)
eidos repo-map --max-tokens 1500

# Locate symbol matches across code AST and link to living specs
eidos find-symbol "AstGrepIndexer"

# Search architecture via FTS5 BM25 ranked search
eidos index search "token design system"

# Calculate recursive CTE blast radius up to 2 hops
eidos index blast-radius CMP-EIDOS-CORE --depth 2

# Check knowledge graph index status and cache size
eidos index status

# Incrementally synchronize or force-rebuild index
eidos index sync --force
```

#### CLI Context Options:
| Flag | Short | Description |
| :--- | :--- | :--- |
| `--file` | `-f` | Target source code file path(s) (can be passed multiple times) |
| `--component` | `-c` | Target component identifier (e.g. `CMP-EIDOS-CLI`) |
| `--task` | `-t` | Free-text task prompt or issue description |
| `--tier` | `-T` | Context tier: `anchor`, `guardrails` (default), or `deep` |
| `--format` | | Output format: `markdown` (default) or `json` |
| `--path` | `-p` | Path to specifications directory (defaults to `./eidos`) |

---

## 4. Automatic Zero-Drift Freshness

The context engine is backed by an embedded SQLite knowledge graph (`.eidos/cache/index.db`) using Python's standard library `sqlite3`:
1. **Sub-3ms Dirty Checking**: Before executing any query or CLI command, `is_dirty()` compares disk modification timestamps (`mtime`) and SHA-256 hashes against the cached manifest.
2. **Instant Sync**: Only modified or new `.adoc` files are re-parsed into SQLite (<5ms). Clean repositories execute dirty checks in ~1.5ms.
3. **Transparent Rebuild**: If `.eidos/cache/index.db` is missing or corrupted, it is automatically rebuilt from the living specifications with zero manual developer intervention.

---

## 5. Interpreting the Architectural Context Slice

The returned markdown context slice contains 5 structured sections:

1. **C4 Level 2 Containers & Level 3 Components (`CNT-*`, `CMP-*`)**: Deployment units, internal modules, and package paths.
2. **Bound Interface Contracts (`CTR-*`)**: Protocols, parameters, schemas, and SemVer stability rules.
3. **Domain Capabilities & JTBD (`CAP-*`)**: Business value, actor personas, and Jobs-to-be-Done.
4. **Active Capability Invariants (`INV-*`)**: Non-negotiable behavioral invariants and test bindings.
5. **Governing System Policies (`POL-*`)**: Repository-wide architectural rules (e.g. single source of truth, test enforcement).

### 1. C4 Level 2/3 Components (`CMP-*`)
- **Container Type & Runtime:** Shows whether the component is a `library`, `service`, `cli_app`, `daemon`, etc., and its runtime stack.
- **Monorepo Package Path:** The directory root where the component's code resides.
- **Internal Dependencies:** Allowed internal component references. Any imports outside these declared dependencies violate architectural boundaries.
- **Internal Modules (C4 Level 3):** Key classes/modules and their single responsibilities.

### 2. Bound Interface Contracts (`CTR-*`)
- **Owner & Protocol:** Identifies whether the contract is `cli_args`, `openapi`, `protobuf`, etc.
- **Contract Path:** The living specification file defining the interface signature.
- **Breaking Change Warning:** Modifying public parameters, arguments, or payload schemas requires a backward-compatible migration plan or SemVer major bump.

### 3. Domain Capabilities & JTBD (`CAP-*`)
- **Owning Domain:** Bounded context (`DOM-*`) governing the capability.
- **Status:** Current lifecycle status (`active`, `proposed`).
- **Jobs-to-be-Done (JTBD):** The human/business outcome this capability fulfills. Ensures technical changes remain aligned with product intent.

### 4. Active Invariants & Test Coverage (`INV-*`)
- **Rule Statement:** The non-negotiable architectural or business rule that must hold true.
- **Coverage Status:**
  - `[✓ Covered]`: Bound test file/function exists and passes in the test suite.
  - `[⚠️ Uncovered]`: Invariant lacks automated test coverage.
- **Test Binding:** Explicit path to the test file or test item (e.g. `tests/test_drift.py::test_reconcile_specs`).
- **Golden Rule:** Code changes must never cause a covered invariant to become uncovered or fail. New capabilities must declare invariants and provide test coverage.

### 5. Governing Policies (`POL-*`)
- Non-negotiable repository policies governing the repository (e.g., `POL-001` Git Source of Truth, `POL-002` Invariant Test Enforcement, `POL-003` Continuous Documentation Sync).

---

## 6. Transitioning into Dual Workflows

Once the architectural context slice is analyzed, choose the appropriate workflow mode:

### Route A: Direct Mode (Fast Fixes, Localized Features)
- **When to Use:**
  - The change impacts only 1 component.
  - No interface contracts are broken.
  - No cross-cutting architectural mutations are introduced.
- **Action Plan:**
  1. Modify code in `src/` or `packages/`.
  2. Implement/update invariant tests matching declared `INV-*` IDs.
  3. Update the component manifest (`CMP-*`) or capability (`CAP-*`) in `eidos/` within the same turn/commit.
  4. Verify with `eidos validate eidos` and `eidos drift --uncommitted`.

### Route B: Planned Mode (SpecDelta `DLT-*`)
- **When to Use:**
  - The change spans multiple components or introduces a new component.
  - Interface contracts (`CTR-*`) undergo breaking or major modifications.
  - A new business domain (`DOM-*`) or high-stakes policy (`POL-*`) is established.
- **Action Plan:**
  1. Author a SpecDelta using `eidos delta create DLT-<year>-<seq> --title "..."`.
  2. Map out the checklist and declare mutations (`add`, `modify`, `deprecate`, `remove`).
  3. Review the delta plan with team/human lead.
  4. Execute code changes and invariant tests against the checklist.
  5. Apply the delta in-place: `eidos delta apply <delta-id>`.

---

## 7. Recommended Agent Workflow Loop

1. **Extract Context Slice**: Run `eidos context -f ... --format json` for targeted progressive disclosure.
2. **Assess Blast Radius**: Review container boundaries, interface contracts, and capability invariants.
3. **Select Workflow Mode**: Choose Direct Mode (routine changes) vs. Planned Mode (cross-cutting/breaking changes).
4. **Implement Code & Tests**: Author code and write polyglot tests referencing `INV-*`.
5. **Reconcile Living Specifications**: Keep specifications in `eidos/` synchronized with code changes.
6. **Validate Integrity**: Run `eidos validate eidos` and `eidos drift --uncommitted` to ensure zero drift.
