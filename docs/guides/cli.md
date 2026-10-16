# CLI Command Reference

The `eidos` command-line tool provides everything needed to scaffold, validate, serve, and mutate living specifications.

```bash
eidos [OPTIONS] COMMAND [ARGS]...
```

Commands are semantically organized into six functional panels in CLI help output (`eidos --help`):
1. **Setup & Workspace**: Environment information, versioning, workspace diagnostics, and repository bootstrapping.
2. **Governance & Verification**: Schema validation and spec-to-code drift detection.
3. **Knowledge Graph & AI Context**: Subgraph context extraction and SQLite/FTS5 knowledge graph maintenance.
4. **Living Catalog & Mutations**: Specifications, C4 component lifecycle management (`eidos spec component`), and SpecDeltas.
5. **Living Web Portal**: Local interactive web portal preview server and static site compilation.
6. **Agent Ecosystem & Plugins**: Multi-agent plugin installation, verification, updates, and lifecycle hooks conforming to `agent-plugins.org`.

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

### `eidos doctor`
Verify Eidos tool installation, AST engine status, specification directory health, knowledge graph index freshness, and installed agent plugin hooks:

```bash
eidos doctor
eidos doctor --fix
eidos doctor --json
```

Options:
* `--path, -p PATH`: Path to the specifications directory (default: `eidos`).
* `--fix`: Automatically repair detected index or symlink view issues.
* `--json`: Output diagnostic report in JSON format.

---

### `eidos init`
Initialize a Git repository with an Eidos living specification skeleton, tracking empty directories with `.gitkeep` files, self-contained `eidos/.gitignore`, and immediate validation. Supports both the full multi-domain catalog and the progressive **Eidos Lite** starter profile (`--lite`). Note that `eidos init` initializes specifications; agent plugins are installed separately via `eidos plugin install`.

```bash
# Full multi-domain catalog skeleton (default)
eidos init

# Progressive 3-file Eidos Lite onboarding profile
eidos init --lite

# Initialize at a custom path:
eidos init /path/to/repo --name my-system --title "My System"
```

Options:
* `PATH`: Target directory to initialize (default: current working directory `.`).
* `--name TEXT`: Custom machine-readable system identifier.
* `--title TEXT`: Human-readable display title.
* `--lite, -l`: Initialize with the progressive Eidos Lite specification profile (`system.adoc`, `architecture/system-architecture.adoc`, `containers/`, `capabilities/`).
* `--force, -f`: Overwrite existing files if present.

---

## 2. Governance & Verification

### `eidos validate`
Validate all AsciiDoc specification documents against their bundled v1 JSON/YAML schemas (including shared `$defs` for `DEC-*` and `INV-*`), check `x-eidos-ref` cross-document graph integrity, verify on-disk existence of declared container package paths and contract specification files, and enforce Typer CLI contract command parity.

```bash
eidos validate eidos
eidos validate --json
```

Options:
* `PATH` / `--path PATH`: Path to the specification directory (default: `eidos`).
* `--json`: Output structured JSON validation report.

Exit Codes:
* `0`: All specifications conform to schema with zero broken cross-references or missing file bindings.
* `1`: Schema validation errors, broken cross-references, missing declared files, or undocumented CLI commands detected.

> [!NOTE]
> `eidos validate` is a static catalog linter. It does not inspect Git diffs or scan the test suite for polyglot invariant coverage, making it sub-second fast for local specification authoring and TDD.

---

### `eidos drift`
The primary Git-native verification gate. Detects spec-to-code drift between Git branches (using fail-closed 3-dot merge-base comparison) or uncommitted working-tree changes, validates schema conformance, enforces substantive spec diffs, and statically verifies polyglot invariant test bindings across both Planned Mode and Direct Mode:

```bash
# Compare current branch against base branch (3-dot merge-base diff):
eidos drift --base origin/main

# Inspect uncommitted working tree and staged changes:
eidos drift --uncommitted

# Generate a markdown summary comment (ideal for CI step summary or PR comment):
eidos drift --base origin/main --markdown drift-report.md

# Output structured JSON (includes touched_components, trivially_covered_files, remediation_suggestions):
eidos drift --uncommitted --json

# Run as non-blocking warning check:
eidos drift --warn-only
```

Options:
* `--base, -b REF`: Base git reference to compare against (defaults to `origin/main` or `main`; fails closed if an explicit ref cannot be resolved).
* `--head REF`: Head git reference (defaults to `HEAD`).
* `--uncommitted, -u`: Analyze uncommitted working tree and staged changes instead of branch diff.
* `--markdown, -m FILE`: Path to write the markdown summary comment.
* `--json`: Output raw JSON verification result (including `trivially_covered_files` flagging whitespace/comment-only spec edits).
* `--warn-only`: Do not exit with code 1 when drift is detected.

Exit Codes:
* `0`: Specifications and code are synchronized, all schemas and bindings valid, invariant tests green.
* `1`: Spec-to-code drift detected, unowned structural code found, specification validation errors found, or uncovered/orphan invariants detected.

> [!IMPORTANT]
> `eidos drift` **subsumes `eidos validate`**: it executes the entire `SpecValidator` suite internally as part of its gate, in addition to checking Git diffs, AST structural changes, and polyglot invariant test bindings.

---

## 3. Knowledge Graph & AI Context

### `eidos context`
Extract a compact, rank-ordered, token-budgeted architectural subgraph slice (C4 containers/components, bound contracts, domain capabilities, active invariants, governing policies, and embedded `DEC-*` decisions) given target file paths, a component/container ID, or a natural-language task prompt. Powered by the embedded SQLite knowledge graph for sub-3ms resolution.

```bash
# Slice context for touched code files (defaults to guardrails tier)
eidos context -f src/eidos/cli/main.py

# Lightweight anchor context (<= 300 tokens)
eidos context -c CNT-EIDOS-CORE --tier anchor

# Deep architectural context including C4 internal modules (<= 2500 tokens)
eidos context -c CNT-EIDOS-CORE --tier deep

# Slice context from a free-text task description
eidos context -t "Implement Git-native drift detection"

# Output in JSON format
eidos context -c CNT-EIDOS-CORE --format json
```

Options:
* `-f, --file TEXT`: Target source code file path(s) to slice context for.
* `-c, --component TEXT`: Target container or component identifier (e.g. `CNT-EIDOS-CLI`, `CMP-SPEC-VALIDATOR`).
* `-t, --task TEXT`: Free-text task description or engineering prompt.
* `-T, --tier [anchor|guardrails|deep]`: Context compression budget (default: `guardrails`).
  * `anchor`: Container/component metadata, direct contract signatures, and 1-line `DEC-*` summaries (`<= 300 tokens`).
  * `guardrails`: Adds domain capabilities, active invariants with `test_binding`, policies, and full `DEC-*` rationale (`<= 1,000 tokens`).
  * `deep`: Adds internal C4 modules, in-process APIs, contract schemas, and downstream dependents (`<= 2,500 tokens`).
* `--format [markdown|json]`: Output serialization format (default: `markdown`).
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

---

### `eidos index`
Manage and query the embedded SQLite architectural knowledge graph and FTS5 lexical search index (`eidos/.cache/index.db`).

The index initializes automatically on first use and lazily resynchronizes before every query via sub-5ms `mtime_ns` and `SHA-256` dirty checking, guaranteeing zero drift.

#### `eidos index status`
Display database cache size, schema version (`2.3.0`), clean/drift synchronization state across specs and code, check latency, entity/edge counts, active AST parser engine (`ast-grep` vs `python-ast`), symbol breakdown by syntax kind, and test-to-invariant assertion counts. Supports `--json` for programmatic agent inspection.

```bash
# Display rich visual status and AST telemetry:
eidos index status

# Output raw telemetry as machine-readable JSON:
eidos index status --json
```

Options:
* `--json`: Output raw index telemetry as formatted JSON.
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

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
Perform column-weighted BM25 full-text search (with `camelCase`/`snake_case` identifier de-compounding) across specification titles, requirements, decisions, and invariants.

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
eidos index blast-radius CNT-EIDOS-CORE --depth 2
# Machine-readable JSON output:
eidos index blast-radius CNT-EIDOS-CORE --depth 2 --json
```

Options:
* `SYMBOL_OR_PATH`: Container ID, component ID, contract ID, domain ID, symbol, or file path.
* `-d, --depth INTEGER`: Maximum graph traversal distance (default: `2`).
* `--json`: Output results as machine-readable JSON.
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

#### `eidos index repo-map`
Generate a token-budgeted structural repo map outline (classes, functions, methods, structs, routes) for AI coding agents. Powered by multi-language AST parsing (`ast-grep-py` with Python stdlib fallback), compressing whole codebases into a high-density architectural outline (~98% token compression).

```bash
# Generate structural repo map with default budget (1500 tokens)
eidos index repo-map

# Or specify a custom token budget and format:
eidos index repo-map --max-tokens 2000 --format markdown

# Output as structured JSON for autonomous agent harnesses:
eidos index repo-map --format json
```

Options:
* `-t, --max-tokens INTEGER`: Maximum context tokens allocated for structural repo map outline (default: `1500`).
* `-f, --format [markdown|json]`: Output format (default: `markdown`).
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

#### `eidos index find-symbol`
Locate symbol matches (classes, functions, methods, structs, API endpoints) across polyglot AST source files and identify their exact file/line coordinates and governing living component specifications.

```bash
# Locate a class or function symbol across the codebase:
eidos index find-symbol "ArchitecturalIndex"

# Or search with custom result limit:
eidos index find-symbol "AstGrepIndexer" --limit 10

# Output as structured JSON (includes source file, line_start, line_end, signature):
eidos index find-symbol "validate" --json
```

Options:
* `QUERY`: Exact or partial symbol name (class, method, function, or route).
* `-l, --limit INTEGER`: Maximum number of symbol results to return (default: `20`).
* `--json`: Output results as machine-readable JSON.
* `-p, --path PATH`: Path to specifications directory (default: `eidos`).

---

## 4. Living Catalog & Mutations

### `eidos spec`
Manage the living specification catalog and C4 Level 3 components: listing, authoring, inspecting, deleting, promoting, and inlining specification documents.

```bash
# List all specifications (including inline C4 Level 3 components)
eidos spec list

# Filter by specification type (e.g. container, component, capability, delta)
eidos spec list --type container
eidos spec list --type component --json

# Show details of a specific document
eidos spec show CNT-EIDOS-CORE

# Scaffold a new specification document
eidos spec create component CMP-AUTH-SERVICE --title "Authentication Service"

# Safely delete a specification document
eidos spec delete CMP-AUTH-SERVICE

# Promote an inline C4 Level 3 component to eidos/architecture/components/
eidos spec component promote CMP-SPEC-VALIDATOR

# Demote / inline a dedicated component back into its parent container manifest
eidos spec component inline CMP-SPEC-VALIDATOR
```

Sub-commands:
* `list`: Lists all living specifications (including inline C4 Level 3 components) in tabular or JSON format, with optional `--type`, `--status`, and `--json` flags.
* `show <id_or_path>`: Displays detailed frontmatter metadata and preview of a specification document.
* `create <type> <id> --title <title>`: Scaffolds a new specification document across all 16 schema types with immediate validation.
* `delete <id_or_path>`: Safely deletes a specification document with cross-reference safety checks.
* `component promote <component_id>`: Promotes an inline C4 Level 3 component (`CMP-*`) to a dedicated specification in `eidos/architecture/components/` and updates the parent container with `ref:`.
* `component inline <component_id>`: Inlines a promoted C4 Level 3 component (`CMP-*`) back into its parent container manifest.

---

### `eidos delta`
Orchestrate SpecDeltas throughout their semantic mutation lifecycle using flat storage (`eidos/deltas/`) and gitignored symlink views (`eidos/deltas/by-status/`):

```bash
# List all SpecDeltas
eidos delta list

# Display delta lifecycle status dashboard
eidos delta status

# Create a new SpecDelta (kind: feature | enabler | bug)
eidos delta create DLT-2026-055 \
  --title "Migrate to PostgreSQL 16" \
  --kind enabler \
  --motivation "Upgrade database runtime and connection pooling."

# Verify all mutations and expect: post-conditions declared in a SpecDelta:
eidos delta verify DLT-2026-055

# Output machine-readable verification results:
eidos delta verify DLT-2026-055 --json

# Apply a SpecDelta in-place (updates status to 'applied'):
eidos delta apply DLT-2026-055

# Deterministically scaffold missing specs declared in delta mutations without LLMs:
eidos delta apply DLT-2026-055 --execute

# Verify all mutations are satisfied before applying:
eidos delta apply DLT-2026-055 --verify

# Audit removed or deprecated entities for dangling references and active leaks:
eidos delta audit DLT-2026-016

# Run in strict mode (exit code 1 if unacknowledged leaks exist):
eidos delta audit DLT-2026-016 --strict

# Output machine-readable JSON audit report for AI agents and CI/CD pipelines:
eidos delta audit DLT-2026-016 --json

# Safely delete an unapplied SpecDelta
eidos delta delete DLT-2026-055

# Rebuild symlink views partitioned by lifecycle state
eidos delta sync-views

# Compile an auditable, SpecDelta-backed changelog from Git release tags
eidos delta changelog

# Output directly to stdout or export to custom file
eidos delta changelog --stdout
eidos delta changelog --output RELEASE_NOTES.md --no-unreleased
```

---

## 5. Living Web Portal

### `eidos portal`
Manage the interactive Living Web Portal and static documentation site export.

#### `eidos portal serve`
Launch the interactive Eidos Living Web Portal locally with live updates.

```bash
eidos portal serve --path eidos --port 8080
```

Options:
* `--path, -p PATH`: Path to the specifications directory (default: `eidos`).
* `--host, -h TEXT`: Bind host (default: `127.0.0.1`).
* `--port INTEGER`: Port number (default: `3000`).
* `--open`: Open portal in browser automatically.

#### `eidos portal build`
Export the Eidos Living Web Portal as a self-contained, air-gapped static site (`index.html` + `data.json`) suitable for deployment on GitHub Pages, GitLab Pages, or static web servers.

```bash
eidos portal build --path eidos --out public
```

Options:
* `--path, -p PATH`: Path to the specifications directory (default: `eidos`).
* `--out, -o PATH`: Destination directory for generated static assets (default: `./site`).

---

## 6. Agent Ecosystem & Plugins

### `eidos plugin`
Manage and validate Eidos agent plugins complying with the `agent-plugins.org v1.0.0` specification:

```bash
# Install plugin for all supported AI agents
eidos plugin install all

# Or install for a specific target:
eidos plugin install antigravity
eidos plugin install claude
eidos plugin install cursor
eidos plugin install aider
eidos plugin install cline
eidos plugin install open-code
eidos plugin install codex
eidos plugin install copilot

# Inspect installed vs bundled plugin version
eidos plugin version

# Validate plugin conformance against agent-plugins.org standard
eidos plugin validate

# Display detailed plugin metadata and manifest
eidos plugin info

# Update installed plugin to latest bundled engine version
eidos plugin update --target all

# Uninstall plugin files and agent configurations
eidos plugin uninstall --target all
```

Sub-commands:
* `install`: Installs the plugin into `.agents/plugins/eidos` and client harness locations via manifest-tracked `TargetAdapter` projections.
* `uninstall`: Safely cleans up Eidos plugin files, skills, and client configurations while preserving user-owned files.
* `version`: Displays and compares bundled vs installed plugin version.
* `update`: Updates project-scoped plugin to the latest bundled version.
* `validate`: Validates an Agent Plugin against canonical JSON schemas and SemVer 2.0.0 rules.
* `info`: Displays plugin metadata, skills, hooks, and extensions.

---

### `eidos hook`
CLI-native lifecycle hook handlers invoked by AI coding agent harnesses (`PreToolUse`, `PostToolUse`, `PostInvocation`, and Block-Once `Stop` gate):

```bash
eidos hook lint --event pre_tool
eidos hook lint --event post_invocation
eidos hook lint --event stop
eidos hook drift --event post_invocation
eidos hook drift --event stop
```
