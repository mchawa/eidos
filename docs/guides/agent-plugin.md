# AI Agent Integration

Eidos is **agent-agnostic** and **model-agnostic**. It provides a standardized plugin and **Model Context Protocol (MCP)** server so any AI coding assistant—such as **Google Antigravity**, **Claude Code**, **Cursor**, **Windsurf**, or **Cline**—can understand, query, and mutate your living specifications safely.

```
                    ┌─────────────────────────────────────────┐
                    │          AI Coding Assistant            │
                    │ (Google Antigravity, Claude, Cursor, …) │
                    └────────────────────┬────────────────────┘
                                         │
                   MCP Tools / Skills    │   Rules & Guidelines
                   (eidos_validate, …)   │   (AGENTS.md, .mdc)
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │       Eidos Agent Plugin & MCP          │
                    │        (agent-plugins.org v1)           │
                    └────────────────────┬────────────────────┘
                                         │
                   Inspect Architecture  │   Scaffold & Apply
                   & Invariants          │   SpecDeltas (DLT-*)
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │          Living Specifications          │
                    │      (eidos/*.adoc in Git repo)         │
                    └─────────────────────────────────────────┘
```

---

## One-Command Setup

Eidos can automatically configure your project for all supported AI coding assistants in a single step:

```bash
# Configure all supported AI assistants at once (Antigravity, Claude, Cursor)
eidos plugin install --target all
```

### Installation Targets

| Target Flag | Target Path / Config | Description |
| :--- | :--- | :--- |
| `--target all` | All environments below | Installs Antigravity plugin bundle and generates Claude & Cursor MCP configs |
| `--target antigravity` | `.agents/plugins/eidos/` | Installs plugin bundle with rules, skills, hooks, and extensions |
| `--target claude` | `.claude/mcp.json` | Configures the project-level MCP server for Claude Code |
| `--target cursor` | `.cursor/mcp.json` | Configures the project-level MCP server for Cursor |
| `--target project` | `.agents/plugins/eidos/` | Standard project-scope installation (alias for `workspace`) |

---

## Assistant Integration Guides

Select your AI coding assistant below for step-by-step setup instructions and configuration examples:

=== "Google Antigravity"

    Google Antigravity natively discovers plugins adhering to the **[agent-plugins.org v1.0.0](https://agent-plugins.org)** standard inside `.agents/plugins/`.

    ### 1. Installation

    Run the installation command in your repository root:

    ```bash
    eidos plugin install --target antigravity
    ```

    This creates the following directory structure inside your project:

    ```text
    .agents/plugins/eidos/
    ├── plugin.json               # agent-plugins.org v1.0.0 manifest
    ├── rules/
    │   └── AGENTS.md             # System prompt architectural guidelines
    ├── skills/
    │   ├── eidos/                # Living spec navigation & dual workflows
    │   ├── eidos-architecture/   # Arc42 & C4 Level 1-3 topology (NO ADRs)
    │   ├── eidos-delta/          # SpecDelta mutation lifecycle (DLT-*)
    │   ├── eidos-design/         # Multi-Design Systems & W3C Tokens
    │   ├── eidos-drift/          # Spec-to-code drift detection & PR gate
    │   ├── eidos-explore/        # Spikes, feasibility & technical discovery (EXP-*)
    │   ├── eidos-extract/        # Brownfield living spec reverse-engineering
    │   ├── eidos-init/           # Greenfield repository bootstrapping
    │   ├── eidos-invariants/     # Policy invariants (POL-*) & test traceability
    │   ├── eidos-portal/         # Living Web Portal & static documentation
    │   ├── eidos-product/        # Domains (DOM-*), capabilities (CAP-*), personas
    │   ├── eidos-reconcile/      # Direct Mode spec reconciliation
    │   └── eidos-review/         # Staff Architect review & blast radius audit
    └── hooks/
        └── eidos-doc-linter/     # Document linting & quality checks
    ```

    ### 2. How Antigravity Uses Eidos

    * **Automatic System Rules**: Antigravity automatically loads `.agents/plugins/eidos/rules/AGENTS.md` into the agent's context, strictly enforcing the 5 Eidos architectural invariants (Single Source of Truth, No ADRs, SpecDelta lifecycle, Invariant-Test traceability, and Spec validation).
    * **Specialized Agent Skills**: Antigravity agents can invoke skills like `/eidos-delta` to scaffold mutations or `/eidos-architecture` to inspect C4 containers.
    * **Quality Hooks**: Quality gates intercept document edits to verify AsciiDoc schemas before changes are finalized.

=== "Claude Code & Claude Desktop"

    Claude Code (Anthropic's agentic CLI) and Claude Desktop communicate with Eidos over the **Model Context Protocol (MCP)** using stdio JSON-RPC.

    ### Claude Code (CLI)

    #### 1. Automatic Configuration
    Run the installer to configure `.claude/mcp.json`:

    ```bash
    eidos plugin install --target claude
    ```

    #### 2. Manual Configuration (`.claude/mcp.json` or `.mcp.json`)
    If you prefer to configure manually, place the following in `.claude/mcp.json` or `.mcp.json` in your repository root:

    ```json
    {
      "mcpServers": {
        "eidos": {
          "command": "uv",
          "args": ["run", "eidos", "mcp"]
        }
      }
    }
    ```

    > [!TIP]
    > If running outside an existing Python virtualenv, use `uvx` for zero-install execution:
    > ```json
    > {
    >   "mcpServers": {
    >     "eidos": {
    >       "command": "uvx",
    >       "args": ["eidos-ai", "mcp"]
    >     }
    >   }
    > }
    > ```

    #### 3. Enforcing Rules in `CLAUDE.md`
    To ensure Claude Code adheres to Eidos architectural governance, reference the Eidos rules in your project's `CLAUDE.md`:

    ```markdown
    <!-- CLAUDE.md -->
    ## Eidos Architectural Guidelines
    - Adhere strictly to the guidelines in `.agents/plugins/eidos/rules/AGENTS.md`.
    - Treat `eidos/` living specifications as the single source of truth.
    - Never create ADRs; all mutations must follow the SpecDelta lifecycle (`eidos_create_delta`).
    - Validate specifications with `eidos_validate` before completing tasks.
    ```

    ---

    ### Claude Desktop

    To connect Claude Desktop to your local project's Eidos MCP server, edit your configuration file:

    * **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
    * **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
    * **Linux**: `~/.config/Claude/claude_desktop_config.json`

    ```json
    {
      "mcpServers": {
        "eidos": {
          "command": "uv",
          "args": ["--directory", "/absolute/path/to/your/project", "run", "eidos", "mcp"]
        }
      }
    }
    ```

=== "Cursor"

    Cursor connects to Eidos via project-level MCP configuration and custom Cursor rules.

    ### 1. Automatic Configuration

    Generate the project-level MCP configuration in `.cursor/mcp.json`:

    ```bash
    eidos plugin install --target cursor
    ```

    ### 2. Manual Configuration (`.cursor/mcp.json`)

    Alternatively, create or edit `.cursor/mcp.json` in your repository root:

    ```json
    {
      "mcpServers": {
        "eidos": {
          "command": "uv",
          "args": ["run", "eidos", "mcp"]
        }
      }
    }
    ```

    ### 3. Cursor Rules (`.cursor/rules/eidos.mdc` or `.cursorrules`)

    Create `.cursor/rules/eidos.mdc` so Cursor Composer and Chat agents understand when and how to call Eidos MCP tools:

    ```markdown
    ---
    description: Eidos living specifications, C4 architecture, and SpecDelta lifecycle
    globs: ["eidos/**", "src/**", "tests/**"]
    alwaysApply: true
    ---

    # Eidos AI Agent Guidelines

    - **Living Specs as Authority**: All system architecture, containers, component manifests, and policies reside in `eidos/`.
    - **No ADRs**: Never write ADRs. For non-trivial changes, use the `eidos_create_delta` MCP tool.
    - **Invariant Traceability**: Verify that every policy (`POL-*`) or capability (`CAP-*`) modified has corresponding test coverage in `tests/`.
    - **Validate Specs**: Always call the `eidos_validate` MCP tool before concluding a task.
    ```

    ### 4. Verification in Cursor

    Open **Cursor Settings > Features > MCP Servers**. You will see `eidos` listed with a green status indicator and the 12 available tools (`eidos_validate`, `eidos_detect_drift`, `eidos_reconcile`, etc.).

=== "Generic MCP Clients (Windsurf, Roo Code, Cline)"

    Eidos implements the standard **Model Context Protocol (MCP)** specification over `stdio`. Any MCP-compliant client can connect immediately.

    ### Cline & Roo Code (`cline_mcp_settings.json`)

    Add the `eidos` server to your Cline or Roo Code MCP settings file:

    ```json
    {
      "mcpServers": {
        "eidos": {
          "command": "uv",
          "args": ["run", "eidos", "mcp"],
          "disabled": false,
          "autoApprove": [
            "eidos_validate",
            "eidos_detect_drift",
            "eidos_reconcile",
            "eidos_list_components"
          ]
        }
      }
    }
    ```

    ### Direct CLI Stdio Execution

    You can run the MCP server directly from the command line:

    ```bash
    # Inside a project with eidos installed in the virtualenv:
    uv run eidos mcp

    # Or run via uvx without pre-installing:
    uvx eidos-ai mcp

    # Or when installed globally:
    eidos mcp
    ```

---

## Exposed MCP Tools Reference

When connected, the Eidos MCP server exposes 12 high-level architectural tools to your AI assistant:

| MCP Tool | Arguments | Description |
| :--- | :--- | :--- |
| `eidos_detect_drift` | `base_ref`, `head_ref`, `uncommitted`, `path` | Inspects spec-to-code drift between git branches or uncommitted changes, validates schemas, and checks test bindings. |
| `eidos_reconcile` | `base_ref`, `uncommitted`, `path`, `as_prompt` | Analyzes code modifications against the living spec catalog and proposes candidate specs or structured prompt. |
| `eidos_validate` | `path` *(optional)* | Validates all living specifications against their bundled JSON Schemas and checks bidirectional cross-references. |
| `eidos_info` | — | Returns current engine version, loaded v1 schemas, and supported platforms. |
| `eidos_list_components` | `path` *(optional)* | Lists monorepo containers, internal modules, dependencies, and monorepo package paths. |
| `eidos_read_spec` | `path` | Reads and parses structured frontmatter metadata and AsciiDoc content of a specification. |
| `eidos_list_specs` | `type`, `status`, `path` *(optional)* | Lists living specifications matching optional type and lifecycle filters. |
| `eidos_create_spec` | `type`, `id`, `title`, ... | Scaffolds a new specification document (capability, domain, policy, contract, component, design system, etc.). |
| `eidos_delete_spec` | `target`, `path`, `force` | Safely deletes a living specification with cross-reference safety checks. |
| `eidos_create_delta` | `id`, `title`, `kind`, `motivation` | Scaffolds a new SpecDelta mutation document (`DLT-*`) for Planned Mode. |
| `eidos_apply_delta` | `target`, `path` | Reconciles a SpecDelta in-place, updating its lifecycle state to `applied` and synchronizing symlink views. |
| `eidos_init_repo` | `path`, `name`, `title`, `with_plugin` | Programmatically bootstraps a repository with living specs and agent plugin. |

---

## The 5 Invariant Architectural Rules

Eidos embeds five golden architectural rules in `.agents/plugins/eidos/rules/AGENTS.md`. When the plugin is installed, every AI assistant is instructed to obey them:

### 1. Single Source of Truth
All product requirements, architectural topologies, component interfaces, and policies are codified inside `eidos/` as living AsciiDoc (`.adoc`) files with YAML frontmatter. The living specs are continuously synchronized with the codebase.

### 2. Absolutely No ADRs
Traditional Architecture Decision Records (ADRs) become write-only documentation graveyards. Decisions and evolutions are recorded directly in the living specifications:
* System Context & Containers: `eidos/architecture/system-architecture.adoc`
* Component Manifests: `eidos/architecture/components/<name>.adoc`
* Interface Contracts: `eidos/architecture/contracts/<contract>.adoc`
* Invariants & Policies: `eidos/policies/<policy>.adoc`
* Semantic Mutations: `eidos/deltas/<delta-id>.adoc`

### 3. SpecDelta Mutation Lifecycle
Before writing code for non-trivial features, refactors, or fixes, the agent must check if a SpecDelta is required. It scaffolds a delta using `eidos_create_delta` (or `eidos delta create`). After implementation and test verification, the delta is reconciled using `eidos_apply_delta` (or `eidos delta apply`).

### 4. Invariant-Test Traceability
Every policy (`POL-*`) and capability (`CAP-*`) defines explicit, testable invariants. All automated tests in `tests/` must trace back to these IDs in test markers, docstrings, or test function names.

### 5. Specification Integrity & Validation
Before concluding any task, the agent must validate all specifications using the CLI (`eidos validate eidos`) or the MCP tool (`eidos_validate`). Specifications must conform to schema with zero broken cross-references.

---

## Bundled Skills for AI Agents

When using an agent client that supports skill bundles (such as Google Antigravity), seven specialized skills are available:

* **`eidos`**: High-level navigation and orchestration of living specifications and platform workflows.
* **`eidos-architecture`**: Authoring, inspecting, and maintaining Arc42 documentation, C4 Level 1–3 topology, container manifests, and interface contracts.
* **`eidos-delta`**: Managing the SpecDelta mutation lifecycle (`DLT-*`), including proposing mutations, authoring diffs, and reconciling applied deltas.
* **`eidos-invariants`**: Defining system policies (`POL-*`), capability invariants (`INV-*`), and enforcing bidirectional invariant-test traceability.
* **`eidos-design`**: Authoring and maintaining Multi-Design Systems, W3C Design Tokens, UI component contracts, and screen state machines.
* **`eidos-extract`**: Reverse-engineering and extracting living specifications from existing legacy codebases.
* **`eidos-init`**: Bootstrapping new Git repositories with baseline Arc42/C4 architecture, core policies, personas, and automated validation.

---

## Plugin Lifecycle & Maintenance

Eidos provides a complete suite of commands to manage, inspect, and update the installed agent plugin:

### Inspect Version Status

Compare the currently installed plugin against the version bundled with the Eidos engine:

```bash
eidos plugin version
```

Output:

```text
               Eidos Agent Plugin Version Status                
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property          ┃ Value                                    ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Bundled Version   │ 0.1.0 (eidos engine)                     │
│ Installed Version │ 0.1.0                                    │
│ Plugin Location   │ /path/to/project/.agents/plugins/eidos   │
│ Sync Status       │ ✓ Up to date                             │
└───────────────────┴──────────────────────────────────────────┘
```

### Upgrade the Installed Plugin

When upgrading `eidos-ai`, update the project's installed plugin files in-place:

```bash
eidos plugin update --target all
```

### Verify Conformance

Ensure that your plugin bundle strictly complies with the `agent-plugins.org v1.0.0` specification:

```bash
eidos plugin validate
```

Output:

```text
              Agent Plugin Conformance (agent-plugins.org v1.0.0)               
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Property               ┃ Value                                               ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Plugin Name            │ eidos                                               │
│ Plugin Version         │ 0.1.0                                               │
│ Overall Status         │ CONFORMANT                                          │
│ Discovered Skills      │ eidos, eidos-architecture, eidos-delta,             │
│                        │ eidos-design, eidos-extract, eidos-init,            │
│                        │ eidos-invariants                                    │
│ Configured MCP Servers │ eidos                                               │
│ Lifecycle Hooks        │ eidos-doc-linter                                    │
│ Client Extensions      │ com.google.antigravity                              │
└────────────────────────┴────────────────────────────────━━━━━━━━━━━━━━━━━━━━━┘
```

### Uninstall / Clean Up

To cleanly remove plugin files and project MCP configurations:

```bash
eidos plugin uninstall --target all
```
