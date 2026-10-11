# AI Agent Integration

Eidos is **agent-agnostic**. It provides a standardized plugin and **Model Context Protocol (MCP)** server so any AI coding assistant can understand, query, and mutate your living specifications safely.

---

## Supported Agent Platforms

* **Google Antigravity**: Native skill bundle & rule loading in `.agents/plugins/eidos/`.
* **Claude Code**: MCP server integration via `claude_desktop_config.json` or `.mcp.json`.
* **Cursor**: MCP server integration via Cursor settings.
* **OpenAI Codex / Custom Agents**: Standard stdio MCP protocol (`eidos mcp`).

---

## Configuring the MCP Server

The Eidos MCP server exposes high-level tools to AI agents:

| MCP Tool | Description |
| :--- | :--- |
| `eidos_validate` | Validate all living specifications and report schema errors |
| `eidos_get_architecture` | Retrieve C4 system topology, containers, and components |
| `eidos_get_component` | Inspect a specific component manifest and its contracts |
| `eidos_get_invariants` | Query all active system policies and capability invariants |
| `eidos_create_delta` | Scaffold a new SpecDelta mutation before editing code |
| `eidos_apply_delta` | Reconcile an applied SpecDelta after implementation |

### Configuring in Claude Code or Cursor (`.mcp.json`):

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

---

## Agent Plugin Conformance

Eidos packages an agent plugin complying with the **[agent-plugins.org v1.0.0](https://agent-plugins.org)** standard.

To verify plugin conformance in your project:

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
└────────────────────────┴─────────────────────────────────────────────────────┘
```

---

## Bundled Skills for AI Agents

When the Eidos plugin is active, your AI agent automatically accesses specialized domain skills:

* `eidos`: High-level navigation and orchestration of living specs.
* `eidos-architecture`: Authoring and maintaining Arc42 / C4 Level 1–3 topology.
* `eidos-delta`: Proposing, executing, and reconciling SpecDeltas.
* `eidos-invariants`: Enforcing policy invariants and test traceability.
* `eidos-extract`: Reverse-engineering living specifications from existing repositories.
* `eidos-init`: Bootstrapping new projects with Eidos governance.

