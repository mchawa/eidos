---
name: eidos
description: Understand, navigate, author, and validate Eidos Git-native living specifications, Arc42/C4 architectures, and SpecDeltas.
---

# Eidos Engineering & Architecture Skill

This skill teaches AI agents how to reason about, navigate, author, and validate software systems built on **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## 1. What is Eidos?

Eidos replaces fragmented documentation, stale wikis, and historical write-only logs (like ADRs) with **living, schema-validated specifications** stored directly in the Git repository under the `eidos/` directory.

### Core Tenets
1. **Git as Single Source of Truth**: All specifications, code, and tests evolve together in atomic Git commits.
2. **AsciiDoc + YAML Frontmatter**: Every spec document is an `.adoc` file with structured, schema-validated YAML frontmatter and human/agent-readable AsciiDoc content.
3. **No ADRs (Architecture Decision Records)**: We do not write ADRs. Historical decision logs become neglected graveyards. Instead, we maintain living component manifests (`tech/component`), system architecture (`tech/architecture`), and governance policies (`product/policy`). Changes are proposed through active `SpecDelta` documents (`delta/mutation`) and reconciled into the living specs upon merge.
4. **Arc42 & C4 Model Alignment**:
   - **System Architecture** (`eidos/architecture/system-architecture.adoc`) covers Arc42 (Sections 1–8), C4 Level 1 (System Context), and C4 Level 2 (Containers).
   - **Component Manifests** (`eidos/architecture/components/<name>.adoc` or `packages/<name>/component.adoc`) define C4 Level 3 (Components), technologies, internal modules, and monorepo package paths.
   - **Interface Contracts** (`eidos/architecture/contracts/<name>.adoc`) define machine-readable protocol boundaries (CLI, REST, gRPC, IPC).
5. **Invariant-Test Traceability**: Product capabilities (`CAP-*`) and architectural policies (`POL-*`) declare invariants. Automated tests in `tests/` explicitly reference these invariants.

---

## 2. Directory Layout & Schema Taxonomy

A conformant Eidos project structures its specification under `eidos/`:

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
│       └── <capability-name>.adoc            # type: product/capability
├── architecture/                             # Technical architecture
│   ├── system-architecture.adoc              # type: tech/architecture (Arc42 + C4 L1/L2)
│   ├── components/                           # type: tech/component (C4 L3 manifests)
│   │   └── <component-name>.adoc
│   └── contracts/                            # type: tech/contract (Interface definitions)
│       └── <contract-name>.adoc
└── deltas/                                   # Change lifecycle
    ├── active/                               # type: delta/mutation (Proposed & accepted deltas)
    │   └── <delta-id>.adoc
    └── applied/                              # Merged and reconciled historical deltas
        └── <delta-id>.adoc
```

### Schemas (`schemas/v1/`)
Every `.adoc` document must declare its `type` and relative `schema` path in its YAML frontmatter:
- `product/system`: Root system definition, business goals, actors, domains.
- `product/domain`: Bounded context, ubiquitous language, aggregates.
- `product/capability`: Behavioral feature requirements, acceptance criteria, invariant IDs.
- `product/policy`: Architecture/governance rules, enforcement tier, invariant list.
- `product/persona`: Actor archetype, responsibilities, capabilities.
- `product/exploration`: Spikes, prototypes, research spikes.
- `tech/architecture`: Arc42 sections 1-8 + C4 System Context & Container diagrams.
- `tech/component`: Monorepo or standalone container manifest, C4 internal components, dependencies, `package_path`.
- `tech/contract`: Interface contracts (API, CLI, IPC), protocols, schemas.
- `delta/mutation`: Proposed specification and code mutations.

---

## 3. Monorepo Support: Dual Layout

Eidos seamlessly supports both centralized and distributed monorepo layouts:
- **Centralized Layout**: All component manifests reside in `eidos/architecture/components/<name>.adoc` with a `package_path` attribute pointing to `packages/<name>/` or `src/<name>/`.
- **Distributed/Co-located Layout**: Component manifests reside directly inside package directories (e.g. `packages/billing/component.adoc`) and are discovered recursively.

In both layouts:
- The component manifest explicitly links the specification to the source directory (`package_path`).
- Cross-references use document IDs (e.g. `CMP-ENGINE-001`, `CON-CLI-001`).

---

## 4. Agent Operating Workflow

When you are asked to implement a feature, fix a bug, or refactor an Eidos repository, follow this 6-step lifecycle:

### Step 1: Discover and Orient
1. Inspect the system context:
   - Read `eidos/system.adoc` to understand system purpose, target actors, and active domains.
   - Read `eidos/architecture/system-architecture.adoc` to understand C4 containers, tech stack, and cross-cutting concepts.
2. Discover components:
   - Run `eidos components` on CLI or call MCP tool `eidos_list_components`.
   - Read relevant component specs in `eidos/architecture/components/`.

### Step 2: Check Architectural Policies & Invariants
1. Scan `eidos/policies/` for architectural invariants (e.g. `POL-001`, `POL-002`).
2. Identify which domain capabilities (`eidos/domains/<domain>/<capability>.adoc`) relate to your task and note their acceptance criteria and invariant IDs (`CAP-*`).

### Step 3: Propose Changes via SpecDelta
Before writing code for non-trivial features or structural refactors:
1. Scaffold a new delta in `eidos/deltas/active/<delta_id>.adoc`:
   - Call MCP tool `eidos_create_delta(delta_id="DLT-2026-001", title="...", kind="feature_addition", motivation="...")`.
   - Or write the `.adoc` file directly with `type: delta/mutation`.
2. Document the proposed mutations: which living specs will be updated and which code paths will be touched.

### Step 4: Implement Code & Bind Tests
1. Implement the requested changes in source code (`src/` or `packages/`).
2. Implement automated tests in `tests/`.
3. Explicitly reference invariant IDs in tests:
   ```python
   # Invariant: CAP-VCS-SYNC-001
   def test_git_sync_atomic_commit():
       ...
   ```

### Step 5: Validate Specifications & Cross-References
Run validation to guarantee that no schemas are violated and no references are broken:
- CLI: `eidos validate eidos`
- MCP Tool: `eidos_validate(path="eidos")`

Ensure output reports `PASS` for all documents and 0 broken references.

### Step 6: Reconcile Living Specifications
1. Apply the specification diffs to the living specs (e.g. update `component.adoc` or add new capabilities in `eidos/domains/`).
2. Set the delta's status to `applied`:
   ```yaml
   status: applied
   ```
3. Move the delta file from `eidos/deltas/active/` to `eidos/deltas/applied/`.

---

## 5. Available MCP Tools

When the Eidos MCP server is active, you have access to:
- `eidos_validate(path="eidos")`: Validate all `.adoc` files in a path against schemas and verify cross-references.
- `eidos_info()`: Return engine version, loaded v1 schemas, and supported platforms.
- `eidos_list_components(path="eidos/architecture")`: List all C4 containers, internal components, runtime dependencies, and monorepo package paths.
- `eidos_read_spec(path="eidos/system.adoc")`: Parse and read an `.adoc` spec, returning structured frontmatter and body.
- `eidos_create_delta(delta_id, title, kind, motivation)`: Scaffold a new SpecDelta file in `eidos/deltas/active/`.

---

## 6. CLI Command Quick Reference

```bash
# Display system info and loaded schemas
eidos info

# Validate all specifications in eidos/
eidos validate eidos

# Inspect monorepo C4 components
eidos components

# Start stdio MCP server for agent clients
eidos mcp

# Install Eidos agent plugin into project scope (workspace):
eidos plugin install                        # Installs to .agent-plugins/eidos and .agents/plugins/eidos
eidos plugin install --target workspace     # Installs to .agent-plugins/eidos (agent-plugins.org v1.0.0)
eidos plugin install --target antigravity   # Installs to .agents/plugins/eidos (workspace plugin)
eidos plugin install --target claude        # Configures project .claude/mcp.json
eidos plugin install --target cursor        # Configures project .cursor/mcp.json

# Validate a plugin package against agent-plugins.org v1.0.0
eidos plugin validate plugins/eidos
```
