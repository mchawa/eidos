---
name: eidos
description: Understand, navigate, author, and validate Eidos Git-native living specifications, Arc42/C4 architectures, and dual workflows.
---

# Eidos Engineering & Architecture Skill

This skill teaches AI agents how to reason about, navigate, author, and validate software systems built on **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## 1. What is Eidos?

Eidos replaces fragmented documentation, stale wikis, and historical write-only logs (like ADRs) with **living, schema-validated specifications** stored directly in the Git repository under the `eidos/` directory.

### Core Tenets
1. **Git as Single Source of Truth**: All specifications, code, and tests evolve together in atomic Git commits.
2. **AsciiDoc + YAML Frontmatter**: Every spec document is an `.adoc` file with structured, schema-validated YAML frontmatter and human/agent-readable AsciiDoc content.
3. **No ADRs (Architecture Decision Records)**: We do not write ADRs or maintain dead changelogs. Git preserves history. We maintain living component manifests (`tech/component`), system architecture (`tech/architecture`), interface contracts (`tech/contract`), and governance policies (`product/policy`).
4. **Dual Workflows**:
   - **Direct Mode (Default):** For fast, routine, or flow-state tasks, co-evolve living specs and code directly. **Git diffs** serve as the change delta.
   - **Planned Mode (Optional):** For complex or breaking changes, **think deeply** and author an explicit SpecDelta (`DLT-*`), review with human/team, implement against the checklist, and reconcile.
5. **Invariant-Test Traceability**: Product capabilities (`CAP-*`) and architectural policies (`POL-*`) declare invariants. Automated tests in `tests/` explicitly reference these invariants.
6. **Automated Drift Governance**: `eidos drift` (or AI tool `eidos_detect_drift`) verifies that code modifications are accompanied by living spec updates before merging.

---

## 2. Directory Layout & Schema Taxonomy

```text
eidos/
├── system.adoc                               # type: product/system (Root system context)
├── personas/                                 # type: product/persona (Human & AI actor personas)
│   ├── product-lead.adoc
│   └── autonomous-agent.adoc
├── policies/                                 # type: product/policy (Invariants & governance rules)
│   ├── POL-001-git-as-single-source-of-truth.adoc
│   ├── POL-002-invariant-test-enforcement.adoc
│   └── POL-003-continuous-documentation-sync.adoc
├── domains/                                  # Bounded contexts
│   └── <domain-name>/                        # type: product/domain
│       ├── domain.adoc
│       └── capabilities/
│           └── <capability-name>.adoc        # type: product/capability
├── architecture/                             # Technical architecture
│   ├── system-architecture.adoc              # type: tech/architecture (Arc42 + C4 L1/L2)
│   ├── components/                           # type: tech/component (C4 L3 manifests)
│   │   └── <component-name>.adoc
│   └── contracts/                            # type: tech/contract (Interface definitions)
│       └── <contract-name>.adoc
├── design/                                   # Multi-Design Systems, Clients & UI contracts
│   ├── clients/                              # type: design/client & screens
│   ├── flows/                                # type: design/flow (User journey state machines)
│   └── systems/<system-name>/
│       ├── system.adoc                       # type: design/system
│       ├── tokens.json                       # W3C DTCG Design Tokens
│       └── components/<comp>.adoc            # type: design/ui-component
└── deltas/                                   # Optional Planned Mode mutations
    └── <delta-id>.adoc                       # type: delta/mutation
```

---

## 3. Agent Operating Workflow

### Step 1: Slice Context & Discover
1. **Extract a token-efficient context slice:**
   - Run `eidos context -f <files> -c <component> -t "<task description>"` or call MCP tool `eidos_get_task_context(files=..., component=..., task=...)`.
   - Inspect the returned slice: matched C4 Level 2/3 components, bound interface contracts (`CTR-*`), domain capabilities (`CAP-*`), active invariants (`INV-*`), and governing policies (`POL-*`).
2. **Inspect high-level system context if needed:**
   - Read `eidos/system.adoc` for goals and core pillars.
   - Read `eidos/architecture/system-architecture.adoc` for global C4 container topology.
   - Run `eidos components` or call MCP tool `eidos_list_components` to view monorepo boundaries.

### Step 2: Choose Workflow Mode
- **Routine task / Localized fix?** -> Use **Direct Mode** (co-evolve code in `src/` and specs in `eidos/` directly in the same turn/commit).
- **Complex / Breaking / Multi-component change?** -> Use **Planned Mode** (Think deeply, scaffold `DLT-*`, review with user, execute against checklist, apply delta).

### Step 3: Implement Code & Invariant Tests
1. Implement the requested changes in source code (`src/` or `packages/`).
2. Implement automated tests explicitly referencing invariant IDs (`INV-*`) using polyglot conventions (comments, docstrings, annotations, or test bindings).
3. Update the corresponding living specifications in `eidos/` (components, contracts, policies, capabilities).

### Step 4: Verify Zero Drift & Invariant Coverage
Run the verification gate:
```bash
eidos validate eidos
eidos drift --uncommitted
```
Ensure all checks pass with exit code `0`.

---

## 4. Available MCP Tools & CLI Commands

### MCP Tools Reference
When the Eidos MCP server is active, you have access to:
- `eidos_get_task_context(...)` (alias `eidos_context`): Extract a compact, token-efficient architectural subgraph context slice (C4 components, bound contracts, domain capabilities, active invariants, and governing policies).
- `eidos_detect_drift`: Inspect spec-to-code drift between git branches or uncommitted changes, validate schemas, and verify invariant test bindings.
- `eidos_reconcile`: Analyze recent code changes against the living spec catalog and return candidate specs to update.
- `eidos_validate(path="eidos")`: Validate all `.adoc` files in a path against schemas and verify bidirectional cross-references.
- `eidos_info()`: Return engine version, loaded v1 schemas, and supported platforms.
- `eidos_list_components(path="eidos/architecture")`: List all C4 containers, internal components, runtime dependencies, and monorepo package paths.
- `eidos_create_delta(...)`: Scaffold an optional SpecDelta mutation file for Planned Mode.
- `eidos_apply_delta(target="<delta-id>")`: Reconcile a SpecDelta in-place to `status: applied`.
- `eidos_create_spec(...)`: Scaffold a new living specification document.
- `eidos_read_spec(path="...")`: Parse and read an Eidos specification.
- `eidos_list_specs(...)`: Query living specifications with type and status filters.
- `eidos_delete_spec(...)`: Safely delete a living specification with reference safety checks.
- `eidos_init_repo(...)`: Programmatically initialize a repository with living specs and agent plugin.

### Primary CLI Commands
- `eidos context`: Extract architectural context slices (`-f`, `-c`, `-t`, `--format`).
- `eidos drift`: Detect spec-to-code drift and verify invariant test coverage (`--uncommitted`, `--base`).
- `eidos reconcile`: Propose living specification updates for recent code changes.
- `eidos validate`: Validate all living specification schemas and entity reference graphs.
- `eidos components`: Scan monorepo packages, runtimes, and dependencies.
- `eidos delta`: Manage flat SpecDelta change lifecycles (`status`, `create`, `apply`).
- `eidos spec`: Scaffold or safely delete living specifications (`create`, `delete`, `list`, `show`).
- `eidos plugin`: Install, validate, update, inspect, and remove portable agent plugins.
- `eidos serve` & `eidos build-site`: Preview and export the interactive Living Web Portal.

---

## 5. Specialized Eidos Skills Portfolio (14 Skills)

Depending on the specific phase of work, consult or invoke the specialized skill:
- **`eidos`**: Root orchestrator skill for living specification navigation, dual workflows, and agent guidelines.
- **`eidos-context`**: Extract token-efficient architectural context slices (C4 Level 2/3 topology, contracts, capabilities, invariants, and policies) for targeted tasks.
- **`eidos-init`**: Bootstrap a repository with Eidos living specifications, C4 topology, and governance policies.
- **`eidos-extract`**: Reverse-engineer and extract living specifications from brownfield codebases.
- **`eidos-product`**: Model business domains (`DOM-*`), capabilities (`CAP-*`), and personas (`PER-*`).
- **`eidos-architecture`**: Maintain Arc42 system context, C4 container topologies, component manifests, and interface contracts (`CTR-*`). Strictly enforces NO ADRs.
- **`eidos-design`**: Author multi-design systems, clients, user flows, W3C Design Tokens, and UI component state machines.
- **`eidos-explore`**: Conduct spikes, technical feasibility studies, and hypothesis-driven discovery (`EXP-*`).
- **`eidos-delta`**: Author, execute, and verify SpecDeltas (`DLT-*`) in Planned Mode.
- **`eidos-invariants`**: Formulate policy invariants (`POL-*`), capability invariants (`INV-*`), and maintain invariant-test traceability.
- **`eidos-review`**: Staff Architect architectural review, blast radius auditing, and contract SemVer evaluation.
- **`eidos-drift`**: Detect spec-to-code drift across branches or uncommitted changes, run the PR gate, and check polyglot test coverage via the `eidos_detect_drift` AI tool.
- **`eidos-reconcile`**: Reconcile code changes to living specifications in Direct Mode.
- **`eidos-portal`**: Preview, serve (`eidos serve`), and build static documentation sites (`eidos build-site`) with interactive C4 diagrams and token studios.
