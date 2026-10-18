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

All living specifications are authored in **AsciiDoc (`.adoc`)** with structured **YAML frontmatter**. This allows them to be human-readable, Git-mergeable, and programmatically validated against canonical JSON/YAML schemas (`assets/schemas/v1/`).

```text
eidos/
├── system.adoc                   # Root system descriptor (SYS-*, C4 Level 1)
├── architecture/                 # Technical architecture (Arc42 + C4)
│   ├── system-architecture.adoc  # Global architecture & C4 Container topology (ARCH-*, L1 & L2)
│   ├── containers/               # Container manifests & inline components (CNT-*, C4 Level 2 & L3)
│   ├── components/               # Promoted standalone component manifests (CMP-*, C4 Level 3)
│   └── contracts/                # Interface protocol contracts (CTR-*: OpenAPI, gRPC, CLI)
├── domains/                      # DDD bounded contexts & capability trees
│   └── <domain-name>/
│       ├── domain.adoc           # Bounded context manifest (DOM-*)
│       └── capabilities/         # Product capabilities & invariants (CAP-*, INV-*)
├── personas/                     # Human & AI actor roles (PER-*)
├── policies/                     # Inviolable system governance policies (POL-*)
├── explorations/                 # Spikes, hypotheses & feasibility discovery (EXP-*)
├── design/                       # Multi-Design Systems, Client Surfaces & UI contracts
│   ├── systems/<system>/         # Design systems (DS-*), W3C DTCG tokens & UI components (UIC-*)
│   ├── clients/<client>/         # Client apps (CLT-*), client screens (SCR-*) & flows (FLOW-*)
│   └── flows/                    # Omnichannel cross-client interaction flows (FLOW-*)
└── deltas/                       # SpecDelta mutations (DLT-*)
```

---

## The Five Living Specification Layers

Eidos models software across five interconnected layers across **16 canonical document schemas**:

1. **Product & Governance Layer**: Defines strategic archetypes ([`SYS-*`](schema-glossary.md#productsystem)), bounded contexts ([`DOM-*`](schema-glossary.md#productdomain)), capabilities ([`CAP-*`](schema-glossary.md#productcapability)), actor personas ([`PER-*`](schema-glossary.md#productpersona)), and non-negotiable policies ([`POL-*`](schema-glossary.md#productpolicy)).
2. **Exploration & Discovery Layer**: Formalizes hypothesis-driven technical spikes and prototypes ([`EXP-*`](explorations.md)) before committing to architectural baselines.
3. **Technical Architecture Layer**: Codifies Arc42 system context ([`ARCH-*`](schema-glossary.md#techarchitecture)), C4 containers ([`CNT-*`](schema-glossary.md#techcontainer)), C4 components ([`CMP-*`](schema-glossary.md#techcomponent)), protocol contracts ([`CTR-*`](schema-glossary.md#techcontract)), and embedded design decisions (`DEC-*`) without ADRs.
4. **Experience & Design Layer**: Governs multi-brand themes ([`DS-*`](design.md)), official W3C Design Tokens, UI component contracts ([`UIC-*`](design.md#4-ui-component-manifests-uic-)), client surfaces ([`CLT-*`](design.md#6-client-applications-clt-)), screen state machines ([`SCR-*`](design.md#5-screen-state-machines-scr-)), and user flows ([`FLOW-*`](design.md#7-interaction-flows-flow-)).
5. **Evolution & Mutation Layer**: Orchestrates planned architectural changes through auditable SpecDeltas ([`DLT-*`](specdeltas.md)).

---

## How Eidos Replaces ADRs: Embedded Decisions (`DEC-*`)

Instead of scattering chronological `ADR-001.md` files into a separate folder where they quickly go stale, Eidos records durable design trade-offs **in-place** inside the `decisions:` frontmatter block of the owning specification (`ARCH-*`, `CNT-*`, `CMP-*`, `CTR-*`, or `CAP-*`).

Validated via the shared `$defs/decision` schema (`assets/schemas/v1/common/defs.yaml`), every `DEC-*` entry captures:
* **`id`**: Globally unique decision identifier (`DEC-<NAME>`).
* **`title` & `decision`**: Concise summary and normative architectural choice.
* **`rationale`**: Why this design was chosen.
* **`rejected_alternatives`**: Options that were evaluated and rejected (with `option` and `reason_rejected`), preserving negative architectural knowledge so future engineers and AI agents never re-introduce rejected designs.
* **`graduated_from`**: Optional link to the Exploration (`EXP-*`) or SpecDelta (`DLT-*`) that validated the decision.

```yaml
decisions:
  - id: DEC-INDEX-SQLITE-WAL
    title: "Use Embedded SQLite WAL + FTS5 Instead of External Vector DB"
    decision: "Store the architectural knowledge graph and lexical index in local SQLite3 with WAL mode and FTS5 BM25."
    rationale: "Guarantees zero external runtime dependencies (Python stdlib), sub-2ms local queries, and 100% deterministic offline operation under POL-004."
    rejected_alternatives:
      - option: "ChromaDB / LanceDB Vector Store"
        reason_rejected: "Requires embedding model weights or external API calls, violating POL-004 and bloating installation size."
    graduated_from: EXP-2026-002
```

When an AI agent runs `eidos context`, Eidos automatically includes compact 1-line `DEC-*` summaries in the `anchor` tier and the full `rationale` + `rejected_alternatives` in the `guardrails` and `deep` tiers.

---

## Technical Explorations (`EXP-*`): Hypothesis-Driven Discovery

Before committing to a new database, protocol, or framework in a living specification, teams frequently encounter technical uncertainty. Traditional teams run informal "spikes" that either produce throwaway code with zero institutional memory or result in unreviewed prototype code merging into production.

Eidos solves this by treating spikes as **Explorations (`EXP-*`)**:

* **Hypothesis & Exit Criteria**: Every exploration declares an unambiguous hypothesis, specific questions to answer, benchmark targets, and measurable exit criteria (`max_duration`, `benchmark`).
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
domain_binding: DOM-SPEC-ENGINE
components:
  - id: CMP-SPEC-PARSER
    name: SpecParser
    responsibility: "Extracts YAML frontmatter metadata and body text from AsciiDoc documents."
    source_path: "src/eidos/core/spec/parser.py"
contracts:
  provides:
    - "CTR-EIDOS-CLI"
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
* **`id`**: Globally unique document identifier (`SYS-*`, `DOM-*`, `CAP-*`, `POL-*`, `PER-*`, `EXP-*`, `ARCH-*`, `CNT-*`, `CMP-*`, `CTR-*`, `DS-*`, `UIC-*`, `SCR-*`, `CLT-*`, `FLOW-*`, `DLT-*`).
* **`schema`**: Reference to the canonical schema file ensuring automated validation and `x-eidos-ref` link checking.
* **Frontmatter Metadata**: Strongly-typed architectural properties (contracts, dependencies, invariants, decisions, tokens).
* **Body text**: Rich AsciiDoc documentation that evolves continuously with the code.

---

## C4 Model Hierarchy & Centralized Hybrid Components (`CMP-*`)

Eidos enforces a strict, first-principles mapping to the canonical **C4 Architecture Model**:

| C4 Level | C4 Term | Eidos Specification Entity | Architectural Boundary |
| :--- | :--- | :--- | :--- |
| **Level 1** | **System Context** | Root System Spec (`SYS-*`) & Architecture (`ARCH-*`) | System boundaries, human/AI personas (`PER-*`), and external systems |
| **Level 2** | **Container** | Container Manifests (`CNT-*`, `type: tech/container`) | **Process / Deployable / Package boundary** (CLI app, service, library, SPA, database) |
| **Level 3** | **Component** | In-Process Modules (`CMP-*`, inline in `CNT-*.adoc` or promoted in `eidos/architecture/components/*.adoc`) | **In-Process / Memory boundary** (internal modules, engines, services, validators) |
| **Level 4** | **Code** | Source Code & AST Symbols (`package_path`, `source_path`) | Classes, functions, methods, and signatures indexed in `eidos/.cache/index.db` |

### Centralized Hybrid C4 Level 3 Architecture
* **80% Inline**: Lightweight internal modules remain inline inside Level 2 container manifests (`eidos/architecture/containers/*.adoc`), keeping macro-architecture clean and saving 87–94% of context tokens.
* **20% Centralized Promoted Specs**: Complex subsystems with dedicated state, invariants (`INV-*`), or rich in-process interfaces receive standalone `type: tech/component` specifications residing centrally in `eidos/architecture/components/<component>.adoc` (linking to implementation files via `source_path` and referenced in the parent container manifest via `ref:`).
* **Strict Centralization Mandate (`INV-COMPONENT-CENTRALIZATION-MANDATE`)**: Dedicated component manifests must always reside centrally under `eidos/architecture/components/`. Specifications must never touch or reside within source code directories (`src/`, `packages/`) so implementation trees remain 100% pure.
* **Interface Boundary Governance (`INV-INTERFACE-BOUNDARY-GOVERNANCE`)**: Containers (`CNT-*`) own cross-process Boundary Protocol Contracts (`CTR-*`), while components (`CMP-*`) declare local in-process language signatures (`interfaces.provides`).
* **CLI Automation**: Promote or inline components with a single command using `eidos spec component promote <ID>` and `eidos spec component inline <ID>`.



