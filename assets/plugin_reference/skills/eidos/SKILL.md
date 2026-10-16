---
name: eidos
description: Master architecture and engineering orchestrator for repositories managed by Eidos. Consult before modifying code, proposing architectural changes, adding features, refactoring, or altering living specifications in eidos/. Strictly enforces living specs (NO ADRs), dual workflows (Direct vs. Planned), tiered context slicing (eidos context), continuous drift detection (eidos drift), invariant-test traceability, and mandatory autonomous gap self-review.
---
# Eidos: Master Architecture & Engineering Orchestrator

This repository uses **Eidos** to maintain a deterministic, zero-drift bond between living specifications (`eidos/`), polyglot source code, and automated tests.

You MUST follow the workflows and guardrails in this skill whenever you inspect, plan, implement, refactor, or review changes in this repository.

---

## 1. Constitutional Guardrails (Non-Negotiable)

1. **Single Source of Truth (`POL-001`)**:
   All product domains, capabilities, personas, C4 architecture containers/components, interface contracts, design systems, and policies live inside `eidos/` as structured AsciiDoc (`.adoc`) files with YAML frontmatter.
2. **Zero ADRs (`POL-001`)**:
   NEVER create standalone Architecture Decision Records (`docs/adr/`, `eidos/adr/`, or `ADR-*.md`). Architectural state is captured in the living specifications (`eidos/architecture/`), durable design rationale and rejected alternatives are recorded directly inside the `decisions:` frontmatter block of the owning specification (`CNT-*`, `CMP-*`, `CTR-*`, `CAP-*`), and architectural transitions are governed by **SpecDeltas** (`eidos/deltas/dlt-*.adoc`).
3. **Dual Workflow Mode Selection (`POL-003`)**:
   Every task operates in either **Direct Mode** (fast, in-place co-evolution) or **Planned Mode** (formal SpecDelta lifecycle). Choose the mode *before* writing code.
4. **Mandatory Invariant-Test Traceability (`POL-002`)**:
   Every capability invariant (`INV-*`) must declare a `test_binding` (`path/to/test_file::test_function`), and the bound test function must explicitly reference the `INV-*` ID in its docstring, comment, or marker.
5. **Deterministic Engine Only (`POL-004`)**:
   Eidos CLI commands are 100% deterministic local operations (`--json` supported). Always use Eidos CLI commands rather than guessing schemas or manually wiring symlinks.
6. **Zero Unreconciled Drift**:
   Never conclude a task with failing specification schemas or unmanaged code-to-spec drift.
7. **Mandatory Autonomous Pre-Completion Review**:
   Never present work or conclude a task without executing an autonomous adversarial review pass over your diffs and requirements. Identify and resolve edge cases, missing tests, spec drift, and gaps *before* responding.

---

## 2. Step 1: Select Your Workflow Mode

Before writing or editing source code, classify the user's request:

| Criteria | ⚡ Direct Mode (In-Place Co-Evolution) | 📐 Planned Mode (SpecDelta Lifecycle) |
| :--- | :--- | :--- |
| **Typical Tasks** | Bug fixes, localized refactors, adding a helper/CLI flag within an existing container, doc/test updates, UI polish. | New containers (`CNT-*`), breaking contract (`CTR-*`) changes, cross-domain features, new policies (`POL-*`), security changes. |
| **Blast Radius** | Confined to 1 existing container/component; no breaking contract changes. | Spans multiple containers/domains or alters public interfaces/schemas. |
| **Spec Mechanism** | Update the affected `.adoc` specs in `eidos/` directly alongside your code and test edits. | Create a SpecDelta (`eidos delta create`), implement code + spec changes, verify (`eidos delta verify`), and reconcile (`eidos delta apply`). |
| **Verification** | `eidos validate eidos && eidos drift --uncommitted` | `eidos delta verify <ID>`, `eidos delta apply <ID>`, `eidos validate eidos && eidos drift --uncommitted` |

---

## 3. Step 2: Slice Architectural Context & Code Graph (`eidos context`)

Never blindly load dozens of files into your context window. Eidos maintains an embedded SQLite + FTS5 + AST knowledge graph (`eidos/.cache/index.db`) that lazily auto-synchronizes in `<5ms` before every query.

### Tiered Context Slicing (`eidos context`)

Extract a compact, token-budgeted subgraph of components, contracts, capabilities, invariants, and policies:

| Tier | Flag | Typical Size | Contents Included | When to Use |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1: Anchor** | `--tier anchor` | `~140 tokens` | Owning `CNT-*` / `CMP-*`, runtime, `package_path`, bound domain, direct `CTR-*` signatures. | Quick single-file bug fixes, minor localized edits. |
| **Tier 2: Guardrails** *(Default)* | `--tier guardrails` | `~300–900 tokens` | Anchor + domain capabilities (`CAP-*`), active invariants (`INV-*`) with `test_binding`, and policies (`POL-*`). | Standard feature work, refactoring, test updates. |
| **Tier 3: Deep** | `--tier deep` | `~500–1,500 tokens` | Guardrails + C4 Level 3 internal building blocks, full contract schemas, and downstream dependents. | Cross-container refactors, SpecDelta authoring, architectural reviews. |

```bash
# Slice context for a specific source file you plan to touch
eidos context -f src/auth/jwt_validator.py --format json

# Slice context for a specific container or component ID
eidos context -c CNT-AUTH-SERVICE --tier guardrails --format json

# Slice context from a natural-language task prompt (BM25 + 1-hop graph expansion)
eidos context -t "rate limiting and token expiration" --tier deep --format json
```

### Code Graph, Repo Map & Blast Radius

```bash
# Generate a token-budgeted structural AST Repo Map (~98% token compression)
eidos index repo-map --max-tokens 1500

# Locate exact file and line coordinates for any class, function, or method
eidos index find-symbol "JwtValidator"

# Full-text BM25 search across all living specifications
eidos index search "authentication" --json

# Compute transitive N-hop dependency and governance blast radius
eidos index blast-radius CNT-AUTH-SERVICE --depth 2 --json

# Inspect knowledge graph health or force a full rebuild
eidos index status --json
eidos index sync --force
```

---

## 4. Step 3: Execute the Selected Workflow

### Workflow A: Direct Mode Execution (`eidos drift`)

Use Direct Mode when your change is localized and does not introduce breaking contract mutations or new C4 containers:

1. **Slice Context**: Run `eidos context -f <touched_file> --format json` to identify the owning container (`CNT-*`), component (`CMP-*`), and bound invariants (`INV-*`).
2. **Implement Code & Tests**: Modify the source code and update or add automated tests preserving `INV-*` references.
3. **Inspect Impacted Specifications**:
   ```bash
   # Map uncommitted code diffs to affected containers, components, and capabilities
   eidos drift --uncommitted --json
   ```
4. **Co-Evolve Living Specs In-Place**:
   * Added or renamed an internal module? Update the `components:` list and C4 Level 3 table in `eidos/architecture/containers/<container>.adoc` (or `eidos/architecture/components/<cmp>.adoc` if promoted).
   * Added a non-breaking CLI flag, endpoint, or config key? Update `eidos/architecture/contracts/<contract>.adoc`.
   * Added a new invariant rule? Add `INV-*` and its `test_binding: file::func` to the capability `.adoc` and reference `INV-*` in the test docstring.
   * Made a non-obvious design trade-off? Record a `DEC-*` entry under `decisions:` in the owning spec's frontmatter.
5. **Verify Zero Drift**: Run `eidos validate eidos && eidos drift --uncommitted`.

### Workflow B: Planned Mode Execution (`eidos-plan`)

Use Planned Mode when introducing new containers (`CNT-*`), breaking contract (`CTR-*`) changes, cross-domain features, or new policies (`POL-*`). Consult the **`eidos-plan`** skill for full details:

1. **Scaffold SpecDelta**: `eidos delta create DLT-YYYY-NNN --title "..." --kind <feature|enabler|bug> --branch <branch>`
2. **Author Declarative Mutations**: Populate `mutations:` (with optional `expect:` post-conditions), `affects:`, and `blast_radius:` in `eidos/deltas/dlt-yyyy-nnn.adoc`.
3. **Implement Code, Specs & Tests**: Apply the architectural and code changes across `src/`, `eidos/`, and `tests/`.
4. **Verify & Apply**:
   ```bash
   eidos delta verify DLT-YYYY-NNN
   eidos delta apply DLT-YYYY-NNN
   ```

---

## 5. Step 4: Autonomous Pre-Completion Gate & Gap Self-Review

Before concluding **any** task or presenting your final response, you MUST complete this two-stage pre-completion verification:

### Stage 1: Deterministic Engine Verification (`eidos validate` & `eidos drift`)

Run the automated specification validation and drift checks:

```bash
eidos validate eidos
eidos drift --uncommitted --json
```

* **`eidos validate`**: Static catalog linter. Validates all specification frontmatters against bundled JSON Schemas, checks `x-eidos-ref` cross-document graph integrity, verifies declared container `package_path` and contract `specification_file` targets on disk, and enforces Typer CLI contract parity.
* **`eidos drift`**: Primary Git-native verification gate. Subsumes `eidos validate` (executes it internally) and adds Git diff classification, AST structural change detection, component ownership mapping, and polyglot invariant test verification.

#### How `eidos drift` Works

1. **Git Diff Classification**: Classifies modified files into `code_files`, `spec_files`, `delta_files`, and `test_files`.
2. **Ownership Mapping**: Maps every modified source file to its owning C4 container (`CNT-*`) via `package_path` and C4 component (`CMP-*`) via `source_path`.
3. **Workflow Mode Evaluation**:
   * **Planned Mode** (when an active `DLT-*` is touched or declared): Verifies that all touched containers/components are accounted for in the SpecDelta's `affects` / `blast_radius`.
   * **Direct Mode** (no active `DLT-*`): Verifies that modified code files are accompanied by corresponding living specification updates (or passes when only tests/non-architectural files are touched).
4. **Specification Catalog Validation**: Runs `SpecValidator` across `eidos/` to ensure schema conformance, valid cross-references, and physical disk binding integrity.
5. **Polyglot Invariant Test Verification**: Scans `tests/` across Python (`.py`), TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`), Go (`_test.go`), Rust (`.rs`), and Java/Kotlin (`.java`, `.kt`) to verify every `INV-*` `test_binding` (`file::func`) exists and references the `INV-*` ID, and detects orphan invariants.

#### Resolving Drift (`drift_detected: true`)

If `eidos drift --uncommitted --json` reports `drift_detected: true` or `is_clean: false`:
* Read the `remediation_suggestions` array in the JSON output.
* Either update the impacted container/component/capability specification in `eidos/` (Direct Mode) or declare the mutation in a SpecDelta in `eidos/deltas/` (Planned Mode).
* Re-run `eidos validate eidos && eidos drift --uncommitted` until exit code is `0`.

### Stage 2: Mandatory Autonomous Adversarial Gap Review

After deterministic checks pass, you MUST step back, assume the mindset of an adversarial Staff Reviewer auditing your own diff (`git diff`), and execute this pre-completion gap audit:

1. **Requirements & Scope Completeness**:
   * Re-read the user's initial prompt and constraints carefully.
   * Did you address every stated requirement and implicit edge condition, or did you stop after the happy path?
   * Are there any half-implemented features, temporary stubs, or leftover `TODO` comments?
2. **Edge Cases, Failure Modes & Resilience**:
   * How does the new/modified code behave with empty inputs, `None`/`null` values, invalid schemas, missing files, or timeout/network failures?
   * Are exceptions handled cleanly with descriptive error messages, or swallowed silently?
3. **Interface Contracts & API Consistency (`CTR-*`)**:
   * Did any function signatures, CLI flags, return schemas, or public APIs change?
   * If yes, are callers across the repository updated, and are interface contracts (`CTR-*`) synchronized with SemVer bumps?
4. **Invariant & Test Assertion Rigor (`INV-*`)**:
   * Did you add or update automated tests for the changes?
   * Do the tests make genuine assertions on behavior and edge cases, or are they superficial tests that pass vacuously?
   * Is every capability invariant (`INV-*`) bound to a real test via `test_binding: file::func`?
5. **Architectural Purity & Decision Capture (`DEC-*`)**:
   * Were any non-obvious trade-offs or design decisions made during implementation?
   * If yes, are they captured under `decisions:` (`DEC-*`) in the owning specification frontmatter?
   * Are there any circular imports or leaky abstractions introduced?

#### Autonomous Remediation Rule
If this self-review reveals **ANY** gap, omission, missing test assertion, or unhandled edge case:
* **DO NOT** wait for the user to prompt you or point it out.
* **IMMEDIATELY fix and implement the missing elements** before declaring completion.
* Re-run `eidos validate eidos && eidos drift --uncommitted` to ensure zero drift after the fix.

### Stage 3: Transparent Pre-Completion Summary
In your final response to the user, briefly summarize the results of your pre-completion check:
* **Deterministic Gate**: Validation & drift status (`clean`).
* **Self-Review Findings**: Key edge cases, invariant bindings, or potential gaps examined and verified.

---

## 6. Living Specification Directory Tree & Taxonomy

```text
eidos/
├── system.adoc                          # C4 Level 1 System Context (SYS-*)
├── architecture/
│   ├── system-architecture.adoc         # Arc42 Sections 1-12 & C4 Level 2 Container Topology
│   ├── containers/                      # C4 Level 2 Containers & inline Level 3 Components (CNT-*)
│   ├── components/                      # Promoted standalone C4 Level 3 Components (CMP-*)
│   └── contracts/                       # Interface Contracts: OpenAPI, Protobuf, CLI, JSON Schema (CTR-*)
├── domains/
│   └── <domain-slug>/
│       ├── domain.adoc                  # Bounded Context & Ubiquitous Language (DOM-*)
│       └── capabilities/                # Functional Capabilities, JTBD & Invariants (CAP-*)
├── design/                              # Optional Multi-Design Systems (DS-*), Tokens, UI Components (UIC-*)
├── clients/                             # Optional Client Apps (CLT-*), Screens (SCR-*), Flows (FLOW-*)
├── personas/                            # Actor Personas (PER-*)
├── policies/                            # System-wide Governance & NFR Policies (POL-*)
├── explorations/                        # Technical Spikes & Feasibility Discovery (EXP-*)
├── deltas/                              # SpecDelta Mutations (DLT-*)
└── views/                               # Auto-generated symlink projections (DO NOT EDIT MANUALLY)
```

---

## 7. Core CLI Command Reference

| Command | Purpose |
| :--- | :--- |
| `eidos validate [PATH] [--json]` | Validate all `.adoc` specifications against v1 JSON Schemas, check cross-references, verify declared container package paths and contract specification files, and enforce Typer CLI contract parity. |
| `eidos drift [--uncommitted] [--base REF] [--json]` | Primary Git-native verification gate: detects spec-to-code drift, enforces substantive spec diffs, executes full spec validation, and statically verifies polyglot invariant test bindings. |
| `eidos context [-f FILE] [-c ID] [-t TASK] [--tier TIER] [--format json]` | Extract token-budgeted (`anchor`, `guardrails`, `deep`) architectural context slices. |
| `eidos index repo-map [--max-tokens N] [--format json]` | Generate an AST-compressed structural repository map. |
| `eidos index find-symbol <QUERY> [--json]` | Resolve class/function/method symbols to exact file/line coordinates and owning specs. |
| `eidos index [status\|sync\|search\|blast-radius] [--json]` | Inspect, synchronize, search (BM25), or compute N-hop blast radius on the knowledge graph. |
| `eidos spec [list\|show\|create\|delete]` | List (including inline `CMP-*` components), inspect, scaffold, or safely delete living specification documents. |
| `eidos spec component [promote\|inline] <ID>` | Promote inline C4 Level 3 components to standalone `.adoc` files in `eidos/architecture/components/`, or inline them back. |
| `eidos delta [create\|status\|list\|verify\|audit\|apply\|changelog]` | Manage the SpecDelta (`DLT-*`) lifecycle in Planned Mode and compile `CHANGELOG.md`. |
| `eidos portal [serve\|build]` | Launch the interactive Living Web Portal server or export a static site bundle. |
| `eidos plugin [install\|uninstall\|version\|update\|validate\|info]` | Install, update, and validate Eidos agent plugins across supported coding environments. |
| `eidos doctor [--json]` | Run workspace diagnostics across CLI binary, living specs, knowledge graph index, and hooks. |

---

## 8. The 4 Cohesive Eidos Skills

To avoid context bloat, Eidos organizes all specialized reference material into **4 cohesive workflow skills**:

| Skill | Scope & When to Load |
| :--- | :--- |
| **`eidos`** *(This Skill)* | **Core Orchestrator, Context & Drift**: Workflow selection (Direct vs. Planned), `eidos context` slicing, and `eidos drift` verification. |
| **`eidos-spec`** | **Living Specification & Invariant Authoring**: Authoring and updating Product specs (`DOM-*`, `CAP-*`, `PER-*`), Arc42/C4 Architecture (`CNT-*`, inline/promoted `CMP-*`, `CTR-*`, embedded `decisions:`), Multi-Design Systems (`DS-*`, W3C tokens, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`), and Policies/Invariants (`POL-*`, `INV-*` with polyglot `file::func` test bindings). |
| **`eidos-plan`** | **Explorations, SpecDeltas & Staff Architectural Review**: Running time-boxed technical spikes (`EXP-*`), authoring/verifying/applying SpecDeltas (`DLT-*`) with declarative `expect:` post-conditions, and conducting 5-dimension Staff Systems Architect reviews. |
| **`eidos-ops`** | **Repository Bootstrap, Brownfield Extraction & Living Web Portal**: Initializing greenfield repositories (`eidos init`), reverse-engineering existing codebases into living specs, and serving/building the interactive Web Portal (`eidos portal serve`, `eidos portal build`). |
