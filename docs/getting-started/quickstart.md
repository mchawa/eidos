# Quickstart Guide

This guide will walk you through initializing, validating, and exploring Eidos living specifications in any repository in less than 5 minutes.

---

## Step 1: Initialize Eidos in your Repository

Navigate to your project root and run `eidos init`:

```bash
eidos init
```

This creates the canonical `eidos/` empty directory skeleton, tracked in Git by `.gitkeep` files:

```
eidos/
├── system.adoc                   # Empty root system specification
├── architecture/
│   ├── containers/               # Container manifests (CNT-*, C4 Level 2)
│   ├── components/               # Dedicated component manifests (CMP-*, C4 Level 3)
│   └── contracts/                # Interface contracts (CTR-*)
├── domains/                      # Domain & capability models (DOM-*, CAP-*)
├── personas/                     # User & agent personas (PER-*)
├── policies/                     # Architectural policies (POL-*)
├── deltas/                       # SpecDelta mutations (DLT-*)
├── explorations/                 # Spikes and discoveries (EXP-*)
└── design/                       # Design systems, screens, and flows
```

> [!NOTE]
> **C4 Level 3 Components (`CMP-*`)** are declared inline within container manifests or promoted to dedicated manifests in `eidos/architecture/components/<component>.adoc`. Eidos specifications **never touch or reside within source code directories** (`src/`, `packages/`).

---

## Step 2: Validate Your Specifications

Run the specification validator to ensure all AsciiDoc files conform to their JSON/YAML schemas and have valid cross-references:

```bash
eidos validate eidos
```

Output:

```text
                         Eidos Specification Validation                         
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Document ID            ┃ Type               ┃ Status ┃ Path                  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ CNT-EIDOS-CORE         │ tech/container     │  PASS  │ eidos/architecture/c… │
│ ARCH-EIDOS-001         │ tech/architecture  │  PASS  │ eidos/architecture/s… │
│ CAP-PROD-001           │ product/capability │  PASS  │ eidos/domains/produc… │
│ POL-001-GIT-SOURCE-OF… │ product/policy     │  PASS  │ eidos/policies/POL-0… │
│ SYS-EIDOS-001          │ product/system     │  PASS  │ eidos/system.adoc     │
└────────────────────────┴────────────────────┴────────┴───────────────────────┘

Success! All specification documents are valid.
```

---

## Step 3: Launch the Interactive Living Portal

Eidos includes a built-in local development server that compiles your living specifications into a visual C4 architecture portal:

```bash
eidos serve --path eidos --port 8080
```

Open your browser to `http://localhost:8080` to explore:
* Interactive C4 Level 1–3 diagrams.
* Component dependency graphs.
* System capabilities and policy matrices.
* SpecDelta mutation logs.

---

## Step 4: Propose a Change (SpecDelta)

Before modifying code for a new feature, scaffold a semantic **SpecDelta**:

```bash
eidos delta create "Add Webhook Notification Support"
```

This scaffolds a new mutation document in `eidos/deltas/dlt-2026-xxx.adoc`. Once your feature is implemented and tests pass, reconcile the delta:

```bash
eidos delta apply dlt-2026-xxx
```

---

## Next Steps

* Learn why Eidos enforces **[Living Specifications over ADRs](../concepts/living-specs.md)**.
* Understand the **[SpecDelta Mutation Lifecycle](../concepts/specdeltas.md)**.
* Configure the **[AI Agent Plugin](../guides/agent-plugin.md)** for your coding assistants.

