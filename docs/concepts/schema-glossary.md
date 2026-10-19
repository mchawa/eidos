# Living Specification Schema Glossary

Every specification document in an Eidos repository is an **AsciiDoc (`.adoc`)** file containing structured **YAML frontmatter**. Frontmatter syntax and cross-references are strictly validated against canonical schemas bundled with the Eidos engine (`assets/schemas/v1/`).

This glossary details all **16 document schemas** (plus shared `$defs` in `common/defs.yaml`), their ID conventions, directory locations, required fields, and architectural intent.

---

## Specification Taxonomy Overview

| Document Type (`type`) | ID Prefix | Default Path in `eidos/` | Canonical Schema | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `product/system` | `SYS-*` | `system.adoc` | `product/system.schema.yaml` | Root system identity, archetypes, and technical stack profile. |
| `product/domain` | `DOM-*` | `domains/<domain>/domain.adoc` | `product/domain.schema.yaml` | DDD bounded context, architectural boundaries, and ownership. |
| `product/capability` | `CAP-*` | `domains/<domain>/capabilities/*.adoc` | `product/capability.schema.yaml` | Granular product feature bridging user JTBD with test-bound invariants. |
| `product/policy` | `POL-*` | `policies/POL-*.adoc` | `product/policy.schema.yaml` | Non-negotiable system invariants, compliance rules, and quality gates. |
| `product/persona` | `PER-*` | `personas/*.adoc` | `product/persona.schema.yaml` | Human and autonomous AI actor roles, goals, and pain points. |
| `product/exploration` | `EXP-*` | `explorations/*.adoc` | `product/exploration.schema.yaml` | Technical spikes, hypotheses, and feasibility discovery. |
| `tech/architecture` | `ARCH-*` | `architecture/system-architecture.adoc` | `tech/architecture.schema.yaml` | Global Arc42 architecture, C4 Level 1-2 container topology. |
| `tech/container` | `CNT-*` | `architecture/containers/*.adoc` | `tech/container.schema.yaml` | C4 Level 2 Container manifests, deployables, runtimes, and nested components. |
| `tech/component` | `CMP-*` | `architecture/components/*.adoc` | `tech/component.schema.yaml` | Promoted C4 Level 3 Component manifests, in-process APIs, and component invariants. |
| `tech/contract` | `CTR-*` | `architecture/contracts/*.adoc` | `tech/contract.schema.yaml` | Interface protocol contracts (OpenAPI, gRPC, CLI, AsyncAPI, JSON Schema). |
| `design/system` | `DS-*` | `design/systems/<system>/system.adoc` | `design/system.schema.yaml` | Multi-design system catalog, brand themes, and W3C DTCG tokens. |
| `design/ui-component` | `UIC-*` | `design/systems/<system>/components/*.adoc` | `design/ui-component.schema.yaml` | UI component contract: variants, states, accessibility, and tests. |
| `design/screen` | `SCR-*` | `design/clients/<client>/screens/*.adoc` | `design/screen.schema.yaml` | Screen views, routes, layout slots, and interaction state machines. |
| `design/client` | `CLT-*` | `design/clients/<client>/client.adoc` | `design/client.schema.yaml` | Client application platform, runtime surface, design system binding, and owned screens. |
| `design/flow` | `FLOW-*` | `design/clients/<client>/flows/*.adoc` | `design/flow.schema.yaml` | Interaction user journeys and cross-client flows with visual Mermaid diagrams. |
| `delta/mutation` | `DLT-*` | `deltas/dlt-*.adoc` | `delta/delta.schema.yaml` | Planned Mode semantic mutations, `expect:` post-conditions, blast radius, and tombstones. |

> [!NOTE]
> **Shared Definitions (`assets/schemas/v1/common/defs.yaml`)**: Reusable definitions for `#/$defs/decision` (`DEC-*`), `#/$defs/invariant` (`INV-*` with `test_binding`), `#/$defs/inline_component` (`CMP-*`), `#/$defs/lifecycle_status`, `#/$defs/delta_status`, `#/$defs/exploration_status`, `#/$defs/code_paths`, `#/$defs/quality_attributes`, `#/$defs/in_process_interfaces`, `#/$defs/contracts_block`, `#/$defs/external_design_references`, and `x-eidos-ref` entity references are automatically injected across schemas by `SpecValidator`.

---

## 1. Product & Business Layer

### `product/system`
* **Schema**: `schemas/v1/product/system.schema.yaml`
* **ID Convention**: `SYS-<NAME>-<NUMBER>` (e.g., `SYS-EIDOS-001`)
* **Location**: `eidos/system.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `archetypes`, `business_identity`, `technical_profile`
* **Intent**: Anchors the root product vision, target personas, technical runtimes, and platform compatibility.

```yaml
---
type: product/system
schema: schemas/v1/product/system.schema.yaml
id: SYS-EIDOS-001
title: "Eidos: Agent-Agnostic Git-Native Development Ecosystem"
status: active
archetypes:
  - framework
  - cli_tool
business_identity:
  vision: "Provide an open, GitOps-driven AI development ecosystem."
  target_users:
    - "Software Engineers & Architects"
    - "Autonomous AI Coding Agents"
  value_proposition: "Eliminates spec-to-code drift through test-bound living specifications and deterministic AST context slicing."
technical_profile:
  primary_languages:
    - "Python"
    - "TypeScript"
  deployment_targets:
    - "Local Workstation (CLI)"
    - "Cloud CI/CD Runners"
---
```

---

### `product/domain`
* **Schema**: `schemas/v1/product/domain.schema.yaml`
* **ID Convention**: `DOM-<NAME>` (e.g., `DOM-SPEC-ENGINE`, `DOM-AUTH`)
* **Location**: `eidos/domains/<domain-name>/domain.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `classification`, `boundaries`
* **Intent**: Implements Domain-Driven Design (DDD) bounded contexts. Classifies domains as `core`, `supporting`, or `generic`, and sets architectural boundaries.

```yaml
---
type: product/domain
schema: schemas/v1/product/domain.schema.yaml
id: DOM-SPEC-ENGINE
title: "Specification Engine Domain"
status: active # draft | planned | active | deprecated | retired
classification: core # core | supporting | generic
boundaries:
  responsibilities:
    - "Living specification parsing, AST generation, and schema validation."
    - "AsciiDoc + YAML frontmatter parsing and JSON Schema validation."
  out_of_scope:
    - "Git working-tree diff analysis (handled by DOM-VCS-LIFECYCLE)."
---
```

---

### `product/capability`
* **Schema**: `schemas/v1/product/capability.schema.yaml`
* **ID Convention**: `CAP-<DOMAIN>-<NUMBER>` (e.g., `CAP-PROD-001`, `CAP-AUTH-002`)
* **Location**: `eidos/domains/<domain-name>/capabilities/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `domain`, `status`, `business`, `technical`
* **Intent**: The core building block of functionality. Bridges human Jobs-to-be-Done (JTBD) with technical containers, components, contracts, and test-bound invariants (`INV-*`).

```yaml
---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: CAP-PROD-001
title: "Living Baseline Specification Engine"
domain: DOM-SPEC-ENGINE
status: active # draft | planned | active | deprecated | retired
version: "1.5.0"

business:
  personas:
    - eidos/personas/product-lead.adoc
  jtbd: "When defining system rules, I want living specs in Git, so docs never rot."
  policies:
    - eidos/policies/POL-001-git-as-single-source-of-truth.adoc

technical:
  containers:
    - "CNT-EIDOS-CORE"
  components:
    - "CMP-SPEC-PARSER"
    - "CMP-SPEC-VALIDATOR"
  contracts:
    - "CTR-EIDOS-CLI"
  invariants:
    - id: INV-SPEC-VALIDATION
      rule: "Every .adoc file with YAML frontmatter must validate against schema."
      test_binding: "tests/test_validator.py::test_validate_eidos_specifications"
---
```

---

### `product/policy`
* **Schema**: `schemas/v1/product/policy.schema.yaml`
* **ID Convention**: `POL-<NUMBER>-<NAME>` (e.g., `POL-001-GIT-SOURCE-OF-TRUTH`)
* **Location**: `eidos/policies/POL-*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `category`, `severity`, `invariants`
* **Intent**: Declarative system-wide governance laws (governance, architectural, security, compliance, testing) enforced via test-bound invariants (`INV-*`).

```yaml
---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: POL-002-INVARIANT-TEST-ENFORCEMENT
title: "Mandatory Invariant Test Binding"
status: active # draft | planned | active | deprecated | retired
category: testing # governance | architectural | security | compliance | testing
severity: blocking # blocking | warning | advisory
invariants:
  - id: INV-POL-002
    rule: "Every active invariant (INV-*) MUST declare a test_binding that resolves to an existing test function."
    test_binding: "tests/test_validator.py::test_validate_eidos_specifications"
---
```

---

### `product/persona`
* **Schema**: `schemas/v1/product/persona.schema.yaml`
* **ID Convention**: `PER-<NAME>` (e.g., `PER-PRODUCT-LEAD`, `PER-AUTONOMOUS-AGENT`)
* **Location**: `eidos/personas/*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `role_type`, `goals`, `pain_points`
* **Intent**: Captures the needs, workflows, goals, and frustrations of human users and autonomous AI coding agents.

```yaml
---
type: product/persona
schema: schemas/v1/product/persona.schema.yaml
id: PER-AUTONOMOUS-AGENT
title: "Autonomous AI Coding Agent"
status: active # draft | planned | active | deprecated | retired
role_type: autonomous_agent # human | autonomous_agent | system_service
goals:
  - "Safely mutate code without violating implicit domain rules."
  - "Retrieve deterministic schema definitions and architectural context via CLI."
pain_points:
  - "Stale wikis and missing context leading to hallucinated APIs."
---
```

---

### `product/exploration`
* **Schema**: `schemas/v1/product/exploration.schema.yaml`
* **ID Convention**: `EXP-<YEAR>-<NUMBER>` (e.g., `EXP-2026-002`)
* **Location**: `eidos/explorations/*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `hypothesis`, `exit_criteria`
* **Intent**: Formalizes technical spikes and feasibility studies. Prevents prototype code from leaking into production by requiring measurable exit criteria and a recorded `decision` (`adopted`, `discarded`, or `pivoted`).

```yaml
---
type: product/exploration
schema: schemas/v1/product/exploration.schema.yaml
id: EXP-2026-002
title: "Embedded SQLite Knowledge Graph & FTS5 Retrieval"
status: adopted # proposed | in_progress | adopted | discarded
hypothesis: "An embedded SQLite3 database with WAL mode and FTS5 BM25 can serve tiered context slices in <5ms with zero external dependencies."
questions_to_answer:
  - "What is the incremental dirty-check latency across 150+ files?"
spikes_to_run:
  - name: "spike-sqlite-fts5"
    target: "Benchmark mtime_ns + SHA-256 sync and BM25 ranking."
exit_criteria:
  max_duration: "3_days"
  benchmark: "Incremental dirty check < 5ms and context slice query < 3ms."
decision:
  outcome: adopted # adopted | discarded | pivoted
  resulting_delta_id: DLT-2026-011
  learnings: "SQLite WAL + FTS5 achieved 1.8ms query latency using Python stdlib sqlite3."
---
```

---

## 2. Technical & Architecture Layer

### `tech/architecture`
* **Schema**: `schemas/v1/tech/architecture.schema.yaml`
* **ID Convention**: `ARCH-<NAME>-<NUMBER>` (e.g., `ARCH-EIDOS-001`)
* **Location**: `eidos/architecture/system-architecture.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `topology_style`, `c4_context`, `c4_containers`, `solution_strategy`, `cross_cutting_concepts`, `deployment_view`
* **Intent**: Global architectural baseline combining Arc42 (Sections 1–12) with C4 Model Levels 1 (System Context) and 2 (Container Topology) and system-wide `decisions:` (`DEC-*`). **Strictly replaces ADRs**.

```yaml
---
type: tech/architecture
schema: schemas/v1/tech/architecture.schema.yaml
id: ARCH-EIDOS-001
title: "Eidos Global System Architecture"
status: active
topology_style: "monorepo_orchestrator"
c4_context:
  actors:
    - "eidos/personas/product-lead.adoc"
    - "eidos/personas/autonomous-agent.adoc"
  external_systems:
    - name: "Git Repository & CI/CD Pipeline"
      role: "Local and remote Git version control and CI verification gates"
      protocol: "Git CLI / Subprocess"
c4_containers:
  - container: "CNT-EIDOS-CORE"
    title: "Eidos Living Specification & Design Core Engine"
    container_type: "library"
    runtime: "Python 3.12 / Hatchling / PyYAML / jsonschema"
    source_path: "src/eidos/core"
solution_strategy:
  - "GitOps State Machine: Git repository is the sole source of authoritative truth."
cross_cutting_concepts:
  data_storage: "Git-backed AsciiDoc + YAML frontmatter with SQLite cache (eidos/.cache/index.db)."
deployment_view:
  hosting_model: "Local developer workstation CLI and CI/CD runners."
  ci_cd_engine: "GitHub Actions"
---
```

---

### `tech/container`
* **Schema**: `schemas/v1/tech/container.schema.yaml`
* **ID Convention**: `CNT-<NAME>` (e.g., `CNT-EIDOS-CORE`, `CNT-EIDOS-CLI`)
* **Location**: `eidos/architecture/containers/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `container_type`, `runtime`, `source_path`
* **Intent**: C4 Level 2 Container manifest (deployable unit, library, service, or CLI). Declares inline C4 Level 3 components (`components[]`, `#/$defs/inline_component`), provided/consumed interface contracts (`CTR-*`), container invariants (`INV-*`), and embedded architectural decisions (`DEC-*`). Promoted standalone components (`eidos/architecture/components/*.adoc`) link bottom-up via their own `container: CNT-*` property.

```yaml
---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-EIDOS-CORE
title: "Eidos Core Engine"
status: active # draft | planned | active | deprecated | retired
container_type: library # service | library | cli_app | web_app | worker | daemon
runtime: "Python 3.12+"
source_path: "src/eidos/core"
domain: DOM-SPEC-ENGINE
components:
  - id: CMP-SPEC-PARSER
    title: "SpecParser"
    responsibility: "Parses AsciiDoc files with YAML frontmatter."
    source_path: "src/eidos/core/spec/parser.py"
contracts:
  provides:
    - "CTR-EIDOS-CLI"
  consumes: []
decisions:
  - id: DEC-INDEX-SQLITE-WAL
    title: "Use Embedded SQLite WAL + FTS5 Instead of External Vector DB"
    decision: "Store the architectural knowledge graph and lexical index in local SQLite3 with WAL mode and FTS5 BM25."
    rationale: "Guarantees zero external runtime dependencies and sub-2ms local queries."
    rejected_alternatives:
      - option: "ChromaDB / LanceDB Vector Store"
        reason_rejected: "Requires external model weights or API calls, violating POL-004."
    graduated_from: EXP-2026-002
---
```

---

### `tech/component`
* **Schema**: `schemas/v1/tech/component.schema.yaml`
* **ID Convention**: `CMP-<NAME>` (e.g., `CMP-SPEC-VALIDATOR`, `CMP-AUTH-TOKEN-MGR`)
* **Location**: Centralized in `eidos/architecture/components/<component>.adoc` (`INV-COMPONENT-CENTRALIZATION-MANDATE`; never placed inside `src/`)
* **Required Properties**: `type`, `schema`, `id`, `title`, `container`, `status`, `responsibility`, `source_path`
* **Intent**: Dedicated C4 Level 3 Component specification in the Centralized Hybrid C4 Architecture (promoted via `eidos spec component promote <ID>` and composed from `#/$defs/inline_component`). Binds to its parent `CNT-*` container (`INV-COMPONENT-CONTAINER-AFFINITY`), maps to implementation files via `source_path`, and declares in-process APIs (`interfaces.provides`), consumed components (`interfaces.consumes`), quality attributes, component invariants (`INV-*`), and embedded decisions (`DEC-*`).

```yaml
---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: CMP-SPEC-VALIDATOR
title: "Specification & Schema Validator"
container: CNT-EIDOS-CORE
status: active # draft | planned | active | deprecated | retired
source_path: "src/eidos/core/spec/validator.py"
domain: DOM-SPEC-ENGINE
responsibility: "Validates specifications against v1 JSON Schemas and enforces x-eidos-ref graph integrity."
interfaces:
  provides:
    - signature: "validate_document(doc: SpecDocument) -> ValidationResult"
      description: "Validates a single parsed specification document against its JSON Schema."
  consumes:
    - "CMP-SPEC-PARSER"
invariants:
  - id: INV-COMPONENT-CENTRALIZATION-MANDATE
    rule: "Every dedicated tech/component specification MUST reside centrally within eidos/architecture/components/."
    test_binding: "tests/test_colocated_components.py::test_spike_1_centralization_mandate_enforcement"
---
```

---

### `tech/contract`
* **Schema**: `schemas/v1/tech/contract.schema.yaml`
* **ID Convention**: `CTR-<NAME>` (e.g., `CTR-EIDOS-CLI`, `CTR-EIDOS-PORTAL`)
* **Location**: `eidos/architecture/contracts/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `protocol`, `container`, `specification_file`
* **Intent**: Formal interface protocol boundary contract owned by a C4 Level 2 Container (`CNT-*`, `INV-INTERFACE-BOUNDARY-GOVERNANCE`). Binds APIs, CLI signatures, or RPC services to concrete schemas or implementation entrypoints.

```yaml
---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: CTR-EIDOS-CLI
title: "Eidos CLI Interface Contract"
status: active # draft | planned | active | deprecated | retired
protocol: cli_args # openapi_3_1 | protobuf_grpc | graphql | asyncapi | cli_args | json_schema | http_json
container: "CNT-EIDOS-CLI"
specification_file: "src/eidos/cli/main.py"
---
```

---

## 3. Design System & UI/UX Layer

### `design/system`
* **Schema**: `schemas/v1/design/system.schema.yaml`
* **ID Convention**: `DS-<NAME>` (e.g., `DS-CORE`, `DS-ACME`)
* **Location**: `eidos/design/systems/<system>/system.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `tokens`
* **Intent**: Defines brand identity, token manifest path, and multi-brand inheritance (`extends`, `override_mode`).

```yaml
---
type: design/system
schema: schemas/v1/design/system.schema.yaml
id: DS-ACME
title: "Acme Enterprise Theme"
status: active # draft | planned | active | deprecated | retired
extends: "DS-CORE"
override_mode: "token_and_component" # token_only | token_and_component | full_custom
tokens:
  spec_format: "w3c_design_tokens"
  manifest: "tokens.json"
---
```

---

### `design/ui-component`
* **Schema**: `schemas/v1/design/ui-component.schema.yaml`
* **ID Convention**: `UIC-<SYSTEM>-<NAME>` (e.g., `UIC-CORE-BUTTON`)
* **Location**: `eidos/design/systems/<system>/components/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `design_system`, `status`
* **Intent**: Visual UI component specification. Defines visual variants, interactive states, base component overrides (`implements`), accessibility criteria (WCAG, ARIA), and test bindings (Storybook, unit tests).

```yaml
---
type: design/ui-component
schema: schemas/v1/design/ui-component.schema.yaml
id: UIC-CORE-BUTTON
title: "Action Button"
design_system: "DS-CORE"
status: active # draft | planned | active | deprecated | retired
category: inputs
variants:
  - name: "primary"
    description: "High-emphasis action."
accessibility:
  wcag_level: "AA"
  aria_role: "button"
test_bindings:
  storybook: "packages/ui/src/Button.stories.tsx"
---
```

---

### `design/screen`
* **Schema**: `schemas/v1/design/screen.schema.yaml`
* **ID Convention**: `SCR-<NAME>` (e.g., `SCR-CHECKOUT`, `SCR-PORTAL-HOME`)
* **Location**: `eidos/design/clients/<client>/screens/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `route`, `status`
* **Intent**: Pure user-facing view/screen definition declaring URL routes, component layout slots, and state machines (`loading`, `empty`, `active`, `error`). Referenced from outside by `design/client.screens`, `design/flow.steps[].screen`, and `product/capability.experience.screens`.

```yaml
---
type: design/screen
schema: schemas/v1/design/screen.schema.yaml
id: SCR-CHECKOUT
title: "Order Checkout Screen"
route: "/checkout"
status: active # draft | planned | active | deprecated | retired
slots:
  payment_form: "UIC-*-PAYMENT-FORM"
states:
  - name: "loading"
    description: "Skeleton placeholder."
  - name: "active"
    description: "Interactive payment form."
---
```

---

### `design/client`
* **Schema**: `schemas/v1/design/client.schema.yaml`
* **ID Convention**: `CLT-<NAME>` (e.g., `CLT-WEB-PORTAL`, `CLT-MOBILE-APP`)
* **Location**: `eidos/design/clients/<client-name>/client.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `platform`, `design_system`
* **Intent**: Client application platform or tenant surface manifest. Encapsulates runtime platform (`web`, `mobile-ios`, `mobile-android`, `desktop`, `tui`), binds primary design systems and themes, links to C4 containers (`CNT-*`), and organizes owned screens and interaction flows. Referenced from outside by `product/capability.experience.clients`.

```yaml
---
type: design/client
schema: schemas/v1/design/client.schema.yaml
id: CLT-WEB-PORTAL
title: "Web Living Documentation Portal"
status: active # draft | planned | active | deprecated | retired
platform: "web"
design_system: "DS-CORE"
container: "CNT-EIDOS-PORTAL"
screens:
  - "eidos/design/clients/web-portal/screens/portal-home.adoc"
  - "eidos/design/clients/web-portal/screens/portal-catalog.adoc"
flows:
  - "eidos/design/clients/web-portal/flows/spec-exploration.adoc"
---
```

---

### `design/flow`
* **Schema**: `schemas/v1/design/flow.schema.yaml`
* **ID Convention**: `FLOW-<NAME>` (e.g., `FLOW-PORTAL-EXPLORATION`, `FLOW-CLI-SERVE-AND-BROWSE`)
* **Location**: `eidos/design/clients/<client>/flows/<name>.adoc` (client-internal) or `eidos/design/flows/<name>.adoc` (cross-client omnichannel)
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `steps`
* **Intent**: Pure multi-step user journey or interaction flow definition connecting screens and actions across client surfaces. Referenced from outside by `design/client.flows` and `product/capability.experience.flows`. Features visual Mermaid flowcharts or sequence diagrams.

```yaml
---
type: design/flow
schema: schemas/v1/design/flow.schema.yaml
id: FLOW-PORTAL-EXPLORATION
title: "Specification Discovery & Inspection Flow"
status: active # draft | planned | active | deprecated | retired
flow_type: client_internal # client_internal | cross_client
steps:
  - step: 1
    title: "Access Dashboard"
    screen: "eidos/design/clients/web-portal/screens/portal-home.adoc"
    action: "User accesses root portal URL"
  - step: 2
    title: "Navigate to Master Catalog"
    screen: "eidos/design/clients/web-portal/screens/portal-catalog.adoc"
    action: "User clicks Quick Nav card or Living Specs Catalog in sidebar"
---
```

---

## 4. Evolution & Mutation Layer

### `delta/mutation`
* **Schema**: `schemas/v1/delta/delta.schema.yaml`
* **ID Convention**: `DLT-<YEAR>-<NUMBER>` (e.g., `DLT-2026-005`)
* **Location**: `eidos/deltas/dlt-*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `intent`, `mutations`
* **Intent**: Declarative mutation artifact for Planned Mode. Documents planned architectural changes (`intent.kind`: `feature`, `enabler`, or `bug`), composable `mutations[].operation` (`add | modify | remove | deprecate`) paired with prefix-validated `target`, optional `parent` scope for `add` / `remove` of child entities (omitted on `modify`), and optional `expect:` post-conditions, plus `tombstone` deletion audit exemptions (while `affects` and `blast_radius` are derived automatically from the knowledge graph). Reconciled in-place upon completion (`status: applied`).

```yaml
---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-005
title: "Add Spec-to-Code Drift Detection Command"
status: applied # proposed | in_review | approved | applied | rejected
intent:
  kind: feature # feature | enabler | bug
  motivation: "Enable automated verification of spec-to-code synchronization in local workflows and CI."
mutations:
  - operation: modify
    target: "CTR-EIDOS-CLI"
    details: "Add eidos drift CLI command"
    expect:
      body:
        contains: "eidos drift"
---
```

