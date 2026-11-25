---
name: eidos
description: "Core workflow for repositories governed by Eidos living specs (eidos/). Load before editing code, tests, or specs, refactoring, adding features, or reviewing changes. Covers discovery with eidos context/index, choosing Direct vs. Planned mode, and the eidos drift completion gate."
---
# Eidos: Master Architecture & Engineering Orchestrator

This repository uses **Eidos** to keep living specifications (`eidos/`), source code, and tests synchronized. Follow these guardrails whenever inspecting, planning, implementing, refactoring, or reviewing changes.

---

## 1. Constitutional Guardrails (Non-Negotiable)

1. **Single Source of Truth**: Domains, capabilities, personas, C4 containers/components, contracts, design systems, and policies live in `eidos/` as `.adoc` files with YAML frontmatter (§6).
2. **Zero ADRs**: NEVER create standalone ADRs (`docs/adr/`, `eidos/adr/`, `ADR-*.md`). Record rationale and rejected alternatives in the owning spec's `decisions:` block (`CNT-*`, `CMP-*`, `CTR-*`, `CAP-*`), and govern architectural changes via SpecDeltas (`eidos/deltas/dlt-*.adoc`).
3. **Dual Workflow Mode**: Select Direct or Planned Mode *before* writing code (§3).
4. **Invariant-Test Traceability**: Every `INV-*` declares `test_binding: path/to/test_file::test_function`, and the bound test references its `INV-*` ID.
5. **CLI-First Discovery & Scaffolding**: Run Eidos discovery commands (§2, `--json`) *before* raw reads or `grep`/`find`. Never hand-write `.adoc` specs or SpecDeltas from scratch: scaffold with `eidos spec create <type> <ID> --title "..."` or `eidos delta create --title "..." --slug --kind <kind>`, and inspect exact keys with `eidos spec schema <type> [--json]`.
6. **Subagent Context Propagation**: If your harness supports subagents, pass the relevant `eidos context --json` slice in subagent prompts and instruct them to trust it without re-reading summarized `.adoc` files.
7. **Zero Unreconciled Drift**: Never finish with failing schemas or drift (§5, Stage 1).
8. **Mandatory Post-Implementation Adversarial Review**: Never finish without running `git diff` and an adversarial falsification review, fixing every finding before responding (§5, Stage 2).

---

## 2. Step 1: Discovery-First Context & Code Graph Intelligence (`eidos context` & `eidos index`)

**STOP before running blind `grep`, `find`, or reading random files.** Eidos maintains an embedded SQLite + AST knowledge graph (`eidos/.cache/index.db`) that resolves files, symbols, specs, and dependencies in milliseconds.

### 2.1 Mandatory Tool Replacement Matrix

Whenever you need to orient in the codebase, locate a symbol, search specs, or assess impact, **prefer the Eidos CLI command first**—even when the user attaches a `@[file]` path (call `eidos context -f <attached_file> --json` alongside or before reading it):

| Instead of doing this... | ALWAYS run this Eidos command FIRST | Why |
| :--- | :--- | :--- |
| Running `find` / `ls -R` or reading many files to orient | `eidos index repo-map --max-tokens 1500` | AST outline of all containers and modules (~85% fewer tokens). |
| Grepping source files for a class, function, method, or route | `eidos index find-symbol "<SymbolName>" --json` | Exact `location.file` and `line_start`..`line_end` (pass to `read_file` instead of reading full files), plus `CNT-*` / `CMP-*`. |
| Grepping `eidos/` for domain concepts, rules, capabilities, or spikes | `eidos index search "<keywords>" --json` | BM25 + synonym search (optional hybrid semantic via `--semantic`) over specs, `DEC-*`, and `INV-*`. |
| Editing or reviewing files/globs (including `@[file]`) without knowing governance | `eidos context -f "<path_or_glob>" --json` *(or `-f a,b` / `-t "<task>"` / `-c <ID>`)* | Owning `CNT-*`/`CMP-*`, `DOM-*`, `CAP-*`, `PER-*`, `POL-*`, `design`, `INV-*`, `DEC-*`, `CTR-*`, and `provenance_tags` in one call (always quote globs; trust without re-reading `.adoc` files). |
| Guessing what breaks when modifying an entity, symbol, or file | `eidos index blast-radius <SYMBOL_OR_PATH> --depth 2 --min-confidence derived --json` | Upstream/downstream k-hop dependents filtered by epistemic certainty (`observed`, `derived`, or `inferred` default). |

### 2.2 Context Slicing Tiers (`eidos context`)

Choose the `--tier` flag matching your task scope:

| Tier | Flag | Budget | Contents Included | When to Use |
| :--- | :--- | :--- | :--- | :--- |
| **Anchor** | `--tier anchor` | `~140 tokens` | Owning `CNT-*` / `CMP-*`, runtime, `source_path`, bound domain, compact `DEC-*`, direct `CTR-*` signatures. | Quick single-file bug fixes, minor localized edits. |
| **Guardrails** *(Default)* | `--tier guardrails` | `~300–900 tokens` | Anchor + `DEC-*` rationale, `domain_path`, `design` (`CLT-*`/`DS-*`), capabilities (`CAP-*` with `personas`, `policies`, `experience`), active `INV-*`, and `POL-*`. | Standard feature work, refactoring, test updates. |
| **Deep** | `--tier deep` | `~500–1,500 tokens` | Guardrails + C4 Level 3 building blocks, full contract schemas, full policies, and downstream dependents. | Cross-container refactors, SpecDelta authoring, architectural reviews. |

```bash
eidos index repo-map --max-tokens 1500
eidos index find-symbol "JwtValidator" --json
eidos index search "authentication" --json
eidos context -f src/auth/jwt_validator.py --json
eidos context -f "apps/*/Dockerfile" --tier guardrails --json
eidos context -c CNT-AUTH-SERVICE --tier guardrails --json
eidos context -t "rate limiting and token expiration" --tier deep --json
eidos index blast-radius CNT-AUTH-SERVICE --depth 2 --json
```

---

## 3. Step 2: Select Your Workflow Mode

Using the ownership and dependency insights from Step 1 (`eidos context` and `eidos index blast-radius`), classify the user's request *before* writing code:

| Criteria | ⚡ Direct Mode (In-Place Co-Evolution) | 📐 Planned Mode (SpecDelta Lifecycle) |
| :--- | :--- | :--- |
| **Typical Tasks** | Bug fixes, localized refactors, adding a helper/flag within an existing container, doc/test updates, UI polish. | New/removed containers (`CNT-*`) or components (`CMP-*`), breaking contract (`CTR-*`) changes, cross-domain features, new policies (`POL-*`). |
| **Blast Radius** | Confined to 1 existing container/component (`eidos index blast-radius`); no breaking contract changes. | Spans multiple containers/domains or alters public interfaces/schemas. |
| **Spec Mechanism** | Update `.adoc` specs in `eidos/` on structural changes (new/renamed/removed public symbols, files, `INV-*`, or `DEC-*`; skip on body-only fixes). | Create a SpecDelta (`eidos delta create`), implement code + spec changes, verify (`eidos delta verify`), and reconcile (`eidos delta apply`). |
| **Verification** | `eidos drift --uncommitted --json` | `eidos delta verify <ID>`, `eidos delta apply <ID>`, `eidos drift --uncommitted --json` |

---

## 4. Step 3: Execute the Selected Workflow

### Workflow A: Direct Mode Execution (`eidos drift`)

1. **Slice Context & Locate Symbols**: Run `eidos context -f "<touched_file_or_glob>" --json` (plus `find-symbol` / `search` as needed) to resolve owning `CNT-*`/`CMP-*` and `INV-*`.
2. **Implement Code & Tests**: Modify source code and update or add automated tests preserving `INV-*` references.
3. **Co-Evolve Living Specs on Structural Changes** (skip on body-only fixes where no public symbol, file, contract, `INV-*`, or `DEC-*` changed; consult **`eidos-spec`** or `eidos spec schema <type> [--json]`):
   * **New internal module or file**: Update `source_path` in the owning `CNT-*` / `CMP-*` spec (or scaffold via `eidos spec create component <ID> --title "..."`).
   * **New package/contract boundary export** (`__init__.py`, `index.ts`, `mod.rs`, `lib.rs`, `__all__`, or `specification_file`): Anchor the export or route in the owning/neighbor spec (`CNT-*`, `CMP-*`, `CTR-*`, or `CAP-*`).
   * **Removed or renamed public symbol**: Remove or rename backticked symbol references in the owning `CNT-*`, `CMP-*` (`== Public Surface`), or `CTR-*` spec body (`stale_removed_symbol`).
   * **Module-internal helper function**: Covered by a substantive update to the owning `CNT-*` / `CMP-*` spec—never pollute C4 specs with helper names (`trivially_covered_files`).
   * **New invariant or design trade-off**: Add `INV-*` (`test_binding: file::func`) to `CAP-*` (`technical.invariants`) or top-level `invariants:` (`POL-*`/`CNT-*`/`CMP-*`), or add `DEC-*` under `decisions:`.
   * **Domain vocabulary, persona, policy, or UI/design change**: Co-evolve bound `DOM-*` (`ubiquitous_language`), `PER-*`, `POL-*`, or `design/*` (`DS-*`, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`, `tokens.json`) specs surfaced by `eidos context`.
4. **Verify Zero Drift**: Run `eidos drift --uncommitted --json` (`--strict` also requires 1-hop spec or bound test co-evolution on body-only edits).

### Workflow B: Planned Mode Execution (Load `eidos-plan`)

When introducing/removing containers or components, making breaking contract changes, or spanning domains, load the **`eidos-plan`** skill:

1. **Discover Scope & Blast Radius**:
   ```bash
   eidos context -t "<feature or architectural change>" --tier deep --json
   eidos index blast-radius <TARGET-ENTITY-ID> --depth 2 --json
   ```
2. **Scaffold SpecDelta** (auto-generates ID, slug, and 4-hex suffix; commands accept short prefixes like `DLT-YYYY-NNN`):
   ```bash
   eidos delta create --title "..." --kind <feature|enabler|bug>
   ```
3. **Declare Mutations, Reconcile Plan & Self-Review**: Populate `mutations:` (`operation`, `target`, `expect: { body, frontmatter }`; `eidos spec schema delta`—never `affects:`/`blast_radius:`) and `== Problem Statement` / `== Proposed Specification Changes` / `== Implementation Plan`. Slice every planned file or **quoted** glob (`eidos context -f "<path_or_glob>" --tier guardrails --json` or `-f a,b`) to declare 1-hop owners (`CMP-*`, parent `CNT-*`, `CTR-*`, `CAP-*`) + `design/*` (`SYS-*`/`ARCH-*` cover only unmapped files), annotate unchanged parent/non-structural files inline (`(covered via parent CNT-*)` / `(body-only; no spec mutation)`) to avoid `UNMODIFIED`, pair tests with `INV-*` (`expect.frontmatter: { rule, test_binding }`), verify live CLI flags/paths/symlinks, and run `eidos-plan` §3's 5-Dimension Review autonomously before presenting.
4. **Implement Code, Specs & Tests**: Apply changes across code, `eidos/`, and `tests/`.
5. **Verify & Apply**:
   ```bash
   eidos delta verify <DLT-ID> --json
   eidos delta apply <DLT-ID>
   ```

---

## 5. Step 4: Pre-Completion Gate & Adversarial Gap Self-Review

Before concluding **any** task or presenting your final response, complete both verification stages:

### Stage 1: Deterministic Engine Gate (`eidos drift`)

```bash
eidos drift --uncommitted --json
```

* **`eidos drift`** runs `eidos validate` internally (v1 schemas, `x-eidos-ref`, `source_path`/`specification_file`, `import_boundaries`, `== Public Surface`, and **bidirectional contract parity** across all canonical `tech/contract` protocols) plus Git diff ownership, workflow mode, substantive diff, `stale_removed_symbol`, `--strict` body drift, and polyglot `INV-*` `test_binding` checks. (Use `eidos validate eidos --json` outside Git.)
* **If `is_clean: false` or `drift_detected: true`**: Follow `remediation_suggestions`, `boundary_violations` (remove forbidden imports/HTTP calls or adjust `ratchet_baseline` via SpecDelta), and `validation_errors` until exit code is `0` (`ratchet_shrink_candidates` are non-blocking hints).

### Stage 2: Mandatory Post-Implementation Adversarial Diff Review

**Before responding**, inspect your actual diff adversarially—never rely on memory:

```bash
git diff
```

If subagents are available on multi-file edits, you may delegate this read-only audit of `git diff` + `eidos context --json` to a fresh-context reviewer; **otherwise execute every check directly**:

1. **Requirements Completeness**: Verify every explicit and implicit requirement is met with zero `TODO`s, stubs, or partial paths.
2. **Adversarial Code & Heuristic Falsification**: Actively try to break at least 2 assumptions in your diff:
   * *Parsing vs. substring heuristics*: Could regex/substring checks misfire on comments, prose, case variants, or dot-directories (`.github/`, `.agents/`) where a structured parser or canonical helper is safer?
   * *Sibling callers & shared state*: Inspect other readers/writers of any touched helper, schema, or table (`eidos index find-symbol`) for unintended side effects.
   * *Scale, boundaries & fallbacks*: Check empty/`None`/unmatched inputs, SQLite/batch limits (`IN (...)` chunking), parent↔child inheritance, and single- vs. multi-container edge cases.
3. **Contract, Public Surface & SemVer Parity (`CTR-*`, `CMP-*`)**: Sync added/removed CLI flags, routes, schemas, and `== Public Surface` symbols with SemVer bumps.
4. **Invariant & Test Rigor (`INV-*`)**: Ensure tests assert real behavior and edge cases (hermetic fixtures for ordering/logic; catalog-agnostic smoke checks on live specs) and bind every `INV-*` (`test_binding: file::func`).
5. **Decisions & Multi-Layer Co-Evolution (`DEC-*`, `DOM-*`, `PER-*`, `POL-*`, `design/*`)**: Record non-obvious trade-offs under `decisions:` and co-evolve any affected domain terms, personas, policies, or UI/design specs.

**Autonomous Remediation Rule**: Fix every defect, edge case, or missing test immediately, then re-run tests and `eidos drift --uncommitted --json` before responding. Report gate status (`clean`) and key verified edge cases.

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
├── memory/                              # Shared Team Operational Memories (MEM-*)
├── explorations/                        # Technical Spikes & Feasibility Discovery (EXP-*)
└── deltas/                              # SpecDelta Mutations (DLT-*)
```

Load the companion skill matching your task:
* **`eidos-spec`**: YAML schemas and scaffolding (`eidos spec create/schema/list/show/delete`) for `DOM-*`, `CAP-*`, `PER-*`, `SYS-*`, `ARCH-*`, `CNT-*`, `CMP-*`, `CTR-*`, `DEC-*`, `DS-*`, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`, `POL-*`, and polyglot `INV-*` test bindings.
* **`eidos-plan`**: Technical spikes (`EXP-*`), SpecDelta lifecycle (`eidos delta create/verify/audit/apply/changelog`), declarative `expect:` post-conditions, and 5-dimension Staff Architectural Reviews.
* **`eidos-ops`**: Greenfield initialization (`eidos init`), 8-phase brownfield codebase extraction, and Living Web Portal operations (`eidos portal serve/build`).
* **`eidos-memory`**: Shared team operational memories (`eidos/memory/mem-*.adoc`) — proactive capture, disclosure, freshness reconciliation, and promotion (`eidos memory add/list/search/show/verify/archive/audit/promote`).
