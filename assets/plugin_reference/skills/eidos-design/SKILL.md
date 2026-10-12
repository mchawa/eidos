---
name: eidos-design
description: Author, inspect, and maintain Eidos Multi-Design Systems, Client Applications (CLT-*), W3C Design Tokens, UI component contracts, screen state machines, and interaction flows (FLOW-*).
---

# Eidos Multi-Design System, Client & UI/UX Authoring (`eidos-design`)

This skill equips AI agents to author, inspect, and maintain **Multi-Design Systems**, **Client Applications (`CLT-*`)**, **W3C Design Tokens**, **UI Component Manifests (`UIC-*`)**, **Screen State Machines (`SCR-*`)**, and **Interaction Flows (`FLOW-*`)** within an Eidos repository, bridging the gap between product capabilities, visual design, and frontend code.

---

## Core Principles

1. **Multi-Design System Topology**:
   - A single product can support multiple client or brand design systems (e.g. `DS-CORE`, `DS-ACME`, `DS-GLOBEX`).
   - Client design systems inherit from a base system (`extends: "DS-CORE"`), supplying brand token overrides and optional specialized components.
2. **Client Application Manifests (`CLT-*`)**:
   - Dedicated client specifications bind frontend targets (web SPA, iOS, Android, desktop, TUI) to their design system (`design_system: "DS-CORE"`), monorepo container (`container: "CMP-..."`), scoped screens, and flows.
3. **Client-Internal & Omnichannel Flows (`FLOW-*`)**:
   - Client-internal user journeys are co-located within the client folder (`eidos/design/clients/<client>/flows/`).
   - Cross-client omnichannel journeys live at the top level (`eidos/design/flows/`), tracing transitions across CLI, Web, and backend services.
4. **Official W3C Design Tokens**:
   - Tokens follow the W3C Design Tokens Community Group (DTCG) standard (`$value`, `$type`, `$description`).
   - Interoperable with Style Dictionary, Tailwind CSS, CSS variables, and Figma Token Studio.
5. **The Product-Experience-Tech Trinity**:
   - Capabilities (`CAP-*`) link directly to their user-facing clients, screens, flows, and design systems via the `experience` block.
   - Screens (`SCR-*`) declare their required layout slots, state machines, owning client, and supported design systems.
6. **Enforced Accessibility (a11y) Invariants**:
   - Every UI component specifies WCAG target level (AA/AAA), WAI-ARIA role, and keyboard interaction behavior bound to automated tests.

---

## 1. Directory Structure (`eidos/design/`)

```
eidos/design/
├── systems/                          <- Multi-Design System catalog
│   ├── core/                         <- Default / Base Design System
│   │   ├── system.adoc               <- type: design/system (id: DS-CORE)
│   │   ├── tokens.json               <- W3C tokens (colors, spacing, typography)
│   │   └── components/               <- Base UI Component Manifests (UIC-CORE-*)
│   │       ├── button.adoc
│   │       ├── modal.adoc
│   │       └── input.adoc
│   │
│   └── acme/                         <- White-Label Theme
│       ├── system.adoc               <- extends: "DS-CORE", client_id: "acme"
│       └── tokens.json               <- Acme brand token overrides
│
├── clients/                          <- Client Applications (CLT-*)
│   └── web-portal/                   <- Web Client target
│       ├── client.adoc               <- type: design/client (id: CLT-WEB-PORTAL)
│       ├── screens/                  <- Client-owned Screens (SCR-*)
│       │   ├── portal-home.adoc      <- client: "CLT-WEB-PORTAL", route: "/"
│       │   └── portal-catalog.adoc   <- client: "CLT-WEB-PORTAL", route: "/catalog"
│       └── flows/                    <- Client-internal user journeys (FLOW-*)
│           └── spec-exploration.adoc <- flow_type: client_internal
│
└── flows/                            <- Omnichannel / Cross-Client Journeys
    └── cli-serve-and-browse.adoc     <- flow_type: cross_client (CLI -> Portal)
```

---

## 2. CLI Scaffolding Commands

Use the Eidos CLI to scaffold design systems, components, and screens:

```bash
# 1. Scaffold the core base design system:
eidos spec create design-system DS-CORE --title "Core Product Design System"

# 2. Scaffold a client-specific design system extending the core:
eidos spec create design-system DS-ACME --title "Acme Enterprise Theme" \
  --client acme --extends DS-CORE

# 3. Scaffold a UI component under a design system:
eidos spec create ui-component UIC-CORE-BUTTON --title "Primary Action Button" \
  --system DS-CORE

# 4. Scaffold a client-specific component override:
eidos spec create ui-component UIC-ACME-HEADER --title "Acme Corporate Header" \
  --system DS-ACME --implements UIC-CORE-HEADER

# 5. Scaffold a product screen:
eidos spec create screen SCR-CHECKOUT --title "Checkout Screen" \
  --route "/checkout"
```

---

## 3. W3C Design Tokens Standard (`tokens.json`)

Token manifests must conform to the W3C DTCG specification:

```json
{
  "color": {
    "brand": {
      "primary": {
        "$value": "#0052cc",
        "$type": "color",
        "$description": "Primary brand action color"
      },
      "surface": {
        "$value": "#ffffff",
        "$type": "color"
      }
    }
  },
  "spacing": {
    "md": {
      "$value": "16px",
      "$type": "dimension"
    }
  },
  "border": {
    "radius": {
      "button": {
        "$value": "8px",
        "$type": "dimension"
      }
    }
  }
}
```

In a client design system (e.g. `DS-ACME`), only specify token overrides:
```json
{
  "color": {
    "brand": {
      "primary": {
        "$value": "#e63946",
        "$type": "color",
        "$description": "Acme corporate red override"
      }
    }
  }
}
```

---

## 4. Component Manifest Anatomy (`ui-component.schema.yaml`)

UI component manifests live in `eidos/design/systems/<system>/components/<name>.adoc`:

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
    description: "High-emphasis visual action."
  - name: "destructive"
    description: "Irreversible or critical actions."

states:
  - default
  - hover
  - active
  - focus
  - disabled
  - loading

accessibility:
  wcag_level: "AA"
  aria_role: "button"
  keyboard_nav: "Enter and Space trigger action; Tab traverses focus."

test_bindings:
  storybook: "packages/ui/src/Button.stories.tsx"
  unit_test: "packages/ui/src/Button.test.tsx"
---
= UI Component: Action Button
:toc: left

== States & Interaction Matrix
Detailed component behavior and rendering rules.
```

---

## 5. Client Application Manifest Anatomy (`client.schema.yaml`)

Client manifests live in `eidos/design/clients/<client-name>/client.adoc`:

```yaml
---
type: design/client
schema: schemas/v1/design/client.schema.yaml
id: CLT-WEB-PORTAL
title: "Living Web Portal Client"
status: active
platform: web
design_system: "eidos/design/systems/core/system.adoc"
container: "eidos/architecture/components/portal.adoc"

capabilities:
  - "eidos/domains/product-core/capabilities/living-spec-engine.adoc"

screens:
  - "eidos/design/clients/web-portal/screens/portal-home.adoc"
  - "eidos/design/clients/web-portal/screens/portal-catalog.adoc"

flows:
  - "eidos/design/clients/web-portal/flows/spec-exploration.adoc"
---
= Client: Living Web Portal Client
:toc: left

== Client Architecture Overview
Single Page Application (SPA) serving as the interactive visualization dashboard.
```

---

## 6. Interaction Flow Anatomy (`flow.schema.yaml`)

Interaction flows document step-by-step user journeys and screen transitions:
* **Client-Internal Flows**: `eidos/design/clients/<client-name>/flows/<name>.adoc`
* **Omnichannel Journeys**: `eidos/design/flows/<name>.adoc`

```yaml
---
type: design/flow
schema: schemas/v1/design/flow.schema.yaml
id: FLOW-PORTAL-EXPLORATION
title: "Specification Discovery & Inspection Flow"
status: active
flow_type: client_internal
client: "CLT-WEB-PORTAL"
capabilities:
  - "eidos/domains/product-core/capabilities/living-spec-engine.adoc"

steps:
  - step: 1
    title: "Access Dashboard"
    screen: "eidos/design/clients/web-portal/screens/portal-home.adoc"
    action: "User accesses root portal URL"
    outcome: "Hero metrics and architectural pillars displayed"

  - step: 2
    title: "Navigate to Master Catalog"
    screen: "eidos/design/clients/web-portal/screens/portal-catalog.adoc"
    action: "User clicks Living Specs Catalog in sidebar"
    outcome: "Hierarchical Tree-Grid loaded at Level 2 zoom"
---
= Flow: Specification Discovery & Inspection Flow
:toc: left

== Visual Interaction Flowchart
[mermaid]
----
flowchart LR
  Home["SCR-PORTAL-HOME<br/>Dashboard"] -->|Click Catalog| Cat["SCR-PORTAL-CATALOG<br/>Tree-Grid"]
  Cat -->|Click Row| Modal["Spec Inspector Drawer"]
----
```

---

## 7. Screen Manifest Anatomy (`screen.schema.yaml`)

Screen specifications live in `eidos/design/clients/<client-name>/screens/<name>.adoc`:

```yaml
---
type: design/screen
schema: schemas/v1/design/screen.schema.yaml
id: SCR-CHECKOUT
title: "Order Checkout Screen"
route: "/checkout"
client: "CLT-WEB-APP"
status: active

capabilities:
  - "eidos/domains/checkout/capabilities/express-checkout.adoc"

supported_design_systems:
  - "eidos/design/systems/core/system.adoc"
  - "eidos/design/systems/acme/system.adoc"

slots:
  express_wallet: "UIC-*-WALLET-BUTTON"
  payment_form: "UIC-*-PAYMENT-FORM"
  cart_summary: "UIC-*-CART-SUMMARY"

states:
  - name: "loading"
    description: "Skeleton loader displayed during session retrieval."
  - name: "active"
    description: "Interactive form with active payment methods."
  - name: "error"
    description: "Error alert boundary for network/card failures."
---
= Screen: Order Checkout
:toc: left

== View Layout & Component Placement
Define structural layout, responsive behavior, and layout slots.
```

---

## 8. Linking Capabilities (`CAP-*`) to Experience

Update `eidos/domains/<domain>/capabilities/<name>.adoc` with the `experience` block:

```yaml
experience:
  clients:
    - "eidos/design/clients/web-portal/client.adoc"
  screens:
    - "eidos/design/clients/web-portal/screens/portal-catalog.adoc"
  flows:
    - "eidos/design/clients/web-portal/flows/spec-exploration.adoc"
  design_systems:
    - "eidos/design/systems/core/system.adoc"
  ui_components:
    - "eidos/design/systems/core/components/portal-shell.adoc"
```

---

## 9. SpecDelta Mutations for Design

When modifying design systems or UI in a SpecDelta (`DLT-*`), declare design mutations:

```yaml
mutations:
  - action: modify_screen
    target_screen: "eidos/design/clients/web-portal/screens/portal-catalog.adoc"
    details: "Add client switcher filter to living specs catalog."
  - action: add_ui_component
    target_ui_component: "eidos/design/systems/core/components/wallet-button.adoc"
    details: "New digital wallet action button."
  - action: modify_design_system
    target_design_system: "eidos/design/systems/acme/system.adoc"
    details: "Reconcile Acme brand tokens for digital wallet component."

blast_radius:
  impacted_design_systems:
    - "DS-CORE"
    - "DS-ACME"
  impacted_screens:
    - "eidos/design/clients/web-portal/screens/portal-catalog.adoc"
```

Always run validation before concluding your work:
```bash
eidos validate eidos
```

