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
│   └── POL-002-invariant-test-enforcement.adoc
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
├── design/                                   # Multi-Design Systems & UI contracts
│   └── systems/<system-name>/
│       ├── system.adoc                       # type: design/system
│       ├── tokens.json                       # W3C DTCG Design Tokens
│       └── components/<comp>.adoc            # type: design/ui-component
└── deltas/                                   # Optional Planned Mode mutations
    └── <delta-id>.adoc                       # type: delta/mutation
```

---

## 3. Agent Operating Workflow

### Step 1: Discover & Orient
1. Inspect the system context:
   - Read `eidos/system.adoc` for goals and core pillars.
   - Read `eidos/architecture/system-architecture.adoc` for C4 containers and runtime stacks.
2. Discover components:
   - Run `eidos components` or call MCP tool `eidos_list_components`.

### Step 2: Choose Workflow Mode
- **Routine task / Fast fix?** -> Use **Direct Mode** (co-evolve code in `src/` and specs in `eidos/` directly).
- **Complex / Breaking / High-risk architecture?** -> Use **Planned Mode** (Think deeply, scaffold `DLT-*`, review with user, execute).

### Step 3: Implement Code & Invariant Tests
1. Implement the requested changes in source code (`src/` or `packages/`).
2. Implement automated tests in `tests/` explicitly referencing invariant IDs (`INV-*`).
3. Update the corresponding living specifications in `eidos/` (components, contracts, policies, capabilities).

### Step 4: Verify Zero Drift & Invariant Coverage
Run the verification gate:
```bash
eidos validate eidos
eidos drift
```
Ensure all checks pass with exit code `0`.

---

## 4. Available MCP Tools

When the Eidos MCP server is active, you have access to:
- `eidos_detect_drift`: Inspect spec-to-code drift between git branches or uncommitted changes, validate schemas, and verify invariant test bindings.
- `eidos_reconcile`: Analyze recent code changes against the living spec catalog and return candidate specs to update.
- `eidos_validate(path="eidos")`: Validate all `.adoc` files in a path against schemas and verify cross-references.
- `eidos_info()`: Return engine version, loaded v1 schemas, and supported platforms.
- `eidos_list_components(path="eidos/architecture")`: List all C4 containers, internal components, runtime dependencies, and monorepo package paths.
- `eidos_create_delta(...)`: Scaffold an optional SpecDelta mutation file for Planned Mode.
- `eidos_apply_delta(target="<delta-id>")`: Reconcile a SpecDelta in-place to `status: applied`.
- `eidos_create_spec(...)`: Scaffold a new living specification document.
- `eidos_read_spec(path="...")`: Parse and read an Eidos specification.

---

## 5. Specialized Eidos Skills Portfolio

Depending on the specific phase of work, consult or invoke the specialized skill:
- **`eidos-init`**: Bootstrap a repository with Eidos living specifications, C4 topology, and governance policies.
- **`eidos-extract`**: Reverse-engineer and extract living specifications from brownfield codebases.
- **`eidos-product`**: Model business domains (`DOM-*`), capabilities (`CAP-*`), and personas (`PER-*`).
- **`eidos-architecture`**: Maintain Arc42 system context, C4 container topologies, component manifests, and interface contracts (`CTR-*`).
- **`eidos-design`**: Author multi-design systems, W3C Design Tokens, and UI component state machines.
- **`eidos-explore`**: Conduct spikes, technical feasibility studies, and hypothesis-driven discovery (`EXP-*`).
- **`eidos-delta`**: Author, execute, and verify SpecDeltas (`DLT-*`) in Planned Mode.
- **`eidos-invariants`**: Formulate policy invariants (`POL-*`), capability invariants (`INV-*`), and maintain invariant-test traceability.
- **`eidos-review`**: Staff Architect architectural review, blast radius auditing, and contract SemVer evaluation.
- **`eidos-drift`**: Detect spec-to-code drift across branches or uncommitted changes, run the PR gate, and check test coverage via the `eidos_detect_drift` AI tool.
- **`eidos-reconcile`**: Reconcile code changes to living specifications in Direct Mode.
- **`eidos-portal`**: Preview, serve (`eidos serve`), and build static documentation sites (`eidos build-site`) with interactive C4 diagrams and token studios.
