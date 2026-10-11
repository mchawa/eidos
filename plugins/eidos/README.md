# Eidos Agent Plugin

[![Agent Plugins 1.0.0](https://img.shields.io/badge/agent--plugins-1.0.0-blue)](https://agent-plugins.org/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)

A portable Agent Plugin conforming strictly to the [Agent Plugins 1.0.0 specification](https://agent-plugins.org/) and the [Agent Skills specification](https://agentskills.io/).

This plugin equips AI agents (Antigravity `agy`, Claude Code, Cursor, GitHub Copilot) with native understanding and control over **Eidos** — the Git-native, agent-agnostic development AI ecosystem and lifecycle orchestration framework.

---

## What It Provides

1. **Standard MCP Server (`mcp.json`)**:
   - `eidos_validate`: Validates all `.adoc` specifications against JSON/YAML schemas and checks cross-references.
   - `eidos_info`: Inspects Eidos engine version and loaded v1 schemas.
   - `eidos_list_components`: Lists monorepo containers, C4 components, internal modules, and package paths.
   - `eidos_read_spec`: Parses and extracts structured frontmatter and AsciiDoc sections.
   - `eidos_create_delta`: Scaffolds a new SpecDelta change file in `eidos/deltas/active/`.

2. **Agent Skill (`skills/eidos/SKILL.md`)**:
   - Conforms to `agentskills.io` standard.
   - Guides the AI agent through the Eidos 6-step lifecycle: Orient -> Policies -> SpecDelta -> Code & Invariant Tests -> Validate -> Reconcile.
   - Teaches the agent to navigate Arc42 sections 1-8, C4 Level 1/2/3 models, and monorepo configurations.
   - Strictly enforces the **NO ADRs** rule.

3. **Continuous Agent Awareness Rules (`rules/AGENTS.md`)**:
   - Automatically injected into agents working in an Eidos repository.

---

## Installation via CLI (Project Scope)

You can install this plugin into your local repository/workspace using the `eidos` CLI:

```bash
# Install to project scope (installs .agent-plugins/eidos and workspace configurations):
eidos plugin install

# Or target specific project configurations:
eidos plugin install --target workspace     # .agent-plugins/eidos (agent-plugins.org v1.0.0)
eidos plugin install --target antigravity   # .gemini/plugins/eidos (Antigravity workspace plugin)
eidos plugin install --target claude        # .claude/mcp.json (Claude Code project MCP)
eidos plugin install --target cursor        # .cursor/mcp.json (Cursor project MCP)
```

## Conformance Verification

You can validate this plugin package against the official `agent-plugins.org` v1.0.0 JSON schemas at any time:

```bash
eidos plugin validate
```
