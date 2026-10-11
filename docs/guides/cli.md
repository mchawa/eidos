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


