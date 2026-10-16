---
name: eidos-spec
description: Author, inspect, and maintain Eidos living specifications across Product (domains DOM-*, capabilities CAP-*, personas PER-*), Arc42/C4 Architecture (containers CNT-*, inline and promoted components CMP-*, contracts CTR-*, embedded decisions DEC-*), Multi-Design Systems (DS-*, W3C tokens, UI components UIC-*, clients CLT-*, screens SCR-*, flows FLOW-*), and Policies/Invariants (POL-*, INV-* with polyglot file::func test bindings). Strictly enforces NO ADRs.
---
# Eidos Specification & Invariant Authoring (`eidos-spec`)

This skill provides the complete authoring reference and schema templates for all **Living Specifications** in `eidos/`.

> **Constitutional Rule (`POL-001`)**: Never create standalone Architecture Decision Records (ADRs). All architectural topology, product requirements, contracts, design systems, policies, and embedded design decisions (`decisions:`) live inside `eidos/` specifications.

---

## 1. CLI Scaffolding & Inspection Commands

Always prefer scaffolding via `eidos spec create` to guarantee valid v1 YAML frontmatter and directory placement:

```bash
# List existing specifications (with optional filtering)
eidos spec list
eidos spec list --type tech/container
eidos spec list --type product/capability --status active

# Scaffold new specifications
eidos spec create domain DOM-BILLING --title "Subscription & Usage Billing"
eidos spec create capability CAP-BILL-001 --title "Metered Usage Aggregation" --domain DOM-BILLING
eidos spec create persona PER-FINANCE-ADMIN --title "Enterprise Finance Administrator"
eidos spec create container CNT-BILLING-WORKER --title "Billing Aggregation Worker"
eidos spec create component CMP-USAGE-LEDGER --title "Double-Entry Usage Ledger"
eidos spec create contract CTR-BILLING-API --title "Billing REST API Contract"
eidos spec create policy POL-005 --title "Idempotent Webhook Processing"
eidos spec create design-system DS-ACME-CORE --title "Acme Core Design System"
eidos spec create ui-component UIC-INVOICE-TABLE --title "Invoice Line-Item Table"
eidos spec create client CLT-CUSTOMER-PORTAL --title "Customer Billing Portal"
eidos spec create screen SCR-BILLING-DASHBOARD --title "Billing Overview Screen"
eidos spec create flow FLOW-UPGRADE-PLAN --title "Subscription Plan Upgrade Flow"

# Inspect, promote, or inline C4 Level 3 components
eidos spec list --type component
eidos spec component promote CMP-USAGE-LEDGER
eidos spec component inline CMP-USAGE-LEDGER

# Validate all specifications, cross-references, and declared filesystem bindings
eidos validate eidos --json
```

---

## 2. Product Layer (`DOM-*`, `CAP-*`, `PER-*`)

### 2.1 Bounded Context Domains (`eidos/domains/<slug>/domain.adoc`)

Defines a DDD Bounded Context (`DOM-[A-Z0-9_-]+`), its strategic classification (`core | supporting | generic`), and ubiquitous language glossary.

```yaml
---
type: product/domain
schema: schemas/v1/product/domain.schema.yaml
id: DOM-BILLING
name: "Subscription & Usage Billing"
classification: core

boundaries:
  responsibilities:
    - "Metered usage event ingestion and deduplication"
    - "Tariff rating and monthly invoice lifecycle"
  out_of_scope:
    - "Payment gateway card tokenization"

ubiquitous_language:
  - term: "Usage Event"
    definition: "An immutable, timestamped record of a billable customer action."
  - term: "Billing Period"
    definition: "A closed calendar interval over which usage events are aggregated into an invoice."

owners:
  - "Revenue Platform Team"
---
= Domain: Subscription & Usage Billing

== Core Responsibilities
Manages metered usage ingestion, tariff rating, and invoice lifecycle.
```

### 2.2 Functional Capabilities (`eidos/domains/<slug>/capabilities/<cap>.adoc`)

Binds user value (`jtbd`, `personas`) to technical containers (`CNT-*`), interface contracts (`CTR-*`), and test-bound invariants (`INV-*`).

```yaml
---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: CAP-BILL-001
title: "Metered Usage Aggregation"
domain: DOM-BILLING
status: active # draft | proposed | planned | active | deprecated | retired
version: "1.0.0"

business:
  personas:
    - eidos/personas/finance-admin.adoc
  jtbd: "When a billing cycle closes, I want customer usage events aggregated accurately without double-counting, so I can issue auditable invoices."
  policies:
    - eidos/policies/POL-001-git-source-of-truth.adoc

technical:
  containers:
    - "CNT-BILLING-WORKER"
  contracts:
    interfaces:
      - "POST /v1/usage/events"
  invariants:
    - id: INV-BILL-IDEMPOTENCY
      rule: "Duplicate usage events sharing the same idempotency_key within a billing period must be deduplicated without incrementing the customer ledger."
      test_binding: "tests/billing/test_ledger.py::test_duplicate_usage_event_idempotency"
---
= Capability: Metered Usage Aggregation

== Functional Overview
Ingests high-throughput usage events and computes deterministic invoice totals.
```

### 2.3 Actor Personas (`eidos/personas/<slug>.adoc`)

```yaml
---
type: product/persona
schema: schemas/v1/product/persona.schema.yaml
id: PER-FINANCE-ADMIN
name: "Enterprise Finance Administrator"
role_type: "admin" # end_user | admin | human_engineer | autonomous_agent
goals:
  - "Reconcile monthly usage invoices against raw telemetry logs"
pain_points:
  - "Opaque line-item adjustments without audit trails"
---
= Persona: Enterprise Finance Administrator
```

---

## 3. Arc42 & C4 Architecture (`SYS-*`, `CNT-*`, `CMP-*`, `CTR-*`)

### 3.1 C4 Level 1 & Arc42 Master Architecture
* **`eidos/system.adoc`** (`type: product/system`, `id: SYS-*`): C4 Level 1 System Context, external actors, and upstream/downstream systems.
* **`eidos/architecture/system-architecture.adoc`**: Master Arc42 Sections 1–12 narrative and C4 Level 2 Container Topology overview.

### 3.2 C4 Level 2 Containers (`eidos/architecture/containers/<name>.adoc`)

Every deployable unit, service, CLI, library, or web app is modeled as a `CNT-*` container.

```yaml
---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-BILLING-WORKER
name: "Billing Aggregation Worker"
container_type: "background_worker" # service | library | cli_app | frontend_spa | background_worker | ml_pipeline | daemon | database | driver
runtime: "Python 3.12 / FastAPI"
package_path: "src/billing"

domain_binding: DOM-BILLING
tier: service # presentation | service | core | persistence | extensibility

components:
  - id: CMP-BILLING-INGESTOR
    name: "UsageEventIngestor"
    responsibility: "Validates incoming usage payloads and enforces idempotency keys."
    source_path: "src/billing/ingestor.py"
  - id: CMP-USAGE-LEDGER
    ref: "eidos/architecture/components/cmp-usage-ledger.adoc"

dependencies:
  internal_containers:
    - "CNT-CORE-DB"
  external_services:
    - "Stripe Billing API"

contracts:
  provides:
    - "CTR-BILLING-API"
  consumes: []

decisions:
  - id: DEC-BILL-001
    title: "Use PostgreSQL Advisory Locks for Invoice Finalization"
    decision: "Acquire transaction-scoped PostgreSQL advisory locks per customer account before finalizing monthly invoices."
    rationale: "Prevents concurrent worker processes from finalizing the same customer invoice without introducing an external Redis lock manager."
    rejected_alternatives:
      - option: "Distributed Redis Redlock"
        reason_rejected: "Adds operational infrastructure complexity for a low-contention periodic batch operation."

quality_attributes:
  stateless: true
  idempotent: true
  concurrency_model: "async_event_loop"
  max_memory_mb: 512
---
= Container: Billing Aggregation Worker (C4 Level 2 Container & Level 3 Components)
```

### 3.3 C4 Level 3 Components: Inline vs. Promoted (`INV-COMPONENT-CENTRALIZATION-MANDATE`)

Eidos supports a two-tier progressive disclosure model for C4 Level 3 components (`CMP-*`):
1. **Inline Components (Default)**: Defined directly in the parent container's `components:` list with `id`, `name`, `responsibility`, and `source_path`. Ideal for straightforward modules.
2. **Promoted Standalone Components (`eidos/architecture/components/<slug>.adoc`)**: When a component has complex internal design, multiple `source_path` entries, component-specific `invariants`, or dedicated `decisions:`, promote it to a standalone file:
   ```bash
   # Promote an inline component to eidos/architecture/components/<id>.adoc
   # and automatically replace the inline entry with '{id: CMP-USAGE-LEDGER, ref: eidos/architecture/components/cmp-usage-ledger.adoc}'
   eidos spec component promote CMP-USAGE-LEDGER

   # Fold a promoted component back inline into its parent container
   eidos spec component inline CMP-USAGE-LEDGER
   ```
   **Single-Definition Invariant (`INV-COMPONENT-CENTRALIZATION-MANDATE`)**: Once promoted, the parent container's `components:` entry MUST use `{id: CMP-*, ref: "eidos/architecture/components/..."}` (with optional cached `name`/`responsibility`)—never duplicate `source_path`, `invariants`, or `decisions` in both places.

### 3.4 Interface Contracts (`eidos/architecture/contracts/<name>.adoc`)

```yaml
---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: CTR-BILLING-API
title: "Billing Usage Ingestion REST Contract"
version: "1.1.0" # Strictly SemVer 2.0.0
owning_container: CNT-BILLING-WORKER
specification_file: "src/billing/openapi.yaml"
consumers:
  - CNT-API-GATEWAY
protocol: openapi # openapi | protobuf | graphql | asyncapi | json_schema | json_rpc_stdio | cli_args | python_protocol
---
= Contract: Billing Usage Ingestion REST Contract
```

### 3.5 Recording Architectural Decisions (`decisions:` Frontmatter Block)

Because standalone ADRs are forbidden (`POL-001`), record non-obvious architectural choices, trade-offs, and rejected alternatives directly in the `decisions:` array of the owning `product/system`, `tech/architecture`, `tech/container` (top-level or inline `components[]`), or `tech/component` specification:

```yaml
decisions:
  - id: DEC-AUTH-001
    title: "Ed25519 Asymmetric Signatures for Session Tokens"
    decision: "Sign all issued session JWTs with Ed25519 private keys and distribute public verification keys via JWKS."
    rationale: "Allows downstream microservices to verify token authenticity locally using only the public key."
    rejected_alternatives:
      - option: "HMAC-SHA256 Shared Secret"
        reason_rejected: "Requires distributing the signing secret to every verifier service, expanding blast radius on compromise."
    graduated_from: EXP-2026-001 # Optional link to EXP-* or DLT-*
```

---

## 4. Multi-Design Systems & Client UX (`DS-*`, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`)

For repositories with web, mobile, desktop, or rich TUI frontends, Eidos models design systems and client state machines under `eidos/design/`:

1. **Design System (`DS-*`, `type: design/system`)**: Lives at `eidos/design/systems/<slug>/system.adoc`. Declares `tokens: { spec_format: "w3c_design_tokens", manifest: "tokens.json" }` referencing a W3C Design Tokens Community Group (DTCG) file using `$value` and `$type` keys (`color`, `dimension`, `fontFamily`, `duration`, etc.), plus optional `inheritance` (`extends`, `override_mode`) and `theme_capabilities` (`color_modes`, `density_modes`).
2. **UI Component Contract (`UIC-*`, `type: design/ui-component`)**: Lives at `eidos/design/systems/<slug>/components/<comp>.adoc`. Defines `design_system` (`DS-*`), `category`, visual/behavioral `variants` (`name`, `description`), interactive `states` (`default`, `hover`, `active`, `focus`, `disabled`, `loading`, `skeleton`, `error`), `accessibility` (`wcag_level`, `aria_role`, `keyboard_nav`), and `test_bindings` (`storybook`, `visual_regression`, `unit_test`).
3. **Client Application (`CLT-*`, `type: design/client`)**: Lives at `eidos/design/clients/<app>/client.adoc`. Binds a frontend `platform` (`web`, `mobile-ios`, `mobile-android`, `desktop`, `tui`, or `multi-platform`) to a `design_system` (`DS-*`), C4 `container` (`CNT-*`), `capabilities` (`CAP-*`), owned `screens` (`SCR-*`), and `flows` (`FLOW-*`).
4. **Screen Specification (`SCR-*`, `type: design/screen`)**: Lives at `eidos/design/clients/<app>/screens/<screen>.adoc` (or `eidos/design/screens/<screen>.adoc`). Defines `route`, owning `client` (`CLT-*`), `capabilities` (`CAP-*`), `supported_design_systems` (`DS-*`), layout `slots`, explicit `states` (`[{name, description}]` covering `loading`, `empty`, `active`, `error`, `partial`), and `user_flows` (`FLOW-*`).
5. **Interaction Flow (`FLOW-*`, `type: design/flow`)**: Lives at `eidos/design/flows/<flow>.adoc` (or `eidos/design/clients/<app>/flows/<flow>.adoc`). Models user journeys with `flow_type` (`client_internal` or `cross_client`), `client` / `clients` (`CLT-*`), `capabilities` (`CAP-*`), and ordered `steps` (`[{step, title, screen, client, action, outcome}]`).

### Frontend Co-Evolution Checklist
Whenever you modify UI components, design tokens, routes, or user flows:
* Added/changed design tokens? Update `tokens.json` in the `DS-*` directory.
* Changed component variants, states, or ARIA behavior? Update `UIC-*`.
* Added a new route or screen state? Create/update `SCR-*` and register it in `CLT-*`.
* Altered a checkout/onboarding/wizard journey? Update `FLOW-*`.

---

## 5. Governance Policies & Polyglot Invariant Traceability (`POL-*`, `INV-*`)

### 5.1 System Policies (`eidos/policies/POL-NNN-<slug>.adoc`)

```yaml
---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: POL-005-WEBHOOK-IDEMPOTENCY
title: "Idempotent External Webhook Processing"
category: architectural # governance | architectural | security | compliance | testing | performance | lifecycle
rule: "All external webhook handlers must deduplicate incoming deliveries by provider event ID before mutating domain state."
enforcement:
  mechanism: automated_test # automated_test | ci_gate | schema_validation | lint | architectural_review
  severity: blocking # blocking | warning | advisory | info
---
= Policy: Idempotent External Webhook Processing
```

### 5.2 Polyglot Invariant Test Bindings (`POL-002`)

Every capability invariant (`INV-[A-Z0-9_-]+`) MUST have:
1. A `test_binding` in the capability YAML frontmatter formatted as `<relative_test_file_path>::<test_function_or_Class::method_name>`.
2. An explicit mention of the `INV-*` ID inside the bound test's docstring, comment, assertion message, or test name so `eidos drift` can verify bidirectional linkage.

| Language | `test_binding` Format | How to Reference `INV-*` in Test Source |
| :--- | :--- | :--- |
| **Python (`pytest`)** | `tests/test_auth.py::test_token_expiry` (or `::TestAuth::test_token_expiry`) | Include `INV-AUTH-EXPIRY` in the function docstring (`"""Verify INV-AUTH-EXPIRY..."""`). |
| **TypeScript / JS (`vitest`, `jest`)** | `tests/auth.test.ts::test_token_expiry` | Include `INV-AUTH-EXPIRY` in `describe`/`it`/`test` title or JSDoc comment above `function test_token_expiry`. |
| **Go (`go test`)** | `pkg/auth/auth_test.go::TestTokenExpiry` | Include `// INV-AUTH-EXPIRY` comment on or inside `func TestTokenExpiry(t *testing.T)`. |
| **Rust (`cargo test`)** | `tests/auth_test.rs::test_token_expiry` | Include `// INV-AUTH-EXPIRY` doc/line comment on `#[test] fn test_token_expiry()`. |
| **Java / Kotlin (`JUnit`)** | `src/test/java/AuthTest.java::testTokenExpiry` | Include `// INV-AUTH-EXPIRY` or `@DisplayName("INV-AUTH-EXPIRY: ...")` on `@Test void testTokenExpiry()`. |

---

## 6. Cross-Reference Integrity Rules

`eidos validate` enforces strict referential integrity across the entire specification graph:
* **File Path References** (`personas`, `policies`, `components[].ref`): Must be valid repository-relative paths (`eidos/personas/...`, `eidos/policies/...`, `eidos/architecture/components/...`) pointing to existing `.adoc` files.
* **Entity ID References** (`domain`, `domain_binding`, `owning_container`, `consumers`, `internal_containers`, `containers`, `provides`, `consumes`): Must match the exact `id:` declared in the target specification's YAML frontmatter.
* **Safe Deletion**: Always use `eidos spec delete <ID_OR_PATH>` to check for incoming references before removing any specification file.
