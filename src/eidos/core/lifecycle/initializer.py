"""Repository initializer: bootstraps living specification skeletons, baseline governance, and gitignore."""

import contextlib
import os
import re
from pathlib import Path

from eidos.core.spec.parser import dump_spec_document
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import render_spec_template

_PROJECT_ROOT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "src")

_GITHUB_CI_WORKFLOW_TEMPLATE = """name: Eidos Living Spec Drift Gate

on:
  pull_request:

jobs:
  eidos-drift:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository (full history for merge-base diff)
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Validate living specifications
        run: uvx --from eidos-ai eidos validate

      - name: Check spec-to-code drift
        run: >-
          uvx --from eidos-ai eidos drift
          --base "origin/${{ github.base_ref }}"
          --markdown "$GITHUB_STEP_SUMMARY"
"""

_GITLAB_CI_WORKFLOW_TEMPLATE = """# Include this template from your root .gitlab-ci.yml:
#   include:
#     - local: '.gitlab-ci.eidos.yml'

eidos-governance:
  stage: test
  image: ghcr.io/astral-sh/uv:python3.12-bookworm-slim
  variables:
    GIT_DEPTH: "0"
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  script:
    - git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - uvx --from eidos-ai eidos validate
    - uvx --from eidos-ai eidos drift --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
"""

_CI_WORKFLOW_TEMPLATES: dict[str, tuple[str, str]] = {
    "github": (".github/workflows/eidos-drift.yml", _GITHUB_CI_WORKFLOW_TEMPLATE),
    "gitlab": (".gitlab-ci.eidos.yml", _GITLAB_CI_WORKFLOW_TEMPLATE),
}


def _is_explicit_eidos_spec_dir(path: Path, eidos_dir_name: str = "eidos") -> bool:
    """Return True when ``path`` targets an ``eidos/`` specification directory rather than a project root."""
    return path.name == eidos_dir_name and not any((path / m).exists() for m in _PROJECT_ROOT_MARKERS)


def _ensure_eidos_gitignore(eidos_dir: Path) -> None:
    """Ensure eidos/.gitignore is present and ignores local SQLite cache so host projects remain unpolluted."""
    with contextlib.suppress(OSError):
        eidos_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = eidos_dir / ".gitignore"
    default_content = "# Eidos local index cache\n.cache/\n\n# OS artifacts\n.DS_Store\nThumbs.db\n"
    if not gitignore_path.exists():
        with contextlib.suppress(OSError):
            gitignore_path.write_text(default_content, encoding="utf-8")
    else:
        with contextlib.suppress(OSError):
            content = gitignore_path.read_text(encoding="utf-8")
            if ".cache" not in content:
                gitignore_path.write_text(
                    f"{content.rstrip()}\n\n# Eidos local index cache\n.cache/\n",
                    encoding="utf-8",
                )


def init_repository(
    project_dir: Path,
    title: str | None = None,
    eidos_dir_name: str = "eidos",
    force: bool = False,
    ci: str | None = None,
) -> tuple[bool, str, list[Path]]:
    """Initialize a repository with an Eidos living specification skeleton, governance, and optional CI workflow."""
    ci_provider = ci.strip().lower() if ci is not None else None
    if ci_provider is not None and ci_provider not in _CI_WORKFLOW_TEMPLATES:
        supported = ", ".join(sorted(_CI_WORKFLOW_TEMPLATES))
        raise ValueError(f"Unsupported --ci provider '{ci}'. Supported providers: {supported}.")

    is_explicit_eidos = _is_explicit_eidos_spec_dir(project_dir, eidos_dir_name)
    eidos_dir = project_dir if is_explicit_eidos else project_dir / eidos_dir_name
    repo_root = eidos_dir.parent if is_explicit_eidos else project_dir
    created_files: list[Path] = []

    non_cache_entries = (
        [p for p in eidos_dir.iterdir() if p.name not in (".cache", ".gitignore", ".DS_Store", "Thumbs.db")]
        if eidos_dir.exists()
        else []
    )
    if non_cache_entries and not force:
        return (
            False,
            f"Eidos specifications already exist at '{eidos_dir}'. Use --force to re-initialize.",
            [],
        )

    # Standard Eidos living specification directory skeleton
    skeleton_dirs = [
        eidos_dir / "architecture" / "containers",
        eidos_dir / "architecture" / "components",
        eidos_dir / "architecture" / "contracts",
        eidos_dir / "domains",
        eidos_dir / "policies",
        eidos_dir / "personas",
        eidos_dir / "deltas",
        eidos_dir / "explorations",
        eidos_dir / "design" / "clients",
        eidos_dir / "design" / "flows",
        eidos_dir / "design" / "screens",
        eidos_dir / "design" / "systems" / "core" / "components",
    ]
    for d in skeleton_dirs:
        d.mkdir(parents=True, exist_ok=True)
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()
        if gitkeep not in created_files:
            created_files.append(gitkeep)

    # Ensure any additional empty leaf directories under eidos_dir are tracked by .gitkeep
    for current_dir, dirnames, filenames in os.walk(eidos_dir):
        p = Path(current_dir)
        if ".cache" in p.parts:
            continue
        if not dirnames and not filenames:
            gitkeep = p / ".gitkeep"
            gitkeep.touch()
            if gitkeep not in created_files:
                created_files.append(gitkeep)

    # Ensure self-contained eidos/.gitignore
    _ensure_eidos_gitignore(eidos_dir)
    gitignore_file = eidos_dir / ".gitignore"
    if gitignore_file.exists() and gitignore_file not in created_files:
        created_files.append(gitignore_file)

    # Root system.adoc: scaffold product/system frontmatter when title is provided,
    # otherwise create 0-byte system.adoc ready for user authoring.
    sys_file = eidos_dir / "system.adoc"
    if title:
        clean_title = title.strip()
        raw_slug = re.sub(r"[^A-Z0-9_-]+", "-", (clean_title or "SYSTEM").upper()).strip("-") or "SYSTEM"
        sys_id = raw_slug if raw_slug.startswith("SYS-") else f"SYS-{raw_slug}"
        sys_title = clean_title or sys_id
        sys_fm = {
            "type": "product/system",
            "id": sys_id,
            "title": sys_title,
            "status": "draft",
            "archetypes": ["service"],
        }
        sys_body = render_spec_template("product/system", heading=sys_title)
        sys_file.write_text(dump_spec_document(sys_fm, sys_body), encoding="utf-8")
    elif not sys_file.exists():
        sys_file.write_text("", encoding="utf-8")
    if sys_file not in created_files:
        created_files.append(sys_file)

    # Validate directory immediately
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir)
    if not report.is_clean:
        errs = []
        for elist in report.schema_errors.values():
            errs.extend(elist)
        for b in report.broken_references:
            errs.append(f"Broken ref: {b}")
        return False, f"Scaffolded specifications failed validation: {'; '.join(errs)}", created_files

    # Optional turnkey CI workflow template (non-destructive unless force=True)
    if ci_provider is not None:
        rel_ci_path, template_text = _CI_WORKFLOW_TEMPLATES[ci_provider]
        ci_file = repo_root / rel_ci_path
        if not ci_file.exists() or force:
            ci_file.parent.mkdir(parents=True, exist_ok=True)
            ci_file.write_text(template_text, encoding="utf-8")
            created_files.append(ci_file)

    return True, f"Successfully initialized empty Eidos living specification skeleton at '{eidos_dir}'.", created_files
