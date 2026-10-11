# Living Specifications

Eidos rejects the concept of static, append-only documentation in favor of **Living Specifications**.

---

## The Flaw of ADRs (Architecture Decision Records)

Traditional software engineering teams frequently use ADRs to capture architectural decisions. Over time, ADRs consistently fail:

1. **Write-Only Graveyards**: ADRs record decisions at a single frozen moment in time. Months later, no one knows which parts of ADR #12 were superseded by ADR #34 or contradicted by ADR #59.
2. **Context Fragmentation**: Engineers and AI agents are forced to mentally reconstruct the current system state by reading an endless stream of chronological notes.
3. **No Verifiability**: ADRs cannot be mechanically validated against actual interfaces, contracts, or test suites.

> [!IMPORTANT]
> **Eidos Rule #2: Absolutely No ADRs.**
> All decisions and architectural evolutions are recorded **in-place** directly inside the living specifications.

---

## Structure of Living Specs

All living specifications are authored in **AsciiDoc (`.adoc`)** with structured **YAML frontmatter**. This allows them to be human-readable, Git-mergeable, and programmatically validated.

```
eidos/
├── system.adoc                   # System boundaries (C4 Level 1)
├── architecture/
│   ├── system-architecture.adoc  # Container topology (C4 Level 2)
│   ├── components/               # Component manifests (C4 Level 3)
│   └── contracts/                # Interface contracts (APIs, schemas)
├── domains/                      # Product domains and capability trees
├── personas/                     # Target personas (users & autonomous agents)
└── policies/                     # Inviolable architectural invariants
```

---

## Anatomy of a Living Specification

Here is an example of a Component manifest (`eidos/architecture/components/core.adoc`):

```asciidoc
---
id: CMP-EIDOS-CORE
name: Eidos Core Engine
type: tech/component
schema: /v1/tech/component.schema.yaml
container_type: library
runtime: python
package_path: src/eidos/core
contracts:
  provides:
    - CTR-EIDOS-VALIDATOR
    - CTR-EIDOS-SCAFFOLDER
  consumes: []
dependencies:
  internal: []
  external:
    - typer
    - pyyaml
    - jsonschema
---
= Eidos Core Engine

The Core Engine provides the fundamental schema validation, specification parsing,
and templating mechanisms for all Eidos operations.
```

### Key Elements:
* **`id`**: Globally unique document identifier (`CMP-*`, `CTR-*`, `CAP-*`, `POL-*`).
* **`schema`**: Formal schema validation reference ensuring structural integrity.
* **`provides` / `consumes`**: Explicit declaration of interface contracts.
* **Body text**: Rich AsciiDoc documentation that evolves continuously with the code.

