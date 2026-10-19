---
name: eidos-spec
description: Author, inspect, and maintain Eidos living specifications across Product (domains DOM-*, capabilities CAP-*, personas PER-*), Arc42/C4 Architecture (containers CNT-*, inline and promoted components CMP-*, contracts CTR-*, embedded decisions DEC-*), Multi-Design Systems (DS-*, W3C tokens, UI components UIC-*, clients CLT-*, screens SCR-*, flows FLOW-*), and Policies/Invariants (POL-*, INV-* with polyglot file::func test bindings). Strictly enforces NO ADRs.
---
# Eidos Specification & Invariant Authoring (`eidos-spec`)

This skill provides the authoring rules, CLI scaffolding commands, and v1 YAML frontmatter schemas for all **Living Specifications** in `eidos/`.

> **Constitutional Rule (`POL-001`)**: Never create standalone Architecture Decision Records (ADRs). All architectural state, product requirements, contracts, design systems, policies, and embedded design decisions (`decisions:`) live inside `eidos/` specifications.

---

## 1. CLI Scaffolding & Inspection (`eidos spec`)

Always scaffold new specifications with `eidos spec create` to guarantee valid v1 YAML frontmatter and canonical paths:

```bash
# List and inspect living specifications (includes inline CMP-* components; excludes SpecDeltas)
eidos spec list [--type <type>] [--status <status>] [--json]
eidos spec show <ID_OR_PATH>

# Scaffold minimal draft blueprints across the 15 living specification types:
#   system | architecture | domain | capability | persona | policy | exploration |
#   container | component | contract | design-system | ui-component | client | screen | flow
eidos spec create domain DOM-BILLING --title "Subscription & Usage Billing"
eidos spec create capability CAP-BILL-001 --title "Metered Usage Aggregation"
eidos spec create container CNT-BILLING-WORKER --title "Billing Aggregation Worker"
eidos spec create component CMP-USAGE-LEDGER --title "Double-Entry Usage Ledger"
eidos spec create contract CTR-BILLING-API --title "Billing REST API"
eidos spec create policy POL-005 --title "Idempotent Webhook Processing"

# Promote an inline C4 Level 3 component to eidos/architecture/components/<slug>.adoc, or inline it back
eidos spec component promote CMP-USAGE-LEDGER
eidos spec component inline CMP-USAGE-LEDGER

# Safely delete a specification after checking incoming cross-references
eidos spec delete <ID_OR_PATH>

# Validate all specifications, cross-references, disk bindings, and bidirectional contract parity
eidos validate eidos --json
```

---

## 2. Product Layer (`SYS-*`, `DOM-*`, `CAP-*`, `PER-*`)

### 2.1 System Context (`eidos/system.adoc`)
* **`type: product/system`** (`schema: schemas/v1/product/system.schema.yaml`, `id: SYS-*`).
* **Required keys**: `type`, `schema`, `id`, `title`, `archetypes` (list), `business_identity: { vision, target_users, value_proposition }`, `technical_profile: { primary_languages }` (optional `runtimes_and_frameworks`, `deployment_targets`, `vcs_platforms`, `agent_support`), optional `status`, `decisions`.

### 2.2 Bounded Context Domains (`eidos/domains/<slug>/domain.adoc`)

```yaml
---
type: product/domain
schema: schemas/v1/product/domain.schema.yaml
id: DOM-BILLING
title: "Subscription & Usage Billing"
status: active
classification: core # core | supporting | generic

boundaries:
  responsibilities:
    - "Metered usage event ingestion and deduplication"
    - "Tariff rating and monthly invoice lifecycle"
  out_of_scope:
    - "Payment gateway card tokenization"

ubiquitous_language:
  - term: "Usage Event"
    definition: "An immutable, timestamped record of a billable customer action."

owners:
  - "Revenue Platform Team"
---
= Domain: Subscription & Usage Billing
```

### 2.3 Functional Capabilities (`eidos/domains/<slug>/capabilities/<cap>.adoc`)

Binds user value (`jtbd`, `personas`) to technical containers (`CNT-*`), components (`CMP-*`), contracts (`CTR-*`), optional UX (`experience: { clients, design_systems, screens, flows, ui_components }`), and test-bound invariants (`INV-*`).

```yaml
---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: CAP-BILL-001
title: "Metered Usage Aggregation"
domain: DOM-BILLING
status: active # draft | planned | active | deprecated | retired
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
  components:
    - "CMP-BILLING-INGESTOR"
  contracts:
    - "CTR-BILLING-API"
  invariants:
    - id: INV-BILL-IDEMPOTENCY
      rule: "Duplicate usage events sharing the same idempotency_key within a billing period must be deduplicated without incrementing the customer ledger."
      test_binding: "tests/billing/test_ledger.py::test_duplicate_usage_event_idempotency"
---
= Capability: Metered Usage Aggregation
```

### 2.4 Actor Personas (`eidos/personas/<slug>.adoc`)

```yaml
---
type: product/persona
schema: schemas/v1/product/persona.schema.yaml
id: PER-FINANCE-ADMIN
title: "Enterprise Finance Administrator"
status: active
role_type: "admin" # end_user | admin | human_engineer | autonomous_agent
goals:
  - "Reconcile monthly usage invoices against raw telemetry logs"
pain_points:
  - "Opaque line-item adjustments without audit trails"
---
= Persona: Enterprise Finance Administrator
```

---

## 3. Arc42 & C4 Architecture (`ARCH-*`, `CNT-*`, `CMP-*`, `CTR-*`, `DEC-*`)

### 3.1 Arc42 Master Architecture (`eidos/architecture/system-architecture.adoc`)
* **`type: tech/architecture`** (`schema: schemas/v1/tech/architecture.schema.yaml`, `id: ARCH-*`).
* **Required keys**: `type`, `schema`, `id`, `title`, `topology_style`, `c4_context: { actors, external_systems }`, `c4_containers` (list of `{ container: CNT-*, title, container_type, runtime, source_path }`), `solution_strategy` (list), `cross_cutting_concepts` (object), `deployment_view: { hosting_model, ci_cd_engine }`. Whenever you add or remove a `CNT-*` container, update `c4_containers` in `system-architecture.adoc`.

### 3.2 C4 Level 2 Containers (`eidos/architecture/containers/<name>.adoc`)

```yaml
---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-BILLING-WORKER
title: "Billing Aggregation Worker"
status: active
container_type: "background_worker" # service | library | cli_app | web_app | background_worker | ml_pipeline | daemon | database | driver
runtime: "Python 3.12 / FastAPI"
source_path: "src/billing" # String or list of repo-relative directory/file paths

domain: DOM-BILLING
tier: service # presentation | service | core | persistence | extensibility

components:
  - id: CMP-BILLING-INGESTOR
    title: "UsageEventIngestor"
    responsibility: "Validates incoming usage payloads and enforces idempotency keys."
    source_path: "src/billing/ingestor.py"

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
= Container: Billing Aggregation Worker
```

### 3.3 C4 Level 3 Components: Inline vs. Standalone (`INV-COMPONENT-CENTRALIZATION-MANDATE`)

1. **Inline Components (Default)**: Defined inside the parent container's `components:` list (`#/$defs/inline_component`) with `id`, `title`, `responsibility`, and `source_path`.
2. **Promoted Standalone Components (`eidos/architecture/components/<slug>.adoc`)**: Promote complex subsystems via `eidos spec component promote <CMP-ID>`.
   * **Single-Definition Invariant (`INV-COMPONENT-CENTRALIZATION-MANDATE`)**: Standalone components reside in `eidos/architecture/components/` and link bottom-up via `container: CNT-*`. They are NOT duplicated in the parent container's `components:` array.

```yaml
---
type: tech/component
schema: schemas/v1/tech/component.schema.yaml
id: CMP-USAGE-LEDGER
title: "Double-Entry Usage Ledger"
container: CNT-BILLING-WORKER
status: active
source_path: "src/billing/ledger.py"
domain: DOM-BILLING
responsibility: "Appends immutable credit/debit usage entries and computes point-in-time account balances."
---
= Component: Double-Entry Usage Ledger
```

### 3.4 Interface Contracts (`eidos/architecture/contracts/<name>.adoc`)

`eidos validate` enforces **bidirectional contract parity** against `specification_file` (`cli_args` across Typer/Click/argparse, `openapi`/`rest_openapi`, `protobuf`/`grpc_proto`, `graphql`, `python_protocol`): operations added in `specification_file` must be documented in the `CTR-*` body, and operations removed from code must be removed from the `CTR-*` body so phantom entries never linger.

```yaml
---
type: tech/contract
schema: schemas/v1/tech/contract.schema.yaml
id: CTR-BILLING-API
title: "Billing Usage Ingestion REST Contract"
status: active
version: "1.1.0" # Strictly SemVer 2.0.0
container: CNT-BILLING-WORKER
specification_file: "src/billing/openapi.yaml"
protocol: openapi # openapi | protobuf | graphql | asyncapi | json_schema | json_rpc_stdio | cli_args | python_protocol
---
= Contract: Billing Usage Ingestion REST Contract
```

### 3.5 Embedded Architectural Decisions (`decisions:` Frontmatter Block)

Record non-obvious trade-offs and rejected alternatives in the `decisions:` array on any living specification (`product/*`, `tech/*`, `design/*`):
* **Required fields per entry**: `id` (`DEC-*`), `title`, `decision`, `rationale`, and `rejected_alternatives` (`[{ option, reason_rejected }]`), plus optional `graduated_from` (`EXP-*` or `DLT-*`).

---

## 4. Multi-Design Systems & Client UX (`DS-*`, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`)

For repositories with web, mobile, desktop, or TUI frontends, model UX specifications under `eidos/design/` as pure reusable definitions referenced from `product/capability.experience` and `design/client`:

| Type & ID Prefix | Canonical Path | Required Keys | Key Optional Properties |
| :--- | :--- | :--- | :--- |
| **`design/system` (`DS-*`)** | `eidos/design/systems/<slug>/system.adoc` | `type`, `schema`, `id`, `title`, `status`, `tokens: { spec_format: "w3c_design_tokens", manifest: "tokens.json" }` | `brand_name`, `extends` (`DS-*`), `override_mode`, `theme_capabilities: { color_modes, density_modes }`, `external_references`, `decisions` |
| **`design/ui-component` (`UIC-*`)** | `eidos/design/systems/<slug>/components/<comp>.adoc` | `type`, `schema`, `id`, `title`, `design_system` (`DS-*`), `status` | `category`, `implements` (`UIC-*`), `variants: [{name, description}]`, `states`, `accessibility: {wcag_level, aria_role, keyboard_nav}`, `test_bindings`, `external_references`, `decisions` |
| **`design/client` (`CLT-*`)** | `eidos/design/clients/<app>/client.adoc` | `type`, `schema`, `id`, `title`, `status`, `platform` (`web`, `mobile-ios`, `mobile-android`, `desktop`, `tui`, `multi-platform`), `design_system` | `container` (`CNT-*`), `screens` (`SCR-*`), `flows` (`FLOW-*`), `decisions` |
| **`design/screen` (`SCR-*`)** | `eidos/design/clients/<app>/screens/<screen>.adoc` | `type`, `schema`, `id`, `title`, `route`, `status` | `slots`, `states: [{name, description}]`, `external_references`, `decisions` |
| **`design/flow` (`FLOW-*`)** | `eidos/design/flows/<flow>.adoc` | `type`, `schema`, `id`, `title`, `status`, `steps: [{step, title, screen, client, action, outcome}]` | `flow_type` (`client_internal`, `cross_client`), `decisions` |

---

## 5. Governance Policies & Polyglot Invariant Traceability (`POL-*`, `INV-*`)

### 5.1 System Policies (`eidos/policies/POL-NNN-<slug>.adoc`)

```yaml
---
type: product/policy
schema: schemas/v1/product/policy.schema.yaml
id: POL-005-WEBHOOK-IDEMPOTENCY
title: "Idempotent External Webhook Processing"
status: active
category: architectural # governance | architectural | security | compliance | testing | performance | lifecycle
severity: blocking # blocking | warning | advisory
invariants:
  - id: INV-POL-005-WEBHOOK-IDEMPOTENCY
    rule: "All external webhook handlers must deduplicate incoming deliveries by provider event ID before mutating domain state."
    test_binding: "tests/billing/test_webhooks.py::test_webhook_idempotency"
---
= Policy: Idempotent External Webhook Processing
```

### 5.2 Polyglot Invariant Test Bindings (`POL-002`)

Every capability, policy, container, or component invariant (`INV-[A-Z0-9_-]+`) MUST declare:
1. `test_binding: "<relative_test_file_path>::<test_function_or_Class::method>"` in YAML frontmatter.
2. An explicit mention of the `INV-*` ID inside the bound test's docstring, comment, assertion message, or test title/display name.

| Language | `test_binding` Format | How to Reference `INV-*` in Test Source |
| :--- | :--- | :--- |
| **Python (`pytest`)** | `tests/test_auth.py::test_token_expiry` (or `::TestAuth::test_token_expiry`) | Include `INV-AUTH-EXPIRY` in docstring (`"""Verify INV-AUTH-EXPIRY..."""`) or comment. |
| **TypeScript / JS (`vitest`, `jest`)** | `tests/auth.test.ts::test_token_expiry` | Include `INV-AUTH-EXPIRY` in `describe`/`it`/`test` title or comment above function. |
| **Go (`go test`)** | `pkg/auth/auth_test.go::TestTokenExpiry` | Include `// INV-AUTH-EXPIRY` comment on or inside `func TestTokenExpiry(t *testing.T)`. |
| **Rust (`cargo test`)** | `tests/auth_test.rs::test_token_expiry` | Include `// INV-AUTH-EXPIRY` comment on or inside `#[test] fn test_token_expiry()`. |
| **Java / Kotlin (`JUnit`)** | `src/test/java/AuthTest.java::testTokenExpiry` | Include `// INV-AUTH-EXPIRY` or `@DisplayName("INV-AUTH-EXPIRY: ...")` on `@Test` method. |

*(Note: Eidos structural AST and diff indexing also supports C/C++, C#, Ruby, PHP, Swift, Vue, and Svelte source files.)*

---

## 6. Cross-Reference Integrity Rules

* **File Path References** (`personas`, `policies`, `screens`, `flows`, `clients`): Must be valid repository-relative paths (`eidos/personas/...`, `eidos/policies/...`, `eidos/design/...`) or entity IDs pointing to existing `.adoc` files.
* **Entity ID References** (`domain`, `container`, `containers`, `components`, `contracts`, `provides`, `consumes`, `extends`, `implements`, `design_system`): Must match the exact `id:` declared in the target specification's YAML frontmatter.
