# Eidos CLI (`eidos`) Dev Container Feature

Installs the **Eidos** Git-native living specification & AI architecture orchestrator CLI (`eidos-ai`) system-wide into `/usr/local/bin/eidos` via `uv`.

## Example Usage

```json
{
  "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
  "features": {
    "ghcr.io/mchawa/eidos/eidos:1": {
      "version": "latest",
      "semantic": false,
      "pythonVersion": "3.14"
    }
  }
}
```

## Options

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `version` | `string` | `"latest"` | Version of `eidos-ai` to install from PyPI (or `"latest"`). |
| `semantic` | `boolean` | `false` | Install optional local Harrier ONNX Hybrid RRF semantic search dependencies (`eidos-ai[semantic]`). |
| `pythonVersion` | `string` | `"3.14"` | Python version for `uv` to use when provisioning the isolated Eidos tool environment (`3.11`–`3.14` required when `semantic=true`). |

## Customizations

### VS Code Extensions

- `asciidoctor.asciidoctor-vscode`
- `redhat.vscode-yaml`

