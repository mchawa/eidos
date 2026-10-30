---
name: eidos-ops
description: Initialize greenfield repositories with Eidos (eidos init), systematically reverse-engineer and extract living specifications from existing brownfield codebases using AST code graph intelligence (eidos index repo-map, eidos index find-symbol, eidos index search, eidos index status, eidos index semantic, eidos context), and serve or build the interactive Eidos Living Web Portal and static documentation site (eidos portal serve, eidos portal build).
---
# Eidos Operations: Bootstrap, Brownfield Extraction, Index & Web Portal (`eidos-ops`)

This skill covers repository-level lifecycle operations in Eidos:
1. **Greenfield Repository Initialization (`eidos init`)**: Bootstrapping a new repository with the Eidos specification skeleton.
2. **Brownfield Codebase Extraction**: Reverse-engineering an existing codebase into accurate Arc42/C4 living specifications, domains, capabilities, and test-bound invariants.
3. **Knowledge Graph & Opt-In Semantic Index Operations (`eidos index`)**: Inspecting index telemetry, force-syncing the SQLite graph, and provisioning/syncing local Harrier ONNX semantic embeddings.
4. **Living Web Portal & Static Site Operations (`eidos portal serve`, `eidos portal build`)**: Launching the interactive architecture portal locally or exporting a static bundle for CI/CD hosting.

---

## 1. Greenfield Repository Bootstrapping (`eidos init`)

```bash
# 1. Scaffold 12-directory eidos/ skeleton, eidos/.gitignore, and root eidos/system.adoc
eidos init --title "My System"

# 2. Scaffold Arc42 Master Architecture, initial product domains, personas, and C4 containers
#    (consult 'eidos-spec' or run 'eidos spec schema <type>' for YAML schemas)
eidos spec schema
eidos spec create architecture ARCH-CORE-001 --title "My System Architecture"
eidos spec create domain DOM-CORE --title "Core Application Domain"
eidos spec create persona PER-DEVELOPER --title "Application Developer"
eidos spec create container CNT-APP-API --title "Core Application API"

# 3. Populate C4 Level 1 System Context (eidos/system.adoc) and Arc42 Master Architecture (eidos/architecture/system-architecture.adoc)

# 4. Synchronize the knowledge graph index and verify workspace health
eidos index sync --force
eidos validate eidos --json
eidos doctor --json
```

---

## 2. Brownfield Reverse-Engineering & Codebase Extraction

When introducing Eidos into an **existing codebase**, never invent speculative architecture. Systematically extract the **actual as-built architecture** across these 8 phases:

1. **Initialize & Map AST Codebase Topology**: Run `eidos init` if `eidos/` does not exist. Inspect build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `docker-compose.yml`, CI workflows) and top-level source directories. As soon as initial `CNT-*` containers and `source_path` bindings are created, run `eidos index sync --force` and use `eidos index repo-map --max-tokens 2000` and `eidos index find-symbol <Name> --json` instead of blind directory walks.
2. **Author C4 Level 1 & Arc42 Master Architecture**: Populate `eidos/system.adoc` (`SYS-*`) and `eidos/architecture/system-architecture.adoc` (`ARCH-*`, registering C4 Level 1 actors and external systems in `c4_context`).
3. **Extract C4 Level 2 Containers (`CNT-*`) & Level 3 Components (`CMP-*`)**:
   * Create a container in `eidos/architecture/containers/<slug>.adoc` for each deployable service, CLI binary, web app, worker, or library package.
   * Set `source_path` to the exact repo-relative source path(s) so `eidos drift`, `eidos context`, and `eidos index repo-map` map every source file accurately.
   * Run `eidos index repo-map --max-tokens 1500` and `eidos index find-symbol <ClassOrFunction> --json` to inspect major internal classes/modules, creating standalone `eidos/architecture/components/<slug>.adoc` specifications (`id: CMP-*`, `title`, `container: CNT-*`, `status`, `source_path` in YAML frontmatter; author `== Responsibility` and `== Public Surface` in the AsciiDoc body) via `eidos spec create component <CMP-ID> --title "..."`.
   * Record non-obvious as-built architectural trade-offs in `decisions:` (`DEC-*`).
4. **Extract Interface Contracts (`CTR-*`)**: Create contract specs in `eidos/architecture/contracts/<slug>.adoc` for REST/OpenAPI, gRPC/Protobuf, GraphQL, or CLI boundaries (`specification_file`), and link `provides:` / `consumes:` on `CNT-*`.
5. **Extract Product Domains (`DOM-*`), Personas (`PER-*`) & Capabilities (`CAP-*`)**: Group business logic into bounded contexts (`eidos/domains/<slug>/domain.adoc`), create actor personas (`eidos/personas/<slug>.adoc`), and author capabilities (`eidos/domains/<slug>/capabilities/<cap>.adoc`) linking `business.personas`, `business.policies`, and `technical.containers` in YAML frontmatter (authoring Jobs-to-be-Done in the AsciiDoc prose body).
6. **Extract Frontend Design Systems & Client Flows (If Applicable)**: For web/mobile/desktop/TUI clients, scaffold `DS-*` (`tokens.json`), `UIC-*`, `CLT-*`, `SCR-*`, and `FLOW-*` under `eidos/design/`.
7. **Bind Existing Automated Tests to Capability Invariants (`INV-*`)**: Use `eidos index find-symbol "test_" --json` to locate test functions in `tests/`, declare `INV-*` entries with `test_binding: "<test_file>::<test_func>"` on the owning capability, and add the `INV-*` ID to each bound test's docstring or comment.
8. **Synchronize, Inspect Graph & Verify Zero Drift**:
   ```bash
   eidos index sync --force
   eidos index status --json
   eidos index repo-map --max-tokens 1500
   eidos index search "core" --json
   eidos context -c CNT-APP-API --tier guardrails --json
   eidos index blast-radius CNT-APP-API --depth 2 --json
   eidos validate eidos --json
   eidos drift --uncommitted --json
   ```
   Extraction is complete only when `eidos validate eidos` and `eidos drift --uncommitted` both exit with `0`.

---

## 3. Knowledge Graph & Opt-In Semantic Index Operations (`eidos index`)

```bash
# Inspect SQLite knowledge graph health, AST parser telemetry, entity/edge counts, and dirty status
eidos index status --json

# Force-rebuild the FTS5 + AST knowledge graph (preserves cached spec_embeddings)
eidos index sync --force

# Optional: Inspect, provision, and synchronize local Harrier-OSS-v1-0.6B ONNX semantic embeddings
# (Requires optional dependency group: pip install "eidos-ai[semantic]")
eidos index semantic status --json
eidos index semantic setup --model quantized
eidos index semantic sync
```

---

## 4. Living Web Portal & Static Site Operations (`eidos portal`)

### 4.1 Serve the Interactive Portal Locally

```bash
# Serve the interactive portal locally (defaults to http://127.0.0.1:3000)
eidos portal serve

# Custom host/port or auto-open browser:
eidos portal serve --host 127.0.0.1 --port 8420 --open
```

### 4.2 Build a Self-Contained Static Portal Bundle

Export the living specification catalog and interactive topology viewer into a static directory (no backend required) for GitHub Pages, GitLab Pages, Cloudflare Pages, or S3:

```bash
# Export static portal bundle (defaults to ./site)
eidos portal build --out dist/portal

# Or export into a subdirectory alongside an existing static documentation site:
eidos portal build --out site/portal
```
