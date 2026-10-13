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
eidos spec containers
# Or using shortcut:
eidos containers

# 2. Inspect C4 Level 3 in-process components (optionally filtered by container):
eidos spec components
# Or using shortcut:
eidos components
eidos components CNT-AUTH-SERVICE

# 3. Search architectural topology and compute dependency blast radius:
eidos index search "authentication"
eidos index blast-radius CNT-AUTH-SERVICE --depth 2

# 4. Scaffold a new technical container manifest:
eidos spec create container CNT-AUTH-SERVICE --title "Authentication & Token Service" \
  --container-type service --runtime "Rust / Axum" --package-path "packages/auth"

# 5. Scaffold an interface contract:
eidos spec create contract CTR-AUTH-API --title "Auth REST API" \
  --protocol openapi --owning-container CNT-AUTH-SERVICE --specification-file "packages/auth/openapi.yaml"

# 6. Promote an inline C4 Level 3 component to a dedicated specification:
eidos component promote CMP-AUTH-VALIDATOR

# 7. Demote / inline a dedicated component back into its container:
eidos component inline CMP-AUTH-VALIDATOR

# 8. List all C4 Level 3 components and their specification status:
eidos component list
```

---

## 2. Container Manifest Anatomy (`container.schema.yaml`)

Container manifests live in `eidos/architecture/containers/<container-name>.adoc`:

```yaml
---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-AUTH-SERVICE
name: "Authentication & Token Service"
container_type: service  # service | library | cli_app | frontend_spa | background_worker | daemon
runtime: "Python 3.12 / FastAPI"
package_path: "src/auth"
domain_binding: DOM-IDENTITY

components:
  - id: CMP-AUTH-PARSER
    name: "TokenParser"
    responsibility: "Parses and decodes incoming bearer tokens and claims."
    source_path: "src/auth/parser.py"
  - id: CMP-AUTH-VALIDATOR
    name: "TokenValidator"
    responsibility: "Validates token signatures, expiration windows, and audience claims."
    source_path: "src/auth/validator.py"
  - id: CMP-AUTH-ISSUER
    name: "SessionIssuer"
    responsibility: "Mints short-lived access tokens and rotates refresh tokens."
    source_path: "src/auth/issuer.py"

dependencies:
  internal_containers: []
  external_services:
    - "PostgreSQL"
    - "Redis"

contracts:
  provides:
    - "CTR-AUTH-API"
---
= Container: Authentication & Token Service
:toc: left

== Architectural Role
Service providing token issuance, verification, and session lifecycle management.

== Building Block View (C4 Level 3 Components)
Internal modules and modular components contained within this deployable.
```

---

## 3. Centralized Hybrid Component Manifests (`tech/component`)

C4 Level 3 components (`CMP-*`) follow a centralized hybrid model:
1. **Inline by Default (80%)**: Simple internal modules remain inline within `CNT-*.adoc` under `components:` (`id`, `name`, `responsibility`, `source_path`).
2. **Promoted Dedicated Specs (20%)**: Architecturally complex modules receive dedicated `type: tech/component` specifications residing centrally in `eidos/architecture/components/<component>.adoc` (linking to implementation code via `source_path`) and referenced in the container manifest via `ref: architecture/components/<component>.adoc`.
3. **Mandatory Invariants**:
   - `INV-COMPONENT-CENTRALIZATION-MANDATE`: Component specifications **MUST** reside centrally within `eidos/architecture/components/`. Specifications must never touch or reside within source code directories (`src/`, `packages/`).
   - `INV-COMPONENT-CONTAINER-AFFINITY`: Every dedicated `CMP-*` manifest must declare `container: CNT-*` pointing to an existing container.
   - `INV-INTERFACE-BOUNDARY-GOVERNANCE`: Boundary Protocol Contracts (`CTR-*`) must be owned by Containers (`CNT-*`). In-process components declare local language-level interfaces via `interfaces.provides`.

```yaml
---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: CMP-AUTH-VALIDATOR
name: "Token Validator"
container: CNT-AUTH-SERVICE
status: active
source_path: "src/auth/validator.py"
responsibility: "Validates JWT signatures and claim invariants."
interfaces:
  provides:
    - signature: "validate_token(token: str) -> Claims"
      description: "Validates a signed bearer token."
  consumes:
    - "CMP-AUTH-PARSER"
quality_attributes:
  stateless: true
  reentrant: true
invariants:
  - id: INV-AUTH-TOKEN-EXPIRY
    rule: "Expired tokens must always be rejected prior to claim evaluation."
    test_binding: "tests/test_auth.py::test_expired_token_rejected"
---
= Component: Token Validator
```

---

## 4. Interface Contract Anatomy (`contract.schema.yaml`)

Interface contracts live in `eidos/architecture/contracts/<contract-name>.adoc`:

```yaml
---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: CTR-AUTH-API
title: "Authentication Service Interface Contract"
protocol: "openapi"  # cli_args | openapi | protobuf | graphql | asyncapi | json_rpc_stdio
owning_container: "CNT-AUTH-SERVICE"
specification_file: "src/auth/openapi.yaml"
---
= Contract: Authentication Service Interface
:toc: left

== Endpoint Specifications
Document API endpoints, CLI flags, arguments, exit codes, and payload schemas here.
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
