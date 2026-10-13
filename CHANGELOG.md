# Changelog

All notable changes to Eidos are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every major feature and architectural mutation is directly backed by an immutable
**SpecDelta (`DLT-*`)** stored in the living specifications catalog.

---

## [0.8.0] - 2026-10-13

### Added
- **Automated Living Architecture Changelog Generation Engine** ([DLT-2026-022](eidos/deltas/dlt-2026-022.adoc)): Zero-dependency automated changelog generation engine compiling applied SpecDeltas (`DLT-*`) and Git release tags into a standard Keep a Changelog compliant ledger (`CHANGELOG.md` and `docs/changelog.md`), establishing bidirectional traceability between release notes, git tags, and living specifications.
- **Dedicated Drift Gate Hook and Scoped Spec Doc Linter**: Pre-tool and post-invocation lifecycle hooks for autonomous coding agents ensuring zero documentation drift and YAML frontmatter validation.

### Changed
- **Polyglot AST Symbol Indexing, AI Code Graph, and Granular Invariant Traceability via ast-grep** ([DLT-2026-021](eidos/deltas/dlt-2026-021.adoc)): Integrate ast-grep (`ast-grep-py`) to empower the Eidos Architectural Knowledge Graph indexer with deep polyglot AST symbol mapping (classes, functions, methods, interface signatures), a high-speed structural AI Code Graph / Repo Map engine, and granular test-function invariant traceability (`(test_fn) -[asserts]-> (INV-*)`).

## [0.7.0] - 2026-10-13

### Changed
- **Hierarchical C4 Level 2 Container Topology and Inter-Container Connection Architecture** ([DLT-2026-020](eidos/deltas/dlt-2026-020.adoc)): Enhance the C4 Level 2 Container Diagram in the Living Web Portal to visually represent the system boundary, tiered architectural hierarchy (presentation, extensibility, core engine, and persistence layers), and explicit inter-container dependency connections and contracts.

## [0.6.0] - 2026-10-13

### Added
- Enhance ui ergonomics, dark theme contrast, reader layout, and introduce eidos brand mark

### Changed
- **Living Web Portal Reading-Friendly Design Revamp** ([DLT-2026-019](eidos/deltas/dlt-2026-019.adoc)): Revamp the Living Web Portal's typography, document layout, and reading experience to transform dense technical specifications into an effortless, reading-friendly documentation surface.

## [0.5.0] - 2026-10-12

### Added
- Change inspector to centered popup modal with spacious max-w-5xl width
- Render formatted living spec bodies with asciidocrenderer and add multi-level hierarchy to living catalog
- Add prominent pol-* system policies section, make domains, capabilities, policies, and personas interactive and clickable with inspection drawers
- Enforce centralized c4 l3 component manifests in eidos/architecture/components/
- Enrich design systems page with living w3c dtcg tokens, ui components, screen state machines, and interactive flows
- Add intelligent auto-fit zoom, pan-and-drag, and expandable viewport to c4 mermaid diagrams
- Add clickable c4 navigation, hierarchical catalog tree, and vite dev server
- Prototype react 19, tailwind v4, md3 & singlefile bundle (exp-2026-003)
- Promote architectural knowledge graph to standalone c4 container cmp-eidos-index
- Synchronize system architecture and domain capabilities with knowledge graph engine
- Wire architecturalindex universally across context, components, spec listing, drift, and skills
- Wire architecturalindex into cli, mcp, and agent skills with auto-sync

### Changed
- **Integrate Embedded Architectural Knowledge Graph & Hybrid Context Retrieval Index** ([DLT-2026-011](eidos/deltas/dlt-2026-011.adoc)): Graduated from validated feasibility exploration EXP-2026-002 to transition Eidos context slicing from linear filesystem scanning to an embedded SQLite knowledge graph with FTS5 lexical retrieval and tiered token-budgeted compression.
- **Dynamic Clickable Hierarchical C4 Architecture Explorer (L1-L3)** ([DLT-2026-012](eidos/deltas/dlt-2026-012.adoc)): Current C4 diagrams in the Eidos Web Portal and static site export are static, vertically stacked Level 1 and Level 2 views without interactive drill-down navigation or Level 3 (Component Whitebox) visualization.
- **Remove Deprecated PRCheckResult Backwards Compatibility Alias** ([DLT-2026-013](eidos/deltas/dlt-2026-013.adoc)): Eliminate redundant PRCheckResult backwards compatibility alias from eidos.vcs and drift modules, maintaining dead-code hygiene and streamlining the public VCS API surface.
- **Formalize First-Principles C4 Architecture: Level 2 Containers (CNT-*) and Level 3 Components (CMP-*)** ([DLT-2026-014](eidos/deltas/dlt-2026-014.adoc)): The historical conflation of C4 Level 2 Containers and Level 3 Components under the overloaded 'tech/component' document type and 'CMP-*' prefix causes recurring cognitive dissonance.
- **Centralized Hybrid C4 Level 3 Component Manifests** ([DLT-2026-015](eidos/deltas/dlt-2026-015.adoc)): Following the conclusion of EXP-2026-004, introduce first-class support for dedicated C4 Level 3 Component specifications (type: tech/component, CMP-*).
- **Deprecate and Remove MCP Server in Favor of Direct CLI and Progressive Agent Skills** ([DLT-2026-016](eidos/deltas/dlt-2026-016.adoc)): Sunsets the stdio Model Context Protocol (MCP) server (CNT-EIDOS-MCP) and its interface contract (CTR-EIDOS-MCP).
- **SpecDelta Deletion and Deprecation Audit Engine (eidos delta audit)** ([DLT-2026-017](eidos/deltas/dlt-2026-017.adoc)): Provides a context-aware reverse-dependency blast-radius audit engine ('eidos delta audit') to verify that entities removed or deprecated by a SpecDelta do not leave unacknowledged dangling references or active leaks in living specifications, code generators, documentation, or contracts.
- **Modernize Living Web Portal with React 19, Tailwind CSS v4, and Material Design 3 (MD3)** ([DLT-2026-018](eidos/deltas/dlt-2026-018.adoc)): Graduated from validated technical spike EXP-2026-003.

### Fixed
- Fallback to portal_html_template in test when dist not present and ignore node_modules in root gitignore
- Default c4 to context view, fix level 2 container and client flow auto-fit zoom, add flows layout toggle
- Optimize dirty checking and batch queries in get_tiered_context_slice
- Normalize auto-fit zoom to prevent over-zooming on small diagrams and remove drill-down helper pill
- Prevent double-scaling by enforcing natural 1:1 svg dimensions and bold readable default zoom
- Resolve zoom reset loop in mermaiddiagram, add wheel zoom and height expansion, and improve default zoom readability
- Resolve dark theme switching and w3c token color mappings

## [0.4.0] - 2026-10-12

### Added
- Release v0.4.0 with context slicing, persona matrix, and bounded context domains
- Implement multi-client architecture (clt-*) and interaction flows (flow-*)
- Refine tree-grid with svg rotating chevrons, sleek search bar, and streamlined columns
- Implement unified hierarchical tree-grid for living specs catalog
- Implement interactive master-detail tree navigation for living specs catalog
- Make sidebar collapsible on desktop and replace home icon with svg house
- Revamp living portal with responsive sidebar, home dashboard, and business-first ia
- Add pol-003 and inv-docs-synchronization enforcing continuous documentation synchronization with automated test suite
- Register docs system as first-class component cmp-eidos-docs

### Changed
- **Revamp Eidos Personas and Core Domains Architecture** ([DLT-2026-010](eidos/deltas/dlt-2026-010.adoc)): Restructure Eidos personas and bounded context domains to align with its true agent-agnostic ecosystem, multi-client experience, and Git-native lifecycle architecture.
- Remove themes and supported_themes in favor of color_modes
- Clarify design_system vs supported_themes distinction

### Fixed
- Format screen state machines as compact horizontal cards in a responsive grid
- Enlarge c4 diagrams with padded nodes, 16px font, and zoom controls
- Resolve c4 level 1 context mermaid syntax error and tab re-render

## [0.3.0] - 2026-10-12

### Added
- Add eidos-product, eidos-explore, eidos-review, and eidos-portal skills
- Replace check-pr with eidos drift, add eidos_detect_drift ai tool, and author eidos-drift skill
- Implement eidos check-pr, eidos reconcile, and dual workflow governance

## [0.2.0] - 2026-10-12

### Added
- Configure dynamic package versioning from git tags using hatch-vcs
- Add python 3.15 to ci testing matrix, release workflow, and documentation

### Fixed
- Track .agents/plugins/eidos and make parity test robust to uninstalled environments

## [0.1.0] - 2026-10-11

### Added
- Add docker-in-docker, configure python 3.15, and remove just
- Add uv and ty toolchain support
- Add ubuntu 26.04 development container
- Isolate gitignore inside eidos/.gitignore to remove dependency on host project
- Add 'eidos-extract' skill for reverse-engineering living specs from codebases
- Add living spec and specdelta lifecycle commands with multi-skill plugin expansion
- Add semver 2.0.0 versioning, inspection, and update lifecycle
- Add 'eidos plugin uninstall' and 'eidos plugin remove' commands
- Restrict installer to project-scoped installation
- Add agent-plugins.org v1.0.0 conformant plugin, mcp server, and cli installer
- Formalize arc42 and c4 model alignment in architecture and component schemas
- Add architecture, component, and contract schemas supporting dual centralized and co-located layouts
- Separate python engine, bundled schemas, and living specifications under eidos/
- Initialize eidos product specification architecture and schemas

### Changed
- **CLI Spec Document Lifecycle Management and Plugin Multi-Skill Expansion** ([DLT-2026-001](eidos/deltas/dlt-2026-001.adoc)): Expand the CLI with commands to create, list, and delete living specifications and manage SpecDelta lifecycles, and expand the Agent Plugin with specialized skills.
- **Relocate Schemas and Plugin Reference to Assets Directory** ([DLT-2026-002](eidos/deltas/dlt-2026-002.adoc)): Relocate non-code JSON/YAML schemas and canonical plugin reference assets out of the Python source package directory (src/eidos) into a dedicated top-level assets/ directory, resolving them dynamically and bundling via Hatchling force-include.
- **Flat SpecDelta Storage with Gitignored Lifecycle Symlink Views** ([DLT-2026-003](eidos/deltas/dlt-2026-003.adoc)): Store SpecDeltas at permanent flat file paths under eidos/deltas/ and manage browseable lifecycle views via gitignored symlinks in eidos/deltas/by-status/.
- **Repository Initialization Engine and eidos-init Agent Skill** ([DLT-2026-004](eidos/deltas/dlt-2026-004.adoc)): Provide eidos init command, eidos_init_repo MCP tool, and eidos-init agent skill to bootstrap living specification workspaces.
- **Multi-Design System Engine, W3C Tokens, and Product Capability Linkage** ([DLT-2026-005](eidos/deltas/dlt-2026-005.adoc)): Enable multi-tenant/multi-brand design systems with W3C tokens, UI component contracts, screen specs, and bidirectional linking to product capabilities.
- **Solution Architecture Synchronization with Multi-Design Systems, MCP Contract, and Lifecycle Scaffolding** ([DLT-2026-006](eidos/deltas/dlt-2026-006.adoc)): Synchronize CMP-EIDOS-ENGINE and ARCH-EIDOS-001 with current engine capabilities including MCP stdio contract, multi-design systems, repository initialization, and plugin validation.
- **Architectural Decomposition of Monolithic Engine into C4 Level 2 Containers** ([DLT-2026-007](eidos/deltas/dlt-2026-007.adoc)): Decompose monolithic CMP-EIDOS-ENGINE into 4 distinct C4 containers: CMP-EIDOS-CORE, CMP-EIDOS-CLI, CMP-EIDOS-MCP, and CMP-EIDOS-PLUGIN.
- **AI Plugin Lifecycle Hooks for Automated Documentation Linting** ([DLT-2026-008](eidos/deltas/dlt-2026-008.adoc)): Introduce lifecycle hooks in the AI agent plugin to enforce syntax and schema validation of living documentation on edit and task completion.
- **Eidos Living Web Portal & Interactive Architecture Visualizer** ([DLT-2026-009](eidos/deltas/dlt-2026-009.adoc)): Provide an interactive web portal (eidos serve and eidos build-site) for non-IDE stakeholders to visualize C4 architecture, living specs, multi-design systems, and SpecDelta Kanban boards.
- Move vs code settings to .vscode/settings.json and clean devcontainer
- Remove claude support from configuration and post-create
- Streamline configuration and post-create setup for eidos
- Use ubuntu:26.04 image directly without dockerfile
- Relocate schemas and plugin reference out of src/ into assets/
- Remove artificial restrictions and closed enums across all v1 schemas
- Remove rigid required fields from cross_cutting_concepts to align with arc42 open catalog model
- Organize v1 schemas into product/, tech/, and delta/ subdirectories
- Make archetypes open and extensible without closed enum restrictions
- Change archetype from single enum to composite archetypes array

### Fixed
- Resolve all ruff issues in lint_docs.py and hooks.json
- Use mcp_config.json exclusively and remove redundant mcp.json
- Unify project and workspace installation target under .agents/plugins/eidos
- Update antigravity workspace plugin path to .agents/plugins/eidos
