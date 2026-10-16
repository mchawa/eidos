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

## Bundled Polyglot AST Engine & Optional Extras

`eidos-ai` bundles **`ast-grep-py` by default** as a core dependency (with automatic fallback to Python's standard library `ast` on platforms without native Tree-sitter wheels), enabling out-of-the-box structural code indexing and test-binding verification across **Python, TypeScript, JavaScript, Go, Rust, Java, and Kotlin**.

Optional extras are available for documentation building and repository development:

| Extra | Description | Installation |
| :--- | :--- | :--- |
| `ast` | Compatibility alias for polyglot AST code indexing via `ast-grep-py` (included in default install) | `uv tool install "eidos-ai[ast]"` or `pipx install "eidos-ai[ast]"` |
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

