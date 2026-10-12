---
name: eidos-init
description: Bootstrap and initialize a Git repository with an empty Eidos living specification skeleton tracked by .gitkeep files.
---

# Eidos Repository Initialization (`eidos-init`)

This skill guides AI agents through initializing a new or existing Git repository under **Eidos living specification governance**.

## Overview & Objectives

Initializing an Eidos workspace establishes the empty living specification skeleton, tracked in Git via `.gitkeep` files, and governance guardrails:

```
  New or Existing Repository
  ┌────────────────────────────────────────────────────────┐
  │  eidos init [PATH]                                     │
  └────────────────────────┬───────────────────────────────┘
                           │ Scaffolds Empty Skeleton
                           ▼
  ┌────────────────────────────────────────────────────────┐
  │ eidos/                                                 │
  │ ├── .gitignore                  <- ignores symlink views│
  │ ├── system.adoc                 <- empty root system doc│
  │ ├── architecture/                                      │
  │ │   ├── components/             <- tracked by .gitkeep │
  │ │   └── contracts/              <- tracked by .gitkeep │
  │ ├── domains/                    <- tracked by .gitkeep │
  │ ├── policies/                   <- tracked by .gitkeep │
  │ ├── personas/                   <- tracked by .gitkeep │
  │ ├── deltas/                     <- tracked by .gitkeep │
  │ ├── explorations/               <- tracked by .gitkeep │
  │ └── design/                                            │
  │     ├── clients/                <- tracked by .gitkeep │
  │     ├── flows/                  <- tracked by .gitkeep │
  │     ├── screens/                <- tracked by .gitkeep │
  │     └── systems/core/components/<- tracked by .gitkeep │
  │                                                        │
  │ .agents/plugins/eidos/          <- AI agent plugin     │
  └────────────────────────────────────────────────────────┘
```

---

## Invocation Mechanisms

### 1. CLI Command

```bash
# Initialize current workspace with default name
eidos init .

# Initialize specific directory with custom name and title
eidos init /path/to/project --name my-service --title "My Microservice Platform"

# Initialize without automatically installing agent plugin
eidos init . --no-plugin

# Force re-initialization (overwrites existing templates)
eidos init . --force
```

### 2. MCP Tool Execution

```json
{
  "name": "eidos_init_repo",
  "arguments": {
    "project_dir": ".",
    "name": "ecommerce-backend",
    "title": "E-Commerce Core API Platform",
    "with_plugin": true,
    "force": false
  }
}
```

---

## The 6-Step Initialization Workflow

When tasked with initializing Eidos in a repository, follow this sequence:

### Step 1: Reconnaissance & Project Identity

Inspect the repository root to determine:
- **Project Name & Slug**: Derive from `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, or the root directory name.
- **Architectural Archetype**: `framework`, `cli_tool`, `library`, `saas_platform`, `api_service`, `mobile_app`, `daemon`, etc.
- **Primary Languages & Runtimes**: Node.js, Python 3.12, Rust, Go, TypeScript, Docker, etc.

### Step 2: Execute Initialization

Invoke `eidos init` via CLI or `eidos_init_repo` via MCP:
- Scaffolds the complete `eidos/` empty directory layout with an empty `system.adoc` (without example specs).
- Places `.gitkeep` files in empty directories so they are tracked by Git.
- Creates self-contained `eidos/.gitignore` targeting `deltas/by-status/`.
- Installs the Eidos Agent Plugin to `.agents/plugins/eidos`.
- Runs immediate validation check.

### Step 3: Author Root System Vision (`eidos/system.adoc`)

Update `eidos/system.adoc` with the project's actual domain purpose:
- **`archetypes`**: List all matching archetypes (e.g. `[api_service, data_pipeline]`).
- **`business_identity`**:
  - `vision`: Strategic purpose and mission statement.
  - `target_users`: Key stakeholders and end users.
  - `value_proposition`: Differentiating value delivered to users.
- **`technical_profile`**:
  - `primary_languages`: Main languages in the repository.
  - `runtimes_and_frameworks`: Runtimes, frameworks, and tools.
  - `deployment_targets`: Infrastructure environments.

### Step 4: Map Initial Architectural Topology (`eidos/architecture/system-architecture.adoc`)

Review and flesh out the C4 Level 1-2 architecture:
- Set `topology_style`: `monolithic`, `modular_monolith`, `microservices`, `serverless`, `library_or_sdk`, etc.
- Verify `c4_context.actors`: Ensure all persona links resolve (e.g. `eidos/personas/autonomous-agent.adoc`).
- Define `c4_context.external_systems`: Third-party APIs, authentication providers, remote databases.
- Declare `c4_containers`: If containers/services exist, add component references or scaffold manifests via `eidos spec create component`.

### Step 5: Review & Enforce Baseline Policies

Eidos initializes two non-negotiable policies:
1. `POL-001-GIT-SOURCE-OF-TRUTH`: Ensures all specifications and architectures are versioned in Git.
2. `POL-002-INVARIANT-TEST-ENFORCEMENT`: Mandates that all capability invariants have automated test bindings.

If the project requires domain-specific guardrails (e.g. compliance, security, zero-ADR), add them via `eidos spec create policy`.

### Step 6: Validate Specification Integrity

Always run validation to confirm zero schema errors and clean cross-references:
```bash
eidos validate eidos
```
Or via MCP:
```json
{"name": "eidos_validate", "arguments": {"path": "eidos"}}
```

---

## Post-Initialization Progression

Once initialization is complete and validated:

1. **For existing/brownfield codebases**:
   - Activate the `eidos-extract` skill to reverse-engineer components, contracts, domains, and capability invariants.

2. **For new/greenfield projects**:
   - Use `eidos-architecture` to model components and interface contracts.
   - Use `eidos-invariants` to define domain policies and test bindings.
   - Use `eidos-delta` to orchestrate new feature additions via SpecDeltas.

