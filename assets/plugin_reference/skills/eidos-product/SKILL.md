---
name: eidos-product
description: Author, inspect, and maintain Eidos product domains (DOM-*), capabilities (CAP-*), actor personas (PER-*), and Jobs-to-be-Done (JTBD).
---

# Eidos Product & Domain Modeling (`eidos-product`)

This skill equips AI agents to define, organize, and evolve the **Product Layer** of an Eidos repository: Bounded Contexts / Domains (`DOM-*`), Capabilities (`CAP-*`), and Actor Personas (`PER-*`).

---

## 1. The Product Layer Architecture

The Product Layer is the authoritative definition of **WHAT** value the software delivers, **WHO** uses it, and **WHY**, independent of transient technical frameworks.

```
eidos/
├── domains/
│   └── <domain-slug>/
│       ├── domain.adoc                  # type: product/domain (DOM-*)
│       └── capabilities/
│           ├── <capability-a>.adoc      # type: product/capability (CAP-*)
│           └── <capability-b>.adoc
└── personas/
    ├── <actor-persona>.adoc             # type: product/persona (PER-*)
    └── autonomous-agent.adoc
```

### Tri-Pillar Linkage
Every capability connects business value directly to technical architecture:
$$\text{Persona (PER-*)} \longrightarrow \text{Capability (CAP-*)} \xrightarrow{\text{binds to}} \begin{cases} \text{Components (CMP-*)} \\ \text{Contracts (CTR-*)} \\ \text{Invariants (INV-*)} \end{cases}$$

---

## 2. Domain Modeling (`DOM-*`)

Domains represent **Bounded Contexts** following Domain-Driven Design (DDD).

### Strategic Classification
When defining a domain, classify its architectural role:
* **`core`**: Primary competitive differentiator and business value driver (e.g., trading algorithm, recommendation engine, document validator).
* **`supporting`**: Custom business capability tailored to the organization (e.g., invoice generator, custom report builder).
* **`generic`**: Standard commodity capability (e.g., authentication, notifications, billing gateway).
* **`platform`**: Foundational developer infrastructure, telemetry, and VCS bridge.

### Authoring Domains
Use the Eidos CLI or MCP tool:
```bash
eidos spec create domain DOM-BILLING --title "Billing & Subscription Management"
```
Or edit `eidos/domains/<slug>/domain.adoc`:
```yaml
---
type: product/domain
schema: schemas/v1/product/domain.schema.yaml
id: DOM-BILLING
name: "Billing & Subscription Management"
classification: core
boundaries:
  responsibilities:
    - "Subscription tier lifecycle (subscribe, upgrade, downgrade, cancel)"
    - "Invoice generation and payment gateway orchestration"
    - "Usage metering and quota tracking"
  out_of_scope:
    - "General ledger accounting (handled by ERP)"
    - "User authentication (delegated to DOM-AUTH)"
---
= Domain: Billing & Subscription Management
:toc: left

== Domain Vision
Orchestrates monetization, payment processing, and subscription lifecycles with zero-tolerance for billing inaccuracies.
```

---

## 3. Capability Authoring (`CAP-*`)

Capabilities represent distinct units of business outcome.

### Jobs-to-be-Done (JTBD) Formula
Every capability must define a clear JTBD:
$$\text{"When [situation], I want to [action], so that [outcome]."}$$

### Authoring Capabilities
Use the Eidos CLI or MCP tool:
```bash
eidos spec create capability CAP-BILL-001 \
  --title "Self-Service Plan Upgrade" \
  --domain DOM-BILLING
```
Or author `eidos/domains/<slug>/capabilities/<slug>.adoc`:
```yaml
---
type: product/capability
schema: schemas/v1/product/capability.schema.yaml
id: CAP-BILL-001
title: "Self-Service Plan Upgrade"
domain: DOM-BILLING
status: active  # proposed | active | deprecated | removed
version: "1.0.0"

business:
  personas:
    - eidos/personas/workspace-admin.adoc
  jtbd: "When my team needs higher quota limits, I want to upgrade our plan self-service, so that our workflows are never blocked."
  policies:
    - eidos/policies/POL-001-git-as-single-source-of-truth.adoc

technical:
  components:
    - "CMP-BILLING-SERVICE"
  contracts:
    interfaces:
      - "CTR-BILLING-API"
  invariants:
    - id: INV-BILL-PRORATION-CALC
      rule: "Mid-cycle plan changes prorate remaining billing days accurately to the second."
      test_binding: "tests/billing/test_proration.py::test_upgrade_proration"
    - id: INV-BILL-ZERO-DOWNTIME-PROVISIONING
      rule: "Quota limits must update immediately upon successful payment confirmation."
      test_binding: "tests/billing/test_provisioning.py::test_instant_quota_update"
---
= Capability: Self-Service Plan Upgrade
:toc: left

== Intent
Allows tenant administrators to upgrade subscription tiers instantaneously without sales assistance.

== Acceptance Criteria
1. Immediate prorated invoice calculation.
2. Immediate quota limit refresh in token bucket cache.
3. Automated confirmation email and receipt generation.
```

---

## 4. Actor & Persona Modeling (`PER-*`)

Personas model the human stakeholders and autonomous agents interacting with the system.

### Role Types
* **`human_engineer`**: Developers, architects, DevOps operators.
* **`end_user`**: Customers, tenant administrators, members.
* **`autonomous_agent`**: AI coding agents (`agy`, Claude, Cursor, CI bots).

### Authoring Personas
Use the Eidos CLI or MCP tool:
```bash
eidos spec create persona PER-WORKSPACE-ADMIN --title "Workspace Administrator"
```
Or author `eidos/personas/<name>.adoc`:
```yaml
---
type: product/persona
schema: schemas/v1/product/persona.schema.yaml
id: PER-WORKSPACE-ADMIN
name: "Workspace Administrator"
role_type: end_user
responsibilities:
  - "Team membership and role assignments"
  - "Subscription management and payment methods"
  - "Security and SSO policy configuration"
goals:
  - "Frictionless self-service team onboarding"
  - "Predictable billing with zero surprises"
---
= Persona: Workspace Administrator
:toc: left

== Profile
Decision maker managing workspace configuration, billing, and team access.
```

---

## 5. Verification & Specification Integrity

After adding or modifying domains, capabilities, or personas:

1. **Verify Schema & Link Integrity:**
   ```bash
   eidos validate eidos
   ```
   Ensure:
   - All `business.personas` paths exist.
   - All `business.policies` paths exist.
   - All referenced `technical.components` and `contracts` exist in `eidos/architecture/`.

2. **Verify Invariant Coverage:**
   ```bash
   eidos drift --uncommitted
   ```
   Ensure every `INV-*` declared in the capability has matching test coverage in `tests/`.
