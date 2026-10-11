# Installation

Eidos is packaged as `eidos-ai` on PyPI and supports Python 3.11, 3.12, 3.13, 3.14, and 3.15.

---

## Prerequisites

* **Python**: `>= 3.11`
* **Git**: Installed and available in your `PATH`

---

## Installing via uv (Recommended)

[uv](https://github.com/astral-sh/uv) is the recommended package manager for Eidos.

To add Eidos to your project environment:

```bash
uv add eidos-ai
```

To run Eidos directly without installing it into a project:

```bash
uvx eidos-ai --help
```

---

## Installing via pip

You can install Eidos directly into any virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install eidos-ai
```

---

## Installing via pipx

If you want the `eidos` CLI tool globally available across your system:

```bash
pipx install eidos-ai
```

---

## Optional Extras

Eidos provides optional feature sets that can be installed on demand:

| Extra | Description | Installation |
| :--- | :--- | :--- |
| `docs` | Documentation building with Zensical | `pip install "eidos-ai[docs]"` |
| `dev` | Test suite (`pytest`, `pytest-cov`, `ruff`) | `pip install "eidos-ai[dev]"` |

Using `uv`:

```bash
uv sync --all-extras --dev
```

---

## Verifying Installation

Verify that the CLI command is registered and accessible:

```bash
eidos --version
eidos --help
```

You are now ready to run the **[Quickstart Guide](quickstart.md)**!

