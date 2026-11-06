# AI Agent Integration

Eidos is **agent-agnostic** and **model-agnostic**. It provides a standardized plugin bundle conforming to **[agent-plugins.org v1.0.0](https://agent-plugins.org)** and 4 cohesive **Agent Skills** conforming to **[agentskills.io](https://agentskills.io)** so any AI coding assistant—such as **Google Antigravity**, **Claude Code**, **Cursor**, **GitHub Copilot**, **Aider**, **Cline**, **OpenCode**, or **OpenAI Codex**—can understand, query, and mutate your living specifications safely through direct CLI execution.

```
                    ┌─────────────────────────────────────────┐
                    │          AI Coding Assistant            │
                    │ (Google Antigravity, Claude, Cursor, …) │
                    └────────────────────┬────────────────────┘
                                         │
                    Direct CLI / Skills  │   Rules & Guidelines
                    (eidos validate, …)  │   (root AGENTS.md block)
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

# Targeted installation for a specific assistant:
eidos plugin install cursor
eidos plugin install claude
eidos plugin install copilot
```

### Installation Targets

| Target Argument | Target Path / Config | Description |
| :--- | :--- | :--- |
| `all` | All environments below | Installs plugins and configs for Antigravity, Claude, Cursor, Aider, Cline, OpenCode, Codex, and GitHub Copilot |
| `project` | `.agents/plugins/eidos/`, `AGENTS.md` | Standard project-scope installation conforming strictly to `agent-plugins.org v1.0.0` |
| `antigravity` | `.agents/plugins/eidos/`, `AGENTS.md` | Installs plugin bundle with 4 skills, hooks, and extensions (agent-plugins.org v1.0.0) |
| `claude` | `.claude/skills/`, `.claude/settings.json`, `CLAUDE.md`, `AGENTS.md` | Installs 4 Claude Code skills, configures the Eidos `Stop` hook, and imports `AGENTS.md` from `CLAUDE.md` |
| `cursor` | `.cursor/skills/`, `AGENTS.md` | Installs 4 flat skills; Cursor reads the root `AGENTS.md` natively |
| `copilot` | `.github/plugin/`, `.github/skills/`, `.github/copilot/`, `AGENTS.md` | Installs GitHub Copilot plugin (Agent Plugins 1.0), 4 skills, and settings; Copilot reads the root `AGENTS.md` natively |
| `aider` | `.aider/skills/`, `.aider.conf.yml`, `AGENTS.md` | Installs 4 skills and adds `AGENTS.md` under `read:` in `.aider.conf.yml` |
| `cline` | `.cline/plugins/`, `.cline/skills/`, `AGENTS.md` | Installs plugin bundle and 4 flat skills; Cline reads the root `AGENTS.md` natively |
| `open-code` | `.opencode/plugins/`, `.opencode/skills/`, `AGENTS.md` | Installs plugin bundle and 4 flat skills; OpenCode reads the root `AGENTS.md` natively |
| `codex` | `.codex/plugin.json`, `.codex/skills/`, `AGENTS.md` | Installs OpenAI Codex plugin bundle with 4 skills; Codex reads the root `AGENTS.md` natively |

### The Root `AGENTS.md` Block

Every target writes one shared Eidos block into your repository's root `AGENTS.md`, the cross-tool instruction file every supported assistant loads. Eidos owns **only** the text between its markers; everything else in `AGENTS.md` stays yours:

```markdown
# Acme Billing Service
Run `pnpm test` before committing.          <- yours, never touched

<!-- eidos:begin agents-md sha256:3f9a1c0b7e2d targets=aider:.aider/skills,claude:.claude/skills · managed by `eidos plugin`; edits inside this block are overwritten -->
## Eidos Living Specifications
...short guardrails, key eidos commands, and every installed skill location...
<!-- eidos:end agents-md -->

## Team notes                                 <- yours, never touched
```

* **Coexists with your content**: Eidos appends the block if it is missing and afterwards updates it in place, wherever you move it. Line endings and encoding are preserved.
* **Shared across assistants**: `targets=` records each installed assistant and its skills directory, so installing several assistants yields one block listing every skill location, independent of install order.
* **Your edits are protected**: the `sha256:` stamp detects hand edits inside the block. Eidos then leaves the block alone (and says so) unless you run `eidos plugin install <target> --force`. `eidos plugin update` never overwrites an edited block.
* **Opt out**: add `<!-- eidos:agents-md off -->` anywhere in `AGENTS.md` (or `<!-- eidos:claude-import off -->` in `CLAUDE.md`) and Eidos will not write that block.
* **Symlinks are respected**: Eidos never writes through a symlinked `AGENTS.md` or `CLAUDE.md`; it reports a warning instead. If `CLAUDE.md` is a symlink to `AGENTS.md`, no import block is needed.
* **Left in place on uninstall**: `eidos plugin uninstall` removes skills and plugin files but leaves the `AGENTS.md` / `CLAUDE.md` blocks and the Aider `read:` entry; delete them by hand if you no longer want them.

> [!NOTE]
> **Upgrading from earlier releases**: per-assistant rule files and instruction blocks written by older Eidos versions are not migrated or removed. The root `AGENTS.md` block replaces them; you can delete the old files by hand.

---

## Assistant Integration Guides

Select your AI coding assistant below for step-by-step setup instructions and configuration examples:

=== "Google Antigravity"

    Google Antigravity natively discovers plugins adhering to the **[agent-plugins.org v1.0.0](https://agent-plugins.org)** standard inside `.agents/plugins/`.

    ### 1. Installation

    Run the installation command in your repository root:

    ```bash
    eidos plugin install antigravity
    ```

    This creates the following directory structure inside your project:

    ```text
    .agents/plugins/eidos/
    ├── plugin.json               # agent-plugins.org v1.0.0 manifest
    ├── hooks.json                # Stateless Stop drift gate hook
    └── skills/
        ├── eidos/                # Master orchestrator, context slicing & continuous drift detection
        ├── eidos-spec/           # Product, Arc42/C4 architecture, design systems & invariants (NO ADRs)
        ├── eidos-plan/           # Technical spikes (EXP-*), SpecDeltas (DLT-*) & Staff Architect review
        └── eidos-ops/            # Greenfield init, brownfield extraction & Living Web Portal
    ```

    ### 2. How Antigravity Uses Eidos

    * **Master Engineering Skill**: Antigravity indexes `skills/eidos/SKILL.md` to guide agent decision-making, strictly enforcing the 8 Constitutional Guardrails (Single Source of Truth, Zero ADRs, Dual Workflow Mode Selection, Mandatory Invariant-Test Traceability, CLI-First Discovery & Determinism, Subagent Context Propagation, Zero Unreconciled Drift, and Mandatory Autonomous Pre-Completion Review) alongside `eidos context`, `eidos index`, and `eidos drift`.
    * **Workflow-Aligned Agent Skills**: Antigravity agents invoke `/eidos-spec` to author or inspect living specs, schemas (`eidos spec schema`), and invariants, `/eidos-plan` for explorations, SpecDeltas, and Staff reviews, and `/eidos-ops` for repository initialization, brownfield extraction, and Web Portal operations.
    * **Stateless Stop Hook**: The `eidos-drift-gate` (`eidos hook`) runs at task completion (`Stop`) to verify AsciiDoc schemas, invariant test bindings, and spec-to-code synchronization.
    * **Root `AGENTS.md`**: The shared Eidos block in the repository's root `AGENTS.md` is loaded as a project rule and lists `.agents/plugins/eidos/skills/` as the skill location.

=== "Claude Code"

    Claude Code (Anthropic's agentic CLI) communicates with Eidos via project-native Agent Skills (`.claude/skills/`), the stateless `Stop` lifecycle hook in `.claude/settings.json`, and direct CLI execution.

    ### Claude Code (CLI)

    Claude Code natively discovers project-scoped skills and lifecycle hooks inside `.claude/`:
    1. **Skills (`.claude/skills/*/SKILL.md`)**: Domain expertise and orchestration workflows automatically discovered via progressive disclosure and directly invokable as `/eidos`, `/eidos-spec`, `/eidos-plan`, and `/eidos-ops`.
    2. **Hooks (`.claude/settings.json`)**: Deterministic `Stop` lifecycle hook (`eidos hook`) that checks specification validity, invariant test bindings, and spec-to-code drift when Claude finishes a turn.

    #### 1. Automatic Installation
    Configure Eidos for Claude Code in your project:

    ```bash
    eidos plugin install claude
    ```

    This automatically:
    - Installs all 4 cohesive Eidos skills (`eidos`, `eidos-spec`, `eidos-plan`, `eidos-ops`) into `.claude/skills/<skill-name>/SKILL.md` so they are immediately discovered by Claude Code on startup.
    - Non-destructively registers the Eidos `Stop` hook (`eidos hook`, or `uvx --from eidos-ai==<ver> eidos hook` when `--launcher uvx` is passed) under `hooks.Stop` in `.claude/settings.json` while preserving your existing settings and custom hooks.
    - Tracks managed skill files in `.claude/.eidos-manifest.json` for clean updates and uninstallation (`eidos plugin uninstall claude`).
    - Writes the shared Eidos block into the root `AGENTS.md` and a one-line import block into the root `CLAUDE.md` so Claude Code loads it as project memory (skipped if your `CLAUDE.md` already imports `@AGENTS.md`):

    ```markdown
    <!-- CLAUDE.md (your own content above/below is preserved) -->
    <!-- eidos:begin claude-import sha256:336cc4fbf19b · managed by `eidos plugin`; edits inside this block are overwritten -->
    @AGENTS.md
    <!-- eidos:end claude-import -->
    ```

=== "Cursor"

    Cursor connects to Eidos via native Agent Skills (`.cursor/skills/`) and the root `AGENTS.md`, which Cursor reads natively, invoking Eidos CLI commands.

    ### 1. Automatic Configuration

    ```bash
    eidos plugin install cursor
    ```

    This automatically:
    - Installs all 4 skills into `.cursor/skills/<skill-name>/` enabling Cursor's native Agent Skills progressive disclosure engine.
    - Writes the shared Eidos block into the root `AGENTS.md` (see [The Root `AGENTS.md` Block](#the-root-agentsmd-block)), which Cursor Composer and Chat agents load as project instructions.

=== "GitHub Copilot"

    GitHub Copilot integrates with Eidos using native **[GitHub Copilot Plugins](https://docs.github.com/en/copilot/concepts/agents/about-plugins)** adhering to **Agent Plugins 1.0** (`agent-plugins.org v1.0.0`), direct Agent Skills (`.github/skills/`), and repository settings (`.github/copilot/settings.json`).

    ### 1. Installation
    ```bash
    eidos plugin install copilot
    ```

    This automatically:
    - Installs the native Copilot plugin bundle into `.github/plugin/` with `plugin.json` (declaring `com.github.copilot` extension).
    - Installs all 4 Eidos skills flat into `.github/skills/<skill-name>/SKILL.md` where GitHub Copilot's Agent Skills engine natively scans and indexes them.
    - Configures `.github/copilot/settings.json` enabling the `eidos` plugin (`enabledPlugins: {"eidos": true}`).
    - Writes the shared Eidos block into the root `AGENTS.md`, which GitHub Copilot loads natively. Your own Copilot instruction files are never touched.

    ### 2. Using with Copilot
    GitHub Copilot automatically discovers the installed skills under `.github/skills/`, activates the plugin declared in `.github/copilot/settings.json`, and loads the root `AGENTS.md` in Copilot Chat and Copilot Agent mode—enforcing the single source of truth under `eidos/`, zero spec-to-code drift, and bidirectional invariant-test traceability.

=== "Aider"

    Aider pairs with Eidos living specifications by loading the root `AGENTS.md` as persistent read-only context via `.aider.conf.yml`.

    ### 1. Installation
    ```bash
    eidos plugin install aider
    ```

    This automatically:
    - Installs all 4 individual Eidos skills into `.aider/skills/<skill-name>/SKILL.md`.
    - Writes the shared Eidos block into the root `AGENTS.md`; the block lists `.aider/skills/<skill>/SKILL.md` as a skill location, serving as Aider's skill index.
    - Adds `AGENTS.md` under `read:` in `.aider.conf.yml`, preserving your existing keys and entries.

    ### 2. Using with Aider
    Run Aider as usual:
    ```bash
    aider
    ```
    Aider automatically ingests `AGENTS.md` as read-only context on launch. You can verify specifications from within Aider using `/run uv run eidos validate --json` and check drift using `/run uv run eidos drift --uncommitted --json`. When working on specific architectural areas, prompt Aider to load the corresponding skill with `/read .aider/skills/<skill-name>/SKILL.md`.

=== "Cline"

    Cline connects to Eidos through native Agent Skills (`.cline/skills/`) and plugin bundle (`.cline/plugins/eidos`). It natively discovers the repository's root `AGENTS.md` for architectural rules and conventions.

    ### 1. Installation
    ```bash
    eidos plugin install cline
    ```

    This automatically:
    - Installs the complete plugin bundle into `.cline/plugins/eidos/`.
    - Installs all 4 individual skills flat into `.cline/skills/<skill-name>/SKILL.md` where Cline's skill discovery engine indexes them for progressive disclosure.
    - Writes the shared Eidos block into the root `AGENTS.md`, which Cline reads natively, without redundant rule files.

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
    - Writes the shared Eidos block into the root `AGENTS.md`, which OpenCode reads natively, without redundant rule files.

=== "OpenAI Codex"

    OpenAI Codex utilizes portable agent plugin packages containing `plugin.json` and skills, and reads project instructions from the root `AGENTS.md` natively.

    ### 1. Installation
    ```bash
    eidos plugin install codex
    ```

    This automatically:
    - Scaffolds `.codex/plugin.json` declaring `com.openai` agent plugin extension and metadata.
    - Installs all 4 individual skills flat into `.codex/skills/<skill-name>/SKILL.md`.
    - Writes the shared Eidos block into the root `AGENTS.md`, which Codex loads as project instructions.

=== "Generic AI Agents (Windsurf, Roo Code, etc.)"

    Any terminal-capable AI coding assistant or agent can operate against Eidos living specifications by reading the root `AGENTS.md` and invoking Eidos CLI commands directly:

    ```bash
    # Check specification validity
    eidos validate --json

    # Inspect spec-to-code drift
    eidos drift --uncommitted --json

    # Extract architectural context slice
    eidos context -f <touched-file> --json
    ```

---

## CLI Automation Commands for AI Agents

AI agents execute Eidos CLI commands directly from terminal / subshell tools. All core commands support machine-readable `--json` output:

| CLI Command | Flags / Options | Description |
| :--- | :--- | :--- |
| `eidos context` | `-f <file>`, `-c <id>`, `-t <task>`, `--tier <tier>`, `--json` | Slices a compact architectural context subgraph (`anchor`, `guardrails`, `deep`). |
| `eidos index repo-map` | `--max-tokens <n>`, `--json` | Generates an AST-compressed structural repository map (~98% token reduction). |
| `eidos index find-symbol` | `<query>`, `--limit <n>`, `--json` | Resolves classes, functions, and methods to exact file/line coordinates and owning specs. |
| `eidos index search` | `"<query>"`, `--limit <n>`, `--json` | Performs two-tier column-weighted BM25 full-text search across living spec titles, rules, and invariants. |
| `eidos index blast-radius` | `<symbol>`, `--depth 2`, `--json` | Computes transitive dependency and governance blast radius for a symbol or spec path. |
| `eidos index status` | `--json` | Reports index health, cache size, AST telemetry, entity/edge counts, and auto-sync freshness. |
| `eidos index sync` | `--force` | Incrementally synchronizes or force-rebuilds the architectural knowledge graph. |
| `eidos drift` | `--uncommitted`, `--base <ref>`, `--json` | Inspects spec-to-code drift, boundary-aware spec anchoring, and polyglot `INV-*` test bindings. |
| `eidos validate` | `[PATH]`, `--json` | Validates all living specifications against their bundled v1 JSON Schemas, `x-eidos-ref` links, and bidirectional contract parity. |
| `eidos doctor` | `--path <path>`, `--json` | Runs workspace health diagnostics across CLI binary, living specs, index, and agent plugin hooks. |
| `eidos info` | | Returns current engine version, loaded v1 schemas, and supported platforms. |
| `eidos spec list` | `--type <type>`, `--status <status>`, `--include-archived`, `--json` | Lists living specifications across all 15 blueprint types. |
| `eidos spec show` | `<id_or_path>` | Displays frontmatter metadata and preview of a specification document. |
| `eidos spec schema` | `[type]`, `--json` | Inspects bundled v1 YAML frontmatter schemas, required/optional keys, nested sub-schemas, and valid templates. |
| `eidos spec create` | `<type> <id> --title <title> [--path <path>]` | Scaffolds a minimal draft blueprint across all 15 living specification types. |
| `eidos spec delete` | `<id_or_path> [--force]` | Safely deletes a living specification with cross-reference safety checks. |
| `eidos delta create` | `[id] --title <title> [--kind <feature\|enabler\|bug>] [--slug]` | Scaffolds a new collision-free SpecDelta mutation document (`DLT-*`) for Planned Mode. |
| `eidos delta list` | `--status <status>`, `--include-archived`, `--json` | Lists SpecDeltas in flat `eidos/deltas/` (and `archive/` when requested). |
| `eidos delta status` | `[id] [new_status]`, `--json` | Displays SpecDelta status summary or transitions a delta's lifecycle status. |
| `eidos delta verify` | `<id>`, `--json` | Verifies that all declared mutations and closed-schema `expect:` post-conditions (including dot-paths) hold in the spec catalog. |
| `eidos delta audit` | `<id>`, `--strict`, `--json` | Audits removed/deprecated entities via `file_tokens_fts` for dangling references vs. approved tombstones. |
| `eidos delta apply` | `<id>`, `--verify/--no-verify`, `--json` | Verifies declared mutations by default (`--verify`), validates the SpecDelta, and reconciles it in-place (`status: applied`). |
| `eidos delta archive` | `--keep-recent <n>`, `--dry-run`, `--json` | Partitions and slims older terminal SpecDeltas and Explorations into `archive/` subdirectories. |
| `eidos delta changelog` | `--stdout`, `--output <file>` | Compiles Keep a Changelog release notes from Git tags and applied SpecDeltas. |
| `eidos init` | `[PATH]`, `--title <title>`, `--force` | Bootstraps a repository with the 12-directory Eidos specification skeleton, `eidos/.gitignore`, and `eidos/system.adoc`. |

---

### Machine-Readable JSON Output Reference

When agents pass `--json`, Eidos outputs structured JSON payloads designed for programmatic consumption:

#### 1. Specification Validation (`eidos validate --json`)
```json
{
  "is_clean": true,
  "total_docs": 62,
  "valid_docs": 62,
  "schema_errors": {},
  "broken_references": []
}
```
* **Exit code**: `0` when all documents are valid (`is_clean: true`), `1` when any schema or cross-reference error is detected.

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
  "unmapped_code_files": [],
  "valid_specs": 62,
  "total_specs": 62,
  "validation_errors": [],
  "uncovered_invariants": [],
  "orphan_invariants": [],
  "remediation_suggestions": [],
  "trivially_covered_files": []
}
```
* **Exit code**: `0` when clean, `1` when unmanaged code changes, trivial comment-only spec edits, or broken test bindings exist.

#### 3. Context Slicing (`eidos context -f src/eidos/cli/main.py --json`)
```json
{
  "tier": "guardrails",
  "components": [
    {
      "id": "CNT-EIDOS-CLI",
      "title": "Eidos Command-Line Interface",
      "type": "tech/container",
      "source_path": "src/eidos/cli"
    }
  ],
  "contracts": [
    {
      "id": "CTR-EIDOS-CLI",
      "protocol": "cli_args",
      "container": "CNT-EIDOS-CLI"
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

## The 8 Constitutional Guardrails

Eidos embeds eight non-negotiable architectural guardrails in its master skill `skills/eidos/SKILL.md`. When working in an Eidos repository, every AI assistant is instructed to obey them:

### 1. Single Source of Truth
All product domains, capabilities, personas, C4 architecture containers/components, interface contracts, design systems, and policies live inside `eidos/` as structured AsciiDoc (`.adoc`) files with YAML frontmatter.

### 2. Zero ADRs
Standalone Architecture Decision Records (`docs/adr/`, `eidos/adr/`, or `ADR-*.md`) are strictly prohibited. Current architectural state lives in `eidos/architecture/`, durable design rationale and rejected alternatives are recorded in-place under the `decisions:` (`DEC-*`) frontmatter block of the owning specification, and transitions are governed by **SpecDeltas** (`eidos/deltas/dlt-*.adoc`).

### 3. Dual Workflow Mode Selection
Every task operates in either **Direct Mode** (fast, in-place co-evolution of code, tests, and owning specs for localized edits) or **Planned Mode** (formal SpecDelta `DLT-*` lifecycle for cross-container or breaking changes). Agents classify the workflow mode *before* writing code.

### 4. Mandatory Invariant-Test Traceability
Every capability invariant (`INV-*`) must declare a concrete `test_binding` (`path/to/test_file::test_function` or `path/to/test_file::TestClass::test_method`), and the bound test function must explicitly reference the `INV-*` ID.

### 5. CLI-First Discovery & Determinism
Eidos CLI commands are 100% deterministic local operations with zero external AI/LLM runtime calls. Agents always run Eidos knowledge graph and context CLI commands (`eidos context`, `eidos index repo-map`, `eidos index find-symbol`, `eidos index search`, `eidos index blast-radius` with `--json`) before falling back to raw file reads or `grep`/`find`, scaffold via `eidos spec create` / `eidos delta create`, and inspect v1 schemas via `eidos spec schema <type> [--json]`.

### 6. Subagent Context Propagation
Whenever an agent delegates coding, refactoring, or specification work to a worker subagent, it passes the relevant `eidos context --json` slice in the subagent's prompt or instructs the subagent to read `.agents/plugins/eidos/skills/eidos/SKILL.md` before editing files.

### 7. Zero Unreconciled Drift
No task may conclude with failing specification schemas (`eidos validate eidos`) or unmanaged code-to-spec drift (`eidos drift --uncommitted`).

### 8. Mandatory Autonomous Adversarial Gap Self-Review
Before presenting work or concluding a task, the agent executes an autonomous adversarial review over its own `git diff`—auditing requirements completeness, edge cases, contract SemVer consistency (`CTR-*`), genuine test assertions (`INV-*`), and embedded decision capture (`DEC-*`)—and fixes any gaps immediately.

---

## Stateless Stop Lifecycle Hook (`eidos hook`)

In agent harnesses that support lifecycle hooks (Google Antigravity, Claude Code, Cursor, Cline, OpenCode, and Codex), Eidos registers a single stateless `Stop` hook in `hooks.json`:

* **`eidos-drift-gate` (`eidos hook`)**:
  * **Stateless Unified Check**: When an agent finishes its task (`Stop` event), `eidos hook` runs `detect_drift(uncommitted=True)`, which verifies living specification schemas and cross-references (`SpecValidator`), invariant test bindings (`INV-*`), and spec-to-code synchronization in a single pass.
  * **Non-Blocking Advisory Warning**: Returns `{"decision": "allow"}` when clean, or `{"decision": "allow", "systemMessage": "⚠️ ..."}` with exact remediation steps when validation errors, broken invariant bindings, or code drift are detected—requiring zero state files, zero per-edit subprocesses, and never trapping agents in infinite retry loops.

---

## Bundled Skills for AI Agents

When using an agent client that supports skill bundles (such as Google Antigravity, Claude Code, Cursor, Cline, OpenCode, Codex, Aider, or GitHub Copilot), 4 cohesive workflow skills are available:

* **`eidos`**: Master workflow orchestrator (Direct vs. Planned Mode), token-efficient architectural context slicing (`eidos context`), and continuous spec-to-code drift detection (`eidos drift`).
* **`eidos-spec`**: Unified authoring reference for Product domains (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), Arc42/C4 architecture (`CNT-*`, standalone `CMP-*`, `CTR-*`, embedded `decisions:`), Multi-Design Systems (`DS-*`, W3C Design Tokens, `UIC-*`, `CLT-*`, `SCR-*`, `FLOW-*`), and system policies/invariants (`POL-*`, `INV-*` with polyglot `file::func` test bindings).
* **`eidos-plan`**: Technical feasibility spikes (`EXP-*`), the SpecDelta mutation lifecycle (`DLT-*`, `create`, `verify`, `audit`, `apply`, `changelog`), and 5-dimension Staff Systems Architect reviews.
* **`eidos-ops`**: Greenfield repository initialization (`eidos init`), systematic reverse-engineering of brownfield codebases into living specifications, and Living Web Portal operations (`eidos portal serve`, `eidos portal build`).

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
│           Version:  0.21.1                                                   │
│       Description:  Git-native, agent-agnostic development AI ecosystem and  │
│                     lifecycle orchestration framework.                       │
│           License:  MIT                                                      │
│     Specification:  agent-plugins.org v1.0.0                                 │
│            Skills:  eidos, eidos-ops, eidos-plan, eidos-spec                 │
│       MCP Servers:  None                                                     │
│   Lifecycle Hooks:  eidos-drift-gate                                         │
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
eidos plugin update all
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
│ Plugin Version         │ 0.21.1                                              │
│ Overall Status         │ CONFORMANT                                          │
│ Discovered Skills      │ eidos, eidos-ops, eidos-plan, eidos-spec            │
│ Configured MCP Servers │ None                                                │
│ Lifecycle Hooks        │ eidos-drift-gate                                    │
│ Client Extensions      │ com.google.antigravity                              │
└────────────────────────┴─────────────────────────────────────────────────────┘
```

> [!NOTE]
> Under `agent-plugins.org v1.0.0`, MCP servers are strictly optional. Eidos plugins decommissioned local stdio MCP servers in favor of direct CLI execution with machine-readable `--json` output, providing superior reliability in cloud, sandbox, and container environments.

### Uninstall / Clean Up

To cleanly remove plugin files and agent configurations:

```bash
eidos plugin uninstall all
```
