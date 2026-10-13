# Architectural Knowledge Graph & Index

Eidos features a built-in, zero-dependency **Architectural Knowledge Graph Index** powered by an embedded SQLite database (`eidos/.cache/index.db`) with full-text search (FTS5) and recursive graph traversals (Common Table Expressions).

The index enables sub-3ms architectural queries, intelligent full-text specification discovery, transitive blast-radius analysis, and token-budgeted 3-tier context slicing for AI coding agents.

```
┌────────────────────────────────────────────────────────┐
│           Living Specifications (eidos/*.adoc)         │
└───────────────────────────┬────────────────────────────┘
                            │  Incremental Dirty Check (SHA-256, ~1.5ms)
                            ▼
┌────────────────────────────────────────────────────────┐
│       Embedded SQLite Knowledge Graph (eidos/.cache)    │
│  ├── entities (70+ nodes: CMP, CTR, CAP, DOM, POL, …)  │
│  ├── edges (depends_on, provides, governs, asserts, …) │
│  ├── symbol_mappings (360+ AST code-to-spec maps)      │
│  └── specs_fts (SQLite FTS5 virtual table with BM25)   │
└───────────────┬────────────────────────┬───────────────┘
                │                        │
       CLI Commands               Agent Skills
  eidos repo-map                   eidos-context
  eidos find-symbol                eidos-delta
  eidos context --tier anchor      eidos-review
  eidos index status               eidos-drift
  eidos index search
  eidos index blast-radius
```

---

## Zero External Dependencies

The architectural index requires **zero external dependencies**, zero background daemons, and zero third-party vector databases.

* Built entirely on Python's standard library **`sqlite3`**, which natively bundles SQLite 3.x with FTS5 and recursive CTEs.
* Stores cache data inside a single, self-contained file: `eidos/.cache/index.db`.
* The `eidos/.cache/` directory is automatically added to `.gitignore`, keeping Git repositories pristine and lightweight.

---

## Automatic Initialization & Lazy Synchronization

You never need to remember to run a build or initialization command.

### 1. Automatic On-Demand Bootstrapping
The first time any command requiring architectural context is invoked (`eidos context`, `eidos index`, or agent skills), Eidos automatically:
1. Creates `eidos/.cache/` if it does not exist.
2. Initializes the SQLite schema (`entities`, `edges`, `symbol_mappings`, `indexed_files`, `specs_fts`).
3. Indexes all living specifications and builds initial reverse symbol mappings in ~100ms.

### 2. Zero-Drift Freshness (`_ensure_synced`)
Before answering any query, search, or context extraction request, the index runs a lazy freshness check (`is_dirty()`):
* Compares file modification timestamps (`mtime`) and SHA-256 hashes against `indexed_files`.
* Executes in **<5ms** on clean working trees across living specifications, polyglot source code files, and automated test suites.
* If any `.adoc` spec, source file, or test file has been edited, added, or deleted, only the modified files are incrementally re-indexed.
* This guarantees that AI agents and CLI users always operate against real-time specifications and code with **zero drift**.

---

## Manual Index Management

While synchronization is completely automatic, developers and CI/CD pipelines can inspect or manage the index directly via the CLI:

### Inspect Index Status & AST Telemetry
View database size, health, total entities, edges, indexed files, AST parser engine telemetry, symbol breakdown by syntax kind, and test invariant assertions:

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
│       Schema Version:  1.0.0                                                 │
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

When AI agents work on software engineering tasks, providing entire specification files consumes excessive context window tokens and dilutes model attention. Eidos introduces a **3-tier compression model**:

| Tier | Flag / Param | Typical Size | Latency | Content Included | Recommended Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1: Anchor** | `--tier anchor` | **~140 tokens** | ~1.5ms | Component ID, container type, runtime, package path, bound domain, direct contract signatures. | Fast, single-file edits, routine bug fixes, syntax changes. |
| **Tier 2: Guardrails** *(Default)* | `--tier guardrails` | **~260–890 tokens** | ~1.9ms | Anchor content + domain capabilities (`CAP-*`), active invariants (`INV-*`) with test bindings, and governing policies (`POL-*`). | Standard feature development, refactorings, policy verification. |
| **Tier 3: Deep** | `--tier deep` | **~500–1500 tokens** | ~2.1ms | Guardrails content + C4 Level 3 internal modules (`c4_internal_components`), contract schemas, and full invariant descriptions. | Complex multi-module features, architecture reviews, SpecDeltas. |

### CLI Usage:

```bash
# Lightweight anchor context
eidos context -c CMP-EIDOS-CORE --tier anchor

# Default guardrails with invariants and policies
eidos context -f src/eidos/cli/main.py --tier guardrails

# Deep architectural context including C4 internal building blocks
eidos context -c CMP-EIDOS-CORE --tier deep
```

---

## Architectural Search & Blast-Radius Analysis

### Full-Text Architectural Search (BM25)
Quickly discover components, contracts, capabilities, and invariants matching concepts, requirements, or keywords:

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
│ -2.21 │ CMP-EIDOS-CORE │ tech/component  │ Core Engine     │ DOM-SPEC-ENGINE │
│ -0.83 │ CTR-EIDOS-CLI  │ tech/contract   │ CLI Contract    │ —               │
└───────┴────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

### Blast-Radius Impact Analysis
Before modifying an interface, component, or domain, compute the transitive blast radius using recursive graph traversals:

```bash
eidos index blast-radius CMP-EIDOS-CORE --hops 2
```

This recursively traces all forward dependencies (`depends_on`, `provides`), owning domains, and bound capabilities up to `N` hops away, alerting engineers and AI agents to downstream components that could be impacted.

---

## Polyglot AST Code Indexing & Code Graph

In addition to living specifications (`eidos/*.adoc`), Eidos indexes source code files across polyglot projects using structural Abstract Syntax Trees (AST):

* **Multi-Language AST Engine**: Powered by `ast-grep` (`ast-grep-py`), pre-bundling Tree-sitter grammars for Python, TypeScript, JavaScript, Go, and Rust in a single lightweight binary wheel.
* **Deterministic Fallback (`INV-INDEX-OPTIONAL-AST-FALLBACK`)**: When `ast-grep-py` is not installed, Eidos seamlessly falls back to Python's standard library `ast` and regex pattern matching. Zero mandatory dependencies, zero runtime crashes (`POL-004`).
* **360+ AST Symbol Mappings**: Extracts classes, standalone functions, class methods, and API routes, mapping them directly to C4 Level 2 containers (`CNT-*`) and C4 Level 3 components (`CMP-*`) in `.eidos/cache/index.db`.
* **Syntax-Aware Invariant Test Traceability (`POL-002`)**: Binds capability invariants (`INV-*`) and policies (`POL-*`) to specific test function signatures (e.g. `tests/test_plugin.py::test_doc_lint_hook_stop_event`), eliminating false positives in drift detection.

---

## Token-Budgeted AI Repo Map (`eidos repo-map`)

AI coding agents struggle with whole-codebase awareness: sending thousands of raw code files exceeds LLM context windows, dilutes attention, and explodes cost.

Eidos provides the **Repo Map Engine** (`eidos repo-map` or `eidos index repo-map`), which compiles a high-signal, token-budgeted structural outline of the entire codebase:

```bash
# Generate structural repo map with default budget (1500 tokens)
eidos repo-map

# Or customize token budget:
eidos repo-map --max-tokens 800

# Output as structured JSON:
eidos repo-map --format json
```

### High-Density Compression (~98% Token Reduction)
The repo map summarizes files by their declared classes, methods, and functions without internal implementation boilerplate:

```text
# 🗺️ Structural Code Graph (Repo Map)
Budget: ~1500 tokens | Generated by Eidos

## src/eidos/core/ast_indexer.py
  class AstSymbol
  class AstGrepIndexer
    def is_available(self) -> bool
    def extract_symbols(self, file_path: Path) -> list[AstSymbol]
    def extract_test_bindings(self, file_path: Path) -> dict[str, list[str]]
    def generate_repo_map(self, root_dir: Path, max_tokens: int) -> str
  class FallbackIndexer
  def get_ast_indexer() -> AstGrepIndexer | FallbackIndexer

## src/eidos/core/graph_index.py
  class ArchitecturalIndex
    def sync(self, force: bool) -> SyncStats
    def find_symbol(self, query: str, limit: int) -> list[dict]
    def get_repo_map(self, max_tokens: int) -> str
```

In benchmarks, 50,000+ tokens of raw source code compress into **under 1,000 tokens** (98.18% compression ratio) while retaining full structural navigability for LLM planning.

---

## Fast Symbol Discovery (`eidos find-symbol`)

Developers and AI agents can query any symbol (class name, function name, API route) to find its exact source location and governing living specification:

```bash
eidos find-symbol "ArchitecturalIndex"
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
from eidos.core.graph_index import get_architectural_index

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

