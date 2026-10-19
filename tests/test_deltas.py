"""Regression tests for SpecDelta engine (A1-A4)."""

import datetime
import subprocess
from pathlib import Path

from eidos.core.lifecycle.delta_engine import (
    _evaluate_declarative_postcondition,
    _resolve_git_baseline_sha,
    create_delta,
    generate_collision_free_delta_id,
    verify_delta_mutations,
)
from eidos.core.lifecycle.initializer import init_repository
from eidos.core.lifecycle.scaffolder import create_spec


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@eidos.dev"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Eidos Test"], cwd=repo, check=True, capture_output=True)


def test_a1_git_baseline_resolves_dedicated_component_without_cmp_prefix(tmp_path: Path) -> None:
    """A1: _resolve_git_baseline_sha locates architecture/components/<slug>.adoc when cmp- prefix is stripped."""
    _init_git_repo(tmp_path)
    ok, msg, _ = init_repository(tmp_path, name="TestSys")
    assert ok, msg
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "container", "CNT-API", "API Container")
    cmp_path = create_spec(
        eidos_dir,
        "component",
        "CMP-AUTH",
        "Auth Component",
    )
    assert cmp_path.name == "auth.adoc"

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: baseline"], cwd=tmp_path, check=True, capture_output=True)

    dlt_path = create_delta(
        eidos_dir,
        raw_id="DLT-2026-001",
        title="Harden Auth Component",
        kind="enabler",
    )
    # Set mutation target to CMP-AUTH with modify operation
    dlt_text = dlt_path.read_text(encoding="utf-8")
    dlt_text = dlt_text.replace(
        "mutations: []",
        "mutations:\n  - operation: modify\n    target: CMP-AUTH\n    details: Update description in CMP-AUTH.",
    )
    dlt_path.write_text(dlt_text, encoding="utf-8")

    # Verify _resolve_git_baseline_sha recovers the git HEAD blob digest for CMP-AUTH (auth.adoc)
    git_sha = _resolve_git_baseline_sha(eidos_dir, dlt_path, "CMP-AUTH", cmp_path)
    assert git_sha is not None, "Expected _resolve_git_baseline_sha to resolve auth.adoc for CMP-AUTH"

    # Wipe the sqlite baseline table so verify_delta_mutations relies on git HEAD fallback
    db_path = eidos_dir / ".cache" / "index.db"
    if db_path.exists():
        db_path.unlink()

    # Mutate auth.adoc in working tree
    cmp_path.write_text(
        cmp_path.read_text(encoding="utf-8") + "\n== Security Hardening\nAdded MFA check.\n",
        encoding="utf-8",
    )

    ok_ver, msg_ver, report = verify_delta_mutations(eidos_dir, dlt_path.name)
    assert ok_ver, (
        f"Expected git baseline fallback to verify modified auth.adoc for CMP-AUTH, got: {msg_ver} ({report})"
    )


def test_a2_declarative_postcondition_does_not_fallback_to_frontmatter_when_body_empty(
    tmp_path: Path,
) -> None:
    """A2: Empty AsciiDoc body on a frontmatter document must not fall back to raw YAML frontmatter."""
    spec_file = tmp_path / "empty_body.adoc"
    spec_file.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-EMPTY\n"
        "title: Empty Body Component\n"
        "container: CNT-API\n"
        "status: active\n"
        "responsibility: Handle token validation\n"
        "---\n",
        encoding="utf-8",
    )

    mutation_yaml_leak = {
        "operation": "modify",
        "target": "CMP-EMPTY",
        "expect": {
            "body": {
                "contains": ["schemas/v1/tech/component.schema.yaml"],
            }
        },
    }
    diag: list[str] = []
    ok_leak = _evaluate_declarative_postcondition(
        mutation_yaml_leak,
        target="CMP-EMPTY",
        specs_by_id={},
        container_components={},
        invariant_map={},
        target_file=spec_file,
        diagnostics=diag,
    )
    assert ok_leak is False, f"Should not match raw YAML frontmatter when extracted_body is empty: {diag}"


def test_a3_git_baseline_memoizes_revisions_and_blobs(tmp_path: Path) -> None:
    """A3: _resolve_git_baseline_sha memoizes resolved revisions and blobs in rev_cache and blob_cache."""
    import hashlib
    from typing import Any

    _init_git_repo(tmp_path)
    ok, msg, _ = init_repository(tmp_path, name="TestSys")
    assert ok, msg
    eidos_dir = tmp_path / "eidos"

    cnt_path = create_spec(eidos_dir, "container", "CNT-API", "API Container")
    head_content = cnt_path.read_text(encoding="utf-8")
    head_sha = hashlib.sha256(cnt_path.read_bytes()).hexdigest()
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial container"], cwd=tmp_path, check=True, capture_output=True)

    dlt_path = create_delta(
        eidos_dir,
        raw_id="DLT-2026-002",
        title="Modify API Container",
        kind="enabler",
    )
    dlt_text = dlt_path.read_text(encoding="utf-8")
    dlt_text = dlt_text.replace(
        "mutations: []",
        "mutations:\n  - operation: modify\n    target: CNT-API\n    details: Update CNT-API container specification.",
    )
    dlt_path.write_text(dlt_text, encoding="utf-8")

    rev_cache: dict[Path, list[str]] = {}
    blob_cache: dict[tuple[str, str], tuple[bytes | None, dict[str, Any] | None]] = {}
    resolved_sha = _resolve_git_baseline_sha(
        eidos_dir,
        dlt_path,
        "CNT-API",
        cnt_path,
        rev_cache=rev_cache,
        blob_cache=blob_cache,
    )
    assert resolved_sha == head_sha
    assert dlt_path in rev_cache
    assert len(blob_cache) == 1

    # Unmodified CNT-API must report UNMODIFIED
    ok_unmod, _, rep_unmod = verify_delta_mutations(eidos_dir, dlt_path.name)
    assert not ok_unmod
    assert rep_unmod is not None and rep_unmod.mutations[0].status == "UNMODIFIED"

    # Modifying CNT-API away from HEAD baseline verifies as MODIFIED
    cnt_path.write_text(head_content + "\n== Updated API Section\n", encoding="utf-8")
    ok_ver, msg_ver, _ = verify_delta_mutations(eidos_dir, dlt_path.name)
    assert ok_ver, f"Expected modification away from HEAD baseline to verify: {msg_ver}"


def test_a4_generate_collision_free_delta_id_reads_frontmatter_and_loops_on_collision(
    tmp_path: Path,
) -> None:
    """A4: generate_collision_free_delta_id inspects frontmatter id on custom filenames and resolves collisions."""
    eidos_dir = tmp_path / "eidos"
    deltas_dir = eidos_dir / "deltas"
    deltas_dir.mkdir(parents=True)

    year = datetime.datetime.now(datetime.UTC).year
    custom_file = deltas_dir / "custom_delta_name.adoc"
    custom_file.write_text(
        "---\n"
        "type: delta/mutation\n"
        f"id: DLT-{year}-007-CUSTOM\n"
        "title: Custom Named Delta\n"
        "status: proposed\n"
        "---\n"
        "= Custom Named Delta\n",
        encoding="utf-8",
    )

    next_id = generate_collision_free_delta_id(eidos_dir, raw_id="auto", title="New Feature")
    assert next_id == f"DLT-{year}-008", f"Expected DLT-{year}-008 after DLT-{year}-007, got {next_id}"

    # Verify collision loop when an explicit ID + slug suffix + first hash already exist
    explicit_base = f"DLT-{year}-007"
    first_colliding = generate_collision_free_delta_id(
        eidos_dir,
        raw_id=f"{explicit_base}-CUSTOM",
        title="Custom Named Delta",
    )
    assert first_colliding != f"{explicit_base}-CUSTOM"
    (deltas_dir / f"{first_colliding.lower()}.adoc").write_text(
        f"---\ntype: delta/mutation\nid: {first_colliding}\ntitle: Colliding\nstatus: proposed\n---\n",
        encoding="utf-8",
    )
    second_colliding = generate_collision_free_delta_id(
        eidos_dir,
        raw_id=f"{explicit_base}-CUSTOM",
        title="Custom Named Delta",
    )
    assert second_colliding not in {f"{explicit_base}-CUSTOM", first_colliding}
