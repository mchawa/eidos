# How Eidos Works: Architecture & Engine Internals

Eidos is not a passive documentation generator or an LLM wrapper—it is a **100% deterministic, Git-native software architecture and lifecycle engine (`POL-004`)**. Every query, schema validation, context slice, and drift check runs locally in milliseconds with zero external AI calls and zero background daemons.

This guide explains how Eidos works under the hood across its end-to-end execution loop and six core engine subsystems.

---

## 1. The End-to-End Eidos Execution Loop

When a human engineer or an autonomous AI coding agent works in an Eidos-governed repository, every change flows through a deterministic 4-stage feedback loop:

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Engineer / AI Agent
    participant CLI as Eidos CLI (CNT-EIDOS-CLI)
    participant Index as SQLite Knowledge Graph (CNT-EIDOS-INDEX)
    participant Core as Spec & Delta Engine (CNT-EIDOS-CORE)
    participant VCS as Git Drift Engine (CNT-EIDOS-VCS)
    participant Git as Git Repo (src/, eidos/, tests/)

    Dev->>CLI: 1. eidos context -f src/... (or -t "task prompt")
    CLI->>Index: Lazy dirty check (mtime_ns + SHA-256 in <5ms)
    Index->>Git: Incrementally re-index modified specs, AST symbols & tests
    Index-->>CLI: Rank-ordered, token-budgeted slice (anchor / guardrails / deep)
    CLI-->>Dev: Owning CNT-*/CMP-*, contracts (CTR-*), capabilities (CAP-*), invariants (INV-*), decisions (DEC-*)

    Note over Dev,Git: 2. Select Workflow Mode: Direct Mode (localized) vs. Planned Mode (SpecDelta DLT-*)
    Dev->>Git: 3. Co-evolve code (src/), living specs (eidos/*.adoc), and bound tests (tests/)

    Dev->>CLI: 4. eidos validate eidos && eidos drift --uncommitted
    CLI->>Core: Validate 16 v1 JSON Schemas, x-eidos-ref graph links & DEC-* uniqueness
    CLI->>VCS: Inspect Git diff, AST public symbols, 1-hop spec coverage & polyglot INV-* test bindings
    VCS-->>Dev: Exit 0 (Clean) or Exit 1 (Structured remediation suggestions)
```

### Why This Loop Eliminates Architectural Drift
1. **Before Writing Code (Context Slicing)**: Instead of stuffing thousands of lines of raw markdown into an LLM context window, `eidos context` queries the embedded SQLite knowledge graph (`eidos/.cache/index.db`) and extracts only the exact containers, components, contracts, active invariants, and historical design decisions (`DEC-*`) governing the target files or task.
2. **During Implementation (Dual Workflow Co-Evolution)**:
   * **Direct Mode**: For bug fixes and single-container refactors, engineers and agents update code, tests, and the owning `.adoc` specification in the same commit.
   * **Planned Mode**: For cross-container features, new containers, or breaking contract changes, changes are declared upfront in a **SpecDelta (`DLT-*`)** with verifiable `mutations:` and `expect:` post-conditions.
3. **Before Completion (Deterministic Gate & Lifecycle Hooks)**: `eidos validate` and `eidos drift` verify that every schema is valid, every cross-reference resolves, every modified code package is backed by a substantive specification update, and every capability invariant (`INV-*`) is bound to a real test function.

---

## 2. System Container Topology (Dogfooded C4 Level 2)

Eidos uses its own specification standard to govern itself. The engine is organized as a modular monorepo with **7 C4 Level 2 Containers** across 4 architectural tiers:

```mermaid
flowchart TD
    subgraph Tier1["Tier 1: Presentation & Interface"]
        CLI["CNT-EIDOS-CLI<br/>Typer CLI & Rich UI<br/>(src/eidos/cli)"]
        PORTAL["CNT-EIDOS-PORTAL<br/>Living Web Portal & Static Builder<br/>(src/eidos/portal)"]
        DOCS["CNT-EIDOS-DOCS<br/>Zensical User Documentation<br/>(docs/)"]
    end

    subgraph Tier2["Tier 2: Extensibility & Agent Harness"]
        PLUGIN["CNT-EIDOS-PLUGIN<br/>TargetAdapters & Lifecycle Hooks<br/>(src/eidos/plugin)"]
    end

    subgraph Tier3["Tier 3: Core Domain & Verification Engines"]
        CORE["CNT-EIDOS-CORE<br/>Parser, Validator, Scaffolder & DeltaEngine<br/>(src/eidos/core)"]
        VCS["CNT-EIDOS-VCS<br/>Git Diff, AST Structural & Drift Engine<br/>(src/eidos/vcs)"]
    end

    subgraph Tier4["Tier 4: Knowledge Graph & Persistence"]
        INDEX["CNT-EIDOS-INDEX<br/>SQLite WAL + FTS5 + AST Graph<br/>(src/eidos/core/index)"]
    end

    CLI --> CORE
    CLI --> VCS
    CLI --> INDEX
    CLI --> PLUGIN
    CLI --> PORTAL
    VCS --> CORE
    VCS --> INDEX
    PORTAL --> CORE
    PORTAL --> INDEX
    PLUGIN --> CORE
    INDEX --> CORE
    DOCS --> PORTAL
```

---

## 3. Subsystem Deep Dive

### 3.1 Specification Parser & Schema Validator (`CNT-EIDOS-CORE`)

Every living specification in `eidos/` is an AsciiDoc (`.adoc`) file headed by a YAML frontmatter block (`--- ... ---`). When `eidos validate` runs, `SpecParser` and `SpecValidator` execute a **three-pass validation pipeline**:

#### Pass 1: Schema Validation with Shared `$defs` & SQLite Caching
1. **Frontmatter Extraction**: `SpecParser` splits the `.adoc` file into structured YAML frontmatter metadata (parsed via Rust-backed `fastyaml-rs`), raw YAML line offsets, and AsciiDoc prose body.
2. **Shared Definition Injection**: `SpecValidator` loads the canonical JSON/YAML schema derived from `type:` under `assets/schemas/v1/` (16 document types across `product/*`, `tech/*`, `design/*`, and `delta/*`, introspectable via `eidos spec schema [type]`) and merges reusable definitions from `assets/schemas/v1/common/defs.yaml`:
   * `#/$defs/decision`: Enforces structure on in-place architectural decisions (`DEC-*`, `title`, `decision`, `rationale`, `rejected_alternatives`, `graduated_from`).
   * `#/$defs/invariant`: Enforces RFC 2119 rules and `test_binding` paths on invariants (`INV-*`).
3. **Content-Hashed Validation Cache (`spec_validation_cache`)**: Validation results and parsed `SpecDocument` payloads are serialized with `orjson` and keyed by repo-relative path, `mtime_ns`, file SHA-256, and schema SHA-256 inside `eidos/.cache/index.db`. Unchanged specifications skip JSON Schema re-evaluation on subsequent runs.

#### Pass 2: Cross-Document Graph Integrity (`x-eidos-ref`) & Architectural Laws
After individual documents validate against their JSON Schema, `SpecValidator` verifies global graph invariants across the entire catalog:
* **Entity ID & Decision Uniqueness**: Ensures zero duplicate document IDs (`CNT-*`, `CMP-*`, `CAP-*`, etc.) and zero duplicate `DEC-*` decision IDs across the repository.
* **Schema-Driven Reference Resolution (`x-eidos-ref`)**: Schemas annotate reference fields with `x-eidos-ref: {prefixes: [...], target_types: [...]}`. The validator checks that every referenced ID or file path resolves to an existing catalog entity of the exact expected type (for example, preventing a `CNT-*` container field from pointing to a `CMP-*` component).
* **Centralized Component Governance**:
  * `INV-COMPONENT-CENTRALIZATION-MANDATE`: Every `tech/component` specification (`CMP-*`) must reside centrally under `eidos/architecture/components/` and never inline in container manifests or inside implementation directories (`src/`, `packages/`).
  * `INV-COMPONENT-CONTAINER-AFFINITY`: Every `CMP-*` component must bind to an existing parent `CNT-*` container via `container:`.
  * `INV-INTERFACE-BOUNDARY-GOVERNANCE`: Containers (`CNT-*`) own cross-process boundary contracts (`CTR-*`), whereas components (`CMP-*`) represent internal building blocks whose intra-container `consumes_api` and inter-container `depends_on` relationships are derived automatically via AST import analysis.

#### Pass 3: Physical Binding Verification & Bidirectional Multi-Protocol Contract Parity
When executed within a repository containing a source tree (`src/`), `SpecValidator` verifies that declared living specification bindings exist in the filesystem:
* **Container Source Path Resolution**: Verifies that every directory or file declared in `source_path` on active C4 containers (`CNT-*`) and components (`CMP-*`) exists on disk.
* **Contract Specification File Resolution**: Verifies that external specifications (e.g., OpenAPI schemas, protobufs, or CLI entrypoint files) referenced in `specification_file` on active contracts (`CTR-*`) exist on disk.
* **Bidirectional Multi-Protocol Contract Parity (`DLT-2026-082`)**: Across `cli_args` (supporting recursive Typer `add_typer` sub-apps, Click, and `argparse`), `openapi`, `protobuf`, `graphql`, and `python_protocol` contracts, Eidos verifies parity in both directions—flagging both undocumented operations added in `specification_file` and stale/phantom operations left in `CTR-*` specifications after removal from code.
* **Applied SpecDelta Fast-Path (`DLT-2026-080`)**: Historical `status: applied` SpecDeltas are cached against both their file SHA-256 and the `delta.schema.yaml` SHA-256 digest so validation remains O(1) for unchanged historical deltas.

> [!NOTE]
> If `eidos validate` runs in a repository without a source tree (e.g. a standalone specification repository or documentation deployment), Pass 3 physical disk checks are automatically skipped.

---

### 3.2 Embedded SQLite Knowledge Graph & Context Slicer (`CNT-EIDOS-INDEX`)

To provide sub-millisecond retrieval without external databases or embedding APIs, `CNT-EIDOS-INDEX` maintains an embedded SQLite 3 database in WAL (Write-Ahead Logging) mode at `eidos/.cache/index.db`.

The index engine is decomposed into 5 focused modules under `src/eidos/core/index/`:

| Module | Component ID | Responsibility |
| :--- | :--- | :--- |
| `storage.py` | `CMP-GRAPH-STORAGE` | Canonical authority for `SCHEMA_VERSION` (`2.17.0`), DDL initialization, WAL pragmas, stateless drop-and-rebuild migration on version mismatch, corruption recovery, and unified `orjson`-backed `SpecDocument` / test-binding cache helpers. |
| `sync.py` | `CMP-INCREMENTAL-SYNC-ENGINE` | Two-tier `mtime_ns` + `SHA-256` dirty detection across `_GOVERNED_TEXT_EXTS`, path-keyed entity eviction on ID rename, canonical unidirectional edge reconstruction (including C4 Level 1 `ARCH-*`/`SYS-*`, exploration `graduated_to`, and AST-derived `consumes_api`/`depends_on` edges), `file_tokens_fts` inverted token indexing, AST symbol FTS projection, and source ownership reconciliation. |
| `query.py` | `CMP-FTS-QUERY-ENGINE` & `CMP-GRAPH-TRAVERSAL-ENGINE` | Two-tier (`AND` conjunction before `OR` fallback) positional column-weighted FTS5 BM25 search, identifier de-compounding, symbol lookup via unified `all_mappings` CTE, indexed catalog reconstruction (`query_spec_catalog_in_db`), and hub-scoped recursive CTE `k`-hop blast-radius and SpecDelta impact (`compute_delta_impact_in_db`) traversal. |
| `context.py` | `CMP-TIERED-CONTEXT-COMPRESSOR` | Rank-ordered, token-budgeted context slicing across `anchor` (`<=300` tokens), `guardrails` (`<=1000` tokens), and `deep` (`<=2500` tokens) tiers with YAML/code-calibrated token estimation. |
| `ast_indexer.py` | `CMP-AST-SYMBOL-INDEXER` | Unified polyglot AST symbol, import, and test-binding extraction via `AstGrepIndexer` (`ast-grep-py` `SgRoot`) across all 17 supported languages (Python, TS, TSX, JS, JSX, Go, Rust, Java, Kotlin, C, C++, C#, Ruby, PHP, Swift, Vue, and Svelte). |

#### Relational & Lexical Schema (`eidos/.cache/index.db`)
The SQLite database is a **100% stateless, derived cache (`DEC-INDEX-STATELESS-DERIVED-CACHE`)** managing 10 coordinated tables (9 domain/FTS/cache tables plus schema `metadata`):
1. **`entities`**: All parsed specification nodes (`SYS-*`, `DOM-*`, `CAP-*`, `POL-*`, `PER-*`, `EXP-*`, `ARCH-*`, `CNT-*`, `CMP-*`, `CTR-*`, `DS-*`, `UIC-*`, `SCR-*`, `CLT-*`, `FLOW-*`, `DLT-*`, plus child `INV-*` nodes across capabilities, policies, containers, and components) with `orjson`-serialized metadata and body prose.
2. **`edges`**: Canonical unidirectional architectural relationships (`realizes`, `provides`, `consumes`, `owned_by`, `implemented_by`, `styled_by`, `extends`, `governed_by`, `serves_persona`, `describes_system`, `contains_container`, `graduated_from`, `graduated_to`, `delivered_by`, `has_screen`, `has_flow`, `uses_screen`, `implements`, `contains_component`, `part_of`, `asserts`, `affects`, plus AST-derived `consumes_api` and `depends_on`).
3. **`indexed_files`**: Tracks `rel_path`, `mtime`, `sha256`, `doc_id`, and `doc_type` across governed specifications, source files, and test suites for incremental synchronization.
4. **`symbol_mappings`**: Maps specification-declared file paths, globs, and symbols (`source_path`, `package_path`, `owned_path`, `inferred_path`, `contract_path`, `specification_file`, `test_binding`) to owning `CNT-*`, `CMP-*`, `CTR-*`, and `INV-*` entities.
5. **`code_symbols`**: Persists AST-extracted classes, functions, methods, structs, enums, interfaces, line coordinates (`line_start`, `line_end`), decorator-prefixed signatures, and primary owner flags (`is_primary`).
6. **`specs_fts`**: SQLite FTS5 virtual table indexing `entity_id` (`UNINDEXED`), `title`, `jtbd_or_rule`, `body_text`, `keywords`, and de-compounded `symbols` (including projected primary AST `code_symbols`, while excluding archived SpecDeltas under `eidos/deltas/archive/` to prevent historical noise).
7. **`file_tokens_fts`**: SQLite FTS5 virtual table (`unicode61` with `tokenchars '-_'`) indexing unique `[A-Za-z0-9_-]{3,}` identifier and entity tokens per governed file (`rel_path`, `doc_type`) for sub-millisecond SpecDelta deletion auditing (`DLT-2026-107`).
8. **`spec_validation_cache`**: Content-addressed cache of parsed `SpecDocument` payloads and JSON Schema validation verdicts keyed by `mtime_ns`, file SHA-256, and schema SHA-256.
9. **`test_binding_cache`**: Content-addressed cache of AST-extracted test functions (`func` and `Class::method`) and referenced `INV-*` / `POL-*` tokens per test file.
10. **`metadata`**: Canonical `SCHEMA_VERSION` (`2.17.0`) and cache lifecycle state.

#### How Lazy Incremental Sync Works (`<5ms`)
Before every CLI query (`eidos context`, `eidos index`, `eidos spec list`), `_ensure_synced()` runs `check_index_dirty()`:
1. Walks `eidos/`, `tests/`, and every container/component's declared `source_path`.
2. If a file's `mtime_ns` matches `indexed_files`, it is skipped immediately without reading bytes from disk.
3. If `mtime_ns` changed, Eidos computes the file's `SHA-256`. If the hash matches the stored hash (e.g., after `git checkout` or `touch`), Eidos refreshes `mtime_ns` in-place without re-indexing.
4. Only genuinely modified, added, or deleted files are re-parsed. If a specification's `id:` was renamed in-place, `sync.py` evicts the old `doc_id` keyed by `file_path` so ghost entities and orphan edges never linger.

#### How Lexical Retrieval & Tiered Context Slicing Work
When an agent runs `eidos context -t "rate limiting and token expiration" --tier guardrails`:
1. **Identifier De-Compounding & AST Symbol Projection**: Query tokens like `detect_drift` or `ArchitecturalIndex` are split into both exact and sub-word tokens (`detect`, `drift`, `architectural`, `index`) and matched against both specification prose and projected AST `code_symbols` (`DLT-2026-078`).
2. **Two-Tier SQL-Scoped FTS5 Ranking**: `specs_fts` prioritizes high-precision multi-term `AND` matches before falling back to `OR` expansion (`DLT-2026-083`), using positional BM25 weights `bm25(specs_fts, 0.0, 4.0, 2.0, 1.0, 2.0, 2.0)` (`title` weighted 4x, `jtbd_or_rule`/`keywords`/`symbols` 2x, `body_text` 1x). Historical `delta/mutation` and `product/exploration` records are filtered out *inside SQL before `LIMIT`* so active architecture entities are never crowded out.
3. **Hub-Scoped Graph Expansion**: Matched domains (`DOM-*`), capabilities (`CAP-*`), or invariants (`INV-*`) expand across `edges` to pull in their realizing containers (`CNT-*`), components (`CMP-*`), contracts (`CTR-*`), and governing policies (`POL-*`), while scoping hub membership edges (`DOM-*`, `DS-*`) at depth >= 2 to prevent sibling fan-out explosion (`DLT-2026-083`).
4. **Calibrated Rank-Ordered Token Budgeting**: `TieredContextCompressor` serializes the subgraph into Markdown or JSON while strictly enforcing the tier's token ceiling (`anchor <= 300`, `guardrails <= 1000`, `deep <= 2500` tokens) using structure-calibrated token estimation (`~3` chars/token for YAML/JSON/code/paths vs `~4` chars/token for prose), rendering compact 1-line `DEC-*` summaries in `anchor` and full rationale/rejected alternatives in `guardrails` and `deep`.

---

### 3.3 Git-Native Drift Detection Engine (`CNT-EIDOS-VCS`)

`eidos drift` is the primary verification gate ensuring that code, specifications, and tests never diverge. Located in `src/eidos/vcs/` (`drift.py`, `diff.py`, `structural.py`, `invariants.py`, `report.py`), it executes an **8-step deterministic verification pipeline**:

```mermaid
flowchart LR
    A["1. Git Diff &<br/>Merge-Base SHA"] --> B["2. Batched .gitattributes<br/>Generated Filter"]
    B --> C["3. Move/Rename Pairing &<br/>AST Symbol Diffing"]
    C --> D["4. Path-Boundary<br/>Ownership Mapping"]
    D --> E["5. Substantive Spec<br/>Diff Verification"]
    E --> F["6. Mode Evaluation<br/>(Direct vs. Planned)"]
    F --> G["7. SpecValidator<br/>Catalog Verification"]
    G --> H["8. Polyglot Static<br/>INV-* Test Verification"]
```

1. **Fail-Closed Base Ref & Merge-Base Resolution (`diff.py`)**:
   * In branch mode (`--base origin/main`), Eidos resolves the exact merge-base SHA (`git merge-base`) for 3-dot diffs (`base...HEAD`) and reads baseline file contents directly from that commit SHA. If an explicit `--base` ref does not exist, Eidos fails closed with an error instead of silently falling back to `--uncommitted`.
   * In working-tree mode (`--uncommitted`), Eidos inspects both staged and unstaged changes plus untracked files against `HEAD`.
2. **Batched Generated/Vendored Filtering (`project.py`)**:
   * Candidate code paths are passed in a single batched `git check-attr linguist-generated linguist-vendored` call. Generated bundles (such as `assets/portal/bundle.html.gz`) are automatically excluded from code drift.
3. **Content-Based Move Pairing & Polyglot Structural AST Diffing (`structural.py`, `DLT-2026-083`)**:
   * Deleted and added files are paired by content and public symbol overlap to recognize file moves/renames without relying on Git rename heuristics.
   * For modified code files across Python, TS/JS, Go, Rust, Java, Kotlin, C/C++, C#, Ruby, PHP, Swift, Vue, and Svelte, `AstSymbolIndexer` compares public AST declarations and signatures between the baseline version and current working tree.
   * If a code change only touches internal implementation comments, docstrings, or function-body-local logic without adding/modifying/removing public symbols or files, Eidos classifies it as a non-structural edit.
4. **Strict Path-Boundary Ownership Mapping**:
   * Every modified source file is mapped to its owning C4 component (`CMP-*` via `source_path`) and C4 container (`CNT-*` via `source_path`).
   * **Unowned Code Detection**: If a structural code file is added or modified outside any declared `source_path`, `eidos drift` flags it as **unowned code drift** unless claimed by a container/component or covered by a substantive root `system.adoc` / `system-architecture.adoc` update.
5. **Substantive Spec Diff & Boundary-Aware Anchoring (`DLT-2026-081`)**:
   * To prevent agents or developers from bypassing drift checks by adding a blank line or comment to a `.adoc` file, `StructuralChangeInspector.is_substantive()` normalizes specification diffs by stripping AsciiDoc comments (`//`), YAML comments (`#`), and whitespace.
   * Structural code changes whose only accompanying spec edit is whitespace or comments are flagged under `trivially_covered_files` (`rule: trivial_spec_diff`) and fail the drift check.
   * **Boundary-Aware Symbol Anchoring (`DLT-2026-081`)**: New unclaimed files and package/contract boundary exports (`__init__.py`, `index.ts`, `mod.rs`, `lib.rs`, `__all__`, or contract `specification_file`) require explicit file or symbol anchoring when covered via a 1-hop neighbor spec (`rule: missing_anchor`), whereas intra-package helper functions are covered by substantive updates to their owning `CNT-*`/`CMP-*` specification without polluting C4 prose with low-level helper identifiers.
6. **Workflow Mode & 1-Hop Neighborhood Evaluation (`DLT-2026-085`, `DLT-2026-090`)**:
   * **Planned Mode** (when an active `DLT-*` SpecDelta is modified in the diff): Verifies that all touched containers and components are authorized strictly by the active SpecDelta's composable `mutations[].target` (and its 1-hop graph ownership neighborhood).
   * **Direct Mode** (no active `DLT-*` in diff): Verifies that for every touched container/component with structural changes, either its own `.adoc` specification or a specification within its **component-scoped 1-hop knowledge graph neighborhood** (`CAP-*`, `CTR-*`, `CNT-*`, `CMP-*`—explicitly excluding global `POL-*` and `PER-*` edges so editing one policy never rubber-stamps the whole repo) was substantively updated.
7. **Specification Schema & Referential Integrity Gate (`validator.py`)**:
   * Runs the full 3-pass `SpecValidator` suite over `eidos/`. Before code changes can merge, Eidos guarantees that all specification documents conform to JSON schemas, cross-references resolve, declared container `source_path` and contract `specification_file` targets exist on disk, and contracts maintain bidirectional parity.
8. **Polyglot Static Invariant Test Verification (`invariants.py`, `DLT-2026-078`)**:
   * Scans every `INV-*` invariant across capabilities (`CAP-*`), policies (`POL-*`), containers (`CNT-*`), and standalone components (`CMP-*`).
   * Uses Git-tracked file enumeration (`get_tracked_files`), `test_binding_cache`, and `ast-grep` across **Python (`.py`), TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`), Go (`_test.go`), Rust (`.rs`), Java (`.java`), and Kotlin (`.kt`, `.kts`)** to verify that:
     1. The file in `test_binding: path/to/test_file::test_func` (or `path/to/test_file::TestClass::test_method`) exists.
     2. The target test function or method is defined in the AST.
     3. The test body, docstring, decorator, or comment explicitly references the `INV-*` ID.
   * **Orphan Invariant Detection**: Scans the test suite to detect tests referencing `INV-*` IDs that do not exist in any living specification.

---

### 3.4 SpecDelta Engine, Declarative Verification, Tombstone Auditor & Archive Ledger (`CNT-EIDOS-CORE`)

In **Planned Mode**, architectural changes are governed by **SpecDeltas (`eidos/deltas/dlt-yyyy-nnn.adoc`)** managed by `DeltaEngine` (`delta_engine.py`), `AuditEngine` (`audit.py`), and `ChangelogGenerator` (`changelog.py`):

* **Composable `operation` + `target` Mutations & Graph-Derived Scope (`DLT-2026-085`, `DLT-2026-087`, `DLT-2026-090`, `DLT-2026-104`, `DLT-2026-112`)**: Each SpecDelta declares an ordered `mutations:` array using the orthogonal composable contract (`operation: add | modify | remove | deprecate` and `x-eidos-ref` validated `target`)—while 0-hop affected entities (`affects`) and `k`-hop downstream impact (`blast_radius`) are computed dynamically via `ArchitecturalIndex.compute_delta_impact`.
* **Closed-Schema Declarative Post-Conditions & Dot-Path Key Resolution (`expect:`, `DLT-2026-079`, `DLT-2026-103`)**: Mutations specify strictly schema-validated (`additionalProperties: false`) post-conditions under `expect.body` (`contains`, `not_contains`, evaluated against comment-stripped AsciiDoc body text) and `expect.frontmatter` (`source_path`, `rule`, `test_binding`, `status`, `version`, `category`, plus nested dot-path keys such as `contracts.provides`, `technical.containers`, or `decisions.id` across nested dicts and lists of dicts). Running `eidos delta verify <ID>` checks that target entities exist (or are absent for `remove` mutations), were substantively changed relative to the Git baseline (`vcs.base_commit` / `_resolve_git_baseline_sha`), and that all `expect:` assertions hold—emitting granular `failure_reason` diagnostics on mismatch.
* **Cross-Ref Collision-Safe IDs & Verified In-Place Application (`DLT-2026-080`, `DLT-2026-088`, `DLT-2026-095`)**: `eidos delta create [ID] --slug` scans all local and remote Git refs (`refs/heads/*`, `refs/remotes/*`) plus `eidos/deltas/archive/` to guarantee collision-free `DLT-YYYY-NNN` identifiers across parallel branches and records `vcs.base_commit`. When `eidos delta apply <ID>` runs, `DeltaEngine` verifies all declared mutations by default (`verify=True`), validates the updated SpecDelta with `SpecValidator`, and transitions `status: applied` in place while preserving YAML comments.
* **Index-Driven Spec-Scoped Deletion Auditing (`eidos delta audit <ID>`, `DLT-2026-107`)**: When a SpecDelta removes or deprecates an entity, `AuditEngine` queries the synced `ArchitecturalIndex` (`file_tokens_fts` and `indexed_files`) in sub-millisecond time—with zero `git ls-files` subprocesses or full-repository `os.walk` scans—and classifies every occurrence across governed files by `doc_type`:
  * **Approved References**: Current or historical `DLT-*` / `EXP-*` records (`self_delta`, `historical_ledger` resolved in $O(1)$ from `entities`), negative assertions or docstrings in `code/test`, cleanup routines, or matches for `tombstone.allowed_reasons` and inline `# eidos:tombstone` / `# eidos:allow-retired` annotations.
  * **Active Leaks**: Unacknowledged live references in active specifications (`active_spec_leak`, `critical`) or governed source/test files (`active_code_leak`, `high`; `--strict` exits with code `1`).
* **Two-Tier `--keep-recent 15` Archive & Slimming Lifecycle (`eidos delta archive`, `DLT-2026-106`)**: To prevent top-level directory clutter, FTS5 search noise, and portal payload bloat as projects mature, `eidos delta archive [--keep-recent 15]` retains all active SpecDeltas/Explorations plus the 15 most recent terminal items (`applied`/`rejected` and `adopted`/`discarded`) in `eidos/deltas/` and `eidos/explorations/`, moving older terminal records into `eidos/deltas/archive/` and `eidos/explorations/archive/` as slimmed, schema-valid `.adoc` files (`~88%` byte reduction, stamping `vcs.applied_commit` and `vcs.release_tag`). `eidos delta changelog` combines Git release tags with applied and archived SpecDeltas to compile a Keep a Changelog compliant `CHANGELOG.md` and `docs/changelog.md`.

---

### 3.5 Agent Plugin Harnesses & Stateless Stop Hook (`CNT-EIDOS-PLUGIN`)

Eidos integrates with AI coding assistants through a declarative `TargetAdapter` pipeline (`src/eidos/plugin/installer.py`) and a stateless CLI-native `Stop` lifecycle hook (`src/eidos/plugin/hooks.py`):

1. **Manifest-Tracked Multi-Harness Projection (`eidos plugin install`)**:
   * Projects the canonical plugin (`assets/plugin_reference/`) and **4 cohesive Discovery-First Agent Skills** (`eidos`, `eidos-spec`, `eidos-plan`, `eidos-ops`) into `.agents/plugins/eidos/`, `.claude/`, `.cursor/`, `.github/`, `.aider/`, `.cline/`, `.opencode/`, and `.codex/`.
   * Records SHA-256 file digests in `.eidos-manifest.json` so `eidos plugin update` and `eidos plugin uninstall` never clobber user-owned files or custom instructions.
2. **Stateless Unified `Stop` Hook (`eidos hook`)**:
   * When an AI agent finishes its task (`Stop` event), `eidos hook` runs `detect_drift(uncommitted=True)` once—evaluating specification schemas, cross-reference integrity, invariant test bindings, and spec-to-code synchronization in a single pass.
   * Returns `{"decision": "allow"}` when clean, or `{"decision": "allow", "systemMessage": "⚠️ ..."}` with actionable remediation guidance when issues exist, requiring zero state files or per-edit subprocesses.

---

### 3.6 Scalable Air-Gapped Living Web Portal (`CNT-EIDOS-PORTAL`)

The interactive Living Web Portal (`eidos portal serve` and `eidos portal build`) compiles all 16 specification types and the SQLite knowledge graph into a single-page visual architecture explorer:

* **Zero Node.js Runtime Dependency (`INV-PORTAL-ZERO-NODE-DEP`)**: The frontend (React 19, Tailwind CSS v4, Material Design 3, Mermaid.js v11, and `@asciidoctor/core`) is pre-compiled at package build time into a deterministic gzip-compressed asset (`assets/portal/bundle.html.gz`, `<5 MiB` uncompressed budget).
* **100% Air-Gapped Bundle (`INV-PORTAL-AIR-GAPPED-BUNDLE`)**: All scripts, stylesheets, and Geist/Geist Mono `woff2` fonts are embedded inline. The generated portal makes zero requests to external CDNs, working offline and over `file://` or internal corporate networks.
* **Static-First Scalability & Deep Linking (`INV-PORTAL-STATIC-SCALABILITY`, `DLT-2026-108`)**: `StaticSiteBuilder` deduplicates specification bodies in `window.__EIDOS_DATA__` and `data.json` (`~40–50%` payload reduction, re-hydrated client-side from `data.specs`) and exports pre-computed graph `edges` and `validation` diagnostics. Every view, C4 container whitebox, contract, SpecDelta, and SpecReader drawer is synchronized with `window.location.hash` (`#/<view>/<entity-id>`) for zero-config deep linking on static hosts, while `eidos portal serve` provides progressive live auto-reload via lightweight `GET /api/version` mtime polling and `GET /api/drift`.
* **Hierarchical C4 Drill-Down, Blast Radius Simulator & Contract Explorer (`INV-PORTAL-INTERACTIVE-C4`)**: `PortalGenerator` synthesizes C4 Level 1 System Context, tiered Level 2 Container diagrams with explicit and AST-derived dependency edges, clickable Level 3 Component Whitebox views, an interactive client-side 1–2 hop **Blast Radius Simulator**, a first-class **Boundary Interface Contracts (`CTR-*`)** explorer, and a **Multi-Design System & Recursive W3C Token Tree** browser.
