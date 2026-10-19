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
        container="CNT-API",
    )
    assert cmp_path.name == "auth.adoc"

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: baseline"], cwd=tmp_path, check=True, capture_output=True)

    dlt_path = create_delta(
        eidos_dir,
        raw_id="DLT-2026-001",
        title="Harden Auth Component",
        kind="enabler",
        motivation="Update description in CMP-AUTH.",
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
    assert ok_ver, f"Expected git baseline fallback to verify modified auth.adoc for CMP-AUTH, got: {msg_ver} ({report})"


def test_a2_declarative_postcondition_does_not_fallback_to_frontmatter_when_body_empty(
    tmp_path: Path,
) -> None:
    """A2: Empty AsciiDoc body on a frontmatter document must not fall back to raw YAML frontmatter."""
    spec_file = tmp_path / "empty_body.adoc"
    spec_file.write_text(
        "---\n"
        "type: tech/component\n"
        "schema: schemas/v1/tech/component.schema.yaml\n"
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


def test_a3_recorded_baseline_not_overwritten_by_git_head_when_unmodified(tmp_path: Path) -> None:
    """A3: Pre-apply verification must not overwrite an existing recorded baseline SHA in delta_baselines with git_sha."""
    import hashlib

    from eidos.core.index.storage import open_cache_connection
    from eidos.core.lifecycle.delta_engine import _snapshot_delta_baselines

    _init_git_repo(tmp_path)
    ok, msg, _ = init_repository(tmp_path, name="TestSys")
    assert ok, msg
    eidos_dir = tmp_path / "eidos"

    cnt_path = create_spec(eidos_dir, "container", "CNT-API", "API Container")
    head_content = cnt_path.read_text(encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial container"], cwd=tmp_path, check=True, capture_output=True)

    # Modify CNT-API in working tree BEFORE creating the delta
    dirty_content = head_content + "\n== Pre-existing Uncommitted Edit\n"
    cnt_path.write_text(dirty_content, encoding="utf-8")
    dirty_sha = hashlib.sha256(cnt_path.read_bytes()).hexdigest()

    # Create the delta now and snapshot current baselines (records dirty_sha of cnt_path as baseline_sha in delta_baselines)
    dlt_path = create_delta(
        eidos_dir,
        raw_id="DLT-2026-002",
        title="Modify API Container",
        kind="enabler",
        motivation="Update CNT-API container specification.",
    )
    dlt_text = dlt_path.read_text(encoding="utf-8")
    dlt_text = dlt_text.replace(
        "mutations: []",
        "mutations:\n  - operation: modify\n    target: CNT-API\n    details: Update CNT-API container specification.",
    )
    dlt_path.write_text(dlt_text, encoding="utf-8")
    _snapshot_delta_baselines(eidos_dir, "DLT-2026-002", overwrite=True)

    # Run verify_delta_mutations; delta_baselines must NOT have been overwritten with HEAD's sha
    verify_delta_mutations(eidos_dir, dlt_path.name)
    import contextlib

    db_path = eidos_dir / ".cache" / "index.db"
    with contextlib.closing(open_cache_connection(db_path)) as conn:
        row = conn.execute(
            "SELECT baseline_sha256 FROM delta_baselines WHERE delta_id = ? AND target_id = ?",
            ("DLT-2026-002", "CNT-API"),
        ).fetchone()
    assert row is not None and row[0] == dirty_sha

    # If the delta now modifies CNT-API back to head_content (different from dirty_sha), verification still succeeds
    cnt_path.write_text(head_content, encoding="utf-8")
    ok_ver, msg_ver, _ = verify_delta_mutations(eidos_dir, dlt_path.name)
    assert ok_ver, f"Expected modification away from recorded dirty_sha baseline to verify: {msg_ver}"


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
        "schema: schemas/v1/delta/delta.schema.yaml\n"
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
