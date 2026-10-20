---
name: eidos-ops
description: Initialize greenfield repositories with Eidos (eidos init), systematically reverse-engineer and extract living specifications from existing brownfield codebases, and serve or build the interactive Eidos Living Web Portal and static documentation site (eidos portal serve, eidos portal build).
---
# Eidos Operations: Bootstrap, Brownfield Extraction & Web Portal (`eidos-ops`)

This skill covers repository-level lifecycle operations in Eidos:
1. **Greenfield Repository Initialization (`eidos init`)**: Bootstrapping a new repository with the full Eidos specification skeleton or the progressive Eidos Lite profile (`--lite`).
2. **Brownfield Codebase Extraction**: Reverse-engineering an existing codebase into accurate Arc42/C4 living specifications, domains, capabilities, and test-bound invariants.
3. **Living Web Portal & Static Site Operations (`eidos portal serve`, `eidos portal build`)**: Launching the interactive architecture portal locally or exporting a static bundle for CI/CD hosting.

---

## 1. Greenfield Repository Bootstrapping (`eidos init`)

```bash
# 1a. Full profile: scaffold eidos/ directory skeleton and baseline governance policies (POL-001..POL-004)
eidos init --name my-system --title "My System"

# 1b. Or Eidos Lite profile: minimal baseline (system.adoc, system-architecture.adoc, containers/, capabilities/)
eidos init --lite --name my-system --title "My System"

# 2. Install AI agent plugin integrations
eidos plugin install
# Or install across all supported coding agents (Antigravity, Claude, Cursor, Copilot, Cline, OpenCode, Codex, Aider):
eidos plugin install all

# 3. Populate C4 Level 1 System Context (eidos/system.adoc) and Arc42 Master Architecture (eidos/architecture/system-architecture.adoc)

# 4. Scaffold initial product domains, personas, and C4 containers (consult 'eidos-spec' or run 'eidos spec schema <type>' for YAML schemas)
eidos spec schema
eidos spec create domain DOM-CORE --title "Core Application Domain"
eidos spec create persona PER-DEVELOPER --title "Application Developer"
eidos spec create container CNT-APP-API --title "Core Application API"

# 5. Synchronize the knowledge graph index and verify workspace health
eidos index sync --force
eidos validate eidos --json
eidos doctor --json
```

---

## 2. Brownfield Reverse-Engineering & Codebase Extraction

When introducing Eidos into an **existing codebase**, never invent speculative architecture. Systematically extract the **actual as-built architecture** across these 8 phases:

1. **Initialize & Inspect Topology**: Run `eidos init` if `eidos/` does not exist. Inspect build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `docker-compose.yml`, CI workflows) and top-level source directories.
2. **Author C4 Level 1 & Arc42 Master Architecture**: Populate `eidos/system.adoc` (`SYS-*`) and `eidos/architecture/system-architecture.adoc` (`ARCH-*`, registering C4 Level 1 actors and external systems in `c4_context`).
3. **Extract C4 Level 2 Containers (`CNT-*`) & Level 3 Components (`CMP-*`)**:
   * Create a container in `eidos/architecture/containers/<slug>.adoc` for each deployable service, CLI binary, web app, worker, or library package.
   * Set `source_path` to the exact repo-relative source path(s) so `eidos drift` and `eidos context` map every source file accurately.
   * Map major internal modules/classes to inline `components:` entries (`id: CMP-*`, `title`, `responsibility`, `source_path`), or promote complex subsystems via `eidos spec component promote <CMP-ID>`.
   * Record non-obvious as-built architectural trade-offs in `decisions:` (`DEC-*`).
4. **Extract Interface Contracts (`CTR-*`)**: Create contract specs in `eidos/architecture/contracts/<slug>.adoc` for REST/OpenAPI, gRPC/Protobuf, GraphQL, or CLI boundaries (`specification_file`), and link `provides:` / `consumes:` on `CNT-*`.
5. **Extract Product Domains (`DOM-*`), Personas (`PER-*`) & Capabilities (`CAP-*`)**: Group business logic into bounded contexts (`eidos/domains/<slug>/domain.adoc`), create actor personas (`eidos/personas/<slug>.adoc`), and author capabilities (`eidos/domains/<slug>/capabilities/<cap>.adoc`) linking `business.personas`, `business.policies`, and `technical.containers` in YAML frontmatter (authoring Jobs-to-be-Done in the AsciiDoc prose body).
6. **Extract Frontend Design Systems & Client Flows (If Applicable)**: For web/mobile/desktop/TUI clients, scaffold `DS-*` (`tokens.json`), `UIC-*`, `CLT-*`, `SCR-*`, and `FLOW-*` under `eidos/design/`.
7. **Bind Existing Automated Tests to Capability Invariants (`INV-*`)**: Scan `tests/` for critical business and security rules, declare `INV-*` entries with `test_binding: "<test_file>::<test_func>"` on the owning capability, and add the `INV-*` ID to each bound test's docstring or comment (`POL-002`).
8. **Synchronize & Verify Zero Drift**:
   ```bash
   eidos index sync --force
   eidos validate eidos --json
   eidos drift --uncommitted --json
   ```
   Extraction is complete only when `eidos validate eidos` and `eidos drift --uncommitted` both exit with `0`.

---

## 3. Living Web Portal & Static Site Operations (`eidos portal`)

### 3.1 Serve the Interactive Portal Locally

```bash
# Serve the interactive portal locally (defaults to http://127.0.0.1:3000)
eidos portal serve

# Custom host/port or auto-open browser:
eidos portal serve --host 127.0.0.1 --port 8420 --open
```

### 3.2 Build a Self-Contained Static Portal Bundle

Export the living specification catalog and interactive topology viewer into a static directory (no backend required) for GitHub Pages, GitLab Pages, Cloudflare Pages, or S3:

```bash
# Export static portal bundle (defaults to ./site)
eidos portal build --out dist/portal

# Or export into a subdirectory alongside an existing static documentation site:
eidos portal build --out site/portal
```
