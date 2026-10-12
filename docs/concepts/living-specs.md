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

All living specifications are authored in **AsciiDoc (`.adoc`)** with structured **YAML frontmatter**. This allows them to be human-readable, Git-mergeable, and programmatically validated against canonical JSON/YAML schemas.

```text
eidos/
├── system.adoc                   # Root system descriptor (C4 Level 1)
├── architecture/                 # Technical architecture (Arc42 + C4)
│   ├── system-architecture.adoc  # Global architecture & C4 Container topology (L1 & L2)
│   ├── containers/               # Container manifests (C4 Level 2)
│   └── contracts/                # Interface protocol contracts (APIs, CLI)
├── domains/                      # DDD bounded contexts & capability trees
│   └── <domain-name>/
│       ├── domain.adoc           # Bounded context manifest (DOM-*)
│       └── capabilities/         # Product capabilities (CAP-*)
├── personas/                     # Human & AI actor roles (PER-*)
├── policies/                     # Inviolable system governance policies (POL-*)
├── explorations/                 # Spikes, hypotheses & feasibility discovery (EXP-*)
├── design/                       # Multi-Design Systems & UI contracts
│   ├── systems/<system>/         # Design systems (DS-*) & W3C DTCG tokens
│   ├── screens/                  # Screen views & route state machines (SCR-*)
│   └── flows/                    # User interaction flows (FLOW-*)
└── deltas/                       # SpecDelta mutations (DLT-*)
```

---

## The Five Living Specification Layers

Eidos models software across five interconnected layers:

1. **Product & Governance Layer**: Defines strategic archetypes ([`SYS-*`](schema-glossary.md#productsystem)), bounded contexts ([`DOM-*`](schema-glossary.md#productdomain)), capabilities ([`CAP-*`](schema-glossary.md#productcapability)), actor personas ([`PER-*`](schema-glossary.md#productpersona)), and non-negotiable policies ([`POL-*`](schema-glossary.md#productpolicy)).
2. **Exploration & Discovery Layer**: Formalizes hypothesis-driven technical spikes and prototypes ([`EXP-*`](explorations.md)) before committing to architectural baselines.
3. **Technical Architecture Layer**: Codifies Arc42 system context ([`ARCH-*`](schema-glossary.md#techarchitecture)), C4 containers ([`CNT-*`](schema-glossary.md#techcontainer)) with internal components ([`CMP-*`]), and protocol contracts ([`CTR-*`](schema-glossary.md#techcontract)) without ADRs.
4. **Experience & Design Layer**: Governs multi-brand themes ([`DS-*`](design.md)), official W3C Design Tokens, UI component contracts ([`UIC-*`](design.md#4-ui-component-manifests-uic-)), and screen state machines ([`SCR-*`](design.md#5-screen-state-machines-scr-)).
5. **Evolution & Mutation Layer**: Orchestrates planned architectural changes through auditable SpecDeltas ([`DLT-*`](specdeltas.md)).

---

## Technical Explorations (`EXP-*`): Hypothesis-Driven Discovery

Before committing to a new database, protocol, or framework in a living specification, teams frequently encounter technical uncertainty. Traditional teams run informal "spikes" that either produce throwaway code with zero institutional memory or result in unreviewed prototype code merging into production.

Eidos solves this by treating spikes as **Explorations (`EXP-*`)**:

* **Hypothesis & Exit Criteria**: Every exploration declares an unambiguous hypothesis, specific questions to answer, benchmark targets, and measurable exit criteria.
* **Disposable Prototype Branches**: Spikes execute on experimental branches (`spike/exp-*`), recording empirical findings (latency percentiles, memory profiles, locking contention).
* **Two Deterministic Outcomes**:
  - **Adopted**: If exit criteria are met, the exploration graduates into a formal **SpecDelta (`DLT-*`)** for production integration.
  - **Discarded**: If the hypothesis is disproven, the exploration is archived with lessons learned, permanently preventing future engineers and AI agents from repeating the same dead end.

> Learn more in the comprehensive **[Explorations Guide](explorations.md)**.

---

## Anatomy of a Living Specification

Here is an example of a Container manifest (`eidos/architecture/containers/core.adoc`):

```asciidoc
---
type: tech/container
schema: schemas/v1/tech/container.schema.yaml
id: CNT-EIDOS-CORE
name: Eidos Core Engine
container_type: library
runtime: "Python 3.12+"
package_path: "src/eidos/core"
components:
  - id: CMP-CORE-PARSER
    name: SpecParser
    responsibility: "Parses AsciiDoc files with YAML frontmatter into structured AST models."
    source_path: "src/eidos/core/parser.py"
contracts:
  provides:
    - "CTR-EIDOS-VALIDATOR"
dependencies:
  internal_containers: []
  external_services:
    - "pyyaml"
    - "jsonschema"
---
= Container: Eidos Core Engine

The Core Engine provides schema validation, specification parsing,
and AST graph generation for all Eidos operations.
```

### Key Elements:
* **`type`**: Canonical specification type matching the Eidos schema taxonomy.
* **`id`**: Globally unique document identifier (`CNT-*`, `CMP-*`, `CTR-*`, `CAP-*`, `POL-*`, `EXP-*`, `DS-*`, `DLT-*`).
* **`schema`**: Reference to the canonical schema file ensuring automated validation.
* **Frontmatter Metadata**: Strongly-typed architectural properties (contracts, dependencies, invariants, tokens).
* **Body text**: Rich AsciiDoc documentation that evolves continuously with the code.

---

## Hybrid Co-Located C4 Level 3 Components (`CMP-*`)

Eidos implements a hybrid architecture for C4 Level 3 components:
* **80% Inline**: Lightweight modules remain inline inside Level 2 container manifests (`eidos/architecture/containers/*.adoc`), keeping macro-architecture clean and compact.
* **20% Co-Located Promoted Specs**: Highly complex subsystems receive dedicated `type: tech/component` specifications residing directly alongside code in the container's `package_path` (e.g. `src/eidos/core/validator.adoc`).
* **Strict Co-Location Mandate (`INV-COMPONENT-COLOCATION-MANDATE`)**: Dedicated component manifests must never be placed under `eidos/architecture/`. Centralizing components is forbidden to guarantee maximum code-spec locality and prevent cognitive clutter in macro-architecture.
* **Interface Separation (`INV-INTERFACE-BOUNDARY-GOVERNANCE`)**: Containers own Boundary Protocol Contracts (`CTR-*`), while components declare local in-process APIs (`interfaces.provides`).
* **CLI Automation**: Promoted and inlined seamlessly using `eidos component promote <ID>` and `eidos component inline <ID>`.



