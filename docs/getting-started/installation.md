# Installation

Eidos is packaged as `eidos-ai` on PyPI and supports Python 3.11, 3.12, 3.13, 3.14, and 3.15.

---

## Prerequisites

* **Python**: `>= 3.11`
* **Git**: Installed and available in your `PATH`

---

## Installing via uv tool (Recommended)

Eidos is a language-agnostic CLI tool that should be installed in an isolated tool environment rather than added to your application's runtime dependencies.

[uv](https://github.com/astral-sh/uv) is the recommended tool installer for Eidos:

```bash
uv tool install eidos-ai
```

To run Eidos ephemerally without installing it permanently:

```bash
uvx --from eidos-ai eidos --help
uvx --from eidos-ai eidos doctor
```

---

## Installing via pipx

You can also install the `eidos` CLI globally in an isolated environment using [pipx](https://pipx.pypa.io/):

```bash
pipx install eidos-ai
```

---

## Installing via Dev Container Feature (VS Code Dev Containers / Codespaces)

To provision `eidos` declaratively in a [Development Container](https://containers.dev/) or GitHub Codespace, add the official Eidos Dev Container Feature (`ghcr.io/mchawa/eidos/eidos:1`) to your `.devcontainer/devcontainer.json`:

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

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `version` | `string` | `"latest"` | Version of `eidos-ai` to install from PyPI (or `"latest"`). |
| `semantic` | `boolean` | `false` | Install optional local Harrier ONNX Hybrid RRF semantic search dependencies (`eidos-ai[semantic]`). |
| `pythonVersion` | `string` | `"3.14"` | Python version for `uv` to use when provisioning the isolated Eidos tool environment (`3.11`–`3.14` required when `semantic=true`). |

---

## Bundled Polyglot AST Engine & Optional Extras

`eidos-ai` bundles **`ast-grep-py` (`SgRoot`), `fastyaml-rs`, and `orjson` by default** as core dependencies, enabling out-of-the-box structural code indexing across 17 languages (Python, TypeScript, TSX, JavaScript, JSX, Go, Rust, Java, Kotlin, C, C++, C#, Ruby, PHP, Swift, Vue, and Svelte) and static test-binding verification across **Python, TypeScript, JavaScript, Go, Rust, Java, and Kotlin**.

Optional extras are available for documentation building and repository development:

| Extra | Description | Installation |
| :--- | :--- | :--- |
| `semantic` | Optional local Harrier-OSS-v1-0.6B ONNX semantic embedding engine (`onnxruntime`, `tokenizers`, `numpy`) | `uv tool install "eidos-ai[semantic]"` |
| `docs` | Documentation site building with Zensical | `uv tool install "eidos-ai[docs]"` |
| `dev` | Contributor test suite (`pytest`, `pytest-cov`, `ruff`) | `uv sync --all-extras --dev` |

---

## Verifying Installation

Verify that the CLI binary is registered on your `PATH` and inspect workspace health:

```bash
eidos --version
eidos doctor
```

You are now ready to run the **[Quickstart Guide](quickstart.md)**!

