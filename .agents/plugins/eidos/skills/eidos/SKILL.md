---
name: eidos
description: >-
  Master architecture and engineering orchestrator for repositories managed by Eidos.
  MUST be consulted before modifying code, proposing architectural changes, adding features,
  refactoring, or altering living specifications in eidos/. Strictly enforces living specs
  (NO ADRs), dual workflows (Direct vs. Planned), invariant-test traceability, and zero drift.
---

# Eidos Engineering & Architecture Skill

This skill teaches AI agents how to reason about, navigate, author, and validate software systems built on **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## 1. Constitutional Guardrails

Whenever operating within a repository containing an `eidos/` directory, adhere strictly to these 5 non-negotiable rules:

1. **Single Source of Truth**: All requirements, topologies, container manifests, and policies are codified inside `eidos/` as living AsciiDoc (`.adoc`) files with YAML frontmatter. Continuously synchronize specs with code.
2. **Strictly Zero ADRs**: Never create Architecture Decision Records or dead markdown logs (`docs/adr/`, `decisions/`). Git preserves history; record all architectural evolution directly in living specifications.
3. **Dual Workflows (Direct vs. Planned)**:
   - **Direct Mode (Default):** Co-evolve code in `src/` and specs in `eidos/` in the same turn/commit. Git diffs serve as the change delta.
   - **Planned Mode:** For breaking, cross-container, or high-risk changes, scaffold a SpecDelta (`DLT-*`) via `eidos delta create <id>`, implement against the checklist, and reconcile via `eidos delta apply <id>`.
4. **Invariant-Test Traceability (`POL-002`)**: Every policy (`POL-*`) and capability (`CAP-*`) declares testable invariants (`INV-*`). All automated tests must explicitly reference these IDs.
5. **Zero-Drift & Graph Integrity**: Never conclude a task with broken references, unvalidated schemas, or unmanaged drift. Always pass `eidos validate eidos && eidos drift --uncommitted`.

---

## 2. C4 Taxonomy & Annotated Directory Tree

Eidos models systems using Simon Brown's canonical **C4 Model** (L1 System $\rightarrow$ L2 Containers $\rightarrow$ L3 Components $\rightarrow$ L4 Code). Every document follows strict prefix conventions:

```text
eidos/
├── system.adoc                               # SYS-*: Root system context, vision & core pillars
├── personas/<name>.adoc                      # PER-*: Human & AI actor personas
├── policies/<name>.adoc                      # POL-*: Governance policies & invariants (INV-*)
├── domains/<name>/                           # DOM-*: Bounded business domains
│   └── capabilities/<name>.adoc              # CAP-*: Product capabilities defining requirements
├── architecture/
│   ├── system-architecture.adoc              # ARCH-*: Arc42 & global C4 container topology
│   ├── containers/<name>.adoc                # CNT-*: C4 L2 deployables (contains CMP-* components)
│   └── contracts/<name>.adoc                 # CTR-*: Boundary contracts (CLI, OpenAPI, REST, gRPC)
├── design/
│   ├── clients/<name>/                       # CLT-*: Client surfaces (screens/ SCR-*)
│   ├── flows/<name>.adoc                     # FLOW-*: User journey state machines
│   └── systems/<name>/                       # DS-*: Design systems (tokens.json, components/ UIC-*)
├── explorations/<name>.adoc                  # EXP-*: Technical spikes & feasibility discovery
└── deltas/<name>.adoc                        # DLT-*: Planned Mode semantic mutations
```

---

## 3. Operational Lifecycle & Verification Gate

1. **Context Slicing**: Run `eidos context -f <files> -c <container> -t "<task>" --format json` to extract only relevant C4 containers, contracts (`CTR-*`), capabilities (`CAP-*`), and invariants (`INV-*`).
2. **Workflow Selection**:
   - *Direct Mode (Routine/local)*: Modify code in `src/` and specs in `eidos/` directly in the same turn.
   - *Planned Mode (Breaking/cross-container)*: Scaffold `DLT-*` via `eidos delta create`, implement against checklist, reconcile via `eidos delta apply`.
   - *Exploration Spike (Feasibility discovery)*: Scaffold `EXP-*` via `eidos spec create exploration EXP-YYYY-NNN`.
3. **Implementation & Invariant Testing**: Implement changes in source code. Automated tests in `tests/` must explicitly bind to invariant IDs (`INV-*` or `POL-*`).
4. **Living Spec Co-Evolution**: Synchronize modified containers (`eidos/architecture/containers/`), contracts (`eidos/architecture/contracts/`), design systems (`eidos/design/`), and domain capabilities in the same commit.
5. **Pre-Completion Gate**: Verify the 6-point self-audit before concluding:
   - [ ] All code changes reflected in `eidos/` living specifications (containers, contracts, capabilities).
   - [ ] Frontend / styling changes co-evolved with `eidos/design/` (tokens.json, UIC-* components, SCR-* screens).
   - [ ] Zero ADRs or static decision markdown files created.
   - [ ] Automated tests bound to `INV-*` or `POL-*` IDs.
   - [ ] `eidos validate eidos` passes with exit code 0.
   - [ ] `eidos drift --uncommitted` confirms zero unmanaged modifications.
   ```bash
   eidos validate eidos && eidos drift --uncommitted
   ```

---

## 4. Core CLI Command Matrix & Machine-Readable Outputs

All inspection and mutation commands provide deterministic `--json` outputs for agent tool calling:

| CLI Command | Arguments & Flags | Primary Use Case |
| :--- | :--- | :--- |
| `eidos context` | `-f <files> -c <container> -t <task> --tier <tier> --format json` | Extract token-efficient architectural context slice for files/tasks |
| `eidos drift` | `--uncommitted --json` or `-b <base> --json` | Detect code-to-spec drift, validate schemas, and check test bindings |
| `eidos reconcile` | `--uncommitted --json` or `--prompt` | Analyze recent Git diffs and suggest living spec updates |
| `eidos validate` | `eidos --json` | Validate all `.adoc` schemas and cross-reference integrity |
| `eidos containers`<br>`eidos components` | `[container_id]` | List C4 Level 2 containers and Level 3 in-process components |
| `eidos delta create` | `<id> -t <title> -k <kind> -m <motivation>` | Scaffold a SpecDelta mutation document (`DLT-*`) for Planned Mode |
| `eidos delta apply` | `<id>` | Reconcile an applied SpecDelta in-place to `status: applied` |
| `eidos spec create` | `<type> <id> -t <title> ...` | Scaffold a living specification (`container`, `contract`, `capability`, etc.) |
| `eidos spec show` | `<id_or_path>` | Parse structured frontmatter metadata and AsciiDoc sections |
| `eidos spec list` | `[--type <type>] [--status <status>]` | Query specs filtered by type (`tech/container`, `product/capability`) or status |
| `eidos spec delete` | `<id_or_path> [--force]` | Safely delete a living specification with graph reference checks |
| `eidos info` | — | Inspect engine version, loaded JSON schemas, and platform status |
| `eidos init` | `[path]` | Initialize a repository with standard living specifications |

---

## 5. Polyglot Invariant Test Tagging (`POL-002`)

All automated tests verifying capability invariants (`INV-*`) or policies (`POL-*`) must declare explicit bindings:

| Language / Framework | Invariant Test Tagging Convention (`POL-002`) |
| :--- | :--- |
| **Python (pytest)** | `@pytest.mark.invariant("INV-ID")` or docstring `"""INV-ID: ..."""` |
| **TypeScript / JS (Vitest/Jest)** | `test('INV-ID: description', () => ...)` or JSDoc `@invariant INV-ID` |
| **Go (`testing`)** | `// Invariant: INV-ID` comment directly above test function |
| **Rust (`#[test]`)** | `/// Invariant: INV-ID` doc comment directly above `#[test]` |
| **Java / Kotlin (JUnit 5)** | `@Tag("INV-ID")` or `@DisplayName("INV-ID: ...")` |

---

## 6. Specialized Skills Routing Directory (14 Skills)

When deeper guidance is needed for a specific architectural phase, consult the specialized skill:

| Skill | Focus Area & When to Dispatch |
| :--- | :--- |
| **`eidos`** | **Master Orchestrator**: High-level workflow, governance principles, and cross-cutting rules. |
| **`eidos-context`** | **Context Slicing**: Extract targeted C4 Level 2/3 topology, contracts, and active invariants. |
| **`eidos-architecture`** | **Arc42 & C4 Topology**: Author C4 containers (`CNT-*`), in-process components (`CMP-*`), and interface contracts (`CTR-*`). |
| **`eidos-invariants`** | **Policy & Invariants**: Formulate policies (`POL-*`), capability invariants (`INV-*`), and maintain test traceability. |
| **`eidos-drift`** | **Drift Detection**: Inspect spec-to-code drift between branches or uncommitted changes via `eidos drift`. |
| **`eidos-reconcile`** | **Direct Mode Reconciliation**: Inspect Git diffs, map code changes to living specifications, and sync specs. |
| **`eidos-delta`** | **Planned Mode Mutations**: Scaffold, manage, verify, and reconcile SpecDeltas (`DLT-*`). |
| **`eidos-explore`** | **Spikes & Feasibility**: Scope technical spikes (`EXP-*`), test hypotheses, and graduate proven spikes. |
| **`eidos-product`** | **Domain & Capability Modeling**: Model bounded contexts (`DOM-*`), capabilities (`CAP-*`), and personas (`PER-*`). |
| **`eidos-design`** | **Multi-Design Systems**: Author W3C tokens (`tokens.json`), client surfaces (`CLT-*`), screens (`SCR-*`), and flows (`FLOW-*`). |
| **`eidos-review`** | **Architectural Review**: Perform Staff Architect audits on blast radius, SemVer breaking changes, and invariant test coverage. |
| **`eidos-extract`** | **Brownfield Reverse-Engineering**: Systematically extract Eidos living specifications from pre-existing codebases. |
| **`eidos-init`** | **Repository Bootstrapping**: Initialize a clean Git repository with living spec skeletons and agent configuration. |
| **`eidos-portal`** | **Living Web Portal**: Preview (`eidos serve`), build, and publish interactive documentation sites with C4 visualizers. |
