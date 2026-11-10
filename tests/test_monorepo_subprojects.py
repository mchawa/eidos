"""End-to-end and unit tests for independent sub-project eidos/ directories in monorepos.

Verifies INV-MONOREPO-SUBPROJECT-ISOLATION and DEC-CORE-MONOREPO-ISOLATED-SUBPROJECTS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from eidos.cli._shared import has_eidos_markers, resolve_cli_eidos_dir
from eidos.cli.main import app
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.index.semantic import resolve_semantic_model_dir
from eidos.core.index.sync import check_index_dirty, sync_index
from eidos.core.lifecycle.delta_engine import apply_delta, create_delta, verify_delta_mutations
from eidos.core.lifecycle.initializer import _ensure_eidos_gitignore, init_repository
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import (
    discover_nested_subproject_roots,
    find_git_root,
    find_repo_root,
    get_tracked_files,
    is_generated_or_vendored,
    resolve_spec_and_repo_roots,
)
from eidos.plugin.hooks import execute_hook, handle_stop_hook
from eidos.plugin.installer import PluginInstallTarget, install_plugin, uninstall_plugin
from eidos.plugin.version import check_plugin_version_status
from eidos.vcs.diff import batch_read_git_blobs, get_git_diff_files
from eidos.vcs.drift import detect_drift

runner = CliRunner()


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _init_git_monorepo(git_root: Path) -> None:
    git_root.mkdir(parents=True, exist_ok=True)
    _git(git_root, "init", "-b", "main")
    _git(git_root, "config", "user.email", "test@example.com")
    _git(git_root, "config", "user.name", "Test User")


def _scaffold_subproject(subproject_root: Path, pkg_slug: str, pkg_upper: str) -> Path:
    """Create a complete, schema-valid self-contained sub-project with source code, specs, and tests."""
    ok, msg, _ = init_repository(subproject_root, title=f"{pkg_slug.title()} Service")
    assert ok, msg
    eidos_dir = subproject_root / "eidos"

    src_dir = subproject_root / "src" / pkg_slug
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "service.py").write_text(
        f'def run_{pkg_slug}(value: int) -> int:\n    """Run {pkg_slug} logic."""\n    return value + 1\n',
        encoding="utf-8",
    )

    tests_dir = subproject_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    inv_id = f"INV-{pkg_upper}-CORE"
    (tests_dir / f"test_{pkg_slug}.py").write_text(
        f'from src.{pkg_slug}.service import run_{pkg_slug}\n\n\n'
        f'def test_{pkg_slug}_core():\n'
        f'    """Verifies {inv_id}."""\n'
        f'    assert run_{pkg_slug}(1) == 2\n',
        encoding="utf-8",
    )

    dom_id = f"DOM-{pkg_upper}"
    cap_id = f"CAP-{pkg_upper}-001"
    cnt_id = f"CNT-{pkg_upper}"
    cmp_id = f"CMP-{pkg_upper}-SERVICE"

    dom_dir = eidos_dir / "domains" / pkg_slug
    (dom_dir / "capabilities").mkdir(parents=True, exist_ok=True)
    (dom_dir / "domain.adoc").write_text(
        f"---\ntype: product/domain\nid: {dom_id}\ntitle: {pkg_slug.title()} Domain\n"
        f"status: active\nclassification: core\n---\n"
        f"= Domain: {pkg_slug.title()}\n\nCore domain for {pkg_slug}.\n",
        encoding="utf-8",
    )
    (dom_dir / "capabilities" / "core.adoc").write_text(
        f"---\ntype: product/capability\nid: {cap_id}\ntitle: {pkg_slug.title()} Core Capability\n"
        f"domain: {dom_id}\nstatus: active\nversion: \"1.0.0\"\n"
        f"technical:\n  containers:\n    - {cnt_id}\n  components:\n    - {cmp_id}\n"
        f"  invariants:\n    - id: {inv_id}\n"
        f"      rule: \"{pkg_slug} service must increment input deterministically.\"\n"
        f"      test_binding: \"tests/test_{pkg_slug}.py::test_{pkg_slug}_core\"\n---\n"
        f"= Capability: {pkg_slug.title()} Core Capability\n\nProvides core {pkg_slug} operations.\n",
        encoding="utf-8",
    )

    (eidos_dir / "architecture" / "containers" / f"{pkg_slug}.adoc").write_text(
        f"---\ntype: tech/container\nid: {cnt_id}\ntitle: {pkg_slug.title()} Container\nstatus: active\n"
        f"container_type: service\nruntime: Python 3.12\nsource_path:\n  - src/{pkg_slug}\n"
        f"domain: {dom_id}\ntier: core\n---\n"
        f"= Container: {pkg_slug.title()}\n\nService container for {pkg_slug}.\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / f"{pkg_slug}-service.adoc").write_text(
        f"---\ntype: tech/component\nid: {cmp_id}\ntitle: {pkg_slug.title()}Service\n"
        f"container: {cnt_id}\nstatus: active\nsource_path: src/{pkg_slug}/service.py\n---\n"
        f"= Component: {pkg_slug.title()}Service\n\n== Responsibility\nImplements `run_{pkg_slug}`.\n\n"
        f"== Public Surface\n* `run_{pkg_slug}(value: int) -> int`\n",
        encoding="utf-8",
    )
    return eidos_dir


def test_monorepo_subproject_eidos_isolation(tmp_path: Path, monkeypatch) -> None:
    """Verifies INV-MONOREPO-SUBPROJECT-ISOLATION across sibling sub-projects in a shared Git monorepo."""
    git_root = tmp_path / "monorepo"
    _init_git_monorepo(git_root)

    api_root = git_root / "packages" / "api"
    billing_root = git_root / "packages" / "billing"

    api_eidos = _scaffold_subproject(api_root, "api", "API")
    billing_eidos = _scaffold_subproject(billing_root, "billing", "BILLING")

    # Add a root README and .gitattributes for linguist-generated check in sub-project
    (git_root / "README.md").write_text("# Monorepo\n", encoding="utf-8")
    (billing_root / ".gitattributes").write_text("src/billing/generated.py linguist-generated=true\n", encoding="utf-8")
    (billing_root / "src" / "billing" / "generated.py").write_text("GEN = 1\n", encoding="utf-8")

    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Initial monorepo with api and billing sub-projects")

    # 1. Verify root & git resolution from nested directories (even with nested package.json marker)
    (api_root / "src" / "api" / "package.json").write_text('{"name": "nested-api"}\n', encoding="utf-8")
    assert find_git_root(api_root / "src" / "api") == git_root.resolve()
    assert find_git_root(billing_eidos) == git_root.resolve()
    assert find_repo_root(api_root / "src" / "api") == api_root.resolve()
    assert find_repo_root(billing_root / "tests") == billing_root.resolve()
    assert resolve_spec_and_repo_roots(api_eidos) == (api_eidos.resolve(), api_root.resolve())
    assert resolve_spec_and_repo_roots(billing_root) == (billing_eidos.resolve(), billing_root.resolve())
    assert resolve_spec_and_repo_roots(api_root / "src" / "api") == (api_eidos.resolve(), api_root.resolve())
    assert resolve_cli_eidos_dir(api_root) == api_eidos.resolve()
    assert resolve_cli_eidos_dir(api_root / "src" / "api") == api_eidos.resolve()
    (api_root / "src" / "api" / "package.json").unlink()

    # 2. Verify tracked files and .gitattributes work inside sub-project without local .git
    api_tracked = get_tracked_files(api_root)
    assert "src/api/service.py" in api_tracked
    assert not any(p.startswith("packages/") for p in api_tracked)
    assert is_generated_or_vendored(billing_root, ["src/billing/generated.py", "src/billing/service.py"]) == {
        "src/billing/generated.py"
    }

    # 3. Validate each sub-project independently (strictly sub-project-relative paths)
    api_report = SpecValidator().validate_directory(api_eidos)
    assert api_report.is_clean, (api_report.schema_errors, api_report.broken_references)
    billing_report = SpecValidator().validate_directory(billing_eidos)
    assert billing_report.is_clean, (billing_report.schema_errors, billing_report.broken_references)

    # 4. Sync each sub-project's SQLite index independently
    api_idx = get_architectural_index(api_eidos)
    sync_index(api_idx, force=True)
    assert not check_index_dirty(api_idx)[0]
    api_idx.close()

    billing_idx = get_architectural_index(billing_eidos)
    sync_index(billing_idx, force=True)
    assert not check_index_dirty(billing_idx)[0]
    billing_idx.close()

    # Both sub-projects are clean before edits
    api_clean = detect_drift(api_root, uncommitted=True, eidos_dir=api_eidos)
    assert api_clean.is_clean and not api_clean.drift_detected
    billing_clean = detect_drift(billing_root, uncommitted=True, eidos_dir=billing_eidos)
    assert billing_clean.is_clean and not billing_clean.drift_detected

    # 5. Modify ONLY billing code (uncommitted structural change) -> api MUST remain 100% clean!
    (billing_root / "src" / "billing" / "service.py").write_text(
        'def run_billing(value: int) -> int:\n    """Run billing logic."""\n    return value + 1\n\n\n'
        'def refund_billing(amount: int) -> int:\n    return amount\n',
        encoding="utf-8",
    )

    api_diff = get_git_diff_files(api_root, uncommitted=True)
    assert api_diff == []

    billing_diff = get_git_diff_files(billing_root, uncommitted=True)
    assert billing_diff == ["src/billing/service.py"]

    # Verify batch_read_git_blobs resolves sub-project-relative paths from Git HEAD
    blobs = batch_read_git_blobs(billing_root, [("HEAD", "src/billing/service.py")])
    assert blobs[("HEAD", "src/billing/service.py")] is not None
    assert "def run_billing" in (blobs[("HEAD", "src/billing/service.py")] or "")

    # API has zero drift bleed from billing's structural code edit
    api_after_billing_edit = detect_drift(api_root, uncommitted=True, eidos_dir=api_eidos)
    assert api_after_billing_edit.is_clean and not api_after_billing_edit.drift_detected

    # Billing detects structural drift on CMP-BILLING-SERVICE
    billing_drift = detect_drift(billing_root, uncommitted=True, eidos_dir=billing_eidos)
    assert billing_drift.drift_detected
    assert "src/billing/service.py" in billing_drift.structural_code_files
    assert "CMP-BILLING-SERVICE" in billing_drift.touched_components

    # Co-evolve billing component spec -> billing becomes clean!
    (billing_eidos / "architecture" / "components" / "billing-service.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-BILLING-SERVICE\ntitle: BillingService\n"
        "container: CNT-BILLING\nstatus: active\nsource_path: src/billing/service.py\n---\n"
        "= Component: BillingService\n\n== Responsibility\nImplements `run_billing` and `refund_billing`.\n\n"
        "== Public Surface\n* `run_billing(value: int) -> int`\n* `refund_billing(amount: int) -> int`\n",
        encoding="utf-8",
    )
    billing_reconciled = detect_drift(billing_root, uncommitted=True, eidos_dir=billing_eidos)
    assert billing_reconciled.is_clean and not billing_reconciled.drift_detected

    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Add refund_billing to billing service")

    # 6. Verify SpecDelta creation, baseline lookup, and verification in nested sub-project
    delta_path = create_delta(billing_eidos, "DLT-2026-001", "Add Refund Audit Policy", kind="feature")
    delta_text = delta_path.read_text(encoding="utf-8")
    assert "branch: main" in delta_text
    assert "base_commit:" in delta_text

    # Author a modify mutation on CMP-BILLING-SERVICE and verify against Git baseline
    delta_path.write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-001-ADD-REFUND-0001\ntitle: Add Refund Note\n"
        "status: proposed\nintent:\n  kind: feature\n  motivation: Document refund note.\n"
        "mutations:\n  - operation: modify\n    target: CMP-BILLING-SERVICE\n"
        "    details: Document deterministic refund note.\n"
        "    expect:\n      body:\n        contains:\n          - \"Deterministic refund\"\n"
        "vcs:\n  branch: main\n---\n= SpecDelta: Add Refund Note\n\nDetails.\n",
        encoding="utf-8",
    )
    (billing_eidos / "architecture" / "components" / "billing-service.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-BILLING-SERVICE\ntitle: BillingService\n"
        "container: CNT-BILLING\nstatus: active\nsource_path: src/billing/service.py\n---\n"
        "= Component: BillingService\n\n== Responsibility\nImplements `run_billing` and `refund_billing`. Deterministic refund.\n\n"
        "== Public Surface\n* `run_billing(value: int) -> int`\n* `refund_billing(amount: int) -> int`\n",
        encoding="utf-8",
    )
    verified, verify_msg, _ = verify_delta_mutations(billing_eidos, "DLT-2026-001")
    assert verified, verify_msg
    applied, apply_msg, _ = apply_delta(billing_eidos, "DLT-2026-001")
    assert applied, apply_msg

    # 7. Shared Git-root semantic model cache resolution & .gitignore hygiene
    expected_shared_model_dir = (git_root / "eidos" / ".cache" / "models" / "harrier-oss-v1-0.6b").resolve()
    assert resolve_semantic_model_dir(api_eidos) == expected_shared_model_dir
    assert resolve_semantic_model_dir(billing_eidos) == expected_shared_model_dir

    # Provision simulated shared cache at <git_root>/eidos/.cache and ensure .gitignore is written
    expected_shared_model_dir.mkdir(parents=True, exist_ok=True)
    _ensure_eidos_gitignore(git_root / "eidos")
    (git_root / "eidos" / ".cache" / ".gitignore").write_text("*\n", encoding="utf-8")
    assert (git_root / "eidos" / ".gitignore").is_file()
    assert ".cache/" in (git_root / "eidos" / ".gitignore").read_text(encoding="utf-8")

    # Cache-only <git_root>/eidos must NOT be treated as an initialized spec root
    assert not has_eidos_markers(git_root / "eidos")
    assert find_repo_root(api_root / "src") == api_root.resolve()

    # 8. Shared Git-root Agent Plugin & AGENTS.md placement when invoked from a sub-project
    install_results = install_plugin(PluginInstallTarget.ANTIGRAVITY, project_dir=api_root)
    assert all(r.success for r in install_results)
    assert (git_root / ".agents" / "plugins" / "eidos").is_dir()
    assert (git_root / "AGENTS.md").is_file()
    assert not (api_root / ".agents").exists()
    assert not (api_root / "AGENTS.md").exists()

    plugin_status = check_plugin_version_status(project_dir=billing_root)
    assert plugin_status["status"] == "up_to_date"

    uninstall_results = uninstall_plugin(PluginInstallTarget.ANTIGRAVITY, project_dir=billing_root)
    assert all(r.success for r in uninstall_results)
    assert not (git_root / ".agents" / "plugins" / "eidos" / ".eidos-manifest.json").exists()

    # 9. Initializing a root eidos/ project at <git_root> later succeeds without --force despite .cache & .gitignore
    root_init_ok, root_init_msg, _ = init_repository(git_root, title="Monorepo Root Architecture")
    assert root_init_ok, root_init_msg
    assert has_eidos_markers(git_root / "eidos")

    # 10. Parent project excludes nested child sub-projects (api and billing)
    nested = discover_nested_subproject_roots(git_root)
    assert api_root.resolve() in nested
    assert billing_root.resolve() in nested
    root_tracked = get_tracked_files(git_root)
    assert not any(p.startswith("packages/api/") for p in root_tracked)
    assert not any(p.startswith("packages/billing/") for p in root_tracked)


def test_cli_and_hook_discovery_in_monorepo_subproject(tmp_path: Path, monkeypatch) -> None:
    """Verify CLI subcommands and Stop hook resolve the nearest sub-project eidos/ directory."""
    git_root = tmp_path / "monorepo"
    _init_git_monorepo(git_root)

    api_root = git_root / "packages" / "api"
    api_eidos = _scaffold_subproject(api_root, "api", "API")
    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Initial api sub-project")

    # From nested subdirectory inside packages/api/src/api
    monkeypatch.chdir(api_root / "src" / "api")
    assert resolve_cli_eidos_dir(None) == api_eidos.resolve()

    res_list = runner.invoke(app, ["spec", "list"])
    assert res_list.exit_code == 0
    assert "CMP-API-SERVICE" in res_list.stdout

    res_sync = runner.invoke(app, ["index", "sync"])
    assert res_sync.exit_code == 0

    res_drift = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert res_drift.exit_code == 0, res_drift.stdout
    assert '"is_clean":true' in res_drift.stdout.replace(" ", "")

    # Hook execution from inside sub-project
    assert execute_hook(api_root / "src" / "api") == 0
    hook_res = handle_stop_hook({}, api_root / "src" / "api")
    assert hook_res["decision"] == "allow"
    assert "systemMessage" not in hook_res

    # From monorepo root, running `eidos context -f packages/api/src/api/service.py` auto-discovers packages/api/eidos
    monkeypatch.chdir(git_root)
    assert resolve_cli_eidos_dir(None, hint_files=["packages/api/src/api/service.py"]) == api_eidos.resolve()
    res_ctx = runner.invoke(app, ["context", "-f", "packages/api/src/api/service.py", "--no-semantic", "--json"])
    assert res_ctx.exit_code == 0
    assert "CMP-API-SERVICE" in res_ctx.stdout
