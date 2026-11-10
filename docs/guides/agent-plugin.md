# AI Agent Integration

Eidos is **agent-agnostic** and **model-agnostic**. It provides a standardized plugin bundle conforming to **[agent-plugins.org v1.0.0](https://agent-plugins.org)**, 4 cohesive **Agent Skills** conforming to **[agentskills.io](https://agentskills.io)** across 9 supported AI coding assistants (**Google Antigravity**, **Claude Code**, **Cursor**, **Aider**, **Cline**, **Kilo Code**, **OpenCode**, **GitHub Copilot**, and **OpenAI Codex**), and an opt-in stateless `Stop` / `TaskComplete` lifecycle hook (`--hooks`) for the 6 hook-capable clients (**Google Antigravity**, **Claude Code**, **Cursor**, **Cline**, **GitHub Copilot**, and **OpenAI Codex**)—so agents can understand, query, and mutate your living specifications safely through direct CLI execution.

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

Modern AI coding agents (Claude Code, Cursor Composer, Google Antigravity, GitHub Copilot, OpenAI Codex, Cline, Kilo Code, OpenCode, Aider) operate inside terminal-capable sandboxes, subshells, and Docker containers. Eidos is built **CLI-first**:

* **Zero Daemon Flakiness**: No long-running background stdio server processes to crash, disconnect, deadlock, or require manual port management.
* **Universal Cloud & Sandbox Portability**: Works out-of-the-box in GitHub Codespaces, remote SSH sessions, CI/CD runners, and ephemeral cloud devboxes where local stdio daemon connections are unreliable.
* **Deterministic `--json` Contracts**: Every inspection, validation, and drift check command supports `--json` output, providing deterministic schemas that agents can parse programmatically without regex or LLM hallucination.
* **Token-Efficient Progressive Disclosure**: Through `eidos context`, agents extract targeted architectural slices (~140 to ~1,500 tokens) rather than dumping thousands of lines of documentation into the context window.
* **Strict Exit Code Semantics**: Commands return standard Unix exit codes (`0` for success/clean, `1` for validation errors or drift), enabling automated tool loops and pre-commit hooks to halt instantly on failure.

---

## One-Command Setup

Eidos automatically configures your project for all supported AI coding assistants in a single step:

```bash
# Full installation across all 9 supported AI assistants (skills + AGENTS.md, no hooks by default):
eidos plugin install all

# Opt in to native Stop / TaskComplete lifecycle hooks across hook-capable AI assistants:
eidos plugin install all --hooks

# Targeted installation for a specific assistant:
eidos plugin install antigravity
eidos plugin install cursor --hooks
eidos plugin install claude --hooks
eidos plugin install cline --hooks
eidos plugin install copilot --hooks
eidos plugin install codex --hooks
eidos plugin install kilocode
eidos plugin install open-code
eidos plugin install aider
```

### Installation Targets

| Target Argument | Target Path / Config | Opt-In `--hooks` Config | Description |
| :--- | :--- | :--- | :--- |
| `all` | All 9 environments below | All 6 hook files below | Installs plugins, skills, and configs for all 9 assistants (plus native `Stop` / `TaskComplete` hooks for the 6 hook-capable assistants when `--hooks` is passed) |
| `antigravity` | `.agents/plugins/eidos/`, `AGENTS.md` | `.agents/plugins/eidos/hooks.json` | Installs `agent-plugins.org v1.0.0` bundle with 4 skills and extensions (plus `hooks.json` when `--hooks` is passed) |
| `claude` | `.claude/skills/`, `CLAUDE.md`, `AGENTS.md` | `.claude/settings.json` (`hooks.Stop`) | Installs 4 Claude Code skills, imports `AGENTS.md` from `CLAUDE.md`, and wires `hooks.Stop` when `--hooks` is passed |
| `cursor` | `.agents/plugins/eidos/`, `AGENTS.md` | `.cursor/hooks.json` (`hooks.stop`, `loop_limit: 1`) | Installs `agent-plugins.org v1.0.0` bundle, updates `AGENTS.md`, and wires `.cursor/hooks.json` when `--hooks` is passed |
| `cline` | `.agents/skills/`, `AGENTS.md` | `.clinerules/hooks/TaskComplete` | Installs 4 Eidos skills into `.agents/skills/`, updates `AGENTS.md`, and wires executable `.clinerules/hooks/TaskComplete` when `--hooks` is passed |
| `copilot` | `.agents/plugins/eidos/`, `AGENTS.md` | `.github/hooks/hooks.json` (`hooks.agentStop`) | Installs `agent-plugins.org v1.0.0` bundle, updates `AGENTS.md`, and wires `.github/hooks/hooks.json` when `--hooks` is passed |
| `codex` | `.agents/plugins/eidos/`, `AGENTS.md` | `.codex/hooks.json` (`hooks.Stop`) | Installs `agent-plugins.org v1.0.0` bundle, updates `AGENTS.md`, and wires `.codex/hooks.json` when `--hooks` is passed |
| `kilocode` | `.agents/skills/`, `AGENTS.md` | _N/A_ | Installs 4 Eidos skills into `.agents/skills/` and updates `AGENTS.md` |
| `open-code` | `.agents/skills/`, `AGENTS.md` | _N/A_ | Installs 4 Eidos skills into `.agents/skills/` and updates `AGENTS.md` |
| `aider` | `.aider/skills/`, `.aider.conf.yml`, `AGENTS.md` | _N/A_ | Installs 4 Eidos skills into `.aider/skills/`, updates `AGENTS.md`, and appends `AGENTS.md` to `read:` in `.aider.conf.yml` |

### The Root `AGENTS.md` Block

Every target writes one shared Eidos block into your repository's root `AGENTS.md`, the cross-tool instruction file every supported assistant loads. Eidos owns **only** the text between its markers; everything else in `AGENTS.md` stays yours:

```markdown
# Acme Billing Service
Run `pnpm test` before committing.          <- yours, never touched

<!-- eidos:begin agents-md sha256:3f9a1c0b7e2d targets=claude:.claude/skills,cursor:.agents/plugins/eidos/skills · managed by `eidos plugin`; edits inside this block are overwritten -->
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
* **Left in place on uninstall**: `eidos plugin uninstall` removes skills, plugin files, and Eidos `Stop` hook entries while leaving the `AGENTS.md` / `CLAUDE.md` blocks in place; delete them by hand if you no longer want them.

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

    Cursor is an **[agent-plugins.org v1.0.0](https://agent-plugins.org/compatible-clients)** compatible client that discovers the canonical plugin bundle inside `.agents/plugins/eidos/` and reads the root `AGENTS.md` natively.

    ### 1. Automatic Configuration

    ```bash
    eidos plugin install cursor
    # Or with native .cursor/hooks.json Stop hook enabled:
    eidos plugin install cursor --hooks
    ```

    This automatically:
    - Installs the canonical `agent-plugins.org v1.0.0` plugin bundle (`plugin.json` and all 4 skills) into `.agents/plugins/eidos/`.
    - Writes the shared Eidos block into the root `AGENTS.md` (see [The Root `AGENTS.md` Block](#the-root-agentsmd-block)), which Cursor Composer and Chat agents load as project instructions.
    - When `--hooks` is passed, non-destructively registers the Eidos `stop` hook (`loop_limit: 1`) in `.cursor/hooks.json`.

=== "GitHub Copilot"

    GitHub Copilot is an **[agent-plugins.org v1.0.0](https://agent-plugins.org/compatible-clients)** compatible client that discovers the canonical plugin bundle inside `.agents/plugins/eidos/` and reads the root `AGENTS.md` natively.

    ### 1. Installation
    ```bash
    eidos plugin install copilot
    # Or with native .github/hooks/hooks.json agentStop hook enabled:
    eidos plugin install copilot --hooks
    ```

    This automatically:
    - Installs the canonical `agent-plugins.org v1.0.0` plugin bundle (`plugin.json` and all 4 Eidos skills) into `.agents/plugins/eidos/`.
    - Writes the shared Eidos block into the root `AGENTS.md`, which GitHub Copilot loads natively. Your own Copilot instruction files are never touched.
    - When `--hooks` is passed, non-destructively registers the Eidos `agentStop` hook in `.github/hooks/hooks.json`.

    ### 2. Using with Copilot
    GitHub Copilot automatically discovers the installed plugin and skills under `.agents/plugins/eidos/` and loads the root `AGENTS.md` in Copilot Chat and Copilot Agent mode—enforcing the single source of truth under `eidos/`, zero spec-to-code drift, and bidirectional invariant-test traceability.

=== "OpenAI Codex"

    OpenAI Codex utilizes portable `agent-plugins.org v1.0.0` packages inside `.agents/plugins/eidos/` and reads project instructions from the root `AGENTS.md` natively.

    ### 1. Installation
    ```bash
    eidos plugin install codex
    # Or with native .codex/hooks.json Stop hook enabled:
    eidos plugin install codex --hooks
    ```

    This automatically:
    - Installs the canonical `agent-plugins.org v1.0.0` plugin bundle (`plugin.json` and all 4 Eidos skills) into `.agents/plugins/eidos/`.
    - Writes the shared Eidos block into the root `AGENTS.md`, which Codex loads as project instructions.
    - When `--hooks` is passed, non-destructively registers the Eidos `Stop` hook in `.codex/hooks.json`.

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

When `--hooks` is passed to `eidos plugin install <target> --hooks` (preserved automatically by `eidos plugin update <target>`), Eidos registers its stateless `Stop` / `TaskComplete` lifecycle hook across the 6 hook-capable coding clients:

* **Google Antigravity**: `.agents/plugins/eidos/hooks.json` (`eidos-drift-gate.Stop`)
* **Claude Code**: `.claude/settings.json` (`hooks.Stop`)
* **Cursor**: `.cursor/hooks.json` (`hooks.stop` with `loop_limit: 1`)
* **Cline**: `.clinerules/hooks/TaskComplete` (executable `0o755` script invoking `eidos hook`)
* **GitHub Copilot**: `.github/hooks/hooks.json` (`hooks.agentStop`)
* **OpenAI Codex**: `.codex/hooks.json` (`hooks.Stop`)

How `eidos hook` works:
* **Stateless Unified Check**: When an agent finishes its turn (`Stop` / `TaskComplete` event), `eidos hook` runs `detect_drift(uncommitted=True)`, verifying living specification schemas and cross-references (`SpecValidator`), invariant test bindings (`INV-*`), and spec-to-code synchronization in a single pass.
* **Client-Aware 1-Turn Continuation**: On the first stop when drift or validation issues exist, `eidos hook` emits each client's native continuation payload (`{"decision": "continue", "reason": "..."}` in Antigravity, `{"followup_message": "..."}` in Cursor, `{"cancel": true, "contextModification": "...", "errorMessage": "..."}` in Cline, and `{"decision": "block", "reason": "..."}` in Claude Code, GitHub Copilot, and OpenAI Codex). Using each client's built-in in-memory `stdin` retry counter (`executionNum`, `loop_count`, `hookName`, `stop_hook_active`), `eidos hook` automatically allows completion on the second stop—requiring zero local state files and never trapping agents in infinite retry loops.
* **`eidos doctor` Hook Health Inspection**: `eidos doctor` inspects `.eidos-manifest.json` to distinguish targets intentionally installed without `--hooks` (`PASS` with `(hooks: disabled)`) from targets installed with `--hooks` (`PASS` with `(hooks: active)` when valid, or `FAIL` when the hook file or entry is missing or corrupted).

---

## Bundled Skills for AI Agents

When using any of the 9 supported agent clients (Google Antigravity, Claude Code, Cursor, Aider, Cline, Kilo Code, OpenCode, OpenAI Codex, or GitHub Copilot), 4 cohesive workflow skills are available:

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
│           Version:  0.23.0                                                   │
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
│ Plugin Version         │ 0.23.0                                              │
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
