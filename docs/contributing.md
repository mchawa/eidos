# Contributing to Eidos

We welcome contributions to Eidos! Whether fixing a bug, improving documentation, or adding a new capability, this guide will help you get started.

---

## Development Environment Setup

### 1. Clone the Repository

```bash
git clone https://github.com/mchawa/eidos.git
cd eidos
```

### 2. Install Dependencies

Using `uv` (recommended):

```bash
uv sync --all-extras --dev
```

Or using standard `pip`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,docs]"
```

---

## Running Quality Checks

### 1. Validate Living Specifications

Eidos uses itself to govern its own architecture! Before submitting any changes, ensure all specifications validate:

```bash
uv run eidos validate eidos
```

### 2. Validate Agent Plugin

```bash
uv run eidos plugin validate
```

### 3. Run the Test Suite

All tests must pass with full coverage:

```bash
uv run pytest -v --cov=src/eidos --cov-report=term-missing
```

### 4. Code Formatting & Linting

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting:

```bash
uv run ruff check .
uv run ruff format --check .
```

---

## Building the Documentation

This documentation site is built with **Zensical**:

### Local Live-Reload Server

```bash
uv run zensical serve
```

Open `http://localhost:8000` to preview changes in real time.

### Static Production Build

```bash
uv run zensical build
```

---

## Commit Guidelines

We follow the Conventional Commits specification:

* `feat(...)`: A new feature or capability
* `fix(...)`: A bug fix
* `docs(...)`: Documentation changes
* `ci(...)`: CI/CD workflows and automation
* `refactor(...)`: Code changes that neither fix a bug nor add a feature
* `test(...)`: Adding or updating test suites

