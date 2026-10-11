# CLI Command Reference

The `eidos` command-line tool provides everything needed to scaffold, validate, serve, and mutate living specifications.

```bash
eidos [OPTIONS] COMMAND [ARGS]...
```

---

## Core Commands

### `eidos info`
Display the Eidos engine version, bundled schemas, and platform status.

```bash
eidos info
```

---

### `eidos init`
Initialize a Git repository with the standard Eidos living specification topology, baseline Arc42/C4 architecture, core policies, and validation rules.

```bash
eidos init
# Or initialize at a custom path:
eidos init --path /path/to/repo
```

---

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

---

### `eidos serve`
Launch the interactive Eidos Living Web Portal locally with live updates.

```bash
eidos serve --path eidos --port 8080
```

Options:
* `--path PATH`: Path to the specifications directory (default: `eidos`).
* `--host TEXT`: Bind host (default: `127.0.0.1`).
* `--port INTEGER`: Port number (default: `8080`).

---

### `eidos build-site`
Export the Eidos Living Web Portal as a self-contained, offline-first static site suitable for deployment on GitHub Pages or static web servers.

```bash
eidos build-site --path eidos --out public
```

Options:
* `--path PATH`: Path to the specifications directory (default: `eidos`).
* `--out PATH`: Destination directory for generated static assets (default: `public` or `site`).

---

### `eidos components`
Inspect and list all C4 containers, internal components, dependencies, and interface contracts.

```bash
eidos components
```

---

### `eidos mcp`
Launch the stdio **Model Context Protocol (MCP)** server, enabling AI coding agents (Claude, Cursor, Antigravity) to query and mutate living specifications programmatically.

```bash
eidos mcp
```

---

## Specification Management (`eidos spec`)

Manage specification documents:

```bash
# List all specifications
eidos spec list

# Show details of a specific document
eidos spec show CMP-EIDOS-CORE

# Scaffold a new specification document
eidos spec create --type tech/component --id CMP-AUTH-SERVICE

# Safely delete a specification document
eidos spec delete CMP-AUTH-SERVICE
```

---

## SpecDelta Management (`eidos delta`)

Orchestrate architectural mutations through their lifecycle:

```bash
# List all SpecDeltas
eidos delta list

# Create a new SpecDelta
eidos delta create "Migrate to PostgreSQL 16"

# Apply and reconcile a SpecDelta in-place
eidos delta apply dlt-2026-005

# Safely delete an unapplied SpecDelta
eidos delta delete dlt-2026-005
```

---

## Drift Detection & Quality Gates (`eidos drift`)

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

## Living Spec Reconciliation (`eidos reconcile`)

Analyze recent code changes against the living specification catalog and generate candidate specs to update or tailored AI prompts for synchronization:

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

## Agent Plugin Management (`eidos plugin`)

Manage and validate Eidos agent plugins complying with the `agent-plugins.org v1.0.0` specification:

```bash
# Install plugin for all supported AI agents (Antigravity, Claude, Cursor)
eidos plugin install --target all

# Or install for a specific target:
eidos plugin install --target antigravity
eidos plugin install --target claude
eidos plugin install --target cursor

# Inspect installed vs bundled plugin version
eidos plugin version

# Validate plugin conformance against agent-plugins.org standard
eidos plugin validate

# Display detailed plugin metadata and manifest
eidos plugin info

# Update installed plugin to latest bundled engine version
eidos plugin update --target all

# Uninstall / remove plugin files and MCP configurations
eidos plugin uninstall --target all
```

---

## Convenience Shortcuts

Eidos provides intuitive root-level shortcuts for common workflows:

* `eidos drift`: Runs spec-to-code drift detection.
* `eidos reconcile`: Runs the spec reconciliation assistant (`eidos sync`).
* `eidos create <type> <id> --title <title>`: Direct shortcut to `eidos spec create`.
* `eidos delete <id_or_path> [--force]`: Direct shortcut to `eidos spec delete`.
* `eidos list [--type <type>] [--status <status>]`: Direct shortcut to `eidos spec list`.



