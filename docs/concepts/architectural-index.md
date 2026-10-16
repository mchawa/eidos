# Architectural Knowledge Graph & Index

Eidos features a built-in, zero-dependency **Architectural Knowledge Graph Index** powered by an embedded SQLite database (`eidos/.cache/index.db`) with full-text search (FTS5) and recursive graph traversals (Common Table Expressions).

The index enables sub-3ms architectural queries, intelligent full-text specification discovery, transitive blast-radius analysis, and token-budgeted 3-tier context slicing for AI coding agents.

```
┌────────────────────────────────────────────────────────┐
│     Living Specs (eidos/) + Polyglot Code & Tests      │
└───────────────────────────┬────────────────────────────┘
                            │  Incremental Dirty Check (mtime_ns + SHA-256, <5ms)
                            ▼
┌────────────────────────────────────────────────────────┐
│       Embedded SQLite Knowledge Graph (eidos/.cache)    │
│  ├── entities (CNT, CMP, CTR, CAP, DOM, POL, INV, …)   │
│  ├── edges (depends_on, provides, governs, asserts, …) │
│  ├── indexed_files (mtime_ns, sha256, doc_id tracking) │
│  ├── symbol_mappings & code_symbols (AST coordinates)  │
│  ├── spec_validation_cache & test_binding_cache        │
│  └── specs_fts (SQLite FTS5 virtual table with BM25)   │
└───────────────┬────────────────────────┬───────────────┘
                │                        │
       CLI Commands               Agent Skills
  eidos index repo-map             eidos
  eidos index find-symbol          eidos-spec
  eidos context --tier anchor      eidos-plan
  eidos index status               eidos-ops
  eidos index search
  eidos index blast-radius
```

---

## Zero External Database Dependencies

The architectural index requires **zero external database daemons** and **zero third-party vector stores**:

* Built on Python's standard library **`sqlite3`** in WAL (Write-Ahead Logging) mode, utilizing native SQLite 3.x FTS5 and recursive Common Table Expressions (CTEs).
* Decomposed across 5 focused internal modules in `src/eidos/core/index/`: `storage.py` (`CMP-GRAPH-STORAGE`), `sync.py` (`CMP-INCREMENTAL-SYNC-ENGINE`), `query.py` (`CMP-FTS-QUERY-ENGINE` & `CMP-GRAPH-TRAVERSAL-ENGINE`), `context.py` (`CMP-TIERED-CONTEXT-COMPRESSOR`), and `ast_indexer.py` (`CMP-AST-SYMBOL-INDEXER`).
* Stores all relational graph data, AST symbol coordinates, and validation/test caches inside a single self-contained file: `eidos/.cache/index.db` (canonical `SCHEMA_VERSION = "2.3.0"`).
* Automatically performs drop-and-rebuild migration on schema version upgrades and self-heals on database corruption.
* The `eidos/.cache/` directory is isolated via `eidos/.gitignore`, keeping Git repositories pristine.

---

## Automatic Initialization & Lazy Synchronization

You never need to remember to run a manual build command before querying.

### 1. Automatic On-Demand Bootstrapping
The first time any command requiring architectural context is invoked (`eidos context`, `eidos index`, `eidos spec list`, or agent skills), Eidos automatically:
1. Creates `eidos/.cache/` if it does not exist.
2. Initializes the 8 SQLite tables (`entities`, `edges`, `indexed_files`, `symbol_mappings`, `code_symbols`, `spec_validation_cache`, `test_binding_cache`, `specs_fts`).
3. Indexes all living specifications, AST code symbols, and test bindings in ~100ms.

### 2. Zero-Drift Freshness (`_ensure_synced`)
Before answering any query, search, or context extraction request, the index runs a lazy two-tier freshness check (`check_index_dirty()`):
* Compares nanosecond file modification timestamps (`mtime_ns`) against `indexed_files` across `eidos/`, `tests/`, and every container's declared `package_path`.
* If `mtime_ns` changed, computes the file's `SHA-256` digest; if the content hash is unchanged, refreshes `mtime_ns` in-place without re-parsing.
* Executes in **<5ms** on clean working trees.
* If any `.adoc` spec, source file, or test file was edited, added, or deleted (or if a specification ID was renamed in-place), Eidos evicts stale entities by the previous `doc_id` stored in `indexed_files` and incrementally re-indexes only the modified files.

---

## Manual Index Management

While synchronization is completely automatic, developers and CI/CD pipelines can inspect or manage the index directly via the CLI:

### Inspect Index Status & AST Telemetry
View database size, schema version, health, total entities, edges, indexed files, AST parser engine telemetry, symbol breakdown by syntax kind, and test invariant assertions:

```bash
# Rich visual panel and tables:
eidos index status

# Machine-readable JSON output for AI agent tool calling:
eidos index status --json
```

Output:
```text
╭───────────────── Eidos Architectural Knowledge Graph Index ──────────────────╮
│        Database Path:  /path/to/project/eidos/.cache/index.db                │
│       Schema Version:  2.3.0                                                 │
│           Cache Size:  1008.0 KB                                             │
│               Status:  SYNCED (Clean) (4.20ms check)                         │
│       Total Entities:  105                                                   │
│          Total Edges:  140                                                   │
│        Indexed Files:  141                                                   │
│           AST Engine:  ast-grep (Native AST)                                 │
│          AST Symbols:  581 (86 classes, 157 funcs, 155 methods, 49 test      │
│                        bindings)                                             │
│ Invariant Assertions:  31 assertions across 31 invariants                    │
╰──────────────────────────────────────────────────────────────────────────────╯
       Entities by Type        
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Type                ┃ Count ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ delta/mutation      │    23 │
│ invariant           │    31 │
│ product/capability  │     8 │
│ tech/container      │     6 │
│ ...                 │   ... │
└─────────────────────┴───────┘
      AST Code Symbols &      
         Traceability         
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Syntax Kind        ┃ Count ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ class              │    86 │
│ function           │   157 │
│ method             │   155 │
│ test_binding       │    49 │
│ ...                 │   ... │
└────────────────────┴───────┘
```

### Force Index Rebuild
To force a clean rebuild of the SQLite database and FTS5 search index:

```bash
eidos index sync --force
```

---

## The 3-Tier Architectural Context Model

When AI agents work on software engineering tasks, providing entire specification files consumes excessive context window tokens and dilutes model attention. Eidos introduces a **rank-ordered, token-budgeted 3-tier compression model** enforced across both Markdown and JSON outputs:

| Tier | Flag / Param | Token Budget Ceiling | Typical Size | Content Included | Recommended Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1: Anchor** | `--tier anchor` | **`<= 300 tokens`** | ~140 tokens | Owning `CNT-*` / `CMP-*`, container type, runtime, `package_path`, bound domain, direct `CTR-*` signatures, compact 1-line `DEC-*` decision summaries. | Fast, single-file edits, routine bug fixes, syntax changes. |
| **Tier 2: Guardrails** *(Default)* | `--tier guardrails` | **`<= 1,000 tokens`** | ~300–900 tokens | Anchor content + domain capabilities (`CAP-*`), active invariants (`INV-*`) with `test_binding`, governing policies (`POL-*`), and full `DEC-*` rationale + rejected alternatives. | Standard feature development, refactorings, policy verification. |
| **Tier 3: Deep** | `--tier deep` | **`<= 2,500 tokens`** | ~500–1,500 tokens | Guardrails content + C4 Level 3 internal modules (`c4_internal_components`), promoted component in-process APIs (`interfaces.provides`), contract schemas, and downstream dependents. | Complex multi-module features, architecture reviews, SpecDeltas. |

### CLI Usage:

```bash
# Lightweight anchor context
eidos context -c CNT-EIDOS-CORE --tier anchor

# Default guardrails with invariants, policies, and decisions
eidos context -f src/eidos/cli/main.py --tier guardrails

# Deep architectural context including C4 internal building blocks
eidos context -c CNT-EIDOS-CORE --tier deep

# Slice context from a natural-language task prompt
eidos context -t "rate limiting and token expiration" --tier guardrails --format json
```

---

## Architectural Search & Blast-Radius Analysis

### Full-Text Architectural Search (BM25)
Quickly discover containers, components, contracts, capabilities, and invariants matching concepts, requirements, or keywords:

```bash
eidos index search "FTS5" --limit 5
```

Output:
```text
              Architectural Search Results for 'FTS5' (4 matches)               
┏━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃  Rank ┃ Entity ID      ┃ Type            ┃ Title           ┃ Domain          ┃
┡━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ -2.79 │ EXP-2026-002   │ product/explor… │ Embedded Graph  │ —               │
│ -2.78 │ DLT-2026-011   │ delta/mutation  │ Integrate Graph │ —               │
│ -2.21 │ CNT-EIDOS-CORE │ tech/container  │ Core Engine     │ DOM-SPEC-ENGINE │
│ -0.83 │ CTR-EIDOS-CLI  │ tech/contract   │ CLI Contract    │ —               │
└───────┴────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

How lexical retrieval works (`INV-AGENT-TASK-RETRIEVAL-COVERAGE`):

* **Every entity is searchable**: child invariants (`INV-*`) get their own `specs_fts` row (rule text, parent title, test binding), and embedded `DEC-*` decisions are indexed alongside their owning container or component.
* **Identifier de-compounding**: `camelCase` / `snake_case` identifiers are split on both index and query sides — `ArchitecturalIndex` is indexed as `architectural index` too, and a prompt like `detect_drift` becomes `detect OR drift` instead of an order-sensitive phrase.
* **Column-weighted BM25**: Positional weights `(0, 4, 2, 1, 2, 2)` rank `title` (4) > `jtbd_or_rule` / `keywords` / `symbols` (2) > `body_text` (1), with `0` assigned to the `UNINDEXED` `entity_id` placeholder column.
* **Slice-scoped candidates & Honest Empty Slices**: `eidos context --task` filters sliceable types (containers, components, contracts, capabilities, policies, invariants, domains, design systems) *inside SQL before `LIMIT`*, so historical SpecDeltas and explorations never consume top-k slots. Matched domains / design systems expand to their member containers and capabilities. If an explicit query matches zero entities, Eidos returns an honest empty slice rather than injecting unrelated alphabetical fallbacks.

### Blast-Radius Impact Analysis
Before modifying an interface, container, component, or domain, compute the transitive blast radius using recursive CTE graph traversals:

```bash
eidos index blast-radius CNT-EIDOS-CORE --depth 2
```

This recursively traces all forward and reverse dependencies (`depends_on`, `provides`, `consumes`, `governs`), owning domains, and bound capabilities up to `N` hops away, alerting engineers and AI agents to downstream components that could be impacted.

---

## Polyglot AST Code Indexing & Code Graph

In addition to living specifications (`eidos/*.adoc`), Eidos indexes source code and test suites across polyglot projects using structural Abstract Syntax Trees (AST):

* **6-Language Native AST Engine**: Powered by core dependency `ast-grep-py` (`ast-grep`), pre-bundling Tree-sitter grammars for **Python (`.py`), TypeScript/JavaScript (`.ts`, `.tsx`, `.js`, `.jsx`), Go (`.go`, including `type_spec` and receiver methods), Rust (`.rs`), Java (`.java`), and Kotlin (`.kt`, `.kts`)**.
* **Deterministic Stdlib Fallback (`INV-INDEX-OPTIONAL-AST-FALLBACK`)**: On platforms where native `ast-grep-py` wheels are unavailable, Eidos seamlessly falls back to Python's standard library `ast` and regex extractors with zero runtime crashes (`POL-004`).
* **Persisted Symbol Coordinates (`code_symbols`)**: Extracts classes, structs, enums, interfaces, functions, and methods with exact `line_start`, `line_end`, and signatures, mapping them directly to C4 Level 2 containers (`CNT-*`) and C4 Level 3 components (`CMP-*`) in `eidos/.cache/index.db`.
* **Syntax-Aware Invariant Test Traceability (`POL-002`)**: Binds capability and component invariants (`INV-*`) to both bare test functions (`tests/test_file.py::test_func`) and class-qualified test methods (`tests/test_file.py::TestClass::test_method`) via `test_binding_cache`.

---

## Token-Budgeted AI Repo Map (`eidos index repo-map`)

AI coding agents struggle with whole-codebase awareness: sending thousands of raw code files exceeds LLM context windows, dilutes attention, and explodes cost.

Eidos provides the **Repo Map Engine** (`eidos index repo-map`), which compiles a high-signal, token-budgeted structural outline of the entire codebase:

```bash
# Generate structural repo map with default budget (1500 tokens)
eidos index repo-map

# Or customize token budget:
eidos index repo-map --max-tokens 800

# Output as structured JSON:
eidos index repo-map --format json
```

### High-Density Compression (~98% Token Reduction)
The repo map summarizes files by their declared classes, methods, and functions without internal implementation boilerplate:

```text
# 🗺️ Structural Code Graph (Repo Map)
Budget: ~1500 tokens | Generated by Eidos

## src/eidos/core/index/ast_indexer.py
  class AstSymbol
  class AstGrepIndexer
    def is_available(self) -> bool
    def extract_symbols(self, file_path: Path) -> list[AstSymbol]
    def extract_test_bindings(self, file_path: Path) -> dict[str, list[str]]
    def generate_repo_map(self, root_dir: Path, max_tokens: int) -> str
  class FallbackIndexer
  def get_ast_indexer() -> AstGrepIndexer | FallbackIndexer

## src/eidos/core/index/graph_index.py
  class ArchitecturalIndex
    def sync(self, force: bool) -> SyncStats
    def find_symbol(self, query: str, limit: int) -> list[dict]
    def get_repo_map(self, max_tokens: int) -> str
```

In benchmarks, 50,000+ tokens of raw source code compress into **under 1,000 tokens** (98.18% compression ratio) while retaining full structural navigability for LLM planning.

---

## Fast Symbol Discovery (`eidos index find-symbol`)

Developers and AI agents can query any symbol (class name, function name, API route) to find its exact source location and governing living specification:

```bash
eidos index find-symbol "ArchitecturalIndex"
```

Output:
```text
           Symbol Search Results for 'ArchitecturalIndex' (2 matches)           
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Symbol             ┃ Kind     ┃ File                       ┃ Spec Entity     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ ArchitecturalIndex │ class    │ src/eidos/core/graph_inde… │ CNT-EIDOS-INDEX │
│ get_architectural… │ function │ src/eidos/core/graph_inde… │ CNT-EIDOS-INDEX │
└────────────────────┴──────────┴────────────────────────────┴─────────────────┘
```

---

## Python API Usage

The knowledge graph index can also be integrated directly into custom Python tools or automation scripts:

```python
from eidos.core.index.graph_index import get_architectural_index

# Lazily initializes or loads cached index
index = get_architectural_index()

# 1. Tiered context slice (~2ms)
context = index.get_tiered_context_slice(
    component_id="CMP-EIDOS-CORE",
    tier="guardrails",
)
print(f"Tokens: ~{context.estimated_tokens}")
print(context.to_markdown())

# 2. BM25 hybrid full-text search
results = index.query_fts("SQLite knowledge graph", limit=5)
for r in results:
    print(f"[{r.entity_type}] {r.entity_id}: {r.title} (Score: {r.bm25_rank:.2f})")

# 3. Transitive blast-radius analysis
neighbors = index.get_k_hop_neighborhood("CMP-EIDOS-CORE", max_hops=2)
for hop, entity in neighbors:
    print(f"Hop {hop}: {entity.id} ({entity.type})")

# 4. AI Repo Map generation
repo_map = index.get_repo_map(max_tokens=1500)
print(repo_map)

# 5. Reverse symbol resolution
symbols = index.find_symbol("AstGrepIndexer", limit=5)
for sym in symbols:
    print(f"{sym['symbol_name']} ({sym['symbol_kind']}) -> {sym['entity_id']} in {sym['file_path']}")
```

