# CLI Command Reference

The `eidos` command-line tool provides everything needed to scaffold, validate, serve, and mutate living specifications.

```bash
eidos [OPTIONS] COMMAND [ARGS]...
```

Commands are semantically organized into six functional panels in CLI help output (`eidos --help`):
1. **Setup & Workspace**: Environment information, versioning, and repository bootstrapping.
2. **Governance & Verification**: Schema validation, linting, drift detection, and living specification reconciliation.
3. **Living Web Portal**: Local interactive web portal preview server and static site compilation.
4. **Living Catalog & Mutations**: Specifications, SpecDeltas, and C4 component lifecycle management.
5. **Knowledge Graph & AI Context**: Subgraph context extraction and SQLite/FTS5 knowledge graph maintenance.
6. **Agent Ecosystem & Plugins**: Multi-agent plugin installation, verification, and updates conforming to `agent-plugins.org`.

---

## 1. Setup & Workspace

### `eidos --version`
Display the installed Eidos engine version.

```bash
eidos --version
# Or using shorthand:
eidos -V
# Or using command form:
eidos version
```

---

### `eidos info`
Display the Eidos engine version, bundled schemas, and platform status.

```bash
eidos info
```

---

### `eidos init`
Initialize a Git repository with an empty Eidos living specification skeleton, tracking empty directories with `.gitkeep` files, self-contained gitignore, and immediate validation. Note that `eidos init` initializes specifications; agent plugins are installed separately via `eidos plugin install`.

```bash
eidos init
# Or initialize at a custom path:
eidos init /path/to/repo
```

Options:
* `PATH`: Target directory to initialize (default: current working directory `.`).
* `--name TEXT`: Custom system identifier.
* `--title TEXT`: Human-readable display title.
* `--force, -f`: Overwrite existing files if present.

---

## 2. Governance & Verification

### `eidos validate`
Validate all AsciiDoc specification documents against their bundled schemas and check bidirectional cross-references.

```bash
eidos validate eidos
```

Options:
* `--path PATH`: Path to the specification directory (default: `eidos`).

---

### `eidos lint`
Lint and validate specification files, catching formatting errors, malformed frontmatter, or broken links before committing.

```bash
eidos lint eidos
```

Options:
* `--path PATH`: Path to the specification directory (default: `eidos`).

---

### `eidos drift`
Detect spec-to-code drift between git branches or uncommitted changes, validate schema conformance, and verify invariant test coverage across both Planned Mode and Direct Mode:

```bash
# Compare current branch against base branch (e.g. origin/main):
eidos drift --base origin/main

# Inspect uncommitted working tree and staged changes:
eidos drift --uncommitted

# Generate a markdown summary comment (ideal for CI step summary or PR comment):
eidos drift --base origin/main --markdown drift-report.md

# Output structured JSON for automation:
eidos drift --json

# Run as non-blocking warning check:
eidos drift --warn-only
```

Options:
* `--base, -b REF`: Base git reference to compare against (defaults to `origin/main` or `main`).
* `--head REF`: Head git reference (defaults to `HEAD`).
* `--uncommitted, -u`: Analyze uncommitted working tree and staged changes instead of branch diff.
* `--markdown, -m FILE`: Path to write the markdown summary comment.
* `--json`: Output raw JSON verification result.
* `--warn-only`: Do not exit with code 1 when drift is detected.

Exit Codes:
* `0`: Specifications and code are synchronized, all schemas valid, invariant tests green.
* `1`: Spec-to-code drift detected or specification validation errors found.

---

### `eidos reconcile`
Analyze recent code changes against the living specification catalog and generate candidate specs to update or tailored AI prompts for synchronization (alias: `eidos sync`):

```bash
# Inspect branch changes and suggest candidate specs to update:
eidos reconcile --base origin/main

# Inspect uncommitted working tree changes:
eidos reconcile --uncommitted

# Generate a structured AI prompt for copy-pasting into chat:
eidos reconcile --prompt

# Output candidate specs as machine-readable JSON:
eidos reconcile --json
```

Options:
* `--base, -b REF`: Base git reference to compare against.
* `--uncommitted, -u`: Analyze uncommitted working tree changes.
* `--prompt`: Output structured markdown AI prompt for copy-pasting into agent chat.
* `--json`: Output raw JSON reconcile suggestions and catalog.

---

## 3. Living Web Portal

### `eidos portal`
Manage the interactive Living Web Portal and documentation site export.

#### `eidos portal serve`
Launch the interactive Eidos Living Web Portal locally with live updates.

```bash
# Using namespaced command:
eidos portal serve --path eidos --port 8080

# Or using top-level shortcut:
eidos serve --path eidos --port 8080
```

Options:
* `--path, -p PATH`: Path to the specifications directory (default: `eidos`).
* `--host, -h TEXT`: Bind host (default: `127.0.0.1`).
* `--port INTEGER`: Port number (default: `3000`).
* `--open`: Open portal in browser automatically.

#### `eidos portal build`
Export the Eidos Living Web Portal as a self-contained, offline-first static site suitable for deployment on GitHub Pages or static web servers (alias: `eidos portal build-site`).

```bash
# Using namespaced command:
eidos portal build --path eidos --out public

# Or using top-level shortcut:
eidos build-site --path eidos --out public
```

Options:
* `--path, -p PATH`: Path to the specifications directory (default: `eidos`).
* `--out, -o PATH`: Destination directory for generated static assets (default: `./site`).

---

### `eidos serve`
Top-level shortcut for `eidos portal serve`. Launches the local interactive Eidos Living Web Portal HTTP server.

```bash
eidos serve --path eidos --port 8080
```

---

### `eidos build-site`
Top-level shortcut for `eidos portal build`. Exports the Living Web Portal as a self-contained static site.

```bash
eidos build-site --path eidos --out public
```

---

## 4. Living Catalog & Mutations

### `eidos spec`
Manage the living specification catalog: listing, authoring, inspecting, and deleting specification documents.

```bash
# List all specifications
eidos spec list

# Show details of a specific document
eidos spec show CMP-EIDOS-CORE

# Scaffold a new specification document
eidos spec create --type tech/component --id CMP-AUTH-SERVICE

# Safely delete a specification document
eidos spec delete CMP-AUTH-SERVICE

# List all C4 containers
eidos spec containers

# List all C4 components
eidos spec components
```

Sub-commands:
* `list`: Lists all living specifications in tabular format.
* `show <id_or_path>`: Displays detailed frontmatter metadata and preview of a specification document.
* `create <type> <id> --title <title>`: Scaffolds a new specification document with schema validation.
* `delete <id_or_path>`: Safely deletes a specification document with cross-reference safety checks.
* `containers`: Lists all C4 Level 2 containers (`CNT-*`).
* `components`: Lists all C4 Level 3 components (`CMP-*`).

---

### `eidos delta`
Orchestrate SpecDeltas throughout their semantic mutation lifecycle using flat storage and gitignored symlink views:

```bash
# List all SpecDeltas
eidos delta list

# Display delta lifecycle status dashboard
eidos delta status

# Create a new SpecDelta
eidos delta create "Migrate to PostgreSQL 16"

# Verify all mutations declared in a SpecDelta exist in the spec catalog:
eidos delta verify dlt-2026-005

# Output machine-readable verification results:
eidos delta verify dlt-2026-005 --json

# Apply a SpecDelta in-place (updates status to 'applied'):
eidos delta apply dlt-2026-005

# Deterministically scaffold missing specs declared in delta mutations without LLMs:
eidos delta apply dlt-2026-005 --execute

# Verify all mutations are satisfied before applying:
eidos delta apply dlt-2026-005 --verify

# Audit removed or deprecated entities for dangling references and active leaks:
eidos delta audit dlt-2026-016

# Run in strict mode (exit code 1 if unacknowledged leaks exist):
eidos delta audit dlt-2026-016 --strict

# Output machine-readable JSON audit report for AI agents and CI/CD pipelines:
eidos delta audit dlt-2026-016 --json

# Safely delete an unapplied SpecDelta
eidos delta delete dlt-2026-005

# Rebuild symlink views partitioned by lifecycle state
eidos delta sync-views
```

---

### `eidos component`
Manage C4 Level 3 in-process components (`CMP-*`) (promotion, inlining, and listing):

```bash
# Promote an inline component to a dedicated specification
eidos component promote CMP-CORE-VALIDATOR

# Demote / inline a dedicated component back into its container manifest
eidos component inline CMP-CORE-VALIDATOR

# List all components
eidos component list
```

---

### `eidos containers`
Top-level command (also available as `eidos spec containers`). Inspect and list all C4 Level 2 containers (`CNT-*`), their runtimes, package paths, and declared C4 Level 3 components.

```bash
eidos containers
```

---

### `eidos components`
Top-level command (also available as `eidos spec components`). Inspect and list all C4 Level 3 in-process components (`CMP-*`), responsibilities, and source file paths, optionally filtered by container ID.

```bash
# List all internal components across all containers
eidos components

# List internal components belonging to a specific container
eidos components CNT-EIDOS-CORE
```

---

## 5. Knowledge Graph & AI Context

### `eidos index`
Manage and query the embedded SQLite architectural knowledge graph and FTS5 hybrid search index (`.eidos/cache/index.db`).

The index initializes automatically on first use and lazily resynchronizes before every query with sub-2ms dirty checking, guaranteeing zero drift.

#### `eidos index status`
Display database cache size, clean/drift synchronization state, check latency, and entity/edge counts.

```bash
eidos index status
```

#### `eidos index sync`
Manually synchronize or force a full clean rebuild of the SQLite database and FTS5 index.

```bash
# Incremental synchronization (auto-triggered on queries if dirty)
eidos index sync

# Force complete rebuild from scratch
eidos index sync --force
```

Options:
* `--force`: Force complete drop and rebuild of all tables and indexes.
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

#### `eidos index search`
Perform BM25 full-text search across specification titles, requirements, and invariants.

```bash
eidos index search "FTS5 hybrid search" --limit 5
```

Options:
* `QUERY`: Search query or keywords.
* `-l, --limit INTEGER`: Maximum number of search results to return (default: `10`).
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

#### `eidos index blast-radius`
Traverse the architectural knowledge graph using recursive Common Table Expressions (CTE) to calculate forward/reverse dependency blast radius before editing code or contracts.

```bash
eidos index blast-radius CMP-EIDOS-CORE --depth 2
```

Options:
* `SYMBOL_OR_PATH`: Component ID, contract ID, domain ID, symbol, or file path.
* `-d, --depth INTEGER`: Maximum graph traversal distance (default: `2`).
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

---

### `eidos context`
Extract a compact, token-efficient architectural subgraph slice (C4 components, bound contracts, domain capabilities, active invariants, and governing policies) given target file paths, a component ID, or a task description. Powered by the embedded SQLite knowledge graph for sub-3ms resolution.

```bash
# Slice context for touched code files (defaults to guardrails tier)
eidos context -f src/eidos/cli/main.py

# Lightweight anchor context (~140 tokens)
eidos context -c CMP-EIDOS-CORE --tier anchor

# Deep architectural context including C4 internal modules (~1000 tokens)
eidos context -c CMP-EIDOS-CORE --tier deep

# Slice context from a free-text task description
eidos context -t "Implement Git and GitLab synchronization bridge"

# Output in JSON format
eidos context -c CMP-EIDOS-CORE --format json
```

Options:
* `-f, --file TEXT`: Target source code file path(s) to slice context for.
* `-c, --component TEXT`: Target component identifier (e.g. `CMP-EIDOS-CLI`).
* `-t, --task TEXT`: Free-text task description or engineering prompt.
* `-T, --tier [anchor|guardrails|deep]`: Context compression budget (default: `guardrails`).
  * `anchor`: Component metadata and direct contract signatures (~140 tokens).
  * `guardrails`: Adds domain capabilities, active invariants, and policies (~260–890 tokens).
  * `deep`: Adds internal C4 modules, contract schemas, and full invariant text (~500–1500 tokens).
* `--format [markdown|json]`: Output serialization format (default: `markdown`).
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

---

## 6. Agent Ecosystem & Plugins

### `eidos plugin`
Manage and validate Eidos agent plugins complying with the `agent-plugins.org v1.0.0` specification:

```bash
# Install plugin for all supported AI agents
eidos install all

# Or install for a specific target:
eidos install antigravity
eidos install claude
eidos install cursor
eidos install aider
eidos install cline
eidos install open-code
eidos install codex
eidos install copilot

# Inspect installed vs bundled plugin version
eidos plugin version

# Validate plugin conformance against agent-plugins.org standard
eidos plugin validate

# Display detailed plugin metadata and manifest
eidos plugin info

# Update installed plugin to latest bundled engine version
eidos plugin update --target all

# Uninstall / remove plugin files and agent configurations
eidos plugin uninstall --target all
```

Sub-commands:
* `install`: Installs the plugin into `.agents/plugins/eidos` and client harness locations.
* `uninstall`: Cleans up the plugin, skills, and client configurations.
* `version`: Displays and compares bundled vs installed plugin version.
* `update`: Updates project-scoped plugin to the latest bundled version.
* `validate`: Validates an Agent Plugin against canonical JSON schemas.
* `info`: Displays plugin metadata, skills, hooks, and extensions.

---

### `eidos install`
Convenience shortcut for `eidos plugin install`. Installs the Eidos Agent Plugin or client integrations into project scope.

```bash
eidos install all
```

---

### `eidos uninstall`
Convenience shortcut for `eidos plugin uninstall`. Uninstalls and cleans up the plugin, skills, and client configurations from project scope.

```bash
eidos uninstall all
```

---

## Convenience Shortcuts

Eidos provides intuitive root-level shortcuts for common workflows:

* `eidos install [TARGET]`: Direct shortcut to `eidos plugin install` (e.g. `eidos install claude`).
* `eidos uninstall [TARGET]`: Direct shortcut to `eidos plugin uninstall`.
* `eidos serve`: Direct shortcut to `eidos portal serve`.
* `eidos build-site`: Direct shortcut to `eidos portal build`.
* `eidos drift`: Runs spec-to-code drift detection.
* `eidos reconcile`: Runs the spec reconciliation assistant (`eidos sync`).
* `eidos create <type> <id> --title <title>`: Direct shortcut to `eidos spec create`.
* `eidos delete <id_or_path> [--force]`: Direct shortcut to `eidos spec delete`.
* `eidos list [--type <type>] [--status <status>]`: Direct shortcut to `eidos spec list`.
* `eidos containers`: Direct shortcut to `eidos spec containers`.
* `eidos components`: Direct shortcut to `eidos spec components`.
