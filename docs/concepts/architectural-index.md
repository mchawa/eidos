# Architectural Knowledge Graph & Index

Eidos features a built-in, zero-dependency **Architectural Knowledge Graph Index** powered by an embedded SQLite database (`.eidos/cache/index.db`) with full-text search (FTS5) and recursive graph traversals (Common Table Expressions).

The index enables sub-3ms architectural queries, intelligent full-text specification discovery, transitive blast-radius analysis, and token-budgeted 3-tier context slicing for AI coding agents.

```
┌────────────────────────────────────────────────────────┐
│           Living Specifications (eidos/*.adoc)         │
└───────────────────────────┬────────────────────────────┘
                            │  Incremental Dirty Check (SHA-256, ~1.5ms)
                            ▼
┌────────────────────────────────────────────────────────┐
│       Embedded SQLite Knowledge Graph (.eidos/cache)    │
│  ├── entities (70+ nodes: CMP, CTR, CAP, DOM, POL, …)  │
│  ├── edges (depends_on, provides, governs, asserts, …) │
│  ├── symbol_mappings (reverse AST code-to-spec maps)   │
│  └── specs_fts (SQLite FTS5 virtual table with BM25)   │
└───────────────┬────────────────────────┬───────────────┘
                │                        │
       CLI Commands               MCP Server & Skills
  eidos context --tier anchor      eidos_search_architecture
  eidos index status               eidos_get_blast_radius
  eidos index search               eidos_get_task_context (tier)
  eidos index blast-radius         eidos_index_status / sync
```

---

## Zero External Dependencies

The architectural index requires **zero external dependencies**, zero background daemons, and zero third-party vector databases.

* Built entirely on Python's standard library **`sqlite3`**, which natively bundles SQLite 3.x with FTS5 and recursive CTEs.
* Stores cache data inside a single, self-contained file: `.eidos/cache/index.db`.
* The `.eidos/cache/` directory is automatically added to `.gitignore`, keeping Git repositories pristine and lightweight.

---

## Automatic Initialization & Lazy Synchronization

You never need to remember to run a build or initialization command.

### 1. Automatic On-Demand Bootstrapping
The first time any command requiring architectural context is invoked (`eidos context`, `eidos index`, MCP server tools, or agent skills), Eidos automatically:
1. Creates `.eidos/cache/` if it does not exist.
2. Initializes the SQLite schema (`entities`, `edges`, `symbol_mappings`, `indexed_files`, `specs_fts`).
3. Indexes all living specifications and builds initial reverse symbol mappings in ~100ms.

### 2. Zero-Drift Freshness (`_ensure_synced`)
Before answering any query, search, or context extraction request, the index runs a lazy freshness check (`is_dirty()`):
* Compares file modification timestamps (`mtime`) and SHA-256 hashes against `indexed_files`.
* Executes in **~1.5ms** on clean working trees across 50+ living specs.
* If any `.adoc` file has been edited, added, or deleted, only the modified specifications are incrementally re-indexed.
* This guarantees that AI agents and CLI users always operate against real-time specifications with **zero drift**.

---

## Manual Index Management

While synchronization is completely automatic, developers and CI/CD pipelines can inspect or manage the index directly via the CLI:

### Inspect Index Status
View database size, health, total entities, edges, indexed files, and check latency:

```bash
eidos index status
```

Output:
```text
╭───────────────── Eidos Architectural Knowledge Graph Index ─────────────────╮
│       Database Path:  /path/to/project/.eidos/cache/index.db                │
│      Schema Version:  1.0.0                                                 │
│          Cache Size:  504.0 KB                                              │
│              Status:  SYNCED (Clean) (1.52ms check)                         │
│      Total Entities:  73                                                    │
│         Total Edges:  69                                                    │
│       Indexed Files:  56                                                    │
│ AST Symbol Mappings:  75                                                    │
╰─────────────────────────────────────────────────────────────────────────────╯
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
```

