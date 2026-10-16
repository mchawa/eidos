# AI Agent Integration

Eidos is **agent-agnostic** and **model-agnostic**. It provides a standardized plugin bundle conforming to **[agent-plugins.org v1.0.0](https://agent-plugins.org)** and 4 cohesive **Agent Skills** conforming to **[agentskills.io](https://agentskills.io)** so any AI coding assistant—such as **Google Antigravity**, **Claude Code**, **Cursor**, **GitHub Copilot**, **Aider**, **Cline**, **OpenCode**, or **OpenAI Codex**—can understand, query, and mutate your living specifications safely through direct CLI execution.

```
                    ┌─────────────────────────────────────────┐
                    │          AI Coding Assistant            │
                    │ (Google Antigravity, Claude, Cursor, …) │
                    └────────────────────┬────────────────────┘
                                         │
                    Direct CLI / Skills  │   Rules & Guidelines
                    (eidos validate, …)  │   (AGENTS.md, .mdc)
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │           Eidos Agent Plugin            │
                    │        (agent-plugins.org v1)           │
                    └────────────────────┬────────────────────┘
                                         │
                    Inspect Architecture │   Scaffold & Apply
                    & Invariants         │   SpecDeltas (DLT-*)
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │          Living Specifications          │
                    │      (eidos/*.adoc in Git repo)         │
                    └─────────────────────────────────────────┘
```

---

## The CLI-First Architecture for AI Agents

Modern AI coding agents (Claude Code, Cursor Composer, Google Antigravity, Copilot, Cline, Aider) operate inside terminal-capable sandboxes, subshells, and Docker containers. Eidos is built **CLI-first**:

* **Zero Daemon Flakiness**: No long-running background stdio server processes to crash, disconnect, deadlock, or require manual port management.
* **Universal Cloud & Sandbox Portability**: Works out-of-the-box in GitHub Codespaces, remote SSH sessions, CI/CD runners, and ephemeral cloud devboxes where local stdio daemon connections are unreliable.
* **Deterministic `--json` Contracts**: Every inspection, validation, and drift check command supports `--json` output, providing deterministic schemas that agents can parse programmatically without regex or LLM hallucination.
* **Token-Efficient Progressive Disclosure**: Through `eidos context`, agents extract targeted architectural slices (~140 to ~1,500 tokens) rather than dumping thousands of lines of documentation into the context window.
* **Strict Exit Code Semantics**: Commands return standard Unix exit codes (`0` for success/clean, `1` for validation errors or drift), enabling automated tool loops and pre-commit hooks to halt instantly on failure.

---

## One-Command Setup

Eidos automatically configures your project for all supported AI coding assistants in a single step:

```bash
# Default installation (configures project scope, Cursor, and Claude Code):
eidos plugin install

# Full installation across all supported AI assistants:
eidos plugin install all
# or:
eidos plugin install --target all

# Targeted installation for a specific assistant:
eidos plugin install cursor
eidos plugin install claude
eidos plugin install copilot
```

### Installation Targets

| Target Flag | Target Path / Config | Description |
| :--- | :--- | :--- |
| `--target all` | All environments below | Installs plugins and configs for Antigravity, Claude, Cursor, Aider, Cline, OpenCode, Codex, and GitHub Copilot |
| `--target project` | `.agents/plugins/eidos/` | Standard project-scope installation conforming strictly to `agent-plugins.org v1.0.0` (also configures Cursor and Claude) |
| `--target antigravity` | `.agents/plugins/eidos/` | Installs plugin bundle with rules, 4 skills, hooks, and extensions (agent-plugins.org v1.0.0) |
| `--target claude` | `.claude/plugins/eidos/`, `.claude/skills/` | Installs Claude Code plugin, 4 flat skills, slash commands (`/validate`, `/drift`), and configures `.claude/settings.json` |
| `--target cursor` | `.cursor/skills/`, `.cursor/rules/eidos.mdc` | Installs 4 flat skills and Cursor rules (`.mdc`) for CLI execution |
| `--target copilot` | `.github/plugin/`, `.github/skills/`, `.github/copilot/` | Installs GitHub Copilot plugin (Agent Plugins 1.0), 4 skills, settings, and instructions |
| `--target aider` | `.aider/skills/`, `.aider.conventions.md`, `.aider.conf.yml` | Installs 4 skills, conventions index, and auto-read configuration |
| `--target cline` | `.cline/plugins/`, `.cline/skills/` | Installs plugin bundle, 4 flat skills, and AGENTS.md conventions |
| `--target open-code` | `.opencode/plugins/`, `.opencode/skills/` | Installs plugin bundle, 4 flat skills, and AGENTS.md instructions |
| `--target codex` | `.codex/plugin.json`, `.codex/skills/`, `.codex/rules.md` | Installs OpenAI Codex plugin bundle with 4 skills and rules |

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
    ├── hooks.json                # Document linting & drift gate hooks
    └── skills/
        ├── eidos/                # Master orchestrator, context slicing & continuous drift detection
        ├── eidos-spec/           # Product, Arc42/C4 architecture, design systems & invariants (NO ADRs)
        ├── eidos-plan/           # Technical spikes (EXP-*), SpecDeltas (DLT-*) & Staff Architect review
        └── eidos-ops/            # Greenfield init, brownfield extraction & Living Web Portal
    ```

    ### 2. How Antigravity Uses Eidos

    * **Master Engineering Skill**: Antigravity indexes `skills/eidos/SKILL.md` to guide agent decision-making, strictly enforcing the 5 Eidos architectural invariants (Single Source of Truth, No ADRs, SpecDelta lifecycle, Invariant-Test traceability, and Spec validation) alongside `eidos context` and `eidos drift`.
    * **Workflow-Aligned Agent Skills**: Antigravity agents invoke `/eidos-spec` to author or inspect living specs and invariants, `/eidos-plan` for explorations, SpecDeltas, and Staff reviews, and `/eidos-ops` for repository initialization, brownfield extraction, and Web Portal operations.
    * **Quality Hooks**: Quality gates intercept document edits to verify AsciiDoc schemas before changes are finalized.

=== "Claude Code"

    Claude Code (Anthropic's agentic CLI) communicates with Eidos via native Agent Skills and direct CLI execution.

    ### Claude Code (CLI)

    Claude Code natively supports a standardized, file-based plugin architecture:
    1. **Manifest (`plugin.json`)**: Plugin namespace, versioning, and metadata.
    2. **Commands (`commands/*.md`)**: Native slash commands (`/validate`, `/drift`, `/context`).
    3. **Skills (`skills/*/SKILL.md`)**: Domain expertise triggerable by user tasks.
    4. **Subagents**: Specialized roles dynamically forked by Claude.
    5. **Hooks (`hooks.json`)**: Lifecycle event handlers (pre-tool checks, lint gates).

    #### 1. Automatic Installation
    Install the Eidos Claude Code plugin into your project:

    ```bash
    eidos plugin install claude
    # or
    eidos plugin install --target claude
    ```

    This automatically:
    - Installs the full plugin bundle into `.claude/plugins/eidos/` (following Claude Code's Method 2: Project Root Injection) with `plugin.json`, `.claude-plugin/plugin.json`, and `hooks.json`.
    - Generates Claude Code interactive slash commands in `.claude/plugins/eidos/commands/` (`/validate`, `/drift`, `/context`).
    - Installs all 4 cohesive Eidos skills into `.claude/skills/<skill-name>/` so they are immediately discoverable by Claude Code's native `/skills` registry and direct command invocations (`/eidos`, `/eidos-spec`, `/eidos-plan`, `/eidos-ops`).
    - Registers and enables the plugin in `.claude/settings.json` (`plugins: [{"path": ".claude/plugins/eidos"}]`, `enabledPlugins: {"eidos": true, "eidos@plugins-dir": true}`).

    #### 2. Enforcing Rules in `CLAUDE.md`
    To ensure Claude Code adheres to Eidos architectural governance, reference the Eidos rules in your project's `CLAUDE.md`:

    ```markdown
    <!-- CLAUDE.md -->
    ## Eidos Architectural Guidelines
    - Adhere strictly to the guidelines in `.agents/plugins/eidos/skills/eidos/SKILL.md`.
    - Treat `eidos/` living specifications as the single source of truth.
    - Never create ADRs; all mutations must follow the SpecDelta lifecycle (`eidos delta create`).
    - Validate specifications with `eidos validate --json` before completing tasks.
    - Check for spec-to-code drift with `eidos drift --uncommitted --json`.
    ```

=== "Cursor"

    Cursor connects to Eidos via native Agent Skills (`.cursor/skills/`) and custom Cursor rules (`.cursor/rules/eidos.mdc`), invoking Eidos CLI commands.

    ### 1. Automatic Configuration

    Generate Cursor rules and 4 native Agent Skills:

    ```bash
    eidos plugin install cursor
    # or
    eidos plugin install --target cursor
    ```

    This automatically:
    - Generates `.cursor/rules/eidos.mdc` with `alwaysApply: true`.
    - Installs all 4 skills into `.cursor/skills/<skill-name>/` enabling Cursor's native Agent Skills progressive disclosure engine.

    ### 2. Cursor Rules (`.cursor/rules/eidos.mdc`)

    Cursor Composer and Chat agents follow `.cursor/rules/eidos.mdc`:

    ```markdown
    ---
    description: "Eidos living specifications and architectural governance"
    alwaysApply: true
    ---

    # Eidos Living Specifications

    This repository is governed by Eidos living specifications under `eidos/`.

    ## Core Principles
    - Consult living specifications before modifying code or proposing architectural changes.
    - Strictly enforce living specifications (NO ADRs). All architectural mutations follow the SpecDelta lifecycle (`eidos/deltas/`).
    - Preserve bidirectional invariant-test traceability (`POL-002`).
    - Verify specification schemas with `eidos validate eidos` (or `eidos validate --json`).
    - Verify synchronization with `eidos drift --uncommitted` (or `eidos drift --uncommitted --json`) before finalizing tasks.
    - Use Eidos CLI commands (`eidos context`, `eidos index search`, `eidos drift`) for real-time inspection.
    - Consult installed Agent Skills in `.cursor/skills/` for specialized architectural workflows.
    ```

=== "GitHub Copilot"

    GitHub Copilot integrates with Eidos using native **[GitHub Copilot Plugins](https://docs.github.com/en/copilot/concepts/agents/about-plugins)** adhering to **Agent Plugins 1.0** (`agent-plugins.org v1.0.0`), direct Agent Skills (`.github/skills/`), and repository settings (`.github/copilot/settings.json`).

    ### 1. Installation
    ```bash
    eidos plugin install copilot
    # or
    eidos plugin install --target copilot
    ```

    This automatically:
    - Installs the native Copilot plugin bundle into `.github/plugin/` with `plugin.json` (declaring `com.github.copilot` extension).
    - Installs all 4 Eidos skills flat into `.github/skills/<skill-name>/SKILL.md` where GitHub Copilot's Agent Skills engine natively scans and indexes them.
    - Configures `.github/copilot/settings.json` enabling the `eidos` plugin (`enabledPlugins: {"eidos": true}`).
    - Configures `.github/copilot-instructions.md` with Eidos living specification rules, safely demarcated with comment markers (`<!-- EIDOS_COPILOT_START -->` ... `<!-- EIDOS_COPILOT_END -->`) to preserve any custom user instructions.

    ### 2. Using with Copilot
    GitHub Copilot automatically discovers the installed skills under `.github/skills/`, activates the plugin declared in `.github/copilot/settings.json`, and loads `.github/copilot-instructions.md` in Copilot Chat and Copilot Agent mode—enforcing the single source of truth under `eidos/`, zero spec-to-code drift, and bidirectional invariant-test traceability.

=== "Aider"

    Aider pairs with Eidos living specifications by loading persistent conventions via `.aider.conf.yml` and `.aider.conventions.md`.

    ### 1. Installation
    ```bash
    eidos plugin install aider
    ```

    This automatically:
    - Installs all 4 individual Eidos skills into `.aider/skills/<skill-name>/SKILL.md`.
    - Generates `.aider.conventions.md` in the project root containing core Eidos guidelines (Single Source of Truth, No ADRs, Invariant traceability, spec validation, and drift gate) alongside an interactive index mapping tasks to `/read .aider/skills/<skill>/SKILL.md`.
    - Configures `.aider.conf.yml` to automatically include `.aider.conventions.md` under `read:`.

    ### 2. Using with Aider
    Run Aider as usual:
    ```bash
    aider
    ```
    Aider automatically ingests `.aider.conventions.md` as read-only context on launch. You can verify specifications from within Aider using `/run uv run eidos validate --json` and check drift using `/run uv run eidos drift --uncommitted --json`. When working on specific architectural areas, prompt Aider to load the corresponding skill with `/read .aider/skills/<skill-name>/SKILL.md`.

=== "Cline"

    Cline connects to Eidos through native Agent Skills (`.cline/skills/`) and plugin bundle (`.cline/plugins/eidos`). It natively discovers the repository's root `AGENTS.md` for architectural rules and conventions.

    ### 1. Installation
    ```bash
    eidos plugin install cline
    ```

    This automatically:
    - Installs the complete plugin bundle into `.cline/plugins/eidos/`.
    - Installs all 4 individual skills flat into `.cline/skills/<skill-name>/SKILL.md` where Cline's skill discovery engine indexes them for progressive disclosure.
    - Leverages the root `AGENTS.md` open standard without requiring redundant rule files.

=== "OpenCode"

    OpenCode integrates with Eidos via local plugin discovery under `.opencode/plugins/eidos` and flat skills under `.opencode/skills/`. OpenCode natively reads the root `AGENTS.md` for project instructions.

    ### 1. Installation
    ```bash
    eidos plugin install open-code
    # or
    eidos plugin install opencode
    ```

    This automatically:
    - Installs the Eidos plugin bundle into `.opencode/plugins/eidos`.
    - Installs all 4 individual skills flat into `.opencode/skills/<skill-name>/SKILL.md`.
    - Leverages the root `AGENTS.md` open standard without requiring redundant rule files.

=== "OpenAI Codex"

    OpenAI Codex utilizes portable agent plugin packages containing `plugin.json`, skills, and rules.

    ### 1. Installation
    ```bash
    eidos plugin install codex
    ```

    This automatically:
    - Scaffolds `.codex/plugin.json` declaring `com.openai` agent plugin extension and metadata.
    - Installs all 4 individual skills flat into `.codex/skills/<skill-name>/SKILL.md`.
    - Generates `.codex/rules.md` governing living spec workflows.

=== "Generic AI Agents (Windsurf, Roo Code, etc.)"

    Any terminal-capable AI coding assistant or agent can operate against Eidos living specifications by reading the root `AGENTS.md` and invoking Eidos CLI commands directly:

    ```bash
    # Check specification validity
    eidos validate --json

    # Inspect spec-to-code drift
    eidos drift --uncommitted --json

    # Extract architectural context slice
    eidos context -f <touched-file> --format json
    ```

---

## CLI Automation Commands for AI Agents

AI agents execute Eidos CLI commands directly from terminal / subshell tools. All core commands support machine-readable `--json` output:

| CLI Command | Flags / Options | Description |
| :--- | :--- | :--- |
| `eidos context` | `-f <file>`, `-c <id>`, `-t <task>`, `--tier <tier>`, `--format json` | Slices a compact architectural context subgraph (`anchor`, `guardrails`, `deep`). |
| `eidos index repo-map` | `--max-tokens <n>`, `--format json` | Generates an AST-compressed structural repository map (~98% token reduction). |
| `eidos index find-symbol` | `<query>`, `--limit <n>`, `--json` | Resolves classes, functions, and methods to exact file/line coordinates and owning specs. |
| `eidos index search` | `"<query>"`, `--limit <n>` | Performs column-weighted BM25 full-text search across living spec titles, rules, and invariants. |
| `eidos index blast-radius` | `<symbol>`, `--depth 2`, `--json` | Computes transitive dependency and governance blast radius for a symbol or spec path. |
| `eidos index status` | `--json` | Reports index health, cache size, AST telemetry, entity/edge counts, and auto-sync freshness. |
| `eidos index sync` | `--force` | Incrementally synchronizes or force-rebuilds the architectural knowledge graph. |
| `eidos drift` | `--uncommitted`, `--base <ref>`, `--json` | Inspects spec-to-code drift, substantive spec diffs, and polyglot `INV-*` test bindings. |
| `eidos validate` | `[PATH]`, `--json` | Validates all living specifications against their bundled v1 JSON Schemas and `x-eidos-ref` links. |
| `eidos info` | | Returns current engine version, loaded v1 schemas, and supported platforms. |
| `eidos spec list` | `--type <type>`, `--status <status>`, `--json` | Lists living specifications (including inline C4 Level 3 components `CMP-*`). |
| `eidos spec create` | `<type> <id> --title <title>` | Scaffolds a new specification document across all 16 schema types. |
| `eidos spec delete` | `<id_or_path> [--force]` | Safely deletes a living specification with cross-reference safety checks. |
| `eidos spec component` | `[promote\|inline] <id>` | Promotes inline C4 Level 3 components to `eidos/architecture/components/` or inlines them back. |
| `eidos delta create` | `<id> --title <title> --kind <feature\|enabler\|bug>` | Scaffolds a new SpecDelta mutation document (`DLT-*`) for Planned Mode. |
| `eidos delta verify` | `<id>`, `--json` | Verifies that all declared mutations and `expect:` post-conditions hold in the spec catalog. |
| `eidos delta audit` | `<id>`, `--strict`, `--json` | Audits removed/deprecated entities for dangling references vs. approved tombstones. |
| `eidos delta apply` | `<id>`, `--execute`, `--verify`, `--json` | Reconciles a SpecDelta in-place (`status: applied`) and optionally executes structural mutations. |
| `eidos delta changelog` | `--stdout`, `--output <file>` | Compiles Keep a Changelog release notes from Git tags and applied SpecDeltas. |
| `eidos init` | `[PATH]`, `--lite` | Bootstraps a repository with the full catalog or progressive 3-file Eidos Lite profile. |

---

### Machine-Readable JSON Output Reference

When agents pass `--json` (or `--format json`), Eidos outputs structured JSON payloads designed for programmatic consumption:

#### 1. Specification Validation (`eidos validate --json`)
```json
{
  "valid": true,
  "total": 62,
  "passed": 62,
  "failed": 0,
  "documents": [
    {
      "id": "CNT-EIDOS-CORE",
      "type": "tech/container",
      "is_valid": true,
      "path": "eidos/architecture/containers/core.adoc"
    }
  ]
}
```
* **Exit code**: `0` when all documents are valid, `1` when any schema or cross-reference error is detected.

#### 2. Drift Detection (`eidos drift --uncommitted --json`)
```json
{
  "is_clean": true,
  "drift_detected": false,
  "workflow_mode": "planned",
  "code_files": ["src/eidos/cli/main.py"],
  "spec_files": ["eidos/architecture/containers/cli.adoc"],
  "delta_files": ["eidos/deltas/dlt-2026-016.adoc"],
  "test_files": ["tests/test_cli_json.py"],
  "touched_components": ["CNT-EIDOS-CLI"],
  "trivially_covered_files": [],
  "valid_specs": 62,
  "total_specs": 62,
  "remediation_suggestions": []
}
```
* **Exit code**: `0` when clean, `1` when unmanaged code changes, trivial comment-only spec edits, or broken test bindings exist.

#### 3. Context Slicing (`eidos context -f src/eidos/cli/main.py --format json`)
```json
{
  "tier": "guardrails",
  "components": [
    {
      "id": "CNT-EIDOS-CLI",
      "name": "Eidos Command-Line Interface",
      "type": "tech/container",
      "package_path": "src/eidos/cli"
    }
  ],
  "contracts": [
    {
      "id": "CTR-EIDOS-CLI",
      "protocol": "cli_args",
      "owning_container": "CNT-EIDOS-CLI"
    }
  ],
  "capabilities": [
    {
      "id": "CAP-AGENT-001",
      "title": "Agent Adapter & Context Engine",
      "domain": "DOM-AGENT-INTELLIGENCE"
    }
  ],
  "invariants": [
    {
      "id": "INV-AGENT-GRAPH-INDEX-FRESHNESS",
      "rule": "Every CLI query lazily checks dirty status via SHA-256 and mtime hashing.",
      "has_test_coverage": true,
      "test_binding": "tests/test_graph_index.py::test_spike_b_incremental_dirty_check_and_lazy_sync"
    }
  ],
  "policies": [
    {
      "id": "POL-001-GIT-SOURCE-OF-TRUTH",
      "title": "Git Repository as the Single Source of Truth"
    }
  ]
}
```

---

## The 7 Constitutional Guardrails

Eidos embeds seven non-negotiable architectural guardrails in its master skill `skills/eidos/SKILL.md`. When working in an Eidos repository, every AI assistant is instructed to obey them:

### 1. Single Source of Truth (`POL-001`)
All product domains, capabilities, personas, C4 architecture containers/components, interface contracts, design systems, and policies live inside `eidos/` as structured AsciiDoc (`.adoc`) files with YAML frontmatter.

### 2. Zero ADRs (`POL-001`)
Standalone Architecture Decision Records (`docs/adr/`, `eidos/adr/`, or `ADR-*.md`) are strictly prohibited. Current architectural state lives in `eidos/architecture/`, durable design rationale and rejected alternatives are recorded in-place under the `decisions:` (`DEC-*`) frontmatter block of the owning specification, and transitions are governed by **SpecDeltas** (`eidos/deltas/dlt-*.adoc`).

### 3. Dual Workflow Mode Selection (`POL-003`)
Every task operates in either **Direct Mode** (fast, in-place co-evolution of code, tests, and owning specs for localized edits) or **Planned Mode** (formal SpecDelta `DLT-*` lifecycle for cross-container or breaking changes). Agents classify the workflow mode *before* writing code.

### 4. Mandatory Invariant-Test Traceability (`POL-002`)
Every capability invariant (`INV-*`) must declare a concrete `test_binding` (`path/to/test_file::test_function` or `path/to/test_file::TestClass::test_method`), and the bound test function must explicitly reference the `INV-*` ID.

### 5. Deterministic Engine Only (`POL-004`)
Eidos CLI commands are 100% deterministic local operations with zero external AI/LLM runtime calls. Agents always invoke Eidos CLI commands rather than guessing schemas or manually wiring symlinks.

### 6. Zero Unreconciled Drift
No task may conclude with failing specification schemas (`eidos validate eidos`) or unmanaged code-to-spec drift (`eidos drift --uncommitted`).

### 7. Mandatory Autonomous Adversarial Gap Self-Review
Before presenting work or concluding a task, the agent executes an autonomous adversarial review over its own `git diff`—auditing requirements completeness, edge cases, contract SemVer consistency (`CTR-*`), genuine test assertions (`INV-*`), and embedded decision capture (`DEC-*`)—and fixes any gaps immediately.

---

## Automated Lifecycle Hooks & the Block-Once Stop Gate (`eidos hook`)

In agent harnesses that support lifecycle hooks (Google Antigravity, Claude Code, Cursor, Cline, OpenCode, and Codex), Eidos registers two CLI-native hooks in `hooks.json`:

1. **`eidos-doc-linter` (`eidos hook lint`)**:
   * **PreToolUse Gate**: Intercepts file-creation and write tool calls before execution, blocking attempts to create prohibited standalone ADR files (`docs/adr/`, `ADR-*.md`).
   * **PostToolUse / PostInvocation Gate**: Automatically validates modified `.adoc` files against their v1 JSON Schemas and reports schema errors back to the agent immediately.
2. **`eidos-drift-gate` (`eidos hook drift`)**:
   * **Block-Once Stop Gate**: When an agent attempts to end its turn (`Stop` event), `eidos hook drift --event stop` runs `eidos drift --uncommitted`. If unmanaged code drift or broken test bindings are detected, Eidos blocks completion and injects the exact `remediation_suggestions` into the agent's context so it can self-correct.
   * **Loop Prevention (`eidos/.cache/stop-gate.json`)**: Eidos hashes the current drift state using SHA-256. If an agent is genuinely blocked by an external constraint and hits the exact same drift fingerprint twice in a row, the Block-Once mechanism allows the turn to stop cleanly so the agent can ask the human user for guidance.

---

## Bundled Skills for AI Agents

When using an agent client that supports skill bundles (such as Google Antigravity, Claude Code, Cursor, Cline, OpenCode, Codex, Aider, or GitHub Copilot), 4 cohesive workflow skills are available:

* **`eidos`**: Master workflow orchestrator (Direct vs. Planned Mode), token-efficient architectural context slicing (`eidos context`), and continuous spec-to-code drift detection (`eidos drift`).
* **`eidos-spec`**: Unified authoring reference for Product domains (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), Arc42/C4 architecture (`CNT-*`, inline and promoted `CMP-*`, `CTR-*`, embedded `decisions:`), Multi-Design Systems (`DS-*`, W3C Design Tokens, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`), and system policies/invariants (`POL-*`, `INV-*` with polyglot `file::func` test bindings).
* **`eidos-plan`**: Technical feasibility spikes (`EXP-*`), the SpecDelta mutation lifecycle (`DLT-*`, `create`, `verify`, `audit`, `apply`, `changelog`), and 5-dimension Staff Systems Architect reviews.
* **`eidos-ops`**: Greenfield repository initialization (`eidos init`, `eidos init --lite`), systematic reverse-engineering of brownfield codebases into living specifications, and Living Web Portal operations (`eidos portal serve`, `eidos portal build`).

---

## Plugin Lifecycle & Maintenance

Eidos provides a complete suite of commands to manage, inspect, and update the installed agent plugin:

### Inspect Plugin Manifest

Display full metadata and configuration for the Eidos Agent Plugin:

```bash
eidos plugin info
```

Output:

```text
╭──────────────────────── Eidos Agent Plugin Manifest ─────────────────────────╮
│ Plugin Identifier:  eidos                                                    │
│           Version:  0.8.0                                                    │
│       Description:  Git-native, agent-agnostic development AI ecosystem and  │
│                     lifecycle orchestration framework.                       │
│           License:  MIT                                                      │
│     Specification:  agent-plugins.org v1.0.0                                 │
│            Skills:  eidos, eidos-ops, eidos-plan, eidos-spec                 │
│       MCP Servers:  None                                                     │
│   Lifecycle Hooks:  eidos-doc-linter, eidos-drift-gate                       │
│        Extensions:  com.google.antigravity                                   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### Inspect Version Status

Compare the currently installed plugin against the version bundled with the Eidos engine:

```bash
eidos plugin version
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
│ Plugin Version         │ 0.8.0                                               │
│ Overall Status         │ CONFORMANT                                          │
│ Discovered Skills      │ eidos, eidos-ops, eidos-plan, eidos-spec            │
│ Configured MCP Servers │ None                                                │
│ Lifecycle Hooks        │ eidos-doc-linter, eidos-drift-gate                  │
│ Client Extensions      │ com.google.antigravity                              │
└────────────────────────┴─────────────────────────────────────────────────────┘
```

> [!NOTE]
> Under `agent-plugins.org v1.0.0`, MCP servers are strictly optional. Eidos plugins decommissioned local stdio MCP servers in favor of direct CLI execution with machine-readable `--json` output, providing superior reliability in cloud, sandbox, and container environments.

### Uninstall / Clean Up

To cleanly remove plugin files and agent configurations:

```bash
eidos plugin uninstall --target all
```
