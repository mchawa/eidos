---
name: eidos
description: Master architecture and engineering orchestrator for repositories managed by Eidos. Consult before modifying code, proposing architectural changes, adding features, refactoring, or altering living specifications in eidos/. Strictly enforces living specs (NO ADRs), dual workflows (Direct vs. Planned), Discovery-First CLI intelligence (eidos context, eidos index repo-map, eidos index find-symbol, eidos index search, eidos index blast-radius), continuous drift detection (eidos drift), invariant-test traceability, and mandatory autonomous gap self-review.
---
# Eidos: Master Architecture & Engineering Orchestrator

This repository uses **Eidos** to maintain a zero-drift bond between living specifications (`eidos/`), source code, and automated tests.

You MUST follow these workflows and guardrails whenever you inspect, plan, implement, refactor, or review changes in this repository.

---

## 1. Constitutional Guardrails (Non-Negotiable)

1. **Single Source of Truth (`POL-001`)**:
   All product domains, capabilities, personas, C4 architecture containers/components, interface contracts, design systems, and policies live inside `eidos/` as structured AsciiDoc (`.adoc`) files with YAML frontmatter.
2. **Zero ADRs (`POL-001`)**:
   NEVER create standalone Architecture Decision Records (`docs/adr/`, `eidos/adr/`, or `ADR-*.md`). Architectural state lives in `eidos/architecture/`, durable design rationale and rejected alternatives are recorded inside the `decisions:` frontmatter block of the owning specification (`CNT-*`, `CMP-*`, `CTR-*`, `CAP-*`), and architectural transitions are governed by **SpecDeltas** (`eidos/deltas/dlt-*.adoc`).
3. **Dual Workflow Mode Selection (`POL-003`)**:
   Every task operates in either **Direct Mode** (fast, in-place co-evolution) or **Planned Mode** (formal SpecDelta lifecycle). Select the mode *before* writing code.
4. **Mandatory Invariant-Test Traceability (`POL-002`)**:
   Every capability invariant (`INV-*`) must declare a `test_binding` (`path/to/test_file::test_function`), and the bound test function must explicitly reference the `INV-*` ID in its docstring, comment, or marker.
5. **CLI-First Discovery & Determinism (`POL-004`)**:
   Always run Eidos knowledge graph and context CLI commands (`eidos context`, `eidos index repo-map`, `eidos index find-symbol`, `eidos index search`, `eidos index blast-radius` with `--json`) *before* falling back to raw file reads or `grep`/`find`. Never create `.adoc` specification or SpecDelta files by hand from scratch—always scaffold via `eidos spec create <type> <ID> --title "..."` or `eidos delta create --title "..." --slug --kind <kind>`, and inspect exact required/optional keys and closed sub-schemas via `eidos spec schema <type> [--json]` before adding nested frontmatter blocks.
6. **Zero Unreconciled Drift**:
   Never conclude a task with failing specification schemas or unmanaged code-to-spec drift.
7. **Mandatory Autonomous Pre-Completion Review**:
   Never present work or conclude a task without executing an autonomous adversarial review pass over your diffs and requirements. Identify and resolve edge cases, missing tests, spec drift, and gaps *before* responding.

---

## 2. Step 1: Discovery-First Context & Code Graph Intelligence (`eidos context` & `eidos index`)

**STOP before running blind `grep`, `find`, or reading random files.** Eidos maintains an embedded SQLite + AST knowledge graph (`eidos/.cache/index.db`) that resolves files, symbols, specs, and dependencies in milliseconds at a fraction of the token cost.

### 2.1 Mandatory Tool Replacement Matrix

Whenever you need to orient in the codebase, locate a symbol, search specifications, or assess impact, **prefer the Eidos CLI command first**—even when the user prompt already attaches a `@[file]` path:

| Instead of doing this... | ALWAYS run this Eidos command FIRST | Why |
| :--- | :--- | :--- |
| Running `find` / `ls -R` or reading many files to orient in the repository | `eidos index repo-map --max-tokens 1500` | AST-compressed structural outline (`>= 85%` token reduction) across all containers and modules. |
| Grepping across source files for a class, function, method, or route | `eidos index find-symbol "<SymbolName>" --json` | Resolves exact `file:line` coordinates, syntax kind, and the owning `CNT-*` / `CMP-*` spec. |
| Grepping `eidos/` for domain concepts, rules, capabilities, or prior spikes | `eidos index search "<keywords>" --json` | BM25-ranked FTS5 search across all living spec titles, bodies, decisions (`DEC-*`), and invariants (`INV-*`). |
| Editing or reviewing a file (including user-attached `@[file]` paths) without knowing its governance rules | `eidos context -f <path/to/file> --json` *(or `-t "<task>"` / `-c <ID>`)* | Extracts the owning `CNT-*`/`CMP-*`, active `INV-*` test bindings, `DEC-*` rationale, and `CTR-*` contracts in one call. |
| Guessing what breaks when modifying a container, component, contract, or capability | `eidos index blast-radius <ENTITY-ID> --depth 2 --json` | Recursive CTE k-hop graph walk revealing upstream/downstream dependents across domains and containers. |

### 2.2 Context Slicing Tiers (`eidos context`)

Choose the `--tier` flag matching your task scope:

| Tier | Flag | Budget | Contents Included | When to Use |
| :--- | :--- | :--- | :--- | :--- |
| **Anchor** | `--tier anchor` | `~140 tokens` | Owning `CNT-*` / `CMP-*`, runtime, `source_path`, bound domain, compact `DEC-*`, direct `CTR-*` signatures. | Quick single-file bug fixes, minor localized edits. |
| **Guardrails** *(Default)* | `--tier guardrails` | `~300–900 tokens` | Anchor + `DEC-*` rationale/alternatives, domain capabilities (`CAP-*`), active invariants (`INV-*`) with `test_binding`, and policies (`POL-*`). | Standard feature work, refactoring, test updates. |
| **Deep** | `--tier deep` | `~500–1,500 tokens` | Guardrails + C4 Level 3 internal building blocks, full contract schemas, full policies, and downstream dependents. | Cross-container refactors, SpecDelta authoring, architectural reviews. |

```bash
# 1. Orient in the codebase with a token-budgeted AST structural repo map
eidos index repo-map --max-tokens 1500

# 2. Locate exact file:line coordinates and owning C4 component for a code symbol
eidos index find-symbol "JwtValidator" --json

# 3. Full-text BM25 search across all specifications, capabilities, decisions, and invariants
eidos index search "authentication" --json

# 4. Slice architectural context for a source file, component ID, or natural-language task
eidos context -f src/auth/jwt_validator.py --json
eidos context -c CNT-AUTH-SERVICE --tier guardrails --json
eidos context -t "rate limiting and token expiration" --tier deep --json

# 5. Compute transitive k-hop dependency and governance blast radius before modifying an entity
eidos index blast-radius CNT-AUTH-SERVICE --depth 2 --json
```

---

## 3. Step 2: Select Your Workflow Mode

Using the ownership and dependency insights from Step 1 (`eidos context` and `eidos index blast-radius`), classify the user's request *before* writing code:

| Criteria | ⚡ Direct Mode (In-Place Co-Evolution) | 📐 Planned Mode (SpecDelta Lifecycle) |
| :--- | :--- | :--- |
| **Typical Tasks** | Bug fixes, localized refactors, adding a helper/flag within an existing container, doc/test updates, UI polish. | New/removed containers (`CNT-*`) or components (`CMP-*`), breaking contract (`CTR-*`) changes, cross-domain features, new policies (`POL-*`). |
| **Blast Radius** | Confined to 1 existing container/component (`eidos index blast-radius`); no breaking contract changes. | Spans multiple containers/domains or alters public interfaces/schemas. |
| **Spec Mechanism** | Update affected `.adoc` specs in `eidos/` directly alongside your code and test edits. | Create a SpecDelta (`eidos delta create`), implement code + spec changes, verify (`eidos delta verify`), and reconcile (`eidos delta apply`). |
| **Verification** | `eidos drift --uncommitted --json` | `eidos delta verify <ID>`, `eidos delta apply <ID>`, `eidos drift --uncommitted --json` |

---

## 4. Step 3: Execute the Selected Workflow

### Workflow A: Direct Mode Execution (`eidos drift`)

1. **Slice Context & Locate Symbols**: Run `eidos context -f <touched_file> --json` (and `eidos index find-symbol <symbol> --json` or `eidos index search "<keywords>" --json` as needed) to identify the owning container (`CNT-*`), component (`CMP-*`), and bound invariants (`INV-*`).
2. **Implement Code & Tests**: Modify source code and update or add automated tests preserving `INV-*` references.
3. **Co-Evolve Living Specs In-Place** (consult **`eidos-spec`** or run `eidos spec schema <type> [--json]` for exact YAML schemas):
   * **New internal module or file**: Update `source_path` or `components:` in the owning `CNT-*` / `CMP-*` spec.
   * **New package/contract boundary export** (e.g., `__init__.py`, `index.ts`, `mod.rs`, `lib.rs`, `__all__`, or contract `specification_file`): Anchor the new boundary symbol or route in the owning/neighbor spec (`CNT-*`, `CMP-*`, `CTR-*`, or `CAP-*`).
   * **Module-internal helper function**: Covered by a substantive (non-whitespace, non-comment) update to the owning `CNT-*` / `CMP-*` spec—do NOT pollute high-level C4 specs with low-level helper names. Whitespace/comment-only spec edits are rejected (`trivially_covered_files`).
   * **New invariant or design trade-off**: Add `INV-*` (`test_binding: file::func`) to `CAP-*` (`technical.invariants`) or top-level `invariants:` (`POL-*`/`CNT-*`/`CMP-*`), or record a `DEC-*` entry under `decisions:` in the owning spec.
4. **Verify Zero Drift**: Run `eidos drift --uncommitted --json`.

### Workflow B: Planned Mode Execution (Load `eidos-plan`)

When introducing/removing containers or components, making breaking contract changes, or spanning domains, load the **`eidos-plan`** skill:

1. **Discover Scope & Blast Radius**:
   ```bash
   eidos context -t "<feature or architectural change>" --tier deep --json
   eidos index blast-radius <TARGET-ENTITY-ID> --depth 2 --json
   ```
2. **Scaffold SpecDelta**:
   ```bash
   eidos delta create --title "..." --slug --kind <feature|enabler|bug>
   ```
3. **Declare Mutations & Implementation Plan**: Populate `mutations:` (with composable `operation`, `target`, and structured `expect: { body, frontmatter }` post-conditions; run `eidos spec schema delta` to inspect allowed keys) and fill out the `== Problem Statement`, `== Proposed Specification Changes`, and `== Implementation Plan` prose sections in the created `eidos/deltas/dlt-*.adoc` file. Never add manual `affects:` or `blast_radius:` blocks.
4. **Implement Code, Specs & Tests**: Apply changes across code, `eidos/`, and `tests/`.
5. **Verify & Apply**:
   ```bash
   eidos delta verify <DLT-ID> --json
   eidos delta apply <DLT-ID>
   ```

---

## 5. Step 4: Pre-Completion Gate & Adversarial Gap Self-Review

Before concluding **any** task or presenting your final response, complete both verification stages:

### Stage 1: Deterministic Engine Gate (`eidos drift` & `eidos validate`)

```bash
eidos validate eidos --json
eidos drift --uncommitted --json
```

* **`eidos validate`** checks v1 JSON Schemas, `x-eidos-ref` cross-references, `source_path`/`specification_file` paths on disk, and **bidirectional multi-protocol contract parity** (`cli_args`, `openapi`, `protobuf`, `graphql`, `python_protocol`).
* **`eidos drift`** runs `eidos validate` internally plus Git diff ownership mapping, workflow mode verification (Direct vs. Planned `mutations[].target`), substantive spec diff checks, and polyglot `INV-*` `test_binding` verification.
* **If `is_clean: false` or `drift_detected: true`**: Follow the `remediation_suggestions` array in the JSON output and re-run until exit code is `0`.

### Stage 2: Mandatory Autonomous Adversarial Gap Review

Step back and audit your own `git diff` as an adversarial Staff Reviewer before responding:

1. **Requirements Completeness**: Did you address every stated requirement and implicit constraint without leaving `TODO`s, stubs, or partial implementations?
2. **Edge Cases & Failure Modes**: How does the modified code handle empty inputs, `None`/`null`, invalid schemas, missing files, or error paths?
3. **Contract & SemVer Parity (`CTR-*`)**: Did any CLI flags, endpoints, schemas, or public signatures change? Are callers updated, deleted operations removed from `CTR-*` tables, and `version:` bumped per SemVer?
4. **Invariant & Test Rigor (`INV-*`)**: Do tests make genuine behavioral assertions on new/modified logic rather than superficial pass-through checks? Is every `INV-*` bound via `test_binding: file::func`?
5. **Decision Capture (`DEC-*`)**: Are non-obvious trade-offs and rejected alternatives recorded under `decisions:` in the owning spec frontmatter?

**Autonomous Remediation Rule**: If this review reveals **any** gap, missing test assertion, or unhandled edge case, **fix it immediately** and re-run `eidos drift --uncommitted --json` before responding to the user. In your final response, briefly report your gate status (`clean`) and key edge cases verified.

---

## 6. Living Specification Directory Layout & Companion Skills

```text
eidos/
├── system.adoc                          # C4 Level 1 System Context (SYS-*)
├── architecture/
│   ├── system-architecture.adoc         # Arc42 Sections 1-12 & C4 Level 2 Topology (ARCH-*)
│   ├── containers/                      # C4 Level 2 Containers (CNT-*)
│   ├── components/                      # Standalone C4 Level 3 Components (CMP-*)
│   └── contracts/                       # Interface Contracts: OpenAPI, Protobuf, GraphQL, CLI (CTR-*)
├── domains/<domain-slug>/
│   ├── domain.adoc                      # Bounded Context & Ubiquitous Language (DOM-*)
│   └── capabilities/                    # Functional Capabilities, JTBD & Invariants (CAP-*)
├── design/                              # Optional Multi-Design Systems & Client UX
│   ├── systems/<slug>/                  # Design Systems (DS-*), tokens.json & UI Components (UIC-*)
│   ├── clients/<app>/                   # Client Apps (CLT-*) & Screen State Machines (SCR-*)
│   └── flows/                           # User Interaction Flows (FLOW-*)
├── personas/                            # Actor Personas (PER-*)
├── policies/                            # System-wide Governance & NFR Policies (POL-*)
├── explorations/                        # Technical Spikes & Feasibility Discovery (EXP-*)
└── deltas/                              # SpecDelta Mutations (DLT-*)
```

Load the companion skill matching your task:
* **`eidos-spec`**: YAML schemas and scaffolding (`eidos spec create/schema/list/show/delete`) for `DOM-*`, `CAP-*`, `PER-*`, `SYS-*`, `ARCH-*`, `CNT-*`, `CMP-*`, `CTR-*`, `DEC-*`, `DS-*`, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`, `POL-*`, and polyglot `INV-*` test bindings.
* **`eidos-plan`**: Technical spikes (`EXP-*`), SpecDelta lifecycle (`eidos delta create/verify/audit/apply/changelog`), declarative `expect:` post-conditions, and 5-dimension Staff Architectural Reviews.
* **`eidos-ops`**: Greenfield initialization (`eidos init [--lite]`), 8-phase brownfield codebase extraction, and Living Web Portal operations (`eidos portal serve/build`).
