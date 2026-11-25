# Contributing to Eidos

We welcome contributions to Eidos! Whether fixing a bug, improving documentation, or adding a new capability, this guide will help you get started.

---

## Development Environment Setup

### 1. Clone the Repository

```bash
git clone https://github.com/mchawa/eidos.git
cd eidos
```

### 2. Install Dependencies & Editable CLI Tool

Using `uv`:

```bash
uv sync --all-extras --dev
uv tool install --force --editable .
```

---

## Running Quality Checks

### 1. Validate Living Specifications & Check Drift

Eidos uses itself to govern its own architecture! Before submitting any changes, ensure all specifications validate and there is zero unreconciled spec-to-code drift:

```bash
uv run eidos validate eidos
uv run eidos drift --uncommitted
```

### 2. Validate Agent Plugin

```bash
uv run eidos plugin validate
```

### 3. Run the Test Suite

All tests run in parallel by default via `pytest-xdist` (`-n auto --dist worksteal` in `pyproject.toml`) and must satisfy `POL-STATEMENT-TEST-COVERAGE` (`>90%` statement coverage for every Python file under `src/eidos/`, with `fail_under = 90` enforced in `pyproject.toml`):

```bash
uv run pytest -v --cov=src/eidos --cov-report=term-missing
```

To run serially (for example, when stepping through a single test with `--pdb` or `-s`), override with `-n 0`:

```bash
uv run pytest -n 0 -k test_name
```

### 4. Code Formatting & Linting

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting:

```bash
uv run ruff check src/ tests/
uv run ruff format --check .
```

---

## Building the Documentation

This documentation site is built with **Zensical** (`zensical.toml`):

### 1. Generate the Living Architecture Changelog

Generate `CHANGELOG.md` and `docs/changelog.md` from applied SpecDeltas and Git tags:

```bash
uv run eidos delta changelog
```

### 2. Local Live-Reload Server

```bash
uv run zensical serve
```

Open `http://localhost:8000` to preview changes in real time.

### 3. Strict Static Production Build

```bash
uv run zensical build --strict
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

---

## Creating a Release

Releases are driven by annotated SemVer Git tags (`vX.Y.Z`) via `hatch-vcs` and [.github/workflows/ci.yml](https://github.com/mchawa/eidos/blob/main/.github/workflows/ci.yml). Always push `main` **first** and push the tag **last** as a separate command so `origin/main` is updated before the release tag triggers (the tag workflow automatically cancels superseded `main` push CI runs without ever being cancelled by `main`):

```bash
# 1. Update version examples in docs/guides/agent-plugin.md and commit
git commit -m "chore(release): bump version to vX.Y.Z"

# 2. Push main first, then create and push the release tag last
git push origin main
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

