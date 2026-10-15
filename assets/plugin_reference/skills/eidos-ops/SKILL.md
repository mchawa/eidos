---
name: eidos-ops
description: Initialize greenfield repositories with Eidos (eidos init), systematically reverse-engineer and extract living specifications from existing brownfield codebases, and serve or build the interactive Eidos Living Web Portal and static documentation site (eidos portal serve, eidos portal build).
---
# Eidos Operations: Bootstrap, Brownfield Extraction & Web Portal (`eidos-ops`)

This skill covers repository-level lifecycle operations in Eidos:
1. **Greenfield Repository Initialization (`eidos init`)**: Bootstrapping a new repository with an Eidos living specification skeleton.
2. **Brownfield Codebase Extraction**: Reverse-engineering an existing codebase into accurate Arc42/C4 living specifications, domains, capabilities, and test-bound invariants.
3. **Living Web Portal & Static Site Operations (`eidos portal serve`, `eidos portal build`)**: Launching the interactive architecture portal and exporting static documentation bundles for CI/CD (GitHub Pages, GitLab Pages, S3).

---

## 1. Greenfield Repository Bootstrapping (`eidos init`)

Use this workflow when setting up Eidos in a brand-new repository or initializing the `eidos/` directory for the first time:

### Step-by-Step Initialization Workflow

```bash
# 1. Scaffold the eidos/ directory skeleton and baseline governance policies (POL-001..POL-004)
eidos init

# 2. Install AI agent plugin integrations (Antigravity, Claude Code, Cursor, Copilot, etc.)
eidos plugin install --target project
# Or install for all 8 supported coding agents:
eidos plugin install --target all

# 3. Author C4 Level 1 System Context (eidos/system.adoc) and Arc42 Master Architecture (eidos/architecture/system-architecture.adoc)

# 4. Scaffold initial product domains, personas, and C4 containers
eidos spec create domain DOM-CORE --title "Core Application Domain"
eidos spec create persona PER-DEVELOPER --title "Application Developer"
eidos spec create container CNT-APP-API --title "Core Application API"

# 5. Build the embedded SQLite + FTS5 + AST knowledge graph index
eidos index sync --force

# 6. Validate all specifications and verify workspace health
eidos validate eidos
eidos doctor
```

---

## 2. Brownfield Reverse-Engineering & Codebase Extraction

When introducing Eidos into an **existing codebase**, never invent speculative architecture. Systematically extract the **actual as-built architecture** by following this 8-phase pipeline:

### Phase 1: Initialize Skeleton & Inspect Workspace Topology
1. Run `eidos init` if `eidos/` does not yet exist.
2. Inspect build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `docker-compose.yml`, `Dockerfile`, CI workflows) and top-level source directories (`src/`, `apps/`, `packages/`, `services/`, `cmd/`, `internal/`).

### Phase 2: Author C4 Level 1 System Context (`eidos/system.adoc`)
* Identify the system's core purpose, external human actors, and third-party external dependencies (databases, auth providers, payment gateways, message brokers).
* Populate `eidos/system.adoc` (`id: SYS-<PROJECT>-001`) and `eidos/architecture/system-architecture.adoc` (Arc42 Sections 1–12).

### Phase 3: Extract C4 Level 2 Containers (`CNT-*`) & Level 3 Components (`CMP-*`)
* Map each deployable service, CLI binary, web app, worker, or library package to a container in `eidos/architecture/containers/<slug>.adoc` via `eidos spec create container CNT-<NAME> --title "..."`.
* Set `package_path` to the exact repository-relative source path(s) (e.g., `"src/billing"` or `["src/api", "src/shared"]`) so `eidos drift` and `eidos context` map every source file accurately.
* Map major internal modules/classes within each container to inline `components:` entries (`id: CMP-*`, `name`, `responsibility`, `source_path`). For complex subsystems, promote them to standalone component specs via `eidos spec component promote <CMP-ID>`.
* Record key as-built architectural trade-offs in the container's `decisions:` (`DEC-*`) frontmatter block.

### Phase 4: Extract Interface Contracts (`CTR-*`)
* Identify public boundaries: REST OpenAPI routers, gRPC `.proto` definitions, GraphQL schemas, CLI command trees, or event schemas.
* Create contract specifications in `eidos/architecture/contracts/<slug>.adoc` (`eidos spec create contract CTR-<NAME> --title "..."`) and link `provides:` / `consumes:` on the corresponding `CNT-*` containers.

### Phase 5: Extract Product Domains (`DOM-*`), Personas (`PER-*`) & Capabilities (`CAP-*`)
* Group business logic into bounded contexts (`eidos/domains/<slug>/domain.adoc`) with clear `ubiquitous_language` glossaries.
* Create actor personas in `eidos/personas/<slug>.adoc`.
* For each user-facing or system feature, create a capability in `eidos/domains/<slug>/capabilities/<cap>.adoc` linking `personas`, `jtbd`, `policies`, `technical.containers`, and `technical.contracts`.

### Phase 6: Extract Frontend Design Systems & Client Flows (If Applicable)
* If the repository includes a frontend or TUI client, extract design tokens into `eidos/design/systems/<slug>/tokens.json` (`DS-*`), reusable UI components (`UIC-*`), client manifest (`CLT-*`), screens (`SCR-*`), and critical user journeys (`FLOW-*`).

### Phase 7: Bind Existing Automated Tests to Capability Invariants (`INV-*`)
* Scan the existing `tests/` suite for critical business rules, security checks, and regression tests.
* Formulate explicit `INV-*` rules in each capability's `technical.invariants` block with `test_binding: "<test_file>::<test_func>"`.
* Add the `INV-*` identifier to the corresponding test function's docstring or comment to satisfy `POL-002` bidirectional traceability.

### Phase 8: Synchronize Index & Verify Zero Drift
```bash
eidos index sync --force
eidos validate eidos
eidos drift --uncommitted
```
Do not consider brownfield extraction complete until `eidos validate eidos` reports **0 errors** across all extracted specifications and test bindings.

---

## 3. Living Web Portal & Static Site Operations (`eidos portal`)

Eidos includes a zero-config, interactive **Living Web Portal** (React 19 + Tailwind v4 + Cytoscape C4 topology graph + W3C Design Tokens Studio + SpecDelta timeline + Invariant coverage matrix).

### 3.1 Serve the Interactive Portal Locally

```bash
# Launch the local FastAPI + SPA portal server (defaults to http://127.0.0.1:3000)
eidos portal serve
# or with custom host/port:
eidos portal serve --port 8420 --host 127.0.0.1

# Open the browser automatically on launch:
eidos portal serve --open
```

### 3.2 Build a Self-Contained Static Portal Bundle

Export the entire living specification graph and interactive SPA into a static HTML/JS/JSON directory (no backend server required) for hosting on GitHub Pages, GitLab Pages, Cloudflare Pages, or S3:

```bash
# Export static portal bundle (defaults to ./site or custom --out directory)
eidos portal build --out dist/portal
# or:
eidos portal build --out site/portal
```

### 3.3 Co-Hosting with Zensical / MkDocs Documentation Sites

When a repository uses Zensical (`zensical.toml`) or MkDocs to publish narrative documentation (`docs/`) alongside the Eidos Living Web Portal:

```bash
# 1. Build the static documentation site into site/
zensical build

# 2. Export the interactive Eidos Living Portal into site/portal/
eidos portal build --out site/portal
```
The compiled `site/` directory can then be deployed directly by `.github/workflows/docs.yml` to GitHub Pages.
