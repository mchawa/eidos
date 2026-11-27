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
        f"from src.{pkg_slug}.service import run_{pkg_slug}\n\n\n"
        f"def test_{pkg_slug}_core():\n"
        f'    """Verifies {inv_id}."""\n'
        f"    assert run_{pkg_slug}(1) == 2\n",
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
        f'domain: {dom_id}\nstatus: active\nversion: "1.0.0"\n'
        f"technical:\n  containers:\n    - {cnt_id}\n  components:\n    - {cmp_id}\n"
        f"  invariants:\n    - id: {inv_id}\n"
        f'      rule: "{pkg_slug} service must increment input deterministically."\n'
        f'      test_binding: "tests/test_{pkg_slug}.py::test_{pkg_slug}_core"\n---\n'
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
        "def refund_billing(amount: int) -> int:\n    return amount\n",
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
        '    expect:\n      body:\n        contains:\n          - "Deterministic refund"\n'
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

    # 8. Shared Git-root Agent Skills & AGENTS.md placement when invoked from a sub-project
    install_results = install_plugin(PluginInstallTarget.ANTIGRAVITY, project_dir=api_root)
    assert all(r.success for r in install_results)
    assert (git_root / ".agents" / "skills" / "eidos" / "SKILL.md").is_file()
    assert (git_root / "AGENTS.md").is_file()
    assert not (api_root / ".agents").exists()
    assert not (api_root / "AGENTS.md").exists()

    plugin_status = check_plugin_version_status(project_dir=billing_root)
    assert plugin_status["status"] == "up_to_date"

    uninstall_results = uninstall_plugin(PluginInstallTarget.ANTIGRAVITY, project_dir=billing_root)
    assert all(r.success for r in uninstall_results)
    assert not (git_root / ".agents" / ".eidos-manifest.json").exists()

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


def test_monorepo_root_cli_routing_drift_validate_and_hooks(tmp_path: Path, monkeypatch) -> None:
    """Verifies INV-MONOREPO-ROOT-CLI-AND-HOOK-ROUTING across both subproject-only and root+subproject monorepos."""
    import orjson

    from eidos.plugin.hooks import capture_session_baseline

    git_root = tmp_path / "monorepo_routing"
    _init_git_monorepo(git_root)

    api_root = git_root / "packages" / "api"
    billing_root = git_root / "packages" / "billing"
    api_eidos = _scaffold_subproject(api_root, "api", "API")
    billing_eidos = _scaffold_subproject(billing_root, "billing", "BILLING")

    # Simulate a shared gitignored semantic model cache directory at <git_root>/eidos/.cache (NOT an initialized root eidos/)
    (git_root / "eidos" / ".cache" / "models").mkdir(parents=True, exist_ok=True)
    _ensure_eidos_gitignore(git_root / "eidos")
    (git_root / "eidos" / ".cache" / ".gitignore").write_text("*\n", encoding="utf-8")

    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Initial monorepo with api and billing sub-projects")

    monkeypatch.chdir(git_root)

    # === LAYOUT 1: Sub-projects only (no initialized root eidos/) ===

    # 1. Ambiguous root commands fail closed with discovered_subprojects hints and NEVER create root index.db
    for cmd_args in (
        ["index", "find-symbol", "run_api", "--json"],
        ["index", "search", "billing", "--no-semantic", "--json"],
        ["index", "repo-map", "--json"],
        ["spec", "list", "--json"],
        ["delta", "list", "--json"],
        ["memory", "list", "--json"],
    ):
        res = runner.invoke(app, cmd_args)
        assert res.exit_code == 1, (cmd_args, res.stdout)
        err_payload = orjson.loads(res.stdout)
        assert err_payload["discovered_subprojects"] == ["packages/api/eidos", "packages/billing/eidos"]
    assert not (git_root / "eidos" / ".cache" / "index.db").exists()

    # Explicit --path works cleanly from monorepo root
    res_sym = runner.invoke(app, ["index", "find-symbol", "run_api", "-p", "packages/api/eidos", "--json"])
    assert res_sym.exit_code == 0, res_sym.stdout
    assert orjson.loads(res_sym.stdout)["count"] >= 1

    # 2. Path-hint auto-routing for planned non-existent subdirs, blast-radius, spec show, and cross-subproject span guard
    res_planned = runner.invoke(
        app,
        ["context", "-f", "packages/api/src/api/new_subdir/planned.py", "--no-semantic", "--json"],
    )
    assert res_planned.exit_code == 0, res_planned.stdout
    assert "CMP-API-SERVICE" in res_planned.stdout or "CNT-API" in res_planned.stdout

    res_span = runner.invoke(
        app,
        [
            "context",
            "-f",
            "packages/api/src/api/service.py,packages/billing/src/billing/service.py",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_span.exit_code == 1
    assert orjson.loads(res_span.stdout)["discovered_subprojects"] == ["packages/api/eidos", "packages/billing/eidos"]

    res_br = runner.invoke(app, ["index", "blast-radius", "packages/api/src/api/service.py", "--json"])
    assert res_br.exit_code == 0, res_br.stdout
    br_data = orjson.loads(res_br.stdout)
    assert "CMP-API-SERVICE" in br_data["resolved_entities"]

    res_spec_show = runner.invoke(
        app,
        ["spec", "show", "packages/api/eidos/architecture/components/api-service.adoc"],
    )
    assert res_spec_show.exit_code == 0, res_spec_show.stdout
    assert "CMP-API-SERVICE" in res_spec_show.stdout

    # 3. Multi-subproject `eidos validate` and clean `eidos drift` from root
    res_val = runner.invoke(app, ["validate", "--json"])
    assert res_val.exit_code == 0, res_val.stdout
    val_data = orjson.loads(res_val.stdout)
    assert val_data["is_clean"] is True
    assert len(val_data["monorepo_projects"]) == 2

    res_clean_drift = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert res_clean_drift.exit_code == 0, res_clean_drift.stdout
    clean_drift_data = orjson.loads(res_clean_drift.stdout)
    assert clean_drift_data["is_clean"] is True
    assert len(clean_drift_data["monorepo_projects"]) == 2

    # 4. Stop hook baseline + single dirty sub-project auto-routing (context, drift, memory, hook)
    capture_session_baseline({"session_id": "mono-s1", "hook_event_name": "SessionStart"}, git_root)
    assert handle_stop_hook({"session_id": "mono-s1"}, git_root) == {}

    (billing_root / "src" / "billing" / "service.py").write_text(
        'def run_billing(value: int) -> int:\n    """Run billing logic."""\n    return value + 1\n\n\n'
        "def charge_billing(amount: int) -> int:\n    return amount\n",
        encoding="utf-8",
    )

    res_ctx_u = runner.invoke(app, ["context", "--uncommitted", "--no-semantic", "--json"])
    assert res_ctx_u.exit_code == 0, res_ctx_u.stdout
    assert "CMP-BILLING-SERVICE" in res_ctx_u.stdout

    res_drift_one = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert res_drift_one.exit_code == 1, res_drift_one.stdout
    drift_one_data = orjson.loads(res_drift_one.stdout)
    assert drift_one_data["drift_detected"] is True
    assert drift_one_data["eidos_dir"] == "packages/billing/eidos"
    assert "CMP-BILLING-SERVICE" in drift_one_data["touched_components"]

    # Rich output auto-routing banner also works cleanly
    res_drift_one_rich = runner.invoke(app, ["drift", "--uncommitted"])
    assert res_drift_one_rich.exit_code == 1
    assert "packages/billing/eidos" in res_drift_one_rich.stdout

    # Stop hook detects sub-project drift even from monorepo root with cache-only <git_root>/eidos/
    stop_l1 = handle_stop_hook({"session_id": "mono-s1"}, git_root)
    assert stop_l1["decision"] == "block"
    assert "packages/billing/eidos" in stop_l1["reason"]

    # Memory add with --file and --from-diff auto-routes to billing and normalizes paths relative to billing_root
    res_mem = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Billing rounding invariant",
            "--guidance",
            "Always round billing amounts in cents.",
            "--file",
            "packages/billing/src/billing/service.py",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_mem.exit_code == 0, res_mem.stdout
    mem_data = orjson.loads(res_mem.stdout)
    mem_file = Path(mem_data["path"])
    assert mem_file.is_relative_to(billing_eidos.resolve())
    assert "src/billing/service.py" in mem_file.read_text(encoding="utf-8")
    assert "packages/billing/src/billing/service.py" not in mem_file.read_text(encoding="utf-8")

    # Path-hint routing on `eidos memory show` and `eidos memory update`
    mem_rel = str(mem_file.relative_to(git_root.resolve()))
    res_mem_show = runner.invoke(app, ["memory", "show", mem_rel, "--json"])
    assert res_mem_show.exit_code == 0, res_mem_show.stdout
    assert orjson.loads(res_mem_show.stdout)["id"] == mem_data["id"]

    res_mem_upd = runner.invoke(
        app,
        [
            "memory",
            "update",
            mem_rel,
            "--file",
            "packages/billing/src/billing/service.py",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_mem_upd.exit_code == 0, res_mem_upd.stdout
    assert "packages/billing/src/billing/service.py" not in mem_file.read_text(encoding="utf-8")
    mem_file.unlink()

    # Memory add --from-diff auto-routes when a single sub-project is dirty
    res_mem_diff = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Billing diff note",
            "--guidance",
            "Captured via --from-diff from monorepo root.",
            "--from-diff",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_mem_diff.exit_code == 0, res_mem_diff.stdout
    mem_diff_file = Path(orjson.loads(res_mem_diff.stdout)["path"])
    assert mem_diff_file.is_relative_to(billing_eidos.resolve())
    mem_diff_file.unlink()

    # Path-hint routing on `eidos delta show` from monorepo root
    delta_file = create_delta(api_eidos, "DLT-2026-901", "API Health Endpoint", kind="feature")
    delta_rel = str(delta_file.relative_to(git_root.resolve()))
    res_dlt_show = runner.invoke(app, ["delta", "show", delta_rel, "--json"])
    assert res_dlt_show.exit_code == 0, res_dlt_show.stdout
    assert orjson.loads(res_dlt_show.stdout)["id"].startswith("DLT-2026-901")
    delta_file.unlink()

    # 5. Modify BOTH api and billing -> multi-subproject drift aggregation + ambiguous context/memory guard
    (api_root / "src" / "api" / "service.py").write_text(
        'def run_api(value: int) -> int:\n    """Run api logic."""\n    return value + 1\n\n\n'
        "def health_api() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )

    res_drift_two = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert res_drift_two.exit_code == 1, res_drift_two.stdout
    drift_two_data = orjson.loads(res_drift_two.stdout)
    assert drift_two_data["is_clean"] is False
    assert drift_two_data["drift_detected"] is True
    assert [p["eidos_dir"] for p in drift_two_data["monorepo_projects"]] == [
        "packages/api/eidos",
        "packages/billing/eidos",
    ]
    res_drift_two_rich = runner.invoke(app, ["drift", "--uncommitted"])
    assert res_drift_two_rich.exit_code == 1
    assert "packages/api/eidos" in res_drift_two_rich.stdout
    assert "packages/billing/eidos" in res_drift_two_rich.stdout

    res_ctx_ambig = runner.invoke(app, ["context", "--uncommitted", "--no-semantic", "--json"])
    assert res_ctx_ambig.exit_code == 1
    assert orjson.loads(res_ctx_ambig.stdout)["discovered_subprojects"] == [
        "packages/api/eidos",
        "packages/billing/eidos",
    ]

    res_mem_ambig = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Ambiguous diff note",
            "--guidance",
            "Spans two sub-projects.",
            "--from-diff",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_mem_ambig.exit_code == 1
    assert orjson.loads(res_mem_ambig.stdout)["discovered_subprojects"] == [
        "packages/api/eidos",
        "packages/billing/eidos",
    ]

    # Reset working tree before testing Layout 2
    _git(git_root, "checkout", "--", ".")

    # === LAYOUT 2: Root eidos/ + nested sub-projects ===
    root_ok, root_msg, _ = init_repository(git_root, title="Monorepo Root")
    assert root_ok, root_msg
    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Add root eidos project alongside sub-projects")

    # Root index queries succeed AND advertise nested_subprojects (JSON & Rich)
    res_root_sym = runner.invoke(app, ["index", "find-symbol", "nonexistent_root_sym", "--json"])
    assert res_root_sym.exit_code == 0, res_root_sym.stdout
    assert orjson.loads(res_root_sym.stdout)["nested_subprojects"] == [
        "packages/api/eidos",
        "packages/billing/eidos",
    ]
    res_root_sym_rich = runner.invoke(app, ["index", "find-symbol", "nonexistent_root_sym"])
    assert res_root_sym_rich.exit_code == 0
    assert "packages/api/eidos" in res_root_sym_rich.stdout

    res_root_search = runner.invoke(app, ["index", "search", "billing", "--no-semantic", "--json"])
    assert res_root_search.exit_code == 0, res_root_search.stdout
    assert orjson.loads(res_root_search.stdout)["nested_subprojects"] == [
        "packages/api/eidos",
        "packages/billing/eidos",
    ]
    res_root_map = runner.invoke(app, ["index", "repo-map", "--json"])
    assert res_root_map.exit_code == 0, res_root_map.stdout
    assert orjson.loads(res_root_map.stdout)["nested_subprojects"] == [
        "packages/api/eidos",
        "packages/billing/eidos",
    ]

    # Root validate checks root + both sub-projects (JSON & Rich)
    res_val_l2 = runner.invoke(app, ["validate", "--json"])
    assert res_val_l2.exit_code == 0, res_val_l2.stdout
    val_l2 = orjson.loads(res_val_l2.stdout)
    assert val_l2["is_clean"] is True
    assert [p["eidos_dir"] for p in val_l2["monorepo_projects"]] == [
        "eidos",
        "packages/api/eidos",
        "packages/billing/eidos",
    ]
    res_val_l2_rich = runner.invoke(app, ["validate"])
    assert res_val_l2_rich.exit_code == 0
    assert "3 monorepo projects" in res_val_l2_rich.stdout

    # Capture session baseline for Layout 2 (Antigravity client payload)
    capture_session_baseline({"conversation_id": "mono-s2", "event": "SessionStart"}, git_root)
    assert handle_stop_hook({"conversation_id": "mono-s2"}, git_root) == {"decision": "allow"}

    # Modify ONLY packages/api/src/api/service.py -> NO false-green from root eidos/!
    (api_root / "src" / "api" / "service.py").write_text(
        'def run_api(value: int) -> int:\n    """Run api logic."""\n    return value + 1\n\n\n'
        "def ping_api() -> str:\n    return 'pong'\n",
        encoding="utf-8",
    )

    res_drift_l2 = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert res_drift_l2.exit_code == 1, res_drift_l2.stdout
    drift_l2 = orjson.loads(res_drift_l2.stdout)
    assert drift_l2["is_clean"] is False
    assert drift_l2["drift_detected"] is True
    assert drift_l2["eidos_dir"] == "packages/api/eidos"

    stop_l2 = handle_stop_hook({"conversation_id": "mono-s2"}, git_root)
    assert stop_l2["decision"] == "continue"
    assert "packages/api/eidos" in stop_l2["reason"]
    assert "Sub-project `eidos`:" not in stop_l2["reason"]

    # Co-evolve packages/api component spec -> both drift and Stop hook become clean!
    (api_eidos / "architecture" / "components" / "api-service.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-API-SERVICE\ntitle: ApiService\n"
        "container: CNT-API\nstatus: active\nsource_path: src/api/service.py\n---\n"
        "= Component: ApiService\n\n== Responsibility\nImplements `run_api` and `ping_api`.\n\n"
        "== Public Surface\n* `run_api(value: int) -> int`\n* `ping_api() -> str`\n",
        encoding="utf-8",
    )
    res_drift_l2_clean = runner.invoke(app, ["drift", "--uncommitted", "--json"])
    assert res_drift_l2_clean.exit_code == 0, res_drift_l2_clean.stdout
    assert orjson.loads(res_drift_l2_clean.stdout)["is_clean"] is True
    assert handle_stop_hook({"conversation_id": "mono-s2"}, git_root) == {"decision": "allow"}

    # Verify bundled AGENTS.md block and master eidos skill carry monorepo sub-project scoping guidance
    repo_root = Path(__file__).resolve().parent.parent
    assert "-p <subproject>/eidos" in (repo_root / "assets/plugin_reference/agents-md/block.md").read_text(
        encoding="utf-8"
    )
    assert "-p <subproject>/eidos" in (repo_root / "assets/plugin_reference/skills/eidos/SKILL.md").read_text(
        encoding="utf-8"
    )

    # === Regression checks from adversarial review ===
    # 1. `eidos index blast-radius` with explicit `-p` from monorepo root strips sub-project prefix,
    #    and from a sub-project subdirectory preserves repo-relative paths.
    res_br_explicit = runner.invoke(
        app, ["index", "blast-radius", "packages/api/src/api/service.py", "-p", "packages/api/eidos", "--json"]
    )
    assert res_br_explicit.exit_code == 0, res_br_explicit.stdout
    assert "CMP-API-SERVICE" in orjson.loads(res_br_explicit.stdout)["resolved_entities"]

    monkeypatch.chdir(api_root / "tests")
    res_br_subdir = runner.invoke(app, ["index", "blast-radius", "src/api/service.py", "--json"])
    assert res_br_subdir.exit_code == 0, res_br_subdir.stdout
    assert "CMP-API-SERVICE" in orjson.loads(res_br_subdir.stdout)["resolved_entities"]
    monkeypatch.chdir(git_root)

    # 2. Path-routed `spec show` without `.adoc` suffix resolves cleanly
    res_spec_no_ext = runner.invoke(app, ["spec", "show", "packages/api/eidos/architecture/components/api-service"])
    assert res_spec_no_ext.exit_code == 0, res_spec_no_ext.stdout
    assert "CMP-API-SERVICE" in res_spec_no_ext.stdout

    # 3. Cline 1-turn retry guard with 2 dirty sub-projects does NOT ping-pong
    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Commit reconciled api changes before Cline retry check")
    cline_start = {"clineVersion": "3.0", "hookName": "TaskStart", "taskId": "cline-t1"}
    capture_session_baseline(cline_start, git_root)
    (api_root / "src" / "api" / "service.py").write_text(
        (api_root / "src" / "api" / "service.py").read_text(encoding="utf-8")
        + "\ndef extra_api() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (billing_root / "src" / "billing" / "service.py").write_text(
        (billing_root / "src" / "billing" / "service.py").read_text(encoding="utf-8")
        + "\ndef extra_billing() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    cline_stop = {
        "clineVersion": "3.0",
        "hookName": "PreToolUse",
        "taskId": "cline-t1",
        "preToolUse": {"toolName": "attempt_completion"},
    }
    first_cline = handle_stop_hook(cline_stop, git_root)
    assert first_cline["cancel"] is True
    assert "packages/api/eidos" in first_cline["errorMessage"]
    assert "packages/billing/eidos" in first_cline["errorMessage"]
    # Immediate unchanged retry MUST allow (never ping-pong between api and billing)
    second_cline = handle_stop_hook(cline_stop, git_root)
    assert second_cline == {"cancel": False}

    # 4. Root-file edit (eidos/system.adoc) does NOT defeat nested sub-project session baseline
    #    (packages/api has pre-existing dirty drift captured in baseline; editing root eidos/system.adoc must not trigger packages/api)
    capture_session_baseline({"conversation_id": "mono-s3", "event": "SessionStart"}, git_root)
    root_sys = git_root / "eidos" / "system.adoc"
    root_sys.write_text(root_sys.read_text(encoding="utf-8") + "\nUpdated root note.\n", encoding="utf-8")
    stop_root_only = handle_stop_hook({"conversation_id": "mono-s3"}, git_root)
    assert stop_root_only == {"decision": "allow"}

    # 5. Zero-config AGENTS.md sub-project auto-listing on plugin install + auto-refresh on `eidos init <subproject>`
    _git(git_root, "checkout", "--", ".")
    install_plugin(PluginInstallTarget.ANTIGRAVITY, project_dir=git_root, force=True)
    agents_md_text = (git_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Sub-projects:" in agents_md_text
    assert "`eidos`" in agents_md_text
    assert "`packages/api/eidos`" in agents_md_text
    assert "`packages/billing/eidos`" in agents_md_text

    res_init_sub = runner.invoke(app, ["init", "packages/payments", "--title", "Payments Service"])
    assert res_init_sub.exit_code == 0, res_init_sub.stdout
    assert "Refreshed monorepo sub-project list in root AGENTS.md" in res_init_sub.stdout
    agents_md_after = (git_root / "AGENTS.md").read_text(encoding="utf-8")
    assert "`packages/payments/eidos`" in agents_md_after


def test_monorepo_cli_branch_coverage_and_edge_cases(tmp_path: Path, monkeypatch) -> None:
    """Exercise branch-diff routing, glob/symlink normalization, Rich error paths, and AGENTS.md refresh safety."""
    import orjson

    from eidos.cli._shared import (
        _looks_like_path_hint,
        _resolve_hint_file_eidos_dir,
        _strip_glob_prefix,
        format_rel_eidos_paths,
        resolve_git_diff_workspace_eidos_dirs,
        resolve_uncommitted_workspace_eidos_dirs,
    )
    from eidos.plugin.installer import refresh_agents_md_subprojects

    # Pure helper edge cases in _shared.py
    assert _strip_glob_prefix("packages/api/src/**/*.py") == "packages/api/src"
    assert _strip_glob_prefix("*.py") == ""
    assert _strip_glob_prefix("//server/share/pkg/*.py") == "//server/share/pkg"
    assert _strip_glob_prefix("C:\\*.py") == "C:/"
    assert _looks_like_path_hint("GET /users") is False
    assert _looks_like_path_hint("") is False
    assert _resolve_hint_file_eidos_dir("") is None

    # Non-git directory returns empty uncommitted/diff lists and refresh_agents_md_subprojects returns None
    non_git = tmp_path / "non_git"
    non_git.mkdir()
    assert resolve_uncommitted_workspace_eidos_dirs(non_git) == []
    assert resolve_git_diff_workspace_eidos_dirs(non_git, uncommitted=False) == []
    assert refresh_agents_md_subprojects(non_git) is None

    git_root = tmp_path / "mono_edges"
    _init_git_monorepo(git_root)
    api_root = git_root / "packages" / "api"
    billing_root = git_root / "packages" / "billing"
    api_eidos = _scaffold_subproject(api_root, "api", "API")
    _scaffold_subproject(billing_root, "billing", "BILLING")
    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Initial rootless monorepo")

    monkeypatch.chdir(git_root)

    # Clean rootless monorepo: context --uncommitted returns empty slice (JSON and Rich), Rich rootless errors work
    res_ctx_empty_json = runner.invoke(app, ["context", "--uncommitted", "--no-semantic", "--json"])
    assert res_ctx_empty_json.exit_code == 0, res_ctx_empty_json.stdout
    res_ctx_empty_rich = runner.invoke(app, ["context", "--uncommitted", "--no-semantic"])
    assert res_ctx_empty_rich.exit_code == 0
    assert "No changed files detected" in res_ctx_empty_rich.stdout

    res_rich_rootless = runner.invoke(app, ["index", "find-symbol", "run_api"])
    assert res_rich_rootless.exit_code == 1
    assert "packages/api/eidos" in res_rich_rootless.stdout

    res_ctx_span_rich = runner.invoke(
        app,
        ["context", "-f", "packages/api/src/api/service.py,packages/billing/src/billing/service.py", "--no-semantic"],
    )
    assert res_ctx_span_rich.exit_code == 1
    assert "multiple independent Eidos sub-projects" in res_ctx_span_rich.stdout

    # Invalid --base ref errors on context and drift (JSON, Rich, and --warn-only)
    for bad_args in (
        ["context", "--base", "refs/heads/no-such-branch", "--no-semantic", "--json"],
        ["context", "--base", "refs/heads/no-such-branch", "--no-semantic"],
        ["drift", "--base", "refs/heads/no-such-branch", "--json"],
        ["drift", "--base", "refs/heads/no-such-branch"],
    ):
        r = runner.invoke(app, bad_args)
        assert r.exit_code == 1, (bad_args, r.stdout)

    r_warn_only = runner.invoke(app, ["drift", "--base", "refs/heads/no-such-branch", "--warn-only", "--json"])
    assert r_warn_only.exit_code == 0

    # Branch-diff mode (`--base main`) auto-routing across 1 dirty sub-project and 2 dirty sub-projects
    _git(git_root, "checkout", "-b", "feat/mono-branch")
    (api_root / "src" / "api" / "service.py").write_text(
        'def run_api(value: int) -> int:\n    """Run api logic."""\n    return value + 1\n\n\n'
        "def branch_api() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Add branch_api in api sub-project")

    res_ctx_base = runner.invoke(app, ["context", "--base", "main", "--no-semantic", "--json"])
    assert res_ctx_base.exit_code == 0, res_ctx_base.stdout
    assert "CMP-API-SERVICE" in res_ctx_base.stdout

    res_drift_base = runner.invoke(app, ["drift", "--base", "main", "--json"])
    assert res_drift_base.exit_code == 1, res_drift_base.stdout
    assert orjson.loads(res_drift_base.stdout)["eidos_dir"] == "packages/api/eidos"

    # Second commit touches billing too -> branch-diff drift aggregates both & context --base fails closed (Rich)
    (billing_root / "src" / "billing" / "service.py").write_text(
        'def run_billing(value: int) -> int:\n    """Run billing logic."""\n    return value + 1\n\n\n'
        "def branch_billing() -> int:\n    return 99\n",
        encoding="utf-8",
    )
    _git(git_root, "add", ".")
    _git(git_root, "commit", "-m", "Add branch_billing in billing sub-project")

    res_drift_base_two = runner.invoke(app, ["drift", "--base", "main", "--json"])
    assert res_drift_base_two.exit_code == 1
    assert len(orjson.loads(res_drift_base_two.stdout)["monorepo_projects"]) == 2

    res_ctx_base_ambig = runner.invoke(app, ["context", "--base", "main", "--no-semantic"])
    assert res_ctx_base_ambig.exit_code == 1
    assert "multiple independent Eidos sub-projects" in res_ctx_base_ambig.stdout

    # Memory glob normalization (./ prefix, absolute glob, cross-subproject guards on add & update in JSON & Rich)
    res_mem_glob = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Billing glob note",
            "--guidance",
            "Normalize globs relative to billing.",
            "--file",
            "./packages/billing/src/**/*.py",
            "--file",
            f"{billing_root.resolve().as_posix()}/tests/*.py",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_mem_glob.exit_code == 0, res_mem_glob.stdout
    mem_glob_data = orjson.loads(res_mem_glob.stdout)
    mem_glob_file = Path(mem_glob_data["path"])
    mem_glob_text = mem_glob_file.read_text(encoding="utf-8")
    assert "src/**/*.py" in mem_glob_text
    assert "tests/*.py" in mem_glob_text
    assert "packages/billing/" not in mem_glob_text

    # Bare MEM-* ID routed via --file on memory update
    res_mem_upd_bare = runner.invoke(
        app,
        [
            "memory",
            "update",
            mem_glob_data["id"],
            "--file",
            "packages/billing/src/billing/service.py",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_mem_upd_bare.exit_code == 0, res_mem_upd_bare.stdout

    # Cross-subproject --file guards on memory add & memory update (JSON and Rich)
    for bad_mem_args in (
        [
            "memory",
            "add",
            "--title",
            "Cross",
            "--guidance",
            "Bad",
            "--file",
            "packages/api/src/api/service.py",
            "--file",
            "packages/billing/src/billing/service.py",
            "--no-semantic",
            "--json",
        ],
        [
            "memory",
            "add",
            "--title",
            "Cross Rich",
            "--guidance",
            "Bad",
            "--file",
            "packages/api/src/api/service.py",
            "--file",
            "packages/billing/src/billing/service.py",
            "--no-semantic",
        ],
        [
            "memory",
            "update",
            str(mem_glob_file.relative_to(git_root.resolve())),
            "--file",
            "packages/api/src/api/service.py",
            "--no-semantic",
            "--json",
        ],
        [
            "memory",
            "update",
            str(mem_glob_file.relative_to(git_root.resolve())),
            "--file",
            "packages/api/src/api/service.py",
            "--no-semantic",
        ],
    ):
        rm = runner.invoke(app, bad_mem_args)
        assert rm.exit_code == 1, (bad_mem_args, rm.stdout)

    # Ambiguous --from-diff Rich error on memory add
    (api_root / "src" / "api" / "service.py").write_text(
        (api_root / "src" / "api" / "service.py").read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )
    (billing_root / "src" / "billing" / "service.py").write_text(
        (billing_root / "src" / "billing" / "service.py").read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )
    res_mem_diff_rich = runner.invoke(
        app,
        ["memory", "add", "--title", "Ambig Rich", "--guidance", "Two dirty", "--from-diff", "--no-semantic"],
    )
    assert res_mem_diff_rich.exit_code == 1
    assert "multiple independent Eidos sub-projects" in res_mem_diff_rich.stdout

    # Symlink hint resolution stays attributed to its containing sub-project & format_rel_eidos_paths handles outside paths
    sym_link = api_root / "src" / "api" / "linked_billing.py"
    sym_link.symlink_to(billing_root / "src" / "billing" / "service.py")
    assert _resolve_hint_file_eidos_dir("packages/api/src/api/linked_billing.py") == api_eidos.resolve()
    assert format_rel_eidos_paths([tmp_path / "outside" / "eidos"], git_root)[0].endswith("outside/eidos")

    # Hand-edited AGENTS.md managed block is NOT clobbered by refresh_agents_md_subprojects (force=False)
    install_plugin(PluginInstallTarget.ANTIGRAVITY, project_dir=git_root, force=True)
    agents_md = git_root / "AGENTS.md"
    agents_md.write_text(
        agents_md.read_text(encoding="utf-8").replace("Living specs", "Custom user edit"), encoding="utf-8"
    )
    res_hand_edit = refresh_agents_md_subprojects(billing_root)
    assert res_hand_edit is not None
    assert "Custom user edit" in agents_md.read_text(encoding="utf-8")
