---
name: eidos-architecture
description: Author, inspect, and maintain Eidos Arc42 architecture, C4 Level 1-3 topology, container manifests, and interface contracts. Strictly enforces NO ADRs.
---

# Eidos Architecture & C4 Topology Authoring (`eidos-architecture`)

This skill equips AI agents to navigate, model, and maintain system architecture following the **Arc42 framework** and **C4 Model** within an Eidos repository.

## Architectural Principles

1. **Absolutely No ADRs (Architecture Decision Records)**:
   - Never create write-only ADR files (`docs/adr/`, `decisions/`).
   - Architectural decisions, rationale, and evolution live directly within the living specifications (`eidos/architecture/`).
2. **C4 Hierarchy Synchronized with Code**:
   - **Level 1 (System Context)**: `eidos/system.adoc` and `eidos/architecture/system-architecture.adoc`.
   - **Level 2 (Containers)**: Deployable units, services, libraries, CLI apps, SPAs (`eidos/architecture/containers/`, `CNT-*`, `tech/container`).
   - **Level 3 (Components & Modules)**: In-process modular building blocks (`CMP-*`) declared within container manifests.
3. **Explicit Interface Contracts**:
   - Every boundary protocol (CLI, OpenAPI, gRPC, JSON-RPC) is defined as a `tech/contract` owned by a container (`owning_container: CNT-*`).

---

## 1. CLI Architecture Commands

Use the Eidos CLI to inspect and scaffold architecture:

```bash
# 1. Scan monorepo packages, C4 Level 2 containers, runtimes, and dependencies:
eidos containers

# 2. Inspect C4 Level 3 in-process components (optionally filtered by container):
eidos components
eidos components CNT-EIDOS-CORE

# 3. Search architectural topology and compute dependency blast radius:
eidos index search "authentication"
eidos index blast-radius CNT-AUTH-SERVICE --depth 2

# 4. Scaffold a new technical container manifest:
eidos spec create container CNT-AUTH-SERVICE --title "Authentication & Token Service" \
  --container-type service --runtime "Rust / Axum" --package-path "packages/auth"

# 5. Scaffold an interface contract:
eidos spec create contract CTR-AUTH-API --title "Auth REST API" \
  --protocol openapi --owning-container CNT-AUTH-SERVICE --specification-file "packages/auth/openapi.yaml"

# 6. Promote an inline C4 Level 3 component to a co-located specification:
eidos component promote CMP-CORE-VALIDATOR

# 7. Demote / inline a co-located component back into its container:
eidos component inline CMP-CORE-VALIDATOR

# 8. List all C4 Level 3 components and their co-location status:
eidos component list
```

---

## 2. Container Manifest Anatomy (`container.schema.yaml`)

Container manifests live in `eidos/architecture/containers/<container-name>.adoc`:

```yaml
---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-EIDOS-CORE
name: "Eidos Core Engine"
container_type: library  # service | library | cli_app | frontend_spa | background_worker | daemon
runtime: "Python 3.12 / Typer / Hatchling"
package_path: "src/eidos"
domain_binding: DOM-SPEC-ENGINE

components:
  - id: CMP-CORE-PARSER
    name: "SpecParser"
    responsibility: "Parses AsciiDoc frontmatter and structured sections."
    source_path: "src/eidos/core/parser.py"
  - id: CMP-CORE-VALIDATOR
    name: "SpecValidator"
    responsibility: "Validates living specs against Draft 2020-12 schemas and checks cross-references."
    source_path: "src/eidos/core/validator.py"
  - id: CMP-CORE-SCAFFOLDER
    name: "LifecycleScaffolder"
    responsibility: "Manages document lifecycle, template generation, and safety checks."
    source_path: "src/eidos/core/scaffolder.py"

dependencies:
  internal_containers: []
  external_services:
    - "pyyaml"
    - "jsonschema"

contracts:
  provides:
    - "CTR-EIDOS-VALIDATOR"
---
= Container: Eidos Core Engine
:toc: left

== Architectural Role
Core engine providing parsing, validation, and lifecycle orchestration.

== Building Block View (C4 Level 3 Components)
Internal modules and modular components contained within this deployable.
```

---

## 3. Centralized Hybrid Component Manifests (`tech/component`)

C4 Level 3 components (`CMP-*`) follow a centralized hybrid model:
1. **Inline by Default (80%)**: Simple internal modules remain inline within `CNT-*.adoc` under `components:` (`id`, `name`, `responsibility`, `source_path`).
2. **Promoted Dedicated Specs (20%)**: Architecturally complex modules receive dedicated `type: tech/component` specifications residing centrally in `eidos/architecture/components/<component>.adoc` (linking to implementation code via `package_path`) and referenced in the container manifest via `ref: architecture/components/<component>.adoc`.
3. **Mandatory Invariants**:
   - `INV-COMPONENT-CENTRALIZATION-MANDATE`: Component specifications **MUST** reside centrally within `eidos/architecture/components/`. Specifications must never touch or reside within source code directories (`src/`, `packages/`).
   - `INV-COMPONENT-CONTAINER-AFFINITY`: Every dedicated `CMP-*` manifest must declare `container: CNT-*` pointing to an existing container.
   - `INV-INTERFACE-BOUNDARY-GOVERNANCE`: Boundary Protocol Contracts (`CTR-*`) must be owned by Containers (`CNT-*`). In-process components declare local language-level interfaces via `interfaces.provides`.

```yaml
---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: CMP-CORE-VALIDATOR
name: "Specification Validator"
container: CNT-EIDOS-CORE
status: active
package_path: "src/eidos/core/validator.py"
responsibility: "Validates specifications against canonical Draft 2020-12 schemas."
interfaces:
  provides:
    - signature: "validate_document(doc: SpecDocument) -> list[str]"
      description: "Validates a single parsed document."
  consumes:
    - "CMP-CORE-PARSER"
quality_attributes:
  stateless: true
  reentrant: true
invariants:
  - id: INV-SPEC-VALIDATION
    rule: "All specifications must validate against schema."
    test_binding: "tests/test_validator.py::test_validate_eidos_specifications"
---
= Component: Specification Validator
```

---

## 4. Interface Contract Anatomy (`contract.schema.yaml`)

Interface contracts live in `eidos/architecture/contracts/<contract-name>.adoc`:

```yaml
---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: CTR-EIDOS-CLI
title: "Eidos Command-Line Interface Contract"
protocol: "cli_args"  # cli_args | openapi | protobuf | graphql | asyncapi | json_rpc_stdio
owning_container: "CNT-EIDOS-CLI"
specification_file: "src/eidos/cli/main.py"
---
= Contract: Eidos CLI Interface
:toc: left

== Command Specifications
Document CLI flags, arguments, exit codes, and JSON-RPC messages here.
```

---

## 5. Entity ID Graph Cross-Reference Checking

Eidos enforces strict relational graph integrity across all architectural entities (`INV-SPEC-GRAPH-INTEGRITY`). When authoring or mutating container, component, contract, and domain specifications, the validator strictly verifies that referenced IDs resolve to real entities registered in the living catalog:

| Spec Relationship Field | Target Entity Type | Validation Rule |
| :--- | :--- | :--- |
| `dependencies.internal_containers` | `tech/container` (`CNT-*`) | Every listed internal dependency must exist as a registered container in the catalog. |
| `contracts.provides` | `tech/contract` (`CTR-*`) | Every provided interface contract must exist in the catalog or point to a valid specification file. |
| `contracts.requires` & `contracts.consumes` | `tech/contract` (`CTR-*`) | Every consumed interface contract must resolve to a valid contract registered in the catalog. |
| `owning_container` | `tech/container` (`CNT-*`) | Contract specifications must reference a valid, existing owning container ID. |
| `domain_binding` | `product/domain` (`DOM-*`) | Container manifests must bind to a registered domain in the catalog. |
| `technical_mapping.dependencies` | `product/domain` (`DOM-*`) | Domain cross-domain dependencies must resolve to existing domains in the catalog. |
| `technical_mapping.containers` | `tech/container` (`CNT-*`) | Domain-to-container mappings must resolve to existing containers in the catalog. |

### Diagnostic Reporting
If any reference fails to resolve, `eidos validate` halts with an explicit error:
```text
Broken entity reference in eidos/architecture/containers/cli.adoc:
container 'CNT-NONEXISTENT' in dependencies.internal_containers does not exist in catalog
```
This guarantees that architectural diagrams, C4 topologies, and blast-radius slices never suffer from phantom dependencies or orphaned contracts.

---

## 6. Verification

Always verify architecture specifications with:
```bash
eidos validate eidos
```
Ensure zero broken container cross-references (`CNT-*`), contracts (`CTR-*`), or dependencies.
