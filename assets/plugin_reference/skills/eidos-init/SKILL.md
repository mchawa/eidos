---
name: eidos-init
description: Bootstrap and initialize a Git repository with an empty Eidos living specification skeleton tracked by .gitkeep files.
---

# Eidos Repository Initialization (`eidos-init`)

This skill guides AI agents through initializing a new or existing Git repository under **Eidos living specification governance**.

## Overview & Objectives

Initializing an Eidos workspace establishes the empty living specification skeleton, tracked in Git via `.gitkeep` files, and governance guardrails:

1. **Execute Initialization**: Run `eidos init [PATH]` in any new or existing repository.
2. **Skeleton Directory Structure Scaffolding**:
   - `eidos/.gitignore` — ignores auto-generated symlink views (e.g., `deltas/by-status/`).
   - `eidos/system.adoc` — root system context document defining business vision and technical profile.
   - `eidos/architecture/` — containers (`containers/`), components (`components/`), and interface contracts (`contracts/`).
   - `eidos/domains/` — bounded contexts and business capabilities (`capabilities/`).
   - `eidos/policies/` — system policies and guardrails (`POL-*`).
   - `eidos/personas/` — user roles and actor personas (`PER-*`).
   - `eidos/deltas/` — active and historical SpecDeltas (`DLT-*`).
   - `eidos/explorations/` — spike discoveries and hypothesis documents (`EXP-*`).
   - `eidos/design/` — client applications, screens, flows, and design systems.
3. **Architectural Index Synchronization**: Update and populate the embedded SQLite architectural knowledge graph index (`eidos/.cache/index.db`) via `eidos index sync`.

---

## Invocation

```bash
# Initialize current workspace with default name
eidos init .

# Initialize specific directory with custom name and title
eidos init /path/to/project --name my-service --title "My Microservice Platform"

# Force re-initialization (overwrites existing templates)
eidos init . --force
```

---

## The 7-Step Initialization Workflow

When tasked with initializing Eidos in a repository, follow this sequence:

### Step 1: Reconnaissance & Project Identity

Inspect the repository root to determine:
- **Project Name & Slug**: Derive from `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, or the root directory name.
- **Architectural Archetype**: `framework`, `cli_tool`, `library`, `saas_platform`, `api_service`, `mobile_app`, `daemon`, etc.
- **Primary Languages & Runtimes**: Node.js, Python 3.12, Rust, Go, TypeScript, Docker, etc.

### Step 2: Execute Initialization

Invoke `eidos init` via CLI:
- Scaffolds the complete `eidos/` empty directory layout with an empty `system.adoc` (without example specs).
- Places `.gitkeep` files in empty directories so they are tracked by Git.
- Creates self-contained `eidos/.gitignore` targeting `deltas/by-status/` and `.cache/`.
- Runs immediate validation check.
- Note: `eidos init` initializes Eidos specifications and does not install plugins (plugins can be installed separately via `eidos plugin install`).

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
- Declare `c4_containers`: If containers/services exist, scaffold container manifests via `eidos spec create container CNT-*`.

### Step 5: Review & Author Repository Policies (`eidos/policies/`)

If the project requires domain-specific or repository-wide guardrails (e.g. compliance, security, invariant test enforcement, zero-ADR), scaffold them via `eidos spec create policy POL-*`.

### Step 6: Validate Specification Integrity

Always run validation to confirm zero schema errors and clean cross-references:
```bash
eidos validate eidos

# Or machine-readable JSON for agents
eidos validate --json
```

### Step 7: Update Architectural Index

At the end of initialization, update and synchronize the embedded SQLite architectural knowledge graph index:
```bash
# Synchronize and populate the architectural knowledge graph index
eidos index sync

# Or force a complete rebuild of the index
eidos index sync --force

# Verify index status and entity counts
eidos index status
```

Updating the index builds `eidos/.cache/index.db` and ensures `eidos context`, `eidos index search`, and dependency blast-radius queries have up-to-date graph metadata immediately available.

---

## Post-Initialization Progression

Once initialization is complete and validated:

1. **For existing/brownfield codebases**:
   - Activate the `eidos-extract` skill to reverse-engineer components, contracts, domains, and capability invariants.

2. **For new/greenfield projects**:
   - Use `eidos-architecture` to model components and interface contracts.
   - Use `eidos-invariants` to define domain policies and test bindings.
   - Use `eidos-delta` to orchestrate new feature additions via SpecDeltas.

