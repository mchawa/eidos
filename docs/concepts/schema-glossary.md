# Living Specification Schema Glossary

Every specification document in an Eidos repository is an **AsciiDoc (`.adoc`)** file containing structured **YAML frontmatter**. Frontmatter syntax and cross-references are strictly validated against canonical schemas bundled with the Eidos engine (`assets/schemas/v1/`).

This glossary details all **13 document schemas**, their ID conventions, directory locations, required fields, and architectural intent.

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
| `tech/component` | `CMP-*` | `architecture/components/*.adoc` | `tech/component.schema.yaml` | C4 Level 3 component manifests, runtimes, packages, and contracts. |
| `tech/contract` | `CTR-*` | `architecture/contracts/*.adoc` | `tech/contract.schema.yaml` | Interface protocol contracts (OpenAPI, gRPC, CLI, AsyncAPI). |
| `design/system` | `DS-*` | `design/systems/<system>/system.adoc` | `design/system.schema.yaml` | Multi-design system catalog, brand themes, and W3C DTCG tokens. |
| `design/ui-component` | `UIC-*` | `design/systems/<system>/components/*.adoc` | `design/ui-component.schema.yaml` | UI component contract: variants, states, accessibility, and tests. |
| `design/screen` | `SCR-*` | `design/screens/*.adoc` | `design/screen.schema.yaml` | Screen views, routes, layout slots, and interaction state machines. |
| `delta/mutation` | `DLT-*` | `deltas/dlt-*.adoc` | `delta/delta.schema.yaml` | Planned Mode semantic mutations, blast radius, and migration steps. |

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
archetypes:
  - framework
  - cli_tool
business_identity:
  vision: "Provide an open, GitOps-driven AI development ecosystem."
  target_users:
    - "Software Engineers & Architects"
    - "Autonomous AI Coding Agents"
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
* **ID Convention**: `DOM-<NAME>` (e.g., `DOM-PRODUCT-CORE`, `DOM-AUTH`)
* **Location**: `eidos/domains/<domain-name>/domain.adoc`
* **Required Properties**: `type`, `schema`, `id`, `name`, `classification`, `boundaries`
* **Intent**: Implements Domain-Driven Design (DDD) bounded contexts. Classifies domains as `core`, `supporting`, or `generic`, and sets architectural boundaries.

```yaml
---
type: product/domain
schema: schemas/v1/product/domain.schema.yaml
id: DOM-PRODUCT-CORE
name: "Product Intelligence Domain"
classification: core # core | supporting | generic
boundaries:
  responsibilities:
    - "Living specification parsing, AST generation, and schema validation."
  in_scope:
    - "Markdown/AsciiDoc frontmatter parsing."
  out_of_scope:
    - "External VCS webhook listening (handled by DOM-VCS-SYNC)."
---
```

---

### `product/capability`
* **Schema**: `schemas/v1/product/capability.schema.yaml`
* **ID Convention**: `CAP-<DOMAIN>-<NUMBER>` (e.g., `CAP-PROD-001`, `CAP-AUTH-002`)
* **Location**: `eidos/domains/<domain-name>/capabilities/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `domain`, `status`, `business`, `technical`
* **Intent**: The core building block of functionality. Bridges human Jobs-to-be-Done (JTBD) with technical components and test-bound invariants (`INV-*`).

```yaml
---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: CAP-PROD-001
title: "Living Baseline Specification Engine"
domain: DOM-PRODUCT-CORE
status: active # draft | proposed | active | deprecated
version: "1.0.0"

business:
  personas:
    - eidos/personas/product-lead.adoc
  jtbd: "When defining system rules, I want living specs in Git, so docs never rot."
  policies:
    - eidos/policies/POL-001-git-as-single-source-of-truth.adoc

technical:
  components:
    - "CMP-EIDOS-CORE"
  invariants:
    - id: INV-SPEC-VALIDATION
      rule: "Every .adoc file with YAML frontmatter must validate against schema."
      test_binding: "tests/test_validator.py::test_all_specs_conform_to_schema"
---
```

---

### `product/policy`
* **Schema**: `schemas/v1/product/policy.schema.yaml`
* **ID Convention**: `POL-<NUMBER>-<NAME>` (e.g., `POL-001-GIT-SOURCE-OF-TRUTH`)
* **Location**: `eidos/policies/POL-*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `category`, `rule`, `enforcement`
* **Intent**: Declarative system-wide governance laws (governance, architectural, security, compliance, testing) enforced via programmatic mechanisms.

```yaml
---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: POL-002-INVARIANT-TEST-ENFORCEMENT
title: "Mandatory Invariant Test Binding"
category: testing # governance | architectural | security | compliance | testing
rule: "Every technical invariant declared in a Capability must specify a test_binding."
enforcement:
  mechanism: "Eidos drift detection and CI test suite"
  severity: blocking # blocking | critical | warning | advisory
---
```

---

### `product/persona`
* **Schema**: `schemas/v1/product/persona.schema.yaml`
* **ID Convention**: `PER-<NAME>` (e.g., `PER-PRODUCT-LEAD`, `PER-AUTONOMOUS-AGENT`)
* **Location**: `eidos/personas/*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `name`, `role_type`, `goals`, `pain_points`
* **Intent**: Captures the needs, workflows, goals, and frustrations of human users and autonomous AI coding agents.

```yaml
---
type: product/persona
schema: schemas/v1/product/persona.schema.yaml
id: PER-AUTONOMOUS-AGENT
name: "Autonomous AI Coding Agent"
role_type: autonomous_agent # human | autonomous_agent | system_service
goals:
  - "Safely mutate code without violating implicit domain rules."
  - "Retrieve deterministic schema definitions via Model Context Protocol (MCP)."
pain_points:
  - "Stale wikis and missing context leading to hallucinated APIs."
---
```

---

### `product/exploration`
* **Schema**: `schemas/v1/product/exploration.schema.yaml`
* **ID Convention**: `EXP-<NUMBER>-<NAME>` (e.g., `EXP-001-VECTOR-INDEXING`)
* **Location**: `eidos/explorations/*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `hypothesis`, `exit_criteria`
* **Intent**: Formalizes technical spikes and feasibility studies. Prevents "spike code" from rotting by demanding clear exit criteria and graduation to SpecDeltas or archival.

```yaml
---
type: product/exploration
schema: schemas/v1/product/exploration.schema.yaml
id: EXP-001-LOCAL-VECTOR-EMBEDDINGS
title: "Feasibility of Local Spec Embeddings via ONNX"
status: active # proposed | active | graduated | archived
owner: "Team AI"
hypothesis: "Running an in-process ONNX model can perform semantic spec search in <50ms."
exit_criteria:
  - "Memory footprint under 150MB."
  - "Benchmark latency under 50ms on commodity laptop hardware."
---
```

---

## 2. Technical & Architecture Layer

### `tech/architecture`
* **Schema**: `schemas/v1/tech/architecture.schema.yaml`
* **ID Convention**: `ARCH-<NAME>-<NUMBER>` (e.g., `ARCH-EIDOS-001`)
* **Location**: `eidos/architecture/system-architecture.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `topology_style`, `c4_context`, `c4_containers`, `solution_strategy`, `cross_cutting_concepts`, `deployment_view`
* **Intent**: Global architectural baseline combining Arc42 (Sections 1–12) with C4 Model Levels 1 (System Context) and 2 (Container Topology). **Strictly replaces ADRs**.

```yaml
---
type: tech/architecture
schema: schemas/v1/tech/architecture.schema.yaml
id: ARCH-EIDOS-001
title: "Eidos Global System Architecture"
topology_style: modular_monolith # microservices | modular_monolith | serverless | event_driven
c4_context:
  system_name: "Eidos Engine"
  external_systems:
    - "GitHub / GitLab API"
    - "Model Context Protocol Clients"
c4_containers:
  - id: "CONTAINER-CLI"
    name: "CLI & Scaffolding Engine"
    technology: "Python 3.12 / Typer"
---
```

---

### `tech/component`
* **Schema**: `schemas/v1/tech/component.schema.yaml`
* **ID Convention**: `CMP-<NAME>` (e.g., `CMP-EIDOS-CORE`, `CMP-EIDOS-CLI`)
* **Location**: `eidos/architecture/components/<name>.adoc` (or co-located in `packages/<name>/component.adoc`)
* **Required Properties**: `type`, `schema`, `id`, `name`, `container_type`, `runtime`, `package_path`
* **Intent**: C4 Level 3 Whitebox component manifest. Enumerates internal modules, package dependencies, and provided/consumed interface contracts.

```yaml
---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: CMP-EIDOS-CORE
name: "Eidos Core Engine"
container_type: library # service | library | cli_app | web_app | worker
runtime: "Python 3.12+"
package_path: "src/eidos/core"
contracts:
  provides:
    - "eidos/architecture/contracts/validator.adoc"
dependencies:
  internal_components: []
  external_services:
    - "pyyaml"
    - "jsonschema"
---
```

---

### `tech/contract`
* **Schema**: `schemas/v1/tech/contract.schema.yaml`
* **ID Convention**: `CTR-<NAME>` (e.g., `CTR-EIDOS-CLI`, `CTR-EIDOS-MCP`)
* **Location**: `eidos/architecture/contracts/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `protocol`, `owning_component`, `specification_file`
* **Intent**: Formal interface protocol boundary contract. Binds APIs, CLI signatures, or RPC services to concrete schemas or specs.

```yaml
---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: CTR-EIDOS-CLI
title: "Eidos CLI Interface Contract"
protocol: cli # openapi | grpc | graphql | asyncapi | cli | sdk
owning_component: "CMP-EIDOS-CLI"
specification_file: "eidos/architecture/contracts/cli.adoc"
---
```

---

## 3. Design System & UI/UX Layer

### `design/system`
* **Schema**: `schemas/v1/design/system.schema.yaml`
* **ID Convention**: `DS-<NAME>` (e.g., `DS-CORE`, `DS-ACME`)
* **Location**: `eidos/design/systems/<system>/system.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `tokens`
* **Intent**: Defines brand identity, token manifest path, and multi-brand inheritance (`extends_system`, `client_id`).

```yaml
---
type: design/system
schema: schemas/v1/design/system.schema.yaml
id: DS-ACME
title: "Acme Enterprise Theme"
status: active
client_id: "acme"
extends_system: "DS-CORE"
tokens: "eidos/design/systems/acme/tokens.json"
---
```

---

### `design/ui-component`
* **Schema**: `schemas/v1/design/ui-component.schema.yaml`
* **ID Convention**: `UIC-<SYSTEM>-<NAME>` (e.g., `UIC-CORE-BUTTON`)
* **Location**: `eidos/design/systems/<system>/components/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `design_system`, `status`
* **Intent**: Visual UI component specification. Defines visual variants, interactive states, accessibility criteria (WCAG, ARIA), and test bindings (Storybook, unit tests).

```yaml
---
type: design/ui-component
schema: schemas/v1/design/ui-component.schema.yaml
id: UIC-CORE-BUTTON
title: "Action Button"
design_system: "DS-CORE"
status: active
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
* **ID Convention**: `SCR-<NAME>` (e.g., `SCR-CHECKOUT`, `SCR-DASHBOARD`)
* **Location**: `eidos/design/screens/<name>.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `route`, `status`
* **Intent**: User-facing view/screen manifest declaring URL routes, component layout slots, and state machines (`loading`, `empty`, `active`, `error`).

```yaml
---
type: design/screen
schema: schemas/v1/design/screen.schema.yaml
id: SCR-CHECKOUT
title: "Order Checkout Screen"
route: "/checkout"
status: active
capabilities:
  - "eidos/domains/checkout/capabilities/checkout.adoc"
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

## 4. Evolution & Mutation Layer

### `delta/mutation`
* **Schema**: `schemas/v1/delta/delta.schema.yaml`
* **ID Convention**: `DLT-<YEAR>-<NUMBER>` (e.g., `DLT-2026-005`)
* **Location**: `eidos/deltas/dlt-*.adoc`
* **Required Properties**: `type`, `schema`, `id`, `title`, `status`, `intent`, `mutations`
* **Intent**: Declarative mutation artifact for Planned Mode. Documents planned architectural changes, blast radius, invariant additions, and migration steps. Reconciled in-place upon completion (`status: applied`).

```yaml
---
type: delta/mutation
schema: schemas/v1/delta/delta.schema.yaml
id: DLT-2026-005
title: "Add Spec-to-Code Drift Detection Command"
status: applied # proposed | drafting | active | applied | rejected
intent: feature # feature | bugfix | refactor | security | deprecation
affects:
  components:
    - CMP-EIDOS-CLI
    - CMP-EIDOS-MCP
mutations:
  - action: add_interface
    target: "CTR-EIDOS-CLI"
    details: "Add eidos drift CLI command"
---
```
