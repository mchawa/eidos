# Quickstart Guide

This guide will walk you through initializing, validating, and exploring Eidos living specifications in any repository in less than 5 minutes.

---

## Step 1: Initialize Eidos in Your Repository

Navigate to your project root and run `eidos init`. You can choose between the **Full Multi-Domain Skeleton** or the progressive **Eidos Lite Profile** (`--lite`):

=== "Full Catalog (Default)"

    ```bash
    # Initialize complete living specification hierarchy
    eidos init

    # Populate and synchronize the architectural knowledge graph
    eidos index sync
    ```

=== "Progressive Eidos Lite (`--lite`)"

    ```bash
    # Minimal 3-file starter profile (system.adoc, system-architecture.adoc, POL-001)
    # plus containers/ and capabilities/ directories
    eidos init --lite

    # Populate and synchronize the architectural knowledge graph
    eidos index sync
    ```

The full profile creates the canonical `eidos/` directory skeleton, tracked in Git by `.gitkeep` files and isolated by a self-contained `eidos/.gitignore`:

```text
eidos/
├── system.adoc                   # Root system specification (SYS-*, C4 Level 1)
├── architecture/
│   ├── system-architecture.adoc  # Global Arc42 & C4 Level 1-2 topology (ARCH-*)
│   ├── containers/               # Container manifests (CNT-*, C4 Level 2 + inline Level 3)
│   ├── components/               # Promoted standalone component manifests (CMP-*, C4 Level 3)
│   └── contracts/                # Interface protocol contracts (CTR-*)
├── domains/                      # Domain & capability models (DOM-*, CAP-*)
├── personas/                     # User & agent personas (PER-*)
├── policies/                     # Architectural & governance policies (POL-*)
├── deltas/                       # SpecDelta mutations (DLT-*)
├── explorations/                 # Technical spikes and discoveries (EXP-*)
└── design/                       # Multi-design systems, clients, screens, and flows
```

> [!NOTE]
> **C4 Level 3 Components (`CMP-*`)** are declared inline within container manifests (`eidos/architecture/containers/*.adoc`) by default, or promoted to dedicated manifests in `eidos/architecture/components/<component>.adoc` via `eidos spec component promote <ID>`. Eidos specifications **never touch or reside within source code directories** (`src/`, `packages/`).

---

## Step 2: Validate Specifications & Check Drift

Run the specification validator to ensure all AsciiDoc files conform to their JSON/YAML schemas and have valid cross-references, then run drift detection:

```bash
eidos validate eidos
eidos drift --uncommitted
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

## Step 3: Slice Architectural Context & Launch the Portal

Extract a token-budgeted architectural context slice for any file, component, or task prompt:

```bash
# Slice guardrails context for a container or component
eidos context -c CNT-EIDOS-CORE --tier guardrails

# Or generate a compressed AST Repo Map of your codebase
eidos index repo-map --max-tokens 1500
```

Launch the built-in local HTTP server to explore your living specifications visually in the **Eidos Portal**:

```bash
eidos portal serve --path eidos --port 8080
```

Open your browser to `http://localhost:8080` to explore:
* Interactive hierarchical C4 Level 1–3 diagrams with clickable drill-down, URL hash deep-linking (`#/...`), and client-side Blast Radius simulation.
* Component dependency graphs, boundary interface contracts (`CTR-*`), and embedded architectural decisions (`DEC-*`).
* System capabilities, policy matrices, and test-bound invariants (`INV-*`).
* SpecDelta Kanban/Timeline pipelines and recursive W3C Design Token Studio.

---

## Step 4: Evolve Architecture (Direct Mode vs. SpecDelta)

Before authoring or editing any specification frontmatter, you can inspect the exact required/optional keys and valid YAML templates via `eidos spec schema`:

```bash
eidos spec schema capability
```

For routine bug fixes or localized changes within an existing container (**Direct Mode**), simply update the code, tests, and owning `.adoc` spec together, then verify with `eidos drift --uncommitted`.

For cross-container features or breaking contract changes (**Planned Mode**), scaffold a declarative **SpecDelta**:

```bash
eidos delta create DLT-2026-001 \
  --title "Add Webhook Notification Support" \
  --kind feature
```

This scaffolds `eidos/deltas/dlt-2026-001.adoc`. Once your code, spec mutations, and tests are implemented, verify and reconcile the delta in-place:

```bash
eidos delta verify DLT-2026-001
eidos delta apply DLT-2026-001
```

---

## Next Steps

* Understand **[How Eidos Works Under the Hood](../concepts/how-eidos-works.md)**.
* Learn why Eidos enforces **[Living Specifications & Embedded Decisions (`DEC-*`) over ADRs](../concepts/living-specs.md)**.
* Explore the **[SpecDelta Mutation Lifecycle](../concepts/specdeltas.md)**.
* Configure the **[AI Agent Plugin](../guides/agent-plugin.md)** for your coding assistants.

