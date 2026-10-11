---
name: eidos-design
description: Author, inspect, and maintain Eidos Multi-Design Systems, W3C Design Tokens, UI component contracts, screen state machines, and bidirectional links to product capabilities and SpecDeltas.
---

# Eidos Multi-Design System & UI/UX Authoring (`eidos-design`)

This skill equips AI agents to author, inspect, and maintain **Multi-Design Systems**, **W3C Design Tokens**, **UI Component Manifests**, and **Screen State Machines** within an Eidos repository, bridging the gap between product capabilities, visual design, and frontend code.

---

## Core Principles

1. **Multi-Design System Topology**:
   - A single product can support multiple client or brand design systems (e.g. `DS-CORE`, `DS-ACME`, `DS-GLOBEX`).
   - Client design systems inherit from a base system (`extends: "DS-CORE"`), supplying brand token overrides and optional specialized components.
2. **Official W3C Design Tokens**:
   - Tokens follow the W3C Design Tokens Community Group (DTCG) standard (`$value`, `$type`, `$description`).
   - Interoperable with Style Dictionary, Tailwind CSS, CSS variables, and Figma Token Studio.
3. **The Product-Experience-Tech Trinity**:
   - Capabilities (`CAP-*`) link directly to their user-facing screens, flows, and design systems via the `experience` block.
   - Screens (`SCR-*`) declare their required layout slots, state machines, and supported design systems.
4. **Enforced Accessibility (a11y) Invariants**:
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
│   ├── acme/                         <- Client Acme Design System
│   │   ├── system.adoc               <- extends: "DS-CORE", client_id: "acme"
│   │   ├── tokens.json               <- Acme brand token overrides
│   │   └── components/               <- (Optional) Acme-specific custom components
│   │       └── header.adoc           <- implements: "UIC-CORE-HEADER"
│   │
│   └── globex/                       <- Client Globex Design System
│       ├── system.adoc               <- extends: "DS-CORE", client_id: "globex"
│       └── tokens.json
│
├── screens/                          <- Product Screens (SCR-*)
│   ├── checkout.adoc                 <- route: "/checkout", slots, states
│   └── dashboard.adoc
│
└── flows/                            <- User Interaction Flows (FLOW-*)
    └── onboarding.adoc
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

## 5. Screen Manifest Anatomy (`screen.schema.yaml`)

Screen specifications live in `eidos/design/screens/<name>.adoc`:

```yaml
---
type: design/screen
schema: schemas/v1/design/screen.schema.yaml
id: SCR-CHECKOUT
title: "Order Checkout Screen"
route: "/checkout"
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

## 6. Linking Capabilities (`CAP-*`) to Experience

Update `eidos/domains/<domain>/capabilities/<name>.adoc` with the `experience` block:

```yaml
experience:
  screens:
    - "eidos/design/screens/checkout.adoc"
  flows:
    - "eidos/design/flows/one-click-buy.adoc"
  design_systems:
    - "eidos/design/systems/core/system.adoc"
    - "eidos/design/systems/acme/system.adoc"
  ui_components:
    - "eidos/design/systems/core/components/button.adoc"
```

---

## 7. SpecDelta Mutations for Design

When modifying design systems or UI in a SpecDelta (`DLT-*`), declare design mutations:

```yaml
mutations:
  - action: modify_screen
    target_screen: "eidos/design/screens/checkout.adoc"
    details: "Add express payment selector row above standard payment form."
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
    - "eidos/design/screens/checkout.adoc"
```

Always run validation before concluding your work:
```bash
eidos validate eidos
```

