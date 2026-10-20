"""Tests for Eidos PR verification gate, drift detection, and spec reconciliation."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.vcs.drift import (
    classify_changed_files,
    detect_drift,
)

runner = CliRunner()


def test_classify_changed_files():
    """Verify categorization of code, living specs, deltas, tests, and documentation."""
    files = [
        "src/eidos/cli/main.py",
        "packages/billing/src/index.ts",
        "eidos/architecture/containers/cli.adoc",
        "eidos/deltas/dlt-2026-001.adoc",
        "tests/test_drift.py",
        "docs/index.md",
        "pyproject.toml",
    ]
    classification = classify_changed_files(files)

    assert "src/eidos/cli/main.py" in classification.code_files
    assert "packages/billing/src/index.ts" in classification.code_files
    assert "eidos/architecture/containers/cli.adoc" in classification.spec_files
    assert "eidos/deltas/dlt-2026-001.adoc" in classification.delta_files
    assert "tests/test_drift.py" in classification.test_files
    assert "docs/index.md" in classification.doc_files
    assert "pyproject.toml" in classification.other_files
    assert classification.total_changed == 7


def test_drift_detection_code_without_specs(monkeypatch):
    """Verify INV-DRIFT-DETECTION-ACCURACY: drift is detected when code changes without living spec updates."""
    # Mock get_git_diff_files to simulate un-synchronized code edits
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/cli/main.py",
        ],
    )

    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert result.is_clean is False
    assert result.workflow_mode == "direct"
    assert len(result.remediation_suggestions) > 0
    assert "DRIFT DETECTED" in result.summary_markdown


def test_pr_check_gate_pass_direct(monkeypatch):
    """Verify INV-DRIFT-DETECTION-ACCURACY: gate passes when code and living specs co-evolve together directly."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/cli/main.py",
            "eidos/architecture/containers/cli.adoc",
        ],
    )

    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.is_clean is True
    assert result.workflow_mode == "direct"
    assert "PASSED" in result.summary_markdown


def test_pr_check_gate_pass_planned(monkeypatch):
    """Verify gate recognizes planned mode when SpecDelta is present in diff."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/core/spec/parser.py",
            "eidos/deltas/dlt-2026-010.adoc",
        ],
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            (None, (Path.cwd() / rel_path).read_text(encoding="utf-8"))
            if "dlt-2026-010" in rel_path
            else (
                (Path.cwd() / rel_path).read_text(encoding="utf-8"),
                (Path.cwd() / rel_path).read_text(encoding="utf-8"),
            )
        ),
    )

    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.workflow_mode == "planned"
    assert "Mode: **Planned (SpecDelta)**" in result.summary_markdown


def test_cli_drift_and_reconcile_commands(monkeypatch, tmp_path):
    """Verify CLI drift, reconcile, and sync commands."""
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/cli/main.py",
            "eidos/architecture/containers/cli.adoc",
        ],
    )

    # 1. drift passing
    md_file = tmp_path / "drift_summary.md"
    res_drift = runner.invoke(app, ["drift", "--markdown", str(md_file)])
    assert res_drift.exit_code == 0
    assert "Verification Passed!" in res_drift.output
    assert md_file.exists()
    assert "PASSED" in md_file.read_text(encoding="utf-8")

    # 2. drift json output
    res_json = runner.invoke(app, ["drift", "--json"])
    assert res_json.exit_code == 0
    assert '"is_clean": true' in res_json.output

    # 3. check-pr is removed from CLI
    res_removed = runner.invoke(app, ["check-pr"])
    assert res_removed.exit_code != 0

    # 4. reconcile and sync commands are removed from CLI
    res_rec = runner.invoke(app, ["reconcile"])
    assert res_rec.exit_code != 0

    res_sync = runner.invoke(app, ["sync"])
    assert res_sync.exit_code != 0


def test_polyglot_test_file_classification():
    """Verify Initiative D: classify_changed_files recognizes polyglot test files."""
    poly_files = [
        "src/app.ts",
        "packages/auth/src/index.test.ts",
        "packages/auth/src/session.spec.ts",
        "packages/ui/button.test.tsx",
        "packages/ui/card.spec.jsx",
        "pkg/server/server_test.go",
        "crates/core/src/parser_test.rs",
        "tests/integration.rs",
        "src/test/java/UserServiceTest.java",
        "src/test/kotlin/OrderServiceSpec.kt",
        "src/test/kotlin/PaymentServiceTest.kt",
        "tests/test_something.py",
        "src/utils/test_helper.py",
    ]
    classification = classify_changed_files(poly_files)
    assert "src/app.ts" in classification.code_files
    assert "src/utils/test_helper.py" in classification.code_files
    assert len(classification.test_files) == 11
    assert "packages/auth/src/index.test.ts" in classification.test_files
    assert "packages/ui/button.test.tsx" in classification.test_files
    assert "pkg/server/server_test.go" in classification.test_files
    assert "crates/core/src/parser_test.rs" in classification.test_files
    assert "tests/integration.rs" in classification.test_files
    assert "src/test/java/UserServiceTest.java" in classification.test_files
    assert "src/test/kotlin/OrderServiceSpec.kt" in classification.test_files
    assert "tests/test_something.py" in classification.test_files


def test_polyglot_invariant_test_scanning(tmp_path):
    """Verify Initiative D: extract_invariants_and_verify accurately traces invariants across polyglot test suites."""  # eidos:fixture
    from eidos.vcs.drift import extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "polyglot"
    cap_dir.mkdir(parents=True)

    # Capability declaring invariants verified across languages
    (cap_dir / "cap-polyglot.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-POLY-001\n"
        "title: Polyglot Test Coverage\n"
        "domain: DOM-TEST\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Verify polyglot suites\n"
        "technical:\n"
        "  containers: []\n"
        "  invariants:\n"
        "    - id: INV-TS-UNIT\n"
        "      rule: TS unit tests\n"
        "    - id: INV-GO-HANDLER\n"
        "      rule: Go handler tests\n"
        "    - id: INV-RS-INTEGRATION\n"
        "      rule: Rust integration tests\n"
        "    - id: INV-JAVA-SERVICE\n"
        "      rule: Java service tests\n"
        "    - id: INV-KT-SPEC\n"
        "      rule: Kotlin spec tests\n"
        "    - id: INV-UNCOVERED\n"
        "      rule: Should be uncovered\n"
        "---\n"
        "= Cap\n"
    )

    # Create polyglot test suite in tmp_path
    ts_file = tmp_path / "packages" / "client" / "src" / "client.test.ts"
    ts_file.parent.mkdir(parents=True)
    ts_file.write_text("// Test implementation verifying INV-TS-UNIT\n")

    go_file = tmp_path / "cmd" / "server" / "handler_test.go"
    go_file.parent.mkdir(parents=True)
    go_file.write_text("// Test verifying INV-GO-HANDLER\n")

    rs_file = tmp_path / "tests" / "integration.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text("// Test verifying INV-RS-INTEGRATION\n")

    java_file = tmp_path / "src" / "test" / "java" / "OrderServiceTest.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text("// Test verifying INV-JAVA-SERVICE\n")

    kt_file = tmp_path / "src" / "test" / "kotlin" / "OrderSpec.kt"
    kt_file.parent.mkdir(parents=True)
    kt_file.write_text("// Test verifying INV-KT-SPEC\n")

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {inv.invariant_id: inv for inv in invariants}

    assert inv_map["INV-TS-UNIT"].has_test_coverage
    assert "packages/client/src/client.test.ts" in inv_map["INV-TS-UNIT"].matching_tests

    assert inv_map["INV-GO-HANDLER"].has_test_coverage
    assert "cmd/server/handler_test.go" in inv_map["INV-GO-HANDLER"].matching_tests

    assert inv_map["INV-RS-INTEGRATION"].has_test_coverage
    assert "tests/integration.rs" in inv_map["INV-RS-INTEGRATION"].matching_tests

    assert inv_map["INV-JAVA-SERVICE"].has_test_coverage
    assert "src/test/java/OrderServiceTest.java" in inv_map["INV-JAVA-SERVICE"].matching_tests

    assert inv_map["INV-KT-SPEC"].has_test_coverage
    assert "src/test/kotlin/OrderSpec.kt" in inv_map["INV-KT-SPEC"].matching_tests

    assert not inv_map["INV-UNCOVERED"].has_test_coverage
    assert len(inv_map["INV-UNCOVERED"].matching_tests) == 0


def test_is_polyglot_test_file_ancestor_robustness():
    """Verify is_polyglot_test_file does not falsely classify code files when ancestor path has 'test'."""
    from pathlib import Path

    from eidos.vcs.drift import is_polyglot_test_file

    repo = Path("/home/testuser/work/test-project")
    code_file = repo / "src" / "app.py"
    ts_code = repo / "src" / "index.ts"
    go_code = repo / "cmd" / "server.go"
    rs_code = repo / "crates" / "core" / "src" / "lib.rs"
    java_code = repo / "src" / "main" / "java" / "App.java"

    # Should all be recognized as non-tests
    assert not is_polyglot_test_file(code_file, repo_root=repo)
    assert not is_polyglot_test_file(ts_code, repo_root=repo)
    assert not is_polyglot_test_file(go_code, repo_root=repo)
    assert not is_polyglot_test_file(rs_code, repo_root=repo)
    assert not is_polyglot_test_file(java_code, repo_root=repo)

    # Real tests inside the repo should be recognized
    assert is_polyglot_test_file(repo / "tests" / "test_app.py", repo_root=repo)
    assert is_polyglot_test_file(repo / "tests" / "integration.rs", repo_root=repo)
    assert is_polyglot_test_file(repo / "cmd" / "server_test.go", repo_root=repo)
    assert is_polyglot_test_file(repo / "src" / "app.spec.ts", repo_root=repo)


def test_extract_invariants_none_frontmatter(tmp_path):
    """Verify extract_invariants_and_verify does not crash on None or empty technical frontmatter."""
    from eidos.vcs.drift import extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "sub"
    pol_dir = eidos_dir / "policies"
    cap_dir.mkdir(parents=True)
    pol_dir.mkdir(parents=True)

    # Capability with empty technical section
    (cap_dir / "cap-empty.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-EMPTY-001\n"
        "title: Empty Tech\n"
        "domain: DOM-TEST\n"
        "status: active\n"
        "business:\n"
        "  personas: []\n"
        "  jtbd: Test\n"
        "technical:\n"
        "---\n"
        "= Cap\n"
    )

    # Policy with empty technical section
    (pol_dir / "pol-empty.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-EMPTY-001\n"
        "title: Empty Policy\n"
        "category: testing\n"
        "rule: Rule\n"
        "technical:\n"
        "---\n"
        "= Policy\n"
    )

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    assert isinstance(invariants, list)


def test_no_circular_import():
    """Verify importing eidos.vcs does not cause circular import with eidos.core."""
    import importlib
    import sys

    # Save existing modules to restore after test
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("eidos.") or k == "eidos"}

    try:
        # Evict modules to test clean import order
        for mod in list(sys.modules.keys()):
            if mod.startswith("eidos.") or mod == "eidos":
                del sys.modules[mod]

        vcs = importlib.import_module("eidos.vcs")
        assert hasattr(vcs, "detect_drift")
    finally:
        sys.modules.update(saved_modules)


def test_is_polyglot_test_file_string_and_ancestor_test():
    """Verify is_polyglot_test_file handles str inputs and ignores ancestor /test/ directory components."""
    from pathlib import Path

    from eidos.vcs.drift import is_polyglot_test_file

    # String input support
    assert is_polyglot_test_file("tests/test_auth.py")
    assert is_polyglot_test_file("src/client.test.ts")
    assert not is_polyglot_test_file("src/client.ts")

    # Ancestor user directory named 'test' should not make code files tests
    assert not is_polyglot_test_file(Path("/home/test/myproject/src/main.py"))
    assert not is_polyglot_test_file(Path("/home/test/myproject/src/index.ts"))


def test_classify_changed_files_repo_named_eidos(tmp_path):
    """Verify repo root named eidos does not falsely classify package.json or setup.py as living specs."""
    from eidos.vcs.drift import classify_changed_files

    repo_dir = tmp_path / "eidos"
    repo_dir.mkdir()

    # Files at root of repo
    pkg_json = repo_dir / "package.json"
    code_file = repo_dir / "src" / "index.js"
    real_spec = repo_dir / "eidos" / "system.adoc"

    classified = classify_changed_files(
        [str(pkg_json), str(code_file), str(real_spec)],
        repo_root=repo_dir,
    )

    assert str(pkg_json) in classified.other_files
    assert str(pkg_json) not in classified.spec_files
    assert str(code_file) in classified.code_files
    assert str(real_spec) in classified.spec_files


def test_classify_changed_files_ancestor_path_named_docs(tmp_path):
    """Verify paths containing 'docs' above the repo root do not classify code files as doc files."""
    from eidos.vcs.drift import classify_changed_files

    repo_dir = tmp_path / "docs" / "myproject"
    repo_dir.mkdir(parents=True)
    code_file = repo_dir / "src" / "app.py"

    classified = classify_changed_files([str(code_file)], repo_root=repo_dir)
    assert str(code_file) in classified.code_files
    assert str(code_file) not in classified.doc_files


def test_classify_changed_files_custom_eidos_dir(tmp_path):
    """Verify classify_changed_files correctly classifies spec and delta files under a custom eidos_dir."""
    from eidos.vcs.drift import classify_changed_files

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    custom_specs = repo_dir / "specs"
    custom_specs.mkdir()
    (custom_specs / "deltas").mkdir()

    spec_file = custom_specs / "system.adoc"
    spec_file.write_text("= System\n", encoding="utf-8")
    delta_file = custom_specs / "deltas" / "dlt-2026-001.adoc"
    delta_file.write_text("= Delta\n", encoding="utf-8")
    code_file = repo_dir / "src" / "main.py"
    code_file.parent.mkdir(parents=True)
    code_file.write_text("print('hi')\n", encoding="utf-8")

    files = [str(spec_file), str(delta_file), str(code_file)]
    classified = classify_changed_files(files, repo_root=repo_dir, eidos_dir=custom_specs)

    assert str(spec_file) in classified.spec_files
    assert str(delta_file) in classified.delta_files
    assert str(code_file) in classified.code_files


def test_is_generated_or_vendored_scalar_string_and_path_input(tmp_path):
    """Verify is_generated_or_vendored accepts scalar str and Path inputs without splitting into characters."""
    import subprocess

    from eidos.core.workspace.project import is_generated_or_vendored

    # 1. If repo has no .git, returns empty set cleanly
    non_git = tmp_path / "non_git"
    non_git.mkdir()
    assert is_generated_or_vendored(non_git, "src/some_file.py") == set()
    assert is_generated_or_vendored(non_git, Path("src/some_file.py")) == set()

    # 2. Real git repo with .gitattributes marking *.gen.py as linguist-generated
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / ".gitattributes").write_text("*.gen.py linguist-generated=true\n", encoding="utf-8")

    assert is_generated_or_vendored(repo, "src/foo.gen.py") == {"src/foo.gen.py"}
    assert is_generated_or_vendored(repo, Path("src/foo.gen.py")) == {"src/foo.gen.py"}
    assert is_generated_or_vendored(repo, "src/normal.py") == set()


def test_classify_changed_files_batches_is_generated_or_vendored(tmp_path, monkeypatch):
    """Verify classify_changed_files batches is_generated_or_vendored into a single invocation."""
    from eidos.vcs.drift import classify_changed_files

    calls = []

    def mock_is_generated_or_vendored(repo_root, rel_paths):
        calls.append((repo_root, list(rel_paths)))
        # Mark generated.py as vendored
        return {p for p in rel_paths if "generated.py" in p}

    monkeypatch.setattr("eidos.vcs.diff.is_generated_or_vendored", mock_is_generated_or_vendored)

    files = [
        "src/app.py",
        "src/utils.py",
        "src/generated.py",
        "tests/test_app.py",
        "eidos/system.adoc",
    ]
    classification = classify_changed_files(files, repo_root=tmp_path)

    # Must be called exactly once with all candidate code paths batched together
    assert len(calls) == 1
    assert calls[0][0] == tmp_path.resolve()
    assert set(calls[0][1]) == {"src/app.py", "src/utils.py", "src/generated.py"}

    # Non-generated code files kept in code_files, generated file excluded
    assert "src/app.py" in classification.code_files
    assert "src/utils.py" in classification.code_files
    assert "src/generated.py" not in classification.code_files
    assert "src/generated.py" in classification.other_files


def test_resolve_base_ref_single_pass(tmp_path, monkeypatch):
    """Verify _resolve_base_ref resolves the candidate Git ref and detect_drift invokes it at most once across multiple files."""
    import subprocess

    from eidos.vcs.drift import _resolve_base_ref

    orig_run = subprocess.run

    # 1. Explicit base ref that verifies successfully
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, cwd=None, capture_output=False, check=False: type(
            "SubprocessResult", (), {"returncode": 0 if cmd[-1] in ("origin/main", "feat/my-branch") else 1}
        )(),
    )

    res = _resolve_base_ref(tmp_path, "feat/my-branch")
    assert res == "feat/my-branch"

    # 2. None supplied -> falls back to candidate list
    res_default = _resolve_base_ref(tmp_path, None)
    assert res_default == "origin/main"

    # 3. Real git repo with multiple modified code and spec files: detect_drift calls _resolve_base_ref at most once
    monkeypatch.setattr("subprocess.run", orig_run)
    repo = tmp_path / "git_repo"
    repo.mkdir()

    def git(*args):
        orig_run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "eidos" / "architecture" / "containers").mkdir(parents=True)
    (repo / "src" / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "b.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    (repo / "eidos" / "architecture" / "containers" / "x.adoc").write_text(_SPEC_BASE, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "c1")

    git("checkout", "-qb", "feat")
    (repo / "src" / "pkg" / "a.py").write_text("def a(x: int):\n    return x\n", encoding="utf-8")
    (repo / "src" / "pkg" / "b.py").write_text("def b(y: int):\n    return y\n", encoding="utf-8")
    (repo / "eidos" / "architecture" / "containers" / "x.adoc").write_text(
        _SPEC_BASE + "\nUpdated signatures for a and b.\n", encoding="utf-8"
    )
    git("add", "-A")
    git("commit", "-qm", "c2")

    resolve_calls: list[str | None] = []
    orig_resolve_base = _resolve_base_ref

    def spy_resolve_base(r, b=None):
        resolve_calls.append(b)
        return orig_resolve_base(r, b)

    monkeypatch.setattr("eidos.vcs.diff._resolve_base_ref", spy_resolve_base)
    drift_res = detect_drift(repo_root=repo, eidos_dir=repo / "eidos")
    assert len(resolve_calls) == 1
    assert drift_res.drift_detected is False


def test_git_resolution_error_on_invalid_explicit_base(tmp_path):
    """DLT-2026-053: Fail closed when an explicitly provided base ref cannot be resolved."""
    import pytest

    from eidos.vcs.drift import GitResolutionError, detect_drift, get_git_diff_files

    with pytest.raises(GitResolutionError, match="Cannot resolve base Git reference 'non-existent-ref'"):
        get_git_diff_files(tmp_path, base_ref="non-existent-ref")

    with pytest.raises(GitResolutionError, match="Cannot resolve base Git reference 'non-existent-ref'"):
        detect_drift(repo_root=tmp_path, base_ref="non-existent-ref")


def test_git_resolution_error_on_orphan_base_without_merge_base(tmp_path):
    """DLT-2026-053: Fail closed when an explicit base_ref is a valid branch but shares no common ancestor with HEAD."""
    import subprocess

    from eidos.vcs.drift import GitResolutionError, detect_drift, get_git_diff_files

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "main commit")

    git("checkout", "--orphan", "orphan-branch")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "orphan commit")

    with pytest.raises(GitResolutionError, match="Cannot find merge base between 'main' and 'HEAD'"):
        get_git_diff_files(tmp_path, base_ref="main")

    with pytest.raises(GitResolutionError, match="Cannot find merge base between 'main' and 'HEAD'"):
        detect_drift(repo_root=tmp_path, base_ref="main")


def test_cli_drift_exits_nonzero_on_git_resolution_error():
    """DLT-2026-053: eidos drift --base <bad_ref> surfaces Git Resolution Error and exits 1."""
    res = runner.invoke(app, ["drift", "--base", "invalid-branch-xyz"])
    assert res.exit_code == 1
    assert "Git Resolution Error:" in res.output
    assert "invalid-branch-xyz" in res.output


def test_preserve_valid_empty_three_dot_diff(tmp_path):
    """DLT-2026-053: Zero changes between merge-base and head returns [] without falling back to 2-dot diff."""
    import subprocess

    from eidos.vcs.drift import get_git_diff_files

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "initial on main")

    # Create feature branch pointing to exact same commit
    git("checkout", "-qb", "feat-empty")

    # On main, commit a new change to make main tip advance past feature branch
    git("checkout", "-q", "main")
    (tmp_path / "other.py").write_text("print('other')\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "advance main")

    git("checkout", "-q", "feat-empty")
    # 3-dot diff (merge-base between main and feat-empty is commit 1; feat-empty has 0 changes since commit 1)
    diff_files = get_git_diff_files(tmp_path, base_ref="main", head_ref="HEAD")
    assert diff_files == []


def test_merge_base_file_content_comparison(tmp_path):
    """DLT-2026-053: _get_file_versions retrieves old_content from merge-base SHA, not base branch tip."""
    import subprocess

    from eidos.vcs.drift import _get_file_versions

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("v1_base\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "c1: base v1")

    # Branch feature from c1
    git("checkout", "-qb", "feat-branch")
    (tmp_path / "app.py").write_text("v2_feature\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "c2: feature v2")

    # Advance main with c3
    git("checkout", "-q", "main")
    (tmp_path / "app.py").write_text("v3_main_tip\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "c3: main tip v3")

    # Inspect from feature branch
    git("checkout", "-q", "feat-branch")
    old_c, new_c = _get_file_versions(tmp_path, "app.py", base_ref="main", head_ref="HEAD")
    assert old_c == "v1_base\n"
    assert new_c == "v2_feature\n"


def test_design_system_drift_detection(monkeypatch):
    """Verify frontend code or styling modifications require eidos/design/ living spec updates."""
    # 1. Frontend CSS changed without eidos/design updates -> drift detected
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/portal/frontend/src/theme/theme.css",
        ],
    )
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert any("Design System Drift Detected" in r for r in result.remediation_suggestions)

    # 2. Frontend CSS changed WITH tokens.json updated -> clean!
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/portal/frontend/src/theme/theme.css",
            "eidos/design/systems/core/tokens.json",
        ],
    )
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.is_clean is True


def test_uncovered_delta_drift_detection(monkeypatch):
    """Verify that presence of an unrelated SpecDelta does not silence drift for uncovered components."""
    # dlt-2026-001 targets CLI, but portal frontend code was modified without portal/design spec updates
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "eidos/deltas/dlt-2026-001.adoc",
            "src/eidos/portal/frontend/src/theme/theme.css",
        ],
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            (None, (Path.cwd() / rel_path).read_text(encoding="utf-8"))
            if "dlt-2026-001" in rel_path
            else (_OLD_CSS, _TOKEN_CSS)
        ),
    )
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is True
    assert result.workflow_mode == "planned"
    assert any("Design System Drift Detected" in r for r in result.remediation_suggestions)


def test_strict_static_invariant_binding_and_orphans(tmp_path):
    """Verify DLT-2026-027: strict file::func existence, exact word-boundary matching, decorator/comment binding, and orphan detection."""  # eidos:fixture
    from eidos.vcs.drift import detect_orphan_invariants, extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "auth"
    cap_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    (cap_dir / "cap-auth.adoc").write_text(
        """---
type: product/capability
id: CAP-AUTH-001
title: Auth Capability
domain: DOM-AUTH
status: active
business:
  personas: []
  jtbd: Authenticate users
technical:
  containers: []
  invariants:
    - id: INV-AUTH-OK
      rule: Must verify auth token
      test_binding: tests/test_auth.py::test_valid_token
    - id: INV-AUTH-MISSING-FUNC
      rule: Points to non-existent function
      test_binding: tests/test_auth.py::test_does_not_exist
    - id: INV-AUTH-UNTAGGED-FUNC
      rule: Points to existing function that lacks invariant tag
      test_binding: tests/test_auth.py::test_untagged_helper
    - id: INV-AUTH-1
      rule: Must not false-match INV-AUTH-10
      test_binding: tests/test_auth.py::test_prefix_collision
---
= Auth Capability
""",
        encoding="utf-8",
    )

    (tests_dir / "test_auth.py").write_text(
        """# Preceding comment binding INV-AUTH-OK
def test_valid_token():
    assert True


def test_untagged_helper():
    assert True


def test_prefix_collision():
    # Mentions INV-AUTH-10 and an orphan INV-ORPHAN-999 only
    assert True
""",
        encoding="utf-8",
    )

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {inv.invariant_id: inv for inv in invariants}

    # 1. Preceding comment above bound function satisfies file::func binding
    assert inv_map["INV-AUTH-OK"].has_test_coverage is True
    assert "tests/test_auth.py::test_valid_token" in inv_map["INV-AUTH-OK"].matching_tests

    # 2. Missing function fails even though tests/test_auth.py exists
    assert inv_map["INV-AUTH-MISSING-FUNC"].has_test_coverage is False

    # 3. Existing function without INV-* tag fails
    assert inv_map["INV-AUTH-UNTAGGED-FUNC"].has_test_coverage is False

    # 4. Exact word-boundary regex prevents INV-AUTH-1 from matching INV-AUTH-10
    assert inv_map["INV-AUTH-1"].has_test_coverage is False

    # 5. Orphan invariant detection catches INV-AUTH-10 and INV-ORPHAN-999
    orphans = detect_orphan_invariants(tmp_path, set(inv_map.keys()))
    orphan_ids = {oid for oid, _ in orphans}
    assert "INV-AUTH-10" in orphan_ids
    assert "INV-ORPHAN-999" in orphan_ids


def test_structural_vs_body_only_drift_detection(monkeypatch):
    """Verify DLT-2026-028: body-only edits inside existing functions do not trigger drift, while structural API/signature changes do."""
    old_code = "def calculate_total(items: list[int]) -> int:\n    return sum(items)\n"
    body_only_new_code = (
        "def calculate_total(items: list[int]) -> int:\n"
        "    # Internal optimization without signature or symbol change\n"
        "    total = 0\n"
        "    for x in items:\n"
        "        total += x\n"
        "    return total\n"
    )
    structural_new_code = (
        "def calculate_total(items: list[int], tax_rate: float = 0.0) -> float:\n"
        "    return float(sum(items)) * (1.0 + tax_rate)\n"
    )

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/cli/main.py",
        ],
    )

    # 1. Body-only edit -> NO drift!
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            old_code,
            body_only_new_code,
        ),
    )
    res_body = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_body.drift_detected is False
    assert res_body.is_clean is True
    assert "src/eidos/cli/main.py" in res_body.body_only_code_files

    # 2. Signature change (structural) without spec update -> DRIFT detected!
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            old_code,
            structural_new_code,
        ),
    )
    res_struct = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_struct.drift_detected is True
    assert res_struct.is_clean is False


def test_scoped_1hop_neighborhood_drift_verification(monkeypatch):
    """Verify DLT-2026-028: touching an unrelated spec does not silence component drift, while a 1-hop linked spec satisfies it."""
    # 1. CLI code changed + unrelated Portal container spec changed -> DRIFT detected!
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/cli/main.py",
            "eidos/architecture/containers/portal.adoc",
        ],
    )
    res_unrelated = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_unrelated.drift_detected is True
    assert res_unrelated.is_clean is False

    # 2. CLI code changed + 1-hop neighbor contract (eidos/architecture/contracts/cli.adoc) changed -> CLEAN!
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/eidos/cli/main.py",
            "eidos/architecture/contracts/cli.adoc",
        ],
    )
    res_1hop = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res_1hop.drift_detected is False
    assert res_1hop.is_clean is True


def test_system_spec_does_not_mask_component_drift(monkeypatch):
    """Verify DLT-2026-030: editing system-level specs cannot silence structural drift inside a mapped container."""
    for system_spec in ("eidos/system.adoc", "eidos/architecture/system-architecture.adoc"):
        monkeypatch.setattr(
            "eidos.vcs.drift.get_git_diff_files",
            lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None, _s=system_spec: [
                "src/eidos/cli/main.py",
                _s,
            ],
        )
        result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
        assert result.drift_detected is True, system_spec
        assert result.is_clean is False


THEME = "src/eidos/portal/frontend/src/theme/theme.css"
_OLD_CSS = ":root {\n  --md-sys-color-primary: #6750a4;\n}\n.card {\n  padding: 8px;\n}\n"
_BODY_CSS = ":root {\n  --md-sys-color-primary: #6750a4;\n}\n.card {\n  padding: 12px;\n  gap: 4px;\n}\n"
_TOKEN_CSS = ":root {\n  --md-sys-color-primary: #7c4dff;\n}\n.card {\n  padding: 8px;\n}\n"


def _mock_versions(monkeypatch, new_css):
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            _OLD_CSS,
            new_css,
        ),
    )


def _mock_diff(monkeypatch, files):
    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: list(files),
    )


def test_body_only_css_tweak_has_no_design_drift(monkeypatch):
    """Verify DLT-2026-030: layout tweaks that do not touch design tokens produce zero drift."""
    _mock_diff(monkeypatch, [THEME])
    _mock_versions(monkeypatch, _BODY_CSS)
    result = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert result.drift_detected is False
    assert result.uncovered_design_files == []
    assert THEME in result.body_only_code_files


def test_graph_scoped_design_token_drift(monkeypatch):
    """Verify DLT-2026-030: token changes are satisfied only by the bound DS-*/CLT-* design subgraph."""
    _mock_versions(monkeypatch, _TOKEN_CSS)

    # 1. Token change without design spec updates -> design drift
    _mock_diff(monkeypatch, [THEME])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.drift_detected is True
    assert THEME in res.uncovered_design_files

    # 2. Token change + bound DS-CORE token manifest -> clean
    _mock_diff(monkeypatch, [THEME, "eidos/design/systems/core/tokens.json"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.drift_detected is False
    assert res.uncovered_design_files == []

    # 3. Token change + UI component of the bound design system -> clean
    _mock_diff(monkeypatch, [THEME, "eidos/design/systems/core/components/portal-reader.adoc"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.uncovered_design_files == []

    # 4. Token change + design spec outside the bound subgraph -> still design drift
    _mock_diff(monkeypatch, [THEME, "eidos/design/flows/cli-serve-and-browse.adoc"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert THEME in res.uncovered_design_files
    assert any("Design System Drift Detected" in r for r in res.remediation_suggestions)


def test_planned_mode_design_target_satisfies_design_drift(monkeypatch):
    """Verify DLT-2026-030: an active SpecDelta declaring the bound design system satisfies design drift."""
    _mock_versions(monkeypatch, _TOKEN_CSS)
    # dlt-2026-019 declares target_design_system: DS-CORE
    _mock_diff(monkeypatch, [THEME, "eidos/deltas/dlt-2026-019.adoc"])
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.workflow_mode == "planned"
    assert res.uncovered_design_files == []


def test_component_invariants_are_verified(tmp_path):
    """Verify DLT-2026-031: tech/component and inline container component invariants are statically verified."""  # eidos:fixture
    from eidos.vcs.drift import detect_orphan_invariants, extract_invariants_and_verify

    eidos_dir = tmp_path / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    cnts_dir = eidos_dir / "architecture" / "containers"
    comps_dir.mkdir(parents=True)
    cnts_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    (comps_dir / "billing.adoc").write_text(
        """---
type: tech/component
id: CMP-BILLING
title: Billing
container: CNT-APP
source_path: src/billing
responsibility: Billing
invariants:
  - id: INV-CMP-BILLING-OK
    rule: Covered component invariant
    test_binding: tests/test_billing.py::test_billing_ok
  - id: INV-CMP-BILLING-BROKEN
    rule: Broken binding
    test_binding: tests/test_billing.py::test_missing
---
= Billing
""",
        encoding="utf-8",
    )
    (cnts_dir / "app.adoc").write_text(
        """---
type: tech/container
id: CNT-APP
title: App
source_path: src
components:
  - id: CMP-INLINE
    title: Inline
    source_path: src/inline.py
    invariants:
      - id: INV-CMP-INLINE-OK
        rule: Inline component invariant
        test_binding: tests/test_billing.py::test_inline_ok
---
= App
""",
        encoding="utf-8",
    )
    (tests_dir / "test_billing.py").write_text(
        "# INV-CMP-BILLING-OK\ndef test_billing_ok():\n    assert True\n\n\n"
        "# INV-CMP-INLINE-OK\ndef test_inline_ok():\n    assert True\n",
        encoding="utf-8",
    )

    invariants = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {inv.invariant_id: inv for inv in invariants}
    assert inv_map["INV-CMP-BILLING-OK"].has_test_coverage is True
    assert inv_map["INV-CMP-BILLING-OK"].spec_id == "CMP-BILLING"
    assert inv_map["INV-CMP-BILLING-BROKEN"].has_test_coverage is False
    assert inv_map["INV-CMP-INLINE-OK"].has_test_coverage is True
    assert inv_map["INV-CMP-INLINE-OK"].spec_id == "CMP-INLINE"

    # Component invariants referenced by tests are no longer reported as orphans
    orphans = {oid for oid, _ in detect_orphan_invariants(tmp_path, set(inv_map))}
    assert "INV-CMP-BILLING-OK" not in orphans
    assert "INV-CMP-INLINE-OK" not in orphans


def test_exact_delta_target_matching_rejects_substring_and_slug_collisions(tmp_path, monkeypatch):
    """Verify A2: delta targets match full entity IDs only (no slug shortening or substring false positives)."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("def run_engine(x: int) -> int:\n    return x\n", encoding="utf-8")

    (cnts_dir / "core.adoc").write_text(
        """---
type: tech/container
id: CNT-EIDOS-CORE
title: Core Engine
source_path: src/core
---
= Core Engine
""",
        encoding="utf-8",
    )
    # Delta targets CMP-SCORE-ENGINE (contains 'core' as substring) and CNT-API (prefix of CNT-API-GATEWAY)
    (deltas_dir / "dlt-2026-099.adoc").write_text(
        """---
type: delta/mutation
id: DLT-2026-099
title: Unrelated Score Engine Delta
status: in_review
intent:
  kind: feature
  motivation: Modify score engine only.
mutations:
  - operation: modify
    target: CMP-SCORE-ENGINE
    details: Update score engine
---
= Unrelated Delta
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/core/engine.py",
            "eidos/deltas/dlt-2026-099.adoc",
        ],
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            "def run_engine() -> int:\n    return 0\n",
            "def run_engine(x: int) -> int:\n    return x\n",
        ),
    )

    res = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    # CNT-EIDOS-CORE must NOT match CMP-SCORE-ENGINE via 'core' slug/substring -> drift detected!
    assert res.drift_detected is True
    assert any("CNT-EIDOS-CORE" in r for r in res.remediation_suggestions)


def test_rejected_and_preexisting_applied_deltas_do_not_activate_planned_mode(tmp_path, monkeypatch):
    """Verify rejected deltas and edits to already-applied deltas do not switch detect_drift into Planned Mode."""
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    deltas_dir = eidos_dir / "deltas"
    cnts_dir.mkdir(parents=True)
    deltas_dir.mkdir(parents=True)
    src_dir = tmp_path / "src" / "core"
    src_dir.mkdir(parents=True)
    (src_dir / "engine.py").write_text("def run_engine(x: int) -> int:\n    return x\n", encoding="utf-8")

    (cnts_dir / "core.adoc").write_text(
        """---
type: tech/container
id: CNT-EIDOS-CORE
title: Core Engine
source_path: src/core
components: []
---
= Core Engine
""",
        encoding="utf-8",
    )
    rejected_delta = """---
type: delta/mutation
id: DLT-2026-101
title: Rejected Core Delta
status: rejected
intent:
  kind: enabler
  motivation: Rejected change.
mutations:
  - operation: modify
    target: CNT-EIDOS-CORE
    details: Touch core
---
= Rejected Delta
"""
    (deltas_dir / "dlt-2026-101.adoc").write_text(rejected_delta, encoding="utf-8")

    applied_old = """---
type: delta/mutation
id: DLT-2026-102
title: Old Applied Delta
status: applied
intent:
  kind: enabler
  motivation: Previously applied.
mutations:
  - operation: modify
    target: CNT-EIDOS-CORE
    details: Touch core
---
= Old Applied
"""
    applied_new = applied_old + "\n// Minor typo fix in historical delta\n"
    (deltas_dir / "dlt-2026-102.adoc").write_text(applied_new, encoding="utf-8")

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: [
            "src/core/engine.py",
            "eidos/deltas/dlt-2026-101.adoc",
            "eidos/deltas/dlt-2026-102.adoc",
        ],
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            (applied_old, applied_new)
            if "dlt-2026-102" in rel_path
            else ("def run_engine() -> int:\n    return 0\n", "def run_engine(x: int) -> int:\n    return x\n")
        ),
    )

    res = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert res.workflow_mode == "direct"
    assert res.drift_detected is True


def test_path_in_requires_separator_boundary_and_no_slug_fallback(tmp_path):
    """Verify _path_in rejects non-boundary suffix collisions and map_code_to_components does not fallback to slug matching."""
    from eidos.vcs.drift import _path_in, map_code_to_components

    assert _path_in("eidos/architecture/containers/other-core.adoc", {"core.adoc"}) is False
    assert _path_in("eidos/architecture/containers/core.adoc", {"architecture/containers/core.adoc"}) is True

    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    cnts_dir.mkdir(parents=True)
    (cnts_dir / "core.adoc").write_text(
        """---
type: tech/container
id: CNT-EIDOS-CORE
title: Core Engine
source_path: src/core
components: []
---
= Core Engine
""",
        encoding="utf-8",
    )

    mappings = map_code_to_components(eidos_dir, ["src/unrelated/core.py"], repo_root=tmp_path)
    assert mappings == []


# ---------------------------------------------------------------------------
# DLT-2026-039: diff-aware specification coverage for structural additions
# ---------------------------------------------------------------------------

_SPEC_REL = "eidos/architecture/containers/x.adoc"
_SPEC_BASE = """---
type: tech/container
id: CNT-X
title: X
container_type: service
runtime: Python
source_path: src/pkg
---
= X

Original prose.
"""
_CODE_OLD = "def existing():\n    return 1\n"
_CODE_WITH_CLASS = _CODE_OLD + "\n\nclass Foo:\n    def run(self):\n        return 1\n"


def _diff_aware_env(tmp_path, monkeypatch, changed, versions, spec_text=_SPEC_BASE, extra_files=None):
    """Tmp repo with one container (CNT-X owning src/pkg) and mocked git diff / file versions."""
    eidos_dir = tmp_path / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / _SPEC_REL).write_text(spec_text, encoding="utf-8")
    (tmp_path / "src" / "pkg" / "svc.py").write_text(_CODE_WITH_CLASS, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    monkeypatch.setattr(
        "eidos.vcs.drift.get_git_diff_files",
        lambda repo_root, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: list(changed),
    )
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: versions.get(
            rel_path, (None, None)
        ),
    )
    return eidos_dir


_TRIVIAL_EDITS = {
    "blank_lines": _SPEC_BASE + "\n\n   \n",
    "line_comment": _SPEC_BASE + "\n// touched to appease drift\n",
    "block_comment": _SPEC_BASE + "\n////\nnote about Foo\n////\n",
    "yaml_comment": _SPEC_BASE.replace("title: X\n", "title: X\n# touched\n"),
    "rewrap": _SPEC_BASE.replace("Original prose.", "Original\nprose."),
}


@pytest.mark.parametrize("edit", sorted(_TRIVIAL_EDITS))
def test_trivial_spec_edit_does_not_cover_structural_addition(tmp_path, monkeypatch, edit):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: whitespace/comment/re-wrap-only spec edits do not cover a new public symbol."""
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            _SPEC_REL: (_SPEC_BASE, _TRIVIAL_EDITS[edit]),
        },
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is True
    assert len(result.trivially_covered_files) == 1
    rec = result.trivially_covered_files[0]
    assert rec["file"] == "src/pkg/svc.py"
    assert rec["rule"] == "trivial_spec_diff"
    assert rec["additions"] == ["added class 'Foo'", "added method 'Foo.run'"]
    assert rec["spec_files"] == [_SPEC_REL]
    assert any("Trivial Spec Edit" in r for r in result.remediation_suggestions)


def test_substantive_spec_edit_covers_structural_addition(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: a real prose/frontmatter change covers a new public symbol in an existing file."""
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            _SPEC_REL: (_SPEC_BASE, _SPEC_BASE + "\nThe service now also exposes a runner.\n"),
        },
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is False
    assert result.trivially_covered_files == []


def test_new_spec_file_counts_as_substantive(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: a newly created 1-hop spec always covers the addition."""
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={"src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS), _SPEC_REL: (None, _SPEC_BASE)},
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is False
    assert result.trivially_covered_files == []


_NEW_MODULE = "def new_fn():\n    return 1\n"


def test_new_unclaimed_file_requires_anchor(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: a new file only owned via source_path needs an anchored owner-spec edit."""
    new_rel = "src/pkg/new_mod.py"
    unrelated = _SPEC_BASE + "\nSome unrelated documentation sentence.\n"
    stem_only = _SPEC_BASE + "\nIntroduces the new_mod helpers.\n"  # bare stem: generic word, not an anchor
    mentions = _SPEC_BASE + "\nIntroduces `new_mod.py`.\n"
    names_symbol = _SPEC_BASE + "\nAdds `new_fn()` for callers.\n"
    declares = _SPEC_BASE.replace(
        "source_path: src/pkg\n",
        "source_path:\n  - src/pkg\n  - src/pkg/new_mod.py\n",
    )

    def run(sub, new_spec):
        root = tmp_path / sub
        root.mkdir()
        env = _diff_aware_env(
            root,
            monkeypatch,
            changed=[new_rel, _SPEC_REL],
            versions={new_rel: (None, _NEW_MODULE), _SPEC_REL: (_SPEC_BASE, new_spec)},
            extra_files={new_rel: _NEW_MODULE},
        )
        return detect_drift(repo_root=root, eidos_dir=env)

    rejected = run("unrelated", unrelated)
    assert rejected.drift_detected is True
    assert [r["rule"] for r in rejected.trivially_covered_files] == ["missing_anchor"]
    assert rejected.trivially_covered_files[0]["additions"] == [f"added file '{new_rel}'"]
    assert any("Missing Spec Anchor" in r for r in rejected.remediation_suggestions)

    stem = run("stem_only", stem_only)
    assert [r["rule"] for r in stem.trivially_covered_files] == ["missing_anchor"]
    assert run("mentions", mentions).drift_detected is False  # file name with extension in added prose
    assert run("names_symbol", names_symbol).drift_detected is False  # new public symbol named
    assert run("declares", declares).drift_detected is False  # ownership keys changed


def test_new_file_already_claimed_by_source_path_only_needs_substantive_edit(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: files matched by the owner's source_path at the base revision need no anchor."""
    new_rel = "src/pkg/new_mod.py"
    claimed_base = _SPEC_BASE.replace(
        "source_path: src/pkg\n",
        "source_path:\n  - src/pkg\n  - src/pkg/*.py\n",
    )
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=[new_rel, _SPEC_REL],
        versions={
            new_rel: (None, _NEW_MODULE),
            _SPEC_REL: (claimed_base, claimed_base + "\nUnrelated documentation sentence.\n"),
        },
        spec_text=claimed_base,
        extra_files={new_rel: _NEW_MODULE},
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is False


def test_non_additive_structural_changes_keep_existing_rule(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: signature changes require substantive spec diffs (whitespace/comments rejected)."""
    changed_sig = "def existing(x):\n    return x\n"
    # Trivial whitespace edit is rejected
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, changed_sig),
            _SPEC_REL: (_SPEC_BASE, _SPEC_BASE + "\n\n"),
        },
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is True
    assert [r["rule"] for r in result.trivially_covered_files] == ["trivial_spec_diff"]

    # Substantive edit is accepted
    eidos_dir_sub = _diff_aware_env(
        tmp_path / "sub",
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, changed_sig),
            _SPEC_REL: (_SPEC_BASE, _SPEC_BASE + "\nUpdated existing function signature.\n"),
        },
    )
    result_sub = detect_drift(repo_root=tmp_path / "sub", eidos_dir=eidos_dir_sub)
    assert result_sub.drift_detected is False
    assert result_sub.trivially_covered_files == []


def test_delta_declared_target_bypasses_diff_check(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: in Planned Mode a delta declaring the owner covers additions regardless of spec diffs."""
    delta_rel = "eidos/deltas/dlt-2026-900.adoc"
    delta = """---
type: delta/mutation
id: DLT-2026-900
title: Declares CNT-X
status: proposed
intent:
  kind: feature
  motivation: Add Foo
mutations:
  - operation: modify
    target: CNT-X
    details: Add Foo
---
= Delta
"""
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", delta_rel, _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            _SPEC_REL: (_SPEC_BASE, _SPEC_BASE + "\n// only a comment\n"),
        },
        extra_files={delta_rel: delta},
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.workflow_mode == "planned"
    assert result.drift_detected is False
    assert result.trivially_covered_files == []


def test_cli_drift_json_reports_trivially_covered_files(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: 'eidos drift --json' exposes trivially_covered_files additively."""
    _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            _SPEC_REL: (_SPEC_BASE, _SPEC_BASE + "\n// touched\n"),
        },
    )
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["drift", "--json"])
    data = json.loads(res.output)
    assert data["drift_detected"] is True
    assert data["trivially_covered_files"][0]["file"] == "src/pkg/svc.py"
    assert data["trivially_covered_files"][0]["rule"] == "trivial_spec_diff"
    assert res.exit_code == 1


_SPEC_WITH_LISTING = (
    _SPEC_BASE
    + """
[source,js]
----
// configure the client
client.init();
----
"""
)


def test_code_sample_edit_inside_listing_block_is_substantive(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: '//' lines inside verbatim blocks (code samples) are content, not comments."""
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            _SPEC_REL: (_SPEC_WITH_LISTING, _SPEC_WITH_LISTING.replace("// configure the client", "// use Foo")),
        },
        spec_text=_SPEC_WITH_LISTING,
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is False
    assert result.trivially_covered_files == []


def test_comment_after_listing_block_is_still_trivial(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: comment stripping resumes after a verbatim block closes."""
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            _SPEC_REL: (_SPEC_WITH_LISTING, _SPEC_WITH_LISTING + "\n// touched\n"),
        },
        spec_text=_SPEC_WITH_LISTING,
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert [r["rule"] for r in result.trivially_covered_files] == ["trivial_spec_diff"]


def test_frontmatter_block_scalar_hash_lines_are_content(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: frontmatter is compared as YAML data, so '#' inside block strings is content."""
    base = _SPEC_BASE.replace(
        "title: X\n",
        "title: X\ncomponents:\n  - id: CMP-X\n    title: X Comp\n    responsibility: |\n      # Overview\n      text\n",
    )
    edited = base.replace("      # Overview\n", "      # Overview of Foo\n")
    eidos_dir = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=["src/pkg/svc.py", _SPEC_REL],
        versions={"src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS), _SPEC_REL: (base, edited)},
        spec_text=base,
    )
    result = detect_drift(repo_root=tmp_path, eidos_dir=eidos_dir)
    assert result.drift_detected is False


def test_moved_file_is_not_a_structural_addition(tmp_path, monkeypatch):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: a pure move/rename (paired by content, not VCS rename detection) keeps the
    classic coverage rule; symbols added during the move are still additions."""
    old_rel, new_rel = "src/pkg/svc.py", "src/pkg/service.py"
    trivial_spec = _SPEC_BASE + "\n// moved\n"
    substantive_spec = _SPEC_BASE + "\nMoved service implementation.\n"

    def run(sub, moved_content, spec_text=substantive_spec):
        root = tmp_path / sub
        root.mkdir()
        env = _diff_aware_env(
            root,
            monkeypatch,
            changed=[old_rel, new_rel, _SPEC_REL],
            versions={
                old_rel: (_CODE_WITH_CLASS, None),
                new_rel: (None, moved_content),
                _SPEC_REL: (_SPEC_BASE, spec_text),
            },
            extra_files={new_rel: moved_content},
        )
        return detect_drift(repo_root=root, eidos_dir=env)

    # Identical content, and reformatted content with the same public surface: both pure moves covered by substantive spec (no anchor needed)
    assert run("identical", _CODE_WITH_CLASS).trivially_covered_files == []
    reformatted = _CODE_WITH_CLASS.replace("return 1", "return  1  # same") + "\n\ndef _private():\n    return 0\n"
    pure = run("reformatted", reformatted)
    assert pure.trivially_covered_files == []
    assert pure.drift_detected is False

    # Under DLT-2026-051, even pure moves require substantive spec edits (comment-only edits fail)
    assert (
        run("identical_trivial", _CODE_WITH_CLASS, spec_text=trivial_spec).trivially_covered_files[0]["rule"]
        == "trivial_spec_diff"
    )

    # A move that also adds a public symbol: with a trivial spec, both the deletion and addition are flagged
    grown = run("grown", _CODE_WITH_CLASS + "\n\ndef extra():\n    return 2\n", spec_text=trivial_spec)
    assert (new_rel, "trivial_spec_diff", ["added function 'extra'"]) in [
        (r["file"], r["rule"], r["additions"]) for r in grown.trivially_covered_files
    ]

    # Losing public symbols is not a move: the new path is a genuinely new file
    shrunk = run("shrunk", "def unrelated():\n    return 3\n", spec_text=trivial_spec)
    assert (new_rel, "trivial_spec_diff") in [(r["file"], r["rule"]) for r in shrunk.trivially_covered_files]


def test_git_diff_lists_both_sides_of_renames(tmp_path):
    """INV-DRIFT-DIFF-AWARE-COVERAGE: the changed-file list keeps the deleted source of a rename so eidos can pair
    moves by content itself."""
    import subprocess

    from eidos.vcs.drift import get_git_diff_files

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text(_CODE_WITH_CLASS, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("mv", "a.py", "b.py")
    assert {"a.py", "b.py"} <= set(get_git_diff_files(tmp_path, uncommitted=True))
    git("commit", "-qm", "rename")
    assert {"a.py", "b.py"} <= set(get_git_diff_files(tmp_path, base_ref="HEAD~1"))


def test_test_binding_cache_warm_drift_and_invalidation(tmp_path, monkeypatch):
    """INV-AGENT-GRAPH-INDEX-FRESHNESS & INV-DRIFT-DETECTION-ACCURACY: extract_invariants_and_verify and
    detect_orphan_invariants reuse test_binding_cache with zero ASTIndexer calls on warm runs, and invalidate
    on test file modification (including # eidos:fixture files) or deletion."""  # eidos:fixture
    import os
    import sqlite3

    from eidos.core.index.ast_indexer import get_ast_indexer
    from eidos.vcs.drift import detect_orphan_invariants, extract_invariants_and_verify

    ast_indexer_cls = type(get_ast_indexer())
    eidos_dir = tmp_path / "eidos"
    cap_dir = eidos_dir / "domains" / "core" / "capabilities"
    cap_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    cap_file = cap_dir / "cap.adoc"
    cap_file.write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-CACHE-001\n"
        "title: Cache Capability\n"
        "domain: DOM-CORE\n"
        "status: active\n"
        "version: 1.0.0\n"
        "business:\n"
        "  jtbd: Verify cached invariant bindings.\n"
        "technical:\n"
        "  invariants:\n"
        "    - id: INV-CACHE-BOUND\n"
        "      rule: Must be bound to a specific function.\n"
        "      test_binding: tests/test_alpha.py::test_bound_fn\n"
        "    - id: INV-CACHE-FILE\n"
        "      rule: Must be bound at file level.\n"
        "      test_binding: tests/test_beta.py\n"
        "---\n"
        "= Capability\n",
        encoding="utf-8",
    )

    test_alpha = tests_dir / "test_alpha.py"
    test_alpha.write_text(
        "def test_bound_fn():\n    # INV-CACHE-BOUND\n    assert True\n",
        encoding="utf-8",
    )

    test_beta = tests_dir / "test_beta.py"
    test_beta.write_text(
        "# Module-level reference to INV-CACHE-FILE\n"
        "def test_fixture_opt_out():\n"
        "    # eidos:fixture\n"
        "    fake = 'INV-SYNTHETIC-IGNORED'\n"
        "    assert fake\n\n"
        "def test_real_orphan():\n"
        "    # INV-ORPHAN-ONE\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # Cold run populates test_binding_cache
    cold_invs = extract_invariants_and_verify(eidos_dir, tmp_path)
    cold_map = {i.invariant_id: i.has_test_coverage for i in cold_invs}
    assert cold_map == {"INV-CACHE-BOUND": True, "INV-CACHE-FILE": True}
    cold_orphans = detect_orphan_invariants(tmp_path, set(cold_map.keys()), eidos_dir=eidos_dir)
    assert cold_orphans == [("INV-ORPHAN-ONE", "tests/test_beta.py::test_real_orphan")]

    # 1. Warm run executes zero ASTIndexer.list_test_functions / extract_test_bindings calls
    ast_calls: list[str] = []
    orig_list_funcs = ast_indexer_cls.list_test_functions
    orig_extract_bindings = ast_indexer_cls.extract_test_bindings

    def _spy_list_funcs(self, file_path, content):
        ast_calls.append(f"list:{file_path}")
        return orig_list_funcs(self, file_path, content)

    def _spy_extract_bindings(self, file_path, content):
        ast_calls.append(f"bind:{file_path}")
        return orig_extract_bindings(self, file_path, content)

    monkeypatch.setattr(ast_indexer_cls, "list_test_functions", _spy_list_funcs)
    monkeypatch.setattr(ast_indexer_cls, "extract_test_bindings", _spy_extract_bindings)

    warm_invs = extract_invariants_and_verify(eidos_dir, tmp_path)
    warm_orphans = detect_orphan_invariants(tmp_path, {i.invariant_id for i in warm_invs}, eidos_dir=eidos_dir)
    assert ast_calls == []
    assert [(i.invariant_id, i.has_test_coverage, i.matching_tests) for i in warm_invs] == [
        (i.invariant_id, i.has_test_coverage, i.matching_tests) for i in cold_invs
    ]
    assert warm_orphans == cold_orphans

    # 2. Modify fixture-bearing test_beta.py: only test_beta.py is re-parsed and results match cold run
    test_beta.write_text(
        "# Module-level reference to INV-CACHE-FILE\n"
        "def test_fixture_opt_out():\n"
        "    # eidos:fixture\n"
        "    fake = 'INV-SYNTHETIC-IGNORED'\n"
        "    assert fake\n\n"
        "def test_real_orphan():\n"
        "    # INV-ORPHAN-TWO\n"
        "    assert True\n",
        encoding="utf-8",
    )
    st = test_beta.stat()
    os.utime(test_beta, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    updated_orphans = detect_orphan_invariants(tmp_path, set(cold_map.keys()), eidos_dir=eidos_dir)
    assert ast_calls == ["list:tests/test_beta.py"]
    assert updated_orphans == [("INV-ORPHAN-TWO", "tests/test_beta.py::test_real_orphan")]

    # 3. Delete test_alpha.py: row is pruned from test_binding_cache and INV-CACHE-BOUND becomes uncovered
    ast_calls.clear()
    test_alpha.unlink()
    after_del_invs = extract_invariants_and_verify(eidos_dir, tmp_path)
    assert ast_calls == []
    after_del_map = {i.invariant_id: i.has_test_coverage for i in after_del_invs}
    assert after_del_map == {"INV-CACHE-BOUND": False, "INV-CACHE-FILE": True}

    import contextlib

    db_path = eidos_dir / ".cache" / "index.db"
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        cached_test_files = {r[0] for r in conn.execute("SELECT file_path FROM test_binding_cache;")}
    assert cached_test_files == {"tests/test_beta.py"}


def test_multiple_test_classes_with_same_method_name_do_not_overwrite(tmp_path):
    """Verify two test classes defining the same method name do not overwrite each other and qualified bindings stay class-specific."""
    # eidos:fixture
    eidos_dir = tmp_path / "eidos"
    caps_dir = eidos_dir / "domains" / "core" / "capabilities"
    caps_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    (caps_dir / "cap.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-CLASS-COLLISION\n"
        "title: Class Method Collision\n"
        "domain: DOM-CORE\n"
        "status: active\n"
        "technical:\n"
        "  invariants:\n"
        "    - id: INV-CLASS-FIRST\n"
        "      rule: First test class method must not be overwritten by second class\n"
        "      test_binding: tests/test_classes.py::test_shared_name\n"
        "    - id: INV-CLASS-QUALIFIED\n"
        "      rule: Qualified Class::method binding must resolve accurately\n"
        "      test_binding: tests/test_classes.py::TestSecondSuite::test_shared_name\n"
        "    - id: INV-CLASS-WRONG-SUITE\n"
        "      rule: Binding to TestFirstSuite must not be satisfied by token only in TestSecondSuite\n"
        "      test_binding: tests/test_classes.py::TestFirstSuite::test_shared_name\n"
        "---\n= Cap\n",
        encoding="utf-8",
    )

    (tests_dir / "test_classes.py").write_text(
        "class TestFirstSuite:\n"
        "    def test_shared_name(self):\n"
        '        """Verifies INV-CLASS-FIRST."""\n'
        "        assert True\n\n"
        "class TestSecondSuite:\n"
        "    def test_shared_name(self):\n"
        '        """Verifies INV-CLASS-QUALIFIED and INV-CLASS-WRONG-SUITE."""\n'
        "        assert True\n",
        encoding="utf-8",
    )

    from eidos.vcs.drift import detect_orphan_invariants, extract_invariants_and_verify

    invs = extract_invariants_and_verify(eidos_dir, tmp_path)
    inv_map = {i.invariant_id: i for i in invs}
    assert inv_map["INV-CLASS-FIRST"].has_test_coverage is True
    assert inv_map["INV-CLASS-QUALIFIED"].has_test_coverage is True
    assert inv_map["INV-CLASS-WRONG-SUITE"].has_test_coverage is False

    # Also verify uncached all_tests_content path and underscore-containing orphan invariant detection
    raw_content = {"tests/test_classes.py": (tests_dir / "test_classes.py").read_text(encoding="utf-8")}
    orphans = detect_orphan_invariants(
        tmp_path, {"INV-CLASS-FIRST", "INV-CLASS-QUALIFIED"}, all_tests_content=raw_content
    )
    assert orphans == [("INV-CLASS-WRONG-SUITE", "tests/test_classes.py::TestSecondSuite::test_shared_name")]


def test_nested_docs_subpackage_treated_as_code(tmp_path):
    """INV-DRIFT-DETECTION-ACCURACY: root-anchored docs/ exempts docs, but nested src/pkg/docs is code."""
    changed = [
        "docs/guide.adoc",
        "docs/sub/readme.md",
        "src/pkg/docs/renderer.py",
        "src/docs/builder.py",
    ]
    res = classify_changed_files(changed, repo_root=tmp_path)
    assert set(res.doc_files) == {"docs/guide.adoc", "docs/sub/readme.md"}
    assert set(res.code_files) == {"src/pkg/docs/renderer.py", "src/docs/builder.py"}


def test_unowned_code_triggers_drift_in_direct_and_planned_modes(tmp_path, monkeypatch):
    """INV-DRIFT-DETECTION-ACCURACY: unowned code triggers drift in direct and planned modes unless covered by system spec."""
    unowned_tool = "src/unowned/tool.py"
    persona_spec = "eidos/personas/product-lead.adoc"
    delta_rel = "eidos/deltas/dlt-2026-900.adoc"
    delta = """---
type: delta/mutation
id: DLT-2026-900
title: Unrelated Delta
status: proposed
intent:
  kind: feature
  motivation: Unrelated
mutations:
  - operation: modify
    target: CNT-X
    details: Modify X
---
= Delta
"""
    tool_code = "def unowned():\n    return 42\n"

    # 1. Direct Mode: unowned code with unrelated persona spec triggers drift
    root_direct = tmp_path / "direct"
    env_direct = _diff_aware_env(
        root_direct,
        monkeypatch,
        changed=[unowned_tool, persona_spec],
        versions={
            unowned_tool: (None, tool_code),
            persona_spec: ("old persona", "new persona"),
        },
        extra_files={unowned_tool: tool_code, persona_spec: "new persona"},
    )
    res_direct = detect_drift(repo_root=root_direct, eidos_dir=env_direct)
    assert res_direct.drift_detected is True
    assert unowned_tool in res_direct.unmapped_code_files

    # 2. Planned Mode: unowned code with unrelated delta triggers drift
    root_planned = tmp_path / "planned"
    env_planned = _diff_aware_env(
        root_planned,
        monkeypatch,
        changed=[unowned_tool, delta_rel],
        versions={
            unowned_tool: (None, tool_code),
            delta_rel: (None, delta),
        },
        extra_files={unowned_tool: tool_code, delta_rel: delta},
    )
    res_planned = detect_drift(repo_root=root_planned, eidos_dir=env_planned)
    assert res_planned.drift_detected is True
    assert unowned_tool in res_planned.unmapped_code_files

    # 3. System spec substantive edit covers unowned code
    root_system = tmp_path / "system"
    system_spec = "eidos/system.adoc"
    env_system = _diff_aware_env(
        root_system,
        monkeypatch,
        changed=[unowned_tool, system_spec],
        versions={
            unowned_tool: (None, tool_code),
            system_spec: ("old system", "new system architecture overview"),
        },
        extra_files={unowned_tool: tool_code, system_spec: "new system architecture overview"},
    )
    res_system = detect_drift(repo_root=root_system, eidos_dir=env_system)
    assert unowned_tool not in res_system.unmapped_code_files
    assert res_system.drift_detected is False


def test_global_policy_does_not_satisfy_1hop_drift(tmp_path, monkeypatch):
    """INV-DRIFT-DETECTION-ACCURACY: global policies (POL-*) do not satisfy 1-hop drift coverage for containers."""
    policy_spec = "eidos/policies/pol-001.adoc"
    root = tmp_path / "policy"
    env = _diff_aware_env(
        root,
        monkeypatch,
        changed=["src/pkg/svc.py", policy_spec],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            policy_spec: ("old policy", "new updated policy"),
        },
        extra_files={policy_spec: "new updated policy"},
    )
    res = detect_drift(repo_root=root, eidos_dir=env)
    assert res.drift_detected is True
    assert "CNT-X" in res.touched_components
    assert any("CNT-X" in rem for rem in res.remediation_suggestions)


def test_trivial_system_spec_edit_does_not_clear_unmapped_code_drift(tmp_path, monkeypatch):
    """DLT-2026-051: trivial whitespace/comment edit to eidos/system.adoc does not clear unmapped_code_files drift."""
    unowned_tool = "src/unowned/tool.py"
    system_spec = "eidos/system.adoc"
    sys_base = "---\ntype: system/context\nid: SYS-TEST\ntitle: Test System\n---\n= System\n\nExisting prose.\n"
    sys_trivial = sys_base + "\n// trivial comment only\n   \n"
    tool_code = "def unowned():\n    return 42\n"

    env = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=[unowned_tool, system_spec],
        versions={
            unowned_tool: (None, tool_code),
            system_spec: (sys_base, sys_trivial),
        },
        extra_files={unowned_tool: tool_code, system_spec: sys_trivial},
    )
    res = detect_drift(repo_root=tmp_path, eidos_dir=env)
    assert res.drift_detected is True
    assert unowned_tool in res.unmapped_code_files
    assert any(r["file"] == unowned_tool and r["rule"] == "trivial_spec_diff" for r in res.trivially_covered_files)


def test_trivial_tokens_json_edit_does_not_cover_design_token_drift(monkeypatch):
    """DLT-2026-051: whitespace-only edit to tokens.json is non-substantive and does not satisfy design token drift."""
    tokens_path = "eidos/design/systems/core/tokens.json"
    tokens_old = '{\n  "color": {\n    "primary": { "$value": "#6750a4" }\n  }\n}\n'
    tokens_trivial = '{\n  "color": {\n    "primary": {   "$value":   "#6750a4"   }\n  }\n\n}\n'

    _mock_diff(monkeypatch, [THEME, tokens_path])
    monkeypatch.setattr(
        "eidos.vcs.drift._get_file_versions",
        lambda repo_root, rel_path, base_ref=None, head_ref="HEAD", uncommitted=False, merge_base=None: (
            (tokens_old, tokens_trivial) if rel_path == tokens_path else (_OLD_CSS, _TOKEN_CSS)
        ),
    )
    res = detect_drift(repo_root=Path.cwd(), eidos_dir=Path("eidos"))
    assert res.drift_detected is True
    assert THEME in res.uncovered_design_files
    assert any(r["file"] == THEME and r["rule"] == "trivial_spec_diff" for r in res.trivially_covered_files)


def test_src_test_helpers_and_package_path_test_classification(tmp_path, monkeypatch):
    """DLT-2026-051: src/app/test_helpers.py and <package_path>/test_helpers.py are production code, while nested tests/ is test."""
    from eidos.vcs.drift import classify_changed_files, is_polyglot_test_file

    assert is_polyglot_test_file("src/app/test_helpers.py", repo_root=tmp_path) is False
    assert (
        is_polyglot_test_file("backend/app/test_helpers.py", repo_root=tmp_path, package_paths=["backend/app"]) is False
    )
    assert is_polyglot_test_file("src/pkg/tests/test_unit.py", repo_root=tmp_path) is True

    # Calling with relative path when CWD is <repo>/tests must not prepend tests/
    tests_cwd = tmp_path / "tests"
    tests_cwd.mkdir(parents=True)
    monkeypatch.chdir(tests_cwd)
    assert is_polyglot_test_file("src/app/test_helpers.py", repo_root=tmp_path) is False

    classified = classify_changed_files(
        ["src/app/test_helpers.py", "src/pkg/tests/test_unit.py"],
        repo_root=tmp_path,
    )
    assert "src/app/test_helpers.py" in classified.code_files
    assert "src/pkg/tests/test_unit.py" in classified.test_files


def test_deleted_unmapped_file_covered_by_substantive_container_spec_update(tmp_path, monkeypatch):
    """DLT-2026-051: deleting a file and removing its ownership from a container spec is covered by the substantive container spec edit."""
    deleted_file = "src/legacy/old_mod.py"
    spec_old = _SPEC_BASE.replace(
        "source_path: src/pkg\n",
        "source_path:\n  - src/pkg\n  - src/legacy/old_mod.py\n",
    )
    spec_new = _SPEC_BASE + "\nRemoved legacy module old_mod.py.\n"

    env = _diff_aware_env(
        tmp_path,
        monkeypatch,
        changed=[deleted_file, _SPEC_REL],
        versions={
            deleted_file: ("def legacy():\n    return 0\n", None),
            _SPEC_REL: (spec_old, spec_new),
        },
        spec_text=spec_new,
    )
    res = detect_drift(repo_root=tmp_path, eidos_dir=env)
    assert res.unmapped_code_files == []
    assert res.drift_detected is False


def test_1hop_neighbor_requires_symbol_anchor_for_public_api_addition(tmp_path, monkeypatch):
    """Verify DLT-2026-055 & INV-DRIFT-DIFF-AWARE-COVERAGE: adding public boundary symbols to an existing file requires symbol anchoring when covered only by a 1-hop neighbor spec."""
    cap_rel = "eidos/domains/core/capabilities/cap-x.adoc"
    cap_base = """---
type: product/capability
id: CAP-X-001
title: X Capability
domain: DOM-X
status: active
business:
  jtbd: Execute X
technical:
  containers:
    - CNT-X
---
= X Capability

Original capability prose.
"""
    cap_unanchored = cap_base + "\nUnrelated capability prose update that never mentions the new class.\n"
    cap_anchored = cap_base + "\nExposes the new `Foo` class and `Foo.run` method.\n"

    # 1. Unanchored 1-hop capability edit -> rejected with missing_anchor
    root_unanchored = tmp_path / "unanchored"
    env_unanchored = _diff_aware_env(
        root_unanchored,
        monkeypatch,
        changed=["src/pkg/svc.py", cap_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            cap_rel: (cap_base, cap_unanchored),
        },
        extra_files={cap_rel: cap_unanchored},
    )
    res_unanchored = detect_drift(repo_root=root_unanchored, eidos_dir=env_unanchored)
    assert res_unanchored.drift_detected is True
    assert [r["rule"] for r in res_unanchored.trivially_covered_files] == ["missing_anchor"]

    # 2. Anchored 1-hop capability edit naming Foo -> clean
    root_anchored = tmp_path / "anchored"
    env_anchored = _diff_aware_env(
        root_anchored,
        monkeypatch,
        changed=["src/pkg/svc.py", cap_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            cap_rel: (cap_base, cap_anchored),
        },
        extra_files={cap_rel: cap_anchored},
    )
    res_anchored = detect_drift(repo_root=root_anchored, eidos_dir=env_anchored)
    assert res_anchored.drift_detected is False
    assert res_anchored.trivially_covered_files == []


def test_dlt_2026_068_unquoted_git_paths_container_invariants_and_reconcile(tmp_path):
    """Verify DLT-2026-068: unquoted git paths and top-level tech/container invariants."""  # eidos:fixture
    import subprocess

    from eidos.vcs.diff import get_git_diff_files, get_tracked_files
    from eidos.vcs.drift import extract_invariants_and_verify

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")

    # 1. File with spaces and non-ASCII characters must not be returned quoted/octal-escaped
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    unicode_rel = "src/pkg/my café.py"
    (tmp_path / unicode_rel).write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "initial")

    tracked = get_tracked_files(tmp_path)
    assert tracked is not None
    assert unicode_rel in tracked

    (tmp_path / unicode_rel).write_text("def greet(name: str):\n    return name\n", encoding="utf-8")
    diff_files = get_git_diff_files(tmp_path, uncommitted=True)
    assert unicode_rel in diff_files

    # 2. Top-level tech/container invariants and components invariants
    eidos_dir = tmp_path / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    caps_dir = eidos_dir / "domains" / "core" / "capabilities"
    cnts_dir.mkdir(parents=True)
    caps_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    (cnts_dir / "pkg.adoc").write_text(
        """---
type: tech/container
id: CNT-PKG
title: Pkg Container
container_type: library
runtime: Python
source_path: src/pkg
invariants:
  - id: INV-CNT-TOP-LEVEL
    rule: Container-level invariant
    test_binding: tests/test_pkg.py::test_cnt_top
components:
  - id: CMP-C4-INTERNAL
    title: C4 Internal
    responsibility: Internal module
    source_path: src/pkg/internal.py
    invariants:
      - id: INV-CMP-C4-INTERNAL
        rule: C4 internal component invariant
        test_binding: tests/test_pkg.py::test_c4_internal
---
= Pkg Container
""",
        encoding="utf-8",
    )
    (caps_dir / "cap-pkg.adoc").write_text(
        """---
type: product/capability
id: CAP-PKG-001
title: Pkg Capability
domain: DOM-CORE
status: active
business:
  jtbd: Run Pkg
technical:
  containers:
    - CNT-PKG
---
= Pkg Capability
""",
        encoding="utf-8",
    )
    (tests_dir / "test_pkg.py").write_text(
        "# INV-CNT-TOP-LEVEL\ndef test_cnt_top():\n    assert True\n\n\n"
        "# INV-CMP-C4-INTERNAL\ndef test_c4_internal():\n    assert True\n",
        encoding="utf-8",
    )

    invs = {inv.invariant_id: inv for inv in extract_invariants_and_verify(eidos_dir, tmp_path)}
    assert invs["INV-CNT-TOP-LEVEL"].has_test_coverage is True
    assert invs["INV-CNT-TOP-LEVEL"].spec_id == "CNT-PKG"
    assert invs["INV-CMP-C4-INTERNAL"].has_test_coverage is True
    assert invs["INV-CMP-C4-INTERNAL"].spec_id == "CMP-C4-INTERNAL"


def test_dlt_2026_074_design_subgraph_inheritance_and_cross_client_flows():
    """Verify DLT-2026-074: _collect_design_subgraph traverses extends, design_system, and client flows."""
    from eidos.vcs.structural import _collect_design_subgraph

    entities = [
        {
            "id": "CLT-WEB",
            "type": "design/client",
            "file_path": "eidos/design/clients/web/client.adoc",
            "flows": ["FLOW-HANDOFF"],
        },
        {
            "id": "CLT-MOBILE",
            "type": "design/client",
            "file_path": "eidos/design/clients/mobile/client.adoc",
            "design_system": "DS-BRAND",
            "flows": ["FLOW-HANDOFF"],
        },
        {
            "id": "DS-BRAND",
            "type": "design/system",
            "file_path": "eidos/design/systems/brand/system.adoc",
            "extends": "DS-FOUNDATION",
        },
        {
            "id": "DS-FOUNDATION",
            "type": "design/system",
            "file_path": "eidos/design/systems/foundation/system.adoc",
            "tokens": {"manifest": "tokens.json"},
        },
        {
            "id": "UIC-BTN",
            "type": "design/ui-component",
            "file_path": "eidos/design/systems/foundation/components/btn.adoc",
            "design_system": "DS-FOUNDATION",
        },
        {
            "id": "FLOW-HANDOFF",
            "type": "design/flow",
            "file_path": "eidos/design/flows/handoff.adoc",
            "flow_type": "cross_client",
        },
    ]

    paths, sub_ids = _collect_design_subgraph(entities, {"CLT-WEB", "CLT-MOBILE"})
    assert {"CLT-WEB", "CLT-MOBILE", "DS-BRAND", "DS-FOUNDATION", "UIC-BTN", "FLOW-HANDOFF"}.issubset(sub_ids)
    assert "eidos/design/systems/foundation/tokens.json" in paths
    assert "eidos/design/flows/handoff.adoc" in paths


def test_nested_spec_subpackage_treated_as_code(tmp_path):
    """Verify nested src/.../spec/*.py subpackages are classified as code_files while root spec/ and nested tests/ are test_files."""
    from eidos.vcs.drift import is_polyglot_test_file

    assert is_polyglot_test_file("src/pkg/spec/validator.py", repo_root=tmp_path) is False
    assert is_polyglot_test_file("src/pkg/specs/models.py", repo_root=tmp_path) is False
    assert is_polyglot_test_file("spec/auth_helper.py", repo_root=tmp_path) is True
    assert is_polyglot_test_file("src/pkg/tests/test_validator.py", repo_root=tmp_path) is True

    classified = classify_changed_files(
        [
            "src/pkg/spec/validator.py",
            "src/pkg/specs/models.py",
            "spec/auth_helper.py",
            "src/pkg/tests/test_validator.py",
        ],
        repo_root=tmp_path,
    )
    assert set(classified.code_files) == {"src/pkg/spec/validator.py", "src/pkg/specs/models.py"}
    assert set(classified.test_files) == {"spec/auth_helper.py", "src/pkg/tests/test_validator.py"}


def test_deep_review_subsystem_4_shallow_clone_deleted_spec_and_json_cli(tmp_path):
    """Verify shallow clone auto-discovery fails closed, deleted specs are not substantive updates,
    and plugin hooks reject frontmatter-less .adoc files."""
    import subprocess

    import pytest

    from eidos.plugin.hooks import handle_stop_hook
    from eidos.vcs.diff import GitResolutionError, _resolve_base_and_merge_base
    from eidos.vcs.structural import _SpecDiffInspector

    # 1. Deleted spec (new is None) is NOT treated as a substantive spec update
    inspector = _SpecDiffInspector(
        tmp_path,
        base_ref=None,
        head_ref="HEAD",
        uncommitted=True,
    )
    inspector._versions["eidos/architecture/containers/cnt-x.adoc"] = (
        "---\nid: CNT-X\n---\n= Old\n",
        None,
    )
    assert inspector.is_substantive("eidos/architecture/containers/cnt-x.adoc") is False

    # 2. Shallow clone (.git/shallow exists) fails closed when base_ref is not resolvable during auto-discovery
    repo = tmp_path / "shallow_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")
    with pytest.raises(GitResolutionError, match="shallow"):
        _resolve_base_and_merge_base(repo, base_ref=None, head_ref="HEAD")

    # 3. Stateless Stop hook surfaces non-exempt .adoc missing YAML frontmatter via systemMessage warning
    dom_dir = tmp_path / "eidos" / "domains"
    dom_dir.mkdir(parents=True, exist_ok=True)
    (dom_dir / "dom-test.adoc").write_text("= Missing Frontmatter\nBody only.\n", encoding="utf-8")
    res_doc = handle_stop_hook({}, workspace_root=tmp_path)
    assert res_doc["decision"] == "allow"
    assert "frontmatter delimiter ('---')" in res_doc["systemMessage"]


def test_real_git_structural_and_design_drift_and_cache_mtime_hit(tmp_path):
    """End-to-end Git test (zero diff/version mocks) for structural drift, comment-only vs substantive spec edit,
    design token drift, HEAD==main fallback to HEAD~1, invalid head_ref, and test_binding_cache SHA-256 hit."""  # eidos:fixture
    import os
    import sqlite3
    import subprocess
    import time

    import pytest

    import eidos.core.index.graph_index as gi
    from eidos.vcs.diff import GitResolutionError
    from eidos.vcs.drift import detect_drift
    from eidos.vcs.invariants import _load_or_update_test_binding_cache

    repo = tmp_path / "real_git_repo"
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    ds_dir = eidos_dir / "design" / "systems" / "core"
    clt_dir = eidos_dir / "design" / "clients" / "web"
    pol_dir = eidos_dir / "policies"
    svc_dir = repo / "src" / "svc"
    tests_dir = repo / "tests"
    for d in (cnt_dir, ds_dir, clt_dir, pol_dir, svc_dir, tests_dir):
        d.mkdir(parents=True)

    cnt_file = cnt_dir / "cnt-svc.adoc"
    cnt_file.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-SVC\n"
        "title: Order Service\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: src/svc\n"
        "---\n"
        "= Order Service\n\n"
        "Baseline architecture.\n",
        encoding="utf-8",
    )
    tokens_file = ds_dir / "tokens.json"
    tokens_file.write_text(
        '{"color": {"primary": {"$value": "#0055ff", "$type": "color"}}}',
        encoding="utf-8",
    )
    (ds_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "id: DS-CORE\n"
        "title: Core Tokens\n"
        "status: active\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: tokens.json\n"
        "---\n"
        "= Core Tokens\n",
        encoding="utf-8",
    )
    (clt_dir / "client.adoc").write_text(
        "---\n"
        "type: design/client\n"
        "id: CLT-WEB\n"
        "title: Web Client\n"
        "platform: web-spa\n"
        "container: CNT-SVC\n"
        "design_system: DS-CORE\n"
        "---\n"
        "= Web Client\n",
        encoding="utf-8",
    )
    (pol_dir / "pol.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-SVC-001\n"
        "title: Service Policy\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-SVC-001\n"
        "    rule: Service handler must be tested.\n"
        "    test_binding: tests/test_svc.py::test_inv_svc\n"
        "---\n"
        "= Service Policy\n",
        encoding="utf-8",
    )
    handler_py = svc_dir / "handler.py"
    handler_py.write_text("def health() -> bool:\n    return True\n", encoding="utf-8")
    theme_css = svc_dir / "theme.css"
    theme_css.write_text(":root { --color-primary: #0055ff; }\n", encoding="utf-8")
    test_file = tests_dir / "test_svc.py"
    test_file.write_text("def test_inv_svc():\n    # INV-SVC-001\n    assert True\n", encoding="utf-8")

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial baseline"], cwd=repo, check=True, capture_output=True)

    try:
        # 1. Structural addition in code + comment-only edit in cnt-svc.adoc -> drift detected!
        handler_py.write_text(
            "def health() -> bool:\n    return True\n\ndef process_order(order_id: str) -> dict:\n    return {'id': order_id}\n",
            encoding="utf-8",
        )
        cnt_file.write_text(
            cnt_file.read_text(encoding="utf-8") + "// trivial comment only\n",
            encoding="utf-8",
        )
        rep1 = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
        assert rep1.drift_detected is True
        assert len(rep1.trivially_covered_files) >= 1
        assert "CNT-SVC" in rep1.touched_components

        # 2. Substantive update to cnt-svc.adoc -> structural drift resolved!
        cnt_file.write_text(
            cnt_file.read_text(encoding="utf-8") + "\nAdded process_order endpoint handler.\n",
            encoding="utf-8",
        )
        rep2 = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
        assert rep2.drift_detected is False

        # 3. Modify CSS token in src/svc/theme.css without updating tokens.json -> design drift detected!
        theme_css.write_text(":root { --color-primary: #ff0055; }\n", encoding="utf-8")
        rep3 = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
        assert rep3.drift_detected is True
        assert len(rep3.uncovered_design_files) >= 1

        # 4. Update tokens.json -> design drift resolved!
        tokens_file.write_text(
            '{"color": {"primary": {"$value": "#ff0055", "$type": "color"}}}',
            encoding="utf-8",
        )
        rep4 = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
        assert rep4.drift_detected is False

        # 5. Commit on main so HEAD == main, then run detect_drift(uncommitted=False) to exercise HEAD~1 fallback
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: add process_order and update tokens"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        rep_committed = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=False)
        assert rep_committed.drift_detected is False
        assert "src/svc/handler.py" in rep_committed.classification.code_files

        # 6. Invalid head_ref raises GitResolutionError
        with pytest.raises(GitResolutionError, match="Cannot resolve head Git reference"):
            detect_drift(repo_root=repo, eidos_dir=eidos_dir, head_ref="nonexistent-head-999", uncommitted=False)

        # 7. test_binding_cache SHA-256 hit when mtime_ns changes but content is identical
        _load_or_update_test_binding_cache(repo, eidos_dir=eidos_dir)
        db_path = eidos_dir / ".cache" / "index.db"
        bump_ns = time.time_ns() + 5_000_000_000
        os.utime(test_file, ns=(bump_ns, bump_ns))
        cached = _load_or_update_test_binding_cache(repo, eidos_dir=eidos_dir)
        assert "tests/test_svc.py" in cached
        import contextlib

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            cached_mtime = conn.execute(
                "SELECT mtime_ns FROM test_binding_cache WHERE file_path = 'tests/test_svc.py';"
            ).fetchone()
            assert cached_mtime is not None and cached_mtime[0] == test_file.stat().st_mtime_ns
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None


def test_drift_report_helpers_and_cli_rich_output(tmp_path):
    """Cover vcs/report.py remediation/summary helpers and cli/drift_commands.py Rich terminal branches."""  # eidos:fixture
    import subprocess
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.vcs.report import generate_remediation_prompt

    # 1. vcs/report.py: generate_remediation_prompt for code drift vs non-code schema/invariant/orphan drift
    res_code = SimpleNamespace(
        code_files=["src/app.py"],
        validation_passed=True,
        validation_errors=[],
        uncovered_invariants=[],
        orphan_invariants=[],
        remediation_suggestions=["Update CNT-APP"],
    )
    prompt_code = generate_remediation_prompt(res_code)
    assert "Modified Source Code Files" in prompt_code and "Update CNT-APP" in prompt_code

    res_schema_only = SimpleNamespace(
        code_files=[],
        classification=SimpleNamespace(code_files=[]),
        validation_passed=False,
        validation_errors=["bad schema"],
        uncovered_invariants=[
            SimpleNamespace(
                invariant_id="INV-BRK-1",
                spec_path="eidos/policies/pol.adoc",
                test_binding="tests/t.py::test_1",
            )
        ],
        orphan_invariants=[("INV-ORPHAN-1", "tests/t.py:1")],
        remediation_suggestions=["Fix schema"],
    )
    prompt_schema = generate_remediation_prompt(res_schema_only)
    assert "Non-Code Drift / Governance Verification Failures" in prompt_schema
    assert "Validation error: bad schema" in prompt_schema
    assert "Broken invariant binding: `INV-BRK-1`" in prompt_schema
    assert "Orphan invariant reference: `INV-ORPHAN-1`" in prompt_schema

    # 2. cli/drift_commands.py: invoke `eidos drift --uncommitted --path <eidos_dir>` without --json on a drifting repo
    repo = tmp_path / "drift_cli_repo"
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    svc_dir = repo / "src" / "svc"
    tests_dir = repo / "tests"
    for d in (cnt_dir, svc_dir, tests_dir):
        d.mkdir(parents=True)
    (cnt_dir / "cnt.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-CLI\ntitle: CLI\ncontainer_type: service\nruntime: Python\nsource_path: src/svc\n---\n= CLI\n",
        encoding="utf-8",
    )
    code_f = svc_dir / "mod.py"
    code_f.write_text("def a():\n    return 1\n", encoding="utf-8")
    (tests_dir / "test_mod.py").write_text(
        "def test_orphan():\n    # INV-ORPHAN-999\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Introduce uncommitted structural code addition + invalid spec so Rich output renders drift + validation errors
    code_f.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n", encoding="utf-8")
    (cnt_dir / "broken.adoc").write_text("---\ntype: tech/container\nid: INVALID_ID\n---\n= Broken\n", encoding="utf-8")

    runner = CliRunner()
    md_out = tmp_path / "drift_summary.md"
    res_cli = runner.invoke(app, ["drift", "--uncommitted", "--path", str(eidos_dir), "--markdown", str(md_out)])
    assert res_cli.exit_code == 1
    assert "DRIFT DETECTED" in res_cli.output
    assert "Specification Schema Errors" in res_cli.output
    assert md_out.exists()

    # 3. vcs/diff.py: collect_polyglot_test_files, _resolve_base_and_merge_base fallback scan & shallow error, get_git_diff_files errors
    import pytest

    import eidos.vcs.drift as drift_mod
    from eidos.vcs.diff import (
        GitResolutionError,
        _resolve_base_and_merge_base,
        collect_polyglot_test_files,
        get_git_diff_files,
    )
    from eidos.vcs.invariants import (
        _verify_single_invariant,
        detect_orphan_invariants,
        extract_invariants_and_verify,
        get_living_specs_catalog,
    )

    assert collect_polyglot_test_files(tmp_path / "does_not_exist_dir") == {}
    poly_tests = collect_polyglot_test_files(repo)
    assert "tests/test_mod.py" in poly_tests

    with pytest.raises(GitResolutionError, match="Cannot resolve base Git reference"):
        get_git_diff_files(repo, base_ref="nonexistent-base-ref")
    with pytest.raises(GitResolutionError, match="Cannot resolve head Git reference"):
        get_git_diff_files(repo, base_ref="main", head_ref="nonexistent-head-ref")
    assert isinstance(get_git_diff_files(repo, merge_base="HEAD"), list)

    # _resolve_base_and_merge_base fallback scan when first candidate has no merge-base and .git/shallow exists
    orig_base_fn = getattr(drift_mod, "_resolve_base_ref", None)
    orig_mb_fn = getattr(drift_mod, "_resolve_merge_base", None)
    (repo / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")
    try:
        drift_mod._resolve_base_ref = lambda _r, _b=None: None  # type: ignore[attr-defined]
        drift_mod._resolve_merge_base = lambda _r, _b, _h="HEAD", fail_loudly=False: None  # type: ignore[attr-defined]
        with pytest.raises(GitResolutionError, match="shallow clone"):
            _resolve_base_and_merge_base(repo, base_ref=None, head_ref="HEAD")
        drift_mod._resolve_base_ref = lambda _r, _b=None: "origin/main"  # type: ignore[attr-defined]
        with pytest.raises(GitResolutionError, match="shallow clone"):
            _resolve_base_and_merge_base(repo, base_ref=None, head_ref="HEAD")
    finally:
        if orig_base_fn is not None:
            drift_mod._resolve_base_ref = orig_base_fn  # type: ignore[attr-defined]
        else:
            delattr(drift_mod, "_resolve_base_ref")
        if orig_mb_fn is not None:
            drift_mod._resolve_merge_base = orig_mb_fn  # type: ignore[attr-defined]
        else:
            delattr(drift_mod, "_resolve_merge_base")
        (repo / ".git" / "shallow").unlink(missing_ok=True)

    # 4. vcs/invariants.py: uncached string path in _verify_single_invariant & detect_orphan_invariants
    (cnt_dir / "broken.adoc").unlink()
    pol_p = eidos_dir / "policies" / "pol_inv.adoc"
    pol_p.parent.mkdir(parents=True, exist_ok=True)
    pol_p.write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-INV-1\n"
        "title: Inv Pol\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-UNCACHED-1\n"
        "    rule: Qualified func match\n"
        "    test_binding: tests/test_mod.py::test_orphan\n"
        "  - id: INV-UNCACHED-FILE\n"
        "    rule: File-level match\n"
        "    test_binding: tests/test_mod.py\n"
        "  - id: INV-UNCACHED-MISS\n"
        "    rule: Missing func\n"
        "    test_binding: tests/test_mod.py::test_missing_fn\n"
        "---\n"
        "= Inv Pol\n",
        encoding="utf-8",
    )
    (tests_dir / "test_mod.py").write_text(
        "def test_orphan():\n    # INV-UNCACHED-1\n    # INV-UNCACHED-FILE\n    assert True\n",
        encoding="utf-8",
    )
    traces = extract_invariants_and_verify(eidos_dir, repo)
    by_id = {t.invariant_id: t for t in traces}
    assert by_id["INV-UNCACHED-1"].has_test_coverage is True
    assert by_id["INV-UNCACHED-FILE"].has_test_coverage is True
    assert by_id["INV-UNCACHED-MISS"].has_test_coverage is False

    # Call _verify_single_invariant with uncached string dict (uses_cached_entries=False) and missing entry on disk
    str_map: dict[str, object] = {"tests/other.py": "def test_other(): pass\n"}
    matched_disk_str = _verify_single_invariant(
        iid="INV-UNCACHED-1",
        tb="tests/test_mod.py::test_orphan",
        repo_root=repo,
        all_tests_content=str_map,
        inv_to_test_funcs={},
        ast_idx=None,
    )
    assert "tests/test_mod.py::test_orphan" in matched_disk_str
    matched_file_str = _verify_single_invariant(
        iid="INV-UNCACHED-FILE",
        tb="tests/test_mod.py",
        repo_root=repo,
        all_tests_content=str_map,
        inv_to_test_funcs={},
        ast_idx=None,
    )
    assert "tests/test_mod.py" in matched_file_str
    matched_no_tb = _verify_single_invariant(
        iid="INV-UNCACHED-1",
        tb=None,
        repo_root=repo,
        all_tests_content=str_map,
        inv_to_test_funcs={},
        ast_idx=None,
    )
    assert "tests/test_mod.py" in matched_no_tb

    # Also test _verify_single_invariant with empty dict (uses_cached_entries=True, loads from disk)
    assert "tests/test_mod.py::test_orphan" in _verify_single_invariant(
        iid="INV-UNCACHED-1",
        tb="tests/test_mod.py::test_orphan",
        repo_root=repo,
        all_tests_content={},
        inv_to_test_funcs={},
        ast_idx=None,
    )

    # Call detect_orphan_invariants on a file that mixes # eidos:fixture and a non-fixture function with an orphan
    mixed_orphans = detect_orphan_invariants(
        repo,
        {"INV-UNCACHED-1", "INV-UNCACHED-FILE", "INV-UNCACHED-MISS"},
        all_tests_content={
            "tests/test_mixed.py": (
                "def test_fixture_fn():\n"
                '    """Fixture docstring."""  # eidos:fixture\n'
                "    x = 'INV-SYNTHETIC-111'\n"
                "\n"
                "def test_real_orphan_fn():\n"
                "    # INV-REAL-ORPHAN-222\n"
                "    assert True\n"
            )
        },
    )
    orphan_ids = {oid for oid, _ in mixed_orphans}
    assert "INV-SYNTHETIC-111" not in orphan_ids
    assert "INV-REAL-ORPHAN-222" in orphan_ids
    assert get_living_specs_catalog(tmp_path / "missing_eidos") == {
        "capabilities": [],
        "components": [],
        "contracts": [],
        "policies": [],
    }

    # Exercise _get_file_versions with uncommitted=False (lines 764-778) and _resolve_merge_base fail_loudly (94-97)
    from eidos.vcs.diff import GitResolutionError, _get_file_versions, _resolve_merge_base

    old_v, new_v = _get_file_versions(repo, "src/svc/mod.py", base_ref="main", head_ref="HEAD", uncommitted=False)
    assert old_v is not None and new_v is not None
    old_miss, new_miss = _get_file_versions(
        repo, "src/svc/nonexistent.py", base_ref="main", head_ref="HEAD", uncommitted=False
    )
    assert old_miss is None and new_miss is None
    assert _get_file_versions(repo, "src/svc/mod.py", base_ref=None, head_ref="HEAD", uncommitted=False)[0] is not None
    no_git_dir = tmp_path / "no_git_dir"
    no_git_dir.mkdir(exist_ok=True)
    assert _get_file_versions(no_git_dir, "src/svc/mod.py", base_ref=None, head_ref="HEAD", uncommitted=False) == (
        None,
        None,
    )
    assert _resolve_merge_base(repo, "nonexistent-branch-xyz", "HEAD", fail_loudly=False) is None
    with pytest.raises(GitResolutionError, match="Cannot find merge base"):
        _resolve_merge_base(repo, "nonexistent-branch-xyz", "HEAD", fail_loudly=True)
    with pytest.raises(GitResolutionError, match="Failed to execute git diff"):
        get_git_diff_files(repo, base_ref="main", merge_base="invalid-merge-base-999")


def test_dlt_2026_081_boundary_aware_symbol_anchoring(tmp_path, monkeypatch):
    """Verify DLT-2026-081: module-internal helper functions are covered by substantive 1-hop spec updates without forcing helper names into prose, while boundary exports require anchoring."""
    cap_rel = "eidos/domains/core/capabilities/cap-x.adoc"
    cap_base = """---
type: product/capability
id: CAP-X
title: X Capability
domain: DOM-CORE
status: active
business:
  jtbd: Execute X
technical:
  containers:
    - CNT-X
---
= X Capability

Original capability prose.
"""
    cap_substantive = cap_base + "\nEnhances payload checksum validation during execution.\n"

    # 1. Module-internal helper function in src/pkg/svc.py (no __all__, not __init__.py)
    # covered by a substantive 1-hop capability update without pasting helper_checksum_fn into prose -> clean!
    code_with_internal_helper = _CODE_OLD + "\ndef helper_checksum_fn(data):\n    return len(data)\n"
    root_internal = tmp_path / "internal_helper"
    env_internal = _diff_aware_env(
        root_internal,
        monkeypatch,
        changed=["src/pkg/svc.py", cap_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, code_with_internal_helper),
            cap_rel: (cap_base, cap_substantive),
        },
        extra_files={cap_rel: cap_substantive},
    )
    res_internal = detect_drift(repo_root=root_internal, eidos_dir=env_internal)
    assert res_internal.drift_detected is False
    assert res_internal.trivially_covered_files == []

    # 2. Boundary export via __all__ in src/pkg/svc.py covered only by unanchored 1-hop capability update -> missing_anchor!
    code_with_dunder_all = (
        '__all__ = ["existing", "exported_boundary_fn"]\n'
        + _CODE_OLD
        + "\ndef exported_boundary_fn(data):\n    return data\n"
    )
    root_boundary = tmp_path / "boundary_unanchored"
    env_boundary = _diff_aware_env(
        root_boundary,
        monkeypatch,
        changed=["src/pkg/svc.py", cap_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, code_with_dunder_all),
            cap_rel: (cap_base, cap_substantive),
        },
        extra_files={cap_rel: cap_substantive},
    )
    res_boundary = detect_drift(repo_root=root_boundary, eidos_dir=env_boundary)
    assert res_boundary.drift_detected is True
    assert [r["rule"] for r in res_boundary.trivially_covered_files] == ["missing_anchor"]

    # 3. Boundary export via __all__ anchored in 1-hop capability update -> clean!
    cap_anchored = cap_base + "\nExposes `exported_boundary_fn` for external callers.\n"
    root_boundary_ok = tmp_path / "boundary_anchored"
    env_boundary_ok = _diff_aware_env(
        root_boundary_ok,
        monkeypatch,
        changed=["src/pkg/svc.py", cap_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, code_with_dunder_all),
            cap_rel: (cap_base, cap_anchored),
        },
        extra_files={cap_rel: cap_anchored},
    )
    res_boundary_ok = detect_drift(repo_root=root_boundary_ok, eidos_dir=env_boundary_ok)
    assert res_boundary_ok.drift_detected is False
    assert res_boundary_ok.trivially_covered_files == []


def test_dlt_2026_085_planned_mode_scope_from_mutations_targets_and_diff_helpers(tmp_path: Path, monkeypatch) -> None:
    """DLT-2026-085 (# eidos:fixture): Verify detect_drift derives Planned Mode scope strictly from
    mutations[].target (ignoring legacy affects/blast_radius) and cover vcs/diff.py helpers."""
    import subprocess

    from eidos.vcs.diff import (
        GitResolutionError,
        _has_structural_ast_change,
        collect_polyglot_test_files,
        get_git_diff_files,
        get_tracked_files,
    )

    # 1. DLT-2026-085: Active delta mutating CNT-OTHER does not authorize edits to src/pkg/svc.py (owned by CNT-X),
    #    even if legacy affects/blast_radius lists CNT-X.
    dlt_rel = "eidos/deltas/dlt-2026-085-test.adoc"
    dlt_unscoped = (
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-085\n"
        "title: Unscoped Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Test scope\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CNT-OTHER\n"
        "affects:\n"
        "  containers:\n"
        "    - CNT-X\n"
        "blast_radius:\n"
        "  impacted_containers:\n"
        "    - CNT-X\n"
        "---\n"
        "= Unscoped\n\nAdds `Foo` handler.\n"
    )
    root_unscoped = tmp_path / "unscoped_delta"
    env_unscoped = _diff_aware_env(
        root_unscoped,
        monkeypatch,
        changed=["src/pkg/svc.py", dlt_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            dlt_rel: (None, dlt_unscoped),
        },
        extra_files={dlt_rel: dlt_unscoped},
    )
    res_unscoped = detect_drift(repo_root=root_unscoped, eidos_dir=env_unscoped)
    assert res_unscoped.drift_detected is True
    assert any("src/pkg/svc.py" in s and "CNT-X" in s for s in res_unscoped.remediation_suggestions)

    # When mutations[].target targets CNT-X, Planned Mode authorizes src/pkg/svc.py
    dlt_scoped = (
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-085\n"
        "title: Scoped Delta\n"
        "status: proposed\n"
        "intent:\n"
        "  kind: refactor\n"
        "  motivation: Test scope\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: CNT-X\n"
        "---\n"
        "= Scoped\n\nAdds `Foo` handler.\n"
    )
    root_scoped = tmp_path / "scoped_delta"
    env_scoped = _diff_aware_env(
        root_scoped,
        monkeypatch,
        changed=["src/pkg/svc.py", dlt_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            dlt_rel: (None, dlt_scoped),
        },
        extra_files={dlt_rel: dlt_scoped},
    )
    res_scoped = detect_drift(repo_root=root_scoped, eidos_dir=env_scoped)
    assert res_scoped.drift_detected is False
    assert not any("src/pkg/svc.py" in s for s in res_scoped.remediation_suggestions)

    # 2. Cover vcs/diff.py helpers: _has_structural_ast_change, collect_polyglot_test_files (non-git + missing), get_git_diff_files
    assert _has_structural_ast_change("def a(): return 1\n", "def a(x): return x\n", "src/a.py") is True
    assert _has_structural_ast_change("def a(): return 1\n", "def a(): return 2\n", "src/a.py") is False
    assert collect_polyglot_test_files(tmp_path / "nonexistent_dir") == {}

    nongit_dir = tmp_path / "nongit_ws"
    (nongit_dir / "tests").mkdir(parents=True)
    (nongit_dir / "tests" / "test_sample.py").write_text("def test_one(): pass\n", encoding="utf-8")
    collected_nongit = collect_polyglot_test_files(nongit_dir)
    assert "tests/test_sample.py" in collected_nongit

    # Real git repo diff with merge_base, invalid base_ref raising GitResolutionError, and get_tracked_files
    git_ws = tmp_path / "git_diff_ws"
    git_ws.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=git_ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@eidos.dev"], cwd=git_ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=git_ws, check=True, capture_output=True)
    (git_ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "c1"], cwd=git_ws, check=True, capture_output=True)
    c1_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_ws, check=True, capture_output=True, text=True
    ).stdout.strip()
    (git_ws / "b.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_ws, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "c2"], cwd=git_ws, check=True, capture_output=True)

    assert get_git_diff_files(git_ws, base_ref=c1_sha, head_ref="HEAD", merge_base=c1_sha) == ["b.py"]
    assert get_git_diff_files(git_ws, base_ref=c1_sha, head_ref="HEAD", merge_base="") == ["b.py"]
    assert set(get_tracked_files(git_ws, include_untracked=False)) == {"a.py", "b.py"}
    with pytest.raises(GitResolutionError):
        get_git_diff_files(
            git_ws, base_ref="bad_nonexistent_ref_xyz", head_ref="HEAD", merge_base="bad_nonexistent_ref_xyz"
        )


def test_origin_main_head_fallback_remediation_prompt_and_line_level_fixture(tmp_path):
    """Verify Bugs 2, 13, 14: origin/main==HEAD advances to HEAD~1, generate_remediation_prompt includes validation/invariant
    errors alongside code_files, and line-level # eidos:fixture skips only that line."""  # eidos:fixture
    import subprocess

    from eidos.core.spec.validator import ValidationReport
    from eidos.vcs.diff import ChangedFilesClassification, _resolve_base_and_merge_base, get_git_diff_files
    from eidos.vcs.drift import DriftCheckResult, InvariantTrace, detect_orphan_invariants, generate_remediation_prompt

    # 1. Bug 2: When both origin/main and main point to HEAD, _resolve_base_and_merge_base advances to HEAD~1
    repo = tmp_path / "origin_main_repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo, check=True, capture_output=True)
    (repo / "first.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "c1"], cwd=repo, check=True, capture_output=True)
    (repo / "second.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "c2"], cwd=repo, check=True, capture_output=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head_sha],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    resolved_base, _resolved_mb, fell_back = _resolve_base_and_merge_base(repo, None, "HEAD")
    assert fell_back is False
    assert resolved_base == "HEAD~1"
    assert get_git_diff_files(repo, uncommitted=False) == ["second.py"]

    # 2. Bug 13: generate_remediation_prompt renders validation_errors, uncovered_invariants, and orphan_invariants even when code_files is non-empty
    val_rep = ValidationReport(
        schema_errors={"eidos/policies/bad.adoc": ["Missing required 'rule'"]},
        broken_references=["CMP-GHOST referenced in CNT-CORE"],
    )
    drift_res = DriftCheckResult(
        is_clean=False,
        drift_detected=True,
        workflow_mode="direct",
        classification=ChangedFilesClassification(code_files=["src/app.py"]),
        validation_report=val_rep,
        touched_components=["CNT-CORE"],
        invariants=[
            InvariantTrace(
                invariant_id="INV-UNCOVERED-001",
                rule="Must be covered.",
                spec_path="eidos/policies/pol.adoc",
                spec_id="POL-001",
                test_binding="tests/test_app.py::test_missing",
                has_test_coverage=False,
                matching_tests=[],
            )
        ],
        orphan_invariants=[("INV-ORPHAN-999", "tests/test_app.py::test_orphan")],
    )
    prompt = generate_remediation_prompt(drift_res)
    assert "Modified Source Code Files (causing drift):" in prompt
    assert "src/app.py" in prompt
    assert "Governance & Verification Failures:" in prompt
    assert "Missing required 'rule'" in prompt
    assert "CMP-GHOST referenced in CNT-CORE" in prompt
    assert "Broken invariant binding: `INV-UNCOVERED-001`" in prompt
    assert "Orphan invariant reference: `INV-ORPHAN-999`" in prompt

    # 3. Bug 14: Line-level # eidos:fixture inside a test function suppresses only that line, not the whole function
    fixture_ws = tmp_path / "line_fixture_ws"
    (fixture_ws / "eidos").mkdir(parents=True)
    (fixture_ws / "tests").mkdir(parents=True)
    test_content = (
        "def test_mixed_lines():\n"
        "    synthetic = 'INV-LINE-IGNORED-001'  # " + "eidos:fixture\n"
        "    real_orphan = 'INV-LINE-ORPHAN-002'\n"
        "    assert synthetic and real_orphan\n"
    )
    (fixture_ws / "tests" / "test_line_fixture.py").write_text(test_content, encoding="utf-8")

    # Both uncached (all_tests_content provided) and cached (all_tests_content=None) must match
    uncached_orphans = detect_orphan_invariants(
        fixture_ws, set(), all_tests_content={"tests/test_line_fixture.py": test_content}
    )
    cached_orphans = detect_orphan_invariants(fixture_ws, set(), eidos_dir=fixture_ws / "eidos")
    assert uncached_orphans == [("INV-LINE-ORPHAN-002", "tests/test_line_fixture.py::test_mixed_lines")]
    assert cached_orphans == [("INV-LINE-ORPHAN-002", "tests/test_line_fixture.py::test_mixed_lines")]


def test_class_qualified_test_binding_rejects_wrong_class_fallback(tmp_path: Path) -> None:
    """Verify B2: class-qualified test_binding (tests/test_cls.py::WrongClass::test_foo) is rejected when only
    RightClass::test_foo exists, while RightClass::test_foo and unqualified test_foo both match."""  # eidos:fixture
    from eidos.vcs.invariants import _verify_single_invariant, extract_invariants_and_verify

    repo = tmp_path / "cls_binding_repo"
    eidos_dir = repo / "eidos"
    pol_dir = eidos_dir / "policies"
    tests_dir = repo / "tests"
    pol_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    test_code = (
        "class RightClass:\n"
        "    def test_foo(self):\n"
        "        # INV-CLS-RIGHT\n"
        "        # INV-CLS-WRONG\n"
        "        # INV-CLS-UNQUAL\n"
        "        assert True\n"
    )
    (tests_dir / "test_cls.py").write_text(test_code, encoding="utf-8")

    (pol_dir / "pol-cls.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-CLS-001\n"
        "title: Class Binding Policy\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-CLS-RIGHT\n"
        "    rule: Matches RightClass::test_foo\n"
        "    test_binding: tests/test_cls.py::RightClass::test_foo\n"
        "  - id: INV-CLS-WRONG\n"
        "    rule: Must not match WrongClass::test_foo\n"
        "    test_binding: tests/test_cls.py::WrongClass::test_foo\n"
        "  - id: INV-CLS-UNQUAL\n"
        "    rule: Unqualified test_foo matches RightClass::test_foo\n"
        "    test_binding: tests/test_cls.py::test_foo\n"
        "---\n= Class Binding Policy\n",
        encoding="utf-8",
    )

    # 1. Cached path via extract_invariants_and_verify
    traces = {t.invariant_id: t for t in extract_invariants_and_verify(eidos_dir, repo)}
    assert traces["INV-CLS-RIGHT"].has_test_coverage is True
    assert traces["INV-CLS-WRONG"].has_test_coverage is False
    assert traces["INV-CLS-UNQUAL"].has_test_coverage is True

    # 2. Uncached string path via _verify_single_invariant
    raw_map: dict[str, object] = {"tests/test_cls.py": test_code}
    assert _verify_single_invariant("INV-CLS-RIGHT", "tests/test_cls.py::RightClass::test_foo", repo, raw_map, {}) != []
    assert _verify_single_invariant("INV-CLS-WRONG", "tests/test_cls.py::WrongClass::test_foo", repo, raw_map, {}) == []
    assert _verify_single_invariant("INV-CLS-UNQUAL", "tests/test_cls.py::test_foo", repo, raw_map, {}) != []


def test_group_c1_inline_component_1hop_and_promoted_component_parent_container(tmp_path: Path, monkeypatch) -> None:
    """Verify C1: 1-hop neighborhood includes capabilities/contracts bound to matching inline CMP-* and parent CNT-* of promoted CMP-*."""  # eidos:fixture
    cnt_rel = "eidos/architecture/containers/api.adoc"
    cnt_spec = """---
type: tech/container
id: CNT-API
title: API Container
source_path: src/api
components:
  - id: CMP-INLINE-PAY
    title: Inline Payments
    source_path: src/api/pay.py
---
= API Container

Original container prose.
"""
    cap_inline_rel = "eidos/domains/core/capabilities/cap-pay.adoc"
    cap_inline_old = """---
type: product/capability
id: CAP-PAY
title: Payment Capability
domain: DOM-CORE
status: active
business:
  jtbd: Process payments
technical:
  components:
    - CMP-INLINE-PAY
---
= Payment Capability

Original payment prose.
"""
    cap_inline_new = cap_inline_old + "\nUpdated payment retry handling in pay.py.\n"

    # 1. File owned by inline CMP-INLINE-PAY covered by 1-hop CAP-PAY bound to CMP-INLINE-PAY -> clean!
    ws1 = tmp_path / "c1_inline"
    env1 = _diff_aware_env(
        ws1,
        monkeypatch,
        changed=["src/api/pay.py", cap_inline_rel],
        versions={
            "src/api/pay.py": (
                "def pay(a: int) -> int:\n    return a\n",
                "def pay(a: int, b: int) -> int:\n    return a + b\n",
            ),
            cap_inline_rel: (cap_inline_old, cap_inline_new),
        },
        spec_text=cnt_spec,
        extra_files={
            cnt_rel: cnt_spec,
            cap_inline_rel: cap_inline_new,
            "src/api/pay.py": "def pay(a: int, b: int) -> int:\n    return a + b\n",
        },
    )
    res1 = detect_drift(repo_root=ws1, eidos_dir=env1)
    assert res1.drift_detected is False

    # 2. File owned by promoted CMP-PROMOTED-AUTH (source_path: src/api/auth/**) covered by parent CNT-API or CAP bound to CNT-API -> clean!
    cmp_auth_rel = "eidos/architecture/components/auth.adoc"
    cmp_auth_spec = """---
type: tech/component
id: CMP-PROMOTED-AUTH
title: Promoted Auth
container: CNT-API
source_path: src/api/auth/**
responsibility: Authentication
---
= Promoted Auth
"""
    cap_cnt_rel = "eidos/domains/core/capabilities/cap-api.adoc"
    cap_cnt_old = """---
type: product/capability
id: CAP-API
title: API Capability
domain: DOM-CORE
status: active
business:
  jtbd: Serve API
technical:
  containers:
    - CNT-API
---
= API Capability

Original API capability prose.
"""
    cap_cnt_new = cap_cnt_old + "\nUpdated login token validation in login.py.\n"

    ws2 = tmp_path / "c1_promoted_parent"
    env2 = _diff_aware_env(
        ws2,
        monkeypatch,
        changed=["src/api/auth/login.py", cap_cnt_rel],
        versions={
            "src/api/auth/login.py": (
                "def login(u: str) -> bool:\n    return True\n",
                "def login(u: str, mfa: str) -> bool:\n    return True\n",
            ),
            cap_cnt_rel: (cap_cnt_old, cap_cnt_new),
        },
        spec_text=cnt_spec,
        extra_files={
            cnt_rel: cnt_spec,
            cmp_auth_rel: cmp_auth_spec,
            cap_cnt_rel: cap_cnt_new,
            "src/api/auth/login.py": "def login(u: str, mfa: str) -> bool:\n    return True\n",
        },
    )
    res2 = detect_drift(repo_root=ws2, eidos_dir=env2)
    assert res2.drift_detected is False


def test_group_c2_fixture_docstrings_decorators_class_top_level_orphans_and_combined_invariants(tmp_path: Path) -> None:
    """Verify C2: docstring/block-comment eidos:fixture with dict decorators, top-level + class same-name orphan retention, and combined invariants."""  # eidos:fixture
    from eidos.vcs.invariants import detect_orphan_invariants, extract_invariants_and_verify

    repo = tmp_path / "c2_repo"
    eidos_dir = repo / "eidos"
    dom_dir = eidos_dir / "domains" / "core"
    cap_dir = dom_dir / "capabilities"
    cnt_dir = eidos_dir / "architecture" / "containers"
    tests_dir = repo / "tests"
    for d in (dom_dir, cap_dir, cnt_dir, tests_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. File with docstring-only eidos:fixture and a @decorator({"k": 1}) containing ':' and '{'
    test_doc_fix = (
        "import pytest\n\n"
        "@pytest.mark.parametrize(\n"
        "    'cfg',\n"
        "    [{'mode': 'strict'}],\n"
        ")\n"
        "def test_decorated_fixture(cfg: dict) -> None:\n"
        '    """Synthetic test fixture (eidos:fixture)."""\n'
        "    fake = 'INV-DOCSTRING-IGNORED-001'\n"
        "    assert fake and cfg\n\n"
        "def test_real_orphan() -> None:\n"
        "    x = 'INV-DOCSTRING-REAL-ORPHAN-002'\n"
        "    assert x\n"
    )
    (tests_dir / "test_doc_fixture.py").write_text(test_doc_fix, encoding="utf-8")

    # 2. File with both top-level test_same() and class TestSuite.test_same()
    test_collision = (
        "def test_same():\n"
        "    # INV-TOP-LEVEL-ORPHAN\n"
        "    assert True\n\n"
        "class TestSuite:\n"
        "    def test_same(self):\n"
        "        # INV-CLASS-LEVEL-ORPHAN\n"
        "        assert True\n"
    )
    (tests_dir / "test_collision.py").write_text(test_collision, encoding="utf-8")

    # Check both cached and uncached orphan detection
    cached_orphans = detect_orphan_invariants(repo, set(), eidos_dir=eidos_dir)
    uncached_orphans = detect_orphan_invariants(
        repo,
        set(),
        all_tests_content={
            "tests/test_doc_fixture.py": test_doc_fix,
            "tests/test_collision.py": test_collision,
        },
    )
    for orphan_list in (cached_orphans, uncached_orphans):
        orphan_ids = {oid for oid, _ in orphan_list}
        assert "INV-DOCSTRING-IGNORED-001" not in orphan_ids
        assert "INV-DOCSTRING-REAL-ORPHAN-002" in orphan_ids
        assert ("INV-TOP-LEVEL-ORPHAN", "tests/test_collision.py::test_same") in orphan_list
        assert ("INV-CLASS-LEVEL-ORPHAN", "tests/test_collision.py::TestSuite::test_same") in orphan_list
        assert ("INV-CLASS-LEVEL-ORPHAN", "tests/test_collision.py::test_same") not in orphan_list

    # 3. Domain and Container with BOTH top-level invariants and technical.invariants
    (dom_dir / "domain.adoc").write_text(
        """---
type: product/domain
id: DOM-CORE
title: Core Domain
invariants:
  - id: INV-DOM-TOP
    rule: Domain top invariant
    test_binding: tests/test_invs.py::test_all_invs
technical:
  invariants:
    - id: INV-DOM-TECH
      rule: Domain technical invariant
      test_binding: tests/test_invs.py::test_all_invs
---
= Core Domain
""",
        encoding="utf-8",
    )
    (cnt_dir / "cnt.adoc").write_text(
        """---
type: tech/container
id: CNT-BOTH
title: Both Container
source_path: src
invariants:
  - id: INV-CNT-TOP
    rule: Container top invariant
    test_binding: tests/test_invs.py::test_all_invs
technical:
  invariants:
    - id: INV-CNT-TECH
      rule: Container technical invariant
      test_binding: tests/test_invs.py::test_all_invs
---
= Both Container
""",
        encoding="utf-8",
    )
    (cap_dir / "cap.adoc").write_text(
        """---
type: product/capability
id: CAP-BOTH
title: Both Capability
domain: DOM-CORE
invariants:
  - id: INV-CAP-TOP
    rule: Capability top invariant
    test_binding: tests/test_invs.py::test_all_invs
technical:
  invariants:
    - id: INV-CAP-TECH
      rule: Capability technical invariant
      test_binding: tests/test_invs.py::test_all_invs
---
= Both Capability
""",
        encoding="utf-8",
    )
    (tests_dir / "test_invs.py").write_text(
        "def test_all_invs():\n"
        "    # INV-DOM-TOP INV-DOM-TECH INV-CNT-TOP INV-CNT-TECH INV-CAP-TOP INV-CAP-TECH\n"
        "    assert True\n",
        encoding="utf-8",
    )

    traces = {t.invariant_id: t for t in extract_invariants_and_verify(eidos_dir, repo)}
    for expected_id in ("INV-DOM-TOP", "INV-DOM-TECH", "INV-CNT-TOP", "INV-CNT-TECH", "INV-CAP-TOP", "INV-CAP-TECH"):
        assert expected_id in traces
        assert traces[expected_id].has_test_coverage is True


def test_group_c3_mode_only_noop_diff_and_delta_file_path_targets(tmp_path: Path, monkeypatch) -> None:
    """Verify C3: identical old/new content in real/versioned diff is not treated as structural/design drift, and file-path delta targets resolve."""  # eidos:fixture
    # 1. Identical old_c == new_c (e.g. git chmod mode-only change) on .py and .css -> zero drift!
    ws_noop = tmp_path / "c3_noop"
    env_noop = _diff_aware_env(
        ws_noop,
        monkeypatch,
        changed=["src/pkg/svc.py", "src/pkg/theme.css"],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_OLD),
            "src/pkg/theme.css": (":root { --c: #000; }\n", ":root { --c: #000; }\n"),
        },
        extra_files={"src/pkg/theme.css": ":root { --c: #000; }\n"},
    )
    res_noop = detect_drift(repo_root=ws_noop, eidos_dir=env_noop)
    assert res_noop.drift_detected is False
    assert "src/pkg/svc.py" in res_noop.body_only_code_files
    assert res_noop.uncovered_design_files == []

    # 2. Active SpecDelta whose mutation target is a .adoc path (eidos/architecture/containers/x.adoc)
    #    and only exists in head_ref (_fetch_versions) satisfies Planned Mode!
    dlt_rel = "eidos/deltas/dlt-2026-090-path.adoc"
    dlt_content = """---
type: delta/mutation
id: DLT-2026-090
title: Path Target Delta
status: proposed
intent:
  kind: feature
  motivation: Support path target in mutations
mutations:
  - operation: modify
    target: eidos/architecture/containers/x.adoc
---
= Path Target Delta
"""
    ws_dlt = tmp_path / "c3_delta_path"
    env_dlt = _diff_aware_env(
        ws_dlt,
        monkeypatch,
        changed=["src/pkg/svc.py", dlt_rel],
        versions={
            "src/pkg/svc.py": (_CODE_OLD, _CODE_WITH_CLASS),
            dlt_rel: (None, dlt_content),
        },
    )
    res_dlt = detect_drift(repo_root=ws_dlt, eidos_dir=env_dlt, uncommitted=False)
    assert res_dlt.workflow_mode == "planned"
    assert res_dlt.drift_detected is False


def test_group_c4_incremental_sync_design_eviction_ownership_component_ref_and_consumes_api(tmp_path: Path) -> None:
    """Verify C4: incremental sync evicts stale styles_container/inferred_path and preserves consumes_api edges."""  # eidos:fixture
    import os
    import time

    from eidos.core.index.graph_index import ArchitecturalIndex

    repo = tmp_path / "c4_repo"
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cmp_dir = eidos_dir / "architecture" / "components"
    ds_dir = eidos_dir / "design" / "systems"
    clt_dir = eidos_dir / "design" / "clients"
    old_pkg = repo / "src" / "old_pkg"
    new_pkg = repo / "src" / "new_pkg"
    for d in (cnt_dir, cmp_dir, ds_dir / "base", ds_dir / "brand", clt_dir, old_pkg, new_pkg):
        d.mkdir(parents=True, exist_ok=True)

    (old_pkg / "old_mod.py").write_text("def old_fn():\n    return 1\n", encoding="utf-8")
    (new_pkg / "new_mod.py").write_text(
        "from old_pkg.old_mod import old_fn\n\ndef new_fn():\n    return old_fn()\n",
        encoding="utf-8",
    )
    (new_pkg / "web_mod.py").write_text("def web_fn():\n    return 3\n", encoding="utf-8")

    cnt_file = cnt_dir / "web.adoc"
    cnt_file.write_text(
        """---
type: tech/container
id: CNT-WEB
title: Web Container
source_path: src/old_pkg
components:
  - id: CMP-OLD
    title: Old Module
    responsibility: Legacy module.
    source_path: src/old_pkg/old_mod.py
---
= Web Container
""",
        encoding="utf-8",
    )
    cmp_file = cmp_dir / "worker.adoc"
    cmp_file.write_text(
        """---
type: tech/component
id: CMP-WORKER
title: Worker
container: CNT-WEB
source_path: src/new_pkg/new_mod.py
---
= Worker
""",
        encoding="utf-8",
    )
    ds_base = ds_dir / "base" / "system.adoc"
    ds_base.write_text(
        """---
type: design/system
id: DS-BASE
title: Base Design System
status: active
---
= Base Design System
""",
        encoding="utf-8",
    )
    ds_brand = ds_dir / "brand" / "system.adoc"
    ds_brand.write_text(
        """---
type: design/system
id: DS-BRAND
title: Brand Design System
status: active
extends: DS-BASE
---
= Brand Design System
""",
        encoding="utf-8",
    )
    clt_file = clt_dir / "web.adoc"
    clt_file.write_text(
        """---
type: design/client
id: CLT-WEB
title: Web Client
platform: web-spa
container: CNT-WEB
design_system: DS-BRAND
---
= Web Client
""",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        # 1. AST-derived consumes_api edge between intra-container components is recorded
        consumes_edges = conn.execute(
            "SELECT source_id, target_id FROM edges WHERE rel_type = 'consumes_api';"
        ).fetchall()
        assert ("CMP-WORKER", "CMP-OLD") in [(r[0], r[1]) for r in consumes_edges]

        # 2. Initially DS-BASE styles CNT-WEB indirectly via DS-BRAND inheritance
        styles_cnt = {
            r[0]
            for r in conn.execute(
                "SELECT source_id FROM edges WHERE rel_type = 'styles_container' AND target_id = 'CNT-WEB';"
            ).fetchall()
        }
        assert styles_cnt == {"DS-BRAND", "DS-BASE"}

        # 3. Incrementally update DS-BRAND (drop extends: DS-BASE) and CNT-WEB (change source_path to src/new_pkg)
        ds_brand.write_text(
            """---
type: design/system
id: DS-BRAND
title: Brand Design System
status: active
---
= Brand Design System
""",
            encoding="utf-8",
        )
        cnt_file.write_text(
            """---
type: tech/container
id: CNT-WEB
title: Web Container
source_path: src/new_pkg
---
= Web Container
""",
            encoding="utf-8",
        )
        bump = time.time() + 10.0
        os.utime(ds_brand, (bump, bump))
        os.utime(cnt_file, (bump, bump))

        idx.sync(force=False)

        # DS-BASE must no longer have a stale styles_container or styled_by edge to CNT-WEB
        styles_cnt_after = {
            r[0]
            for r in conn.execute(
                "SELECT source_id FROM edges WHERE rel_type = 'styles_container' AND target_id = 'CNT-WEB';"
            ).fetchall()
        }
        assert styles_cnt_after == {"DS-BRAND"}

        # Stale src/old_pkg must not remain in source_path or inferred_path; src/new_pkg must be present
        cnt_sources = {
            r[0]
            for r in conn.execute(
                "SELECT symbol FROM symbol_mappings WHERE entity_id = 'CNT-WEB' AND kind IN ('package_path', 'source_path');"
            ).fetchall()
        }
        assert "src/old_pkg" not in cnt_sources
        assert "src/old_pkg/old_mod.py" not in cnt_sources
        assert "src/new_pkg" in cnt_sources
        assert "src/new_pkg/web_mod.py" in cnt_sources

        inferred = {
            r[0]
            for r in conn.execute(
                "SELECT symbol FROM symbol_mappings WHERE entity_id = 'CLT-WEB' AND kind = 'inferred_path';"
            ).fetchall()
        }
        assert "src/old_pkg" not in inferred
        assert "src/old_pkg/old_mod.py" not in inferred
        assert "src/new_pkg" in inferred
        assert "src/new_pkg/new_mod.py" in inferred
    finally:
        idx.close()


def test_deep_audit_group_c_c1_through_c10(tmp_path: Path) -> None:
    """Verify deep audit Group C fixes (C1 through C10)."""  # eidos:fixture
    import os
    import time

    from eidos.core.index.graph_index import ArchitecturalIndex
    from eidos.core.spec.models import FrontmatterModel
    from eidos.vcs.diff import _discover_container_package_paths
    from eidos.vcs.invariants import (
        _INV_OR_POL_TOKEN_RE,
        _has_function_level_fixture,
        _verify_single_invariant,
        extract_invariants_and_verify,
    )
    from eidos.vcs.structural import _SpecDiffInspector

    # --- C2: _has_function_level_fixture handles Rust #[test], standalone # comments before @decorator, and multi-line /* ... */ ---
    rust_lines = [
        "#[test]",
        "fn test_rust_fixture() {",
        "    /* multi-line",
        "       eidos:fixture",
        "    */",
        '    let x = "INV-RUST-001";',
        "}",
    ]
    assert _has_function_level_fixture("\n".join(rust_lines), _INV_OR_POL_TOKEN_RE) is True

    py_comment_dec_lines = [
        "# Leading comment before decorator",
        "@pytest.mark.parametrize('x', [1])",
        "def test_py_fixture(x):",
        '    """Docstring with eidos:fixture."""',
        "    pass",
    ]
    assert _has_function_level_fixture("\n".join(py_comment_dec_lines), _INV_OR_POL_TOKEN_RE) is True

    # --- C6: _discover_container_package_paths handles inline list, YAML block list, and empty key ---
    cnt_dir_c6 = tmp_path / "c6_eidos" / "architecture" / "containers"
    cnt_dir_c6.mkdir(parents=True)
    (cnt_dir_c6 / "inline_list.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-1\nsource_path: [\"src/a\", 'src/b/']\n---\n",
        encoding="utf-8",
    )
    (cnt_dir_c6 / "block_list.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-2\nsource_path:\n  - src/c\n  - src/d  # comment\nstatus: active\n---\n",
        encoding="utf-8",
    )
    (cnt_dir_c6 / "empty_key.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-3\nsource_path:\nstatus: active\n---\n",
        encoding="utf-8",
    )
    discovered_pkgs = _discover_container_package_paths(tmp_path / "c6_eidos")
    assert set(discovered_pkgs) == {"src/a", "src/b", "src/c", "src/d"}

    # --- C7: _SpecDiffInspector synthetic_diff_mock=False vs True when old == new ---
    spec_txt = "---\ntype: tech/container\nid: CNT-X\n---\n= X\n"
    insp_real = _SpecDiffInspector(
        tmp_path,
        None,
        "HEAD",
        False,
        versions_fn=lambda _p: (spec_txt, spec_txt),
        synthetic_diff_mock=False,
    )
    assert insp_real.is_substantive("eidos/architecture/containers/x.adoc") is False

    insp_mock = _SpecDiffInspector(
        tmp_path,
        None,
        "HEAD",
        False,
        versions_fn=lambda _p: (spec_txt, spec_txt),
        synthetic_diff_mock=True,
    )
    assert insp_mock.is_substantive("eidos/architecture/containers/x.adoc") is True

    # --- C8: _verify_single_invariant with "::::" and extract_invariants_and_verify skipping README.adoc & _partial.adoc ---
    assert _verify_single_invariant("INV-1", "::::", tmp_path, {}, {}) == []

    # --- C10: FrontmatterModel.source_paths deduplicates source_path entries ---
    fm_c10 = FrontmatterModel.model_validate(
        {"id": "CNT-C10", "type": "tech/container", "source_path": ["src/pkg", "src/pkg", "src/alt"]}
    )
    assert fm_c10.source_paths() == ["src/pkg", "src/alt"]

    # --- C3, C4, C5, C8, C9, C10: ArchitecturalIndex sync, domain invariants, duplicate ID protection, file_path in context/query, symbol_mappings test_binding ---
    repo = tmp_path / "c_index_repo"
    eidos_dir = repo / "eidos"
    dom_dir = eidos_dir / "domains" / "billing"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cmp_dir = eidos_dir / "architecture" / "components"
    tests_dir = repo / "tests"
    src_owned = repo / "src" / "owned_mod"
    for d in (dom_dir, cnt_dir, cmp_dir, tests_dir, src_owned):
        d.mkdir(parents=True, exist_ok=True)

    (src_owned / "handler.py").write_text("def handle():\n    return True\n", encoding="utf-8")
    (tests_dir / "test_billing.py").write_text(
        "def test_dom_inv():\n    # INV-DOM-BILL-001\n    assert True\n",
        encoding="utf-8",
    )

    # C8: README.adoc and _partial.adoc with fake invariants should be ignored by extract_invariants_and_verify
    (eidos_dir / "README.adoc").write_text(
        "---\ntype: product/policy\nid: POL-README\ninvariants:\n  - id: INV-README-IGNORE\n    rule: Ignored\n---\n",
        encoding="utf-8",
    )
    (eidos_dir / "_partial.adoc").write_text(
        "---\ntype: product/policy\nid: POL-PARTIAL\ninvariants:\n  - id: INV-PARTIAL-IGNORE\n    rule: Ignored\n---\n",
        encoding="utf-8",
    )

    # C3: Domain with invariant
    (dom_dir / "domain.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "id: DOM-BILLING\n"
        "title: Billing Domain\n"
        "invariants:\n"
        "  - id: INV-DOM-BILL-001\n"
        "    rule: All invoices must balance.\n"
        "    test_binding: tests/test_billing.py::test_dom_inv\n"
        "---\n= Billing Domain\n",
        encoding="utf-8",
    )

    # C4: Canonical container CNT-BILL + colliding duplicate file
    cnt_canon = cnt_dir / "01_billing.adoc"
    cnt_dup = cnt_dir / "02_billing_dup.adoc"
    cnt_canon.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-BILL\n"
        "title: Canonical Billing\n"
        "source_path: src/owned_mod\n"
        "---\n= Canonical Billing\n",
        encoding="utf-8",
    )
    cnt_dup.write_text(
        "---\ntype: tech/container\nid: CNT-BILL\ntitle: Duplicate Billing\n---\n= Duplicate Billing\n",
        encoding="utf-8",
    )

    # C10: Component with non-glob directory source_path
    cmp_file = cmp_dir / "bill_mod.adoc"
    cmp_file.write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-BILL-MOD\n"
        "title: Billing Module\n"
        "container: CNT-BILL\n"
        "source_path: src/owned_mod\n"
        "responsibility: Handle billing\n"
        "---\n= Billing Module\n",
        encoding="utf-8",
    )

    invs_extracted = {t.invariant_id for t in extract_invariants_and_verify(eidos_dir, repo)}
    assert "INV-DOM-BILL-001" in invs_extracted
    assert "INV-README-IGNORE" not in invs_extracted
    assert "INV-PARTIAL-IGNORE" not in invs_extracted

    idx = ArchitecturalIndex(repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        # C3: INV-DOM-BILL-001 indexed in entities table with source_id == DOM-BILLING and has_test_coverage
        import json

        inv_ent = conn.execute("SELECT type, data_json FROM entities WHERE id = 'INV-DOM-BILL-001';").fetchone()
        assert inv_ent is not None and inv_ent[0] == "invariant"
        inv_meta = json.loads(inv_ent[1])
        assert inv_meta["source_id"] == "DOM-BILLING" and inv_meta["has_test_coverage"] is True

        # C9: test_binding row present in symbol_mappings
        tb_rows = conn.execute(
            "SELECT symbol, entity_id FROM symbol_mappings WHERE kind = 'test_binding' AND entity_id = 'INV-DOM-BILL-001';"
        ).fetchall()
        assert ("tests/test_billing.py", "INV-DOM-BILL-001") in [(r[0], r[1]) for r in tb_rows]

        # C4: Delete colliding duplicate file and run incremental sync -> canonical CNT-BILL must remain intact!
        cnt_dup.unlink()
        idx.sync(force=False)
        cnt_row = conn.execute("SELECT title, file_path FROM entities WHERE id = 'CNT-BILL';").fetchone()
        assert cnt_row is not None and cnt_row[0] == "Canonical Billing"

        # C5: query_fts and get_tiered_context_slice include file_path
        q_res = idx.query_fts("Billing")
        assert any(r.get("id") == "CNT-BILL" and r.get("file_path") for r in q_res)
        ctx_guard = idx.get_tiered_context_slice(component="CNT-BILL", tier="guardrails")
        assert any(c.get("id") == "CNT-BILL" and c.get("file_path") for c in ctx_guard.components)
        ctx_deep = idx.get_tiered_context_slice(component="CNT-BILL", tier="deep")
        assert any(c.get("id") == "CNT-BILL" and c.get("file_path") for c in ctx_deep.components)

        # C10: Creating a new file under non-glob owned_path marks index dirty and maps to CMP-BILL-MOD
        new_src = src_owned / "sub_handler.py"
        new_src.write_text("def sub():\n    return 1\n", encoding="utf-8")
        bump = time.time() + 10.0
        os.utime(new_src, (bump, bump))
        assert idx.is_dirty()[0] is True
        idx.sync(force=False)
        mapped = conn.execute(
            "SELECT entity_id FROM symbol_mappings WHERE symbol = 'src/owned_mod/sub_handler.py' AND kind = 'source_path';"
        ).fetchall()
        assert "CMP-BILL-MOD" in {r[0] for r in mapped}
    finally:
        idx.close()
