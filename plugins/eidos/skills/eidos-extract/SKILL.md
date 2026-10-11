---
name: eidos-extract
description: Systematically reverse-engineer and extract Eidos living specifications (Arc42/C4 architecture, domains, capabilities, invariants, contracts) from any existing codebase.
---

# Eidos Reverse-Engineering & Codebase Extraction (`eidos-extract`)

This skill guides AI agents through the systematic reverse-engineering of an existing (brownfield/legacy) codebase into a formal, validated **Eidos living specification repository**.

## The Extraction Objective

Transform an unannotated codebase into a synchronized, self-describing Eidos workspace:

```
 Existing Codebase                    Eidos Living Specifications
 ┌─────────────────────────┐          ┌────────────────────────────────────────┐
 │ package.json / pyproject │ ──────> │ eidos/architecture/components/*.adoc   │
 │ src/ routes / handlers  │ ──────> │ eidos/architecture/contracts/*.adoc    │
 │ models / services       │ ──────> │ eidos/domains/<name>/domain.adoc       │
 │ business rules / checks │ ──────> │ eidos/domains/<name>/capabilities/*.adoc│
 │ tests/ directory        │ ──────> │ Invariant test bindings (INV-*)        │
 │ linter / CI / guardrails│ ──────> │ eidos/policies/POL-*.adoc              │
 └─────────────────────────┘          └────────────────────────────────────────┘
```

---

## The 8-Phase Extraction Pipeline

Follow this disciplined sequence when onboarded to extract Eidos from a codebase.

### Phase 1: Codebase Reconnaissance & Container Inventory

Scan package configurations and build manifests to identify deployable containers and library packages.

1. **Detect Frameworks and Runtimes**:
   - Python: `pyproject.toml`, `setup.cfg`, `requirements.txt`
   - Node/TypeScript: `package.json`, `pnpm-workspace.yaml`, `lerna.json`
   - Rust: `Cargo.toml`
   - Go: `go.mod`
   - Java/Kotlin: `pom.xml`, `build.gradle`

2. **Catalog Containers**:
   Identify whether each package is a `service`, `library`, `cli_app`, `frontend_spa`, or `background_worker`.

3. **Scaffold Component Manifests**:
   For each package, generate `tech/component` manifests:
   ```bash
   eidos spec create component CMP-SERVICE-NAME \
     --title "Service Display Name" \
     --container-type service \
     --runtime "Node.js 20 / Fastify" \
     --package-path "packages/service-name"
   ```

4. **Document Internal Modules (C4 Level 3 Whitebox)**:
   Inspect source directories and record key modules in `c4_internal_components` within each component manifest:
   ```yaml
   c4_internal_components:
     - name: "TokenManager"
       responsibility: "Signs and verifies JWT session tokens."
       source_path: "src/auth/tokens.ts"
     - name: "UserStore"
       responsibility: "PostgreSQL persistence adapter for account profiles."
       source_path: "src/storage/users.ts"
   ```

---

### Phase 2: Interface Boundaries & Contracts Extraction

Locate external communication boundaries and translate them into `tech/contract` specifications.

1. **HTTP / REST APIs**:
   - Search for OpenAPI / Swagger specs (`openapi.yaml`, `swagger.json`).
   - Or identify routing files (Express `routes/`, FastAPI `routers/`, Spring `@RestController`).
   - Scaffold:
     ```bash
     eidos spec create contract CTR-SERVICE-API \
       --title "Service Public REST API" \
       --protocol openapi \
       --owning-component CMP-SERVICE-NAME \
       --specification-file "openapi.yaml"
     ```

2. **RPC / gRPC / Protobuf**:
   - Search for `.proto` definitions.
   - Set protocol to `protobuf`.

3. **CLI Interfaces**:
   - Inspect CLI parsers (Typer, Click, Commander, Yargs, Clap).
   - Set protocol to `cli_args` with owning CLI component.

4. **Event Streams / Messaging**:
   - Look for Kafka, RabbitMQ, Redis Pub/Sub, or SQS consumers/producers.
   - Set protocol to `asyncapi` or `json_schema`.

---

### Phase 3: Bounded Contexts & Domain Extraction

Group code by business responsibility using Domain-Driven Design (DDD) taxonomy.

1. **Identify Domains**:
   - Inspect top-level modules or directory names (e.g., `billing/`, `auth/`, `analytics/`, `inventory/`).
   - Classify domain roles:
     - `core`: Unique differentiator (e.g. trading algorithm, recommendation engine).
     - `supporting`: Custom internal helpers (e.g. report generator).
     - `generic`: Common commodity (e.g. auth, notifications).
     - `platform`: Infrastructure, telemetry, VCS bridge.

2. **Scaffold Domains**:
   ```bash
   eidos spec create domain DOM-BILLING --title "Billing & Subscription Management"
   ```

3. **Define Boundary Responsibilities**:
   Edit `eidos/domains/<name>/domain.adoc` to list explicit responsibilities and out-of-scope boundaries.

---

### Phase 4: Product Capabilities & Invariant Harvesting

Extract concrete user capabilities from controllers, services, and domain logic.

1. **Extract Capabilities (`CAP-*`)**:
   - For each business feature, formulate a Job-To-Be-Done (JTBD):
     *"When [situation], I want to [action], so that [outcome]."*
   - Scaffold capability:
     ```bash
     eidos spec create capability CAP-BILL-001 \
       --title "Subscription Checkout" \
       --domain DOM-BILLING
     ```

2. **Harvest Invariants from Code Logic**:
   Look for:
   - Input validation rules (e.g. Joi, Zod, Pydantic, class-validator).
   - State transition guards (e.g. "Cannot cancel an already refunded invoice").
   - Security constraints (e.g. "Tenants can only access their own organization data").
   - Database constraints (e.g. unique email per tenant, positive balances).

3. **Declare Invariants with Stable IDs**:
   Add them under `technical.invariants` in `eidos/domains/<domain>/capabilities/*.adoc`:
   ```yaml
   technical:
     components:
       - "CMP-BILLING-SERVICE"
     contracts:
       interfaces:
         - "CTR-BILLING-API"
     invariants:
       - id: INV-BILL-NON-NEGATIVE-AMOUNT
         rule: "Invoice charge amounts must strictly be greater than zero."
         test_binding: "tests/billing/test_invoice.py::test_charge_positive"
       - id: INV-BILL-PRORATION-CALC
         rule: "Mid-cycle plan changes prorate remaining billing days accurately to the second."
         test_binding: "tests/billing/test_proration.py::test_upgrade_proration"
   ```

---

### Phase 5: Invariant-Test Traceability (`POL-002`)

Bind existing automated tests directly to harvested invariants.

1. **Inspect Test Suite**:
   Scan `tests/`, `spec/`, or `__tests__/` for unit, integration, and E2E tests.

2. **Establish Bindings**:
   - Add `test_binding` in the capability frontmatter pointing to the test file and function.
   - Annotate the test in code with docstring or comment referencing `INV-*`:
     ```python
     def test_charge_positive():
         """Verify invoice amount positivity (INV-BILL-NON-NEGATIVE-AMOUNT, POL-002)."""
         ...
     ```

3. **Identify Test Coverage Gaps**:
   If an invariant is enforced in production code but lacks an automated test, create the test or mark as tech-debt in a SpecDelta.

---

### Phase 6: System Policies Extraction (`POL-*`)

Extract universal architectural guardrails and engineering ground truth.

1. **Locate Guardrails**:
   - Monorepo boundary rules (`nx.json`, `lerna.json`, ESLint boundary rules).
   - Data security & privacy rules (encryption at rest, PII retention).
   - Core invariants (e.g. `POL-001-GIT-SOURCE-OF-TRUTH`, `POL-002-INVARIANT-TEST-ENFORCEMENT`).

2. **Scaffold Policies**:
   ```bash
   eidos spec create policy POL-003-DATABASE-ISOLATION \
     --title "Multi-Tenant Data Isolation" \
     --category security \
     --rule "Every database query must filter by tenant_id partition key."
   ```

---

### Phase 7: Actor & Persona Modeling (`PER-*`)

Extract the human and automated actors that interact with the system.

1. **Inspect User Roles & Auth Enums**:
   - Check RBAC / permission tables (e.g. `Role: ADMIN | MEMBER | AUDITOR | BILLING_MANAGER`).
   - Identify background systems or autonomous bot integrations.

2. **Scaffold Personas**:
   ```bash
   eidos spec create persona PER-WORKSPACE-ADMIN \
     --title "Workspace Administrator"
   ```

3. **Map Personas to Capabilities**:
   Ensure capabilities declare which personas benefit from them under `business.personas`.

---

### Phase 8: System Context & Baseline Locking

Assemble the top-level System Context and validate the extracted specification.

1. **Author `eidos/system.adoc`**:
   - Summarize the global ecosystem mission, primary external actors, and high-level architecture.

2. **Validate Entire Specification Graph**:
   ```bash
   eidos validate eidos
   ```
   Resolve all schema warnings, broken cross-references, or missing links until:
   ```
   Success! All specification documents are valid.
   ```

3. **Commit the Extraction**:
   Commit the complete `eidos/` specification baseline to git.

---

## Agent Extraction Heuristics

| Artifact in Codebase | Target Eidos Specification | Schema |
| :--- | :--- | :--- |
| `package.json`, `Cargo.toml`, `pyproject.toml` | `eidos/architecture/components/<name>.adoc` | `tech/component` |
| `openapi.yaml`, `.proto`, CLI parser | `eidos/architecture/contracts/<name>.adoc` | `tech/contract` |
| Feature directory, business module | `eidos/domains/<name>/domain.adoc` | `product/domain` |
| Controller endpoint, user journey, service method | `eidos/domains/<name>/capabilities/<name>.adoc` | `product/capability` |
| Validation rules, assert statements, state guards | `technical.invariants` in capability | `INV-*` |
| Test suite functions, test files | `test_binding` in capability invariant | `path/to/test.py::test_fn` |
| Security rules, multi-tenancy rules, quality gates | `eidos/policies/POL-*.adoc` | `product/policy` |
| User roles, auth token scopes | `eidos/personas/<name>.adoc` | `product/persona` |

