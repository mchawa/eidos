"""Repository initializer: bootstraps living specification skeletons, baseline governance, and gitignore."""

import os
import re
from pathlib import Path

from eidos.core.lifecycle.delta_engine import _ensure_eidos_gitignore
from eidos.core.spec.parser import dump_spec_document
from eidos.core.spec.validator import SpecValidator


def init_repository(
    project_dir: Path,
    name: str | None = None,
    title: str | None = None,
    eidos_dir_name: str = "eidos",
    force: bool = False,
    lite: bool = False,
) -> tuple[bool, str, list[Path]]:
    """Initialize a repository with an Eidos living specification skeleton and governance."""
    eidos_dir = project_dir / eidos_dir_name
    created_files: list[Path] = []

    if eidos_dir.exists() and any(eidos_dir.iterdir()) and not force:
        return (
            False,
            f"Eidos specifications already exist at '{eidos_dir}'. Use --force to re-initialize.",
            [],
        )

    # Standard vs Progressive Lite Eidos living specification directory skeleton
    if lite:
        skeleton_dirs = [
            eidos_dir / "architecture" / "containers",
            eidos_dir / "domains" / "core" / "capabilities",
            eidos_dir / "deltas",
        ]
    else:
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

    # Root system.adoc: scaffold product/system frontmatter when name or title is provided,
    # otherwise create 0-byte system.adoc ready for user authoring.
    sys_file = eidos_dir / "system.adoc"
    if name or title:
        raw_slug = re.sub(r"[^A-Z0-9_-]+", "-", (name or title or "SYSTEM").strip().upper()).strip("-") or "SYSTEM"
        sys_id = raw_slug if raw_slug.startswith("SYS-") else f"SYS-{raw_slug}"
        sys_title = (title or name or sys_id).strip()
        sys_fm = {
            "type": "product/system",
            "schema": "schemas/v1/product/system.schema.yaml",
            "id": sys_id,
            "title": sys_title,
            "status": "draft",
            "archetypes": ["service"],
            "business_identity": {
                "vision": f"System vision for {sys_title}.",
                "target_users": ["Developers"],
                "value_proposition": f"Core capabilities delivered by {sys_title}.",
            },
            "technical_profile": {
                "primary_languages": ["AsciiDoc"],
            },
        }
        sys_body = f"= System: {sys_title}\n:toc: left\n\n== Strategic Vision\n{sys_fm['business_identity']['vision']}\n"
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

    return True, f"Successfully initialized empty Eidos living specification skeleton at '{eidos_dir}'.", created_files
